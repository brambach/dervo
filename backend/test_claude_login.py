"""Native Connect Claude flow with a fake CLI on a real pseudo-terminal. No browser, account or network."""
import json
import os
from pathlib import Path
import pwd
import shlex
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import claude_login as m
import claude_policy
import platform_os
import transport
from dervo_connections import Rejected


class EnvironmentTests(unittest.TestCase):
    """Keychain regression: Claude gets the OS account home, resolved from the passwd database."""

    def test_claude_home_is_the_account_home_regardless_of_inherited_home(self):
        real = pwd.getpwuid(os.getuid()).pw_dir
        with tempfile.TemporaryDirectory(prefix='dervo-env-') as tmp:
            profile = str(Path(tmp) / 'owned profile')
            process = str(Path(tmp) / 'owned process')
            for inherited in ('/nonexistent disposable home', ''):
                with patch.dict(os.environ, {'HOME': inherited}):
                    env = transport.clean_environment('claude', profile, process)
                self.assertEqual(env['HOME'], real)
                self.assertEqual(env['CLAUDE_CONFIG_DIR'], profile)
                self.assertNotEqual(env['HOME'], profile)
                self.assertEqual(set(env), {'HOME', 'PATH', 'LANG', 'LC_ALL', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY'})
            with patch.dict(os.environ, clear=True):
                self.assertEqual(transport.clean_environment('claude', profile, process)['HOME'], real)
                self.assertEqual(transport.account_home(), real)

    def test_codex_environment_is_unchanged(self):
        with tempfile.TemporaryDirectory(prefix='dervo-env-') as tmp:
            profile = str(Path(tmp) / 'owned profile')
            process = str(Path(tmp) / 'owned process')
            env = transport.clean_environment('codex', profile, process)
        self.assertEqual(env['HOME'], process)
        self.assertEqual(env['CODEX_HOME'], profile)
        self.assertEqual(set(env), {'HOME', 'PATH', 'LANG', 'LC_ALL', 'CODEX_HOME'})

    def test_relative_or_unknown_inputs_are_rejected(self):
        for provider, profile, process in (('claude', 'relative', '/abs'), ('claude', '/abs', 'relative'), ('other', '/abs', '/abs')):
            with self.assertRaises(Rejected):
                transport.clean_environment(provider, profile, process)

    def test_account_home_failure_is_a_clear_rejection(self):
        class NoEntry:
            def getpwuid(self, uid):
                raise KeyError(uid)
        with patch.dict('sys.modules', {'pwd': NoEntry()}):
            with self.assertRaises(Rejected):
                transport.account_home()
            with self.assertRaises(Rejected):
                transport.account_name()

    def test_your_existing_sign_in_gets_USER_from_the_passwd_database(self):
        """Keychain regression (21 Sep): Claude Code names its Keychain item after USER, so without it the existing
        sign-in reads as signed out. The value is the passwd name, never the inherited environment, which the host
        doesn't have."""
        name = pwd.getpwuid(os.getuid()).pw_name
        with tempfile.TemporaryDirectory(prefix='dervo-env-') as tmp:
            system = str(Path(tmp) / '.claude')
            with patch.object(transport, 'account_home', lambda: tmp):
                for inherited in ({'USER': 'someone-else', 'LOGNAME': 'someone-else'}, {}):
                    with patch.dict(os.environ, inherited, clear=True):
                        env = transport.clean_environment('claude', system, system)
                    self.assertEqual(env['USER'], name)
                    self.assertNotIn('LOGNAME', env, 'LOGNAME alone does nothing for the Keychain lookup, so it is not sent')
                keyed = transport.clean_environment('claude', system, system, api_key='sk-ant-synthetic-not-a-key')
                self.assertEqual(keyed['USER'], name)

    def test_USER_widens_nothing_else(self):
        """The rest of the allowlist is exactly what it was, for every kind of Claude launch and for Codex."""
        with tempfile.TemporaryDirectory(prefix='dervo-env-') as tmp:
            system, owned = str(Path(tmp) / '.claude'), str(Path(tmp) / 'profiles' / 'p')
            with patch.object(transport, 'account_home', lambda: tmp):
                existing = transport.clean_environment('claude', system, system)
                dervo_owned = transport.clean_environment('claude', owned, owned)
                codex = transport.clean_environment('codex', owned, owned)
            fixed = {'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8'}
            if sys.platform.startswith('linux'):  # ~/.local/bin first and C.UTF-8 (platform_os.provider_environment)
                fixed = platform_os.provider_environment()
            self.assertEqual({k: v for k, v in existing.items() if k != 'USER'},
                             {**fixed, 'HOME': tmp, 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1'})
            # A Dervo-owned account signed in without USER, so its Keychain item is under the CLI's fallback name: adding
            # USER would sign every connected account out (measured on three, 21 Sep). It gets none.
            self.assertEqual(dervo_owned, {**fixed, 'HOME': tmp, 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1', 'CLAUDE_CONFIG_DIR': owned})
            self.assertEqual(codex, {**fixed, 'HOME': owned, 'CODEX_HOME': owned})

IDENTITY = 'personal@example.test'
VERIFIED = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': IDENTITY,
            'subscriptionType': 'max'}
# Labeled dummies only. Nothing here is or resembles a real credential in use anywhere.
DUMMY_CODE = 'DUMMY-AUTH-CODE-not-a-real-credential'
DUMMY_TOKEN = 'DUMMY-TOKEN-not-a-real-credential'

FAKE = r'''#!/bin/sh
C="__CONTROL__"
if [ "$2" = auth ] && [ "$3" = status ]; then cat "$C/status.json"; exit 0; fi
printf '%s\n' "$@" > "$C/argv.txt"
env > "$C/env.txt"
pwd > "$C/cwd.txt"
printf '\033[1mOpening browser to sign in...\033[0m\n'
if [ -f "$C/links.txt" ]; then cat "$C/links.txt"; else printf 'If your browser does not open, visit: https://claude.ai/oauth/authorize?client_id=synthetic&state=abc\n'; fi
if [ -f "$C/ask-code" ]; then printf 'Paste code here if prompted: '; read code; printf '%s' "$code" > "$C/code.txt"; printf 'You typed %s\n' "$code"; fi
while [ ! -f "$C/finish" ] && [ ! -f "$C/fail" ]; do sleep 0.02; done
if [ -f "$C/fail" ]; then printf 'Login failed: synthetic refusal token=%s\n' "$DUMMY_TOKEN"; exit 1; fi
if [ -f "$C/exit-on-success" ]; then printf 'Login successful.\n'; exec 0<&- 1>&- 2>&-; sleep 0.5; exit 0; fi
printf 'Login successful. Press Enter to continue\n'
read enter
printf 'done\n' > "$C/entered.txt"
exit 0
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dervo-claude-login-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()
        self.control = self.base / 'control dir'
        self.control.mkdir()
        (self.control / 'status.json').write_text(json.dumps(VERIFIED))
        tools = self.base / 'fake tools'
        tools.mkdir()
        self.claude = tools / 'claude'
        self.claude.write_text(FAKE.replace('__CONTROL__', str(self.control)).replace('$DUMMY_TOKEN', DUMMY_TOKEN))
        self.claude.chmod(0o755)
        self.home = self.base / 'profiles' / 'p'
        self.home.mkdir(parents=True)
        self.profile = {'id': 'p', 'provider': 'claude', 'identity': IDENTITY, 'category': 'personal', 'home': str(self.home)}

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.02)
        return False

    def wait_status(self, session, condition, timeout=5):
        def step():
            session.poll()
            return condition(session.status())
        self.assertTrue(self.wait_for(step, timeout), session.status())
        return session.status()


class LoginSessionTests(Fixture):
    def start(self):
        session = m.LoginSession(self.profile, str(self.claude))
        self.addCleanup(session.cancel)
        return session

    def test_launch_uses_documented_login_in_owned_profile_only(self):
        session = self.start()
        status = self.wait_status(session, lambda s: s['url'] is not None)
        self.assertEqual(status['state'], 'waiting')
        self.assertEqual(status['phase'], 'browser')
        self.assertEqual(status['url'], 'https://claude.ai/oauth/authorize?client_id=synthetic&state=abc')
        self.assertIn('browser', status['detail'].lower())
        self.assertTrue(status['active'])
        argv = (self.control / 'argv.txt').read_text().splitlines()
        self.assertEqual(argv, ['--safe-mode', 'auth', 'login', '--email', IDENTITY])
        env = dict(line.split('=', 1) for line in (self.control / 'env.txt').read_text().splitlines() if '=' in line)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(self.home))
        # macOS Keychain lookup needs the real account home; the owned profile stays distinct via CLAUDE_CONFIG_DIR.
        self.assertEqual(env['HOME'], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertNotEqual(env['HOME'], env['CLAUDE_CONFIG_DIR'])
        self.assertFalse([k for k in env if k.startswith('ANTHROPIC') or (k.startswith('CLAUDE_CODE') and k != 'CLAUDE_CODE_DISABLE_AUTO_MEMORY')], env)
        self.assertEqual(Path((self.control / 'cwd.txt').read_text().strip()).resolve(), self.home.resolve())
        self.assertNotIn('\x1b', session.text, 'escape sequences are stripped from observed output')

    def test_success_presses_enter_and_reports_succeeded(self):
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        status = self.wait_status(session, lambda s: s['state'] != 'waiting')
        self.assertEqual((status['state'], status['exitStatus'], status['active']), ('succeeded', 0, False))
        self.assertTrue((self.control / 'entered.txt').exists(), 'Enter was sent after Login successful')
        self.assertIn('identity', status['detail'].lower())

    def test_a_cli_that_exits_on_its_own_after_success_reports_succeeded_not_a_fault(self):
        # claude auth login (CLI 2.1.274, read from its bundle 20 Sep) prints "Login successful." and exits by
        # itself; nothing asks for Enter. Seen against the real host: the Enter press landed on a terminal the CLI
        # had already closed, os.write failed with EIO, and the status read that was polling failed with it.
        (self.control / 'exit-on-success').write_text('')
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        # wait_status polls through poll(); a Rejected raised there ends the test, as it ended the status read.
        status = self.wait_status(session, lambda s: s['state'] != 'waiting')
        self.assertEqual((status['state'], status['exitStatus'], status['active']), ('succeeded', 0, False))
        self.assertTrue(session.entered, 'the Enter was still attempted once, for a CLI that does ask')

    def test_failure_uses_fixed_wording_and_never_surfaces_terminal_output(self):
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        (self.control / 'fail').write_text('')
        status = self.wait_status(session, lambda s: s['state'] != 'waiting')
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['detail'], m.FAILED_DETAIL.format(code=1))
        self.assertNotIn('synthetic refusal', json.dumps(status))
        self.assertNotIn(DUMMY_TOKEN, json.dumps(status))
        self.assertEqual((session.text, session.url, status['url']), ('', None, None), 'buffers are cleared on closure')

    def test_code_prompt_is_observable_and_forwarded_once(self):
        (self.control / 'ask-code').write_text('')
        session = self.start()
        status = self.wait_status(session, lambda s: s['needsCode'])
        self.assertEqual(status['phase'], 'code_prompt')
        for bad in ('', 'with\nnewline', 'x' * 513, 7):
            with self.assertRaises(Rejected):
                session.submit_code(bad)
        session.submit_code(f'  {DUMMY_CODE}  ')
        self.assertFalse(session.status()['needsCode'])
        self.assertTrue(self.wait_for(lambda: (self.control / 'code.txt').exists()))
        self.assertEqual((self.control / 'code.txt').read_text(), DUMMY_CODE)
        with self.assertRaises(Rejected):
            session.submit_code('again')
        (self.control / 'finish').write_text('')
        self.assertEqual(self.wait_status(session, lambda s: s['state'] != 'waiting')['state'], 'succeeded')

    def test_pasted_code_never_appears_in_observed_output_status_or_failure(self):
        (self.control / 'ask-code').write_text('')
        session = self.start()
        self.wait_status(session, lambda s: s['needsCode'])
        session.submit_code(DUMMY_CODE)
        self.assertTrue(self.wait_for(lambda: (self.control / 'code.txt').exists()))
        self.wait_status(session, lambda s: 'You typed' in session.text)
        self.assertIn(m.CODE_MASK, session.text, 'the pty echo of the code is masked as it arrives')
        self.assertNotIn(DUMMY_CODE, session.text)
        self.assertNotIn(DUMMY_CODE, json.dumps(session.status()))
        (self.control / 'fail').write_text('')
        status = self.wait_status(session, lambda s: s['state'] != 'waiting')
        self.assertEqual(status['state'], 'failed')
        self.assertNotIn(DUMMY_CODE, json.dumps(status))
        self.assertNotIn(DUMMY_TOKEN, json.dumps(status))
        self.assertEqual(session.code_value, None)
        self.assertEqual(session.text, '')

    def test_only_exact_official_https_links_are_surfaced(self):
        (self.control / 'links.txt').write_text(
            'Visit https://claude.ai.evil.example/oauth/authorize?state=1 first,\n'
            'or http://claude.ai/oauth/authorize?state=2 (not https),\n'
            'or https://user@claude.ai/oauth/authorize?state=3 (userinfo),\n'
            'then https://claude.ai/oauth/authorize?client_id=synthetic&state=ok.\n')
        session = self.start()
        status = self.wait_status(session, lambda s: 'state=ok' in session.text)
        self.assertEqual(status['url'], 'https://claude.ai/oauth/authorize?client_id=synthetic&state=ok')
        self.assertEqual(m.official_link('see https://evil.example/oauth/login now'), None)
        self.assertEqual(m.official_link('https://platform.claude.com/login'), None, 'console login is not requested')
        self.assertEqual(m.official_link('https://claude.ai/login.'), 'https://claude.ai/login')

    def test_claude_code_2_1_280_sign_in_link_on_claude_com_is_surfaced(self):
        """Claude Code 2.1.280 and later prints its link on claude.com (Windows runner, 25 Sep). Connect Claude showed
        nothing, on the Mac too. Only the exact sign-in path counts on claude.com."""
        (self.control / 'links.txt').write_text(
            "If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&client_id=synthetic&state=ok\n")
        session = self.start()
        status = self.wait_status(session, lambda s: s.get('url'))
        self.assertEqual(status['url'], 'https://claude.com/cai/oauth/authorize?code=true&client_id=synthetic&state=ok')
        self.assertEqual(status['phase'], 'browser')
        for other in ('https://claude.com/pricing', 'https://claude.com/cai/oauth/authorize/extra?state=1',
                      'https://claude.com.evil.example/cai/oauth/authorize?state=1', 'http://claude.com/cai/oauth/authorize?state=1',
                      'https://user@claude.com/cai/oauth/authorize?state=1', 'https://claude.com:8443/cai/oauth/authorize?state=1',
                      'https://platform.claude.com/oauth/authorize?state=1'):
            self.assertEqual(m.official_link(f'visit: {other}'), None, other)

    def test_unofficial_links_leave_no_link_and_no_browser_phase(self):
        (self.control / 'links.txt').write_text('Visit https://evil.example/oauth/authorize?state=1\n')
        session = self.start()
        status = self.wait_status(session, lambda s: 'evil.example' in session.text)
        self.assertEqual((status['url'], status['phase']), (None, 'starting'))
        self.assertNotIn('evil.example', json.dumps(status))

    def test_code_is_rejected_when_not_asked(self):
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        with self.assertRaises(Rejected):
            session.submit_code('unsolicited')

    def test_cancel_stops_the_owned_process_group(self):
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        pid = session.process.pid
        session.cancel()
        status = session.status()
        self.assertEqual((status['state'], status['active']), ('cancelled', False))
        self.assertIsNotNone(session.process.poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)
        session.poll()
        self.assertEqual(session.status()['state'], 'cancelled', 'a later poll never rewrites a cancelled outcome')

    def start_login_with_child(self):
        child_file = self.control / 'child.pid'
        script = '''import os, signal, time
from pathlib import Path
child_file = Path(%r)
if os.fork() == 0:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    child_file.write_text(str(os.getpid()))
    while True: time.sleep(.02)
signal.signal(signal.SIGINT, lambda *_: os._exit(0))
while not child_file.exists(): time.sleep(.02)
print('https://claude.ai/oauth/authorize?state=synthetic', flush=True)
while True: time.sleep(.02)
''' % str(child_file)
        self.claude.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(script) + ' "$@"\n')
        session = self.start()
        def cleanup():
            try: os.killpg(session.process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        self.addCleanup(cleanup)
        self.wait_status(session, lambda s: s['url'] is not None)
        return session

    def test_cancel_stops_a_child_after_the_login_leader_exits(self):
        session = self.start_login_with_child()
        session.cancel()
        self.assertIsNotNone(session.process.poll())
        with self.assertRaises(ProcessLookupError):
            os.killpg(session.process.pid, 0)
        self.assertEqual(session.status()['state'], 'cancelled')

    def test_cancel_escalates_after_a_signal_is_denied(self):
        session = self.start_login_with_child()
        killpg = os.killpg
        denied = False
        def denied_term(pid, sig):
            nonlocal denied
            if sig == signal.SIGTERM:
                denied = True
                raise PermissionError('Synthetic signal denial')
            return killpg(pid, sig)
        with patch.object(m.os, 'killpg', denied_term):
            session.cancel()
        self.assertTrue(denied)
        with self.assertRaises(ProcessLookupError):
            killpg(session.process.pid, 0)
        self.assertFalse(session.status()['active'])

    def test_denied_cleanup_stays_active_until_the_child_stops(self):
        session = self.start_login_with_child()
        killpg = os.killpg
        def denied_signals(pid, sig):
            if sig in (signal.SIGTERM, signal.SIGKILL):
                raise PermissionError('Synthetic signal denial')
            return killpg(pid, sig)
        with patch.object(m.os, 'killpg', denied_signals):
            with self.assertRaisesRegex(Rejected, 'cleanup is still pending'):
                session.cancel()
            session.poll()
            self.assertTrue(session.status()['active'])
            self.assertEqual(session.status()['state'], 'failed')
            killpg(session.process.pid, 0)
        session.cancel()
        with self.assertRaises(ProcessLookupError):
            killpg(session.process.pid, 0)
        self.assertEqual((session.status()['active'], session.status()['state']), (False, 'cancelled'))

    def test_cancel_waits_through_a_disappearing_group_permission_error(self):
        session = self.start()
        self.wait_status(session, lambda s: s['url'] is not None)
        killpg = os.killpg
        denied = False
        def disappearing_group(pid, sig):
            nonlocal denied
            if sig == 0 and not denied:
                denied = True
                raise PermissionError('Synthetic disappearing process group')
            return killpg(pid, sig)
        with patch.object(m.os, 'killpg', disappearing_group):
            session.cancel()
        self.assertTrue(denied)
        self.assertIsNotNone(session.process.poll())
        with self.assertRaises(ProcessLookupError):
            killpg(session.process.pid, 0)
        self.assertEqual(session.status()['state'], 'cancelled')

    def test_timeout_cancels(self):
        session = m.LoginSession(self.profile, str(self.claude), timeout=0.2)
        self.addCleanup(session.cancel)
        status = self.wait_status(session, lambda s: s['state'] != 'waiting')
        self.assertEqual(status['state'], 'cancelled')
        self.assertIn('timed out', status['detail'])

    def test_wrong_provider_and_bad_identity_never_launch(self):
        with self.assertRaises(Rejected):
            m.LoginSession({**self.profile, 'provider': 'codex'}, str(self.claude))
        with self.assertRaises(Rejected):
            m.LoginSession({**self.profile, 'identity': ''}, str(self.claude))
        with self.assertRaises(Rejected):
            claude_policy.login_argv('relative/claude', IDENTITY)
        self.assertFalse((self.control / 'argv.txt').exists())


class HostFlowTests(Fixture):
    def setUp(self):
        super().setUp()
        from host import Host
        self.host = Host(self.base)
        self.host_closed = False
        self.addCleanup(self.close_host)
        self.host.store.add_profile('p', 'claude', self.home, IDENTITY, 'personal')
        self.host.store.add_profile('x', 'codex', self.base / 'profiles' / 'x', 'codex@example.test', 'personal')
        self.enterContext(patch.object(claude_policy, 'executable', lambda: str(self.claude)))

    def close_host(self):
        if not self.host_closed:
            self.host_closed = True
            self.host.close()

    def login(self, operation, **extra):
        return self.host.dispatch(operation, {'profileId': 'p', **extra})

    def until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self.login('connection_login_status')
            if condition(status):
                return status
            time.sleep(.02)
        self.fail(status)

    def test_nothing_starts_before_an_explicit_connect(self):
        self.assertEqual(self.login('connection_login_status')['state'], 'idle')
        self.assertFalse((self.control / 'argv.txt').exists())
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'unchecked')

    def test_connect_waits_then_verifies_identity_through_the_ordinary_probe(self):
        started = self.login('connection_login')
        self.assertEqual(started['state'], 'waiting')
        again = self.login('connection_login')
        self.assertTrue(again['active'], 'a second Connect click reports the running sign-in instead of starting another')
        self.assertEqual(len(self.host.logins), 1)
        self.until(lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        status = self.until(lambda s: s['state'] == 'succeeded' and s.get('identity', {}).get('state') not in (None, 'checking'))
        self.assertEqual(status['identity']['state'], 'identity_verified')
        self.assertFalse(status['identity']['canSend'])
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'identity_verified')

    def test_mismatched_identity_after_login_is_reported_not_accepted(self):
        (self.control / 'status.json').write_text(json.dumps({**VERIFIED, 'email': 'work@example.test'}))
        self.login('connection_login')
        self.until(lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        status = self.until(lambda s: s.get('identity', {}).get('state') not in (None, 'checking'))
        self.assertEqual(status['identity']['state'], 'identity_mismatch')

    def test_cancel_and_reconnect(self):
        self.login('connection_login')
        self.until(lambda s: s['url'] is not None)
        cancelled = self.login('connection_login_cancel')
        self.assertEqual((cancelled['state'], cancelled['active']), ('cancelled', False))
        self.assertEqual(self.login('connection_login_status')['state'], 'cancelled')
        (self.control / 'argv.txt').unlink()
        restarted = self.login('connection_login')
        self.assertEqual(restarted['state'], 'waiting')
        self.assertTrue(self.wait_for(lambda: (self.control / 'argv.txt').exists()))

    def test_code_operation_routes_to_the_session(self):
        (self.control / 'ask-code').write_text('')
        self.login('connection_login')
        self.until(lambda s: s['needsCode'])
        with self.assertRaises(Rejected):
            self.login('connection_login_code', code='')
        self.login('connection_login_code', code=DUMMY_CODE)
        self.assertTrue(self.wait_for(lambda: (self.control / 'code.txt').exists()))
        self.until(lambda s: 'You typed' in self.host.logins['p'].text)
        self.assertNotIn(DUMMY_CODE, json.dumps(self.login('connection_login_status')))
        (self.control / 'fail').write_text('')
        status = self.until(lambda s: s['state'] != 'waiting')
        self.assertNotIn(DUMMY_CODE, json.dumps(status))
        self.assertNotIn(DUMMY_TOKEN, json.dumps(status))

    def test_connection_status_follows_the_sign_in_and_never_bleeds_between_profiles(self):
        """Regression for the stale account label: the host's own status must agree with the finished sign-in."""
        self.login('connection_login')
        self.until(lambda s: s['url'] is not None)
        (self.control / 'finish').write_text('')
        self.until(lambda s: s['state'] == 'succeeded' and s.get('identity', {}).get('state') not in (None, 'checking'))
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'identity_verified')
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'x'})['state'], 'unchecked',
                         'another profile never inherits this identity, and nothing was launched for it')
        # A new sign-in can change the signed-in account, so the earlier observation is dropped until its probe reports.
        (self.control / 'finish').unlink()
        self.assertEqual(self.login('connection_login')['state'], 'waiting')
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'unchecked')
        self.until(lambda s: s['url'] is not None)
        cancelled = self.login('connection_login_cancel')
        self.assertEqual((cancelled['state'], cancelled['active']), ('cancelled', False))
        self.assertNotIn('identity', cancelled, 'a cancelled sign-in carries no identity result')
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'p'})['state'], 'unchecked', 'cancel is not ready')
        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'x'})['state'], 'unchecked')
        # Cancelling an already finished sign-in changes nothing observed about it.
        self.assertEqual(self.login('connection_login_cancel')['state'], 'cancelled')

    def test_active_login_refuses_identity_probe(self):
        self.login('connection_login')
        with patch('connections_ui.probe') as probe:
            with self.assertRaisesRegex(Rejected, 'sign-in'):
                self.login('connection_check')
            probe.assert_not_called()

    def test_terminal_login_drops_completed_and_late_identity_observations(self):
        from concurrent.futures import Future
        verified = {'state': 'identity_verified', 'canSend': False, 'detail': 'Synthetic identity matches.'}
        for terminal in ('timeout', 'failure', 'cancel', 'success'):
            for delayed in (False, True):
                with self.subTest(terminal=terminal, delayed=delayed):
                    for marker in ('finish', 'fail'):
                        (self.control / marker).unlink(missing_ok=True)
                    earlier, during, fresh = Future(), Future(), Future()
                    if not delayed:
                        earlier.set_result(verified)
                        during.set_result(verified)
                    fresh.set_result(verified)
                    with patch.object(self.host.checker, 'submit', side_effect=[earlier, during, fresh]) as submit:
                        self.login('connection_check')
                        self.login('connection_login')
                        self.until(lambda s: s['url'] is not None)
                        # Exercise the public route that admitted stale probes before the correction.
                        # A corrected host refuses it; the pre-login future still tests late completion.
                        try:
                            self.login('connection_check')
                        except Rejected:
                            pass
                        if terminal == 'timeout':
                            self.host.logins['p'].deadline = 0
                        elif terminal == 'failure':
                            (self.control / 'fail').write_text('')
                        elif terminal == 'success':
                            (self.control / 'finish').write_text('')
                        else:
                            self.login('connection_login_cancel')
                        result = self.until(lambda s: not s['active'])
                        self.assertEqual(result['state'], {'timeout': 'cancelled', 'failure': 'failed',
                                                          'cancel': 'cancelled', 'success': 'succeeded'}[terminal])
                        if delayed:
                            earlier.set_result(verified)
                            during.set_result(verified)
                        if terminal == 'success':
                            self.assertEqual(self.login('connection_login_status')['identity']['state'], 'identity_verified')
                        else:
                            self.assertEqual(self.login('connection_status')['state'], 'unchecked')
                            self.assertEqual(self.login('connection_login_status')['state'], result['state'])
                            self.assertEqual(self.login('connection_status')['state'], 'unchecked')
                            # Retrying after the terminal transition remains allowed and survives polling.
                            self.login('connection_check')
                            self.login('connection_login_status')
                            self.assertEqual(self.login('connection_status')['state'], 'identity_verified')
                        self.assertEqual(self.host.dispatch('connection_status', {'profileId': 'x'})['state'], 'unchecked')
                        self.assertEqual(self.host.turns, {}, 'identity never starts a conversation')

    def test_terminal_transition_invalidates_existing_cache_independently_of_probe_refusal(self):
        from concurrent.futures import Future
        verified = {'state': 'identity_verified', 'canSend': False, 'detail': 'Synthetic old observation.'}
        for terminal in ('timeout', 'failure', 'cancel'):
            for delayed in (False, True):
                with self.subTest(terminal=terminal, delayed=delayed):
                    (self.control / 'fail').unlink(missing_ok=True)
                    self.login('connection_login')
                    self.until(lambda s: s['url'] is not None)
                    old = Future()
                    if not delayed:
                        old.set_result(verified)
                    # Seed the legacy cache state directly so refusal alone can't make this test pass.
                    # All lifecycle actions and observations still go through public Host dispatch.
                    self.host.checks['p'] = old
                    if terminal == 'timeout':
                        self.host.logins['p'].deadline = 0
                        # Identity status itself must observe timeout, even before login-status polling.
                        self.assertEqual(self.login('connection_status')['state'], 'unchecked')
                    elif terminal == 'failure':
                        (self.control / 'fail').write_text('')
                    else:
                        self.login('connection_login_cancel')
                    self.until(lambda s: not s['active'])
                    if delayed:
                        old.set_result(verified)
                    self.assertEqual(self.login('connection_status')['state'], 'unchecked')
                    self.assertEqual(self.login('connection_status')['canSend'], False)

    def test_code_rejections_never_echo_the_pasted_code(self):
        self.login('connection_login')
        self.until(lambda s: s['url'] is not None)
        for bad in (DUMMY_CODE, DUMMY_CODE + '\n', 'x' * 600):
            with self.assertRaises(Rejected) as caught:
                self.login('connection_login_code', code=bad)
            self.assertNotIn(DUMMY_CODE, str(caught.exception))
            self.assertNotIn('xxxx', str(caught.exception))
        self.assertNotIn(DUMMY_CODE, json.dumps(self.login('connection_login_status')))

    def test_codex_missing_cli_is_rejected(self):
        with patch('codex_policy.executable', side_effect=Rejected('Synthetic missing CLI')) as executable:
            with self.assertRaisesRegex(Rejected, 'Synthetic missing CLI'):
                self.host.dispatch('connection_login', {'profileId': 'x'})
            executable.assert_called_once()

    def test_unknown_login_fields_are_rejected(self):
        with self.assertRaises(Rejected):
            self.host.dispatch('connection_login', {'profileId': 'p', 'extra': 1})
        with self.assertRaises(Rejected):
            self.host.dispatch('connection_login_code', {'profileId': 'p'})

    def test_host_close_cancels_the_owned_login(self):
        self.login('connection_login')
        self.until(lambda s: s['url'] is not None)
        session = self.host.logins['p']
        pid = session.process.pid
        self.close_host()
        self.assertEqual(session.state, 'cancelled')
        self.assertTrue(session.closed)
        with self.assertRaises(ProcessLookupError):
            os.killpg(pid, 0)


if __name__ == '__main__':
    unittest.main()
