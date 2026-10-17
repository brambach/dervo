"""The one place the backend asks the operating system to start and stop process trees, open files and folders, find its
app-data folder, lock a file and run a pseudo-terminal (PLAN-beta16-build-20260925.md, b16/windows milestone 1).

On macOS each function does exactly what its call sites did before this module existed: same syscalls, same argv, same
Popen arguments and same errors. New process, path, lock or terminal code goes through here, not straight to os.killpg,
pty, fcntl or /usr/bin/open.

Windows (b16/windows) has versions for the host to start (milestone 2) and for a Claude Code run: the account home,
the provider environment, git, pipes and walking project folders without following links (milestone 3). They're
checked by the Windows build workflow's runner, not on a Windows PC yet. An agent run (transport.OwnedProcess) is
contained in its own Job Object, so stopping it ends every process it started. Other process trees still end with
taskkill, which misses a child whose parent already exited. A project terminal's shell runs in a ConPTY
pseudo-console in its own Job Object (WindowsConsole, milestone 4).

The Unix-only modules (fcntl, pty, termios) and the Windows-only ones (msvcrt, ctypes.windll) are imported inside the
functions that use them, so this module imports on any platform.
"""
import errno
import os
from pathlib import Path
import sys

WINDOWS = os.name == 'nt'
# Linux gets the Mac's POSIX paths (process groups, pty, fcntl) and its own desktop pieces: xdg-open in place of
# /usr/bin/open, the XDG data folder in place of Application Support, /tmp in place of /private/tmp and the Secret
# Service in place of the Keychain (linux_credentials.py).
LINUX = sys.platform.startswith('linux')
# xdg-open is a shell script that finds the desktop's own opener on PATH. The app gives the host this PATH and nothing
# from the user's shell (src/launch.rs, LINUX_PATH), so it's spelled out wherever an opener starts.
XDG_OPEN = '/usr/bin/xdg-open'

# os.open's flag for "don't follow a symlink here". Windows' Python has none, so there it's 0 and a link is followed.
# Making a symlink on Windows needs Developer Mode or an administrator, which narrows this, but it's still a gap to
# close with the file-access work in milestone 3.
NOFOLLOW = getattr(os, 'O_NOFOLLOW', 0)


# The strongest stop: SIGKILL on a Mac. Windows' Python has no SIGKILL, and signal_group ends a tree there for anything
# but 0 and SIGINT, so SIGTERM stands in.
import signal as _signal
SIGKILL = getattr(_signal, 'SIGKILL', _signal.SIGTERM)


# Process trees. A child started with new_group() leads its own process group, so signal_group reaches it and every
# descendant that stayed in the group.

def new_group(contained=False):
    """Popen keyword arguments that start the child as the leader of its own process group (a new session).

    Windows: a new console process group. With ``contained`` the child also starts suspended, and contain() must follow
    the Popen call: it puts the child in its own Job Object and only then lets it run. A Mac ignores ``contained``."""
    if WINDOWS:
        import subprocess
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | (0x4 if contained else 0)}  # CREATE_SUSPENDED
    return {'start_new_session': True}


# Windows Job Objects, one per contained child and keyed by its pid. Everything the child starts joins the job and
# can't leave it, so ending the job ends the whole tree, including a grandchild whose parent already exited, which
# taskkill /T misses. The job ends with this host (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE): a host that crashes takes its
# runs with it, where a Mac host's next start reaps them (coding_integration.reap_orphan).
_JOBS = {}


def contain(process):
    """Call right after Popen(..., **new_group(contained=True)). Does nothing on a Mac.

    Windows: puts the suspended child in its own Job Object, then resumes it, so nothing it starts can run outside the
    job. If any step fails the child is ended before it ran a single instruction, and the OSError is raised."""
    if not WINDOWS:
        return
    import ctypes
    kernel32 = _job_kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    handle = None
    try:
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _job_limits_type()()
        limits.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):  # extended limits
            raise ctypes.WinError(ctypes.get_last_error())
        handle = kernel32.OpenProcess(0x0100 | 0x0001, False, process.pid)  # PROCESS_SET_QUOTA, PROCESS_TERMINATE
        if not handle or not kernel32.AssignProcessToJobObject(job, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        _windows_resume(process.pid)
    except BaseException:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass
        if job:
            kernel32.CloseHandle(job)
        raise
    finally:
        if handle:
            kernel32.CloseHandle(handle)
    old = _JOBS.pop(process.pid, None)  # a reused pid: its earlier job is empty, or its handle would still hold the pid
    if old:
        kernel32.CloseHandle(old)
    _JOBS[process.pid] = job


def release(pid):
    """Forget a contained child once its owner is done with it. Does nothing on a Mac or for an uncontained ``pid``.

    Windows: closes the Job Object, which ends anything still in it (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)."""
    job = _JOBS.pop(pid, None)
    if job:
        _job_kernel32().CloseHandle(job)


def signal_group(pid, sig):
    """Send ``sig`` to the process group ``pid`` leads. Signal 0 only probes. Raises what os.killpg raises.

    Windows: 0 probes, SIGINT is Ctrl+Break to the group, and anything else ends the tree. A contained child's tree is
    its Job Object, which counts as running while any process in it runs, like a Mac group. Otherwise it's ``pid`` and
    the processes it started (taskkill /T), which misses a child whose parent already exited. Raises
    ProcessLookupError when the job is empty or, uncontained, when ``pid`` is gone."""
    if WINDOWS:
        import signal
        if sig == 0:
            _windows_probe(pid)
        elif sig == signal.SIGINT:
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            except OSError:
                pass  # no shared console to send it through: the caller's harder stop follows
        elif pid in _JOBS:
            _windows_probe(pid)
            _job_kernel32().TerminateJobObject(_JOBS[pid], 1)
        else:
            import subprocess
            _windows_probe(pid)
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=10)
        return
    os.killpg(pid, sig)


def hidden_console():
    """Popen keyword arguments that start a child the way the app starts the host: on Windows with its own hidden console
    (CREATE_NO_WINDOW, src/launch.rs), which is what Stop's Ctrl+Break to a run's group travels through. Nothing on a
    Mac."""
    if WINDOWS:
        import subprocess
        return {'creationflags': subprocess.CREATE_NO_WINDOW}
    return {}


