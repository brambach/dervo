"""Host-only Supabase transport. Public identity and session material stay separate.

No automatic retry: email sends and refresh-token rotation need explicit lifecycle
handling. The production account adapter owns that lifecycle and Keychain storage.
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
import os
import ssl
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID


class AccountError(Exception):
    MESSAGES = {
        'photo': 'Choose a PNG, JPEG or WebP photo. Dervo will resize it before saving.',
        'photo_service': 'Dervo couldn’t update your photo. Check its status before trying again.',
        'configuration': 'Dervo account configuration needs attention.',
        'unavailable': 'Dervo couldn’t reach the account service. Try again when you’re connected.',
        'expired': 'Your Dervo session expired. Sign in again.',
        'tls': 'Dervo couldn’t verify the account service’s secure connection. Check for a Dervo update before trying again.',
        'email_send': 'Dervo couldn’t send your sign-in code. The email service returned an error. Please try again later.',
        'service': 'The Dervo account service returned an error. Please try again later.',
        'rate_limited': 'Please wait before trying again.',
        'rejected': 'Dervo couldn’t confirm that request. Check your details and try again.',
        'invalid_response': 'Dervo couldn’t verify the account response.',
    }

    def __init__(self, code, *, transient=False):
        self.code = code if code in self.MESSAGES else 'unavailable'
        # Transport classification only. Never retain the response, URL or token.
        self.transient = transient and self.code in ('unavailable', 'service')
        super().__init__(self.MESSAGES[self.code])


@dataclass(frozen=True, repr=False)
class PublicProject:
    project_ref: str
    url: str
    publishable_key: str = field(repr=False)

    def __post_init__(self):
        if (type(self.project_ref) is not str or not re.fullmatch('[a-z0-9]{20}', self.project_ref)
                or self.url != f'https://{self.project_ref}.supabase.co'
                or type(self.publishable_key) is not str
                or not re.fullmatch(r'sb_publishable_[A-Za-z0-9_-]{10,256}', self.publishable_key)):
            raise AccountError('configuration')

    @classmethod
    def from_public_config(cls, document):
        if type(document) is not dict or set(document) != {'project_ref', 'url', 'publishable_key'}:
            raise AccountError('configuration')
        return cls(**document)


@dataclass(frozen=True, repr=False)
class Session:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    user_id: str
    expires_at: float


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        # Never forward authentication headers to a redirect destination.
        return None


def _uuid(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise AccountError('invalid_response') from None


def _confirmed_user(user):
    if type(user) is not dict or user.get('is_anonymous', False) is not False:
        raise AccountError('invalid_response')
    try:
        stamp = user.get('email_confirmed_at')
        if type(stamp) is not str:
            raise ValueError()
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise AccountError('invalid_response') from None


# Where Linux distributions keep the system's CA bundle, in the order Go's crypto/x509 looks: Debian, Ubuntu and Arch;
# Fedora and RHEL; openSUSE; newer Fedora; Alpine. Dervo's own Python on Linux (python-build-standalone) has no bundle
# of its own, and its OpenSSL's default folder isn't set up the same way on every distribution.
LINUX_CA_BUNDLES = ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/ca-bundle.pem',
                    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem', '/etc/ssl/cert.pem')


def account_https_context():
    """Use the system certificate bundle, independent of Python installer setup: macOS's, or the first Linux
    distribution's bundle that exists (OpenSSL's own defaults where none does, as on Windows).

    This is the system PEM bundle, not the user Keychain trust store. A missing
    or invalid bundle fails closed. Never disable certificate or hostname checks.
    """
    try:
        if sys.platform == 'darwin':
            return ssl.create_default_context(cafile='/etc/ssl/cert.pem')
        if sys.platform.startswith('linux'):
            bundle = next((path for path in LINUX_CA_BUNDLES if os.path.isfile(path)), None)
            return ssl.create_default_context(cafile=bundle) if bundle else ssl.create_default_context()
        return ssl.create_default_context()
    except Exception:
        raise AccountError('tls') from None


class SupabaseClient:
    MAX_RESPONSE = 1024 * 1024

    def __init__(self, project, *, opener=None, clock=time.time):
        if not isinstance(project, PublicProject):
            raise AccountError('configuration')
        self.project, self.clock = project, clock
        # Don't inherit proxy destinations from a project or shell environment.
        self.opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect(),
                                                                  HTTPSHandler(context=account_https_context()))

    def _request(self, method, path, payload=None, access_token=None, *, prefer=None, timeout=15, origin=None):
        # origin is set only for Dervo's own website (the waitlist), which gets the session but not the project key.
        headers = {'Accept': 'application/json'} if origin else {'apikey': self.project.publishable_key, 'Accept': 'application/json'}
        headers['User-Agent'] = 'Dervo'
        if prefer is not None:
            headers['Prefer'] = prefer
        if access_token is not None:
            self._token(access_token)
            headers['Authorization'] = 'Bearer ' + access_token
        body = None
        if payload is not None:
            body = json.dumps(payload, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        request = Request((origin or self.project.url) + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise AccountError('unavailable')
                raw = response.read(self.MAX_RESPONSE + 1)
                if len(raw) > self.MAX_RESPONSE:
                    raise AccountError('invalid_response')
                return json.loads(raw) if raw else None
        except HTTPError as error:
            # The response body and exception URL may contain sensitive details.
            code = error.code
            error.close()
            raise AccountError('expired' if code in (401, 403) else
                               'rate_limited' if code == 429 else
                               'rejected' if code in (400, 422) else
                               'email_send' if code >= 500 and path == '/auth/v1/otp' else
                               'service' if code >= 500 else 'unavailable',
                               transient=code in (502, 503, 504)) from None
        except URLError as error:
            tls = isinstance(error.reason, ssl.SSLError)
            raise AccountError('tls' if tls else 'unavailable', transient=not tls) from None
        except ssl.SSLCertVerificationError:
            raise AccountError('tls') from None
        except (TimeoutError, ConnectionError):
            raise AccountError('unavailable', transient=True) from None
        except AccountError:
            raise
        except (ValueError, UnicodeError):
            raise AccountError('invalid_response') from None
        except Exception:
            raise AccountError('unavailable') from None

    @staticmethod
    def _token(value):
        if type(value) is not str or not 10 <= len(value) <= 32768 or any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise AccountError('invalid_response')
        return value

    @staticmethod
    def _email(value):
        if (type(value) is not str or len(value) > 254 or value != value.strip()
                or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value)
                or any(ord(c) < 32 for c in value)):
            raise AccountError('rejected')
        return value

    def send_code(self, email):
        self._request('POST', '/auth/v1/otp', {'email': self._email(email), 'create_user': True})
        # A successful send isn't a session, and doesn't reveal whether the account exists.

    def verify_code(self, email, code):
        if type(code) is not str or not re.fullmatch(r'[0-9]{6,10}', code):
            raise AccountError('rejected')
        try:
            data = self._request('POST', '/auth/v1/verify',
                                 {'email': self._email(email), 'token': code, 'type': 'email'})
        except AccountError as error:
            if error.code == 'expired':
                # Auth answers a wrong, used or old code with 401 or 403, which _request reads as an expired session. No
                # session exists yet, so it's the code: the card can ask for it again instead of saying the session
                # expired and dropping the tester out of the code step.
                raise AccountError('rejected') from None
            raise
        if (type(data) is not dict or type(data.get('user')) is not dict
                or type(data['user'].get('email')) is not str
                or data['user']['email'].casefold() != email.casefold()):
            raise AccountError('invalid_response')
        return self._session(data)

    def _session(self, data, expected_user=None):
        if type(data) is not dict or data.get('token_type') != 'bearer' or type(data.get('user')) is not dict:
            raise AccountError('invalid_response')
        user_id = _uuid(data['user'].get('id'))
        if expected_user is not None and user_id != expected_user:
            raise AccountError('invalid_response')
        access, refresh = self._token(data.get('access_token')), self._token(data.get('refresh_token'))
        duration = data.get('expires_in')
        if type(duration) not in (int, float) or not 0 < duration <= 86400:
            raise AccountError('invalid_response')
        # This response comes directly from Auth's OTP/refresh endpoint over TLS.
        # Return rotated tokens immediately so the adapter can save them before
        # further network calls. Restoring a saved session uses user() explicitly.
        _confirmed_user(data['user'])
        return Session(access, refresh, user_id, self.clock() + duration)

    def user(self, access_token, *, expected_user):
        data = self._request('GET', '/auth/v1/user', access_token=access_token)
        if type(data) is not dict or _uuid(data.get('id')) != expected_user:
            raise AccountError('invalid_response')
        _confirmed_user(data)
        return {'issuer': self.project.url + '/auth/v1', 'subject': expected_user}

    def refresh(self, session):
        data = self._request('POST', '/auth/v1/token?grant_type=refresh_token',
                             {'refresh_token': self._token(session.refresh_token)})
        return self._session(data, expected_user=session.user_id)

    def sign_out(self, session):
        # Revoke this Dervo session, not the user's other devices or provider logins.
        self._request('POST', '/auth/v1/logout?scope=local', access_token=session.access_token)

    def access(self, session):
        data = self._request('POST', '/rest/v1/rpc/dervo_access', {}, session.access_token)
        fields = {'status', 'allowed', 'expires_at', 'release_channel', 'is_admin'}
        if (type(data) is not dict or set(data) != fields
                or data.get('status') not in ('pending', 'approved', 'revoked')
                or type(data.get('allowed')) is not bool or type(data.get('is_admin')) is not bool
                or data.get('release_channel') != 'private-beta'):
            raise AccountError('invalid_response')
        expiry = data['expires_at']
        if expiry is not None:
            try:
                parsed = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError()
                expiry = parsed.timestamp()
            except (ValueError, TypeError, AttributeError, OverflowError):
                raise AccountError('invalid_response') from None
        # An inconsistent grant fails closed, even if the server DTO says allowed.
        if data['allowed'] and (data['status'] != 'approved' or (expiry is not None and expiry <= self.clock())):
            raise AccountError('invalid_response')
        return dict(data)

    # Paid plans and the free trial (8 Oct 2026, supabase/migrations/202610080001_dervo_billing.sql). The plan only
    # describes; access itself still comes from access(), which billing writes on the server.
    def plan(self, session):
        return _plan_document(self._request('POST', '/rest/v1/rpc/dervo_plan', {}, session.access_token))

    def start_trial(self, session, device):
        if type(device) is not str or not re.fullmatch('[0-9a-f]{64}', device):
            raise AccountError('rejected')
        data = self._request('POST', '/rest/v1/rpc/dervo_start_trial', {'device': device}, session.access_token)
        if (type(data) is not dict or set(data) != {'state', 'ends_at'}
                or data['state'] not in ('started', 'already_used', 'not_needed', 'used_elsewhere', 'removed', 'not_offered')):
            raise AccountError('invalid_response')
        return {'state': data['state'], 'ends_at': _stamp(data['ends_at'])}

    def billing_link(self, session, action, interval=None, device=None):
        """A Paddle checkout (``interval`` month or year, from the Mac ``device`` hashes to) or customer portal link for
        this session's own account."""
        if action == 'checkout' and interval in ('month', 'year') and type(device) is str and re.fullmatch('[a-f0-9]{64}', device):
            payload = {'action': 'checkout', 'interval': interval, 'device': device}
        elif action == 'manage' and interval is None:
            payload = {'action': 'manage'}
        else:
            raise AccountError('rejected')
        data = self._request('POST', '/functions/v1/dervo-billing', payload, session.access_token, timeout=20)
        if type(data) is not dict or set(data) != {'action', 'url'} or data['action'] != action or not billing_url(data['url']):
            raise AccountError('invalid_response')
        return data['url']

    def profile(self, session):
        user_id = _uuid(session.user_id)
        data = self._request('GET', '/rest/v1/dervo_profiles?select=user_id,display_name&user_id=eq.' + user_id,
                             access_token=session.access_token)
        if (type(data) is not list or len(data) != 1 or type(data[0]) is not dict
                or set(data[0]) != {'user_id', 'display_name'} or data[0]['user_id'] != user_id
                or type(data[0]['display_name']) is not str or len(data[0]['display_name']) > 80):
            raise AccountError('invalid_response')
        return dict(data[0])

    def save_name(self, session, name):
        if type(name) is not str or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise AccountError('rejected')
        user_id = _uuid(session.user_id)
        self._request('PATCH', '/rest/v1/dervo_profiles?user_id=eq.' + user_id,
                      {'display_name': name.strip()}, session.access_token)
        return self.profile(session)

    def testers(self, session):
        """Everyone with a membership, newest sign-up first, with their email. Admins only (the server refuses others).
        The server returns at most ``TESTERS_CAP`` rows, so a full page means older sign-ups may be missing."""
        data = self._request('POST', '/rest/v1/rpc/dervo_admin_memberships', {}, session.access_token)
        if type(data) is not list or len(data) > TESTERS_CAP:
            raise AccountError('invalid_response')
        return [_tester_row(row) for row in data]

    def tester_audit(self, session, before=None, limit=20):
        """Membership changes, newest first, ``limit`` at a time, older than audit row ``before`` when given."""
        if (before is not None and (type(before) is not int or before < 1)) or type(limit) is not int or not 1 <= limit <= 100:
            raise AccountError('rejected')
        data = self._request('POST', '/rest/v1/rpc/dervo_admin_membership_audit',
                             {'before_id': before, 'max_rows': limit}, session.access_token)
        if type(data) is not list or len(data) > limit:
            raise AccountError('invalid_response')
        return [_audit_row(row) for row in data]

    def set_membership(self, session, user_id, status, expiry=None):
        """Approve, revoke or set an end date through the existing dervo_set_membership, which audits every change."""
        if status not in TESTER_STATUSES:
            raise AccountError('rejected')
        stamp = None
        if expiry is not None:
            if type(expiry) not in (int, float) or isinstance(expiry, bool) or expiry != expiry:
                raise AccountError('rejected')
            stamp = datetime.fromtimestamp(expiry).astimezone().isoformat()
        self._request('POST', '/rest/v1/rpc/dervo_set_membership',
                      {'target_user': _uuid(user_id), 'new_status': status, 'expiry': stamp}, session.access_token)

    # Waitlist and beta invitations. The waitlist lives with the website, which also sends the email. It checks the
    # caller's session with Supabase before answering and refuses anyone who isn't an admin (website/server/invitations.mjs).
    def waitlist(self, session):
        """Everyone on the waitlist, oldest signup first, with where their invitation stands. Admins only."""
        data = self._request('GET', '/api/admin/waitlist', access_token=session.access_token, origin=WAITLIST_ORIGIN, timeout=20)
        if (type(data) is not dict or not {'ok', 'people', 'more'} <= set(data) <= {'ok', 'people', 'more', 'email_preview'} or data['ok'] is not True
                or type(data['more']) is not bool or type(data['people']) is not list or len(data['people']) > WAITLIST_CAP):
            raise AccountError('invalid_response')
        result = {'people': [_waitlist_row(row) for row in data['people']], 'more': data['more']}
        if 'email_preview' in data:
            result['email_preview'] = _invitation_preview(data['email_preview'])
        return result

    def waitlist_action(self, session, action, emails):
        """Invite, resend to or revoke a small batch. Returns one result per email, in the order sent."""
        if (action not in WAITLIST_ACTIONS or type(emails) is not list or not 1 <= len(emails) <= WAITLIST_BATCH
                or any(type(email) is not str for email in emails) or len(set(emails)) != len(emails)):
            raise AccountError('rejected')
        emails = [normalize_waitlist_email(email) for email in emails]
        if len(set(emails)) != len(emails):
            raise AccountError('rejected')
        # Each person is a grant and an email, one after another, so a full batch can take a while.
        data = self._request('POST', '/api/admin/invitations', {'action': action, 'emails': emails}, session.access_token,
                             origin=WAITLIST_ORIGIN, timeout=90)
        if (type(data) is not dict or set(data) != {'ok', 'results'} or data['ok'] is not True
                or type(data['results']) is not list or len(data['results']) != len(emails)):
            raise AccountError('invalid_response')
        results = []
        for email, row in zip(emails, data['results']):
            if (type(row) is not dict or not {'email', 'result'} <= set(row) <= {'email', 'result', 'expires_at'}
                    or row['email'] != email.lower() or row['result'] not in WAITLIST_RESULTS):
                raise AccountError('invalid_response')
            results.append({'email': email, 'result': row['result'], 'expires_at': _epoch(row.get('expires_at'))})
        return results

    # Usage events (beta.9). Rows are already cleaned by usage_events.clean. The server fills user_id from the token and
    # refuses a row that names one (migrations/202609190002_dervo_usage_events.sql), so these calls never send it.
    def record_usage(self, session, rows):
        """Add usage rows as the signed-in user, in one short request. Nothing is read back."""
        if type(rows) is not list or not 1 <= len(rows) <= 500 or any(type(row) is not dict or set(row) != USAGE_ROW for row in rows):
            raise AccountError('rejected')
        self._request('POST', '/rest/v1/dervo_usage_events', rows, session.access_token, prefer='return=minimal', timeout=10)

    def usage_report(self, session, days=7):
        """The admin's counts for the last ``days`` days (dervo_admin_usage). Admins only: the server refuses anyone else."""
        if type(days) is not int or isinstance(days, bool) or not 1 <= days <= 90:
            raise AccountError('rejected')
        return _usage_document(self._request('POST', '/rest/v1/rpc/dervo_admin_usage', {'days': days}, session.access_token))

    def usage_people(self, session, days=7):
        """The admin's counts per tester for the last ``days`` days (dervo_admin_usage_people). Admins only."""
        if type(days) is not int or isinstance(days, bool) or not 1 <= days <= 90:
            raise AccountError('rejected')
        return _people_document(self._request('POST', '/rest/v1/rpc/dervo_admin_usage_people', {'days': days}, session.access_token))

    # Error reports (beta.11). Rows are already reduced to their shape by error_reports.row; the server checks every field
    # again and fills user_id from the token (migrations/202609190004_dervo_error_reports.sql).
    def record_errors(self, session, rows):
        """Add error report rows as the signed-in user, in one short request. Nothing is read back."""
        if type(rows) is not list or not 1 <= len(rows) <= 500 or any(type(row) is not dict or set(row) != ERROR_ROW for row in rows):
            raise AccountError('rejected')
        self._request('POST', '/rest/v1/dervo_error_reports', rows, session.access_token, prefer='return=minimal', timeout=10)

    def error_report(self, session, days=7):
        """The admin's grouped error counts for the last ``days`` days (dervo_admin_errors). Admins only."""
        if type(days) is not int or isinstance(days, bool) or not 1 <= days <= 90:
            raise AccountError('rejected')
        return _error_document(self._request('POST', '/rest/v1/rpc/dervo_admin_errors', {'days': days}, session.access_token))

    # Tester reports (beta.16): what testers sent with Send feedback and ⌘⇧B, for the Tester reports section of Next up.
    # The server checks dervo_private.admins and returns a first name, never an email or id
    # (migrations/202609250001_dervo_admin_feedback.sql).
    def admin_feedback(self, session, days=30):
        """The admin's tester reports for the last ``days`` days, newest first. Admins only."""
        if type(days) is not int or isinstance(days, bool) or not 1 <= days <= 90:
            raise AccountError('rejected')
        return _feedback_document(self._request('POST', '/rest/v1/rpc/dervo_admin_feedback', {'days': days}, session.access_token))


