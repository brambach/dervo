"""Host-owned personal connection lifecycle. Network checks never touch SQLite."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import time
from uuid import uuid4

import account_config
from dervo_connections import Rejected
from openrouter_connection import ConnectionError, KeyVault, ReadOnlyClient, valid_key
from release_profiles import require_provider

FIELDS = {'openrouter_status': set(), 'openrouter_connect': {'key', 'context'},
          'openrouter_check': {'context'}, 'openrouter_cancel': {'context'}, 'openrouter_disconnect': {'context'},
          'openrouter_budget_save': {'context', 'revision', 'totalUsd', 'perRequestUsd'},
          'openrouter_model_check': {'context', 'modelId'},
          'openrouter_route_save': {'context', 'revision', 'modelId', 'provider', 'maxOutputTokens'}}


class Connections:
    def __init__(self, host, *, resources=None, vault_factory=KeyVault,
                 client_factory=ReadOnlyClient, executor=None, clock=time.monotonic):
        self.host = host
        self.resources = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
        self.vault_factory, self.client_factory = vault_factory, client_factory
        self.executor = executor
        self.future = None
        self.cancelled = Event()
        self.owner = None
        self.state, self.detail = 'unchecked', ''
        self.result = None
        self.closed = False
        self.context = uuid4().hex
        self.budget = None
        self.recovered_owners = set()
        self.clock = clock
        self.route_store = None
        self.checked_routes = None
        self.checked_until = 0
        self.pending_kind = 'connection'

    def _routes(self):
        from openrouter_routes import Routes
        if self.route_store is None:
            self.route_store = Routes(self.host.store.db, self.resources)
        return self.route_store

    def _budget(self, owner):
        from openrouter_budget import Budget
        if self.budget is None:
            self.budget = Budget(self.host.store.db, resources=self.resources)
        if owner not in self.recovered_owners:
            # This host owns the workspace; OpenRouter workers from a previous
            # host aren't resumed. Preserve any potentially charged requests.
            self.budget.recover(owner)
            self.recovered_owners.add(owner)
        return self.budget

    def _owner(self):
        require_provider('openrouter', self.resources)
        project = account_config.require(self.resources)
        account = getattr(self.host, 'account_session', None)
        if account is None:
            raise Rejected('Sign in to Dervo before connecting OpenRouter.')
        account.require_access()
        identity = account.status().get('identity')
        if not identity or identity.get('issuer') != project.url + '/auth/v1':
            raise Rejected('Check your Dervo account before connecting OpenRouter.')
        account.require_identity(identity['issuer'], identity['subject'])
        return project.project_ref, identity['issuer'], identity['subject']

    def _vault(self, owner):
        return self.vault_factory(resources=self.resources, project_ref=owner[0],
                                  workspace=self.host.root, user_id=owner[2])

    def invalidate(self):
        """Call before account transitions, including a same-owner sign-in retry."""
        self.cancelled.set()
        self.owner, self.result = None, None
        self.state, self.detail = 'unchecked', ''
        self.context = uuid4().hex
        self.checked_routes = None
        self.checked_until = 0

    def _sync_owner(self):
        try:
            owner = self._owner()
        except Exception:
            owner = None
        if owner != self.owner:
            self.invalidate()
            self.owner = owner
        return owner

    def tick(self):
        if self.closed:
            return
        owner = self._sync_owner()
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        if self.cancelled.is_set():
            # Don't inspect a stale result or forward its exception.
            return
        try:
            checked, key, save = future.result()
            if owner != self._owner():
                self.invalidate()
                return
            if save:
                self._vault(owner).save(key)
            if self.pending_kind == 'model':
                self.checked_routes = checked['routes']
                self.result = checked['connection']
                self.checked_until = checked['checkedAt'] + 300
            else:
                self.result = checked
            self.state, self.detail = 'checked', ''
        except Exception as error:
            self.result = None
            self.state = 'error'
            self.detail = str(error) if isinstance(error, ConnectionError) else 'Dervo couldn’t confirm this connection. Check the saved key before retrying.'

    def status(self):
        self.tick()
        try:
            require_provider('openrouter', self.resources)
        except Exception:
            return {'available': False}
        try:
            owner = self._owner()
            signed_in = True
        except Exception:
            signed_in = False
        return {'available': True, 'state': self.state, 'detail': self.detail,
                'context': self.context,
                'budget': self._budget(owner).status(owner) if signed_in else None,
                'routing': self._routes().status(owner) if signed_in else None,
                'checkedRoutes': self.checked_routes if self.clock() < self.checked_until else None,
                'canCheck': signed_in and self.future is None and not self.closed,
                'busy': self.future is not None, 'connection': self.result}

    def _check(self, owner, key, cancelled):
        """Worker inputs are immutable identity and transient key, never an account session."""
        save = key is not None
        try:
            if cancelled.is_set():
                return None
            if key is None:
                key = self._vault(owner).load()
            key = valid_key(key)
            client = self.client_factory(self.resources)
            if cancelled.is_set():
                return None
            limits = client.key_status(key)
            if cancelled.is_set():
                return None
            models = client.models(key)
            if cancelled.is_set():
                return None
            return {'limits': limits, 'models': models}, key, save
        except Exception as error:
            # Provider/native exceptions can contain credentials. Keep only fixed codes.
            code = error.code if isinstance(error, ConnectionError) else 'unavailable'
            raise ConnectionError(code) from None

    def _model_check(self, owner, model, cancelled):
        try:
            checked = self._check(owner, None, cancelled)
            if cancelled.is_set() or checked is None:
                return None
            connection, key, _ = checked
            if not any(row['id'] == model for row in connection['models']):
                raise ConnectionError('response')
            client = self.client_factory(self.resources)
            if cancelled.is_set():
                return None
            routes = client.endpoints(key, model)
            return {'routes': routes, 'connection': connection, 'checkedAt': self.clock()}, None, False
        except Exception as error:
            code = error.code if isinstance(error, ConnectionError) else 'unavailable'
            raise ConnectionError(code) from None

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported OpenRouter connection fields.')
        if operation == 'openrouter_status':
            return self.status()
        if self.closed:
            raise Rejected('This OpenRouter connection is closed.')
        self._sync_owner()
        owner = self._owner()
        if args['context'] != self.context:
            raise Rejected('Your Dervo account changed. Reopen the connection settings and try again.')
        if operation == 'openrouter_budget_save':
            self._budget(owner).configure(owner, total=args['totalUsd'], per_request=args['perRequestUsd'], revision=args['revision'])
            return self.status()
        if operation == 'openrouter_route_save':
            if self.future is not None or self.result is None or self.clock() >= self.checked_until:
                raise Rejected('Check the model’s providers again before saving this route.')
            self._routes().save(owner, checked=self.checked_routes, model=args['modelId'], provider=args['provider'],
                                max_output=args['maxOutputTokens'], revision=args['revision'])
            return self.status()
        if operation in ('openrouter_cancel', 'openrouter_disconnect'):
            self.invalidate()
            if operation == 'openrouter_disconnect':
                try:
                    self._vault(owner).delete()
                except Exception:
                    raise Rejected('Dervo couldn’t remove the saved OpenRouter key. Try disconnecting again.') from None
            return self.status()
        self.tick()
        if self.future is not None:
            raise Rejected('The previous connection check is still finishing. Try again shortly.')
        if operation == 'openrouter_model_check':
            from openrouter_routes import model_id
            model = model_id(args['modelId'])
            if self.result is None or not any(row['id'] == model for row in self.result['models']):
                raise Rejected('Check your OpenRouter connection and choose an available model first.')
            self.checked_routes, self.checked_until = None, 0
            self.cancelled = Event()
            self.pending_kind = 'model'
            self.state, self.detail = 'checking', ''
            if self.executor is None:
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-openrouter')
            self.future = self.executor.submit(self._model_check, owner, model, self.cancelled)
            return self.status()
        key = valid_key(args['key']) if operation == 'openrouter_connect' else None
        self.owner, self.result = owner, None
        self.cancelled = Event()
        self.pending_kind = 'connection'
        self.checked_routes, self.checked_until = None, 0
        self.state, self.detail = 'checking', ''
        if self.executor is None:
            self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-openrouter')
        self.future = self.executor.submit(self._check, owner, key, self.cancelled)
        return self.status()

    def close(self):
        self.invalidate()
        self.closed = True
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
        self.future = None
