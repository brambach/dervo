"""Usage figures come only from provider reports. Disposable store, fixed clock, no provider."""
import json
import tempfile
import time
import unittest
from pathlib import Path

import provider_usage as u
from host import Host


class NormaliseTests(unittest.TestCase):
    def test_claude_result_fields_and_codex_token_totals_take_one_shape(self):
        self.assertEqual(u.normalise('claude', {'usage': {'input_tokens': 100, 'output_tokens': 40, 'cache_read_input_tokens': 10}, 'total_cost_usd': 0.0123}),
                         {'input_tokens': 110, 'output_tokens': 40, 'total_tokens': 150, 'cost_usd': 0.0123})
        self.assertEqual(u.normalise('claude', {'usage': {}, 'total_cost_usd': 0}), {'input_tokens': None, 'output_tokens': None, 'total_tokens': None, 'cost_usd': 0.0})
        self.assertIsNone(u.normalise('claude', {'usage': 'bad'}))
        self.assertIsNone(u.normalise('claude', {'usage': {'input_tokens': -5}, 'total_cost_usd': 'free'}))
        self.assertEqual(u.normalise('codex', {'tokenUsage': {'total': {'inputTokens': 120, 'outputTokens': 30, 'totalTokens': 150}}}),
                         {'input_tokens': 120, 'output_tokens': 30, 'total_tokens': 150, 'cost_usd': None})
        self.assertEqual(u.normalise('codex', {'tokenUsage': {'total': {'inputTokens': 1, 'outputTokens': 2}}})['total_tokens'], 3)
        self.assertIsNone(u.normalise('codex', {'tokenUsage': {'last': {}}}))
        self.assertIsNone(u.normalise('codex', 'nothing'))

    def test_rate_limit_snapshots_keep_only_the_documented_window_fields(self):
        raw = {'planType': 'plus', 'primary': {'usedPercent': 42, 'resetsAt': 1800000000, 'windowDurationMins': 300, 'extra': 'ignored'},
               'secondary': {'usedPercent': 'bad'}, 'spendControlReached': False, 'credits': {'hasCredits': True}}
        self.assertEqual(u.rate_limit_snapshot(raw), {'primary': {'usedPercent': 42, 'resetsAt': 1800000000, 'windowDurationMins': 300}, 'planType': 'plus', 'spendControlReached': False})
        self.assertIsNone(u.rate_limit_snapshot({'primary': {}}))
        self.assertIsNone(u.rate_limit_snapshot([]))


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.host = Host(Path(self.temp.name) / 'data'); self.addCleanup(self.host.close)
        self.db = self.host.store.db
        u.schema(self.db)
        self.sub = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        self.codex = self.host.dispatch('add_profile', {'provider': 'codex', 'identity': 'codex@example.test', 'category': 'personal'})['id']
        self.key = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'Anthropic API key ending abcd', 'category': 'personal'})['id']
        with self.db:
            self.db.execute("UPDATE profiles SET method='api_key', monthly_limit_usd=50 WHERE id=?", (self.key,))
        self.now = time.mktime((2026, 9, 17, 14, 0, 0, 0, 0, -1))

    def record(self, profile, provider, task, at, **usage):
        row = {'input_tokens': None, 'output_tokens': None, 'total_tokens': None, 'cost_usd': None, **usage}
        u.record_usage(self.db, task, 'run-' + task, {'profile_id': profile, 'provider': provider, 'method': None}, row, at=at)

    def account(self, profile_id):
        return next(a for a in u.summary(self.db, now=self.now)['accounts'] if a['profile_id'] == profile_id)

    def test_today_and_month_windows_and_the_three_bar_bases(self):
        day = self.now - 3 * 3600
        self.record(self.sub, 'claude', 't1', day, total_tokens=1000, cost_usd=0.5)
        self.record(self.sub, 'claude', 't2', day + 60, total_tokens=200, cost_usd=0.25)
        self.record(self.sub, 'claude', 't3', self.now - 2 * 86400, total_tokens=5000, cost_usd=3.0)  # earlier this month
        self.record(self.sub, 'claude', 't4', self.now - 40 * 86400, total_tokens=9999, cost_usd=99.0)  # last month
        sub = self.account(self.sub)
        self.assertEqual((sub['runs_today'], sub['tokens_today'], sub['cost_today_usd']), (2, 1200, 0.75))
        self.assertEqual((sub['runs_month'], sub['cost_month_usd']), (3, 3.75))
        self.assertEqual((sub['percent'], sub['low'], sub['basis'], sub['rate_limits']), (None, False, 'runs', None), 'a Claude subscription reports no allowance')
        # An API key measures spend against its limit.
        self.record(self.key, 'claude', 'k1', day, total_tokens=10, cost_usd=41.0)
        key = self.account(self.key)
        self.assertEqual((key['method'], key['monthly_limit_usd'], key['cost_month_usd'], key['percent'], key['low'], key['basis']), ('api_key', 50.0, 41.0, 82, True, 'spend_limit'))
        # Codex shows its own primary window.
        u.record_rate_limits(self.db, self.codex, {'planType': 'plus', 'primary': {'usedPercent': 79, 'resetsAt': 1800000000, 'windowDurationMins': 300}}, at=self.now - 5)
        self.record(self.codex, 'codex', 'c1', day, total_tokens=150)
        codex = self.account(self.codex)
        self.assertEqual((codex['percent'], codex['low'], codex['basis'], codex['resets_at'], codex['rate_limits_at']), (79, False, 'rate_limits', 1800000000, int(self.now - 5)))
        self.assertEqual((codex['tokens_today'], codex['cost_today_usd'], codex['cost_month_usd']), (150, None, None), 'Codex reports tokens, not dollars')
        u.record_rate_limits(self.db, self.codex, {'primary': {'usedPercent': 80}})
        self.assertTrue(self.account(self.codex)['low'])
        # A run that reports twice keeps the later totals; nothing is double counted.
        self.record(self.sub, 'claude', 't1', day + 5, total_tokens=1500, cost_usd=0.6)
        self.assertEqual((self.account(self.sub)['runs_today'], self.account(self.sub)['tokens_today']), (2, 1700))
        self.assertEqual(self.host.dispatch('usage_summary', {})['accounts'][0]['profile_id'], self.sub)
        self.assertEqual(u.summary(self.db, now=self.now)['at'], int(self.now))


    def test_claude_windows_and_a_window_that_already_reset(self):
        info = {'status': 'allowed', 'rateLimitType': 'five_hour',
                'unifiedWindows': {'five_hour': {'utilization': 0.14, 'resetsAt': int(self.now) + 3600}, 'seven_day': {'utilization': 0.35, 'resetsAt': int(self.now) + 86400}}}
        u.record_rate_limits(self.db, self.sub, u.claude_rate_limits(info), at=self.now - 5)
        sub = self.account(self.sub)
        self.assertEqual((sub['percent'], sub['basis'], sub['resets_at'], sub['window_mins']), (35, 'rate_limits', int(self.now) + 86400, 10080),
                         'the tighter of the 5-hour and 7-day windows')
        self.assertEqual(sub['windows'], [{'percent': 14, 'resets_at': int(self.now) + 3600, 'window_mins': 300},
                                         {'percent': 35, 'resets_at': int(self.now) + 86400, 'window_mins': 10080}])
        rejected = u.claude_rate_limits({'status': 'rejected', 'rateLimitType': 'seven_day', 'resetsAt': int(self.now) + 60})
        self.assertEqual(rejected, {'secondary': {'usedPercent': 100, 'windowDurationMins': 10080, 'resetsAt': int(self.now) + 60}, 'rateLimitReachedType': 'rate_limit_reached'})
        self.assertIsNone(u.claude_rate_limits({'status': 'allowed'}))
        self.assertIsNone(u.claude_rate_limits({'unifiedWindows': {'five_hour': {'utilization': 'high'}}}))
        # Bryce, 17 Sep: Codex's weekly window was used up, then reset; the saved snapshot must not keep saying 100%.
        u.record_rate_limits(self.db, self.codex, {'primary': {'usedPercent': 100, 'resetsAt': int(self.now) - 10, 'windowDurationMins': 10080}})
        codex = self.account(self.codex)
        self.assertEqual((codex['percent'], codex['low'], codex['resets_at']), (0, False, None))
        self.assertEqual(codex['windows'], [{'percent': 0, 'resets_at': None, 'window_mins': 10080}])
        u.record_rate_limits(self.db, self.codex, {'primary': {'usedPercent': 100, 'resetsAt': int(self.now) + 10, 'windowDurationMins': 10080}})
        self.assertEqual(self.account(self.codex)['percent'], 100)

    def test_homes_own_replies_count_toward_tokens_and_spend_but_not_as_runs(self):
        # Efficiency review, 23 Sep, item 10: Home's Sonnet calls were invisible on the bars. They're recorded now, and a
        # sidebar line saying "2 runs today" still means two runs.
        day = self.now - 3 * 3600
        self.record(self.key, 'claude', 't1', day, total_tokens=1000, cost_usd=2.0)
        self.record(self.key, 'claude', u.HOME_TASK + 'project-1', day + 60, total_tokens=9000, cost_usd=8.0)
        u.record_usage(self.db, u.HOME_TASK + 'project-1', 'reply-2', {'profile_id': self.key, 'provider': 'claude', 'method': 'api_key'},
                       {'input_tokens': None, 'output_tokens': None, 'total_tokens': 500, 'cost_usd': 0.5}, at=day + 120)
        key = self.account(self.key)
        self.assertEqual((key['runs_today'], key['tokens_today'], key['cost_today_usd']), (1, 10500, 10.5))
        self.assertEqual((key['runs_month'], key['cost_month_usd'], key['percent']), (1, 10.5, 21), 'Home’s spend moves the key’s bar')
        empty = self.account(self.codex)
        self.assertEqual((empty['runs_today'], empty['runs_month']), (0, 0), 'no rows is zero runs, not None')

if __name__ == '__main__':
    unittest.main()
