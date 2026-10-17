"""The last part of an AppImage update: swap the file, open the new Dervo and wait for it to say it started.

    python linux_install.py SWITCH_FOLDER

linux_update.run starts this and exits. It runs on the copy of Dervo's Python in SWITCH_FOLDER/python, because the
Python the update began on lives in the old AppImage's mount, which goes when the old Dervo closes. For the same reason
it uses the standard library only and nothing from Dervo's backend.

SWITCH_FOLDER/install.json says what to do. In order:
1. Wait for the old Dervo and the helper that started this to exit.
2. Check the download's SHA-256 again.
3. Keep the old AppImage as SWITCH_FOLDER/previous.AppImage, then put the new one in its place: written beside it,
   checked, made executable and renamed over it, so the file is never half there.
4. Open the new Dervo with DERVO_HANDOFF_FILE and wait up to a minute for it to write that it started (the app's
   acknowledge_startup in src/version_switch.rs).
If the swap in step 3 fails before its rename, the old AppImage is untouched and opens again. If anything after the
rename fails, the new Dervo is stopped, the previous AppImage goes back, and it opens again. A download that changed
after it was checked also reopens the old one.
Every step is written to SWITCH_FOLDER/switch.json.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

START_SECONDS = 60
# Dervo opens in its own session, so it outlives this step. platform_os.new_group() says the same, but this file can't
# import Dervo's backend (see above), as windows_install.py can't.
DETACHED = {'start_new_session': True}


def running(pid):
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
    return subprocess.Popen([str(executable)], env={**env, **extra}, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            **DETACHED)


def stop(process, seconds=5):
    """End the new Dervo and everything it started, its Python host too: open_app made it its session's group leader,
    and the host stays in that group. SIGTERM first, then SIGKILL for whatever is left. A negative pid signals the
    group: platform_os.signal_group does the same, but this file can't import Dervo's backend (see above)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(-process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                pass
    process.wait(timeout=seconds)


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


def place(source, target, sha256=None):
    """Copy ``source`` beside ``target``, check it, make it executable and rename it over ``target``.

    Until the rename, ``target`` is untouched, and any failure removes the part file before it's raised."""
    target = Path(target)
    part = target.with_name('.' + target.name + '.dervo-part')
    part.unlink(missing_ok=True)
    try:
        with Path(source).open('rb') as reader, open(part, 'xb') as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if sha256 and digest(part) != sha256:
            raise RuntimeError('The new AppImage didn’t copy whole. Nothing was replaced.')
        part.chmod(0o755)
        os.replace(part, target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def install(folder, popen=open_app, wait=wait_started):
    folder = Path(folder)
    plan = json.loads((folder / 'install.json').read_text())
    switch = Switch(folder)
    target, previous, env = Path(plan['target']), Path(plan['previous']), plan['env']
    deadline = time.monotonic() + 60
    while any(running(pid) for pid in plan['waitPids']):
        if time.monotonic() >= deadline:
            switch.save(state='failed', error='The previous Dervo didn’t close. Nothing was installed.')
            return switch.record
        time.sleep(.1)
    if digest(plan['file']) != plan['sha256']:
        switch.save(state='failed', error='The download changed after it was checked. Nothing was installed.')
        popen(target, env, None)
        return switch.record
    try:
        kept = digest(target)
        shutil.copy2(target, previous)
    except OSError as exc:
        switch.save(state='failed', error=f'Couldn’t keep the previous AppImage ({exc}). Nothing was installed.')
        popen(target, env, None)
        return switch.record
    process, swapped = None, False
    with (folder / 'launch.log').open('a') as log:
        try:
            switch.save(state='installing')
            place(plan['file'], target, plan['sha256'])
            swapped = True
            switch.save(state='opening')
            started = folder / 'started.json'
            process = popen(target, env, log, started)
            wait(process, started)
        except Exception as exc:  # noqa: BLE001 - every failure after the swap starts puts the previous file back
            error = str(exc)
            if process is not None:
                stop(process)
            if not swapped:
                # place() failed before its rename (a full disk, say), so the previous AppImage never left.
                switch.save(state='failed', error=error + ' The previous version is still in place.')
                popen(target, env, log)
                return switch.record
            try:
                place(previous, target, kept)
            except Exception as restore:  # noqa: BLE001
                switch.save(state='restore_failed', error=error, restoreError=str(restore))
                if target.is_file():
                    popen(target, env, log)  # whichever version is there, rather than none
                return switch.record
            switch.save(state='restored_previous', error=error)
            popen(target, env, log)
            return switch.record
    switch.save(state='opened', launchedPid=process.pid)
    # Tidying is best effort: the new version is open. The download goes, and so do earlier switches' kept AppImages
    # and Python copies (this one's previous stays, for going back).
    tidied = []
    for path in [Path(plan['updates']), folder / 'python',
                 *(p / name for p in folder.parent.iterdir() if p != folder for name in ('previous.AppImage', 'python'))]:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            else:
                continue
            tidied.append(str(path))
        except OSError:
            continue
    switch.save(tidied=tidied)
    return switch.record


if __name__ == '__main__':
    install(sys.argv[1])
