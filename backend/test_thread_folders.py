"""Thread folders (beta.18, direction B): the pick, setup, the check before a run, commit to the branch, Merge and
removal. Every git test runs against scratch repositories in a temp folder (git commit is refused in the project
inside a Dervo run, and allowed there)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import platform_os
import thread_folders
from dervo_connections import Rejected, Store

GIT = '/usr/bin/git'
ENV = {'PATH': '/usr/bin:/bin', 'HOME': os.path.expanduser('~'), 'GIT_CONFIG_NOSYSTEM': '1',
       'GIT_AUTHOR_NAME': 'Test', 'GIT_AUTHOR_EMAIL': 'test@example.com',
       'GIT_COMMITTER_NAME': 'Test', 'GIT_COMMITTER_EMAIL': 'test@example.com'}


def git(cwd, *args, check=True):
    result = subprocess.run([GIT, '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd, env=ENV, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise AssertionError(f'git {" ".join(args)}: {result.stderr}')
    return result.stdout.strip()


class Scratch(unittest.TestCase):
    """A project repository on ``main`` with a few files, a lockfile and an installed node_modules, and a root for
    thread folders, all in one temp folder."""
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix='dervo-thread-folders-')).resolve()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.project = self.temp / 'project'
        self.root = self.temp / 'Worktrees'
        self.project.mkdir()
        git(self.project, 'init', '-q', '-b', 'main')
        git(self.project, 'config', 'user.name', 'Test')
        git(self.project, 'config', 'user.email', 'test@example.com')
        (self.project / 'app.py').write_text('print("one")\n')
        (self.project / 'notes.md').write_text('notes\n')
        (self.project / 'tool.sh').write_text('#!/bin/sh\necho hi\n')
        os.chmod(self.project / 'tool.sh', 0o755)
        (self.project / 'web').mkdir()
        (self.project / 'web' / 'pnpm-lock.yaml').write_text('lockfileVersion: 9\n')
        (self.project / '.gitignore').write_text('node_modules/\nruntime/\n.env\ndist/\n')
        os.symlink('app.py', self.project / 'link.py')
        git(self.project, 'add', '-A')
        git(self.project, 'commit', '-q', '-m', 'first')
        # Installed and ignored: dependencies, a runtime binary, a secret and build output.
        (self.project / 'web' / 'node_modules' / 'left-pad').mkdir(parents=True)
        (self.project / 'web' / 'node_modules' / 'left-pad' / 'index.js').write_text('module.exports = 1\n')
        (self.project / 'runtime').mkdir()
        (self.project / 'runtime' / 'codex').write_text('binary\n')
        (self.project / '.env').write_text('SECRET=1\n')
        (self.project / 'dist').mkdir()
        (self.project / 'dist' / 'out.js').write_text('built\n')

    def plan(self, **kw):
        args = dict(thread_id='thread-1', project_id='project-12345678', name='Fix the login bug', root=self.root)
        args.update(kw)
        return thread_folders.plan(str(self.project), **args)


class Pick(unittest.TestCase):
    def test_code_explore_read_only_and_repository(self):
        self.assertEqual(thread_folders.pick('Fix the login bug'), ('own', 'code'))
        self.assertEqual(thread_folders.pick('Explore three directions for the sidebar'), ('project', 'explore'))
        self.assertEqual(thread_folders.pick('Write a plan for beta.18'), ('own', 'code'))  # "write" comes before "plan"
        self.assertEqual(thread_folders.pick('Plan beta.18 and note the risks'), ('project', 'explore'))
        self.assertEqual(thread_folders.pick('hello'), ('own', 'unsure'))  # when unsure, own branch
        self.assertEqual(thread_folders.pick(''), ('own', 'unsure'))
        self.assertEqual(thread_folders.pick('Fix it', read_only=True), ('project', 'read_only'))
        self.assertEqual(thread_folders.pick('Fix it', can_edit=False), ('project', 'no_edit'))
        self.assertEqual(thread_folders.pick('Fix it', repo=False), ('project', 'not_repo'))
        self.assertEqual(thread_folders.pick('Fix it', commits=False), ('project', 'no_commits'))
        for reason in ('code', 'unsure', 'explore', 'read_only', 'no_edit', 'not_repo', 'no_commits', 'started', 'windows', 'you'):
            self.assertIn(reason, thread_folders.REASONS)
            self.assertNotIn('—', thread_folders.REASONS[reason])

    def test_windows_always_picks_the_project_folder(self):
        """Bryce, 6 Oct 2026: Windows threads work in the project folder in beta.18, whatever the request says."""
        with patch.object(platform_os, 'WINDOWS', True):
            for text in ('Fix the login bug', 'hello', '', 'Explore three directions'):
                self.assertEqual(thread_folders.pick(text), ('project', 'windows'), text)
            self.assertEqual(thread_folders.pick('Fix it', repo=False), ('project', 'windows'))
        self.assertEqual(thread_folders.pick('Fix the login bug'), ('own', 'code'), 'the Mac is unchanged')


class Places(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'the Mac folder')
    def test_folders_live_in_application_support_never_a_fixed_home(self):
        with patch('pathlib.Path.home', return_value=Path('/Users/someone')):
            self.assertEqual(platform_os.worktrees_dir(), Path('/Users/someone/Library/Application Support/Dervo/Worktrees'))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'the Linux folder')
    def test_linux_folders_live_in_the_xdg_data_folder(self):
        with patch.object(platform_os, 'account_home', return_value='/home/someone'), patch.dict(os.environ, {'XDG_DATA_HOME': ''}):
            self.assertEqual(platform_os.worktrees_dir(), Path('/home/someone/.local/share/Dervo/Worktrees'))
        with patch.object(platform_os, 'account_home', return_value='/home/someone'), patch.dict(os.environ, {'XDG_DATA_HOME': '/data/someone'}):
            self.assertEqual(platform_os.worktrees_dir(), Path('/data/someone/Dervo/Worktrees'))

    def test_windows_uses_local_app_data(self):
        windows = types.SimpleNamespace(name='nt', environ={'LOCALAPPDATA': '/c/Users/someone/AppData/Local'})
        with patch.object(platform_os, 'os', windows):
            self.assertEqual(platform_os.worktrees_dir(), Path('/c/Users/someone/AppData/Local/Dervo/Worktrees'))

    def test_only_the_app_sets_folders_up_in_the_real_place(self):
        """A test's Host once made real thread folders in ~/Library/Application Support/Dervo/Worktrees (5 Oct 2026).
        Any Host keeps them in its own folder unless it's given one, and only host.py's main gives the real one."""
        from host import Host
        with tempfile.TemporaryDirectory() as temp, patch('account_config.create', lambda *args, **kwargs: None):
            host = Host(Path(temp) / 'store')
            try:
                self.assertEqual(host.coding_tasks().folders.worktrees_root(), Path(temp) / 'store' / 'Worktrees')
            finally:
                host.close()
        main = Path(__file__).with_name('host.py').read_text().split("if __name__ == '__main__':", 1)[1]
        self.assertIn('Host(sys.argv[1], worktrees_root=platform_os.worktrees_dir())', main)

    def test_queue_says_why_a_message_waits_for_setup(self):
        import coding_queue
        self.assertIn('setup', coding_queue.WHY_DETAILS)

    def test_worktree_refusal_says_the_thread_has_its_own_branch(self):
        import claude_policy
        output, refused = claude_policy.subagent_hook({'tool_input': {'isolation': 'worktree'}})
        self.assertTrue(refused)
        reason = output['hookSpecificOutput']['permissionDecisionReason']
        self.assertIn('own branch and folder', reason)
        self.assertIn('without isolation', reason)
        self.assertEqual(claude_policy.subagent_hook({'tool_input': {}}), ({}, False))


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clones')
class Setup(Scratch):
    def test_clones_tracked_files_and_matches_the_branch(self):
        p = self.plan()
        self.assertTrue(p['clone'])
        self.assertEqual(p['branch'], 'dervo/fix-the-login-bug')
        self.assertTrue(p['folder'].startswith(str(self.root)))
        self.assertFalse(Path(p['folder']).is_relative_to(self.project))
        result = thread_folders.setup(p)
        folder = Path(p['folder'])
        self.assertEqual(result['state'], 'ready')
        self.assertEqual(git(folder, 'status', '--porcelain', '--untracked-files=no'), '')
        self.assertEqual(git(folder, 'symbolic-ref', 'HEAD'), 'refs/heads/dervo/fix-the-login-bug')
        self.assertEqual(git(folder, 'rev-parse', 'HEAD'), git(self.project, 'rev-parse', 'HEAD'))
        self.assertEqual((folder / 'app.py').read_text(), 'print("one")\n')
        self.assertTrue(os.access(folder / 'tool.sh', os.X_OK))
        self.assertTrue((folder / 'link.py').is_symlink())
        self.assertGreater(result['cloned'], 0)
        # Never copied: untracked or ignored files nobody approved, and secrets.
        self.assertFalse((folder / '.env').exists())
        self.assertFalse((folder / 'runtime').exists())
        self.assertFalse((folder / 'dist').exists())
        # The project folder is untouched: same branch, nothing new in its status.
        self.assertEqual(git(self.project, 'symbolic-ref', '--short', 'HEAD'), 'main')
        self.assertIsNone(thread_folders.verify({**p, 'branch': p['branch']}, str(self.project)))

    def test_your_uncommitted_change_never_reaches_the_thread(self):
        (self.project / 'app.py').write_text('print("my unfinished work")\n')
        p = self.plan()
        self.assertEqual(p['dirty'], ['app.py'])
        thread_folders.setup(p)
        self.assertEqual((Path(p['folder']) / 'app.py').read_text(), 'print("one")\n')
        self.assertEqual((self.project / 'app.py').read_text(), 'print("my unfinished work")\n')
        self.assertEqual(git(p['folder'], 'status', '--porcelain', '--untracked-files=no'), '')

    def test_another_base_is_a_plain_checkout(self):
        git(self.project, 'switch', '-q', '-c', 'other')
        (self.project / 'app.py').write_text('print("other")\n')
        git(self.project, 'commit', '-qam', 'other')
        p = self.plan(base='main')
        self.assertFalse(p['clone'])
        result = thread_folders.setup(p)
        self.assertEqual(result['cloned'], 0)
        self.assertEqual((Path(p['folder']) / 'app.py').read_text(), 'print("one")\n')
        files = next(s for s in result['steps'] if s['key'] == 'files')
        self.assertIn('checked out from main', files['line'])

    def test_matching_lockfile_clones_dependencies_and_a_different_one_asks(self):
        p = self.plan()
        result = thread_folders.setup(p)
        deps = result['dependencies']
        self.assertEqual([(d['lockfile'], d['folder'], d['state']) for d in deps], [('web/pnpm-lock.yaml', 'web/node_modules', 'cloned')])
        cloned = Path(p['folder']) / 'web/node_modules/left-pad/index.js'
        self.assertTrue(cloned.is_file() and not Path(p['folder'], 'web/node_modules').is_symlink())
        # A clone, not a link: one thread's install can't change the project folder's copy.
        cloned.write_text('changed in the thread\n')
        self.assertEqual((self.project / 'web/node_modules/left-pad/index.js').read_text(), 'module.exports = 1\n')

        # A thread whose base has another lockfile: nothing installed, the card asks with the exact command.
        git(self.project, 'switch', '-q', '-c', 'newer')
        (self.project / 'web/pnpm-lock.yaml').write_text('lockfileVersion: 9\nleft-pad: 2\n')
        git(self.project, 'commit', '-qam', 'bump')
        git(self.project, 'switch', '-q', 'main')
        p2 = self.plan(thread_id='thread-2', name='Bump left pad', base='newer')
        result2 = thread_folders.setup(p2)
        self.assertEqual(result2['state'], 'needs_install')
        dep = result2['dependencies'][0]
        self.assertEqual((dep['state'], dep['command']), ('differs', ['pnpm', 'install', '--frozen-lockfile']))
        self.assertFalse(Path(p2['folder'], 'web/node_modules').exists())
        self.assertIn('Install dependencies?', thread_folders.dependency_line(result2['dependencies']))

    def test_a_linked_dependency_folder_stays_a_link(self):
        shutil.rmtree(self.project / 'web' / 'node_modules')
        shared = self.temp / 'shared_modules'
        shared.mkdir()
        os.symlink(shared, self.project / 'web' / 'node_modules')
        p = self.plan()
        result = thread_folders.setup(p)
        self.assertEqual(result['dependencies'][0]['state'], 'linked')
        self.assertEqual(os.readlink(Path(p['folder'], 'web/node_modules')), str(shared))

    def test_approved_ignored_files_are_cloned_and_secrets_are_never_proposed(self):
        candidates = thread_folders.ignored_candidates(str(self.project))
        self.assertIn('runtime', candidates)
        self.assertNotIn('.env', candidates)
        self.assertNotIn('dist', candidates)
        self.assertFalse(any('node_modules' in c for c in candidates))
        p = self.plan(ignored=['runtime/codex'])
        result = thread_folders.setup(p)
        self.assertEqual(result['copied'], ['runtime/codex'])
        self.assertEqual(Path(p['folder'], 'runtime/codex').read_text(), 'binary\n')
        self.assertFalse(Path(p['folder'], '.env').exists())

    def test_the_folders_root_is_kept_out_of_time_machine(self):
        thread_folders.setup(self.plan())
        names = subprocess.run(['/usr/bin/xattr', str(self.root)], capture_output=True, text=True).stdout.split()
        self.assertIn(platform_os.BACKUP_EXCLUDE, names)

    def test_low_space_makes_nothing(self):
        p = self.plan()
        p['free'] = 4 * 1024 ** 3
        with self.assertRaises(thread_folders.SetupStop) as stopped:
            thread_folders.setup(p)
        self.assertEqual(stopped.exception.key, 'space')
        self.assertEqual(stopped.exception.extra['free'], 4 * 1024 ** 3)
        self.assertGreaterEqual(stopped.exception.extra['needed'], thread_folders.FLOOR)
        self.assertFalse(Path(p['folder']).exists())
        self.assertFalse(thread_folders.branch_exists(str(self.project), p['branch']))
        space = stopped.exception.extra['steps'][0]
        self.assertEqual((space['key'], space['state']), ('space', 'needs'))
        self.assertIn('Dervo keeps 6.0 GB free', space['line'])

    def test_a_failed_setup_leaves_no_folder_or_branch(self):
        p = self.plan()
        with patch.object(thread_folders, 'dependencies', side_effect=OSError('disk went away')):
            with self.assertRaises(thread_folders.SetupStop):
                thread_folders.setup(p)
        self.assertFalse(Path(p['folder']).exists())
        self.assertFalse(thread_folders.branch_exists(str(self.project), p['branch']))
        self.assertNotIn(p['folder'], git(self.project, 'worktree', 'list'))

    def test_branch_names_clash_with_a_number(self):
        git(self.project, 'branch', 'dervo/fix-the-login-bug')
        self.assertEqual(self.plan()['branch'], 'dervo/fix-the-login-bug-2')

    def test_no_branch_no_commits_and_not_a_repo_stop_with_their_key(self):
        git(self.project, 'switch', '-q', '--detach')
        with self.assertRaises(thread_folders.SetupStop) as stopped:
            self.plan()
        self.assertEqual(stopped.exception.key, 'no_base')
        plain = self.temp / 'plain'
        plain.mkdir()
        with self.assertRaises(thread_folders.SetupStop) as stopped:
            thread_folders.plan(str(plain), thread_id='t', project_id='p', name='x', root=self.root)
        self.assertEqual(stopped.exception.key, 'not_repo')
        empty = self.temp / 'empty'
        empty.mkdir()
        git(empty, 'init', '-q')
        with self.assertRaises(thread_folders.SetupStop) as stopped:
            thread_folders.plan(str(empty), thread_id='t', project_id='p', name='x', root=self.root)
        self.assertEqual(stopped.exception.key, 'no_commits')

    def test_a_project_folder_inside_a_bigger_repository_maps_into_the_thread_folder(self):
        nested = self.project / 'web'
        p = thread_folders.plan(str(nested), thread_id='t', project_id='project-1', name='Fix web', root=self.root)
        self.assertEqual(p['prefix'], 'web')
        self.assertEqual(p['cwd'], os.path.join(p['folder'], 'web'))
        thread_folders.setup(p)
        self.assertTrue(Path(p['cwd'], 'pnpm-lock.yaml').is_file())


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clones')
class Check(Scratch):
    def made(self):
        p = self.plan()
        thread_folders.setup(p)
        return {'folder': p['folder'], 'cwd': p['cwd'], 'branch': p['branch'], 'base': p['base']}

    def test_the_folder_dervo_made_passes(self):
        self.assertIsNone(thread_folders.verify(self.made(), str(self.project)))

    def test_anything_else_is_unknown(self):
        row = self.made()
        git(row['folder'], 'switch', '-q', '-c', 'elsewhere')
        self.assertIn('isn’t on dervo/fix-the-login-bug', thread_folders.verify(row, str(self.project)))
        git(row['folder'], 'switch', '-q', row['branch'])
        self.assertIsNone(thread_folders.verify(row, str(self.project)))
        moved = self.temp / 'moved'
        os.rename(row['folder'], moved)
        self.assertEqual(thread_folders.verify(row, str(self.project)), 'The thread’s folder is missing.')
        os.symlink(moved, row['folder'])
        self.assertIn('link', thread_folders.verify(row, str(self.project)))
        os.unlink(row['folder'])
        os.rename(moved, row['folder'])
        git(self.project, 'worktree', 'remove', '--force', row['folder'])
        self.assertIn('missing', thread_folders.verify(row, str(self.project)))

    def test_a_plain_folder_at_that_path_is_not_the_thread_folder(self):
        row = {'folder': str(self.temp / 'impostor'), 'branch': 'dervo/x', 'base': 'main'}
        Path(row['folder']).mkdir()
        self.assertIn('doesn’t list', thread_folders.verify(row, str(self.project)))


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clones')
class MergeAndRemove(Scratch):
    def thread(self, base=None, change=('app.py', 'print("two")\n')):
        p = self.plan(**({'base': base} if base else {}))
        thread_folders.setup(p)
        path, text = change
        Path(p['folder'], path).write_text(text)
        # Dervo's own commit on the thread's branch (what auto_commit does through task_git), in a scratch repository.
        git(p['folder'], 'commit', '-qam', 'Dervo: change\n\nTask t1 approved and committed automatically in Dervo. Dervo made this commit.')
        return {'folder': p['folder'], 'cwd': p['cwd'], 'branch': p['branch'], 'base': p['base']}

    def test_a_result_and_its_undo_leave_nothing_to_merge(self):
        """Polish, 9 Oct: after an Undo the card still offered Merge for "2 commits ahead", a commit and its revert."""
        row = self.thread()
        self.assertFalse(thread_folders.branch_state(str(self.project), row['branch'], 'main')['empty'])
        git(row['folder'], 'revert', '--no-edit', 'HEAD')
        state = thread_folders.branch_state(str(self.project), row['branch'], 'main')
        self.assertEqual((state['ahead'], state['clashes'], state['empty']), (2, [], True))

    def test_base_checked_out_in_the_project_folder_gets_a_merge_commit(self):
        row = self.thread()
        (self.project / 'notes.md').write_text('your own unfinished note\n')  # not in the merge: it stays
        before = git(self.project, 'rev-parse', 'HEAD')
        state = thread_folders.branch_state(str(self.project), row['branch'], 'main')
        self.assertEqual((state['ahead'], state['clashes']), (1, []))
        result = thread_folders.merge(row, str(self.project), actor='local-user', thread_name='Login fix')
        self.assertEqual(result['where'], str(self.project))
        parents = git(self.project, 'rev-list', '--parents', '-n', '1', 'HEAD').split()
        self.assertEqual(len(parents), 3)  # a merge commit, never a squash
        self.assertEqual(parents[1], before)
        message = git(self.project, 'log', '-1', '--format=%B')
        self.assertTrue(message.startswith('Dervo: Merge dervo/fix-the-login-bug into main'))
        self.assertIn(' in Dervo. Dervo made this commit', message)
        self.assertEqual((self.project / 'app.py').read_text(), 'print("two")\n')
        self.assertEqual((self.project / 'notes.md').read_text(), 'your own unfinished note\n')
        import native_evidence
        self.assertTrue(native_evidence.dervo_commits_only(str(self.project), before, git(self.project, 'rev-parse', 'HEAD')))

    def test_base_not_checked_out_merges_without_a_folder(self):
        git(self.project, 'switch', '-q', '-c', 'mvp1')
        row = self.thread(base='main')
        main_before = git(self.project, 'rev-parse', 'main')
        result = thread_folders.merge(row, str(self.project), actor='local-user', thread_name='Login fix')
        self.assertIsNone(result['where'])
        parents = git(self.project, 'rev-list', '--parents', '-n', '1', 'main').split()
        self.assertEqual(parents[1:], [main_before, git(self.project, 'rev-parse', row['branch'])])
        self.assertEqual(git(self.project, 'show', 'main:app.py'), 'print("two")')
        # The project folder, on another branch, didn't change.
        self.assertEqual(git(self.project, 'symbolic-ref', '--short', 'HEAD'), 'mvp1')
        self.assertEqual((self.project / 'app.py').read_text(), 'print("one")\n')

    def test_clash_refuses_and_names_the_file(self):
        row = self.thread()
        (self.project / 'app.py').write_text('print("base moved")\n')
        git(self.project, 'commit', '-qam', 'base moved')
        state = thread_folders.branch_state(str(self.project), row['branch'], 'main')
        self.assertEqual(state['clashes'], ['app.py'])
        with self.assertRaises(Rejected) as refused:
            thread_folders.merge(row, str(self.project), actor='local-user')
        self.assertIn('main also changed app.py', str(refused.exception))

    def test_uncommitted_work_blocks_merge_on_either_side(self):
        row = self.thread()
        Path(row['folder'], 'notes.md').write_text('agent left this\n')
        with self.assertRaises(Rejected) as refused:
            thread_folders.merge(row, str(self.project), actor='local-user')
        self.assertIn('aren’t committed to its branch yet: notes.md', str(refused.exception))
        git(row['folder'], 'checkout', '--', 'notes.md')
        (self.project / 'app.py').write_text('your change\n')
        with self.assertRaises(Rejected) as refused:
            thread_folders.merge(row, str(self.project), actor='local-user')
        self.assertIn('uncommitted changes in app.py', str(refused.exception))
        self.assertEqual((self.project / 'app.py').read_text(), 'your change\n')

    def test_nothing_to_merge(self):
        p = self.plan()
        thread_folders.setup(p)
        with self.assertRaises(Rejected) as refused:
            thread_folders.merge({'folder': p['folder'], 'branch': p['branch'], 'base': 'main'}, str(self.project), actor='a')
        self.assertIn('Nothing to merge', str(refused.exception))

    def test_a_running_folder_waits(self):
        row = self.thread()
        with self.assertRaises(Rejected) as refused:
            thread_folders.merge(row, str(self.project), actor='a', busy=lambda folder: folder == str(self.project))
        self.assertIn('Merge when it ends', str(refused.exception))

    def test_remove_keeps_the_branch_and_refuses_uncommitted_changes(self):
        row = self.thread()
        Path(row['folder'], 'new.txt').write_text('not committed\n')
        with self.assertRaises(Rejected) as refused:
            thread_folders.remove(row, str(self.project))
        self.assertIn('new.txt', str(refused.exception))
        self.assertTrue(Path(row['folder'], 'new.txt').exists())
        os.unlink(Path(row['folder'], 'new.txt'))
        self.assertTrue(Path(row['folder'], 'web/node_modules').exists())  # ignored clones go with the folder
        self.assertTrue(thread_folders.remove(row, str(self.project)))
        self.assertFalse(Path(row['folder']).exists())
        self.assertTrue(thread_folders.branch_exists(str(self.project), row['branch']))
        self.assertTrue(thread_folders.remove(row, str(self.project)))  # already gone: fine

    def test_set_up_again_from_the_kept_branch(self):
        row = self.thread()
        tip = git(self.project, 'rev-parse', row['branch'])
        thread_folders.remove(row, str(self.project))
        p = self.plan(branch=row['branch'])
        self.assertTrue(p['reuse'])
        self.assertEqual(p['folder'], row['folder'])  # the same folder, so earlier tasks still match it
        thread_folders.setup(p)
        self.assertEqual(git(p['folder'], 'rev-parse', 'HEAD'), tip)
        self.assertEqual(Path(p['folder'], 'app.py').read_text(), 'print("two")\n')


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clones')
class CodexTrust(Scratch):
    def test_a_worktree_marks_the_main_checkout_untrusted_too(self):
        import codex_policy
        p = self.plan()
        thread_folders.setup(p)
        inside = Path(p['folder']) / 'web'
        self.assertEqual(codex_policy.main_checkouts(str(inside)), [self.project, self.project / 'web'])
        table = codex_policy.untrusted_projects(str(inside))
        for folder in (str(inside), p['folder'], str(self.project), str(self.project / 'web')):
            self.assertEqual(table[folder], {'trust_level': 'untrusted'})
        # A main checkout adds nothing new.
        self.assertEqual(codex_policy.main_checkouts(str(self.project)), [])
        self.assertEqual(codex_policy.main_checkouts(str(self.temp)), [])


class StoreBinding(Scratch):
    def store(self):
        store = Store(self.temp / 'store' / 'dervo.sqlite3')
        self.addCleanup(store.close)
        home = self.temp / 'home'
        home.mkdir(exist_ok=True)
        store.add_profile('profile', 'claude', home, 'me@example.com', 'personal')
        store.add_project('project-12345678', self.project, 'profile', 'claude-opus-5-5')
        store.add_thread('thread-1', 'project-12345678', 'channel', 'shared')
        return store

    def test_binding_follows_the_thread_folder_only_when_it_is_set(self):
        store = self.store()
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        self.assertEqual((binding['cwd'], binding['project_cwd']), (str(self.project), str(self.project)))
        row = {'thread_id': 'thread-1', 'project_id': 'project-12345678', 'choice': 'own', 'reason': 'code',
               'folder': str(self.root / 'f'), 'cwd': str(self.root / 'f'), 'branch': 'dervo/f', 'base': 'main', 'state': 'setting_up'}
        with store.db:
            thread_folders.write(store.db, row)
        self.assertEqual(store.binding('thread-1', 'project-12345678', actor='me')['cwd'], str(self.project))
        for state in ('ready', 'merged', 'archived'):
            with store.db:
                thread_folders.write(store.db, {**row, 'state': state})
            binding = store.binding('thread-1', 'project-12345678', actor='me')
            self.assertEqual((binding['cwd'], binding['project_cwd']), (str(self.root / 'f'), str(self.project)))
        with store.db:
            thread_folders.write(store.db, {**row, 'choice': 'project', 'state': 'ready'})
        self.assertEqual(store.binding('thread-1', 'project-12345678', actor='me')['cwd'], str(self.project))

    def test_an_older_store_gets_the_table(self):
        store = self.store()
        with store.db:
            store.db.execute('DROP TABLE thread_folders')
        store.close()
        again = Store(self.temp / 'store' / 'dervo.sqlite3')
        self.addCleanup(again.close)
        self.assertIsNotNone(again.db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_folders'").fetchone())

    def test_the_project_row_holds_the_approved_ignored_files(self):
        store = self.store()
        with store.db:
            thread_folders.set_ignored(store.db, 'project-12345678', ['runtime/codex', 'runtime/codex-code-mode-host'], 'me')
        self.assertEqual(thread_folders.project_settings(store.db, 'project-12345678')['ignored'],
                         ['runtime/codex', 'runtime/codex-code-mode-host'])
        for bad in (['../outside'], ['/abs'], ['.git/config'], 'runtime', ['a', 'a']):
            with self.assertRaises(Rejected):
                thread_folders.set_ignored(store.db, 'project-12345678', bad, 'me')

    def test_project_level_records_compare_with_the_project_folder(self):
        import conversation_coding
        binding = {'cwd': '/w/thread', 'project_cwd': '/p', 'project_id': 'x'}
        self.assertEqual(conversation_coding.at_project(binding)['cwd'], '/p')
        plain = {'cwd': '/p', 'project_id': 'x'}
        self.assertIs(conversation_coding.at_project(plain), plain)


class FakeConversation:
    def __init__(self, granted=True):
        self.granted = granted

    def permission(self, binding):
        return {'state': 'granted' if self.granted else 'missing'}


class WindowsView:
    """platform_os as thread_folders sees it on Windows: WINDOWS is True, everything else is this OS's. Patching
    platform_os.WINDOWS itself would send the Store's lock and the run's file checks to msvcrt on a Mac."""
    WINDOWS = True

    def __getattr__(self, name):
        return getattr(platform_os, name)


def on_windows():
    return patch.object(thread_folders, 'platform_os', WindowsView())


class OnWindows(Scratch):
    """Beta.18 on Windows: no thread folders. Runs on any OS with thread_folders' platform_os.WINDOWS patched; the
    project is a real git repository with a commit, so only the Windows rule keeps threads out of a folder of their own."""
    store = StoreBinding.store

    def setUp(self):
        super().setUp()
        self.enterContext(on_windows())
        self.enterContext(patch.object(thread_folders, 'setup', side_effect=AssertionError('no thread folder setup on Windows')))
        self.enterContext(patch.object(thread_folders, 'plan', side_effect=AssertionError('no thread folder plan on Windows')))

    def folders(self, store):
        coding = types.SimpleNamespace(store=store, conversation=FakeConversation(), turns={}, busy_thread=lambda tid: False,
                                       _cwd=lambda task_id: None)
        coding.folders = thread_folders.Folders(coding, root=self.root)
        return coding.folders

    def test_the_first_code_request_starts_in_the_project_folder_straight_away(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        view = folders.view(binding, 'Fix the login bug')
        self.assertEqual((view['choice'], view['reason'], view['flippable']), ('project', 'windows', False))
        self.assertIsNone(folders.gate(binding, {'text': 'Fix the login bug'}), 'nothing queues for folder setup')
        row = thread_folders.read(store.db, 'thread-1')
        self.assertEqual((row['choice'], row['reason'], row['state']), ('project', 'windows', 'chosen'))
        self.assertFalse(folders.waiting('thread-1'))
        self.assertEqual(store.binding('thread-1', 'project-12345678', actor='me')['cwd'], str(self.project))
        self.assertIsNone(folders.check(binding))
        self.assertFalse(folders.committing({'context': {'thread_id': 'thread-1'}, 'binding': {'cwd': str(self.project)}}))
        self.assertEqual(folders.jobs, {})
        self.assertFalse(self.root.exists())

    def test_choosing_its_own_branch_is_refused_with_a_plain_reason_and_the_project_folder_is_fine(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        with self.assertRaises(Rejected) as refused:
            folders.choose(binding, 'own', 'me')
        self.assertEqual(str(refused.exception), thread_folders.NOT_ON_WINDOWS)
        self.assertNotIn('—', thread_folders.NOT_ON_WINDOWS)
        self.assertIsNone(thread_folders.read(store.db, 'thread-1'), 'a refusal saves nothing')
        view = folders.choose(binding, 'project', 'me')  # test_run_path's setUp does this through the host
        self.assertEqual((view['choice'], view['reason']), ('project', 'you'))
        self.assertIsNone(folders.gate(binding, {'text': 'Fix it'}))

    def test_an_own_pick_from_a_mac_store_runs_in_the_project_folder_and_never_sets_up(self):
        store = self.store()
        folders = self.folders(store)
        for state in ('chosen', 'failed', 'needs_install'):
            with store.db:
                thread_folders.write(store.db, {'thread_id': 'thread-1', 'project_id': 'project-12345678', 'choice': 'own',
                                                'reason': 'code', 'state': state})
            self.assertFalse(folders.waiting('thread-1'), state)
            binding = store.binding('thread-1', 'project-12345678', actor='me')
            self.assertIsNone(folders.gate(binding, {'text': 'Fix it'}), state)
            self.assertEqual(thread_folders.read(store.db, 'thread-1')['choice'], 'project')
        with store.db:
            thread_folders.write(store.db, {'thread_id': 'thread-1', 'project_id': 'project-12345678', 'choice': 'own',
                                            'reason': 'code', 'state': 'failed'})
        with self.assertRaises(Rejected) as refused:
            folders.retry(store.binding('thread-1', 'project-12345678', actor='me'))
        self.assertEqual(str(refused.exception), thread_folders.NOT_ON_WINDOWS)



def _first_request_on_windows():
    """test_conversation_coding's real Host and synthetic Codex peer, without its tests (they stay that module's): the
    project is a git repository with a commit and edits are granted, so on a Mac Dervo would pick the thread's own
    branch and queue the first message for setup."""
    import test_conversation_coding
    from host import ACTOR
    base = test_conversation_coding.ConversationCodingTests

    class FirstRequestOnWindows(base):
        def setUp(self):
            super().setUp()
            self.enterContext(patch.object(thread_folders, 'pick', PICK))  # undo the base's patch: the real pick
            git(self.project, 'init', '-q', '-b', 'main')
            git(self.project, 'add', '-A')
            git(self.project, 'commit', '-q', '-m', 'first')

        def test_the_first_coding_request_in_a_thread_starts_its_task_at_once(self):
            """As test_run_path, test_headless_lifecycle and test_windows_terminal make it on the Windows runner."""
            self.grant()
            text = 'Please fix greeting.py and run the tests'
            binding = self.host.coding_tasks().store.binding('c', 'p', actor=ACTOR)
            self.assertEqual(self.host.coding_tasks().folders.proposed(binding, text), ('own', 'code'), 'a Mac would queue it')
            with on_windows(), patch.object(thread_folders, 'setup', side_effect=AssertionError('no thread folder setup on Windows')):
                started, detail = self.run_request(text)
            self.assertIsNotNone(started['taskId'], started)
            self.assertNotEqual(started['state'], 'queued')
            self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
            self.assertEqual(self.starts, 1)
            row = thread_folders.read(self.host.coding_tasks().store.db, 'c')
            self.assertEqual((row['choice'], row['reason']), ('project', 'windows'))
            self.assertEqual(detail['task']['binding']['cwd'], str(self.project))

    for name in [n for n in dir(base) if n.startswith('test_')]:
        setattr(FirstRequestOnWindows, name, None)  # the base's own tests run in their module, not again here
    return FirstRequestOnWindows


PICK = thread_folders.pick
FirstRequestOnWindows = _first_request_on_windows()


@unittest.skipUnless(sys.platform == 'darwin', 'APFS clones')
class Gate(StoreBinding):
    def folders(self, store):
        coding = types.SimpleNamespace(store=store, conversation=FakeConversation(), turns={}, busy_thread=lambda tid: False,
                                       _cwd=lambda task_id: None)
        coding.folders = thread_folders.Folders(coding, root=self.root)
        return coding.folders

    def wait(self, folders, thread_id='thread-1'):
        for _ in range(400):
            job = folders.jobs.get(thread_id)
            if job is None or job.done:
                break
            time.sleep(0.05)
        folders.settle()

    def test_first_code_request_sets_up_then_runs_in_the_folder(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        view = folders.view(binding, 'Fix the login bug')
        self.assertEqual((view['choice'], view['reason'], view['flippable']), ('own', 'code', True))
        self.assertEqual(view['suggested_branch'], 'dervo/fix-the-login-bug')
        self.assertEqual(view['base'], 'main')
        self.assertEqual(folders.gate(binding, {'text': 'Fix the login bug'}), 'setup')
        self.assertTrue(folders.waiting('thread-1') or folders.jobs['thread-1'].done)
        self.wait(folders)
        row = thread_folders.read(store.db, 'thread-1')
        self.assertEqual(row['state'], 'ready', row['detail'])
        self.assertFalse(folders.waiting('thread-1'))
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        self.assertEqual(binding['cwd'], row['cwd'])
        self.assertIsNone(folders.gate(binding, {'text': 'and again'}))
        self.assertIsNone(folders.check(binding, {'binding': {'cwd': row['cwd']}}))
        with self.assertRaises(Rejected):
            folders.check(binding, {'binding': {'cwd': str(self.project)}})  # a task recorded elsewhere never runs here
        self.assertTrue(folders.committing({'context': {'thread_id': 'thread-1'}, 'binding': {'cwd': row['cwd']}}))
        # Unknown folder: no run.
        git(row['folder'], 'switch', '-q', '-c', 'moved')
        with self.assertRaises(Rejected) as refused:
            folders.check(binding)
        self.assertIn('Unknown folder', str(refused.exception))

    def test_explores_and_older_threads_stay_in_the_project_folder(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        self.assertIsNone(folders.gate(binding, {'text': 'Explore three directions'}))
        row = thread_folders.read(store.db, 'thread-1')
        self.assertEqual((row['choice'], row['reason']), ('project', 'explore'))
        self.assertIsNone(folders.check(binding))
        # A thread that already ran before beta.18 keeps the project folder, whatever its next message says.
        store.add_thread('thread-2', 'project-12345678', 'channel', 'shared')
        store.send('thread-2', 'project-12345678', 'c1', 'hello', actor='me')
        old = store.binding('thread-2', 'project-12345678', actor='me')
        self.assertIsNone(folders.gate(old, {'text': 'Fix the login bug'}))
        self.assertEqual(thread_folders.read(store.db, 'thread-2')['reason'], 'started')
        with self.assertRaises(Rejected):
            folders.choose(old, 'own', 'me')

    def test_flip_before_the_first_run_and_run_in_the_project_folder_instead(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        view = folders.choose(binding, 'project', 'me')
        self.assertEqual((view['choice'], view['reason']), ('project', 'you'))
        self.assertIsNone(folders.gate(binding, {'text': 'Fix the login bug'}))
        view = folders.choose(binding, 'own', 'me')
        self.assertEqual(view['choice'], 'own')
        with patch.object(thread_folders, 'free_bytes', return_value=2 * 1024 ** 3):
            self.assertEqual(folders.gate(binding, {'text': 'Fix it'}), 'setup')
        self.wait(folders)
        view = folders.view(binding)
        self.assertEqual((view['state'], view['error']['key']), ('failed', 'space'))
        self.assertEqual(view['steps'][0]['state'], 'needs')
        self.assertTrue(folders.waiting('thread-1'))
        self.assertFalse(any(self.root.rglob('*')) if self.root.exists() else False)
        view = folders.project_folder_instead(binding, 'me')
        self.assertEqual((view['choice'], view['state']), ('project', 'chosen'))
        self.assertFalse(folders.waiting('thread-1'))
        self.assertEqual(store.binding('thread-1', 'project-12345678', actor='me')['cwd'], str(self.project))

    def test_merge_and_archive(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        folders.gate(binding, {'text': 'Fix the login bug'})
        self.wait(folders)
        row = thread_folders.read(store.db, 'thread-1')
        Path(row['cwd'], 'app.py').write_text('print("two")\n')
        git(row['folder'], 'commit', '-qam', 'Dervo: fix\n\nTask t approved and committed automatically in Dervo. Dervo made this commit.')
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        view = folders.view(binding)
        self.assertEqual((view['branch_state']['ahead'], view['branch_state']['clashes'], view['unknown']), (1, [], None))
        view = folders.merge(binding, 'me')
        self.assertEqual(view['state'], 'merged')
        self.assertEqual(view['merge']['base'], 'main')
        self.assertFalse(Path(row['folder']).exists())
        self.assertEqual((self.project / 'app.py').read_text(), 'print("two")\n')
        # Binding keeps the folder path, so the thread's earlier tasks still match it.
        self.assertEqual(store.binding('thread-1', 'project-12345678', actor='me')['cwd'], row['cwd'])
        # The next message sets the same folder up again from the kept branch.
        self.assertEqual(folders.gate(binding, {'text': 'one more fix'}), 'setup')
        self.wait(folders)
        again = thread_folders.read(store.db, 'thread-1')
        self.assertEqual((again['state'], again['folder']), ('ready', row['folder']))
        # Archive removes it, keeps the branch.
        self.assertEqual(folders.archived('thread-1', str(self.project)), 'archived')
        self.assertFalse(Path(row['folder']).exists())
        self.assertTrue(thread_folders.branch_exists(str(self.project), row['branch']))

    def test_restart_during_setup_stops_cleanly(self):
        store = self.store()
        row = {'thread_id': 'thread-1', 'project_id': 'project-12345678', 'choice': 'own', 'reason': 'code',
               'folder': str(self.root / 'half'), 'cwd': str(self.root / 'half'), 'branch': 'dervo/half', 'base': 'main',
               'state': 'setting_up', 'detail': {}}
        git(self.project, 'worktree', 'add', '-q', '--no-checkout', '-b', 'dervo/half', row['folder'], 'main')
        with store.db:
            thread_folders.write(store.db, row)
        folders = self.folders(store)
        after = thread_folders.read(store.db, 'thread-1')
        self.assertEqual((after['state'], after['detail']['error']['key']), ('failed', 'restart'))
        self.assertFalse(Path(row['folder']).exists())
        self.assertFalse(thread_folders.branch_exists(str(self.project), 'dervo/half'))
        self.assertTrue(folders.waiting('thread-1'))

    # Beta.18 polish pass 2 (7 Oct 2026): the chip asked ``thread_folder`` three times per thread click and every 15 s, and
    # each answer started 18 git processes (about 0.4 s on a small repo) while the host answered nothing else.
    def counted(self):
        calls = []
        real = thread_folders._git
        def counting(cwd, *args, **kw):
            calls.append(args[0])
            return real(cwd, *args, **kw)
        return calls, patch.object(thread_folders, '_git', counting)

    def own_thread(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        folders.gate(binding, {'text': 'Fix the login bug'})
        self.wait(folders)
        return store, folders, store.binding('thread-1', 'project-12345678', actor='me')

    def test_a_repeated_read_of_a_thread_folder_asks_git_once(self):
        store, folders, binding = self.own_thread()
        folders.facts.clear()
        calls, counting = self.counted()
        with counting:
            first = folders.view(binding)
            cold = len(calls)
            again = folders.view(binding)
            third = folders.view(binding)
        self.assertEqual(first, again)
        self.assertEqual(first, third)
        self.assertGreater(cold, 5, 'the first read starts git')
        self.assertEqual(len(calls), cold, 'the next two reads are answered from what the first found')
        self.assertEqual(first['branch_state']['ahead'], 0)
        self.assertIsNone(first['unknown'])
        # A thread folder reads git no more than 11 times: the project's repo facts once (5), the folder's check (3, not 8) and the comparison (3).
        self.assertLessEqual(cold, 11, calls)

    def test_a_read_after_the_kept_time_asks_git_again_and_sees_the_change(self):
        store, folders, binding = self.own_thread()
        folders.facts.clear()
        before = folders.view(binding)
        row = thread_folders.read(store.db, 'thread-1')
        Path(row['cwd'], 'app.py').write_text('print("two")\n')
        git(row['folder'], 'commit', '-qam', 'Dervo: fix\n\nDervo made this commit.')
        self.assertEqual(folders.view(binding)['branch_state']['ahead'], 0, 'within the kept time it still says what it said')
        with patch.object(thread_folders.time, 'monotonic', return_value=time.monotonic() + folders.FACTS_FOR + 1):
            after = folders.view(binding)
        self.assertEqual((before['branch_state']['ahead'], after['branch_state']['ahead']), (0, 1))

    def test_what_the_host_does_to_a_folder_is_never_answered_from_the_kept_facts(self):
        store, folders, binding = self.own_thread()
        folders.facts.clear()
        folders.view(binding)
        kept = folders.facts[('info', str(self.project))][1]['commit']
        self.assertEqual(kept, git(self.project, 'rev-parse', 'HEAD'))
        row = thread_folders.read(store.db, 'thread-1')
        Path(row['cwd'], 'app.py').write_text('print("two")\n')
        git(row['folder'], 'commit', '-qam', 'Dervo: fix\n\nDervo made this commit.')
        self.assertEqual(folders.merge(binding, 'me')['state'], 'merged')
        moved = git(self.project, 'rev-parse', 'HEAD')
        self.assertNotEqual(moved, kept, 'the merge moved the project folder to a merge commit')
        self.assertEqual(folders.facts[('info', str(self.project))][1]['commit'], moved, 'the view after the merge read git again')
        self.assertFalse([key for key in folders.facts if key[0] == 'standing'], 'a merged thread has no comparison to keep')

    # Beta.18 review (7 Oct 2026): every clean result in a thread folder commits to its branch when the run ends
    # (auto_commit), and the branch card's Merge only shows once it reads ahead >= 1. A kept "0 ahead" hid Merge for 10 s.
    def test_a_result_committed_to_the_branch_shows_on_the_card_at_once(self):
        import coding_integration
        store, folders, binding = self.own_thread()
        self.assertEqual(folders.view(binding)['branch_state']['ahead'], 0)
        row = thread_folders.read(store.db, 'thread-1')
        Path(row['cwd'], 'app.py').write_text('print("two")\n')
        def commit(task, evidence, cwd, **kw):
            git(row['folder'], 'commit', '-qam', 'Dervo: fix\n\nDervo made this commit.')
            return {'sha': git(row['folder'], 'rev-parse', 'HEAD'), 'how': kw['how']}
        fake = types.SimpleNamespace(folders=folders, thread_name=lambda t: 'Fix it',
                                     evidence=types.SimpleNamespace(read=lambda *a, **k: {}, for_commit=lambda e, t: e))
        task = {'id': 'task-1', 'context': {'thread_id': 'thread-1', 'project_id': 'project-12345678', 'actor': 'me'}}
        with patch('task_git.commit', commit), patch.object(coding_integration.CodingTasks, 'shared_files', lambda *a: {}):
            record = coding_integration.CodingTasks.commit_record(fake, task, binding, how='automatic')
        self.assertTrue(record['sha'])
        self.assertEqual(folders.view(binding)['branch_state']['ahead'], 1, 'the commit Dervo just made shows without waiting')

    def test_the_check_before_a_run_still_reads_git_every_time(self):
        store, folders, binding = self.own_thread()
        folders.view(binding)
        calls, counting = self.counted()
        with counting:
            self.assertIsNone(folders.check(binding))
            first = len(calls)
            self.assertIsNone(folders.check(binding))
        self.assertGreater(first, 0)
        self.assertEqual(len(calls), first * 2, 'nothing was answered from the kept facts')
        # A folder that goes missing is caught by the check at once, whatever a view kept a moment ago.
        row = thread_folders.read(store.db, 'thread-1')
        shutil.rmtree(row['folder'])
        with self.assertRaisesRegex(Rejected, 'Unknown folder'):
            folders.check(binding)

    def test_verify_takes_the_repo_facts_the_view_already_has(self):
        store, folders, binding = self.own_thread()
        row = thread_folders.read(store.db, 'thread-1')
        info = thread_folders.repo_info(str(self.project))
        calls, counting = self.counted()
        with counting:
            self.assertIsNone(thread_folders.verify(row, str(self.project), info))
        self.assertEqual(sorted(calls), ['rev-parse', 'symbolic-ref', 'worktree'], 'no second read of the repository')

    def test_a_new_thread_reads_the_project_once_for_the_pick_and_the_chip(self):
        store = self.store()
        folders = self.folders(store)
        binding = store.binding('thread-1', 'project-12345678', actor='me')
        calls, counting = self.counted()
        with counting:
            view = folders.view(binding, 'Fix the login bug')
            once = len(calls)
            folders.view(binding, 'Fix the login bug and add a test')
        self.assertEqual((view['choice'], view['repo']), ('own', True))
        self.assertLessEqual(once, 5 + 2, 'one repo read (5 git calls) and the branch name check, not two repo reads')
        self.assertEqual(len(calls), once + 2, 'the second read only checks the new branch name')
        # A request about to start reads the repository fresh.
        calls.clear()
        with counting:
            folders.proposed(binding, 'Fix it')
        self.assertEqual(len(calls), 5)


class AutoCommit(unittest.TestCase):
    """coding_integration.auto_commit: in a thread folder every clean result commits to the branch, whatever the
    project's setting; in the project folder the 16.2 rule stands."""
    def run_it(self, task, own, setting=False):
        import coding_integration
        calls = []
        fake = types.SimpleNamespace(
            folders=types.SimpleNamespace(committing=lambda t: own),
            store=types.SimpleNamespace(binding=lambda **ctx: {'cwd': '/w/t', 'project_cwd': '/p'}),
            conversation=types.SimpleNamespace(full_access=lambda b: {'auto_commit': setting}),
            evidence=types.SimpleNamespace(read=lambda *a, **k: {'attempts': [{'number': 1}]}, review=lambda *a, **k: None),
            commit_record=lambda t, b, how: calls.append(how) or {'sha': 'abc', 'how': how},
            items_done=lambda t: None)
        with patch('native_evidence.settle_carried', lambda *a: None):
            coding_integration.CodingTasks.auto_commit(fake, task)
        return calls

    def task(self, **kw):
        base = {'id': 'task-1', 'origin': 'conversation', 'state': 'ready_for_review', 'context': {'thread_id': 't', 'project_id': 'p', 'actor': 'me'},
                'binding': {'cwd': '/w/t'}, 'collaboration': {}}
        base.update(kw)
        return base

    def test_thread_folder_commits_without_the_setting(self):
        self.assertEqual(self.run_it(self.task(), own=True), ['automatic'])

    def test_project_folder_keeps_the_16_2_rule(self):
        self.assertEqual(self.run_it(self.task(), own=False), [])
        self.assertEqual(self.run_it(self.task(full_access=True, auto_commit=True), own=False, setting=True), ['automatic'])

    def test_unclean_results_still_wait(self):
        for extra in ({'error': 'x'}, {'unrecorded': ['a']}, {'review': 'approved'}, {'carried': {'from_task_id': 'x'}}, {'state': 'blocked'},
                      {'collaboration': {'review': {'phase': 'running'}}}):
            self.assertEqual(self.run_it(self.task(**extra), own=True), [], extra)
        self.assertEqual(self.run_it(self.task(binding={'cwd': '/elsewhere'}), own=True), [])


if __name__ == '__main__':
    unittest.main()
