"""Adding an account by signing in first, using another sign-in, and removing any account (account_signin.py, beta.18).
A real Host and store in a temp folder, and the fake Claude Code from test_claude_login on a real pseudo-terminal: no
browser, account, Keychain, ~/.claude or network is touched."""
import json
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import account_signin
import claude_policy
import connections_ui
from dervo_connections import Rejected
from test_claude_login import Fixture, VERIFIED

OTHER = 'someone.else@example.test'
# One email in two Claude organisations (Bryce, 8 Oct 2026: a personal Max and a Team seat on the same address).
MAX_ORG = dict(VERIFIED, orgId='org-max-0001', orgName="personal@example.test's Organization")
TEAM_ORG = dict(VERIFIED, orgId='org-team-0002', orgName='Acme', subscriptionType='team')


def finished(result):
    job = Future()
    job.set_result(result)
    return job


class ClassifyTests(unittest.TestCase):
    """What the probe learns about a sign-in that isn't the account's email, kept for the host only."""

    def pending(self, provider='claude', category='personal'):
        return dict(id='n', provider=provider, identity='', category=category, method='subscription', home='/tmp/n', pending=True)

    def test_a_sign_in_with_no_email_yet_reports_who_signed_in_and_whether_it_can_be_added(self):
        result = connections_ui.classify(self.pending(), VERIFIED)
        self.assertEqual((result['state'], result['seen'], result['seenOk'], result['seenPlan']), ('identity_mismatch', VERIFIED['email'], True, 'max'))
        free = connections_ui.classify(self.pending(), dict(VERIFIED, subscriptionType='free'))
        self.assertFalse(free['seenOk'])
        self.assertIn('Free plan', free['seenDetail'])
        codex = connections_ui.classify(self.pending('codex'), {'account': {'type': 'chatgpt', 'email': 'Me@Example.test ', 'planType': 'plus'}})
        self.assertEqual((codex['seen'], codex['seenOk'], codex['seenPlan']), ('Me@Example.test', True, 'plus'))

    def test_a_work_side_sign_in_on_a_personal_plan_still_follows_the_side_rules(self):
        team = connections_ui.classify(self.pending(category='personal'), dict(VERIFIED, subscriptionType='enterprise'))
        self.assertFalse(team['seenOk'], 'Enterprise is a work plan only')

    def test_a_claude_account_is_its_email_and_its_organisation(self):
        account = {'id': 'p', 'provider': 'claude', 'identity': VERIFIED['email'], 'category': 'personal', 'method': 'subscription',
                   'org': MAX_ORG['orgId'], 'org_name': MAX_ORG['orgName']}
        self.assertEqual(connections_ui.classify(account, MAX_ORG)['state'], 'identity_verified')
        other = connections_ui.classify(account, TEAM_ORG)
        self.assertEqual(other['state'], 'identity_mismatch', 'the same email in another organisation is another account')
        self.assertIn('in Acme', other['detail'])
        self.assertIn('No messages were sent.', other['detail'])
        self.assertFalse(other['seenOk'])
        # An account from before Dervo kept the organisation takes any until its first good check records one.
        older = {**account, 'org': None, 'org_name': None}
        self.assertEqual(connections_ui.classify(older, TEAM_ORG)['state'], 'identity_verified')
        # Negative control: without the organisation rule the Team sign-in would pass as the Max account.
        with patch.object(connections_ui, 'same_org', lambda profile, metadata: True):
            self.assertEqual(connections_ui.classify(account, TEAM_ORG)['state'], 'identity_verified')

    def test_a_good_check_records_an_older_accounts_organisation_once(self):
        from host import Host
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            host = Host(Path(root))
            try:
                host.store.add_profile('p', 'claude', Path(root) / 'profiles' / 'p', VERIFIED['email'], 'personal')
                row = lambda: dict(host.store.db.execute('SELECT * FROM profiles').fetchone())
                checked = host._checked(row(), {'state': 'identity_verified', 'canSend': False, 'detail': '', 'org': 'org-max-0001', 'orgName': 'X'})
                self.assertFalse({'org', 'orgName'} & set(checked), 'the organisation stays on the host')
                self.assertEqual((row()['org'], row()['org_name']), ('org-max-0001', 'X'))
                host._checked(row(), {'state': 'identity_verified', 'canSend': False, 'detail': '', 'org': 'org-other', 'orgName': 'Y'})
                self.assertEqual(row()['org'], 'org-max-0001', 'recorded once, never moved by a later check')
                host.store.db.execute('UPDATE profiles SET org=NULL')
                host._checked(row(), {'state': 'identity_mismatch', 'canSend': False, 'detail': '', 'org': 'org-other'})
                self.assertIsNone(row()['org'], 'only a verified check records it')
            finally:
                host.close()

    def test_the_screens_never_get_the_host_only_fields(self):
        from host import Host
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            host = Host(Path(root))
            try:
                host.store.add_profile('p', 'claude', Path(root) / 'profiles' / 'p', 'typed@example.test', 'personal')
                checked = host._checked(dict(host.store.db.execute('SELECT * FROM profiles').fetchone()),
                                        connections_ui.classify({'id': 'p', 'provider': 'claude', 'identity': 'typed@example.test', 'category': 'personal', 'method': 'subscription'}, VERIFIED))
                self.assertFalse({'seen', 'seenOk', 'seenPlan', 'seenDetail'} & set(checked))
                self.assertEqual((checked['signedInAs'], checked['canUseSignedIn'], checked['signedInPlan']), (VERIFIED['email'], True, 'max'))
            finally:
                host.close()


