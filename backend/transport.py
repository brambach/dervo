"""Private stdio transport. Never expose argv or this class to a renderer."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from dervo_connections import Rejected
import platform_os


def account_home():
    """The OS account home from the passwd database. The host runs with a cleared environment,
    so inherited HOME is never consulted."""
    try:
        home = platform_os.account_home()
    except KeyError:
        raise Rejected('The OS account home could not be resolved') from None
    if not home or not Path(home).is_absolute():
        raise Rejected('The OS account home is not an absolute path')
    return home


def account_name():
    """The OS account name from the passwd database, the same source as ``account_home`` so the two always agree.

    Not ``os.environ`` and not ``getpass.getuser``: the host is started with a cleared environment (launch.rs,
    ``env_clear``), so USER is never there to read, and a launchd-started process has none either. The passwd entry is
    what USER holds in the Terminal where the person signed Claude Code in, which is what the Keychain item is named
    after. An account name is not a credential."""
    try:
        name = platform_os.account_name()
    except KeyError:
        raise Rejected('The OS account name could not be resolved') from None
    if not name:
        raise Rejected('The OS account name is empty')
    return name


def clean_environment(provider, profile_home, process_home, *, api_key=None):
    """Allowlist, not a credential denylist. No shell startup files are loaded.

    ``api_key`` is the Claude API key of an API-key account, read from Keychain by the caller for this one process. It
    is placed in ANTHROPIC_API_KEY, which Claude Code's documented precedence uses ahead of any saved login, and it is
    never part of argv. Codex API-key accounts need nothing here: Codex keeps its own key inside CODEX_HOME.

    Claude: HOME is the real OS account home because macOS Keychain lookup needs it
    (a substitute HOME has no default keychain). The credential stays owned and distinct
    through CLAUDE_CONFIG_DIR, which the CLI documents as keying both the credentials
    file and the Keychain entry. HOME therefore provides no filesystem isolation for
    Claude; tool and permission restrictions do that. process_home is validated but
    unused for Claude.
    Your existing sign-in (the system config dir) also gets USER, and only it: see the comment at the assignment.
    Codex: unchanged, HOME is the owned process home and CODEX_HOME the profile.
    """
    if provider not in ('claude', 'codex') or not all(Path(p).is_absolute() for p in (profile_home, process_home)):
        raise Rejected('Provider and owned absolute homes are required')
    env = platform_os.provider_environment()
    if provider == 'claude':
        env['HOME'] = account_home()
        # Dervo's project memory is the one memory (PLAN-native.md, A9). --restricted switches Claude Code's auto memory
        # off in 2.1.274 too, but the docs don't say so; this is the documented switch.
        env['CLAUDE_CODE_DISABLE_AUTO_MEMORY'] = '1'
        from claude_policy import is_system_home
        if not is_system_home(profile_home):
            # A Dervo-owned account: its own credentials and Keychain entry. Your existing sign-in is Claude Code's
            # default, which only resolves when CLAUDE_CONFIG_DIR is unset.
            env['CLAUDE_CONFIG_DIR'] = profile_home
        else:
            # Claude Code reads its sign-in from the Keychain item for the current user, and it names that user from
            # USER (2.1.274 runs `security find-generic-password -a "$USER"`; its fallback doesn't resolve under this
            # environment). Without USER `auth status` truthfully reports loggedIn false, authMethod none, and first run
            # told signed-in testers to sign in (21 Sep). Measured both directions: this environment alone is signed
            # out, plus USER it is signed in; the full shell environment minus USER is signed out. LOGNAME alone does
            # nothing, and an empty USER reads as absent. A username is not a credential, and nothing else reads it.
            # Only your existing sign-in gets it. A Dervo-owned account signed in through LoginSession, in this
            # environment without USER, so its Keychain item is under the CLI's fallback name: with USER present all
            # three owned accounts on this Mac read as signed out (measured 21 Sep). They stay as they are.
            env['USER'] = account_name()
        if api_key is not None:
            if type(api_key) is not str or not api_key or not api_key.isascii() or any(c.isspace() for c in api_key):
                raise Rejected('The API key for this account is unreadable.')
            env['ANTHROPIC_API_KEY'] = api_key
    else:
        env['HOME'] = process_home
        env['CODEX_HOME'] = profile_home
    return env


# One line holds one event. Claude Code puts a whole tool result on a line, and a screenshot it reads arrives as base64.
LINE_LIMIT = 16 * 1024 * 1024
# A whole run. Streamed deltas, file reads and command output add up over a long task: 8 MiB stopped a real full-access
# run partway (17 Sep). The deadlines still bound a stuck agent; this bounds one that floods.
RUN_LIMIT = 1024 * 1024 * 1024


def is_limit_message(text):
    """True for the two output-limit reasons below, so a caller can tell Dervo's own stop from a crash."""
    return isinstance(text, str) and text.startswith('The agent sent ') and 'so Dervo stopped' in text


