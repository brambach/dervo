"""Identity-verified Claude helper binding: Host -> real Supervisor -> the library's ClaudeCliWorker over a FAKE CLI.

Nothing here reads credentials or infers a provider. The worker runs the CLI's own ``auth status`` in the profile's
owned CLAUDE_CONFIG_DIR and refuses any identity other than the profile's; the fake CLI answers that protocol from a
scenario file. Disposable stores only.
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
MODEL = 'claude-fake-model-1'


class ClaudeHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.exe = self.base / 'fake-claude'
        self.exe.write_text(f'#!/bin/sh\nexec {sys.executable} -B {BACKEND / "fake_claude_cli.py"} "$@"\n')
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        self.hosts = []
        self.addCleanup(lambda: [self.close_host(h) for h in list(self.hosts)])
        self.host = self.open_host()

    def open_host(self, root=None):
        from harness_adapter import open_supervisor
        host = Host(root or self.base / 'store', supervisor_factory=open_supervisor, harness_claude_executable=str(self.exe))
        self.hosts.append(host)
        return host

    def close_host(self, host):
        if host in self.hosts:
            self.hosts.remove(host)
            host.close()

    def seed(self, *, provider='claude', identity=IDENTITY, category='personal', model=MODEL, host=None):
        host = host or self.host
        if category == 'work' and identity == IDENTITY:
            identity = 'work-' + IDENTITY  # one email is never both a work and a personal account
        profile = host.dispatch('add_profile', {'provider': provider, 'identity': identity, 'category': category})['id']
        project = host.add_picked_project(str(self.project), profile, model)['id']
        channel = host.dispatch('create_conversation', {'projectId': project, 'name': 'Planning', 'private': False})['id']
        home = (host.root / 'profiles' / profile)
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {'projectId': project, 'channelId': channel}, profile, home

    def scenario(self, home, data):
        (home / 'fake-scenario.json').write_text(json.dumps(data))

    def calls(self, home):
        path = home / 'calls.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def received(self, home):
        path = home / 'received.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def ask(self, scope, client_id, text, helper, *, host=None):
        return (host or self.host).dispatch('harness_request', {**scope, 'clientId': client_id, 'text': text, 'mode': 'coordinate', 'helper': helper})

    def settle(self, scope, request_id, *, host=None, states=('completed', 'cancelled', 'failed', 'lost', 'unknown'), timeout=20):
        host = host or self.host
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            host.tick()
            status = host.dispatch('harness_status', {**scope, 'requestId': request_id})
            if status['state'] in states:
                return status
            time.sleep(.01)
        self.fail('Harness request did not settle')

    def test_helper_is_derived_from_the_owned_profile_and_runs_text_only_after_identity_check(self):
        from harness_claude import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual((harness['state'], harness['helpers']), ('available', [name]))
        self.assertEqual(harness['details'][name], {'provider': 'claude', 'model': MODEL, 'account': 'profile:' + profile, 'fake': False, 'can_ask': False})
        self.assertNotIn(IDENTITY, json.dumps(harness))
        self.scenario(home, {'result_text': 'Next Friday is the 18th.'})
        # What the composer sees before sending: this conversation's own helper, text only, or why none.
        conversation = self.host.dispatch('conversation_status', scope)['harness']
        self.assertEqual((conversation['state'], conversation['helper'], conversation['provider'], conversation['model']), ('available', name, 'claude', MODEL))
        self.assertIn('text only', conversation['detail'])
        started = self.ask(scope, 'ask-1', 'What date is next Friday?', name)
        self.assertEqual((started['state'], started['provider'], started['text']), ('queued', 'claude', 'What date is next Friday?'))
        status = self.settle(scope, started['id'])
        self.assertEqual((status['state'], status['helper']), ('completed', name), status)
        listed = self.host.dispatch('harness_requests', scope)
        self.assertEqual([(r['id'], r['state'], r['text'], r['linked_return']['summary']) for r in listed], [(started['id'], 'completed', 'What date is next Friday?', 'Next Friday is the 18th.')])
        self.assertEqual(self.host.dispatch('harness_requests', {**scope, 'channelId': self.host.dispatch('create_conversation', {'projectId': scope['projectId'], 'name': 'Other', 'private': False})['id']}), [])
        self.assertEqual(status['linked_return']['summary'], 'Next Friday is the 18th.')
        self.assertFalse(status['linked_return']['worker_is_fake'])
        self.assertEqual(status['linked_return']['worker'], 'claude-cli-text-only')
        routed = next(e for e in self.host.dispatch('harness_events', {**scope, 'after': 0}) if e['kind'] == 'routed')
        self.assertEqual((routed['data']['provider'], routed['data']['account'], routed['data']['is_fake']), ('claude', 'profile:' + profile, False))
        # The CLI's own auth status ran first, in the owned profile dir, before any launch; then one text-only launch.
        calls = self.calls(home)
        self.assertEqual(calls[0]['argv'], ['--safe-mode', 'auth', 'status'])
        self.assertEqual(len(calls), 2)
        argv = calls[1]['argv']
        self.assertEqual(argv[argv.index('--model') + 1], MODEL)
        for flag in ('--safe-mode', '--restricted', '--strict-mcp-config', '--disable-slash-commands'):
            self.assertIn(flag, argv)
        self.assertNotIn('--allowedTools', argv)
        self.assertEqual(argv[argv.index('--tools') + 1], '')
        self.assertEqual(calls[1]['env']['CLAUDE_CONFIG_DIR'], str(home))
        # The worker passes exactly its allowlist; the /bin/sh wrapper of the fake adds its own shell variables on top.
        allowlist = {'PATH', 'LANG', 'LC_ALL', 'HOME', 'CLAUDE_CONFIG_DIR'}
        self.assertTrue(allowlist <= set(calls[1]['env']))
        self.assertTrue(set(calls[1]['env']) - allowlist <= {'PWD', 'SHLVL', 'OLDPWD', '_', '__CF_USER_TEXT_ENCODING'}, sorted(calls[1]['env']))
        self.assertTrue(calls[1]['cwd'].startswith(str(self.base / 'store' / 'harness' / 'work')))
        user = next(m for m in self.received(home) if m.get('type') == 'user')
        self.assertIn('What date is next Friday?', user['message']['content'])
        self.assertIn('<dervo-context>', user['message']['content'])
        self.assertTrue((home / 'DERVO_CLAUDE_CONFIG_DIR.json').is_file())
        db = sqlite3.connect(f'file:{self.base / "store" / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT worker_name, worker_is_fake, status FROM runs').fetchall(), [('claude-cli-text-only', 0, 'succeeded')])
        # The account email never reaches the model or the room: not in the prompt, not in any message or request row.
        # The library's worker does log the observed auth metadata (email included) as one audit event on the run.
        self.assertNotIn(IDENTITY, user['message']['content'])
        for table in ('messages', 'requests', 'rooms', 'projects', 'sessions'):
            self.assertFalse(any(IDENTITY in json.dumps(list(r)) for r in db.execute(f'SELECT * FROM {table}').fetchall()), table)
        audited = db.execute("SELECT kind, payload_json FROM run_events WHERE payload_json LIKE ?", (f'%{IDENTITY}%',)).fetchall()
        self.assertEqual([k for k, _ in audited], ['progress'])
        self.assertEqual(json.loads(audited[0][1])['phase'], 'auth_observed')

    def test_identity_mismatch_refuses_before_any_context_is_sent(self):
        from harness_claude import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        self.scenario(home, {'auth': {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max',
                                      'email': 'someone-else@example.com'}})
        status = self.settle(scope, self.ask(scope, 'ask-1', 'What date?', name)['id'])
        self.assertEqual(status['state'], 'failed')
        self.assertIn('not the configured identity', status['error'])
        self.assertEqual([c['argv'] for c in self.calls(home)], [['--safe-mode', 'auth', 'status']])
        self.assertEqual(self.received(home), [])
        # Signed out: same refusal, same silence.
        self.scenario(home, {'auth': {'loggedIn': False}})
        status = self.settle(scope, self.ask(scope, 'ask-2', 'What date?', name)['id'])
        self.assertEqual(status['state'], 'failed')
        self.assertIn('not logged in', status['error'])
        self.assertEqual(self.received(home), [])

    def test_bad_model_or_identity_or_work_profile_is_never_offered_and_nothing_launches(self):
        from harness_claude import helper_name
        scope, profile, home = self.seed(model='gpt-5-codex')
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual((harness['state'], harness['helpers']), ('unavailable', []))
        conversation = self.host.dispatch('conversation_status', scope)['harness']
        self.assertEqual((conversation['state'], conversation['helper']), ('unavailable', None))
        self.assertIn('No helper is bound', conversation['detail'])
        self.assertTrue(any('model' in x['reason'] for x in harness['excluded']), harness['excluded'])
        # With no helper bound at all the truthful answer is unavailable, with the reason; nothing is selected instead.
        result = self.ask(scope, 'ask-1', 'What date?', helper_name(profile, 'gpt-5-codex'))
        self.assertEqual((result['state'], result['id']), ('unavailable', None))
        self.assertIn('No helper is bound', result['detail'])
        self.assertEqual(self.calls(home), [])
        scope2, profile2, home2 = self.seed(identity='nobody')
        excluded = self.host.dispatch('status', {})['harness']['excluded']
        self.assertTrue(any(x['profile'] == profile2 and 'identity' in x['reason'] for x in excluded), excluded)
        self.assertEqual(self.ask(scope2, 'ask-1', 'What date?', helper_name(profile2, MODEL))['state'], 'unavailable')
        scope3, profile3, home3 = self.seed(category='work')
        excluded = self.host.dispatch('status', {})['harness']['excluded']
        self.assertTrue(any(x['profile'] == profile3 and 'Work' in x['reason'] for x in excluded), excluded)
        self.assertEqual(self.ask(scope3, 'ask-1', 'What date?', helper_name(profile3, MODEL))['state'], 'unavailable')
        # Once a good helper exists elsewhere, naming an excluded one is a rejection, still with nothing launched.
        scope4, profile4, home4 = self.seed()
        self.assertEqual(self.host.dispatch('status', {})['harness']['helpers'], [helper_name(profile4, MODEL)])
        with self.assertRaises(Rejected):
            self.ask(scope3, 'ask-2', 'What date?', helper_name(profile3, MODEL))
        with self.assertRaises(Rejected) as refused:
            self.ask(scope3, 'ask-3', 'What date?', helper_name(profile4, MODEL))
        self.assertIn('account', str(refused.exception).lower())
        self.assertEqual(self.calls(home2) + self.calls(home3) + self.calls(home4), [])
        self.assertFalse((self.base / 'store' / 'harness').exists())

    def test_codex_conversation_never_selects_a_claude_helper(self):
        from harness_claude import helper_name
        claude_scope, claude_profile, home = self.seed()
        codex_scope, codex_profile, _ = self.seed(provider='codex', identity='codex@example.com', model='gpt-5-codex')
        name = helper_name(claude_profile, MODEL)
        with self.assertRaises(Rejected) as refused:
            self.ask(codex_scope, 'ask-1', 'What date?', name)
        self.assertIn('provider', str(refused.exception).lower())
        self.assertEqual(self.calls(home), [])
        self.assertFalse((self.base / 'store' / 'harness').exists())
        # A Claude conversation cannot borrow another profile's helper either.
        other_scope, other_profile, other_home = self.seed(identity='other@example.com')
        with self.assertRaises(Rejected) as refused:
            self.ask(other_scope, 'ask-1', 'What date?', name)
        self.assertIn('account', str(refused.exception).lower())
        self.assertEqual(self.calls(home) + self.calls(other_home), [])

    def test_cancel_stops_a_running_claude_process_and_the_run_is_cancelled_only_on_exit(self):
        from harness_claude import helper_name
        scope, profile, home = self.seed()
        name = helper_name(profile, MODEL)
        self.scenario(home, {'mode': 'hang', 'hang_seconds': 30})
        started = self.ask(scope, 'ask-1', 'Take your time', name)['id']
        self.settle(scope, started, states=('running',))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(m.get('type') == 'user' for m in self.received(home)):
            self.host.tick()
            time.sleep(.05)
        self.assertTrue(any(m.get('type') == 'user' for m in self.received(home)))
        cancelled = self.host.dispatch('harness_cancel', {**scope, 'requestId': started})
        self.assertIn(cancelled['state'], ('stopping', 'cancelled'))
        status = self.settle(scope, started)
        self.assertEqual((status['state'], status['linked_return']), ('cancelled', None))
        kinds = [e['kind'] for e in self.host.dispatch('harness_events', {**scope, 'after': 0})]
        self.assertIn('stopped', kinds)
        self.assertLess(kinds.index('cancel_requested'), kinds.index('cancelled'))
        # A second request in the same conversation is possible again: the scope was released on confirmed exit.
        self.scenario(home, {'result_text': 'Done.'})
        self.assertEqual(self.settle(scope, self.ask(scope, 'ask-2', 'Quick one', name)['id'])['state'], 'completed')


    # ---------------------------------------------------------------- API-key accounts (19 Sep 2026)
    KEY = 'sk-ant-api03-fixture-helper-key-not-real-00000000000abcd'
    KEY_AUTH = {'loggedIn': True, 'authMethod': 'api_key', 'apiProvider': 'firstParty', 'apiKeySource': 'ANTHROPIC_API_KEY'}

    def keyed(self):
        """An API-key Claude account added the way Connected accounts adds one, with a memory Keychain."""
        from provider_keys import ProviderKeys
        items = {}

        class Keychain:
            def __init__(self, **kwargs):
                self.slot = (kwargs['workspace_id'], kwargs['purpose'])
            def read(self):
                return items.get(self.slot)
            def write(self, data):
                items[self.slot] = data
            def delete(self):
                items.pop(self.slot, None)
        self.host._keys = ProviderKeys(self.host, keychain_factory=Keychain, fetch=lambda *a, **k: 200)
        profile = self.host.dispatch('profile_key_add', {'provider': 'claude', 'key': self.KEY})['id']
        project = self.host.add_picked_project(str(self.project), profile, MODEL)['id']
        channel = self.host.dispatch('create_conversation', {'projectId': project, 'name': 'Keyed', 'private': False})['id']
        return {'projectId': project, 'channelId': channel}, profile, self.host.root / 'profiles' / profile

    def test_an_api_key_account_helper_gets_its_key_in_the_environment_only_and_must_report_api_key_auth(self):
        from harness_claude import helper_name
        scope, profile, home = self.keyed()
        name = helper_name(profile, MODEL)
        harness = self.host.dispatch('status', {})['harness']
        self.assertIn(name, harness['helpers'], 'an API-key account is a helper like any other')
        self.assertNotIn(self.KEY[8:], json.dumps(harness))
        self.scenario(home, {'auth': self.KEY_AUTH, 'result_text': 'Answered on the key.'})
        status = self.settle(scope, self.ask(scope, 'ask-1', 'What date is next Friday?', name)['id'])
        self.assertEqual(status['state'], 'completed', status)
        calls = self.calls(home)
        self.assertEqual([c['argv'][:3] for c in calls][0], ['--safe-mode', 'auth', 'status'])
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call['env'].get('ANTHROPIC_API_KEY'), self.KEY, 'auth status and the run both get this account’s key')
            self.assertNotIn(self.KEY, ' '.join(call['argv']), 'never in argv')
        # The key isn't written anywhere the harness or the host keeps: not in a run, event, message or request.
        db = sqlite3.connect(f'file:{self.base / "store" / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            self.assertFalse(any(self.KEY[8:] in json.dumps(list(r), default=str) for r in db.execute(f'SELECT * FROM "{table}"')), table)
        self.assertNotIn(self.KEY[8:], json.dumps(self.host.dispatch('harness_events', {**scope, 'after': 0})))
        for path in (self.base / 'store' / 'harness').rglob('*'):
            if path.is_file():
                self.assertNotIn(self.KEY[8:].encode(), path.read_bytes(), str(path))

    def test_an_api_key_helper_refuses_a_subscription_login_and_launches_nothing(self):
        from harness_claude import helper_name
        scope, profile, home = self.keyed()
        self.scenario(home, {})  # the fake reports its default claude.ai subscription login
        status = self.settle(scope, self.ask(scope, 'ask-1', 'What date?', helper_name(profile, MODEL))['id'])
        self.assertEqual(status['state'], 'failed')
        self.assertIn('isn’t using this account’s API key', status['error'])
        self.assertEqual([c['argv'] for c in self.calls(home)], [['--safe-mode', 'auth', 'status']])
        self.assertEqual(self.received(home), [])

    def test_a_subscription_helper_never_gets_a_key_even_with_one_saved_for_another_account(self):
        from harness_claude import helper_name
        self.keyed()
        scope, profile, home = self.seed()
        self.scenario(home, {'result_text': 'Fine.'})
        status = self.settle(scope, self.ask(scope, 'ask-1', 'What date?', helper_name(profile, MODEL))['id'])
        self.assertEqual(status['state'], 'completed', status)
        for call in self.calls(home):
            self.assertNotIn('ANTHROPIC_API_KEY', call['env'])
            self.assertNotIn(self.KEY, json.dumps(call))

    def test_without_the_key_store_an_api_key_account_is_excluded_with_the_reason(self):
        from harness_claude import derive, port_factory
        _, profile, _ = self.keyed()
        helpers, excluded = derive(self.host.store, self.host.root, executable=str(self.exe))
        self.assertEqual(helpers, {})
        self.assertIn('key store', excluded[0]['reason'])
        row = dict(self.host.store.db.execute('SELECT * FROM profiles WHERE id=?', (profile,)).fetchone())
        with self.assertRaisesRegex(Rejected, 'needs its key'):
            port_factory(row, MODEL, str(self.exe))


if __name__ == '__main__':
    unittest.main()
