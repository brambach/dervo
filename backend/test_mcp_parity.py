"""MCP parity with a terminal (Harrison, 9 Oct 2026): a full-access run gets the MCP servers `claude` would start in the
same folder, handed over with --mcp-config because --restricted drops every scope on Claude Code 2.1.295. Temporary
folders stand in for the profile and the project. No CLI, model, account or network."""
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest

import claude_coding
import claude_policy
import mcp_connectors
from dervo_connections import Rejected
from test_claude_coding import CLAUDE, SID

USER = {'command': 'npx', 'args': ['-y', 'user-server'], 'env': {'TOKEN': 'SECRET-USER'}}
LOCAL = {'command': 'uvx', 'args': ['local-server']}
SHARED = {'command': 'node', 'args': ['shared.js']}


class Folders(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.home = Path(root.name) / 'profile'
        self.project = Path(root.name) / 'project'
        self.home.mkdir()
        (self.project / '.claude').mkdir(parents=True)

    def profile(self, projects=None, servers=None):
        (self.home / '.claude.json').write_text(json.dumps({'mcpServers': servers if servers is not None else {'user-one': USER},
                                                            'projects': projects or {}}))

    def shared(self, servers):
        (self.project / '.mcp.json').write_text(json.dumps({'mcpServers': servers}))

    def configured(self, project=None, **kw):
        return mcp_connectors.configured(str(self.home), str(project or self.project), **kw)


class ConfiguredTests(Folders):
    def test_user_local_and_approved_project_servers_all_come_through_local_first(self):
        self.shared({'shared-one': SHARED, 'user-one': {'command': 'shadowed-by-project'}})
        self.profile({str(self.project): {'hasTrustDialogAccepted': True, 'enabledMcpjsonServers': ['shared-one', 'user-one'],
                                          'mcpServers': {'local-one': LOCAL, 'shared-one': {'command': 'local-wins'}}}})
        servers = self.configured()
        self.assertEqual(set(servers), {'user-one', 'local-one', 'shared-one'})
        self.assertEqual(servers['shared-one'], {'command': 'local-wins'}, 'local over project, as in Claude Code')
        self.assertEqual(servers['user-one'], {'command': 'shadowed-by-project'}, 'project over user')
        self.assertEqual(servers['local-one'], LOCAL)

    def test_approval_in_settings_local_or_your_own_settings_counts(self):
        self.shared({'a': SHARED, 'b': SHARED, 'c': SHARED})
        self.profile({str(self.project): {'hasTrustDialogAccepted': True}})
        (self.project / '.claude' / 'settings.local.json').write_text(json.dumps({'enabledMcpjsonServers': ['a']}))
        (self.home / 'settings.json').write_text(json.dumps({'enabledMcpjsonServers': ['b'], 'disabledMcpjsonServers': ['a']}))
        self.assertEqual(set(self.configured()), {'user-one', 'b'}, 'disabled wins over enabled')
        (self.home / 'settings.json').write_text(json.dumps({'enableAllProjectMcpServers': True}))
        self.assertEqual(set(self.configured()), {'user-one', 'a', 'b', 'c'})

    def test_a_repository_cant_approve_its_own_servers_or_skip_the_trust_prompt(self):
        self.shared({'evil': {'command': 'curl'}})
        # The repository's committed settings say "approve everything": not a place you control, so it doesn't count.
        (self.project / '.claude' / 'settings.json').write_text(json.dumps({'enableAllProjectMcpServers': True,
                                                                            'enabledMcpjsonServers': ['evil']}))
        self.profile({str(self.project): {'hasTrustDialogAccepted': True}})
        self.assertNotIn('evil', self.configured())
        # Approved but the folder was never trusted: Claude Code shows it as pending, so Dervo leaves it out too.
        self.profile({str(self.project): {'enabledMcpjsonServers': ['evil']}})
        self.assertNotIn('evil', self.configured())
        # Trusting a folder above it doesn't count: Claude Code checks the project's own entry (correctness review 9 Oct).
        self.profile({str(self.project.parent): {'hasTrustDialogAccepted': True}, str(self.project): {'enabledMcpjsonServers': ['evil']}})
        self.assertNotIn('evil', self.configured())

    def test_a_subfolder_is_keyed_by_its_git_root_like_claude_code(self):
        (self.project / '.git').mkdir()
        sub = self.project / 'sub'
        sub.mkdir()
        (sub / '.mcp.json').write_text(json.dumps({'mcpServers': {'both': {'command': 'sub-wins'}}}))
        self.shared({'both': SHARED, 'root-only': SHARED})
        (self.project / '.claude' / 'settings.local.json').write_text(json.dumps({'enableAllProjectMcpServers': True}))
        self.profile({str(self.project): {'hasTrustDialogAccepted': True, 'mcpServers': {'local-one': LOCAL}},
                      str(sub): {'mcpServers': {'ignored': LOCAL}}})
        servers = self.configured(sub)
        self.assertEqual(set(servers), {'user-one', 'local-one', 'both', 'root-only'})
        self.assertEqual(servers['both'], {'command': 'sub-wins'})

    def test_dervos_own_server_name_is_never_taken_and_junk_is_skipped(self):
        self.profile(servers={'dervo': USER, 'Dervo': USER, 'ok': USER, 'bad': 'not a dict', '': USER, 'line\nbreak': USER,
                              'sdk-one': {'type': 'sdk', 'name': 'sdk-one'}})
        self.assertEqual(list(self.configured()), ['ok'])

    def test_a_run_without_its_own_shell_never_gets_project_servers(self):
        # Security review 9 Oct, 2: .mcp.json and settings.local.json are in the folder a run writes, so a run with
        # commands off or a Home job could plant a server a later run starts. Only user and local servers for those.
        self.shared({'shared-one': SHARED})
        (self.project / '.claude' / 'settings.local.json').write_text(json.dumps({'enableAllProjectMcpServers': True}))
        self.profile({str(self.project): {'hasTrustDialogAccepted': True, 'mcpServers': {'local-one': LOCAL}}})
        self.assertEqual(set(self.configured()), {'user-one', 'local-one', 'shared-one'})
        self.assertEqual(set(self.configured(project_scope=False)), {'user-one', 'local-one'})

    def test_a_server_you_switched_off_in_claude_code_stays_off(self):
        self.profile({str(self.project): {'disabledMcpServers': ['user-one'], 'mcpServers': {'local-one': LOCAL}}})
        self.assertEqual(set(self.configured()), {'local-one'})

    def test_nothing_readable_is_nothing(self):
        self.assertEqual(self.configured(), {})
        (self.home / '.claude.json').write_text('{not json')
        self.assertEqual(self.configured(), {})
        # Security review 9 Oct, 4: deeply nested JSON raised RecursionError, not ValueError, and broke the launch.
        self.profile()
        (self.project / '.mcp.json').write_text('[' * 200000 + ']' * 200000)
        self.assertEqual(set(self.configured()), {'user-one'})
        self.assertEqual(mcp_connectors.configured(str(self.home), 'relative/folder'), {})
        self.assertEqual(mcp_connectors.configured(str(self.home), None), {})

    def worktree(self, name='thread-folder', back=True):
        worktree = self.project.parent / name
        worktree.mkdir()
        record = self.project / '.git' / 'worktrees' / name
        record.mkdir(parents=True)
        if back:
            (record / 'gitdir').write_text(f'{worktree}/.git\n')
        (worktree / '.git').write_text(f'gitdir: {record}\n')
        (worktree / '.mcp.json').write_text(json.dumps({'mcpServers': {'shared-one': SHARED}}))
        return worktree

    def test_a_dervo_worktree_gets_the_local_servers_of_its_main_checkout(self):
        worktree = self.worktree()
        (self.project / '.claude' / 'settings.local.json').write_text(json.dumps({'enabledMcpjsonServers': ['shared-one']}))
        self.profile({str(self.project): {'hasTrustDialogAccepted': True, 'mcpServers': {'local-one': LOCAL}}})
        self.assertEqual(set(self.configured(worktree)), {'user-one', 'local-one', 'shared-one'})

    def test_a_git_file_alone_cant_borrow_another_folders_trust(self):
        # Security review 9 Oct, 1: a folder whose .git file names a trusted checkout, without git's record pointing
        # back at it, is not that checkout's worktree.
        fake = self.worktree('copied-in', back=False)
        self.profile({str(self.project): {'hasTrustDialogAccepted': True, 'enabledMcpjsonServers': ['shared-one'],
                                          'mcpServers': {'local-one': LOCAL}}})
        self.assertEqual(set(self.configured(fake)), {'user-one'})


class ServersFileTests(Folders):
    def test_the_file_is_private_and_never_overwritten(self):
        path = mcp_connectors.write_servers({'user-one': USER}, str(self.home))
        self.assertEqual(json.loads(Path(path).read_text()), {'mcpServers': {'user-one': USER}})
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            mcp_connectors.write_servers({}, str(self.home))

    def test_the_launch_names_the_file_and_keeps_every_restriction(self):
        file = str(self.home / 'mcp-servers.json')
        full = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, connectors=['user-one'], servers_file=file)
        self.assertEqual(full[-2:], ['--mcp-config', file])
        self.assertNotIn('SECRET-USER', ' '.join(full), 'server settings stay out of argv')
        for flag in ('--restricted', '--setting-sources', '--disable-slash-commands'):
            self.assertIn(flag, full)
        self.assertIn('mcp__user-one__*', full[full.index('--allowedTools') + 1].split(','))
        home = claude_policy.connectors_argv(CLAUDE, 'model', SID, resume=False, connectors=['user-one'], servers_file=file)
        self.assertEqual(home[-2:], ['--mcp-config', file])
        self.assertNotIn('Bash', home[home.index('--tools') + 1].split(','))
        # Without a file nothing is added, and a relative path is refused.
        self.assertEqual(claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False).count('--mcp-config'), 1)
        with self.assertRaises(Rejected):
            claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, servers_file='servers.json')


