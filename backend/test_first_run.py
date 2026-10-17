"""First run: detection, your existing Claude Code sign-in as an account, folder facts and finish. Fake home and fake CLI
output only; the real ~/.claude and the real CLI are never touched."""
import json
import os
import pwd
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dervo_connections import Rejected
from host import Host
import claude_policy
import connections_ui
import first_run
import transport


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'; (self.home / '.claude').mkdir(parents=True)
        self.enterContext(patch.object(transport, 'account_home', lambda: str(self.home)))
        self.cli = self.base / 'claude'; self.cli.write_text('#!/bin/sh\n'); self.cli.chmod(0o755)
        self.enterContext(patch.object(claude_policy, 'executable', lambda candidates=None: str(self.cli)))
        self.meta = dict(loggedIn=True, authMethod='claude.ai', apiProvider='firstParty', email='bryce@example.test', subscriptionType='team')
        self.calls = []
        self.host = Host(self.base / 'store'); self.addCleanup(self.host.close)

    def fake_run(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs.get('env') or {})))
        if argv[0] == str(self.cli) and argv[1:] == ['--version']:
            return SimpleNamespace(returncode=0, stdout='2.1.274 (Claude Code)\n', stderr='')
        if 'auth' in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.meta), stderr='')
        if argv[0] == '/usr/bin/xcode-select':
            return SimpleNamespace(returncode=0, stdout='/Library/Developer/CommandLineTools\n', stderr='')
        if argv[:2] == ['/usr/bin/git', '--version']:
            return SimpleNamespace(returncode=0, stdout='git version 2.39.5 (Apple Git-154)\n', stderr='')
        return subprocess.run(argv, **kwargs)

    def test_your_existing_claude_sign_in_is_read_without_a_config_dir_or_safe_mode(self):
        found = first_run._claude(self.host, run=self.fake_run)
        self.assertEqual((found['state'], found['version'], found['email'], found['plan']), ('ready', '2.1.274', 'bryce@example.test', 'team'))
        argv, env = next(call for call in self.calls if 'auth' in call[0])
        self.assertNotIn('--safe-mode', argv, 'safe mode hides the default login (checked live, CLI 2.1.274)')
        self.assertNotIn('CLAUDE_CONFIG_DIR', env, 'naming ~/.claude explicitly also hides it')
        self.assertEqual(env['HOME'], str(self.home))
        self.assertEqual(env['USER'], pwd.getpwuid(os.getuid()).pw_name, 'the Keychain item is named after USER (21 Sep)')
        # A Dervo-owned account still gets its own folder and safe mode.
        owned = self.base / 'store/profiles/x'; owned.mkdir(parents=True)
        claude_policy.auth_status(str(self.cli), str(owned), run=self.fake_run)
        argv, env = self.calls[-1]
        self.assertIn('--safe-mode', argv)
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(owned))

    def test_team_is_accepted_and_other_sign_ins_say_why_not(self):
        self.assertIn('team', claude_policy.PLANS)
        for patch_, words in ((dict(loggedIn=False), 'couldn’t find its sign-in'), (dict(apiProvider='bedrock'), 'cloud provider'),
                              (dict(subscriptionType='enterprise'), 'enterprise plan')):
            self.meta = {**dict(loggedIn=True, authMethod='claude.ai', apiProvider='firstParty', email='b@example.test', subscriptionType='max'), **patch_}
            self.assertIn(words, first_run._claude(self.host, run=self.fake_run)['detail'])
        with patch.object(claude_policy, 'executable', side_effect=Rejected('missing')):
            missing = first_run._claude(self.host, run=self.fake_run)
        self.assertEqual((missing['state'], missing['install']), ('missing', 'curl -fsSL https://claude.ai/install.sh | bash'))

    def test_claude_code_signed_in_with_its_own_api_key_is_supported(self):
        # Anthropic's terms for apps that run Claude Code: no built-in sign-in method may be restricted, including the
        # user's own API key. First run used to call this "signed in another way" and refuse it.
        self.meta = dict(loggedIn=True, authMethod='api_key', apiProvider='firstParty', apiKeySource='/login managed key')
        found = first_run._claude(self.host, run=self.fake_run)
        self.assertEqual((found['state'], found['method'], found['plan']), ('ready', 'api_key', None))
        self.assertIn('per use', found['detail'])
        with patch.object(first_run, '_claude', lambda host, run=None, **_: found):
            account = first_run.use_claude(self.host)
        self.assertEqual(account['identity'], first_run.KEY_IDENTITY, 'no email is reported, so the account is named for what it is')
        profile = connections_ui.owned_profile(self.host.store, self.host.root, account['id'])
        self.assertEqual(connections_ui.classify(profile, self.meta)['state'], 'identity_verified')
        import adapters
        adapter = adapters.ClaudeAdapter({**profile, 'provider': 'claude', 'identity': account['identity']}, lambda *a: None)
        adapter.authenticate(self.meta)
        self.assertEqual(adapter.state, 'ready', 'a run with that sign-in starts too')
        # Negative: only your existing Claude Code's own folder. A Dervo-owned subscription account reporting a key isn't it.
        owned = self.host.root / 'profiles' / 'sub'; owned.mkdir(parents=True)
        self.host.store.add_profile('sub', 'claude', owned, 'b@example.test', 'personal')
        mine = dict(self.host.store.db.execute("SELECT * FROM profiles WHERE id='sub'").fetchone())
        self.assertEqual(connections_ui.classify(mine, self.meta)['state'], 'identity_mismatch')
        with self.assertRaises(Rejected):
            adapters.ClaudeAdapter({**mine, 'provider': 'claude'}, lambda *a: None).authenticate(self.meta)

    def test_an_old_claude_code_asks_for_an_update_not_an_install(self):
        def old(argv, **kwargs):
            if argv[0] == str(self.cli) and argv[1:] == ['--version']:
                return SimpleNamespace(returncode=0, stdout='2.1.260 (Claude Code)\n', stderr='')
            return self.fake_run(argv, **kwargs)
        found = first_run._claude(self.host, run=old)
        self.assertEqual((found['state'], found['version'], found['update']), ('outdated', '2.1.260', 'claude update'))
        self.assertNotIn('install', found, 'it is installed; the install command would be the wrong fix')
        self.assertIn('2.1.271', found['detail'])

    def test_using_the_existing_sign_in_makes_one_account_that_passes_the_ownership_check(self):
        with patch.object(first_run, '_claude', lambda host, run=None, **_: dict(state='ready', email='bryce@example.test', detail='')):
            first = first_run.use_claude(self.host)
            again = first_run.use_claude(self.host)
        self.assertEqual(first['id'], again['id'], 'a second click reuses the same account')
        profile = connections_ui.owned_profile(self.host.store, self.host.root, first['id'])
        self.assertEqual(Path(profile['home']), (self.home / '.claude').resolve())
        self.assertEqual(connections_ui.classify(profile, self.meta)['state'], 'identity_verified')
        env = transport.clean_environment('claude', profile['home'], profile['home'])
        self.assertNotIn('CLAUDE_CONFIG_DIR', env)
        # Negative: only Claude Code's own default folder is exempt from the ownership rule; any other outside folder isn't.
        elsewhere = self.home / 'elsewhere'; elsewhere.mkdir()
        self.host.store.add_profile('outside', 'claude', elsewhere, 'x@example.test', 'personal')
        with self.assertRaisesRegex(Rejected, 'no longer matches'):
            connections_ui.owned_profile(self.host.store, self.host.root, 'outside')

    def test_status_checks_everything_without_asking(self):
        report = first_run.status(self.host, run=self.fake_run)
        self.assertEqual(report['account']['state'], 'ready', 'a source checkout needs no beta sign-in')
        self.assertEqual(report['claude']['state'], 'ready')
        self.assertEqual((report['git']['state'], report['git']['version']), ('ready', '2.39.5'))
        self.assertEqual(report['projects'], 0)
        pending = SimpleNamespace(host=SimpleNamespace(account_session=SimpleNamespace(status=lambda: dict(state='signed_in', identity={}, membership=dict(allowed=False)))))
        self.assertEqual(first_run._account(pending.host)['state'], 'pending')
        # No command line tools: git is reported missing without ever calling /usr/bin/git (that opens Apple's installer).
        def no_tools(argv, **kwargs):
            self.calls.append((list(argv), {}))
            return SimpleNamespace(returncode=2, stdout='', stderr='') if argv[0] == '/usr/bin/xcode-select' else self.fake_run(argv, **kwargs)
        self.calls.clear()
        self.assertEqual(first_run._git(run=no_tools)['state'], 'missing')
        self.assertFalse(any(call[0][0] == '/usr/bin/git' for call in self.calls))

    def test_a_slow_claude_code_says_checking_within_the_bridge_limit_and_the_next_ask_gets_the_answer(self):
        """Systems report, 23 Sep, problem 7: the probes took up to 26 s in one request against the bridge's 10 s."""
        import threading
        import time as clock
        release, auth_calls = threading.Event(), []
        def slow(argv, **kwargs):
            if 'auth' in argv:
                auth_calls.append(1)
                release.wait(10)
            return self.fake_run(argv, **kwargs)
        started = clock.monotonic()
        report = first_run.status(self.host, run=slow, wait=.3)
        self.assertLess(clock.monotonic() - started, 2, 'the request never waits on the slow probe past its budget')
        self.assertEqual((report['claude']['state'], report['claude']['detail']), ('checking', first_run.CHECKING_CLAUDE))
        self.assertEqual(report['git']['state'], 'ready', 'git ran beside it, not after it')
        again = first_run.status(self.host, run=slow, wait=.3)
        self.assertEqual(again['claude']['state'], 'checking', 'still going: the same probe, not a second one')
        release.set()
        done = first_run.status(self.host, run=slow, wait=5)
        self.assertEqual(done['claude']['state'], 'ready')
        self.assertEqual(len(auth_calls), 1, 'one auth status for three asks')

    def test_a_hung_sign_in_check_reads_as_unknown_not_signed_out_or_a_broken_request(self):
        """AGENTS.md: show unknown when observation is lost (product review, 23 Sep, 6)."""
        def hung(argv, **kwargs):
            if 'auth' in argv:
                raise subprocess.TimeoutExpired(argv, kwargs.get('timeout'))
            return self.fake_run(argv, **kwargs)
        found = first_run.status(self.host, run=hung)['claude']
        self.assertEqual((found['state'], found['detail']), ('unknown', first_run.UNKNOWN_CLAUDE))
        self.assertNotIn('login', found, 'no Terminal sign-in command for a check that never answered')

    def test_a_poll_that_finds_the_probe_still_running_only_glances(self):
        """Backend review, 23 Sep, 4: the screen asks each second, and the host serves one request at a time."""
        import threading
        import time as clock
        release = threading.Event()
        def slow(argv, **kwargs):
            if 'auth' in argv:
                release.wait(10)
            return self.fake_run(argv, **kwargs)
        self.addCleanup(release.set)
        first_run.status(self.host, run=slow, wait=.2)
        started = clock.monotonic()
        polled = first_run.status(self.host, run=slow)
        self.assertLess(clock.monotonic() - started, first_run.JOIN_WAIT + 1, 'a poll never waits the full budget on a probe it joined')
        self.assertEqual(polled['claude']['state'], 'checking')
        release.set()
        clicked = first_run._claude(self.host, run=slow, join_wait=first_run.PROBE_WAIT)
        self.assertEqual(clicked['state'], 'ready', 'a click waits for the answer')

    def test_check_again_never_gets_the_answer_from_before_you_signed_in(self):
        self.meta = dict(loggedIn=False)
        self.assertEqual(first_run._claude(self.host, run=self.fake_run)['state'], 'signed_out')
        self.meta = dict(loggedIn=True, authMethod='claude.ai', apiProvider='firstParty', email='bryce@example.test', subscriptionType='max')
        self.assertEqual(first_run._claude(self.host, run=self.fake_run)['state'], 'ready', 'an answer is read once')

    def test_a_chosen_folder_says_what_it_found_from_git_and_marker_files(self):
        folder = self.base / 'shop'; folder.mkdir()
        (folder / 'package.json').write_text('{}'); (folder / 'tsconfig.json').write_text('{}'); (folder / 'src-tauri').mkdir()
        git(folder, 'init', '-q', '-b', 'main'); git(folder, 'config', 'user.email', 'f@example.test'); git(folder, 'config', 'user.name', 'F')
        git(folder, 'add', '-A'); git(folder, 'commit', '-qm', 'start')
        (folder / 'notes.md').write_text('draft')
        from project_setup import stage
        picked = stage(self.host, folder)
        facts = first_run.inspect(self.host, {'selectionId': picked['selectionId']})
        self.assertEqual((facts['git'], facts['repo'], facts['branch'], facts['uncommitted']), (True, 'shop', 'main', 1))
        self.assertEqual(facts['stack'], ['Tauri', 'TypeScript'])
        plain = self.base / 'plain'; plain.mkdir()
        facts = first_run.inspect(self.host, {'selectionId': stage(self.host, plain)['selectionId']})
        self.assertEqual((facts['git'], facts['stack']), (False, []))
        with self.assertRaisesRegex(Rejected, 'again'):
            first_run.inspect(self.host, {'selectionId': picked['selectionId']})

    def test_finish_creates_the_project_allows_editing_with_approval_and_starts_the_overview(self):
        folder = self.base / 'app'; folder.mkdir()
        from project_setup import stage
        picked = stage(self.host, folder)
        with patch.object(first_run, '_claude', lambda host, run=None, **_: dict(state='ready', email='bryce@example.test', detail='')):
            account = first_run.use_claude(self.host)
        started = []
        real = self.host.dispatch
        def dispatch(operation, args):
            if operation == 'project_explore':
                started.append(args)
                return {'id': 'req', 'state': 'working', 'channelId': 'overview'}
            return real(operation, args)
        with patch.object(self.host, 'dispatch', side_effect=dispatch):
            done = first_run.finish(self.host, {'selectionId': picked['selectionId'], 'profileId': account['id'], 'model': account['model'],
                                                'name': 'App', 'access': 'edit'})
        self.assertEqual((done['name'], done['access'], done['overview']['channelId']), ('App', 'edit', 'overview'))
        self.assertEqual(started[0]['projectId'], done['id'])
        grant = self.host.dispatch('coding_permission', {'projectId': done['id'], 'channelId': done['channelId']})
        self.assertEqual((grant['state'], grant.get('project_access'), grant.get('check_profile')), ('granted', True, 'manual-review'))
        with self.assertRaises(Rejected):
            first_run.dispatch(self.host, 'first_run_finish', {'selectionId': 'x', 'profileId': 'y', 'model': 'z', 'name': 'n', 'access': 'everything'})
        with self.assertRaises(Rejected):
            first_run.dispatch(self.host, 'first_run_status', {'extra': 1})


    def test_full_access_is_turned_on_only_with_the_warning_shown_and_saves_the_suggested_checks(self):
        # Bryce, 9 Oct: setup offers full access with Dervo's checks (recommended) or editing with your approval. Nothing
        # is granted unseen: finish needs the token inspect handed out with the warning.
        import subprocess
        from project_setup import stage
        def git(folder, *argv):
            subprocess.run(['git', '-C', str(folder), *argv], check=True, capture_output=True)
        with patch.object(first_run, '_claude', lambda host, run=None, **_: dict(state='ready', email='bryce@example.test', detail='')):
            account = first_run.use_claude(self.host)
        def project(name, *, repo=True):
            folder = self.base / name; folder.mkdir()
            (folder / 'test_app.py').write_text('import unittest\n\n\nclass AppTests(unittest.TestCase):\n    def test_app(self):\n        pass\n')
            if repo:
                git(folder, 'init', '-q', '-b', 'main'); git(folder, 'config', 'user.email', 'f@example.test'); git(folder, 'config', 'user.name', 'F')
                git(folder, 'add', '-A'); git(folder, 'commit', '-qm', 'start')
            picked = stage(self.host, folder)
            return picked, first_run.inspect(self.host, {'selectionId': picked['selectionId']})
        real = self.host.dispatch
        def dispatch(operation, args):
            if operation == 'project_explore':
                return {'id': 'req', 'state': 'working', 'channelId': 'overview'}
            return real(operation, args)
        def finish(picked, **extra):
            with patch.object(self.host, 'dispatch', side_effect=dispatch):
                return first_run.dispatch(self.host, 'first_run_finish', {'selectionId': picked['selectionId'], 'profileId': account['id'],
                                                                           'model': account['model'], 'name': 'App', 'access': 'full', **extra})
        picked, facts = project('full')
        self.assertEqual(facts['full_access']['checks'], ['Python tests'])
        self.assertIn('no sandbox', facts['full_access']['detail']['claude'])
        self.assertIn('Codex runs commands', facts['full_access']['detail']['codex'])
        done = finish(picked, warning=facts['full_access']['warning'])
        self.assertEqual((done['access'], done['checks']), ('full', ['Python tests']))
        view = self.host.dispatch('coding_permission', {'projectId': done['id'], 'channelId': done['channelId']})
        self.assertEqual((view['state'], view['full_access']['enabled'], view['full_access']['commands'], view['full_access']['auto_commit']),
                         ('granted', True, True, False), 'commands on, and nothing is committed without your approval')
        self.assertEqual([c['name'] for c in view['run_checks']['commands']], ['Python tests'])
        # Start again on the same folder after a failure partway, choosing less: what the first try granted gives way.
        again = finish(picked, access='read')
        self.assertEqual((again['id'], again['access']), (done['id'], 'read'))
        view = self.host.dispatch('coding_permission', {'projectId': done['id'], 'channelId': done['channelId']})
        self.assertEqual((view['state'], view['full_access']['enabled']), ('missing', False))
        # Negative: without the warning's token it stays at editing with your approval, and says why.
        picked, facts = project('unseen')
        done = finish(picked)
        self.assertEqual(done['access'], 'edit')
        self.assertIn('warning', done['access_detail'])
        view = self.host.dispatch('coding_permission', {'projectId': done['id'], 'channelId': done['channelId']})
        self.assertEqual((view['state'], view['full_access']['enabled'], view['run_checks']['commands']), ('granted', False, []))
        # A folder outside git: no checks offered, and full access can't start, so it's editing with the reason.
        picked, facts = project('plain', repo=False)
        self.assertEqual(facts['full_access']['checks'], [])
        done = finish(picked, warning=facts['full_access']['warning'])
        self.assertEqual(done['access'], 'edit')
        self.assertIn('git', done['access_detail'])
        with self.assertRaises(Rejected):
            first_run.dispatch(self.host, 'first_run_inspect', {'selectionId': 'x', 'warning': 'beta.15'})

    def _finish(self, *, failing=(), account=None):
        folder = self.base / 'waits'; folder.mkdir(exist_ok=True)
        from project_setup import stage
        picked = stage(self.host, folder)
        with patch.object(first_run, '_claude', lambda host, run=None, **_: dict(state='ready', email='bryce@example.test', detail='')):
            profile = first_run.use_claude(self.host)
        real, seen = self.host.dispatch, []
        def dispatch(operation, args):
            seen.append(operation)
            if operation in failing:
                raise Rejected(failing[operation])
            if operation == 'coding_request':
                return {'id': 'req', 'state': 'working'}
            return real(operation, args)
        session = SimpleNamespace(status=lambda: account, close=lambda: None) if account is not None else getattr(self.host, 'account_session', None)
        with patch.object(self.host, 'dispatch', side_effect=dispatch), patch.object(self.host, 'account_session', session, create=True):
            done = first_run.finish(self.host, {'selectionId': picked['selectionId'], 'profileId': profile['id'], 'model': profile['model'],
                                                'name': 'Waits', 'access': 'edit'})
        return done, seen

    def test_a_pending_tester_finishes_setup_and_the_overview_waits_in_its_own_thread(self):
        pending = dict(state='signed_in', identity={'email': 'new@example.test'}, membership=dict(allowed=False, status='pending'))
        done, seen = self._finish(account=pending)
        self.assertNotIn('coding_request', seen, 'nothing is sent while access waits, so no refused request is recorded')
        self.assertEqual(done['overview']['state'], 'waiting')
        self.assertIn('waiting for approval', done['overview_detail'], 'the reason comes back instead of being dropped')
        thread = done['overview']['channelId']
        names = [c['name'] for c in self.host.dispatch('conversations', {'projectId': done['id']}) if c['id'] == thread]
        self.assertEqual(names, ['Project overview'], 'the overview thread exists, so the project opens on it and waits')
        # Approval: the renderer's project_explore starts the overview in that same thread, under first run's request ID.
        started, _ = [], None
        real = self.host.dispatch
        def dispatch(operation, args):
            if operation == 'coding_request':
                started.append(args)
                return {'id': 'req', 'state': 'working'}
            return real(operation, args)
        with patch.object(self.host, 'dispatch', side_effect=dispatch):
            now = self.host.dispatch('project_explore', {'projectId': done['id'], 'clientId': 'first-run-' + done['id']})
        self.assertEqual((now['channelId'], started[0]['channelId'], started[0]['clientId']), (thread, thread, 'first-run-' + done['id']))
        # Negative: a refusal for another reason isn't a wait; it says why.
        other, _ = self._finish(failing={'coding_request': 'The model is gone.'})
        self.assertEqual((other['overview']['state'], other['overview_detail']), ('not_started', 'The model is gone.'))

    def test_with_no_plan_the_project_is_set_up_and_the_overview_waits_for_one(self):
        # Paid plans (8 Oct): a trial that ended, or one not started, is a wait like beta approval, not a refused request.
        ended = dict(state='signed_in', identity={'email': 'new@example.test'}, membership=dict(allowed=False, status='approved'),
                     plan=dict(plan='none'), detail='Your free trial has ended. Choose a plan in Settings › Account to keep starting agent work.')
        self.assertEqual(first_run._account(SimpleNamespace(account_session=SimpleNamespace(status=lambda: ended)))['state'], 'plan')
        done, seen = self._finish(account=ended)
        self.assertNotIn('coding_request', seen)
        self.assertEqual((done['overview']['state'], done['overview']['waiting_for']), ('waiting', 'plan'))
        self.assertIn('trial has ended', done['overview_detail'])
        # Approval comes first: a pending tester with no trial yet waits for approval, which a trial can't give.
        pending = {**ended, 'membership': dict(allowed=False, status='pending')}
        self.assertEqual(first_run._account(SimpleNamespace(account_session=SimpleNamespace(status=lambda: pending)))['state'], 'pending')
        # Access taken away reads as removed, never as waiting for approval.
        for gone in ({**ended, 'plan': dict(plan='removed')}, {**ended, 'membership': dict(allowed=False, status='revoked'), 'plan': dict(plan='beta')}):
            self.assertEqual(first_run._account(SimpleNamespace(account_session=SimpleNamespace(status=lambda: gone)))['state'], 'removed')

    def test_a_project_made_the_normal_way_reads_as_blank_until_you_start_a_thread(self):
        # 20 Sep: the blank-project onboarding offer decides from the conversation list, and every new project has two
        # threads the host made (project setup's starter, then Project overview), so it read as live and the offer never
        # showed. Each host-made thread carries its origin from creation; a thread you start has none.
        done, _ = self._finish()
        rows = self.host.dispatch('conversations', {'projectId': done['id']})
        # Onboarding review, 9 Oct: one thread after setup. The project's own thread is the overview, not an empty one beside it.
        self.assertEqual([(row['id'], row['origin'], row['name']) for row in rows], [(done['channelId'], 'overview', 'Project overview')])
        self.assertEqual(done['overview']['channelId'], done['channelId'])
        self.assertEqual([row for row in rows if not row['origin']], [], 'nothing of yours yet, so the project is blank')
        yours = self.host.dispatch('create_conversation', {'projectId': done['id'], 'name': 'Fix the build', 'private': False})['id']
        rows = self.host.dispatch('conversations', {'projectId': done['id']})
        self.assertEqual([row['id'] for row in rows if not row['origin']], [yours], 'one thread of yours, so the project is live')
        # Renaming a host-made thread doesn't lose the mark: the origin is a row, not the name.
        scope = {'projectId': done['id'], 'channelId': done['overview']['channelId']}
        state = self.host.dispatch('organisation_get', scope)
        self.host.dispatch('organisation_set', {**scope, 'expectedRevision': state['revision'], 'name': 'Read of the app', 'group': state['group'],
                                                'nameLocked': True, 'groupLocked': state['groupLocked']})
        rows = {row['id']: (row['name'], row['origin']) for row in self.host.dispatch('conversations', {'projectId': done['id']})}
        self.assertEqual(rows[done['overview']['channelId']], ('Read of the app', 'overview'))

    def test_editing_that_cant_be_granted_leaves_the_project_reading_and_still_starts_the_overview(self):
        done, seen = self._finish(failing={'coding_permission_grant': 'Git isn’t working in this folder.'})
        self.assertEqual((done['access'], done['access_detail']), ('read', 'Git isn’t working in this folder.'))
        self.assertIn('project_explore', seen, 'the overview is read-only, so it doesn’t need editing')
        self.assertTrue(done['overview']['channelId'])


    def test_your_claude_code_starts_on_opus_5_5_only_when_it_can_run_it(self):
        # Opus 5.5 needs Claude Code 2.1.280 (model configuration, checked 23 Sep 2026). Older versions start on Opus 5.
        for version, model in (('2.1.280', 'claude-opus-5-5'), ('2.1.290', 'claude-opus-5-5'), ('2.1.274', 'claude-opus-5'), (None, 'claude-opus-5')):
            with self.subTest(version=version):
                found = dict(state='ready', method='subscription', email='bryce@example.test', plan='team', version=version,
                             path=str(self.cli), profile_id=None, detail='Signed in.')
                with patch.object(first_run, '_claude', lambda host, run=None, **_: found):
                    account = first_run.use_claude(self.host)
                self.assertEqual(account['model'], model)


if __name__ == '__main__':
    unittest.main()
