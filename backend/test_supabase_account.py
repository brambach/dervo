"""Offline account lifecycle tests with real background work and private fake storage."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from account_config import NAME, UnavailableAccount, create, read
from account_keychain import KeychainError
from dervo_connections import Rejected
from host import Host
from supabase_account import SessionVault, SupabaseAccount
from supabase_client import AccountError, Session
from test_supabase_client import ACCESS, REFRESH, PROJECT, USER, OTHER


class Keychain:
    def __init__(self):
        self.data = None
        self.fail_write = False

    def read(self):
        return self.data

    def write(self, data):
        if self.fail_write:
            raise KeychainError()
        self.data = data

    def delete(self):
        self.data = None


def plan_doc(plan, *, available=False, can_buy=True, can_manage=False):
    return {'plan': plan, 'environment': 'sandbox', 'trial': {'days': 7, 'mode': 'app', 'ends_at': None, 'available': available},
            'subscription': None, 'can_buy': can_buy, 'can_manage': can_manage}


class Client:
    project = PROJECT

    def __init__(self, clock):
        self.clock = clock
        self.calls = []
        self.user_id = USER
        self.name = 'Fixture user'
        self.allowed = True
        self.membership_status = 'approved'
        self.fail_access = False
        self.fail_logout = False
        self.fail_user_once = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.pause_verify = False
        # Testers (admins only): the fixture server's rows, and what the page asked it to change.
        self.is_admin = False
        self.people = []
        self.audit = []
        self.changes = []
        self.fail_testers = False
        # Waitlist: the fixture website's rows, the batches it was sent, and how each send ends.
        self.waiting = []
        self.batches = []
        self.fail_waitlist = False
        self.fail_batch = False
        # Paid plans: the fixture server's plan (None stands for a server without billing), the trial answer, and the
        # billing links asked for. Plan reads aren't added to calls, so the sign-in sequences above stay as they were.
        self.plan_doc = None
        self.fail_plan = False
        self.trial_state = 'started'
        self.trials = []
        self.links = []
        self.devices = []
        self.fail_link = None

    def waitlist(self, session):
        self.calls.append('waitlist')
        if self.fail_waitlist:
            raise AccountError('unavailable')
        return {'people': [dict(row) for row in self.waiting], 'more': False}

    def waitlist_action(self, session, action, emails):
        self.calls.append('waitlist_action')
        self.batches.append((session.user_id, action, list(emails)))
        if self.fail_batch:
            raise AccountError('service')
        results = []
        for email in emails:
            if action == 'add':
                existing = any(row['email'] == email for row in self.waiting)
                if not existing:
                    self.waiting.append(dict(email=email, source='admin', signed_up_at=self.clock(), status='waiting',
                                             invited_at=None, expires_at=None, delivery=None))
                results.append({'email': email, 'result': 'already_listed' if existing else 'added', 'expires_at': None})
                continue
            row = next(row for row in self.waiting if row['email'] == email)
            if action == 'revoke':
                row.update(status='revoked'); results.append({'email': email, 'result': 'revoked', 'expires_at': None})
            elif row['status'] == 'invited' and action == 'invite':
                results.append({'email': email, 'result': 'already_invited', 'expires_at': None})
            elif email.startswith('bounce'):
                row.update(status='invited', delivery='failed'); results.append({'email': email, 'result': 'send_failed', 'expires_at': None})
            else:
                row.update(status='invited', delivery='sent', invited_at=self.clock(), expires_at=self.clock() + 14 * 86400)
                results.append({'email': email, 'result': 'sent', 'expires_at': row['expires_at']})
        return results

    def send_code(self, email):
        self.calls.append('send')
        if getattr(self, 'send_error', None):
            raise AccountError(self.send_error)

    def verify_code(self, email, code):
        self.calls.append('verify')
        if code == '000000':
            raise AccountError('rejected')
        if self.pause_verify:
            self.entered.set()
            if not self.release.wait(3):
                raise RuntimeError('Fixture timed out')
        return Session(ACCESS, REFRESH, self.user_id, self.clock() + 1000)

    def user(self, token, *, expected_user):
        self.calls.append('user')
        if self.fail_user_once:
            self.fail_user_once = False
            raise AccountError('expired')
        return {'issuer': PROJECT.url + '/auth/v1', 'subject': expected_user}

    def access(self, session):
        self.calls.append('access')
        if self.fail_access:
            raise AccountError('unavailable')
        return dict(status=self.membership_status, allowed=self.allowed, expires_at=None,
                    release_channel='private-beta', is_admin=self.is_admin)

    def plan(self, session):
        if self.fail_plan:
            raise AccountError('unavailable')
        if self.plan_doc is None:
            raise AccountError('unavailable')  # what an older server answers: no such function
        return json.loads(json.dumps(self.plan_doc))

    def start_trial(self, session, device):
        self.calls.append('start_trial')
        self.trials.append(device)
        if self.trial_state == 'started':
            self.plan_doc = plan_doc('trial', available=False)
            self.membership_status, self.allowed = 'approved', True
        return {'state': self.trial_state, 'ends_at': None}

    def billing_link(self, session, action, interval=None, device=None):
        self.calls.append('billing_link')
        self.links.append((session.user_id, action, interval))
        self.devices.append(device)
        if self.fail_link:
            raise AccountError(self.fail_link)
        return 'https://sandbox-checkout.paddle.io/fixture' if action == 'checkout' else 'https://sandbox-customer-portal.paddle.com/fixture'

    def testers(self, session):
        self.calls.append('testers')
        if self.fail_testers:
            raise AccountError('expired')
        return [dict(row) for row in self.people]

    def tester_audit(self, session, before=None, limit=20):
        self.calls.append(('audit', before, limit))
        return [dict(row) for row in self.audit if before is None or row['id'] < before][:limit]

    def set_membership(self, session, user_id, status, expiry=None):
        self.calls.append('set_membership')
        self.changes.append((session.user_id, user_id, status, expiry))
        for row in self.people:
            if row['user_id'] == user_id:
                row.update(status=status, expires_at=expiry)

    def profile(self, session):
        self.calls.append('profile')
        return {'user_id': session.user_id, 'display_name': self.name}

    def save_name(self, session, name):
        self.calls.append('save_name')
        self.name = name.strip()

    def refresh(self, session):
        self.calls.append('refresh')
        return Session(ACCESS, 'fixture-rotated-refresh-token', session.user_id, self.clock() + 1000)

    def sign_out(self, session):
        self.calls.append('logout')
        if self.fail_logout:
            raise AccountError('unavailable')


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.host = Host(Path(self.tmp.name)/'data')
        self.now = 1000
        self.client = Client(lambda: self.now)
        self.keychain = Keychain()
        self.account = self.new_account()
        self.settle()

    def new_account(self):
        return SupabaseAccount(self.host.store, self.client, SessionVault(self.keychain), clock=lambda: self.now)

    def reopen(self):
        """A new SupabaseAccount on the same store and folder, the way a launch after an update makes one."""
        self.account.close()
        self.account.worker.shutdown(wait=True)
        self.account = self.new_account()

    def tearDown(self):
        self.client.release.set()
        self.account.close()
        self.account.worker.shutdown(wait=True)
        self.host.close()
        self.tmp.cleanup()

    def settle(self):
        deadline = time.monotonic() + 3
        while self.account.future is not None and time.monotonic() < deadline:
            self.account.tick()
            time.sleep(.001)
        self.assertIsNone(self.account.future, 'Background account operation did not finish')
        return self.account.status()

    def call(self, name, **args):
        return self.account.dispatch('dervo_account_' + name, args)

    def send(self):
        self.call('send_code', email='fixture@example.test')
        return self.settle()['operation_id']

    def signin(self):
        op = self.send()
        self.call('verify_code', operationId=op, code='123456')
        return self.settle()

    def restart(self):
        self.account.close()
        self.account.worker.shutdown(wait=True)
        self.account = self.new_account()
        return self.settle()

    def test_code_send_is_not_signin_and_no_secrets_enter_sqlite_or_status(self):
        self.send()
        state = self.account.status()
        self.assertEqual(state['state'], 'awaiting_user')
        self.assertTrue(state['can_verify'])
        self.assertIsNone(state['identity'])
        with self.assertRaises(Rejected):
            self.account.require_access()
        self.signin_after_cancel()
        saved = json.dumps([tuple(r) for r in self.host.store.db.execute('select * from dervo_account_runtime')])
        public = json.dumps(self.account.status())
        for value in [ACCESS, REFRESH, 'fixture@example.test', '123456']:
            self.assertNotIn(value, saved)
            self.assertNotIn(value, public)

    def test_a_code_that_couldnt_be_sent_offers_the_email_step_again_with_the_reason(self):
        # Onboarding audit, 8 Oct: a resend inside the auth cooldown, or no connection, used to land on 'unknown',
        # whose only ways out (Check session, Sign out) made no sense before any session existed.
        for code, words in (('rate_limited', 'Wait a minute'), ('unavailable', 'Check your connection')):
            self.client.send_error = code
            self.send()
            state = self.account.status()
            self.assertEqual(state['state'], 'failed')
            self.assertTrue(state['can_start'])
            self.assertFalse(state['can_restore'])
            self.assertIn(words, state['detail'])
        self.client.send_error = None
        self.send()
        self.assertEqual(self.account.status()['state'], 'awaiting_user')

    def test_quitting_before_the_first_sign_in_finished_opens_on_the_email_step_again(self):
        # Code review, 8 Oct: a failed send, or quitting while the code was on its way, used to reopen on "Sign-in was
        # interrupted", with Check session and Sign out, though no account had ever signed in here.
        self.client.send_error = 'rate_limited'
        self.send()
        self.client.send_error = None
        self.reopen()
        self.settle()
        self.assertEqual(self.account.status()['state'], 'signed_out')
        self.assertTrue(self.account.status()['can_start'])
        self.send()
        self.reopen()
        self.settle()
        state = self.account.status()
        self.assertEqual(state['state'], 'signed_out')
        self.assertTrue(state['can_start'])
        self.assertFalse(state['can_restore'])
        # Negative control: once an account has signed in here, an interrupted sign-in is still said and checked.
        self.signin()
        self.account._save('pending')
        self.reopen()
        self.assertEqual(self.account.status()['state'], 'interrupted')

    def signin_after_cancel(self):
        self.call('cancel', operationId=self.account.operation['id'])
        self.settle()
        self.signin()

    def test_pending_and_revoked_accounts_are_real_identities_without_beta_grants(self):
        self.client.allowed = False
        self.client.membership_status = 'pending'
        state = self.signin()
        self.assertEqual(state['state'], 'signed_in')
        self.assertFalse(state['membership']['allowed'])
        with self.assertRaises(Rejected):
            self.account.require_access()
        self.assertEqual(len(self.account.require_identity(PROJECT.url + '/auth/v1', USER)), 64)
        self.client.allowed = True
        self.client.membership_status = 'approved'
        self.now += self.account.PENDING_CHECK_SECONDS + 1  # a waiting membership is checked every minute
        self.account.tick()
        self.settle()
        self.account.require_access()
        self.client.allowed = False
        self.client.membership_status = 'revoked'
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick()
        self.settle()
        self.assertEqual(self.account.status()['membership']['status'], 'revoked')
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_expired_access_lease_denies_even_before_next_tick(self):
        self.signin()
        self.account.require_access()
        self.now += self.account.ACCESS_SECONDS + 1
        self.assertFalse(self.account.status()['membership']['allowed'])
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_cancel_is_prompt_and_late_tokens_cannot_activate(self):
        op = self.send()
        self.client.pause_verify = True
        started = time.monotonic()
        verifying = self.call('verify_code', operationId=op, code='123456')
        self.assertLess(time.monotonic()-started, .2)
        self.assertTrue(self.client.entered.wait(1))
        started = time.monotonic()
        self.call('cancel', operationId=verifying['operation_id'])
        self.assertLess(time.monotonic()-started, .2)
        self.assertEqual(json.loads(self.host.store.db.execute('select document from dervo_account_runtime').fetchone()[0])['intent'], 'cancelled')
        self.client.release.set()
        state = self.settle()
        self.assertEqual(state['state'], 'cancelled')
        self.assertIsNone(self.keychain.data)
        self.assertIsNone(state['identity'])
        self.assertEqual(self.restart()['state'], 'signed_out')

    def test_cancel_tombstone_removes_late_token_on_restart(self):
        self.send()
        self.call('cancel', operationId=self.account.operation['id'])
        self.settle()
        SessionVault(self.keychain).save(Session(ACCESS, REFRESH, USER, 2000))
        self.assertEqual(self.restart()['state'], 'signed_out')
        self.assertIsNone(self.keychain.data)
        self.assertNotIn('user', self.client.calls)

    def test_wrong_code_can_retry_but_stale_operation_is_rejected(self):
        op = self.send()
        state = self.call('verify_code', operationId=op, code='000000')
        self.settle()
        self.assertTrue(self.account.status()['can_verify'])
        with self.assertRaises(Rejected):
            self.call('verify_code', operationId=op, code='123456')
        self.call('verify_code', operationId=state['operation_id'], code='123456')
        self.assertEqual(self.settle()['state'], 'signed_in')

    def test_restart_verifies_saved_session_and_keeps_owner(self):
        self.signin()
        self.client.calls.clear()
        state = self.restart()
        self.assertEqual(state['state'], 'signed_in')
        self.assertIn('user', self.client.calls)
        self.assertIn('access', self.client.calls)
        self.assertNotIn('send', self.client.calls)
        self.account.require_access()

    def test_restart_recovers_each_temporary_read_failure_with_fresh_access(self):
        self.signin()
        session = SessionVault(self.keychain).load()
        for stage, value in [('user', self.client.user(ACCESS, expected_user=USER)),
                             ('access', self.client.access(session)),
                             ('profile', self.client.profile(session))]:
            with self.subTest(stage=stage):
                self.client.calls.clear()
                def recovered_connection(_):
                    self.now += 3
                with patch.object(self.client, stage, side_effect=[AccountError('unavailable', transient=True), value]) as read, \
                        patch('supabase_account.retry_pause', side_effect=recovered_connection) as pause:
                    state = self.restart()
                self.assertEqual(state['state'], 'signed_in')
                self.assertEqual(read.call_count, 2)
                pause.assert_called_once_with(1)
                self.assertEqual(state['access_valid_until'], self.now + self.account.ACCESS_SECONDS)
                self.assertFalse(set(self.client.calls) & {'send', 'verify', 'refresh', 'save_name'})
                self.account.require_access()

    def test_persistent_read_failure_stops_after_one_retry_without_access(self):
        self.signin()
        with patch.object(self.client, 'access', side_effect=AccountError('unavailable', transient=True)) as read, \
                patch('supabase_account.retry_pause') as pause:
            state = self.restart()
        self.assertEqual(read.call_count, 2)
        pause.assert_called_once_with(1)
        self.assertEqual(state['state'], 'unknown')
        self.assertTrue(state['can_restore'])
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_retry_uses_current_revocation_instead_of_previous_grant(self):
        self.signin()
        revoked = dict(status='revoked', allowed=False, expires_at=None,
                       release_channel='private-beta', is_admin=False)
        with patch.object(self.client, 'access', side_effect=[AccountError('service', transient=True), revoked]), \
                patch('supabase_account.retry_pause'):
            state = self.restart()
        self.assertEqual(state['membership']['status'], 'revoked')
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_read_retry_does_not_repeat_refresh_or_profile_write(self):
        self.signin()
        profile = {'user_id': USER, 'display_name': 'New name'}
        self.client.calls.clear()
        with patch.object(self.client, 'profile', side_effect=[AccountError('unavailable', transient=True), profile]), \
                patch('supabase_account.retry_pause'):
            self.call('save_profile', displayName='New name')
            state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(self.client.calls.count('save_name'), 1)
        self.now = 1950
        with patch.object(self.client, 'profile', side_effect=[AccountError('unavailable', transient=True), profile]), \
                patch('supabase_account.retry_pause'):
            state = self.restart()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(self.client.calls.count('refresh'), 1)
        self.assertEqual(self.client.calls.count('save_name'), 1)

    def test_invalid_read_and_uncertain_refresh_are_not_retried(self):
        self.signin()
        with patch.object(self.client, 'profile', side_effect=AccountError('invalid_response')) as read, \
                patch('supabase_account.retry_pause') as pause:
            self.assertEqual(self.restart()['state'], 'unknown')
        self.assertEqual(read.call_count, 1)
        pause.assert_not_called()
        self.now = 1950
        with patch.object(self.client, 'refresh', side_effect=AccountError('unavailable', transient=True)) as refresh, \
                patch('supabase_account.retry_pause') as pause:
            self.assertEqual(self.restart()['state'], 'unknown')
        self.assertEqual(refresh.call_count, 1)
        pause.assert_not_called()

    def test_rotated_tokens_saved_before_failing_access_check(self):
        self.signin()
        self.now = 1950
        self.client.fail_access = True
        self.account.tick()
        self.settle()
        self.assertEqual(self.account.status()['state'], 'unknown')
        self.assertEqual(SessionVault(self.keychain).load().refresh_token, 'fixture-rotated-refresh-token')
        with self.assertRaises(Rejected):
            self.account.require_access()
        self.client.fail_access = False
        self.call('restore')
        self.assertEqual(self.settle()['state'], 'signed_in')
        self.assertEqual(self.client.calls.count('refresh'), 1)

    def test_dropped_connection_during_a_background_check_keeps_access(self):
        # 18 Sep: a design sweep stopped partway with "beta access needs confirmation". One background check that lost
        # its connection dropped the account to unknown, and the next tool call of the running task was refused.
        self.signin()
        self.now += self.account.CHECK_SECONDS + 1
        with patch.object(self.client, 'access', side_effect=AccountError('unavailable', transient=True)), \
                patch('supabase_account.retry_pause'):
            self.account.tick()
            state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.account.require_access()
        self.assertGreater(self.account.next_check, self.now)
        # The grant it already had still runs out if the service never answers again.
        self.now += self.account.ACCESS_SECONDS
        with patch.object(self.client, 'access', side_effect=AccountError('unavailable', transient=True)), \
                patch('supabase_account.retry_pause'):
            self.account.tick()
            self.settle()
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_revocation_still_ends_access_at_the_next_check(self):
        self.signin()
        self.account.require_access()
        self.client.allowed = False
        self.client.membership_status = 'revoked'
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick()
        self.settle()
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_a_slow_check_does_not_leave_a_gap_in_access(self):
        # A task asks for access on every tool call. The grant has to outlast a check that takes a while to answer, and
        # leave room for a few retries after a dropped connection.
        self.signin()
        self.assertGreaterEqual(self.account.ACCESS_SECONDS - self.account.CHECK_SECONDS, 120)
        self.assertGreaterEqual((self.account.ACCESS_SECONDS - self.account.CHECK_SECONDS) // self.account.RETRY_SECONDS, 3)
        self.now += self.account.CHECK_SECONDS + 60
        self.account.require_access()

    def checks(self):
        return self.client.calls.count('access')

    def test_an_idle_signed_in_mac_checks_every_three_minutes_not_every_twenty_seconds(self):
        # Efficiency review, 23 Sep: three HTTPS requests every 20 seconds, 540 an hour per open Mac, idle or not.
        self.signin()
        before = self.checks()
        for _ in range(17):  # ticks 10 seconds apart, up to 170 seconds after sign-in
            self.now += 10
            self.account.tick()
            self.settle()
        self.assertEqual(self.checks(), before, 'nothing is due before three minutes')
        self.account.require_access()
        self.now += 11
        self.account.tick()
        self.settle()
        self.assertEqual(self.checks(), before + 1)
        for _ in range(360):  # an hour of ticks
            self.now += 10
            self.account.tick()
            self.settle()
            self.account.require_access()  # and the grant never lapses between checks
        self.assertEqual(self.checks(), before + 1 + 20, 'twenty checks an hour, not 180')

    def test_a_dropped_connection_retries_soon_enough_to_keep_the_grant(self):
        self.signin()
        self.now += self.account.CHECK_SECONDS + 1
        failing = patch.object(self.client, 'access', side_effect=AccountError('unavailable', transient=True))
        with failing as access, patch('supabase_account.retry_pause'):
            for _ in range(8):  # 80 seconds of ticks: the check fails and retries every 20 seconds
                self.account.tick()
                self.settle()
                self.account.require_access()
                self.now += 10
        self.assertEqual(access.call_count, 4 * 2, 'four checks, each tried twice, 20 seconds apart')
        # The service answers again before the grant runs out, and the grant is renewed with no gap.
        self.account.tick()
        self.settle()
        self.account.require_access()
        self.assertGreater(self.account.status()['access_valid_until'], self.now + self.account.ACCESS_SECONDS - 20)

    def test_a_session_that_ends_before_the_next_check_is_refreshed_first(self):
        with patch.object(self.client, 'verify_code', return_value=Session(ACCESS, REFRESH, USER, self.now + 100)):
            self.signin()
        self.assertEqual(self.account.status()['access_valid_until'], self.now + 100)
        self.assertEqual(self.account.next_check, self.now + 100 - self.account.REFRESH_LEAD)
        self.now += 100 - self.account.REFRESH_LEAD
        self.account.tick()
        self.settle()
        self.assertEqual(self.client.calls.count('refresh'), 1)
        self.now += 50  # past the old session's end
        self.account.require_access()

    def test_waking_from_sleep_checks_at_once(self):
        self.signin()
        self.account.tick()
        before = self.checks()
        self.now += 30
        self.account.tick()
        self.settle()
        self.assertEqual(self.checks(), before, 'a tick 30 seconds later is not a wake')
        self.now += self.account.WAKE_GAP + 40  # the Mac slept for a minute and a half, less than a check interval
        self.account.tick()
        self.settle()
        self.assertEqual(self.checks(), before + 1)

    def test_a_refused_action_checks_on_the_next_tick_at_most_every_twenty_seconds(self):
        # Waiting for approval, you try something: Dervo asks now rather than at the next scheduled check.
        self.client.allowed = False
        self.client.membership_status = 'pending'
        self.signin()
        self.account.tick()
        self.settle()
        before = self.checks()
        with self.assertRaises(Rejected):
            self.account.require_access()
        self.client.allowed = True
        self.client.membership_status = 'approved'
        self.now += 1
        self.account.tick()
        self.settle()
        self.assertEqual(self.checks(), before + 1)
        self.account.require_access()
        # Refused again within 20 seconds of that check: no second request until the retry time.
        self.client.allowed = False
        self.client.membership_status = 'revoked'
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick()
        self.settle()
        after = self.checks()
        for _ in range(3):
            with self.assertRaises(Rejected):
                self.account.require_access()
            self.now += 5
            self.account.tick()
            self.settle()
        self.assertEqual(self.checks(), after, 'refusals within 20 seconds of a check ask nothing more')
        with self.assertRaises(Rejected):
            self.account.require_access()
        self.now += 6
        self.account.tick()
        self.settle()
        self.assertEqual(self.checks(), after + 1)

    def test_the_host_loop_can_sleep_until_the_next_check(self):
        self.assertIsNone(self.account.tick_due(), 'signed out: nothing is scheduled')
        self.signin()
        self.assertEqual(self.account.tick_due(), self.account.CHECK_SECONDS)
        self.now += self.account.CHECK_SECONDS
        self.assertEqual(self.account.tick_due(), 0)
        self.account.tick()
        self.assertEqual(self.account.tick_due(), 0, 'a check in flight is collected on the next tick')
        self.settle()
        self.assertEqual(self.account.tick_due(), self.account.CHECK_SECONDS)

    def test_keychain_failure_never_activates_account(self):
        self.keychain.fail_write = True
        state = self.signin()
        self.assertEqual(state['state'], 'unknown')
        self.assertIsNone(state['identity'])
        self.assertNotIn('access', self.client.calls)

    def test_signout_clears_local_tokens_even_if_server_unavailable(self):
        self.signin()
        self.client.fail_logout = True
        self.call('sign_out')
        state = self.settle()
        self.assertEqual(state['state'], 'signed_out')
        self.assertIsNone(self.keychain.data)
        self.assertIn('couldn’t confirm server sign-out', state['detail'])
        self.assertEqual(self.restart()['state'], 'signed_out')

    def make_project(self):
        """Work in this workspace, which the first account owns."""
        root = Path(self.tmp.name)
        (root / 'project').mkdir(exist_ok=True)
        self.host.store.add_profile('provider', 'codex', root / 'profile', 'fixture@example.test', 'personal')
        self.host.store.add_project('project', root / 'project', 'provider', 'synthetic-model')

    def test_different_account_does_not_claim_existing_workspace(self):
        self.signin()
        self.make_project()
        owner = dict(self.account.saved['owner'])
        self.call('sign_out')
        self.settle()
        self.client.user_id = OTHER
        state = self.signin()
        self.assertEqual(state['state'], 'account_mismatch')
        self.assertIsNone(state['identity'])
        self.assertEqual(self.account.saved['owner'], owner)
        with self.assertRaises(Rejected):
            self.account.require_access()

    def test_a_different_account_takes_a_workspace_with_no_project_yet(self):
        # 30 Sep: a tester who signed in with the wrong email is waiting for approval. Before any project exists there's
        # nothing here to belong to that account, so the right one can sign in.
        self.client.allowed = False
        self.client.membership_status = 'pending'
        self.signin()
        first = dict(self.account.saved['owner'])
        self.call('sign_out')
        self.settle()
        self.client.user_id = OTHER
        self.client.allowed = True
        self.client.membership_status = 'approved'
        state = self.signin()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['identity']['subject'], OTHER)
        self.assertNotEqual(self.account.saved['owner'], first)
        self.account.require_access()
        self.assertEqual(self.restart()['state'], 'signed_in')
        self.assertEqual(self.account.saved['owner']['subject'], OTHER, 'still theirs after a restart')
        # A project makes the workspace theirs for good, so the first account is now the one that's refused.
        self.make_project()
        self.call('sign_out')
        self.settle()
        self.client.user_id = USER
        self.assertEqual(self.signin()['state'], 'account_mismatch')
        self.assertEqual(self.account.saved['owner']['subject'], OTHER)

    def test_profile_update_is_explicit_and_keeps_membership(self):
        self.signin()
        self.call('save_profile', displayName='New name')
        self.account.require_access()
        self.assertEqual(self.account.status()['state'], 'signed_in')
        self.assertFalse(self.account.status()['can_save_profile'])
        state = self.settle()
        self.assertEqual(state['identity']['display_name'], 'New name')
        self.assertTrue(state['membership']['allowed'])
        self.assertEqual(self.client.calls.count('save_name'), 1)


class ConfigAndGateTests(unittest.TestCase):
    def test_missing_configuration_is_local_only_but_invalid_configuration_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(read(root))
            (root/'runtime').mkdir()
            (root/'runtime'/NAME).write_text('{"schema":1,"required":false,"project":{}}')
            with self.assertRaises(Rejected):
                read(root)
            account = create(None, root/'data', root)
            self.assertIsInstance(account, UnavailableAccount)
            with self.assertRaises(Rejected):
                account.require_access()

    def test_configured_denial_applies_before_coding_or_harness_start(self):
        from coding_integration import CodingTasks
        from harness_host import HarnessCoordinator
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(tmp, account_runtime=lambda _: UnavailableAccount())
            try:
                coding = object.__new__(CodingTasks)
                coding.host = host
                harness = object.__new__(HarnessCoordinator)
                harness.host = host
                with self.assertRaises(Rejected):
                    coding.start({})
                with self.assertRaises(Rejected):
                    harness.request({})
                self.assertEqual(host.dispatch('projects', {}), [])
            finally:
                host.close()

    def test_vault_rejects_corrupted_secret_without_echoing_it(self):
        store = Keychain()
        store.data = (ACCESS+'bad-json').encode()
        with self.assertRaises(KeychainError) as raised:
            SessionVault(store).load()
        self.assertNotIn(ACCESS, str(raised.exception))


PERSON = '00000000-0000-0000-0000-00000000000a'


class TesterTests(unittest.TestCase):
    """Testers in Settings: admins only, on the account worker, through the server's own admin checks."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.host = Host(Path(self.tmp.name) / 'data'); self.addCleanup(self.host.close)
        self.now = 1000
        self.client = Client(lambda: self.now)
        self.client.people = [
            dict(user_id=USER, email='admin@example.test', status='approved', expires_at=None, signed_up_at=10, updated_at=10, is_admin=True),
            dict(user_id=PERSON, email='maya@example.test', status='pending', expires_at=None, signed_up_at=900, updated_at=900, is_admin=False)]
        self.client.audit = [dict(id=n, changed_at=100 + n, actor_id=USER if n % 2 else OTHER, actor_email='admin@example.test',
                                  subject_id=PERSON, subject_email='maya@example.test', previous_status='pending',
                                  new_status='approved', expires_at=None) for n in range(30, 0, -1)]
        self.account = SupabaseAccount(self.host.store, self.client, SessionVault(Keychain()), clock=lambda: self.now)
        self.addCleanup(lambda: (self.account.close(), self.account.worker.shutdown(wait=True)))

    def settle(self):
        deadline = time.monotonic() + 3
        while (self.account.future is not None or self.account.testers_future is not None) and time.monotonic() < deadline:
            self.account.tick()
            time.sleep(.001)
        self.assertIsNone(self.account.testers_future, 'Testers job did not finish')

    def signin(self, admin=True):
        self.client.is_admin = admin
        self.account.dispatch('dervo_account_send_code', {'email': 'admin@example.test'}); self.settle()
        self.account.dispatch('dervo_account_verify_code', {'operationId': self.account.operation['id'], 'code': '123456'}); self.settle()
        self.assertEqual(self.account.status()['state'], 'signed_in')

    def load(self, reload=False):
        view = self.account.dispatch('dervo_testers', {'reload': reload})
        self.settle()
        return self.account.dispatch('dervo_testers', {'reload': False}), view

    def test_only_an_admin_can_load_testers_and_nothing_is_asked_of_the_server_otherwise(self):
        with self.assertRaisesRegex(Rejected, 'Only Dervo admins'):
            self.account.dispatch('dervo_testers', {'reload': False})
        self.signin(admin=False)
        for op, args in (('dervo_testers', {'reload': True}), ('dervo_tester_set', {'userId': PERSON, 'status': 'approved', 'expiresAt': None})):
            with self.subTest(op=op), self.assertRaisesRegex(Rejected, 'Only Dervo admins|isn’t on the list'):
                self.account.dispatch(op, args)
        self.assertNotIn('testers', self.client.calls)
        self.assertFalse(any(isinstance(c, tuple) for c in self.client.calls), 'no audit read')
        self.assertEqual(self.client.changes, [])

    def test_an_admin_sees_people_and_the_audit_a_page_at_a_time(self):
        self.signin()
        view, first = self.load()
        self.assertEqual(first['state'], 'loading')
        self.assertEqual(view['state'], 'ready')
        self.assertEqual([(p['email'], p['is_you']) for p in view['people']], [('admin@example.test', True), ('maya@example.test', False)])
        self.assertEqual(len(view['audit']), 20)
        self.assertTrue(view['more'])
        self.assertEqual([row['by_you'] for row in view['audit'][:2]], [False, True], 'the page can say "You" for your own changes')
        self.assertIn(('audit', None, 21), self.client.calls, 'one more than a page, to know there are older ones')
        self.account.dispatch('dervo_testers_older', {}); self.settle()
        view = self.account.dispatch('dervo_testers', {'reload': False})
        self.assertEqual((len(view['audit']), view['more']), (30, False))
        self.assertEqual([row['id'] for row in view['audit']], list(range(30, 0, -1)))
        with self.assertRaisesRegex(Rejected, 'no older changes'):
            self.account.dispatch('dervo_testers_older', {})
        before = len(self.client.calls)
        self.account.dispatch('dervo_testers', {'reload': False})
        self.assertEqual(len(self.client.calls), before, 'reading the page again doesn’t ask the server again')

    def test_a_list_cut_at_the_servers_500_says_so(self):
        # Security review, 19 Sep, finding 6: past 500 memberships the oldest sign-ups dropped off and the page looked whole.
        self.signin()
        view, _ = self.load()
        self.assertIs(view['cut'], False, 'two people is the whole list')
        self.client.people = [dict(user_id=USER, email='admin@example.test', status='approved', expires_at=None, signed_up_at=10,
                                   updated_at=10, is_admin=True)] + [
            dict(user_id=f'00000000-0000-4000-8000-{n:012d}', email=f'p{n}@example.test', status='pending', expires_at=None,
                 signed_up_at=1000 - n, updated_at=1000 - n, is_admin=False) for n in range(499)]
        view, _ = self.load(reload=True)
        self.assertEqual((len(view['people']), view['cut']), (500, True), 'a full page means older sign-ups may be missing')

    def test_approve_decline_and_end_dates_go_through_set_membership_and_the_list_reloads(self):
        self.signin()
        self.load()
        view = self.account.dispatch('dervo_tester_set', {'userId': PERSON, 'status': 'approved', 'expiresAt': self.now + 14 * 86400})
        self.assertEqual((view['state'], view['busy']), ('saving', PERSON))
        self.settle()
        self.assertEqual(self.client.changes, [(USER, PERSON, 'approved', self.now + 14 * 86400)])
        view = self.account.dispatch('dervo_testers', {'reload': False})
        self.assertEqual(next(p for p in view['people'] if p['user_id'] == PERSON)['status'], 'approved', 'the list reloaded after the change')
        self.account.dispatch('dervo_tester_set', {'userId': PERSON, 'status': 'revoked', 'expiresAt': None}); self.settle()
        self.assertEqual(self.client.changes[-1][2:], ('revoked', None))
        for args, reason in (({'userId': USER, 'status': 'revoked', 'expiresAt': None}, 'your own access'),
                             ({'userId': 'someone-else', 'status': 'approved', 'expiresAt': None}, 'isn’t on the list'),
                             ({'userId': PERSON, 'status': 'pending', 'expiresAt': None}, 'Approve or revoke'),
                             ({'userId': PERSON, 'status': 'approved', 'expiresAt': self.now - 1}, 'after today'),
                             ({'userId': PERSON, 'status': 'approved', 'expiresAt': self.now + 401 * 86400}, 'within a year'),
                             ({'userId': PERSON, 'status': 'approved', 'expiresAt': True}, 'after today'),
                             ({'userId': PERSON, 'status': 'revoked', 'expiresAt': self.now + 86400}, 'after today')):
            with self.subTest(args=args), self.assertRaisesRegex(Rejected, reason):
                self.account.dispatch('dervo_tester_set', args)
        self.assertEqual(len(self.client.changes), 2, 'refused changes never reach the server')

    def test_a_failure_says_so_and_signing_out_forgets_what_was_loaded(self):
        self.signin()
        self.load()
        self.client.fail_testers = True
        view, _ = self.load(reload=True)
        self.assertEqual(view['state'], 'error')
        self.assertIn('couldn’t load testers', view['detail'])
        self.assertEqual(len(view['people']), 2, 'what was loaded stays readable')
        self.account.dispatch('dervo_account_sign_out', {}); self.settle()
        self.assertEqual(self.account.testers, SupabaseAccount._testers_empty())
        with self.assertRaisesRegex(Rejected, 'Only Dervo admins'):
            self.account.dispatch('dervo_testers', {'reload': False})

    def test_a_local_workspace_without_a_dervo_account_has_no_testers(self):
        with self.assertRaises(Rejected):
            self.host.dispatch('dervo_testers', {'reload': False})


