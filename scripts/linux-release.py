"""Turn a Linux build into a release build, the way scripts/windows-release.py does for Windows: stamp its version
before packaging, then keep each package with the package.json the Dervo release signer reads (tools/release-sign).

    python linux-release.py stage HELD_FOLDER [FORMAT]
    python linux-release.py restore HELD_FOLDER
    python linux-release.py package HELD_FOLDER BUNDLE_FOLDER OUT_FOLDER

The Linux build workflow runs these. A push builds a release when .github/linux-release.json names a release id ending
in -linux, and a plain test build when it says null. stage and restore are windows-release.py's own, reading
tauri.linux.conf.json: the tests stay out of the packages, and a release is stamped with the private-beta profile and
runtime/dervo-preview.json and audited file by file.

A catalog row is one installer with its own release id and build number, and the signer and the app check that a
package's id is the one stamped inside it. So a release stages and bundles once per FORMAT (deb, rpm, appimage), each
stamped 0.1.0-beta.19-linux-deb and so on with a build number of its own. Without FORMAT, stage makes the plain test
build (tests held out, nothing stamped) even when the switch names a release, so the same run tests the packages too. An installed Dervo then knows from its own
id which package it came from. package copies that format's file from BUNDLE_FOLDER (target/release/bundle) into
OUT_FOLDER/FORMAT with its package.json.

Nothing here signs, uploads or publishes.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('windows_release', ROOT / 'scripts' / 'windows-release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
release.SWITCH = Path('.github/linux-release.json')
release.PLATFORM = 'linux-x64'
release.CONFIG = 'tauri.linux.conf.json'
release.ID_ENDING = '-linux'

# Each format: the bundle folder Tauri writes it to, its file pattern and the name testers download.
FORMATS = {'deb': ('deb', '*.deb', 'Dervo-linux-x64.deb'),
           'rpm': ('rpm', '*.rpm', 'Dervo-linux-x64.rpm'),
           'appimage': ('appimage', '*.AppImage', 'Dervo-linux-x64.AppImage')}
SIGNING = {'mode': 'unsigned', 'detail': 'No package-manager signature (GPG). The Dervo release signature covers the '
                                         'SHA-256 when tools/release-sign signs this package.json.'}


def stage(root, held, fmt=None, clock=None):
    """windows-release.py's stage, with the release id this format's packages carry."""
    base = release.release_id(root)
    if fmt is not None and fmt not in FORMATS:
        raise ValueError('The format is one of ' + ', '.join(FORMATS) + '.')
    if fmt is None:
        base = None  # without a format it's the plain test build, whatever the switch says
    if base is not None and not str(base).endswith('-linux'):
        raise ValueError('A linux-x64 release id ends in -linux, like 0.1.0-beta.17-linux.')
    named = release.release_id
    release.release_id = lambda root: None if base is None else base + '-' + fmt
    release.ID_ENDING = '-linux' if fmt is None else '-linux-' + fmt
    try:
        record = release.stage(root, held) if clock is None else release.stage(root, held, clock)
    finally:
        release.release_id = named
        release.ID_ENDING = '-linux'
    record['format'] = fmt
    (held / 'stage.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def package(root, held, bundle, out):
    record = json.loads((held / 'stage.json').read_text())
    if record['releaseId'] is None:
        raise ValueError('This is a test build. Name a release id in .github/linux-release.json to package one.')
    fmt = record['format']
    folder_name, pattern, name = FORMATS[fmt]
    matches = sorted((bundle / folder_name).glob(pattern))
    if len(matches) != 1:
        raise ValueError(f'Expected one {pattern} in {bundle / folder_name}, found {len(matches)}.')
    folder = out / fmt
    folder.mkdir(parents=True, exist_ok=False)
    artifact = folder / name
    shutil.copyfile(matches[0], artifact)
    if fmt == 'appimage':
        artifact.chmod(0o755)
    result = {
        'schema': 1, 'releaseId': record['releaseId'], 'platform': release.PLATFORM, 'format': fmt,
        'artifact': name, 'sha256': release.digest(artifact), 'bytes': artifact.stat().st_size,
        'sourceCommit': os.environ.get('GITHUB_SHA'), 'builtFrom': matches[0].name, 'signing': SIGNING,
        'bundleAudit': {'nativeVersion': record['nativeVersion'], 'releaseProfile': record['releaseProfile'],
                        'excludedTests': record['excludedTests'], **record['audit']},
    }
    (folder / 'package.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main(argv):
    match argv:
        case ['stage', held]:
            result = stage(ROOT, Path(held))
        case ['stage', held, fmt]:
            result = stage(ROOT, Path(held), fmt)
        case ['restore', held]:
            result = release.restore(ROOT, Path(held))
        case ['package', held, bundle, out]:
            result = package(ROOT, Path(held), Path(bundle), Path(out))
        case _:
            print(__doc__.split('\n\n')[1], file=sys.stderr)
            return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
