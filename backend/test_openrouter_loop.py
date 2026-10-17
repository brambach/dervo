"""Real local Cline child with synthetic model events and disposable host tools."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from codex_coding import dynamic_tools
from dervo_connections import Rejected
from openrouter_loop import Loop
from release_profiles import stage


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dervo-openrouter-loop-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stage(self.root, 'personal-experimental')
        self.runtime = Path(__file__).resolve().parent / 'openrouter_runtime'
        self.node = shutil.which('node')
        # Real dependency/runtime absence must fail this suite, not become a skip.
        self.assertTrue(self.node)
        self.allowed = True
        self.tools = [{key: row[key] for key in ('name', 'description', 'inputSchema')}
                      for row in dynamic_tools()]
        self.task = dict(runId='run-one', taskId='task-one', conversationId='conversation-one',
                         model='openai/gpt-4.1-mini', systemPrompt='', prompt='Improve the README and check it.',
                         tools=self.tools, maxModelCalls=5)

    def launch(self, **kwargs):
        loop = Loop(resources=self.root, node=self.node, runtime_root=self.runtime,
                    task=self.task, authorize=lambda: self.allowed, **kwargs)
        self.addCleanup(loop.close)
        return loop

    def drive(self, loop, handler):
        events = []
        deadline = time.monotonic() + 12
        while not loop.closed and time.monotonic() < deadline:
            for event in loop.poll():
                events.append(event)
                handler(event)
        self.assertTrue(loop.closed, 'Local loop did not terminate')
        return events

    def model(self, loop, request, events):
        for event in events:
            loop.model_event(request['id'], event)
        loop.model_end(request['id'])

    def test_real_loop_reads_writes_checks_and_uses_host_results(self):
        path = self.root / 'README.md'
        path.write_text('Field notes\n')
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        calls, actions = [], []
        loop = self.launch()
        def handle(event):
            if event['kind'] == 'model':
                calls.append(event)
                prior = [part for msg in event['payload']['messages'] for part in msg['content'] if part['type'] == 'tool-result']
                if len(calls) == 1:
                    args, name = {'path': 'README.md'}, 'dervo_read_file'
                elif len(calls) == 2:
                    self.assertEqual(prior[-1]['output'], 'Field notes\n')
                    args, name = {'path': 'README.md', 'expected_hash': original_hash, 'content': 'Field notes for outdoor trips.\n'}, 'dervo_write_file'
                elif len(calls) == 3:
                    self.assertEqual(prior[-1]['output'], 'Write recorded.')
                    args, name = {}, 'dervo_run_tests'
                else:
                    self.assertEqual(prior[-1]['output'], 'README check passed.')
                    return self.model(loop, event, [{'type': 'text-delta', 'text': 'Updated the README. The check passed.'},
                                                    {'type': 'finish', 'reason': 'stop'}])
                self.model(loop, event, [{'type': 'tool-call-delta', 'index': 0, 'toolCallId': 'call-' + str(len(calls)), 'toolName': name, 'input': args},
                                         {'type': 'finish', 'reason': 'tool-calls'}])
            elif event['kind'] == 'tool':
                call = event['payload']; actions.append(call['name'])
                # Disposable host broker. Child cannot read this directory.
                if call['name'] == 'dervo_read_file':
                    self.assertEqual(call['arguments']['path'], path.name)
                    result = path.read_text()
                elif call['name'] == 'dervo_write_file':
                    self.assertEqual(call['arguments']['path'], path.name)
                    self.assertEqual(call['arguments']['expected_hash'], hashlib.sha256(path.read_bytes()).hexdigest())
                    path.write_text(call['arguments']['content'])
                    result = 'Write recorded.'
                else:
                    self.assertIn('outdoor trips', path.read_text())
                    result = 'README check passed.'
                loop.tool_result(event['id'], success=True, text=result)
        events = self.drive(loop, handle)
        self.assertEqual(events[-1]['state'], 'completed')
        self.assertEqual(actions, ['dervo_read_file', 'dervo_write_file', 'dervo_run_tests'])
        self.assertEqual(path.read_text(), 'Field notes for outdoor trips.\n')

    def test_beta_rejects_before_any_process_or_authorization(self):
        stage(self.root, 'private-beta')
        with patch('openrouter_loop._Process') as process:
            with self.assertRaises(ValueError): self.launch()
            process.assert_not_called()

    def test_the_tool_list_is_the_shared_one_with_the_edit_tool_and_read_range(self):
        # Behaviour review N8 (24 Sep): the loop takes exactly codex_coding.dynamic_tools, so beta.15's dervo_edit_file
        # and dervo_read_file's start_line/end_line reach it with no list of its own. Nothing in the app launches this
        # loop for coding yet (conversation_coding.PROVIDERS is Codex and Claude); this keeps it in step if it ever does.
        rows = {row['name']: row for row in self.tools}
        self.assertIn('dervo_edit_file', rows)
        self.assertLessEqual({'start_line', 'end_line'}, set(rows['dervo_read_file']['inputSchema']['properties']))
        with patch('openrouter_loop._Process'):
            self.launch()
            stale = [{**row, 'inputSchema': {**row['inputSchema'], 'properties': {'path': row['inputSchema']['properties']['path']}}}
                     if row['name'] == 'dervo_read_file' else row for row in self.tools]
            for tools in (stale, [row for row in self.tools if row['name'] != 'dervo_edit_file'] + [{**rows['dervo_edit_file'], 'description': 'Edit.'}]):
                with self.subTest(tools=[row['name'] for row in tools]), self.assertRaisesRegex(Rejected, 'unsupported tool contract'):
                    Loop(resources=self.root, node=self.node, runtime_root=self.runtime, task={**self.task, 'tools': tools}, authorize=lambda: True)

    def test_revocation_closes_pending_model_before_reply(self):
        loop = self.launch()
        def handle(event):
            if event['kind'] == 'model':
                self.allowed = False
                with self.assertRaises(Rejected): loop.model_event(event['id'], {'type': 'text-delta', 'text': 'Late'})
        self.drive(loop, handle)

    def test_late_response_cannot_be_delivered_to_another_request(self):
        loop = self.launch()
        def handle(event):
            if event['kind'] == 'model':
                with self.assertRaises(Rejected): loop.model_end(event['id'] + 1)
        self.drive(loop, handle)

    def test_child_cannot_invoke_a_tool_outside_the_advertised_contract(self):
        loop = self.launch()
        def handle(event):
            if event['kind'] == 'model':
                self.model(loop, event, [{'type': 'tool-call-delta', 'toolCallId': 'bad', 'toolName': 'dervo_shell', 'input': {'cmd': 'anything'}},
                                         {'type': 'finish', 'reason': 'tool-calls'}])
            if event['kind'] == 'tool': self.fail('Unadvertised tool escaped')
        events = self.drive(loop, handle)
        self.assertEqual(events[-1]['state'], 'stopped')

    def test_invalid_tool_path_is_refused_by_existing_host_validation(self):
        loop = self.launch()
        def handle(event):
            if event['kind'] == 'model':
                self.model(loop, event, [{'type': 'tool-call-delta', 'toolCallId': 'bad', 'toolName': 'dervo_read_file', 'input': {'path': '../other/secret'}},
                                         {'type': 'finish', 'reason': 'tool-calls'}])
        with self.assertRaises(Rejected): self.drive(loop, handle)
        self.assertTrue(loop.closed)

    def test_actual_child_permissions_deny_files_writes_processes_and_network(self):
        loop = self.launch()
        args = list(loop.process.process.args[:-1])
        loop.close()
        probe = r'''
const fs = require('node:fs');
const cp = require('node:child_process');
const net = require('node:net');
const result = {};
try {fs.readFileSync('/etc/hosts'); result.read = 'allowed';} catch(e) {result.read=e.code;}
try {fs.writeFileSync('permission-probe.tmp','x'); result.write='allowed';} catch(e) {result.write=e.code;}
try {cp.execFileSync('/usr/bin/true'); result.process='allowed';} catch(e) {result.process=e.code;}
const server = net.createServer();
process.on('uncaughtException', e => {result.network=e.code; console.log(JSON.stringify(result));});
server.on('error', e => {result.network=e.code; console.log(JSON.stringify(result));});
server.listen(0,'127.0.0.1',() => {result.network='allowed'; server.close(); console.log(JSON.stringify(result));});
'''
        completed = subprocess.run(args + ['-e', probe], cwd=self.runtime, env={'LANG': 'en_US.UTF-8'},
                                   capture_output=True, text=True, timeout=8)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['read'], 'ERR_ACCESS_DENIED')
        self.assertEqual(result['write'], 'ERR_ACCESS_DENIED')
        self.assertEqual(result['process'], 'ERR_ACCESS_DENIED')
        self.assertIn(result['network'], ('EPERM', 'EACCES', 'ERR_ACCESS_DENIED'))

    def test_terminal_batch_never_returns_actionable_requests_in_either_order(self):
        request = {'kind': 'model', 'id': 1, 'runId': self.task['runId'], 'payload': {
            'model': self.task['model'], 'systemPrompt': '', 'messages': [], 'tools': self.task['tools']}}
        terminal = {'kind': 'terminal', 'runId': self.task['runId'], 'seq': 1, 'state': 'stopped', 'modelCalls': 1}
        for batch in ([request, terminal], [terminal, request]):
            loop = self.launch(); loop.ready = True
            with patch.object(loop.process, 'read', return_value=batch):
                self.assertEqual(loop.poll(), [terminal])
            self.assertTrue(loop.closed)

    def test_real_cline_loop_uses_http_projection_budget_and_host_read(self):
        from unittest.mock import Mock
        import sqlite3
        from uuid import uuid4
        from openrouter_budget import Budget
        from openrouter_request import prepare
        from openrouter_stream import StreamClient
        from test_openrouter_stream import KEY, OWNER, Response, chunk, wire

        path = self.root / 'README.md'
        path.write_text('Fieldnotes is a journal for outdoor trips.\n')
        db = sqlite3.connect(self.root / 'budget.sqlite3')
        self.addCleanup(db.close)
        budget = Budget(db, resources=self.root)
        budget.configure(OWNER, total='0.20', per_request='0.10', revision=0)
        selection = {'modelId': self.task['model'], 'provider': 'openai', 'maxOutputTokens': 1000, 'allowFallbacks': False}
        catalog = {'modelId': self.task['model'], 'endpoints': [{'provider': 'openai', 'selectable': True,
            'contextTokens': 100000, 'budgetContextTokens': 100000, 'maxOutputTokens': 4000,
            'parameters': ['max_tokens', 'tools'], 'pricing': {'prompt': '0.0000004', 'completion': '0.0000016',
            'input_cache_read': '0', 'input_cache_write': '0'}}]}
        posts, actions = [], []
        loop = self.launch()

        def handle(event):
            if event['kind'] == 'model':
                prepared = prepare(event['payload'], selection, catalog, checked_at=100, now=101)
                body = json.loads(prepared.body)
                posts.append(body)
                request, generation = str(uuid4()), 'gen-fixture-' + str(len(posts))
                budget.reserve(OWNER, request, prepared.upper_bound_usd)
                if len(posts) == 1:
                    rows = [chunk({'tool_calls': [{'index': 0, 'id': 'read-one', 'type': 'function',
                        'function': {'name': 'dervo_read_file', 'arguments': '{"path":'}}]}, id=generation),
                        chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"README.md"}'}}]}, id=generation),
                        chunk(reason='tool_calls', id=generation)]
                else:
                    self.assertEqual(json.loads(body['messages'][-1]['content']), {'success': True, 'text': path.read_text()})
                    rows = [chunk({'content': 'Fieldnotes is an outdoor journal.'}, id=generation), chunk(reason='stop', id=generation)]
                rows.append('[DONE]')
                opener = Mock(); opener.open.return_value = Response(wire(*rows))
                finished = StreamClient(self.root, opener=opener).run(KEY, prepared,
                    allowed=lambda: self.allowed, before_send=lambda: budget.mark_sent(OWNER, request),
                    on_generation=lambda g: budget.bind_generation(OWNER, request, g),
                    emit=lambda part: loop.model_event(event['id'], part))
                # Synthetic receipt, not a live charge. Finish is withheld until
                # the host has reconciled this exact request and generation.
                budget.settle(OWNER, request, generation=finished.generation, cost='0.002')
                loop.model_event(event['id'], {'type': 'finish', 'reason': finished.reason})
                loop.model_end(event['id'])
                opener.open.assert_called_once()
            elif event['kind'] == 'tool':
                self.assertEqual(budget.status(OWNER)['reservedUsd'], '0')
                self.assertEqual(event['payload']['name'], 'dervo_read_file')
                self.assertEqual(event['payload']['arguments'], {'path': path.name})
                actions.append('read')
                loop.tool_result(event['id'], success=True, text=path.read_text())
        events = self.drive(loop, handle)
        self.assertEqual(events[-1]['state'], 'completed')
        self.assertEqual(actions, ['read'])
        self.assertEqual(len(posts), 2)
        self.assertEqual(budget.status(OWNER)['usedUsd'], '0.004')


if __name__ == '__main__':
    unittest.main()
