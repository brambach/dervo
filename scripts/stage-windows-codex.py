"""Stage the pinned official Codex CLI for Windows into runtime/codex-win, the way runtime/codex is staged on a Mac, or
for Linux into runtime/codex-linux with --pin runtime/codex-linux-x64.json.

The Windows build (.github/workflows/windows.yml) runs this before `tauri build`. It downloads the npm platform package
named in runtime/codex-win32-x64.json (or uses a copy already in CACHE), checks npm's sha512 integrity, and extracts only
the listed files, each checked against its sha256. The folder keeps the package's own layout (codex-package.json names
bin/codex.exe, codex-resources and codex-path relative to itself), so codex.exe finds its sandbox helpers. Voice
support isn't copied. No npm, no install scripts, no PATH.

Linux (.github/workflows/linux.yml) uses the same steps with its own pin, and marks the files executable.

Usage: python scripts/stage-windows-codex.py [--pin PIN] [CACHE]
"""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PIN = ROOT / 'runtime' / 'codex-win32-x64.json'


def fetch(pin, cache):
    archive = Path(cache) / Path(pin['tarball']).name
    if not archive.exists():
        if not pin['tarball'].startswith('https://registry.npmjs.org/@openai/codex/-/'):
            raise SystemExit('The pinned Codex tarball is not on the official npm registry.')
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(pin['tarball'], timeout=300) as response:
            archive.write_bytes(response.read())
    raw = archive.read_bytes()
    actual = 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode()
    if actual != pin['integrity']:
        raise SystemExit('The Codex package does not match its npm integrity. Nothing was staged.')
    return raw


def stage(pin, raw, root=ROOT):
    target = root / pin['folder']
    if target.exists():
        shutil.rmtree(target)
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tar:
        for relative, expected in pin['files'].items():
            member = tar.getmember(pin['archive_root'] + relative)
            if not member.isfile():
                raise SystemExit('Not a regular file in the Codex package: ' + relative)
            data = tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise SystemExit('A Codex file does not match its pinned sha256: ' + relative)
            out = target / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            if member.mode & 0o111:
                out.chmod(0o755)  # the Linux binaries; Windows ignores the bits
    return target


def main(argv):
    argv = list(argv)
    pin_path = PIN
    if len(argv) > 2 and argv[1] == '--pin':
        pin_path = ROOT / argv[2]
        del argv[1:3]
    pin = json.loads(pin_path.read_text())
    cache = argv[1] if len(argv) > 1 else ROOT / 'tools' / 'cache'
    target = stage(pin, fetch(pin, cache))
    size = sum(p.stat().st_size for p in target.rglob('*') if p.is_file())
    print(f"Staged Codex {pin['version']} for {pin['platform']} in {target.relative_to(ROOT)} ({size // (1024 * 1024)} MB, "
          f"{len(pin['files'])} files checked).")


if __name__ == '__main__':
    main(sys.argv)
