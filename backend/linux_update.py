"""Update and restart on Linux (8 Oct 2026). Each Linux format updates from its own catalog rows (beta_updates.linux_format).

An AppImage updates in place, like a Mac app: Settings > Updates downloads the new AppImage, beta_download checks its
size, SHA-256 and release signature, and Update and restart swaps it for the file the person opened, keeping the old
one to go back to. That needs no administrator rights, and the file keeps its name, so menu entries still work.

A .deb or .rpm belongs to the system's package manager, and installing one runs its scripts as root. Dervo doesn't ask
for root itself (three reviewers, 8 Oct: a package in a folder the user can write could be swapped between Dervo's check
and a root install). Update and restart backs up the workspace, closes Dervo and opens the checked package in the
desktop's software installer, which asks for the password and installs it. The person opens Dervo again afterwards.
That narrows the gap between Dervo's last check and the install to processes running as the same user, which could
swap the file in Dervo's 0700 updates folder; it doesn't close it. Closing it needs a root helper that checks the
signature itself, or signed apt and dnf repositories.

The AppImage steps, in the app's switch helper (release_switch.py hands a plan with channel "appimage" here):
1. prepared: check the download again and that the AppImage can be replaced where it is.
2. backup: back up the workspace and, while the old Dervo is still open, copy its Python and linux_install.py into
   this switch's folder. The helper's own Python lives in the AppImage's mount, which goes when Dervo closes.
3. launch: start linux_install.py on that copy and exit. It waits for Dervo to close, swaps the file, opens the new
   Dervo, waits for it to say it started, and puts the old file back if anything fails.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import time
import uuid

from dervo_connections import Rejected
import platform_os
from release_handoff import WorkspaceLease, local_path

PLATFORM = 'linux-x64'
# Only these reach the new Dervo from the helper's environment: what a desktop app needs to find the session bus and
# the display. Same list as LINUX_ENVIRONMENT in src/launch.rs.
LAUNCH_ENVIRONMENT = ('HOME', 'XDG_RUNTIME_DIR', 'XDG_DATA_HOME', 'DBUS_SESSION_BUS_ADDRESS', 'DISPLAY',
                      'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_CURRENT_DESKTOP', 'TMPDIR')
PYTHON = Path('linux-python')
RUNNER = 'linux_install.py'
PLAN_FIELDS = {'format', 'channel', 'file', 'sha256', 'releaseId', 'build', 'current', 'currentRelease', 'workspace',
               'dataOverride', 'parentPid'}


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def appimage_problem(path):
    """Why an update can't replace the AppImage at ``path``, or None. It has to be the person's own file, in a folder
    they can write, not a link or a mount (AppImageLauncher's /run/user/.../appimagelauncherfs is one)."""
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        return 'Dervo can’t find its own AppImage. Reopen Dervo and try again.'
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or path.resolve() != path:
        return 'This AppImage is opened through a link. Open the AppImage file itself to update it here.'
    if info.st_uid != os.getuid():
        return 'This AppImage belongs to another user. Download the new one from the beta page.'
    folder = path.parent.stat()
    # Others who can write the folder could swap the file between the copy kept for going back and the switch, even
    # with the sticky bit (they can still leave a file where the new one goes). A group's write is fine only for the
    # person's own group.
    if folder.st_mode & stat.S_IWOTH or (folder.st_mode & stat.S_IWGRP and folder.st_gid != os.getgid()):
        return (f'Other people on this computer can change files in {path.parent}. Move the AppImage to a folder of '
                'yours, like ~/Applications.')
    if not os.access(path.parent, os.W_OK | os.X_OK) or str(path).startswith('/run/'):
        return f'Dervo can’t replace files in {path.parent}. Move the AppImage to a folder of yours, like ~/Applications.'
    return None


def stage(file, release, running_build, fmt):
    """Record a downloaded, checked AppImage or package as ready. ``release`` is its catalog row, ``fmt`` the format
    this Dervo was installed as."""
    file = local_path(file)
    if (release.get('platform') != PLATFORM or release.get('format') != fmt
            or not str(release.get('release_id', '')).endswith('-linux-' + fmt)
            or type(release.get('build_number')) is not int or release['build_number'] <= running_build):
        raise RuntimeError('The download isn’t a newer Dervo for this Linux install. Nothing was installed.')
    if digest(file) != release['sha256']:
        raise RuntimeError('The download changed after it was checked. Nothing was installed.')
    # Downloads are saved private (0600); an AppImage has to be executable to open. A package is made 0644, though its
    # folder stays 0700, so only this user and root can reach it: PackageKit installs as root.
    file.chmod(0o755 if fmt == 'appimage' else 0o644)
    staged = {'file': str(file), 'releaseId': release['release_id'], 'build': release['build_number'],
              'sha256': release['sha256'], 'format': fmt}
    (file.parent / 'release.json').write_text(json.dumps({**staged, 'state': 'staged', 'source': 'download'}, indent=2))
    return staged


def prepare(host, args, staged, resources):
    """The helper's plan for Update and restart. The app names only its own executable ($APPIMAGE for an AppImage,
    src/startup.rs); the download comes from the host."""
    from beta_update_host import running_version
    from release_switch import require_idle
    if type(args) is not dict or set(args) != {'currentExecutable', 'dataOverride', 'parentPid'}:
        raise Rejected('Unsupported update request')
    if type(args['dataOverride']) is not bool or type(args['parentPid']) is not int or args['parentPid'] < 2:
        raise Rejected('The native app must start the update.')
    require_idle(host)
    if not staged or 'file' not in staged:
        raise Rejected('Check for updates first. There’s no newer Dervo ready.')
    try:
        current = platform_os.app_executable(resources)
    except RuntimeError as exc:
        raise Rejected(str(exc)) from None
    if local_path(args['currentExecutable']) != current:
        raise Rejected('Update from the installed Dervo app.')
    channel = 'appimage' if staged['format'] == 'appimage' else 'package'
    if channel == 'appimage' and (problem := appimage_problem(current)):
        raise Rejected(problem)
    version = running_version(Path(resources))
    return {'format': 1, 'channel': channel, 'file': staged['file'], 'sha256': staged['sha256'],
            'releaseId': staged['releaseId'], 'build': staged['build'], 'current': str(current),
            'currentRelease': version['releaseId'], 'workspace': str(local_path(host.root)),
            'dataOverride': args['dataOverride'], 'parentPid': args['parentPid']}


def receive(stream, seconds=120):
    if not platform_os.wait_readable(stream.fileno(), seconds):
        raise RuntimeError('The old Dervo window didn’t finish the update. Reopen Dervo.')
    line = stream.readline(131073)
    if not line or len(line) > 131072:
        raise RuntimeError('The update was interrupted.')
    return json.loads(line)


def launch_environment(plan, source=os.environ):
    env = {name: source[name] for name in LAUNCH_ENVIRONMENT if name in source}
    env['PATH'] = '/usr/local/bin:/usr/bin:/bin'
    if plan['dataOverride']:
        env['DERVO_DATA_DIR'] = plan['workspace']
    return env


def write_recovery(folder, plan, env):
    """What to do by hand if an update stops halfway."""
    lines = ['#!/bin/sh']
    if 'DERVO_DATA_DIR' in env:
        lines.append("export DERVO_DATA_DIR='" + env['DERVO_DATA_DIR'].replace("'", "'\\''") + "'")
    if plan['channel'] == 'appimage':
        lines.append("exec '" + str(folder / 'previous.AppImage').replace("'", "'\\''") + "'")
        kept = 'The previous Dervo is kept as previous.AppImage, and open-previous-dervo.sh opens it with the same workspace.'
    else:
        lines.append("exec '" + plan['current'].replace("'", "'\\''") + "'")
        kept = ('The package manager keeps one Dervo installed. If the new one doesn’t open, download the previous '
                'version from the beta page and install it.')
    script = folder / 'open-previous-dervo.sh'
    script.write_text('\n'.join(lines) + '\n')
    script.chmod(0o700)
    (folder / 'Recovery.txt').write_text(
        'Dervo update\n\n' + kept + '\nThe workspace as it was before the update is in backup/workspace.\n'
        'Look at switch.json and launch.log before recovering anything. Don’t replace workspace files by hand.\n')


def run(plan, stream, *, start=subprocess.Popen, opener=subprocess.Popen, report=None, helper_pid=None, resources=None):
    """The helper's part of Update and restart on Linux (see the module docstring). Returns the switch record."""
    from release_switch import report as default_report, save_record
    report = report or default_report
    if (type(plan) is not dict or set(plan) != PLAN_FIELDS or plan['format'] != 1
            or plan['channel'] not in ('appimage', 'package') or type(plan['dataOverride']) is not bool
            or type(plan['parentPid']) is not int or plan['parentPid'] < 2 or type(plan['build']) is not int):
        raise RuntimeError('The update plan is invalid.')
    file, current, workspace = local_path(plan['file']), local_path(plan['current']), local_path(plan['workspace'])
    from app_update import updates_root
    # The last step removes the download's folder once the new version opens, so it must be one of Dervo's own.
    if file.parent.parent != updates_root():
        raise RuntimeError('The update plan is invalid.')
    if digest(file) != plan['sha256']:
        raise RuntimeError('The download changed after it was checked. Nothing was installed.')
    if plan['channel'] == 'appimage' and (problem := appimage_problem(current)):
        raise RuntimeError(problem)
    key = hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
    history = local_path(platform_os.app_support_dir() / 'Dervo/Version history' / key)
    if history.is_relative_to(workspace) or workspace.is_relative_to(history):
        raise RuntimeError('Version backups must be separate from the workspace.')
    folder = history / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    record = {**plan, 'state': 'prepared', 'directory': str(folder),
              'current': {'id': plan['currentRelease'], 'executable': str(current)},
              'target': {'id': plan['releaseId'], 'build': plan['build'], 'executable': str(current)}}
    save_record(folder, record)
    resources = Path(resources or Path(__file__).resolve().parent.parent)
    try:
        return _switch(plan, stream, record, folder, start, opener, report, helper_pid or os.getpid(), resources)
    except BaseException as exc:
        record.update(state='failed', error=str(exc)); save_record(folder, record)
        raise


def _switch(plan, stream, record, folder, start, opener, report, helper_pid, resources):
    from release_switch import save_record
    file, workspace = local_path(plan['file']), local_path(plan['workspace'])
    env = launch_environment(plan)
    write_recovery(folder, plan, env)
    report({'state': 'prepared'})
    if receive(stream) != {'action': 'backup'}:
        raise RuntimeError('Dervo cancelled the update.')
    with WorkspaceLease(workspace).acquire() as lease:
        lease.snapshot(folder / 'backup')
    if plan['channel'] == 'appimage':
        # While the old Dervo is open its AppImage is still mounted: copy the Python and the last step out of it now.
        shutil.copytree(resources / PYTHON, folder / 'python', symlinks=True)
        shutil.copy2(resources / 'backend' / RUNNER, folder / RUNNER)
    if digest(file) != plan['sha256']:
        raise RuntimeError('The download changed during the backup. Nothing was installed.')
    record['state'] = 'backed_up'; save_record(folder, record)
    report({'state': 'backed_up', 'directory': str(folder)})
    if receive(stream) != {'action': 'launch'}:
        raise RuntimeError('Dervo cancelled before installing the new version.')
    if plan['channel'] == 'appimage':
        (folder / 'install.json').write_text(json.dumps({
            'file': str(file), 'sha256': plan['sha256'], 'target': plan['current'],
            'previous': str(folder / 'previous.AppImage'), 'releaseId': plan['releaseId'], 'build': plan['build'],
            'env': env, 'waitPids': [plan['parentPid'], helper_pid], 'updates': str(file.parent)}, indent=2))
        record['state'] = 'handing_over'; save_record(folder, record)
        with (folder / 'launch.log').open('a') as log:
            start([str(folder / 'python/bin/python3.13'), '-E', '-s', '-B', str(folder / RUNNER), str(folder)],
                  env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **platform_os.new_group())
        return record
    # A package: wait for Dervo to close, so nothing of it is running while the package manager replaces it, then
    # hand the checked package to the desktop's software installer.
    deadline = time.monotonic() + 60
    while platform_os.process_running(plan['parentPid']):
        if time.monotonic() >= deadline:
            raise RuntimeError('The previous Dervo window is still open. Your workspace backup is ready; nothing was installed.')
        time.sleep(.1)
    if digest(file) != plan['sha256']:
        raise RuntimeError('The download changed while Dervo closed. Nothing was installed.')
    with (folder / 'launch.log').open('a') as log:
        if not hand_over(opener, platform_os.open_command(file), env, log):
            # No software installer took it: show the package in its folder instead, with the command beside it.
            (file.parent / 'How to install.txt').write_text(install_hint(file))
            hand_over(opener, platform_os.open_folder_command(file.parent), env, log)
            record['state'] = 'shown_package'
        else:
            record['state'] = 'opened_installer'
    save_record(folder, record)
    return record


HANDOVER_SECONDS = 5


def hand_over(opener, argv, env, log, seconds=HANDOVER_SECONDS):
    """Start ``argv`` (xdg-open) in its own session so it outlives this helper, never waiting on what it opens.

    False when it exits with an error within ``seconds``, which is how xdg-open says nothing took the file. Still
    running after that, or exited cleanly, counts as handed over."""
    process = opener(argv, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **platform_os.new_group())
    try:
        return process.wait(timeout=seconds) == 0
    except subprocess.TimeoutExpired:
        return True


def install_hint(file):
    if file.suffix == '.deb':
        command = f'sudo apt install {shlex.quote(str(file))}'
    else:
        quoted = shlex.quote(str(file))
        command = f'sudo dnf install {quoted}   (openSUSE: sudo zypper install {quoted})'
    return ('Dervo downloaded and checked this update, but no software installer opened it.\n\n'
            f'Install it from a terminal:\n\n    {command}\n\nThen open Dervo again.\n')
