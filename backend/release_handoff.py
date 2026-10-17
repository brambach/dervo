"""Private, stopped-workspace backups for version handoff and explicit recovery.

The native restart controller must stop its host before taking this lease. This
module never terminates a process or silently replaces a running workspace.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform_os
import shutil
import stat
import uuid

RECOVERY_MARKER = '.dervo-restore-pending.json'


def local_path(value):
    path = Path(value).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise RuntimeError('This version handoff needs a normal local folder, without linked parent folders.')
    return path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root, *, reject_reserved=False):
    """Record links without opening their targets; refuse sockets and special files."""
    result = {}
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            if relative in ('conversations.sqlite3.lock', RECOVERY_MARKER):
                if reject_reserved:
                    raise RuntimeError('A backup or prepared restore contains a reserved workspace control file. Nothing was restored.')
                continue
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                result[relative] = {'kind': 'link', 'target': os.readlink(path)}
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {'kind': 'directory', 'mode': mode}
            elif stat.S_ISREG(info.st_mode):
                result[relative] = {'kind': 'file', 'mode': mode, 'bytes': info.st_size, 'sha256': file_hash(path)}
            else:
                raise RuntimeError('Close the programs using this workspace before backing it up. A special file is still present.')
    return result


class WorkspaceLease:
    def __init__(self, root):
        self.root = local_path(root)
        self.held = False

    @contextmanager
    def acquire(self):
        pending = (self.root / RECOVERY_MARKER).exists() or (self.root / RECOVERY_MARKER).is_symlink()
        if not self.root.is_dir() or (not pending and not (self.root / 'conversations.sqlite3').is_file()):
            raise RuntimeError('The saved Dervo workspace is missing. Nothing was changed.')
        local_path(self.root / 'conversations.sqlite3')
        lock = self.root / 'conversations.sqlite3.lock'
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError('The workspace lock needs inspection. Nothing was changed.')
            try:
                platform_os.lock_nowait(fd)
            except BlockingIOError:
                raise RuntimeError('Dervo is still using this workspace. Close it before switching versions.') from None
            self.held = True
            try:
                yield self
            finally:
                self.held = False
        finally:
            os.close(fd)

    def snapshot(self, destination):
        if not self.held:
            raise RuntimeError('Close and lock the workspace before making a version backup.')
        if (self.root / RECOVERY_MARKER).exists() or (self.root / RECOVERY_MARKER).is_symlink():
            raise RuntimeError('Finish the interrupted restore before making another workspace backup.')
        destination = local_path(destination)
        if destination.exists() or destination.is_relative_to(self.root) or self.root.is_relative_to(destination):
            raise RuntimeError('Use a new backup folder outside the workspace. Nothing was replaced.')
        before = inventory(self.root)
        bytes_needed = sum(item.get('bytes', 0) for item in before.values()) + 64 * 1024 ** 2
        parent = destination.parent
        while not parent.exists():
            parent = parent.parent
        if shutil.disk_usage(parent).free < bytes_needed:
            raise RuntimeError('There isn’t enough free space for a verified workspace backup. Nothing was switched.')
        destination.mkdir(parents=True, mode=0o700)
        os.chmod(destination, 0o700)
        data = destination / 'workspace'
        record = {'format': 1, 'source': str(self.root), 'state': 'copying'}
        manifest = destination / 'backup.json'
        manifest.write_text(json.dumps(record))
        try:
            shutil.copytree(self.root, data, symlinks=True,
                            ignore=lambda parent, names: ['conversations.sqlite3.lock'] if Path(parent) == self.root else [])
            after = inventory(self.root)
            copied = inventory(data, reject_reserved=True)
            if before != after or before != copied:
                raise RuntimeError('The workspace changed during backup. Nothing was switched; close other writers and try again.')
            record.update(state='verified', files=before)
            temporary = manifest.with_suffix('.tmp')
            temporary.write_text(json.dumps(record, indent=2)); temporary.replace(manifest)
        except BaseException:
            record['state'] = 'incomplete'
            manifest.write_text(json.dumps(record))
            raise
        return record


def verified_backup(destination, expected_workspace):
    destination = local_path(destination)
    workspace = local_path(expected_workspace)
    if destination.is_relative_to(workspace) or workspace.is_relative_to(destination):
        raise RuntimeError('A restore backup must be separate from the current workspace.')
    local_path(destination / 'backup.json')
    local_path(destination / 'workspace')
    try:
        record = json.loads((destination / 'backup.json').read_text())
    except (OSError, ValueError):
        raise RuntimeError('The workspace backup record is missing or unreadable.') from None
    if (type(record) is not dict or record.get('format') != 1 or record.get('state') != 'verified'
            or record.get('source') != str(local_path(expected_workspace)) or type(record.get('files')) is not dict):
        raise RuntimeError('This backup doesn’t belong to the selected workspace or wasn’t verified.')
    if inventory(destination / 'workspace', reject_reserved=True) != record['files']:
        raise RuntimeError('The workspace backup has changed since verification. Nothing was restored.')
    return record


def restore_snapshot(lease, backup, recovery, *, confirmation):
    """Restore only with an exact backup choice and a verified copy of newer work.

    Keep the workspace lock inode in place while replacing entries. A directory
    rename would release the path for a second host to acquire a different lock.
    """
    if not lease.held:
        raise RuntimeError('Close and lock the workspace before restoring a version backup.')
    backup, recovery = local_path(backup), local_path(recovery)
    if backup.is_relative_to(recovery) or recovery.is_relative_to(backup):
        raise RuntimeError('Keep the newer-work recovery folder separate from the selected backup.')
    expected = 'RESTORE ' + backup.name
    if confirmation != expected:
        raise RuntimeError('Confirm the exact backup to restore. Newer work will be preserved separately.')
    record = verified_backup(backup, lease.root)
    if recovery.exists():
        raise RuntimeError('Choose a new folder to preserve the newer workspace before restoring.')
    lease.snapshot(recovery)
    # Prepare and verify another copy before moving any current workspace entry.
    stage = recovery / 'restore-staging'
    shutil.copytree(backup / 'workspace', stage, symlinks=True)
    if inventory(stage, reject_reserved=True) != record['files']:
        raise RuntimeError('The prepared restore copy couldn’t be verified. The current workspace is unchanged.')
    displaced = recovery / 'displaced'
    displaced.mkdir(mode=0o700)
    moved_old, moved_new = [], []
    journal = recovery / 'restore.json'
    marker = lease.root / RECOVERY_MARKER
    def save(state):
        temporary = journal.with_suffix('.tmp')
        temporary.write_text(json.dumps({'state': state, 'backup': str(backup), 'workspace': str(lease.root),
                                         'moved_old': moved_old, 'moved_new': moved_new}, indent=2))
        temporary.replace(journal)
    marker.write_text(json.dumps({'backup': str(backup), 'recovery': str(recovery)}))
    save('restoring')
    try:
        for item in sorted(lease.root.iterdir()):
            if item.name in ('conversations.sqlite3.lock', RECOVERY_MARKER):
                continue
            item.rename(displaced / item.name)
            moved_old.append(item.name); save('restoring')
        for item in sorted(stage.iterdir()):
            if item.name in ('conversations.sqlite3.lock', RECOVERY_MARKER):
                raise RuntimeError('A prepared restore contains a reserved workspace control file.')
            item.rename(lease.root / item.name)
            moved_new.append(item.name); save('restoring')
        if inventory(lease.root) != record['files']:
            raise RuntimeError('Restored workspace verification failed.')
        save('restored')
        marker.unlink()
    except BaseException:
        # Roll back an ordinary I/O error while still owning the same workspace lock.
        for name in reversed(moved_new):
            (lease.root / name).rename(stage / name)
        for name in reversed(moved_old):
            (displaced / name).rename(lease.root / name)
        save('recovered_after_error')
        marker.unlink()
        raise
    return {'state': 'restored', 'backup': str(backup), 'newer_workspace': str(recovery / 'workspace')}


def recover_interrupted(lease, *, confirmation):
    """Return to the verified newer-work copy after a restore was interrupted."""
    if not lease.held:
        raise RuntimeError('Lock the workspace before recovering an interrupted restore.')
    marker = local_path(lease.root / RECOVERY_MARKER)
    try:
        pending = json.loads(marker.read_text())
        recovery = local_path(pending['recovery'])
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError('The restore recovery record needs inspection. No files were changed.') from None
    if confirmation != 'RECOVER ' + recovery.name:
        raise RuntimeError('Confirm the saved newer-work copy before recovering the interrupted restore.')
    record = verified_backup(recovery, lease.root)
    attempt = recovery / ('recovery-' + uuid.uuid4().hex)
    attempt.mkdir(mode=0o700)
    stage = attempt / 'prepared'
    shutil.copytree(recovery / 'workspace', stage, symlinks=True)
    if inventory(stage, reject_reserved=True) != record['files']:
        raise RuntimeError('The recovery copy couldn’t be verified. Saved backups are unchanged.')
    partial = attempt / 'partial-workspace'
    partial.mkdir(mode=0o700)
    # The marker remains until verification, including if recovery itself crashes.
    for item in sorted(lease.root.iterdir()):
        if item.name not in ('conversations.sqlite3.lock', RECOVERY_MARKER):
            item.rename(partial / item.name)
    for item in sorted(stage.iterdir()):
        if item.name in ('conversations.sqlite3.lock', RECOVERY_MARKER):
            raise RuntimeError('A prepared recovery contains a reserved workspace control file.')
        item.rename(lease.root / item.name)
    if inventory(lease.root) != record['files']:
        raise RuntimeError('Workspace recovery needs inspection. Saved backups are unchanged.')
    (attempt / 'result.json').write_text(json.dumps({'state': 'recovered', 'workspace': str(lease.root)}))
    marker.unlink()
    return {'state': 'recovered', 'workspace': str(lease.root), 'partial_workspace': str(partial)}