def signal_own_group(sig):
    """Send ``sig`` to this process's own group, this process included."""
    if WINDOWS:
        signal_group(os.getpid(), sig)
        return
    os.killpg(os.getpgrp(), sig)


def is_group_leader(pid):
    """True when ``pid`` still leads its own process group. Raises what os.getpgid raises for a missing process.

    Windows has no process groups: a running ``pid``, or a contained one whose job still runs, counts."""
    if WINDOWS:
        _windows_probe(pid)
        return True
    return os.getpgid(pid) == pid


def _windows_probe(pid):
    """Raise ProcessLookupError unless ``pid`` is running (or, contained, anything in its job is), PermissionError when
    Windows won't say. Never ends it: os.kill with signal 0 would, because Windows' os.kill terminates for anything but
    the two console events. An empty job is closed and forgotten here."""
    import ctypes
    from ctypes import wintypes
    job = _JOBS.get(pid)
    if job is not None:
        accounting = _job_accounting_type()()
        if not _job_kernel32().QueryInformationJobObject(job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
            raise PermissionError(errno.EPERM, 'Windows refused to say whether this job runs', str(pid))
        if accounting.ActiveProcesses:
            return
        if _JOBS.pop(pid, None) is not None:
            _job_kernel32().CloseHandle(job)
        raise ProcessLookupError(errno.ESRCH, 'No such process', str(pid))
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        if ctypes.get_last_error() == 5:  # ERROR_ACCESS_DENIED
            raise PermissionError(errno.EPERM, 'Windows refused to say whether this process runs', str(pid))
        raise ProcessLookupError(errno.ESRCH, 'No such process', str(pid))
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise PermissionError(errno.EPERM, 'Windows refused to say whether this process runs', str(pid))
        if code.value != 259:  # STILL_ACTIVE
            raise ProcessLookupError(errno.ESRCH, 'No such process', str(pid))
    finally:
        kernel32.CloseHandle(handle)


def _job_kernel32():
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                   ctypes.c_void_p)
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Thread32First.argtypes = kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _job_limits_type():
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION, its basic limits and IO_COUNTERS laid out flat (144 bytes on x64)."""
    import ctypes
    u64, size, dword = ctypes.c_int64, ctypes.c_size_t, ctypes.c_uint32

    class Limits(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', u64), ('PerJobUserTimeLimit', u64), ('LimitFlags', dword),
                    ('MinimumWorkingSetSize', size), ('MaximumWorkingSetSize', size), ('ActiveProcessLimit', dword),
                    ('Affinity', size), ('PriorityClass', dword), ('SchedulingClass', dword)] + [
                    (name, ctypes.c_uint64) for name in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')] + [
                    (name, size) for name in ('ProcessMemoryLimit', 'JobMemoryLimit', 'PeakProcessMemoryUsed',
                                              'PeakJobMemoryUsed')]
    return Limits


def _job_accounting_type():
    """JOBOBJECT_BASIC_ACCOUNTING_INFORMATION."""
    import ctypes

    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime',
                                                        'ThisPeriodTotalKernelTime')] + [
                    (name, ctypes.c_uint32) for name in ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses',
                                                         'TotalTerminatedProcesses')]
    return Accounting


def _windows_resume(pid):
    """Resume the threads of a process started with CREATE_SUSPENDED (it has one). Raises OSError if none resumed."""
    import ctypes

    class ThreadEntry(ctypes.Structure):  # THREADENTRY32
        _fields_ = [('dwSize', ctypes.c_uint32), ('cntUsage', ctypes.c_uint32), ('th32ThreadID', ctypes.c_uint32),
                    ('th32OwnerProcessID', ctypes.c_uint32), ('tpBasePri', ctypes.c_int32),
                    ('tpDeltaPri', ctypes.c_int32), ('dwFlags', ctypes.c_uint32)]
    kernel32 = _job_kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    resumed = 0
    try:
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = kernel32.OpenThread(0x0002, False, entry.th32ThreadID)  # THREAD_SUSPEND_RESUME
                if thread:
                    try:
                        if kernel32.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed += 1
                    finally:
                        kernel32.CloseHandle(thread)
            more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    if not resumed:
        raise OSError(errno.ESRCH, 'The new process had no thread to resume', str(pid))


# Opening files and folders. Each returns the argv to run, so callers keep their own subprocess call and its timeout.
# On Windows the argv is this Python running one line, so no path ever passes through cmd.exe's parsing, and the exit
# code means something (explorer.exe's doesn't).

def _windows_python(code, path):
    return [sys.executable, '-E', '-s', '-B', '-c', code, str(path)]


def reveal_command(path):
    """Show ``path`` selected in the file manager. Linux has no portable "select this file", so its folder opens."""
    if WINDOWS:
        return _windows_python('import subprocess,sys; subprocess.Popen(["explorer.exe", "/select,", sys.argv[1]])', path)
    if LINUX:
        path = Path(path)
        return [XDG_OPEN, str(path if path.is_dir() else path.parent)]
    return ['/usr/bin/open', '-R', str(path)]


def open_command(path):
    """Open ``path`` in its default app, or a folder in the file manager."""
    if WINDOWS:
        return _windows_python('import os,sys; os.startfile(sys.argv[1])', path)
    if LINUX:
        return [XDG_OPEN, str(path)]
    return ['/usr/bin/open', '--', str(path)]


def open_as_text_command(path):
    """Open ``path`` in the default text editor."""
    if WINDOWS:
        return _windows_python('import subprocess,sys; subprocess.Popen(["notepad.exe", sys.argv[1]])', path)
    if LINUX:
        return [XDG_OPEN, str(path)]  # the desktop's handler for its type, a text editor for Markdown and plain text
    return ['/usr/bin/open', '-t', str(path)]


def open_folder_command(path):
    """Open the folder ``path`` in the file manager."""
    if WINDOWS or LINUX:
        return open_command(path)
    return ['/usr/bin/open', str(path)]


# The app itself.

# Set by the Windows app for its host and update helper (src/launch.rs, host_environment): the path of its own exe.
APP_EXECUTABLE = 'DERVO_APP_EXECUTABLE'


def app_executable(resources):
    """The running Dervo app's own executable, for ``resources`` (the folder that holds backend/ and runtime/).

    A Mac finds it in the bundle: Contents/Resources/../MacOS/dervo-local. On Windows the resources sit beside the exe,
    and the installed exe's name comes from the installer, so the app says where it is in DERVO_APP_EXECUTABLE. That
    path is used only when it's a file in ``resources`` itself; anything else is refused."""
    resources = Path(resources)
    if LINUX and not WINDOWS:
        # A Linux package keeps resources in /usr/lib/<product> and the exe in /usr/bin (an AppImage under its own
        # mount), so the app names its exe in DERVO_APP_EXECUTABLE as on Windows. It has to be a real file.
        named = os.environ.get(APP_EXECUTABLE, '')
        if not named or not Path(named).is_absolute() or not Path(named).is_file():
            raise RuntimeError('Dervo didn’t say where its own app is. Reopen Dervo and try again.')
        return Path(named)
    if not WINDOWS:
        return resources.parent / 'MacOS' / 'dervo-local'
    named = os.environ.get(APP_EXECUTABLE, '')
    path = Path(named)
    if not named or not path.is_absolute() or path.suffix.lower() != '.exe' or not path.is_file():
        raise RuntimeError('Dervo didn’t say where its own app is. Reopen Dervo and try again.')
    if os.path.normcase(str(path.parent.resolve())) != os.path.normcase(str(resources.resolve())):
        raise RuntimeError('Dervo’s app isn’t in the folder it runs from. Reinstall Dervo from the beta page.')
    return path


