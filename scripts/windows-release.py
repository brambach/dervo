"""Turn a Windows build into a release build: stamp its version before packaging, then keep the installer with the
package.json the Dervo release signer reads (tools/release-sign).

    python windows-release.py stage HELD_FOLDER
    python windows-release.py restore HELD_FOLDER
    python windows-release.py package HELD_FOLDER NSIS_FOLDER OUT_FOLDER

The Windows build workflow runs these on the runner (b16/windows, milestone 5 in PLAN-windows-m5-20260926.md). GitHub
only offers a manual run for workflows on the repo's default branch, and this branch is the only one pushed, so a push
builds a release when .github/windows-release.json names a release id, and a plain test build when it says null.

stage moves backend/test_*.py into HELD_FOLDER so the installer doesn't carry them (release_profiles.exclude_test_modules
does the same on a Mac). For a release it also writes the private-beta profile and runtime/dervo-preview.json, the
version marker Updates reads (beta_update_host.running_version), and audits every file the installer takes from
tauri.windows.conf.json's resources with the Mac's own check (release_profiles.verify_bundle). HELD_FOLDER/stage.json
records what it did. restore puts the tests back and removes the stamped files, so the tests after packaging see the
same tree as a test build. package copies the one NSIS installer to OUT_FOLDER/Dervo-windows-x64.exe and writes
OUT_FOLDER/package.json.

Nothing here signs, uploads or publishes. The installer has no Authenticode signature (Bryce, 26 Sep 2026: unsigned
while only his friend tests on Windows).
"""
import datetime
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

import release_profiles  # noqa: E402
import release_version  # noqa: E402

SWITCH = Path('.github/windows-release.json')
PROFILE = 'private-beta'
PLATFORM = 'windows-x64'
ARTIFACT = 'Dervo-windows-x64.exe'
MARKER = Path('runtime/dervo-preview.json')
# scripts/linux-release.py reuses stage, restore and audit with its own config, switch and id ending.
CONFIG = 'tauri.windows.conf.json'
ID_ENDING = '-windows'


def release_id(root):
    """The release id .github/windows-release.json names, or None for a test build."""
    path = root / SWITCH
    if not path.exists():
        return None
    record = json.loads(path.read_text())
    if type(record) is not dict or set(record) != {'releaseId'}:
        raise ValueError(f'{SWITCH} holds only {{"releaseId": ...}}.')
    return record['releaseId']


def resources(root):
    """Every file the installer takes from the source, from tauri.windows.conf.json's resource globs. A glob that matches
    a folder counts everything in it, so the audit checks at least what gets packaged."""
    patterns = json.loads((root / CONFIG).read_text())['bundle']['resources']
    found = set()
    for pattern in patterns:
        for match in glob.glob(pattern, root_dir=root, recursive=True):
            path = root / match
            if path.is_dir():
                found.update(p for p in path.rglob('*') if p.is_file() or p.is_symlink())
            else:
                found.add(path)
    return sorted(found)


def audit(root):
    """release_profiles.verify_bundle on a copy of exactly the files the installer takes."""
    with tempfile.TemporaryDirectory() as scratch:
        for path in resources(root):
            if path.is_symlink():
                raise ValueError('Linked resource needs packaging review: ' + str(path.relative_to(root)))
            target = Path(scratch) / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        return release_profiles.verify_bundle(scratch, PROFILE)


def stage(root, held, clock=time.time_ns):
    record = {'releaseId': release_id(root)}
    if record['releaseId'] is not None:
        version = json.loads((root / 'tauri.conf.json').read_text())['version']
        # Microseconds since 1970, the same kind of number the Mac builder allocates (release_storage.next_build_number).
        build = str(clock() // 1000)
        release_version.validate(version, record['releaseId'], build)
        # Windows rows have their own release id (Bryce, 26 Sep 2026), which the catalog requires to end in -windows.
        if not record['releaseId'].endswith(ID_ENDING):
            raise ValueError(f'A {PLATFORM} release id ends in {ID_ENDING}, like 0.1.0-beta.17{ID_ENDING}.')
        native = {'productVersion': version, 'buildNumber': build, 'releaseId': record['releaseId']}
    held.mkdir(parents=True, exist_ok=False)
    tests = sorted((root / 'backend').glob('test_*.py'))
    for path in tests:
        path.rename(held / path.name)
    record['excludedTests'] = ['backend/' + path.name for path in tests]
    if record['releaseId'] is not None:
        profile = release_profiles.stage(root, PROFILE)
        (root / MARKER).write_text(json.dumps({
            'workspaceHandoff': 1, 'releaseId': record['releaseId'],
            'builtAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'releaseProfile': profile, 'nativeVersion': native}))
        record.update(nativeVersion=native, releaseProfile=profile, audit=audit(root))
    (held / 'stage.json').write_text(json.dumps(record, indent=2) + '\n')
    return record


def restore(root, held):
    record = json.loads((held / 'stage.json').read_text())
    for name in record['excludedTests']:
        (held / Path(name).name).rename(root / name)
    if record['releaseId'] is not None:
        for path in (root / MARKER, root / 'runtime' / release_profiles.MARKER):
            path.unlink(missing_ok=True)
    return record


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def package(root, held, nsis, out):
    record = json.loads((held / 'stage.json').read_text())
    if record['releaseId'] is None:
        raise ValueError('This is a test build. Name a release id in .github/windows-release.json to package one.')
    installers = sorted(nsis.glob('*.exe'))
    if len(installers) != 1:
        raise ValueError(f'Expected one NSIS installer in {nsis}, found {len(installers)}.')
    out.mkdir(parents=True, exist_ok=False)
    installer = out / ARTIFACT
    shutil.copyfile(installers[0], installer)
    result = {
        'schema': 1, 'releaseId': record['releaseId'], 'platform': PLATFORM, 'format': 'exe', 'artifact': ARTIFACT,
        'sha256': digest(installer), 'bytes': installer.stat().st_size,
        'sourceCommit': os.environ.get('GITHUB_SHA'), 'builtFrom': installers[0].name,
        'signing': {'mode': 'unsigned', 'detail': 'No Authenticode signature. Windows SmartScreen warns on install.'},
        'bundleAudit': {'nativeVersion': record['nativeVersion'], 'releaseProfile': record['releaseProfile'],
                        'excludedTests': record['excludedTests'], **record['audit']},
    }
    (out / 'package.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main(argv):
    match argv:
        case ['stage', held]:
            result = stage(ROOT, Path(held))
        case ['restore', held]:
            result = restore(ROOT, Path(held))
        case ['package', held, nsis, out]:
            result = package(ROOT, Path(held), Path(nsis), Path(out))
        case _:
            print(__doc__.split('\n\n')[1], file=sys.stderr)
            return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
