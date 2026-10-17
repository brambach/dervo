"""Owned-profile Claude sign-in through the published CLI's own login command.

Dervo never reads, copies or proxies credentials. It starts `claude auth login`
inside the owned CLAUDE_CONFIG_DIR on a pseudo-terminal (pipes on Windows), mirrors the CLI's
observable state (sign-in link, paste-code prompt, success, failure), forwards
a code the user pastes, presses Enter when the CLI reports success, and leaves
identity read-back to `claude auth status` in the same owned profile.

Documented CLI behaviour this relies on (code.claude.com, authentication):
  * the login opens a browser; if the browser can't reach the local callback it
    shows a code to paste at "Paste code here if prompted";
  * on completion the terminal shows "Login successful" and asks for Enter;
  * with CLAUDE_CONFIG_DIR set, the credential and its keychain entry are keyed
    to that directory, so the user's own Claude login is never touched.
"""
import os
import re
import signal
import subprocess
import time
from urllib.parse import urlsplit
from dervo_connections import Rejected
from claude_policy import login_argv
from transport import clean_environment
import platform_os

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]|\r')
URL = re.compile(r'https://[^\s"\'<>]+')
# Subscription sign-in is a claude.ai browser flow (code.claude.com, authentication). Console
# sign-in is not requested, so no other host is ever surfaced as a sign-in link.
OFFICIAL_HOSTS = ('claude.ai',)
# Claude Code 2.1.280 and later prints its subscription link on claude.com (Windows runner, 25 Sep; the
# 2.1.280 binary on the Mac holds https://claude.com/cai/oauth/authorize). Only that exact path counts there:
# the rest of claude.com is the website, and platform.claude.com, the console, is still never shown.
OFFICIAL_PATHS = {'claude.com': '/cai/oauth/authorize'}
SUCCESS_TEXT = 'Login successful'
CODE_PROMPT = 'Paste code here'
OUTPUT_LIMIT = 65536
CODE_MASK = '[code]'
FAILED_DETAIL = 'Sign-in did not complete (exit status {code}). Try Connect Claude again, or use the manual sign-in steps.'


def official_link(text):
    """First https link on an exact official host, or on claude.com's exact sign-in path, or None. Anything else is
    never shown."""
    for link in URL.findall(text):
        candidate = link.rstrip('.,;)')
        parts = urlsplit(candidate)
        if parts.scheme != 'https' or '@' in parts.netloc or ':' in parts.netloc:
            continue
        if parts.hostname in OFFICIAL_HOSTS or OFFICIAL_PATHS.get(parts.hostname) == parts.path:
            return candidate
    return None
DETAILS = {
    'starting': 'Starting the official Claude Code sign-in inside the Dervo-owned profile. Your existing Claude login is not read or changed.',
    'browser': 'Your browser should open the official Claude sign-in page. If it did not, open the link below.',
    'code_prompt': 'Claude Code is asking for the code shown in your browser. Paste it here.',
    'finishing': 'Claude Code reported a successful login. Finishing up.',
}


