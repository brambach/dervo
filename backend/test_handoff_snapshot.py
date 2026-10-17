"""The handoff that carries the work (23 Sep review, bet 3): the snapshot Dervo saves before another agent carries on
from uncommitted files, the brief that agent gets, the confirm, and the restore.

Real temporary git repositories and a real Host; the providers are scripted doubles. Nothing here touches a real project
or Bryce's data, or calls a model. The fixtures run git themselves to set a repository up (a commit, a stash entry, a
staged change); the code under test never runs git stash, reset, checkout or clean.
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import handoff_snapshot as hs
import platform_os
from dervo_connections import Rejected
from host import Host, ACTOR
import test_conversation_coding as base
import thread_folders

GIT = platform_os.git_executable()
# Git for Windows needs SystemRoot and its own PATH to start, which a bare Unix PATH leaves out (every test here stopped
# in setUp on Windows with WinError 2).
GIT_ENV = platform_os.provider_environment() if platform_os.WINDOWS else {'PATH': '/usr/bin:/bin'}
SAMPLE = base.SAMPLE


def remove_repository(folder):
    """shutil.rmtree for a .git folder. Git for Windows makes its objects read-only, and Windows won't delete those."""
    def writable(remove, path, _):
        os.chmod(path, stat.S_IWRITE)
        remove(path)
    shutil.rmtree(folder, onexc=writable)


CLAUDE_EDIT = 'def farewell(): return "bye"\n'
CLAUDE_NOTE = '- farewell needs a test\n'  # what Claude adds to notes.md, when a test asks for a second file


def git(cwd, *args, text=False):
    result = subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=text,
                            # No opportunistic index refresh: the fixture's own git status must not write the index it checks.
                            env={**GIT_ENV, 'HOME': str(cwd), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_OPTIONAL_LOCKS': '0'})
    if result.returncode != 0:
        raise AssertionError(f'git {args} failed: {result.stderr}')
    return result.stdout


def make_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, 'init', '-q')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.test')
    git(root, 'config', 'commit.gpgsign', 'false')
    return root


def commit_all(root, message='fixture'):
    git(root, 'add', '-A')
    git(root, 'commit', '-q', '-m', message)


def pinned(repo):
    """What a snapshot must never move: git status, the index file's bytes, the stash list, HEAD and every ref that
    isn't Dervo's own."""
    index = (repo / '.git' / 'index').read_bytes()
    return dict(index=index, status=git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all'), stash=git(repo, 'stash', 'list'),
                head=git(repo, 'rev-parse', 'HEAD'),
                refs=[line for line in git(repo, 'for-each-ref').splitlines() if b'refs/dervo/' not in line])


def repo_trace(repo):
    """Everything a write into git would leave: every ref (Dervo's too), the object counts, and the loose object and
    pack files themselves."""
    objects = sorted(str(p.relative_to(repo / '.git' / 'objects')) for p in (repo / '.git' / 'objects').rglob('*') if p.is_file())
    return dict(refs=git(repo, 'for-each-ref'), counts=git(repo, 'count-objects', '-v'), objects=objects)


def files(root):
    """Every file and link under ``root`` except .git: {path: (kind, bytes or target, permission bits)}."""
    found = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != '.git' and not os.path.islink(os.path.join(folder, d))]
        for name in names + [d for d in os.listdir(folder) if os.path.islink(os.path.join(folder, d)) and os.path.isdir(os.path.join(folder, d))]:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root)
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                found[rel] = ('link', os.readlink(path), None)
            else:
                found[rel] = ('file', Path(path).read_bytes(), stat.S_IMODE(info.st_mode))
    return found


