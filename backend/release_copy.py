"""Independent snapshot files that share unchanged APFS blocks where supported."""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys


def file_hash(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def clone_file(source, destination):
    """Create a new clone or raise. Never overwrite a file or create a hard link."""
    source, destination = Path(source), Path(destination)
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError('Snapshot source must be a regular file')
    if sys.platform != 'darwin':
        raise OSError(errno.ENOTSUP, 'File cloning is unavailable')
    libc = ctypes.CDLL(None, use_errno=True)
    clone = libc.clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
    clone.restype = ctypes.c_int
    # Apple sys/clonefile.h: don't follow the source link; copy access controls.
    if clone(os.fsencode(source), os.fsencode(destination), 0x0001 | 0x0004) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    return str(destination)


def copy_snapshot_file(source, destination):
    """Clone on APFS, use an ordinary independent copy on other filesystems."""
    if not stat.S_ISREG(Path(source).lstat().st_mode):
        raise ValueError('Snapshot source must be a regular file')
    if os.path.lexists(destination):
        raise FileExistsError('Snapshot destination already exists')
    try:
        clone_file(source, destination)
    except OSError as error:
        if error.errno not in {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}:
            raise
        # Exclusive creation preserves the no-overwrite contract on fallback.
        with open(source, 'rb') as reader, open(destination, 'xb') as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
    shutil.copystat(source, destination, follow_symlinks=False)
    return str(destination)
