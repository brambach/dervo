"""Native-only version handoff. A provider can't request a workspace restart.

The native process owns the confirmation and stops its host. This helper verifies
both apps, backs up the stopped workspace and waits for the old window to exit.
It never overwrites a retained app or automatically restores older workspace data.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import select
import shlex
import subprocess
import sys
import time
import uuid
from dervo_connections import Rejected
import platform_os
from release_handoff import WorkspaceLease, file_hash, local_path, verified_backup, restore_snapshot


def require_idle(host):
    from project_lifecycle import idle
    host.tick()
    for row in host.store.db.execute('SELECT id, cwd FROM projects'):
        try:
            # A version switch keeps the workspace and its unknown outcomes. Only active work must stop.
            idle(host, row[0], refused='This update hasn’t started.', running_only=True)
        except Rejected as exc:
            raise Rejected('Stop the running work before switching versions. Project: ' + str(row[1]) + '. ' + str(exc)) from None
    if any(not turn.closed for turn in host.turns.values()):
        raise Rejected('Wait for message cleanup before switching versions.')
    if any(not login.closed for login in host.logins.values()):
        raise Rejected('Finish or cancel sign-in before switching versions.')
    if any(not job.done() for jobs in (host.checks, host.catalogs) for job in jobs.values()):
        raise Rejected('Wait for account and model checks before switching versions.')
    session = getattr(host, 'account_session', None)
    if session and getattr(session, 'operation', None):
        raise Rejected('Finish Dervo sign-in before switching versions.')


def prepare(host, args):
    if type(args) is not dict or set(args) != {'projectId', 'currentExecutable', 'dataOverride', 'parentPid'}:
        raise Rejected('Unsupported version switch request')
    if (type(args['projectId']) is not str or type(args['dataOverride']) is not bool
            or type(args['parentPid']) is not int or args['parentPid'] < 2):
        raise Rejected('The native app must select the project and workspace for this switch.')
    project = host.store.db.execute('SELECT id FROM projects WHERE id=?', (args['projectId'],)).fetchone()
    if not project:
        raise Rejected('Choose the project that built this preview.')
    require_idle(host)
    previews = getattr(host, 'previews', None)
    job = previews.read(project['id']) if previews else None
    if not job or job.get('state') != 'built' or not job.get('bundle'):
        raise Rejected('Build and try a successful preview before using it with your workspace.')
    current = local_path(args['currentExecutable'])
    if current.name != 'dervo-local' or current.parent.name != 'MacOS' or current.parent.parent.name != 'Contents':
        raise Rejected('Switch versions from a packaged Dervo app.')
    target = local_path(job['directory']) / 'Dervo Local.app'
    if str(target) != job['bundle'] or target == current.parents[2]:
        raise Rejected('Choose a different retained preview app.')
    return {'format': 1, 'target': str(target), 'current': str(current.parents[2]),
            'releaseId': job['id'], 'workspace': str(local_path(host.root)),
            'dataOverride': args['dataOverride'], 'parentPid': args['parentPid']}


def signing_team(bundle):
    """Return the signing team, or None only for an explicitly identified ad hoc signature."""
    result = subprocess.run(['/usr/bin/codesign', '-d', '--verbose=2', str(local_path(bundle))],
                            capture_output=True, timeout=60, check=False)
    text = result.stderr.decode(errors='replace') if result.stderr else ''
    match = re.search(r'^TeamIdentifier=(.*)$', text, re.M)
    team = match.group(1).strip() if match else ''
    if result.returncode == 0:
        if re.fullmatch(r'[A-Z0-9]{10}', team):
            return team
        if team == 'not set' and re.search(r'^Signature=adhoc$', text, re.M):
            return None
    raise RuntimeError('The app’s signing identity couldn’t be checked. Nothing was switched. Try again or rebuild the app.')


def require_same_team(current_team, target):
    """Once the running app is Developer ID signed, an update must come from the same Team ID. An ad hoc app has no
    team, so it keeps today's checks (the Ed25519 release signature and codesign --verify) and nothing more."""
    if current_team is None:
        return None
    found = signing_team(target)
    if found != current_team:
        raise RuntimeError('This Dervo isn’t signed by the developer who signed the app you’re running. Nothing was switched.')
    return found


