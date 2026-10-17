"""Stage and verify native release metadata before signing a retained bundle."""
import json
from pathlib import Path
import plistlib
import re


def validate(version, release_id, build_number):
    if type(version) is not str or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('Set a numeric major.minor.patch product version before building.')
    if type(build_number) is not str or not re.fullmatch(r'[1-9][0-9]{0,19}', build_number):
        raise ValueError('The native build number must be a positive integer.')
    if type(release_id) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', release_id):
        raise ValueError('Choose a plain release ID before building.')


def stage(work, release_id, build_number):
    work = Path(work)
    config_path = work / 'tauri.conf.json'
    plist_path = work / 'Info.plist'
    if config_path.is_symlink() or plist_path.is_symlink():
        raise ValueError('Version metadata must use regular source files.')
    config = json.loads(config_path.read_text())
    version = config.get('version')
    validate(version, release_id, build_number)
    info = plistlib.loads(plist_path.read_bytes())
    config.setdefault('bundle', {}).setdefault('macOS', {})['bundleVersion'] = build_number
    # Set both source surfaces explicitly. An old custom plist cannot override
    # Tauri's generated values and silently reuse another build's identity.
    info.update(CFBundleShortVersionString=version, CFBundleVersion=build_number,
                DervoReleaseID=release_id)
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    plist_path.write_bytes(plistlib.dumps(info))
    return {'productVersion': version, 'buildNumber': build_number, 'releaseId': release_id}


def verify(bundle, expected):
    if type(expected) is not dict or set(expected) != {'productVersion', 'buildNumber', 'releaseId'}:
        raise ValueError('Native version metadata needs product version, build number and release ID.')
    validate(expected['productVersion'], expected['releaseId'], expected['buildNumber'])
    path = Path(bundle) / 'Contents/Info.plist'
    if path.is_symlink():
        raise ValueError('Native version metadata must be a regular bundled plist.')
    info = plistlib.loads(path.read_bytes())
    if (info.get('CFBundleShortVersionString') != expected['productVersion']
            or info.get('CFBundleVersion') != expected['buildNumber']
            or info.get('DervoReleaseID') != expected['releaseId']):
        raise ValueError('Native bundle version differs from the retained release.')
    return expected.copy()