class WaitlistTests(unittest.TestCase):
    """The waitlist on the Testers page: admins only, a small batch at a time, one result per person."""
    signin = TesterTests.signin

    def setUp(self):
        TesterTests.setUp(self)
        self.client.waiting = [dict(email=email, signed_up_at=100 + n, status='waiting', invited_at=None, expires_at=None, delivery=None)
                               for n, email in enumerate(['ada@example.test', 'bo@example.test', 'bounce@example.test'])]

    def settle(self):
        deadline = time.monotonic() + 3
        while (self.account.future is not None or self.account.waitlist_future is not None) and time.monotonic() < deadline:
            self.account.tick()
            time.sleep(.001)
        self.assertIsNone(self.account.waitlist_future, 'Waitlist job did not finish')

    def waitlist(self, reload=False):
        self.account.dispatch('dervo_waitlist', {'reload': reload}); self.settle()
        return self.account.dispatch('dervo_waitlist', {'reload': False})

    def invite(self, action, emails):
        first = self.account.dispatch('dervo_waitlist_invite', {'action': action, 'emails': emails}); self.settle()
        return self.account.dispatch('dervo_waitlist', {'reload': False}), first

    def test_a_tester_never_reaches_the_waitlist(self):
        with self.assertRaisesRegex(Rejected, 'Only Dervo admins'):
            self.account.dispatch('dervo_waitlist', {'reload': False})
        self.signin(admin=False)
        for op, args in (('dervo_waitlist', {'reload': True}), ('dervo_waitlist_invite', {'action': 'invite', 'emails': ['ada@example.test']})):
            with self.subTest(op=op), self.assertRaisesRegex(Rejected, 'Only Dervo admins|isn’t on the list'):
                self.account.dispatch(op, args)
        self.assertNotIn('waitlist', self.client.calls)
        self.assertEqual(self.client.batches, [])

    def test_a_batch_reports_each_person_and_reloads_the_list(self):
        self.signin()
        view = self.waitlist()
        self.assertEqual([(p['email'], p['status']) for p in view['people']],
                         [('ada@example.test', 'waiting'), ('bo@example.test', 'waiting'), ('bounce@example.test', 'waiting')])
        view, first = self.invite('invite', ['ada@example.test', 'bounce@example.test'])
        self.assertEqual(first['state'], 'sending')
        self.assertEqual(self.client.batches, [(USER, 'invite', ['ada@example.test', 'bounce@example.test'])])
        self.assertEqual([(r['email'], r['result']) for r in view['results']], [('ada@example.test', 'sent'), ('bounce@example.test', 'send_failed')])
        self.assertEqual(view['results_id'], 1)
        self.assertEqual([(p['status'], p['delivery']) for p in view['people']], [('invited', 'sent'), ('waiting', None), ('invited', 'failed')])
        # Inviting someone again is the server's call, and it sends nothing; Resend is its own action.
        view, _ = self.invite('invite', ['ada@example.test'])
        self.assertEqual(view['results'], [{'email': 'ada@example.test', 'result': 'already_invited', 'expires_at': None}])
        view, _ = self.invite('resend', ['ada@example.test'])
        self.assertEqual((view['results'][0]['result'], view['results_id']), ('sent', 3))

    def test_adding_a_direct_address_only_adds_it_and_normalizes_before_duplicate_checks(self):
        self.signin(); self.waitlist()
        view, first = self.invite('add', [' NEW@Example.test '])
        self.assertEqual(first['state'], 'saving')
        self.assertEqual(self.client.batches, [(USER, 'add', ['new@example.test'])])
        person = next(p for p in view['people'] if p['email'] == 'new@example.test')
        self.assertEqual((person['status'], person['source'], person['delivery']), ('waiting', 'admin', None))
        view, _ = self.invite('add', ['new@example.test'])
        self.assertEqual(view['results'][0]['result'], 'already_listed')
        for emails in [['ada@example.test', ' ADA@Example.test '], ['not an email'], ['a..b@example.test']]:
            with self.subTest(emails=emails), self.assertRaises(Rejected):
                self.account.dispatch('dervo_waitlist_invite', {'action': 'add', 'emails': emails})
        self.assertEqual(len(self.client.batches), 2)

    def test_late_waitlist_results_dont_survive_a_changed_session_or_admin_removal(self):
        self.signin(); self.waitlist()
        result = {'listing': {'people': self.client.waiting, 'more': False}, 'results': None}
        old_session = self.account.session
        self.account.session = Session('new-session-token', REFRESH, USER, self.now + 1000)
        self.account._finish_waitlist(result)
        self.assertEqual(self.account.waitlist, SupabaseAccount._waitlist_empty())
        self.account.session = old_session
        self.account.membership['is_admin'] = False
        self.account._finish_waitlist(result)
        self.assertEqual(self.account.waitlist, SupabaseAccount._waitlist_empty())

    def test_only_listed_people_in_a_small_batch(self):
        self.signin(); self.waitlist()
        for action, emails in (('invite', []), ('invite', ['stranger@example.test']), ('invite', ['ada@example.test', 'ada@example.test']),
                               ('email', ['ada@example.test']), ('invite', 'ada@example.test'), ('invite', [None]),
                               ('invite', ['ada@example.test'] * 11)):
            with self.subTest(action=action, emails=emails), self.assertRaises(Rejected):
                self.account.dispatch('dervo_waitlist_invite', {'action': action, 'emails': emails})
        self.assertEqual(self.client.batches, [])
        with self.assertRaisesRegex(Rejected, 'Unsupported Dervo account fields'):
            self.account.dispatch('dervo_waitlist_invite', {'action': 'invite', 'emails': ['ada@example.test'], 'force': True})

    def test_an_unconfirmed_batch_says_so_and_nothing_outlives_the_session(self):
        self.signin(); self.waitlist()
        self.client.fail_batch = True
        view, _ = self.invite('invite', ['ada@example.test'])
        self.assertEqual(view['state'], 'error')
        self.assertIn('won’t email anyone twice', view['detail'])
        self.assertEqual(len(view['people']), 3, 'what was loaded stays readable')
        self.client.fail_batch, self.client.fail_waitlist = False, True
        view, _ = self.invite('invite', ['bo@example.test'])
        self.assertEqual((view['state'], view['results'][0]['result']), ('ready', 'sent'))
        self.assertIn('couldn’t reload', view['detail'])
        self.account.dispatch('dervo_account_sign_out', {}); self.settle()
        self.assertEqual(self.account.waitlist, SupabaseAccount._waitlist_empty())


