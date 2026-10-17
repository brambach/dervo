"""Send to cloud (cloud_runs.py): preview and send, Codex status from ``codex cloud list --json``, Claude runs shown
unknown until a branch appears, lost launches never sent again, and results brought back as a local branch without
touching the folder. Real host, real store, real git with a bare repository standing in for GitHub. The Claude Code and
Codex CLIs are stand-ins (FakeCli): no test calls a provider, signs in or reaches the network."""
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_runs
import task_push
from cloud_runs import CLAUDE_LINK, CloudRuns
from dervo_connections import Rejected
from host import ACTOR, Host

ENV = {'PATH': '/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'}
CLAUDE = '/fake/bin/claude'
CODEX = '/fake/bin/codex'
SESSION = 'session_01DiUkqY2kzbUbDmW1w96rfi'
CLAUDE_SENT = (f'Sent to cloud session.\nSession ID: {SESSION}\n'
               f'View: https://claude.ai/code/{SESSION}?from=cli&m=0\n')
CODEX_TASK = 'task_e_68f1c0ffee'
DIFF = ('diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n hello\n+from the cloud\n')


class Clock:
    def __init__(self):
        self.at = 1_790_000_000.0

    def __call__(self):
        return self.at


class Pool:
    """Runs submitted work straight away, so a test reads the outcome on its next request."""
    def submit(self, fn):
        fn()

    def shutdown(self, **_):
        pass