# ---------------------------------------------------------------------- Testers (Settings, admins only)
# The server decides who's an admin: each function below checks dervo_private.admins for the caller's own session
# (migrations/202609190001_dervo_testers.sql and dervo_set_membership). These calls only shape and check the answers.
# dervo_admin_memberships ends at 500 rows, newest sign-ups first (supabase/migrations/202609190001_dervo_testers.sql).
TESTERS_CAP = 500
TESTER_STATUSES = ('pending', 'approved', 'revoked')


# Where checkout and billing may open: Paddle's own pages or the default payment link on trydervo.com. The Edge
# Function checks the same list (supabase/functions/dervo-billing/handler.ts).
BILLING_HOST = re.compile(r'(?:(?:sandbox-)?(?:checkout|customer-portal|buy|pay)\.paddle\.(?:com|io)|(?:www\.)?trydervo\.com)')


def billing_url(value):
    from urllib.parse import urlsplit
    if type(value) is not str or len(value) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme == 'https' and parts.username is None and parts.password is None and parts.port is None
                and bool(BILLING_HOST.fullmatch(parts.hostname or '')) and parts.netloc == parts.hostname)
    except ValueError:
        return False


PLANS = ('beta', 'trial', 'paid', 'none', 'removed')
SUBSCRIPTION_STATES = ('active', 'trialing', 'past_due', 'paused', 'canceled')