class PhotoCacheTests(AccountTests):
    """#14: the last confirmed photo is kept on this Mac and shown at once after a restart or update."""
    def setUp(self):
        from test_account_photo import png
        import account_photo
        self.photo, self.raw = account_photo, account_photo.normalize(png())
        super().setUp()

    def new_account(self):
        return SupabaseAccount(self.host.store, self.client, SessionVault(self.keychain), clock=lambda: self.now,
                               data_root=self.host.root)

    @property
    def cached(self):
        return self.photo.cache_path(self.host.root, USER)

    def load(self, side_effect):
        with patch('account_photo.request', side_effect=side_effect) as request:
            self.call('photo', subject=USER)
            state = self.settle()
        return state, request

    def signed_in_with_cache(self):
        self.signin()
        state, _ = self.load([self.raw])
        self.assertEqual(state['photo']['state'], 'ready')
        self.assertTrue(self.cached.exists())
        return state['photo']['data_url']

    def test_a_restart_shows_the_cached_photo_before_any_fetch(self):
        data_url = self.signed_in_with_cache()
        with patch('account_photo.request', side_effect=AccountError('photo_service')) as request:
            self.reopen()
            self.assertEqual(self.account.photo['state'], 'cached')
            self.assertEqual(self.account.photo['data_url'], data_url)
            self.assertFalse(self.account.photo['checked'])
            request.assert_not_called()
            state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['photo']['data_url'], data_url)
        # Negative control: the same account on a folder with no copy starts not loaded, as before.
        self.account.close()
        self.account.worker.shutdown(wait=True)
        self.account = SupabaseAccount(self.host.store, self.client, SessionVault(self.keychain), clock=lambda: self.now,
                                       data_root=Path(self.tmp.name) / 'elsewhere')
        self.assertEqual(self.account.photo, {'state': 'not_loaded', 'data_url': None})

    def test_a_failed_fetch_keeps_the_cached_photo(self):
        data_url = self.signed_in_with_cache()
        state, _ = self.load(AccountError('photo_service'))
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['photo']['state'], 'cached')
        self.assertEqual(state['photo']['data_url'], data_url)
        self.assertEqual(state['photo']['error'], 'photo_service')
        self.assertTrue(self.cached.exists())
        # Negative control: with no copy on the Mac the same failure is today's error with no image.
        self.cached.unlink()
        state, _ = self.load(AccountError('photo_service'))
        self.assertEqual(state['photo'], {'state': 'error', 'data_url': None})

    def test_a_failed_save_keeps_the_old_photo_and_a_confirmed_save_replaces_it(self):
        data_url = self.signed_in_with_cache()
        from test_account_photo import png
        chosen = self.photo.normalize(png(pixels=(b'\0' + b'\x11\x22\x33\xff' * 128) * 128))
        # The read-back is stale, then the host's own re-check of the kept photo fails too.
        with patch('account_photo.request', side_effect=[None, self.raw, AccountError('photo_service')]) as request:
            self.call('save_photo', subject=USER, png=__import__('base64').b64encode(chosen).decode())
            state = self.settle()
        self.assertEqual(request.call_count, 3)
        self.assertEqual(state['photo']['state'], 'cached')
        self.assertEqual(state['photo']['data_url'], data_url)
        self.assertEqual(self.photo.read_cached(self.host.root, USER), self.raw)
        with patch('account_photo.request', side_effect=[None, chosen]):
            self.call('save_photo', subject=USER, png=__import__('base64').b64encode(chosen).decode())
            state = self.settle()
        self.assertEqual(state['photo']['state'], 'ready')
        self.assertEqual(self.photo.read_cached(self.host.root, USER), chosen)

    def test_a_real_missing_photo_clears_the_cache(self):
        self.signed_in_with_cache()
        state, _ = self.load([None])
        self.assertEqual(state['photo']['state'], 'missing')
        self.assertFalse(self.cached.exists())
        # A confirmed removal clears it the same way.
        self.load([self.raw])
        with patch('account_photo.request', side_effect=[None, None]):
            self.call('remove_photo', subject=USER)
            state = self.settle()
        self.assertEqual(state['photo']['state'], 'missing')
        self.assertFalse(self.cached.exists())

    def test_a_workspace_with_no_project_hands_over_and_drops_the_first_photo(self):
        self.signed_in_with_cache()
        self.call('sign_out')
        self.settle()
        self.assertTrue(self.photo.write_cached(self.host.root, USER, self.raw))
        self.client.user_id = OTHER
        self.assertEqual(self.signin()['state'], 'signed_in')
        self.assertFalse(self.cached.exists(), 'the first account’s photo copy goes when the workspace changes hands')

    def test_sign_out_clears_the_cached_photo_and_mismatch_leaves_it(self):
        self.signed_in_with_cache()
        self.make_project()  # with no project the workspace would change hands, so the lock needs one
        self.call('sign_out')
        self.settle()
        self.assertFalse(self.cached.exists())
        # The workspace still belongs to USER. A copy of their photo stays put when someone else is refused.
        self.assertTrue(self.photo.write_cached(self.host.root, USER, self.raw))
        self.client.user_id = OTHER
        state = self.signin()
        self.assertEqual(state['state'], 'account_mismatch')
        self.assertTrue(self.cached.exists())
        self.assertEqual(self.account.photo, {'state': 'not_loaded', 'data_url': None})
        self.assertIsNone(state['photo'])
        self.assertFalse(self.photo.cache_path(self.host.root, OTHER).exists())

    def test_an_interrupted_sign_out_clears_the_photo_on_the_next_launch(self):
        self.signed_in_with_cache()
        self.account._save('signed_out')  # the sign-out was recorded, then Dervo quit before cleanup ran
        self.reopen()
        self.assertEqual(self.account.photo['state'], 'not_loaded')
        self.settle()
        self.assertFalse(self.cached.exists())
        # Negative control: a cancelled sign-in on a fresh launch leaves an owner's copy alone.
        self.assertTrue(self.photo.write_cached(self.host.root, USER, self.raw))
        self.send()
        self.call('cancel', operationId=self.account.operation['id'])
        self.settle()
        self.assertTrue(self.cached.exists())

    def test_the_host_retries_a_cached_photo_up_to_three_times_then_stops(self):
        self.signed_in_with_cache()
        with patch('account_photo.request', side_effect=AccountError('photo_service')) as request:
            self.reopen()
            self.settle()
            for _ in range(6):
                self.account.tick()
                self.settle()
                self.now += 10  # under the 30 second gap: at most one try per three ticks
                self.account.tick()
                self.settle()
                self.now += 21
            self.assertEqual(request.call_count, 3)
            state = self.account.status()
            self.assertEqual(state['state'], 'signed_in')
            self.assertEqual(state['photo']['state'], 'cached')
            self.assertEqual(state['photo']['error'], 'photo_service')
            # "Check photo" in Settings still asks.
            self.account.tick()
            self.settle()
            self.call('photo', subject=USER)
            self.settle()
            self.assertEqual(request.call_count, 4)

    def test_a_confirmed_photo_isnt_fetched_again(self):
        self.signed_in_with_cache()
        with patch('account_photo.request', side_effect=[self.raw]) as request:
            self.reopen()
            self.settle()
            self.assertEqual(self.account.status()['photo']['state'], 'ready')
            # Negative control for the retry: once confirmed, ticks make no GET.
            for _ in range(5):
                self.now += 31
                self.account.tick()
                self.settle()
        self.assertEqual(request.call_count, 1)

    def test_a_dropped_connection_during_the_photo_retry_keeps_the_account(self):
        self.signed_in_with_cache()
        for transient, expected in [(True, 'signed_in'), (False, 'unknown')]:
            real = self.client.user

            def user(token, *, expected_user):
                if self.account.operation and self.account.operation['kind'] == 'photo_load':
                    raise AccountError('unavailable', transient=transient)
                return real(token, expected_user=expected_user)
            self.assertTrue(self.photo.write_cached(self.host.root, USER, self.raw))
            with patch.object(self.client, 'user', side_effect=user), patch('account_photo.request') as request:
                self.reopen()
                state = self.settle()
            request.assert_not_called()
            # Negative control (transient False): a real answer still ends the session, as it did before.
            self.assertEqual(state['state'], expected, transient)
            if transient:
                self.assertEqual(state['photo']['state'], 'cached')
                self.assertEqual(state['photo']['error'], 'photo_service')
                self.account.require_access()

    def test_no_token_or_path_is_in_the_photo_status(self):
        self.signed_in_with_cache()
        with patch('account_photo.request', side_effect=AccountError('photo_service')):
            self.reopen()
            self.settle()
        public = json.dumps(self.account.status())
        self.assertIn('"state": "cached"', public)
        self.assertEqual(set(self.account.status()['photo']), {'state', 'data_url', 'checked', 'error'})
        for value in [str(self.cached), 'profile-photo', str(self.host.root), ACCESS, REFRESH, 'access_token']:
            self.assertNotIn(value, public)
        self.assertNotIn(ACCESS.encode(), self.cached.read_bytes())

    def test_create_threads_the_data_folder_through(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'runtime').mkdir()
            (root / 'runtime' / NAME).write_text(json.dumps({'schema': 1, 'required': True, 'project': {
                'project_ref': PROJECT.project_ref, 'url': PROJECT.url, 'publishable_key': PROJECT.publishable_key}}))
            with patch('account_config.MacKeychain'), patch('account_config.LinuxSecretService'), \
                    patch('account_config.WindowsCredentials'), patch('release_profiles.read', return_value={'profile': 'fixture'}), \
                    patch('account_config.SupabaseAccount') as account:
                create(self.host.store, self.host.root, root)
            self.assertEqual(account.call_args.kwargs['data_root'], self.host.root)