def process_running(pid):
    """Whether ``pid`` still runs. Never signals it (Windows' os.kill would end it)."""
    try:
        if WINDOWS:
            _windows_probe(pid)
        else:
            os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# App data.

def app_support_dir():
    """The per-user folder apps keep their data in: ~/Library/Application Support, or %APPDATA% on Windows."""
    if WINDOWS:
        appdata = os.environ.get('APPDATA')
        if not appdata or not Path(appdata).is_absolute():
            raise RuntimeError('Windows did not say where app data goes (APPDATA).')
        return Path(appdata)
    if LINUX:
        data = os.environ.get('XDG_DATA_HOME')
        return Path(data) if data and Path(data).is_absolute() else Path(account_home()) / '.local/share'
    return Path.home() / 'Library/Application Support'


def worktrees_dir():
    """Where Dervo keeps thread folders (beta.18): Application Support/Dervo/Worktrees on a Mac, %LOCALAPPDATA%\\Dervo\\
    Worktrees on Windows (not app_support_dir's %APPDATA%, which roams). Never inside a project folder."""
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Dervo' / 'Worktrees'
    return app_support_dir() / 'Dervo' / 'Worktrees'


# Thread folders (beta.18). A Mac clones files on APFS; elsewhere there are no clones and a thread folder is a plain
# checkout (on Windows git also needs -c core.longpaths=true there, still to come with b16/windows).

CANT_CLONE = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}
_libc = None


def _mac_libc():
    global _libc
    if _libc is None:
        import ctypes
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
        _libc.clonefile.restype = ctypes.c_int
        _libc.setxattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
                                   ctypes.c_int]
    return _libc


def can_clone():
    """True where clone_path can work at all. Whether a pair of folders can is only known by trying."""
    import sys
    return sys.platform == 'darwin'


def clone_path(source, destination):
    """Clone a file or a whole folder (APFS clonefile(2), no link followed). Raises OSError; an errno in CANT_CLONE
    means this disk, pair of volumes or system can't clone, so copy instead."""
    if not can_clone():
        raise OSError(errno.ENOTSUP, 'File cloning is unavailable')
    import ctypes
    if _mac_libc().clonefile(os.fsencode(source), os.fsencode(destination), 0x0001 | 0x0004) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


BACKUP_EXCLUDE = 'com.apple.metadata:com_apple_backup_excludeItem'


def exclude_from_backup(folder):
    """Keep the system backup (Time Machine) out of ``folder``. The attribute ``tmutil addexclusion`` writes, set
    directly: tmutil took 11 seconds here (5 Oct 2026). False when it couldn't, or there's nothing to do here."""
    if not can_clone():
        return False
    import plistlib
    try:
        value = plistlib.dumps('com.apple.backupd', fmt=plistlib.FMT_BINARY)
        return _mac_libc().setxattr(os.fsencode(folder), BACKUP_EXCLUDE.encode(), value, len(value), 0, 0) == 0
    except (OSError, AttributeError):
        return False


def tool_dirs():
    """Where Dervo looks for a package manager it was asked to run (npm, pnpm, uv and so on), never the shell's PATH."""
    home = Path.home()
    return ['/opt/homebrew/bin', '/usr/local/bin', str(home / '.local/bin'), str(home / '.bun/bin'),
            str(home / 'Library/pnpm'), str(home / '.local/share/pnpm'), '/snap/bin', '/usr/bin', '/bin']


def system_path():
    """The system folders added after a tool's own folder in a run's PATH."""
    return ['/usr/bin', '/bin']


# File locks.

def lock_nowait(fd):
    """Take an exclusive lock on the open file ``fd`` without waiting. Raises BlockingIOError when someone else holds it.
    The lock goes when the file is closed."""
    if WINDOWS:
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(errno.EWOULDBLOCK, 'The file is locked by another process') from exc
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


# Waiting for input.

