"""Official browser login lifecycle. CLI output never crosses the host boundary.

Grounding: developers.openai.com/codex/auth (saved 2026-09-13) documents
`codex login` as the default browser flow. No app-server login schema is used.
The evidence doesn't establish a browser origin, so no printed URL is exposed.
"""
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
import time
from dervo_connections import Rejected
from transport import clean_environment
from codex_policy import check_profile, login_argv
import platform_os


def official_link(_text):
    """No evidenced exact browser origin is available. Fail closed for every URL."""
    return None


def idle_status():
    return dict(state='idle', phase=None, url=None, needsCode=False, active=False,
                exitStatus=None, detail='Choose Connect Codex to start the official browser sign-in.')


class LoginSession:
    def __init__(self, profile, executable_path, *, popen=subprocess.Popen, timeout=600):
        if (profile.get('provider') != 'codex' or profile.get('category') not in ('personal', 'work')
                or not (profile.get('identity') or profile.get('pending') is True) or not Path(profile['home']).is_absolute()):
            raise Rejected('Choose a Codex account with an expected identity.')
        if platform_os.WINDOWS:
            raise Rejected('Codex isn’t available in Dervo on Windows yet. Claude Code is.')
        check_profile(profile['home'])
        self.profile, self.executable_path = dict(profile), executable_path
        args = login_argv(executable_path)
        self.neutral = tempfile.TemporaryDirectory(prefix='dervo-codex-login-', dir=platform_os.scratch_root())
        self.master = None
        slave = None
        try:
            self.master, slave = platform_os.open_pty()
            self.process = popen(args, stdin=slave, stdout=slave, stderr=slave,
                cwd=self.neutral.name, env=clean_environment('codex', profile['home'], profile['home']),
                **platform_os.new_group(), close_fds=True)
        except Exception:
            if self.master is not None: os.close(self.master)
            self.neutral.cleanup()
            raise Rejected('Codex sign-in could not start. Check the CLI installation, then retry Connect Codex.') from None
        finally:
            if slave is not None: os.close(slave)
        self.state, self.phase = 'waiting', 'starting'
        self.detail = 'Starting official Codex sign-in. No identity has been verified.'
        self.closed, self.identity_scheduled = False, False
        self.cleanup_pending = False
        self.exit_status = None
        self.deadline = time.monotonic() + timeout

    def _drain(self):
        # Bound each poll even when a peer floods the PTY. Discard bytes, including
        # split URLs, auth codes, escape sequences and errors, without retaining them.
        for _ in range(16):
            try:
                if not select.select([self.master], [], [], 0)[0]: break
                if not os.read(self.master, 4096): break
            except OSError:
                break

    def poll(self):
        if self.closed: return
        if self.cleanup_pending:
            self._close()
            return
        self._drain()
        code = self.process.poll()
        if code is not None:
            self.exit_status = code
            self.state = 'succeeded' if code == 0 else 'failed'
            self.detail = ('Codex sign-in process finished. Checking the expected identity.' if code == 0 else
                           'Codex sign-in failed. Retry Connect Codex. No identity has been verified.')
            self._close()
        elif time.monotonic() >= self.deadline:
            self.cancel('Codex sign-in timed out. Retry Connect Codex or choose Check identity to recheck.')
            if self.closed: self.state = 'timed_out'
        else:
            self.phase = 'browser'
            self.detail = ('Codex is running its browser sign-in. Complete it in the browser if it opened. '
                           'If no browser opens, cancel and retry Connect Codex. Identity is still unverified.')

    def submit_code(self, _code):
        raise Rejected('Codex uses the official browser callback. Dervo does not accept sign-in codes.')

    def cancel(self, reason='Codex sign-in cancelled. Retry Connect Codex when ready.'):
        if self.closed: return
        self.state, self.detail = 'cancelled', reason
        self._close()

    def _close(self):
        if self.closed: return
        # Kill the group even if its leader already exited, so descendants don't
        # keep the callback listener alive. Each grace period is bounded.
        for sig, grace in ((signal.SIGTERM, .2), (platform_os.SIGKILL, .2)):
            try: platform_os.signal_group(self.process.pid, sig)
            except ProcessLookupError: break
            except OSError:
                self.cleanup_pending = True
                self.state = 'unknown'
                self.detail = 'Codex sign-in cleanup is unconfirmed. Cancel again before retrying. Identity is unknown.'
                return
            try: self.process.wait(timeout=grace)
            except subprocess.TimeoutExpired: pass
        try: self.process.wait(timeout=.2)
        except subprocess.TimeoutExpired:
            self.cleanup_pending = True
            self.state = 'unknown'
            self.detail = 'Codex sign-in cleanup is unconfirmed. Cancel again before retrying. Identity is unknown.'
            return
        self.exit_status = self.process.poll()
        self.closed = True
        os.close(self.master)
        self.neutral.cleanup()

    def status(self):
        return dict(state=self.state, phase=self.phase, url=None, needsCode=False,
                    active=not self.closed, detail=self.detail, exitStatus=self.exit_status,
                    expectedIdentity=self.profile['identity'])