def _plan_document(data):
    """dervo_plan(), checked field by field. Times become epoch seconds."""
    if (type(data) is not dict or set(data) != {'plan', 'environment', 'trial', 'subscription', 'can_buy', 'can_manage'}
            or data['plan'] not in PLANS or data['environment'] not in ('sandbox', 'live')
            or type(data['can_buy']) is not bool or type(data['can_manage']) is not bool):
        raise AccountError('invalid_response')
    trial = data['trial']
    if (type(trial) is not dict or set(trial) != {'days', 'mode', 'ends_at', 'available'} or type(trial['days']) is not int
            or not 1 <= trial['days'] <= 30 or type(trial['available']) is not bool or trial['mode'] not in ('card', 'app')):
        raise AccountError('invalid_response')
    subscription = data['subscription']
    if subscription is not None:
        if (type(subscription) is not dict
                or set(subscription) != {'status', 'interval', 'period_ends_at', 'cancel_at', 'access_until'}
                or subscription['status'] not in SUBSCRIPTION_STATES or subscription['interval'] not in ('month', 'year')):
            raise AccountError('invalid_response')
        subscription = {'status': subscription['status'], 'interval': subscription['interval'],
                        'period_ends_at': _stamp(subscription['period_ends_at']),
                        'cancel_at': _stamp(subscription['cancel_at']), 'access_until': _stamp(subscription['access_until'])}
    return {'plan': data['plan'], 'environment': data['environment'],
            'trial': {'days': trial['days'], 'mode': trial['mode'], 'ends_at': _stamp(trial['ends_at']),
                      'available': trial['available']},
            'subscription': subscription, 'can_buy': data['can_buy'], 'can_manage': data['can_manage']}


