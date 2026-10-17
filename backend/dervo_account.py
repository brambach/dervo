"""Dervo identity lifecycle, separate from provider profiles and credentials.

The injected host adapter owns actual authentication and any session material. Only
verified public identity results reach this layer. No production adapter is wired
until the authentication service and ownership migration are implemented.
"""
import hashlib
import json
import math
import time
import uuid

from dervo_connections import Rejected

FIELDS = {
    'dervo_account_status': set(), 'dervo_account_start': {'mode'},
    'dervo_account_poll': {'operationId'}, 'dervo_account_cancel': {'operationId'},
    'dervo_account_restore': set(), 'dervo_account_sign_out': set(),
}
DETAILS = {
    'unavailable': "Dervo sign-in isn't available in this build. Your projects stay on this Mac. Claude and Codex connections are separate.",
    'signed_out': 'You’re signed out of Dervo. Your provider connections are unchanged.',
    'awaiting_user': 'Finish the Dervo sign-in step, then return here. You can cancel this attempt.',
    'verifying': 'Dervo is checking the account. You aren’t signed in yet.',
    'signed_in': 'Your Dervo identity is verified. Existing local projects haven’t been transferred to this account.',
    'expired': 'Your Dervo session expired. Check the session before continuing.',
    'cancelled': 'Dervo sign-in was cancelled. No account was activated.',
    'unknown': 'Dervo couldn’t confirm the account operation. Check the session before trying again.',
    'interrupted': 'Dervo closed before the account operation was confirmed. Check the session to continue.',
    'restore_required': 'Check your saved Dervo session. Saved account details alone don’t establish sign-in.',
    'account_mismatch': 'The returned Dervo account differs from the saved account. No projects or memory were transferred. Account selection needs review.',
    'failed': 'Dervo sign-in didn’t finish. You can try again.',
}


