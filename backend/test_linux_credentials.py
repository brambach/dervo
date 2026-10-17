"""The Linux store for Dervo's own saved sessions (linux_credentials.py).

The round trip runs against a real Secret Service when one answers: the Linux build workflow starts GNOME Keyring in a
private D-Bus session for it (DERVO_TEST_SECRET_SERVICE=1). Everywhere else a stand-in secret-tool checks the argv,
the environment and what reaches the store, and a machine without secret-tool is checked to save nothing."""
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from account_keychain import KeychainError
import linux_credentials
from linux_credentials import LinuxSecretService

REF = 'abcdefghij0123456789'
WORKSPACE = '/home/someone/.local/share/local.dervo.connected'

# A stand-in secret-tool: keeps each item as a file named after its attributes, and records its argv and environment.
FAKE = r'''#!/bin/sh
dir=$(dirname "$0")
printf '%s\n' "$*" >> "$dir/argv"
env | sort > "$dir/env"
command=$1; shift
[ "$command" = store ] && shift
key=$(printf '%s' "$*" | tr ' /' '__')
case $command in
  store) cat > "$dir/item-$key" ;;
  lookup) [ -f "$dir/item-$key" ] || exit 1; cat "$dir/item-$key" ;;
  clear) rm -f "$dir/item-$key" ;;
esac
'''


@unittest.skipIf(os.name == 'nt', 'a POSIX shell stand-in')
class WithAStandIn(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(__import__('shutil').rmtree, self.folder, True)
        self.tool = self.folder / 'secret-tool'
        self.tool.write_text(FAKE)
        self.tool.chmod(self.tool.stat().st_mode | stat.S_IXUSR)

    def store(self, purpose='account', workspace=WORKSPACE):
        return LinuxSecretService(project_ref=REF, profile='private-beta', workspace_id=workspace, purpose=purpose,
                                  tool=str(self.tool))

    def test_write_read_replace_delete(self):
        store = self.store()
        self.assertIsNone(store.read())
        secret = b'{"refresh_token": "' + b'x' * 4000 + b'"}\n\x00binary'
        store.write(secret)
        self.assertEqual(store.read(), secret)
        self.assertEqual(self.store().read(), secret, 'a new instance for the same workspace reads it back')
        store.write(b'second')
        self.assertEqual(store.read(), b'second')
        store.delete()
        self.assertIsNone(store.read())
        store.delete()  # deleting nothing is fine, as on a Mac

    def test_purposes_and_workspaces_never_share_an_item(self):
        self.store().write(b'account secret')
        self.assertIsNone(self.store(purpose='provider-key').read())
        self.assertIsNone(self.store(workspace='/elsewhere').read())

    def test_the_item_is_named_like_the_mac_and_the_secret_never_reaches_argv(self):
        self.store().write(b'a-plain-refresh-token-value')
        argv = (self.folder / 'argv').read_text()
        self.assertIn('service com.dervo.account.v1.' + REF + '.private-beta account ', argv)
        self.assertNotIn('a-plain-refresh-token-value', argv)
        stored = next(self.folder.glob('item-*')).read_bytes()
        self.assertNotIn(b'a-plain-refresh-token-value', stored, 'base64 on the way in, as text secret-tool keeps')

    def test_only_the_session_variables_reach_secret_tool(self):
        with patch.dict(os.environ, {'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
                                     'ANTHROPIC_API_KEY': 'sk-not-for-the-keyring', 'XDG_RUNTIME_DIR': '/run/user/1000'}):
            self.store().read()
        env = (self.folder / 'env').read_text()
        self.assertIn('DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus', env)
        self.assertIn('XDG_RUNTIME_DIR=/run/user/1000', env)
        self.assertNotIn('ANTHROPIC_API_KEY', env)

    def test_a_refused_store_is_a_keychain_error_and_nothing_is_written_elsewhere(self):
        self.tool.write_text('#!/bin/sh\nexit 1\n')
        with self.assertRaises(KeychainError) as caught:
            self.store().write(b'secret')
        if sys.platform.startswith('linux'):
            self.assertIn('desktop keyring', str(caught.exception))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['secret-tool'])

    def test_limits_and_names_match_the_mac(self):
        with self.assertRaises(KeychainError):
            self.store().write(b'')
        with self.assertRaises(KeychainError):
            self.store().write(b'x' * (LinuxSecretService.MAX_BYTES + 1))
        with self.assertRaises(KeychainError):
            LinuxSecretService(project_ref='short', profile='private-beta', workspace_id=WORKSPACE)
        with self.assertRaises(KeychainError):
            LinuxSecretService(project_ref=REF, profile='private-beta', workspace_id=WORKSPACE, purpose='openrouter')


class WithoutSecretTool(unittest.TestCase):
    def test_reading_finds_nothing_and_saving_refuses(self):
        with patch.object(linux_credentials, 'SECRET_TOOL_CANDIDATES', ('/nonexistent/secret-tool',)):
            store = LinuxSecretService(project_ref=REF, profile='private-beta', workspace_id=WORKSPACE)
            self.assertIsNone(store.read())
            store.delete()
            with self.assertRaises(KeychainError):
                store.write(b'secret')


@unittest.skipUnless(os.environ.get('DERVO_TEST_SECRET_SERVICE') == '1', 'needs a running Secret Service (CI starts one)')
class WithARealSecretService(unittest.TestCase):
    def test_round_trip_through_the_desktop_keyring(self):
        store = LinuxSecretService(project_ref=REF, profile='private-beta', workspace_id=tempfile.mkdtemp())
        self.addCleanup(store.delete)
        self.assertIsNone(store.read())
        store.write(b'{"refresh_token": "' + b'y' * 6000 + b'"}')
        self.assertEqual(store.read(), b'{"refresh_token": "' + b'y' * 6000 + b'"}')
        store.delete()
        self.assertIsNone(store.read())


if __name__ == '__main__':
    unittest.main()