class SignInFirstTests(Fixture):
    def setUp(self):
        super().setUp()
        from host import Host
        self.host = Host(self.base)
        self.addCleanup(self.host.close)
        self.enterContext(patch.object(claude_policy, 'executable', lambda: str(self.claude)))

    def op(self, operation, **args):
        return self.host.dispatch(operation, args)

    def until(self, pending_id, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.op('connection_login_status', profileId=pending_id)
            if condition(status):
                return status
            time.sleep(.02)
        self.fail(status)

    def sign_in(self, category='personal', status=VERIFIED):
        (self.control / 'status.json').write_text(json.dumps(status))
        for name in ('finish', 'argv.txt'):
            (self.control / name).unlink(missing_ok=True)
        started = self.op('account_signin_start', provider='claude', category=category)
        pending_id = started['pendingId']
        self.until(pending_id, lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        found = self.until(pending_id, lambda s: s.get('identity', {}).get('state') not in (None, 'checking'))
        return pending_id, found

    def folders(self):
        # The login fixture's own folder (profiles/p) isn't one of these sign-ins.
        return sorted(p.name for p in (self.base / 'profiles').iterdir() if p.name != 'p')

    def test_signing_in_first_runs_claude_without_an_email_and_adds_the_account_it_reports(self):
        pending_id, found = self.sign_in()
        argv = (self.control / 'argv.txt').read_text().split('\n')
        self.assertIn('login', argv)
        self.assertNotIn('--email', argv, 'nothing to pre-fill: the provider asks for the account')
        self.assertEqual(found['identity']['state'], 'signed_in')
        self.assertEqual(found['identity']['signedInAs'], VERIFIED['email'])
        self.assertIn('Signed in as personal@example.test, Claude Max plan.', found['identity']['detail'])
        self.assertFalse(found['identity']['canSend'])
        # Nothing exists as an account until you click Add: no row, so nothing can run on it.
        self.assertIsNone(self.host.store.db.execute('SELECT 1 FROM profiles WHERE id=?', (pending_id,)).fetchone())
        with self.assertRaises(Rejected):
            self.op('connection_check', profileId=pending_id)
        added = self.op('account_signin_add', pendingId=pending_id)
        self.assertEqual((added['id'], added['state'], added['identity']), (pending_id, 'added', VERIFIED['email']))
        row = dict(self.host.store.db.execute('SELECT * FROM profiles WHERE id=?', (pending_id,)).fetchone())
        self.assertEqual((row['identity'], row['category'], row['method']), (VERIFIED['email'], 'personal', 'subscription'))
        self.assertEqual(Path(row['home']), (self.base / 'profiles' / pending_id).resolve())
        self.assertFalse((Path(row['home']) / account_signin.MARKER).exists())
        # The same exact-email rule as every account from here on: its own check says verified.
        deadline = time.monotonic() + 5
        while self.op('connection_status', profileId=pending_id)['state'] != 'identity_verified' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(self.op('connection_status', profileId=pending_id)['state'], 'identity_verified')
        self.assertEqual([p['plan'] for p in self.op('profiles') if p['id'] == pending_id], ['max'])

    def test_adding_an_account_already_here_hands_back_that_one(self):
        first, _ = self.sign_in()
        self.op('account_signin_add', pendingId=first)
        second, found = self.sign_in(status=dict(VERIFIED, email=VERIFIED['email'].upper()))
        self.assertEqual((found['identity']['state'], found['identity']['profileId']), ('existing', first))
        again = self.op('account_signin_add', pendingId=second)
        self.assertEqual((again['id'], again['state']), (first, 'existing'))
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM profiles').fetchone()[0], 1)
        self.assertEqual(self.folders(), [first], 'the second sign-in’s folder is gone')

    def test_the_same_email_in_another_organisation_is_a_second_account(self):
        first, _ = self.sign_in(status=MAX_ORG)
        self.op('account_signin_add', pendingId=first)
        second, found = self.sign_in(status=TEAM_ORG)
        self.assertEqual(found['identity']['state'], 'signed_in')
        self.assertIn('Signed in as personal@example.test, Claude Team plan in Acme.', found['identity']['detail'])
        self.op('account_signin_add', pendingId=second)
        rows = {r['id']: dict(r) for r in self.host.store.db.execute('SELECT * FROM profiles')}
        self.assertEqual((rows[first]['org'], rows[second]['org']), (MAX_ORG['orgId'], TEAM_ORG['orgId']))
        self.assertEqual(rows[second]['name'], 'Acme', 'named so the two rows read differently')
        # Signing in to the first organisation again is that account, not a third.
        third, found = self.sign_in(status=MAX_ORG)
        self.assertEqual((found['identity']['state'], found['identity']['profileId']), ('existing', first))
        self.op('account_signin_cancel', pendingId=third)
        # Each account's own check holds it to its organisation.
        (self.control / 'status.json').write_text(json.dumps(TEAM_ORG))
        self.op('connection_check', profileId=first)
        deadline = time.monotonic() + 5
        while (status := self.op('connection_status', profileId=first))['state'] == 'checking' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(status['state'], 'identity_mismatch')
        self.assertIn('in Acme', status['detail'])

    def test_the_other_organisation_can_be_the_other_side(self):
        first, _ = self.sign_in('personal', status=MAX_ORG)
        self.op('account_signin_add', pendingId=first)
        second, found = self.sign_in('work', status=TEAM_ORG)
        self.assertEqual(found['identity']['state'], 'signed_in', 'a Team seat in its own organisation is a separate sign-in')
        self.op('account_signin_add', pendingId=second)
        same, found = self.sign_in('work', status=MAX_ORG)
        self.assertEqual(found['identity']['state'], 'other_side', 'the personal organisation is still never a work account')
        self.op('account_signin_cancel', pendingId=same)

    def test_an_account_added_before_organisations_were_kept_is_checked_once_then_told_apart(self):
        self.host.store.add_profile('old', 'claude', self.base / 'profiles' / 'old', VERIFIED['email'], 'personal')
        checks = []
        def recheck(signins, profile_id):
            checks.append(profile_id)
            self.host.checks[profile_id] = finished({'state': 'identity_verified', 'canSend': False, 'detail': '',
                                                     'org': MAX_ORG['orgId'], 'orgName': MAX_ORG['orgName']})
        with patch.object(account_signin.AccountSignIns, '_recheck', recheck):
            pending_id, found = self.sign_in(status=TEAM_ORG)
        self.assertEqual(found['identity']['state'], 'signed_in')
        self.assertEqual(checks, ['old'], 'checked once, not on every poll')
        self.assertEqual(self.host.store.db.execute("SELECT org FROM profiles WHERE id='old'").fetchone()[0], MAX_ORG['orgId'])

    def test_your_account_on_the_other_side_is_refused_by_name_and_never_added(self):
        first, _ = self.sign_in('personal')
        self.op('account_signin_add', pendingId=first)
        second, found = self.sign_in('work', status=dict(VERIFIED, subscriptionType='team'))
        self.assertEqual((found['identity']['state'], found['identity']['otherSide']), ('other_side', 'personal'))
        self.assertIn('That’s your personal account.', found['identity']['detail'])
        self.assertIn('Nothing was added.', found['identity']['detail'])
        with self.assertRaises(Rejected):
            self.op('account_signin_add', pendingId=second)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM profiles').fetchone()[0], 1)
        self.op('account_signin_cancel', pendingId=second)
        self.assertEqual(self.folders(), [first])

    def test_a_plan_this_side_cannot_use_is_said_and_not_added(self):
        pending_id, found = self.sign_in(status=dict(VERIFIED, subscriptionType='free'))
        self.assertEqual(found['identity']['state'], 'refused')
        self.assertIn('Free plan', found['identity']['detail'])
        self.assertIn('Nothing was added.', found['identity']['detail'])
        with self.assertRaises(Rejected):
            self.op('account_signin_add', pendingId=pending_id)

    def test_cancel_stops_the_sign_in_and_removes_its_folder(self):
        started = self.op('account_signin_start', provider='claude', category='personal')
        pending_id = started['pendingId']
        self.assertTrue((self.base / 'profiles' / pending_id / account_signin.MARKER).is_file())
        self.until(pending_id, lambda s: s['url'] is not None)
        self.assertEqual(self.op('account_signin_cancel', pendingId=pending_id)['state'], 'cancelled')
        self.assertEqual(self.folders(), [])
        with self.assertRaises(Rejected):
            self.op('account_signin_add', pendingId=pending_id)

    def test_add_waits_for_the_sign_in_to_finish(self):
        started = self.op('account_signin_start', provider='claude', category='personal')
        with self.assertRaisesRegex(Rejected, 'Finish signing in first'):
            self.op('account_signin_add', pendingId=started['pendingId'])
        self.op('account_signin_cancel', pendingId=started['pendingId'])

    def test_left_over_sign_in_folders_are_swept_and_nothing_else(self):
        leftover = self.base / 'profiles' / 'abc123'
        leftover.mkdir(parents=True)
        (leftover / account_signin.MARKER).write_text('')
        unmarked = self.base / 'profiles' / 'def456'
        unmarked.mkdir()
        self.host.signins().sweep()
        self.assertFalse(leftover.exists())
        self.assertTrue(unmarked.exists(), 'a folder Dervo didn’t mark is never removed')

    def test_wrong_fields_and_sides_are_refused_before_anything_starts(self):
        for args in ({'provider': 'claude'}, {'provider': 'gemini', 'category': 'personal'}, {'provider': 'claude', 'category': 'both'}):
            with self.assertRaises(Rejected):
                self.host.dispatch('account_signin_start', args)
        self.assertFalse((self.control / 'argv.txt').exists())
        self.assertEqual(self.folders(), [])


class UseSignedInTests(Fixture):
    """An existing account whose sign-in is a different valid account on its side: "Use this account instead"."""

    def setUp(self):
        super().setUp()
        from host import Host
        self.host = Host(self.base)
        self.addCleanup(self.host.close)
        self.host.store.add_profile('p', 'claude', self.home, 'typo@example.test', 'personal')
        self.enterContext(patch.object(claude_policy, 'executable', lambda: str(self.claude)))

    def check(self):
        self.host.dispatch('connection_check', {'profileId': 'p'})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.host.dispatch('connection_status', {'profileId': 'p'})
            if status['state'] not in ('checking', 'unchecked'):
                return status
            time.sleep(.02)
        self.fail(status)

    def test_a_valid_sign_in_on_the_same_side_is_offered_by_name_and_used_only_on_the_click(self):
        status = self.check()
        self.assertEqual((status['state'], status['signedInAs'], status['canUseSignedIn']), ('identity_mismatch', VERIFIED['email'], True))
        self.assertEqual(self.host.store.db.execute("SELECT identity FROM profiles WHERE id='p'").fetchone()[0], 'typo@example.test')
        self.host.store.db.execute("INSERT INTO projects (id, cwd, profile_id, model, context) VALUES ('j', ?, 'p', 'claude-opus-5-5', 'personal')", (str(self.base),))
        self.host.store.db.execute("INSERT INTO threads VALUES ('t', 'j', 't', 'p', 'claude-opus-5-5', 'shared', NULL, 'old-session')")
        self.host.store.db.commit()
        used = self.host.dispatch('account_use_signed_in', {'profileId': 'p'})
        self.assertEqual(used, {'id': 'p', 'identity': VERIFIED['email']})
        self.assertIsNone(self.host.store.db.execute("SELECT session_id FROM threads WHERE id='t'").fetchone()[0],
                          'the old sign-in’s session isn’t resumed by the new one')
        deadline = time.monotonic() + 5
        while self.host.dispatch('connection_status', {'profileId': 'p'})['state'] != 'identity_verified' and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'identity_verified')

    def test_never_your_other_side_and_never_an_email_another_account_has(self):
        self.host.store.add_profile('w', 'claude', self.base / 'profiles' / 'w', VERIFIED['email'], 'work')
        status = self.check()
        self.assertEqual(status.get('otherSide'), 'work')
        self.assertNotIn('canUseSignedIn', status)
        with self.assertRaises(Rejected):
            self.host.dispatch('account_use_signed_in', {'profileId': 'p'})
        self.host.store.db.execute("UPDATE profiles SET category='personal' WHERE id='w'")
        self.host.store.db.commit()
        status = self.check()
        self.assertEqual((status['canUseSignedIn'], status['takenBy']), (False, 'your Claude account'))
        with self.assertRaisesRegex(Rejected, 'Pick that account instead'):
            self.host.dispatch('account_use_signed_in', {'profileId': 'p'})
        self.assertEqual(self.host.store.db.execute("SELECT identity FROM profiles WHERE id='p'").fetchone()[0], 'typo@example.test')

    def test_an_invalid_plan_is_never_offered(self):
        (self.control / 'status.json').write_text(json.dumps(dict(VERIFIED, subscriptionType='free')))
        status = self.check()
        self.assertNotIn('canUseSignedIn', status)
        with self.assertRaises(Rejected):
            self.host.dispatch('account_use_signed_in', {'profileId': 'p'})


class RemoveTests(Fixture):
    def setUp(self):
        super().setUp()
        from host import Host
        self.host = Host(self.base)
        self.addCleanup(self.host.close)
        self.host.store.add_profile('p', 'claude', self.home, 'personal@example.test', 'personal')
        (self.home / 'sign-in.json').write_text('{}')
        self.host.store.db.execute("INSERT INTO projects (id, cwd, profile_id, model, context) VALUES ('j', ?, 'p', 'claude-opus-5-5', 'personal')", (str(self.base),))
        self.host.store.db.execute("INSERT INTO threads VALUES ('t', 'j', 't', 'p', 'claude-opus-5-5', 'shared', NULL, NULL)")
        self.host.store.db.commit()

    def test_remove_forgets_the_account_deletes_its_own_folder_and_leaves_its_threads_asking(self):
        removed = self.host.dispatch('account_remove', {'profileId': 'p'})
        self.assertEqual((removed['state'], removed['projects'], removed['threads'], removed['kept']), ('removed', 1, 1, False))
        self.assertFalse(self.home.exists(), 'its Dervo-owned folder and the sign-in in it are gone')
        self.assertEqual(self.host.dispatch('profiles', {}), [])
        self.assertEqual([a['profile_id'] for a in self.host.dispatch('usage_summary', {})['accounts']], [])
        # The project and thread still point at it, and nothing runs there or switches by itself.
        self.assertEqual(self.host.store.db.execute("SELECT profile_id FROM threads WHERE id='t'").fetchone()[0], 'p')
        with self.assertRaisesRegex(Rejected, 'Pick another account'):
            connections_ui.owned_profile(self.host.store, self.host.root, 'p')
        self.assertFalse(self.home.exists(), 'a refused run doesn’t make the folder again')
        with self.assertRaisesRegex(Rejected, 'already removed'):
            self.host.dispatch('account_remove', {'profileId': 'p'})

    def test_a_thread_on_a_removed_account_moves_only_when_you_pick_another(self):
        self.host.store.add_profile('q', 'claude', self.base / 'profiles' / 'q', 'other@example.test', 'personal')
        self.host.dispatch('account_remove', {'profileId': 'p'})
        where = {'projectId': 'j', 'channelId': 't'}
        with self.assertRaisesRegex(Rejected, 'Pick another account'):
            self.host.dispatch('conversation_model_set', {**where, 'expectedModel': 'claude-opus-5-5', 'model': 'claude-sonnet-5-5'})
        moved = self.host.dispatch('conversation_profile_set', {**where, 'expectedProfileId': 'p', 'profileId': 'q',
                                                                'expectedModel': 'claude-opus-5-5', 'model': 'claude-opus-5-5'})
        self.assertEqual(moved['profile_id'], 'q')
        with self.assertRaisesRegex(Rejected, 'Pick another account'):
            self.host.dispatch('project_profile_set', {'projectId': 'j', 'expectedProfileId': 'q', 'profileId': 'p',
                                                       'expectedModel': 'claude-opus-5-5', 'model': 'claude-opus-5-5'})

    def test_the_same_email_can_be_added_again_after_removing(self):
        self.host.dispatch('account_remove', {'profileId': 'p'})
        self.host.store.add_profile('q', 'claude', self.base / 'profiles' / 'q', 'personal@example.test', 'work')
        self.assertEqual([p['id'] for p in self.host.dispatch('profiles', {})], ['q'])

    def test_your_existing_claude_code_is_only_forgotten(self):
        system = self.base / 'system claude'
        system.mkdir()
        (system / '.credentials.json').write_text('{}')
        with patch.object(claude_policy, 'system_config_dir', lambda: system):
            self.host.store.add_profile('s', 'claude', system, 'me@example.test', 'personal')
            removed = self.host.dispatch('account_remove', {'profileId': 's'})
        self.assertTrue(removed['kept'])
        self.assertTrue((system / '.credentials.json').is_file(), '~/.claude is never touched')

    def test_a_live_run_or_queued_message_holds_the_removal(self):
        class Job:
            closed = False
            binding = {'profile_id': 'p'}
        self.host.turns['m'] = Job()
        with self.assertRaisesRegex(Rejected, 'running on it right now. Nothing was removed.'):
            self.host.dispatch('account_remove', {'profileId': 'p'})
        self.host.turns.pop('m')
        self.host.store.db.execute('CREATE TABLE IF NOT EXISTS coding_queue (id TEXT PRIMARY KEY, thread_id TEXT, client_id TEXT, document TEXT)')
        self.host.store.db.execute("INSERT INTO coding_queue VALUES ('q1', 't', 'c', ?)", (json.dumps({'state': 'queued'}),))
        self.host.store.db.commit()
        with self.assertRaisesRegex(Rejected, '1 queued message is waiting'):
            self.host.dispatch('account_remove', {'profileId': 'p'})
        self.assertTrue(self.home.exists())
        self.assertEqual(len(self.host.dispatch('profiles', {})), 1)

    def test_an_api_key_account_is_removed_even_when_threads_use_it(self):
        keyed = self.base / 'profiles' / 'k'
        keyed.mkdir(parents=True)
        (keyed / 'auth.json').write_text('{}')
        self.host.store.add_profile('k', 'codex', keyed, 'OpenAI API key ending abcd', 'personal', method='api_key')
        self.host.store.db.execute("INSERT INTO threads VALUES ('t2', 'j', 't2', 'k', 'gpt-6-astra', 'shared', NULL, NULL)")
        self.host.store.db.commit()
        self.assertEqual(self.host.dispatch('account_remove', {'profileId': 'k'})['state'], 'removed')
        self.assertFalse(keyed.exists(), 'Codex’s saved key goes with its folder')


if __name__ == '__main__':
    unittest.main()
