"""Update and restart on Windows (b16/windows, milestone 5 step 6 in PLAN-windows-m5-20260926.md).

A Mac update swaps one .app bundle for another (app_update, release_switch). Windows has an installer instead, which
replaces the files in Dervo's install folder, so it goes like this:

1. stage: Settings > Updates downloads the installer into Updates/<release>/, and beta_download has already checked its
   size, its SHA-256 and the release key's signature (release_signature). This records it as ready.
2. prepare: the host plans the switch for the app, which names only itself (src/startup.rs, update_version).
3. run, in the app's switch helper (release_switch.py, which hands a plan with channel "installer" here): the same
   steps as on a Mac up to the restart. Check the installer again, back up the workspace, wait for Dervo to close.
   Then copy the install folder into this switch's folder in Version history, the copy to go back to, and start
   windows_install.py on that copy's Python. It runs the installer, opens the new Dervo and waits for it to confirm,
   and puts the copy back if anything fails. This helper exits first, because it runs on the Python in the install
   folder, which the installer replaces.

Nothing is installed without the person's Update and restart, and nothing here asks Windows for administrator rights:
the installer is per-user (tauri.windows.conf.json, installMode currentUser).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid

from dervo_connections import Rejected
import platform_os
from release_handoff import WorkspaceLease, local_path

PLATFORM = 'windows-x64'
# Only these reach the new Dervo from the helper's environment when it opens: what a Windows app needs to find the
# system, the user's folders and WebView2. The app passes them to its helper (src/version_switch.rs).
LAUNCH_ENVIRONMENT = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'USERPROFILE', 'USERNAME', 'HOMEDRIVE', 'HOMEPATH',
                      'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
                      'COMMONPROGRAMFILES', 'TEMP', 'TMP', 'PATH', 'PATHEXT', 'COMPUTERNAME', 'OS',
                      'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE')
# In the install folder, and in the copy of it each update keeps: the bundled Python and the install step.
PYTHON = Path('windows-python', 'python.exe')
RUNNER = Path('backend', 'windows_install.py')
PLAN_FIELDS = {'format', 'channel', 'installer', 'sha256', 'releaseId', 'build', 'current', 'currentRelease',
               'workspace', 'dataOverride', 'parentPid'}


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def stage(installer, release, running_build):
    """Record a downloaded, checked installer as ready for Update and restart. ``release`` is its catalog row."""
    installer = local_path(installer)
    if (release.get('platform') != PLATFORM or release.get('format') != 'exe'
            or not str(release.get('release_id', '')).endswith('-windows')
            or type(release.get('build_number')) is not int or release['build_number'] <= running_build):
        raise RuntimeError('The downloaded installer isn’t a newer Windows release. Nothing was installed.')
    if digest(installer) != release['sha256']:
        raise RuntimeError('The downloaded installer changed after it was checked. Nothing was installed.')
    staged = {'installer': str(installer), 'releaseId': release['release_id'], 'build': release['build_number'],
              'sha256': release['sha256']}
    (installer.parent / 'release.json').write_text(json.dumps({**staged, 'state': 'staged', 'source': 'download'},
                                                              indent=2))
    return staged


def prepare(host, args, staged, resources):
    """The helper's plan for Update and restart. The app names only its own exe; the installer comes from the host."""
    from beta_update_host import running_version
    from release_switch import require_idle
    if type(args) is not dict or set(args) != {'currentExecutable', 'dataOverride', 'parentPid'}:
        raise Rejected('Unsupported update request')
    if type(args['dataOverride']) is not bool or type(args['parentPid']) is not int or args['parentPid'] < 2:
        raise Rejected('The native app must start the update.')
    require_idle(host)
    if not staged or 'installer' not in staged:
        raise Rejected('Check for updates first. There’s no newer Dervo ready.')
    try:
        current = platform_os.app_executable(resources)
    except RuntimeError as exc:
        raise Rejected(str(exc)) from None
    if os.path.normcase(str(local_path(args['currentExecutable']))) != os.path.normcase(str(current)):
        raise Rejected('Update from the installed Dervo app.')
    version = running_version(Path(resources))
    return {'format': 1, 'channel': 'installer', 'installer': staged['installer'], 'sha256': staged['sha256'],
            'releaseId': staged['releaseId'], 'build': staged['build'], 'current': str(current),
            'currentRelease': version['releaseId'], 'workspace': str(local_path(host.root)),
            'dataOverride': args['dataOverride'], 'parentPid': args['parentPid']}


def receive(stream, seconds=120):
    """The app's next instruction on the helper's stdin. Windows' select() takes sockets only (platform_os)."""
    if not platform_os.wait_readable(stream.fileno(), seconds):
        raise RuntimeError('The old Dervo window didn’t finish the update. Reopen Dervo.')
    line = stream.readline(131073)
    if not line or len(line) > 131072:
        raise RuntimeError('The update was interrupted.')
    return json.loads(line)


def launch_environment(plan, source=os.environ):
    env = {name: source[name] for name in LAUNCH_ENVIRONMENT if name in source}
    if plan['dataOverride']:
        env['DERVO_DATA_DIR'] = plan['workspace']
    return env