class PickTests(Folders):
    def turn(self, allowed):
        adapter = SimpleNamespace(connectors=(), hidden_connectors=(), servers_file=None)
        turn = SimpleNamespace(task=SimpleNamespace(connectors_allowed=allowed, full_access=True, commands=True), adapter=adapter, servers=None,
                               binding={'home': str(self.home), 'cwd': str(self.project)})
        self.addCleanup(lambda: turn.servers and turn.servers.cleanup())
        return turn

    def test_a_run_allows_your_project_servers_and_never_starts_a_hidden_one(self):
        self.profile({str(self.project): {'mcpServers': {'local-one': LOCAL}}})
        turn = self.turn(['claude.ai Gmail', 'local-one'])
        claude_coding.ClaudeCodingTurn._pick_connectors(turn, ['claude.ai Gmail', 'claude.ai Slack'])
        self.assertEqual(turn.adapter.connectors, ['claude.ai Gmail', 'local-one'])
        self.assertEqual(turn.adapter.hidden_connectors, ['claude.ai Slack', 'user-one'])
        written = json.loads(Path(turn.adapter.servers_file).read_text())['mcpServers']
        self.assertEqual(list(written), ['local-one'], 'a hidden server is denied and never written, so it never starts')
        self.assertNotEqual(Path(turn.adapter.servers_file).parent, self.project)

    def test_with_nothing_configured_there_is_no_file(self):
        turn = self.turn(None)
        claude_coding.ClaudeCodingTurn._pick_connectors(turn, ['claude.ai Gmail'])
        self.assertEqual(turn.adapter.connectors, ['claude.ai Gmail'])
        self.assertIsNone(turn.adapter.servers_file)


