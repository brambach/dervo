#!/usr/bin/env python3
"""Vendor the Dervo harness library (``dervo_harness``) into backend/dervo_harness with provenance.

Usage:
    python3 scripts/vendor-harness.py <path to the harness-core source tree>

What it does, and nothing more:
- copies exactly the top-level ``.py`` files of ``<source>/dervo_harness`` (the library is a flat package; a
  subpackage in the source is refused so nothing is silently dropped);
- copies ``THIRD_PARTY_NOTICES.md`` and ``reference/LICENSE.txt`` (the upstream MIT notice) next to them;
- writes ``NOTICE.md`` (origin and version, stated plainly) and ``PROVENANCE.json`` (library version, sha256 of every
  vendored file, a digest of the whole set, and the vendoring time).

The source path is a build-time argument only. Nothing written into the bundle names it, so the packaged app never
depends on the machine it was vendored on. backend/harness_port.py refuses to load a package whose files do not
match PROVENANCE.json byte for byte, so a hand edit inside backend/dervo_harness makes the harness unavailable
rather than running modified library code.
"""
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'backend' / 'dervo_harness'
PACKAGE = 'dervo_harness'
NOTICES = 'THIRD_PARTY_NOTICES.md'
UPSTREAM_LICENSE = 'LICENSE-Hermes.txt'
NOTICE = 'NOTICE.md'
PROVENANCE = 'PROVENANCE.json'
PORT_VERSION = 'dervo-harness-port-v2'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    source = Path(argv[1]).resolve()
    package = source / PACKAGE
    required = {'package init': package / '__init__.py', 'third-party notices': source / NOTICES,
                'upstream licence': source / 'reference' / 'LICENSE.txt'}
    missing = [f'{label} ({path})' for label, path in required.items() if not path.is_file()]
    if missing:
        print('vendor-harness: refusing; the source tree lacks ' + ', '.join(missing), file=sys.stderr)
        return 2
    nested = [p.name for p in package.iterdir() if p.is_dir() and p.name != '__pycache__']
    if nested:
        print(f'vendor-harness: refusing; {PACKAGE} has subpackages this script does not vendor: {nested}', file=sys.stderr)
        return 2
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', (package / '__init__.py').read_text(encoding='utf-8'), re.M)
    if not match:
        print(f'vendor-harness: refusing; {PACKAGE}/__init__.py declares no __version__', file=sys.stderr)
        return 2
    version = match.group(1)

    if TARGET.exists():
        shutil.rmtree(TARGET)
    TARGET.mkdir(parents=True)
    for path in sorted(package.glob('*.py')):
        shutil.copyfile(path, TARGET / path.name)
    shutil.copyfile(source / NOTICES, TARGET / NOTICES)
    shutil.copyfile(source / 'reference' / 'LICENSE.txt', TARGET / UPSTREAM_LICENSE)
    vendored_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
    (TARGET / NOTICE).write_text(
        f'# Dervo harness core, vendored copy\n\n'
        f'This directory holds the `{PACKAGE}` library, version {version}, copied unmodified from the harness-core source tree '
        f'on {vendored_at}. Every `.py` file is byte for byte the original; `{PROVENANCE}` records a sha256 for each one and the '
        f'app refuses to load the library if any file differs. The app-side adapter lives outside this directory '
        f'(`backend/harness_adapter.py`) and uses the library through its public API only.\n\n'
        f'The library is Dervo\'s own code. No licence file exists in the harness-core source tree at vendoring time, so this '
        f'notice adds no terms of its own.\n\n'
        f'Seven helpers in `upstream_adapted.py` are rewrites of code from the Hermes agent, Copyright (c) 2025 Nous Research, '
        f'MIT License. The upstream licence text is in `{UPSTREAM_LICENSE}` and the adaptation map, listing each symbol and what '
        f'changed, is in `{NOTICES}`. No upstream module is imported, vendored or executed.\n', encoding='utf-8')

    files = {p.name: sha256(p) for p in sorted(TARGET.iterdir()) if p.is_file()}
    tree = hashlib.sha256('\n'.join(f'{name} {digest}' for name, digest in sorted(files.items())).encode('utf-8')).hexdigest()
    provenance = {
        'port': PORT_VERSION, 'source': 'harness-core', 'package': PACKAGE, 'version': version, 'vendored_at': vendored_at,
        'license': NOTICES, 'notices': [NOTICES, UPSTREAM_LICENSE, NOTICE], 'tree_sha256': tree, 'files': files,
    }
    (TARGET / PROVENANCE).write_text(json.dumps(provenance, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(f'vendor-harness: {PACKAGE} {version}: {len(files)} files into {TARGET.relative_to(ROOT)}; tree sha256 {tree}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
