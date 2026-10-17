"""Replies that do the next step (beta.15, group 1): opening a project document, reading Markdown for Preview, the
files git reports since a thread's first run, the terminal's running-command state, and the per-project switch for
agents running commands. Temporary folders, a real temporary git repository, real local PTYs and synthetic Claude Code
peers. No CLI, model, account or network."""
import os
import platform_os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import claude_coding as m
import claude_policy
import conversation_coding
import project_files
from codex_coding import TrustedTaskBinding
from dervo_connections import Rejected
from test_claude_coding import CLAUDE, SID, SyntheticClaudePeer
import test_full_access


class OpeningFiles(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / 'project'
        (self.project / 'evidence').mkdir(parents=True)
        (self.project / 'evidence/mock.html').write_text('<h1>Mock</h1>')
        (self.project / 'notes.md').write_text('# Notes\n\nOne line.')
        self.open = self.enterContext(patch('project_files.subprocess.run', return_value=SimpleNamespace(returncode=0)))

    def test_a_document_opens_with_its_default_app_and_nothing_runnable_does(self):
        self.assertTrue(project_files.open_default(self.project, 'evidence/mock.html')['opened'])
        self.assertEqual(self.open.call_args.args[0], platform_os.open_command(str(self.project / 'evidence/mock.html')))
        self.assertTrue(project_files.open_default(self.project, str(self.project / 'notes.md'))['opened'], 'absolute paths inside the project too')
        self.open.reset_mock()
        # Scripts, apps, shortcuts, folders, hidden files and anything outside the project never reach `open`.
        (self.project / 'run.command').write_text('#!/bin/sh\necho hi\n')
        (self.project / 'Tool.app').mkdir()
        (self.project / 'link.webloc').write_text('<plist/>')
        (self.project / 'setup.sh').write_text('echo hi\n')
        (self.project / '.env').write_text('SECRET=1')
        (self.project / '.hidden').mkdir()
        (self.project / '.hidden/page.html').write_text('<p>hidden</p>')
        outside = self.root / 'outside.html'
        outside.write_text('<p>outside</p>')
        (self.project / 'escape.html').symlink_to(outside)
        for path in ('run.command', 'Tool.app', 'link.webloc', 'setup.sh', '.env', '.hidden/page.html', 'evidence',
                     str(outside), '../outside.html', 'escape.html', 'missing.pdf', 'bad\x00.html', ''):
            with self.subTest(path=path), self.assertRaises(Rejected):
                project_files.open_default(self.project, path)
        self.open.assert_not_called()

    def test_markdown_is_read_as_text_with_a_size_cap_and_nothing_else_is(self):
        self.assertEqual(project_files.read_text(self.project, 'notes.md'), {'path': 'notes.md', 'text': '# Notes\n\nOne line.', 'truncated': False})
        (self.project / 'big.md').write_bytes(b'a' * (project_files.TEXT_LIMIT + 10))
        big = project_files.read_text(self.project, 'big.md')
        self.assertTrue(big['truncated'])
        self.assertEqual(len(big['text']), project_files.TEXT_LIMIT)
        for path in ('evidence/mock.html', '.env', '../x.md'):
            with self.subTest(path=path), self.assertRaises(Rejected):
                project_files.read_text(self.project, path)


def git(cwd, *args):
    return subprocess.run(['/usr/bin/git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


class ShellMadeFiles(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve()
        git(self.project, 'init', '-q')  # git status needs no commit, so the fixture makes none
        (self.project / '.gitignore').write_text('build/\n')

    def test_previewable_files_changed_since_the_first_run_are_listed_newest_first(self):
        old = self.project / 'old.png'
        old.write_bytes(b'png')
        os.utime(old, (1000, 1000))
        since = time.time() - 5
        (self.project / 'evidence/shots').mkdir(parents=True)
        (self.project / 'evidence/shots/home.png').write_bytes(b'png')
        (self.project / 'evidence/mock.html').write_text('<p>mock</p>')
        later = time.time() + 1
        os.utime(self.project / 'evidence/mock.html', (later, later))
        (self.project / 'report.md').write_text('# Report')
        (self.project / 'script.py').write_text('print(1)')
        (self.project / 'build').mkdir()
        (self.project / 'build/ignored.html').write_text('<p>ignored</p>')
        (self.project / '.cache').mkdir()
        (self.project / '.cache/page.html').write_text('<p>hidden</p>')
        files = project_files.changes(self.project, since)['files']
        self.assertEqual([f['path'] for f in files][0], 'evidence/mock.html', 'newest first')
        self.assertEqual(sorted(f['path'] for f in files), ['evidence/mock.html', 'evidence/shots/home.png', 'report.md'])
        # Before the thread's first run, older files come too; bad input is refused.
        self.assertIn('old.png', [f['path'] for f in project_files.changes(self.project, 0)['files']])
        for bad in (-1, 'now', None):
            with self.assertRaises(Rejected):
                project_files.changes(self.project, bad)

    def test_a_slow_git_answers_an_empty_list_well_inside_the_bridges_ten_seconds(self):
        """The read runs on the host's one request loop; git's own 30 s a call would reach the bridge's kill."""
        calls = []
        def hung(argv, **kwargs):
            calls.append(kwargs.get('timeout'))
            time.sleep(kwargs['timeout'])
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        began = time.monotonic()
        with patch.object(project_files, 'CHANGES_SECONDS', .3):
            self.assertEqual(project_files.changes(self.project, 0, run=hung), {'files': []})
        self.assertLess(time.monotonic() - began, 1.5, 'the budget covers every call together')
        self.assertTrue(calls and all(timeout <= .3 for timeout in calls), calls)

    def test_a_folder_outside_git_lists_nothing_rather_than_failing(self):
        with tempfile.TemporaryDirectory() as plain:
            (Path(plain) / 'page.html').write_text('<p>x</p>')
            self.assertEqual(project_files.changes(plain, 0), {'files': []})


class RunningCommand(unittest.TestCase):
    def setUp(self):
        from test_project_terminal import TerminalTests
        # The terminal tests' own disposable host, projects and /bin/sh account, borrowed rather than copied.
        self.fixture = TerminalTests('test_project_cwd_tty_sanitized_environment_and_no_automatic_command')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(lambda: self.fixture.host.terminals.close() if hasattr(self.fixture.host, 'terminals') else None)

    def test_the_header_knows_a_command_is_running_and_when_the_prompt_is_back(self):
        f = self.fixture
        row = f.open()
        self.assertIsNone(f.host.dispatch('terminal_list', {'projectId': 'one'})['sessions'][0]['foreground'], 'at the prompt')
        f.send(row, 'sleep 3\r')
        f.wait(lambda: f.host.dispatch('terminal_list', {'projectId': 'one'})['sessions'][0]['foreground'] == 'sleep')
        f.send(row, '\x03')
        f.wait(lambda: f.host.dispatch('terminal_list', {'projectId': 'one'})['sessions'][0]['foreground'] is None)


class AgentCommandsPolicy(unittest.TestCase):
    def test_commands_off_leaves_bash_out_and_says_so(self):
        off = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, tools=('dervo_update_task_guide',), commands=False)
        self.assertEqual(off[off.index('--tools') + 1], 'Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Agent,ToolSearch')
        self.assertNotIn('Bash', off[off.index('--allowedTools') + 1].split(','))
        rules = off[off.index('--append-system-prompt') + 1]
        self.assertIn('no Bash tool', rules)
        self.assertNotIn('your own Bash', rules)
        self.assertNotIn('—', rules)
        on = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, tools=('dervo_update_task_guide',))
        self.assertIn('Bash', on[on.index('--tools') + 1].split(','), 'on by default')
        self.assertNotIn('no Bash tool', on[on.index('--append-system-prompt') + 1])
        with self.assertRaises(Rejected):
            claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, commands='no')

    def test_only_claude_code_runs_full_access_without_commands(self):
        allow = lambda task, request: True
        TrustedTaskBinding('t', {'provider': 'claude'}, allow, full_access=True, commands=False)
        with self.assertRaises(Rejected):
            TrustedTaskBinding('t', {'provider': 'codex'}, allow, full_access=True, commands=False)
        with self.assertRaises(Rejected):
            TrustedTaskBinding('t', {'provider': 'claude'}, allow, full_access=True, commands='off')


class AgentCommandsSetting(test_full_access.HostTests):
    def test_on_by_default_off_leaves_the_shell_out_and_a_change_stops_older_tasks(self):
        self.grant()
        view = self.host.dispatch('coding_permission', self.scope)['full_access']
        self.assertTrue(view['commands'], 'on for whoever has full access')
        self.script = lambda peer: peer.finish(text='Nothing to do.')
        self.run_request()
        self.assertIn('Bash', self.peers[-1].argv[self.peers[-1].argv.index('--tools') + 1].split(','))
        with self.assertRaisesRegex(Rejected, 'exactly on'):
            self.host.dispatch('coding_commands_set', {**self.scope, 'enabled': 'no'})
        off = self.host.dispatch('coding_commands_set', {**self.scope, 'enabled': False})['full_access']
        self.assertFalse(off['commands'])
        task = self.run_request('Another change')['task']
        self.assertIs(task['commands'], False)
        # Still a full-access task: it asks for approval as one, not only as a manual-review check (the Codex branch
        # beside it once took this line).
        self.assertEqual(task['acceptance'], {'required': True, 'source': 'full-access'})
        argv = self.peers[-1].argv
        self.assertNotIn('Bash', argv[argv.index('--tools') + 1].split(','))
        self.assertIn('no Bash tool', argv[argv.index('--append-system-prompt') + 1])
        binding = self.host.store.binding(thread_id='c', project_id='p', actor='local-user')
        self.assertIsNone(self.host.coding.conversation.full_access_problem(task, binding))
        self.host.dispatch('coding_commands_set', {**self.scope, 'enabled': True})
        self.assertEqual(self.host.coding.conversation.full_access_problem(task, binding), 'Agent commands changed in Access. Start a new request.')
        # A task from before the setting existed counts as on.
        self.assertIsNone(self.host.coding.conversation.full_access_problem({k: v for k, v in task.items() if k != 'commands'}, binding))

    def test_it_needs_full_access_and_is_a_project_setting(self):
        self.grant(full=False)
        with self.assertRaisesRegex(Rejected, 'Turn on full access first'):
            self.host.dispatch('coding_commands_set', {**self.scope, 'enabled': False})
        self.assertFalse(self.host.dispatch('coding_permission', self.scope)['full_access']['commands'])
        self.assertIn('coding_commands_set', conversation_coding.PROJECT_OPS)


def load_tests(loader, tests, pattern):
    """Only this file's tests: AgentCommandsSetting borrows the full-access host fixture, not its tests."""
    suite = unittest.TestSuite()
    for case in (OpeningFiles, ShellMadeFiles, RunningCommand, AgentCommandsPolicy, AgentCommandsSetting):
        suite.addTests(case(name) for name in sorted(vars(case)) if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main()