def verify_bundle(bundle, *, release_id=None, handoff=False, team=None):
    bundle = local_path(bundle)
    executable = local_path(bundle / 'Contents/MacOS/dervo-local')
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError('The retained Dervo executable is missing. Nothing was switched.')
    info_path = local_path(bundle / 'Contents/Info.plist')
    with info_path.open('rb') as stream:
        info = plistlib.load(stream)
    if info.get('CFBundleIdentifier') != 'local.dervo.connected':
        raise RuntimeError('This app isn’t a Dervo build. Nothing was switched.')
    marker_path = local_path(bundle / 'Contents/Resources/runtime/dervo-preview.json')
    marker = json.loads(marker_path.read_text())
    if type(marker) is not dict or not isinstance(marker.get('releaseId'), str):
        raise RuntimeError('The retained app has no readable Dervo version.')
    if release_id is not None and marker['releaseId'] != release_id:
        raise RuntimeError('The selected app version doesn’t match the build record.')
    if handoff and marker.get('workspaceHandoff') != 1:
        raise RuntimeError('Rebuild this preview before switching. It doesn’t include workspace recovery support.')
    result = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                            capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError('The retained app’s signature check failed. Rebuild it before switching.')
    require_same_team(team, bundle)
    if release_id is not None:
        record_path = local_path(bundle.parent / 'release.json')
        record = json.loads(record_path.read_text())
        if (record.get('state') != 'built' or record.get('bundle') != str(bundle)
                or record.get('executable_sha256') != file_hash(executable)):
            raise RuntimeError('The retained executable has changed since its build checks.')
    return {'bundle': str(bundle), 'id': marker['releaseId'], 'executable': str(executable),
            'executable_sha256': file_hash(executable)}


def launch_environment(plan):
    # No DERVO_PYTHON (beta.17): every version finds its own Python. A built app since beta.17 runs the copy it carries,
    # and 16.4 or older uses the framework when it's unset. This helper's interpreter is the current app's own runtime,
    # which an older app can't use (its keychain and confinement checks refuse it) and which moves with this app.
    env = {'HOME': str(Path.home()), 'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'}
    # Preserve the native WebKit store mode. Setting an override for a default
    # workspace would turn it into an incognito window and hide saved drafts.
    if plan['dataOverride']:
        env['DERVO_DATA_DIR'] = plan['workspace']
    return env


def save_record(folder, record):
    temporary = folder / 'switch.tmp'
    temporary.write_text(json.dumps(record, indent=2)); temporary.replace(folder / 'switch.json')


def receive(stream, seconds=120):
    if not select.select([stream], [], [], seconds)[0]:
        raise RuntimeError('The old Dervo window didn’t finish the version switch. Reopen your current app.')
    line = stream.readline(131073)
    if not line or len(line) > 131072:
        raise RuntimeError('The native version switch was interrupted.')
    return json.loads(line)


def report(value):
    try:
        print(json.dumps(value), flush=True)
    except BrokenPipeError:
        pass  # The old native process has already exited after launch commit.


def write_launcher(path, executable, env, *, exact=False):
    lines = ['#!/bin/zsh', 'unset DERVO_DATA_DIR DERVO_PYTHON DERVO_HANDOFF_FD DERVO_OPEN_THIS_VERSION']
    lines += ['export ' + name + '=' + shlex.quote(value) for name, value in env.items()
              if name in ('DERVO_DATA_DIR', 'DERVO_PYTHON')]
    if exact:
        lines.append('export DERVO_OPEN_THIS_VERSION=1')
    lines += ['exec ' + shlex.quote(executable)]
    path.write_text('\n'.join(lines) + '\n'); path.chmod(0o700)