def wait_readable(fd, timeout):
    """True once the pipe or file ``fd`` has something to read (or has closed), False after ``timeout`` seconds.

    Windows' select() takes sockets only, so there the pipe is polled every 10 ms."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        import msvcrt
        import time
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        handle = msvcrt.get_osfhandle(fd)
        deadline = time.monotonic() + timeout
        available = wintypes.DWORD()
        while True:
            if not kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                return True  # closed or not a pipe: the read that follows says which
            if available.value:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.01, remaining))
    import select
    return bool(select.select([fd], [], [], timeout)[0])


# Pseudo-terminals. A Mac has a pty pair and file descriptors. Windows has no pty: WindowsConsole (below) runs the
# shell in a ConPTY pseudo-console instead (milestone 4), and these two refuse there.

def open_pty():
    """A new pseudo-terminal pair: (controller fd, terminal fd)."""
    if WINDOWS:
        raise OSError(errno.ENOSYS, 'Windows has no pty pair. Use WindowsConsole')
    import pty
    return pty.openpty()


def set_pty_size(fd, rows, cols):
    """Tell the terminal on ``fd`` its size in character cells."""
    if WINDOWS:
        raise OSError(errno.ENOSYS, 'Windows has no pty pair. Use WindowsConsole')
    import fcntl
    import struct
    import termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))


def account_environment():
    """Windows only: the environment a new program of this account starts with, the account's and the machine's own
    variables with PATH, from Windows (CreateEnvironmentBlock) and never from the host's own, which the app keeps to six
    variables (src/launch.rs). A terminal's shell needs the PATH you'd get in a new PowerShell window."""
    import ctypes
    from ctypes import wintypes
    advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
    userenv = ctypes.WinDLL('userenv', use_last_error=True)
    kernel32 = _job_kernel32()
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    userenv.CreateEnvironmentBlock.argtypes = (ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL)
    userenv.DestroyEnvironmentBlock.argtypes = (ctypes.c_void_p,)
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008 | 0x0002, ctypes.byref(token)):  # QUERY, DUPLICATE
        raise ctypes.WinError(ctypes.get_last_error())
    block = ctypes.c_void_p()
    try:
        if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):  # not inherited from the host
            raise ctypes.WinError(ctypes.get_last_error())
        env, offset = {}, 0
        while True:
            entry = ctypes.wstring_at(block.value + offset)
            if not entry:
                return env
            offset += len(entry.encode('utf-16-le')) + 2
            name, equals, value = entry.partition('=')
            if name and equals:  # the hidden per-drive "=C:=C:\..." entries have no name
                env[name] = value
    finally:
        if block:
            userenv.DestroyEnvironmentBlock(block)
        kernel32.CloseHandle(token)


class WindowsConsole:
    """Windows only: a shell running in a ConPTY pseudo-console, inside its own Job Object, for a project terminal.

    Behaves like the Mac's pty controller as the terminal code uses it: read() returns what the shell printed since the
    last read, write() queues keystrokes, resize() and close(). It also stands in for the shell's Popen (pid, poll(),
    returncode). The host never waits on the console: a thread reads its output into a buffer and another writes queued
    input. close() ends the job, so every process the shell started goes with it, then closes the console."""
    OUTPUT_LIMIT = 1024 * 1024  # the terminal keeps the last 1 MiB anyway (project_terminal.OUTPUT_LIMIT)
    INPUT_CHUNKS = 8

    def __init__(self, argv, cwd, env, cols, rows):
        import ctypes
        import queue
        import subprocess
        import threading
        from ctypes import wintypes
        kernel32 = self._kernel32 = _console_kernel32()
        self._lock = threading.Lock()
        self._output = bytearray()
        self._input = queue.Queue(self.INPUT_CHUNKS)
        self._console = self._process = self._job = None
        self.pid = self.returncode = None
        in_read, in_write, out_read, out_write = (wintypes.HANDLE() for _ in range(4))
        try:
            if not kernel32.CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0) or \
                    not kernel32.CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            console = ctypes.c_void_p()
            result = kernel32.CreatePseudoConsole(_coord(cols, rows), in_read, out_write, 0, ctypes.byref(console))
            if result != 0:
                raise OSError(errno.EIO, f'Windows could not create a pseudo-console ({result & 0xFFFFFFFF:#x})')
            self._console = console
            # The console holds its own ends now. Ours would keep the pipes open after it closes.
            for handle in (in_read, out_write):
                kernel32.CloseHandle(handle)
            in_read = out_write = None
            self._start(argv, cwd, env, subprocess.list2cmdline(argv))
        except BaseException:
            for handle in (in_read, out_write, in_write, out_read):
                if handle:
                    kernel32.CloseHandle(handle)
            self.close()
            raise
        self._in, self._out = in_write, out_read
        threading.Thread(target=self._read_loop, name='dervo-terminal-output', daemon=True).start()
        threading.Thread(target=self._write_loop, name='dervo-terminal-input', daemon=True).start()

    def _start(self, argv, cwd, env, command_line):
        """Start the shell suspended with the console as its terminal, put it in a new job, then let it run."""
        import ctypes
        kernel32 = self._kernel32
        size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))  # asks for the size, so it "fails"
        attributes = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # The console's handle itself is the value (PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE), not a pointer to it.
            if not kernel32.UpdateProcThreadAttribute(attributes, 0, 0x00020016, self._console,
                                                      ctypes.sizeof(ctypes.c_void_p), None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            info = _console_types()['StartupInfoEx']()
            info.StartupInfo.cb = ctypes.sizeof(info)
            # No standard handles of its own, so the shell can't pick up the host's pipes instead of the console.
            info.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
            info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            block = ''.join(f'{name}={env[name]}\0' for name in sorted(env, key=str.upper)) + '\0'
            environment = (ctypes.c_wchar * len(block))(*block)
            process = _console_types()['ProcessInformation']()
            flags = 0x00080000 | 0x00000400 | 0x00000004  # EXTENDED_STARTUPINFO_PRESENT, UNICODE_ENVIRONMENT, SUSPENDED
            if not kernel32.CreateProcessW(None, ctypes.create_unicode_buffer(command_line), None, None, False, flags,
                                           environment, str(cwd), ctypes.byref(info), ctypes.byref(process)):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.DeleteProcThreadAttributeList(attributes)
        self._process, self.pid = process.hProcess, process.dwProcessId
        try:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            self._job = job
            limits = _job_limits_type()()
            limits.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: it ends with this host too
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or \
                    not kernel32.AssignProcessToJobObject(job, process.hProcess):
                raise ctypes.WinError(ctypes.get_last_error())
            if kernel32.ResumeThread(process.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            kernel32.TerminateProcess(process.hProcess, 1)
            raise
        finally:
            kernel32.CloseHandle(process.hThread)

    def _read_loop(self):
        import ctypes
        from ctypes import wintypes
        buffer, count = ctypes.create_string_buffer(16384), wintypes.DWORD()
        try:
            while self._kernel32.ReadFile(self._out, ctypes.cast(buffer, ctypes.c_void_p), len(buffer),
                                          ctypes.byref(count), None) and count.value:
                with self._lock:
                    self._output.extend(buffer.raw[:count.value])
                    if len(self._output) > self.OUTPUT_LIMIT:
                        del self._output[:-self.OUTPUT_LIMIT]
        finally:
            self._kernel32.CloseHandle(self._out)  # the console closed its end, or close() ended it

    def _write_loop(self):
        import ctypes
        from ctypes import wintypes
        written = wintypes.DWORD()
        try:
            while True:
                data = self._input.get()
                if data is None:
                    return
                while data:
                    if not self._kernel32.WriteFile(self._in, data, len(data), ctypes.byref(written), None):
                        return
                    data = data[written.value:]
        finally:
            self._kernel32.CloseHandle(self._in)

    def read(self):
        """Everything the shell printed since the last read, at most the last OUTPUT_LIMIT bytes."""
        with self._lock:
            data = bytes(self._output)
            self._output.clear()
        return data

    def write(self, data):
        """Queue ``data`` for the shell. Returns how many bytes were taken: all of them, or none while the queue is full."""
        import queue
        if self._console is None:
            raise OSError(errno.EBADF, 'This terminal has closed')
        try:
            self._input.put_nowait(bytes(data))
        except queue.Full:
            return 0
        return len(data)

    def resize(self, rows, cols):
        if self._console is None:
            raise OSError(errno.EBADF, 'This terminal has closed')
        result = self._kernel32.ResizePseudoConsole(self._console, _coord(cols, rows))
        if result != 0:
            raise OSError(errno.EIO, f'Windows could not resize the pseudo-console ({result & 0xFFFFFFFF:#x})')

    def poll(self):
        """The shell's exit code once it has ended, like Popen.poll()."""
        import ctypes
        from ctypes import wintypes
        if self.returncode is None and self._process and self._kernel32.WaitForSingleObject(self._process, 0) == 0:
            code = wintypes.DWORD()
            if self._kernel32.GetExitCodeProcess(self._process, ctypes.byref(code)):
                self.returncode = code.value
                self._kernel32.CloseHandle(self._process)
                self._process = None
        return self.returncode

    def processes(self):
        """The ids of every process still in the terminal's job, the shell included."""
        import ctypes
        if not self._job:
            return []

        class ProcessList(ctypes.Structure):  # JOBOBJECT_BASIC_PROCESS_ID_LIST with room for 64
            _fields_ = [('Assigned', ctypes.c_uint32), ('Listed', ctypes.c_uint32), ('Ids', ctypes.c_size_t * 64)]
        listing = ProcessList()
        if not self._kernel32.QueryInformationJobObject(self._job, 3, ctypes.byref(listing), ctypes.sizeof(listing), None):
            return []
        return [listing.Ids[i] for i in range(min(listing.Listed, 64))]

    def foreground(self):
        """The file name of the newest process the shell started that's still running, or None at the prompt. A
        command's own helpers count too, since Windows has no foreground group to ask."""
        import ctypes
        from ctypes import wintypes
        for pid in reversed([p for p in self.processes() if p != self.pid]):
            handle = self._kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                continue
            try:
                size = wintypes.DWORD(1024)
                name = ctypes.create_unicode_buffer(size.value)
                if self._kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                    return Path(name.value).name
            finally:
                self._kernel32.CloseHandle(handle)
        return None

    def close(self):
        """End the shell and everything it started, then close the console. The output thread gets what's left."""
        import threading
        if self._job:
            self._kernel32.TerminateJobObject(self._job, 1)
            self._kernel32.CloseHandle(self._job)
            self._job = None
        if self._console is not None:
            console, self._console = self._console, None
            # Older Windows waits in ClosePseudoConsole until the output pipe is read. The output thread keeps reading,
            # and the host never waits more than 3 s for it.
            closing = threading.Thread(target=self._kernel32.ClosePseudoConsole, args=(console,), daemon=True)
            closing.start()
            closing.join(3)
            try:
                self._input.put_nowait(None)
            except Exception:
                pass
        self.poll()


