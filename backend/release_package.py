"""Create a local tester archive from one verified bundle. Never upload or install."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

import release_profiles
import release_signing
import release_version
from release_storage import normal_path, require_space

# Dervo's beta distribution team, distinct from development signing identities.
DISTRIBUTION_TEAM = 'PP5K3YUUX6'


def distribution_signature(bundle, record):
    signing = record.get('signing') or {}
    if (signing.get('mode') != 'developer-id' or signing.get('team') != DISTRIBUTION_TEAM
            or signing.get('notarised') is not True or signing.get('stapled') is not True):
        raise ValueError('Tester packages require Dervo’s notarised Developer ID build with a stapled ticket.')
    code, details = release_signing.inspect(['/usr/bin/codesign', '-d', '--verbose=2', str(bundle)],
                                           {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'})
    actual = release_signing.signature_details(details)
    if code or actual['team'] != DISTRIBUTION_TEAM or not actual['hardened'] or not actual['timestamped']:
        raise ValueError('The app doesn’t have Dervo’s expected Developer ID signature.')
    run(['/usr/bin/xcrun', 'stapler', 'validate', str(bundle)])
    run(['/usr/sbin/spctl', '--assess', '--type', 'execute', str(bundle)])
    return {'team': DISTRIBUTION_TEAM, 'stapledTicket': 'validated', 'gatekeeper': 'accepted'}


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def inventory(root):
    """Keep every byte and executable permission in the archive comparison."""
    root = normal_path(root)
    result = {}
    forbidden = {'profiles', 'preview-data', 'auth.json', 'credentials.json',
                 'conversations.sqlite3', 'preview-tools.json', 'preview-jobs.json'}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError('Linked bundle entry needs packaging review: ' + str(relative))
        if any(part in forbidden or part == '.env' or part.startswith('.env.') for part in relative.parts):
            raise ValueError('Local account or workspace data found in bundle: ' + str(relative))
        if path.is_file():
            result[str(relative)] = {'sha256': digest(path), 'mode': path.stat().st_mode & 0o777}
        elif not path.is_dir():
            raise ValueError('Unsupported bundle entry: ' + str(relative))
    return result


def run(command):
    # Don't relay arbitrary tool output into reports. No auth environment is passed.
    result = subprocess.run(command, env={'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
                            capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError(Path(command[0]).name + ' failed while preparing the local package.')
    return result.stdout


def publish(source, destination):
    # macOS sys/stdio.h: RENAME_EXCL (0x4) fails if anything already exists.
    # Unlike exists()+rename(), the filesystem checks this atomically.
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    rename = libc.renamex_np
    rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(os.fsencode(source), os.fsencode(destination), 0x4):
        raise OSError(ctypes.get_errno(), 'The package could not be published into a new directory.')


def check_disk_image(artifact, parent, files, instructions):
    # Keep mounts outside the recursively cleaned staging folder. If detaching
    # fails, leave the mount directory intact instead of traversing its contents.
    mount = Path(tempfile.mkdtemp(prefix='.dervo-mount-', dir=parent))
    attached = False
    try:
        run(['/usr/bin/hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(artifact)])
        attached = True
        check_payload(mount, files, instructions, disk_image=True)
    finally:
        try:
            # Even a failed attach may already have mounted the image.
            run(['/usr/bin/hdiutil', 'detach', str(mount)])
        except Exception:
            if attached or os.path.ismount(mount):
                raise RuntimeError('The packaging disk image could not be detached. Its mount folder was kept: ' + str(mount)) from None
        if os.path.ismount(mount):
            raise RuntimeError('The packaging disk image is still mounted. Its folder was kept: ' + str(mount))
        mount.rmdir()


def inspect_release(release):
    release = normal_path(release)
    record_path = normal_path(release / 'release.json')
    record = json.loads(record_path.read_text())
    release_id = record.get('id', '')
    if not isinstance(release_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', release_id):
        raise ValueError('The release ID cannot be used as a package name.')
    if record.get('state') != 'built' or release_id != release.name:
        raise ValueError('Choose a completed, named release.')
    if record.get('release_profile') != release_profiles.manifest('private-beta'):
        raise ValueError('Tester packages require the private-beta profile.')
    bundle = normal_path(release / 'Dervo Local.app')
    files = inventory(bundle)
    resources = bundle / 'Contents/Resources'
    audit = release_profiles.verify_bundle(resources, 'private-beta')
    marker = json.loads((resources / 'runtime/dervo-preview.json').read_text())
    if marker.get('releaseId') != release_id or marker.get('releaseProfile') != record['release_profile']:
        raise ValueError('The app and release record identify different builds.')
    native = record.get('native_version')
    if not native or marker.get('nativeVersion') != native or native.get('releaseId') != release_id:
        raise ValueError('This release needs native version metadata before tester packaging.')
    release_version.verify(bundle, native)
    executable = bundle / 'Contents/MacOS/dervo-local'
    if digest(executable) != record.get('executable_sha256'):
        raise ValueError('The app executable differs from the completed build.')
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier') != 'local.dervo.connected':
        raise ValueError('This package flow only supports the Dervo beta app.')
    audit['nativeVersion'] = native
    audit['declaredMinimumMacOS'] = info.get('LSMinimumSystemVersion')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)])
    audit['distributionSignature'] = distribution_signature(bundle, record)
    return release_id, bundle, files, audit


def package(release, output, instructions, kind='zip'):
    if kind not in ('zip', 'dmg'):
        raise ValueError('Choose ZIP or DMG packaging.')
    release, output, instructions = map(normal_path, (release, output, instructions))
    if output.exists() or output.is_relative_to(release) or release.is_relative_to(output):
        raise ValueError('Choose a new package folder outside the retained release.')
    text = instructions.read_text()
    if not text.strip():
        raise ValueError('Tester setup instructions are required.')
    release_id, bundle, files, audit = inspect_release(release)
    require_space(output.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Stage beside the destination so publication is a single rename. Only our
    # temporary folder is removed on failure; previous artifacts aren't touched.
    with tempfile.TemporaryDirectory(prefix='.dervo-package-', dir=output.parent) as temporary:
        work = Path(temporary)
        payload = work / 'payload'
        payload.mkdir()
        copied = payload / bundle.name
        run(['/usr/bin/ditto', '--norsrc', '--noextattr', str(bundle), str(copied)])
        if inventory(copied) != files:
            raise ValueError('The staged app differs from the verified build.')
        (payload / 'Start here.txt').write_text(text)
        ready = work / 'ready'
        ready.mkdir()
        artifact = ready / ('Dervo-' + release_id + '-macos.' + kind)
        if kind == 'zip':
            run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--noextattr', str(payload), str(artifact)])
            extracted = work / 'extracted'
            run(['/usr/bin/ditto', '-x', '-k', str(artifact), str(extracted)])
            check_payload(extracted, files, text)
        else:
            # The window a disk image opens: the app beside an Applications shortcut to drag it onto, so it runs from
            # Applications and can update itself (Harrison, 25 Sep: an app opened where it was unzipped can't).
            (payload / 'Applications').symlink_to('/Applications')
            run(['/usr/bin/hdiutil', 'create', '-quiet', '-srcfolder', str(payload),
                 '-volname', 'Dervo ' + release_id, '-format', 'UDZO', str(artifact)])
            check_disk_image(artifact, output.parent, files, text)
        # A running app mustn't change its bundle, but also verify the input hasn't
        # changed while the archive was being made.
        if inventory(bundle) != files:
            raise ValueError('The source app changed while packaging. Try again with a fixed build.')
        report = {'releaseId': release_id, 'profile': 'private-beta', 'artifact': artifact.name,
                  'sha256': digest(artifact), 'bytes': artifact.stat().st_size,
                  'bundleFiles': len(files), 'bundleAudit': audit,
                  'archiveReadback': 'passed', 'signatureVerification': 'passed',
                  'distributionApproved': False,
                  'limits': ['No fresh-Mac installation or manual upgrade was tested.',
                             'The app’s stapled ticket and Gatekeeper acceptance were checked on this Mac. Other Macs remain untested.',
                             'This command does not approve, publish or distribute the release.']}
        (ready / 'package.json').write_text(json.dumps(report, indent=2) + '\n')
        (ready / 'SHA256SUMS.txt').write_text(report['sha256'] + '  ' + artifact.name + '\n')
        publish(ready, output)
    return report


def check_payload(root, expected, instructions, disk_image=False):
    names = {p.name for p in root.iterdir()}
    # Disk images may contain filesystem-owned bookkeeping directories, and carry the Applications shortcut.
    wanted = {'Dervo Local.app', 'Start here.txt'} | ({'Applications'} if disk_image else set())
    if names - {'.fseventsd', '.Trashes', '.DS_Store'} != wanted:
        raise ValueError('The archive contains unexpected top-level entries.')
    if disk_image and not ((root / 'Applications').is_symlink() and os.readlink(root / 'Applications') == '/Applications'):
        raise ValueError('The disk image’s Applications shortcut doesn’t point at Applications.')
    bundle = root / 'Dervo Local.app'
    if inventory(bundle) != expected or (root / 'Start here.txt').read_text() != instructions:
        raise ValueError('The archive readback differs from the staged package.')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)])
    run(['/usr/bin/xcrun', 'stapler', 'validate', str(bundle)])
    run(['/usr/sbin/spctl', '--assess', '--type', 'execute', str(bundle)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--instructions', required=True)
    parser.add_argument('--format', choices=['zip', 'dmg'], default='zip')
    args = parser.parse_args()
    print(json.dumps(package(args.release, args.output, args.instructions, args.format), indent=2))
