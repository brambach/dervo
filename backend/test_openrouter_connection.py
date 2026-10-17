"""Offline controls for the personal profile's read-only OpenRouter connection."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

import release_profiles
from openrouter_connection import ConnectionError, KeyVault, NoRedirect, ReadOnlyClient

KEY = 'sk-or-' + 'v1-' + 'fixture' * 8
USER = '12345678-1234-4234-8234-123456789abc'


class Response:
    status = 200
    def __init__(self, value): self.raw = json.dumps(value).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, count): return self.raw[:count]


def key_data(**changes):
    return {'data': {'is_management_key': False, 'is_provisioning_key': False,
                     'limit': 5, 'limit_remaining': 4, 'usage': 1, 'usage_daily': .5,
                     'usage_monthly': 1, 'label': KEY, 'creator_user_id': 'private-owner', **changes}}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        release_profiles.stage(self.root, 'personal-experimental')
        self.opener = Mock()
        self.client = ReadOnlyClient(self.root, opener=self.opener)

    def test_beta_blocks_network_and_keychain_before_access(self):
        release_profiles.stage(self.root, 'private-beta')
        factory = Mock()
        with self.assertRaises(ValueError):
            ReadOnlyClient(self.root, opener=self.opener)
        with self.assertRaises(ValueError):
            KeyVault(resources=self.root, project_ref='z'*20, workspace=self.root, user_id=USER,
                     keychain_factory=factory)
        self.opener.open.assert_not_called()
        factory.assert_not_called()

    def test_endpoint_get_uses_fixed_origin_and_exact_validated_model(self):
        from test_openrouter_routes import response, MODEL
        self.opener.open.return_value = Response({'data': response()})
        result = self.client.endpoints(KEY, MODEL)
        self.assertEqual(result['modelId'], MODEL)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/models/provider/model/endpoints')
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.opener.open.reset_mock()
        with self.assertRaises(ValueError): self.client.endpoints(KEY, '../key')
        self.opener.open.assert_not_called()

    def test_rechecks_profile_before_each_existing_client_request(self):
        release_profiles.stage(self.root, 'private-beta')
        with self.assertRaises(ValueError): self.client.key_status(KEY)
        self.opener.open.assert_not_called()

    def test_key_check_only_returns_allowlisted_numeric_fields(self):
        self.opener.open.return_value = Response(key_data())
        result = self.client.key_status(KEY)
        self.assertEqual(result, {'limitUsd': 5, 'remainingUsd': 4, 'usedUsd': 1,
                                  'usedTodayUsd': .5, 'usedThisMonthUsd': 1})
        self.assertTrue(KEY not in json.dumps(result), 'Credential reached the public result')
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/key')
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertTrue(request.get_header('Authorization') == 'Bearer ' + KEY)

    def test_privileged_or_unknown_key_role_never_passes(self):
        for field in ('is_management_key', 'is_provisioning_key'):
            for value in (True, None, 0, 'false'):
                self.opener.open.return_value = Response(key_data(**{field: value}))
                with self.assertRaises(ConnectionError) as caught: self.client.key_status(KEY)
                self.assertEqual(caught.exception.code, 'permissions')

    def test_unknown_budget_stays_unknown_and_invalid_money_fails(self):
        self.opener.open.return_value = Response(key_data(limit=None, limit_remaining=None))
        self.assertIsNone(self.client.key_status(KEY)['remainingUsd'])
        for value in (True, -1, float('nan'), float('inf'), '5', 10**400):
            self.opener.open.return_value = Response(key_data(usage=value))
            with self.assertRaises(ConnectionError): self.client.key_status(KEY)

    def test_models_use_user_filtered_endpoint_and_return_no_descriptions(self):
        self.opener.open.return_value = Response({'data': [{'id': 'example/model-one',
            'context_length': 4096, 'description': KEY, 'name': KEY}]})
        self.assertEqual(self.client.models(KEY), [{'id': 'example/model-one', 'contextTokens': 4096}])
        self.assertEqual(self.opener.open.call_args.args[0].full_url,
                         'https://openrouter.ai/api/v1/models/user')

    def test_bad_catalogs_fail_instead_of_silently_dropping_entries(self):
        for rows in ({}, [None], [{'id':'bad','context_length':1}],
                     [{'id':'a/b','context_length':True}],
                     [{'id':'a/b','context_length':1}]*2,
                     [{'id':'a/'+KEY,'context_length':1}]):
            self.opener.open.return_value = Response({'data':rows})
            with self.assertRaises(ConnectionError): self.client.models(KEY)

    def test_partial_catalog_does_not_claim_all_models_are_available(self):
        self.opener.open.return_value = Response({'data': [], 'total_count': 12})
        with self.assertRaises(ConnectionError): self.client.models(KEY)

    def test_error_text_never_echoes_provider_credentials(self):
        for status, code in [(401, 'unauthorized'), (403, 'unauthorized'), (429, 'rate_limited'),
                             (302, 'unavailable'), (500, 'unavailable')]:
            self.opener.open.side_effect = HTTPError('https://example.invalid/'+KEY, status,
                                                    KEY, {}, BytesIO(KEY.encode()))
            with self.assertRaises(ConnectionError) as caught: self.client.key_status(KEY)
            self.assertEqual(caught.exception.code, code)
            self.assertTrue(KEY not in str(caught.exception), 'Credential reached the error')
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://example.invalid'))

    def test_no_completion_path_or_invalid_key_can_send(self):
        for key in ('', 'bad', KEY+'\n', True):
            with self.assertRaises(ConnectionError): self.client.key_status(key)
        with self.assertRaises(ConnectionError): self.client._get('/chat/completions', KEY)
        self.opener.open.assert_not_called()

    def test_response_limits_and_malformed_json_fail(self):
        response = Response({})
        self.opener.open.return_value = response
        for raw in (b'{', b'null', b'[]', b'{"data":null}', b' '*(self.client.MAX_RESPONSE+1)):
            response.raw = raw
            with self.assertRaises(ConnectionError): self.client.key_status(KEY)

    def test_vault_scope_and_profile_revalidation(self):
        factory = Mock()
        vault = KeyVault(resources=self.root, project_ref='z'*20, workspace=self.root, user_id=USER,
                         keychain_factory=factory)
        self.assertEqual(factory.call_args.kwargs['purpose'], 'openrouter')
        self.assertEqual(factory.call_args.kwargs['profile'], 'personal-experimental')
        self.assertEqual(json.loads(factory.call_args.kwargs['workspace_id']), [str(self.root.resolve()), USER])
        store = factory.return_value
        store.read.return_value = None
        self.assertIsNone(vault.load())
        vault.save(KEY)
        self.assertTrue(store.write.call_args.args[0] == KEY.encode())
        store.read.return_value = KEY.encode()
        self.assertTrue(vault.load() == KEY)
        release_profiles.stage(self.root, 'private-beta')
        store.reset_mock()
        for action in (vault.load, lambda: vault.save(KEY), vault.delete):
            with self.assertRaises(ValueError): action()
        self.assertEqual(store.mock_calls, [])

    def test_bad_owner_never_opens_keychain(self):
        factory = Mock()
        with self.assertRaises(ConnectionError):
            KeyVault(resources=self.root, project_ref='z'*20, workspace=self.root, user_id='bad',
                     keychain_factory=factory)
        factory.assert_not_called()


if __name__ == '__main__': unittest.main()