_CONSOLE_TYPES = {}


def _console_types():
    """COORD, STARTUPINFOEXW and PROCESS_INFORMATION, made on first use so this module imports on any platform."""
    if _CONSOLE_TYPES:
        return _CONSOLE_TYPES
    import ctypes
    from ctypes import wintypes

    class Coord(ctypes.Structure):
        _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

    class StartupInfo(ctypes.Structure):  # STARTUPINFOW
        _fields_ = [('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR), ('lpDesktop', wintypes.LPWSTR),
                    ('lpTitle', wintypes.LPWSTR)] + [(name, wintypes.DWORD) for name in (
                    'dwX', 'dwY', 'dwXSize', 'dwYSize', 'dwXCountChars', 'dwYCountChars', 'dwFillAttribute', 'dwFlags')] + [
                    ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD), ('lpReserved2', ctypes.c_void_p),
                    ('hStdInput', wintypes.HANDLE), ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE)]

    class StartupInfoEx(ctypes.Structure):  # STARTUPINFOEXW
        _fields_ = [('StartupInfo', StartupInfo), ('lpAttributeList', ctypes.c_void_p)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE), ('dwProcessId', wintypes.DWORD),
                    ('dwThreadId', wintypes.DWORD)]
    _CONSOLE_TYPES.update(Coord=Coord, StartupInfoEx=StartupInfoEx, ProcessInformation=ProcessInformation)
    return _CONSOLE_TYPES


def _coord(cols, rows):
    return _console_types()['Coord'](cols, rows)


