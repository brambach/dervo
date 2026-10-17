"""Beta.16's shared operations (shared_operations.py): b16/git's are one list in the host, the Rust allowlist and the
renderer's github.ts; b16/lifecycle's keep its names (Bryce, 25 Sep) and reach the renderer through the Rust allowlist;
the host checks fields and types before b16/git's handler sees them; an operation whose handler hasn't landed says so
and does nothing; archived projects and threads keep their rules. A real Host on a temporary store. No account, model,
gh or network."""
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import shared_operations as m
from dervo_connections import Rejected
from host import Host

APP = Path(__file__).resolve().parent.parent


class SharedOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        (root / 'source').mkdir()
        self.host = Host(root / 'data')
        self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', root / 'source', 'account', 'test-model')
        self.channel = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Saved', 'private': False})['id']
        self.calls = []

    def owner(self, name, shared):
        """A handler for ``shared`` in the owner's module, as the owning thread would add: on the real module where it
        exists, else on a stand-in (github_host isn't written yet)."""
        import importlib
        handler = lambda host, operation, args, actor: self.calls.append((operation, args, actor)) or {'ok': operation}
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            module = types.ModuleType(name)
            sys.modules[name] = module
            self.addCleanup(sys.modules.pop, name, None)
        self.enterContext(patch.object(module, 'SHARED', set(shared), create=True))
        self.enterContext(patch.object(module, 'shared_dispatch', handler, create=True))

    def rust_names(self):
        return set(re.findall(r'Self::\w+=>"(\w+)"', (APP / 'src/bridge.rs').read_text()))

    def test_the_host_the_rust_allowlist_and_the_renderer_name_the_same_operations(self):
        names = self.rust_names()
        renderer = (APP / 'frontend/src/local/github.ts').read_text()
        called = set(re.findall(r"local<.*?>\('(\w+)'", renderer))
        for operation in m.OPERATIONS:
            self.assertIn(operation, names, f'{operation} missing from src/bridge.rs')
            self.assertIn(operation, called, f'{operation} missing from github.ts')
        for spec in m.OPERATIONS.values():
            for field in spec['required'] | spec['optional']:
                self.assertIn(field, m.TYPES, f'{field} has no type check')
        self.assertEqual({spec['owner'] for spec in m.OPERATIONS.values()}, {'b16/git'})

    def test_lifecycles_operations_keep_its_names_and_its_own_checks(self):
        names = self.rust_names()
        self.assertEqual(len(set(m.LIFECYCLE)), 16)
        for operation in m.LIFECYCLE:
            self.assertIn(operation, names, f'{operation} missing from src/bridge.rs')
        # The first names this branch gave them are gone, so a caller still using one fails at the bridge.
        for retired in ('next_up_list', 'next_up_drop', 'conversation_pin_set', 'project_tidy_get', 'tester_reports_list', 'tester_report_seen'):
            self.assertNotIn(retired, names)
        # No shared spec shadows one: b16/lifecycle's own FIELDS checks their fields, not shared_operations.
        self.assertFalse(set(m.LIFECYCLE) & set(m.OPERATIONS))
        from project_lifecycle import FIELDS
        self.assertFalse(set(m.OPERATIONS) & set(FIELDS))
        # Once b16/lifecycle has merged, its FIELDS routes exactly this list: a rename on either side fails here.
        routed = set(m.LIFECYCLE) & set(FIELDS)
        if routed:
            self.assertEqual(routed, set(m.LIFECYCLE))

    def test_an_operation_without_its_handler_says_so_and_changes_nothing(self):
        # A build without the owner's module: github_host is merged now, so this hides it for these two calls.
        with patch.dict(sys.modules, {'github_host': None}):
            for operation, args in (('github_status', {'projectId': 'one'}),
                                    ('pr_status', {'projectId': 'one', 'channelId': self.channel})):
                with self.assertRaisesRegex(Rejected, 'isn’t in this build'):
                    self.host.dispatch(operation, args)
        # A module that exists but hasn't listed the operation is the same: a partial landing never half-answers.
        self.owner('github_host', {'github_status'})
        with self.assertRaisesRegex(Rejected, 'isn’t in this build'):
            self.host.dispatch('pr_create', {'projectId': 'one', 'channelId': self.channel, 'previewId': 'p1', 'draft': True})
        self.assertEqual(self.calls, [])

    def test_the_owner_gets_exactly_the_checked_fields(self):
        self.owner('github_host', {'pr_create', 'github_status'})
        self.assertEqual(self.host.dispatch('github_status', {'projectId': 'one', 'recheck': True}), {'ok': 'github_status'})
        self.host.dispatch('pr_create', {'projectId': 'one', 'channelId': self.channel, 'previewId': 'p1', 'draft': False})
        self.assertEqual([c[0] for c in self.calls], ['github_status', 'pr_create'])
        self.assertEqual(self.calls[1][1], {'projectId': 'one', 'channelId': self.channel, 'previewId': 'p1', 'draft': False})

    def test_wrong_fields_and_types_never_reach_the_owner(self):
        self.owner('github_host', m.OPERATIONS.keys())
        bad = [
            ('github_status', {'recheck': True}),                                                              # no project
            ('github_status', {'projectId': 'x' * 65}),
            ('github_status', {'projectId': 'one', 'recheck': 'yes'}),
            ('pr_preview', {'projectId': 'one', 'channelId': self.channel, 'mode': 'force'}),
            ('pr_create', {'projectId': 'one', 'channelId': self.channel, 'previewId': 'p1', 'draft': True, 'force': True}),
            ('pr_create', {'projectId': 'one', 'channelId': self.channel, 'draft': True}),                     # no confirm id
            ('pr_status', ['projectId']),
        ]
        for operation, args in bad:
            with self.assertRaises(Rejected, msg=f'{operation} {args}'):
                self.host.dispatch(operation, args)
        self.assertEqual(self.calls, [])

    def test_archived_threads_and_projects_keep_their_rules(self):
        self.owner('github_host', {'pr_status', 'pr_preview'})
        self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': self.channel})
        # Reading a PR works in an archived thread (History keeps archived threads, with their PRs).
        self.host.dispatch('pr_status', {'projectId': 'one', 'channelId': self.channel})
        # Opening a PR from one waits for Restore.
        with self.assertRaisesRegex(Rejected, 'archived'):
            self.host.dispatch('pr_preview', {'projectId': 'one', 'channelId': self.channel, 'mode': 'thread'})
        self.host.dispatch('conversation_restore', {'projectId': 'one', 'channelId': self.channel})
        self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'project is archived'):
            self.host.dispatch('pr_status', {'projectId': 'one', 'channelId': self.channel})
        self.assertEqual([c[0] for c in self.calls], ['pr_status'])


if __name__ == '__main__':
    unittest.main()
