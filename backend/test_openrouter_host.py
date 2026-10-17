"""Offline lifecycle tests. No personal key, native Keychain or network is used."""
from concurrent.futures import Future
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

import account_config
from dervo_connections import Rejected
from openrouter_host import Connections
from release_profiles import stage
from supabase_client import PublicProject

KEY = 'sk-or-' + 'v1-' + 'fixture' * 8
SUBJECT = '12345678-1234-4234-8234-123456789abc'


class Executor:
    def submit(self, fn, *args):
        self.fn, self.args = fn, args
        self.future = Future()
        self.future.set_running_or_notify_cancel()
        return self.future

    def finish(self):
        try:
            self.future.set_result(self.fn(*self.args))
        except Exception as error:
            self.future.set_exception(error)
        self.fn, self.args = None, None

    def shutdown(self, **kwargs):
        if not self.future.done():
            self.finish()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        stage(self.root, 'personal-experimental')
        self.project = PublicProject('z'*20, 'https://'+'z'*20+'.supabase.co', 'sb_publishable_fixturefixture')
        account_config.stage(self.root, self.project)
        self.account = Mock()
        self.account.status.return_value = {'identity': {'issuer': self.project.url+'/auth/v1', 'subject': SUBJECT}}
        self.host = SimpleNamespace(root=self.root/'workspace', account_session=self.account)
        db = sqlite3.connect(self.root/'budget.sqlite3')
        self.addCleanup(db.close)
        self.host.store = SimpleNamespace(db=db)
        self.vault = Mock()
        self.vault.load.return_value = KEY
        self.vault_factory = Mock(return_value=self.vault)
        self.client = Mock()
        self.client.key_status.return_value = {'limitUsd': 5}
        self.client.models.return_value = [{'id': 'provider/model', 'contextTokens': 42}]
        self.client_factory = Mock(return_value=self.client)
        self.executor = Executor()
        self.manager = Connections(self.host, resources=self.root, vault_factory=self.vault_factory,
                                   client_factory=self.client_factory, executor=self.executor)

    def connect(self):
        return self.manager.dispatch('openrouter_connect', {'key': KEY, 'context': self.manager.status().get('context')})

    def finish(self):
        self.executor.finish()
        return self.manager.status()

    def test_new_key_only_saved_after_verified_completion(self):
        self.assertEqual(self.connect()['state'], 'checking')
        self.vault.save.assert_not_called()
        status = self.finish()
        self.assertEqual(status['state'], 'checked')
        self.vault.save.assert_called_once_with(KEY)
        self.assertNotIn(KEY, json.dumps(status))
        self.assertEqual(self.vault_factory.call_args.kwargs['user_id'], SUBJECT)
        self.assertEqual(self.vault_factory.call_args.kwargs['workspace'], self.host.root)

    def test_saved_key_checked_without_rewriting(self):
        self.manager.dispatch('openrouter_check', {'context': self.manager.status().get('context')})
        self.assertEqual(self.finish()['state'], 'checked')
        self.vault.load.assert_called_once_with()
        self.vault.save.assert_not_called()

    def test_beta_never_opens_vault_or_network(self):
        stage(self.root, 'private-beta')
        self.assertEqual(self.manager.status(), {'available': False})
        for operation, args in [('openrouter_connect', {'key': KEY, 'context': 'stale'}), ('openrouter_check', {'context': 'stale'}),
                                ('openrouter_disconnect', {'context': 'stale'})]:
            with self.assertRaises(ValueError): self.manager.dispatch(operation, args)
        self.vault_factory.assert_not_called()
        self.client_factory.assert_not_called()

    def test_no_signed_in_account_cannot_start(self):
        del self.host.account_session
        self.assertFalse(self.manager.status()['canCheck'])
        with self.assertRaises(Rejected): self.connect()
        self.vault_factory.assert_not_called()

    def test_foreign_issuer_cannot_start(self):
        self.account.status.return_value['identity']['issuer'] = 'https://foreign.invalid/auth/v1'
        with self.assertRaises(Rejected): self.connect()
        self.vault_factory.assert_not_called()

    def test_cancel_prevents_save_and_new_check_until_worker_finishes(self):
        self.connect()
        self.manager.dispatch('openrouter_cancel', {'context': self.manager.status().get('context')})
        with self.assertRaises(Rejected): self.connect()
        self.assertEqual(self.finish()['state'], 'unchecked')
        self.client_factory.assert_not_called()
        self.vault.save.assert_not_called()
        self.assertTrue(self.manager.status()['canCheck'])

    def test_cancel_after_worker_finishes_prevents_unconsumed_key_save(self):
        context = self.connect()['context']
        self.executor.finish()
        self.manager.dispatch('openrouter_cancel', {'context': context})
        self.vault.save.assert_not_called()
        self.assertIsNone(self.manager.status()['connection'])

    def test_disconnect_after_worker_finishes_never_saves_candidate_key(self):
        context = self.connect()['context']
        self.executor.finish()
        self.manager.dispatch('openrouter_disconnect', {'context': context})
        self.vault.save.assert_not_called()
        self.vault.delete.assert_called_once_with()

    def test_disconnect_during_network_check_drops_result(self):
        self.client.key_status.side_effect = lambda key: self.manager.dispatch('openrouter_disconnect', {'context': self.manager.status().get('context')})
        self.connect()
        self.assertEqual(self.finish()['state'], 'unchecked')
        self.vault.delete.assert_called_once_with()
        self.vault.save.assert_not_called()
        self.client.models.assert_not_called()

    def test_signout_after_network_completion_before_tick_discards_result(self):
        self.connect()
        self.executor.finish()
        self.account.require_access.side_effect = Rejected('signed out')
        self.assertEqual(self.manager.status()['state'], 'unchecked')
        self.vault.save.assert_not_called()

    def test_same_owner_reauthentication_invalidates_inflight_result(self):
        self.connect()
        self.executor.finish()
        self.manager.invalidate()
        self.assertEqual(self.manager.status()['state'], 'unchecked')
        self.vault.save.assert_not_called()

    def test_context_stable_while_idle_and_stale_form_cannot_connect(self):
        before = self.manager.status()['context']
        self.assertEqual(before, self.manager.status()['context'])
        self.account.status.return_value['identity']['subject'] = '22345678-1234-4234-8234-123456789abc'
        with self.assertRaises(Rejected):
            self.manager.dispatch('openrouter_connect', {'key': KEY, 'context': before})
        self.vault_factory.assert_not_called()
        self.assertNotEqual(before, self.manager.status()['context'])

    def test_budget_save_is_durable_and_uses_verified_owner_context(self):
        context = self.manager.status()['context']
        status = self.manager.dispatch('openrouter_budget_save', {'context': context, 'revision': 0, 'totalUsd': '5', 'perRequestUsd': '0.50'})
        self.assertEqual(status['budget']['totalUsd'], '5')
        recreated = Connections(self.host, resources=self.root)
        self.assertEqual(recreated.status()['budget']['perRequestUsd'], '0.5')
        self.manager.dispatch('openrouter_disconnect', {'context': context})
        self.assertEqual(self.manager.status()['budget']['totalUsd'], '5')
        self.account.status.return_value['identity']['subject'] = '22345678-1234-4234-8234-123456789abc'
        with self.assertRaises(Rejected):
            self.manager.dispatch('openrouter_budget_save', {'context': context, 'revision': 1, 'totalUsd': '10', 'perRequestUsd': '1'})
        self.assertFalse(self.manager.status()['budget']['configured'])
        self.client_factory.assert_not_called()
        self.vault.delete.assert_called_once_with()

    def prepare_routes(self):
        from test_openrouter_routes import endpoints, response, MODEL
        self.client.endpoints.return_value = endpoints(response(), MODEL, KEY)
        self.connect()
        context = self.finish()['context']
        self.manager.dispatch('openrouter_model_check', {'context':context, 'modelId':MODEL})
        return context, self.finish()

    def test_model_check_and_route_save_use_current_catalog_and_provider(self):
        context, status = self.prepare_routes()
        self.assertEqual(status['checkedRoutes']['modelId'], 'provider/model')
        selected = self.manager.dispatch('openrouter_route_save', {'context':context, 'revision':0,
            'modelId':'provider/model', 'provider':'provider', 'maxOutputTokens':1024})
        self.assertEqual(selected['routing']['selection']['provider'], 'provider')
        self.assertFalse(selected['routing']['selection']['allowFallbacks'])
        self.assertEqual(self.vault.save.call_count, 1)

    def test_model_removed_from_fresh_catalog_never_gets_endpoint_request(self):
        self.connect()
        context = self.finish()['context']
        self.client.models.return_value = []
        self.manager.dispatch('openrouter_model_check', {'context':context, 'modelId':'provider/model'})
        self.assertEqual(self.finish()['state'], 'error')
        self.client.endpoints.assert_not_called()

    def test_stale_endpoints_cannot_be_saved(self):
        now = [0]
        self.manager.clock = lambda: now[0]
        context, _ = self.prepare_routes()
        now[0] = 301
        with self.assertRaises(Rejected):
            self.manager.dispatch('openrouter_route_save', {'context':context, 'revision':0,
                'modelId':'provider/model', 'provider':'provider', 'maxOutputTokens':1024})
        self.assertIsNone(self.manager.status()['checkedRoutes'])

    def test_delayed_consumption_does_not_extend_freshness(self):
        from test_openrouter_routes import endpoints, response, MODEL
        now = [0]
        self.manager.clock = lambda: now[0]
        self.client.endpoints.return_value = endpoints(response(), MODEL, KEY)
        self.connect()
        context = self.finish()['context']
        self.manager.dispatch('openrouter_model_check', {'context':context, 'modelId':MODEL})
        self.executor.finish()
        now[0] = 301
        self.assertIsNone(self.manager.status()['checkedRoutes'])
        with self.assertRaises(Rejected):
            self.manager.dispatch('openrouter_route_save', {'context':context, 'revision':0,
                'modelId':MODEL, 'provider':'provider', 'maxOutputTokens':1024})

    def test_account_transition_during_endpoint_check_discards_routes(self):
        self.connect()
        context = self.finish()['context']
        self.client.endpoints.side_effect = lambda *args: self.manager.invalidate()
        self.manager.dispatch('openrouter_model_check', {'context':context, 'modelId':'provider/model'})
        self.assertIsNone(self.finish()['checkedRoutes'])

    def test_stale_cancel_and_disconnect_cannot_change_new_activation(self):
        before = self.manager.status()['context']
        self.manager.invalidate()
        after = self.manager.status()['context']
        for operation in ('openrouter_cancel', 'openrouter_disconnect', 'openrouter_check'):
            with self.assertRaises(Rejected): self.manager.dispatch(operation, {'context': before})
        self.assertEqual(after, self.manager.status()['context'])
        self.vault_factory.assert_not_called()

    def test_changed_owner_discards_result(self):
        self.connect()
        self.executor.finish()
        self.account.status.return_value['identity']['subject'] = '22345678-1234-4234-8234-123456789abc'
        self.assertIsNone(self.manager.status()['connection'])
        self.vault.save.assert_not_called()

    def test_profile_change_discards_result(self):
        self.connect()
        self.executor.finish()
        stage(self.root, 'private-beta')
        self.assertEqual(self.manager.status(), {'available': False})
        self.vault.save.assert_not_called()

    def test_failure_does_not_expose_provider_exception_or_replace_key(self):
        self.client.models.side_effect = RuntimeError(KEY)
        self.connect()
        status = self.finish()
        self.assertEqual(status['state'], 'error')
        self.assertNotIn(KEY, json.dumps(status))
        self.vault.save.assert_not_called()

    def test_keychain_failure_is_sanitized(self):
        self.vault.save.side_effect = RuntimeError(KEY)
        self.connect()
        status = self.finish()
        self.assertEqual(status['state'], 'error')
        self.assertIsNone(status['connection'])
        self.assertNotIn(KEY, json.dumps(status))

    def test_fields_cannot_select_identity_or_resources(self):
        for extra in ('user_id', 'resources', 'workspace', 'project_ref'):
            with self.assertRaises(Rejected):
                self.manager.dispatch('openrouter_connect', {'key': KEY, extra: 'override'})
        self.vault_factory.assert_not_called()

    def test_close_discards_pending_key(self):
        self.connect()
        self.manager.close()
        self.vault.save.assert_not_called()
        self.client_factory.assert_not_called()
        self.assertFalse(self.manager.status()['canCheck'])

    def test_cancel_while_loading_saved_key_never_starts_network(self):
        def load():
            self.manager.dispatch('openrouter_cancel', {'context': self.manager.status().get('context')})
            return KEY
        self.vault.load.side_effect = load
        self.manager.dispatch('openrouter_check', {'context': self.manager.status().get('context')})
        self.finish()
        self.client.key_status.assert_not_called()

    def test_close_does_not_wait_for_network_or_publish_late_results(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        entered, release = Event(), Event()
        def slow_check(key):
            entered.set()
            release.wait(3)
            return {'limitUsd': 5}
        self.client.key_status.side_effect = slow_check
        self.manager.executor = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(lambda: self.manager.executor.shutdown(wait=True))
        self.addCleanup(release.set)
        self.connect()
        self.assertTrue(entered.wait(1))
        self.manager.close()
        self.assertFalse(release.is_set())
        release.set()
        self.manager.executor.shutdown(wait=True)
        self.manager.tick()
        self.vault.save.assert_not_called()
        self.client.models.assert_not_called()
        self.assertIsNone(self.manager.status()['connection'])

    def test_host_photo_read_preserves_checked_connection(self):
        from host import Host
        host = Host(self.root/'test-host', account_runtime=lambda store: self.account)
        self.addCleanup(host.close)
        host.openrouter = self.manager
        self.connect()
        self.finish()
        host.dispatch('dervo_account_photo', {'subject': SUBJECT})
        self.assertEqual(host.dispatch('openrouter_status', {})['state'], 'checked')

    def test_host_routes_and_invalidates_before_account_action(self):
        from host import Host
        host = Host(self.root/'test-host', account_runtime=lambda store: self.account)
        self.addCleanup(host.close)
        host.openrouter = self.manager
        self.connect()
        self.executor.finish()
        host.dispatch('dervo_account_sign_out', {})
        self.assertEqual(host.dispatch('openrouter_status', {})['state'], 'unchecked')
        self.vault.save.assert_not_called()
        self.account.dispatch.assert_called_once_with('dervo_account_sign_out', {})