def _console_kernel32():
    """kernel32 with the signatures WindowsConsole calls, on top of the job ones."""
    import ctypes
    from ctypes import wintypes
    kernel32 = _job_kernel32()
    kernel32.CreatePipe.argtypes = (ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p,
                                    wintypes.DWORD)
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.CreatePseudoConsole.argtypes = (_console_types()['Coord'], wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD,
                                             ctypes.POINTER(ctypes.c_void_p))
    kernel32.ResizePseudoConsole.restype = ctypes.c_long
    kernel32.ResizePseudoConsole.argtypes = (ctypes.c_void_p, _console_types()['Coord'])
    kernel32.ClosePseudoConsole.restype = None
    kernel32.ClosePseudoConsole.argtypes = (ctypes.c_void_p,)
    kernel32.InitializeProcThreadAttributeList.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                                           ctypes.POINTER(ctypes.c_size_t))
    kernel32.UpdateProcThreadAttribute.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                                   ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p)
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    kernel32.CreateProcessW.argtypes = (wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p)
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.ReadFile.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                  ctypes.c_void_p)
    kernel32.WriteFile.argtypes = (wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                   ctypes.c_void_p)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    return kernel32


# The OS account (b16/windows milestone 3).

def account_home():
    """The account's home folder from the account database, never from an inherited HOME. Raises KeyError when the
    account has none. Windows keeps no such database for Python, so there it's USERPROFILE, which the app passes to the
    host (WINDOWS_ENVIRONMENT in src/launch.rs)."""
    if WINDOWS:
        home = os.environ.get('USERPROFILE')
        if not home:
            raise KeyError('USERPROFILE')
        return home
    import pwd
    return pwd.getpwuid(os.getuid()).pw_dir


def account_name():
    """The account's login name from the same source as account_home. Raises KeyError when there's none."""
    if WINDOWS:
        import ctypes
        from ctypes import wintypes
        size = wintypes.DWORD(257)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.WinDLL('advapi32', use_last_error=True).GetUserNameW(buffer, ctypes.byref(size)):
            raise KeyError('GetUserNameW')
        return buffer.value
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


# What a provider CLI (Claude Code, Codex) starts with before Dervo adds its own variables. Allowlisted, never the
# host's own environment.

# The folders Windows programs need to find themselves, their home and temp. None of them is a secret.
WINDOWS_FOLDERS = ('SYSTEMROOT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP')


