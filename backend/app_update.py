"""One-click updates: find a newer Dervo, stage it, and plan the switch that replaces this app.

A newer Dervo comes from one of two places. On a Mac that builds Dervo, the release folder holding this app has newer
builds beside it. For everyone else, the private beta publishes an installer, which Updates downloads, checks against
its published size and SHA-256, and unpacks here. Either way the switch is release_switch.run: back up the workspace,
wait for this window to close, open the new app and confirm it started.

After a switch there's one earlier copy to go back to and no pile of old ones. A downloaded app takes this app's
place, and this app moves into that switch's version-history folder. A local build keeps its own release folder, and
builds older than the newest two lose their app bundle, never their logs, source archive or workspace folders. An app
that's running, or has files open, is never removed.
"""
import json
import os
from pathlib import Path
import shutil
from subprocess import run as run_process

from dervo_connections import Rejected
import platform_os
from release_handoff import file_hash, local_path

APP = 'Dervo Local.app'
KEEP_APPS = 2
IDENTIFIER = 'local.dervo.connected'


def updates_root():
    return local_path(platform_os.app_support_dir() / 'Dervo/Updates')


def bundle_from_executable(executable):
    exe = local_path(executable)
    if exe.name != 'dervo-local' or exe.parent.name != 'MacOS' or exe.parent.parent.name != 'Contents':
        raise Rejected('Update from a packaged Dervo app.')
    return exe.parents[2]


MOVE_FIRST = ('Dervo can’t update itself from where it’s running. Quit Dervo, move Dervo Local.app into your '
              'Applications folder, open it from there, then update again.')
NO_PERMISSION = ('Dervo can’t replace itself in this folder with your account’s permissions. Download the new version '
                 'from trydervo.com/beta and drag it into Applications.')


def install_problem(bundle, staged=None):
    """Why a downloaded app can't take this app's place, in plain words, or None. A downloaded app opened without
    moving it first runs from a read-only copy (macOS App Translocation), and an app on a disk image or another disk
    can't be swapped by rename either. Checked before anything is backed up, so a failed update leaves no folder."""
    bundle = Path(bundle)
    if '/AppTranslocation/' in str(bundle):
        return MOVE_FIRST
    in_applications = bundle.parent in (Path('/Applications'), Path.home() / 'Applications')
    try:
        if not (os.access(bundle, os.W_OK) and os.access(bundle.parent, os.W_OK)):
            return NO_PERMISSION if in_applications else MOVE_FIRST
        # A rename can't cross disks. Compare with where the download was unpacked once that folder exists.
        if staged is not None and Path(staged).parent.is_dir() and os.stat(bundle.parent).st_dev != os.stat(Path(staged).parent).st_dev:
            return MOVE_FIRST
    except OSError:
        return MOVE_FIRST
    return None


def running_bundle(resources):
    """This app's bundle when running from one (``resources`` is Contents/Resources), else None."""
    resources = Path(resources)
    if resources.name != 'Resources' or resources.parent.name != 'Contents' or resources.parent.parent.suffix != '.app':
        return None
    return resources.parent.parent


def marker(bundle):
    data = json.loads(local_path(Path(bundle) / 'Contents/Resources/runtime/dervo-preview.json').read_text())
    version = data.get('nativeVersion') if type(data) is dict else None
    if type(version) is not dict or data.get('releaseId') != version.get('releaseId'):
        raise RuntimeError('This Dervo app has no readable version.')
    return {'releaseId': version['releaseId'], 'build': int(version['buildNumber']), 'nativeVersion': version}


def built(folder):
    """A retained local build: its app and a release record that names that app as built. Anything else is None."""
    folder = Path(folder)
    app, record = folder / APP, folder / 'release.json'
    if folder.is_symlink() or not folder.is_dir() or app.is_symlink() or not app.is_dir() or record.is_symlink() or not record.is_file():
        return None
    try:
        data = json.loads(record.read_text())
        number = int(data['native_version']['buildNumber'])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if data.get('state') != 'built' or data.get('bundle') != str(app):
        return None
    return {'bundle': str(app), 'releaseId': data.get('id') or folder.name, 'build': number}


def local_candidate(current_bundle):
    """The newest build beside this app in its release folder, when it's newer than this one."""
    current = local_path(current_bundle)
    own = built(current.parent)
    if own is None or own['bundle'] != str(current):
        return None
    best = None
    for folder in sorted(current.parent.parent.iterdir()):
        if folder == current.parent:
            continue
        found = built(folder)
        if found and found['build'] > own['build'] and (best is None or found['build'] > best['build']):
            best = found
    return best


def in_use(bundle):
    """Whether a process runs from this app or holds a file inside it open."""
    bundle = str(local_path(bundle))
    listed = run_process(['/bin/ps', '-axo', 'command='], capture_output=True, text=True, timeout=20, check=False)
    if listed.returncode or any(line.startswith(bundle + '/') for line in listed.stdout.splitlines()):
        return True
    opened = run_process(['/usr/sbin/lsof', '-t', '+D', bundle], capture_output=True, text=True, timeout=60, check=False)
    return bool(opened.stdout.strip())


