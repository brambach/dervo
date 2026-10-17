"""Coding through the regular conversation composer. Real Host, broker, evidence and confined tests; provider is a double.

No form per request, no proposal, no per-message approval: one explicit project permission (files and the host test
file) granted once, then a natural message becomes a coding task through the existing lifecycle. Nothing here
launches a real provider, reads credentials or touches anything outside the temporary project.
"""
import hashlib
import json
import re
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import thread_folders
from host import Host, ACTOR
from dervo_connections import Rejected

SAMPLE = 'import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n'
# A dependency-free root config naming the one granted TypeScript file. No package is ever installed into the project.
TSCONFIG_ROOT = '{"compilerOptions": {"target": "ES2020", "module": "ESNext", "moduleResolution": "bundler", "strict": true, "noEmit": true, "lib": ["ES2020"]}, "files": ["a.ts"]}\n'


class ConversationCodingTests(unittest.TestCase):
    provider = 'codex'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        # Beta.18: these tests are about a thread in the project folder, as in beta.17. Own branches are
        # test_thread_folders'.
        self.enterContext(patch.object(thread_folders, 'pick', return_value=('project', 'explore')))
        # Each test's scripted peer is the run it launched, so nothing is started ahead of time here. The warm tests in
        # ClaudeConversationCodingTests turn it back on (warm_runs).
        self.enterContext(patch('coding_integration.WARM_WAIT_SECONDS', -1))
        self.host = Host(self.base / 'store')
        self.host_closed = False
        self.addCleanup(self.close_host)
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text(SAMPLE)
        (self.project / 'greeting.py').write_text('def greet(): return "hi"\n')
        (self.project / 'secret_notes.py').write_text('# never in scope\n')
        # The Claude auth double reports the identity test_claude_coding pins; the Codex peer reports its own.
        self.identity = 'personal@example.test' if self.provider == 'claude' else 'synthetic@example.test'
        self.host.store.add_profile('p', self.provider, self.base / 'store/profiles/p', self.identity, 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')
        self.peers = []
        self.starts = 0
        # The scripted peer reads this granted file, writes it back with the appendix and runs the host check.
        self.target, self.appendix = 'test_sample.py', '\n# conversation attempt\n'

    def close_host(self):
        if not self.host_closed:
            self.host_closed = True
            self.host.close()

    def reopen(self):
        self.close_host()
        self.host_closed = False
        self.host = Host(self.base / 'store')

    def grant(self, files=('greeting.py', 'test_sample.py'), test_file='test_sample.py'):
        granted = self.host.dispatch('coding_permission_grant', {**self.scope, 'files': list(files), 'testFile': test_file})
        # This suite tests the explicit human review lifecycle: result approval is configured on, as a migrated store has it.
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        return granted

    def request(self, text, client_id='req-1'):
        return self.host.dispatch('coding_request', {**self.scope, 'clientId': client_id, 'text': text})

    def sample(self):
        return (self.project / 'test_sample.py').read_bytes()

    # --- provider doubles ------------------------------------------------------------------------------------------
    def peer_patch(self, mode, provider=None):
        """Patch the provider process with a scripted peer. Modes: success, none (text answer only), verify_only,
        outside (asks for a file outside the granted scope, then answers), hang (asks to run tests and never finishes),
        overloaded (the provider's side fails before any tool call). ``provider`` patches the other provider's process."""
        test = self
        if (provider or self.provider) == 'codex':
            from test_codex_coding import SyntheticCodingPeer, SID
            class Peer(SyntheticCodingPeer):
                def __init__(peer, *a, **kw):
                    super().__init__(*a, **kw)
                    test.peers.append(peer)
                def tool(peer, name, args, number):
                    peer.queue.append({'id': str(number), 'method': 'item/tool/call', 'params': dict(
                        threadId=SID, turnId='turn-1', callId=str(number), tool=name, arguments=args)})
                def finish(peer, text='Changed greeting.py and ran the tests.'):
                    peer.queue.extend([
                        {'method': 'item/agentMessage/delta', 'params': dict(threadId=SID, turnId='turn-1', itemId='answer', delta=text)},
                        {'method': 'thread/tokenUsage/updated', 'params': dict(threadId=SID, turnId='turn-1', tokenUsage=dict(
                            last=dict(inputTokens=10, cachedInputTokens=0, outputTokens=5, reasoningOutputTokens=0, totalTokens=15),
                            total=dict(inputTokens=120, cachedInputTokens=20, outputTokens=30, reasoningOutputTokens=4, totalTokens=150), modelContextWindow=200000))},
                        {'method': 'account/rateLimits/updated', 'params': dict(rateLimits=dict(planType='plus', primary=dict(usedPercent=42, resetsAt=1800000000, windowDurationMins=300),
                            secondary=dict(usedPercent=12, resetsAt=1800600000, windowDurationMins=10080)))},
                        {'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'completed'}}}])
                def send(peer, message):
                    if message.get('method') == 'turn/start':
                        test.starts += 1
                        peer.sent.append(message)
                        peer.queue.append({'id': message['id'], 'result': {'turn': {'id': 'turn-1'}}})
                        if mode == 'none': peer.finish('The greeting already looks right. No change needed.')
                        elif mode == 'overloaded':
                            peer.queue.append({'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'failed',
                                'error': {'message': 'The server is overloaded.', 'codexErrorInfo': 'serverOverloaded'}}}})
                        elif mode == 'signed_out':
                            peer.queue.append({'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'failed',
                                'error': {'message': 'Sign in again.', 'codexErrorInfo': 'unauthorized'}}}})
                        elif mode == 'usage_limit':
                            peer.queue.append({'method': 'turn/completed', 'params': {'threadId': SID, 'turn': {'id': 'turn-1', 'status': 'failed',
                                'error': {'message': 'You have hit your usage limit.', 'codexErrorInfo': 'usageLimitExceeded'}}}})
                        elif mode == 'verify_only': peer.tool('dervo_run_tests', {}, 3)
                        elif mode == 'outside': peer.tool('dervo_read_file', {'path': 'secret_notes.py'}, 9)
                        elif mode == 'hang': peer.tool('dervo_run_tests', {}, 3)
                        elif mode == 'big': peer.tool('dervo_read_file', test.big_read, 11)
                        else: peer.tool('dervo_read_file', {'path': test.target}, 1)
                    elif message.get('id') in ('11', '12'):
                        text = message['result']['contentItems'][0]['text']
                        test.big_replies.append(json.loads(text))
                        if message['id'] == '11':
                            peer.tool('dervo_edit_file', test.big_edit(json.loads(text)), 12)
                        else:
                            peer.tool('dervo_run_tests', {}, 3)
                    elif message.get('id') == '9':
                        test.outside_reply = json.loads(message['result']['contentItems'][0]['text'])
                        peer.finish('I could not read that file.')
                    elif message.get('id') == '1':
                        result = json.loads(message['result']['contentItems'][0]['text'])
                        peer.tool('dervo_write_file', dict(path=test.target, expected_hash=result['sha256'], content=result['content'] + test.appendix), 2)
                    elif message.get('id') == '2':
                        peer.tool('dervo_run_tests', {}, 3)
                    elif message.get('id') == '3':
                        if mode == 'hang': return
                        peer.finish()
                    else:
                        super().send(message)
            stack = ExitStack()
            # The process is synthetic, so its argv mustn't require a packaged runtime in a source-only checkout.
            stack.enter_context(patch('codex_coding_policy.executable', return_value='/synthetic/codex'))
            stack.enter_context(patch('codex_coding.OwnedProcess', Peer))
            return stack
        from test_claude_coding import SyntheticClaudePeer, verified_auth, CLAUDE
        import claude_coding
        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
            def send(peer, message):
                if message.get('type') == 'user':
                    test.starts += 1
                super().send(message)
            def on_prompt(peer):
                if mode == 'none': peer.finish(text='The greeting already looks right. No change needed.')
                elif mode == 'overloaded':
                    peer.api_error('API Error: Overloaded')
                    peer.finish(ok=False, text='API Error: Overloaded', code=1, spoke=False)
                elif mode == 'signed_out': peer.finish(ok=False, text='Invalid API key. Please run /login', code=1)
                elif mode == 'usage_limit':
                    peer.api_error('Claude AI usage limit reached')
                    peer.finish(ok=False, text='Claude AI usage limit reached', code=1, spoke=False)
                elif mode == 'verify_only': peer.tool('dervo_run_tests', {}, 3)
                elif mode == 'outside': peer.tool('dervo_read_file', {'path': 'secret_notes.py'}, 9)
                elif mode == 'hang': peer.tool('dervo_run_tests', {}, 3)
                elif mode == 'big': peer.tool('dervo_read_file', test.big_read, 11)
                else: peer.tool('dervo_read_file', {'path': test.target}, 1)
            def on_tool_result(peer, mid, result):
                text = result['content'][0]['text']
                if mid in (11, 12):
                    test.big_replies.append(json.loads(text))
                    if mid == 11:
                        peer.tool('dervo_edit_file', test.big_edit(json.loads(text)), 12)
                    else:
                        peer.tool('dervo_run_tests', {}, 3)
                elif mid == 9:
                    test.outside_reply = json.loads(text)
                    peer.finish(text='I could not read that file.')
                elif mid == 1:
                    body = json.loads(text)
                    peer.tool('dervo_write_file', dict(path=test.target, expected_hash=body['sha256'], content=body['content'] + test.appendix), 2)
                elif mid == 2: peer.tool('dervo_run_tests', {}, 3)
                elif mid == 3:
                    if mode == 'hang': return
                    peer.finish(text='Changed greeting.py and ran the tests.')
        stack = ExitStack()
        stack.enter_context(patch.object(claude_coding, 'auth_status', verified_auth))
        stack.enter_context(patch.object(claude_coding, 'executable', lambda: CLAUDE))
        stack.enter_context(patch.object(claude_coding, 'OwnedProcess', Peer))
        return stack

    @staticmethod
    def finished(detail):
        return detail['task']['state'] != 'working'

    @staticmethod
    def verifying(detail):
        return any(t['state'] == 'running' for t in detail['task']['tokens'])

    @staticmethod
    def stopped(detail):
        attempts = detail['evidence']['attempts']
        return detail['task']['state'] != 'working' and bool(attempts) and attempts[-1]['state'] != 'running'

    def settle(self, task_id, *, until=None):
        until = until or self.finished
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})
            if until(detail):
                return detail
            time.sleep(.01)
        self.fail('Synthetic completion timed out')

    def run_request(self, text, mode='success', client_id='req-1'):
        with self.peer_patch(mode):
            started = self.request(text, client_id)
            self.assertIsNotNone(started['id'], started)
            detail = self.settle(started['taskId'])
        return started, detail

    # --- tests -----------------------------------------------------------------------------------------------------
    def test_a_request_without_edit_permission_has_read_only_tools(self):
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['coding']['state'], 'missing')
        self.assertEqual(status['coding']['reading']['state'], 'available')
        with self.peer_patch('none'):
            answer = self.request('Explain the greeting')
            detail = self.settle(answer['taskId'])
        self.assertEqual(detail['task']['state'], 'answered')
        self.assertTrue(detail['task']['read_only'])
        self.assertNotIn('dervo_write_file', self.host.coding.tools_for(detail['task']))
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertEqual(self.starts, 1)
        permission = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(permission['state'], 'missing')
        self.assertEqual(permission['test_candidates'], ['test_sample.py'])

    def test_check_profile_is_selected_once_persists_and_refuses_unavailable_or_missing_runtime(self):
        import check_profiles
        from unittest.mock import patch
        (self.project / 'test_sample.mjs').write_text("import {test} from 'node:test';\ntest('ok', () => {});\n")
        (self.project / 'tsconfig.json').write_text(TSCONFIG_ROOT)
        permission = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual([e['id'] for e in permission['check_profiles']], ['manual-review', 'rust-test', 'rust-check', 'python-unittest', 'node-test', 'typescript-check'])
        self.assertTrue(next(e for e in permission['check_profiles'] if e['id'] == 'typescript-check')['available'],
                        'the TypeScript check is offered; a project without its own compiler is refused at grant, below')
        self.assertIn('test_sample.mjs', permission['test_candidates'])
        self.assertIn('tsconfig.json', permission['test_candidates'], 'the root tsconfig.json is the TypeScript check file')
        # A Python test file under the TypeScript profile, an unknown profile, a Python test file under the Node profile, a Node
        # file under the Python profile, and the TypeScript check in a project with no node_modules/typescript are all refused.
        for args, reason in (({'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'checkProfile': 'typescript-check'}, 'tsconfig.json'),
                             ({'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'checkProfile': 'shell'}, 'Unknown check profile'),
                             ({'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'checkProfile': 'node-test'}, 'root test file'),
                             ({'files': ['greeting.py', 'test_sample.mjs'], 'testFile': 'test_sample.mjs'}, 'root test file'),
                             ({'files': ['greeting.py', 'tsconfig.json'], 'testFile': 'tsconfig.json', 'checkProfile': 'typescript-check'}, 'No TypeScript compiler')):
            with self.subTest(args=args):
                with self.assertRaisesRegex(Rejected, reason):
                    self.host.dispatch('coding_permission_grant', {**self.scope, **args})
        with patch.object(check_profiles, 'NODE_CANDIDATES', ('/nonexistent/node',)):
            with self.assertRaisesRegex(Rejected, 'No trusted Node runtime'):
                self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.mjs'], 'testFile': 'test_sample.mjs', 'checkProfile': 'node-test'})
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'missing', 'a refused grant records nothing')
        # The legacy shape (no checkProfile) is still a Python grant.
        legacy = self.grant()
        self.assertEqual(legacy['check_profile'], 'python-unittest')
        self.assertIn('python-unittest check on test_sample.py', legacy['detail'])
        try:
            check_profiles.node_runtime()
        except Rejected as exc:
            self.skipTest(str(exc))
        granted = self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.mjs'], 'testFile': 'test_sample.mjs', 'checkProfile': 'node-test'})
        self.assertEqual((granted['state'], granted['check_profile'], granted['test_file']), ('granted', 'node-test', 'test_sample.mjs'))
        self.assertIn('node-test check on test_sample.mjs', granted['detail'])
        self.assertIn('No shell, no npm scripts', granted['check_detail'])
        self.reopen()
        again = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual((again['state'], again['check_profile'], again['test_file']), ('granted', 'node-test', 'test_sample.mjs'))
        # A Node runtime that disappears after the grant makes the permission stale with the reason; nothing falls back to Python.
        with patch.object(check_profiles, 'NODE_CANDIDATES', ('/nonexistent/node',)):
            stale = self.host.dispatch('coding_permission', self.scope)
            self.assertEqual(stale['state'], 'stale')
            self.assertIn('No trusted Node runtime', stale['detail'])
        # A request under the Node grant creates a task bound to the Node argv, never a Python one.
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        with patch.object(self.host.coding, 'start', lambda task: None):
            view = self.request('run the node tests', client_id='node-req')
        task = self.host.dispatch('task_detail', {**self.scope, 'taskId': view['taskId']})['task']
        self.assertEqual(task['check_profile'], 'node-test')
        self.assertEqual(task['argv'], [check_profiles.node_runtime(), *check_profiles.node_flags(check_profiles.node_runtime()), 'test_sample.mjs'])

    def test_a_grant_can_allow_several_checks_and_a_request_picks_one(self):
        """Bryce's dogfood goal, 16 September 2026: Dervo's own source is Python, Rust and TypeScript, and a grant allowed
        exactly one check. A grant now lists up to four; a request names the one it wants, or gets the first."""
        import check_profiles
        from unittest.mock import patch
        (self.project / 'test_sample.mjs').write_text("import {test} from 'node:test';\ntest('ok', () => {});\n")
        (self.project / 'lib.rs').write_text('pub fn one() -> i32 { 1 }\n')
        base = {**self.scope, 'files': ['greeting.py', 'test_sample.py', 'test_sample.mjs', 'lib.rs'], 'testFile': 'test_sample.py'}
        for extra, reason in (([{'checkProfile': 'node-test'}], 'checkProfile and testFile'),
                              ([{'checkProfile': 'manual-review', 'testFile': 'test_sample.py'}], 'different automated check'),
                              ([{'checkProfile': 'python-unittest', 'testFile': 'test_sample.py'}], 'different automated check'),
                              ([{'checkProfile': 'node-test', 'testFile': 'test_sample.py'}], 'root test file'),
                              ([{'checkProfile': 'node-test', 'testFile': 'other.mjs'}], 'root test file'),
                              ([{'checkProfile': 'rust-check', 'testFile': 'lib.rs', 'rustToolchain': ''}], 'only on a Mac' if sys.platform.startswith('linux') else 'toolchain folder'),
                              ([{'checkProfile': 'node-test', 'testFile': 'test_sample.mjs'}] * 2, 'different automated check'),
                              ([{'checkProfile': p, 'testFile': f} for p, f in (('node-test', 'test_sample.mjs'), ('typescript-check', 'tsconfig.json'), ('rust-check', 'lib.rs'), ('rust-test', 'lib.rs'))], 'up to 3 entries')):
            with self.subTest(extra=extra), self.assertRaisesRegex(Rejected, reason):
                self.host.dispatch('coding_permission_grant', {**base, 'checks': extra})
        with self.assertRaisesRegex(Rejected, 'cannot be combined'):
            self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.mjs'], 'testFile': None, 'checkProfile': 'manual-review', 'checks': [{'checkProfile': 'node-test', 'testFile': 'test_sample.mjs'}]})
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'missing', 'a refused grant records nothing')
        legacy = self.grant()
        self.assertEqual([(c['profile'], c['test_file'], c['label']) for c in legacy['checks']], [('python-unittest', 'test_sample.py', 'Python unittest')])
        with self.assertRaisesRegex(Rejected, "doesn't include the node-test check"):
            self.host.dispatch('coding_request', {**self.scope, 'clientId': 'pick-1', 'text': 'Run the node tests', 'checkProfile': 'node-test'})
        try:
            check_profiles.node_runtime()
        except Rejected as exc:
            self.skipTest(str(exc))
        granted = self.host.dispatch('coding_permission_grant', {**base, 'checks': [{'checkProfile': 'node-test', 'testFile': 'test_sample.mjs'}]})
        self.assertEqual((granted['state'], granted['check_profile'], granted['test_file']), ('granted', 'python-unittest', 'test_sample.py'))
        self.assertEqual([(c['profile'], c['test_file']) for c in granted['checks']], [('python-unittest', 'test_sample.py'), ('node-test', 'test_sample.mjs')])
        self.assertIn('the python-unittest check on test_sample.py, or the node-test check on test_sample.mjs', granted['detail'])
        self.reopen()
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        with patch.object(self.host.coding, 'start', lambda task: None):
            default = self.request('Fix the greeting', client_id='pick-default')
            node = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'pick-node', 'text': 'Run the node tests', 'checkProfile': 'node-test'})
            with self.assertRaisesRegex(Rejected, "doesn't include the typescript-check check"):
                self.host.dispatch('coding_request', {**self.scope, 'clientId': 'pick-ts', 'text': 'Type check', 'checkProfile': 'typescript-check'})
        tasks = {view['taskId']: self.host.dispatch('task_detail', {**self.scope, 'taskId': view['taskId']})['task'] for view in (default, node)}
        self.assertEqual((tasks[default['taskId']]['check_profile'], tasks[default['taskId']]['argv'][-1]), ('python-unittest', 'test_sample.py'))
        self.assertEqual((tasks[node['taskId']]['check_profile'], tasks[node['taskId']]['argv']), ('node-test', [check_profiles.node_runtime(), *check_profiles.node_flags(check_profiles.node_runtime()), 'test_sample.mjs']))
        # A grant that drops the Node check makes the Node task's continuation refuse, and the Python one still fine.
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
        self.assertIn('no longer permits the node-test check', self.host.coding.conversation.grant_problem(tasks[node['taskId']]))
        self.assertIsNone(self.host.coding.conversation.grant_problem(tasks[default['taskId']]))
        # A Node runtime that disappears makes the whole permission stale, naming the check that failed.
        with patch.object(check_profiles, 'NODE_CANDIDATES', ('/nonexistent/node',)):
            self.host.dispatch('coding_permission_grant', {**base, 'checks': []})
            granted = self.host.dispatch('coding_permission', self.scope)
            self.assertEqual(granted['state'], 'granted', 'without the node check nothing depends on Node')

    def test_typescript_check_needs_the_project_compiler_and_runs_a_conversation_request_in_confinement(self):
        """The TypeScript grant: refused without the project's own in-root node_modules/typescript, refused when that path
        leaves the project, refused for a tsconfig.json that reaches outside; granted with a disposable in-root copy of the
        cached compiler (never the coordinator's checkout path in production); stale with the exact reason when the compiler
        goes away; and a message under the grant writes a TypeScript file and gets real confined tsc evidence bound to the
        compiler's content hashes. No dependency is installed: the project stays dependency-free."""
        import check_profiles
        import os
        import shutil
        from test_check_profiles import cached_compiler, install_compiler, node_available
        try:
            check_profiles.node_runtime()
        except Rejected as exc:
            self.skipTest(str(exc))
        if not cached_compiler():
            self.skipTest('No cached TypeScript compiler to copy into the temporary project')
        (self.project / 'tsconfig.json').write_text(TSCONFIG_ROOT)
        (self.project / 'a.ts').write_text("export function greet(name: string): string { return 'hi ' + name; }\n")
        grant = {'files': ['a.ts', 'tsconfig.json'], 'testFile': 'tsconfig.json', 'checkProfile': 'typescript-check'}
        with self.assertRaisesRegex(Rejected, 'No TypeScript compiler'):
            self.host.dispatch('coding_permission_grant', {**self.scope, **grant})
        # A compiler that resolves outside the project (the pnpm symlink shape of a builder checkout) is refused, never followed.
        outside = self.base / 'outside-typescript'
        install_compiler(outside.parent / 'holder', cached_compiler())  # a real compiler, just not inside the project
        (self.project / 'node_modules').mkdir()
        os.symlink(outside.parent / 'holder/node_modules/typescript', self.project / 'node_modules/typescript')
        with self.assertRaisesRegex(Rejected, 'outside the project'):
            self.host.dispatch('coding_permission_grant', {**self.scope, **grant})
        os.unlink(self.project / 'node_modules/typescript')
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'missing', 'refused grants record nothing')
        install_compiler(self.project)
        (self.project / 'tsconfig.json').write_text('{"extends": "../base.json"}')
        with self.assertRaisesRegex(Rejected, 'extends'):
            self.host.dispatch('coding_permission_grant', {**self.scope, **grant})
        (self.project / 'tsconfig.json').write_text(TSCONFIG_ROOT)
        granted = self.host.dispatch('coding_permission_grant', {**self.scope, **grant})
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        self.assertEqual((granted['state'], granted['check_profile'], granted['test_file']), ('granted', 'typescript-check', 'tsconfig.json'))
        self.assertIn('typescript-check check on tsconfig.json', granted['detail'])
        self.assertIn('TS2307', granted['check_detail'], 'the grant says external packages fail visibly')
        self.reopen()
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'granted')
        # The compiler disappearing after the grant makes the permission stale with the reason; a request then starts nothing.
        shutil.move(self.project / 'node_modules', self.base / 'parked')
        stale = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(stale['state'], 'stale')
        self.assertIn('No TypeScript compiler', stale['detail'])
        with self.peer_patch('success'):
            self.assertEqual(self.request('Add an export', 'no-compiler')['state'], 'needs_permission')
        self.assertEqual(self.starts, 0)
        shutil.move(self.base / 'parked', self.project / 'node_modules')
        # A good change: real confined tsc, exit 0, the compiler bound by content hash on the task and on the evidence.
        self.target, self.appendix = 'a.ts', '\nexport const added: number = 1;\n'
        started, detail = self.run_request('Add an exported constant to a.ts', client_id='ts-good')
        task, attempt = detail['task'], detail['evidence']['attempts'][-1]
        if attempt['state'] == 'blocked' and 'Confinement' in (attempt['blocker'] or ''):
            self.skipTest(attempt['blocker'])
        self.assertEqual(task['check_profile'], 'typescript-check')
        self.assertEqual(task['argv'], [node_available(), *check_profiles.node_flags(node_available()), check_profiles.TS_ENTRY, *check_profiles.TS_ARGS])
        self.assertEqual(task['files'], ['a.ts', 'tsconfig.json'])
        self.assertEqual(task['state'], 'ready_for_review', task['error'])
        self.assertEqual((attempt['state'], attempt['test']['exit_status'], attempt['test']['check_profile']), ('verified', 0, 'typescript-check'), attempt['test'])
        self.assertIn('export const added', (self.project / 'a.ts').read_text())
        compiler = detail['evidence']['compiler']
        self.assertEqual(attempt['test']['compiler'], compiler)
        self.assertEqual(set(compiler['sha256']), set(compiler['files']))
        self.assertTrue(all(len(digest) == 64 for digest in compiler['sha256'].values()))
        self.assertNotIn('content', compiler, 'persisted evidence carries hashes, never compiler bytes')
        self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': attempt['number'], 'decision': 'approved'})
        # A bad change fails visibly in the evidence (TS2322) and is not approvable.
        self.appendix = '\nexport const wrong: number = "not a number";\n'
        _, detail = self.run_request('Break a.ts', client_id='ts-bad')
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.assertNotEqual(attempt['test']['exit_status'], 0)
        self.assertIn('TS2322', attempt['test']['stdout'] + attempt['test']['stderr'])
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId': detail['task']['id'], 'attemptNumber': attempt['number'], 'decision': 'approved'})

    def test_a_grant_that_fails_before_its_checks_reads_as_stale_not_a_crash(self):
        # beta.9: a Rejected raised before the per-check loop (a replaced folder, a bad stored time limit) left the
        # handler reading an unassigned variable, so every update failed with an UnboundLocalError instead of a reason.
        (self.host.root / 'test_sample.py').write_text(SAMPLE)
        self.grant()
        with patch('check_profiles.check_timeout', side_effect=Rejected('Maximum check time must be a whole number from 5 to 120 seconds')):
            permission = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(permission['state'], 'stale')
        self.assertIn('Maximum check time', permission['detail'])

    def test_a_restart_that_renumbers_the_disk_keeps_whole_project_access(self):
        # beta.9: macOS gave the data volume a new device number after a restart, and every whole-project grant read as
        # "the project folder was replaced". The folder's inode is unchanged, so the grant still holds; a new inode doesn't.
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': [], 'testFile': None, 'projectAccess': True, 'checkProfile': 'manual-review'})
        def recorded(identity):
            row = self.host.store.db.execute('SELECT document FROM coding_permissions WHERE project_id=?', ('p',)).fetchone()
            grant = json.loads(row[0])
            grant['root_identity'] = identity(grant['root_identity'])
            self.host.store.db.execute('UPDATE coding_permissions SET document=? WHERE project_id=?', (json.dumps(grant), 'p'))
        recorded(lambda old: [old[0] + 3, old[1]])
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'granted')
        recorded(lambda old: [old[0], old[1] + 1])
        permission = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(permission['state'], 'stale')
        self.assertIn('project folder was replaced', permission['detail'])

    def test_project_permission_is_explicit_bounded_and_revocable(self):
        for files, test_file in ((['missing.py', 'test_sample.py'], 'test_sample.py'), (['greeting.py'], 'test_sample.py'),
                                 (['greeting.py', 'test_sample.py'], 'greeting.py'), ([], 'test_sample.py'),
                                 (['../outside.py', 'test_sample.py'], 'test_sample.py')):
            with self.subTest(files=files):
                with self.assertRaises(Rejected):
                    self.grant(files, test_file)
        (self.host.root / 'test_sample.py').write_text(SAMPLE)
        self.host.store.add_project('protected', self.host.root, 'p', 'synthetic-model')
        self.host.store.add_thread('protected', 'protected', 'protected', 'private', ACTOR)
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_permission_grant', dict(projectId='protected', channelId='protected', files=['test_sample.py'], testFile='test_sample.py'))
        granted = self.grant()
        self.assertEqual((granted['state'], granted['files'], granted['test_file']), ('granted', ['greeting.py', 'test_sample.py'], 'test_sample.py'))
        self.assertEqual(granted['actor'], ACTOR)
        status = self.host.dispatch('conversation_status', self.scope)['coding']
        self.assertEqual((status['state'], status['files']), ('granted', ['greeting.py', 'test_sample.py']))
        # A second conversation in the same project shares the project permission; another project does not.
        self.host.store.add_thread('shared', 'p', 'shared', 'shared', None)
        self.assertEqual(self.host.dispatch('coding_permission', dict(projectId='p', channelId='shared'))['state'], 'granted')
        other = self.base / 'other'
        other.mkdir()
        (other / 'test_sample.py').write_text(SAMPLE)
        self.host.store.add_project('o', other, 'p', 'synthetic-model')
        self.host.store.add_thread('o', 'o', 'o', 'private', ACTOR)
        self.assertEqual(self.host.dispatch('coding_permission', dict(projectId='o', channelId='o'))['state'], 'missing')
        revoked = self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertEqual(revoked['state'], 'missing')
        self.assertEqual(self.host.dispatch('conversation_status', self.scope)['coding']['state'], 'missing')
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['acceptance']['required'], True, 'revoking the grant changes nothing about result approval')
        # A granted file that disappears makes the permission stale, never silently narrower.
        self.grant()
        (self.project / 'greeting.py').unlink()
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'stale')
        with self.peer_patch('success'):
            self.assertEqual(self.request('Fix it')['id'], None)
        self.assertEqual(self.starts, 0)

    def test_a_natural_request_changes_files_with_evidence_through_the_existing_lifecycle(self):
        self.grant()
        before = self.sample()
        started, detail = self.run_request('Please add a comment to test_sample.py and run the tests')
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual(started['taskId'], task['id'])
        self.assertEqual(task['origin'], 'conversation')
        self.assertEqual(task['contract'], 'dervo-dynamic-tools-v1')
        self.assertEqual(task['files'], ['greeting.py', 'test_sample.py'])
        self.assertEqual(self.starts, 1)
        self.assertEqual(task['state'], 'ready_for_review', task['error'])
        self.assertEqual([t['tool'] for t in task['tokens']], ['dervo_read_file', 'dervo_write_file', 'dervo_run_tests'])
        attempt = evidence['attempts'][-1]
        self.assertEqual(attempt['state'], 'verified')
        self.assertEqual(attempt['test']['exit_status'], 0, attempt['test'])
        self.assertIn('Ran 1 test', attempt['test']['stderr'])
        self.assertIn('# conversation attempt', attempt['diff']['test_sample.py'])
        self.assertNotEqual(self.sample(), before, 'the project file really changed')
        self.assertEqual(evidence['approval'], {'scope_version': 1, 'actor': ACTOR})
        self.assertEqual(task['messages'][0]['text'], 'Please add a comment to test_sample.py and run the tests')
        self.assertIn('ran the tests', task['messages'][-1]['text'])
        # The request is linked to its task durably and the status line reports the task, not a form.
        requests = self.host.dispatch('coding_requests', self.scope)
        self.assertEqual([(r['client_id'], r['task_id'], r['state']) for r in requests], [('req-1', task['id'], 'ready_for_review')])
        self.assertEqual(self.host.dispatch('conversation_status', self.scope)['state'], 'ready_for_review')
        # Human result review is the existing one, in the same conversation.
        review = self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': attempt['number'], 'decision': 'approved'})
        self.assertEqual(review['state'], 'idle')
        self.assertEqual(self.host.store.shared_context('p'), [])

    def test_a_text_answer_is_shown_as_text_and_never_as_executed_files(self):
        self.grant()
        before = self.sample()
        _, detail = self.run_request('Does greeting.py look right to you?', mode='none')
        task = detail['task']
        self.assertEqual(task['runs'][-1]['state'], 'completed')
        self.assertEqual((task['state'], task['error']), ('answered', None))
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertEqual(task['tokens'], [])
        self.assertEqual(self.sample(), before)
        self.assertIn('No change needed', task['messages'][-1]['text'])
        self.assertFalse(detail['retry']['available'])
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': 1, 'decision': 'approved'})
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'answered')

    def test_verification_without_writes_records_real_test_evidence_and_changes_nothing(self):
        self.grant()
        before = self.sample()
        _, detail = self.run_request('Just run the tests, change nothing', mode='verify_only')
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual((attempt['kind'], attempt['state'], attempt['diff']), ('verification', 'verified', {}))
        self.assertEqual(attempt['test']['exit_status'], 0)
        self.assertEqual(self.sample(), before)
        self.assertEqual(detail['task']['state'], 'ready_for_review')

    def test_a_file_outside_the_granted_scope_is_refused_by_the_broker(self):
        self.grant()
        notes = (self.project / 'secret_notes.py').read_bytes()
        _, detail = self.run_request('Read secret_notes.py and summarise it', mode='outside')
        self.assertEqual(self.outside_reply, {'error': 'File is outside task scope'})
        self.assertEqual((self.project / 'secret_notes.py').read_bytes(), notes)
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertEqual(detail['task']['state'], 'answered')
        self.assertEqual(detail['broker_audit'][0]['response']['success'], False)

    def test_the_same_request_id_is_idempotent_and_never_starts_twice(self):
        self.grant()
        with self.peer_patch('success'):
            first = self.request('Add a comment and run the tests', 'same-id')
            again = self.request('Add a comment and run the tests', 'same-id')
            self.assertEqual(first['taskId'], again['taskId'])
            self.assertEqual(first['id'], again['id'])
            with self.assertRaises(Rejected):
                self.request('Different text', 'same-id')
            waiting = self.request('A second request while the first is working', 'other-id')
            self.assertEqual((waiting['taskId'], waiting['state']), (None, 'queued'), 'a second message here waits; it never starts a second run')
            self.host.dispatch('coding_queue_cancel', {**self.scope, 'queueId': waiting['id']})
            detail = self.settle(first['taskId'])
            after = self.request('Add a comment and run the tests', 'same-id')
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
        self.assertEqual(self.starts, 1)
        self.assertEqual((after['taskId'], after['state']), (first['taskId'], 'ready_for_review'))
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        for bad in ({'clientId': '', 'text': 'x'}, {'clientId': 'k', 'text': ''}, {'clientId': 'k', 'text': 'x' * 40000}):
            with self.assertRaises(Rejected):
                self.host.dispatch('coding_request', {**self.scope, **bad})

    def _fake_runs(self, *runs):
        from types import SimpleNamespace
        store = self.host.store
        for task_id, thread, name, visibility, owner, origin in runs:
            if not store.db.execute('SELECT 1 FROM threads WHERE id=?', (thread,)).fetchone():
                store.add_thread(thread, 'p', thread, visibility, owner)
                store.db.execute('INSERT INTO local_channels VALUES (?,?)', (thread, name))
            store.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)', (task_id, json.dumps(
                dict(id=task_id, origin=origin, context=dict(thread_id=thread, project_id='p', actor=ACTOR)))))
            self.host.coding.turns[task_id] = SimpleNamespace(closed=False, stop=lambda: None, close=lambda: None, tick=lambda: None)
        store.db.commit()
        fakes = [run[0] for run in runs]
        self.addCleanup(lambda: [self.host.coding.turns.pop(task_id, None) for task_id in fakes])

    def test_owner_runs_share_the_host_and_one_past_the_limit_waits_for_a_slot(self):
        """Bryce's 17 September decision: several coding tasks at once, one per conversation. Since 23 Sep a request past
        the limit waits for a slot instead of being refused. A private conversation of someone else is counted, never named.
        The limit is ten since 23 Sep; this runs the same rule at three so the fixture stays small."""
        import coding_integration
        self.assertEqual(coding_integration.MAX_PARALLEL_OWNERS, 10, 'Bryce, 23 Sep: ten coding tasks at once')
        self.enterContext(patch.object(coding_integration, 'MAX_PARALLEL_OWNERS', 3))
        self.grant()
        self._fake_runs(('hidden-task', 'h', 'Hidden', 'private', 'someone-else', 'conversation'),
                        ('other-task', 'd', 'Design notes', 'shared', None, 'conversation'))
        with self.peer_patch('success'):
            started = self.request('Fix it while two other conversations are coding')
        self.assertNotEqual(started['state'], 'busy_elsewhere', 'two runs elsewhere leave room for this one')
        self.assertIn(started['taskId'], self.host.coding.turns)
        self._fake_runs(('third-task', 'e', 'Engineering', 'shared', None, 'conversation'),
                        ('helper-task', 'd', 'Design notes', 'shared', None, 'collaboration'))
        self.host.store.add_thread('f', 'p', 'f', 'shared', None)
        self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('f', 'Fourth'))
        self.host.store.db.commit()
        with self.peer_patch('success'):
            waiting = self.host.dispatch('coding_request', {'projectId': 'p', 'channelId': 'f', 'clientId': 'req-4', 'text': 'A fourth request'})
        # Bryce, 23 Sep: a request that finds every slot busy waits for one, from the composer too, and says so.
        self.assertEqual((waiting['state'], waiting['taskId'], waiting['why']), ('queued', None, 'slot'))
        self.assertEqual(waiting['detail'], 'Waiting for a slot. Every task slot is busy, and this starts when one frees up.')
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1, 'the waiting request started nothing')
        self.host.dispatch('coding_queue_cancel', {'projectId': 'p', 'channelId': 'f', 'queueId': waiting['id']})
        # The orchestrator's jobs wait the same way: the fourth is queued in its own conversation, a retry of the same
        # id is the same item, and it starts once a slot frees.
        fourth = {'projectId': 'p', 'channelId': 'f', 'clientId': 'req-5', 'text': 'A fourth job from the orchestrator', 'queueIfBusy': True}
        with self.peer_patch('success'):
            queued = self.host.dispatch('coding_request', fourth)
            again = self.host.dispatch('coding_request', fourth)
        self.assertEqual((queued['state'], queued['taskId'], queued['why']), ('queued', None, 'slot'))
        self.assertEqual(again['id'], queued['id'])
        # Once a run is going in its own thread (an earlier item from it started first), it waits for that run, and
        # says so rather than "Every task slot is busy".
        with patch.object(self.host.coding, 'busy_thread', lambda thread: thread == 'f'):
            behind = self.host.coding.conversation.queue.views('f')[0]
        self.assertEqual((behind['why'], behind['detail']), (None, 'Queued. Starts when the current task ends.'))
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_request', {**fourth, 'clientId': 'req-6', 'queueIfBusy': 'yes'})
        for fake in ('hidden-task', 'other-task', 'third-task', 'helper-task'):
            self.host.coding.turns.pop(fake)
        with self.peer_patch('success'):
            self.host.coding.conversation.queue.drain()
        self.assertEqual(self.host.coding.conversation.queue.items('f'), [], 'the waiting job started')
        self.assertEqual(len(self.host.dispatch('tasks', {'projectId': 'p', 'channelId': 'f'})), 1)

    def test_a_run_in_someone_elses_private_conversation_is_counted_but_never_named(self):
        # Kept from the three-run test after a request past the limit started waiting instead of being refused: the
        # refusal that still names a holding run (a handoff, a Home job with images) never names a private one.
        self.grant()
        self._fake_runs(('hidden-task', 'h', 'Hidden', 'private', 'someone-else', 'conversation'))
        active = self.host.coding.active_run()
        self.assertEqual((active['task_id'], active['thread_id'], active['name']), ('hidden-task', 'h', None))

    def test_a_file_changed_by_another_running_task_is_held_until_that_run_ends(self):
        from types import SimpleNamespace
        self.grant()
        store = self.host.store
        store.add_thread('d', 'p', 'd', 'shared', None)
        store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('d', 'Design notes'))
        other = dict(id='other', origin='conversation', context=dict(thread_id='d', project_id='p', actor=ACTOR),
                     runs=[dict(id='old'), dict(id='now')],
                     tokens=[dict(run_id='now', tool='dervo_write_file', state='delivered', path='src/pricing.py'),
                             dict(run_id='now', tool='dervo_create_file', state='cancelled', path='src/new.py'),
                             dict(run_id='now', tool='dervo_write_file', state='delivered', success=False, path='src/refused.py'),
                             dict(run_id='old', tool='dervo_write_file', state='delivered', path='src/old.py')])
        helper = dict(id='fixer', origin='collaboration', context=dict(thread_id='d', project_id='p', actor=ACTOR), runs=[], tokens=[])
        store.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('other', json.dumps(other)))
        store.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('fixer', json.dumps(helper)))
        store.db.commit()
        turn = SimpleNamespace(closed=False, stop=lambda: None, close=lambda: None, tick=lambda: None)
        self.host.coding.turns['other'] = turn
        self.addCleanup(lambda: self.host.coding.turns.pop('other', None))
        holder = self.host.coding.parallel_holder
        self.assertEqual(holder('mine', 'src/./pricing.py'), '#Design notes')
        self.assertIsNone(holder('other', 'src/pricing.py'), 'a task never blocks itself')
        self.assertIsNone(holder('fixer', 'src/pricing.py'), "a collaboration helper follows its owner's packet rules, not the parallel hold")
        self.assertIsNone(holder('mine', 'src/new.py'), 'a cancelled write holds nothing')
        self.assertIsNone(holder('mine', 'src/refused.py'), 'a refused write holds nothing')
        self.assertIsNone(holder('mine', 'src/old.py'), 'an earlier run holds nothing')
        turn.closed = True
        self.assertIsNone(holder('mine', 'src/pricing.py'), 'the hold ends with the run')

    def feed_queue(self):
        feed = self.host.dispatch('conversation_feed', {**self.scope, 'cursor': None, 'force': True})
        return feed['snapshot']['queue'], feed['active']

    def run_cannot_take_messages(self):
        """The queue is for a run that can't take a message any more (it just ended, or the provider refused)."""
        from coding_integration import CodingTasks
        self.enterContext(patch.object(CodingTasks, 'steer', side_effect=Rejected('This run has finished.')))

    def test_a_message_sent_while_the_agent_works_joins_that_run_without_stopping_it(self):
        """Bryce, 17 Sep: "when you send a message mid coding convo ... it should be able to add onto the convo without
        stopping it completely"."""
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
            peer = self.peers[-1]
            before = len(peer.sent)
            added = self.request('Also check the README', 'steer-1')
            self.assertEqual((added['state'], added['taskId']), ('added_to_run', first['taskId']))
            self.assertEqual(self.request('Also check the README', 'steer-1')['taskId'], first['taskId'], 'a retry adds nothing twice')
            detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': first['taskId']})
            self.assertEqual(detail['task']['state'], 'working', 'the run keeps going')
            self.assertEqual([(m['text'], m.get('steer')) for m in detail['task']['messages'] if m['role'] == 'user'],
                             [('Run the tests', None), ('Also check the README', 'sent')])
            self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
            self.assertEqual(self.feed_queue()[0], [], 'nothing waits in the queue')
            sent = peer.sent[before:]
            if self.provider == 'codex':
                steer = [m for m in sent if m.get('method') == 'turn/steer']
                self.assertEqual(len(steer), 1)
                self.assertEqual(steer[0]['params']['input'], [{'type': 'text', 'text': 'Also check the README'}])
                self.assertTrue(steer[0]['params']['expectedTurnId'])
                # Codex refuses a message it can't take into the turn: it moves to the queue instead of vanishing.
                peer.steer_error = True
                self.assertEqual(self.request('Then tidy up', 'steer-2')['state'], 'added_to_run')
                for _ in range(20):
                    self.host.tick()
                    if self.feed_queue()[0]:
                        break
                    time.sleep(.01)
                self.assertEqual([item['text'] for item in self.feed_queue()[0]], ['Then tidy up'])
                detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': first['taskId']})
                self.assertNotIn('Then tidy up', [m['text'] for m in detail['task']['messages']])
            else:
                users = [m for m in sent if m.get('type') == 'user']
                self.assertEqual([m['message']['content'] for m in users], ['Also check the README'])
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})

    def test_a_message_sent_while_this_conversation_runs_is_queued_and_starts_when_the_run_ends(self):
        """Bryce's 17 September decision: sending while this conversation's task runs queues the message. Nothing is
        created or sent for it until that run ends; then it starts through the same path as a fresh send."""
        self.run_cannot_take_messages()
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
        queued = self.request('Then fix the greeting', 'req-2')
        self.assertEqual((queued['state'], queued['taskId'], queued['position'], queued['queued']), ('queued', None, 1, True))
        self.assertEqual((queued['why'], queued['detail']), ('no_run', 'Waits for this run to end.'))
        self.assertEqual(self.request('Then fix the greeting', 'req-2')['id'], queued['id'], 'the same request ID finds the waiting item')
        with self.assertRaises(Rejected):
            self.request('Different text', 'req-2')
        items, active = self.feed_queue()
        self.assertEqual([(item['id'], item['text']) for item in items], [(queued['id'], 'Then fix the greeting')])
        self.assertTrue(active)
        for _ in range(3):
            self.host.tick()
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1, 'nothing was created for the waiting message')
        self.assertEqual(self.starts, 1, 'nothing was sent for the waiting message')
        with self.peer_patch('success'):
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})
            self.settle(first['taskId'], until=self.stopped)
            self.host.tick()
            second = next(r for r in self.host.dispatch('coding_requests', self.scope) if r['client_id'] == 'req-2')
            self.assertIsNotNone(second['taskId'], 'the waiting message became a task once the run ended')
            self.assertEqual(self.feed_queue()[0], [])
            detail = self.settle(second['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual(detail['task']['messages'][0]['text'], 'Then fix the greeting')
        self.assertEqual(self.starts, 2)
        self.assertEqual(self.request('Then fix the greeting', 'req-2')['taskId'], second['taskId'], 'the request ID now finds the task')

    def upload_image(self):
        import base64, hashlib, uuid
        from test_image_attachments import png
        raw = png()
        image = self.host.dispatch('image_begin', {**self.scope, 'clientId': uuid.uuid4().hex, 'name': 'Screenshot.png',
                                                   'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        self.host.dispatch('image_chunk', {**self.scope, 'id': image['id'], 'offset': 0, 'data': base64.b64encode(raw).decode()})
        self.host.dispatch('image_finish', {**self.scope, 'id': image['id']})
        return image['id']

    def test_a_message_with_an_image_says_why_it_waits(self):
        """DC4 (#5): a run takes a message as plain text only, so one carrying an image waits, and the reply and the
        queue row both say why. The same text without the image joins the run."""
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
            image_id = self.upload_image()
            queued = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'img-1', 'text': 'Make it look like this', 'images': [image_id]})
            self.assertEqual((queued['state'], queued['taskId'], queued['why']), ('queued', None, 'image'))
            self.assertEqual(queued['detail'], 'Waits for this run to end: a message with an image can’t join a run.')
            rows = self.feed_queue()[0]
            self.assertEqual([(row['why'], row['detail'], row['images']) for row in rows], [('image', queued['detail'], [image_id])])
            # Negative control: the same text without an image joins the run and carries no reason.
            added = self.request('Make it look like this', 'plain-1')
            self.assertEqual((added['state'], added['taskId']), ('added_to_run', first['taskId']))
            self.assertNotIn('why', added)
            self.assertEqual(len(self.feed_queue()[0]), 1, 'the steered message never touched the queue')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})

    def test_review_check_and_read_only_messages_say_why_they_wait(self):
        """DC4 (#5): a review override, a check choice and read-only mode each wait with their own reason."""
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
            cases = (('review', {'review': {'enabled': False, 'reviewer': None, 'effort': None}}),
                     ('check', {'checkProfile': 'python-unittest'}), ('read_only', {'readOnly': True}))
            for why, extra in cases:
                with self.subTest(why=why):
                    queued = self.host.dispatch('coding_request', {**self.scope, 'clientId': f'{why}-1', 'text': f'Later, with {why}', **extra})
                    self.assertEqual((queued['state'], queued['taskId'], queued['why']), ('queued', None, why))
                    self.assertTrue(queued['detail'].startswith('Waits for this run to end: '), queued['detail'])
                    self.assertNotIn('\u2014', queued['detail'])
            self.assertEqual([row['why'] for row in self.feed_queue()[0]], ['review', 'check', 'read_only'])
            self.assertEqual(len({row['detail'] for row in self.feed_queue()[0]}), 3, 'each reason has its own sentence')
            # Negative control: a plain message still joins the run.
            self.assertEqual(self.request('Plain follow-up', 'plain-2')['state'], 'added_to_run')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})

    def test_a_message_when_no_run_can_take_it_says_it_waits_for_the_run_to_end(self):
        """The run just ended or refused the message: it waits, and the reason is the run, not the message."""
        self.run_cannot_take_messages()
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
            queued = self.request('Then fix the greeting', 'late-1')
            self.assertEqual((queued['state'], queued['why'], queued['detail']), ('queued', 'no_run', 'Waits for this run to end.'))
            self.assertEqual([row['why'] for row in self.feed_queue()[0]], ['no_run'])
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})
        # Negative control: an item queued without a steer (the orchestrator's queueIfBusy) has no reason and reads as before.
        queue = self.host.coding.conversation.queue
        ctx = dict(thread_id='c', project_id='p', actor=ACTOR)
        plain = queue.add({'clientId': 'job-1', 'text': 'A job that waited for a slot'}, ctx, self.host.store.binding(**ctx))
        self.assertEqual((plain['why'], plain['detail']), (None, 'Queued. Starts when the current task ends.'))
        with self.assertRaises(Rejected):
            queue.add({'clientId': 'job-2', 'text': 'A made-up reason'}, ctx, self.host.store.binding(**ctx), why='because')

    def test_a_queued_message_can_be_cancelled_and_one_that_cannot_start_keeps_its_reason(self):
        self.run_cannot_take_messages()
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
        a = self.request('Follow-up A', 'req-a')
        b = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-b', 'text': 'Follow-up B', 'checkProfile': 'python-unittest'})
        self.assertEqual((a['position'], b['position']), (1, 2))
        self.assertEqual(self.host.dispatch('coding_queue_cancel', {**self.scope, 'queueId': a['id']}), {'id': a['id'], 'state': 'cancelled'})
        self.assertEqual([(item['id'], item['position']) for item in self.feed_queue()[0]], [(b['id'], 1)])
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_queue_cancel', {**self.scope, 'queueId': a['id']})
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_queue_cancel', {'projectId': 'p', 'channelId': 'c', 'queueId': b['id'], 'extra': 1})
        # Coding is re-allowed with a Node check only before the run ends: B asked for the Python check, so it can't start.
        (self.project / 'test_sample.mjs').write_text("import {test} from 'node:test';\ntest('ok', () => {});\n")
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.mjs'], 'testFile': 'test_sample.mjs', 'checkProfile': 'node-test'})
        with self.peer_patch('success'):
            self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})
            self.settle(first['taskId'], until=self.stopped)
            self.host.tick()
        items, active = self.feed_queue()
        self.assertEqual((items[0]['id'], items[0]['state'], items[0]['position']), (b['id'], 'failed', None))
        self.assertEqual(items[0]['detail'], "This project's coding permission doesn't include the python-unittest check. Allow coding again to add it.")
        self.assertFalse(active, 'a failed item is not live work')
        self.assertEqual(self.starts, 1, 'nothing was sent for the item that could not start')
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
        self.host.dispatch('coding_queue_cancel', {**self.scope, 'queueId': b['id']})
        self.assertEqual(self.feed_queue()[0], [])

    def test_a_queued_message_does_not_start_by_itself_after_a_restart(self):
        self.run_cannot_take_messages()
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
        queued = self.request('Then fix the greeting', 'req-2')
        self.reopen()
        with self.peer_patch('success'):
            for _ in range(3):
                self.host.tick()
        items, _ = self.feed_queue()
        self.assertEqual((items[0]['id'], items[0]['state']), (queued['id'], 'failed'))
        self.assertEqual(items[0]['detail'], 'Dervo restarted before this request started. Nothing was sent. Send it again.')
        self.assertEqual(self.starts, 1)
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)

    def git(self, *args, cwd=None):
        import subprocess
        return subprocess.run(['/usr/bin/git', '-c', 'commit.gpgsign=false', *args], cwd=cwd or self.project, capture_output=True, text=True,
                              env={'PATH': '/usr/bin:/bin', 'HOME': str(self.base), 'GIT_TERMINAL_PROMPT': '0'}, timeout=30)

    def git_init(self, root=None):
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'),
                     ('add', '-A'), ('commit', '-q', '-m', 'Before Dervo')):
            result = self.git(*args, cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_project_folder_inside_a_larger_repository_commits_and_undoes_with_paths_relative_to_it(self):
        self.grant()
        self.git_init(root=self.base)  # the repository root is above the project folder, as with app/ inside dervo/
        before = self.sample()
        _, detail = self.run_request('Add a comment and run the tests')
        approved = self.approve(detail)
        self.assertEqual(approved['commit']['files'], ['test_sample.py'])
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD', cwd=self.base).stdout.split(), ['project/test_sample.py'])
        undone = self.host.dispatch('task_undo', {**self.scope, 'taskId': detail['task']['id']})
        self.assertEqual(undone['commit']['reverted']['sha'], self.git('rev-parse', 'HEAD', cwd=self.base).stdout.strip())
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD', cwd=self.base).stdout.split(), ['project/test_sample.py'])
        self.assertEqual(self.sample(), before)

    def approve(self, detail):
        attempt = detail['evidence']['attempts'][-1]
        return self.host.dispatch('task_review', {**self.scope, 'taskId': detail['task']['id'], 'attemptNumber': attempt['number'], 'decision': 'approved'})

    def test_approving_a_result_commits_exactly_that_tasks_files_and_undo_reverts_the_commit(self):
        """Bryce's 17 September decision: Dervo commits on approval, the agent never runs git, Undo reverts that commit."""
        self.grant()
        self.git_init()
        before = self.sample()
        (self.project / 'secret_notes.py').write_text('# staged by you, not by Dervo\n')
        self.assertEqual(self.git('add', 'secret_notes.py').returncode, 0)
        _, detail = self.run_request('Add a comment and run the tests')
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        approved = self.approve(detail)
        self.assertEqual(approved['state'], 'idle')
        commit = approved['commit']
        self.assertEqual((commit['files'], commit['how'], commit['reverted']), (['test_sample.py'], 'approved', None))
        self.assertEqual(commit['sha'], self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertEqual(commit['branch'], self.git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip())
        shown = self.git('show', '--name-only', '--format=%s%n%b', 'HEAD').stdout
        self.assertEqual(shown.splitlines()[0], 'Dervo: Add a comment and run the tests')
        self.assertIn('Dervo made this commit; the agent ran no git.', shown)
        self.assertEqual([line for line in shown.splitlines() if line.endswith('.py')], ['- test_sample.py', 'test_sample.py'], 'only the task\'s file')
        self.assertEqual(self.git('diff', '--cached', '--name-only').stdout.split(), ['secret_notes.py'], 'your own staged change is untouched')
        self.assertEqual(self.git('status', '--porcelain', 'test_sample.py').stdout, '')
        with self.assertRaisesRegex(Rejected, 'already committed'):
            self.host.dispatch('task_commit', {**self.scope, 'taskId': detail['task']['id']})
        undone = self.host.dispatch('task_undo', {**self.scope, 'taskId': detail['task']['id']})
        self.assertEqual(undone['commit']['reverted']['sha'], self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertNotEqual(undone['commit']['reverted']['sha'], commit['sha'])
        self.assertEqual(self.sample(), before, 'the file is back to what it was before the task')
        self.assertIn('Dervo: Undo', self.git('log', '-1', '--format=%s').stdout)
        with self.assertRaisesRegex(Rejected, 'already undone'):
            self.host.dispatch('task_undo', {**self.scope, 'taskId': detail['task']['id']})
        self.assertEqual(undone['state'], 'idle', 'undoing the commit does not change the recorded approval')

    def big_fixture(self):
        """About 1 MB of ability data, the shape of harrison's game files (beta.14 report, 24 Sep)."""
        lines = [f'line {n}: value {n} ' + 'x' * 40 + '\n' for n in range(1, 16001)]
        data = ''.join(lines).encode()
        self.assertGreater(len(data), 1000000)
        (self.project / 'abilities.txt').write_bytes(data)
        self.big_replies = []
        self.big_read = {'path': 'abilities.txt', 'start_line': 5000, 'end_line': 5002}
        self.big_edit = lambda read: dict(path='abilities.txt', expected_hash=read['sha256'],
                                          old_string='line 5001: value 5001 ', new_string='line 5001: frost nova ')
        return data

    def test_a_big_file_is_read_by_range_edited_committed_and_undone_without_full_access(self):
        """Harrison, 24 Sep: without full access a worker refused any file over 256 KiB and handed back paste-in code."""
        original = self.big_fixture()
        self.grant(files=('abilities.txt', 'test_sample.py'))
        self.git_init()
        _, detail = self.run_request('Rename the ability on line 5001', mode='big')
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual(task['state'], 'ready_for_review', task['error'])
        self.assertEqual([t['tool'] for t in task['tokens']], ['dervo_read_file', 'dervo_edit_file', 'dervo_run_tests'])
        read, edit = self.big_replies
        self.assertEqual((read['start_line'], read['end_line'], read['total_lines']), (5000, 5002, 16000))
        self.assertEqual(read['content'].splitlines()[1].split(' x')[0], 'line 5001: value 5001')
        self.assertEqual(read['sha256'], hashlib.sha256(original).hexdigest(), 'the whole file’s hash, so an edit can name it')
        now = (self.project / 'abilities.txt').read_bytes()
        self.assertEqual(now, original.replace(b'line 5001: value 5001 ', b'line 5001: frost nova '))
        self.assertEqual(edit['sha256'], hashlib.sha256(now).hexdigest())
        attempt = evidence['attempts'][-1]
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.assertIn('@@ -4998,7 +4998,7 @@', attempt['diff']['abilities.txt'])
        self.assertIn('+line 5001: frost nova ', attempt['diff']['abilities.txt'])
        # The before copy is a file in Dervo's app data, not base64 in the task record.
        stored = self.host.store.db.execute('SELECT document FROM task_evidence WHERE id=?', (task['id'],)).fetchone()[0]
        self.assertLess(len(stored), 200000, 'no copy of the 1 MB file in the record')
        first = next(a for a in json.loads(stored)['attempts'] if 'abilities.txt' in (a.get('after') or {}))
        before = first['before']['abilities.txt']
        self.assertEqual((before['stored'], before['bytes']), (True, len(original)))
        self.assertEqual(self.host.coding.evidence.snapshot_bytes(before), original, 'the before copy can be put back')
        self.assertTrue((self.base / 'store' / 'file-versions' / before['sha256'][:2] / before['sha256']).is_file())
        approved = self.approve(detail)
        self.assertEqual(approved['commit']['files'], ['abilities.txt'], approved['commit'])
        self.assertEqual(self.git('show', 'HEAD:abilities.txt').stdout.encode(), now)
        undone = self.host.dispatch('task_undo', {**self.scope, 'taskId': task['id']})
        self.assertEqual(undone['commit']['reverted']['sha'], self.git('rev-parse', 'HEAD').stdout.strip())
        self.assertEqual((self.project / 'abilities.txt').read_bytes(), original, 'Undo put the file back')

    def test_a_big_file_edit_against_a_stale_hash_is_refused_and_changes_nothing(self):
        original = self.big_fixture()
        self.grant(files=('abilities.txt', 'test_sample.py'))
        self.big_edit = lambda read: dict(path='abilities.txt', expected_hash='0' * 64,
                                          old_string='line 5001: value 5001 ', new_string='line 5001: frost nova ')
        _, detail = self.run_request('Rename the ability on line 5001', mode='big')
        self.assertIn('Stale file preimage', self.big_replies[1]['error'])
        self.assertEqual((self.project / 'abilities.txt').read_bytes(), original)
        self.assertFalse([a for a in detail['evidence']['attempts'] if 'abilities.txt' in (a.get('after') or {}) and a['kind'] == 'change'])

    def test_approving_outside_a_git_repository_records_that_nothing_was_committed(self):
        self.grant()
        _, detail = self.run_request('Add a comment and run the tests')
        approved = self.approve(detail)
        self.assertEqual(approved['state'], 'idle')
        self.assertEqual((approved['commit']['sha'], approved['commit']['skipped']), (None, 'Not a git repository. Nothing was committed.'))
        with self.assertRaisesRegex(Rejected, 'Not a git repository'):
            self.host.dispatch('task_commit', {**self.scope, 'taskId': detail['task']['id']})
        with self.assertRaisesRegex(Rejected, 'no commit to undo'):
            self.host.dispatch('task_undo', {**self.scope, 'taskId': detail['task']['id']})

    def test_a_completed_result_is_committed_on_request_and_a_file_changed_since_the_check_is_refused(self):
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': False})
        self.git_init()
        _, detail = self.run_request('Add a comment and run the tests')
        detail = self.settle(detail['task']['id'], until=lambda d: d['task']['state'] == 'completed')
        self.assertNotIn('commit', detail['task'], 'a completed result is not committed until you ask')
        committed = self.host.dispatch('task_commit', {**self.scope, 'taskId': detail['task']['id']})
        self.assertEqual((committed['commit']['how'], committed['commit']['files']), ('completed', ['test_sample.py']))
        self.assertEqual(committed['commit']['sha'], self.git('rev-parse', 'HEAD').stdout.strip())
        self.appendix = '\n# second attempt\n'
        _, second = self.run_request('Another comment', client_id='req-2')
        second = self.settle(second['task']['id'], until=lambda d: d['task']['state'] == 'completed')
        with open(self.project / 'test_sample.py', 'a') as handle:
            handle.write('# edited by you after the check\n')
        head = self.git('rev-parse', 'HEAD').stdout.strip()
        with self.assertRaisesRegex(Rejected, 'Files changed after this result was completed'):
            self.host.dispatch('task_commit', {**self.scope, 'taskId': second['task']['id']})
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), head, 'nothing was committed')

    def test_a_run_records_what_its_provider_reported_and_the_usage_summary_adds_it_up(self):
        """Bryce's 17 September decision: usage per account, from the provider's own reports only."""
        self.grant()
        _, detail = self.run_request('Add a comment and run the tests')
        usage = detail['task']['runs'][-1]['usage']
        summary = self.host.dispatch('usage_summary', {})
        account = next(a for a in summary['accounts'] if a['profile_id'] == 'p')
        self.assertEqual((account['runs_today'], account['runs_month'], account['method'], account['identity']), (1, 1, 'subscription', self.identity))
        if self.provider == 'codex':
            self.assertEqual(usage, {'input_tokens': 120, 'output_tokens': 30, 'total_tokens': 150, 'cost_usd': None})
            self.assertEqual((account['tokens_today'], account['cost_today_usd'], account['cost_month_usd']), (150, None, None))
            self.assertEqual((account['percent'], account['low'], account['basis'], account['resets_at']), (42, False, 'rate_limits', 1800000000))
            self.assertEqual(account['rate_limits']['secondary'], {'usedPercent': 12, 'resetsAt': 1800600000, 'windowDurationMins': 10080})
            self.assertEqual(account['rate_limits']['planType'], 'plus')
        else:
            self.assertEqual(usage, {'input_tokens': 1, 'output_tokens': None, 'total_tokens': 1, 'cost_usd': None})
            self.assertEqual((account['tokens_today'], account['percent'], account['basis'], account['rate_limits']), (1, None, 'runs', None))
        rows = self.host.store.db.execute('SELECT task_id, run_id, profile_id, provider FROM provider_usage').fetchall()
        self.assertEqual([tuple(r) for r in rows], [(detail['task']['id'], detail['task']['runs'][-1]['id'], 'p', self.provider)])

    def test_auto_mode_suggests_by_default_and_the_project_can_opt_into_switching_by_itself(self):
        """Bryce's 17 September decision: suggest the other account after a provider failure; switching by itself is an opt-in."""
        route = self.host.dispatch('coding_permission', self.scope)['route']
        self.assertEqual((route['auto_switch'], route['source']), (False, 'default'))
        self.assertIn('suggests your other account', route['detail'])
        changed = self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        self.assertEqual((changed['auto_switch'], changed['source'], changed['actor']), (True, 'explicit', ACTOR))
        self.assertIn('switches', changed['detail'])
        self.assertTrue(self.host.dispatch('conversation_status', self.scope)['coding']['route']['auto_switch'])
        for bad in ({'autoSwitch': 'yes'}, {'autoSwitch': 1}, {'autoSwitch': None}, {}):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.host.dispatch('coding_route_set', {**self.scope, **bad})
        self.assertTrue(self.host.dispatch('coding_permission', self.scope)['route']['auto_switch'])
        self.assertFalse(self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': False})['auto_switch'])
        self.reopen()
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['route']['source'], 'explicit', 'the choice survives a restart')

    # --- who runs a request: Auto, the other provider, retry there (C1, C2) ------------------------------------------
    @property
    def other(self):
        return 'claude' if self.provider == 'codex' else 'codex'

    def add_other_provider(self):
        """Your account on the other provider, used by conversation 'd'. Auto only ever knows the other provider this way."""
        identity = 'personal@example.test' if self.other == 'claude' else 'synthetic@example.test'
        self.host.store.add_profile('q', self.other, self.base / 'store/profiles/q', identity, 'personal')
        self.host.store.add_thread('d', 'p', 'd', 'shared')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='q', model='synthetic-model' WHERE id='d'")

    def mark_low(self, profile_id, percent=90):
        import provider_usage
        provider_usage.schema(self.host.store.db)
        provider_usage.record_rate_limits(self.host.store.db, profile_id, {'primary': {'usedPercent': percent, 'resetsAt': 1800000000, 'windowDurationMins': 300}})

    def thread_binding(self):
        return tuple(self.host.store.db.execute("SELECT profile_id, model, session_id FROM threads WHERE id='c'").fetchone())

    def test_a_request_records_who_ran_it_and_why_and_auto_is_a_choice_per_conversation(self):
        here = 'Codex' if self.provider == 'codex' else 'Claude Code'
        self.grant()
        permission = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(permission['conversation_route'], {'mode': 'fixed', 'alternative': None})
        _, detail = self.run_request('Add a comment and run the tests')
        route = detail['task']['route']
        self.assertEqual((route['mode'], route['why'], route['provider'], route['profile_id'], route['label'], route['switched_from']),
                         ('fixed', 'Chosen for this conversation', self.provider, 'p', here, None))
        with self.assertRaisesRegex(Rejected, 'Choose Auto'):
            self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'rotate'})
        chosen = self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'auto'})
        self.assertEqual(chosen['conversation_route']['mode'], 'auto')
        self.appendix = '\n# second attempt\n'
        _, second = self.run_request('Another comment', client_id='req-2')
        self.assertEqual((second['task']['route']['mode'], second['task']['route']['why'], second['task']['route']['suggest']), ('auto', f'Auto: {here}', None))
        # Auto with no account on the other provider says so when the account is low, and stays.
        self.mark_low('p')
        self.appendix = '\n# third attempt\n'
        _, third = self.run_request('A third comment', client_id='req-3')
        self.assertEqual(third['task']['route']['why'], f'Auto: {here}, which is at 90% of its usage window. No account on the other provider is set up.')
        self.assertEqual(self.thread_binding()[0], 'p')

    def test_a_low_account_is_only_suggested_away_from_until_the_project_switches_by_itself(self):
        here, there = ('Codex', 'Claude Code') if self.provider == 'codex' else ('Claude Code', 'Codex')
        self.grant()
        self.add_other_provider()
        self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'auto'})
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['conversation_route']['alternative']['profile_id'], 'q')
        self.mark_low('p')
        _, detail = self.run_request('Add a comment and run the tests')
        route = detail['task']['route']
        self.assertEqual(route['why'], f'Auto: {here}, which is at 90% of its usage window')
        self.assertEqual((route['suggest']['profile_id'], route['suggest']['label'], route['suggest']['reason']), ('q', there, f'{here} is at 90% of its usage window.'))
        self.assertEqual((route['provider'], self.thread_binding()[0]), (self.provider, 'p'), 'nothing moved by default')
        # A fixed conversation never moves, even with switching on and its account low.
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        self.mark_low('p')
        self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'fixed'})
        self.appendix = '\n# fixed attempt\n'
        _, fixed = self.run_request('Fixed comment', client_id='req-fixed')
        self.assertEqual((fixed['task']['route']['provider'], self.thread_binding()[0]), (self.provider, 'p'))
        # Auto with switching on moves to your account on the other provider before starting, and says why.
        self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'auto'})
        self.mark_low('p')  # the Codex double reports its own window after each run
        self.appendix = '\n# switched attempt\n'
        with self.peer_patch('success', provider=self.other):
            started = self.request('Switched comment', 'req-switch')
            switched = self.settle(started['taskId'])
        route = switched['task']['route']
        self.assertEqual((route['provider'], route['profile_id'], route['why']), (self.other, 'q', f'Auto: {here} is at 90% of its usage window, so {there}'))
        self.assertEqual((route['switched_from']['profile_id'], route['switched_from']['provider']), ('p', self.provider))
        self.assertEqual((switched['task']['binding']['provider'], self.thread_binding()), (self.other, ('q', 'synthetic-model', self.thread_binding()[2])))
        # Both low: Auto stays where it is.
        self.mark_low('p')
        self.mark_low('q')
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        self.appendix = '\n# both low\n'
        with self.peer_patch('success', provider=self.other):
            started = self.request('Both low', 'req-both')
            both = self.settle(started['taskId'])
        self.assertEqual((both['task']['route']['provider'], both['task']['route']['switched_from']), (self.other, None))

    def test_a_provider_failure_offers_the_other_account_and_retry_on_sends_the_same_request_there(self):
        here, there = ('Codex', 'Claude Code') if self.provider == 'codex' else ('Claude Code', 'Codex')
        words = 'was overloaded' if self.provider == 'codex' else 'hit an API error'
        self.grant()
        self.add_other_provider()
        before = self.sample()
        _, failed = self.run_request('Add a comment and run the tests', mode='overloaded')
        task = failed['task']
        self.assertEqual((task['state'], task['runs'][-1]['state']), ('blocked', 'failed'))
        self.assertEqual(task['runs'][-1]['provider_failure'], 'overloaded' if self.provider == 'codex' else 'api_error')
        self.assertEqual((task['suggest']['reason'], task['suggest']['other']['profile_id'], task['suggest']['automatic'], task['suggest']['taken']),
                         (f'{here} {words}.', 'q', False, None))
        self.assertEqual(self.sample(), before)
        starts = self.starts
        with self.peer_patch('success', provider=self.other):
            retried = self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id']})
            done = self.settle(retried['taskId'])
            again = self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id']})
        self.assertEqual(again['taskId'], retried['taskId'], 'retrying twice returns the same request')
        self.assertEqual(self.starts, starts + 1)
        route = done['task']['route']
        self.assertEqual((route['provider'], route['retry_of'], route['automatic'], route['why']),
                         (self.other, task['id'], False, f'Retried on {there} after {here} {words}'))
        self.assertEqual(done['task']['messages'][0]['text'], 'Add a comment and run the tests')
        self.assertEqual(done['task']['state'], 'ready_for_review')
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual(original['suggest']['taken'], retried['taskId'])
        self.assertEqual(self.thread_binding()[0], 'q', 'the conversation now uses the other account')

    def test_retry_on_is_refused_without_a_provider_failure_after_the_account_changed_or_with_no_other_provider(self):
        self.grant()
        _, lonely = self.run_request('Add a comment and run the tests', mode='overloaded')
        self.assertIsNone(lonely['task']['suggest']['other'], 'no other provider is set up')
        with self.assertRaisesRegex(Rejected, 'no other account to try'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': lonely['task']['id']})
        self.add_other_provider()
        _, signed_out = self.run_request('Again, please', mode='signed_out', client_id='req-2')
        self.assertIsNone(signed_out['task']['runs'][-1].get('provider_failure'), 'a sign-in problem is not the provider failing')
        self.assertIsNone(signed_out['task'].get('suggest'))
        with self.assertRaisesRegex(Rejected, 'no other account to try'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': signed_out['task']['id']})
        _, failed = self.run_request('And once more', mode='overloaded', client_id='req-3')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='another-model' WHERE id='c'")
        with self.assertRaisesRegex(Rejected, 'account changed since the task ran'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': failed['task']['id']})
        self.assertEqual(self.thread_binding()[:2], ('p', 'another-model'), 'nothing was switched')

    def test_a_retry_on_the_other_account_that_is_refused_leaves_the_conversation_where_it_was(self):
        # Bryce, 17 Sep: Retry on the other provider moved the conversation, then a later check refused the request, so the
        # conversation sat on the other account with no task, and a second retry said the account had changed.
        self.grant()
        self.add_other_provider()
        _, failed = self.run_request('Add a comment and run the tests', mode='overloaded')
        task = failed['task']
        before = self.thread_binding()
        plan = self.host.coding.collaboration.review_plan

        def refuse_there(binding, *args, **kwargs):
            if binding['profile_id'] == 'q':
                raise Rejected('Independent review: the reviewer cannot review this account')
            return plan(binding, *args, **kwargs)
        with patch.object(self.host.coding.collaboration, 'review_plan', refuse_there):
            with self.assertRaisesRegex(Rejected, 'cannot review this account'):
                self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id']})
        self.assertEqual(self.thread_binding(), before, 'nothing was switched')
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
        # A conversation already on the suggested account, as the earlier build left it, can still send the retry there.
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='q', model='synthetic-model', session_id=NULL WHERE id='c'")
        with self.peer_patch('success', provider=self.other):
            retried = self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id']})
            done = self.settle(retried['taskId'])
        route = done['task']['route']
        self.assertEqual((route['provider'], route['retry_of'], route['switched_from']), (self.other, task['id'], None))
        self.assertEqual(done['task']['state'], 'ready_for_review')

    def test_a_task_that_cannot_be_created_after_a_switch_moves_the_conversation_back(self):
        self.grant()
        self.add_other_provider()
        _, failed = self.run_request('Add a comment and run the tests', mode='overloaded')
        before = self.thread_binding()
        with patch.object(type(self.host.coding.conversation), 'create_task', side_effect=Rejected('task could not be created')):
            with self.assertRaisesRegex(Rejected, 'task could not be created'):
                self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': failed['task']['id']})
        self.assertEqual(self.thread_binding(), before, 'the conversation is back on its account, model and session')

    def test_with_switching_on_a_provider_failure_retries_once_on_the_other_account_by_itself(self):
        here, there = ('Codex', 'Claude Code') if self.provider == 'codex' else ('Claude Code', 'Codex')
        self.grant()
        self.add_other_provider()
        # Another conversation still on this provider, so a failed retry would have a way back: it must not take it.
        self.host.store.add_thread('e', 'p', 'e', 'shared')
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        with self.peer_patch('overloaded'), self.peer_patch('overloaded', provider=self.other):
            started = self.request('Add a comment and run the tests')
            first = self.settle(started['taskId'])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                self.host.tick()
                tasks = self.host.dispatch('tasks', self.scope)
                if len(tasks) == 2 and all(t['state'] != 'working' for t in tasks):
                    break
                time.sleep(.01)
            for _ in range(20):
                self.host.tick()
        tasks = self.host.dispatch('tasks', self.scope)
        self.assertEqual(len(tasks), 2, 'one automatic retry, and no retry of the retry')
        retry = next(t for t in tasks if t['id'] != started['taskId'])
        self.assertEqual((retry['route']['automatic'], retry['route']['retry_of'], retry['route']['provider']), (True, started['taskId'], self.other))
        self.assertTrue(retry['route']['why'].startswith(f'Auto: {here} '), retry['route']['why'])
        self.assertTrue(retry['route']['why'].endswith(f', so {there}'))
        self.assertEqual((retry['state'], retry['suggest']['automatic']), ('blocked', False))
        self.assertEqual(self.host.dispatch('task_detail', {**self.scope, 'taskId': started['taskId']})['task']['suggest']['taken'], retry['id'])

    def test_earlier_tasks_stay_listed_and_readable_after_the_conversation_changes_account_but_cannot_be_continued(self):
        self.grant()
        self.add_other_provider()
        _, failed = self.run_request('Add a comment and run the tests', mode='overloaded')
        task_id = failed['task']['id']
        moved = self.host.dispatch('conversation_profile_set', {**self.scope, 'expectedProfileId': 'p', 'profileId': 'q',
                                                               'expectedModel': 'synthetic-model', 'model': 'synthetic-model'})
        self.assertEqual(moved['profile_id'], 'q')
        listed = self.host.dispatch('tasks', self.scope)
        self.assertEqual([t['id'] for t in listed], [task_id], 'the record stays in the conversation')
        requests = self.host.dispatch('coding_requests', self.scope)
        self.assertEqual([r['taskId'] for r in requests], [task_id])
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})
        self.assertEqual((detail['rebound'], detail['retry']['available'], detail['task']['binding']['profile_id']), (True, False, 'p'))
        with self.assertRaisesRegex(Rejected, 'binding changed'):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': task_id, 'text': 'Try again'})
        # Moved to the suggested account, a retry would go there (see the retry test); moved anywhere else, it's refused.
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='another-model' WHERE id='c'")
        with self.assertRaisesRegex(Rejected, 'account changed since the task ran'):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task_id})
        # The conversation's own route choice goes with it when the conversation is deleted.
        self.host.dispatch('conversation_route_set', {**self.scope, 'mode': 'auto'})
        self.assertEqual(self.host.store.db.execute("SELECT count(*) FROM conversation_route WHERE thread_id='c'").fetchone()[0], 1)
        self.host.dispatch('conversation_delete', {**self.scope, 'confirmation': 'DELETE c'})
        self.assertEqual(self.host.store.db.execute("SELECT count(*) FROM conversation_route WHERE thread_id='c'").fetchone()[0], 0)

    def test_a_request_stopped_by_a_used_up_account_waits_for_the_reported_reset_then_starts_again(self):
        self.grant()
        _, failed = self.run_request('Add a comment and run the tests', mode='usage_limit')
        task_id = failed['task']['id']
        wait = {**self.scope, 'taskId': task_id}
        # Both providers (23 Sep): Claude Code's limit report is a usage limit too, and its reset comes from the
        # rate-limit event it sends, recorded like Codex's windows.
        self.assertEqual(failed['task']['runs'][-1]['provider_failure'], 'usage_limit')
        with self.assertRaisesRegex(Rejected, 'didn’t say when its usage resets'):
            self.host.dispatch('coding_wait_for_reset', wait)
        self.assertEqual(self.host.dispatch('conversation_feed', {**self.scope, 'cursor': None, 'force': True})['snapshot']['queue'], [])
        import provider_usage
        provider_usage.schema(self.host.store.db)
        resets = int(time.time()) + 2
        provider_usage.record_rate_limits(self.host.store.db, 'p', {'primary': {'usedPercent': 100, 'resetsAt': resets, 'windowDurationMins': 300}})
        queued = self.host.dispatch('coding_wait_for_reset', wait)
        self.assertEqual((queued['state'], queued['until'], queued['text'], queued['queued']), ('queued', resets, 'Add a comment and run the tests', True))
        self.assertIn('usage window to reset', queued['detail'])
        self.assertEqual(self.host.dispatch('coding_wait_for_reset', wait)['id'], queued['id'], 'asking twice waits once')
        detail = self.host.dispatch('task_detail', wait)['task']
        self.assertEqual(detail['waiting_for_reset'], {'queue_id': queued['id'], 'until': resets})
        starts = self.starts
        with self.peer_patch('success'):
            for _ in range(5):
                self.host.tick()
            self.assertEqual(self.starts, starts, 'nothing starts before the reset')
            self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and len(self.host.dispatch('tasks', self.scope)) < 2:
                self.host.tick()
                time.sleep(.05)
            tasks = self.host.dispatch('tasks', self.scope)
            self.assertEqual(len(tasks), 2, 'the request starts once the reset passes')
            again = self.settle(tasks[-1]['id'])
        self.assertEqual(again['task']['messages'][0]['text'], 'Add a comment and run the tests')
        self.assertEqual(self.starts, starts + 1)
        self.assertEqual(self.host.dispatch('conversation_feed', {**self.scope, 'cursor': None, 'force': True})['snapshot']['queue'], [])

    def test_a_request_waiting_for_a_reset_keeps_waiting_after_a_restart(self):
        """Systems report, 23 Sep, problem 5: nothing is sent for a queued request, and a usage reset isn't in doubt after
        a restart, so it keeps waiting instead of turning into "Send it again"."""
        self.grant()
        _, failed = self.run_request('Add a comment and run the tests', mode='usage_limit')
        import provider_usage
        provider_usage.schema(self.host.store.db)
        resets = int(time.time()) + 3600
        provider_usage.record_rate_limits(self.host.store.db, 'p', {'primary': {'usedPercent': 100, 'resetsAt': resets, 'windowDurationMins': 300}})
        self.host.dispatch('coding_wait_for_reset', {**self.scope, 'taskId': failed['task']['id']})
        starts = self.starts
        self.reopen()
        with self.peer_patch('success'):
            for _ in range(3):
                self.host.tick()
        queue = self.host.dispatch('conversation_feed', {**self.scope, 'cursor': None, 'force': True})['snapshot']['queue']
        self.assertEqual([(item['state'], item['error'], item['until']) for item in queue], [('queued', None, resets)])
        self.assertEqual(self.starts, starts, 'still nothing before the reset')

    def test_a_weekly_limit_waits_for_the_weekly_reset_not_the_next_five_hours(self):
        from conversation_coding import limit_reset
        now = int(time.time())
        five, week = now + 2 * 3600, now + 3 * 86400
        self.assertEqual(limit_reset({'primary': {'usedPercent': 40, 'resetsAt': five}, 'secondary': {'usedPercent': 100, 'resetsAt': week}}), week)
        self.assertEqual(limit_reset({'primary': {'usedPercent': 100, 'resetsAt': five}, 'secondary': {'usedPercent': 100, 'resetsAt': week}}), week,
                         'both full: it can start only once both have reset')
        self.assertEqual(limit_reset({'primary': {'usedPercent': 100, 'resetsAt': five}, 'secondary': {'usedPercent': 35, 'resetsAt': week}}), five)
        self.assertEqual(limit_reset({'primary': {'usedPercent': 80, 'resetsAt': five}}), five, 'no window at 100%: the primary, as before')
        self.assertIsNone(limit_reset({}))
        self.assertIsNone(limit_reset(None))

    def test_after_a_crash_a_kept_request_waits_for_the_lost_runs_in_its_project_and_fails_behind_its_own(self):
        """Reviews, 23 Sep: a kept slot request mustn't start beside runs Dervo lost track of before anyone checks the
        folder, and one whose own conversation lost a run fails as a follow-up always has."""
        import coding_queue
        self.grant()  # the coding runtime and its queue exist once coding is set up
        queue = self.host.coding.conversation.queue
        now = int(time.time())
        base = {'project_id': 'p', 'state': 'queued', 'error': None, 'created_at': now - 60, 'args': {'text': 'Later'}}
        with self.host.store.db:
            queue._put({**base, 'id': 'q-own', 'thread_id': 'c', 'client_id': 'own', 'why': 'slot'})
        self.host.coding.lost_runs = [{'task_id': 'lost-task', 'thread_id': 'c', 'project_id': 'p'}]
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO coding_tasks (id, document) VALUES (?, ?)',
                                       ('lost-task', json.dumps({'id': 'lost-task', 'state': 'disconnected', 'context': {'thread_id': 'c', 'project_id': 'p'}})))
        coding_queue.CodingQueue(self.host.coding.conversation)
        own = queue.get('c', 'q-own')
        self.assertEqual((own['state'], own['error']), ('failed', coding_queue.RESTART_ERROR), 'its own conversation lost a run')
        # Another conversation in the same project: kept, but held while the lost run is unsettled.
        with self.host.store.db:
            queue._put({**base, 'id': 'q-other', 'thread_id': 'c', 'client_id': 'other', 'why': 'slot', 'kept': now})
        self.assertTrue(queue._held('p'))
        self.assertEqual(queue.view(queue.get('c', 'q-other'))['detail'], coding_queue.KEPT_DETAIL)
        self.assertIs(queue.view(queue.get('c', 'q-other'))['held'], True)
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?',
                                       (json.dumps({'id': 'lost-task', 'state': 'blocked', 'context': {'thread_id': 'c', 'project_id': 'p'}}), 'lost-task'))
        self.assertFalse(queue._held('p'), 'settled: nothing holds it any more')
        self.assertFalse(queue._held('another project'))
        self.assertIn('Waiting for a slot', queue.view(queue.get('c', 'q-other'))['detail'])
        self.assertIs(queue.view(queue.get('c', 'q-other'))['held'], False)
        # A second restart before anyone settles it: the run is disconnected already, so it isn't in this restart's
        # lost_runs, and the kept request still waits for it.
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?',
                                       (json.dumps({'id': 'lost-task', 'state': 'disconnected', 'context': {'thread_id': 'c', 'project_id': 'p'}}), 'lost-task'))
        self.host.coding.lost_runs = []
        again = coding_queue.CodingQueue(self.host.coding.conversation)
        kept = again.get('c', 'q-other')
        self.assertEqual((kept['state'], again.view(kept)['detail']), ('queued', coding_queue.KEPT_DETAIL))
        self.assertTrue(again._held('p'), 'the first restart’s lost run still holds it')

    def test_only_a_request_waiting_for_a_slot_or_a_reset_survives_a_restart(self):
        import coding_queue
        now = time.time()
        item = lambda **kw: {'state': 'queued', 'created_at': int(now) - 60, 'why': None, **kw}
        self.assertTrue(coding_queue.survives_restart(item(why='slot'), now))
        self.assertTrue(coding_queue.survives_restart(item(not_before=int(now) + 600), now))
        self.assertFalse(coding_queue.survives_restart(item(why='slot', created_at=int(now) - coding_queue.KEPT_FOR - 1), now),
                         'a slot wait from days ago fails rather than starting by surprise')
        for why in ('no_run', 'home', 'unread', 'image', 'review', 'check', 'read_only', None):
            self.assertFalse(coding_queue.survives_restart(item(why=why), now), why)
        self.assertFalse(coding_queue.survives_restart(item(not_before=int(now) - coding_queue.KEPT_FOR - 1), now),
                         'a reset that passed a day ago fails rather than starting the moment Dervo opens')
        self.assertFalse(coding_queue.survives_restart(item(why='home', not_before=int(now) + 600), now),
                         'waiting behind its own run: that run ended without confirmation')

    def test_one_writer_per_conversation_with_chat_turns_and_coordination(self):
        from types import SimpleNamespace
        self.grant()
        self.host.turns['chat'] = SimpleNamespace(binding={'id': 'c'}, closed=False, begun=True, adapter=SimpleNamespace(state='running'), stop=lambda: None, close=lambda: None, tick=lambda: None)
        with self.peer_patch('success'):
            with self.assertRaises(Rejected):
                self.request('Fix it while chat is active')
        self.assertEqual(self.starts, 0)
        self.host.turns.clear()
        with self.peer_patch('hang'):
            started = self.request('Run the tests', 'hang-1')
            detail = self.settle(started['taskId'], until=self.verifying)
            binding = self.host.store.binding('c', 'p', actor=ACTOR)
            self.assertTrue(self.host._harness().busy(binding), 'the harness yields to a working conversation task')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            detail = self.settle(started['taskId'], until=self.stopped)
        self.assertFalse(self.host._harness().busy(binding))

    def test_stop_ends_the_run_truthfully_and_late_results_are_not_delivered(self):
        self.grant()
        before = self.sample()
        with self.peer_patch('hang'):
            started = self.request('Run the tests')
            self.settle(started['taskId'], until=self.verifying)
            stopped = self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            self.assertIn(stopped['state'], ('working', 'blocked'))
            detail = self.settle(started['taskId'], until=self.stopped)
        task, attempt = detail['task'], detail['evidence']['attempts'][-1]
        self.assertEqual(task['runs'][-1]['state'], 'interrupted')
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(attempt['state'], 'cancelled')
        self.assertNotEqual(task['tokens'][0]['state'], 'delivered')
        self.assertEqual(self.sample(), before)
        self.assertEqual(self.host.dispatch('coding_requests', self.scope)[0]['state'], 'blocked')
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': attempt['number'], 'decision': 'approved'})

    def test_restart_keeps_permission_linkage_and_work_without_replay(self):
        self.grant()
        started, detail = self.run_request('Add a comment and run the tests', client_id='done-1')
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        with self.peer_patch('hang'):
            hanging = self.request('Run the tests again', 'hang-2')
            self.settle(hanging['taskId'], until=self.verifying)
        self.reopen()
        for _ in range(5):
            self.host.tick()
        self.assertEqual(self.starts, 2, 'a restart replays nothing')
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'granted')
        requests = self.host.dispatch('coding_requests', self.scope)
        self.assertEqual([(r['client_id'], r['task_id']) for r in requests], [('done-1', started['taskId']), ('hang-2', hanging['taskId'])])
        self.assertEqual(requests[0]['state'], 'ready_for_review', 'finished work is retained')
        self.assertEqual(requests[1]['state'], 'blocked', 'work the quit paused (16.4) stays paused, not replayed')
        kept = self.host.dispatch('task_detail', {**self.scope, 'taskId': started['taskId']})
        self.assertIn('# conversation attempt', kept['evidence']['attempts'][-1]['diff']['test_sample.py'])
        with self.peer_patch('success'):
            self.assertEqual(self.request('Run the tests again', 'hang-2')['taskId'], hanging['taskId'])
        self.assertEqual(self.starts, 2)

    def test_context_carries_earlier_exchanges_and_only_granted_files(self):
        self.grant()
        self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        turns = []
        from coding_integration import CodingTasks
        original = CodingTasks.start
        def capture(coding, task):
            result = original(coding, task)
            turns.append(coding.turns.get(task['id']))
            return result
        with patch.object(CodingTasks, 'start', capture):
            self.run_request('Now add a comment to test_sample.py and run the tests', client_id='two')
        # The trusted context loader is the one the turn delivered; reading it again after the run is side-effect free for this assertion.
        text = turns[-1].context()[0]
        self.assertIn('Now add a comment to test_sample.py', text)
        if self.provider == 'claude':
            # No saved session file in this fake account folder, so the turn starts fresh, says why and sends the summary.
            second = self.host.dispatch('task_detail', {**self.scope, 'taskId': turns[-1].adapter.task.task_id})['task']
            self.assertEqual(second['continuity']['state'], 'fresh')
            self.assertIn('isn’t saved', second['continuity']['reason'])
            self.assertNotIn('--fork-session', self.peers[-1].argv)
        self.assertIn('Does greeting.py look right to you?', text)
        self.assertIn('No change needed', text)
        self.assertIn('["greeting.py", "test_sample.py"]', text)
        self.assertNotIn('secret_notes.py', text)
        self.assertIn('dervo_write_file', text)


