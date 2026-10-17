"""Stream one host-validated installer into a private temporary file.

No installation, redirects, credentials in subprocess arguments or URL logging.
The main thread publishes the file only after checking the account activation.
"""
import hashlib
import os
from pathlib import Path
import time
from threading import RLock
from urllib.request import build_opener, HTTPSHandler, ProxyHandler, Request
from uuid import uuid4

import platform_os
from supabase_client import _NoRedirect, account_https_context


class DownloadError(Exception):
    pass


class PendingFile:
    def __init__(self, folder, filename):
        path = Path(folder)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            raise DownloadError('Choose an existing folder for the installer.')
        self.folder = path.resolve(strict=True)
        self.filename = filename
        self.directory = platform_os.open_dir(self.folder)
        self.temporary = '.dervo-download-' + uuid4().hex + '.part'
        self.closed = False
        self.detached = False
        self.cleanup_failed = False
        self.lock = RLock()
        try:
            # Refuse existing files and symlinks. Never replace another download.
            try:
                platform_os.stat_at(filename, self.directory)
            except FileNotFoundError:
                pass
            else:
                raise DownloadError('This installer already exists in that folder. Choose another folder or move the existing file.')
            self.fd = platform_os.open_file_at(self.temporary, self.directory, 'new', 0o600)
        except Exception:
            os.close(self.directory)
            self.closed = True
            raise

    def discard(self):
        with self.lock:
            if self.closed:
                return
            try:
                self.detach()
            finally:
                self.closed = True
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None
                os.close(self.directory)

    def detach(self):
        # Cancel can remove the name while the worker still owns the open file.
        # A killed worker then releases its disk space without leaving a .part.
        with self.lock:
            if self.closed or self.detached:
                return
            try:
                platform_os.unlink_at(self.temporary, self.directory)
            except FileNotFoundError:
                pass
            except OSError:
                self.cleanup_failed = True
                raise
            self.detached = True

    def publish(self):
        if self.closed or self.detached:
            raise DownloadError('This download is no longer available.')
        # Ensure the selected folder still names the directory we opened.
        current = os.stat(self.folder, follow_symlinks=False)
        opened = os.fstat(self.directory)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise DownloadError('The download folder changed. Choose it again.')
        # Same-volume hard-link creation refuses an existing destination atomically.
        platform_os.link_at(self.temporary, self.filename, self.directory)
        result = str(self.folder / self.filename)
        try:
            self.discard()
        except OSError:
            self.cleanup_failed = True
        return result


def fetch_installer(link, folder, cancelled, progress, *, opener=None, clock=time.monotonic):
    row = link.release
    filename = 'Dervo-' + row['release_id'] + '.' + row['format']
    output = PendingFile(folder, filename)
    progress['pending'] = output
    try:
        if platform_os.free_bytes(output.directory) < row['bytes'] + 64 * 1024 * 1024:
            raise DownloadError('There isn’t enough free space in that folder for this installer.')
        if cancelled.is_set():
            raise DownloadError('Download cancelled.')
        client = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect(),
                                                        HTTPSHandler(context=account_https_context()))
        started, total, digest = clock(), 0, hashlib.sha256()
        req = Request(link.url, headers={'Accept-Encoding': 'identity'}, method='GET')
        with client.open(req, timeout=10) as response:
            if (response.status != 200 or response.geturl() != link.url
                    or response.headers.get('Content-Length') != str(row['bytes'])
                    or response.headers.get('Content-Encoding', 'identity') != 'identity'):
                raise DownloadError('The installer response didn’t match the selected release.')
            while True:
                if cancelled.is_set():
                    raise DownloadError('Download cancelled.')
                if clock() - started >= 1800:
                    raise DownloadError('The download took too long. Try again.')
                # read1 returns after one buffered/socket read. A slow peer
                # can't keep filling a large read past cancellation/deadlines.
                chunk = response.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > row['bytes']:
                    raise DownloadError('The installer size didn’t match the selected release.')
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(output.fd, view)
                    if written <= 0:
                        raise DownloadError('The installer couldn’t be saved.')
                    view = view[written:]
                progress['received'] = total
            if total != row['bytes'] or digest.hexdigest() != row['sha256']:
                raise DownloadError('The installer checksum didn’t match. Nothing was installed. Try downloading again.')
            if cancelled.is_set():
                raise DownloadError('Download cancelled.')
            os.fsync(output.fd)
            os.close(output.fd)
            output.fd = None
        return output
    except Exception as error:
        output.discard()
        if isinstance(error, DownloadError):
            raise
        raise DownloadError('The installer couldn’t be downloaded. Check your connection and chosen folder, then retry.') from None
