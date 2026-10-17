import tempfile
import platform_os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dervo_connections import Rejected
from project_files import reveal


class FileLinks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.file = self.project / 'A report café.txt'
        self.file.write_text('fixture')
        self.open = self.enterContext(patch('project_files.subprocess.run', return_value=SimpleNamespace(returncode=0)))

    def test_relative_and_absolute_files_are_revealed_without_execution(self):
        for path in (self.file.name, str(self.file)):
            self.assertTrue(reveal(self.project, path)['revealed'])
            self.assertEqual(self.open.call_args.args[0], platform_os.reveal_command(str(self.file)))

    def test_missing_files_and_relative_links_out_of_the_project_are_refused(self):
        outside = self.root / 'private.txt'
        outside.write_text('outside')
        (self.project / 'escape').symlink_to(outside)
        # Until beta.15 an absolute path outside the project was refused here too; dd #15 changed that (next test).
        for path in ('missing.txt', '../private.txt', 'escape', '\x00bad', str(self.root / 'missing.txt')):
            with self.subTest(path=path), self.assertRaises(Rejected):
                reveal(self.project, path)
        self.open.assert_not_called()

    def test_an_absolute_or_home_path_outside_the_project_is_shown_in_finder_but_never_opened(self):
        """dd #15 (beta.15): agents print paths in worktrees, /private/tmp screenshots and Downloads. Show in Finder only
        selects the item; opening it and Preview stay inside the project."""
        import project_files
        outside = self.root / 'elsewhere' / 'shot.png'
        outside.parent.mkdir()
        outside.write_bytes(b'png')
        self.assertTrue(reveal(self.project, str(outside))['revealed'])
        self.assertEqual(self.open.call_args.args[0], platform_os.reveal_command(str(outside)))
        with patch.dict('os.environ', {'HOME': str(self.root)}):
            self.assertTrue(reveal(self.project, '~/elsewhere/shot.png')['revealed'])
        self.assertEqual(self.open.call_args.args[0], platform_os.reveal_command(str(outside)))
        self.open.reset_mock()
        with self.assertRaisesRegex(Rejected, 'only shows it in Finder'):
            project_files.open_default(self.project, str(outside))
        with self.assertRaisesRegex(Rejected, 'only shows it in Finder'):
            project_files.read_text(self.project, str(outside))
        self.open.assert_not_called()

    def test_a_path_written_from_a_subfolder_finds_its_one_file(self):
        """dd #15: an agent working in frontend/ writes src/local/Home.tsx. One match opens; two are refused, not guessed."""
        home = self.project / 'frontend' / 'src' / 'local' / 'Home.tsx'
        home.parent.mkdir(parents=True)
        home.write_text('x')
        (self.project / 'frontend' / 'src' / 'local' / 'notes.md').write_text('# n')
        self.assertTrue(reveal(self.project, 'src/local/Home.tsx')['revealed'])
        self.assertEqual(self.open.call_args.args[0], platform_os.reveal_command(str(home)))
        self.assertTrue(reveal(self.project, './local/')['revealed'], 'a folder too')
        self.assertEqual(self.open.call_args.args[0], platform_os.reveal_command(str(home.parent)))
        import project_files
        self.assertEqual(project_files.read_text(self.project, 'local/notes.md')['path'], 'frontend/src/local/notes.md')
        other = self.project / 'backend' / 'src' / 'local' / 'Home.tsx'
        other.parent.mkdir(parents=True)
        other.write_text('y')
        with self.assertRaisesRegex(Rejected, 'More than one file'):
            reveal(self.project, 'src/local/Home.tsx')
        # Hidden and dependency folders aren't searched, and a relative path never climbs out.
        (self.project / 'node_modules' / 'pkg').mkdir(parents=True)
        (self.project / 'node_modules' / 'pkg' / 'only.js').write_text('z')
        (self.project / '.git').mkdir()
        (self.project / '.git' / 'config.txt').write_text('z')
        for path in ('pkg/only.js', 'config.txt', '../project/missing/Home.tsx'):
            with self.subTest(path=path), self.assertRaises(Rejected):
                reveal(self.project, path)

    def test_finder_failure_is_reported(self):
        self.open.return_value.returncode = 1
        with self.assertRaisesRegex(Rejected, 'Finder'):
            reveal(self.project, self.file.name)


