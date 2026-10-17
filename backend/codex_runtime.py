"""The same packaged official Codex binary for every provider operation."""
from functools import lru_cache
import hashlib
import os
from pathlib import Path

from dervo_connections import Rejected
import platform_os

VERSION = '0.156.1'
RUNTIME = Path(__file__).resolve().parent.parent / 'runtime'
if platform_os.WINDOWS:
    # The official win32-x64 npm package, staged by scripts/stage-windows-codex.py in its own layout (codex.exe finds its
    # sandbox helpers through codex-package.json). Its pins come from the same file the staging checked them against.
    import json
    _PIN = json.loads((RUNTIME / 'codex-win32-x64.json').read_text())
    BINARY = RUNTIME / 'codex-win' / 'bin' / 'codex.exe'
    BINARY_SHA256 = _PIN['files']['bin/codex.exe']
    HOST = BINARY.with_name('codex-code-mode-host.exe')
    HOST_SHA256 = _PIN['files']['bin/codex-code-mode-host.exe']
elif platform_os.LINUX:
    # The official linux-x64 npm package (a static musl build), staged by scripts/stage-windows-codex.py --pin
    # runtime/codex-linux-x64.json in its own layout, like Windows.
    import json
    _PIN = json.loads((RUNTIME / 'codex-linux-x64.json').read_text())
    BINARY = RUNTIME / 'codex-linux' / 'bin' / 'codex'
    BINARY_SHA256 = _PIN['files']['bin/codex']
    HOST = BINARY.with_name('codex-code-mode-host')
    HOST_SHA256 = _PIN['files']['bin/codex-code-mode-host']
else:
    BINARY_SHA256 = '0196e89fe5a7598f816ee54232c3d7c26d75e502ab5cfe2c9240e81d90f7255a'
    BINARY = RUNTIME / 'codex'
    HOST = BINARY.with_name('codex-code-mode-host')
    HOST_SHA256 = '588df0df1dab35f99f272f9f499a9fada658c613d09ef34562fe274dd54287d6'


@lru_cache(maxsize=8)
def _verified(path, identity, expected_hash):
    # Stat identity invalidates the cache when the packaged file changes. The hash streams in small reads: reading both
    # binaries whole (about 285 MB) made a 300 MB spike on the first Codex operation (efficiency review, 23 Sep).
    with open(path, 'rb') as binary:
        digest = hashlib.file_digest(binary, 'sha256').hexdigest()
    if digest != expected_hash:
        raise Rejected('The bundled Codex runtime has changed. Rebuild or reinstall Dervo before connecting.')
    return path


def verified_binary(path, expected_hash):
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise OSError('Missing regular executable')
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        return _verified(str(path), identity, expected_hash)
    except OSError:
        raise Rejected('Dervo is missing its bundled Codex runtime. Rebuild or reinstall the complete app.') from None


def executable():
    """Never select another CLI from PATH or a builder's home directory."""
    binary = verified_binary(BINARY, BINARY_SHA256)
    verified_binary(HOST, HOST_SHA256)
    return binary
