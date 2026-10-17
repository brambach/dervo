"""Synthetic protocol tests only. No live provider or model evidence."""
import importlib
import json
import os
import unittest
from adapters import CodexAdapter
from codex_policy import SANDBOX, for_folder
from codex_coding_policy import POLICY
from dervo_connections import Rejected

# Absolute on this system: codex_policy refuses a relative folder, and a POSIX path isn't absolute on Windows (every
# module that imports this one stopped there on Windows).
SYNTHETIC = 'C:\\' if os.name == 'nt' else '/private/tmp/'
BINDING = dict(provider='codex', identity='synthetic@example.test', category='personal',
               home=SYNTHETIC + 'synthetic-home', cwd=SYNTHETIC + 'synthetic-project',
               model='synthetic-model', session_id=None)
SID = '00000000-0000-4000-8000-000000000001'
INIT = {'userAgent': 'dervo/0.156.1 (synthetic peer)', 'codexHome': BINDING['home']}
LAUNCHED = for_folder(POLICY, BINDING['cwd'])  # the launch for BINDING, with its folder marked untrusted
CONFIG = {'config': LAUNCHED, 'layers': [{'name': {'type': 'sessionFlags'}, 'config': LAUNCHED}]}


class CodingProtocolTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.find_spec('codex_coding')
        self.assertIsNotNone(spec, 'Separate coding adapter is missing')
        return importlib.import_module('codex_coding')

    def make(self, session_id=None, *, full_access=False):
        m = self.module()
        self.sent, self.events, self.checks = [], [], []
        self.approved = True
        binding = {**BINDING, 'session_id': session_id}
        def authorize(task, request):
            self.checks.append((task, request))
            return self.approved
        task = m.TrustedTaskBinding('synthetic-task', binding, authorize, full_access=full_access)
        a = m.CodexCodingAdapter(binding, self.sent.append,
                                 lambda k, d: self.events.append((k, d)), task=task)
        return a

    def preflight(self, a):
        a.connect()
        for result in [INIT, CONFIG,
                       {'account': {'type': 'chatgpt', 'email': BINDING['identity'], 'planType': 'plus'}},
                       {'data': [{'model': BINDING['model'], 'hidden': False}], 'nextCursor': None}]:
            a.receive({'id': self.sent[-1]['id'], 'result': result})
        return self.sent[-1]

    def ready(self, session_id=None):
        a = self.make(session_id)
        self.preflight(a)
        a.receive({'id': self.sent[-1]['id'], 'result': {
            'model': BINDING['model'], 'cwd': BINDING['cwd'], 'modelProvider': 'openai',
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user', 'sandbox': SANDBOX,
            'instructionSources': [], 'thread': {'id': SID, 'turns': []}}})
        return a

    def test_coding_opt_in_after_preflight_and_chat_is_unchanged(self):
        a = self.make()
        request = self.preflight(a)
        self.assertTrue(self.sent[0]['params']['capabilities']['experimentalApi'])
        self.assertEqual([m.get('method') for m in self.sent],
                         ['initialize', 'initialized', 'config/read', 'account/read', 'model/list', 'thread/start'])
        self.assertEqual([t['name'] for t in request['params']['dynamicTools']],
                         ['dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests'])
        self.assertEqual(request['params']['sandbox'], 'read-only')
        sent = []
        chat = CodexAdapter(BINDING, sent.append, lambda *_: None)
        chat.connect()
        self.assertFalse(sent[0]['params']['capabilities']['experimentalApi'])
        self.assertNotIn('dynamicTools', chat._thread_params())

    def test_full_access_verifies_launch_and_records_native_activity_then_stops_on_revocation(self):
        from codex_coding_policy import policy_for
        a = self.make(full_access=True)
        policy = for_folder(policy_for(True), BINDING['cwd'])
        a.connect()
        for result in (INIT, {'config': policy, 'layers': [{'name': {'type': 'sessionFlags'}, 'config': policy}]},
                       {'account': {'type': 'chatgpt', 'email': BINDING['identity'], 'planType': 'plus'}},
                       {'data': [{'model': BINDING['model'], 'hidden': False}], 'nextCursor': None}):
            a.receive({'id': self.sent[-1]['id'], 'result': result})
        self.assertEqual(self.sent[-1]['params']['sandbox'], 'danger-full-access')
        a.receive({'id': self.sent[-1]['id'], 'result': {
            'model': BINDING['model'], 'cwd': BINDING['cwd'], 'modelProvider': 'openai',
            'approvalPolicy': 'never', 'approvalsReviewer': 'user', 'sandbox': {'type': 'dangerFullAccess'},
            'instructionSources': [], 'thread': {'id': SID, 'turns': []}}})
        a.begin('Run a project check', 'client')
        self.assertEqual(self.sent[-1]['params']['sandboxPolicy'], {'type': 'dangerFullAccess'})
        self.assertEqual(self.sent[-1]['params']['approvalPolicy'], 'never')
        a.receive({'id': self.sent[-1]['id'], 'result': {'turn': {'id': 'turn-1'}}})
        scope = {'threadId': SID, 'turnId': 'turn-1'}
        for phase in ('started', 'completed'):
            a.receive({'method': f'item/{phase}', 'params': {**scope, 'item': {
                'id': 'command-1', 'type': 'commandExecution', 'command': 'python3 check.py', 'exitCode': 0}}})
        a.receive({'method': 'item/completed', 'params': {**scope, 'item': {
            'id': 'file-1', 'type': 'fileChange', 'status': 'completed', 'changes': [{'path': '/project/check.py'}]}}})
        tools = [data for kind, data in self.events if kind == 'native_tool']
        self.assertEqual([(t['name'], t['phase']) for t in tools], [('Bash', 'started'), ('Bash', 'ended'), ('Edit', 'started'), ('Edit', 'ended')])
        self.assertFalse(tools[1]['is_error'])
        self.assertEqual(tools[2]['path'], '/project/check.py')
        self.approved = False
        with self.assertRaises(Rejected):
            a.receive({'method': 'item/started', 'params': {**scope, 'item': {'id': 'late', 'type': 'commandExecution'}}})
        self.assertEqual(a.state, 'blocked')

    def running(self):
        a = self.ready()
        a.begin('Synthetic task context', 'client')
        a.receive({'id': self.sent[-1]['id'], 'result': {'turn': {'id': 'turn-1'}}})
        return a

    def tool(self, a, **patch):
        return {'id': 'server-1', 'method': 'item/tool/call', 'params': {
            'threadId': a.session_id, 'turnId': a.turn_id, 'callId': 'call-1',
            'tool': 'dervo_read_file', 'arguments': {'path': 'approved.py'}, **patch}}

    def test_stream_pending_real_broker_result_then_continuation(self):
        a = self.running()
        scope = {'threadId': a.session_id, 'turnId': a.turn_id}
        a.receive({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'answer', 'delta': 'Reading'}})
        a.receive(self.tool(a))
        self.assertTrue(hasattr(a, 'pending_tool_requests'), 'Validated asynchronous tool queue is missing')
        pending = a.pending_tool_requests
        self.assertEqual(len(pending), 1)
        request = pending[0]
        self.assertEqual(request.task_id, 'synthetic-task')
        self.assertEqual(dict(request.arguments), {'path': 'approved.py'})
        self.assertFalse(any('result' in m for m in self.sent))
        a.complete_tool_result(request.token, success=True, text='Synthetic broker file bytes')
        self.assertEqual(self.sent[-1], {'id': 'server-1', 'result': {
            'success': True, 'contentItems': [{'type': 'inputText', 'text': 'Synthetic broker file bytes'}]}})
        self.assertEqual(a.pending_tool_requests, ())
        a.receive({'method': 'item/agentMessage/delta', 'params': {**scope, 'itemId': 'answer', 'delta': ' done'}})
        a.receive({'method': 'turn/completed', 'params': {'threadId': a.session_id,
                   'turn': {'id': a.turn_id, 'status': 'completed'}}})
        self.assertEqual(a.state, 'completed')
        self.assertEqual([d['text'] for k, d in self.events if k == 'delta'], ['Reading', ' done'])

    def test_failed_turn_error_object_reaches_the_interface_as_text(self):
        """Codex TurnError is an object. The interface renders this value directly,
        so only its message crosses, and its extras never reach a saved task."""
        a = self.running()
        a.receive({'method': 'turn/completed', 'params': {'threadId': a.session_id, 'turn': {
            'id': a.turn_id, 'status': 'failed', 'error': {
                'message': 'The model ran out of context.',
                'additionalDetails': 'cwd=/Users/someone/secret-project --token=SHOULD-NOT-LEAK',
                'codexErrorInfo': {'kind': 'context_window_exceeded'},
                'misalignment': {'reported': True}}}}})
        terminal = [d for k, d in self.events if k == 'terminal']
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]['state'], 'failed')
        self.assertEqual(terminal[0]['error'], 'The model ran out of context.')
        self.assertIsInstance(terminal[0]['error'], str)
        self.assertNotIn('SHOULD-NOT-LEAK', terminal[0]['error'])
        self.assertNotIn('codexErrorInfo', terminal[0]['error'])

    def test_failed_turn_without_a_message_still_reaches_the_interface_as_text(self):
        a = self.running()
        a.receive({'method': 'turn/completed', 'params': {'threadId': a.session_id, 'turn': {
            'id': a.turn_id, 'status': 'failed', 'error': {'codexErrorInfo': {'kind': 'unknown'}}}}})
        error = [d for k, d in self.events if k == 'terminal'][0]['error']
        self.assertIsInstance(error, str)
        self.assertNotIn('codexErrorInfo', error)

    def test_completed_turn_reports_no_error(self):
        a = self.running()
        a.receive({'method': 'turn/completed', 'params': {'threadId': a.session_id,
                   'turn': {'id': a.turn_id, 'status': 'completed'}}})
        self.assertIsNone([d for k, d in self.events if k == 'terminal'][0]['error'])

    def test_protocol_problems_fail_closed_and_bad_calls_return_errors(self):
        # 16.4 item 4: a wrong thread, turn or call id still ends the run.
        for patch in ({'threadId': 'other'}, {'turnId': 'other'}, {'turnId': None}, {'callId': ''},
                      {'callId': 1}, {'callId': 'x' * 257}):
            with self.subTest(patch=str(patch)[:120]):
                a = self.running()
                with self.assertRaises(Rejected):
                    a.receive(self.tool(a, **patch))
                self.assertEqual(a.pending_tool_requests, ())
                self.assertEqual(a.state, 'blocked')
        # What the model got wrong comes back as an error result it can read, and the run goes on.
        patches = [
            {'tool': 'shell'}, {'tool': []},
            {'arguments': {'path': 'approved.py', 'command': 'bad'}},
            {'arguments': '{"path":"approved.py"}'}, {'arguments': {'path': True}},
            {'arguments': {}},
            {'arguments': {'path': 'a' * 4097}}, {'namespace': 'other'}, {'extra': 1},
            {'tool': 'dervo_write_file', 'arguments': {'path': 'a', 'expected_hash': 'BAD', 'content': ''}},
            {'tool': 'dervo_write_file', 'arguments': {'path': 'a', 'expected_hash': 'a' * 64, 'content': False}},
            {'tool': 'dervo_write_file', 'arguments': {'path': 'a', 'expected_hash': 'a' * 64, 'content': 'x' * 65537}},
            {'tool': 'dervo_run_tests', 'arguments': {'command': 'bad'}},
        ]
        for patch in patches:
            with self.subTest(patch=str(patch)[:120]):
                a = self.running()
                a.receive(self.tool(a, **patch))
                self.assertEqual(a.pending_tool_requests, ())
                self.assertEqual(a.state, 'running')
                self.assertFalse(self.sent[-1]['result']['success'])
                self.assertTrue(self.sent[-1]['result']['contentItems'][0]['text'])
                corrected = self.tool(a, callId='corrected')
                corrected['id'] = 'server-2'
                a.receive(corrected)
                self.assertEqual(len(a.pending_tool_requests), 1)
        a = self.running()
        a.receive(self.tool(a, tool='shell'))
        self.assertIn('This task can use: dervo_edit_file, dervo_read_file', self.sent[-1]['result']['contentItems'][0]['text'])
        a.receive(self.tool(a, callId='c2', arguments={'path': 'approved.py', 'command': 'bad'}) | {'id': 's2'})
        self.assertIn('dervo_read_file: unexpected or missing arguments. It takes', self.sent[-1]['result']['contentItems'][0]['text'])
        with self.assertRaises(Rejected):
            a.receive(self.tool(a, tool='shell'))  # the same call again is a replay
        self.assertEqual(a.state, 'blocked')
        for rid in (None, True, {}, [], '', 'x' * 257):
            a = self.running()
            msg = self.tool(a); msg['id'] = rid
            with self.assertRaises(Rejected): a.receive(msg)
            self.assertEqual(a.pending_tool_requests, ())
        for params in (None, [], {}, 'bad'):
            a = self.running()
            with self.assertRaises(Rejected): a.receive({'id': 100, 'method': 'item/tool/call', 'params': params})

    def test_call_limit_asks_the_model_to_finish_then_ends_the_run(self):
        from codex_coding import TOOL_CALL_LIMIT, TOOL_CALL_CEILING
        a = self.running()
        a._seen_calls.update(f'old-{n}' for n in range(TOOL_CALL_LIMIT))
        a.receive(self.tool(a))
        self.assertEqual(a.state, 'running')
        self.assertEqual(a.pending_tool_requests, ())
        self.assertIn('finish up', self.sent[-1]['result']['contentItems'][0]['text'])
        a._seen_calls.update(f'more-{n}' for n in range(TOOL_CALL_CEILING))
        with self.assertRaises(Rejected):
            a.receive(self.tool(a, callId='one-more') | {'id': 'server-9'})
        self.assertEqual(a.state, 'blocked')

    def test_bad_path_is_returned_as_a_tool_error_and_corrected_without_disconnect(self):
        for path in ['/outside', '../outside', './approved.py', 'a//b', 'a\x00b']:
            with self.subTest(path=path):
                a = self.running()
                bad = self.tool(a, arguments={'path':path})
                a.receive(bad)
                self.assertEqual(a.state, 'running')
                self.assertEqual(a.pending_tool_requests, ())
                self.assertFalse(self.sent[-1]['result']['success'])
                self.assertIn('relative to the project', self.sent[-1]['result']['contentItems'][0]['text'])
                corrected = self.tool(a, callId='corrected')
                corrected['id'] = 'server-2'
                a.receive(corrected)
                self.assertEqual(dict(a.pending_tool_requests[0].arguments), {'path':'approved.py'})
                a.complete_tool_result(a.pending_tool_requests[0].token, success=True, text='source')
                with self.assertRaises(Rejected): a.receive(bad)

    def test_bad_path_does_not_return_a_result_after_revocation(self):
        a = self.running()
        self.approved = False
        sent = len(self.sent)
        with self.assertRaises(Rejected): a.receive(self.tool(a, arguments={'path':'/outside'}))
        self.assertEqual(len(self.sent), sent)
        self.assertEqual(a.state, 'blocked')

    def test_replay_call_or_request_id_never_executes_twice(self):
        for done in (False, True):
            for change in ('same', 'call', 'request'):
                a = self.running()
                msg = self.tool(a)
                a.receive(msg)
                if done:
                    a.complete_tool_result(a.pending_tool_requests[0].token, success=True, text='Synthetic')
                if change == 'call': msg['params']['callId'] = 'new-call'
                if change == 'request': msg['id'] = 'new-request'
                with self.assertRaises(Rejected): a.receive(msg)
                self.assertEqual(a.pending_tool_requests, ())

    def test_only_approved_context_and_results_and_no_stale_stop_completion(self):
        a = self.ready(); self.approved = False
        with self.assertRaises(Rejected): a.begin('Must not send', 'client')
        self.assertNotEqual(self.sent[-1].get('method'), 'turn/start')
        a = self.running(); a.receive(self.tool(a)); token = a.pending_tool_requests[0].token
        self.approved = False
        with self.assertRaises(Rejected): a.complete_tool_result(token, success=True, text='Must not return')
        self.assertEqual(a.pending_tool_requests, ())
        a = self.running(); a.receive(self.tool(a)); token = a.pending_tool_requests[0].token
        a.stop()
        self.assertEqual(a.pending_tool_requests, ())
        with self.assertRaises(Rejected): a.complete_tool_result(token, success=True, text='Late')
        with self.assertRaises(Rejected): a.receive(self.tool(a, callId='late'))
        self.assertFalse(any(m.get('result', {}).get('success') is True for m in self.sent))

    def test_results_require_boolean_and_bounded_text(self):
        for success, text in ((1, 'x'), (True, {}), (True, 'x' * 65537)):
            a = self.running(); a.receive(self.tool(a))
            with self.assertRaises(Rejected): a.complete_tool_result(a.pending_tool_requests[0].token, success=success, text=text)

    def test_resume_preserves_uuid_and_trusted_binding_and_effective_gates(self):
        a = self.make(SID)
        request = self.preflight(a)
        self.assertEqual(request['method'], 'thread/resume')
        self.assertEqual(request['params']['threadId'], SID)
        self.assertNotIn('dynamicTools', request['params'])
        for sid in ('not-a-uuid', SID.upper().replace('000000000001', 'ABCDEF000001')):
            with self.assertRaises(Rejected): self.make(sid)
        for result in ({**INIT, 'userAgent': 'dervo/0.154.0 (synthetic)'}, {**INIT, 'codexHome': '/wrong'}):
            a = self.make(); a.connect()
            with self.assertRaises(Rejected): a.receive({'id': 1, 'result': result})
            self.assertEqual(a.state, 'blocked')
            self.assertEqual(len(self.sent), 1)
        a = self.make(); a.connect()
        a.receive({'id': self.sent[-1]['id'], 'result': INIT})
        with self.assertRaises(Rejected): a.receive({'id': self.sent[-1]['id'], 'result': {'config': {}, 'layers': []}})
        self.assertNotIn('account/read', [m.get('method') for m in self.sent])
        m = self.module()
        with self.assertRaises(Rejected): m.TrustedTaskBinding('task', BINDING, None)
        task = m.TrustedTaskBinding('task', BINDING, lambda *_: True)
        with self.assertRaises(Rejected): m.CodexCodingAdapter({**BINDING, 'model': 'other'}, lambda *_: None, lambda *_: None, task=task)
        for key in ('executable', 'argv', 'env', 'protocol', 'factory'):
            b = {**BINDING, key: 'untrusted'}
            with self.assertRaises(Rejected):
                m.CodexCodingAdapter(b, lambda *_: None, lambda *_: None,
                                     task=m.TrustedTaskBinding('task', b, lambda *_: True))

    def test_approved_write_and_tests_and_revoked_pending_access(self):
        a = self.running()
        a.receive(self.tool(a, tool='dervo_write_file', arguments={
            'path': 'approved.py', 'expected_hash': 'a' * 64, 'content': 'real requested bytes'}))
        r = a.pending_tool_requests[0]
        with self.assertRaises(TypeError): r.arguments['path'] = 'outside'
        a.complete_tool_result(r.token, success=False, text='Synthetic broker hash mismatch')
        msg = self.tool(a, callId='call-2', tool='dervo_run_tests', arguments={}); msg['id'] = 100
        a.receive(msg)
        self.assertEqual(a.pending_tool_requests[0].tool, 'dervo_run_tests')
        self.approved = False
        with self.assertRaises(Rejected): _ = a.pending_tool_requests
        self.assertEqual(a.state, 'blocked')

    def test_invalid_new_thread_and_turn_ids_and_terminal_latch(self):
        a = self.make(); self.preflight(a)
        response = {'id': self.sent[-1]['id'], 'result': {
            'model': BINDING['model'], 'cwd': BINDING['cwd'], 'modelProvider': 'openai',
            'approvalPolicy': 'on-request', 'approvalsReviewer': 'user', 'sandbox': SANDBOX,
            'thread': {'id': 'not-a-uuid', 'turns': []}}}
        with self.assertRaises(Rejected): a.receive(response)
        self.assertNotIn('session', [k for k, _ in self.events])
        for tid in (True, 2, '', 'x' * 257):
            a = self.ready(); a.begin('Synthetic', 'client')
            with self.assertRaises(Rejected):
                a.receive({'id': self.sent[-1]['id'], 'result': {'turn': {'id': tid}}})
        a = self.running()
        with self.assertRaises(Rejected): a.receive(self.tool(a, threadId='other'))  # 16.4: an unknown tool is an error result now
        with self.assertRaises(Rejected):
            a.receive({'method': 'turn/started', 'params': {'threadId': SID, 'turn': {'id': 'turn-1'}}})
        self.assertEqual(a.state, 'blocked')

    def test_completed_requires_acknowledged_exact_scope(self):
        for phase in ('initializing', 'ready', 'sending', 'running', 'stopping'):
            for scope in ('missing', 'empty', 'wrong-thread', 'wrong-turn', 'valid'):
                with self.subTest(phase=phase, scope=scope):
                    if phase == 'initializing':
                        a = self.make(); a.connect()
                    elif phase in ('ready', 'sending'):
                        a = self.ready()
                        if phase == 'sending': a.begin('Synthetic', 'client')
                    else:
                        a = self.running()
                        if phase == 'stopping': a.stop()
                    params = {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'completed'}}
                    if scope == 'missing':
                        params = {'turn': {'status': 'completed'}}
                        if phase != 'initializing': params['threadId'] = SID
                    elif scope == 'empty': params['turn']['id'] = ''
                    elif scope == 'wrong-thread': params['threadId'] = 'other'
                    elif scope == 'wrong-turn': params['turn']['id'] = 'other'
                    event = {'method': 'turn/completed', 'params': params}
                    if phase in ('running', 'stopping') and scope == 'valid':
                        a.receive(event)
                        self.assertEqual(a.state, 'completed')
                    else:
                        with self.assertRaises(Rejected): a.receive(event)
                        self.assertFalse(any(k == 'terminal' for k, _ in self.events))
                        self.assertEqual(a.state, 'blocked')

    def test_exact_generated_schema_contract_and_builtin_denials(self):
        import json
        from pathlib import Path
        import codex_coding as m
        schema = Path(__file__).resolve().parents[1] / 'schemas/codex-app-server'
        start = json.loads((schema / 'v2/ThreadStartParams.json').read_text())
        resume = json.loads((schema / 'v2/ThreadResumeParams.json').read_text())
        self.assertIn('dynamicTools', start['properties'])
        self.assertNotIn('dynamicTools', resume['properties'])
        spec = start['definitions']['DynamicToolSpec']['oneOf'][0]
        for tool in m.dynamic_tools():
            self.assertTrue(set(spec['required']) <= set(tool) <= set(spec['properties']))
            self.assertEqual(spec['properties']['type']['enum'], [tool['type']])
        a = self.running()
        scope = {'threadId': SID, 'turnId': a.turn_id}
        for method, result in (
            ('item/commandExecution/requestApproval', {'decision': 'decline'}),
            ('item/fileChange/requestApproval', {'decision': 'decline'}),
            ('item/permissions/requestApproval', {'permissions': {}, 'scope': 'turn'})):
            a.receive({'id': 200, 'method': method, 'params': scope})
            self.assertEqual(self.sent[-1]['result'], result)
        a.receive({'id': 201, 'method': 'unknown/execution', 'params': scope})
        self.assertIn('error', self.sent[-1])
        a.receive(self.tool(a))
        a.complete_tool_result(a.pending_tool_requests[0].token, success=False, text='Synthetic failure')
        response_schema = json.loads((schema / 'DynamicToolCallResponse.json').read_text())
        self.assertEqual(set(self.sent[-1]['result']), set(response_schema['required']))