class ThreadFolderLinks(unittest.TestCase):
    """Bryce, 8 Oct: links from a thread that changes code said "couldn't be found" or opened the old file, because Dervo
    looked in the project folder and the thread works in its own (thread_folders). Disposable stores only."""
    def setUp(self):
        import thread_folders
        from dervo_connections import Store
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.project, self.folder = root / 'project', root / 'Worktrees' / 'thread-1'
        for folder in (self.project, self.folder):
            (folder / 'src').mkdir(parents=True)
            (folder / 'src' / 'Home.md').write_text(f'# {folder.name}')
        (self.project / 'only-here.md').write_text('project')
        (self.folder / 'src' / 'New.md').write_text('new')
        self.store = Store(root / 'store' / 'dervo.sqlite3')
        self.addCleanup(self.store.close)
        (root / 'home').mkdir()
        self.store.add_profile('profile', 'claude', root / 'home', 'me@example.com', 'personal')
        self.store.add_project('project-1', self.project, 'profile', 'claude-opus-5-5')
        self.store.add_thread('thread-1', 'project-1', 'channel', 'shared')
        self.store.add_thread('thread-2', 'project-1', 'channel', 'shared')
        with self.store.db:
            thread_folders.write(self.store.db, {'thread_id': 'thread-1', 'project_id': 'project-1', 'choice': 'own', 'reason': 'code',
                                                 'folder': str(self.folder), 'cwd': str(self.folder), 'branch': 'dervo/t', 'base': 'main',
                                                 'state': 'ready'})
        self.host = SimpleNamespace(store=self.store)

    def text(self, path, **extra):
        import project_files
        return project_files.dispatch(self.host, 'project_file_text', {'projectId': 'project-1', 'path': path, **extra})

    def test_a_thread_with_its_own_folder_opens_its_own_files_first(self):
        self.assertEqual(self.text('src/New.md', threadId='thread-1')['text'], 'new', 'a file the thread made')
        self.assertEqual(self.text('src/Home.md', threadId='thread-1')['text'], '# thread-1', 'its copy, not the old one')
        self.assertEqual(self.text('Home.md', threadId='thread-1')['text'], '# thread-1', 'a short path, in its folder')
        self.assertEqual(self.text(str(self.folder / 'src' / 'New.md'), threadId='thread-1')['path'], 'src/New.md', 'its absolute path')
        self.assertEqual(self.text('only-here.md', threadId='thread-1')['text'], 'project', 'then the project folder')

    def test_a_link_never_needs_the_thread_account(self):
        # Review, 8 Oct: Store.binding also reads the account and the project's Work or Personal side, which a link doesn't.
        with patch.object(self.store, 'binding', side_effect=AssertionError('no account read')):
            self.assertEqual(self.text('src/New.md', threadId='thread-1')['text'], 'new')

    def test_without_a_thread_or_its_own_folder_it_is_the_project_folder(self):
        self.assertEqual(self.text('src/Home.md')['text'], '# project')
        self.assertEqual(self.text('src/Home.md', threadId='thread-2')['text'], '# project')
        for path in ('src/New.md', str(self.folder / 'src' / 'New.md')):
            with self.subTest(path=path), self.assertRaises(Rejected):
                self.text(path)

    def test_a_folder_that_is_gone_falls_back_to_the_project_and_odd_threads_are_refused(self):
        import shutil
        shutil.rmtree(self.folder)
        self.assertEqual(self.text('src/Home.md', threadId='thread-1')['text'], '# project')
        for thread in ('missing', '', 7):
            with self.subTest(thread=thread), self.assertRaises(Rejected):
                self.text('src/Home.md', threadId=thread)
        with self.assertRaises(Rejected):
            self.text('src/Home.md', threadId='thread-1', extra=True)

    def test_preview_lists_what_changed_in_the_thread_folder(self):
        import project_files
        with patch('project_files.changes', return_value={'files': []}) as changes:
            project_files.dispatch(self.host, 'preview_changes', {'projectId': 'project-1', 'since': 0, 'threadId': 'thread-1'})
            self.assertEqual(changes.call_args.args[0], str(self.folder))
            project_files.dispatch(self.host, 'preview_changes', {'projectId': 'project-1', 'since': 0})
            self.assertEqual(changes.call_args.args[0], str(self.project))
