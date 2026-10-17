"""A4: the Claude plan an account is on reaches the model picker, so Fable's paid credits are never a surprise.
Fake auth output and synthetic peers only; no CLI, no model."""
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import claude_coding
import claude_policy
import connections_ui
import first_run
import provider_plans
import transport
from codex_coding import TrustedTaskBinding
from dervo_connections import Rejected
from host import Host
from test_claude_coding import BINDING, CLAUDE, SyntheticClaudePeer, VERIFIED, verified_auth
from test_conversation_coding import ConversationCodingTests


class PlanRecordTests(unittest.TestCase):
    def test_plan_of_takes_only_a_short_plain_plan_string(self):
        self.assertEqual(provider_plans.plan_of({'subscriptionType': 'pro'}), 'pro')
        for bad in ({'subscriptionType': ''}, {'subscriptionType': 'x' * 33}, {'subscriptionType': 7}, {'subscriptionType': 'pro\n'}, {}, None, 'pro'):
            self.assertIsNone(provider_plans.plan_of(bad), bad)

    def test_record_keeps_the_latest_plan_per_profile(self):
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        self.assertEqual(provider_plans.record(db, 'a', {'subscriptionType': 'pro'}, at=1), 'pro')
        self.assertEqual(provider_plans.record(db, 'b', {'subscriptionType': 'max'}, at=2), 'max')
        self.assertIsNone(provider_plans.record(db, 'a', {}, at=3), 'no plan in the metadata records nothing')
        self.assertIsNone(provider_plans.record(db, '', {'subscriptionType': 'pro'}, at=3))
        self.assertEqual(provider_plans.plans(db), {'a': 'pro', 'b': 'max'})
        provider_plans.record(db, 'a', {'subscriptionType': 'max'}, at=4)
        self.assertEqual(provider_plans.plans(db)['a'], 'max')
        self.assertEqual(db.execute('SELECT at FROM profile_plans WHERE profile_id=?', ('a',)).fetchone()[0], 4)

    def test_fable_costs_credits_on_pro_but_team_seat_is_unknown(self):
        self.assertTrue(provider_plans.fable_costs_credits('pro'))
        self.assertFalse(provider_plans.fable_costs_credits('team'), 'Team alone does not establish Standard or Premium')
        self.assertFalse(provider_plans.fable_costs_credits('max'))
        self.assertFalse(provider_plans.fable_costs_credits(None))

    def test_the_best_alias_is_refused_before_any_launch(self):
        # On Pro the alias resolves to Fable, which print mode bills without asking. Exact IDs only.
        sid = '00000000-0000-4000-8000-00000000c1a0'
        with self.assertRaisesRegex(Rejected, 'best alias'):
            claude_policy.argv(CLAUDE, 'best', sid, resume=False)
        with self.assertRaisesRegex(Rejected, 'best alias'):
            claude_policy.full_access_argv(CLAUDE, 'best', sid, resume=False)
        self.assertIn('--model', claude_policy.argv(CLAUDE, 'claude-fable-5-1', sid, resume=False))


class HostPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.host = Host(self.base / 'store'); self.addCleanup(self.host.close)

    def test_profiles_carry_the_plan_the_check_reported_and_the_check_result_does_not(self):
        self.host.store.add_profile('c', 'claude', self.host.root / 'profiles' / 'c', 'me@example.test', 'personal')
        self.host.store.add_profile('x', 'codex', self.host.root / 'profiles' / 'x', 'me@example.test', 'personal')
        self.assertEqual([p['plan'] for p in self.host.dispatch('profiles', {})], [None, None], 'unknown until a check or run reports it')
        probed = {'state': 'identity_verified', 'canSend': False, 'detail': 'verified', 'plan': 'pro'}
        with patch.object(connections_ui, 'probe', lambda profile, *a, **k: dict(probed)):
            self.host.dispatch('connection_check', {'profileId': 'c'})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = self.host.dispatch('connection_status', {'profileId': 'c'})
                if result['state'] != 'checking':
                    break
                time.sleep(.01)
        self.assertEqual(result, {'state': 'identity_verified', 'canSend': False, 'detail': 'verified'}, 'the plan is kept, not shown as identity')
        self.assertEqual({p['id']: p['plan'] for p in self.host.dispatch('profiles', {})}, {'c': 'pro', 'x': None})

    def test_the_claude_probe_carries_the_plan_only_when_auth_status_names_one(self):
        profile = {'provider': 'claude', 'home': str(self.base / 'store/profiles/c'), 'identity': 'me@example.test', 'category': 'personal', 'method': 'subscription'}
        meta = {**VERIFIED, 'email': 'me@example.test', 'subscriptionType': 'pro'}
        with patch.object(claude_policy, 'executable', lambda: CLAUDE), patch.object(claude_policy, 'auth_status', lambda *a, **k: dict(meta)):
            self.assertEqual(connections_ui.probe(profile)['plan'], 'pro')
            meta.pop('subscriptionType')
            self.assertNotIn('plan', connections_ui.probe(profile))

    def test_first_run_records_the_existing_sign_ins_plan(self):
        home = self.base / 'home'; (home / '.claude').mkdir(parents=True)
        with patch.object(transport, 'account_home', lambda: str(home)), \
             patch.object(first_run, '_claude', lambda host, run=None, **_: dict(state='ready', email='me@example.test', plan='team', detail='')):
            created = first_run.use_claude(self.host)
        self.assertEqual(provider_plans.plans(self.host.store.db), {created['id']: 'team'})


class TurnPlanTests(unittest.TestCase):
    def test_a_claude_run_reports_the_plan_auth_status_returned(self):
        events = []
        with patch.object(claude_coding, 'OwnedProcess', SyntheticClaudePeer), patch.object(claude_coding, 'auth_status', verified_auth), \
             patch.object(claude_coding, 'executable', lambda: CLAUDE):
            binding = {**BINDING, 'session_id': None}
            turn = claude_coding.ClaudeCodingTurn(binding, task=TrustedTaskBinding('t', binding, lambda t, r: True),
                                                  context=lambda: ('x', 'run-1'), emit=lambda k, d: events.append((k, d)))
            turn.close()
        self.assertIn(('plan', {'plan': VERIFIED['subscriptionType']}), events)
        self.assertLess([k for k, _ in events].index('plan'), [k for k, _ in events].index('session'), 'known before any content is sent')


class RunRecordsPlan(ConversationCodingTests):
    provider = 'claude'

    def test_a_conversation_run_records_the_plan_for_the_picker(self):
        self.grant()
        self.assertEqual(provider_plans.plans(self.host.store.db), {})
        with self.peer_patch('none'):
            self.settle(self.request('Inspect the greeting.')['taskId'])
        self.assertEqual(provider_plans.plans(self.host.store.db), {'p': VERIFIED['subscriptionType']})
        self.assertEqual(self.host.dispatch('profiles', {})[0]['plan'], 'max')


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (PlanRecordTests, HostPlanTests, TurnPlanTests):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    suite.addTest(RunRecordsPlan('test_a_conversation_run_records_the_plan_for_the_picker'))
    return suite
