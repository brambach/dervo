"""Keep the Mac awake while agents work (systems report problem 10 and market report #5, 23 Sep 2026).

Nothing held a power assertion, so three runs left overnight on battery were cut off when the Mac went to sleep, and the
morning said "check your folder, then mark it stopped" for each. While any coding run is open, the host now holds macOS's
idle-sleep assertion through /usr/bin/caffeinate -i, tied to the host's own process with -w so it ends when Dervo does,
even after a crash. It's released as soon as nothing runs.

On Windows (beta.18) the same hold is SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED). That state belongs to
the thread that set it, so a small thread of its own sets it, waits until runs end, then clears it. Windows also drops
it when that thread or Dervo ends, crash included.

The honest limit, which Home says in one line: this stops idle sleep only. Closing the lid still sleeps a laptop.

It's on unless you turn it off in Settings, Preferences (keep_awake_set, 9 Oct). The phone sees whether it's holding, so
it can say the Mac is staying awake for the runs it shows.
"""
import os
from pathlib import Path
import subprocess
import threading

import platform_os

CAFFEINATE = '/usr/bin/caffeinate'
LINE = ('Keeping your PC awake while these run. Closing the lid still puts it to sleep.' if platform_os.WINDOWS else
        'Keeping your Mac awake while these run. Closing the lid still puts it to sleep.')
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def set_thread_execution_state(flags):
    """kernel32's SetThreadExecutionState. Returns the previous state, 0 when it failed."""
    import ctypes
    call = ctypes.windll.kernel32.SetThreadExecutionState
    call.argtypes, call.restype = [ctypes.c_uint], ctypes.c_uint
    return call(flags)


class WindowsHold:
    """Windows' stand-in for the caffeinate process, with the same poll/terminate/wait/kill, so KeepAwake treats both
    alike. Its thread holds the state from start until terminate."""
    def __init__(self, set_state):
        self.set_state = set_state
        self.stopping, self.started = threading.Event(), threading.Event()
        self.ok = False
        self.thread = threading.Thread(target=self._run, name='dervo-keep-awake', daemon=True)

    def start(self, timeout=2):
        self.thread.start()
        self.started.wait(timeout)
        if not self.ok:
            self.stopping.set()
            raise OSError('SetThreadExecutionState refused')
        return self

    def _run(self):
        try:
            self.ok = bool(self.set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
        except (OSError, AttributeError, ValueError):
            self.ok = False
        self.started.set()
        if not self.ok:
            return
        self.stopping.wait()
        try:
            self.set_state(ES_CONTINUOUS)
        except (OSError, AttributeError, ValueError):
            pass  # the thread ends next, and Windows drops its state with it

    def poll(self):
        return None if self.thread.is_alive() else 0

    def terminate(self):
        self.stopping.set()

    kill = terminate

    def wait(self, timeout=None):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise subprocess.TimeoutExpired('SetThreadExecutionState', timeout)
        return 0


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS keep_awake_setting (id INTEGER PRIMARY KEY CHECK (id=1), enabled INTEGER NOT NULL)')


def enabled(db):
    """On unless you turned it off."""
    schema(db)
    row = db.execute('SELECT enabled FROM keep_awake_setting WHERE id=1').fetchone()
    return True if row is None else bool(row[0])


def set_enabled(db, on):
    schema(db)
    with db:
        db.execute('INSERT INTO keep_awake_setting (id, enabled) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled',
                   (int(on),))


def supported():
    return platform_os.WINDOWS or Path(CAFFEINATE).is_file()


def status(host):
    keep = getattr(host, 'keep_awake', None)
    return {'enabled': host.keep_awake_on(), 'holding': bool(keep and keep.holding), 'supported': supported(), 'line': LINE}


def dispatch(host, operation, args):
    """keep_awake_status and keep_awake_set, after the host checked their fields."""
    from dervo_connections import Rejected
    if operation == 'keep_awake_set':
        if type(args['enabled']) is not bool:
            raise Rejected('Choose on or off.')
        set_enabled(host.store.db, args['enabled'])
        host._keep_awake_on = args['enabled']
        host.keep_awake_tick()  # holds or lets go now, not at the next round
    return status(host)


class KeepAwake:
    def __init__(self, spawn=subprocess.Popen, executable=CAFFEINATE, pid=None, set_state=None, windows=None):
        self.spawn, self.executable, self.pid = spawn, executable, pid or os.getpid()
        self.windows = platform_os.WINDOWS if windows is None else windows
        self.set_state = set_state or set_thread_execution_state
        self.process = None
        # caffeinate couldn't start (a Mac without it, or a spawn error), or Windows refused the state. Not retried every
        # tick while runs go on; the next time runs start after a quiet spell it tries again.
        self.failed = False

    @property
    def holding(self):
        return self.process is not None and self.process.poll() is None

    def update(self, active):
        """Hold the assertion while ``active`` (any run open), release it when not. Never raises."""
        if active:
            if not self.holding and not self.failed:
                try:
                    if self.windows:
                        self.process = WindowsHold(self.set_state).start()
                    else:
                        self.process = self.spawn([self.executable, '-i', '-w', str(self.pid)], stdin=subprocess.DEVNULL,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                    self.process, self.failed = None, True
        else:
            self.release()
        return self.holding

    def release(self):
        process, self.process, self.failed = self.process, None, False
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass  # already gone, or it outlived the kill: -w still ends it with the host

    close = release