class SyntheticCodingPeer:
    """In-memory app-server protocol peer. Never authenticates or runs a model."""
    def __init__(self, argv, *, cwd, env, native_commands=False):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.native_commands = native_commands
        self.sent, self.queue, self.closed = [], [], False

    def send(self, message):
        self.sent.append(message)
        method, p = message.get('method'), message.get('params', {})
        if method == 'initialize': result = {**INIT, 'codexHome': self.env['CODEX_HOME']}
        elif method == 'initialized': return
        elif method == 'config/read':
            from test_codex_policy import config_read
            result = config_read(self.argv)
        elif method == 'account/read': result = {'account': {'type': 'chatgpt', 'email': BINDING['identity'], 'planType': 'plus'}}
        elif method == 'model/list': result = {'data': [{'model': BINDING['model'], 'hidden': False}], 'nextCursor': None}
        elif method in ('thread/start', 'thread/resume'):
            result = {'model': BINDING['model'], 'cwd': p['cwd'], 'modelProvider': 'openai',
                      'approvalPolicy': 'on-request', 'approvalsReviewer': 'user', 'sandbox': SANDBOX,
                      'thread': {'id': SID, 'turns': []}}
        elif method == 'turn/start':
            self.queue.extend([{'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}},
                {'id': 'tool-rpc', 'method': 'item/tool/call', 'params': {
                    'threadId': SID, 'turnId': 'turn-1', 'callId': 'tool-call',
                    'tool': 'dervo_run_tests', 'arguments': {}}}])
            return
        elif method == 'turn/steer':
            # app-server: a steer names the active turn; a mismatch or an ended turn is an error.
            if message['params'].get('expectedTurnId') != 'turn-1' or getattr(self, 'steer_error', False):
                self.queue.append({'id': message['id'], 'error': {'code': -32600, 'message': 'no active turn to steer'}})
                return
            result = {'turnId': 'turn-1'}
        elif method == 'turn/interrupt':
            result = {}
            self.queue.append({'method': 'turn/completed', 'params': {
                'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'interrupted'}}})
        elif message.get('id') == 'tool-rpc':
            self.queue.extend([{'method': 'item/agentMessage/delta', 'params': {
                'threadId': SID, 'turnId': 'turn-1', 'itemId': 'answer', 'delta': 'Synthetic continuation'}},
                {'method': 'turn/completed', 'params': {
                    'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'completed'}}}])
            return
        else: raise AssertionError(method)
        self.queue.append({'id': message['id'], 'result': result})

    def read(self, timeout):
        if not self.queue: raise TimeoutError()
        return [self.queue.pop(0)]

    def close(self): self.closed = True


class TrustingCodexPeer(SyntheticCodingPeer):
    """SyntheticCodingPeer with Codex's project trust, as measured on the bundled 0.154.0 on 23 Sep and 0.156.1 on 25 Sep
    with a throwaway home (no sign-in, no turn). thread/start with a danger-full-access or workspace-write sandbox, for a folder
    whose trust isn't set, writes [projects."<folder>"] trust_level = "trusted" into CODEX_HOME/config.toml. config/read
    reports that file as the user layer, the -c projects table over it, and a repo's .codex/config.toml as a project
    layer that's disabled unless the folder is trusted."""
    repo_config = None  # a repo .codex/config.toml, when the test gives the folder one

    def user_config(self):
        import tomllib
        from pathlib import Path
        path = Path(self.env['CODEX_HOME']) / 'config.toml'
        return tomllib.loads(path.read_text()) if path.exists() else {}

    def projects(self):
        from test_codex_policy import config_read
        return {**self.user_config().get('projects', {}), **config_read(self.argv)['config'].get('projects', {})}

    def send(self, message):
        from pathlib import Path
        from test_codex_policy import config_read
        method, p = message.get('method'), message.get('params', {})
        trust = (self.projects().get(p.get('cwd')) or {}).get('trust_level')
        if method == 'config/read':
            self.sent.append(message)
            layers = [{'name': {'type': 'user', 'file': str(Path(self.env['CODEX_HOME']) / 'config.toml'), 'profile': None},
                       'config': self.user_config()}]
            if self.repo_config is not None:
                layer = {'name': {'type': 'project', 'dotCodexFolder': p['cwd'] + '/.codex'}, 'config': self.repo_config}
                if trust != 'trusted':
                    layer['disabledReason'] = 'To load project-local config, add it as a trusted project.'
                layers.insert(0, layer)
            result = config_read(self.argv, *layers)
            if self.projects():
                result['config']['projects'] = self.projects()
            self.queue.append({'id': message['id'], 'result': result})
            return
        if method == 'thread/start' and p.get('sandbox') == 'danger-full-access':
            if trust is None:
                with open(Path(self.env['CODEX_HOME']) / 'config.toml', 'a') as handle:
                    handle.write(f'[projects.{json.dumps(p["cwd"])}]\ntrust_level = "trusted"\n')
            self.sent.append(message)
            self.queue.append({'id': message['id'], 'result': {'model': p['model'], 'cwd': p['cwd'], 'modelProvider': 'openai',
                'approvalPolicy': 'never', 'approvalsReviewer': 'user', 'sandbox': {'type': 'dangerFullAccess'},
                'thread': {'id': SID, 'turns': []}}})
            return
        super().send(message)


class CodingPumpProtocolTests(unittest.TestCase):
    def full_access_run(self, home, repo_config=None):
        """One full-access run on ``home`` up to Codex starting the turn, then closed. Returns the run."""
        from unittest.mock import patch
        import codex_coding as m
        binding = {**BINDING, 'home': home}
        task = m.TrustedTaskBinding('synthetic-task', binding, lambda *_: True, full_access=True)
        peer = type('Peer', (TrustingCodexPeer,), {'repo_config': repo_config})
        with patch.object(m, 'OwnedProcess', peer):
            turn = m.CodexCodingTurn(binding, task=task, context=lambda: ('Synthetic', 'client'), emit=lambda *_: None)
        self.addCleanup(turn.close)
        for _ in range(10):
            turn.tick()
            if turn.closed or turn.adapter.state == 'running':
                break
        turn.close()
        return turn

    def test_a_full_access_run_leaves_no_trust_in_the_profile(self):
        import tempfile
        from pathlib import Path
        from codex_policy import check_profile
        home = self.enterContext(tempfile.TemporaryDirectory())
        for repo_config in (None, {'model_reasoning_effort': 'minimal'}):
            run = self.full_access_run(home, repo_config)
            self.assertEqual((run.adapter.state, run.error), ('running', None))
            self.assertEqual(run.process.sent[-1]['params']['sandboxPolicy'], {'type': 'dangerFullAccess'})
            self.assertFalse((Path(home) / 'config.toml').exists())
            check_profile(home)

    def test_a_profile_an_earlier_full_access_run_trusted_still_runs(self):
        """The founder's case (task fb5aa438): an earlier build's full-access run had Codex write project trust into the
        profile, and every later run, sign-in and account check was refused."""
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from codex_policy import check_profile
        from dervo_connections import Rejected
        home = self.enterContext(tempfile.TemporaryDirectory())
        config = Path(home) / 'config.toml'
        earlier = lambda policy, cwd: dict(policy)  # the launch before 23 Sep passed no projects table
        with patch('codex_policy.for_folder', earlier), patch('codex_coding_policy.for_folder', earlier):
            run = self.full_access_run(home)
            self.assertEqual((run.adapter.state, run.error), ('running', None))
            written = config.read_text()
            self.assertEqual(written, '[projects."/private/tmp/synthetic-project"]\ntrust_level = "trusted"\n')
            check_profile(home)
            self.assertEqual(self.full_access_run(home).adapter.state, 'running')
            # Trusted, the repo's own .codex/config.toml is live, and a live project layer never runs.
            run = self.full_access_run(home, {'model_reasoning_effort': 'minimal'})
            self.assertIn('Custom or managed Codex configuration', run.error)
        # Today's launch marks the folder untrusted over the old entry: the same repo runs with its settings unloaded.
        for repo_config in (None, {'model_reasoning_effort': 'minimal'}):
            run = self.full_access_run(home, repo_config)
            self.assertEqual((run.adapter.state, run.error), ('running', None))
        self.assertEqual(config.read_text(), written)
        check_profile(home)
        config.write_text('notify = ["/bin/sh"]\n' + written)
        with self.assertRaisesRegex(Rejected, 'needs review: config.toml'):
            self.full_access_run(home)

    def make_turn(self, peer=SyntheticCodingPeer, limits=None):
        import tempfile
        from unittest.mock import patch
        import codex_coding as m
        home = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(m, 'OwnedProcess', peer))
        binding = {**BINDING, 'home': home}
        task = m.TrustedTaskBinding('synthetic-task', binding, lambda *_: True, limits=limits)
        self.events = []
        turn = m.CodexCodingTurn(binding, task=task, context=lambda: ('Synthetic', 'client'),
                                 emit=lambda k, d: self.events.append((k, d)))
        self.addCleanup(turn.close)
        return turn

    def test_project_limits_end_a_quiet_or_long_codex_run_as_a_limit(self):
        """Beta.17.1: Codex takes the project's silence limit (it had a fixed 30 minutes) and gets a length cap. With no
        Dervo tool call in flight, Dervo knows it ended the run: failed at that limit, not an unknown outcome."""
        import time
        from unittest.mock import patch
        # The synthetic peer stands in for Codex, so the bundled runtime (gitignored, absent in a fresh worktree) isn't run.
        self.enterContext(patch('codex_coding_policy.executable', lambda: '/usr/bin/false'))
        class Quiet(SyntheticCodingPeer):
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.append({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})
                    return
                super().send(message)
        def started(limits):
            turn = self.make_turn(peer=Quiet, limits=limits)
            for _ in range(20):
                turn.tick()
                if turn.begun and turn.adapter.state == 'running':
                    return turn
            self.fail('the run never began')
        turn = self.make_turn(peer=Quiet)
        self.assertEqual((turn.idle_seconds, turn.run_seconds), (7200, 86400), 'the defaults when the project chose none')
        quiet = started({'idle_seconds': 1800, 'run_seconds': None})
        self.assertIsNone(quiet.hard_deadline)
        quiet.deadline = time.monotonic() - 1
        quiet.tick()
        end = [d for k, d in self.events if k == 'terminal'][-1]
        self.assertEqual((end['state'], end['host_stop'], end['limit'], end['not_started']), ('failed', True, 'idle', False))
        self.assertEqual(end['error'], 'Codex sent nothing for 30 minutes, so Dervo stopped the run.')
        self.assertIsInstance(end['last_seen'], float)
        long = started({'idle_seconds': 1800, 'run_seconds': 43200})
        long.hard_deadline = time.monotonic() - 1
        long.tick()
        end = [d for k, d in self.events if k == 'terminal'][-1]
        self.assertEqual((end['state'], end['limit']), ('failed', 'hard'))
        # A Dervo tool call still waiting: its result may or may not have landed, so the outcome stays unknown.
        busy = self.make_turn()
        for _ in range(20):
            busy.tick()
            if busy.adapter.pending_tool_requests:
                break
        busy.deadline = time.monotonic() - 1
        busy.tick()
        end = [d for k, d in self.events if k == 'terminal'][-1]
        self.assertEqual((end['state'], end['host_stop']), ('uncertain', False))

    def test_partial_start_write_is_uncertain_without_retry(self):
        for error in (BrokenPipeError, TimeoutError):
            with self.subTest(error=error.__name__):
                turn = self.make_turn()
                send = turn.process.send
                attempted = []
                def partial_send(message):
                    if message.get('method') == 'turn/start':
                        attempted.append(message)
                        raise error('Synthetic partial write')
                    send(message)
                turn.process.send = partial_send
                for _ in range(10): turn.tick()
                self.assertEqual([d['state'] for k, d in self.events if k == 'terminal'], ['uncertain'])
                self.assertTrue(turn.begun)
                self.assertTrue(turn.closed)
                self.assertTrue(turn.process.closed)
                self.assertEqual(len(attempted), 1)

    def test_a_second_stop_changes_nothing(self):
        """Run-lifecycle map, finding 3: a second Stop while the first was still being honoured used to end the run
        as "outcome unknown"."""
        class Slow(SyntheticCodingPeer):
            def send(peer, message):
                if message.get('method') == 'turn/interrupt':
                    peer.interrupts = getattr(peer, 'interrupts', 0) + 1
                    peer.queue.append({'id': message['id'], 'result': {}})  # acknowledged; the turn takes a while to end
                    return
                super().send(message)
        turn = self.make_turn(Slow)
        for _ in range(10): turn.tick()
        self.assertEqual(turn.adapter.state, 'running')
        turn.stop()
        turn.stop()
        self.assertEqual([k for k, _ in self.events if k in ('stopping', 'terminal')], ['stopping'], 'the second Stop adds nothing')
        self.assertEqual(turn.process.interrupts, 1, 'the interrupt is sent once')
        self.assertTrue(turn.stopping and not turn.closed)
        turn.process.queue.append({'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'interrupted'}}})
        for _ in range(10): turn.tick()
        self.assertEqual([d['state'] for k, d in self.events if k == 'terminal'], ['interrupted'], 'the first Stop ends the run cleanly')
        self.assertTrue(turn.closed)
        turn.stop()
        self.assertEqual(len([k for k, _ in self.events if k == 'terminal']), 1, 'Stop after the end adds nothing either')
        # Negative control: Stop before the run started still ends it at once, with the same words as before.
        early = self.make_turn()
        early.stop()
        self.assertEqual((self.events[-1][0], self.events[-1][1]['state'], self.events[-1][1]['error']),
                         ('terminal', 'interrupted', 'Stopped before provider acknowledgement'))
        self.assertTrue(early.closed)

    def test_public_transport_failure_cancels_remaining_tokens(self):
        for operation in ('result', 'stop'):
            for error in (BrokenPipeError, TimeoutError):
                with self.subTest(operation=operation, error=error.__name__):
                    turn = self.make_turn()
                    for _ in range(10): turn.tick()
                    first = turn.pending_tool_requests[0]
                    turn.adapter.receive({'id': 'second-rpc', 'method': 'item/tool/call', 'params': {
                        'threadId': SID, 'turnId': 'turn-1', 'callId': 'second-call',
                        'tool': 'dervo_run_tests', 'arguments': {}}})
                    second = turn.pending_tool_requests[1]
                    attempts = []
                    def partial_send(message):
                        attempts.append(message)
                        raise error('Synthetic partial public write')
                    turn.process.send = partial_send
                    try:
                        if operation == 'result':
                            turn.complete_tool_result(first.token, success=True, text='Actual synthetic result')
                        else:
                            turn.stop()
                    except (BrokenPipeError, TimeoutError):
                        pass
                    self.assertTrue(turn.closed)
                    self.assertTrue(turn.process.closed)
                    self.assertEqual(turn.adapter._tools, {})
                    self.assertEqual([d['state'] for k, d in self.events if k == 'terminal'], ['uncertain'])
                    cancelled = [t for k, d in self.events if k == 'tools_cancelled' for t in d['tokens']]
                    self.assertIn(second.token, cancelled)
                    with self.assertRaises(Rejected):
                        turn.complete_tool_result(first.token, success=True, text='No replay')
                    turn.stop()
                    turn.tick()
                    self.assertEqual(len(attempts), 1)

    def test_host_emit_failure_still_closes_and_cancels(self):
        from pathlib import Path
        for operation in ('tick', 'stop-before-start', 'stop-running', 'result-write', 'stop-write'):
            with self.subTest(operation=operation):
                turn = self.make_turn()
                if operation != 'stop-before-start':
                    for _ in range(10): turn.tick()
                neutral = turn.neutral.name
                def broken_emit(kind, data):
                    if operation == 'stop-write' and kind != 'terminal':
                        return
                    raise RuntimeError('Synthetic host emit failure')
                turn.emit = turn.adapter.emit = broken_emit
                if operation in ('result-write', 'stop-write'):
                    def broken_send(message): raise BrokenPipeError('Synthetic broken write')
                    turn.process.send = broken_send
                if operation == 'tick': turn.process.read = lambda timeout: None
                try:
                    if operation == 'tick': turn.tick()
                    elif operation == 'result-write':
                        turn.complete_tool_result(turn.pending_tool_requests[0].token, success=True, text='Synthetic')
                    else: turn.stop()
                except RuntimeError:
                    pass
                self.assertTrue(turn.closed)
                self.assertTrue(turn.process.closed)
                self.assertEqual(turn.adapter._tools, {})
                self.assertFalse(Path(neutral).exists())

    def test_terminal_batch_and_revocation_cleanup(self):
        import tempfile
        from unittest.mock import patch
        import codex_coding as m
        for revoke in (False, True):
            with tempfile.TemporaryDirectory() as home, patch.object(m, 'OwnedProcess', SyntheticCodingPeer):
                approved = [True]
                binding = {**BINDING, 'home': home}
                task = m.TrustedTaskBinding('synthetic-task', binding, lambda *_: approved[0])
                events = []
                turn = m.CodexCodingTurn(binding, task=task, context=lambda: ('Synthetic', 'client'),
                                          emit=lambda k, d: events.append((k, d)))
                try:
                    for _ in range(10): turn.tick()
                    request = turn.pending_tool_requests[0]
                    if revoke:
                        approved[0] = False
                        turn.tick()
                        self.assertTrue(turn.closed)
                        with self.assertRaises(Rejected): turn.complete_tool_result(request.token, success=True, text='Late')
                        self.assertIn('tools_cancelled', [k for k, _ in events])
                    else:
                        turn.stop()
                        turn.process.read = lambda timeout: [
                            {'method': 'turn/completed', 'params': {'threadId': SID,
                             'turn': {'id': 'turn-1', 'status': 'interrupted'}}},
                            {'id': turn.adapter.sequence, 'result': {}}]
                        turn.tick()
                        self.assertEqual([d['state'] for k, d in events if k == 'terminal'], ['interrupted'])
                        self.assertIsNone(turn.error)
                finally: turn.close()

    def test_async_pump_fixed_launch_real_test_result_and_stop(self):
        import tempfile
        import time
        import subprocess
        import sys
        from unittest.mock import patch
        import codex_coding as m
        from codex_coding_policy import argv
        self.assertTrue(hasattr(m, 'CodexCodingTurn'), 'Separate asynchronous coding pump is missing')
        for cancel in (False, True):
            with tempfile.TemporaryDirectory() as home, patch.object(m, 'OwnedProcess', SyntheticCodingPeer):
                binding = {**BINDING, 'home': home}
                task = m.TrustedTaskBinding('synthetic-task', binding, lambda *_: True)
                events, contexts = [], []
                def context():
                    contexts.append(True)
                    return ('Synthetic approved task', 'client')
                turn = m.CodexCodingTurn(binding, task=task, context=context,
                                          emit=lambda k, d: events.append((k, d)))
                self.assertEqual(turn.process.argv, argv(cwd=BINDING['cwd']))
                self.assertEqual(turn.process.env['CODEX_HOME'], home)
                self.assertEqual(contexts, [])
                try:
                    for _ in range(10): turn.tick()
                    self.assertEqual(contexts, [True])
                    req = turn.pending_tool_requests[0]
                    start = time.monotonic()
                    for _ in range(100): turn.tick()
                    self.assertLess(time.monotonic() - start, 1)
                    self.assertFalse(turn.closed)
                    if cancel:
                        turn.stop()
                        with self.assertRaises(Rejected): turn.complete_tool_result(req.token, success=True, text='Late')
                        for _ in range(4): turn.tick()
                        self.assertEqual(turn.adapter.state, 'interrupted')
                        self.assertIn('tools_cancelled', [k for k, _ in events])
                    else:
                        # Actual local Python unittest execution, not a fabricated tool result.
                        result = subprocess.run([sys.executable, '-m', 'unittest', 'test_local_codex', '-q'],
                                                capture_output=True, text=True, timeout=10)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        output = result.stdout + result.stderr
                        turn.complete_tool_result(req.token, success=result.returncode == 0, text=output)
                        for _ in range(4): turn.tick()
                        reply = next(x for x in turn.process.sent if x.get('id') == 'tool-rpc')
                        self.assertEqual(reply['result']['contentItems'][0]['text'], output)
                        self.assertEqual(turn.adapter.state, 'completed')
                    self.assertTrue(turn.closed)
                    self.assertTrue(turn.process.closed)
                finally: turn.close()


if __name__ == '__main__':
    unittest.main()
