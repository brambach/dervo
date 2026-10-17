"""Full access on Windows (b16/windows milestone 4): the agent's instructions, computer access, the gh and credential
denies, the warning, and the PATH a run gets. The launch and warning tests run on a Mac too, with platform_os.WINDOWS
patched, and check the Mac's own launch is unchanged. The PATH test needs Windows (CreateEnvironmentBlock). No CLI,
account, network or model call."""
import os
import tempfile
import unittest
from unittest.mock import patch

import claude_coding
import claude_policy
import platform_os
from conversation_coding import ConversationCoding
from test_claude_coding import SID


SHOTS = os.path.join(os.path.realpath(tempfile.gettempdir()), 'dervo-claude-1')  # absolute on this machine
# test_claude_coding's '/fixture/bin dir/claude' has no drive, so Windows doesn't count it as absolute.
CLAUDE = os.path.join(os.path.realpath(tempfile.gettempdir()), 'fixture bin dir', 'claude')


def launch(**kwargs):
    return claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, tools=('dervo_update_task_guide',), **kwargs)


class WindowsLaunchTests(unittest.TestCase):
    def test_the_mac_launch_keeps_its_screenshot_commands_root_and_denies(self):
        with patch.object(platform_os, 'WINDOWS', False):
            full = launch(computer_access=True)
        rules = full[full.index('--append-system-prompt') + 1]
        self.assertIn(claude_policy.MAC_SCREENSHOTS, claude_policy.FULL_ACCESS_CONTEXT, 'the Windows swap has something to replace')
        self.assertIn(claude_policy.MAC_SCREENSHOTS, rules)
        self.assertIn('macOS permissions still apply.', rules)
        self.assertEqual(full[full.index('--add-dir') + 1], '/')
        self.assertNotIn('gh.exe', full[full.index('--disallowedTools') + 1])

    def test_windows_agents_get_no_mac_commands_and_are_told_bash_is_git_bash(self):
        with patch.object(platform_os, 'WINDOWS', True), patch.object(platform_os, 'account_home', lambda: 'C:\\Users\\tester'):
            full = launch(shots=SHOTS)
            computer = launch(computer_access=True)
        for args in (full, computer):
            rules = args[args.index('--append-system-prompt') + 1]
            for mac in ('screencapture', 'sips', 'macOS'):
                self.assertNotIn(mac, rules)
            self.assertIn(claude_policy.WINDOWS_SCREENSHOTS, rules)
            self.assertIn('DERVO_SCREENSHOTS', rules)
            self.assertNotIn('\u2014', rules)
        self.assertIn('Windows permissions still apply.', computer[computer.index('--append-system-prompt') + 1])
        self.assertEqual(full[full.index('--add-dir') + 1], SHOTS)

    def test_windows_computer_access_opens_the_home_drive_not_slash(self):
        with patch.object(platform_os, 'WINDOWS', True), patch.object(platform_os, 'account_home', lambda: 'D:\\Users\\tester'):
            computer = launch(computer_access=True, shots=SHOTS)
        self.assertEqual([computer[i + 1] for i, arg in enumerate(computer) if arg == '--add-dir'], ['D:\\'],
                         'computer access covers the screenshot folder, as on a Mac')

    def test_windows_denies_gh_exe_and_git_credential_manager_as_well(self):
        with patch.object(platform_os, 'WINDOWS', True), patch.object(platform_os, 'account_home', lambda: 'C:\\Users\\tester'):
            full = launch()
        denied = full[full.index('--disallowedTools') + 1].split(',')
        for rule in claude_policy.GH_DENIED + claude_policy.WINDOWS_GH_DENIED + ('Bash(git commit:*)', 'Bash(git push:*)'):
            self.assertIn(rule, denied)

    def test_the_warning_says_pc_on_windows_under_the_same_token(self):
        with patch.object(platform_os, 'WINDOWS', False):
            self.assertEqual(ConversationCoding.full_access_detail(), ConversationCoding.FULL_ACCESS_DETAIL)
        with patch.object(platform_os, 'WINDOWS', True):
            detail = ConversationCoding.full_access_detail()
        self.assertNotIn('Mac', detail)
        self.assertIn('runs commands on your PC as you', detail)
        self.assertIn('sign-ins saved on this PC, Dervo’s included', detail)
        self.assertEqual(ConversationCoding.FULL_ACCESS_WARNING, 'beta.15', 'wording only, so nobody has to confirm again')


@unittest.skipUnless(platform_os.WINDOWS, 'The account PATH comes from CreateEnvironmentBlock, which is Windows only.')
class WindowsPathTests(unittest.TestCase):
    def test_a_full_access_run_gets_the_accounts_own_path_ahead_of_the_fixed_one(self):
        base = platform_os.provider_environment()['PATH']
        account = {name.upper(): value for name, value in platform_os.account_environment().items()}['PATH']
        path = claude_coding.full_access_path(base).split(os.pathsep)
        keys = [os.path.normcase(p.rstrip('\\/')) for p in path]
        self.assertEqual(len(keys), len(set(keys)), 'each folder once')
        for folder in account.split(os.pathsep) + base.split(os.pathsep):
            if folder.strip():
                self.assertIn(os.path.normcase(folder.strip().rstrip('\\/')), keys)
        system32 = os.path.normcase(os.path.join(os.environ.get('SYSTEMROOT', 'C:\\Windows'), 'System32'))
        self.assertIn(system32, keys)

    def test_only_path_comes_from_the_account_and_a_failure_leaves_the_fixed_path(self):
        home = platform_os.account_home()
        extra = {os.path.normcase(os.path.join(home, *parts)) for parts in (('.local', 'bin'), ('.cargo', 'bin'), ('.bun', 'bin'))}
        with patch.object(platform_os, 'account_environment', return_value={'Path': 'C:\\Tools', 'GH_TOKEN': 'PRIVATE-FIXTURE'}):
            given = claude_coding.full_access_path('C:\\Windows\\System32').split(os.pathsep)
        self.assertEqual(given[-2:], ['C:\\Tools', 'C:\\Windows\\System32'], 'the account’s Path, found whatever its case')
        self.assertNotIn('PRIVATE-FIXTURE', os.pathsep.join(given))
        with patch.object(platform_os, 'account_environment', side_effect=OSError('no token')):
            failed = claude_coding.full_access_path('C:\\Windows\\System32').split(os.pathsep)
        self.assertEqual(failed[-1], 'C:\\Windows\\System32')
        self.assertLessEqual({os.path.normcase(p) for p in failed[:-1]}, extra, 'nothing but the fixed per-user folders')


if __name__ == '__main__':
    unittest.main()