def write_recovery(folder, executable, env):
    """What to do by hand if an update stops halfway: open the kept copy of the previous Dervo."""
    lines = ['@echo off']
    if 'DERVO_DATA_DIR' in env:
        lines.append('set "DERVO_DATA_DIR=' + env['DERVO_DATA_DIR'] + '"')
    lines.append('start "" "' + str(executable) + '"')
    (folder / 'Open previous Dervo.cmd').write_text('\r\n'.join(lines) + '\r\n')
    (folder / 'Recovery.txt').write_text(
        'Dervo update\r\n\r\nThe previous Dervo is kept in the previous folder, and Open previous Dervo.cmd opens it '
        'with the same workspace.\r\nThe workspace as it was before the update is in backup\\workspace.\r\n'
        'Look at switch.json and launch.log before recovering anything. Don\'t replace workspace files by hand.\r\n')


def run(plan, stream, *, start=subprocess.Popen, report=None, helper_pid=None):
    """The helper's part of Update and restart on Windows (see the module docstring). Returns the switch record once
    windows_install.py has been started, which waits for ``helper_pid`` (this process) to exit first."""
    from release_switch import report as default_report, save_record
    report = report or default_report
    if (type(plan) is not dict or set(plan) != PLAN_FIELDS or plan['format'] != 1 or plan['channel'] != 'installer'
            or type(plan['dataOverride']) is not bool or type(plan['parentPid']) is not int or plan['parentPid'] < 2
            or type(plan['build']) is not int):
        raise RuntimeError('The update plan is invalid.')
    installer, current = local_path(plan['installer']), local_path(plan['current'])
    install_dir, workspace = current.parent, local_path(plan['workspace'])
    if digest(installer) != plan['sha256']:
        raise RuntimeError('The installer changed after it was checked. Nothing was installed.')
    if not (install_dir / RUNNER).is_file() or not (install_dir / PYTHON).is_file() or not current.is_file():
        raise RuntimeError('This Dervo is missing its own files. Reinstall it from the beta page.')
    key = hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
    history = local_path(platform_os.app_support_dir() / 'Dervo/Version history' / key)
    if history.is_relative_to(workspace) or workspace.is_relative_to(history) or history.is_relative_to(install_dir):
        raise RuntimeError('Version backups must be separate from the workspace and the app.')
    folder = history / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    record = {**plan, 'state': 'prepared', 'directory': str(folder),
              'current': {'id': plan['currentRelease'], 'executable': str(current), 'installDir': str(install_dir)},
              'target': {'id': plan['releaseId'], 'build': plan['build'], 'executable': str(current)}}
    save_record(folder, record)
    try:
        return _switch(plan, stream, record, folder, start, report, helper_pid or os.getpid())
    except BaseException as exc:
        halfway = record['state'] in ('keeping_previous', 'handing_over')
        record.update(state='failed', error=str(exc)); save_record(folder, record)
        if halfway:
            subprocess.run(platform_os.open_folder_command(folder), check=False, capture_output=True, timeout=10)
        raise


def _switch(plan, stream, record, folder, start, report, helper_pid):
    from release_switch import save_record
    installer, current = local_path(plan['installer']), local_path(plan['current'])
    install_dir, workspace, previous = current.parent, local_path(plan['workspace']), folder / 'previous'
    env = launch_environment(plan)
    write_recovery(folder, previous / current.name, env)
    report({'state': 'prepared'})
    if receive(stream) != {'action': 'backup'}:
        raise RuntimeError('Dervo cancelled the update.')
    with WorkspaceLease(workspace).acquire() as lease:
        lease.snapshot(folder / 'backup')
    if digest(installer) != plan['sha256']:
        raise RuntimeError('The installer changed during the backup. Nothing was installed.')
    record['state'] = 'backed_up'; save_record(folder, record)
    report({'state': 'backed_up', 'directory': str(folder)})
    if receive(stream) != {'action': 'launch'}:
        raise RuntimeError('Dervo cancelled before installing the new version.')
    deadline = time.monotonic() + 60
    while platform_os.process_running(plan['parentPid']):
        if time.monotonic() >= deadline:
            raise RuntimeError('The previous Dervo window is still open. Your workspace backup is ready; nothing was installed.')
        time.sleep(.1)
    # Nothing else may have opened the workspace since the backup. The new Dervo takes this lock too.
    with WorkspaceLease(workspace).acquire():
        pass
    record['state'] = 'keeping_previous'; save_record(folder, record)
    shutil.copytree(install_dir, previous)
    (folder / 'install.json').write_text(json.dumps({
        'installer': str(installer), 'sha256': plan['sha256'], 'installDir': str(install_dir), 'previous': str(previous),
        'executable': str(current), 'releaseId': plan['releaseId'], 'build': plan['build'], 'env': env,
        'waitPid': helper_pid, 'updates': str(installer.parent)}, indent=2))
    record['state'] = 'handing_over'; save_record(folder, record)
    # The kept copy's own Python runs the install, so nothing in the install folder is open while the installer runs.
    # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW: it outlives this helper, and no console flashes up.
    options = {'creationflags': 0x00000200 | 0x08000000} if platform_os.WINDOWS else {'start_new_session': True}
    with (folder / 'launch.log').open('a') as log:
        start([str(previous / PYTHON), '-E', '-s', '-B', str(previous / RUNNER), str(folder)],
              env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
    return record