class SnapshotTests(unittest.TestCase):
    """The snapshot and restore themselves, in real repositories."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.repo = make_repo(self.base / 'repo')
        self.data = self.base / 'dervo-data' / 'task-1'
        (self.repo / 'a.py').write_text('a = 1\n')
        (self.repo / 'gone.py').write_text('bye\n')
        (self.repo / 'bin.dat').write_bytes(bytes(range(256)))
        (self.repo / 'with space.txt').write_text('first\n')
        (self.repo / 'staged.txt').write_text('before\n')
        (self.repo / 'sub').mkdir()
        (self.repo / 'sub' / 'inner.py').write_text('inner\n')
        (self.repo / 'run.sh').write_text('#!/bin/sh\necho hi\n')
        os.chmod(self.repo / 'run.sh', 0o755)
        commit_all(self.repo)
        # Something already on the stash list, so "the list is unchanged" can't pass by being empty.
        (self.repo / 'a.py').write_text('stashed earlier\n')
        git(self.repo, 'stash', 'push', '-q', '-m', 'yours, from before')

    def leave_work(self):
        """What a stopped run typically leaves: edits, a deletion, a changed binary, a new file in a new folder, a file
        with a space in its name, a staged change and an untracked link."""
        (self.repo / 'a.py').write_text('a = 1\nb = 2\n')
        (self.repo / 'gone.py').unlink()
        (self.repo / 'bin.dat').write_bytes(bytes(range(255, -1, -1)) + b'\0tail')
        (self.repo / 'with space.txt').write_text('second\n')
        (self.repo / 'new dir').mkdir()
        (self.repo / 'new dir' / 'n.txt').write_text('new file\n')
        (self.repo / 'staged.txt').write_text('after\n')
        git(self.repo, 'add', 'staged.txt')
        os.symlink('a.py', self.repo / 'link-to-a')

    def test_the_snapshot_moves_nothing_and_restore_puts_every_byte_back(self):
        self.leave_work()
        before, left = pinned(self.repo), files(self.repo)
        self.assertIn(b'yours, from before', before['stash'])
        record = hs.snapshot(self.repo, self.data, 'task-1')
        self.assertEqual(pinned(self.repo), before, 'status, the index bytes, the stash list, HEAD and refs are untouched')
        self.assertEqual(files(self.repo), left, 'no file in the folder changed')
        self.assertEqual(git(self.repo, 'rev-parse', 'refs/dervo/handoff/task-1', text=True).strip(), record['commit'])
        parents = git(self.repo, 'rev-list', '--parents', '-n', '1', record['commit'], text=True).split()[1:]
        self.assertEqual(parents[0], before['head'].decode().strip(), 'stash-shaped: HEAD first, then the index commit')
        self.assertEqual(git(self.repo, 'rev-parse', f"{parents[1]}^{{tree}}", text=True).strip(), record['index_tree'])
        self.assertEqual(set(record['tracked']), {'a.py', 'gone.py', 'bin.dat', 'with space.txt', 'staged.txt'})
        self.assertEqual(set(record['untracked']), {'new dir/n.txt', 'link-to-a'})
        self.assertEqual(record['untracked']['link-to-a'], dict(kind='link', target='a.py'))
        copy = self.data / 'files' / record['untracked']['new dir/n.txt']['sha256']
        self.assertEqual(copy.read_bytes(), b'new file\n', 'the untracked file is copied into Dervo’s folder')
        self.assertNotIn(b'new file', git(self.repo, 'cat-file', '-p', f"{record['commit']}^{{tree}}"), 'and not into git')
        self.assertEqual(hs.identity(self.repo, self.data), record['identity'])

        # The next agent goes wrong: rewrites, recreates, deletes, adds, and flips a mode.
        (self.repo / 'a.py').write_text('broken\n')
        (self.repo / 'gone.py').write_text('back again\n')
        (self.repo / 'new dir' / 'n.txt').unlink()
        (self.repo / 'bin.dat').write_bytes(b'\x00\x01')
        (self.repo / 'extra.txt').write_text('made later\n')
        (self.repo / 'later').mkdir()
        (self.repo / 'later' / 'deep.txt').write_text('deep\n')
        os.chmod(self.repo / 'run.sh', 0o644)
        (self.repo / 'sub' / 'inner.py').write_text('inner changed\n')
        os.unlink(self.repo / 'link-to-a')
        os.symlink('elsewhere', self.repo / 'link-to-a')
        self.assertNotEqual(hs.identity(self.repo, self.data), record['identity'])

        snap = hs.Snapshot(self.repo, record, self.data)
        paths = [path for path, _now, _then in snap.differences()]
        self.assertEqual(paths, ['a.py', 'bin.dat', 'extra.txt', 'gone.py', 'later/deep.txt', 'link-to-a', 'new dir/n.txt', 'run.sh', 'sub/inner.py'])
        written, removed = hs.restore(self.repo, snap, paths)
        self.assertEqual(sorted(removed), ['extra.txt', 'gone.py', 'later/deep.txt'])
        self.assertEqual(files(self.repo), left, 'every byte, link and mode is back')
        self.assertFalse((self.repo / 'later').exists(), 'a folder only the later agent made goes too')
        self.assertEqual(pinned(self.repo), before, 'git sees the folder exactly as at the snapshot')
        self.assertEqual(hs.Snapshot(self.repo, record, self.data).differences(), [])

    def test_git_stash_create_would_have_rewritten_the_index(self):
        """Why the snapshot builds its commits on a copy of the index: ``git stash create`` refreshes and writes the real
        one. Control for the byte-for-byte check above."""
        self.leave_work()
        os.utime(self.repo / 'sub' / 'inner.py', (time.time() + 30, time.time() + 30))  # stat-dirty, content the same
        before = (self.repo / '.git' / 'index').read_bytes()
        git(self.repo, 'stash', 'create')
        self.assertNotEqual((self.repo / '.git' / 'index').read_bytes(), before)
        os.utime(self.repo / 'sub' / 'inner.py', (time.time() + 60, time.time() + 60))
        before = (self.repo / '.git' / 'index').read_bytes()
        hs.snapshot(self.repo, self.data, 'task-1')
        self.assertEqual((self.repo / '.git' / 'index').read_bytes(), before)

    def test_a_folder_outside_git_is_refused_and_nothing_is_written(self):
        plain = self.base / 'plain'
        plain.mkdir()
        (plain / 'a.py').write_text('a\n')
        with self.assertRaisesRegex(Rejected, 'isn’t in a git repository'):
            hs.snapshot(plain, self.data, 'task-1')
        self.assertFalse(self.data.exists())
        self.assertEqual(os.listdir(plain), ['a.py'])

    def test_a_project_folder_inside_a_larger_repository_keeps_to_itself(self):
        (self.repo / 'a.py').write_text('outside the project\n')
        (self.repo / 'sub' / 'inner.py').write_text('inside\n')
        (self.repo / 'sub' / 'fresh.txt').write_text('fresh\n')
        project = self.repo / 'sub'
        record = hs.snapshot(project, self.data, 'task-1')
        self.assertEqual((record['prefix'], set(record['tracked']), set(record['untracked'])), ('sub/', {'inner.py'}, {'fresh.txt'}))
        (project / 'inner.py').write_text('later\n')
        (self.repo / 'a.py').write_text('outside, later\n')
        snap = hs.Snapshot(project, record, self.data)
        self.assertEqual([p for p, _n, _t in snap.differences()], ['inner.py'])
        hs.restore(project, snap, ['inner.py'])
        self.assertEqual((project / 'inner.py').read_text(), 'inside\n')
        self.assertEqual((self.repo / 'a.py').read_text(), 'outside, later\n', 'nothing outside the project folder is restored')

    def test_a_restore_never_writes_through_a_linked_folder(self):
        self.leave_work()
        record = hs.snapshot(self.repo, self.data, 'task-1')
        outside = self.base / 'outside'
        outside.mkdir()
        shutil.rmtree(self.repo / 'new dir')
        os.symlink(outside, self.repo / 'new dir')
        snap = hs.Snapshot(self.repo, record, self.data)
        with self.assertRaisesRegex(Rejected, 'can’t restore new dir/n.txt safely'):
            hs.restore(self.repo, snap, ['a.py', 'new dir/n.txt'])
        self.assertEqual(os.listdir(outside), [], 'nothing was written through the link')
        self.assertEqual((self.repo / 'a.py').read_text(), 'a = 1\nb = 2\n', 'and nothing else either')

    def test_a_long_diff_is_cut_and_says_so(self):
        (self.repo / 'a.py').write_text(''.join(f'line {n}\n' for n in range(5000)))
        record = hs.snapshot(self.repo, self.data, 'task-1')
        item, = hs.changes(self.repo, record, self.data, ['a.py'])
        self.assertEqual((item['change'], item['added'], item['removed']), ('changed', 5000, 1))
        self.assertLess(len(item['diff']), hs.FILE_DIFF_LIMIT + 100)
        self.assertIn('[Dervo cut this file’s diff here:', item['diff'])

    def test_limits_refuse_instead_of_saving_half_a_folder(self):
        (self.repo / 'big.bin').write_bytes(b'\0' * 16)
        with patch.object(hs, 'UNTRACKED_FILE_LIMIT', 8):
            with self.assertRaisesRegex(Rejected, 'big.bin is too big'):
                hs.snapshot(self.repo, self.data, 'task-1')
        self.assertEqual(git(self.repo, 'for-each-ref', 'refs/dervo/'), b'', 'no ref was written')


class HandoffFlowTests(unittest.TestCase):
    """Continue in Codex after Claude stopped at its usage limit with a changed file: the confirm, the send, the brief the
    next run gets, and the restore. A real Host on a real git repository; both providers are scripted."""
    provider = 'claude'
    other = 'codex'
    settle = base.ConversationCodingTests.settle
    finished = staticmethod(base.ConversationCodingTests.finished)
    peer_patch = base.ConversationCodingTests.peer_patch

    def setUp(self):
        # The beta-access gate isn't what's under test here (as in test_handoff_after_stop). Without this, a source
        # checkout carries runtime/dervo-account-public.json and every flow test stopped at "beta access needs confirmation".
        self.enterContext(patch('account_config.create', lambda *args, **kwargs: None))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.addCleanup(lambda: self.host.close())
        # Beta.18: these tests are about a thread in the project folder, as in beta.17. Own branches are
        # test_thread_folders'.
        self.enterContext(patch.object(thread_folders, 'pick', return_value=('project', 'explore')))
        self.project = make_repo(self.base / 'project')
        (self.project / 'test_sample.py').write_text(SAMPLE)
        (self.project / 'greeting.py').write_text('def greet(): return "hi"\n')
        (self.project / 'notes.md').write_text('# notes\n')
        commit_all(self.project)
        self.host.store.add_profile('p', 'claude', self.base / 'store/profiles/p', 'personal@example.test', 'personal')
        self.host.store.add_profile('q', 'codex', self.base / 'store/profiles/q', 'synthetic@example.test', 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.host.store.add_thread('d', 'p', 'd', 'shared')
        with self.host.store.db:
            self.host.store.db.execute("INSERT INTO local_channels VALUES ('c', 'Farewell')")  # the other provider's account is known from a thread that used it
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='q', model='synthetic-model' WHERE id='d'")
        self.scope = dict(projectId='p', channelId='c')
        self.peers, self.starts = [], 0
        self.target, self.appendix = 'greeting.py', '# codex carried on\n'
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'notes.md', 'test_sample.py'], 'testFile': 'test_sample.py'})
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})

    def stopping_claude(self, also=False):
        """Claude reads greeting.py, adds a farewell, reports its step and next step, runs the check, then hits its
        usage limit before it can answer. With ``also`` it adds a line to notes.md too, before the guide."""
        from test_claude_coding import SyntheticClaudePeer, verified_auth, CLAUDE
        import claude_coding
        from contextlib import ExitStack
        test = self

        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)

            def on_prompt(peer):
                peer.tool('dervo_read_file', {'path': 'greeting.py'}, 1)

            def on_tool_result(peer, mid, result):
                text = result['content'][0]['text']
                if mid == 1:
                    body = json.loads(text)
                    peer.tool('dervo_write_file', dict(path='greeting.py', expected_hash=body['sha256'], content=body['content'] + CLAUDE_EDIT), 2)
                elif mid == 2 and also:
                    peer.tool('dervo_read_file', {'path': 'notes.md'}, 5)
                elif mid == 5:
                    body = json.loads(text)
                    peer.tool('dervo_write_file', dict(path='notes.md', expected_hash=body['sha256'], content=body['content'] + CLAUDE_NOTE), 6)
                elif mid in (2, 6):
                    peer.tool('dervo_update_task_guide', dict(goal='Add a farewell', step='Writing the farewell', why='You asked for one.',
                                                              next='Add a test for farewell in test_sample.py.', test_steps='Run the tests.',
                                                              limits='The test for farewell isn’t written yet.'), 4)
                elif mid == 4:
                    peer.tool('dervo_run_tests', {}, 3)
                elif mid == 3:
                    peer.api_error('Claude AI usage limit reached')
                    peer.finish(ok=False, text='Claude AI usage limit reached', code=1, spoke=False)
        stack = ExitStack()
        stack.enter_context(patch.object(claude_coding, 'auth_status', verified_auth))
        stack.enter_context(patch.object(claude_coding, 'executable', lambda: CLAUDE))
        stack.enter_context(patch.object(claude_coding, 'OwnedProcess', Peer))
        return stack

    def stop_with_changes(self, also=False):
        with self.stopping_claude(also):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'Add a farewell to greeting.py'})
            detail = self.settle(started['taskId'])
        task = detail['task']
        self.assertEqual((task['state'], task['runs'][-1]['state'], task['suggest']['other']['profile_id']), ('blocked', 'failed', 'q'))
        self.assertTrue(task['runs'][-1]['provider_failure'])
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + CLAUDE_EDIT)
        return task

    def thread_account(self):
        return self.host.store.db.execute("SELECT profile_id FROM threads WHERE id='c'").fetchone()[0]

    def tasks(self):
        return self.host.dispatch('tasks', self.scope)

    def carry_on(self, task, handoff_id):
        with self.peer_patch('success', provider='codex'):
            result = self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': handoff_id})
            return self.settle(result['taskId'])

    def test_continue_after_a_stop_with_changed_files_shows_the_brief_saves_a_snapshot_and_carries_the_work_on(self):
        task = self.stop_with_changes()
        # Negative control: the rule Continue used until today refuses this exact record.
        evidence = self.host.coding.evidence.read(task['id'], thread_id='c', project_id='p', actor=ACTOR)
        self.assertFalse(self.host.coding.retry_status(task, evidence)['available'])
        # And a Continue that skips the confirm still sends nothing.
        with self.assertRaisesRegex(Rejected, 'Open Continue in Codex first to see what it picks up. Nothing was sent.'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id']})
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'))

        before, trace = pinned(self.project), repo_trace(self.project)
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual(pinned(self.project), before, 'the confirm moved nothing')
        self.assertEqual(repo_trace(self.project), trace, 'and wrote nothing into git: no ref, no object')
        self.assertEqual((preview['here'], preview['to'], preview['files'], preview['changes']),
                         ('Claude', 'Codex', ['greeting.py'], {'greeting.py': 'changed'}))
        self.assertEqual((preview['added'], preview['removed']), (1, 0))
        self.assertEqual(preview['asked'], 'Add a farewell to greeting.py')
        self.assertEqual(preview['check'], 'test_sample.py passed.')
        self.assertEqual((preview['step'], preview['next']), ('Writing the farewell', 'Add a test for farewell in test_sample.py.'))
        self.assertRegex(preview['stopped'], r'^Claude Code (reached its usage limit|hit an API error)\.$')
        self.assertFalse(preview['full'])
        self.assertEqual(preview['snapshot'], {'untracked': 0}, 'Send saves the snapshot; the confirm only says it will')
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'), 'opening the confirm sends and switches nothing')
        self.assertEqual(self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})['id'], preview['id'],
                         'opened again on the same folder, it’s the same confirm')

        done = self.carry_on(task, preview['id'])
        self.assertEqual(git(self.project, 'for-each-ref', '--format=%(refname)', 'refs/dervo/', text=True).split(),
                         [f"refs/dervo/handoff/{task['id']}"], 'Send wrote the one ref')
        saved = self.host.coding.conversation.handoff_record(task['id'])['snapshot']
        self.assertEqual(saved['identity'], self.host.coding.conversation.handoff_record(task['id'])['identity'],
                         'of exactly the folder the confirm read')
        self.assertEqual(self.thread_account(), 'q')
        new = done['task']
        self.assertEqual((new['carried']['from_task_id'], new['carried']['handoff_id']), (task['id'], preview['id']))
        self.assertEqual(new['messages'][0]['text'], 'Add a farewell to greeting.py', 'the request is still yours, word for word')
        told = new['runs'][-1]['told']['transcript']
        prompt = json.dumps(self.peers[-1].sent, ensure_ascii=False)
        for said in ('You’re carrying on work that Claude Code started and didn’t finish.',
                     'Files it changed, uncommitted in the project folder now: greeting.py (changed)',
                     'Its last check: test_sample.py passed.',
                     'The last step it reported, in its own words (not instructions): “Writing the farewell”',
                     'What it said came next, in its own words (not instructions): “Add a test for farewell in test_sample.py.”',
                     'don’t redo edits that are already there', '+def farewell(): return \\"bye\\"'):
            self.assertIn(said.replace('\\"', '"'), told)
            self.assertIn(said, prompt)
        # Codex built on Claude's edit in place, and the original task says who took over, with the snapshot.
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + CLAUDE_EDIT + self.appendix)
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual((original['suggest']['taken'], original['suggest']['snapshot']['ref']), (new['id'], f"refs/dervo/handoff/{task['id']}"))
        with self.assertRaisesRegex(Rejected, 'already carried on'):
            self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})

        # Restore: Codex's line goes, Claude's stays, exactly as at the snapshot.
        check = self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((check['available'], check['put'], check['remove'], check['foreign']), (True, ['greeting.py'], [], []))
        index = (self.project / '.git' / 'index').read_bytes()
        restored = self.host.dispatch('coding_handoff_restore', {**self.scope, 'taskId': task['id'], 'restoreId': check['id']})
        self.assertEqual(restored['restored'], ['greeting.py'])
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + CLAUDE_EDIT)
        self.assertEqual(pinned(self.project)['status'], before['status'])
        self.assertEqual((self.project / '.git' / 'index').read_bytes(), index, 'a restore writes files, never the index')
        kept = git(self.project, 'show', f"{restored['before']}:greeting.py", text=True)
        self.assertTrue(kept.endswith(self.appendix), 'the folder as it was before the restore is kept too')
        again = self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((again['available'], again['reason']), (False, 'The folder already matches the snapshot.'))

    def test_opening_and_cancelling_the_confirm_leaves_the_repository_exactly_as_it_was(self):
        task = self.stop_with_changes()
        (self.project / 'draft.txt').write_text('an untracked file the run left\n')  # Send would copy this one
        before, trace = pinned(self.project), repo_trace(self.project)
        for _ in range(2):  # open, Cancel, open again: Cancel sends nothing, so the host hears nothing more
            preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual(repo_trace(self.project), trace, 'git for-each-ref and git count-objects -v read the same')
        self.assertEqual(preview['snapshot'], {'untracked': 1})
        self.assertEqual(pinned(self.project), before)
        self.assertFalse((self.base / 'store' / 'handoff' / task['id']).exists(), 'and nothing was copied into Dervo’s folder')
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'))
        # Control: what the confirm did until today, a snapshot on open, shows up in exactly this comparison.
        hs.snapshot(self.project, self.base / 'control', task['id'])
        changed = repo_trace(self.project)
        self.assertNotEqual(changed['refs'], trace['refs'])
        self.assertNotEqual(changed['counts'], trace['counts'])

    def test_the_snapshot_is_taken_at_send_and_refused_if_the_folder_moved_in_between(self):
        import handoff_snapshot
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        record = self.host.coding.conversation.handoff_record(task['id'])
        trace = repo_trace(self.project)
        # The folder changes after Send's read-only check passed and before the snapshot: the snapshot's own identity
        # catches it, and the ref is never written.
        (self.project / 'notes.md').write_text('# notes\nsaved in the gap\n')
        with patch.object(handoff_snapshot, 'identity', lambda *_a, **_k: record['identity']):
            with self.assertRaisesRegex(Rejected, 'Files in the project changed after you opened this, so nothing was sent'):
                self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(repo_trace(self.project)['refs'], trace['refs'], 'no ref')
        self.assertFalse((self.base / 'store' / 'handoff' / task['id']).exists(), 'no copies kept')
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'))

    def test_a_send_that_starts_nothing_takes_its_snapshot_back(self):
        import conversation_coding
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        trace = repo_trace(self.project)

        def refuse(*_a, **_k):
            raise Rejected('Coding permission for this project is no longer current.')
        with patch.object(conversation_coding.ConversationCoding, 'request', refuse):
            with self.assertRaisesRegex(Rejected, 'no longer current'):
                self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(repo_trace(self.project)['refs'], trace['refs'], 'the ref Send wrote is gone again')
        self.assertIsNone(self.host.coding.conversation.handoff_record(task['id'])['snapshot'])
        done = self.carry_on(task, preview['id'])
        self.assertTrue(done['task']['carried']['snapshot'], 'the same confirm sends once things are right')

    def test_a_folder_that_changed_after_the_confirm_opened_sends_nothing(self):
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        (self.project / 'notes.md').write_text('# notes\nyours, typed while the confirm was open\n')
        trace = repo_trace(self.project)
        with self.assertRaisesRegex(Rejected, 'Files in the project changed after you opened this, so nothing was sent'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(repo_trace(self.project), trace, 'a refused Send writes nothing into git either')
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'))
        again = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertNotEqual(again['id'], preview['id'])
        self.assertEqual(again['also'], ['notes.md'], 'your own change is named, apart from the run’s')
        with self.assertRaisesRegex(Rejected, 'This handoff changed after you opened it'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})

    def test_a_folder_outside_git_keeps_todays_refusal(self):
        task = self.stop_with_changes()
        remove_repository(self.project / '.git')
        for op, args in (('coding_handoff_preview', {}), ('coding_retry_on', {})):
            with self.assertRaisesRegex(Rejected, 'Claude left changed files, and this folder isn’t a git repository'):
                self.host.dispatch(op, {**self.scope, 'taskId': task['id'], **args})
        self.assertEqual((len(self.tasks()), self.thread_account()), (1, 'p'))
        self.assertFalse((self.base / 'store' / 'handoff' / task['id']).exists(), 'nothing saved for a folder Dervo can’t snapshot')

    def test_an_automatic_switch_never_carries_changed_files_on(self):
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        task = self.stop_with_changes()
        for _ in range(20):
            self.host.tick()
        tasks = self.tasks()
        self.assertEqual(len(tasks), 1, 'nothing started without your click')
        self.assertEqual(tasks[0]['suggest']['error'], 'This run left file changes or is still settling. Review it before trying another account.')
        self.assertEqual(self.thread_account(), 'p')

    def approve(self, task_id, carried_id=None):
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})
        args = {**self.scope, 'taskId': task_id, 'attemptNumber': detail['evidence']['attempts'][-1]['number'], 'decision': 'approved'}
        return self.host.dispatch('task_review', {**args, **({'carriedId': carried_id} if carried_id else {})})

    def test_approving_after_a_handoff_commits_the_whole_piece_of_work(self):
        import task_git
        task = self.stop_with_changes(also=True)
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual(preview['files'], ['greeting.py', 'notes.md'])
        new = self.carry_on(task, preview['id'])['task']
        self.assertEqual(new['state'], 'ready_for_review')
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})
        self.assertEqual({k: detail['carried'][k] for k in ('here', 'to', 'files', 'flagged', 'from_task_id')},
                         dict(here='Claude', to='Codex', files=['greeting.py', 'notes.md'], flagged=[], from_task_id=task['id']))
        head = git(self.project, 'rev-parse', 'HEAD')

        # Negative control, on a copy of the folder: today's commit of Codex's task can't take greeting.py (Claude's line
        # sits under Codex's) and never takes notes.md, which only Claude touched.
        control = self.base / 'control'
        shutil.copytree(self.project, control, symlinks=True)
        evidence = self.host.coding.evidence.read(new['id'], thread_id='c', project_id='p', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, 'greeting.py also holds'):
            task_git.commit(new, evidence, str(control))
        self.assertEqual(task_git.changed_files(new, evidence), ['greeting.py'], 'Codex’s own record names one file')

        # The sheet's list is the only way in: an approval that didn't see it approves and commits nothing.
        with self.assertRaisesRegex(Rejected, 'carries on from Claude’s stopped run, so approving it commits files Claude changed too'):
            self.approve(new['id'])
        self.assertEqual((git(self.project, 'rev-parse', 'HEAD'), self.tasks()[-1]['state']), (head, 'ready_for_review'))

        done = self.approve(new['id'], detail['carried']['id'])
        commit = done['commit']
        self.assertEqual((done['state'], commit['files'], commit['carried']['files'], commit['carried']['flagged']),
                         ('idle', ['greeting.py', 'notes.md'], ['greeting.py', 'notes.md'], []))
        self.assertEqual(git(self.project, 'show', 'HEAD:greeting.py', text=True), 'def greet(): return "hi"\n' + CLAUDE_EDIT + self.appendix)
        self.assertEqual(git(self.project, 'show', 'HEAD:notes.md', text=True), '# notes\n' + CLAUDE_NOTE)
        self.assertEqual(git(self.project, 'status', '--porcelain', '--', 'greeting.py', 'notes.md', text=True), '', 'nothing of the work is left behind')
        body = git(self.project, 'show', '--format=%B', '--no-patch', 'HEAD', text=True)
        self.assertIn(f"Task {new['id']} approved in Dervo.", body)
        self.assertIn(f"Carried on from Claude's stopped run, task {task['id']}. Its changes before the handoff are in this commit too: greeting.py, notes.md.", body)

    def test_a_file_changed_outside_both_runs_is_flagged_and_left_out_of_the_commit(self):
        task = self.stop_with_changes(also=True)
        # Codex's grant doesn't cover notes.md, so only the carried plan can speak for it.
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        new = self.carry_on(task, preview['id'])['task']
        seen = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})['carried']
        self.assertEqual((seen['files'], seen['flagged']), (['greeting.py', 'notes.md'], []))
        yours = '# notes\n' + CLAUDE_NOTE + 'mine, after the handoff\n'
        (self.project / 'notes.md').write_text(yours)
        head = git(self.project, 'rev-parse', 'HEAD')
        with self.assertRaisesRegex(Rejected, 'The files from before the handoff changed after you looked, so nothing was approved'):
            self.approve(new['id'], seen['id'])
        self.assertEqual((git(self.project, 'rev-parse', 'HEAD'), self.tasks()[-1]['state']), (head, 'ready_for_review'))
        again = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})['carried']
        self.assertEqual((again['files'], again['flagged']),
                         (['greeting.py'], [dict(path='notes.md', why='changed after the handoff, and not by either run')]))
        commit = self.approve(new['id'], again['id'])['commit']
        self.assertEqual((commit['files'], commit['carried']['flagged'][0]['path']), (['greeting.py'], 'notes.md'))
        self.assertEqual(git(self.project, 'show', 'HEAD:greeting.py', text=True), 'def greet(): return "hi"\n' + CLAUDE_EDIT + self.appendix)
        self.assertEqual(git(self.project, 'show', 'HEAD:notes.md', text=True), '# notes\n', 'the flagged file isn’t committed')
        self.assertEqual((self.project / 'notes.md').read_text(), yours, 'and stays as you left it')
        self.assertIn('Left out: notes.md (changed after the handoff, and not by either run)',
                      git(self.project, 'show', '--format=%B', '--no-patch', 'HEAD', text=True))

    def test_restore_refuses_to_overwrite_changes_nobody_carried_on_and_names_them(self):
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.carry_on(task, preview['id'])
        (self.project / 'notes.md').write_text('# notes\nmine, after the handoff\n')
        check = self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((check['available'], check['put'], check['foreign']), (False, ['greeting.py'], ['notes.md']))
        self.assertIn('notes.md changed after the snapshot, and not by the run that carried on', check['reason'])
        with self.assertRaisesRegex(Rejected, 'notes.md changed after the snapshot'):
            self.host.dispatch('coding_handoff_restore', {**self.scope, 'taskId': task['id'], 'restoreId': check['id']})
        self.assertTrue((self.project / 'greeting.py').read_text().endswith(self.appendix), 'nothing was restored')
        self.assertEqual((self.project / 'notes.md').read_text(), '# notes\nmine, after the handoff\n')
        # Committed, your change is safe in git, so the restore can go ahead; a stale confirm is refused first.
        commit_all(self.project, 'my notes')
        with self.assertRaisesRegex(Rejected, 'Files changed after you looked'):
            self.host.dispatch('coding_handoff_restore', {**self.scope, 'taskId': task['id'], 'restoreId': check['id']})
        check = self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((check['available'], check['put']), (True, ['greeting.py', 'notes.md']))
        self.host.dispatch('coding_handoff_restore', {**self.scope, 'taskId': task['id'], 'restoreId': check['id']})
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + CLAUDE_EDIT)
        self.assertEqual(git(self.project, 'show', 'HEAD:notes.md', text=True), '# notes\nmine, after the handoff\n')

    # ------------------------------------------------------------------ how long a snapshot is kept (23 Sep, follow-up 2)
    def dervo_refs(self):
        return git(self.project, 'for-each-ref', '--format=%(refname)', 'refs/dervo/handoff/', 'refs/dervo/restore/', text=True).split()

    def yours(self):
        """What cleanup must never touch: every ref that isn't one of Dervo's snapshot refs, the stash, the index, HEAD,
        git status and every file in the folder."""
        refs = [line for line in git(self.project, 'for-each-ref').splitlines() if b'refs/dervo/handoff/' not in line and b'refs/dervo/restore/' not in line]
        return dict(pinned(self.project), refs=refs, files=files(self.project))

    def test_cleanup_removes_only_dervos_refs_a_week_after_the_work_settles_and_restore_works_until_then(self):
        import conversation_coding
        # Yours, from before: a stash entry, a branch, and a ref under refs/dervo/ that isn't a snapshot's.
        (self.project / 'notes.md').write_text('# notes\nstashed by you\n')
        git(self.project, 'stash', 'push', '-q', '-m', 'yours', '--', 'notes.md')
        git(self.project, 'branch', 'mine')
        git(self.project, 'update-ref', 'refs/dervo/elsewhere', 'HEAD')
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        new = self.carry_on(task, preview['id'])['task']
        conversation = self.host.coding.conversation
        self.assertEqual(conversation.sweep_handoffs(now=int(time.time()) + 365 * 86400), [], 'nothing goes while the work waits for review')
        self.assertIsNone(conversation.handoff_record(task['id']).get('settled_at'))

        carried = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})['carried']
        self.approve(new['id'], carried['id'])  # settled: the grace period starts here
        settled = conversation.handoff_record(task['id'])['settled_at']
        self.assertTrue(settled)
        self.assertEqual(conversation.sweep_handoffs(now=settled + conversation_coding.HANDOFF_GRACE - 1), [])
        # Inside the week, Restore still works, and leaves its own ref for the folder as it was before.
        check = self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((check['available'], check['put']), (True, ['greeting.py']))
        self.host.dispatch('coding_handoff_restore', {**self.scope, 'taskId': task['id'], 'restoreId': check['id']})
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + CLAUDE_EDIT)
        self.assertEqual(self.dervo_refs(), [f"refs/dervo/handoff/{task['id']}", f"refs/dervo/restore/{task['id']}-1"])
        folder = self.base / 'store' / 'handoff' / task['id']
        self.assertTrue(folder.is_dir())

        before = self.yours()
        with patch.object(conversation, 'folder_busy', lambda _cwd: True):
            self.assertEqual(conversation.sweep_handoffs(now=settled + conversation_coding.HANDOFF_GRACE), [], 'never while a run works there')
        self.assertEqual(conversation.sweep_handoffs(now=settled + conversation_coding.HANDOFF_GRACE), [task['id']])
        self.assertEqual(self.dervo_refs(), [], 'both of Dervo’s refs are gone')
        self.assertEqual(self.yours(), before, 'your refs (refs/dervo/elsewhere too), the stash, the index, status and files are as they were')
        self.assertIn(b'yours', before['stash'])
        self.assertFalse(folder.exists(), 'Dervo’s copies are gone too')
        with self.assertRaisesRegex(Rejected, 'Dervo removed this snapshot a week after the work that carried on from it was settled'):
            self.host.dispatch('coding_handoff_restore_preview', {**self.scope, 'taskId': task['id']})
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual(original['suggest']['snapshot']['removed_at'], settled + conversation_coding.HANDOFF_GRACE, 'Restore isn’t offered any more')
        self.assertEqual(conversation.sweep_handoffs(now=settled + 2 * conversation_coding.HANDOFF_GRACE), [], 'and a second sweep has nothing to do')

    def test_undoing_the_work_starts_the_week_again(self):
        import conversation_coding
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        new = self.carry_on(task, preview['id'])['task']
        conversation = self.host.coding.conversation
        self.approve(new['id'], self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})['carried']['id'])
        approved = conversation.handoff_record(task['id'])
        # Six days on, you undo the task: its snapshot is kept a week from the undo, not from the approval.
        record = conversation.handoff_record(task['id'])
        record['settled_at'] = approved['settled_at'] - 6 * 86400
        conversation.save_handoff(record)
        self.host.dispatch('task_undo', {**self.scope, 'taskId': new['id']})
        undone = conversation.handoff_record(task['id'])
        self.assertTrue(undone['settled_mark'][4], 'the record read as undone')
        self.assertGreaterEqual(undone['settled_at'], approved['settled_at'])
        self.assertEqual(conversation.sweep_handoffs(now=approved['settled_at'] + 86400), [], 'a day after the undo, it stays')
        self.assertEqual(self.dervo_refs(), [f"refs/dervo/handoff/{task['id']}"])
        self.assertEqual(conversation.sweep_handoffs(now=undone['settled_at'] + conversation_coding.HANDOFF_GRACE), [task['id']])

    def test_a_rejected_result_settles_too_and_the_sweep_at_host_start_removes_it(self):
        import conversation_coding
        task = self.stop_with_changes()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        new = self.carry_on(task, preview['id'])['task']
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})
        self.host.dispatch('task_review', {**self.scope, 'taskId': new['id'], 'attemptNumber': detail['evidence']['attempts'][-1]['number'], 'decision': 'rejected'})
        self.assertTrue(self.host.coding.conversation.handoff_record(task['id'])['settled_at'])
        self.assertEqual(self.dervo_refs(), [f"refs/dervo/handoff/{task['id']}"])
        before = self.yours()
        self.host.close()  # the cleanup from setUp closes whichever host is current, so this one closes once
        with patch.object(conversation_coding, 'HANDOFF_GRACE', 0):  # the week has passed by the next start
            self.host = Host(self.base / 'store')
        self.assertEqual(self.dervo_refs(), [], 'removed at host start')
        self.assertEqual(self.yours(), before)
        self.assertTrue(self.host.coding.conversation.handoff_record(task['id'])['removed_at'])

    def test_a_snapshot_an_older_build_saved_on_open_goes_at_the_next_sweep_and_only_its_own_commit(self):
        task = self.stop_with_changes()
        conversation = self.host.coding.conversation
        # What the confirm did until today: a snapshot on open, then Cancel. The folder differed a little then, so the
        # two snapshots can't be the same commit (two captures of one folder in the same second are).
        (self.project / 'notes.md').write_text('# notes\nfor a moment\n')
        old = hs.snapshot(self.project, self.base / 'store' / 'handoff' / task['id'], task['id'])
        (self.project / 'notes.md').write_text('# notes\n')
        conversation.save_handoff(dict(id='old', task_id=task['id'], thread_id='c', project_id='p', cwd=str(self.project), created_at=1,
                                       brief={}, snapshot=old, full=False, sent_task_id=None, restores=[]))
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual(conversation.handoff_record(task['id'])['leftovers'][0]['commit'], old['commit'], 'reopening keeps track of it')
        new = self.carry_on(task, preview['id'])['task']
        sent = conversation.handoff_record(task['id'])['snapshot']['commit']
        self.assertNotEqual(sent, old['commit'])
        self.assertEqual(conversation.sweep_handoffs(now=int(time.time()) + 365 * 86400), [], 'the sent one waits for the work')
        self.assertEqual(git(self.project, 'rev-parse', f"refs/dervo/handoff/{task['id']}", text=True).strip(), sent)
        carried = self.host.dispatch('task_detail', {**self.scope, 'taskId': new['id']})['carried']
        self.approve(new['id'], carried['id'])
        settled = conversation.handoff_record(task['id'])['settled_at']
        import conversation_coding
        self.assertEqual(conversation.sweep_handoffs(now=settled + conversation_coding.HANDOFF_GRACE), [task['id']])
        self.assertEqual(self.dervo_refs(), [])

        # Never sent at all: it goes at the next sweep, with no grace, since nothing can restore it.
        other = hs.snapshot(self.project, self.base / 'store' / 'handoff' / 'unsent-1', 'unsent-1')
        conversation.save_handoff(dict(id='unsent', task_id='unsent-1', thread_id='c', project_id='p', cwd=str(self.project), created_at=1,
                                       brief={}, snapshot=other, full=False, sent_task_id=None, restores=[]))
        self.assertEqual(conversation.sweep_handoffs(), ['unsent-1'])
        self.assertEqual(self.dervo_refs(), [])
        self.assertFalse((self.base / 'store' / 'handoff' / 'unsent-1').exists())

    def test_forget_touches_only_dervos_snapshot_refs_and_only_at_the_recorded_commit(self):
        head = git(self.project, 'rev-parse', 'HEAD', text=True).strip()
        git(self.project, 'update-ref', 'refs/dervo/handoff/t1', head)
        for ref in ('refs/heads/master', 'refs/heads/main', 'refs/stash', 'refs/dervo/elsewhere', 'refs/dervo/handoff/../heads/main', 'HEAD'):
            with self.assertRaisesRegex(Rejected, 'only removes its own snapshot refs'):
                hs.forget(self.project, ref, head)
        self.assertFalse(hs.forget(self.project, 'refs/dervo/handoff/t1', '0' * 40), 'pointing elsewhere: left alone')
        self.assertEqual(self.dervo_refs(), ['refs/dervo/handoff/t1'])
        self.assertTrue(hs.forget(self.project, 'refs/dervo/handoff/t1', head))
        self.assertTrue(hs.forget(self.project, 'refs/dervo/handoff/t1', head), 'already gone is fine')
        self.assertEqual(self.dervo_refs(), [])

    def test_home_offers_continue_for_a_stop_that_left_changes_only_in_a_git_repository(self):
        task = self.stop_with_changes()
        item = next(i for i in self.host.dispatch('project_pickup', {'projectId': 'p', 'profileId': 'p'})['items'] if i['id'] == 'c')
        self.assertEqual((item['kind'], item['handoff']), ('stopped', 'Codex'))
        remove_repository(self.project / '.git')
        item = next(i for i in self.host.dispatch('project_pickup', {'projectId': 'p', 'profileId': 'p'})['items'] if i['id'] == 'c')
        self.assertIsNone(item['handoff'], 'no snapshot possible, so no handoff on offer')
        self.assertEqual(task['state'], 'blocked')


class AccessCapTests(unittest.TestCase):
    """Security review H1: text an agent wrote never gets more access than its source. A handoff carries the stopped
    run's words, so the run it starts never has more access than the stopped run had, whatever the project allows."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.host = Host(Path(temp.name) / 'store')
        self.addCleanup(self.host.close)
        self.conversation = self.host.coding_tasks().conversation

    def view(self, computer):
        return {'enabled': True, 'active': True, 'available': True, 'unavailable': None, 'provider': 'codex', 'detail': '',
                'computer_access': computer, 'computer_available': False, 'connectors': None, 'actor': None, 'created_at': None}

    def test_the_cap_is_what_the_stopped_run_had(self):
        cases = ((dict(), False, False), (dict(full_access=False), False, False), (dict(full_access=True), False, True),
                 (dict(full_access=True), True, False), (dict(full_access=True, computer_access=True), True, True))
        for cap, computer, allowed in cases:
            with self.subTest(cap=cap, computer=computer):
                with patch.object(type(self.conversation), 'full_access', lambda _self, _binding, c=computer: self.view(c)):
                    self.assertEqual(self.conversation.within_cap(cap, {}), allowed)


if __name__ == '__main__':
    unittest.main()