def _stamp(value, *, optional=True):
    """An ISO timestamp with a zone, as the server sends it, or None when it may be absent."""
    if value is None and optional:
        return None
    try:
        if type(value) is not str or len(value) > 64:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise AccountError('invalid_response') from None


def _address(value):
    if value is None:
        return None
    if type(value) is not str or len(value) > 320 or any(ord(c) < 32 for c in value):
        raise AccountError('invalid_response')
    return value


def _tester_row(row):
    fields = {'user_id', 'email', 'status', 'expires_at', 'signed_up_at', 'updated_at', 'is_admin'}
    if type(row) is not dict or set(row) != fields or row.get('status') not in TESTER_STATUSES or type(row.get('is_admin')) is not bool:
        raise AccountError('invalid_response')
    return {'user_id': _uuid(row['user_id']), 'email': _address(row['email']), 'status': row['status'],
            'expires_at': _stamp(row['expires_at']), 'signed_up_at': _stamp(row['signed_up_at']),
            'updated_at': _stamp(row['updated_at']), 'is_admin': row['is_admin']}


def _audit_row(row):
    fields = {'id', 'changed_at', 'actor_id', 'actor_email', 'subject_id', 'subject_email', 'previous_status', 'new_status', 'expires_at'}
    if (type(row) is not dict or set(row) != fields or type(row.get('id')) is not int or isinstance(row.get('id'), bool)
            or row.get('new_status') not in TESTER_STATUSES or row.get('previous_status') not in (None, *TESTER_STATUSES)):
        raise AccountError('invalid_response')
    return {'id': row['id'], 'changed_at': _stamp(row['changed_at'], optional=False), 'actor_id': _uuid(row['actor_id']),
            'actor_email': _address(row['actor_email']), 'subject_id': _uuid(row['subject_id']),
            'subject_email': _address(row['subject_email']), 'previous_status': row['previous_status'],
            'new_status': row['new_status'], 'expires_at': _stamp(row['expires_at'])}