class PlanTests(unittest.TestCase):
    """Paid plans and the free trial (8 Oct 2026). The server grants access; the app only asks and shows."""
    setUp, tearDown, settle, call, send, signin = (AccountTests.setUp, AccountTests.tearDown, AccountTests.settle,
        AccountTests.call, AccountTests.send, AccountTests.signin)

    def new_account(self):
        self.opened = []
        return SupabaseAccount(self.host.store, self.client, SessionVault(self.keychain), clock=lambda: self.now,
                               open_link=self.open_link)

    def open_link(self, url):
        if getattr(self, 'browser_fails', False):
            raise OSError('no browser')
        self.opened.append(url)

    def plan(self, operation, **args):
        return self.account.dispatch(operation, args)

    def ended_trial(self):
        self.client.allowed, self.client.membership_status = False, 'pending'
        self.client.plan_doc = plan_doc('none', available=True)
        self.client.plan_doc['trial']['ends_at'] = None
        return self.signin()

    def test_a_server_without_billing_signs_in_exactly_as_before(self):
        state = self.signin()
        self.assertEqual(state['state'], 'signed_in')
        self.assertIsNone(state['plan'])
        self.assertFalse(state['can_start_trial'] or state['can_buy'] or state['can_manage_billing'])
        self.account.require_access()
        self.client.fail_plan = True
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick(); self.settle()
        self.account.require_access()  # a failed plan read never ends access

    def test_starting_the_trial_asks_the_server_with_a_device_hash_and_picks_up_access(self):
        state = self.ended_trial()
        self.assertTrue(state['can_start_trial'])
        with self.assertRaises(Rejected) as refused:
            self.account.require_access()
        self.assertIn('Start your free trial', str(refused.exception))
        self.account.tick(); self.settle()  # the refusal asks for a fresh check first
        with patch('trial_device.machine_id', return_value='00000000-0000-0000-0000-0000000000AB'):
            self.plan('dervo_plan_start_trial')
            state = self.settle()
        self.assertEqual(len(self.client.trials), 1)
        self.assertRegex(self.client.trials[0], '^[0-9a-f]{64}$')
        self.assertNotIn('0000000000AB', self.client.trials[0])
        self.assertEqual(state['plan']['plan'], 'trial')
        self.assertFalse(state['can_start_trial'])
        self.assertEqual(state['billing']['state'], 'idle')
        self.account.require_access()

    def test_a_turn_already_running_outlives_the_trial_but_nothing_new_starts(self):
        self.client.plan_doc = plan_doc('trial')
        self.signin()
        self.host.account_session = self.account
        self.host.require_beta_access = self.account.require_access  # this account, however the runner sets the gate up
        self.host.require_run_access(); self.host.require_beta_access()
        self.assertFalse(self.account.plan_lapsed())
        self.client.allowed, self.client.membership_status = False, 'pending'
        self.client.plan_doc = plan_doc('none')
        self.client.plan_doc['trial']['ends_at'] = '2026-10-08T00:00:00Z'
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick(); self.settle()
        with self.assertRaisesRegex(Rejected, 'free trial has ended'):
            self.host.require_beta_access()  # a new send
        self.assertTrue(self.account.plan_lapsed())
        self.host.require_run_access()  # the turn under way finishes
        self.client.plan_doc = plan_doc('removed')
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick(); self.settle()
        with self.assertRaises(Rejected, msg='a removed account stops even a running turn'):
            self.host.require_run_access()
        self.client.plan_doc = plan_doc('none')
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick(); self.settle()
        self.host.require_run_access()
        self.account.dispatch('dervo_account_sign_out', {}); self.settle()
        with self.assertRaises(Rejected, msg='signing out stops it too'):
            self.host.require_run_access()

    def test_a_card_trial_is_bought_at_checkout_not_started_in_the_app(self):
        self.client.allowed, self.client.membership_status = False, 'pending'
        self.client.plan_doc = plan_doc('none', available=True)
        self.client.plan_doc['trial']['mode'] = 'card'
        state = self.signin()
        self.assertFalse(state['can_start_trial'])
        self.assertTrue(state['can_buy'])

    def test_a_refused_trial_explains_itself_and_keeps_the_session(self):
        self.ended_trial()
        self.client.trial_state = 'used_elsewhere'
        with patch('trial_device.machine_id', return_value='00000000-0000-0000-0000-0000000000AB'):
            self.plan('dervo_plan_start_trial')
            state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['billing']['state'], 'error')
        self.assertIn('already been used on this Mac', state['billing']['detail'])

    def test_no_trial_when_the_server_says_none_is_available(self):
        self.client.plan_doc = plan_doc('beta')
        self.signin()
        with self.assertRaises(Rejected):
            self.plan('dervo_plan_start_trial')
        self.assertEqual(self.client.trials, [])

    def test_checkout_opens_a_paddle_link_for_this_account_and_then_checks_every_minute(self):
        self.ended_trial()
        self.plan('dervo_billing_open', action='checkout', interval='year')
        state = self.settle()
        self.assertEqual(self.client.links, [(USER, 'checkout', 'year')])
        self.assertEqual(state['billing'], {'state': 'opened', 'action': 'checkout', 'detail': None})
        self.assertEqual(self.opened, ['https://sandbox-checkout.paddle.io/fixture'])
        self.assertNotIn('paddle.io', json.dumps(state))
        # Paying happens in the browser; the webhook approves the membership on the server.
        self.client.allowed, self.client.membership_status = True, 'approved'
        self.client.plan_doc = plan_doc('paid', can_buy=False, can_manage=True)
        self.now += self.account.PENDING_CHECK_SECONDS + 1
        self.account.tick(); state = self.settle()
        self.assertEqual(state['plan']['plan'], 'paid')
        self.assertTrue(state['can_manage_billing'])
        self.account.require_access()

    def test_billing_requests_are_checked_before_anything_is_sent(self):
        self.client.plan_doc = plan_doc('paid', can_buy=False, can_manage=True)
        self.signin()
        for args in [dict(action='checkout', interval='month'), dict(action='checkout', interval='week'),
                     dict(action='manage', interval='month'), dict(action='refund', interval=None)]:
            with self.assertRaises(Rejected):
                self.plan('dervo_billing_open', **args)
        with self.assertRaises(Rejected):
            self.account.dispatch('dervo_billing_open', {'action': 'manage'})
        self.assertEqual(self.client.links, [])
        self.plan('dervo_billing_open', action='manage', interval=None)
        self.assertEqual(self.settle()['billing']['state'], 'opened')
        self.assertEqual(self.opened, ['https://sandbox-customer-portal.paddle.com/fixture'])
        self.browser_fails = True
        self.plan('dervo_billing_open', action='manage', interval=None)
        self.assertEqual(self.settle()['billing']['state'], 'error')

    def test_a_billing_failure_is_reported_without_signing_out(self):
        self.ended_trial()
        self.client.fail_link = 'service'
        self.plan('dervo_billing_open', action='checkout', interval='month')
        state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['billing']['state'], 'error')
        self.assertEqual(self.opened, [])

    def test_checkout_sends_the_mac_hash_and_the_note_stays_until_the_plan_changes(self):
        # Someone on an app trial opens checkout: the plan already reads 'trial', so only a real change clears the note.
        self.client.plan_doc = plan_doc('trial')
        self.signin()
        with patch('trial_device.machine_id', return_value='00000000-0000-0000-0000-0000000000AB'):
            self.plan('dervo_billing_open', action='checkout', interval='year')
            self.assertEqual(self.settle()['billing']['state'], 'opened')
        self.assertRegex(self.client.devices[-1], '^[0-9a-f]{64}$')
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick()
        self.assertEqual(self.settle()['billing']['state'], 'opened')
        self.client.plan_doc = plan_doc('paid', can_buy=False, can_manage=True)
        self.client.plan_doc['subscription'] = {'status': 'active', 'interval': 'year', 'period_ends_at': None,
                                                'cancel_at': None, 'access_until': None}
        self.now += self.account.CHECK_SECONDS + 1
        self.account.tick()
        self.assertEqual(self.settle()['billing']['state'], 'idle')

    def test_refresh_checks_again_soon_and_signed_out_has_no_plan(self):
        self.ended_trial()
        self.client.allowed, self.client.membership_status = True, 'approved'
        self.client.plan_doc = plan_doc('paid', can_buy=False, can_manage=True)
        self.now += self.account.RETRY_SECONDS + 1
        self.plan('dervo_plan_refresh'); state = self.settle()
        self.assertEqual(state['plan']['plan'], 'paid')
        self.call('sign_out'); state = self.settle()
        self.assertIsNone(state['plan']); self.assertIsNone(state['billing'])
        with self.assertRaises(Rejected):
            self.plan('dervo_plan_refresh')


class TrialDeviceTests(unittest.TestCase):
    def test_the_key_is_a_salted_hash_and_falls_back_to_a_kept_random_id(self):
        import trial_device
        key = trial_device.device_key(None, read=lambda: 'ABC')
        self.assertRegex(key, '^[0-9a-f]{64}$')
        self.assertEqual(key, trial_device.device_key(None, read=lambda: 'ABC'))
        self.assertNotEqual(key, trial_device.device_key(None, read=lambda: 'ABD'))
        self.assertIsNone(trial_device.device_key(None, read=lambda: None))
        with tempfile.TemporaryDirectory() as root:
            first = trial_device.device_key(root, read=lambda: None)
            self.assertEqual(first, trial_device.device_key(root, read=lambda: None))


if __name__ == '__main__':
    unittest.main()