def open_target(target, env, folder):
    read_fd, write_fd = os.pipe()
    try:
        with (folder / 'launch.log').open('a') as log:
            process = subprocess.Popen([target['executable']], env={**env, 'DERVO_HANDOFF_FD': str(write_fd)},
                                       stdout=log, stderr=log, **platform_os.new_group(), pass_fds=(write_fd,))
    finally:
        os.close(write_fd)
    try:
        deadline = time.monotonic() + 60
        data = b''
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('The new Dervo app stopped during startup. Your backup and previous app are retained.')
            if select.select([read_fd], [], [], .1)[0]:
                chunk = os.read(read_fd, 4096)
                if not chunk:
                    raise RuntimeError('The new Dervo app closed its startup connection before confirming it was ready.')
                data += chunk
                if len(data) > 8192:
                    raise RuntimeError('The new Dervo app returned an unreadable startup result.')
                if b'\n' in data:
                    result = json.loads(data.split(b'\n')[0])
                    if result.get('ready') is not True:
                        raise RuntimeError('The new Dervo app couldn’t open your workspace. ' + str(result.get('detail', 'Inspect the launch log.')))
                    return process.pid
        raise RuntimeError('The new app hasn’t confirmed startup. It may still be opening. Inspect it before reopening another version.')
    finally:
        os.close(read_fd)


