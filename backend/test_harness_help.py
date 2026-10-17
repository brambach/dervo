"""Named help through the app's shared harness: Host -> real Supervisor -> Claude action port (fake CLI playing the SDK
MCP client and the actual request_help tool contract) -> linked Codex helper (fake app-server) -> linked return.

Nothing here infers a provider or reads credentials. Every ask is the fake CLI's scripted tools/call; the library's
broker validates it, dispatches the helper, and returns the answer through the packet. Disposable stores only.
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
CLAUDE_MODEL = 'claude-fake-model-1'
CODEX_MODEL = 'codex-fake-model-1'


def wrapper(path, script):
    path.write_text(f'#!/bin/sh\nexec {sys.executable} -B {script} "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


class NamedHelpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.claude_exe = wrapper(self.base / 'fake-claude', BACKEND / 'fake_claude_cli.py')
        self.codex_exe = wrapper(self.base / 'fake-codex', BACKEND / 'fake_codex_app_server.py')
        self.hosts = []
        self.addCleanup(lambda: [self.close_host(h) for h in list(self.hosts)])
        self.host = self.open_host()

    def open_host(self):
        from harness_adapter import open_supervisor
        host = Host(self.base / 'store', supervisor_factory=open_supervisor, harness_claude_executable=str(self.claude_exe),
                    harness_codex_executable=str(self.codex_exe))
        self.hosts.append(host)
        return host

    def close_host(self, host):
        if host in self.hosts:
            self.hosts.remove(host)
            host.close()

    def seed(self, *, provider, model, identity=IDENTITY, private=False):
        profile = self.host.dispatch('add_profile', {'provider': provider, 'identity': identity, 'category': 'personal'})['id']
        project = self.host.add_picked_project(str(self.project), profile, model)['id']
        channel = self.host.dispatch('create_conversation', {'projectId': project, 'name': 'Help QA', 'private': private})['id']
        home = self.host.root / 'profiles' / profile
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        return {'projectId': project, 'channelId': channel}, profile, home

    def scenario(self, home, data):
        (home / 'fake-scenario.json').write_text(json.dumps(data))

    def ask(self, scope, client_id, text, helper, mentions=None):
        args = {**scope, 'clientId': client_id, 'text': text, 'mode': 'coordinate', 'helper': helper}
        if mentions is not None:
            args['mentions'] = mentions
        return self.host.dispatch('harness_request', args)

    def settle(self, scope, request_id, *, until, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.host.tick()
            status = self.host.dispatch('harness_status', {**scope, 'requestId': request_id})
            if until(status):
                return status
            time.sleep(.02)
        self.fail(f'request did not settle: {status}')

    def link(self, claude_profile, codex_profile):
        record = self.host.dispatch('harness_boundary', {'profileIds': [claude_profile, codex_profile]})
        toggled = self.host.dispatch('harness_actions', {'profileId': claude_profile, 'enabled': True})
        self.assertEqual((toggled['enabled'], toggled['linked']), (True, True))
        return record

    def test_catalogue_resolution_and_action_port_are_explicit_and_off_by_default(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        codex_scope, codex_profile, codex_home = self.seed(provider='codex', model=CODEX_MODEL)
        claude, codex = claude_name(claude_profile, CLAUDE_MODEL), codex_name(codex_profile, CODEX_MODEL)
        # Unlinked: same email on both, yet no catalogue, no asking, and an explicit mention is refused before anything opens.
        conversation = self.host.dispatch('conversation_status', scope)['harness']
        self.assertEqual((conversation['can_ask'], conversation['catalogue']), (False, []))
        answer = self.host.dispatch('harness_resolve', {**scope, 'mention': '@' + codex})
        self.assertFalse(answer['resolved'])
        self.assertIn('not a helper linked', answer['reason'])
        with self.assertRaises(Rejected):
            self.ask(scope, 'ask-0', 'Ask @' + codex, claude, mentions=['@' + codex])
        self.assertFalse((self.base / 'store' / 'harness').exists())
        # The action port is Claude only and off until switched on.
        with self.assertRaises(Rejected):
            self.host.dispatch('harness_actions', {'profileId': codex_profile, 'enabled': True})
        off = self.host.dispatch('harness_actions', {'profileId': claude_profile, 'enabled': True})
        self.assertEqual((off['enabled'], off['linked']), (True, False))
        self.assertIn('nobody to ask', off['detail'])
        self.assertFalse(self.host.dispatch('conversation_status', scope)['harness']['can_ask'], 'opt-in without a boundary configures nothing')
        # Linked: the catalogue lists the Codex helper as answer-only; the Claude helper may ask; resolution is explicit.
        self.host.dispatch('harness_boundary', {'profileIds': [claude_profile, codex_profile]})
        conversation = self.host.dispatch('conversation_status', scope)['harness']
        self.assertTrue(conversation['can_ask'])
        self.assertEqual(conversation['catalogue'], [{'handle': '@' + codex, 'provider': 'codex', 'model': CODEX_MODEL, 'can_ask': False}])
        self.assertIn('may ask the linked helpers', conversation['detail'])
        codex_side = self.host.dispatch('conversation_status', codex_scope)['harness']
        self.assertEqual((codex_side['can_ask'], codex_side['catalogue'][0]['handle'], codex_side['catalogue'][0]['can_ask']), (False, '@' + claude, True))
        self.assertNotIn('may ask', codex_side['detail'])
        status = self.host.dispatch('status', {})['harness']
        self.assertEqual((status['actions'], status['details'][claude]['can_ask'], status['details'][codex]['can_ask']), ([claude_profile], True, False))
        answer = self.host.dispatch('harness_resolve', {**scope, 'mention': '@' + codex})
        self.assertEqual((answer['resolved'], answer['handle'], answer['asker_can_ask'], answer['verified']), (True, '@' + codex, True, None))
        self.assertFalse(self.host.dispatch('harness_resolve', {**scope, 'mention': '@' + claude})['resolved'], 'a helper cannot ask itself')
        self.assertFalse(self.host.dispatch('harness_resolve', {**scope, 'mention': '@Not Valid!'})['resolved'])
        self.assertEqual(self.host.dispatch('harness_actions', {'profileId': claude_profile, 'enabled': False})['enabled'], False)
        self.assertFalse(self.host.dispatch('conversation_status', scope)['harness']['can_ask'])

    def test_linked_help_is_asked_through_the_action_tool_and_the_answer_is_delivered(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        _, codex_profile, codex_home = self.seed(provider='codex', model=CODEX_MODEL)
        claude, codex = claude_name(claude_profile, CLAUDE_MODEL), codex_name(codex_profile, CODEX_MODEL)
        self.link(claude_profile, codex_profile)
        self.scenario(claude_home, {'tool_calls': [{'arguments': {'target': '@' + codex, 'purpose': 'Which date is next Friday?', 'source_refs': ['{first_ref}']}}],
                                    'result_text': 'Asked the Codex helper; its answer arrives in this conversation. {tool_results}'})
        self.scenario(codex_home, {'result_text': 'CODEX FAKE: next Friday is the 18th.'})
        started = self.ask(scope, 'ask-1', 'Please ask @' + codex + ' which date is next Friday.', claude, mentions=['@' + codex])
        self.assertEqual(started['mentions'], ['@' + codex])
        done = self.settle(scope, started['id'], until=lambda s: s['state'] in ('completed', 'failed', 'unknown', 'cancelled'))
        self.assertEqual(done['state'], 'completed', done)
        self.assertTrue(done['linked_return']['summary'].startswith('Asked the Codex helper'))
        # The helper works on after the asking run finished; the app keeps pumping until the linked answer is returned.
        settled = self.settle(scope, started['id'], until=lambda s: s['delegations'] and s['delegations'][0]['status'] not in ('dispatched', 'cancelling'))
        delegation = settled['delegations'][0]
        self.assertIn(delegation['id'], done['linked_return']['summary'], 'the tool reply the model saw named this delegation')
        self.assertEqual((delegation['handle'], delegation['status'], delegation['result']['summary']), ('@' + codex, 'returned', 'CODEX FAKE: next Friday is the 18th.'))
        self.assertTrue(delegation['returned_at'])
        texts = [a['text'] for a in settled['activity']]
        self.assertEqual(texts, [f'Asked @{codex} for help (Codex)', f'@{codex} returned an answer'])
        self.assertNotIn(IDENTITY, json.dumps(settled))
        # The real tool contract on the wire: the launch allowed exactly the bridge tool, the reply carried the delegation.
        argv = next(c['argv'] for c in jsonl(claude_home / 'calls.jsonl') if '-p' in c['argv'])
        self.assertEqual(argv[argv.index('--allowedTools') + 1], 'mcp__dervo__request_help')
        self.assertNotIn('--safe-mode', argv)
        tool_results = jsonl(claude_home / 'tool-results.jsonl')
        self.assertEqual(len(tool_results), 1)
        reply = json.loads(tool_results[0]['text'])
        self.assertEqual((tool_results[0]['is_error'], reply['ok'], reply['target'], reply['delegation_id']), (False, True, '@' + codex, delegation['id']))
        turn = [m for m in jsonl(codex_home / 'received.jsonl') if m.get('method') == 'turn/start']
        self.assertEqual(len(turn), 1)
        self.assertIn('Please ask @' + codex, turn[0]['params']['input'][0]['text'])
        self.assertTrue(turn[0]['params']['input'][0]['text'].endswith('Task: Which date is next Friday?\n'))
        db = sqlite3.connect(f'file:{self.base / "store" / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT status FROM handoffs').fetchall(), [('returned',)])
        room = 'conversation:' + scope['channelId']
        returned = db.execute("SELECT content FROM messages WHERE room_id=? AND kind='handoff_result'", (room,)).fetchall()
        self.assertEqual(len(returned), 1)
        self.assertIn('CODEX FAKE: next Friday is the 18th.', returned[0][0])
        # The next request in the conversation sees the linked answer in its context, and nothing is asked again.
        self.scenario(claude_home, {'result_text': 'Restating: the 18th.'})
        second = self.ask(scope, 'ask-2', 'So which date?', claude)
        self.assertEqual(self.settle(scope, second['id'], until=lambda s: s['state'] in ('completed', 'failed'))['state'], 'completed')
        prompts = [m['message']['content'] for m in jsonl(claude_home / 'received.jsonl') if m.get('type') == 'user' and isinstance(m['message'].get('content'), str)]
        self.assertIn('CODEX FAKE: next Friday is the 18th.', prompts[-1])
        self.assertEqual(len([m for m in jsonl(codex_home / 'received.jsonl') if m.get('method') == 'turn/start']), 1)
        listed = self.host.dispatch('harness_requests', scope)
        self.assertEqual([(r['state'], len(r['delegations'])) for r in listed], [('completed', 1), ('completed', 0)])

    def test_unlinked_and_private_asks_are_refused_by_the_library_and_shown_as_refusals(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        _, codex_profile, codex_home = self.seed(provider='codex', model=CODEX_MODEL)
        _, stranger_profile, stranger_home = self.seed(provider='codex', model=CODEX_MODEL, identity=IDENTITY)  # same email, not linked
        claude, codex, stranger = claude_name(claude_profile, CLAUDE_MODEL), codex_name(codex_profile, CODEX_MODEL), codex_name(stranger_profile, CODEX_MODEL)
        self.link(claude_profile, codex_profile)
        self.scenario(codex_home, {'result_text': 'CODEX FAKE: unused'})
        self.scenario(stranger_home, {'result_text': 'STRANGER: must never run'})
        # A scripted ask of the unlinked helper: the library refuses it (not registered in this project), nothing is created.
        self.scenario(claude_home, {'tool_calls': [{'arguments': {'target': '@' + stranger, 'purpose': 'Which date?', 'source_refs': ['{first_ref}']}}],
                                    'result_text': 'Tried. {tool_results}'})
        started = self.ask(scope, 'ask-1', 'Try asking the other one.', claude)
        done = self.settle(scope, started['id'], until=lambda s: s['state'] in ('completed', 'failed'))
        self.assertEqual((done['state'], done['delegations']), ('completed', []))
        self.assertEqual(len(done['activity']), 1)
        self.assertTrue(done['activity'][0]['text'].startswith(f'Help from @{stranger} refused:'))
        reply = json.loads(jsonl(claude_home / 'tool-results.jsonl')[-1]['text'])
        self.assertEqual((reply['ok'], reply['error_type']), (False, 'NotFound'))
        self.assertEqual(jsonl(stranger_home / 'calls.jsonl'), [])
        # A private conversation may not hand its text to a helper's shared room: refused by the library's transfer rule.
        private_scope, _, _ = (lambda s: s)(None) or ({'projectId': scope['projectId'], 'channelId': self.host.dispatch('create_conversation', {'projectId': scope['projectId'], 'name': 'Private', 'private': True})['id']}, None, None)
        self.scenario(claude_home, {'tool_calls': [{'arguments': {'target': '@' + codex, 'purpose': 'Which date?', 'source_refs': ['{first_ref}']}}],
                                    'result_text': 'Tried privately. {tool_results}'})
        started = self.ask(private_scope, 'ask-2', 'Ask in private.', claude, mentions=['@' + codex])
        done = self.settle(private_scope, started['id'], until=lambda s: s['state'] in ('completed', 'failed'))
        self.assertEqual((done['state'], done['delegations']), ('completed', []))
        self.assertIn('refused', done['activity'][0]['text'])
        reply = json.loads(jsonl(claude_home / 'tool-results.jsonl')[-1]['text'])
        self.assertEqual((reply['ok'], reply['error_type']), (False, 'PolicyViolation'))
        self.assertEqual([m for m in jsonl(codex_home / 'received.jsonl') if m.get('method') == 'turn/start'], [])

    def test_stop_withdraws_help_in_flight_and_a_restart_replays_nothing(self):
        from harness_claude import helper_name as claude_name
        from harness_codex import helper_name as codex_name
        scope, claude_profile, claude_home = self.seed(provider='claude', model=CLAUDE_MODEL)
        _, codex_profile, codex_home = self.seed(provider='codex', model=CODEX_MODEL)
        claude, codex = claude_name(claude_profile, CLAUDE_MODEL), codex_name(codex_profile, CODEX_MODEL)
        self.link(claude_profile, codex_profile)
        self.scenario(claude_home, {'tool_calls': [{'arguments': {'target': '@' + codex, 'purpose': 'Take your time.', 'source_refs': ['{first_ref}']}}],
                                    'result_text': 'Waiting for the helper.', 'mode': 'hang', 'hang_seconds': 120})
        self.scenario(codex_home, {'mode': 'hang', 'hang_seconds': 120})
        started = self.ask(scope, 'ask-1', 'Ask @' + codex + ' and wait.', claude, mentions=['@' + codex])
        pending = self.settle(scope, started['id'], until=lambda s: s['delegations'] or s['state'] in ('failed', 'unknown'))
        self.assertEqual((pending['state'], pending['delegations'][0]['status']), ('running', 'dispatched'))
        # Let the helper reach its turn before stopping, so the stop exercises the protocol interrupt, not just a signal.
        self.settle(scope, started['id'], until=lambda s: 'turn/start' in [m.get('method') for m in jsonl(codex_home / 'received.jsonl')])
        self.host.dispatch('harness_cancel', {**scope, 'requestId': started['id']})
        stopped = self.settle(scope, started['id'], until=lambda s: s['state'] == 'cancelled' and s['delegations'][0]['status'] == 'cancelled')
        self.assertIsNone(stopped['linked_return'])
        self.assertIn('turn/interrupt', [m.get('method') for m in jsonl(codex_home / 'received.jsonl')])
        launches = lambda home, flag: len([c for c in jsonl(home / 'calls.jsonl') if flag in c['argv']])
        claude_before, codex_before = launches(claude_home, '-p'), launches(codex_home, 'app-server')
        self.close_host(self.host)
        self.host = self.open_host()
        listed = self.host.dispatch('harness_requests', scope)
        self.assertEqual([(r['state'], r['delegations'][0]['status']) for r in listed], [('cancelled', 'cancelled')])
        for _ in range(5):
            self.host.tick()
            time.sleep(.02)
        self.assertEqual((launches(claude_home, '-p'), launches(codex_home, 'app-server')), (claude_before, codex_before), 'nothing replays')
        db = sqlite3.connect(f'file:{self.base / "store" / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT status FROM handoffs').fetchall(), [('cancelled',)])
        self.assertEqual(sorted(r[0] for r in db.execute('SELECT status FROM runs').fetchall()), ['cancelled', 'cancelled'])


if __name__ == '__main__':
    unittest.main()
