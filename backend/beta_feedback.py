"""Authenticated, bounded beta feedback. No relay publishing or automatic transcript capture."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import re
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4
from threading import Event

from dervo_connections import Rejected
from supabase_client import AccountError

FIELDS = {'beta_feedback_context': set(),
          'beta_feedback_send': {'requestId', 'category', 'message', 'context', 'contextId'},
          'beta_feedback_status': {'requestId', 'contextId'}}
ROUTES = {'conversation', 'catch-up', 'search', 'knowledge', 'settings', 'welcome', 'workspace'}
CONTEXT = {'route', 'projectId', 'taskId', 'conversationId', 'sessionId', 'provider', 'appVersion', 'viewport'}
ID = re.compile(r'[A-Za-z0-9_:-]{1,128}')
ERROR = 'Feedback wasn’t confirmed. Your draft is kept. Check your account and connection, then retry.'
LIMIT = 'You’ve sent a lot of feedback this hour. Your draft is kept, so try again later.'


class LimitReached(Rejected):
    """The server's hourly feedback limit (supabase/migrations/202609230001: SQLSTATE PT429, which PostgREST answers as
    HTTP 429). The client keeps no response body, so the status is the signal, and the line shown is always LIMIT."""
    def __init__(self):
        super().__init__(LIMIT)


def request_id(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, AttributeError):
        raise Rejected('Feedback needs a valid request ID.') from None


def validate(args):
    request_id(args['requestId'])
    if type(args['category']) is not str or args['category'] not in ('feedback', 'bug', 'confusing', 'idea'):
        raise Rejected('Choose Feedback, Bug, Confusing or Idea.')
    message = args['message']
    if type(message) is not str or not 1 <= len(message.strip()) <= 4000 or len(message) > 4000 or '\x00' in message:
        raise Rejected('Write between 1 and 4,000 characters of feedback.')
    context = args['context']
    if type(context) is not dict or not set(context) <= CONTEXT or type(context.get('route')) is not str or context.get('route') not in ROUTES:
        raise Rejected('Unsupported feedback context.')
    for key, value in context.items():
        if key.endswith('Id'):
            valid = type(value) is str and ID.fullmatch(value)
        elif key == 'route':
            valid = value in ROUTES
        elif key == 'provider':
            valid = value in ('codex', 'claude', 'openrouter')
        elif key == 'appVersion':
            valid = type(value) is str and re.fullmatch(r'[A-Za-z0-9.+_-]{1,80}', value)
        else:
            valid = (type(value) is dict and set(value) == {'width', 'height'}
                     and all(type(n) is int and 1 <= n <= 32768 for n in value.values()))
        if not valid:
            raise Rejected('Unsupported feedback context.')
    return {'request_id': args['requestId'], 'category': args['category'], 'message': message, 'context': dict(context)}


def submit(client, session, payload, check_active=lambda: None):
    check_active()
    client.user(session.access_token, expected_user=session.user_id)
    check_active()
    membership = client.access(session)
    if membership.get('allowed') is not True or membership.get('status') != 'approved':
        raise Rejected('Approved Dervo beta access is required to send feedback.')
    check_active()
    try:
        result = client._request('POST', '/rest/v1/rpc/dervo_submit_feedback',
                                 {'submission': payload}, access_token=session.access_token)
    except AccountError as error:
        if error.code == 'rate_limited':  # only the hourly limit answers this call with 429
            raise LimitReached() from None
        raise
    if type(result) is not dict or set(result) != {'id', 'created_at'} or result['id'] != payload['request_id']:
        raise Rejected(ERROR)
    try:
        stamp = datetime.fromisoformat(result['created_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Rejected(ERROR) from None
    return result


class Feedback:
    def __init__(self, host, *, executor=None):
        self.host = host
        self.executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-feedback')
        self.jobs = {}
        self.closed = False
        self.context = uuid4().hex
        self.bound_account = self.bound_owner = None
        self.app_version = None
        try:
            from beta_update_host import running_version
            value = running_version(Path(__file__).resolve().parent.parent)['releaseId']
            if type(value) is str and re.fullmatch(r'[A-Za-z0-9.+_-]{1,80}', value):
                self.app_version = value
        except Exception:
            pass  # Development builds may have no release metadata. Don't invent a version.

    def owner(self):
        account = getattr(self.host, 'account_session', None)
        if self.closed or account is None or not hasattr(account, 'require_access'):
            raise Rejected('Sign in to your approved Dervo account to send feedback.')
        account.require_access()
        session, identity = account.session, account.status().get('identity')
        if not session or not identity or identity['subject'] != session.user_id or identity['issuer'] != account.client.project.url + '/auth/v1':
            raise Rejected('Confirm your Dervo account before sending feedback.')
        account.require_identity(identity['issuer'], identity['subject'])
        return account, (identity['issuer'], identity['subject'])

    def invalidate(self):
        """Invalidate queued sends and receipts before any account transition."""
        self.context = uuid4().hex
        self.bound_account = self.bound_owner = None
        for job in self.jobs.values():
            job['cancelled'].set()
            job['state'] = 'error'
            job.pop('receipt', None)
            job.pop('detail', None)

    def _active(self, account, owner, session, cancelled):
        # Worker reads only in-memory account state. It never calls status(),
        # which can touch SQLite or schedule account work on the host thread.
        identity = getattr(account, 'identity', None)
        if (self.closed or cancelled.is_set() or getattr(self.host, 'account_session', None) is not account
                or getattr(account, 'session', None) is not session or getattr(account, 'state', None) != 'signed_in'
                or not identity or (identity.get('issuer'), identity.get('subject')) != owner):
            raise Rejected('The Dervo account changed. This feedback request was stopped.')

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported feedback fields.')
        try:
            account, owner = self.owner()
        except Rejected:
            self.invalidate()
            raise
        if self.bound_account is not account or self.bound_owner != owner:
            self.invalidate()
            self.bound_account, self.bound_owner = account, owner
        if operation == 'beta_feedback_context':
            return {'contextId': self.context, 'ownerKey': hashlib.sha256(json.dumps(owner).encode()).hexdigest(),
                    'appVersion': self.app_version}
        if args['contextId'] != self.context:
            raise Rejected('Your Dervo account changed. Reopen feedback before sending it.')
        ident = request_id(args['requestId'])
        key = (*owner, ident)
        job = self.jobs.get(key)
        if operation == 'beta_feedback_send':
            payload = validate(args)
            if job and job['payload'] != payload:
                raise Rejected('Changed feedback needs a new request ID.')
            if not job or job['state'] == 'error':
                if any(not row['future'].done() for row in self.jobs.values()):
                    raise Rejected('Wait for the current feedback request to finish.')
                if not job and len(self.jobs) >= 100:
                    raise Rejected('Reopen Dervo before sending more feedback.')
                # Capture this verified session. No background Keychain use or account mutation.
                cancelled = Event()
                session = account.session
                activation = self.context
                def check_active():
                    if activation != self.context:
                        raise Rejected('The Dervo account changed. This feedback request was stopped.')
                    self._active(account, owner, session, cancelled)
                job = {'payload': payload, 'state': 'sending', 'cancelled': cancelled, 'activation': activation,
                       'future': self.executor.submit(submit, account.client, session, payload, check_active)}
                self.jobs[key] = job
        if job is None:
            raise Rejected('No feedback request was found for this account.')
        if job['state'] == 'sending' and job['future'].done():
            try:
                receipt = job['future'].result()
                if job['cancelled'].is_set() or job['activation'] != self.context:
                    raise Rejected(ERROR)
                job['receipt'] = receipt
                job['state'] = 'sent'
            except LimitReached:
                job['state'], job['detail'] = 'error', LIMIT  # Dervo's own fixed line, never the server's words
            except Exception:
                # Raw server errors, request bodies and credentials never enter UI state.
                job['state'] = 'error'
        return {'requestId': ident, 'state': job['state'],
                'receipt': job.get('receipt'), 'detail': job.get('detail', ERROR) if job['state'] == 'error' else None}

    def close(self):
        self.closed = True
        self.invalidate()
        self.executor.shutdown(wait=False, cancel_futures=True)