class JsonLines:
    def __init__(self, limit=LINE_LIMIT):
        self.buffer, self.limit = bytearray(), limit

    def feed(self, chunk):
        self.buffer.extend(chunk)
        result = []
        while b'\n' in self.buffer:
            line, _, rest = self.buffer.partition(b'\n')
            self.buffer = bytearray(rest)
            if len(line) > self.limit:
                raise Rejected(f'The agent sent one message over {self.limit // (1024 * 1024)} MiB, so Dervo stopped the run.')
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                raise Rejected('Provider sent malformed JSONL') from None
            if not isinstance(value, dict):
                raise Rejected('Provider sent a non-object event')
            result.append(value)
        if len(self.buffer) > self.limit:
            raise Rejected(f'The agent sent one message over {self.limit // (1024 * 1024)} MiB, so Dervo stopped the run.')
        return result

    def finish(self):
        if self.buffer:
            raise Rejected('Provider exited in the middle of an event')


def threaded_writes():
    """Whether a provider's stdin is written from its own thread (Windows) instead of with a non-blocking deadline."""
    return platform_os.WINDOWS


class OwnedProcess:
    """Bounded POSIX child group. Call only from trusted host code."""
    def __init__(self, argv, *, cwd, env, byte_limit=RUN_LIMIT, native_commands=False):
        if not argv or not Path(argv[0]).is_absolute():
            raise Rejected('Use an explicitly selected absolute executable')
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        **platform_os.new_group(contained=True), bufsize=0)
        try:
            platform_os.contain(self.process)
        except OSError:
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                pipe.close()
            raise
        self.selector = platform_os.pipe_selector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ, 'stdout')
        self.selector.register(self.process.stderr, selectors.EVENT_READ, 'stderr')
        self.decoder, self.stderr = JsonLines(), bytearray()
        self.byte_limit, self.read_bytes, self.closed = byte_limit, 0, False
        self.native_commands = native_commands
        self._writer = self._write_error = None

    def __enter__(self):
        return self

    def send(self, message):
        from provider_image_input import encode_message
        self._send_bytes(encode_message(message))

    def send_text(self, text):
        raw = text.encode()
        if len(raw) > 131072:
            raise Rejected('Provider input exceeds 128 KiB')
        self._send_bytes(raw)

    def _send_bytes(self, raw):
        if threaded_writes():
            return self._queue_bytes(raw)
        # Nonblocking writes with a deadline prevent a stalled child blocking the host.
        fd = self.process.stdin.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + 2
        with platform_os.pipe_selector() as writable:
            writable.register(fd, selectors.EVENT_WRITE)
            while raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not writable.select(remaining):
                    raise TimeoutError('Provider did not consume input')
                try:
                    count = os.write(fd, raw)
                    raw = raw[count:]
                except BlockingIOError:
                    continue

    def _queue_bytes(self, raw):
        """Windows: a writer thread owns stdin and waits on it, so the host never does. An anonymous pipe there holds
        about 4 KiB, so a bigger message (a file a tool read, say) only goes through while the provider is reading, and
        the 2 s non-blocking write ended Harry's run on 27 Sep with "Provider did not consume input". A write that fails
        (the provider exited or closed stdin) is reported on the next send, the way the deadline reported it before."""
        if self._write_error is not None:
            raise TimeoutError('Provider did not consume input')
        if self._writer is None:
            import queue
            import threading
            self._pending = queue.SimpleQueue()
            self._writer = threading.Thread(target=self._write_loop, name='dervo-provider-stdin', daemon=True)
            self._writer.start()
        self._pending.put(bytes(raw))

    def _write_loop(self):
        stdin = self.process.stdin
        while True:
            raw = self._pending.get()
            if raw is None:
                # End of input comes from this thread: closing a handle another thread is writing to can hang on Windows.
                stdin.close()
                return
            try:
                view = memoryview(raw)
                while view:
                    view = view[stdin.write(view) or 0:]
            except (OSError, ValueError) as exc:
                self._write_error = exc
                return

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while self.selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Provider event deadline exceeded')
            for key, _ in self.selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if key.data == 'stdout':
                        self.decoder.finish()
                    continue
                self.read_bytes += len(chunk)
                if self.read_bytes > self.byte_limit:
                    raise Rejected(f'The agent sent more than {self.byte_limit // (1024 * 1024)} MiB of output in one run, so Dervo stopped it.')
                if key.data == 'stderr':
                    self.stderr.extend(chunk[:max(0, 65536 - len(self.stderr))])
                else:
                    events = self.decoder.feed(chunk)
                    if events:
                        return events
        return None

    def interrupt(self):
        if self.process.poll() is None:
            platform_os.signal_group(self.process.pid, signal.SIGINT)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._writer is not None:
            self._pending.put(None)
        children = {}
        if self.native_commands and self.process.poll() is None:
            from process_descendants import descendants
            children = descendants(self.process.pid)
            # Codex closes its independent command sessions on stdin EOF. Killing the server first leaves them alive.
            if self._writer is None:
                self.process.stdin.close()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                children.update(descendants(self.process.pid))
        # macOS answers EPERM, not ESRCH, for the group of a leader that already exited.
        try:
            platform_os.signal_group(self.process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # Same EPERM as above when the leader has gone. Kill the leader itself so the wait below can end.
            try:
                platform_os.signal_group(self.process.pid, platform_os.SIGKILL)
            except (ProcessLookupError, PermissionError):
                self.process.kill()
            self.process.wait(timeout=1)
        # A leader can exit before descendants. Clean the owned group too.
        try:
            platform_os.signal_group(self.process.pid, platform_os.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        platform_os.release(self.process.pid)
        if children:
            from process_descendants import stop_descendants
            stop_descendants(children)
        self.selector.close()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()

    def __exit__(self, *args):
        self.close()
