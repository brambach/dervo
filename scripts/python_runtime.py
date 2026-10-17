#!/usr/bin/env python3
"""Dervo's own Python 3.13 for the Mac app (PLAN-beta17 section 4).

runtime/python.json pins one archive by URL, size and SHA-256, the way runtime/codex.json pins Codex. At build time
``stage`` fetches it into a download cache once, checks its hash every time it's used, unpacks it into the built app at
Contents/Resources/runtime/python, removes what Dervo never runs (pip, Tcl/Tk, headers and the rest of the pin's
``remove`` list), precompiles the standard library and checks the copy starts and reports the pinned version. The
Mach-O files left must be exactly the pin's ``mach_o`` list, so the managed build signs a known set
(backend/release_signing.py signs every nested Mach-O with the hardened runtime).

Nothing is installed on the Mac. Only the cache folder and the app named by --app change. A cached file whose hash
doesn't match is never used: it's replaced by a fresh download, which must match too.

  python_runtime.py fetch [--pin runtime/python.json] [--cache DIR]
  python_runtime.py stage --app "Dervo Local.app" [--pin runtime/python.json] [--cache DIR]

Standard library only, Python 3.12 or newer (tarfile's data filter).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
PIN = ROOT / 'runtime/python.json'
CACHE = Path.home() / 'Library/Caches/Dervo/build-downloads'
BUNDLED = 'Contents/Resources/runtime/python'
FIELDS = {'version', 'build', 'platform', 'url', 'size', 'sha256', 'archive_root', 'executable', 'remove', 'mach_o'}
MACH_O_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
                b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
# What the backend needs from the runtime: the modules the host, the keychain bridge (ctypes), the terminal (pty, fcntl)
# and the account client (ssl with the macOS certificate bundle, sqlite3) import. Printed back as JSON by the copy itself.
SMOKE = ('import ctypes, fcntl, json, pty, sqlite3, ssl, sys, zlib, lzma, bz2, hashlib, select, resource, _ctypes; '
         'ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security"); '
         'ssl.create_default_context(cafile="/etc/ssl/cert.pem"); '
         'print(json.dumps({"version": "%d.%d.%d" % sys.version_info[:3], "prefix": sys.prefix, '
         '"base_prefix": sys.base_prefix, "sqlite": sqlite3.sqlite_version, "openssl": ssl.OPENSSL_VERSION}))')


class StageError(RuntimeError):
    pass


def fail(message):
    raise StageError(message)


def read_pin(path=PIN):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        fail(f'{path} is missing. Dervo pins its Python in runtime/python.json; nothing is fetched without it.')
    pin = json.loads(path.read_text())
    if type(pin) is not dict or not FIELDS <= set(pin):
        fail(f'{path} must have {", ".join(sorted(FIELDS))}.')
    if not re.fullmatch(r'3\.13\.\d+', str(pin['version'])):
        fail('The pinned Python must be 3.13.x, got ' + str(pin['version']))
    if not str(pin['url']).startswith('https://') or not re.fullmatch(r'[0-9a-f]{64}', str(pin['sha256'])):
        fail('The pin needs an https URL and a SHA-256.')
    if type(pin['size']) is not int or not 0 < pin['size'] <= 200 * 1024 * 1024:
        fail('The pinned archive size must be a whole number of bytes, at most 200 MB.')
    for key in ('remove', 'mach_o'):
        if type(pin[key]) is not list or any(type(p) is not str or not safe_relative(p) for p in pin[key]):
            fail(f'The pin\'s {key} list must hold relative paths inside the runtime.')
    if not safe_relative(pin['executable']) or pin['executable'] not in pin['mach_o']:
        fail('The pinned executable must be a relative path listed in mach_o.')
    return pin


def safe_relative(text):
    path = Path(text)
    return bool(text) and not path.is_absolute() and '..' not in path.parts and '.' not in path.parts


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def cache_folder(cache):
    cache = Path(cache).absolute()
    if any(p.is_symlink() for p in (cache, *cache.parents)):
        fail(f'The download cache {cache} is linked. Choose a normal folder.')
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def fetch(pin, cache=CACHE, opener=None):
    """The verified archive in the cache, downloading it first when it's absent or doesn't match. Returns its path."""
    cache = cache_folder(cache)
    target = cache / (pin['sha256'] + '-' + Path(urllib.request.url2pathname(pin['url'].rsplit('/', 1)[-1])).name)
    if target.is_symlink():
        fail(f'{target} is a link. Remove it and build again.')
    if target.is_file() and target.stat().st_size == pin['size'] and sha256(target) == pin['sha256']:
        return target
    if opener is None:
        # The macOS certificate bundle, independent of how the builder's Python was installed (supabase_client.py).
        context = ssl.create_default_context(cafile='/etc/ssl/cert.pem') if sys.platform == 'darwin' else None
        opener = lambda url: urllib.request.urlopen(url, timeout=120, context=context)
    with tempfile.NamedTemporaryFile(dir=cache, prefix='.download-', delete=False) as temporary:
        partial = Path(temporary.name)
        try:
            with opener(pin['url']) as response:
                received = 0
                while chunk := response.read(1024 * 1024):
                    received += len(chunk)
                    if received > pin['size']:
                        fail(f'{pin["url"]} is larger than the pinned {pin["size"]} bytes. Nothing was used.')
                    temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    actual = sha256(partial)
    if partial.stat().st_size != pin['size'] or actual != pin['sha256']:
        partial.unlink(missing_ok=True)
        fail(f'The download from {pin["url"]} doesn\'t match runtime/python.json (SHA-256 {actual}). Nothing was used.')
    partial.replace(target)
    return target


def is_mach_o(path):
    try:
        with open(path, 'rb') as stream:
            return stream.read(4) in MACH_O_MAGIC
    except OSError:
        return False


def inventory(root):
    """Mach-O files and symlinks under root, as sorted relative paths."""
    mach_o, links = [], []
    for directory, folders, files in os.walk(root):
        for name in folders + files:
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                links.append(relative)
            elif path.is_file() and is_mach_o(path):
                mach_o.append(relative)
    return sorted(mach_o), sorted(links)


def stage(app, pin, cache=CACHE, opener=None, run=subprocess.run):
    """Put the pinned runtime into a built app. Returns the record the build keeps."""
    app = Path(app).absolute()
    if app.is_symlink() or not (app / 'Contents/Info.plist').is_file():
        fail(f'{app} isn\'t a built app bundle.')
    destination = app / BUNDLED
    if destination.exists() or destination.is_symlink():
        fail(f'{destination} already exists. Build the app again rather than staging twice.')
    if any(p.is_symlink() for p in (destination.parent, *destination.parent.parents) if p.is_relative_to(app)):
        fail('The app\'s runtime folder is linked. Build the app again.')
    archive = fetch(pin, cache, opener)
    unpack = Path(tempfile.mkdtemp(prefix='.python-', dir=destination.parent))
    try:
        with tarfile.open(archive, 'r:gz') as bundle:
            # The data filter refuses absolute paths, links that leave the folder, devices and setuid bits.
            bundle.extractall(unpack, filter='data')
        root = unpack / pin['archive_root']
        if root.is_symlink() or not root.is_dir():
            fail(f'The archive has no {pin["archive_root"]} folder.')
        for relative in pin['remove']:
            path = root / relative
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                fail(f'{relative} is listed to remove but isn\'t in the archive. Check runtime/python.json against it.')
        mach_o, links = inventory(root)
        if links:
            fail('The trimmed runtime still has links, which the bundle audit refuses: ' + ', '.join(links))
        if mach_o != sorted(pin['mach_o']):
            fail('The trimmed runtime\'s Mach-O files differ from runtime/python.json: ' + ', '.join(mach_o))
        root.rename(destination)
    finally:
        shutil.rmtree(unpack, ignore_errors=True)
    python = destination / pin['executable']
    stdlib = destination / 'lib/python3.13'
    # Precompiled once here, because the app's own copy is signed and read-only: -B means it never writes them later.
    # unchecked-hash: a copied or re-signed bundle with new modification times still uses them.
    compiled = run([str(python), '-E', '-s', '-I', '-m', 'compileall', '-q', '-j', '0', '--invalidation-mode',
                    'unchecked-hash', str(stdlib)], capture_output=True, text=True, timeout=600, env={})
    if compiled.returncode:
        fail('Precompiling Dervo\'s Python failed: ' + (compiled.stdout + compiled.stderr)[-2000:])
    smoke = run([str(python), '-E', '-s', '-B', '-c', SMOKE], capture_output=True, text=True, timeout=60, env={})
    if smoke.returncode:
        fail('Dervo\'s Python didn\'t start after staging: ' + (smoke.stdout + smoke.stderr)[-2000:])
    facts = json.loads(smoke.stdout)
    if facts['version'] != pin['version'] or Path(facts['prefix']).resolve() != destination.resolve():
        fail(f'Dervo\'s Python reports {facts["version"]} at {facts["prefix"]}, not {pin["version"]} at {destination}.')
    size = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())
    return {'version': pin['version'], 'build': pin['build'], 'sha256': pin['sha256'], 'path': BUNDLED,
            'executable': BUNDLED + '/' + pin['executable'], 'mach_o': [BUNDLED + '/' + p for p in pin['mach_o']],
            'bytes': size, 'sqlite': facts['sqlite'], 'openssl': facts['openssl']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('command', choices=('fetch', 'stage'))
    parser.add_argument('--pin', default=str(PIN))
    parser.add_argument('--cache', default=str(CACHE))
    parser.add_argument('--app')
    args = parser.parse_args(argv)
    try:
        pin = read_pin(args.pin)
        if args.command == 'fetch':
            print(json.dumps({'archive': str(fetch(pin, args.cache)), 'sha256': pin['sha256']}))
        else:
            if not args.app:
                parser.error('stage needs --app')
            print(json.dumps(stage(args.app, pin, args.cache), indent=2))
    except (StageError, OSError, ValueError, tarfile.TarError, subprocess.SubprocessError) as exc:
        print('python_runtime.py: ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
