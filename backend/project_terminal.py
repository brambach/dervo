"""Human terminal sessions. No provider tools, saved transcripts or model context."""
import base64
import binascii
from collections import OrderedDict
import errno
import hashlib
import os
from pathlib import Path
try:
    import pwd
except ImportError:  # Windows: the shell runs in a ConPTY console (platform_os.WindowsConsole) and needs no pwd
    pwd = None
import signal
import subprocess
import sys
import time
import uuid

from dervo_connections import Rejected
import platform_os

FIELDS = {
    'terminal_open': {'projectId', 'clientId', 'cols', 'rows'},
    'terminal_list': {'projectId'},
    'terminal_read': {'projectId', 'sessionId', 'cursor'},
    'terminal_write': {'projectId', 'sessionId', 'sequence', 'dataBase64'},
    'terminal_resize': {'projectId', 'sessionId', 'cols', 'rows'},
    'terminal_close': {'projectId', 'sessionId'},
}
MAX_SESSIONS = 4
OUTPUT_LIMIT = 1024 * 1024
READ_LIMIT = 65536
INPUT_LIMIT = 65536
WRITE_LIMIT = 16384
REPLAY_LIMIT = 64
OPEN_LIMIT = 4096  # small idempotency tombstones, never terminal content


def dimensions(cols, rows):
    if type(cols) is not int or type(rows) is not int or not 2 <= cols <= 500 or not 1 <= rows <= 300:
        raise Rejected('Choose a terminal size between 2 and 500 columns and 1 and 300 rows.')


def shell_environment():
    account = pwd.getpwuid(os.getuid())
    shell, home = account.pw_shell or '/bin/sh', account.pw_dir
    if not Path(shell).is_absolute() or not Path(shell).is_file() or not os.access(shell, os.X_OK) or not Path(home).is_absolute():
        raise Rejected('The Mac account shell or home folder is unavailable.')
    # The host may hold provider credentials and a Keychain pipe. None is inherited.
    return shell, {'HOME': home, 'USER': account.pw_name, 'LOGNAME': account.pw_name, 'SHELL': shell,
                   'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
                   'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8', 'TERM': 'xterm-256color',
                   'COLORTERM': 'truecolor', 'TERM_PROGRAM': 'Dervo'}


def windows_shell():
    """Windows: PowerShell 7 (pwsh.exe) when it's on the account's PATH, otherwise Windows PowerShell, which every
    Windows 10 and 11 has, and cmd.exe as the last resort. The environment is the one a new PowerShell window gets for
    this account (platform_os.account_environment), never the host's."""
    env = platform_os.account_environment()
    names = {name.upper(): name for name in env}
    get = lambda name: env.get(names.get(name.upper(), name))
    root = get('SYSTEMROOT') or r'C:\Windows'
    folders = [folder for folder in (get('PATH') or '').split(os.pathsep) if folder and Path(folder).is_absolute()]
    candidates = [os.path.join(folder, 'pwsh.exe') for folder in folders]
    candidates += [os.path.join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
                   get('COMSPEC') or os.path.join(root, 'System32', 'cmd.exe')]
    shell = next((path for path in candidates if os.path.isfile(path)), None)
    if shell is None:
        raise Rejected('Windows PowerShell is unavailable, so the terminal can’t start.')
    env.update(TERM_PROGRAM='Dervo', COLORTERM='truecolor')
    return ([shell] if shell.lower().endswith('cmd.exe') else [shell, '-NoLogo']), env


def identity(pid):
    from coding_integration import process_identity
    return process_identity(pid)


def owned_processes(roots, expected=None):
    """One bounded process-table read for every terminal being closed."""
    saved = {}
    for pid in roots:
        live = identity(pid)
        if live and (expected is None or (expected.get(pid) and live['start'] == expected[pid]['start'])):
            saved[pid] = live
    try:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], capture_output=True, text=True,
                                timeout=.5, check=True)
        parents = dict(tuple(map(int, line.split())) for line in result.stdout.splitlines())
    except (OSError, ValueError, subprocess.SubprocessError):
        return saved
    pending = set(saved)
    while pending:
        children = {pid for pid, parent in parents.items() if parent in pending and pid not in saved}
        pending = set()
        for pid in children:
            live = identity(pid)
            if live and live['ppid'] == parents[pid]:
                saved[pid] = live
                pending.add(pid)
    return saved


def still_owned(pid, saved):
    live = identity(pid)
    # exec changes the executable, but a process start timestamp stays its identity.
    return live is not None and live['start'] == saved['start']


