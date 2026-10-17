"""Dervo's own saved sessions on Linux (the Dervo account, an OpenRouter or provider key): what the macOS Keychain holds
on a Mac (account_keychain.MacKeychain) and DPAPI files hold on Windows (windows_credentials.WindowsCredentials), with
the same names, limits and read, write and delete.

Each secret goes to the desktop's Secret Service (GNOME Keyring, KWallet 6 or KeePassXC) through libsecret's
``secret-tool``, under the same service name the Mac uses and a hash of the workspace as the account. The secret is
base64 text, because secret-tool reads a line of text. Nothing is ever written to a plain file: a machine with no Secret
Service can't save a session, and says so, the same as a Mac whose Keychain refuses. Reading on such a machine finds
nothing, since nothing could have been saved there.

The host runs with almost no environment (src/launch.rs gives Linux its desktop session's variables and a fixed PATH),
so secret-tool is run by its absolute path with only the variables it needs to reach the session bus.
Nothing is logged, and a failure is a KeychainError like the Mac's, so the callers don't change.
"""
import base64
import hashlib
import os
import re
import subprocess

from account_keychain import KeychainError

SECRET_TOOL_CANDIDATES = ('/usr/bin/secret-tool', '/usr/local/bin/secret-tool', '/bin/secret-tool')
# What secret-tool needs to find the session bus and, when the keyring is locked, ask the desktop to unlock it.
SESSION_VARIABLES = ('HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY')
TIMEOUT_SECONDS = 30  # long enough for the desktop's unlock prompt on a locked keyring


class LinuxSecretService:
    MAX_BYTES = 131072

    def __init__(self, *, project_ref, profile, workspace_id, purpose='account', tool=None):
        if (not isinstance(project_ref, str) or not re.fullmatch('[a-z0-9]{20}', project_ref)
                or profile not in ('private-beta', 'stable', 'personal-experimental')
                or type(workspace_id) is not str or not workspace_id
                or purpose not in ('account', 'openrouter', 'provider-key')
                or (purpose == 'openrouter' and profile != 'personal-experimental')):
            raise KeychainError()
        self.service = 'com.dervo.' + purpose + '.v1.' + project_ref + '.' + profile
        self.account = hashlib.sha256(workspace_id.encode()).hexdigest()
        self.tool = tool

    def _tool(self):
        if self.tool is not None:
            return self.tool
        return next((path for path in SECRET_TOOL_CANDIDATES if os.path.isfile(path) and os.access(path, os.X_OK)), None)

    def _run(self, tool, *args, data=None):
        env = {name: os.environ[name] for name in SESSION_VARIABLES if os.environ.get(name)}
        env.update(PATH='/usr/bin:/bin', LC_ALL='C.UTF-8')
        try:
            return subprocess.run([tool, *args, 'service', self.service, 'account', self.account], input=data,
                                  capture_output=True, timeout=TIMEOUT_SECONDS, env=env, stdin=None if data is not None
                                  else subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            raise KeychainError() from None

    def read(self):
        tool = self._tool()
        if tool is None:
            return None  # no Secret Service client here, so nothing was ever saved
        result = self._run(tool, 'lookup')
        if result.returncode != 0 or not result.stdout.strip():
            # secret-tool answers 1 with no output for a missing item. A locked keyring the user declined to unlock
            # reads the same way, which signs them out rather than failing every request.
            return None
        try:
            data = base64.b64decode(result.stdout.strip(), validate=True)
        except ValueError:
            raise KeychainError() from None
        if not 0 < len(data) <= self.MAX_BYTES:
            raise KeychainError()
        return data

    def write(self, data):
        if type(data) is not bytes or not 0 < len(data) <= self.MAX_BYTES:
            raise KeychainError()
        tool = self._tool()
        if tool is None:
            raise KeychainError()
        result = self._run(tool, 'store', '--label=Dervo (' + self.service.split('.')[2] + ')',
                           data=base64.b64encode(data))
        if result.returncode != 0:
            raise KeychainError()

    def delete(self):
        tool = self._tool()
        if tool is None:
            return
        # secret-tool clear answers 1 and says nothing when there was no item; anything it says is a real failure.
        result = self._run(tool, 'clear')
        if result.returncode != 0 and result.stderr.strip():
            raise KeychainError()