# ---------------------------------------------------------------------- Waitlist (Testers page, admins only)
WAITLIST_ORIGIN = 'https://trydervo.com'
WAITLIST_CAP = 500
WAITLIST_BATCH = 10
WAITLIST_ACTIONS = ('add', 'invite', 'resend', 'revoke')
WAITLIST_STATUSES = ('waiting', 'invited', 'accepted', 'declined', 'revoked')
WAITLIST_RESULTS = ('added', 'already_listed', 'delivery_review', 'sent', 'already_invited', 'already_accepted', 'already_approved', 'member_revoked', 'needs_resend',
                    'not_on_waitlist', 'rate_limited', 'send_budget_reached', 'grant_failed', 'send_failed', 'not_admin',
                    'revoked', 'revoke_failed', 'failed')


def _epoch(value, *, optional=True):
    """Whole seconds since 1970, as the website sends times, or None when it may be absent."""
    if value is None and optional:
        return None
    if type(value) is not int or isinstance(value, bool) or not 0 < value < 4102444800:
        raise AccountError('invalid_response')
    return value


def _waitlist_row(row):
    fields = {'email', 'signed_up_at', 'status', 'invited_at', 'expires_at', 'delivery'}
    if (type(row) is not dict or not fields <= set(row) <= fields | {'source'} or row.get('source', 'website') not in ('website', 'admin') or row.get('status') not in WAITLIST_STATUSES
            or row.get('delivery') not in (None, 'pending', 'sent', 'failed') or type(row.get('email')) is not str):
        raise AccountError('invalid_response')
    return {**({'source': row['source']} if 'source' in row else {}), 'email': _address(row['email']), 'signed_up_at': _epoch(row['signed_up_at'], optional=False),
            'status': row['status'], 'invited_at': _epoch(row['invited_at']), 'expires_at': _epoch(row['expires_at']),
            'delivery': row['delivery']}


