"""Account names on Connected accounts (Settings design sweep, 19 Sep 2026). Disposable stores only.

The picker, the composer chip, the sidebar and Connected accounts all read ``name`` from the host, so these check the
rename rules and that the name reaches both payloads the renderer reads: ``profiles`` and ``usage_summary``.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dervo_connections import Rejected, Store
from host import Host


class ProfileNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.host = Host(Path(self.temp.name) / 'data'); self.addCleanup(self.host.close)
        self.claude = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        self.team = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@work.example.test', 'category': 'personal'})['id']
        self.codex = self.host.dispatch('add_profile', {'provider': 'codex', 'identity': 'me@example.test', 'category': 'personal'})['id']

    def named(self, profile_id):
        listed = next(p for p in self.host.dispatch('profiles', {}) if p['id'] == profile_id)
        usage = next(a for a in self.host.dispatch('usage_summary', {})['accounts'] if a['profile_id'] == profile_id)
        return listed['name'], usage['name']

    def test_an_unnamed_account_sends_null_and_a_rename_reaches_both_payloads(self):
        self.assertEqual(self.named(self.claude), (None, None), 'nobody named it yet, so every screen uses its default')
        result = self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': '  Acme   Corp '})
        self.assertEqual(result, {'id': self.claude, 'name': 'Acme Corp'}, 'spaces are tidied, nothing else changes')
        self.assertEqual(self.named(self.claude), ('Acme Corp', 'Acme Corp'))
        self.assertEqual(self.named(self.team), (None, None), 'only that account is renamed')

    def test_the_same_name_is_refused_within_a_provider_and_allowed_across_providers(self):
        self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': 'Personal'})
        with self.assertRaisesRegex(Rejected, 'Another Claude account is already called Personal'):
            self.host.dispatch('profile_rename', {'profileId': self.team, 'name': 'personal'})
        self.host.dispatch('profile_rename', {'profileId': self.codex, 'name': 'Personal'})
        self.assertEqual(self.named(self.codex)[0], 'Personal', 'Claude Personal and Codex Personal can both exist')
        self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': 'Personal'})  # renaming to its own name is fine

    def test_names_that_would_mislead_or_leak_are_refused_and_nothing_is_saved(self):
        for name, reason in ((' ', 'Give the account a name'), ('', 'Give the account a name'), (None, 'Give the account a name'),
                             ('x' * 41, '40 characters'), ('Tabbell', 'letters, numbers and spaces'),
                             ('sk-ant-api03-abcdefghijklmnop', 'looks like a key'),
                             ('ghp_' + 'a' * 36, 'looks like a key')):
            with self.subTest(name=name), self.assertRaisesRegex(Rejected, reason):
                self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': name})
        self.assertEqual(self.named(self.claude), (None, None))
        with self.assertRaisesRegex(Rejected, 'isn’t connected any more'):
            self.host.dispatch('profile_rename', {'profileId': 'missing', 'name': 'Personal'})
        for args in ({'profileId': self.claude}, {'profileId': self.claude, 'name': 'A', 'identity': 'x'}):
            with self.subTest(args=args), self.assertRaises(Rejected):
                self.host.dispatch('profile_rename', args)

    def test_tokens_the_old_rule_missed_are_refused_and_plain_names_are_kept(self):
        # Security review, 19 Sep, finding 4: each of these got through the old rule, and the last plain name was refused.
        for token in ('xoxb-1234567890-abcdefghij', 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY',
                      'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0', 'SK-ANT-API03-ABCDEFGH', 'ghp_aB3dE6gH9jK2mN5pQ8sT1vW4yZ7',
                      'AKIAIOSFODNN7EXAMPLE', 'AIzaSy' + 'A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q', 'my key Sk_test_4eC39HqLyjWDarjtT1z',
                      'keys/sk-live-12345678', 'glpat-abcdefghijklmnopqrst', '9f86d081884c7d659a2feaa0c55ad015'):
            with self.subTest(token=token), self.assertRaisesRegex(Rejected, 'looks like a key'):
                self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': token})
        self.assertEqual(self.named(self.claude), (None, None), 'nothing key-like was saved')
        for name in ('digital-directions-personal-acct', 'Acme', 'acme-2026-personal-account', 'Aizawa Studio',
                     'Eyjafjallajokull', 'ASIA-PACIFIC team', 'SK Telecom', 'sky-high-projects', 'sb_personal',
                     'Client A1 (Q3 2026)', 'me@work.example.test'):
            with self.subTest(name=name):
                self.assertEqual(self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': name})['name'], name)

    def test_a_name_is_a_label_and_never_changes_which_account_runs(self):
        before = dict(self.host.store.db.execute('SELECT id, provider, home, identity, category, method FROM profiles WHERE id=?',
                                                 (self.claude,)).fetchone())
        self.host.dispatch('profile_rename', {'profileId': self.claude, 'name': 'me@work.example.test'})
        after = dict(self.host.store.db.execute('SELECT id, provider, home, identity, category, method FROM profiles WHERE id=?',
                                                (self.claude,)).fetchone())
        self.assertEqual(before, after, 'identity, home and method are untouched by a rename')

    def test_an_older_store_gains_the_name_column_empty(self):
        path = Path(self.temp.name) / 'old.db'
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE profiles (id TEXT PRIMARY KEY, provider TEXT NOT NULL, home TEXT UNIQUE NOT NULL, "
                         "identity TEXT NOT NULL, category TEXT NOT NULL); "
                         "INSERT INTO profiles VALUES ('old', 'claude', '/private/tmp/old-home', 'me@example.test', 'personal');")
        db.commit(); db.close()
        store = Store(path); self.addCleanup(store.close)
        row = store.db.execute("SELECT name, method FROM profiles WHERE id='old'").fetchone()
        self.assertEqual((row['name'], row['method']), (None, 'subscription'))


if __name__ == '__main__':
    unittest.main()
