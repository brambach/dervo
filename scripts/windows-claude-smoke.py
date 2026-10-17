"""Find Claude Code the way Dervo does and ask it what Dervo asks before a run: its version and its sign-in.

    python windows-claude-smoke.py

Uses the backend's own claude_policy.executable(), first_run's probe environment and claude_policy.auth_status() on
your existing sign-in, with no account and no model call. On the Windows build runner (b16/windows) nobody is signed
in, so the expected answer is a version and loggedIn false. Then it starts Connect Claude's sign-in without a terminal
and cancels it (login() below). Exits 0 when Claude Code was found, ran, auth status answered as JSON, and the sign-in
reached its claude.ai link and cancelled cleanly.
"""
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import claude_policy  # noqa: E402
import first_run  # noqa: E402
from dervo_connections import Rejected  # noqa: E402


def main():
    try:
        path = claude_policy.executable()
    except Rejected as exc:
        print(f'Not found: {exc}')
        return 1
    print(f'Claude Code: {path}')
    version = subprocess.run([path, '--version'], env=first_run._claude_env(), capture_output=True, text=True, timeout=60)
    print(f'--version exit {version.returncode}: {version.stdout.strip()} {version.stderr.strip()[:300]}')
    home = str(claude_policy.system_config_dir())
    meta = claude_policy.auth_status(path, home, timeout=60)
    # Only fields that say nothing about who's signed in: never the email, organisation or account ids.
    shown = {key: meta.get(key) for key in ('loggedIn', 'authMethod', 'apiProvider', 'subscriptionType')}
    print(f'auth status: {shown!r}')
    if version.returncode != 0 or not meta:
        print('Claude Code ran, but Dervo could not read its version and sign-in.')
        return 1
    print('Dervo found Claude Code and read its sign-in state.')
    return login(path)


def login(path):
    """Start Connect Claude's sign-in in a throwaway profile, see how far it gets without a terminal, then cancel it.

    Nobody signs in: the email is a reserved .invalid address and the session is cancelled after at most 45 seconds.
    Prints the phase, whether a claude.ai link appeared (host and path only) and the CLI's last output with links cut
    to their host, so a raw-mode or TTY error shows. Exits 0 when the link appeared and cancel ended the process."""
    import re
    import tempfile
    import time
    from urllib.parse import urlsplit
    import claude_login
    with tempfile.TemporaryDirectory(prefix='dervo-login-smoke-', ignore_cleanup_errors=True) as home:
        profile = {'id': 'smoke', 'provider': 'claude', 'identity': 'windows-smoke@example.invalid', 'home': home}
        session = claude_login.LoginSession(profile, str(path))
        # The session clears its output and link when the CLI exits. Keep them for this report.
        kept, close = {}, session._close
        session._close = lambda: (kept.update(text=session.text, url=session.url), close())
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and session.status()['state'] == 'waiting' and not session.url:
            session.poll()
            time.sleep(0.2)
        time.sleep(1)  # anything the CLI prints right after its link
        session.poll()
        text, url = kept.get('text', session.text), kept.get('url', session.url)
        status, tail = session.status(), re.sub(r'https://([^/\s]+)\S*', r'https://\1/...', text or '')[-800:]
        link = urlsplit(url) if url else None
        # Every https link the CLI printed, host and path only (the query carries the sign-in's state), so a link on a
        # host Dervo doesn't show (claude_login.OFFICIAL_HOSTS) is still named here.
        printed = sorted({f'{p.hostname}{p.path}' for p in map(urlsplit, claude_login.URL.findall(text or ''))})
        print(f'login: state {status["state"]}, phase {status["phase"]}, exit {status["exitStatus"]}, '
              f'link {link.hostname + link.path if link else None}, links printed {printed}')
        print('login output (last 800 characters):\n' + tail)
        if session.status()['active']:
            session.cancel('Smoke check done.')
        stopped = session._group_stopped()
        print(f'login cancelled: {session.status()["state"]}, process stopped {stopped}')
    if link and stopped:
        print('Connect Claude reached the sign-in link on Windows and cancelled cleanly.')
        return 0
    print('Connect Claude did not reach a sign-in link on Windows, or cancel left it running.')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