class PathTests(unittest.TestCase):
    def test_nvms_default_node_is_found_without_a_shell(self):
        with tempfile.TemporaryDirectory() as home:
            versions = Path(home) / '.nvm' / 'versions' / 'node'
            for version in ('v18.20.1', 'v22.3.0', 'v22.11.0', 'v9.0.0'):
                (versions / version / 'bin').mkdir(parents=True)
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v22.11.0' / 'bin'), 'no alias: newest')
            (Path(home) / '.nvm' / 'alias').mkdir()
            (Path(home) / '.nvm' / 'alias' / 'default').write_text('18\n')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v18.20.1' / 'bin'))
            (Path(home) / '.nvm' / 'alias' / 'default').write_text('v22.3.0')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v22.3.0' / 'bin'))
            (Path(home) / '.nvm' / 'alias' / 'default').write_text('lts/iron')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v22.11.0' / 'bin'), 'unresolved: newest')
            (Path(home) / '.nvm' / 'alias' / 'lts').mkdir()
            (Path(home) / '.nvm' / 'alias' / 'lts' / 'iron').write_text('v18.20.1')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v18.20.1' / 'bin'), 'followed through the lts alias')
            (Path(home) / '.nvm' / 'alias' / 'default').write_text('system')
            self.assertIsNone(claude_coding.nvm_default(home), 'system means no nvm Node')
            (Path(home) / '.nvm' / 'alias' / 'default').write_text('../../../etc/passwd')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v22.11.0' / 'bin'))
            (versions / 'v99.0.0').write_text('not a folder')
            self.assertEqual(claude_coding.nvm_default(home), str(versions / 'v22.11.0' / 'bin'), 'a stray file is skipped')
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(claude_coding.nvm_default(home))



class FileLifetimeTests(Folders):
    def test_the_server_file_is_removed_once_claude_code_has_read_it(self):
        # Security review 9 Oct, 5: the file holds your servers' env, so it doesn't outlive the handshake.
        path = mcp_connectors.write_servers({'user-one': USER}, str(self.home))
        adapter = SimpleNamespace(servers_file=path, _mcp_request_id=None, _init_request_id='req_1', state='initializing',
                                  wait_for_connectors=lambda: None)
        claude_coding.ClaudeCodingAdapter._control_result(adapter, {'response': {'subtype': 'success', 'request_id': 'req_1', 'response': {}}})
        self.assertEqual(adapter.state, 'initialized')
        self.assertFalse(Path(path).exists())


if __name__ == '__main__':
    unittest.main()
