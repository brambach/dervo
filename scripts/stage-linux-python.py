#!/usr/bin/env python3
"""Dervo's own Python 3.13 for the Linux packages.

runtime/python-linux-x64.json pins one python-build-standalone archive by URL, size and SHA-256, the way
runtime/python.json pins the Mac's. This fetches it into a download cache (python_runtime.fetch: a cached file is used
only while its hash matches), unpacks it into linux-python/ at the repository root, removes what Dervo never runs (pip,
Tcl/Tk, headers, the shared libpython the static interpreter doesn't load), precompiles the standard library and checks
the copy starts, reports the pinned version and has the modules the host imports. tauri.linux.conf.json then packages
linux-python/ among the app's resources, where src/launch.rs looks for it.

  stage-linux-python.py [--pin runtime/python-linux-x64.json] [--cache DIR] [--dest linux-python]

Standard library only, Python 3.12 or newer.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import python_runtime  # noqa: E402  (fetch, sha256 and the path rule are shared with the Mac)

PIN = ROOT / 'runtime/python-linux-x64.json'
DEST = ROOT / 'linux-python'
FIELDS = {'version', 'build', 'platform', 'url', 'size', 'sha256', 'archive_root', 'executable', 'remove'}
# What the host imports on Linux: the terminal (pty, fcntl), the account client (ssl, sqlite3), ctypes for the
# process helpers and the compression modules attachments use.
SMOKE = ('import ctypes, fcntl, json, pty, sqlite3, ssl, sys, zlib, lzma, bz2, hashlib, select, resource, _ctypes; '
         'print(json.dumps({"version": "%d.%d.%d" % sys.version_info[:3], "prefix": sys.prefix, '
         '"sqlite": sqlite3.sqlite_version, "openssl": ssl.OPENSSL_VERSION}))')


def fail(message):
    raise python_runtime.StageError(message)


def read_pin(path):
    path = Path(path)
    pin = json.loads(path.read_text())
    if type(pin) is not dict or not FIELDS <= set(pin):
        fail(f'{path} must have {", ".join(sorted(FIELDS))}.')
    if not str(pin['version']).startswith('3.13.') or 'linux' not in pin['platform']:
        fail(f'{path} must pin a Linux Python 3.13.')
    if not str(pin['url']).startswith('https://') or len(pin['sha256']) != 64:
        fail('The pin needs an https URL and a SHA-256.')
    if not all(python_runtime.safe_relative(p) for p in [*pin['remove'], pin['executable']]):
        fail('The pin\'s paths must be relative paths inside the runtime.')
    return pin


def clear_unstaged(dest):
    """A debug build (build.rs) leaves a folder holding only NOT-STAGED so cargo can build without the runtime. That
    isn't a staged runtime, so it goes before the real one is put there."""
    if dest.is_dir() and not dest.is_symlink() and [p.name for p in dest.iterdir()] == ['NOT-STAGED']:
        shutil.rmtree(dest)


def stage(pin, dest=DEST, cache=python_runtime.CACHE, run=subprocess.run):
    dest = Path(dest).absolute()
    clear_unstaged(dest)
    if dest.exists() or dest.is_symlink():
        fail(f'{dest} already exists. Remove it to stage again.')
    archive = python_runtime.fetch(pin, cache)
    unpack = Path(tempfile.mkdtemp(prefix='.python-', dir=dest.parent))
    try:
        with tarfile.open(archive, 'r:gz') as bundle:
            bundle.extractall(unpack, filter='data')
        root = unpack / pin['archive_root']
        for relative in pin['remove']:
            path = root / relative
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                fail(f'{relative} is listed to remove but isn\'t in the archive. Check {PIN.name} against it.')
        links = [str(p.relative_to(root)) for p in root.rglob('*') if p.is_symlink()]
        if links:
            fail('The trimmed runtime still has links, which packaging would copy as files: ' + ', '.join(links))
        root.rename(dest)
    finally:
        shutil.rmtree(unpack, ignore_errors=True)
    python = dest / pin['executable']
    # Precompiled once here: the installed copy is read-only and the host runs with -B. unchecked-hash keeps them valid
    # when packaging changes the modification times.
    compiled = run([str(python), '-E', '-s', '-I', '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode',
                    'unchecked-hash', str(dest / 'lib/python3.13')], capture_output=True, text=True, timeout=600, env={})
    if compiled.returncode:
        fail('Precompiling Dervo\'s Python failed: ' + (compiled.stdout + compiled.stderr)[-2000:])
    smoke = run([str(python), '-E', '-s', '-B', '-c', SMOKE], capture_output=True, text=True, timeout=60, env={})
    if smoke.returncode:
        fail('Dervo\'s Python didn\'t start after staging: ' + (smoke.stdout + smoke.stderr)[-2000:])
    facts = json.loads(smoke.stdout)
    if facts['version'] != pin['version'] or Path(facts['prefix']).resolve() != dest.resolve():
        fail(f'Dervo\'s Python reports {facts["version"]} at {facts["prefix"]}, not {pin["version"]} at {dest}.')
    licence = ROOT / 'runtime' / pin.get('licence', 'LICENSE-Python')
    if not licence.is_file():
        fail(f'{licence} is missing; the packages carry Python\'s licence.')
    size = sum(p.stat().st_size for p in dest.rglob('*') if p.is_file())
    return {'version': pin['version'], 'build': pin['build'], 'sha256': pin['sha256'], 'path': str(dest),
            'bytes': size, 'sqlite': facts['sqlite'], 'openssl': facts['openssl']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--pin', default=str(PIN))
    parser.add_argument('--cache', default=os.environ.get('DERVO_BUILD_CACHE', str(Path.home() / '.cache/dervo-build')))
    parser.add_argument('--dest', default=str(DEST))
    args = parser.parse_args(argv)
    try:
        print(json.dumps(stage(read_pin(args.pin), args.dest, args.cache), indent=2))
    except (python_runtime.StageError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print('stage-linux-python.py: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
