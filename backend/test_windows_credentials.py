"""The Windows store for Dervo's own saved sessions (windows_credentials.py). The round trip runs on Windows, in the
Windows build workflow's test step; a Mac only checks it refuses to act as the Keychain."""
import os
from pathlib import Path
import tempfile
import unittest

from account_keychain import KeychainError
from windows_credentials import WindowsCredentials

REF = 'abcdefghij0123456789'


@unittest.skipUnless(os.name == 'nt', 'Windows data protection')
class OnWindows(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, self.folder, True)

    def store(self, purpose='account', workspace='C:\\Users\\someone\\AppData\\Roaming\\local.dervo.connected'):
        return WindowsCredentials(project_ref=REF, profile='private-beta', workspace_id=workspace, purpose=purpose,
                                  folder=self.folder)

    def test_write_read_replace_delete(self):
        store = self.store()
        self.assertIsNone(store.read())
        secret = b'{"refresh_token": "' + b'x' * 4000 + b'"}'  # over Credential Manager's 2,560-byte limit
        store.write(secret)
        self.assertEqual(store.read(), secret)
        self.assertEqual(self.store().read(), secret, 'a new instance for the same workspace reads it back')
        store.write(b'second')
        self.assertEqual(store.read(), b'second')
        store.delete()
        self.assertIsNone(store.read())
        store.delete()  # deleting nothing is fine, as on a Mac

    def test_the_file_is_not_the_secret(self):
        store = self.store()
        store.write(b'a-plain-refresh-token-value')
        sealed = store.path.read_bytes()
        self.assertNotIn(b'a-plain-refresh-token-value', sealed)
        self.assertEqual(list(self.folder.iterdir()), [store.path], 'one file, no temporary left behind')

    def test_a_secret_is_bound_to_its_purpose_and_workspace(self):
        account = self.store()
        account.write(b'account-session')
        other_workspace = self.store(workspace='C:\\elsewhere')
        self.assertIsNone(other_workspace.read(), 'another workspace has its own file')
        provider = self.store(purpose='provider-key')
        provider.path.write_bytes(account.path.read_bytes())
        # Negative control: the account's sealed bytes don't open as a provider key, because the service is the entropy.
        with self.assertRaises(KeychainError):
            provider.read()

    def test_limits_match_the_mac(self):
        store = self.store()
        with self.assertRaises(KeychainError):
            store.write(b'')
        with self.assertRaises(KeychainError):
            store.write(b'x' * (WindowsCredentials.MAX_BYTES + 1))
        with self.assertRaises(KeychainError):
            store.write('text, not bytes')


class Everywhere(unittest.TestCase):
    def test_names_are_checked_like_the_keychain(self):
        for bad in (dict(project_ref='short'), dict(profile='nightly'), dict(workspace_id=''), dict(purpose='mail'),
                    dict(purpose='openrouter')):
            args = dict(project_ref=REF, profile='private-beta', workspace_id='w', purpose='account', **{})
            args.update(bad)
            with self.assertRaises(KeychainError, msg=repr(bad)):
                WindowsCredentials(**args)

    @unittest.skipIf(os.name == 'nt', 'the Mac side')
    def test_off_windows_it_refuses_rather_than_standing_in_for_the_keychain(self):
        with self.assertRaises(KeychainError):
            WindowsCredentials(project_ref=REF, profile='private-beta', workspace_id='w')


if __name__ == '__main__':
    unittest.main()
