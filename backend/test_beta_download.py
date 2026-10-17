import hashlib
import io
from pathlib import Path
from threading import Event
import tempfile
import unittest
from types import SimpleNamespace

from beta_download import DownloadError, fetch_installer
from beta_updates import SignedDownload


class Response(io.BytesIO):
    status = 200
    def __init__(self, data, url, headers=None):
        super().__init__(data)
        self.url = url
        self.headers = headers if headers is not None else {'Content-Length': str(len(data))}
    def geturl(self):
        return self.url


class Downloads(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.data = b'example installer bytes' * 6000
        self.link = SignedDownload('https://fixture.invalid/private-link', {
            'release_id': 'trial.9', 'format': 'zip', 'bytes': len(self.data),
            'sha256': hashlib.sha256(self.data).hexdigest()}, 9999999999)
        self.event, self.progress = Event(), {'received': 0}
        self.requests = []

    def opener(self, response):
        def open(request, timeout):
            self.requests.append(request)
            self.assertEqual(timeout, 10)
            self.assertNotIn('Authorization', request.headers)
            return response
        return SimpleNamespace(open=open)

    def fetch(self, response=None, **kwargs):
        return fetch_installer(self.link, self.folder, self.event, self.progress,
                opener=self.opener(response or Response(self.data, self.link.url)), **kwargs)

    def test_bytes_are_verified_before_main_thread_publication(self):
        pending = self.fetch()
        self.assertFalse((self.folder/'Dervo-trial.9.zip').exists())
        self.assertEqual(self.progress['received'], len(self.data))
        saved = Path(pending.publish())
        self.assertEqual(saved.read_bytes(), self.data)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.resolve() for p in self.folder.iterdir()], [saved])
        pending.discard()

    def test_short_oversized_corrupt_or_redirected_downloads_leave_no_file(self):
        for data, url, headers in [
                (self.data[:-1], self.link.url, {'Content-Length': str(len(self.data))}),
                (self.data+b'x', self.link.url, {'Content-Length': str(len(self.data))}),
                (b'x'*len(self.data), self.link.url, {'Content-Length': str(len(self.data))}),
                (self.data, 'https://evil.invalid', {'Content-Length': str(len(self.data))}),
                (self.data, self.link.url, {}),
                (self.data, self.link.url, {'Content-Length': str(len(self.data)), 'Content-Encoding': 'gzip'})]:
            with self.assertRaises(DownloadError):
                self.fetch(Response(data, url, headers))
            self.assertEqual(list(self.folder.iterdir()), [])

    def test_existing_file_or_symlink_is_never_overwritten(self):
        target = self.folder/'Dervo-trial.9.zip'
        target.write_text('keep')
        with self.assertRaises(DownloadError): self.fetch()
        self.assertEqual(target.read_text(), 'keep')
        target.unlink(); target.symlink_to(self.folder/'missing')
        with self.assertRaises(DownloadError): self.fetch()
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.requests, [])

    def test_destination_created_during_download_wins(self):
        pending = self.fetch()
        target = self.folder/'Dervo-trial.9.zip'; target.write_text('keep')
        try:
            with self.assertRaises(FileExistsError): pending.publish()
        finally: pending.discard()
        self.assertEqual(target.read_text(), 'keep')
        self.assertEqual(list(self.folder.iterdir()), [target])

    def test_cancel_and_deadline_remove_partial_data(self):
        response = Response(self.data, self.link.url)
        original = response.read1
        def read(size):
            value = original(size); self.event.set(); return value
        response.read1 = read
        with self.assertRaisesRegex(DownloadError, 'cancelled'): self.fetch(response)
        self.assertEqual(list(self.folder.iterdir()), [])
        self.event.clear()
        clock = iter([0, 1801])
        with self.assertRaisesRegex(DownloadError, 'too long'): self.fetch(clock=lambda: next(clock))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_folder_replacement_cannot_redirect_publication(self):
        pending = self.fetch()
        moved = self.folder.with_name(self.folder.name+'-moved')
        self.folder.rename(moved); self.folder.mkdir()
        try:
            with self.assertRaises(DownloadError): pending.publish()
        finally:
            pending.discard(); moved.rmdir()
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_network_errors_never_echo_private_urls(self):
        def fail(*a, **kw): raise RuntimeError(self.link.url)
        with self.assertRaises(DownloadError) as error:
            fetch_installer(self.link, self.folder, self.event, self.progress, opener=SimpleNamespace(open=fail))
        self.assertNotIn(self.link.url, str(error.exception))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_cancellation_unlinks_open_partial_without_closing_worker_descriptor(self):
        from beta_download import PendingFile
        import os
        pending = PendingFile(self.folder, 'Dervo-trial.zip')
        try:
            descriptor = pending.fd
            pending.detach()
            self.assertEqual(list(self.folder.iterdir()), [])
            os.write(descriptor, b'worker still owns this descriptor')
            with self.assertRaises(DownloadError): pending.publish()
        finally: pending.discard()
