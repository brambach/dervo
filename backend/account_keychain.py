"""Dervo-owned macOS generic-password storage.

Signed apps use the private native socket and migrate v1 items only after a verified
v2 save. Development hosts retain the legacy Python-owned store. Neither path
prompts from background operations or writes a plaintext fallback.
"""
from contextlib import contextmanager
import ctypes as C
import hashlib
import sys


class KeychainError(Exception):
    def __init__(self):
        # Each platform's own store by name: the Keychain, Windows' data protection, or the desktop keyring on Linux.
        where = ('Windows data protection' if sys.platform == 'win32' else
                 'your desktop keyring (Secret Service)' if sys.platform.startswith('linux') else 'macOS Keychain')
        super().__init__(f'Dervo couldn’t access its saved session in {where}. No plaintext copy was saved.')


class _Ref:
    def __init__(self, pointer):
        self.pointer = pointer


class LegacyMacKeychain:
    MAX_BYTES = 131072
    NOT_FOUND = -25300
    DUPLICATE = -25299

    def __init__(self, *, project_ref, profile, workspace_id, purpose='account'):
        import re
        if (not isinstance(project_ref, str) or not re.fullmatch('[a-z0-9]{20}', project_ref)
                or profile not in ('private-beta', 'stable', 'personal-experimental')
                or type(workspace_id) is not str or not workspace_id
                or purpose not in ('account', 'openrouter', 'provider-key')
                or (purpose == 'openrouter' and profile != 'personal-experimental')):
            raise KeychainError()
        if sys.platform != 'darwin':
            raise KeychainError()
        self.service = 'com.dervo.' + purpose + '.v1.' + project_ref + '.' + profile
        self.account = hashlib.sha256(workspace_id.encode()).hexdigest()
        try:
            self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
            signatures = {
                'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
                'CFDataCreate': ([C.c_void_p, C.c_void_p, C.c_long], C.c_void_p),
                'CFDictionaryCreateMutable': ([C.c_void_p, C.c_long, C.c_void_p, C.c_void_p], C.c_void_p),
                'CFDictionarySetValue': ([C.c_void_p, C.c_void_p, C.c_void_p], None),
                'CFRelease': ([C.c_void_p], None),
                'CFDataGetLength': ([C.c_void_p], C.c_long),
                'CFDataGetBytePtr': ([C.c_void_p], C.c_void_p),
                'CFGetTypeID': ([C.c_void_p], C.c_ulong),
                'CFDataGetTypeID': ([], C.c_ulong),
            }
            for name, (args, result) in signatures.items():
                fn = getattr(self.cf, name)
                fn.argtypes, fn.restype = args, result
            for name, args in {
                'SecItemCopyMatching': [C.c_void_p, C.POINTER(C.c_void_p)],
                'SecItemAdd': [C.c_void_p, C.POINTER(C.c_void_p)],
                'SecItemUpdate': [C.c_void_p, C.c_void_p],
                'SecItemDelete': [C.c_void_p],
            }.items():
                fn = getattr(self.sec, name)
                fn.argtypes, fn.restype = args, C.c_int32
            self.sec.SecKeychainSetUserInteractionAllowed.argtypes = [C.c_ubyte]
            self.sec.SecKeychainSetUserInteractionAllowed.restype = C.c_int32
            if self.sec.SecKeychainSetUserInteractionAllowed(0) != 0:
                raise KeychainError()
        except Exception:
            raise KeychainError() from None

    def _symbol(self, name, cf=False):
        return _Ref(C.c_void_p.in_dll(self.cf if cf else self.sec, name).value)

    def _base(self):
        return {
            'kSecClass': self._symbol('kSecClassGenericPassword'),
            'kSecAttrService': self.service,
            'kSecAttrAccount': self.account,
            'kSecAttrSynchronizable': self._symbol('kCFBooleanFalse', cf=True),
            'kSecUseAuthenticationUI': self._symbol('kSecUseAuthenticationUIFail'),
        }

    @contextmanager
    def _dictionary(self, fields):
        keys = C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryKeyCallBacks'))
        values = C.addressof(C.c_byte.in_dll(self.cf, 'kCFTypeDictionaryValueCallBacks'))
        result = self.cf.CFDictionaryCreateMutable(None, 0, keys, values)
        if not result:
            raise KeychainError()
        try:
            for key, value in fields.items():
                owned = not isinstance(value, _Ref)
                if isinstance(value, _Ref):
                    ref = value.pointer
                elif isinstance(value, bytes):
                    buffer = C.create_string_buffer(value)
                    ref = self.cf.CFDataCreate(None, buffer, len(value))
                elif isinstance(value, str):
                    ref = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
                else:
                    raise KeychainError()
                if not ref:
                    raise KeychainError()
                try:
                    self.cf.CFDictionarySetValue(result, self._symbol(key).pointer, ref)
                finally:
                    if owned:
                        self.cf.CFRelease(ref)
            yield result
        finally:
            self.cf.CFRelease(result)

    def read(self):
        fields = {**self._base(), 'kSecReturnData': self._symbol('kCFBooleanTrue', cf=True),
                  'kSecMatchLimit': self._symbol('kSecMatchLimitOne')}
        result = C.c_void_p()
        with self._dictionary(fields) as query:
            status = self.sec.SecItemCopyMatching(query, C.byref(result))
        if status == self.NOT_FOUND:
            return None
        try:
            if status != 0 or not result.value or self.cf.CFGetTypeID(result) != self.cf.CFDataGetTypeID():
                raise KeychainError()
            length = self.cf.CFDataGetLength(result)
            if not 0 < length <= self.MAX_BYTES:
                raise KeychainError()
            return C.string_at(self.cf.CFDataGetBytePtr(result), length)
        finally:
            if result.value:
                self.cf.CFRelease(result)

    def write(self, data):
        if type(data) is not bytes or not 0 < len(data) <= self.MAX_BYTES:
            raise KeychainError()
        with self._dictionary(self._base()) as query, self._dictionary({'kSecValueData': data}) as update:
            status = self.sec.SecItemUpdate(query, update)
            if status == self.NOT_FOUND:
                with self._dictionary({**self._base(), 'kSecValueData': data}) as create:
                    status = self.sec.SecItemAdd(create, None)
                if status == self.DUPLICATE:
                    status = self.sec.SecItemUpdate(query, update)
            if status != 0:
                raise KeychainError()

    def delete(self):
        with self._dictionary(self._base()) as query:
            status = self.sec.SecItemDelete(query)
        if status not in (0, self.NOT_FOUND):
            raise KeychainError()


class MacKeychain(LegacyMacKeychain):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from native_keychain import peer, NativeKeychain
        channel = peer()
        self.native = NativeKeychain(channel, self.service.replace('.v1.', '.v2.', 1), self.account,
                                     lambda: super(MacKeychain, self).read(),
                                     lambda: super(MacKeychain, self).delete()) if channel else None

    def read(self):
        return self.native.read() if self.native else super().read()

    def write(self, data):
        return self.native.write(data) if self.native else super().write(data)

    def delete(self):
        return self.native.delete() if self.native else super().delete()
