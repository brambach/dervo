"""Keychain integration is opt-in and touches only randomly named Dervo canaries."""
import os
import secrets
import sys
import unittest
import uuid

from account_keychain import KeychainError, MacKeychain, LegacyMacKeychain
from unittest.mock import Mock, patch


class KeychainInputTests(unittest.TestCase):
    def test_legacy_no_prompt_mode_is_required_before_any_query(self):
        for status in (0, -50):
            framework = Mock()
            framework.SecKeychainSetUserInteractionAllowed.return_value = status
            with self.subTest(status=status), patch('account_keychain.sys.platform', 'darwin'), patch('account_keychain.C.CDLL', return_value=framework):
                scope = dict(project_ref='z' * 20, profile='private-beta', workspace_id='fixture')
                if status:
                    with self.assertRaises(KeychainError): LegacyMacKeychain(**scope)
                else: LegacyMacKeychain(**scope)
                framework.SecKeychainSetUserInteractionAllowed.assert_called_once_with(0)
                framework.SecItemCopyMatching.assert_not_called()

    def test_invalid_scope_is_rejected_before_native_access(self):
        for changes in [{'project_ref': 'bad'}, {'profile': 'unknown'}, {'workspace_id': ''},
                        {'purpose': 'unknown'}, {'purpose': 'openrouter'}]:
            with self.assertRaises(KeychainError):
                MacKeychain(**{'project_ref': 'z' * 20, 'profile': 'private-beta',
                               'workspace_id': 'fixture', **changes})


@unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('DERVO_KEYCHAIN_CANARY') == '1',
                     'Explicit disposable macOS Keychain test only')
class KeychainCanaryTests(unittest.TestCase):
    def test_openrouter_namespace_is_separate_and_durable(self):
        scope = dict(project_ref='z' * 20, profile='personal-experimental',
                     workspace_id='disposable-openrouter-canary-' + uuid.uuid4().hex)
        account = MacKeychain(**scope)
        router = MacKeychain(**scope, purpose='openrouter')
        try:
            self.assertIsNone(account.read())
            self.assertIsNone(router.read())
            data = secrets.token_bytes(32)
            router.write(data)
            self.assertTrue(MacKeychain(**scope, purpose='openrouter').read() == data)
            self.assertIsNone(account.read())
            router.delete()
            self.assertIsNone(router.read())
        finally:
            router.delete()
            account.delete()

    def test_roundtrip_new_instance_rotation_scope_isolation_and_removal(self):
        scope = dict(project_ref='z' * 20, profile='private-beta',
                     workspace_id='disposable-canary-' + uuid.uuid4().hex)
        store = MacKeychain(**scope)
        try:
            self.assertIsNone(store.read())
            first, second = secrets.token_bytes(32), secrets.token_bytes(48)
            store.write(first)
            # Boolean assertions prevent the test runner from rendering values on failure.
            self.assertTrue(MacKeychain(**scope).read() == first, 'Saved item could not be recovered')
            for change in [{'project_ref': 'y' * 20}, {'profile': 'personal-experimental'},
                           {'workspace_id': scope['workspace_id'] + '-other'}]:
                self.assertIsNone(MacKeychain(**{**scope, **change}).read())
            store.write(second)
            self.assertTrue(MacKeychain(**scope).read() == second, 'Replacement could not be recovered')
            with self.assertRaises(KeychainError):
                store.write(b'')
            with self.assertRaises(KeychainError):
                store.write(b'x' * (MacKeychain.MAX_BYTES + 1))
            self.assertTrue(store.read() == second, 'Rejected write changed the saved item')
            store.delete()
            self.assertIsNone(MacKeychain(**scope).read())
            store.delete()
        finally:
            store.delete()


if __name__ == '__main__':
    unittest.main()
