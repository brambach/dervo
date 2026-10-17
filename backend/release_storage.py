"""Owned preview staging and one locked compiler cache per source project."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform_os
import shutil
import tarfile
import time

MIN_FREE_BYTES = 6 * 1024 ** 3


def normal_path(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise RuntimeError('Linked build storage is not allowed. Choose a normal folder.')
    return path


def require_space(path, minimum=MIN_FREE_BYTES):
    path = Path(path)
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free < minimum:
        raise RuntimeError(
            f'This preview needs at least {minimum / 1024 ** 3:.0f} GB free to start. '
            f'Only {free / 1024 ** 3:.1f} GB is available. Free some space and try again.')
    return free


class BuildStorage:
    def __init__(self, source, root=None):
        self.source = Path(source).resolve()
        base = root if root is not None else Path.home() / 'Library/Caches/Dervo/previews'
        self.base = normal_path(base)
        key = hashlib.sha256(str(self.source).encode()).hexdigest()[:24]
        self.directory = self.base / key
        self.target = self.directory / 'target'
        self.work = self.directory / 'source'
        self.locked = False
        self.lock_fd = None

    @contextmanager
    def acquire(self):
        normal_path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # Keep the inode in place. Unlinking a lock permits two owners after a crash.
        fd = os.open(self.directory / 'build.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                platform_os.lock_nowait(fd)
            except BlockingIOError:
                raise RuntimeError('Another preview is using this project’s build cache. Wait for it to finish.')
            owner = self.directory / 'owner.json'
            expected = {'format': 1, 'source': str(self.source)}
            normal_path(owner)
            if owner.exists():
                if json.loads(owner.read_text()) != expected:
                    raise RuntimeError('Build storage belongs to a different project. Nothing was cleaned.')
            else:
                if any(p.name != 'build.lock' for p in self.directory.iterdir()):
                    raise RuntimeError('This build folder has no ownership record. Nothing was cleaned.')
                owner.write_text(json.dumps(expected))
            self.locked = True
            self.lock_fd = fd
            try:
                yield self
            finally:
                self.locked = False
                self.lock_fd = None
        finally:
            os.close(fd)

    def child_fds(self):
        if not self.locked or self.lock_fd is None:
            raise RuntimeError('Lock the project cache before starting build tools.')
        # Direct build children retain the flock if the builder leader crashes.
        return (self.lock_fd,)

    def next_build_number(self):
        """Allocate increasing native build numbers under the existing project lock."""
        if not self.locked:
            raise RuntimeError('Lock the project cache before allocating a build number.')
        path = normal_path(self.directory / 'build-number.json')
        previous = 0
        if path.exists():
            record = json.loads(path.read_text())
            if (type(record) is not dict or set(record) != {'schema', 'last'}
                    or record['schema'] != 1 or type(record['last']) is not int or record['last'] < 1):
                raise RuntimeError('The native build-number record needs attention.')
            previous = record['last']
        number = max(time.time_ns() // 1000, previous + 1)
        temporary = normal_path(self.directory / 'build-number.tmp')
        with temporary.open('w') as handle:
            json.dump({'schema': 1, 'last': number}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        return str(number)

    def clean_work(self):
        if not self.locked:
            raise RuntimeError('Lock the project build cache before cleaning temporary source.')
        normal_path(self.work)
        if self.work.exists():
            # shutil.rmtree removes inner symlinks themselves, not their destinations.
            shutil.rmtree(self.work)

    def archive_source(self, output):
        if not self.locked:
            raise RuntimeError('Lock the project build cache before archiving source.')
        archive = Path(output) / 'source.tar.gz'
        temporary = Path(output) / 'source.tar.gz.tmp'
        try:
            with tarfile.open(temporary, 'w:gz') as handle:
                def include(info):
                    if 'node_modules' in Path(info.name).parts:
                        return None
                    if info.issym() or info.islnk():
                        raise RuntimeError('Linked source cannot be retained in a release archive.')
                    return info
                handle.add(self.work, arcname='source', filter=include)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
        return archive
