"""Identity-verified Codex helper binding: Host -> the same real Supervisor -> the library's CodexAppServerWorker over a
FAKE app-server, next to the Claude helper, with the explicit cross-provider boundary record.

Nothing here reads credentials or infers a provider. The worker walks the official protocol (initialize, config/read,
account/read, model/list, thread/start) in the profile's owned CODEX_HOME and refuses before any text is sent; the fake
answers that protocol from a scenario file. Disposable stores only. Skipped, with the reason, when the packaged library
predates the Codex worker (vendor harness-core 0.7.1 or later).
"""
import json
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dervo_connections import Rejected
from host import Host

BACKEND = Path(__file__).resolve().parent
IDENTITY = 'dervo-test@example.com'
MODEL = 'codex-fake-model-1'
CLAUDE_MODEL = 'claude-fake-model-1'
ALLOWLIST = {'PATH', 'LANG', 'LC_ALL', 'HOME', 'CODEX_HOME'}
SHELL_EXTRAS = {'PWD', 'SHLVL', 'OLDPWD', '_', '__CF_USER_TEXT_ENCODING'}


def library_has_codex():
    import dervo_harness
    return hasattr(dervo_harness, 'CodexAppServerWorker')


def wrapper(path, script):
    path.write_text(f'#!/bin/sh\nexec {sys.executable} -B {script} "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@unittest.skipUnless(library_has_codex(), 'packaged dervo_harness has no Codex worker; vendor harness-core 0.7.1 or later')
class CodexHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.codex_exe = wrapper(self.base / 'fake-codex', BACKEND / 'fake_codex_app_server.py')
        self.claude_exe = wrapper(self.base / 'fake-claude', BACKEND / 'fake_claude_cli.py')
        self.hosts = []
        self.addCleanup(lambda: [self.close_host(h) for h in list(self.hosts)])
        self.host = self.open_host()

    def open_host(self, root=None):
        from harness_adapter import open_supervisor
        host = Host(root or self.base / 'store', supervisor_factory=open_supervisor, harness_claude_executable=str(self.claude_exe),
                    harness_codex_executable=str(self.codex_exe))
        self.hosts.append(host)
        return host

    def close_host(self, host):
        if host in self.hosts:
            self.hosts.remove(host)
            host.close()

    def seed(self, *, provider='codex', identity=IDENTITY, category='personal', model=MODEL):
        if category == 'work' and identity == IDENTITY:
            identity = 'work-' + IDENTITY  # one email is never both a work and a personal account
        profile = self.host.dispatch('add_profile', {'provider': provider, 'identity': identity, 'category': category})['id']
        project = self.host.add_picked_project(str(self.project), profile, model)['id']
        channel = self.host.dispatch('create_conversation', {'projectId': project, 'name': 'Planning', 'private': False})['id']
        home = self.host.root / 'profiles' / profile
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {'projectId': project, 'channelId': channel}, profile, home

    def scenario(self, home, data):
        (home / 'fake-scenario.json').write_text(json.dumps(data))

    def ask(self, scope, client_id, text, helper):
        return self.host.dispatch('harness_request', {**scope, 'clientId': client_id, 'text': text, 'mode': 'coordinate', 'helper': helper})

    def settle(self, scope, request_id, *, states=('completed', 'cancelled', 'failed', 'lost', 'unknown'), timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.host.tick()
            status = self.host.dispatch('harness_status', {**scope, 'requestId': request_id})
            if status['state'] in states:
                return status
            time.sleep(.01)
        self.fail('Harness request did not settle')

    def events(self, scope, request_id):
        return [e for e in self.host.dispatch('harness_events', {**scope, 'after': 0}) if e['request_id'] == request_id]

    def methods(self, home):
        return [m['method'] for m in jsonl(home / 'received.jsonl') if 'method' in m]

    def test_helper_is_derived_from_the_owned_profile_and_runs_text_only_after_the_protocol_checks(self):
        from harness_codex import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual((harness['state'], harness['helpers']), ('available', [name]))
        self.assertEqual(harness['details'][name], {'provider': 'codex', 'model': MODEL, 'account': 'profile:' + profile, 'fake': False, 'can_ask': False})
        self.assertNotIn(IDENTITY, json.dumps(harness))
        self.scenario(home, {'result_text': 'Next Friday is the 18th.'})
        started = self.ask(scope, 'ask-1', 'What date is next Friday?', name)
        self.assertEqual((started['state'], started['helper']), ('queued', name))
        status = self.settle(scope, started['id'])
        self.assertEqual(status['state'], 'completed', status)
        self.assertEqual(status['linked_return']['summary'], 'Next Friday is the 18th.')
        self.assertEqual((status['linked_return']['worker'], status['linked_return']['worker_is_fake']), ('codex-app-server-text-only', False))
        routed = next(e for e in self.events(scope, started['id']) if e['kind'] == 'routed')
        self.assertEqual((routed['data']['provider'], routed['data']['account'], routed['data']['is_fake']), ('codex', 'profile:' + profile, False))
        # One launch of the pinned app-server with the app's policy overrides, in the owned home, from an empty work cwd.
        calls = jsonl(home / 'calls.jsonl')
        self.assertEqual(len(calls), 1)
        argv = calls[0]['argv']
        self.assertEqual(argv[-4:], ['app-server', '--listen', 'stdio://', '--strict-config'])
        self.assertIn('sandbox_mode="read-only"', argv)
        self.assertIn('approval_policy="on-request"', argv)
        self.assertIn('mcp_servers={}', argv)
        self.assertEqual((calls[0]['env']['CODEX_HOME'], calls[0]['env']['HOME']), (str(home), str(home)))
        self.assertTrue(ALLOWLIST <= set(calls[0]['env']) and set(calls[0]['env']) - ALLOWLIST <= SHELL_EXTRAS, sorted(calls[0]['env']))
        self.assertTrue(calls[0]['cwd'].startswith(str(self.base / 'store' / 'harness' / 'work')))
        # The official protocol, in order, before the one turn that carries the context and the request.
        self.assertEqual(self.methods(home), ['initialize', 'initialized', 'config/read', 'account/read', 'model/list', 'thread/start', 'turn/start'])
        turn = next(m for m in jsonl(home / 'received.jsonl') if m.get('method') == 'turn/start')
        self.assertEqual((turn['params']['model'], turn['params']['sandboxPolicy']), (MODEL, {'type': 'readOnly', 'networkAccess': False}))
        text = turn['params']['input'][0]['text']
        self.assertIn('<dervo-context>', text)
        self.assertIn('What date is next Friday?', text)
        self.assertNotIn(IDENTITY, text)
        self.assertTrue((home / 'DERVO_CODEX_HOME.json').is_file())
        phases = [e['data'].get('phase') for e in self.events(scope, started['id']) if e['kind'] == 'progress']
        for phase in ('profile_checked', 'initialized', 'config_verified', 'account_observed', 'catalog_verified', 'thread_started', 'turn_started', 'turn_completed'):
            self.assertIn(phase, phases)
        db = sqlite3.connect(f'file:{self.base / "store" / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT worker_name, worker_is_fake, status FROM runs').fetchall(), [('codex-app-server-text-only', 0, 'succeeded')])
        for table in ('messages', 'requests', 'rooms', 'projects', 'sessions'):
            self.assertFalse(any(IDENTITY in json.dumps(list(r)) for r in db.execute(f'SELECT * FROM {table}').fetchall()), table)

    def test_identity_model_and_profile_problems_refuse_before_any_text_is_sent(self):
        from harness_codex import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        cases = [({'account': {'type': 'chatgpt', 'email': 'someone-else@example.com', 'planType': 'plus'}}, 'not the configured identity'),
                 ({'signed_out': True}, 'not signed in'),
                 ({'account': {'type': 'apiKey'}}, 'API-key billing'),
                 ({'catalog': [{'model': 'other-model', 'hidden': False}]}, 'not in the Codex catalog'),
                 ({'config_patch': {'sandbox_mode': 'danger-full-access'}}, 'differs from the launch policy')]
        for i, (data, expected) in enumerate(cases):
            self.scenario(home, data)
            status = self.settle(scope, self.ask(scope, f'ask-{i}', 'What date?', name)['id'])
            self.assertEqual(status['state'], 'failed', (data, status))
            self.assertIn(expected, status['error'])
            self.assertNotIn('turn/start', self.methods(home), data)
        # Custom configuration in the owned home is refused before the binary is even launched.
        (home / 'config.toml').write_text('sandbox_mode = "danger-full-access"\n')
        launches = len(jsonl(home / 'calls.jsonl'))
        status = self.settle(scope, self.ask(scope, 'ask-config', 'What date?', name)['id'])
        self.assertEqual(status['state'], 'failed')
        self.assertIn('needs review', status['error'])
        self.assertEqual(len(jsonl(home / 'calls.jsonl')), launches)
        (home / 'config.toml').unlink()
        self.scenario(home, {'result_text': 'ok now'})
        self.assertEqual(self.settle(scope, self.ask(scope, 'ask-ok', 'What date?', name)['id'])['state'], 'completed')

    def test_providers_and_profiles_stay_apart_without_a_record(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        codex_scope, codex_profile, codex_home = self.seed()
        claude_scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        other_scope, other_profile, other_home = self.seed(identity=IDENTITY)  # a second Codex profile with the same email
        work_scope, work_profile, _ = self.seed(category='work')
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual(harness['boundaries'], [])
        self.assertEqual({harness['details'][n]['account'] for n in harness['helpers']},
                         {'profile:' + codex_profile, 'profile:' + claude_profile, 'profile:' + other_profile})
        self.assertTrue(any(x['profile'] == work_profile and 'Work' in x['reason'] for x in harness['excluded']))
        for scope, helper in ((codex_scope, claude_name(claude_profile, CLAUDE_MODEL)), (claude_scope, codex_name(codex_profile, MODEL))):
            with self.assertRaises(Rejected) as refused:
                self.ask(scope, 'ask-x', 'What date?', helper)
            self.assertIn('provider', str(refused.exception).lower())
        with self.assertRaises(Rejected) as refused:
            self.ask(codex_scope, 'ask-y', 'What date?', codex_name(other_profile, MODEL))
        self.assertIn('account', str(refused.exception).lower())
        self.assertEqual(jsonl(codex_home / 'calls.jsonl') + jsonl(claude_home / 'calls.jsonl') + jsonl(other_home / 'calls.jsonl'), [])
        self.assertFalse((self.base / 'store' / 'harness').exists())

    def test_boundary_record_is_explicit_by_profile_id_and_makes_helpers_visible_to_each_other(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        claude_scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        codex_scope, codex_profile, codex_home = self.seed()
        twin_scope, twin_profile, twin_home = self.seed(identity=IDENTITY)  # same email, deliberately left out of the record
        _, work_profile, _ = self.seed(category='work')
        claude, codex = claude_name(claude_profile, CLAUDE_MODEL), codex_name(codex_profile, MODEL)
        self.scenario(claude_home, {'result_text': 'Claude answered.'})
        self.scenario(codex_home, {'result_text': 'Codex answered.'})
        # Without a record: same person, same email, two profiles, no shared label, nothing rendered across.
        first = self.settle(claude_scope, self.ask(claude_scope, 'ask-1', 'Plan the week', claude)['id'])
        self.assertEqual(first['state'], 'completed')
        prompt = next(m for m in jsonl(claude_home / 'received.jsonl') if m.get('type') == 'user')['message']['content']
        self.assertNotIn('Helpers this run may ask', prompt)
        # Refused records: too few, duplicates, unknown, work, and later a profile already covered.
        for bad in ([claude_profile], [claude_profile, claude_profile], [claude_profile, 'nope'], [claude_profile, work_profile], 'x'):
            with self.assertRaises(Rejected):
                self.host.dispatch('harness_boundary', {'profileIds': bad})
        record = self.host.dispatch('harness_boundary', {'profileIds': [codex_profile, claude_profile]})
        self.assertEqual(record['profiles'], sorted([claude_profile, codex_profile]))
        with self.assertRaises(Rejected):
            self.host.dispatch('harness_boundary', {'profileIds': [claude_profile, twin_profile]})
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual([b['id'] for b in harness['boundaries']], [record['id']])
        label = 'boundary:' + record['id']
        self.assertEqual((harness['details'][claude]['account'], harness['details'][codex]['account']), (label, label))
        self.assertEqual(harness['details'][codex_name(twin_profile, MODEL)]['account'], 'profile:' + twin_profile)
        # With the record: the Claude run's context lists the Codex helper as one it may ask, and the run carries the label.
        second = self.settle(claude_scope, self.ask(claude_scope, 'ask-2', 'Plan the week again', claude)['id'])
        self.assertEqual(second['state'], 'completed', second)
        prompts = [m['message']['content'] for m in jsonl(claude_home / 'received.jsonl') if m.get('type') == 'user']
        self.assertIn('Helpers this run may ask', prompts[-1])
        self.assertIn('@' + codex, prompts[-1])
        self.assertNotIn('@' + codex_name(twin_profile, MODEL), prompts[-1])
        routed = [e for e in self.events(claude_scope, second['id']) if e['kind'] == 'routed'][0]
        self.assertEqual(routed['data']['account'], label)
        # And the other way round, in the Codex helper's own conversation, through the same pool.
        third = self.settle(codex_scope, self.ask(codex_scope, 'ask-3', 'Which date?', codex)['id'])
        self.assertEqual(third['state'], 'completed', third)
        turn = [m for m in jsonl(codex_home / 'received.jsonl') if m.get('method') == 'turn/start'][-1]
        self.assertIn('@' + claude, turn['params']['input'][0]['text'])
        # Still no crossing of what a conversation may run: the record never lets a Codex conversation run the Claude helper.
        with self.assertRaises(Rejected):
            self.ask(codex_scope, 'ask-4', 'What date?', claude)
        self.assertEqual(len(jsonl(twin_home / 'calls.jsonl')), 0)

    def test_cancel_interrupts_the_turn_and_the_run_is_cancelled_only_on_exit(self):
        from harness_codex import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        self.scenario(home, {'mode': 'hang', 'hang_seconds': 30})
        started = self.ask(scope, 'ask-1', 'Take your time', name)['id']
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and 'turn/start' not in self.methods(home):
            self.host.tick()
            time.sleep(.05)
        self.assertIn('turn/start', self.methods(home))
        cancelled = self.host.dispatch('harness_cancel', {**scope, 'requestId': started})
        self.assertIn(cancelled['state'], ('stopping', 'cancelled'))
        status = self.settle(scope, started)
        self.assertEqual((status['state'], status['linked_return']), ('cancelled', None))
        self.assertIn('turn/interrupt', self.methods(home))
        stopped = next(e for e in self.events(scope, started) if e['kind'] == 'stopped')
        self.assertEqual(stopped['data']['interrupt'], 'confirmed')
        self.scenario(home, {'result_text': 'Done.'})
        self.assertEqual(self.settle(scope, self.ask(scope, 'ask-2', 'Quick one', name)['id'])['state'], 'completed')


@unittest.skipUnless(library_has_codex(), 'packaged dervo_harness has no Codex worker; vendor harness-core 0.7.1 or later')
class CodexHelperVersionTests(unittest.TestCase):
    """No Host, so the beta access gate doesn't stand in the way: the worker a helper port builds must expect the Codex the
    app bundles (codex_runtime.VERSION), not the version the vendored library was written against."""

    def test_the_worker_expects_the_bundled_codex_version(self):
        import dervo_harness as lib
        from codex_runtime import VERSION
        from harness_codex import port_factory
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            exe = wrapper(base / 'fake-codex', BACKEND / 'fake_codex_app_server.py')
            worker = port_factory({'home': base / 'home', 'identity': IDENTITY}, MODEL, str(exe))('p1', base / 'work')
            self.assertIsInstance(worker, lib.CodexAppServerWorker)
            self.assertEqual(worker.config.version, VERSION)

    def test_the_worker_accepts_the_plans_setup_accepts(self):
        # Josh, feedback #9 (27 Sep): the library's default is Plus and Pro only, so a $100 Pro ("prolite") account that
        # passed setup would have had every helper run refused.
        from connections_ui import CODEX_PLANS
        from harness_codex import port_factory
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            exe = wrapper(base / 'fake-codex', BACKEND / 'fake_codex_app_server.py')
            worker = port_factory({'home': base / 'home', 'identity': IDENTITY}, MODEL, str(exe))('p1', base / 'work')
            self.assertEqual(worker.config.plan_types, CODEX_PLANS['personal'])
            self.assertIn('prolite', worker.config.plan_types)


if __name__ == '__main__':
    unittest.main()
