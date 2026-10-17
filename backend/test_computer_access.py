"""Explicit account-bound outside-project grants, using disposable stores and synthetic providers."""
from unittest.mock import patch
import unittest
from test_full_access import HostTests as _FullAccessHost, CLAUDE, SID, BINDING
from codex_coding import TrustedTaskBinding
from dervo_connections import Rejected
import claude_policy


class ComputerAccessTests(unittest.TestCase):
    setUp = _FullAccessHost.setUp
    grant = _FullAccessHost.grant
    def test_computer_access_is_off_until_explicit_and_bound_to_account(self):
        self.grant()
        view = self.host.dispatch('coding_permission', self.scope)['full_access']
        self.assertFalse(view['computer_access'])
        result = self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': True})
        self.assertTrue(result['full_access']['computer_access'])
        binding = self.host.store.binding(project_id='p', thread_id='c', actor='local-user')
        conversation = self.host.coding.conversation
        for field, other in [('profile_id', 'other'), ('identity', 'someone@example.test'), ('home', '/other/account')]:
            self.assertFalse(conversation.full_access({**binding, field: other})['active'])
            self.assertFalse(conversation.full_access({**binding, field: other})['computer_access'])
        self.assertIsNone(conversation.full_access_problem({'origin':'conversation','computer_access':True}, binding))
        self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': False})
        self.assertIn('Computer access changed', conversation.full_access_problem({'origin':'conversation','computer_access':True}, binding))
        self.assertFalse(self.host.dispatch('coding_permission', self.scope)['full_access']['computer_access'])

    def test_computer_access_requires_full_access_and_cannot_change_during_a_run(self):
        self.grant(False)
        with self.assertRaises(Rejected): self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': True})
        self.grant()
        with patch.object(self.host.coding.conversation, 'busy', return_value=True):
            with self.assertRaisesRegex(Rejected, 'Stop the running task'):
                self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': True})
        for value in (1, 'yes', None):
            with self.assertRaises(Rejected): self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': value})

    def test_thread_controls_cannot_change_project_permissions_while_another_thread_runs(self):
        import json
        from types import SimpleNamespace
        self.grant()
        db = self.host.store.db
        record = dict(id='other-running-task', context=dict(project_id='p', thread_id='other'))
        with db: db.execute('INSERT INTO coding_tasks VALUES (?,?)', (record['id'], json.dumps(record)))
        try:
            with patch.dict(self.host.coding.turns, {record['id']: SimpleNamespace(closed=False)}):
                for operation, fields in [('coding_full_access_set', {'enabled':False}),
                                          ('coding_computer_access_set', {'enabled':True}),
                                          ('coding_connectors_set', {'connectors':[]})]:
                    with self.subTest(operation=operation), self.assertRaisesRegex(Rejected, '[Ss]top the running task'):
                        self.host.dispatch(operation, {**self.scope, **fields})
        finally:
            with db: db.execute('DELETE FROM coding_tasks WHERE id=?', (record['id'],))

    def test_queued_request_cannot_gain_computer_access(self):
        self.grant()
        conversation = self.host.coding.conversation
        binding = self.host.store.binding(project_id='p', thread_id='c', actor='local-user')
        ctx = dict(project_id='p', thread_id='c', actor='local-user')
        args = dict(clientId='queued-computer', text='Explain the greeting')
        conversation.queue.add(args, ctx, binding)
        queued = conversation.queue.find('c', 'queued-computer')
        self.assertFalse(queued['computer_access'])
        self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': True})
        with self.assertRaisesRegex(Rejected, 'Computer access changed while this request waited'):
            conversation.request(args, ctx, binding, starting=queued)

    def test_disabling_full_access_removes_computer_permission(self):
        self.grant()
        self.host.dispatch('coding_computer_access_set', {**self.scope, 'enabled': True})
        self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': False})
        self.grant()
        self.assertFalse(self.host.dispatch('coding_permission', self.scope)['full_access']['computer_access'])


del _FullAccessHost


class ComputerPolicyTests(unittest.TestCase):
    def test_only_the_explicit_mode_adds_root_and_keeps_current_request_rules(self):
        normal = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, shots='/tmp/shots')
        full = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, computer_access=True)
        self.assertEqual(normal[normal.index('--add-dir')+1], '/tmp/shots')
        self.assertEqual(full[full.index('--add-dir')+1], '/')
        self.assertIn('--restricted', full)
        prompt = full[full.index('--append-system-prompt')+1]
        self.assertIn('only as needed for the current request', prompt)
        self.assertIn('Dervo cannot undo files outside this project', prompt)
        self.assertNotIn('or touch anything outside the project folder', prompt)
        self.assertIn('or touch anything outside the project folder', normal[normal.index('--append-system-prompt')+1])
        with self.assertRaises(Rejected):
            TrustedTaskBinding('t', {**BINDING,'provider':'codex'}, lambda *_:True, full_access=True, computer_access=True)
        with self.assertRaises(Rejected):
            TrustedTaskBinding('t', BINDING, lambda *_:True, computer_access=True)


if __name__ == '__main__': unittest.main()