def provider_environment():
    """A Mac gets the fixed system PATH and UTF-8 locale. Windows gets System32, PowerShell, Git for Windows when it's
    installed (Claude Code's Bash tool runs in Git Bash) and the account's own .local\\bin, plus WINDOWS_FOLDERS."""
    if WINDOWS:
        root = os.environ.get('SYSTEMROOT', r'C:\Windows')
        folders = [os.path.join(root, 'System32'), root, os.path.join(root, 'System32', 'Wbem'),
                   os.path.join(root, 'System32', 'WindowsPowerShell', 'v1.0')]
        git = git_executable()
        if os.path.isfile(git):
            folders.append(os.path.dirname(git))
        home = os.environ.get('USERPROFILE')
        if home:
            folders.append(os.path.join(home, '.local', 'bin'))
        env = {name: os.environ[name] for name in WINDOWS_FOLDERS if os.environ.get(name)}
        env.update(PATH=os.pathsep.join(dict.fromkeys(folders)), LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
        return env
    if LINUX:
        # Claude Code's own installer puts it in ~/.local/bin, and its Bash tool needs the same folder to find itself.
        # C.UTF-8 because en_US.UTF-8 isn't generated on every distribution.
        try:
            local = os.path.join(account_home(), '.local', 'bin')
        except KeyError:
            local = None
        folders = [local, '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']
        return {'PATH': ':'.join(f for f in folders if f), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    return {'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'}


def git_executable():
    """The git Dervo runs itself: Apple's /usr/bin/git on a Mac, Git for Windows' cmd\\git.exe on Windows (the
    machine-wide install first, then the per-user one). On Windows it's the expected path even when Git isn't there, so
    a call fails as a missing file."""
    if WINDOWS:
        bases = [os.environ.get('ProgramW6432'), os.environ.get('ProgramFiles'), r'C:\Program Files']
        if os.environ.get('LOCALAPPDATA'):
            bases.append(os.path.join(os.environ['LOCALAPPDATA'], 'Programs'))
        candidates = [os.path.join(base, 'Git', 'cmd', 'git.exe') for base in bases if base]
        return next((path for path in candidates if os.path.isfile(path)), candidates[0])
    return '/usr/bin/git'


def scratch_root():
    """Where a run's own scratch folder goes: a folder only this account can write, never one other users share.

    Not /private/tmp (MCP parity review, 9 Oct): any local user can write there, and npx and uv walk up from a run's
    folder looking for node_modules, .npmrc and uv.toml, so a file another account left in /private/tmp could steer
    what a Home or connectors-only run starts.

    Mac: the account's own temp folder under /private/var/folders, from the system (confstr), since the app starts the
    host with a cleared environment and no TMPDIR, or from TMPDIR; else ~/Library/Caches/Dervo/scratch, made private.
    Linux: $XDG_CACHE_HOME/dervo/scratch (~/.cache/dervo/scratch), made private.

    Windows: %LOCALAPPDATA%\\Dervo\\scratch, made for this account, as its real path because TEMP and LOCALAPPDATA
    can hold a short 8.3 name (C:\\Users\\RUNNER~1\\... on the build runner). A run's folder is Claude Code's working
    folder, and its saved session lives under a folder named after that path (session_continuity.place_session). Dervo
    names it from the real path, so Claude Code has to start there too. Without LOCALAPPDATA, TEMP's real path, which
    Windows already keeps per account."""
    import tempfile
    if WINDOWS:
        local = os.environ.get('LOCALAPPDATA')
        if not (local and os.path.isabs(local)):
            return os.path.realpath(tempfile.gettempdir())
        return _private_folder(os.path.join(local, 'Dervo', 'scratch'))
    if LINUX:
        cache = os.environ.get('XDG_CACHE_HOME')
        base = cache if cache and os.path.isabs(cache) else os.path.join(account_home(), '.cache')
        return _private_folder(os.path.join(base, 'dervo', 'scratch'))
    found = []
    if 'CS_DARWIN_USER_TEMP_DIR' in getattr(os, 'confstr_names', {}):
        try:
            found.append(os.confstr('CS_DARWIN_USER_TEMP_DIR'))
        except (OSError, ValueError):
            pass
    found.append(tempfile.gettempdir())
    for temp in found:
        temp = os.path.realpath(temp) if temp else ''
        try:
            if temp.startswith('/private/var/folders/') and os.stat(temp).st_uid == os.getuid():
                return temp
        except OSError:
            continue
    return _private_folder(os.path.join(account_home(), 'Library', 'Caches', 'Dervo', 'scratch'))


def _private_folder(folder):
    """``folder``, made if it's missing and kept to this account (0700; on Windows the profile's own ACL does that)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    if not WINDOWS:
        os.chmod(folder, 0o700)
    return os.path.realpath(folder)


def executable_name(name):
    """``name`` as an executable file is called here: claude on a Mac, claude.exe on Windows."""
    return name + '.exe' if WINDOWS else name


# Waiting on pipes. selectors only takes sockets on Windows, so there a selector that polls pipes stands in.

def pipe_selector():
    """A selector for a child process's pipes: selectors.DefaultSelector on a Mac."""
    if WINDOWS:
        return _WindowsPipeSelector()
    import selectors
    return selectors.DefaultSelector()


class _WindowsPipeSelector:
    """The part of the selectors API transport.py uses, for anonymous pipes. A pipe is ready to read once PeekNamedPipe
    sees bytes or the pipe has closed, so the os.read that follows either gets data or b''. Writing is reported ready at
    once: the writer's own non-blocking os.write and deadline do the waiting. Polls every 10 ms."""

    def __init__(self):
        self._keys = {}

    def register(self, fileobj, events, data=None):
        import selectors
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        key = selectors.SelectorKey(fileobj, fd, events, data)
        self._keys[fd] = key
        return key

    def unregister(self, fileobj):
        fd = fileobj if isinstance(fileobj, int) else fileobj.fileno()
        return self._keys.pop(fd)

    def get_map(self):
        return dict(self._keys)

    def select(self, timeout=None):
        import selectors
        import time
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        while True:
            ready = []
            for key in list(self._keys.values()):
                events = 0
                if key.events & selectors.EVENT_READ and _pipe_readable(key.fd):
                    events |= selectors.EVENT_READ
                if key.events & selectors.EVENT_WRITE:
                    events |= selectors.EVENT_WRITE
                if events:
                    ready.append((key, events))
            if ready:
                return ready
            if deadline is not None and time.monotonic() >= deadline:
                return []
            time.sleep(0.01 if deadline is None else min(0.01, max(0, deadline - time.monotonic())))

    def close(self):
        self._keys.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def readable(fd):
    """True when a read from ``fd`` won't wait: bytes are waiting or the other end has closed. A Mac asks select with a
    zero timeout. Windows: ``fd`` is an anonymous pipe, and PeekNamedPipe says (select only takes sockets there)."""
    if WINDOWS:
        return _pipe_readable(fd)
    import select
    return bool(select.select([fd], [], [], 0)[0])


def _pipe_readable(fd):
    """True when the pipe ``fd`` has bytes waiting or has closed (or isn't a pipe), so a read won't wait."""
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.PeekNamedPipe.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                       ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    available = wintypes.DWORD()
    if not kernel32.PeekNamedPipe(msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(available), None):
        return True
    return available.value > 0


# Walking into a folder without following links. A Mac opens each part relative to the one before with O_NOFOLLOW, so
# a symlink anywhere on the way is refused. Windows has no dir_fd in Python, so there each part is a real directory
# handle, held open without delete sharing (nothing can rename or remove it while Dervo holds it), and every open is
# checked afterwards: the handle's real path must be the parent's real path plus the name asked for. A symlink or
# junction lands somewhere else and is refused like O_NOFOLLOW refuses it. Descriptors are ordinary fds either way, so
# os.fstat, os.read, os.write, os.fsync and os.close work on them unchanged.

def open_root(path):
    """An open directory descriptor for the filesystem root ``path`` is under: '/' on a Mac, its drive on Windows."""
    if WINDOWS:
        return _windows_open(Path(path).anchor, directory=True, mode='r', check=None)
    return os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def below_root(path):
    """``path``'s parts after its root, to open one by one with open_dir_at."""
    return Path(path).parts[1:]


def open_dir_at(name, dir_fd):
    """Open the directory ``name`` inside the open directory ``dir_fd``, refusing a link."""
    if WINDOWS:
        return _windows_open_at(name, dir_fd, directory=True, mode='r')
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def open_file_at(name, dir_fd, mode='r', perms=0o644):
    """Open the file ``name`` inside the open directory ``dir_fd``, refusing a link. ``mode`` is 'r' (read), 'rw' (read
    and write) or 'new' (create for writing, failing with FileExistsError when anything is already there)."""
    if WINDOWS:
        return _windows_open_at(name, dir_fd, directory=False, mode=mode)
    if mode == 'new':
        return os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, perms, dir_fd=dir_fd)
    flags = (os.O_RDWR if mode == 'rw' else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    return os.open(name, flags, dir_fd=dir_fd)


def stat_at(name, dir_fd):
    """os.stat of ``name`` inside the open directory ``dir_fd``, not following a link."""
    if WINDOWS:
        return os.lstat(_windows_child(dir_fd, name))
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def open_dir(path, follow=False):
    """Open the existing folder at the absolute real path ``path``, refusing a link. A Mac opens it with O_NOFOLLOW;
    Windows walks each part from its drive with open_dir_at, so a junction on the way is refused too. ``follow`` opens
    whatever ``path`` leads to: on a Mac without O_NOFOLLOW, on Windows by walking its resolved path."""
    if WINDOWS:
        if follow:
            path = Path(path).resolve(strict=True)
        fd = open_root(path)
        try:
            for part in below_root(path):
                child = open_dir_at(part, fd)
                os.close(fd)
                fd = child
        except BaseException:
            os.close(fd)
            raise
        return fd
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | (0 if follow else os.O_NOFOLLOW))


def listdir_fd(dir_fd):
    """The names in the open directory ``dir_fd``. Windows lists the handle's real path (no os.listdir(fd) there)."""
    if WINDOWS:
        return os.listdir(_windows_final_path(dir_fd))
    return os.listdir(dir_fd)


def mkdir_at(name, dir_fd, mode=0o755):
    """Make the folder ``name`` inside the open directory ``dir_fd``."""
    if WINDOWS:
        return os.mkdir(_windows_child(dir_fd, name), mode)
    return os.mkdir(name, mode, dir_fd=dir_fd)


def rmdir_at(name, dir_fd):
    """Remove the empty folder ``name`` inside the open directory ``dir_fd``."""
    if WINDOWS:
        return os.rmdir(_windows_child(dir_fd, name))
    return os.rmdir(name, dir_fd=dir_fd)


def replace_at(source, name, dir_fd):
    """Rename ``source`` to ``name`` in the open directory ``dir_fd``, replacing a file already called ``name``."""
    if WINDOWS:
        return os.replace(_windows_child(dir_fd, source), _windows_child(dir_fd, name))
    return os.replace(source, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def symlink_at(target, name, dir_fd):
    """Make the link ``name`` to ``target`` inside the open directory ``dir_fd``. Windows needs Developer Mode or an
    administrator for this and raises OSError without it."""
    if WINDOWS:
        return os.symlink(target, _windows_child(dir_fd, name))
    return os.symlink(target, name, dir_fd=dir_fd)


def unlink_at(name, dir_fd):
    """Remove the file ``name`` inside the open directory ``dir_fd``."""
    if WINDOWS:
        return os.unlink(_windows_child(dir_fd, name))
    return os.unlink(name, dir_fd=dir_fd)


def link_at(source, name, dir_fd):
    """Hard-link ``source`` to the new name ``name``, both inside the open directory ``dir_fd``. It fails with
    FileExistsError when anything already has that name, on both platforms (link(2), CreateHardLinkW)."""
    if WINDOWS:
        return os.link(_windows_child(dir_fd, source), _windows_child(dir_fd, name))
    return os.link(source, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)


def free_bytes(dir_fd):
    """Bytes free to this account on the volume the open directory ``dir_fd`` is on."""
    if WINDOWS:
        import shutil
        return shutil.disk_usage(_windows_final_path(dir_fd)).free
    volume = os.fstatvfs(dir_fd)
    return volume.f_bavail * volume.f_frsize


def scandir_fd(fd):
    """os.scandir over the open directory ``fd``. Windows lists the handle's real path, and each entry's
    stat(follow_symlinks=False) is a real lstat there, because a Windows DirEntry reports st_nlink as 0."""
    if WINDOWS:
        return _WindowsScandir(_windows_final_path(fd))
    return os.scandir(fd)


class _WindowsScandir:
    def __init__(self, path):
        self._scan = os.scandir(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._scan.close()

    def __iter__(self):
        for entry in self._scan:
            yield _WindowsEntry(entry)


class _WindowsEntry:
    def __init__(self, entry):
        self._entry, self.name, self.path = entry, entry.name, entry.path

    def stat(self, *, follow_symlinks=True):
        return os.stat(self.path) if follow_symlinks else os.lstat(self.path)

    def is_symlink(self):
        return self._entry.is_symlink() or self._entry.is_junction()

    def is_dir(self, *, follow_symlinks=True):
        return self._entry.is_dir(follow_symlinks=follow_symlinks)

    def is_file(self, *, follow_symlinks=True):
        return self._entry.is_file(follow_symlinks=follow_symlinks)


def _kernel32():
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel32.GetFinalPathNameByHandleW.argtypes = (wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _windows_final_path(fd):
    """The real path of the open ``fd``, as Windows reports it (with its \\\\?\\ prefix)."""
    import ctypes
    import msvcrt
    kernel32 = _kernel32()
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetFinalPathNameByHandleW(msvcrt.get_osfhandle(fd), buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.value


def _windows_child(dir_fd, name):
    if (not isinstance(name, str) or name in ('', '.', '..')
            or any(c in name for c in '\\/:\x00') or name != name.rstrip('. ')):
        raise OSError(errno.EINVAL, 'Not a plain name inside a folder', name)
    parent = _windows_final_path(dir_fd)
    return parent + name if parent.endswith('\\') else parent + '\\' + name


def _windows_open_at(name, dir_fd, *, directory, mode):
    path = _windows_child(dir_fd, name)
    return _windows_open(path, directory=directory, mode=mode, check=path)


def _windows_open(path, *, directory, mode, check):
    """CreateFileW for ``path`` wrapped as a CRT fd. ``check`` is the real path the result must have."""
    import ctypes
    import msvcrt
    kernel32 = _kernel32()
    if directory:
        access = 0x0001 | 0x0080 | 0x00100000  # FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES, SYNCHRONIZE
        share = 0x1 | 0x2                      # read and write sharing, never delete
        flags = 0x02000000                     # FILE_FLAG_BACKUP_SEMANTICS: needed to open a directory
    else:
        access = {'r': 0x80000000, 'rw': 0x80000000 | 0x40000000, 'new': 0x40000000}[mode]
        share = 0x1 | 0x2 | 0x4
        flags = 0x80                           # FILE_ATTRIBUTE_NORMAL
    disposition = 1 if mode == 'new' else 3    # CREATE_NEW or OPEN_EXISTING
    handle = kernel32.CreateFileW(path, access, share, None, disposition, flags, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        raise OSError(None, ctypes.FormatError(error).strip(), path, error)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if mode == 'r' else 0)
    except OSError:
        kernel32.CloseHandle(handle)
        raise
    try:
        import stat
        if check is not None and os.path.normcase(_windows_final_path(fd)) != os.path.normcase(check):
            raise OSError(errno.ELOOP, 'A link or junction leads somewhere else', check)
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        if directory and not is_dir:
            raise NotADirectoryError(errno.ENOTDIR, 'Not a directory', path)
        if not directory and is_dir:
            raise IsADirectoryError(errno.EISDIR, 'Is a directory', path)
    except BaseException:
        os.close(fd)
        raise
    return fd
