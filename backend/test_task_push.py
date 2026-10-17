"""Push to GitHub against a local bare repository. No network, no model, temporary folders only.

The fixture: a bare "origin", a working clone bound to a Host project, HOME pointed at the temp folder so the Mac's own
git config (credential helpers, signing, hooks) stays out of it."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import task_git
import task_push
from dervo_connections import Rejected
from host import Host, ACTOR

ENV = {'PATH': '/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'}


def verb(args):
    """The git command after task_push's ``-c`` pairs, and everything from it on."""
    i = 0
    while i < len(args) and args[i] == '-c':
        i += 2
    return list(args[i:]) or [None]


class TaskPushTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = patch.dict(os.environ, {'HOME': str(self.root)}); self.home.start(); self.addCleanup(self.home.stop)
        # The fixture's origin is a local path. Production allows HTTPS and SSH only (test_repo_config_runs_nothing).
        self.protocols = patch.object(task_push, 'PROTOCOLS', ('https', 'ssh', 'file')); self.protocols.start(); self.addCleanup(self.protocols.stop)
        self.origin = self.root / 'origin.git'
        self.git(self.root, 'init', '-q', '--bare', '-b', 'main', str(self.origin))
        self.clone = self.root / 'work'
        self.git(self.root, 'clone', '-q', str(self.origin), str(self.clone))
        self.git(self.clone, 'config', 'user.name', 'Fixture')
        self.git(self.clone, 'config', 'user.email', 'fixture@example.test')
        (self.clone / 'README.md').write_text('hello\n')
        self.git(self.clone, 'add', '-A')
        self.git(self.clone, 'commit', '-q', '-m', 'Before')
        self.git(self.clone, 'push', '-q', '-u', 'origin', 'main')
        self.host = Host(self.root / 'store'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('p', 'codex', self.root / 'store/profiles/p', 'synthetic@example.test', 'personal')
        self.host.store.add_project('p', self.clone, 'p', 'synthetic-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('c', 'Connector picker'))
            self.host.store.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')

    # ---------------------------------------------------------------- helpers
    def git(self, cwd, *args):
        result = subprocess.run(['/usr/bin/git', *args], cwd=cwd, capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, name, cwd=None):
        cwd = cwd or self.clone
        (cwd / name).write_text(name + '\n')
        self.git(cwd, 'add', '-A')
        self.git(cwd, 'commit', '-q', '-m', f'Add {name}')
        return self.git(cwd, 'rev-parse', 'HEAD')

    def task(self, task_id, sha, *, thread_id='c', reverted=None):
        document = dict(id=task_id, context=dict(thread_id=thread_id, project_id='p', actor=ACTOR), commit=dict(sha=sha, reverted=reverted))
        with self.host.store.db:
            self.host.store.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)', (task_id, json.dumps(document)))

    def preview(self):
        return self.host.dispatch('push_preview', dict(projectId='p'))

    def start(self, token, set_upstream=False):
        return self.host.dispatch('push_start', dict(projectId='p', confirmToken=token, setUpstream=set_upstream))

    def settled(self, deadline=30):
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            record = self.host.dispatch('push_status', dict(projectId='p'))
            if record and record['state'] != 'running':
                return record
            time.sleep(0.01)
        self.fail('the push never settled')

    def origin_head(self, branch='main'):
        result = subprocess.run(['/usr/bin/git', 'rev-parse', '--verify', '--quiet', f'refs/heads/{branch}'], cwd=self.origin,
                                capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        return result.stdout.strip() or None

    # ---------------------------------------------------------------- tests
    def test_preview_counts_commits_and_names_the_full_remote_url(self):
        first = self.commit('one.py')
        second = self.commit('two.py')
        self.task('task-1', first)
        preview = self.preview()
        self.assertEqual((preview['branch'], preview['upstream'], preview['remote'], preview['url']), ('main', 'origin/main', 'origin', str(self.origin)))
        self.assertEqual((preview['ahead'], preview['behind'], preview['head'], preview['can_push'], preview['git']), (2, 0, second, True, True))
        self.assertEqual(len(preview['confirm_token']), 16)
        self.assertEqual([(c['sha'], c['short'], c['subject'], c['thread_id'], c['thread_name'], c['task_id'], c['made_outside_dervo']) for c in preview['commits']],
                         [(second, second[:7], 'Add two.py', None, None, None, True), (first, first[:7], 'Add one.py', 'c', 'Connector picker', 'task-1', False)])
        self.assertIsNone(preview['latest'])
        for commit in preview['commits']:
            self.assertEqual(set(commit), {'sha', 'short', 'subject', 'thread_id', 'thread_name', 'task_id', 'made_outside_dervo'})
        # An undo commit is the task's too.
        third = self.commit('three.py')
        self.task('task-1', first, reverted=dict(sha=third))
        self.assertEqual([c['task_id'] for c in self.preview()['commits']], ['task-1', None, 'task-1'])
        # Negative control: once pushed there is nothing to count, no token, and the reply says so.
        self.git(self.clone, 'push', '-q', 'origin', 'main')
        preview = self.preview()
        self.assertEqual((preview['ahead'], preview['commits'], preview['confirm_token'], preview['can_push']), (0, [], None, False))
        self.assertIn('Nothing to push', preview['detail'])
        with self.assertRaisesRegex(Rejected, 'Unsupported push fields'):
            self.host.dispatch('push_preview', dict(projectId='p', extra=1))
        with self.assertRaisesRegex(Rejected, 'Unknown project'):
            self.host.dispatch('push_preview', dict(projectId='nope'))

    def test_push_sends_exactly_the_counted_commits_and_never_force(self):
        self.commit('one.py')
        head = self.commit('two.py')
        argv = []
        real = task_git._git
        def spy(cwd, *args, **kwargs):
            if verb(args)[0] == 'push':
                argv.append(verb(args))
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', spy):
            preview = self.preview()
            record = self.start(preview['confirm_token'])
            self.assertEqual((record['state'], record['count'], record['head'], record['branch'], record['remote'], record['url']),
                             ('running', 2, head, 'main', 'origin', str(self.origin)))
            self.assertEqual(set(record), {'id', 'project_id', 'cwd', 'branch', 'remote', 'url', 'upstream', 'sets_upstream', 'head', 'count',
                                           'state', 'detail', 'started_at', 'finished_at'})
            with self.assertRaisesRegex(Rejected, 'already running|no longer valid'):
                self.start(preview['confirm_token'])  # a confirm is good for one push, and one push at a time
            done = self.settled()
        self.assertEqual(argv, [['push', '--', 'origin', 'main']])
        self.assertEqual((done['state'], done['detail']), ('pushed', 'Pushed 2 commits to origin/main.'))
        self.assertEqual(self.origin_head(), head)
        self.assertEqual(self.preview()['latest']['state'], 'pushed')
        # Negative control: when GitHub has a commit you don't have, nothing runs and the bare head stays.
        other = self.root / 'other'
        self.git(self.root, 'clone', '-q', str(self.origin), str(other))
        self.git(other, 'config', 'user.name', 'Other'); self.git(other, 'config', 'user.email', 'other@example.test')
        theirs = self.commit('theirs.py', cwd=other)
        self.git(other, 'push', '-q', 'origin', 'main')
        self.commit('mine.py')
        self.git(self.clone, 'fetch', '-q', 'origin')
        preview = self.preview()
        self.assertEqual((preview['ahead'], preview['behind'], preview['can_push'], preview['confirm_token']), (1, 1, False, None))
        self.assertEqual(preview['detail'], task_push.BEHIND)
        argv.clear()
        with patch.object(task_git, '_git', spy), self.assertRaisesRegex(Rejected, 'no longer valid'):
            self.start('0' * 16)
        self.assertEqual((argv, self.origin_head()), ([], theirs))

    def test_a_changed_remote_url_refuses_the_push(self):
        head = self.commit('one.py')
        preview = self.preview()
        other = self.root / 'elsewhere.git'
        self.git(self.root, 'init', '-q', '--bare', '-b', 'main', str(other))
        self.git(self.clone, 'remote', 'set-url', 'origin', str(other))
        with self.assertRaisesRegex(Rejected, 'remote changed'):
            self.start(preview['confirm_token'])
        self.assertIsNone(self.host.dispatch('push_status', dict(projectId='p')))
        self.assertNotEqual(self.origin_head(), head)  # nothing reached the remote the confirm showed
        elsewhere = subprocess.run(['/usr/bin/git', 'rev-parse', '--verify', '--quiet', 'refs/heads/main'], cwd=other, capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        self.assertEqual((elsewhere.returncode == 0, elsewhere.stdout.strip()), (False, ''))  # nor the new one
        # Negative control: the same token with the url put back pushes.
        self.git(self.clone, 'remote', 'set-url', 'origin', str(self.origin))
        self.start(preview['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(self.origin_head(), head)
        # A renamed remote is a changed remote too.
        second = self.commit('two.py')
        preview = self.preview()
        self.git(self.clone, 'remote', 'rename', 'origin', 'github')
        with self.assertRaisesRegex(Rejected, 'remote changed'):
            self.start(preview['confirm_token'])
        self.assertNotEqual(self.origin_head(), second)

    def test_the_confirm_shows_and_binds_the_push_url(self):
        # M1: pushurl and pushInsteadOf change where git pushes. The confirm shows that URL, and a change after it refuses.
        head = self.commit('one.py')
        evil = self.root / 'evil.git'
        self.git(self.root, 'init', '-q', '--bare', '-b', 'main', str(evil))
        self.git(self.clone, 'config', 'remote.origin.pushurl', str(evil))
        self.assertEqual(self.preview()['url'], str(evil))
        self.git(self.clone, 'config', '--unset', 'remote.origin.pushurl')
        self.git(self.clone, 'config', f'url.{evil}.pushInsteadOf', str(self.origin))
        self.assertEqual(self.preview()['url'], str(evil))
        self.git(self.clone, 'config', '--unset', f'url.{evil}.pushInsteadOf')
        preview = self.preview()
        self.assertEqual(preview['url'], str(self.origin))
        self.git(self.clone, 'config', 'remote.origin.pushurl', str(evil))  # planted after you confirmed
        with self.assertRaisesRegex(Rejected, 'remote changed'):
            self.start(preview['confirm_token'])
        self.assertIsNone(subprocess.run(['/usr/bin/git', 'rev-parse', '--verify', '--quiet', 'refs/heads/main'], cwd=evil,
                                         capture_output=True, text=True).stdout.strip() or None)
        # Two push URLs means git pushes to both: refused, with no token.
        self.git(self.clone, 'config', '--add', 'remote.origin.pushurl', str(self.origin))
        preview = self.preview()
        self.assertEqual((preview['url'], preview['can_push'], preview['confirm_token'], preview['detail']), (None, False, None, task_push.MANY_URLS))
        # Negative control: with the planted URL gone the same token pushes to the origin it showed.
        self.git(self.clone, 'config', '--unset-all', 'remote.origin.pushurl')
        self.start(self.preview()['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(self.origin_head(), head)

    def test_repo_config_runs_nothing(self):
        # M1: the project's .git/config can name programs. The push's own config must not run them.
        marker = self.root / 'marker'
        def script(name, body=''):
            path = self.root / name
            path.write_text(f'#!/bin/sh\necho {name} >> {marker}\n{body}')
            path.chmod(0o755)
            return str(path)
        # Global helper (yours) stays; the repository's helper and askpass don't run.
        self.git(self.root, 'config', '--file', str(self.root / '.gitconfig'), 'credential.helper', '!' + script('global-helper'))
        self.git(self.clone, 'config', 'credential.helper', '!' + script('repo-helper'))
        self.git(self.clone, 'config', 'credential.https://github.com.helper', '!' + script('repo-url-helper'))
        self.git(self.clone, 'config', 'core.askpass', script('repo-askpass', 'echo x\n'))
        env = {**ENV, 'HOME': str(self.root)}
        def fill(extra):
            marker.unlink(missing_ok=True)
            subprocess.run(['/usr/bin/git', *extra, 'credential', 'fill'], cwd=self.clone, env=env, capture_output=True, text=True,
                           input='protocol=https\nhost=github.com\n\n', timeout=30)
            return marker.read_text().split() if marker.exists() else []
        self.assertEqual(fill(task_push.hardening(self.clone)), ['global-helper'])
        self.assertEqual(fill([]), ['global-helper', 'repo-helper', 'repo-url-helper', 'repo-askpass', 'repo-askpass'])  # negative control
        # core.sshCommand is pinned to ssh.
        self.git(self.clone, 'config', 'core.sshCommand', script('repo-ssh', 'exit 1\n'))
        def ls(extra):
            marker.unlink(missing_ok=True)
            subprocess.run(['/usr/bin/git', *extra, 'ls-remote', 'ssh://git@example.invalid/x.git'], cwd=self.clone, env=env, capture_output=True, timeout=60)
            return marker.read_text().split() if marker.exists() else []
        self.assertEqual(ls(task_push.hardening(self.clone)), [])
        self.assertIn('repo-ssh', ls([]))  # negative control (git may retry once)
        # A local remote's receivepack is a local program: production refuses the URL before anything runs.
        self.git(self.clone, 'config', 'remote.origin.receivepack', script('repo-receivepack', 'exec git-receive-pack "$@"\n'))
        self.commit('one.py')
        marker.unlink(missing_ok=True)
        with patch.object(task_push, 'PROTOCOLS', ('https', 'ssh')):
            preview = self.preview()
            self.assertEqual((preview['url'], preview['can_push'], preview['detail']), (None, False, task_push.UNSAFE_URL))
        self.assertFalse(marker.exists())
        # Negative control: allow the file transport and the same push does run it.
        self.start(self.preview()['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(marker.read_text().split(), ['repo-receivepack'])

    def test_a_changed_head_or_branch_refuses_the_push(self):
        self.commit('one.py')
        preview = self.preview()
        self.commit('two.py')
        with self.assertRaisesRegex(Rejected, 'new commits since you confirmed'):
            self.start(preview['confirm_token'])
        preview = self.preview()
        self.git(self.clone, 'checkout', '-q', '-b', 'feature')
        with self.assertRaisesRegex(Rejected, 'branch changed'):
            self.start(preview['confirm_token'])
        self.git(self.clone, 'checkout', '-q', 'main')
        self.assertIsNone(self.origin_head('feature'))
        self.assertNotEqual(self.origin_head(), self.git(self.clone, 'rev-parse', 'HEAD'))
        # Negative control: back on the branch the confirm showed, the same head pushes.
        self.start(preview['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(self.origin_head(), self.git(self.clone, 'rev-parse', 'HEAD'))

    def test_no_upstream_pushes_with_u_only_when_asked(self):
        self.git(self.clone, 'checkout', '-q', '-b', 'feature')
        head = self.commit('one.py')
        preview = self.preview()
        self.assertEqual((preview['branch'], preview['upstream'], preview['remote'], preview['ahead'], preview['can_push']), ('feature', None, 'origin', 2, True))
        self.assertIn("isn't on GitHub yet", preview['detail'])
        with self.assertRaisesRegex(Rejected, "isn't on GitHub yet"):
            self.start(preview['confirm_token'], set_upstream=False)
        self.assertIsNone(self.origin_head('feature'))
        argv = []
        real = task_git._git
        def spy(cwd, *args, **kwargs):
            if verb(args)[0] == 'push':
                argv.append(verb(args))
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', spy):
            record = self.start(preview['confirm_token'], set_upstream=True)
            self.assertEqual((record['sets_upstream'], record['upstream']), (True, 'origin/feature'))
            self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(argv, [['push', '-u', '--', 'origin', 'feature']])
        self.assertEqual(self.origin_head('feature'), head)
        self.assertEqual(self.git(self.clone, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}'), 'origin/feature')
        # Negative control: with an upstream, setUpstream=True adds nothing.
        argv.clear()
        self.commit('two.py')
        with patch.object(task_git, '_git', spy):
            self.start(self.preview()['confirm_token'], set_upstream=True)
            self.settled()
        self.assertEqual(argv, [['push', '--', 'origin', 'feature']])

    def test_timeout_is_unknown_and_recount_settles_it_both_ways(self):
        head = self.commit('one.py')
        real = task_git._git
        def slow(cwd, *args, **kwargs):
            if verb(args)[0] == 'push':
                raise subprocess.TimeoutExpired(['git', 'push'], kwargs.get('timeout'))
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', slow):
            self.start(self.preview()['confirm_token'])
            unknown = self.settled()
        self.assertEqual((unknown['state'], unknown['detail']), ('unknown', task_push.LOST))
        preview = self.preview()
        self.assertEqual((preview['can_push'], preview['confirm_token']), (False, None))
        self.assertIn('Check GitHub before pushing again', preview['detail'])
        with self.assertRaisesRegex(Rejected, 'no longer valid'):
            self.start('0' * 16)
        # Recount: the bare repo doesn't have it.
        record = self.host.dispatch('push_recount', dict(projectId='p'))
        self.assertEqual((record['state'], record['detail']), ('not_pushed', task_push.NOT_PUSHED))
        self.assertNotEqual(self.origin_head(), head)
        # Pushed out of band, recount finds it.
        self.git(self.clone, 'push', '-q', 'origin', 'main')
        record = self.host.dispatch('push_recount', dict(projectId='p'))
        self.assertEqual((record['state'], record['detail'], record['head']), ('pushed', task_push.CONFIRMED, head))
        # A dead process is unknown too, and a recount that can't reach the remote stays unknown with the reason.
        second = self.commit('two.py')
        def dead(cwd, *args, **kwargs):
            if verb(args)[0] == 'push':
                raise OSError('process died')
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', dead):
            self.start(self.preview()['confirm_token'])
            self.assertEqual(self.settled()['state'], 'unknown')
        def unreachable(cwd, *args, **kwargs):
            if verb(args)[0] == 'ls-remote':
                raise subprocess.TimeoutExpired(['git', 'ls-remote'], 30)
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', unreachable):
            record = self.host.dispatch('push_recount', dict(projectId='p'))
        self.assertEqual(record['state'], 'unknown')
        self.assertIn("couldn't reach the remote", record['detail'])
        self.assertEqual(self.host.dispatch('push_recount', dict(projectId='p'))['state'], 'not_pushed')
        # Negative control: a real failure (non-fast-forward) is failed, not unknown, and a new push needs no recount.
        other = self.root / 'other'
        self.git(self.root, 'clone', '-q', str(self.origin), str(other))
        self.git(other, 'config', 'user.name', 'Other'); self.git(other, 'config', 'user.email', 'other@example.test')
        self.commit('theirs.py', cwd=other)
        self.git(other, 'push', '-q', 'origin', 'main')  # the clone hasn't fetched, so its counts still say behind=0
        self.start(self.preview()['confirm_token'])
        failed = self.settled()
        self.assertEqual((failed['state'], failed['detail']), ('failed', task_push.BEHIND))
        self.assertNotEqual(self.origin_head(), second)
        self.assertTrue(self.preview()['can_push'])

    def test_restart_marks_a_running_push_unknown(self):
        pushes = self.host.pushes()
        running = dict(id='r1', project_id='p', cwd=str(self.clone), branch='main', remote='origin', url=str(self.origin), upstream='origin/main',
                       sets_upstream=False, head='abc', count=1, state='running', detail='Pushing 1 commit to origin/main.', started_at=1, finished_at=None)
        pushes._save(running)
        pushes._save(dict(running, id='r0', project_id='other', state='pushed', detail='Pushed 1 commit to origin/main.'))
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO projects (id, cwd, profile_id, model) VALUES (?,?,?,?)', ('other', str(self.clone), 'p', 'synthetic-model'))
        fresh = task_push.Pushes(self.host)
        self.addCleanup(fresh.close)
        record = fresh.latest('p')
        self.assertEqual((record['state'], record['detail']), ('unknown', task_push.RESTARTED))
        self.assertIsNotNone(record['finished_at'])
        # Negative control: a settled record stays as it was.
        self.assertEqual(fresh.latest('other')['state'], 'pushed')
        self.assertEqual(fresh.latest('other')['detail'], 'Pushed 1 commit to origin/main.')

    def test_failure_reasons_are_plain_and_carry_no_token(self):
        samples = {
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled\n": task_push.SIGN_IN,
            'remote: Invalid username or password.\nfatal: Authentication failed for https://github.com/x/y.git/\n': task_push.SIGN_IN,
            'git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.\n': task_push.SSH,
            ' ! [rejected]        main -> main (fetch first)\nerror: failed to push some refs\nhint: Updates were rejected\n': task_push.BEHIND,
            ' ! [rejected]        main -> main (non-fast-forward)\nerror: failed to push some refs\n': task_push.BEHIND,
            "fatal: unable to access 'https://github.com/x/y.git/': Could not resolve host: github.com\n": task_push.NO_CONNECTION,
            'remote: something odd\nerror: last line here\n': 'error: last line here',
            '': 'git push failed',
        }
        for stderr, sentence in samples.items():
            self.assertEqual(task_push.failure_reason(stderr), sentence, stderr)
        leaky = 'fatal: unable to access https://bryce:ghp_abcdef0123456789@github.com/x/y.git: token ghp_abcdef0123456789 rejected\n'
        self.assertNotIn('ghp_abcdef', task_push.failure_reason(leaky))
        self.assertNotIn('ghp_abcdef', task_push.scrub(leaky))
        self.assertEqual(task_push.scrub('https://bryce:ghp_abcdef0123456789@github.com/x/y.git'), 'https://bryce:[redacted]@github.com/x/y.git')
        self.assertEqual(task_push.scrub('Authorization: Bearer abcdefghijklmnop'), 'Authorization: [redacted]')
        # Negative control: a plain URL and plain words pass through unchanged.
        self.assertEqual(task_push.scrub('https://github.com/brambach/dervo.git'), 'https://github.com/brambach/dervo.git')
        self.assertEqual(task_push.scrub('Add the ghost picker'), 'Add the ghost picker')
        # The whole path: a push whose stderr carries a token stores the mapped sentence only.
        self.commit('one.py')
        real = task_git._git
        def leaky_push(cwd, *args, **kwargs):
            if verb(args)[0] == 'push':
                return subprocess.CompletedProcess(args, 128, '', leaky)
            return real(cwd, *args, **kwargs)
        with patch.object(task_git, '_git', leaky_push):
            self.start(self.preview()['confirm_token'])
            failed = self.settled()
        self.assertEqual(failed['state'], 'failed')
        self.assertNotIn('ghp_', json.dumps(failed))
        self.assertNotIn('ghp_', self.host.store.db.execute('SELECT document FROM push_records').fetchone()[0])
        # And a remote URL that carries a password is shown without it.
        self.git(self.clone, 'remote', 'set-url', 'origin', 'https://bryce:ghp_abcdef0123456789@github.com/x/y.git')
        preview = self.preview()
        self.assertEqual(preview['url'], 'https://bryce:[redacted]@github.com/x/y.git')
        self.assertNotIn('ghp_', json.dumps(preview))

    def test_no_remote_and_detached_head_say_so(self):
        alone = self.root / 'alone'
        alone.mkdir()
        self.git(alone, 'init', '-q', '-b', 'main')
        self.git(alone, 'config', 'user.name', 'Fixture'); self.git(alone, 'config', 'user.email', 'fixture@example.test')
        self.commit('one.py', cwd=alone)
        state = task_push.remote_state(str(alone))
        self.assertEqual((state['git'], state['branch'], state['remote'], state['url'], state['detail']), (True, 'main', None, None, task_push.NO_REMOTE))
        self.assertEqual(task_push.pending_commits(self.host.store.db, str(alone), state), [])
        plain = self.root / 'plain'
        plain.mkdir()
        self.assertEqual((task_push.remote_state(str(plain))['git'], task_push.remote_state(str(plain))['detail']), (False, task_git.NOT_A_REPO))
        # Detached HEAD in the real project: the preview says so and a push is refused.
        head = self.commit('one.py')
        preview = self.preview()
        token = preview['confirm_token']
        self.git(self.clone, 'checkout', '-q', '--detach', 'HEAD')
        preview = self.preview()
        self.assertEqual((preview['branch'], preview['can_push'], preview['detail']), (None, False, task_push.DETACHED))
        with self.assertRaisesRegex(Rejected, "isn't on a branch"):
            self.start(token)  # the confirm is still valid, but the live state is checked again and refuses
        self.assertNotEqual(self.origin_head(), head)
        # Negative control: back on the branch, the same commit pushes.
        self.git(self.clone, 'checkout', '-q', 'main')
        self.start(self.preview()['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(self.origin_head(), head)

    def test_a_stale_confirm_token_is_refused(self):
        head = self.commit('one.py')
        now = [1000.0]
        self.host.pushes().clock = lambda: now[0]
        preview = self.preview()
        now[0] += task_push.CONFIRM_SECONDS + 1
        with self.assertRaisesRegex(Rejected, 'older than 10 minutes'):
            self.start(preview['confirm_token'])
        self.assertNotEqual(self.origin_head(), head)
        with self.assertRaisesRegex(Rejected, 'no longer valid'):
            self.start(preview['confirm_token'])  # a stale token is dropped, not kept around
        # Negative control: a token nine minutes old still pushes.
        preview = self.preview()
        now[0] += task_push.CONFIRM_SECONDS - 60
        self.start(preview['confirm_token'])
        self.assertEqual(self.settled()['state'], 'pushed')
        self.assertEqual(self.origin_head(), head)
        with self.assertRaisesRegex(Rejected, 'Unsupported push fields'):
            self.host.dispatch('push_start', dict(projectId='p', confirmToken=5, setUpstream=False))
        with self.assertRaisesRegex(Rejected, 'Unsupported push fields'):
            self.host.dispatch('push_start', dict(projectId='p', confirmToken='x', setUpstream='yes'))

    def test_thread_name_for_reads_the_conversation_name(self):
        task = dict(id='t', context=dict(thread_id='c', project_id='p', actor=ACTOR))
        self.assertEqual(task_push.thread_name_for(self.host.store.db, task), 'Connector picker')
        self.assertEqual(task_git.subject(dict(task, messages=[dict(role='user', text='Fix it')]), task_push.thread_name_for(self.host.store.db, task)), 'Dervo: Connector picker')
        # Negative controls: an unknown thread, no context, and the placeholder name give the fallback.
        self.assertIsNone(task_push.thread_name_for(self.host.store.db, dict(task, context=dict(thread_id='missing'))))
        self.assertIsNone(task_push.thread_name_for(self.host.store.db, dict(id='t')))
        self.host.store.add_thread('d', 'p', 'd', 'private', ACTOR)
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('d', 'New conversation'))
        placeholder = dict(task, context=dict(thread_id='d'))
        self.assertEqual(task_push.thread_name_for(self.host.store.db, placeholder), 'New conversation')
        self.assertEqual(task_git.subject(dict(placeholder, messages=[dict(role='user', text='Fix it')]), 'New conversation'), 'Dervo: Fix it')

    def test_host_close_shuts_the_worker_and_an_archived_project_is_refused(self):
        self.commit('one.py')
        self.assertTrue(self.preview()['can_push'])  # negative control: live, it previews
        self.host.dispatch('project_archive', dict(projectId='p'))
        with self.assertRaisesRegex(Rejected, 'archived'):
            self.preview()
        with self.assertRaisesRegex(Rejected, 'archived'):
            self.host.pushes()._cwd('p')  # the module refuses on its own too, not only through the host's archive check
        pushes = self.host.pushes()
        self.host.close()
        self.assertTrue(pushes.closed)
        with self.assertRaisesRegex(Rejected, 'closing'):
            pushes.start('p', str(self.clone), 'x', False)


if __name__ == '__main__':
    unittest.main()
