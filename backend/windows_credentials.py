"""Dervo's own saved sessions on Windows (the Dervo account, an OpenRouter or provider key): what the macOS Keychain
holds on a Mac (account_keychain.MacKeychain), with the same names, limits and read, write and delete.

Each secret is encrypted with Windows' per-user data protection (DPAPI, CryptProtectData, the same protection Credential
Manager uses underneath) and kept as one file in the account's app-data folder. Only the same Windows user on the same
PC can decrypt it; another user, or a copy of the file on another PC, can't. The service name is mixed in as extra
entropy, so a secret saved for one purpose never decrypts as another. Credential Manager itself holds 2,560 bytes at
most, and a session can be larger, which is why the store is a file.

Nothing is logged, and a failure is a KeychainError like the Mac's, so the callers don't change (b16/windows).
"""
import os
from pathlib import Path

from account_keychain import KeychainError
import platform_os


class WindowsCredentials:
    MAX_BYTES = 131072

    def __init__(self, *, project_ref, profile, workspace_id, purpose='account', folder=None):
        import hashlib
        import re
        if (not isinstance(project_ref, str) or not re.fullmatch('[a-z0-9]{20}', project_ref)
                or profile not in ('private-beta', 'stable', 'personal-experimental')
                or type(workspace_id) is not str or not workspace_id
                or purpose not in ('account', 'openrouter', 'provider-key')
                or (purpose == 'openrouter' and profile != 'personal-experimental')):
            raise KeychainError()
        if not platform_os.WINDOWS:
            raise KeychainError()
        self.service = 'com.dervo.' + purpose + '.v1.' + project_ref + '.' + profile
        self.account = hashlib.sha256(workspace_id.encode()).hexdigest()
        try:
            base = Path(folder) if folder is not None else platform_os.app_support_dir() / 'Dervo' / 'Credentials'
        except RuntimeError:
            raise KeychainError() from None
        self.path = base / (hashlib.sha256((self.service + '\n' + self.account).encode()).hexdigest() + '.bin')

    def read(self):
        try:
            sealed = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            raise KeychainError() from None
        data = _unprotect(sealed, self.service.encode())
        if not 0 < len(data) <= self.MAX_BYTES:
            raise KeychainError()
        return data

    def write(self, data):
        if type(data) is not bytes or not 0 < len(data) <= self.MAX_BYTES:
            raise KeychainError()
        sealed = _protect(data, self.service.encode())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            with open(temporary, 'wb') as handle:
                handle.write(sealed)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise KeychainError() from None

    def delete(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise KeychainError() from None


_BLOB = None


def _blob_type():
    """DATA_BLOB, defined once so every blob passed to one call has the same ctypes type."""
    global _BLOB
    if _BLOB is None:
        import ctypes
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]
        _BLOB = Blob
    return _BLOB


def _call(name, data, entropy):
    import ctypes
    Blob = _blob_type()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    data_buffer = ctypes.create_string_buffer(data, len(data))
    entropy_buffer = ctypes.create_string_buffer(entropy, len(entropy))
    source = Blob(len(data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_char)))
    extra = Blob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    result = Blob()
    function = getattr(crypt32, name)
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt from the host.
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result)):
        raise KeychainError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _protect(data, entropy):
    return _call('CryptProtectData', data, entropy)


def _unprotect(sealed, entropy):
    return _call('CryptUnprotectData', sealed, entropy)