def run(plan, stream=sys.stdin):
    if type(plan) is dict and plan.get('channel') == 'installer':
        # Windows: the new version comes as an installer, not an app bundle (windows_update).
        import windows_update
        return windows_update.run(plan, stream)
    if type(plan) is dict and plan.get('channel') in ('appimage', 'package'):
        # Linux: an AppImage swapped in place, or a package for the desktop's software installer (linux_update).
        import linux_update
        return linux_update.run(plan, stream)
    fields = {'format', 'target', 'current', 'releaseId', 'workspace', 'dataOverride', 'parentPid'}
    if type(plan) is not dict or set(plan) not in (fields, fields | {'rollback'}, fields | {'channel'}):
        raise RuntimeError('The version switch plan is invalid.')
    channel = plan.get('channel')
    if channel not in (None, 'local', 'download'):
        raise RuntimeError('The version switch plan is invalid.')
    if (plan['format'] != 1 or type(plan['dataOverride']) is not bool
            or type(plan['parentPid']) is not int or plan['parentPid'] < 2):
        raise RuntimeError('The native launch context is invalid.')
    workspace = local_path(plan['workspace'])
    current = verify_bundle(plan['current'])
    # A Developer ID app only ever switches to a bundle its own team signed (a rollback included). Ad hoc: no team.
    team = signing_team(plan['current'])
    target = verify_bundle(plan['target'], release_id=plan['releaseId'], handoff=True, team=team)
    rollback_folder = None
    if 'rollback' in plan:
        from release_history import switch_record
        rollback_folder, previous = switch_record(workspace, plan['rollback'])
        if (previous['target'] != current or previous['current'] != target
                or previous.get('rollback') or previous.get('dataOverride') != plan['dataOverride']):
            raise RuntimeError('The selected rollback doesn’t match these retained app versions.')
        verified_backup(rollback_folder / 'backup', workspace)
    if current['bundle'] == target['bundle']:
        raise RuntimeError('This version is already open.')
    if channel == 'download':
        # A downloaded app takes this app's place by rename. Say so before a backup folder exists if it can't.
        import app_update
        problem = app_update.install_problem(current['bundle'], target['bundle'])
        if problem:
            raise RuntimeError(problem)
    key = hashlib.sha256(str(workspace).encode()).hexdigest()[:24]
    history = local_path(platform_os.app_support_dir() / 'Dervo/Version history' / key)
    if history.is_relative_to(workspace) or workspace.is_relative_to(history):
        raise RuntimeError('Version backups must be separate from the workspace.')
    folder = history / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    os.chmod(history, 0o700); os.chmod(folder, 0o700)
    record = {**plan, 'state': 'prepared', 'current': current, 'target': target, 'directory': str(folder)}
    save_record(folder, record)
    env = launch_environment(plan)
    write_launcher(folder / 'Open previous Dervo.command', current['executable'], env, exact=True)
    (folder / 'Recovery.txt').write_text('Dervo version switch\n\nYour previous app is retained. Open previous Dervo.command uses the same workspace.\nThe verified pre-switch workspace copy is in backup/workspace when backup completed.\nA previous app may not support changes made by a newer version. Don’t replace workspace files manually.\nInspect switch.json and launch.log before recovery. An older workspace restore must preserve newer work first.\n')
    try:
        report({'state': 'prepared'})
        if receive(stream) != {'action': 'backup'}:
            raise RuntimeError('Dervo cancelled the version switch.')
        with WorkspaceLease(workspace).acquire() as lease:
            lease.snapshot(folder / 'backup')
        # Recheck after the potentially long backup, before committing to quit.
        verify_bundle(plan['target'], release_id=plan['releaseId'], handoff=True, team=team)
        record['state'] = 'backed_up'; save_record(folder, record)
        report({'state': 'backed_up', 'directory': str(folder)})
        if receive(stream) != {'action': 'launch'}:
            raise RuntimeError('Dervo cancelled before opening the new version.')
        deadline = time.monotonic() + 60
        while True:
            try:
                os.kill(plan['parentPid'], 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('The previous Dervo window is still open. Your workspace backup is ready; nothing was launched.')
            time.sleep(.1)
        # A second host can acquire the workspace after backup. Refuse to launch
        # another version if that happened. The new host also takes this lock.
        with WorkspaceLease(workspace).acquire() as lease:
            if rollback_folder is not None:
                record['state'] = 'restoring'; save_record(folder, record)
                restored = restore_snapshot(lease, rollback_folder / 'backup', folder / 'newer-work',
                                            confirmation='RESTORE backup')
                record['restored'] = restored; save_record(folder, record)
        target = verify_bundle(plan['target'], release_id=plan['releaseId'], handoff=True, team=team)
        swapped = None
        try:
            if channel == 'download':
                # The downloaded app takes this app's place; this app is kept in this switch's folder to go back to.
                # That moves this helper's own script folder and, since beta.17, its Python too, so everything it
                # imports after the move is loaded first.
                import app_update
                import release_selection  # noqa: F401  publish(), below
                record['state'] = 'replacing'; save_record(folder, record)
                swapped = app_update.swap_in(current['bundle'], plan['target'], folder / 'previous')
                record['previous'] = str(swapped[1]); save_record(folder, record)
                write_launcher(folder / 'Open previous Dervo.command', str(swapped[1] / 'Contents/MacOS/dervo-local'), env, exact=True)
                moved = verify_bundle(swapped[0], handoff=True, team=team)
                if moved['id'] != plan['releaseId'] or moved['executable_sha256'] != target['executable_sha256']:
                    raise RuntimeError('The new app changed while it was moved into place. Your previous app is back.')
                target = moved
            record['state'] = 'opening'; save_record(folder, record)
            pid = open_target(target, env, folder)
        except BaseException:
            if swapped:
                import app_update
                app_update.swap_back(swapped[0], swapped[1], plan['target'])
                record['state'] = 'swapped_back'; save_record(folder, record)
                write_launcher(folder / 'Open previous Dervo.command', current['executable'], env, exact=True)
            raise
        record.update(state='opened', launchedPid=pid); save_record(folder, record)
        # Publish only after the target confirms its host and window started.
        write_launcher(history / 'Open current Dervo.command', target['executable'], env)
        from release_selection import publish
        publish(workspace, target, plan['dataOverride'])
        if channel:
            # Tidying is best effort: the new version is already open and selected.
            import app_update
            try:
                if channel == 'local':
                    record['pruned'] = app_update.prune(Path(target['bundle']).parent.parent, protect=(target['bundle'], current['bundle']))
                else:
                    record['pruned'] = {'removed': app_update.clear_history_apps(history, folder)}
            except Exception as exc:
                record['pruned'] = {'error': str(exc)}
            save_record(folder, record)
        return record
    except BaseException as exc:
        opening = record['state'] in ('opening', 'restoring', 'replacing', 'swapped_back')
        record.update(state='failed', error=str(exc)); save_record(folder, record)
        if opening:
            subprocess.run(platform_os.open_folder_command(folder), check=False, capture_output=True, timeout=10)
        raise


if __name__ == '__main__':
    try:
        run(receive(sys.stdin))
    except Exception as exc:
        report({'error': str(exc)})
        sys.exit(1)
