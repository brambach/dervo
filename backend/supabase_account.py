"""Native account lifecycle. The main thread owns metadata and public state.

One background worker serializes Auth requests and Keychain writes. Cancellation
and sign-out record a durable tombstone before cleanup, so late tokens can't turn
into an active account after restart. No email, code or token enters SQLite.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import math
import time
import uuid
from time import sleep as retry_pause

from account_keychain import KeychainError
from dervo_connections import Rejected
from supabase_client import AccountError, Session, SupabaseClient

FIELDS = {
    'dervo_account_status': set(), 'dervo_account_send_code': {'email'},
    'dervo_account_verify_code': {'operationId', 'code'},
    'dervo_account_poll': {'operationId'}, 'dervo_account_cancel': {'operationId'},
    'dervo_account_restore': set(), 'dervo_account_sign_out': set(),
    'dervo_account_save_profile': {'displayName'},
    'dervo_account_photo': {'subject'},
    'dervo_account_save_photo': {'subject', 'png'},
    'dervo_account_remove_photo': {'subject'},
    # Testers, in Settings, for admins only (19 Sep 2026). The server refuses anyone else; these check first too.
    'dervo_testers': {'reload'}, 'dervo_testers_older': set(), 'dervo_tester_set': {'userId', 'status', 'expiresAt'},
    # The waitlist and beta invitations, on the same page (22 Sep 2026). The website refuses anyone who isn't an admin.
    'dervo_waitlist': {'reload'}, 'dervo_waitlist_invite': {'action', 'emails'},
    # Paid plans and the free trial (8 Oct 2026): start the trial, open Paddle checkout or billing in the browser, and
    # check the plan again after paying. None of them grants access here: the server does, from a trial or a signed
    # Paddle event, and the membership check below picks it up.
    'dervo_plan_start_trial': set(), 'dervo_billing_open': {'action', 'interval'}, 'dervo_plan_refresh': set(),
}
PLAN_FIELDS = ('dervo_plan_start_trial', 'dervo_billing_open', 'dervo_plan_refresh')
TRIAL_OUTCOMES = {
    'started': None,
    'already_used': 'This account has already used its free trial. Choose a plan to keep going.',
    'not_needed': 'This account already has access, so it doesn’t need a trial.',
    'used_elsewhere': 'A free trial has already been used on this Mac or with this email. Choose a plan to keep going.',
    'removed': 'This account no longer has access to Dervo. Your saved work is still here.',
    'not_offered': 'The free trial starts at checkout. Choose monthly or yearly; you won’t be charged until it ends.',
}
SAVED = 'Your saved work is still here.'
TESTER_FIELDS = ('dervo_testers', 'dervo_testers_older', 'dervo_tester_set')
WAITLIST_FIELDS = ('dervo_waitlist', 'dervo_waitlist_invite')
AUDIT_PAGE = 20
# The longest end date the Testers page sets: a year and a bit, so a typo can't grant access for decades.
LONGEST_END = 400 * 86400



# Why a code couldn't be sent, in words the email step shows under the field. Anything else keeps its AccountError text.
SEND_FAILED = {
    'rate_limited': 'Too many codes were asked for in a row. Wait a minute, then send a new one.',
    'unavailable': 'Dervo couldn’t reach the account service, so no code was sent. Check your connection and try again.',
}
# Anything else the send hit (a refused address, a TLS or reply problem) says no code went out, not a session problem.
SEND_FAILED_OTHER = 'Dervo couldn’t send a code to that address. Check it and try again, or use another email.'

class SessionVault:
    def __init__(self, keychain):
        self.keychain = keychain

    def load(self):
        raw = self.keychain.read()
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {'schema', 'user_id', 'access_token', 'refresh_token', 'expires_at'} or data['schema'] != 1:
                raise ValueError()
            if type(data['user_id']) is not str or str(uuid.UUID(data['user_id'])) != data['user_id']:
                raise ValueError()
            expiry = data['expires_at']
            if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
                raise ValueError()
            return Session(SupabaseClient._token(data['access_token']), SupabaseClient._token(data['refresh_token']),
                           data['user_id'], expiry)
        except Exception:
            raise KeychainError() from None

    def save(self, session):
        self.keychain.write(json.dumps({'schema': 1, 'user_id': session.user_id,
            'access_token': session.access_token, 'refresh_token': session.refresh_token,
            'expires_at': session.expires_at}, separators=(',', ':')).encode())

    def delete(self):
        self.keychain.delete()


def open_in_browser(url):
    """Open a checked billing link in the default browser. The window has no opener of its own, so the host does it."""
    import subprocess
    import platform_os
    if platform_os.WINDOWS or platform_os.LINUX:
        command = platform_os.open_command(url)
    else:
        command = ['/usr/bin/open', url]  # billing_url() already required https://, so it can't read as an option
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SupabaseAccount:
    # A grant lasts 5 minutes because every tool call of a running task asks for it: at 30 seconds, one slow check or a
    # Mac waking from sleep stopped runs partway (Bryce's design sweep, 18 Sep). Membership is checked every 3 minutes,
    # which leaves a check 2 minutes to answer before the grant it renews runs out, and ends a revoked membership's
    # access within 3 minutes. Until 23 Sep it was every 20 seconds: three HTTPS requests each, idle or not, 540 an hour
    # per open Mac (efficiency review). A check that lost its connection asks again after RETRY_SECONDS. A grant that
    # ends sooner, with the session, is checked REFRESH_LEAD before, when _work refreshes the session. A membership
    # waiting for approval is checked every minute, as first run says. Checks also run at once on sign-in (the sign-in
    # is one), after the Mac wakes, and when an action finds the grant has run out (require_access).
    ACCESS_SECONDS = 300
    CHECK_SECONDS = 180
    PENDING_CHECK_SECONDS = 60
    RETRY_SECONDS = 20
    REFRESH_LEAD = 60
    # Two ticks this far apart by the wall clock mean the process was suspended: the Mac slept. The host ticks at least
    # every host.IDLE_WAIT while it's awake.
    WAKE_GAP = 60
    # A cached photo is confirmed in the background: at most three tries a session, 30 seconds apart. "Check photo"
    # in Settings still works after that.
    PHOTO_RETRY_SECONDS = 30
    PHOTO_RETRIES = 3
    # After checkout or billing opens in the browser, check every minute for this long, so a payment shows up quickly.
    BILLING_WATCH_SECONDS = 1800

    def __init__(self, store, client, vault, *, clock=time.time, data_root=None, open_link=open_in_browser):
        self.store, self.client, self.vault, self.clock = store, client, vault, clock
        self.open_link = open_link
        self.photo_root = data_root
        self.photo_tries = 0
        self.photo_tried_at = None
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-account')
        self.future = None
        self.operation = None
        self.identity = self.session = self.membership = None
        self.plan = None
        self.billing = self._billing_empty()
        self.billing_watch_until = 0
        self.billing_opened_on = None
        self.photo = {'state': 'not_loaded', 'data_url': None}
        self.email = None
        self.access_until = 0
        self.next_check = 0
        self.checked_at = float('-inf')  # when the last background check started (tick), for require_access's pull
        self.last_tick = None
        self.closed = False
        self.detail = 'Sign in with your email to check Dervo beta access.'
        self.testers = self._testers_empty()
        self.testers_future = None
        self.waitlist = self._waitlist_empty()
        self.waitlist_future = None
        self.waitlist_session = None
        with store.db:
            store.db.execute('create table if not exists dervo_account_runtime (id integer primary key check(id=1), document text not null)')
        row = store.db.execute('select document from dervo_account_runtime where id=1').fetchone()
        self.saved = {'schema': 1, 'owner': None, 'intent': 'signed_out'}
        if row:
            try:
                saved = json.loads(row[0])
                if (type(saved) is not dict or set(saved) != set(self.saved) or saved['schema'] != 1
                        or saved['intent'] not in ('signed_out', 'active', 'pending', 'cancelled', 'checking')):
                    raise ValueError()
                if saved['owner'] is not None:
                    owner = saved['owner']
                    if (type(owner) is not dict or set(owner) != {'issuer', 'subject'}
                            or owner['issuer'] != client.project.url + '/auth/v1'
                            or str(uuid.UUID(owner['subject'])) != owner['subject']):
                        raise ValueError()
                self.saved = saved
            except Exception:
                self.worker.shutdown(wait=False)
                raise Rejected('Saved Dervo account metadata needs inspection') from None
        if self.saved['owner'] is not None and self.saved['intent'] in ('active', 'checking'):
            # Shown as soon as the restore signs in, before any fetch, so an update doesn't swap the photo for initials.
            self.photo = self._cached_photo(self.saved['owner']['subject']) or self.photo
        self.state = 'signed_out'
        if self.saved['intent'] in ('active', 'checking'):
            self._launch('restore')
        elif self.saved['intent'] == 'pending' and self.saved['owner'] is None:
            # Quit while a code was on its way or being typed, before any account was ever signed in here: nothing to
            # resume or check, so the email step opens again (code review, 8 Oct), after the same cleanup a sign-out gets.
            self._launch('cleanup', intent='signed_out')
        elif self.saved['intent'] == 'pending':
            self.state = 'interrupted'
            self.detail = 'Sign-in was interrupted. Check the saved session or sign out before trying again.'
        elif self.saved['intent'] in ('cancelled', 'signed_out'):
            # A previous shutdown may have interrupted local cleanup.
            self._launch('cleanup', intent=self.saved['intent'])

    def _cached_photo(self, subject, error=None):
        if self.photo_root is None or subject is None:
            return None
        import account_photo
        raw = account_photo.read_cached(self.photo_root, subject)
        if raw is None:
            return None
        photo = account_photo.status(raw, 'cached')
        return {**photo, 'error': error} if error else photo

    def _clear_cached_photo(self):
        if self.photo_root is not None and self.saved['owner'] is not None:
            import account_photo
            account_photo.clear_cached(self.photo_root, self.saved['owner']['subject'])

    def _holds_projects(self):
        """Whether this workspace has any project yet. If that can't be read, it counts as yes, so the lock holds."""
        try:
            return self.store.db.execute('select 1 from projects limit 1').fetchone() is not None
        except Exception:
            return True

    def _save(self, intent):
        self.saved['intent'] = intent
        with self.store.db:
            self.store.db.execute('insert into dervo_account_runtime values (1,?) on conflict(id) do update set document=excluded.document',
                                  (json.dumps(self.saved),))

    def _snapshot(self, session):
        # These three calls only read identity, membership and profile. Retry the
        # whole snapshot once after a temporary connection failure, so its access
        # lease comes from the new check. Never replay OTP, refresh or writes.
        try:
            return self._read_snapshot(session)
        except AccountError as error:
            if not error.transient:
                raise
            retry_pause(1)
            return self._read_snapshot(session)

    def _read_snapshot(self, session):
        identity = self.client.user(session.access_token, expected_user=session.user_id)
        checked_at = self.clock()
        membership = self.client.access(session)
        profile = self.client.profile(session)
        # The plan only describes access. A server without billing yet, or a failed read, leaves it unknown and never
        # stops a sign-in or a membership check.
        try:
            plan = self.client.plan(session)
        except Exception as error:  # noqa: BLE001 - only an expired session is an answer about the account
            if isinstance(error, AccountError) and error.code == 'expired':
                raise
            plan = None
        return {'session': session, 'identity': {**identity, 'display_name': profile['display_name']},
                'membership': membership, 'plan': plan, 'checked_at': checked_at}

    @staticmethod
    def _billing_empty():
        return {'state': 'idle', 'action': None, 'detail': None}

    def _work(self, kind, payload):
        # Only this serial worker touches Keychain or performs network requests.
        try:
            if kind == 'send':
                self.client.send_code(payload['email'])
                return {'state': 'awaiting_user'}
            if kind in ('cleanup', 'cancel', 'sign_out'):
                old = None
                if kind == 'sign_out':
                    try:
                        old = self.vault.load()
                    except KeychainError:
                        pass
                self.vault.delete()
                if old is not None:
                    try:
                        self.client.sign_out(old)
                    except AccountError as error:
                        if error.code != 'expired':
                            return {'state': 'signed_out', 'detail': 'Signed out on this Mac. Dervo couldn’t confirm server sign-out.'}
                return {'state': 'cancelled' if kind == 'cancel' else 'signed_out'}
            if kind == 'verify':
                session = self.client.verify_code(payload['email'], payload['code'])
                # Save before any more network calls, including the membership read.
                self.vault.save(session)
                return self._snapshot(session)
            session = self.vault.load()
            if session is None:
                return {'state': 'signed_out'}
            refreshed = False
            # A background check refreshes a session that would end before the next one, so the grant it gives is a
            # full one and no extra check is needed at the session's end. Everything else refreshes in its last minute.
            lead = self.CHECK_SECONDS + self.REFRESH_LEAD if kind == 'check' else self.REFRESH_LEAD
            if session.expires_at <= self.clock() + lead:
                session = self.client.refresh(session)
                self.vault.save(session)
                refreshed = True
            if kind in ('trial', 'billing'):
                # A refused trial or a billing service that's down is an answer about billing, not about the session.
                outcome = {}
                try:
                    device = None
                    if kind == 'trial' or payload.get('action') == 'checkout':
                        # The salted Mac hash: one trial per Mac, for the app trial and for a card trial at checkout.
                        import trial_device
                        device = trial_device.device_key(self.photo_root)
                        if device is None:
                            raise AccountError('rejected')
                    if kind == 'trial':
                        outcome['trial'] = self.client.start_trial(session, device)['state']
                    else:
                        outcome['billing_url'] = self.client.billing_link(session, payload['action'], payload['interval'], device)
                except AccountError as error:
                    if error.code == 'expired':
                        raise
                    outcome['billing_error'] = error.code
                return {**self._snapshot(session), **outcome}
            if kind == 'profile' or kind.startswith('photo_'):
                if session.user_id != payload['subject']:
                    raise AccountError('invalid_response')
                self.client.user(session.access_token, expected_user=payload['subject'])
            if kind == 'profile':
                self.client.save_name(session, payload['displayName'])
            if kind.startswith('photo_'):
                import account_photo
                try:
                    if kind == 'photo_save':
                        account_photo.request(self.client, session, 'POST', payload['png'])
                    elif kind == 'photo_remove':
                        account_photo.request(self.client, session, 'DELETE')
                    image = account_photo.request(self.client, session, 'GET')
                    if kind == 'photo_save' and image != payload['png']:
                        raise AccountError('photo_service')
                    if kind == 'photo_remove' and image is not None:
                        raise AccountError('photo_service')
                    photo = account_photo.status(image)
                    if self.photo_root is not None:
                        # Only a confirmed answer changes the copy on this Mac: the checked photo, or a real "no photo".
                        if image is None:
                            account_photo.clear_cached(self.photo_root, payload['subject'])
                        else:
                            account_photo.write_cached(self.photo_root, payload['subject'], image)
                except AccountError:
                    # A failed check never replaces a known photo with nothing.
                    photo = self._photo_failure(payload['subject'])
                return {**self._snapshot(session), 'photo': photo}
            try:
                return self._snapshot(session)
            except AccountError as error:
                if error.code != 'expired' or refreshed:
                    raise
                session = self.client.refresh(session)
                self.vault.save(session)
                return self._snapshot(session)
        except AccountError as error:
            return {'error': error.code, 'detail': str(error), 'transient': bool(getattr(error, 'transient', False))}
        except KeychainError:
            return {'error': 'keychain', 'detail': str(KeychainError())}
        except Exception:
            return {'error': 'unavailable', 'detail': 'Dervo couldn’t confirm the account operation. Check the session before trying again.'}

    def _photo_failure(self, subject):
        return self._cached_photo(subject, 'photo_service') or {'state': 'error', 'data_url': None}

    def _launch(self, kind, payload=None, intent=None):
        if self.closed:
            raise Rejected('Dervo is closing. Reopen it before changing your account.')
        self.operation = {'id': uuid.uuid4().hex, 'kind': kind}
        self._save(intent or ('signed_out' if kind == 'sign_out' else 'cancelled' if kind == 'cancel'
                             else 'checking' if kind in ('restore', 'check', 'profile', 'trial', 'billing') or kind.startswith('photo_') else 'pending'))
        if kind not in ('check', 'profile', 'trial', 'billing') and not kind.startswith('photo_'):
            self.state = 'verifying'
            self.access_until = 0
        self.detail = 'Saving your name.' if kind == 'profile' else 'Starting your free trial.' if kind == 'trial' else 'Opening billing.' if kind == 'billing' else 'Checking your profile photo.' if kind.startswith('photo_') else 'Checking your Dervo account.'
        self.future = self.worker.submit(self._work, kind, payload or {})

    def _finish(self, result, kind):
        self.future = None
        if 'error' in result:
            if kind in ('check', 'photo_load') and result.get('transient') and self.state == 'signed_in' and self.membership:
                # A dropped connection during a background check isn't an answer. Keep the grant it already has, which
                # runs out on its own, and ask again soon. Only a real answer (revoked, expired, signed out) ends access.
                # A photo check is background work too, now the host retries it, so it gets the same rule.
                self.operation = None
                self._save('active')
                if kind == 'check':
                    self.next_check = self.clock() + self.RETRY_SECONDS
                else:
                    if self.photo['state'] in ('not_loaded', 'cached', 'error'):
                        self.photo = self._photo_failure(self.identity['subject'])
                return
            self.access_until = 0
            if kind == 'verify' and result['error'] in ('rejected', 'rate_limited'):
                self.state = 'awaiting_user'
                self.detail = 'That code couldn’t be confirmed. Check the code and try again, or cancel to start over.'
                return
            if kind == 'send' and self.session is None:
                # Sending a code made nothing yet: no session to restore and nothing to sign out of. 'failed' offers the
                # email step again with the reason, instead of 'unknown', whose only ways out were Check session and Sign
                # out (onboarding audit, 8 Oct: a quick resend inside the auth cooldown landed there).
                self.state = 'failed'
                self.detail = SEND_FAILED.get(result['error'], SEND_FAILED_OTHER)
                self.email = None
                self.operation = None
                # Nothing to resume: a relaunch opens on the email step, not "Sign-in was interrupted" (code review, 8 Oct).
                self._save('signed_out')
                return
            self.state = 'expired' if result['error'] == 'expired' else 'unknown'
            self.detail = result['detail']
            self.identity = self.session = self.membership = self.plan = None
            self.billing = self._billing_empty()
            self.photo = {'state': 'not_loaded', 'data_url': None}
            self.operation = None
            return
        if result.get('state') == 'awaiting_user':
            self.state = 'awaiting_user'
            self.detail = 'Enter the code from your email. This doesn’t grant beta access until your account is approved.'
            return
        self.operation = None
        if 'session' not in result:
            if kind in ('sign_out', 'cleanup'):
                # Signing out forgets the photo on this Mac too. A cancelled sign-in or a missing session doesn't.
                self._clear_cached_photo()
            self.state = result['state']
            self.testers = self._testers_empty()  # nothing an admin loaded outlives their session
            self.waitlist = self._waitlist_empty()
            self.identity = self.session = self.membership = self.plan = None
            self.billing = self._billing_empty()
            self.billing_watch_until = 0
            self.billing_opened_on = None
            self.photo = {'state': 'not_loaded', 'data_url': None}
            self.email = None
            self.access_until = 0
            self._save('cancelled' if self.state == 'cancelled' else 'signed_out')
            self.detail = result.get('detail', 'You’re signed out of Dervo. Your projects and provider connections stay on this Mac.')
            return
        identity = result['identity']
        owner = {'issuer': identity['issuer'], 'subject': identity['subject']}
        if self.saved['owner'] is not None and owner != self.saved['owner'] and not self._holds_projects():
            # 30 Sep, Bryce: a tester who signed in with the wrong email is waiting for approval, and used to be refused
            # the right one for good. With no project yet there's nothing here to belong to the first account, so the
            # account signing in now takes the workspace. Once a project exists, the lock below holds.
            self._clear_cached_photo()  # the first account's photo copy goes with it
            self.saved['owner'] = None
        if self.saved['owner'] is not None and owner != self.saved['owner']:
            self.identity = self.session = self.membership = self.plan = None
            self.photo = {'state': 'not_loaded', 'data_url': None}
            self.state = 'account_mismatch'
            self.access_until = 0
            self.detail = 'This workspace belongs to another Dervo account. Sign out and use the original account. No projects or memory were transferred.'
            return
        self.saved['owner'] = owner
        self._save('active')
        self.identity, self.session, self.membership = identity, result['session'], result['membership']
        self.plan = result.get('plan')
        if kind == 'billing':
            opened = False
            if 'billing_url' in result:
                from supabase_client import billing_url
                try:
                    if billing_url(result['billing_url']):
                        self.open_link(result['billing_url'])
                        opened = True
                except Exception:  # noqa: BLE001 - reported below; the link is never shown or logged
                    pass
            if opened:
                # The link itself stays here: it opens checkout for this account, so it isn't for the window or logs.
                self.billing = {'state': 'opened', 'action': self.billing['action'], 'detail': None}
                self.billing_watch_until = self.clock() + self.BILLING_WATCH_SECONDS
                self.billing_opened_on = self._plan_mark()
            else:
                self.billing = {'state': 'error', 'action': self.billing['action'],
                                'detail': 'Dervo couldn’t open billing. Check your connection and try again.'}
        if kind == 'check' and self.billing['state'] in ('opened', 'error') and (
                self.clock() >= self.billing_watch_until or self._plan_mark() != self.billing_opened_on):
            # Finished in the browser (the plan or subscription changed) or long enough ago: the note has done its job.
            self.billing = self._billing_empty()
        if kind == 'trial':
            refused = TRIAL_OUTCOMES.get(result.get('trial')) if 'trial' in result else (
                'Dervo couldn’t start your free trial. Check your connection and try again.')
            self.billing = {'state': 'error' if refused else 'idle', 'action': 'trial', 'detail': refused}
        if 'photo' in result:
            self.photo = result['photo']
        self.email = None
        self.state = 'signed_in'
        self.access_until = min(self.session.expires_at, result['checked_at'] + self.ACCESS_SECONDS)
        if self.membership['expires_at']:
            expiry = datetime.fromisoformat(self.membership['expires_at'].replace('Z', '+00:00')).timestamp()
            self.access_until = min(self.access_until, expiry)
        self.next_check = self._next_check(result['checked_at'])
        plan = (self.plan or {}).get('plan')
        past_due = ((self.plan or {}).get('subscription') or {}).get('status') == 'past_due'
        self.detail = ('Your last payment failed. Update your payment method in Settings › Account.' if past_due else
                       'Your free trial is on.' if self.membership['allowed'] and plan == 'trial' else
                       'Your Dervo plan is active.' if self.membership['allowed'] and plan == 'paid' else
                       'You’re ready to use the Dervo beta.' if self.membership['allowed'] else
                       f'This account no longer has access to Dervo. {SAVED}' if plan == 'removed' else
                       self._no_plan_line() if plan == 'none' else
                       'Your beta access has been revoked. You can still read your saved work.' if self.membership['status'] == 'revoked' else
                       'Your beta access has expired. You can still read your saved work.' if self.membership['status'] == 'approved' else
                       'You’re signed in. Your beta access is waiting for approval.')

    def _next_check(self, checked_at):
        """When to check membership again after a check answered at ``checked_at``."""
        waiting = self.membership['status'] == 'pending' or checked_at < self.billing_watch_until
        due = checked_at + (self.PENDING_CHECK_SECONDS if waiting else self.CHECK_SECONDS)
        if self.access_until > checked_at:
            # A live grant is renewed before it runs out, never after. A session that expires within REFRESH_LEAD is
            # refreshed by the check (_work), so this grant is followed by a full one.
            due = min(due, max(checked_at + self.RETRY_SECONDS, self.access_until - self.REFRESH_LEAD))
        return due

    def tick_due(self):
        """Seconds until tick() has work to do, for the host's request loop to sleep: 0 while an account job runs (its
        result is collected on a tick), else until the next membership check or photo retry, or None when nothing is
        scheduled (signed out, closed)."""
        if self.closed:
            return None
        if self.future is not None or self.testers_future is not None or self.waitlist_future is not None:
            return 0
        if self.state != 'signed_in':
            return None
        now = self.clock()
        due = self.next_check - now
        if (self.identity and self.photo['state'] == 'cached' and not self.photo.get('checked')
                and self.photo_tries < self.PHOTO_RETRIES):
            due = min(due, 0 if self.photo_tried_at is None else self.photo_tried_at + self.PHOTO_RETRY_SECONDS - now)
        return max(0, due)

    # ------------------------------------------------------------------ Testers (admins only)
    @staticmethod
    def _testers_empty():
        return {'state': 'idle', 'people': [], 'audit': [], 'more': False, 'cut': False, 'detail': None, 'busy': None}

    def _require_admin(self):
        status = self.status()
        membership = status.get('membership') or {}
        if status['state'] != 'signed_in' or membership.get('is_admin') is not True:
            raise Rejected('Only Dervo admins can see testers.')
        if self.session is None or self.session.expires_at <= self.clock() + 60:
            raise Rejected('Dervo is checking your account. Try again in a moment.')
        return self.session

    def _tester_work(self, kind, payload, session):
        # On the account worker, after any account job ahead of it: the only place Testers touches the network.
        try:
            if kind == 'set':
                self.client.set_membership(session, payload['userId'], payload['status'], payload['expiresAt'])
            if kind == 'older':
                audit = self.client.tester_audit(session, before=payload['before'], limit=AUDIT_PAGE + 1)
                return {'audit': audit[:AUDIT_PAGE], 'more': len(audit) > AUDIT_PAGE, 'append': True}
            people = self.client.testers(session)
            audit = self.client.tester_audit(session, limit=AUDIT_PAGE + 1)
            # The server stops at TESTERS_CAP people, newest first. A full list means older sign-ups may be missing, and
            # the page says so rather than looking complete (security review, 19 Sep, finding 6). Paging needs a migration.
            from supabase_client import TESTERS_CAP
            return {'people': people, 'audit': audit[:AUDIT_PAGE], 'more': len(audit) > AUDIT_PAGE, 'cut': len(people) >= TESTERS_CAP}
        except AccountError as error:
            return {'error': error.code}
        except Exception:
            return {'error': 'unavailable'}

    def _launch_testers(self, kind, payload=None):
        if self.closed:
            raise Rejected('Dervo is closing. Reopen it before changing testers.')
        if self.testers_future is not None:
            raise Rejected('Wait for the last change to finish.')
        session = self._require_admin()
        self.testers = {**self.testers, 'state': 'saving' if kind == 'set' else 'loading', 'detail': None,
                        'busy': (payload or {}).get('userId')}
        self.testers_future = self.worker.submit(self._tester_work, kind, payload or {}, session)

    def _finish_testers(self, result):
        self.testers_future = None
        if 'error' in result:
            saving = self.testers['state'] == 'saving'
            self.testers = {**self.testers, 'state': 'error', 'busy': None,
                            'detail': ('Dervo couldn’t confirm that change. Check the list before trying again.' if saving
                                       else 'Dervo couldn’t load testers. Check your connection, then try again.')}
            return
        if result.get('append'):
            self.testers = {**self.testers, 'state': 'ready', 'audit': self.testers['audit'] + result['audit'],
                            'more': result['more'], 'busy': None, 'detail': None}
            return
        self.testers = {'state': 'ready', 'people': result['people'], 'audit': result['audit'], 'more': result['more'],
                        'cut': result['cut'], 'detail': None, 'busy': None}

    def testers_view(self):
        you = self.identity['subject'] if self.identity else None
        view = dict(self.testers)
        view['people'] = [{**row, 'is_you': row['user_id'] == you} for row in self.testers['people']]
        view['audit'] = [{**row, 'by_you': row['actor_id'] == you} for row in self.testers['audit']]
        return view

    def _testers_dispatch(self, operation, args):
        if operation == 'dervo_testers':
            if type(args['reload']) is not bool:
                raise Rejected('Unsupported Dervo account fields')
            self._require_admin()
            if self.testers_future is None and (args['reload'] or self.testers['state'] == 'idle'):
                self._launch_testers('load')
            return self.testers_view()
        if operation == 'dervo_testers_older':
            if not self.testers['audit'] or not self.testers['more']:
                raise Rejected('There are no older changes to show.')
            self._launch_testers('older', {'before': self.testers['audit'][-1]['id']})
            return self.testers_view()
        user_id, status, expiry = args['userId'], args['status'], args['expiresAt']
        known = {row['user_id'] for row in self.testers['people']}
        if type(user_id) is not str or user_id not in known:
            raise Rejected('That person isn’t on the list any more. Reload testers and try again.')
        if self.identity and user_id == self.identity['subject']:
            raise Rejected('You can’t change your own access here.')
        if status not in ('approved', 'revoked'):
            raise Rejected('Approve or revoke, with an optional end date.')
        if expiry is not None:
            if (type(expiry) not in (int, float) or isinstance(expiry, bool) or expiry != expiry or status != 'approved'
                    or not self.clock() < expiry <= self.clock() + LONGEST_END):
                raise Rejected('Choose an end date after today and within a year.')
        self._launch_testers('set', {'userId': user_id, 'status': status, 'expiresAt': expiry})
        return self.testers_view()

    # ------------------------------------------------------------------ Waitlist and invitations (admins only)
    @staticmethod
    def _waitlist_empty():
        return {'state': 'idle', 'people': [], 'more': False, 'detail': None, 'results': [], 'results_id': 0, 'email_preview': None}

    def _waitlist_work(self, payload, session):
        # On the account worker. An invite is sent first, then the list is read again so it shows what changed.
        try:
            results = None
            if payload:
                results = self.client.waitlist_action(session, payload['action'], payload['emails'])
            try:
                listing = self.client.waitlist(session)
            except AccountError:
                if results is None:
                    raise
                listing = None  # the batch went through, so its results still have to be shown
            return {'results': results, 'listing': listing}
        except AccountError as error:
            return {'error': error.code}
        except Exception:
            return {'error': 'unavailable'}

    def _launch_waitlist(self, payload=None):
        if self.closed:
            raise Rejected('Dervo is closing. Reopen it before inviting anyone.')
        if self.waitlist_future is not None:
            raise Rejected('Wait for the last change to finish.')
        session = self._require_admin()
        self.waitlist_session = session.access_token
        self.waitlist = {**self.waitlist, 'state': ('saving' if payload['action'] == 'add' else 'sending') if payload else 'loading', 'detail': None}
        self.waitlist_future = self.worker.submit(self._waitlist_work, payload, session)

    def _finish_waitlist(self, result):
        self.waitlist_future = None
        if (self.state != 'signed_in' or not self.session or self.session.access_token != self.waitlist_session
                or not (self.membership or {}).get('is_admin')):
            self.waitlist = self._waitlist_empty()
            return
        if 'error' in result:
            sending = self.waitlist['state'] == 'sending'
            # A batch with no answer may have been sent in part. The list says who was, and a repeat sends nothing twice.
            self.waitlist = {**self.waitlist, 'state': 'error',
                             'detail': ('Dervo couldn’t confirm that address was added. Reload the list before trying again. Adding an address doesn’t send email or approve access.' if self.waitlist['state'] == 'saving' else 'Dervo couldn’t confirm those invitations. Reload the waitlist to see who was invited. Inviting the same people again won’t email anyone twice.' if sending
                                        else 'Dervo couldn’t load the waitlist. Check your connection, then try again.')}
            return
        view = {**self.waitlist, 'state': 'ready', 'detail': None}
        if result['listing'] is not None:
            view.update(people=result['listing']['people'], more=result['listing']['more'], email_preview=result['listing'].get('email_preview'))
        else:
            view['detail'] = 'Dervo couldn’t reload the waitlist. Reload it to see the latest statuses.'
        if result['results'] is not None:
            view.update(results=result['results'], results_id=self.waitlist['results_id'] + 1)
        self.waitlist = view

    def _waitlist_dispatch(self, operation, args):
        if operation == 'dervo_waitlist':
            if type(args['reload']) is not bool:
                raise Rejected('Unsupported Dervo account fields')
            self._require_admin()
            if self.waitlist_future is None and (args['reload'] or self.waitlist['state'] == 'idle'):
                self._launch_waitlist()
            return dict(self.waitlist)
        from supabase_client import WAITLIST_ACTIONS, WAITLIST_BATCH, normalize_waitlist_email
        self._require_admin()
        action, emails = args['action'], args['emails']
        if action not in WAITLIST_ACTIONS:
            raise Rejected('Add an address, invite, resend or withdraw.')
        if type(emails) is not list or not 1 <= len(emails) <= WAITLIST_BATCH or any(type(email) is not str for email in emails):
            raise Rejected(f'Choose between 1 and {WAITLIST_BATCH} people.')
        try:
            emails = [normalize_waitlist_email(email) for email in emails]
        except AccountError:
            raise Rejected('Enter a valid email address.') from None
        known = {row['email'] for row in self.waitlist['people']}
        if len(set(emails)) != len(emails) or (action != 'add' and not set(emails) <= known):
            raise Rejected('Someone you chose isn’t on the list any more. Reload the waitlist and try again.')
        self._launch_waitlist({'action': action, 'emails': list(emails)})
        return dict(self.waitlist)

    def tick(self):
        if self.closed:
            return
        now = self.clock()
        if self.last_tick is not None and now - self.last_tick > self.WAKE_GAP:
            self.next_check = min(self.next_check, now)  # the Mac woke: the grant may have run out while it slept
        self.last_tick = now
        if self.waitlist_future is not None and self.waitlist_future.done():
            try:
                result = self.waitlist_future.result()
            except Exception:
                result = {'error': 'unavailable'}
            self._finish_waitlist(result)
        if self.testers_future is not None and self.testers_future.done():
            try:
                result = self.testers_future.result()
            except Exception:
                result = {'error': 'unavailable'}
            self._finish_testers(result)
        if self.future is not None and self.future.done():
            kind = self.operation['kind']
            try:
                result = self.future.result()
            except Exception:
                result = {'error': 'unavailable', 'detail': 'Dervo couldn’t confirm the account operation.'}
            self._finish(result, kind)
        if self.future is None and self.state == 'signed_in' and self.clock() >= self.next_check:
            self.checked_at = self.clock()
            self._launch('check')
        if (self.future is None and self.state == 'signed_in' and self.identity and self.photo['state'] == 'cached'
                and not self.photo.get('checked') and self.photo_tries < self.PHOTO_RETRIES
                and (self.photo_tried_at is None or self.clock() >= self.photo_tried_at + self.PHOTO_RETRY_SECONDS)):
            self.photo_tries += 1
            self.photo_tried_at = self.clock()
            self._launch('photo_load', {'subject': self.identity['subject']})

    def status(self):
        now = self.clock()
        busy = self.future is not None
        membership = dict(self.membership) if self.membership else None
        if membership is not None:
            membership['allowed'] = bool(membership['allowed'] and self.state == 'signed_in' and now < self.access_until)
        detail = self.detail
        if self.state == 'signed_in' and now >= self.access_until and self.membership and self.membership['allowed']:
            detail = 'Dervo is checking your beta access. You can still read your saved work.'
        return dict(state=self.state, detail=detail, method='email_code',
                    identity=dict(self.identity) if self.identity else None,
                    photo=dict(self.photo) if self.state == 'signed_in' else None,
                    operation_id=self.operation['id'] if self.operation else None,
                    expires_at=self.session.expires_at if self.session else None,
                    can_start=not busy and self.state in ('signed_out', 'cancelled', 'failed'),
                    can_verify=not busy and self.state == 'awaiting_user' and self.email is not None,
                    can_cancel=self.operation is not None and self.operation['kind'] in ('send', 'verify'),
                    can_poll=busy, can_restore=not busy and self.state in ('unknown', 'expired', 'interrupted', 'restore_required'),
                    can_sign_out=not busy and self.state in ('signed_in', 'unknown', 'expired', 'interrupted', 'account_mismatch'),
                    can_save_profile=not busy and self.state == 'signed_in', membership=membership,
                    access_valid_until=self.access_until or None, ownership='local_unclaimed', sync_available=False,
                    plan=self._plan_view() if self.state == 'signed_in' else None,
                    billing=dict(self.billing) if self.state == 'signed_in' else None,
                    can_start_trial=not busy and self.state == 'signed_in' and bool(self.plan) and self.plan['trial']['available'] and self.plan['trial']['mode'] == 'app',
                    can_buy=not busy and self.state == 'signed_in' and bool(self.plan) and self.plan['can_buy'],
                    can_manage_billing=not busy and self.state == 'signed_in' and bool(self.plan) and self.plan['can_manage'])

    def _no_plan_line(self):
        """Why there's no plan, in the words the window and a refused task both show."""
        plan = self.plan or {}
        if (plan.get('subscription') or {}).get('status') == 'canceled':
            return f'Your Dervo Pro plan has ended. Choose a plan in Settings › Account to keep starting agent work. {SAVED}'
        if (plan.get('trial') or {}).get('available'):
            return f'Start your free trial in Settings › Account to start agent work. {SAVED}'
        if (plan.get('trial') or {}).get('ends_at'):
            return f'Your free trial has ended. Choose a plan in Settings › Account to keep starting agent work. {SAVED}'
        return f'Choose a plan in Settings › Account to start agent work. {SAVED}'

    def _plan_view(self):
        return json.loads(json.dumps(self.plan)) if self.plan else None

    def require_access(self):
        status = self.status()
        if status['state'] != 'signed_in' or not status['membership'] or not status['membership']['allowed']:
            if status['state'] == 'signed_in' and self.future is None:
                # An action needs fresh access: check on the next tick rather than at the scheduled time (a grant that ran
                # out while a check was retrying, or a pending membership approved since), at most once per RETRY_SECONDS.
                # Only the time moves here, since a harness helper may ask from its own thread; tick() launches the check.
                self.next_check = min(self.next_check, max(self.clock(), self.checked_at + self.RETRY_SECONDS))
            plan = (self.plan or {}).get('plan') if status['state'] == 'signed_in' else None
            if plan == 'none':
                raise Rejected(self._no_plan_line())
            if plan == 'removed':
                raise Rejected(TRIAL_OUTCOMES['removed'])
            raise Rejected('Dervo beta access needs confirmation. Open Account in Settings. Your saved work is still available.')

    def plan_lapsed(self):
        """Still signed in, to an account whose trial or plan has ended: the one refusal a running turn outlives."""
        return self.status()['state'] == 'signed_in' and (self.plan or {}).get('plan') == 'none'

    def require_identity(self, issuer, subject):
        if self.state != 'signed_in' or not self.session or self.session.expires_at <= self.clock() or not self.identity or (
                self.identity['issuer'], self.identity['subject']) != (issuer, subject):
            raise Rejected('This work needs its verified Dervo account')
        return hashlib.sha256(json.dumps([issuer, subject], separators=(',', ':')).encode()).hexdigest()

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported Dervo account fields')
        if operation in ('dervo_account_poll', 'dervo_account_cancel', 'dervo_account_verify_code'):
            if not self.operation or args['operationId'] != self.operation['id']:
                raise Rejected('This sign-in step is no longer current.')
        # A valid cancel wins even if verification finished just before the click.
        # Don't consume that result and activate it before recording cancellation.
        if operation != 'dervo_account_cancel':
            self.tick()
        if operation in TESTER_FIELDS:
            return self._testers_dispatch(operation, args)
        if operation in WAITLIST_FIELDS:
            return self._waitlist_dispatch(operation, args)
        status = self.status()
        if operation in PLAN_FIELDS:
            return self._plan_dispatch(operation, args, status)
        if operation == 'dervo_account_status':
            return status
        if operation == 'dervo_account_poll':
            return status
        if operation == 'dervo_account_send_code':
            if not status['can_start']:
                raise Rejected('Finish or cancel the current sign-in first.')
            self.email = SupabaseClient._email(args['email'])
            self._launch('send', {'email': self.email})
        elif operation == 'dervo_account_verify_code':
            if not status['can_verify']:
                raise Rejected('Wait for the email step before entering a code.')
            import re
            if type(args['code']) is not str or not re.fullmatch('[0-9]{6,10}', args['code']):
                raise Rejected('Enter the code from your email.')
            self._launch('verify', {'email': self.email, 'code': args['code']})
        elif operation == 'dervo_account_cancel':
            if not status['can_cancel']:
                raise Rejected('This account operation can’t be cancelled.')
            self.email = None
            self._launch('cancel')
        elif operation == 'dervo_account_restore':
            if not status['can_restore']:
                raise Rejected('Your account doesn’t need restoration right now.')
            self._launch('restore')
        elif operation == 'dervo_account_sign_out':
            if not status['can_sign_out']:
                raise Rejected('Wait for the account check to finish before signing out.')
            self._launch('sign_out')
        elif operation == 'dervo_account_save_profile':
            name = args['displayName']
            if not status['can_save_profile'] or type(name) is not str or len(name) > 80 or any(ord(c) < 32 for c in name):
                raise Rejected('Enter a display name of up to 80 characters after signing in.')
            self._launch('profile', {'displayName': name, 'subject': self.identity['subject']})
        elif operation in ('dervo_account_photo', 'dervo_account_save_photo', 'dervo_account_remove_photo'):
            if not status['can_save_profile'] or args['subject'] != self.identity['subject']:
                raise Rejected('Check your Dervo account before changing its photo.')
            import account_photo
            kind = {'dervo_account_photo': 'photo_load', 'dervo_account_save_photo': 'photo_save',
                    'dervo_account_remove_photo': 'photo_remove'}[operation]
            payload = {'subject': args['subject']}
            if kind == 'photo_save':
                try:
                    payload['png'] = account_photo.decode(args['png'])
                except AccountError as error:
                    raise Rejected(str(error)) from None
            self._launch(kind, payload)
        return self.status()

    def _plan_mark(self):
        """What a finished checkout or billing change would change: the plan and its subscription."""
        plan = self.plan or {}
        sub = plan.get('subscription') or {}
        return (plan.get('plan'), sub.get('status'), sub.get('interval'), sub.get('cancel_at'))

    def _plan_dispatch(self, operation, args, status):
        if operation == 'dervo_plan_refresh':
            if status['state'] != 'signed_in':
                raise Rejected('Sign in to check your plan.')
            # At most once per RETRY_SECONDS, like require_access; tick() starts the check. It doesn't extend the
            # minute-by-minute watch, so repeated checks can't keep it going.
            if self.future is None:
                self.next_check = min(self.next_check, max(self.clock(), self.checked_at + self.RETRY_SECONDS))
                self.tick()
            return self.status()
        if self.future is not None:
            raise Rejected('Dervo is checking your account. Try again in a moment.')
        if operation == 'dervo_plan_start_trial':
            if not status['can_start_trial']:
                raise Rejected('A free trial isn’t available for this account right now.')
            self.billing = {'state': 'starting', 'action': 'trial', 'detail': None}
            self._launch('trial')
            return self.status()
        action, interval = args['action'], args['interval']
        if not ((action == 'checkout' and interval in ('month', 'year') and status['can_buy'])
                or (action == 'manage' and interval is None and status['can_manage_billing'])):
            raise Rejected('Billing isn’t available for this account right now.')
        self.billing = {'state': 'opening', 'action': action, 'detail': None}
        self._launch('billing', {'action': action, 'interval': interval})
        return self.status()

    def close(self):
        if self.closed:
            return
        if self.operation and self.operation['kind'] in ('send', 'verify'):
            self.email = None
            self._launch('cancel')
        self.closed = True
        self.worker.shutdown(wait=False, cancel_futures=False)
