"""Bound retained preview apps before building. Never delete a retained version."""
from contextlib import contextmanager
import os
import stat
from pathlib import Path

import platform_os
from release_storage import normal_path

APP_LIMIT = 8


def inventory(root):
    root = normal_path(root)
    apps = []
    if root.exists():
        if not root.is_dir():
            raise RuntimeError('The release folder is not a directory.')
        for release in root.iterdir():
            # Every immediate release folder is counted, including failed builds.
            # Links are never followed or silently treated as an absent app.
            if release.is_symlink():
                raise RuntimeError('A linked entry in the release folder needs review before building.')
            if not release.is_dir():
                continue
            app = normal_path(release / 'Dervo Local.app')
            if app.exists():
                if not app.is_dir():
                    raise RuntimeError('A retained preview app needs review before building.')
                apps.append(str(app))
    return {'directory':str(root), 'retainedApps':len(apps), 'limit':APP_LIMIT,
            'canBuild':len(apps)<APP_LIMIT, 'apps':sorted(apps)}


def status(root):
    try:
        result = inventory(root)
        return {key:result[key] for key in ('directory','retainedApps','limit','canBuild')}
    except (OSError, RuntimeError):
        return {'directory':str(Path(root).absolute()), 'retainedApps':None,
                'limit':APP_LIMIT, 'canBuild':False,
                'error':'Dervo couldn’t check saved versions. Check the release folder before building.'}


class ReleaseSlot:
    """Serialize builds sharing a release folder, including different source projects."""
    def __init__(self, root):
        self.root = normal_path(root)
        self.fd = None

    @contextmanager
    def acquire(self):
        normal_path(self.root).mkdir(parents=True, exist_ok=True)
        # Keep the inode: a child compiler can outlive its builder and retain this lock.
        fd = os.open(self.root/'.build-slots.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise RuntimeError('The release-folder lock needs review before building.')
            try:
                platform_os.lock_nowait(fd)
            except BlockingIOError:
                raise RuntimeError('Another preview is using this release folder. Wait for it to finish.') from None
            report = inventory(self.root)
            if not report['canBuild']:
                raise RuntimeError(
                    f"There are {report['retainedApps']} saved Dervo apps in this release folder. "
                    f"The limit is {APP_LIMIT}. Review older app copies before building another preview. "
                    'Keep your running app and rollback versions. Source snapshots, logs and workspace folders are separate.')
            self.fd = fd
            yield self
        finally:
            self.fd = None
            os.close(fd)

    def child_fds(self):
        if self.fd is None:
            raise RuntimeError('Reserve a release slot before starting build tools.')
        return (self.fd,)