class LoginSession:
    """One owned login process. Host thread only; poll() is cheap and never blocks."""

    def __init__(self, profile, executable_path, *, popen=subprocess.Popen, timeout=600):
        if profile.get('provider') != 'claude':
            raise Rejected('Native sign-in is available for Claude Code profiles')
        self.profile = dict(profile)
        home = profile['home']
        # A sign-in that adds an account (account_signin.py) has no email yet: Claude Code's page asks for it.
        argv = login_argv(executable_path, None if profile.get('pending') is True else profile['identity'])
        if platform_os.WINDOWS:
            # Dervo's Windows terminal (ConPTY) isn't built yet. `claude auth login` prints its link and prompts as
            # plain lines, so it runs on pipes, with stderr on the same pipe as a terminal would show it. Not in a Job
            # Object: a browser the CLI opens can end up in the job, and cancelling would close the tester's browser.
            self.process = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 cwd=home, env=clean_environment('claude', home, home), **platform_os.new_group())
            self.pipes = (self.process.stdin, self.process.stdout)
            self.master, self.input, self.enter = self.process.stdout.fileno(), self.process.stdin.fileno(), '\r\n'
        else:
            self.master, slave = platform_os.open_pty()
            try:
                self.process = popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=home,
                                     env=clean_environment('claude', home, home), **platform_os.new_group(), close_fds=True)
            except Exception:
                os.close(self.master)
                os.close(slave)
                raise
            os.close(slave)
            self.pipes, self.input, self.enter = None, self.master, '\r'
        self.text, self.url, self.state, self.phase = '', None, 'waiting', 'starting'
        self.code_value = None  # the pasted code is masked out of the observed output as it echoes
        self.cancel_requested, self.entered, self.code_submitted, self.closed = False, False, False, False
        self.exit_status, self.detail = None, DETAILS['starting']
        self.deadline = time.monotonic() + timeout
        self.identity_scheduled = False

    def _drain(self):
        while not self.closed:
            try:
                if not platform_os.readable(self.master):
                    return
                chunk = os.read(self.master, 4096)
            except OSError:
                return
            if not chunk:
                return
            text = self.text + ANSI.sub('', chunk.decode('utf-8', 'replace'))
            if self.code_value:
                text = text.replace(self.code_value, CODE_MASK)
            self.text = text[-OUTPUT_LIMIT:]
            self._observe()

    def _observe(self):
        if self.url is None:
            self.url = official_link(self.text)
        if SUCCESS_TEXT in self.text:
            self.phase = 'finishing'
        elif CODE_PROMPT in self.text and not self.code_submitted:
            self.phase = 'code_prompt'
        elif self.url:
            self.phase = 'browser'
        self.detail = DETAILS[self.phase]

    def _write(self, text):
        try:
            os.write(self.input, text.encode('utf-8'))
        except OSError as error:
            raise Rejected(f'Sign-in process is not accepting input: {error}') from None

    def poll(self):
        if self.closed:
            return
        if self.cancel_requested:
            if self._group_stopped():
                self.exit_status = self.process.poll()
                self.state, self.detail = 'cancelled', self.cancel_reason
                self._close()
            return
        self._drain()
        code = self.process.poll()
        if code is None and self.phase == 'finishing' and not self.entered:
            self.entered = True
            # The documented interactive login asks for Enter after "Login successful". `claude auth login` (CLI
            # 2.1.274, read from its bundle 20 Sep) prints the line and exits on its own, so by the time this press
            # arrives the CLI may have closed its side of the terminal and the write fails with EIO. That is the
            # CLI leaving, not a fault: its exit status is read here or on the next poll, and the status read that
            # asked keeps working. A CLI that does wait for Enter still gets it.
            try:
                self._write(self.enter)
            except Rejected:
                code = self.process.poll()
        if code is None:
            if time.monotonic() > self.deadline:
                self.cancel('Sign-in timed out. Start it again when you are ready.')
            return
        self._drain()
        self.exit_status = code
        if self.cancel_requested:
            self.state, self.detail = 'cancelled', self.detail if self.state == 'cancelled' else 'Sign-in cancelled.'
        elif code == 0:
            self.state = 'succeeded'
            self.detail = 'Sign-in finished. Checking that the signed-in identity matches this profile.'
        else:
            # Fixed wording only. Terminal output can carry codes or token-like text and is never surfaced.
            self.state = 'failed'
            self.detail = FAILED_DETAIL.format(code=code)
        self._close()

    def submit_code(self, code):
        if type(code) is not str or not code.strip() or len(code) > 512 or any(c in code for c in '\r\n\x00'):
            raise Rejected('Paste the single code shown in the browser')
        if self.closed or self.state != 'waiting':
            raise Rejected('No sign-in is waiting for a code')
        self._drain()
        if self.phase != 'code_prompt':
            raise Rejected('Claude Code has not asked for a code')
        self.code_submitted = True
        self.code_value = code.strip()
        self._write(self.code_value + self.enter)
        self.phase, self.detail = 'starting', 'Code sent to Claude Code. Waiting for confirmation.'

    def cancel(self, reason='Sign-in cancelled.'):
        if self.closed:
            return
        self.cancel_requested = True
        self.cancel_reason = reason
        self.state, self.detail = 'cancelled', reason
        for sig, grace in ((signal.SIGINT, 1), (signal.SIGTERM, 1), (platform_os.SIGKILL, 2)):
            try:
                platform_os.signal_group(self.process.pid, sig)
            except ProcessLookupError:
                break
            except PermissionError:
                pass
            # A login launcher can exit before its children. Wait for the owned group,
            # then escalate if a child ignored the signal even though the leader left.
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if self._group_stopped():
                    break
                time.sleep(.02)
            else:
                continue
            break
        self.exit_status = self.process.poll()
        if not self._group_stopped():
            self.state = 'failed'
            self.detail = "Sign-in cleanup is still pending. Try Cancel sign-in again."
            raise Rejected(self.detail)
        self._close()

    def _group_stopped(self):
        self.process.poll()
        try:
            platform_os.signal_group(self.process.pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # macOS can briefly deny a probe while a group disappears. Denial alone
            # doesn't prove that a child stopped, so keep cleanup pending.
            pass
        return False

    def _close(self):
        if self.closed:
            return
        self.closed = True
        for pipe in self.pipes or ():
            try:
                pipe.close()  # the file objects own the Windows descriptors: closing the fd under them would close it twice
            except OSError:
                pass
        if self.pipes is None:
            try:
                os.close(self.master)
            except OSError:
                pass
        # Only state, phase, detail and exit status remain after closure.
        self.text, self.url, self.code_value = '', None, None

    def status(self):
        return {'state': self.state, 'phase': self.phase, 'url': self.url, 'needsCode': self.phase == 'code_prompt',
                'detail': self.detail, 'exitStatus': self.exit_status, 'active': not self.closed}


def idle_status():
    return {'state': 'idle', 'phase': None, 'url': None, 'needsCode': False, 'exitStatus': None, 'active': False,
            'detail': 'Not signed in through Dervo yet. Connect to run the official Claude Code sign-in in this owned profile.'}
