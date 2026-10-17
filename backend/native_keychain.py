"""Client for the signed app's inherited socket. A channel failure never falls back to Python storage."""
from contextlib import suppress
import json
import os
import socket
import threading

LIMIT = 131072
WIRE_LIMIT = LIMIT * 2 + 2048
_peer = None
_setup = threading.Lock()


def failed():
    from account_keychain import KeychainError
    return KeychainError()


class Peer:
    def __init__(self, fd):
        self.lock = threading.Lock()
        self.dead = False
        try:
            fd = int(fd)
            if fd < 3: raise ValueError()
            os.set_inheritable(fd, False)
            self.socket = socket.socket(fileno=fd)
            self.socket.settimeout(5)
            self.stream = self.socket.makefile('rwb')
        except Exception:
            self.dead = True
            raise failed() from None

    def call(self, operation, service, account, data=None):
        request = dict(op=operation, service=service, account=account)
        if data is not None: request['data'] = data.hex()
        with self.lock:
            if self.dead: raise failed()
            try:
                encoded = (json.dumps(request, separators=(',', ':')) + '\n').encode()
                if len(encoded) > WIRE_LIMIT: raise ValueError()
                self.stream.write(encoded)
                self.stream.flush()
                line = self.stream.readline(WIRE_LIMIT + 1)
                if len(line) > WIRE_LIMIT or not line.endswith(b'\n'): raise ValueError()
                reply = json.loads(line)
                if type(reply) is not dict or 'error' in reply: raise ValueError()
                return reply
            except Exception:
                self.dead = True
                with suppress(Exception): self.stream.close()
                with suppress(Exception): self.socket.close()
                raise failed() from None


def peer():
    global _peer
    with _setup:
        if _peer is not None: return _peer
        fd = os.environ.get('DERVO_KEYCHAIN_FD')
        if fd is None: return None
        # Keep the variable on setup failure so the next request also fails closed.
        _peer = Peer(fd)
        os.environ.pop('DERVO_KEYCHAIN_FD', None)
        return _peer


class NativeKeychain:
    def __init__(self, peer, service, account, legacy_read, legacy_delete):
        self.peer, self.service, self.account = peer, service, account
        self.legacy_read, self.legacy_delete = legacy_read, legacy_delete

    def _read(self):
        reply = self.peer.call('read', self.service, self.account)
        state = reply.get('state')
        if state in ('missing', 'deleted') and set(reply) == {'state'}: return state, None
        if state != 'found' or set(reply) != {'state', 'data'} or type(reply['data']) is not str:
            raise failed()
        try:
            if not 2 <= len(reply['data']) <= LIMIT * 2 or any(c not in '0123456789abcdef' for c in reply['data']): raise ValueError()
            data = bytes.fromhex(reply['data'])
            if not 0 < len(data) <= LIMIT: raise ValueError()
            return state, data
        except ValueError: raise failed() from None

    def _write(self, data):
        if type(data) is not bytes or not 0 < len(data) <= LIMIT: raise failed()
        if self.peer.call('write', self.service, self.account, data) != {'ok': True}: raise failed()
        if self._read() != ('found', data): raise failed()

    def read(self):
        state, value = self._read()
        if state == 'missing':
            value = self.legacy_read()
            if value is None: return None
            self._write(value)
        # Also finish deletion after interruption between a verified native save and legacy deletion.
        self.legacy_delete()
        return value

    def write(self, data):
        self._write(data)
        self.legacy_delete()

    def delete(self):
        # The native tombstone prevents re-import if deletion of v1 is interrupted.
        if self.peer.call('delete', self.service, self.account) != {'ok': True}: raise failed()
        if self._read() != ('deleted', None): raise failed()
        self.legacy_delete()
