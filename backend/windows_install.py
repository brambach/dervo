"""The last part of a Windows update: run the installer, open the new Dervo and wait for it to say it started.

    python windows_install.py SWITCH_FOLDER

windows_update.run starts this and exits. It runs on the Python in the copy of the previous version it kept in
SWITCH_FOLDER/previous, because the installer replaces everything in Dervo's install folder, the Python the update
began on included. For the same reason it uses the standard library only and nothing from Dervo's backend.

SWITCH_FOLDER/install.json says what to do. In order:
1. Wait for the helper that started this to exit.
2. Check the installer's SHA-256 again.
3. Run it silently into Dervo's install folder (NSIS: /S, and /D= last).
4. Check the installed version marker (runtime/dervo-preview.json) names the release and build it was published as.
5. Open the new Dervo with DERVO_HANDOFF_FILE and wait up to a minute for it to write that it started (the app's
   acknowledge_startup in src/version_switch.rs).
If step 3 or anything after it fails, the new Dervo is stopped, the previous copy goes back into the install folder,
and the previous Dervo opens again, but only when the copy went back whole. Every step is written to
SWITCH_FOLDER/switch.json. Windows' Apps list may still name the newer version after a restore, since the installer
recorded it there.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

WINDOWS = os.name == 'nt'
INSTALL_SECONDS = 600
START_SECONDS = 60
# CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW: Dervo opens in its own group, and no console flashes up.
DETACHED = 0x00000200 | 0x08000000


def running(pid):
    """Whether ``pid`` still runs, without signalling it (Windows' os.kill would end it)."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


class Switch:
    """SWITCH_FOLDER/switch.json, rewritten whole at every step."""
    def __init__(self, folder):
        self.folder = Path(folder)
        self.record = json.loads((self.folder / 'switch.json').read_text())

    def save(self, **changes):
        self.record.update(changes)
        temporary = self.folder / 'switch.tmp'
        temporary.write_text(json.dumps(self.record, indent=2))
        temporary.replace(self.folder / 'switch.json')


def open_app(executable, env, log, started=None):
    extra = {'DERVO_HANDOFF_FILE': str(started)} if started else {}
    options = {'creationflags': DETACHED} if WINDOWS else {'start_new_session': True}
    return subprocess.Popen([str(executable)], env={**env, **extra}, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            **options)


def wait_started(process, started, seconds=START_SECONDS):
    """The new Dervo's own word that its host and window started, or RuntimeError."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if started.exists():
            text = started.read_text(errors='replace')
            if text.endswith('\n'):
                result = json.loads(text.splitlines()[0])
                if result.get('ready') is not True:
                    raise RuntimeError('The new Dervo couldn’t open your workspace. ' + str(result.get('detail', '')))
                return
        if process.poll() is not None:
            raise RuntimeError('The new Dervo stopped while it was starting.')
        time.sleep(.1)
    raise RuntimeError('The new Dervo didn’t confirm it started within a minute.')


def installer_command(installer, install_dir):
    """NSIS's silent install into ``install_dir``. /D= must come last and unquoted even with spaces in it, and Python
    would quote it as a list item, so on Windows the command line is written out whole."""
    if WINDOWS:
        return f'"{installer}" /S /D={install_dir}'
    return [str(installer), '/S', '/D=' + str(install_dir)]


def put_back(previous, install_dir, seconds=30):
    """Replace the install folder with the previous copy. Files can stay locked for a moment after a process ends, so
    removing the folder is retried for up to ``seconds``."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            if install_dir.exists():
                shutil.rmtree(install_dir)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.5)
    shutil.copytree(previous, install_dir)


def install(folder, run=subprocess.run):
    folder = Path(folder)
    plan = json.loads((folder / 'install.json').read_text())
    switch = Switch(folder)
    install_dir, previous = Path(plan['installDir']), Path(plan['previous'])
    executable, env = Path(plan['executable']), plan['env']
    deadline = time.monotonic() + 60
    while running(plan['waitPid']):
        if time.monotonic() >= deadline:
            switch.save(state='failed', error='The update helper didn’t exit. Nothing was installed.')
            return switch.record
        time.sleep(.1)
    if digest(plan['installer']) != plan['sha256']:
        switch.save(state='failed', error='The installer changed after it was checked. Nothing was installed.')
        return switch.record
    process = None
    with (folder / 'launch.log').open('a') as log:
        try:
            switch.save(state='installing')
            result = run(installer_command(plan['installer'], install_dir), stdout=log, stderr=log,
                         timeout=INSTALL_SECONDS, check=False)
            if result.returncode != 0:
                raise RuntimeError(f'The installer stopped with code {result.returncode}.')
            marker = json.loads((install_dir / 'runtime' / 'dervo-preview.json').read_text())
            installed = marker.get('nativeVersion') or {}
            if marker.get('releaseId') != plan['releaseId'] or installed.get('buildNumber') != str(plan['build']):
                raise RuntimeError('The installed Dervo isn’t the release it was published as.')
            switch.save(state='opening')
            started = folder / 'started.json'
            process = open_app(executable, env, log, started)
            wait_started(process, started)
        except Exception as exc:  # noqa: BLE001 - every failure after the installer starts puts the previous copy back
            error = str(exc)
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            try:
                put_back(previous, install_dir)
            except Exception as restore:  # noqa: BLE001
                switch.save(state='restore_failed', error=error, restoreError=str(restore))
                return switch.record
            switch.save(state='restored_previous', error=error)
            open_app(executable, env, log)
            return switch.record
    switch.save(state='opened', launchedPid=process.pid)
    # Tidying is best effort: the new version is open. The downloaded installer goes, and so do earlier switches' kept
    # copies of Dervo (not this one's, which the restore needs).
    tidied = []
    for path in [Path(plan['updates']), *(p / 'previous' for p in folder.parent.iterdir() if p != folder)]:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
                tidied.append(str(path))
        except OSError:
            continue
    switch.save(tidied=tidied)
    return switch.record


if __name__ == '__main__':
    install(sys.argv[1])