def normalize_waitlist_email(value):
    if type(value) is not str or len(value) > 320:
        raise AccountError('rejected')
    email = value.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+\-/=?^_`{|}~]+@[a-z0-9.-]+\.[a-z]{2,63}", email):
        raise AccountError('rejected')
    local, domain = email.split('@')
    if (len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in local
            or any(len(label) > 63 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) for label in domain.split('.'))):
        raise AccountError('rejected')
    return email


def _invitation_preview(value):
    fields = {'from', 'reply_to', 'subject', 'text'}
    if (type(value) is not dict or set(value) != fields
            or any(type(value[key]) is not str or not 1 <= len(value[key]) <= 8000 for key in fields)
            or any('\n' in value[key] or '\r' in value[key] for key in ('from', 'reply_to', 'subject'))):
        raise AccountError('invalid_response')
    return dict(value)


# ---------------------------------------------------------------------- Usage report (Testers page, admins only)
USAGE_ROW = {'install_id', 'event', 'properties', 'app_version'}
USAGE_SECTIONS = {'daily': {'day', 'users', 'events'}, 'events': {'day', 'event', 'users', 'count'},
                  'screens': {'day', 'screen', 'users', 'count'}, 'funnel': {'step', 'reached', 'completed'},
                  'tasks': {'provider', 'outcome', 'count', 'users', 'median_seconds'}}


def _usage_value(key, value):
    """Counts are whole numbers, days are dates, names are short lowercase words. A median may be absent."""
    if key == 'median_seconds' and value is None:
        return None
    if key in ('users', 'events', 'count', 'reached', 'completed', 'median_seconds'):
        if type(value) is float and value.is_integer():
            value = int(value)
        if type(value) is not int or value < 0:
            raise AccountError('invalid_response')
        return value
    if key == 'day':
        if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise AccountError('invalid_response')
        return value
    if type(value) is not str or not re.fullmatch(r'[a-z0-9_.-]{1,40}', value):
        raise AccountError('invalid_response')
    return value


# One row per tester (migrations/202609280001_dervo_admin_usage_people.sql). The server ends the list at 500.
PEOPLE_COUNTS = ('active_days', 'opens', 'threads', 'home_requests', 'tasks_started', 'tasks_completed', 'tasks_failed',
                 'claude_tasks', 'codex_tasks', 'knowledge', 'feedback_opened')
PEOPLE_ROW = {'user_id', 'email', 'status', 'is_admin', 'last_seen', 'app_version', *PEOPLE_COUNTS}


def _people_document(data):
    """Each tester's counts, checked field by field. A tester who never sent a row has no last_seen or version."""
    if type(data) is not dict or set(data) != {'since', 'days', 'people'} or type(data['people']) is not list or len(data['people']) > TESTERS_CAP:
        raise AccountError('invalid_response')
    if type(data['days']) is not int or isinstance(data['days'], bool) or not 1 <= data['days'] <= 90:
        raise AccountError('invalid_response')
    people = []
    for row in data['people']:
        if (type(row) is not dict or set(row) != PEOPLE_ROW or type(row['is_admin']) is not bool
                or row['status'] not in (None, *TESTER_STATUSES)):
            raise AccountError('invalid_response')
        version = row['app_version']
        if version is not None and (type(version) is not str or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,40}', version)):
            raise AccountError('invalid_response')
        person = {'user_id': _uuid(row['user_id']), 'email': _address(row['email']), 'status': row['status'],
                  'is_admin': row['is_admin'], 'last_seen': _stamp(row['last_seen']), 'app_version': version}
        for key in PEOPLE_COUNTS:
            value = row[key]
            if type(value) is float and value.is_integer():
                value = int(value)
            if type(value) is not int or isinstance(value, bool) or value < 0:
                raise AccountError('invalid_response')
            person[key] = value
        people.append(person)
    return {'since': _stamp(data['since'], optional=False), 'days': data['days'], 'people': people}


ERROR_ROW = {'install_id', 'source', 'error_type', 'file', 'function', 'line', 'operation', 'provider', 'app_version', 'count'}
ERROR_GROUP = {'source', 'error_type', 'file', 'function', 'line', 'operation', 'provider', 'count', 'users', 'first_seen',
               'last_seen', 'app_version'}


def _error_document(data):
    """The admin's error groups, each field checked for the same shape the server allows."""
    import error_reports
    if type(data) is not dict or set(data) != {'since', 'days', 'errors'} or type(data['errors']) is not list or len(data['errors']) > 200:
        raise AccountError('invalid_response')
    if type(data['days']) is not int or isinstance(data['days'], bool) or not 1 <= data['days'] <= 90:
        raise AccountError('invalid_response')
    groups = []
    for group in data['errors']:
        if type(group) is not dict or set(group) != ERROR_GROUP:
            raise AccountError('invalid_response')
        where = (group['file'], group['function'], group['line']) if group['file'] is not None else None
        shaped = error_reports.row(group['source'], group['error_type'], where, group['operation'], group['provider'])
        if (shaped is None or (where is not None) != (shaped['file'] is not None)
                or group['operation'] is not None and shaped['operation'] is None
                or group['provider'] is not None and shaped['provider'] is None):
            raise AccountError('invalid_response')
        counts = {}
        for key in ('count', 'users'):
            value = group[key]
            if type(value) is float and value.is_integer():
                value = int(value)
            if type(value) is not int or isinstance(value, bool) or value < 1:
                raise AccountError('invalid_response')
            counts[key] = value
        version = group['app_version']
        if version is not None and (type(version) is not str or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,40}', version)):
            raise AccountError('invalid_response')
        groups.append({**shaped, **counts, 'first_seen': _stamp(group['first_seen'], optional=False),
                       'last_seen': _stamp(group['last_seen'], optional=False), 'app_version': version})
    return {'since': _stamp(data['since'], optional=False), 'days': data['days'], 'errors': groups}


FEEDBACK_ROW = {'id', 'category', 'message', 'created_at', 'tester', 'app_version', 'route', 'provider'}
FEEDBACK_CATEGORIES = ('feedback', 'bug', 'confusing', 'idea')
FEEDBACK_ROUTES = ('conversation', 'catch-up', 'search', 'knowledge', 'settings', 'welcome', 'workspace')


def _feedback_document(data):
    """The admin's tester reports, each field checked for the shape dervo_submit_feedback allows."""
    if type(data) is not dict or set(data) != {'days', 'reports'} or type(data['reports']) is not list or len(data['reports']) > 200:
        raise AccountError('invalid_response')
    if type(data['days']) is not int or isinstance(data['days'], bool) or not 1 <= data['days'] <= 90:
        raise AccountError('invalid_response')
    reports = []
    for row in data['reports']:
        if type(row) is not dict or set(row) != FEEDBACK_ROW:
            raise AccountError('invalid_response')
        message, tester, version = row['message'], row['tester'], row['app_version']
        if (row['category'] not in FEEDBACK_CATEGORIES or type(message) is not str or not 1 <= len(message) <= 4000
                or (tester is not None and (type(tester) is not str or not 1 <= len(tester) <= 80))
                or (version is not None and (type(version) is not str or not re.fullmatch(r'[A-Za-z0-9.+_-]{1,80}', version)))
                or (row['route'] is not None and row['route'] not in FEEDBACK_ROUTES)
                or (row['provider'] is not None and row['provider'] not in ('codex', 'claude', 'openrouter'))):
            raise AccountError('invalid_response')
        reports.append({'id': str(_uuid(row['id'])), 'category': row['category'], 'message': message,
                        'created_at': _stamp(row['created_at'], optional=False), 'tester': tester,
                        'app_version': version, 'route': row['route'], 'provider': row['provider']})
    return {'days': data['days'], 'reports': reports}


def _usage_document(data):
    if type(data) is not dict or set(data) != {'since', 'days', 'active', *USAGE_SECTIONS}:
        raise AccountError('invalid_response')
    if any(type(data[key]) is not int or isinstance(data[key], bool) or data[key] < 0 for key in ('days', 'active')):
        raise AccountError('invalid_response')
    report = {'since': _stamp(data['since'], optional=False), 'days': data['days'], 'active': data['active']}
    for section, names in USAGE_SECTIONS.items():
        rows = data[section]
        if type(rows) is not list or len(rows) > 5000:
            raise AccountError('invalid_response')
        report[section] = []
        for row in rows:
            if type(row) is not dict or set(row) != names:
                raise AccountError('invalid_response')
            report[section].append({key: _usage_value(key, value) for key, value in row.items()})
    return report