class AccountSession:
    """Adapter.perform(kind, id, previous) and poll(id) return bounded public DTOs.

    They must return promptly. A future network adapter must own its background
    jobs. Renderer arguments never select or construct the adapter. Store holds
    only last identity and pending operation metadata, never session credentials.
    """
    def __init__(self, store, adapter=None, clock=time.time):
        self.store, self.adapter, self.clock = store, adapter, clock
        self.identity = None
        self.expires_at = None
        self.operation = None
        self.last_poll = 0
        self.saved = {'version': 1, 'last_identity': None, 'pending': None}
        # An unavailable service has no reason to create or change account storage.
        self.state = 'unavailable'
        if adapter is None:
            return
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS dervo_account_state (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)')
        row = self.store.db.execute('SELECT document FROM dervo_account_state WHERE id=1').fetchone()
        if row:
            saved = json.loads(row[0])
            if set(saved) != set(self.saved) or saved['version'] != 1:
                raise Rejected('Saved Dervo account metadata needs inspection')
            if saved['last_identity'] is not None:
                self._identity(saved['last_identity'])
            if saved['pending'] is not None:
                pending = saved['pending']
                if (type(pending) is not dict or set(pending) != {'id', 'kind', 'previous'}
                        or pending['kind'] not in ('sign_in', 'sign_up', 'restore', 'sign_out', 'cancel')
                        or type(pending['id']) is not str or type(pending['previous']) not in (str, type(None))):
                    raise Rejected('Saved Dervo operation needs inspection')
            self.saved = saved
        self.state = 'interrupted' if self.saved['pending'] else 'restore_required' if self.saved['last_identity'] else 'signed_out'

    @staticmethod
    def _identity(value):
        if type(value) is not dict or set(value) != {'issuer', 'subject', 'display_name'}:
            raise Rejected('The Dervo identity response is invalid')
        for field in value.values():
            if type(field) is not str or not field.strip() or len(field.encode()) > 240 or any(ord(c) < 32 for c in field):
                raise Rejected('The Dervo identity response is invalid')
        return dict(value)

    def _save(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO dervo_account_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                                  (json.dumps(self.saved),))

    def status(self):
        if self.state == 'signed_in' and self.clock() >= self.expires_at:
            self.state, self.identity, self.expires_at = 'expired', None, None
        available = self.adapter is not None
        return dict(state=self.state, detail=DETAILS[self.state], identity=dict(self.identity) if self.identity else None,
                    operation_id=self.operation['id'] if self.operation else None,
                    can_start=available and self.state in ('signed_out', 'expired', 'cancelled', 'failed') and not self.operation,
                    can_cancel=available and bool(self.operation) and self.operation['kind'] in ('sign_in', 'sign_up'),
                    can_poll=available and bool(self.operation) and self.state in ('awaiting_user', 'verifying'),
                    can_restore=available and self.state in ('restore_required', 'interrupted', 'unknown', 'expired', 'account_mismatch'),
                    can_sign_out=available and self.state == 'signed_in',
                    expires_at=self.expires_at if self.state == 'signed_in' else None,
                    ownership='local_unclaimed', sync_available=False)

    def _result(self, operation_id, result):
        # A cancelled or replaced operation can never activate a late account.
        if not self.operation or self.operation['id'] != operation_id:
            return self.status()
        try:
            if type(result) is not dict or result.get('operation_id') != operation_id:
                raise Rejected('The Dervo operation response is invalid')
            state = result.get('state')
            expected = {'operation_id', 'state'} | ({'identity', 'expires_at'} if state == 'signed_in' else set())
            if set(result) != expected or state not in ('signed_in', 'signed_out', 'awaiting_user', 'verifying', 'expired', 'cancelled', 'failed', 'unknown'):
                raise Rejected('The Dervo operation response is invalid')
            kind = self.operation['kind']
            allowed = {
                'sign_in': {'signed_in', 'signed_out', 'awaiting_user', 'verifying', 'expired', 'cancelled', 'failed', 'unknown'},
                'sign_up': {'signed_in', 'signed_out', 'awaiting_user', 'verifying', 'expired', 'cancelled', 'failed', 'unknown'},
                'restore': {'signed_in', 'signed_out', 'verifying', 'expired', 'unknown'},
                'sign_out': {'signed_out', 'verifying', 'unknown'},
                'cancel': {'cancelled', 'verifying', 'unknown'},
            }
            if state not in allowed[kind]:
                raise Rejected('The Dervo operation response is invalid for this action')
            if state == 'signed_in':
                if kind not in ('sign_in', 'sign_up', 'restore'):
                    raise Rejected('This operation cannot sign in an account')
                identity = self._identity(result['identity'])
                expiry = result['expires_at']
                if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= self.clock():
                    raise Rejected('The Dervo session response is expired')
                last = self.saved['last_identity']
                if last and (identity['issuer'], identity['subject']) != (last['issuer'], last['subject']):
                    self.state = 'account_mismatch'
                else:
                    self.identity, self.expires_at, self.state = identity, expiry, state
                    self.saved['last_identity'] = identity
            else:
                if kind in ('sign_out', 'cancel') and state == 'awaiting_user':
                    raise Rejected('This operation cannot request a sign-in step')
                self.state = state
            if self.state not in ('awaiting_user', 'verifying', 'unknown'):
                self.operation = None
                self.saved['pending'] = None
            self._save()
        except Exception:
            # Adapter diagnostics may contain credentials. Never propagate them to renderer or logs.
            self.state, self.identity, self.expires_at = 'unknown', None, None
        return self.status()

    def _perform(self, kind, previous=None):
        self.identity, self.expires_at = None, None
        self.operation = {'id': uuid.uuid4().hex, 'kind': kind, 'previous': previous}
        self.saved['pending'] = dict(self.operation)
        self.state = 'verifying'
        self._save()  # Record intent before asking the adapter to act.
        operation = dict(self.operation)
        try:
            result = self.adapter.perform(kind, operation['id'], previous)
        except Exception:
            result = {'operation_id': operation['id'], 'state': 'unknown'}
        return self._result(operation['id'], result)

    def tick(self):
        current = self.status()
        if current['can_poll'] and self.clock() - self.last_poll >= 1:
            self.last_poll = self.clock()
            self.dispatch('dervo_account_poll', {'operationId': self.operation['id']})

    def close(self):
        if self.status()['can_cancel']:
            self.dispatch('dervo_account_cancel', {'operationId': self.operation['id']})

    def require_identity(self, issuer, subject):
        """For future account-owned work. Legacy local projects aren't claimed here."""
        status = self.status()
        if status['state'] != 'signed_in' or (self.identity['issuer'], self.identity['subject']) != (issuer, subject):
            raise Rejected('This work needs its verified Dervo account')
        return hashlib.sha256(json.dumps([issuer, subject], separators=(',', ':')).encode()).hexdigest()

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported Dervo account fields')
        current = self.status()
        if operation == 'dervo_account_status':
            return current
        if self.adapter is None:
            raise Rejected(DETAILS['unavailable'])
        if operation == 'dervo_account_start':
            if args['mode'] not in ('sign_in', 'sign_up') or not current['can_start']:
                raise Rejected('Check or finish the current Dervo account operation first')
            return self._perform(args['mode'])
        if operation == 'dervo_account_restore':
            if not current['can_restore']:
                raise Rejected('This Dervo account state does not need restoration')
            return self._perform('restore', (self.saved['pending'] or {}).get('id'))
        if operation == 'dervo_account_sign_out':
            if not current['can_sign_out']:
                raise Rejected('No verified Dervo session is available to sign out')
            return self._perform('sign_out')
        if not self.operation or type(args['operationId']) is not str or args['operationId'] != self.operation['id']:
            raise Rejected('This Dervo account operation is no longer current')
        if operation == 'dervo_account_cancel':
            if not current['can_cancel']:
                raise Rejected('This Dervo account operation cannot be cancelled')
            return self._perform('cancel', self.operation['id'])
        if not current['can_poll']:
            raise Rejected('Check the Dervo session before polling this operation again')
        try:
            result = self.adapter.poll(self.operation['id'])
        except Exception:
            result = {'operation_id': self.operation['id'], 'state': 'unknown'}
        return self._result(self.operation['id'], result)