class Session:
    def __init__(self, project, actor, cols, rows):
        self.id, self.project, self.actor = uuid.uuid4().hex, project['id'], actor
        self.cwd = project['cwd']
        self.cols, self.rows = cols, rows
        self.output, self.pending = bytearray(), bytearray()
        self.end = self.sequence = 0
        self.replays = OrderedDict()
        self.state, self.exit_code = 'running', None
        self.completion = 'closed'
        self.cleanup_owned = {}
        self.master = self.console = None
        self.command_name = (0, None)
        if platform_os.WINDOWS:
            try:
                argv, env = windows_shell()
                self.console = self.process = platform_os.WindowsConsole(argv, self.cwd, env, cols, rows)
            except OSError:
                raise Rejected('The terminal couldn’t start. Check that the project folder and PowerShell are available.') from None
            # Its Job Object is its process tree, so there's no process table to remember (Terminals.remember).
            self.root_identity = None
            return
        shell, env = shell_environment()
        slave = None
        try:
            self.master, slave = platform_os.open_pty()
            platform_os.set_pty_size(slave, rows, cols)
            os.set_blocking(self.master, False)
            helper = str(Path(__file__).with_name('project_terminal_bootstrap.py'))
            self.process = subprocess.Popen([sys.executable, '-E', '-s', '-B', helper, str(slave), shell],
                                            cwd=self.cwd, env=env, pass_fds=(slave,), close_fds=True,
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            self.root_identity = identity(self.process.pid)
        except (OSError, ValueError):
            if self.master is not None:
                os.close(self.master)
            raise Rejected('The terminal couldn’t start. Check that the project folder and Mac shell are available.') from None
        finally:
            if slave is not None:
                os.close(slave)

    @property
    def active(self):
        return self.state in ('running', 'closing')

    def foreground(self):
        """The name of the command the shell is running now, or None at the prompt. Read from the terminal's foreground
        process group, so it needs nothing in your shell's startup files. The name is looked up once per command."""
        if self.console is not None:
            return self.console.foreground() if self.state == 'running' else None
        if self.state != 'running' or self.master is None:
            return None
        try:
            group = os.tcgetpgrp(self.master)
        except OSError:
            return None
        if group <= 0 or group == self.process.pid:
            return None
        if self.command_name[0] != group:
            try:
                result = subprocess.run(['/bin/ps', '-o', 'comm=', '-p', str(group)], capture_output=True, text=True, timeout=.5)
                name = Path(result.stdout.strip()).name[:64] if result.returncode == 0 else ''
            except (OSError, subprocess.SubprocessError):
                name = ''
            self.command_name = (group, ''.join(c for c in name if c.isprintable()) or 'A command')
        return self.command_name[1]

    def metadata(self):
        return {'sessionId': self.id, 'projectId': self.project, 'state': self.state, 'cwd': self.cwd,
                'cols': self.cols, 'rows': self.rows, 'nextInputSeq': self.sequence,
                'startCursor': self.end - len(self.output), 'endCursor': self.end,
                'exitCode': self.exit_code, 'pendingInputBytes': len(self.pending), 'foreground': self.foreground()}

    def append(self, raw):
        self.output.extend(raw)
        self.end += len(raw)
        if len(self.output) > OUTPUT_LIMIT:
            del self.output[:-OUTPUT_LIMIT]

    def drain(self):
        if self.console is not None:
            self.append(self.console.read())
            if self.pending and self.state == 'running':
                try:
                    del self.pending[:self.console.write(self.pending[:WRITE_LIMIT])]
                except OSError:
                    pass
            return
        if self.master is None:
            return
        for _ in range(4):
            try:
                raw = os.read(self.master, 16384)
                if not raw:
                    break
                self.append(raw)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno not in (errno.EIO, errno.EBADF):
                    raise
                break
        if self.pending and self.state == 'running':
            try:
                count = os.write(self.master, self.pending[:WRITE_LIMIT])
                del self.pending[:count]
            except BlockingIOError:
                pass
            except OSError as exc:
                if exc.errno not in (errno.EIO, errno.EBADF):
                    raise

    def read(self, cursor):
        if type(cursor) is not int or cursor < 0 or cursor > self.end:
            raise Rejected('The terminal output position is invalid. Reopen the terminal view.')
        start = self.end - len(self.output)
        offset = max(cursor, start) - start
        raw = bytes(self.output[offset:offset + READ_LIMIT])
        return {**self.metadata(), 'dataBase64': base64.b64encode(raw).decode('ascii'),
                'nextCursor': max(cursor, start) + len(raw), 'dropped': max(0, start - cursor)}

    def write(self, sequence, encoded):
        if type(sequence) is not int or sequence < 0 or type(encoded) is not str or len(encoded) > 4 * ((WRITE_LIMIT + 2) // 3):
            raise Rejected('Terminal input is too large or has an invalid sequence.')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise Rejected('Terminal input must contain valid base64 bytes.') from None
        if not 1 <= len(raw) <= WRITE_LIMIT or base64.b64encode(raw).decode('ascii') != encoded:
            raise Rejected('Terminal input must contain between 1 and 16384 bytes in canonical base64.')
        digest = hashlib.sha256(raw).digest()
        if sequence < self.sequence:
            if self.replays.get(sequence) != (digest, len(raw)):
                raise Rejected('This terminal input retry is stale or changed. It wasn’t sent again.')
        else:
            if sequence != self.sequence:
                raise Rejected('Terminal input arrived out of order. Retry the pending input first.')
            if self.state != 'running':
                raise Rejected('This terminal has stopped. Open a new terminal to type commands.')
            if len(self.pending) + len(raw) > INPUT_LIMIT:
                raise Rejected('The terminal input queue is full. Wait for it to accept more input.')
            self.pending.extend(raw)
            self.replays[sequence] = (digest, len(raw))
            self.sequence += 1
            if len(self.replays) > REPLAY_LIMIT:
                self.replays.popitem(last=False)
        return {'sessionId': self.id, 'sequence': sequence, 'nextInputSeq': self.sequence,
                'acceptedBytes': len(raw), 'pendingInputBytes': len(self.pending)}

    def resize(self, cols, rows):
        dimensions(cols, rows)
        if self.state == 'running' and (self.master is not None or self.console is not None):
            try:
                if self.console is not None:
                    self.console.resize(rows, cols)
                else:
                    platform_os.set_pty_size(self.master, rows, cols)
            except OSError:
                raise Rejected('The terminal couldn’t be resized. Check whether its shell has stopped.') from None
            self.cols, self.rows = cols, rows
        return self.metadata()


class Terminals:
    def __init__(self, host):
        self.host = host
        self.sessions = OrderedDict()
        self.opens = {}
        self.next_snapshot = 0

    def project(self, pid):
        if type(pid) is not str or not pid or len(pid) > 256:
            raise Rejected('Choose a project for the terminal.')
        row = self.host.store.db.execute('SELECT id,cwd FROM projects WHERE id=?', (pid,)).fetchone()
        if row is None:
            raise Rejected('This terminal project is unavailable.')
        return dict(row)

    def active(self, pid):
        return any(session.project == pid and session.active for session in self.sessions.values())

    def remember(self, sessions):
        if platform_os.WINDOWS:
            return  # each shell's Job Object holds everything it started, so close() ends them all
        for session in sessions:
            # An unreaped child can't have its PID reused. Never adopt a new root
            # after poll has reaped it, even if the OS gives its PID to another app.
            if session.root_identity is None and session.process.returncode is None:
                live = identity(session.process.pid)
                if live and live['ppid'] == os.getpid():
                    session.root_identity = live
        snapshot = owned_processes([s.process.pid for s in sessions],
                                   {s.process.pid: s.root_identity for s in sessions})
        for session in sessions:
            members = {session.process.pid}
            while True:
                children = {pid for pid, saved in snapshot.items() if saved['ppid'] in members}
                if children.issubset(members):
                    break
                members.update(children)
            session.cleanup_owned.update({pid: saved for pid, saved in snapshot.items() if pid in members})
            session.cleanup_owned = {pid: saved for pid, saved in session.cleanup_owned.items() if still_owned(pid, saved)}

    def tick(self):
        if time.monotonic() >= self.next_snapshot and any(s.active for s in self.sessions.values()):
            self.remember([s for s in self.sessions.values() if s.active])
            self.next_snapshot = time.monotonic() + 1
        ended = []
        for session in self.sessions.values():
            session.drain()
            if session.state == 'running' and session.process.poll() is not None:
                ended.append(session)
            elif session.state == 'closing':
                session.cleanup_owned = {pid: saved for pid, saved in session.cleanup_owned.items() if still_owned(pid, saved)}
                session.exit_code = session.process.poll()
                if not session.cleanup_owned and session.exit_code is not None:
                    session.state = session.completion
        if ended:
            self.stop(ended, exited=True)

    def stop(self, sessions, *, exited=False):
        sessions = [s for s in sessions if s.active]
        if not sessions:
            return
        # A single grace period covers every shell, including jobs in other groups.
        self.remember(sessions)
        owned = {}
        for session in sessions:
            owned.update(session.cleanup_owned)
            session.state = 'closing'
            session.completion = 'exited' if exited else 'closed'
            session.drain()
            session.pending.clear()
            if session.console is not None:
                session.console.close()
            if session.master is not None:
                os.close(session.master)
                session.master = None
        # Windows has no SIGKILL (platform_os.SIGKILL stands in), and nothing is in ``owned`` there: close() ended the job.
        for sig, grace in ((signal.SIGTERM, .2), (platform_os.SIGKILL, .3)):
            for pid, saved in reversed(list(owned.items())):
                if still_owned(pid, saved):
                    try:
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError):
                        pass
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                for session in sessions:
                    session.process.poll()
                if all(s.process.returncode is not None for s in sessions) and not any(still_owned(pid, saved) for pid, saved in owned.items()):
                    break
                time.sleep(.01)
        surviving = {pid: saved for pid, saved in owned.items() if still_owned(pid, saved)}
        for session in sessions:
            session.exit_code = session.process.poll()
            # Keep a hold if process ownership couldn't be cleared. Retrying Close
            # retries cleanup rather than pretending the process is gone.
            session.cleanup_owned = {pid: saved for pid, saved in session.cleanup_owned.items() if pid in surviving}
            session.state = 'closing' if session.cleanup_owned or session.exit_code is None else session.completion

    def close(self):
        self.stop(list(self.sessions.values()))

    def dispatch(self, operation, args, actor):
        if type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported terminal fields.')
        project = self.project(args['projectId'])
        pid = project['id']
        if operation == 'terminal_open':
            self.host.require_beta_access()
            if self.host.store.db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (pid,)).fetchone():
                raise Rejected('Restore this project before opening a terminal.')
            dimensions(args['cols'], args['rows'])
            client = args['clientId']
            if type(client) is not str or not 1 <= len(client) <= 128:
                raise Rejected('Choose a terminal request ID.')
            key = (actor, pid, client)
            if key in self.opens:
                previous = self.sessions.get(self.opens[key])
                if previous is None:
                    raise Rejected('That terminal has closed. Choose New terminal to start another.')
                return previous.metadata()
            if len(self.opens) >= OPEN_LIMIT:
                raise Rejected('Restart Dervo before opening more terminal sessions.')
            previous = next((s for s in self.sessions.values() if s.project == pid), None)
            if previous and previous.active:
                if previous.actor != actor:
                    raise Rejected('This terminal belongs to another local identity.')
                self.opens[key] = previous.id
                return previous.metadata()
            try:
                folder = Path(project['cwd'])
                if not folder.is_absolute() or not folder.is_dir() or str(folder.resolve(strict=True)) != project['cwd']:
                    raise ValueError()
            except (OSError, ValueError):
                raise Rejected('The project folder moved or is unavailable. Select it again before opening a terminal.') from None
            disposable = previous.id if previous else None
            if disposable is None and len(self.sessions) >= MAX_SESSIONS:
                disposable = next((s.id for s in self.sessions.values() if not s.active), None)
                if disposable is None:
                    raise Rejected('Close a terminal before opening another. Dervo keeps up to four shells open.')
            session = Session(project, actor, args['cols'], args['rows'])
            if disposable:
                del self.sessions[disposable]
            self.sessions[session.id] = session
            self.opens[key] = session.id
            return session.metadata()
        if operation == 'terminal_list':
            return {'sessions': [s.metadata() for s in self.sessions.values() if s.project == pid and s.actor == actor]}
        sid = args['sessionId']
        session = self.sessions.get(sid) if type(sid) is str else None
        if session is None or session.project != pid or session.actor != actor:
            raise Rejected('This terminal doesn’t belong to the selected project or is no longer available.')
        if operation == 'terminal_read':
            session.drain()
            return session.read(args['cursor'])
        if operation == 'terminal_write':
            self.host.require_beta_access()
            if self.host.store.db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (pid,)).fetchone():
                raise Rejected('Restore this project before typing in its terminal.')
            return session.write(args['sequence'], args['dataBase64'])
        if operation == 'terminal_resize':
            return session.resize(args['cols'], args['rows'])
        self.stop([session])
        return session.metadata()
