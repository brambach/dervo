"""Home's Codex call (home_codex.py) against the fake app-server: what it sends, what it checks first, and the sentence
each failure gives. The fake records every message it receives, so a test sees whether project content went out."""
import json
import os
import platform_os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import home_codex
import platform_os
from project_orchestrator import SCHEMA, SYSTEM
from transport import OwnedProcess, clean_environment

BACKEND = Path(__file__).resolve().parent
IDENTITY = 'dervo-test@example.com'  # the fake's own default account
MODEL = 'codex-fake-model-1'          # and its default catalog entry
PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='


def answer(**fields):
    """An answer in the strict shape Codex gives: optional fields present as null."""
    return {'reply': 'Two threads are waiting on you.', 'looked_at': [], 'mentions': [], 'actions': [], 'offers': [],
            'filing': [], 'workstreams': [], 'log': [], 'rule': '',
            'question': {'kind': None, 'workstream': None, 'ask': '', 'options': []}, **fields}


class Injected(OwnedProcess):
    """The real transport, with events slipped in just before the fake's turn/completed: what a real server would send
    that the fake doesn't (token usage, a web search, a failed turn)."""
    extra, status = [], None

    def read(self, timeout):
        batch = super().read(timeout)
        if not batch:
            return batch
        out = []
        for event in batch:
            if event.get('method') == 'turn/completed':
                scope = {'threadId': event['params']['threadId'], 'turnId': event['params']['turn']['id']}
                out += [dict(extra, params={**scope, **extra.get('params', {})}) for extra in self.extra]
                if self.status:
                    event = dict(event, params=dict(event['params'], turn=dict(event['params']['turn'], **self.status)))
            out.append(event)
        return out


class HomeCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home = base / 'profiles' / 'cx'
        self.home.mkdir(parents=True)
        self.exe = base / 'fake-codex'
        self.exe.write_text(f'#!/bin/sh\nexec {sys.executable} -B {BACKEND / "fake_codex_app_server.py"} "$@"\n')
        self.exe.chmod(self.exe.stat().st_mode | stat.S_IXUSR)
        self.profile = dict(id='cx', provider='codex', home=str(self.home), identity=IDENTITY, method='subscription', category='personal')
        Injected.extra, Injected.status = [], None

    def scenario(self, **values):
        (self.home / 'fake-scenario.json').write_text(json.dumps(values))

    def ask(self, prompt='Snapshot: project p.', images=(), timeout=20, model=MODEL, factory=Injected):
        call = home_codex.Call(self.profile, SYSTEM, SCHEMA, model=model, model_name='Fake Luna')
        return home_codex.ask(call, clean_environment('codex', str(self.home), str(self.home)), prompt, images=images,
                              timeout=timeout, factory=factory, executable=str(self.exe))

    def received(self, method=None):
        path = self.home / 'received.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if method is None or row.get('method') == method]

    def calls(self):
        return [json.loads(line) for line in (self.home / 'calls.jsonl').read_text().splitlines()]

    def fails(self, sentence, **kwargs):
        with self.assertRaises(home_codex.Failed) as caught:
            self.ask(**kwargs)
        self.assertEqual(str(caught.exception), sentence)
        return caught.exception

    # ---------------------------------------------------------------- an answer
    def test_a_schema_answer_comes_back_shaped_like_claudes(self):
        self.scenario(result_text=json.dumps(answer()))
        body, usage = self.ask()
        self.assertEqual(body['reply'], 'Two threads are waiting on you.')
        self.assertEqual(body['question'], {'ask': '', 'options': []}, 'the nulls strict mode asked for come off')
        self.assertIsNone(usage, 'the fake reports no tokens, so nothing is made up')
        [start] = self.received('thread/start')
        self.assertIs(start['params']['ephemeral'], True, 'nothing saved')
        self.assertEqual(start['params']['developerInstructions'], SYSTEM)
        self.assertEqual(start['params']['model'], MODEL)
        self.assertEqual(start['params']['sandbox'], 'read-only')
        [turn] = self.received('turn/start')
        self.assertEqual(turn['params']['input'], [{'type': 'text', 'text': 'Snapshot: project p.'}])
        self.assertEqual(turn['params']['outputSchema'], home_codex.strict(SCHEMA))
        self.assertEqual(turn['params']['model'], MODEL)

    def test_it_runs_in_an_empty_untrusted_folder_with_tools_and_web_search_off(self):
        self.scenario(result_text=json.dumps(answer()))
        self.ask()
        [call] = self.calls()
        folder = call['cwd']
        self.assertTrue(folder.startswith(os.path.join(platform_os.scratch_root(), 'dervo-home-')), folder)  # this account's own, never /private/tmp
        self.assertFalse(os.path.exists(folder), 'the folder goes when the call ends')
        [start] = self.received('thread/start')
        self.assertEqual(start['params']['cwd'], folder, 'never the project folder')
        flags = dict(arg.partition('=')[::2] for arg in call['argv'] if '=' in arg)
        self.assertEqual(flags['web_search'], '"disabled"')
        self.assertEqual(flags['sandbox_mode'], '"read-only"')
        self.assertIn('"shell_tool"=false', flags['features'])
        self.assertIn(json.dumps(folder) + '={"trust_level"="untrusted"}', flags['projects'])
        self.assertEqual(call['argv'][-4:], ['app-server', '--listen', 'stdio://', '--strict-config'])
        self.assertEqual(call['env']['CODEX_HOME'], str(self.home))

    def test_checks_come_before_any_project_content(self):
        self.scenario(result_text=json.dumps(answer()))
        self.ask(prompt='SECRET-SNAPSHOT')
        methods = [row['method'] for row in self.received() if 'method' in row]
        self.assertEqual(methods[:6], ['initialize', 'initialized', 'config/read', 'account/read', 'model/list', 'thread/start'])
        self.assertEqual(methods[6], 'turn/start')
        before = json.dumps(self.received()[:6])
        self.assertNotIn('SECRET-SNAPSHOT', before)

    def test_usage_is_what_codex_reported(self):
        self.scenario(result_text=json.dumps(answer()))
        Injected.extra = [{'method': 'thread/tokenUsage/updated', 'params': {'tokenUsage': {
            'total': {'inputTokens': 9000, 'outputTokens': 250, 'totalTokens': 9250}}}}]
        _, usage = self.ask()
        self.assertEqual(usage, {'input_tokens': 9000, 'output_tokens': 250, 'total_tokens': 9250, 'cost_usd': None})

    # ---------------------------------------------------------------- refusals, each before the turn
    def test_signed_out_says_so_and_sends_nothing(self):
        self.scenario(signed_out=True, result_text=json.dumps(answer()))
        self.fails(home_codex.SIGNED_OUT)
        self.assertEqual(self.received('thread/start') + self.received('turn/start'), [])

    def test_another_account_is_refused_not_used(self):
        self.scenario(account={'type': 'chatgpt', 'email': 'someone-else@example.com', 'planType': 'plus'})
        self.fails(home_codex.OTHER_ACCOUNT)
        self.assertEqual(self.received('turn/start'), [])

    def test_a_plan_setup_wouldnt_take_is_refused(self):
        self.scenario(account={'type': 'chatgpt', 'email': IDENTITY, 'planType': 'free'})
        self.fails(home_codex.PLAN.format('free'))
        self.assertEqual(self.received('turn/start'), [])

    def test_a_model_the_account_doesnt_offer_isnt_swapped_for_another(self):
        self.scenario(catalog=[{'model': 'codex-other', 'hidden': False}], result_text=json.dumps(answer()))
        self.fails(home_codex.model_missing('Fake Luna'))
        self.assertEqual(self.received('thread/start') + self.received('turn/start'), [])

    def test_a_hidden_model_counts_as_missing(self):
        self.scenario(catalog=[{'model': MODEL, 'hidden': True}])
        self.fails(home_codex.model_missing('Fake Luna'))

    def test_settings_codex_didnt_take_stop_it(self):
        self.scenario(config_patch={'web_search': 'cached'})
        error = self.fails(home_codex.SETTINGS.format('Codex effective configuration differs from the launch policy'))
        self.assertIn('nothing was sent', str(error))
        self.assertEqual(self.received('account/read'), [], 'stopped at the settings check')

    def test_no_model_set_sends_nothing(self):
        self.fails(home_codex.NO_MODEL, model='')
        self.assertFalse((self.home / 'calls.jsonl').exists(), 'Codex never started')

    def test_custom_codex_settings_in_the_profile_are_refused(self):
        # Orchestrator.account runs check_profile first; the call itself is never reached. Shown here for the sentence.
        from codex_policy import check_profile
        from dervo_connections import Rejected
        (self.home / 'hooks.json').write_text('{}')
        with self.assertRaises(Rejected):
            check_profile(self.home)

    # ---------------------------------------------------------------- an answer that doesn't come, or can't be read
    def test_a_timeout_says_so(self):
        self.scenario(mode='hang', result_text='{"reply":')
        self.fails(home_codex.NO_ANSWER, timeout=1.5)

    def test_a_malformed_answer_says_so(self):
        self.scenario(result_text='Sure! Here is what I found.')
        self.fails(home_codex.UNREADABLE)

    def test_an_answer_that_isnt_an_object_says_so(self):
        self.scenario(result_text='["reply"]')
        self.fails(home_codex.UNREADABLE)

    def test_a_failed_turn_gives_codexs_reason(self):
        self.scenario(result_text=json.dumps(answer()))
        Injected.status = {'status': 'failed', 'error': {'message': 'You have hit your usage limit.'}}
        self.fails('Codex answered: You have hit your usage limit.')

    def test_a_web_search_ends_the_call(self):
        self.scenario(result_text=json.dumps(answer()))
        Injected.extra = [{'method': 'item/started', 'params': {'item': {'type': 'webSearch', 'id': 'ws1', 'query': 'x'}}}]
        self.fails(home_codex.SEARCHED)

    def test_a_server_that_goes_away_mid_call_says_so(self):
        class Gone(Injected):
            def send(self, message):
                if message.get('method') == 'turn/start':
                    raise BrokenPipeError('the server went away')
                return super().send(message)
        self.scenario(result_text=json.dumps(answer()))
        self.fails(home_codex.STOPPED, factory=Gone)

    def test_a_server_that_wont_start_says_so(self):
        def broken(*args, **kwargs):
            raise OSError('no such file')
        self.fails(home_codex.NOT_STARTED, factory=broken)

    # ---------------------------------------------------------------- images
    def test_images_go_with_the_turn_when_the_model_reads_them(self):
        self.scenario(catalog=[{'model': MODEL, 'hidden': False, 'inputModalities': ['text', 'image']}],
                      result_text=json.dumps(answer()))
        self.ask(images=(PNG,))
        [turn] = self.received('turn/start')
        self.assertEqual(turn['params']['input'], [{'type': 'image', 'url': PNG}, {'type': 'text', 'text': 'Snapshot: project p.'}])

    def test_images_on_a_model_without_them_say_so_before_anything_is_sent(self):
        self.scenario(catalog=[{'model': MODEL, 'hidden': False, 'inputModalities': ['text']}], result_text=json.dumps(answer()))
        self.fails(home_codex.NO_IMAGES.format('Fake Luna'), images=(PNG,))
        self.assertEqual(self.received('thread/start') + self.received('turn/start'), [])


