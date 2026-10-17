"""Account TLS regression. Uses fixture data and a local untrusted TLS server."""
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from supabase_client import AccountError, SupabaseClient, account_https_context
from test_supabase_client import HTTP, PROJECT


class AccountTLSTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux bundles')
    def test_linux_uses_the_distributions_bundle_without_environment_ca(self):
        import supabase_client
        with patch.dict(os.environ, {'SSL_CERT_FILE': '/fixture/missing.pem'}):
            with patch('supabase_client.ssl.create_default_context', wraps=ssl.create_default_context) as create:
                context = account_https_context()
        bundle = next(path for path in supabase_client.LINUX_CA_BUNDLES if os.path.isfile(path))
        create.assert_called_once_with(cafile=bundle)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(len(context.get_ca_certs()), 10, 'the system bundle loaded')

    @unittest.skipUnless(sys.platform == 'darwin', 'the Mac bundle at /etc/ssl/cert.pem')
    def test_macos_uses_system_bundle_without_installer_or_environment_ca(self):
        with patch('supabase_client.sys.platform', 'darwin'), patch.dict(os.environ, {'SSL_CERT_FILE': '/fixture/missing.pem'}):
            with patch('supabase_client.ssl.create_default_context', wraps=ssl.create_default_context) as create:
                context = account_https_context()
                create.assert_called_once_with(cafile='/etc/ssl/cert.pem')
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)

    def test_missing_or_invalid_system_bundle_fails_without_retry_or_unverified_context(self):
        for error in (FileNotFoundError('fixture path'), ssl.SSLError('fixture certificate')):
            with patch('supabase_client.ssl.create_default_context', side_effect=error) as create:
                with self.assertRaises(AccountError) as raised:
                    SupabaseClient(PROJECT)
                self.assertEqual(raised.exception.code, 'tls')
                self.assertNotIn('fixture', str(raised.exception))
                self.assertEqual(create.call_count, 1)

    def test_certificate_failures_are_distinct_and_sanitized_without_retry(self):
        for error in (ssl.SSLCertVerificationError('fixture-sensitive-value'),
                      URLError(ssl.SSLCertVerificationError('fixture-sensitive-value'))):
            http = HTTP(error)
            client = SupabaseClient(PROJECT, opener=http)
            with self.assertRaises(AccountError) as raised:
                client.send_code('fixture@example.test')
            self.assertEqual(raised.exception.code, 'tls')
            self.assertNotIn('fixture-sensitive-value', str(raised.exception))
            self.assertEqual(len(http.requests), 1)
        client = SupabaseClient(PROJECT, opener=HTTP(URLError('fixture-sensitive-value')))
        with self.assertRaises(AccountError) as raised:
            client.send_code('fixture@example.test')
        self.assertEqual(raised.exception.code, 'unavailable')

    def test_production_opener_installs_verified_context_and_no_environment_proxy(self):
        client = SupabaseClient(PROJECT)
        handlers = [handler for handler in client.opener.handlers if isinstance(handler, HTTPSHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertTrue(handlers[0]._context.check_hostname)
        self.assertEqual(handlers[0]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(any(isinstance(handler, ProxyHandler) and handler.proxies for handler in client.opener.handlers))

    @unittest.skipUnless(sys.platform == 'darwin', 'Native macOS certificate regression')
    def test_real_tls_handshake_rejects_untrusted_certificate_before_http(self):
        with tempfile.TemporaryDirectory(prefix='dervo-tls-fixture-') as folder:
            root = Path(folder)
            result = subprocess.run(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                                     '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'),
                                     '-days', '1', '-subj', '/CN=localhost'], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, 'Could not generate the local TLS fixture')
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(root/'cert.pem', root/'key.pem')
            listener = socket.socket()
            listener.bind(('127.0.0.1', 0));listener.listen(1);listener.settimeout(3)
            seen = []
            def serve():
                try:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(3)
                        with server_context.wrap_socket(conn, server_side=True) as secure:
                            seen.append(secure.recv(1024))
                except (ssl.SSLError, TimeoutError):
                    pass
            thread = threading.Thread(target=serve, daemon=True);thread.start()
            try:
                opener = build_opener(ProxyHandler({}), HTTPSHandler(context=account_https_context()))
                with self.assertRaises(URLError) as raised:
                    opener.open(Request('https://localhost:'+str(listener.getsockname()[1])+'/'), timeout=3)
                self.assertIsInstance(raised.exception.reason, ssl.SSLCertVerificationError)
            finally:
                thread.join(timeout=4);listener.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(seen, [], 'No HTTP data may reach an untrusted TLS peer')


if __name__ == '__main__':
    unittest.main()
