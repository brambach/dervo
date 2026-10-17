"""Which failures count as the provider's side, and how Auto names accounts and low usage. Pure functions, no host."""
import sqlite3
import unittest

import conversation_route
import provider_failure


class ProviderFailureTests(unittest.TestCase):
    def test_codex_counts_limits_overload_outage_and_lost_connections_only(self):
        for info, kind in (('usageLimitExceeded', 'usage_limit'), ('rateLimitExceeded', 'rate_limit'), ('serverOverloaded', 'overloaded'),
                           ('internalServerError', 'server_error'), ({'httpConnectionFailed': {'httpStatusCode': 502}}, 'connection'),
                           ({'responseStreamConnectionFailed': {}}, 'connection'), ({'responseStreamDisconnected': {'httpStatusCode': None}}, 'connection'),
                           ({'responseTooManyFailedAttempts': {}}, 'connection')):
            self.assertEqual(provider_failure.codex({'message': 'x', 'codexErrorInfo': info}), kind, info)
            self.assertIn(kind, provider_failure.KINDS)
        for info in ('contextWindowExceeded', 'sessionBudgetExceeded', 'cyberPolicy', 'misalignmentPolicyViolation', 'unauthorized', 'badRequest',
                     'threadRollbackFailed', 'sandboxError', 'other', None, {'httpConnectionFailed': {}, 'extra': {}}, {'somethingNew': {}}, 7):
            self.assertIsNone(provider_failure.codex({'message': 'x', 'codexErrorInfo': info}), info)
        for error in (None, 'overloaded', [], {'message': 'no info'}):
            self.assertIsNone(provider_failure.codex(error))
        self.assertIn(provider_failure.CLAUDE_API_ERROR, provider_failure.KINDS)

    def test_labels_and_low_notes_use_the_summary_terms(self):
        self.assertEqual((conversation_route.label('claude'), conversation_route.label('codex', 'api_key'), conversation_route.label('claude', 'subscription')),
                         ('Claude Code', 'Codex API key', 'Claude Code'))
        self.assertIsNone(conversation_route.low_note({'low': False, 'percent': 79, 'basis': 'rate_limits'}))
        self.assertIsNone(conversation_route.low_note({'low': True, 'percent': None, 'basis': 'runs'}))
        self.assertIsNone(conversation_route.low_note(None))
        self.assertEqual(conversation_route.low_note({'low': True, 'percent': 81, 'basis': 'rate_limits'}), 'at 81% of its usage window')
        self.assertEqual(conversation_route.low_note({'low': True, 'percent': 95, 'basis': 'spend_limit'}), 'at 95% of its monthly spend limit')
        for kind in provider_failure.KINDS:
            self.assertIn(kind, conversation_route.FAILURE_WORDS)

    def test_the_alternative_is_the_other_provider_only_and_the_most_recent_personal_pair(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.executescript('''CREATE TABLE profiles (id TEXT PRIMARY KEY, provider TEXT, identity TEXT, category TEXT, method TEXT);
            CREATE TABLE threads (id TEXT PRIMARY KEY, profile_id TEXT, model TEXT);''')
        db.executemany('INSERT INTO profiles VALUES (?,?,?,?,?)', [('c1', 'claude', 'me', 'personal', 'subscription'), ('c2', 'claude', 'key', 'personal', 'api_key'),
                                                                     ('x1', 'codex', 'me', 'personal', None), ('x2', 'codex', 'team', 'organisation', 'subscription'),
                                                                     ('o1', 'openrouter', 'me', 'personal', 'subscription')])
        binding = {'provider': 'claude'}
        self.assertIsNone(conversation_route.alternative(db, binding), 'another Claude account is never an alternative')
        db.executemany('INSERT INTO threads VALUES (?,?,?)', [('t1', 'c2', 'claude-opus-5'), ('t2', 'x1', 'gpt-a'), ('t3', 'x2', 'gpt-team'), ('t4', 'o1', 'router'), ('t5', 'c1', 'claude-fable-5-1')])
        other = conversation_route.alternative(db, binding)
        self.assertEqual((other['profile_id'], other['model'], other['provider'], other['method'], other['label']), ('x1', 'gpt-a', 'codex', 'subscription', 'Codex'))
        other = conversation_route.alternative(db, {'provider': 'codex'})
        self.assertEqual((other['profile_id'], other['model'], other['label']), ('c1', 'claude-fable-5-1', 'Claude Code'))


if __name__ == '__main__':
    unittest.main()