class StrictSchemaTests(unittest.TestCase):
    def walk(self, schema, path='$'):
        if isinstance(schema.get('items'), dict):
            yield from self.walk(schema['items'], path + '[]')
        if isinstance(schema.get('properties'), dict):
            yield path, schema
            for key, value in schema['properties'].items():
                yield from self.walk(value, f'{path}.{key}')

    def test_every_object_requires_every_property_and_forbids_others(self):
        # OpenAI's strict structured outputs refuse a schema where an object leaves a property out of required.
        for path, node in self.walk(home_codex.strict(SCHEMA)):
            self.assertEqual(sorted(node['required']), sorted(node['properties']), path)
            self.assertIs(node.get('additionalProperties'), False, path)

    def test_optional_fields_become_nullable_and_required_ones_dont(self):
        strict = home_codex.strict(SCHEMA)
        action = strict['properties']['actions']['items']['properties']
        self.assertEqual(action['kind']['type'], 'string', 'required in SCHEMA, so never null')
        self.assertEqual(action['thread']['type'], ['string', 'null'])
        log = strict['properties']['log']['items']['properties']
        self.assertEqual(log['about']['enum'], ['dervo', 'project', None])
        self.assertEqual(log['item']['type'], ['integer', 'null'])
        self.assertEqual(strict['properties']['reply']['type'], 'string')

    def test_schema_itself_is_left_alone(self):
        before = json.dumps(SCHEMA, sort_keys=True)
        home_codex.strict(SCHEMA)
        self.assertEqual(json.dumps(SCHEMA, sort_keys=True), before, 'Claude still gets SCHEMA as it was')

    def test_optional_fields_say_null_when_they_dont_apply(self):
        strict = home_codex.strict(SCHEMA)
        action = strict['properties']['actions']['items']['properties']
        self.assertTrue(action['context_quote']['description'].endswith(home_codex.NULL_WHEN))
        self.assertFalse(action['quote']['description'].endswith(home_codex.NULL_WHEN), 'a required field is never null')
        self.assertEqual(strict['properties']['log']['items']['properties']['item']['description'][-len(home_codex.NULL_WHEN):], home_codex.NULL_WHEN)

    def test_placeholders_in_optional_fields_come_off_and_required_ones_stay(self):
        # 2 Oct, gpt-6-luna: an optional field it has to fill but has nothing for. Only an optional field reads this way.
        raw = answer(rule='', actions=[{'kind': 'start', 'thread': 'none', 'title': 'Retry button', 'workstream': 'N/A',
                                        'focus': 'retry button', 'context_quote': '', 'quote': 'Add a retry button'}],
                     question={'kind': None, 'workstream': '-', 'ask': '', 'options': []})
        body = home_codex.loose(raw, SCHEMA)
        self.assertEqual(body['actions'], [{'kind': 'start', 'title': 'Retry button', 'focus': 'retry button', 'quote': 'Add a retry button'}])
        self.assertEqual(body['question'], {'ask': '', 'options': []}, 'ask is required, so its "" stays')
        self.assertEqual(body['rule'], '', 'required at the top, so it stays')
        self.assertEqual(home_codex.loose({'thread': 'none'}), {'thread': 'none'}, 'without the schema only nulls go')

    def test_loose_drops_nulls_at_every_depth_and_keeps_falsy_values(self):
        self.assertEqual(home_codex.loose({'a': None, 'b': [{'c': None, 'd': 0}], 'e': '', 'f': False}),
                         {'b': [{'d': 0}], 'e': '', 'f': False})


if __name__ == '__main__':
    unittest.main()