class FakeCli:
    """Stands in for subprocess.run: claude and codex answer from ``answers``; git runs for real."""
    def __init__(self, test):
        self.test, self.calls, self.answers = test, [], {}

    @staticmethod
    def key(argv):
        if argv[0] == CLAUDE:
            return CLAUDE, 'follow' if '-p' in argv else 'start'
        return CODEX, argv[2]

    def answer(self, argv, kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers.get(self.key(argv))
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = answer(argv)
        return answer or (1, '', 'not set up')

    def __call__(self, argv, **kwargs):
        if argv[0] in (CLAUDE, CODEX):
            code, out, err = self.answer(argv, kwargs)
            return subprocess.CompletedProcess(argv, code, out, err)
        kwargs.pop('errors', None)
        return subprocess.run(argv, **kwargs)

    def terminal(self, argv, **kwargs):
        """Stands in for run_terminal: (exit code or None at the timeout, everything printed)."""
        try:
            code, out, err = self.answer(argv, kwargs)
        except subprocess.TimeoutExpired:
            return None, ''
        return code, out + err


class CloudRunsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.enterContext(patch.dict(os.environ, {'HOME': str(self.root)}))
        self.enterContext(patch.object(task_push, 'PROTOCOLS', ('https', 'ssh', 'file')))
        import platform_os  # /private/tmp is a Mac folder; the scratch worktree goes in this test's own folder
        self.enterContext(patch.object(platform_os, 'scratch_root', lambda: str(self.root)))
        self.origin = self.root / 'origin.git'
        self.git(self.root, 'init', '-q', '--bare', '-b', 'main', str(self.origin))
        self.clone = self.root / 'work'
        self.git(self.root, 'clone', '-q', str(self.origin), str(self.clone))
        self.git(self.clone, 'config', 'user.name', 'Fixture')
        self.git(self.clone, 'config', 'user.email', 'fixture@example.test')
        (self.clone / 'README.md').write_text('hello\n')
        self.git(self.clone, 'add', '-A'); self.git(self.clone, 'commit', '-q', '-m', 'Before')
        self.git(self.clone, 'push', '-q', '-u', 'origin', 'main')
        self.host = Host(self.root / 'store'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('c', 'claude', self.root / 'store/profiles/c', 'bryce@example.test', 'personal')
        self.host.store.add_profile('x', 'codex', self.root / 'store/profiles/x', 'bryce@example.test', 'personal')
        self.host.store.add_project('p', self.clone, 'c', 'claude-opus-5-5')
        self.host.store.add_project('q', self.clone, 'x', 'gpt-5.6')
        self.cli, self.clock, self.probed = FakeCli(self), Clock(), []
        self.identity = 'identity_verified'

        def probe(profile):
            self.probed.append(profile)
            return {'state': self.identity, 'detail': 'The signed-in account isn’t this one. No messages were sent.'}
        self.cloud = CloudRuns(self.host, run=self.cli, terminal=self.cli.terminal, probe=probe, claude_path=CLAUDE, codex_path=CODEX,
                               clock=self.clock, workers=Pool())
        self.host.cloud = self.cloud

    # ---------------------------------------------------------------- helpers
    def git(self, cwd, *args):
        result = subprocess.run(['/usr/bin/git', *args], cwd=cwd, capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def call(self, operation, **args):
        return self.host.dispatch(operation, args)

    def push_cloud_branch(self, name, text='cloud\n'):
        """What a cloud session does when it finishes: a commit on a new branch on the remote."""
        other = self.root / f'cloud-{name.replace("/", "-")}'
        self.git(self.root, 'clone', '-q', str(self.origin), str(other))
        self.git(other, 'checkout', '-q', '-b', name)
        (other / 'CLOUD.md').write_text(text)
        self.git(other, 'add', '-A')
        self.git(other, '-c', 'user.name=Cloud', '-c', 'user.email=cloud@example.test', 'commit', '-q', '-m', 'From the cloud')
        self.git(other, 'push', '-q', 'origin', name)

    def send(self, project='p', text='Fix the flaky test'):
        shown = self.call('cloud_preview', projectId=project, conversationId=None, text=text)
        self.assertTrue(shown['canSend'], shown)
        self.call('cloud_send', previewId=shown['previewId'])
        return self.call('cloud_runs', projectId=project)['runs'][0]

    # ---------------------------------------------------------------- preview
    def test_preview_names_the_account_repository_branch_and_what_wont_go(self):
        (self.clone / 'local.txt').write_text('x')
        self.git(self.clone, 'add', '-A'); self.git(self.clone, 'commit', '-q', '-m', 'Local only')
        (self.clone / 'README.md').write_text('edited\n')
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.assertEqual((shown['provider'], shown['account'], shown['branch'], shown['remote']),
                         ('claude', 'bryce@example.test', 'main', 'origin'))
        self.assertEqual(shown['repository'], str(self.origin))
        self.assertIn('1 local commit on main isn’t on origin', ' '.join(shown['notes']))
        self.assertIn('1 uncommitted or new file', ' '.join(shown['notes']))
        self.assertIn('Claude’s cloud picks the model', ' '.join(shown['notes']))
        self.assertEqual(self.cli.calls, [])  # a preview never runs a provider CLI

    def test_the_key_check_reads_names_always_and_contents_within_a_budget(self):
        paths = [f'big-{n}.json' for n in range(50)] + ['deep/node_modules/x/.env.local']
        for path in paths:
            (self.clone / path).parent.mkdir(parents=True, exist_ok=True)
            (self.clone / path).write_text('{"a": "' + 'x' * 1000 + '"}')
        ticks = iter(range(10_000))
        found = cloud_runs.secret_files(str(self.clone), paths, clock=lambda: next(ticks))  # every read is "a second"
        self.assertEqual(found, (['deep/node_modules/x/.env.local'], False))
        self.assertLess(next(ticks), 5)  # the content scan stopped at its time budget
        self.assertEqual(cloud_runs.porcelain_paths('R  new name\0old name\0?? a b.txt\0 M c\0'),
                         [('R ', 'new name'), ('??', 'a b.txt'), (' M', 'c')])

    def test_the_name_check_catches_key_files_and_leaves_ordinary_ones(self):
        keys = ['.env', 'app/.env.local', '.env.production', 'deploy/server.pem', 'tls.key', 'id_ed25519', '.netrc',
                'credentials.json', 'config/secrets.yaml', 'client_secret.json']
        fine = ['backend/secret_scan.py', 'test_secrets.py', 'docs/secrets-handling.md', '.env.example', '.env.sample',
                '.env.template', 'keyboard.ts', 'monkey.py']
        self.assertEqual([path for path in keys if not cloud_runs.SECRET_FILE.search(path)], [])
        self.assertEqual([path for path in fine if cloud_runs.SECRET_FILE.search(path)], [])

    def test_preview_names_new_files_that_look_like_keys(self):
        (self.clone / '.env').write_text('PLAIN=1\n')
        (self.clone / 'notes.txt').write_text('ANTHROPIC_API_KEY=sk-ant-api03-' + 'b' * 40 + '\n')
        (self.clone / 'todo.txt').write_text('buy milk\n')
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.assertFalse(shown['canSend'])  # fail closed: Claude may upload the folder
        self.assertIsNone(shown['previewId'])
        self.assertIn('.env, notes.txt look like they hold keys', shown['detail'])
        self.assertNotIn('todo.txt', shown['detail'])
        self.assertIn('3 uncommitted or new files', ' '.join(shown['notes']))
        (self.clone / '.env').unlink(); (self.clone / 'notes.txt').unlink()
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.assertTrue(shown['canSend'])
        (self.clone / 'id_ed25519').write_text('x')  # appears between the preview and the send
        with self.assertRaisesRegex(Rejected, 'id_ed25519 looks like it holds keys'):
            self.call('cloud_send', previewId=shown['previewId'])
        self.assertEqual(self.cli.calls, [])

    def test_a_key_check_that_runs_out_of_time_blocks_the_send(self):
        with patch.object(cloud_runs, 'SECRET_SCAN_SECONDS', -1):
            (self.clone / 'settings.json').write_text('{}')
            shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.assertFalse(shown['canSend'])
        self.assertIn('too many new or changed settings files', shown['detail'])

    def test_a_branch_that_isnt_on_the_remote_is_refused_before_anything_goes(self):
        self.git(self.clone, 'checkout', '-q', '-b', 'local-only')
        with self.assertRaisesRegex(Rejected, 'local-only isn’t on origin yet'):
            self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')

    def test_api_key_accounts_and_codex_without_an_environment_are_refused(self):
        with self.host.store.db:
            self.host.store.db.execute("update profiles set method='api_key' where id='c'")
        with self.assertRaisesRegex(Rejected, 'needs a Claude subscription sign-in'):
            self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        shown = self.call('cloud_preview', projectId='q', conversationId=None, text='Fix it')
        self.assertFalse(shown['canSend'])
        self.assertIsNone(shown['previewId'])
        self.assertIn('environment', shown['detail'])

    def test_fields_are_exact(self):
        for operation, args in (('cloud_runs', {}), ('cloud_runs', {'projectId': 'p', 'x': 1}), ('cloud_send', {'id': 1})):
            with self.assertRaisesRegex(Rejected, 'Unsupported Send to cloud fields'):
                self.call(operation, **args)

    # ---------------------------------------------------------------- Claude
    def test_claude_run_is_unknown_with_its_link_until_a_branch_appears_then_comes_back_as_a_local_branch(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        argv, kwargs = self.cli.calls[0]
        self.assertEqual(argv, [CLAUDE, '--setting-sources', 'user', '--cloud', 'Fix the flaky test'])
        self.assertEqual(kwargs['cwd'], str(self.clone))
        self.assertEqual(kwargs['env'].get('CLAUDE_CONFIG_DIR'), str(self.root / 'store/profiles/c'))
        self.assertEqual(len(self.probed), 1)  # the account was checked before anything was sent
        self.assertEqual((run['state'], run['remoteId'], run['kind']), ('unknown', SESSION, 'cloud'))
        self.assertTrue(run['link'].startswith(f'https://claude.ai/code/{SESSION}'))
        self.assertIn('can’t see its progress', run['line'])
        self.assertTrue(run['canFollowUp'])
        self.assertFalse(run['canBringBack'])

        self.push_cloud_branch('claude/fix-flaky-test-a1b2')
        self.clock.at += 200
        self.call('cloud_check', runId=run['id'])
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        # Even one new branch is offered, not claimed: another session on the repository could have pushed it.
        self.assertEqual((run['state'], run['branch'], run['candidates']), ('ready', None, ['claude/fix-flaky-test-a1b2']))
        self.assertFalse(run['following'])
        with self.assertRaisesRegex(Rejected, 'no result branch yet'):
            self.call('cloud_bring_back', runId=run['id'], branch=None)

        head = self.git(self.clone, 'rev-parse', '--abbrev-ref', 'HEAD')
        status = self.git(self.clone, 'status', '--porcelain')
        self.call('cloud_bring_back', runId=run['id'], branch='claude/fix-flaky-test-a1b2')
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual(run['broughtBack'], 'claude/fix-flaky-test-a1b2')
        self.assertEqual(self.git(self.clone, 'show', 'claude/fix-flaky-test-a1b2:CLOUD.md'), 'cloud')
        self.assertEqual(self.git(self.clone, 'rev-parse', '--abbrev-ref', 'HEAD'), head)  # nothing checked out
        self.assertEqual(self.git(self.clone, 'status', '--porcelain'), status)          # folder untouched
        self.assertFalse(run['canBringBack'])

    def test_a_branch_that_was_there_before_the_launch_isnt_taken_as_the_result(self):
        self.push_cloud_branch('claude/older-run')
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.clock.at += 200
        self.call('cloud_check', runId=run['id'])
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['branch']), ('unknown', None))

    def test_two_new_branches_are_offered_to_pick_never_guessed(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.push_cloud_branch('claude/one')
        self.push_cloud_branch('claude/two')
        self.clock.at += 200
        self.call('cloud_check', runId=run['id'])
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['branch'], run['candidates']), ('ready', None, ['claude/one', 'claude/two']))
        with self.assertRaisesRegex(Rejected, 'Pick one of the branches'):
            self.call('cloud_bring_back', runId=run['id'], branch='main')
        self.call('cloud_bring_back', runId=run['id'], branch='claude/two')
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['branch'], run['broughtBack']), ('claude/two', 'claude/two'))

    def test_a_local_branch_with_the_same_name_is_never_overwritten(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.push_cloud_branch('claude/fix')
        self.git(self.clone, 'branch', 'claude/fix')
        mine = self.git(self.clone, 'rev-parse', 'claude/fix')
        self.clock.at += 200
        self.call('cloud_check', runId=run['id'])
        self.call('cloud_bring_back', runId=run['id'], branch='claude/fix')
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual(run['broughtBack'], 'claude/fix-2')
        self.assertEqual(self.git(self.clone, 'rev-parse', 'claude/fix'), mine)

    def test_a_launch_with_no_session_in_its_output_is_unknown_and_a_timeout_is_never_sent_again(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, 'Starting…\n', '')
        run = self.send()
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('may have started', run['line'])
        self.cli.answers[(CLAUDE, 'start')] = subprocess.TimeoutExpired(CLAUDE, 120)
        run = self.send(text='Second task')
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('won’t send it again', run['line'])
        launches = [c for c in self.cli.calls if c[0][0] == CLAUDE and '-p' not in c[0]]
        self.assertEqual(len(launches), 2)  # one per send, no retries

    def test_a_refusal_is_failed_with_the_cli_words_and_secrets_redacted(self):
        self.cli.answers[(CLAUDE, 'start')] = (1, '', "Error: Cloud sessions are disabled by your organization's policy. "
                                                         'token sk-ant-api03-' + 'a' * 40)
        run = self.send()
        self.assertEqual(run['state'], 'failed')
        self.assertIn('disabled by your organization', run['line'])
        self.assertNotIn('sk-ant-api03-aaaa', run['line'])

    def test_a_different_account_signed_in_means_nothing_is_sent(self):
        self.identity = 'identity_mismatch'
        run = self.send()
        self.assertEqual(run['state'], 'failed')
        self.assertIn('isn’t this one', run['line'])
        self.assertEqual(self.cli.calls, [])

    def test_a_preview_is_used_once(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.call('cloud_send', previewId=shown['previewId'])
        with self.assertRaisesRegex(Rejected, 'out of date'):
            self.call('cloud_send', previewId=shown['previewId'])
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        self.clock.at += cloud_runs.PREVIEW_SECONDS + 1
        with self.assertRaisesRegex(Rejected, 'out of date'):
            self.call('cloud_send', previewId=shown['previewId'])

    def test_follow_up_queues_in_the_claude_session(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.cli.answers[(CLAUDE, 'follow')] = (0, json.dumps({'ok': True, 'session_id': SESSION, 'url': 'https://claude.ai/code/x'}), '')
        self.call('cloud_follow_up', runId=run['id'], text='Also update the docs')
        argv = self.cli.calls[-1][0]
        self.assertEqual(argv, [CLAUDE, '--setting-sources', 'user', '-p', 'Also update the docs', '--cloud', SESSION, '--output-format', 'json'])
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertIn('Follow-up queued', run['line'])
        self.cli.answers[(CLAUDE, 'follow')] = (1, json.dumps({'ok': False, 'session_id': SESSION, 'error': 'archived'}), '')
        self.call('cloud_follow_up', runId=run['id'], text='One more')
        self.assertIn('didn’t take the follow-up', self.call('cloud_runs', projectId='p')['runs'][0]['line'])

    def test_a_launch_left_starting_by_a_closed_host_is_unknown_on_the_next_start(self):
        with self.host.store.db:
            self.host.store.db.execute(
                "insert into cloud_runs (id, project_id, provider, profile_id, task, remote, base_branch, state, created_at, "
               "updated_at) values ('r', 'p', 'claude', 'c', 'x', 'origin', 'main', 'starting', ?, ?)", (self.clock.at, self.clock.at))
        reads = []
        again = CloudRuns(self.host, run=self.cli, terminal=self.cli.terminal, probe=lambda p: {}, claude_path=CLAUDE, clock=self.clock, workers=Pool())
        again._heads = lambda *args: reads.append(args) or {}
        row = again.view(again.row('r'))
        self.assertEqual(row['state'], 'unknown')
        self.assertIn('may have started', row['line'])
        self.assertFalse(row['following'])
        self.clock.at += 1000
        again.check('r', force=False)
        self.assertEqual((self.cli.calls, reads), ([], []))  # without the launch's branch list there's nothing to read

    def test_an_older_cloud_branch_moving_after_the_launch_isnt_taken_as_the_result(self):
        self.push_cloud_branch('claude/older-run')
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        other = self.root / 'cloud-claude-older-run'
        (other / 'CLOUD.md').write_text('moved\n')
        self.git(other, '-c', 'user.name=Cloud', '-c', 'user.email=cloud@example.test', 'commit', '-q', '-am', 'Moved')
        self.git(other, 'push', '-q', 'origin', 'claude/older-run')
        self.clock.at += 200
        self.call('cloud_check', runId=run['id'])
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['branch'], run['candidates']), ('unknown', None, []))

    def test_a_run_that_never_started_is_never_checked_into_ready(self):
        self.identity = 'identity_mismatch'
        run = self.send()
        self.assertEqual(run['state'], 'failed')
        self.assertTrue(run['line'].startswith('Didn’t start'))
        self.assertFalse(run['canCheck'])
        self.push_cloud_branch('claude/someone-else')
        self.clock.at += 200
        with self.assertRaisesRegex(Rejected, 'nothing more to check'):
            self.call('cloud_check', runId=run['id'])
        self.cloud.tick()
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['branch']), ('failed', None))
        self.assertIsNone(self.cloud.row(run['id'])['known_heads'])

    def test_an_error_before_anything_is_sent_is_failed_not_unknown(self):
        def broken(profile):
            raise RuntimeError('probe broke')
        self.cloud._probe = broken
        self.cloud.probe = broken
        run = self.send()
        self.assertEqual(run['state'], 'failed')
        self.assertIn('Nothing was sent', run['line'])
        self.assertEqual(self.cli.calls, [])

    def test_a_cli_that_crashes_after_making_the_session_keeps_its_link(self):
        self.cli.answers[(CLAUDE, 'start')] = (139, CLAUDE_SENT + 'Segmentation fault\n', '')
        run = self.send()
        self.assertEqual((run['state'], run['remoteId']), ('unknown', SESSION))
        self.assertTrue(run['link'].startswith(CLAUDE_LINK))

    def test_an_unexplained_nonzero_exit_is_unknown_not_failed(self):
        self.cli.answers[(CLAUDE, 'start')] = (1, '', 'Error: 502 Bad Gateway')
        run = self.send()
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('may have started', run['line'])
        self.assertIn('502', run['line'])

    def test_every_write_moves_the_conversations_row(self):
        import conversation_activity
        bumped = []
        self.enterContext(patch.object(conversation_activity, 'bump', lambda db, thread: bumped.append(thread), create=True))
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        shown = self.call('cloud_preview', projectId='p', conversationId=None, text='Fix it')
        with patch.object(self.cloud, 'binding', wraps=self.cloud.binding) as binding:
            binding.side_effect = lambda project, conversation: {**CloudRuns.binding(self.cloud, project, None)}
            shown_for = dict(self.cloud.previews[shown['previewId']], conversation_id='t1')
            self.cloud.previews[shown['previewId']] = shown_for
            self.call('cloud_send', previewId=shown['previewId'])
            self.cloud.tick()
        self.assertGreaterEqual(bumped.count('t1'), 2)  # the insert, then the launch's outcome

    def test_a_task_or_follow_up_starting_with_a_dash_is_refused(self):
        with self.assertRaisesRegex(Rejected, 'not a dash'):
            self.call('cloud_preview', projectId='p', conversationId=None, text='--dangerously-skip-permissions')
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        with self.assertRaisesRegex(Rejected, 'not a dash'):
            self.call('cloud_follow_up', runId=run['id'], text='-x')

    def test_a_follow_up_while_the_run_is_busy_is_refused_not_dropped(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.cloud.busy.add(run['id'])
        with self.assertRaisesRegex(Rejected, 'checking this run right now'):
            self.call('cloud_follow_up', runId=run['id'], text='Also the docs')
        self.assertEqual([c for c in self.cli.calls if '-p' in c[0]], [])

    def test_a_run_whose_conversation_moved_to_another_account_isnt_followed_as_that_account(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.host.store.add_profile('c2', 'claude', self.root / 'store/profiles/c2', 'other@example.test', 'personal')
        with self.host.store.db:
            self.host.store.db.execute("update projects set profile_id='c2' where id='p'")
        for operation, args in (('cloud_follow_up', {'text': 'more'}), ('cloud_check', {})):
            with self.assertRaisesRegex(Rejected, 'another account now'):
                self.call(operation, runId=run['id'], **args)
        self.assertEqual(len([c for c in self.cli.calls if c[0][0] == CLAUDE]), 1)

    def test_a_run_quiet_for_days_isnt_followed_on_its_own(self):
        self.cli.answers[(CLAUDE, 'start')] = (0, CLAUDE_SENT, '')
        run = self.send()
        self.clock.at += cloud_runs.FOLLOW_DAYS * 86400 + 1
        self.push_cloud_branch('claude/late')
        self.cloud.tick()
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['branch']), ('unknown', None))
        self.assertIn('stopped checking', run['line'])
        self.assertFalse(run['following'])  # the page stops polling it too
        self.call('cloud_check', runId=run['id'])  # Check now still looks
        run = self.call('cloud_runs', projectId='p')['runs'][0]
        self.assertEqual((run['state'], run['candidates']), ('ready', ['claude/late']))

    # ---------------------------------------------------------------- Codex
    def codex_send(self):
        self.call('cloud_environment_set', projectId='q', environment='env_backend')
        self.cli.answers[(CODEX, 'exec')] = (0, f'https://chatgpt.com/codex/tasks/{CODEX_TASK}\n', '')
        return self.send(project='q')

    def codex_list(self, status, summary=None):
        tasks = [{'id': 'task_e_other', 'status': 'ready'},
                 {'id': CODEX_TASK, 'url': f'https://chatgpt.com/codex/tasks/{CODEX_TASK}', 'title': 'Fix the flaky test',
                  'status': status, 'updated_at': '2026-10-08T14:00:00Z', 'environment_id': 'env_backend',
                  'summary': summary or {'files_changed': 1, 'lines_added': 1, 'lines_removed': 0}, 'attempt_total': 1}]
        self.cli.answers[(CODEX, 'list')] = (0, json.dumps({'tasks': tasks, 'cursor': None}), '')

    def test_codex_run_follows_its_status_from_the_list(self):
        run = self.codex_send()
        argv = self.cli.calls[0][0]
        self.assertEqual(argv, [CODEX, 'cloud', 'exec', '--env', 'env_backend', '--branch', 'main', '--', 'Fix the flaky test'])
        self.assertEqual(self.cli.calls[0][1]['env'].get('CODEX_HOME'), str(self.root / 'store/profiles/x'))
        self.assertEqual((run['state'], run['remoteId']), ('working', CODEX_TASK))
        self.codex_list('pending')
        self.clock.at += 31
        run = self.call('cloud_runs', projectId='q')['runs'][0]  # the read starts a check that's due
        run = self.call('cloud_runs', projectId='q')['runs'][0]
        self.assertEqual(run['state'], 'working')
        self.assertEqual(self.cli.calls[-1][0], [CODEX, 'cloud', 'list', '--json', '--limit', '20', '--env', 'env_backend'])
        self.codex_list('READY')
        self.clock.at += 31
        self.call('cloud_runs', projectId='q')
        run = self.call('cloud_runs', projectId='q')['runs'][0]
        self.assertEqual((run['state'], run['summary']), ('ready', '1 files, 1 added, 0 removed'))
        self.assertTrue(run['canBringBack'])

    def test_codex_status_goes_unknown_when_checks_fail_and_time_passes(self):
        run = self.codex_send()
        self.cli.answers[(CODEX, 'list')] = (1, '', 'network down')
        self.clock.at += cloud_runs.STALE_SECONDS + 1
        self.call('cloud_runs', projectId='q')
        run = self.call('cloud_runs', projectId='q')['runs'][0]
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('doesn’t know where this run is', run['line'])

    def test_codex_follow_up_is_refused_with_where_to_go(self):
        run = self.codex_send()
        with self.assertRaisesRegex(Rejected, 'chatgpt.com/codex'):
            self.call('cloud_follow_up', runId=run['id'], text='more')

    def test_codex_result_without_a_branch_comes_back_from_its_diff_without_touching_the_folder(self):
        run = self.codex_send()
        self.codex_list('ready')
        self.clock.at += 31
        self.call('cloud_check', runId=run['id'])
        self.cli.answers[(CODEX, 'diff')] = (0, DIFF, '')
        status = self.git(self.clone, 'status', '--porcelain')
        self.call('cloud_bring_back', runId=run['id'], branch=None)
        run = self.call('cloud_runs', projectId='q')['runs'][0]
        local = f'codex/cloud-{CODEX_TASK[-12:]}'
        self.assertEqual(run['broughtBack'], local, run['line'])
        self.assertEqual(self.git(self.clone, 'show', f'{local}:README.md'), 'hello\nfrom the cloud')
        self.assertEqual(self.git(self.clone, 'status', '--porcelain'), status)
        self.assertEqual(self.git(self.clone, 'worktree', 'list', '--porcelain').count('worktree '), 1)  # scratch removed

    def test_a_codex_task_off_the_list_after_every_page_says_so(self):
        run = self.codex_send()

        def pages(argv):
            if '--cursor' in argv:
                return 0, json.dumps({'tasks': [{'id': 'task_e_older', 'status': 'ready'}], 'cursor': None}), ''
            return 0, json.dumps({'tasks': [{'id': 'task_e_newer', 'status': 'pending'}], 'cursor': 'next'}), ''
        self.cli.answers[(CODEX, 'list')] = pages
        self.clock.at += 31
        self.call('cloud_check', runId=run['id'])
        run = self.call('cloud_runs', projectId='q')['runs'][0]
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('don’t include this one', run['line'])
        lists = [c for c in self.cli.calls if c[0][:3] == [CODEX, 'cloud', 'list']]
        self.assertEqual(lists[-1][0][-2:], ['--cursor', 'next'])
        self.assertNotEqual(lists[-1][1]['cwd'], str(self.clone))  # a read never runs in the project's folder

    def test_environment_ids_are_checked(self):
        with self.assertRaisesRegex(Rejected, 'Codex environment id'):
            self.call('cloud_environment_set', projectId='q', environment='bad id; rm -rf')
        self.assertEqual(self.call('cloud_environment_set', projectId='q', environment=None), {'environment': None})

    # ---------------------------------------------------------------- parsing
    def test_parsers(self):
        self.assertEqual(cloud_runs.parse_claude_start(json.dumps({'ok': True, 'session_id': SESSION, 'url': 'https://claude.ai/code/' + SESSION})),
                         (SESSION, 'https://claude.ai/code/' + SESSION))
        self.assertEqual(cloud_runs.parse_claude_start('\x1b[32m✓\x1b[0m View: https://claude.ai/code/' + SESSION + '?from=cli')[0], SESSION)
        self.assertEqual(cloud_runs.parse_claude_start('nothing here'), (None, None))
        self.assertEqual(cloud_runs.parse_codex_start(f'https://chatgpt.com/codex/tasks/{CODEX_TASK}?x=1\n')[0], CODEX_TASK)
        self.assertEqual(cloud_runs.parse_codex_list(json.dumps([{'id': 'a', 'status': 'error', 'summary': 'x'}]))['a']['summary'], 'x')
        with self.assertRaises(ValueError):
            cloud_runs.parse_codex_list('{"nope": 1}')


if __name__ == '__main__':
    unittest.main()
