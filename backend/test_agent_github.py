"""Agents can't use your GitHub sign-in (b16/git survey, section 2, item 7; must-fix for beta.16). Claude runs deny gh by
rule, and every Claude and Codex run gets an empty GH_CONFIG_DIR with no GitHub token, so gh reads as signed out. The
last test runs the real gh, when it's installed, with a run's environment. No account, model or network."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import claude_coding
import claude_policy
from codex_coding_policy import policy_for
from test_claude_coding import CLAUDE, SID
from transport import clean_environment

GH = next((p for p in ('/opt/homebrew/bin/gh', '/usr/local/bin/gh') if os.access(p, os.X_OK)), None) or shutil.which('gh')


class AgentGitHubTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='dervo-gh-test-')
        self.addCleanup(shutil.rmtree, self.folder, True)

    def test_full_access_claude_denies_gh_however_it_is_named(self):
        full = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, tools=('dervo_update_task_guide',))
        denied = full[full.index('--disallowedTools') + 1].split(',')
        for rule in ('Bash(gh:*)', 'Bash(/opt/homebrew/bin/gh:*)', 'Bash(/usr/local/bin/gh:*)', 'Bash(hub:*)', 'Bash(git credential:*)',
                     'Bash(security find-generic-password:*)', 'Bash(security find-internet-password:*)', 'Bash(/usr/bin/security:*)'):
            self.assertIn(rule, denied)
        self.assertIn('Bash(git push:*)', denied, 'the git write rules stay beside it')
        prompt = full[full.index('--append-system-prompt') + 1]
        self.assertIn('don\'t use gh, git credentials or the Keychain to reach GitHub', prompt)
        # Security review S2, 25 Sep: the prompt doesn't claim gh is signed out, which a steered run could test and disprove.
        self.assertNotIn('isn\'t signed in for you', prompt)
        # With connectors hidden and agent commands off, the rule still goes in.
        bare = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, commands=False, hidden=('Gmail',))
        self.assertIn('Bash(gh:*)', bare[bare.index('--disallowedTools') + 1].split(','))
        # Negative: the default launch has no Bash at all, so there's nothing to deny.
        default = claude_policy.argv(CLAUDE, 'model', SID, resume=False)
        self.assertNotIn('Bash', default[default.index('--tools') + 1].split(','))

    def test_the_guard_drops_every_github_token_and_points_gh_at_an_empty_folder(self):
        env = {'PATH': '/usr/bin', 'GH_TOKEN': 'ghp_x', 'GITHUB_TOKEN': 'ghp_y', 'GH_ENTERPRISE_TOKEN': 'z',
               'GITHUB_ENTERPRISE_TOKEN': 'w', 'GH_HOST': 'github.example.com', 'GH_CONFIG_DIR': str(Path.home() / '.config/gh')}
        claude_coding.github_guard(env, self.folder)
        self.assertEqual(set(env), {'PATH', 'GH_CONFIG_DIR'})
        self.assertEqual(env['GH_CONFIG_DIR'], os.path.join(self.folder, 'dervo-gh'))
        self.assertEqual(os.listdir(env['GH_CONFIG_DIR']), [])
        self.assertEqual(os.stat(env['GH_CONFIG_DIR']).st_mode & 0o777, 0o700)

    def test_codex_commands_see_the_empty_gh_folder_and_no_token(self):
        include = policy_for(full_access=True)['shell_environment_policy']['include_only']
        self.assertIn('GH_CONFIG_DIR', include)
        for key in claude_coding.GH_SECRETS[:-1]:
            self.assertNotIn(key, include)
        self.assertEqual(policy_for(full_access=False)['shell_environment_policy'], {'inherit': 'none'}, 'no shell, nothing forwarded')

    @unittest.skipUnless(GH, 'gh is not installed on this Mac')
    def test_real_gh_reads_as_signed_out_in_a_runs_environment(self):
        # The environment a full-access Claude run gets: your real HOME (for the Keychain), the toolchain PATH and the
        # guard. gh looks only in the empty folder, so it finds no host and never asks the Keychain for a token.
        home = tempfile.mkdtemp(prefix='dervo-gh-profile-')
        self.addCleanup(shutil.rmtree, home, True)
        env = clean_environment('claude', home, home)
        env['PATH'] = claude_coding.full_access_path(env['PATH'])
        env['GH_TOKEN'] = 'not-a-real-token'
        claude_coding.github_guard(env, self.folder)
        result = subprocess.run([GH, 'auth', 'status', '--hostname', 'github.com'], env=env, capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('not logged in', (result.stdout + result.stderr).lower())
        self.assertEqual(os.listdir(env['GH_CONFIG_DIR']), [], 'gh wrote no login into the run folder')


if __name__ == '__main__':
    unittest.main()