def prune(root, keep=KEEP_APPS, protect=()):
    """Remove the app bundle of every local build older than the newest ``keep``. Protected, running or open apps stay,
    and so does everything else in each release folder. Returns what was removed and what was kept back and why."""
    root = local_path(root)
    protected = {str(local_path(path)) for path in protect}
    builds = sorted((found for folder in root.iterdir() if (found := built(folder))), key=lambda found: found['build'], reverse=True)
    removed, skipped = [], []
    for found in builds[keep:]:
        if found['bundle'] in protected:
            skipped.append({**found, 'reason': 'protected'})
            continue
        if in_use(found['bundle']):
            skipped.append({**found, 'reason': 'in use'})
            continue
        shutil.rmtree(found['bundle'])
        removed.append(found)
    return {'removed': removed, 'skipped': skipped}


def stage(archive, release, running_build, *, verify):
    """Unpack a checked installer zip into Updates/<release>/ and record it like a local build, so the switch verifies
    it the same way. ``verify`` is release_switch.verify_bundle. The archive is removed once the app is staged."""
    archive = local_path(archive)
    folder = archive.parent
    unpacked = folder / 'unpacked'
    if unpacked.exists():
        shutil.rmtree(unpacked)
    unpacked.mkdir(mode=0o700)
    result = run_process(['/usr/bin/ditto', '-x', '-k', str(archive), str(unpacked)], capture_output=True, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError('The downloaded update couldn’t be unpacked. Nothing was installed.')
    apps = [path for path in unpacked.rglob(APP) if path.is_dir() and not path.is_symlink()]
    if len(apps) != 1:
        raise RuntimeError('The downloaded update doesn’t contain exactly one Dervo app. Nothing was installed.')
    app = folder / APP
    if app.exists():
        shutil.rmtree(app)
    apps[0].rename(app)
    shutil.rmtree(unpacked)
    version = marker(app)
    if version['releaseId'] != release or version['build'] <= running_build:
        shutil.rmtree(app)
        raise RuntimeError('The downloaded app isn’t the newer release it was published as. Nothing was installed.')
    executable = app / 'Contents/MacOS/dervo-local'
    (folder / 'release.json').write_text(json.dumps({'id': release, 'state': 'built', 'bundle': str(app),
                                                     'native_version': version['nativeVersion'], 'executable_sha256': file_hash(executable),
                                                     'source': 'download'}, indent=2))
    verify(app, release_id=release, handoff=True)
    archive.unlink()
    return {'bundle': str(app), 'releaseId': release, 'build': version['build']}


def prepare(host, args, staged=None):
    """The native plan for Update and restart. ``staged`` is a downloaded app from Updates; without it, a newer local
    build beside this app is used."""
    from release_switch import require_idle
    if platform_os.WINDOWS:
        import windows_update
        return windows_update.prepare(host, args, staged, Path(__file__).resolve().parent.parent)
    if platform_os.LINUX:
        import linux_update
        return linux_update.prepare(host, args, staged, Path(__file__).resolve().parent.parent)
    if type(args) is not dict or set(args) != {'currentExecutable', 'dataOverride', 'parentPid'}:
        raise Rejected('Unsupported update request')
    if type(args['dataOverride']) is not bool or type(args['parentPid']) is not int or args['parentPid'] < 2:
        raise Rejected('The native app must start the update.')
    require_idle(host)
    current = bundle_from_executable(args['currentExecutable'])
    target = staged or local_candidate(current)
    if not target:
        raise Rejected('Check for updates first. There’s no newer Dervo ready.')
    if target['bundle'] == str(current):
        raise Rejected('This version is already open.')
    if staged and (problem := install_problem(current, staged['bundle'])):
        raise Rejected(problem)
    return {'format': 1, 'target': target['bundle'], 'current': str(current), 'releaseId': target['releaseId'],
            'workspace': str(local_path(host.root)), 'dataOverride': args['dataOverride'], 'parentPid': args['parentPid'],
            'channel': 'download' if staged else 'local'}


def swap_in(current, staged, keep):
    """Put a downloaded app where this app is, keeping this app in ``keep``. Returns the new bundle path."""
    current, staged, keep = Path(current), Path(staged), Path(keep)
    keep.mkdir(mode=0o700, exist_ok=True)
    previous = keep / APP
    os.rename(current, previous)
    try:
        os.rename(staged, current)
    except OSError:
        os.rename(previous, current)
        raise
    return current, previous


def swap_back(current, previous, staged):
    """Undo swap_in when the new app didn't start: the downloaded app goes back to Updates, this app back in place."""
    os.rename(current, staged)
    os.rename(previous, current)


def clear_history_apps(history, keep_folder):
    """After a downloaded update opened, earlier switches' kept apps go; the one this switch kept stays."""
    removed = []
    for folder in Path(history).iterdir():
        app = folder / 'previous' / APP
        if folder != Path(keep_folder) and app.is_dir() and not app.is_symlink() and not in_use(app):
            shutil.rmtree(app)
            removed.append(str(app))
    return removed
