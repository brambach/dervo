"""Disposable socket peers and migration state only. These tests never open Keychain."""
import json
import os
import socket
import threading
import unittest
from unittest.mock import Mock, patch
from account_keychain import KeychainError
from native_keychain import NativeKeychain, Peer


class MemoryPeer:
    def __init__(self):
        self.state, self.data = 'missing', None
        self.fail_write = False

    def call(self, op, service, account, data=None):
        if op == 'read':
            return {'state': self.state, **({'data': self.data.hex()} if self.state == 'found' else {})}
        if op == 'write':
            if self.fail_write: raise KeychainError()
            self.state, self.data = 'found', data
        elif op == 'delete':
            self.state, self.data = 'deleted', None
        return {'ok': True}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.peer = MemoryPeer()
        self.legacy_read = Mock(return_value=b'disposable test value')
        self.legacy_delete = Mock()
        self.store = NativeKeychain(self.peer, 'fixture', 'fixture', self.legacy_read, self.legacy_delete)

    def test_migration_reads_back_before_removing_legacy_and_never_reimports(self):
        self.legacy_delete.side_effect = lambda: self.assertEqual(self.peer.data, b'disposable test value')
        self.assertEqual(self.store.read(), b'disposable test value')
        self.assertEqual(self.store.read(), b'disposable test value')
        self.legacy_read.assert_called_once()
        self.assertEqual(self.legacy_delete.call_count, 2)

    def test_failed_save_keeps_legacy_and_can_retry(self):
        self.peer.fail_write = True
        with self.assertRaises(KeychainError): self.store.read()
        self.legacy_delete.assert_not_called()
        self.peer.fail_write = False
        self.assertEqual(self.store.read(), b'disposable test value')

    def test_interruption_after_save_finishes_cleanup_without_reading_old_value(self):
        self.legacy_delete.side_effect = KeychainError()
        with self.assertRaises(KeychainError): self.store.read()
        self.legacy_delete.side_effect = None
        self.legacy_read.side_effect = AssertionError('Must use the verified native item')
        self.assertEqual(self.store.read(), b'disposable test value')

    def test_sign_out_tombstone_prevents_resurrection_when_cleanup_fails(self):
        self.store.write(b'new value')
        self.legacy_delete.side_effect = KeychainError()
        with self.assertRaises(KeychainError): self.store.delete()
        self.legacy_delete.side_effect = None
        self.assertIsNone(self.store.read())
        self.legacy_read.assert_not_called()
        self.store.write(b'new sign in')
        self.assertEqual(self.store.read(), b'new sign in')

    def test_channel_failure_does_not_read_or_delete_legacy(self):
        self.peer.call = Mock(side_effect=KeychainError())
        with self.assertRaises(KeychainError): self.store.read()
        self.legacy_read.assert_not_called()
        self.legacy_delete.assert_not_called()

    def test_invalid_readback_keeps_legacy(self):
        self.peer.call = Mock(side_effect=[{'state': 'missing'}, {'ok': True}, {'state': 'found', 'data': '00'}])
        with self.assertRaises(KeychainError): self.store.read()
        self.legacy_delete.assert_not_called()

    def test_invalid_protocol_replies_fail_closed(self):
        for reply in ({'state': 'missing', 'data': '00'}, {'state': 'found', 'data': ''},
                      {'state': 'found', 'data': '00 01'}, {'state': 'found', 'data': 'GG'}, {'state': 'unknown'}):
            self.peer.call = Mock(return_value=reply)
            with self.assertRaises(KeychainError): self.store.read()
        self.legacy_read.assert_not_called()
        self.legacy_delete.assert_not_called()


class SocketTests(unittest.TestCase):
    def exchange(self, reply):
        client, server = socket.socketpair()
        peer = Peer(client.detach())
        self.addCleanup(peer.socket.close)
        self.addCleanup(peer.stream.close)
        self.assertFalse(os.get_inheritable(peer.socket.fileno()))
        def answer():
            with server, server.makefile('rwb') as stream:
                request = json.loads(stream.readline())
                self.assertEqual(request, {'op': 'read', 'service': 'fixture', 'account': 'fixture'})
                stream.write(reply)
                stream.flush()
        worker = threading.Thread(target=answer)
        worker.start()
        self.addCleanup(worker.join, 2)
        return peer

    def test_socket_roundtrip(self):
        self.assertEqual(self.exchange(b'{"state":"missing"}\n').call('read', 'fixture', 'fixture'), {'state': 'missing'})

    def test_eof_malformed_and_error_replies_permanently_close_channel(self):
        for reply in (b'', b'{}', b'[]\n', b'{"error":"no"}\n', b'not json\n'):
            peer = self.exchange(reply)
            with self.assertRaises(KeychainError): peer.call('read', 'fixture', 'fixture')
            with self.assertRaises(KeychainError): peer.call('read', 'fixture', 'fixture')
            self.assertTrue(peer.dead)

    def test_invalid_descriptor_never_means_legacy_fallback(self):
        import native_keychain
        with patch.object(native_keychain, '_peer', None), patch.dict(os.environ, {'DERVO_KEYCHAIN_FD': '-1'}):
            for _ in range(2):
                with self.assertRaises(KeychainError): native_keychain.peer()
                self.assertIn('DERVO_KEYCHAIN_FD', os.environ)


if __name__ == '__main__': unittest.main()


class CanaryTests(unittest.TestCase):
    def test_disposable_query_distinguishes_denial_missing_and_readable(self):
        from contextlib import nullcontext
        import keychain_canary
        store = Mock()
        store._base.return_value = {}
        store._dictionary.side_effect = lambda fields: nullcontext(None)
        with patch.object(keychain_canary, 'LegacyMacKeychain', return_value=store):
            for status, expected in [(-25308, 0), (-25293, 0), (-25300, 3), (0, 5), (-50, 4)]:
                store.sec.SecItemCopyMatching.return_value = status
                with self.subTest(status=status):
                    self.assertEqual(keychain_canary.main('0' * 24 + 'a' * 40), expected)
            store.reset_mock()
            self.assertEqual(keychain_canary.main('a' * 64), 2)
            store.sec.SecItemCopyMatching.assert_not_called()