class ClaudeConversationCodingTests(ConversationCodingTests):
    provider = 'claude'

    def steer_then_end(self, end):
        """A run takes a mid-run message it never reads (no replay), then ends by ``end``: 'vanish' (the CLI exits on
        its own) or 'stop' (you pressed Stop). Returns the first task's detail."""
        self.grant()
        with self.peer_patch('hang'):
            first = self.request('Run the tests', 'hang-1')
            self.settle(first['taskId'], until=self.verifying)
            self.assertEqual(self.request('Also check the README', 'steer-1')['state'], 'added_to_run')
            if end == 'stop':
                self.host.dispatch('task_stop', {**self.scope, 'taskId': first['taskId']})
            else:
                self.peers[-1].vanish(code=1)
            detail = self.settle(first['taskId'])
            for _ in range(20):
                self.host.tick()  # the pump drains the queue
                time.sleep(.01)
            for task in self.host.dispatch('tasks', self.scope):
                if task['id'] != first['taskId'] and task['state'] == 'working':
                    self.host.dispatch('task_stop', {**self.scope, 'taskId': task['id']})
                    self.settle(task['id'])
        return detail

    def test_when_the_last_session_was_never_saved_the_launch_tries_the_one_before_and_says_so(self):
        """Phase 2, item 6 (H4): the fork fell straight back to fresh when the previous message's session file was
        missing, even though the session that message continued from was still saved."""
        import pickup
        self.grant()
        _, first = self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        old = first['task']['session_id']
        home = self.base / 'store/profiles/p'
        (home / 'projects/-private-tmp-dervo-claude-gone').mkdir(parents=True)
        (home / f'projects/-private-tmp-dervo-claude-gone/{old}.jsonl').write_text('{}\n')
        _, second = self.run_request('Now do that', mode='none', client_id='two')
        self.assertIn(f'--resume={old}', self.peers[-1].argv, 'control: a saved session is resumed as before')
        self.assertNotIn('fallback', second['task']['continuity'])
        # The second message's own session file was never written (the scripted peer writes none).
        _, third = self.run_request('And the README', mode='none', client_id='three')
        self.assertIn(f'--resume={old}', self.peers[-1].argv, 'the session before it')
        self.assertIn('--fork-session', self.peers[-1].argv)
        continuity = third['task']['continuity']
        self.assertEqual((continuity['state'], continuity['session_id'], continuity['fallback'], continuity['parent']), ('continues', old, True, True))
        prompt = next(m for m in reversed(self.peers[-1].sent) if m.get('type') == 'user')['message']['content']
        self.assertIn('The user asked: Now do that', prompt, 'told the exchange its session never had')
        self.assertEqual(pickup.continuity_line(third['task']),
                         'Claude couldn’t reopen the last session, so it carried on from the one before and was told the last message.')
        # Neither the last session nor the one before it is saved: fresh, and the record says it fell back.
        for saved in (home / 'projects').glob(f'*/{old}.jsonl'):
            saved.unlink()
        _, fourth = self.run_request('Then the tests', mode='none', client_id='four')
        self.assertNotIn('--fork-session', self.peers[-1].argv)
        self.assertEqual((fourth['task']['continuity']['state'], fourth['task']['continuity']['fallback']), ('fresh', True))
        self.assertIn('Claude couldn’t reopen the earlier session', pickup.continuity_line(fourth['task']))

    def test_what_each_message_was_told_is_kept_on_its_run_and_the_composer_says_what_a_fresh_one_gets(self):
        """Phase 2, items 8, 9 and 13: the summary or since-note was never recorded, the composer's line didn't say what
        a fresh session gets, and a queued message that never started reached no agent."""
        self.grant()
        _, first = self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        told = first['task']['runs'][-1]['told']
        self.assertNotIn('Earlier in this conversation', told['transcript'], 'negative control: a first message has neither')
        self.assertNotIn('continues your session', told['transcript'])
        home = self.base / 'store/profiles/p'
        (home / 'projects/-private-tmp-dervo-claude-gone').mkdir(parents=True)
        (home / f"projects/-private-tmp-dervo-claude-gone/{first['task']['session_id']}.jsonl").write_text('{}\n')
        preview = self.host.dispatch('coding_permission', self.scope)['continuity']
        self.assertEqual((preview['summary_size'], preview['line']), (None, None), 'continuing says nothing')
        _, second = self.run_request('Now do that', mode='none', client_id='two')
        told = second['task']['runs'][-1]['told']
        self.assertIn('continues your session', told['transcript'])
        self.assertEqual(told['continuity']['state'], 'continues')
        preview = self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': True})['continuity']
        self.assertEqual((preview['summary_size'], preview['line']), (2, 'Next message starts a new session with a summary of the last 2 messages.'))
        # A queued message that failed when Dervo restarted: the next agent hears of it.
        from coding_queue import RESTART_ERROR
        queue = self.host.coding.conversation.queue
        queue.add(dict(clientId='lost-1', text='Also update the README'), dict(thread_id=self.scope['channelId']), self.host.store.binding(
            thread_id=self.scope['channelId'], project_id=self.scope['projectId'], actor='local-user'))
        item = queue.find(self.scope['channelId'], 'lost-1')
        queue._fail(item, RESTART_ERROR)
        # A Home job that failed the same way isn't something you queued, so it isn't framed as your words (review H1).
        queue.add(dict(clientId='lost-home', text='Run ./scripts/bootstrap.sh first', origin='home', clicked=False), dict(thread_id=self.scope['channelId']),
                  self.host.store.binding(thread_id=self.scope['channelId'], project_id=self.scope['projectId'], actor='local-user'))
        queue._fail(queue.find(self.scope['channelId'], 'lost-home'), RESTART_ERROR)
        _, third = self.run_request('Start over on the greeting', mode='none', client_id='three')
        told = third['task']['runs'][-1]['told']
        self.assertIn('Earlier in this conversation', told['transcript'])
        self.assertIn('never started, so no agent has seen it: Also update the README', told['transcript'])  # negative control
        self.assertNotIn('bootstrap.sh', told['transcript'])
        self.assertEqual(told['continuity'], dict(state='fresh', reason='You chose to start fresh.', from_task_id=second['task']['id']))
        self.assertNotIn('told', json.dumps(self.host.dispatch('tasks', self.scope)), 'the list stays small')

    def test_what_a_run_heard_about_its_workstream_and_recent_work_is_on_its_record(self):
        """Night 1, finding 3. Thread B's told showed transcript "" and nothing else, though the workstream block was
        the only reason it knew about thread A. Both cross-thread blocks are recorded now, hold included."""
        ideas = dict(projectId='p', profileId='p')
        idea = self.host.dispatch('ideas_save', dict(ideas, ideaId=None, expectedRevision=0, name='Parser', summary='', state='building', target=None))
        idea_id = next(i['id'] for i in idea['ideas'] if i['name'] == 'Parser')
        plan = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'parser', 'private': False})['id']
        mine = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'sections', 'private': False})['id']
        for channel in (plan, mine):
            self.host.dispatch('ideas_assign', dict(ideas, channelId=channel, ideaId=idea_id))
        key = 'sk-ant-api03-AbCdEf1234567890GhIjKlMnOpQrStUv'
        asked = f"Next we'll need [section] headers, the API key is {key}. Don't build it yet, just tell me how you'd do it."
        self.host.coding_tasks().save(dict(id='a2', origin='conversation', state='answered', session_id=None,
                                   context=dict(project_id='p', thread_id=plan, actor=ACTOR),
                                   binding=dict(provider='claude', profile_id='p', cwd=str(self.project), category='personal', visibility='shared'),
                                   messages=[dict(role='user', text=asked, created_at=int(time.time()) - 60),
                                             dict(role='assistant', text='Nested dicts. Open questions: case? repeated sections?')],
                                   runs=[dict(id='run-a2', state='completed')], tokens=[]))
        self.scope = dict(projectId='p', channelId=mine)
        self.grant()
        _, detail = self.run_request('carry on from the parser thread', mode='none', client_id='b1')
        told = detail['task']['runs'][-1]['told']
        self.assertIn('#parser: answered', told['workstream'])
        self.assertIn('The user held work back there: "Don\'t build it yet, just tell me how you\'d do it."', told['workstream'])
        self.assertIn('questions they haven’t answered yet', told['workstream'])
        self.assertTrue(told['recent'].startswith('Recent work in other chats of this project'))
        self.assertIn("Next we'll need [section] headers", told['recent'])
        # A key typed into the sibling chat is redacted on this run's record (reviewer, 20 Sep), the rest kept.
        self.assertNotIn(key, json.dumps(told))
        self.assertIn('the API key is [redacted]', told['workstream'])
        self.assertIn('[redacted]', told['recent'])
        self.assertIn('#parser', told['workstream'])
        # Negative control: a private thread gets no workstream block (the rules file it nowhere), so none is recorded.
        self.scope = dict(projectId='p', channelId='c')
        self.grant()
        _, detail = self.run_request('Anything to do?', mode='none', client_id='l1')
        self.assertNotIn('workstream', detail['task']['runs'][-1]['told'])

    def test_a_note_claude_never_read_goes_next_when_the_run_ends_on_its_own(self):
        """Handoff map H5, phase 2 item 7: a steer still unread when the run ended stayed 'sent' and was never re-queued."""
        import pickup
        first = self.steer_then_end('vanish')
        self.assertEqual([m['text'] for m in first['task']['messages'] if m['role'] == 'user'], ['Run the tests'])
        self.assertEqual(first['task']['runs'][-1]['requeued'], 1)
        later = [t for t in self.host.dispatch('tasks', self.scope) if t['id'] != first['task']['id']]
        queued = [item['text'] for item in self.feed_queue()[0]]
        self.assertEqual([t['messages'][0]['text'] for t in later] + queued, ['Also check the README'], 'sent as the next message')
        self.assertEqual(pickup.note_line(first['task'], 'needs'), 'Claude stopped before reading your note, so Dervo sent it as the next message.')

    def test_a_note_left_unread_by_your_own_stop_waits_for_your_next_message(self):
        """Negative control: after your own Stop nothing starts by itself; the note stays on the task for the next message."""
        import pickup
        first = self.steer_then_end('stop')
        self.assertEqual([(m['text'], m.get('steer')) for m in first['task']['messages'] if m['role'] == 'user'],
                         [('Run the tests', None), ('Also check the README', 'sent')])
        self.assertNotIn('requeued', first['task']['runs'][-1])
        self.assertEqual(len(self.host.dispatch('tasks', self.scope)), 1)
        self.assertEqual(self.feed_queue()[0], [])
        self.assertEqual(pickup.note_line(first['task'], 'needs'), 'Claude stopped before reading your note. It goes with your next message.')

    def test_resume_after_your_own_stop_carries_the_unread_note(self):
        """Polish review, 9 Oct: the paused card said the note "goes with your next message", but Resume sent only
        "Carry on from where you paused." and the note was lost. It's told to the resumed run once, then counts as carried."""
        first = self.steer_then_end('stop')
        # Resume continues this same task when the pause came before any attempt (GuideActions' retry button); this
        # fixture's run had started a check, so that's stood in for here.
        from unittest import mock
        with self.peer_patch('none'), mock.patch.object(type(self.host.coding_tasks()), 'retry_status', lambda *_: dict(available=True)):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': first['task']['id'], 'text': 'Carry on from where you paused.'})
            resumed = self.settle(first['task']['id'])
        user = [m for m in resumed['task']['messages'] if m['role'] == 'user']
        self.assertEqual([(m['text'], m.get('steer')) for m in user],
                         [('Run the tests', None), ('Also check the README', 'carried'), ('Carry on from where you paused.', None)])
        self.assertEqual(user[-1]['carries'], ['Also check the README'])
        self.assertIn('The user sent this while you were working and the run ended before you read it: Also check the README',
                      resumed['task']['runs'][-1]['told']['transcript'])

    def test_a_key_in_an_earlier_request_is_redacted_in_a_fresh_transcript(self):
        """Security review 20 Sep, M4: the fresh transcript's "User:" and "Assistant" lines carried earlier requests
        unredacted into a new session's prompt."""
        self.grant()
        key = '3f2a9c1b7d4e5f60718293a4b5c6d7e8'
        self.run_request(f'The mailgun key is {key}. Does greeting.py look right?', mode='none', client_id='one')
        self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': True})
        _, second = self.run_request('Now do that', mode='none', client_id='two')
        transcript = second['task']['runs'][-1]['told']['transcript']
        self.assertIn('Earlier in this conversation', transcript)
        self.assertIn('User: The mailgun key is [redacted]. Does greeting.py look right?', transcript)
        self.assertNotIn(key, transcript)
        self.assertNotIn(key, json.dumps(second['task']['runs'][-1]['told']))

    def test_each_run_records_when_each_step_of_starting_happened(self):
        """"Claude Code is starting" is measured, not guessed: the run keeps when it was asked for, launched, sent its
        task and saw the first words of the reply, in that order, and when it ended."""
        self.grant()
        _, done = self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        timing = done['task']['runs'][-1]['timing']
        steps = [timing[key] for key in ('requested', 'launched', 'sent', 'first_output', 'ended')]
        self.assertEqual(steps, sorted(steps), timing)
        self.assertNotIn('warm', timing)

    def warm_thread(self, settle=True):
        """A finished first message with its session saved where Claude Code keeps it, then you start typing, so the
        thread's next Claude Code is started ahead of time. Returns (first task, the held peer). With ``settle`` false
        the start may still be going on its own thread, and the second item is how many had launched before it."""
        self.enterContext(patch('coding_integration.WARM_WAIT_SECONDS', 120))
        self.grant()
        _, first = self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        old = first['task']['session_id']
        home = self.base / 'store/profiles/p'
        (home / 'projects/-private-tmp-dervo-claude-gone').mkdir(parents=True)
        (home / f'projects/-private-tmp-dervo-claude-gone/{old}.jsonl').write_text('{}\n')
        thread_id = first['task']['context']['thread_id']
        launched = len(self.peers)
        with self.peer_patch('none'):
            self.host.tick()
        self.assertEqual(len(self.peers), launched, 'a run ending starts nothing by itself (code review 8 Oct, H1)')
        self.assertEqual(self.host.dispatch('coding_warm', self.scope), {'warm': 'wanted'})
        with self.peer_patch('none'):
            self.host.tick()
            if not settle:
                return first, launched  # the start may still be going, so count from before it
            self.host.coding.warm_starting[thread_id][0].result(timeout=10); self.host.coding.finish_warm_start(thread_id)
        self.assertEqual(self.host.dispatch('coding_warm', self.scope), {'warm': 'held'}, 'typing more starts nothing new')
        self.assertEqual(len(self.peers), launched + 1, 'one Claude Code started ahead of time')
        held = self.host.coding.warm.held[thread_id]
        self.assertFalse(held.turn.begun)
        self.assertFalse(any(m.get('type') == 'user' for m in self.peers[-1].sent), 'nothing was sent to it')
        return first, self.peers[-1]

    def test_a_follow_up_takes_the_claude_code_started_when_the_last_run_ended(self):
        """8 Oct, Bryce: a send should feel like typing into Claude Code itself. The follow-up runs in the process
        started for it, continuing the same session with the same command, and its run says it started warm."""
        gate = self.hold_warm_starts()
        first, launched = self.warm_thread(settle=False)
        with self.peer_patch('none'):
            # Sent while its Claude Code is still starting: it waits in the queue, as behind a run (third review, 8 Oct).
            started = self.request('Now do that', 'two')
            self.assertEqual((started['state'], started['taskId']), ('queued', None))
            for _ in range(5):
                self.host.tick()
            self.assertEqual([task['id'] for task in self.host.dispatch('tasks', self.scope)], [first['task']['id']], 'nothing starts while it starts')
            gate.set()
            second = self.settle(self.queued_start(first['task']['id']))
            self.assertEqual(len(self.peers), launched + 1, 'one launch in all, the one started ahead of time')
            warm = self.peers[-1]
        task = second['task']
        self.assertEqual(task['state'], 'answered')
        self.assertEqual(task['continuity']['state'], 'continues')
        self.assertIn(f"--resume={first['task']['session_id']}", warm.argv)
        self.assertEqual(warm.argv[-1], f"--session-id={task['session_id']}")
        prompt = next(m for m in reversed(warm.sent) if m.get('type') == 'user')['message']['content']
        self.assertIn('continues your session', prompt)
        self.assertIn('Now do that', prompt)
        timing = task['runs'][-1]['timing']
        self.assertTrue(timing['warm'])
        steps = [timing[key] for key in ('requested', 'launched', 'sent', 'first_output', 'ended')]
        self.assertEqual(steps, sorted(steps), timing)

    def test_a_follow_up_whose_launch_changed_starts_cold_and_the_held_one_is_closed(self):
        """A warm start never changes what a run may do: when anything the launch depends on changed since (here, Start
        fresh), the held process is closed and the send launches as before."""
        _, warm = self.warm_thread()
        held = next(iter(self.host.coding.warm.held.values()))
        self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': True})
        launched = len(self.peers)
        with self.peer_patch('none'):
            started = self.request('Start over on the greeting', 'two')
            self.assertEqual(len(self.peers), launched + 1, 'a fresh launch')
            second = self.settle(started['taskId'])
        self.assertTrue(held.turn.closed)
        self.assertFalse(any(m.get('type') == 'user' for m in warm.sent), 'nothing was ever sent to the held one')
        self.assertEqual(second['task']['continuity']['state'], 'fresh')
        self.assertNotIn('warm', second['task']['runs'][-1]['timing'])
        self.assertNotIn('--fork-session', self.peers[launched].argv)

    def test_a_held_claude_code_is_closed_when_it_expires_and_when_dervo_closes(self):
        import warm_runs
        self.warm_thread()
        thread_id, held = next(iter(self.host.coding.warm.held.items()))
        with self.peer_patch('none'):
            self.host.coding.warm.tick(now=held.born + warm_runs.WARM_SECONDS + 1)
        self.assertTrue(held.turn.closed)
        self.assertNotIn(thread_id, self.host.coding.warm.held)
        with self.peer_patch('none'):
            self.host.dispatch('coding_warm', self.scope)
            self.host.tick()
            self.host.coding.warm_starting[thread_id][0].result(timeout=10); self.host.coding.finish_warm_start(thread_id)
        held = self.host.coding.warm.held[thread_id]
        coding = self.host.coding
        self.close_host()
        self.assertTrue(held.turn.closed)
        self.assertEqual(coding.warm.held, {})

    def hold_warm_starts(self):
        """Keep every start ahead of time going until the returned event is set: the one worker is busy until then."""
        import concurrent.futures
        import threading
        gate = threading.Event()
        coding = self.host.coding_tasks()
        coding.warm_starter = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-warm')
        coding.warm_starter.submit(gate.wait, 20)
        self.addCleanup(gate.set)
        return gate

    def queued_start(self, before):
        """The task the queue started after ``before``, ticking until it has."""
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            later = [task['id'] for task in self.host.dispatch('tasks', self.scope) if task['id'] != before]
            if later:
                return later[0]
            time.sleep(.01)
        self.fail('The queued message never started')

    def test_a_send_never_waits_inside_the_request_for_a_start_and_never_fails_for_one(self):
        """Third review (8 Oct): waiting for the start inside the send failed queued follow-ups as "Didn't start". A
        send while the start is going waits in the queue, and nothing waits in the request itself."""
        gate = self.hold_warm_starts()
        first, launched = self.warm_thread(settle=False)
        with self.peer_patch('none'):
            began = time.monotonic()
            started = self.request('Now do that', 'two')
            self.assertLess(time.monotonic() - began, 1, 'the send came straight back')
            self.assertEqual((started['state'], started.get('why')), ('queued', 'no_run'))
            gate.set()
            second = self.settle(self.queued_start(first['task']['id']))
        self.assertEqual(second['task']['state'], 'answered')

    def test_a_start_that_never_finishes_is_dropped_and_the_waiting_message_launches_as_before(self):
        import coding_integration
        gate = self.hold_warm_starts()
        first, launched = self.warm_thread(settle=False)
        thread_id = first['task']['context']['thread_id']
        with self.peer_patch('none'):
            started = self.request('Now do that', 'two')
            self.assertEqual(started['state'], 'queued')
            _, held = self.host.coding.warm_starting[thread_id]
            held.born -= coding_integration.WARM_START_LIMIT + 1
            second = self.settle(self.queued_start(first['task']['id']))
            self.assertNotIn(thread_id, self.host.coding.warm_starting)
            self.assertEqual(second['task']['state'], 'answered')
            self.assertNotIn('warm', second['task']['runs'][-1]['timing'], 'it launched cold')
            gate.set()

    def test_archiving_the_thread_closes_its_held_claude_code_at_once(self):
        self.warm_thread()
        held = next(iter(self.host.coding.warm.held.values()))
        self.host.dispatch('conversation_archive', self.scope)
        self.assertTrue(held.turn.closed)
        self.assertEqual(self.host.coding.warm.held, {})

    def test_a_follow_up_continues_the_previous_session_and_start_fresh_breaks_it(self):
        self.grant()
        _, first = self.run_request('Does greeting.py look right to you?', mode='none', client_id='one')
        old = first['task']['session_id']
        # Claude Code saved the session under the empty folder the first run used; that folder is gone now.
        home = self.base / 'store/profiles/p'
        (home / 'projects/-private-tmp-dervo-claude-gone').mkdir(parents=True)
        (home / f'projects/-private-tmp-dervo-claude-gone/{old}.jsonl').write_text('{}\n')
        status = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual(status['continuity']['state'], 'continues')
        _, second = self.run_request('Now do that', mode='none', client_id='two')
        task = second['task']
        self.assertEqual(task['continuity']['state'], 'continues')
        self.assertEqual(task['continuity']['from_task_id'], first['task']['id'])
        argv = self.peers[-1].argv
        self.assertIn(f'--resume={old}', argv)
        self.assertIn('--fork-session', argv)
        self.assertEqual(argv[-1], f"--session-id={task['session_id']}")
        self.assertNotEqual(task['session_id'], old)
        moved = list((home / 'projects').glob(f'*/{old}.jsonl'))
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].parent.name, re.sub('[^A-Za-z0-9]', '-', self.peers[-1].cwd))
        prompt = next(m for m in reversed(self.peers[-1].sent) if m.get('type') == 'user')['message']['content']
        self.assertIn('continues your session', prompt)
        self.assertNotIn('Earlier in this conversation', prompt)
        self.assertNotIn('Does greeting.py look right to you?', prompt)
        # Start fresh: said before sending, undoable, and the next message sends the summary instead.
        status = self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': True})
        self.assertEqual(status['continuity'], dict(state='fresh', reason='You chose to start fresh.', from_task_id=task['id'], session_id=None, fresh_chosen=True,
                                                    summary_size=2, line='Next message starts a new session with a summary of the last 2 messages.'))
        self.assertEqual(self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': False})['continuity']['state'], 'continues')
        self.host.dispatch('coding_fresh_start_set', {**self.scope, 'enabled': True})
        _, third = self.run_request('Start over on the greeting', mode='none', client_id='three')
        self.assertEqual(third['task']['continuity']['state'], 'fresh')
        self.assertNotIn('--fork-session', self.peers[-1].argv)
        self.assertIn('Earlier in this conversation', next(m for m in reversed(self.peers[-1].sent) if m.get('type') == 'user')['message']['content'])
        # The message after a fresh start continues from it.
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['continuity']['state'], 'continues')


class DetailWordsTests(unittest.TestCase):
    def test_the_working_line_says_what_the_run_can_touch(self):
        """Night 1, finding 5: every run said "granted files only", read-only and full access included."""
        import conversation_coding as cc
        self.assertEqual(cc.detail_for(dict(state='working', read_only=True)), 'Reading this project only. Nothing is changed. Stop ends the run.')
        self.assertEqual(cc.detail_for(dict(state='working', full_access=True, project_access=True)),
                         'Working in the project folder with full access. Stop ends the run; nothing is replayed.')
        self.assertEqual(cc.detail_for(dict(state='working', project_access=True)),
                         'Working in this project’s files, without the shell. Stop ends the run; nothing is replayed.')
        # Negative controls: a granted-files run keeps the old line, and other states keep theirs.
        self.assertEqual(cc.detail_for(dict(state='working', files=['a.py'])), cc.DETAILS['working'])
        self.assertEqual(cc.detail_for(dict(state='answered', full_access=True)), cc.DETAILS['answered'])


if __name__ == '__main__':
    unittest.main()
