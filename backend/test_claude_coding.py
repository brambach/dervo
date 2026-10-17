"""Claude coding path with synthetic stream-json peers. No CLI, model, account or network."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import claude_coding as m
import claude_policy
from codex_coding import TrustedTaskBinding
from dervo_connections import Rejected

IDENTITY = 'personal@example.test'
SID = '00000000-0000-4000-8000-00000000c1a0'
BINDING = dict(provider='claude', identity=IDENTITY, category='personal', home='/private/tmp/synthetic claude home',
               cwd='/private/tmp/synthetic project', model='synthetic-claude-model', session_id=None)
VERIFIED = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': IDENTITY,
            'subscriptionType': 'max'}
CLAUDE = str(Path('/fixture/bin dir/claude').absolute())


class _Process:
    """Stands in for the Popen handle OwnedProcess exposes."""
    class _Stdin:
        def __init__(self): self.closed = False
        def close(self): self.closed = True

    def __init__(self):
        self.stdin, self.returncode, self.pid = self._Stdin(), 0, 4242
    def wait(self, timeout=None): return self.returncode
    def poll(self): return self.returncode


class SyntheticClaudePeer:
    """In-memory Claude Code stream-json peer. It never authenticates or runs a model."""
    def __init__(self, argv, *, cwd, env):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.sent, self.queue, self.closed, self.eof, self.counter = [], [], False, False, 0
        # The last session flag names the run's own session; a continued session puts --resume=<earlier> --fork-session before it.
        flag = [a for a in argv if a.startswith('--resume=') or a.startswith('--session-id=')][-1]
        self.session_id = flag.split('=', 1)[1]
        self.model = argv[argv.index('--model') + 1]
        self.process = _Process()
        self.initialized = False

    def control(self, rpc):
        self.counter += 1
        self.queue.append({'type': 'control_request', 'request_id': f'req-{self.counter}',
                           'request': {'subtype': 'mcp_message', 'server_name': 'dervo', 'message': rpc}})

    def tool(self, name, arguments, mid):
        self.control({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def delta(self, text):
        self.queue.append({'type': 'stream_event', 'session_id': self.session_id, 'event': {
            'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': text}}})

    def assistant(self, content=(), model=None):
        """A genuine assistant message. The real CLI always writes one before its result."""
        self.queue.append({'type': 'assistant', 'session_id': self.session_id, 'parent_tool_use_id': None,
                           'message': {'role': 'assistant', 'model': model or self.model, 'content': list(content)}})

    def api_error(self, text='API Error: Overloaded'):
        """Claude Code's own report of a failed API call: model <synthetic>, flagged, zero usage."""
        self.queue.append({'type': 'assistant', 'session_id': self.session_id, 'parent_tool_use_id': None, 'isApiErrorMessage': True,
                           'message': {'role': 'assistant', 'model': '<synthetic>', 'stop_reason': 'stop_sequence',
                                       'usage': {'input_tokens': 0, 'output_tokens': 0},
                                       'content': [{'type': 'text', 'text': text}]}})

    def finish(self, ok=True, text='Done. Ready for review.', code=0, spoke=True):
        if spoke:
            self.assistant()
        self.queue.append({'type': 'result', 'subtype': 'success' if ok else 'error_during_execution',
                           'is_error': not ok, 'session_id': self.session_id, 'result': text,
                           'usage': {'input_tokens': 1}, 'permission_denials': []})
        self.process.returncode, self.eof = code, True

    def vanish(self, code=137):
        self.process.returncode, self.eof = code, True

    def on_prompt(self):
        self.finish()

    def on_tool_result(self, mid, result):
        pass

    def send(self, message):
        self.sent.append(message)
        if message.get('type') == 'control_request' and message['request'].get('subtype') == 'initialize':
            # The SDK handshake: the CLI acknowledges before any user content is accepted.
            self.initialized = True
            self.queue.append({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                                         'response': {'commands': [], 'output_style': 'default'}}})
            return
        if message.get('type') == 'user':
            assert self.initialized, 'user content arrived before the initialize handshake completed'
            assert message['parent_tool_use_id'] is None and message['session_id'] == 'default', message
            self.control({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}})
            self.control({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            self.control({'jsonrpc': '2.0', 'id': 'list', 'method': 'tools/list', 'params': {}})
            self.queue.append({'type': 'system', 'subtype': 'init', 'session_id': self.session_id, 'model': self.model,
                               'tools': [], 'mcp_servers': [{'name': 'dervo', 'status': 'connected'}]})
            self.delta('Working')
            self.on_prompt()
        elif message.get('type') == 'control_response':
            # --replay-user-messages echoes everything the host writes, including its own replies (real CLI 2.1.274,
            # 18 Sep). Every task failed with "Unexpected control response" until the adapter ignored these.
            self.queue.append(json.loads(json.dumps(message)))
            mcp = message['response'].get('response', {}).get('mcp_response')
            if mcp and 'result' in mcp and mcp.get('id') not in (0, 'list', None):
                self.on_tool_result(mcp['id'], mcp['result'])

    def read(self, timeout):
        if self.queue:
            return [self.queue.pop(0)]
        if self.eof:
            return None
        raise TimeoutError()

    def interrupt(self):
        self.queue.clear()
        self.process.returncode, self.eof = 130, True

    def close(self):
        self.closed = True


class PolicyTests(unittest.TestCase):
    def test_executable_discovery_uses_standard_locations_only(self):
        with tempfile.TemporaryDirectory(prefix='dervo-claude-policy-') as tmp:
            base = Path(tmp) / 'tools with spaces'
            base.mkdir()
            plain = base / 'plain'
            plain.write_text('#!/bin/sh\n')
            plain.chmod(0o644)
            real = base / 'claude'
            real.write_text('#!/bin/sh\n')
            real.chmod(0o755)
            self.assertEqual(claude_policy.executable([str(plain), str(base), str(real)]), str(real))
            with self.assertRaises(Rejected):
                claude_policy.executable([str(plain), str(base / 'missing')])
        for candidate in claude_policy.CANDIDATES:
            self.assertNotIn('/Users/', candidate)
        self.assertIn('~/.claude/local/claude', claude_policy.CANDIDATES, 'the local install (claude migrate-installer)')
        with tempfile.TemporaryDirectory(prefix='dervo-claude-local-') as home, patch.dict(os.environ, {'HOME': home}):
            local = Path(home) / '.claude/local/claude'
            local.parent.mkdir(parents=True)
            local.write_text('#!/bin/sh\n')
            local.chmod(0o755)
            self.assertEqual(claude_policy.executable(['~/.claude/local/claude']), str(local))

    def test_argv_is_restricted_and_pins_session_identity(self):
        fresh = claude_policy.argv(CLAUDE, 'm', SID, resume=False)
        resumed = claude_policy.argv(CLAUDE, 'm', SID, resume=True)
        for command in (fresh, resumed):
            self.assertEqual(command[command.index('--append-system-prompt') + 1], claude_policy.DERVO_SYSTEM_CONTEXT)
            self.assertEqual(command[command.index('--system-prompt-snapshot') + 1], 'off')
            self.assertNotIn('--system-prompt', command, 'preserve the provider default prompt')
        self.assertEqual(fresh[0], CLAUDE)
        for flag in ('-p', '--restricted', '--strict-mcp-config', '--disable-slash-commands', '--no-chrome'):
            self.assertIn(flag, fresh)
        self.assertEqual(fresh[fresh.index('--tools') + 1], 'WebSearch', 'web search is the one built-in tool (PLAN-native A5)')
        self.assertEqual(fresh[fresh.index('--setting-sources') + 1], '')
        for command in (fresh, resumed):
            self.assertEqual(json.loads(command[command.index('--settings') + 1]), {'crossSessionInbound': 'refuse'},
                             'other Claude sessions must not push unrecorded turns into a Dervo run')
        self.assertEqual(fresh[fresh.index('--input-format') + 1], 'stream-json')
        self.assertEqual(json.loads(fresh[fresh.index('--mcp-config') + 1]), claude_policy.MCP_CONFIG)
        self.assertEqual(fresh[fresh.index('--allowedTools') + 1].split(','),
                         ['WebSearch', 'mcp__dervo__dervo_read_file', 'mcp__dervo__dervo_write_file', 'mcp__dervo__dervo_edit_file', 'mcp__dervo__dervo_run_tests'])  # edit: beta.15 big files
        self.assertEqual(fresh[-1], f'--session-id={SID}')
        self.assertEqual(resumed[-1], f'--resume={SID}')
        self.assertFalse(any(a.startswith('--session-id') for a in resumed))
        # --safe-mode disables MCP servers (CLI reference), so the coding launch spells out each restriction instead.
        self.assertNotIn('--safe-mode', fresh)
        for flags in claude_policy.SAFE_MODE_REPLACEMENTS.values():
            index = fresh.index(flags[0])
            self.assertEqual(fresh[index:index + len(flags)], ['--tools', 'WebSearch'] if flags[0] == '--tools' else flags, flags)
        self.assertEqual(claude_policy.user_message('hi'), {'type': 'user', 'message': {'role': 'user', 'content': 'hi'},
                                                            'parent_tool_use_id': None, 'session_id': 'default'})
        for bad in (dict(executable_path='relative/claude', model='m', session_id=SID),
                    dict(executable_path=CLAUDE, model='', session_id=SID),
                    dict(executable_path=CLAUDE, model='m', session_id='not-a-uuid')):
            with self.assertRaises(Rejected):
                claude_policy.argv(bad['executable_path'], bad['model'], bad['session_id'], resume=False)

    def test_auth_status_uses_official_command_in_clean_owned_environment(self):
        calls = []
        class Result:
            def __init__(self, code, stdout): self.returncode, self.stdout = code, stdout
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return Result(0, json.dumps(VERIFIED).encode())
        home = '/private/tmp/owned claude home'
        self.assertEqual(claude_policy.auth_status(CLAUDE, home, run=run), VERIFIED)
        argv, kwargs = calls[0]
        self.assertEqual(argv, [CLAUDE, '--safe-mode', 'auth', 'status'], 'JSON is the documented default; --text is the only output flag')
        self.assertEqual(kwargs['cwd'], home)
        self.assertEqual(set(kwargs['env']), {'HOME', 'PATH', 'LANG', 'LC_ALL', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY'})
        self.assertEqual(kwargs['env']['CLAUDE_CONFIG_DIR'], home)
        import pwd
        self.assertEqual(kwargs['env']['HOME'], pwd.getpwuid(os.getuid()).pw_dir, 'Keychain lookup needs the account home')
        self.assertEqual(claude_policy.auth_status(CLAUDE, home, run=lambda *a, **k: Result(2, b'{}')), {})
        self.assertEqual(claude_policy.auth_status(CLAUDE, home, run=lambda *a, **k: Result(0, b'not json')), {})
        self.assertEqual(claude_policy.auth_status(CLAUDE, home, run=lambda *a, **k: Result(0, b'[1]')), {})
        with self.assertRaises(Rejected):
            claude_policy.auth_status('claude', home, run=run)

    def test_mcp_tools_match_codex_dynamic_tools(self):
        from codex_coding import dynamic_tools
        self.assertEqual([t['name'] for t in claude_policy.mcp_tools()], [t['name'] for t in dynamic_tools()])
        for tool in claude_policy.mcp_tools():
            self.assertEqual(set(tool), {'name', 'description', 'inputSchema'})


class AdapterTests(unittest.TestCase):
    def make(self, session_id=None):
        self.sent, self.events, self.approved = [], [], True
        binding = {**BINDING, 'session_id': session_id}
        task = TrustedTaskBinding('synthetic-task', binding, lambda task, request: self.approved)
        return m.ClaudeCodingAdapter(binding, self.sent.append, lambda k, d: self.events.append((k, d)), task=task)

    def handshake(self, a, subtype='success'):
        a.initialize()
        request = self.sent[-1]
        self.assertEqual((request['type'], request['request']), ('control_request', {'subtype': 'initialize', 'hooks': None}))
        a.receive({'type': 'control_response', 'response': {'subtype': subtype, 'request_id': request['request_id'],
                                                             'response': {'commands': []}, 'error': 'synthetic refusal'}})

    def launched(self, session_id=None):
        a = self.make(session_id)
        a.authenticate(VERIFIED)
        return a, a.command(CLAUDE)

    def running(self, session_id=None):
        a, argv = self.launched(session_id)
        self.handshake(a)
        a.begin('Synthetic task context', 'client')
        a.receive({'type': 'system', 'subtype': 'init', 'session_id': a.session_id, 'model': BINDING['model']})
        return a, argv

    def control(self, rpc, rid='req-1'):
        return {'type': 'control_request', 'request_id': rid,
                'request': {'subtype': 'mcp_message', 'server_name': 'dervo', 'message': rpc}}

    def call(self, mid=7, name='dervo_read_file', arguments=None, rid='req-1', **patch):
        params = {'name': name, 'arguments': {'path': 'approved.py'} if arguments is None else arguments, **patch}
        return self.control({'jsonrpc': '2.0', 'id': mid, 'method': 'tools/call', 'params': params}, rid)

    def test_an_echo_of_our_own_reply_is_ignored(self):
        """18 Sep: --replay-user-messages echoed the host's answer to Claude Code's handshake, and every task died at
        once with "Unexpected control response"."""
        a, _ = self.running()
        echo = {'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'claude-own-id',
                                                         'response': {'mcp_response': {'jsonrpc': '2.0', 'id': 0, 'result': {}}}}}
        a.receive(echo)
        self.assertEqual(a.state, 'running', 'an echo of our own answer changes nothing')
        with self.assertRaisesRegex(Rejected, 'Unexpected control response'):
            a.receive({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': a._init_request_id, 'response': {}}})

    def test_a_message_added_mid_run_is_read_in_that_turn_or_starts_one_more(self):
        a, argv = self.running()
        self.assertIn('--replay-user-messages', argv)
        replay = lambda text: {'type': 'user', 'isReplay': True, 'session_id': a.session_id, 'parent_tool_use_id': None,
                               'message': {'role': 'user', 'content': text}}
        a.receive(replay('Synthetic task context'))
        a.steer('Also check the README')
        self.assertEqual(self.sent[-1]['message']['content'], 'Also check the README')
        self.assertEqual(a.steers_unread, 1)
        a.receive(replay('Also check the README'))
        self.assertEqual((a.steers_unread, self.events[-1][0]), (0, 'steer_read'))
        # Sent just as the turn ended: the result doesn't close the run, and the replay starts one more turn.
        a.steer('One more thing')
        a.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': a.session_id, 'result': 'Done', 'usage': {}})
        self.assertEqual(a.steers_unread, 1, 'the turn can’t end on a message Claude Code hasn’t read')
        a.receive(replay('One more thing'))
        self.assertIsNone(a.result, 'a new turn begins for it')
        a.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': a.session_id, 'result': 'Done too', 'usage': {}})
        self.assertIsNotNone(a.result)
        with self.assertRaisesRegex(Rejected, 'waits for the next one'):
            a.steer('Too late')

    def test_wrong_provider_and_binding_mismatch_are_rejected(self):
        codex = {**BINDING, 'provider': 'codex'}
        with self.assertRaises(Rejected):
            m.ClaudeCodingAdapter(codex, list().append, lambda *_: None,
                                  task=TrustedTaskBinding('t', codex, lambda *_: True))
        with self.assertRaises(Rejected):
            m.ClaudeCodingAdapter(BINDING, list().append, lambda *_: None,
                                  task=TrustedTaskBinding('t', {**BINDING, 'model': 'other'}, lambda *_: True))
        with self.assertRaises(Rejected):
            m.ClaudeCodingAdapter({**BINDING, 'executable': '/x'}, list().append, lambda *_: None,
                                  task=TrustedTaskBinding('t', {**BINDING, 'executable': '/x'}, lambda *_: True))

    def test_a_team_subscription_is_accepted(self):
        """Bryce, 18 Sep 2026: allow Team for everyone. Enterprise and non-subscription logins stay refused."""
        a = self.make()
        a.authenticate({**VERIFIED, 'subscriptionType': 'team'})
        self.assertEqual(a.state, 'ready')

    def test_authentication_is_strict_and_blocks(self):
        for patch_ in ({'loggedIn': False}, {'authMethod': 'console'}, {'apiProvider': 'bedrock'},
                       {'email': 'work@example.test'}, {'subscriptionType': 'enterprise'}, {'subscriptionType': None}):
            with self.subTest(patch=patch_):
                a = self.make()
                with self.assertRaises(Rejected):
                    a.authenticate({**VERIFIED, **patch_})
                self.assertEqual(a.state, 'blocked')
        a = self.make()
        with self.assertRaises(Rejected):
            a.command(CLAUDE)
        # Work accounts run since 26 Sep 2026 (b16-work), but only as their own work email, never the personal sign-in.
        binding = {**BINDING, 'category': 'work', 'identity': 'you@acme.test'}
        work = m.ClaudeCodingAdapter(binding, [].append, lambda *_: None, task=TrustedTaskBinding('t', binding, lambda *_: True))
        with self.assertRaises(Rejected):
            work.authenticate(VERIFIED)
        binding = {**BINDING, 'category': 'both'}
        unmarked = m.ClaudeCodingAdapter(binding, [].append, lambda *_: None, task=TrustedTaskBinding('t', binding, lambda *_: True))
        with self.assertRaises(Rejected):
            unmarked.authenticate(VERIFIED)

    def test_fresh_session_is_chosen_by_dervo_and_resume_is_exact(self):
        a, argv = self.running()
        claude_policy.check_uuid(a.session_id)
        self.assertEqual(argv[-1], f'--session-id={a.session_id}')
        self.assertEqual(self.sent[0]['request'], {'subtype': 'initialize', 'hooks': None})
        self.assertEqual(self.sent[1], claude_policy.user_message('Synthetic task context'))
        self.assertEqual([k for k, _ in self.events], ['started'])
        b, argv = self.running(SID)
        self.assertEqual(argv[-1], f'--resume={SID}')
        with self.assertRaises(Rejected):
            b.receive({'type': 'stream_event', 'session_id': 'other-session', 'event': {}})
        self.assertEqual(b.state, 'blocked')
        with self.assertRaises(Rejected):
            self.make('not-a-uuid')

    def test_init_verifies_model_and_session(self):
        for message in ({'model': 'other-model'}, {'session_id': SID}):
            a, _ = self.launched()
            self.handshake(a)
            a.begin('x')
            with self.assertRaises(Rejected):
                a.receive({'type': 'system', 'subtype': 'init', 'session_id': a.session_id, 'model': BINDING['model'], **message})
            self.assertEqual(a.state, 'blocked')

    def test_no_conversation_events_before_the_task_is_sent(self):
        a, _ = self.launched()
        with self.assertRaises(Rejected):
            a.begin('too early')
        self.handshake(a)
        a.receive(self.control({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {}}))
        self.assertEqual(self.sent[-1]['response']['response']['mcp_response']['result']['serverInfo']['name'], 'dervo')
        with self.assertRaises(Rejected):
            a.receive({'type': 'system', 'subtype': 'init', 'session_id': a.session_id, 'model': BINDING['model']})

    def test_initialize_handshake_gates_user_content(self):
        a, _ = self.launched()
        a.initialize()
        self.assertEqual(a.state, 'initializing')
        with self.assertRaises(Rejected):
            a.begin('before the response')
        self.assertEqual(a.state, 'initializing')
        self.assertFalse(any(x.get('type') == 'user' for x in self.sent))
        b, _ = self.launched()
        b.initialize()
        # An answer to someone else's request is the CLI echoing what Dervo wrote (--replay-user-messages). It's ignored,
        # and the handshake still waits for the answer to Dervo's own request.
        b.receive({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'someone-else', 'response': {}}})
        self.assertEqual(b.state, 'initializing')
        with self.assertRaises(Rejected):
            b.begin('still before the response')
        c, _ = self.launched()
        with self.assertRaises(Rejected):
            self.handshake(c, subtype='error')
        self.assertEqual(c.state, 'blocked')
        d, _ = self.launched()
        self.handshake(d)
        with self.assertRaises(Rejected):
            d.initialize()
        self.assertEqual(len([x for x in self.sent if x.get('type') == 'control_request']), 1, 'initialize is sent once')

    def assistant(self, a, model=None, content=None, **extra):
        return {'type': 'assistant', 'session_id': a.session_id, 'parent_tool_use_id': None,
                'message': {'role': 'assistant', 'model': model or BINDING['model'],
                            'content': [{'type': 'text', 'text': 'Working'}] if content is None else content}, **extra}

    def synthetic_error(self, a, text='API Error: Overloaded', **extra):
        """The exact shape Claude Code wrote for task 7f2a2fe7 on 16 September 2026, minus the text."""
        return {'type': 'assistant', 'session_id': a.session_id, 'parent_tool_use_id': None, 'isApiErrorMessage': True,
                'message': {'role': 'assistant', 'model': '<synthetic>', 'stop_reason': 'stop_sequence',
                            'usage': {'input_tokens': 0, 'output_tokens': 0, 'cache_creation_input_tokens': 0},
                            'content': [{'type': 'text', 'text': text}]}, **extra}

    def result(self, a, usage, ok=True, text='ok'):
        return {'type': 'result', 'subtype': 'success' if ok else 'error_during_execution', 'is_error': not ok,
                'session_id': a.session_id, 'result': text,
                'modelUsage': {name: {'inputTokens': 1, 'outputTokens': 1} for name in usage}}

    def answer_text(self):
        text = ''
        for kind, data in self.events:
            if kind == 'delta':
                text += data['text']
            elif kind == 'snapshot':
                text = data['text']
        return text[:65536]

    def stream(self, a, kind, **data):
        a.receive({'type': 'stream_event', 'session_id': a.session_id,
                   'event': {'type': kind, **data}})

    def text_block(self, a, text, index=0, message_id='msg-1'):
        self.stream(a, 'content_block_start', index=index, content_block={'type': 'text', 'text': ''})
        self.stream(a, 'content_block_delta', index=index, delta={'type': 'text_delta', 'text': text})
        a.receive(self.assistant(a, message={'id': message_id, 'model': BINDING['model'],
                                           'content': [{'type': 'text', 'text': text}]}))
        self.stream(a, 'content_block_stop', index=index)

    def test_terminal_summary_preserves_answer_before_tool_call(self):
        a, _ = self.running()
        answer = 'briefing(items) accepts an iterable.\n\n```python\nbriefing(iter(pairs))\n```'
        self.text_block(a, answer)
        a.receive(self.assistant(a, message={'id': 'msg-1', 'model': BINDING['model'],
                                           'content': [{'type': 'tool_use', 'input': {'private': 'not answer text'}}]}))
        self.stream(a, 'message_stop')
        self.stream(a, 'message_start', message={'id': 'msg-2', 'model': BINDING['model']})
        ending = 'Suggested a lesson. No files changed.'
        self.text_block(a, ending, message_id='msg-2')
        a.receive(self.result(a, [BINDING['model']]) | {'result': ending})
        a.exited(0)
        self.assertEqual(self.answer_text(), answer + '\n\n' + ending)
        self.assertEqual(a.state, 'completed')

    def test_multiple_blocks_share_message_id_without_being_dropped_or_duplicated(self):
        a, _ = self.running()
        self.text_block(a, 'First', index=0)
        self.text_block(a, 'Second', index=2)
        a.receive(self.result(a, [BINDING['model']]) | {'result': 'Second'})
        self.assertEqual(self.answer_text(), 'First\n\nSecond')

    def test_complete_blocks_work_without_streaming_and_exclude_nontext(self):
        a, _ = self.running()
        for text in ['First', 'Second']:
            a.receive(self.assistant(a, message={'id': 'same-id', 'model': BINDING['model'], 'content': [
                {'type': 'thinking', 'thinking': 'private reasoning'},
                {'type': 'text', 'text': text}, {'type': 'tool_use', 'input': {'secret': 'private input'}}]}))
        a.receive(self.result(a, [BINDING['model']]) | {'result': 'Second'})
        self.assertEqual(self.answer_text(), 'First\n\nSecond')

    def test_aggregated_complete_event_reconciles_partial_then_keeps_extra_blocks(self):
        a, _ = self.running()
        self.stream(a, 'content_block_delta', delta={'type': 'text_delta', 'text': 'Fir'})
        a.receive(self.assistant(a, message={'model': BINDING['model'], 'content': [
            {'type': 'text', 'text': 'First'}, {'type': 'text', 'text': 'Second'}]}))
        a.receive(self.result(a, [BINDING['model']]) | {'result': 'Second'})
        self.assertEqual(self.answer_text(), 'First\n\nSecond')

    def test_nontext_streams_and_subagent_text_never_enter_answer(self):
        a, _ = self.running()
        for delta in [{'type': 'thinking_delta', 'thinking': 'private reasoning'},
                      {'type': 'input_json_delta', 'partial_json': 'private tool input'}]:
            self.stream(a, 'content_block_delta', delta=delta)
        self.assertEqual(self.answer_text(), '')
        with self.assertRaises(Rejected):
            a.receive({'type': 'stream_event', 'parent_tool_use_id': 'unapproved-child',
                       'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Child answer'}}})
        self.assertEqual(self.answer_text(), '')

    def test_long_text_stays_bounded_without_replacing_the_beginning(self):
        a, _ = self.running()
        first = 'A' * 60000
        self.text_block(a, first)
        self.text_block(a, 'B' * 10000)
        a.receive(self.result(a, [BINDING['model']]) | {'result': 'B' * 10000})
        self.assertEqual(self.answer_text(), (first + '\n\n' + 'B' * 10000)[:65536])
        self.assertLessEqual(len(a._answer_text), 65536)

    def test_partial_answer_survives_interruption_without_a_result(self):
        a, _ = self.running()
        self.text_block(a, 'Earlier answer')
        self.stream(a, 'content_block_start', index=0, content_block={'type': 'text', 'text': ''})
        self.stream(a, 'content_block_delta', delta={'type': 'text_delta', 'text': 'Still writing'})
        a.exited(137)
        self.assertEqual(self.answer_text(), 'Earlier answer\n\nStill writing')

    def test_result_only_and_unframed_stream_keep_distinct_text(self):
        for partial, result, expected in [('', 'Done', 'Done'), ('Done', 'Done', 'Done'),
                                          ('Do', 'Done', 'Done'), ('Reading', 'Done', 'Reading\n\nDone')]:
            with self.subTest(partial=partial):
                a, _ = self.running()
                if partial:
                    self.stream(a, 'content_block_delta', delta={'type': 'text_delta', 'text': partial})
                a.receive(self.result(a, [BINDING['model']]) | {'result': result})
                self.assertEqual(self.answer_text(), expected)

    def test_a_rate_limit_event_reports_the_subscription_windows(self):
        """Captured from Claude Code 2.1.274 on 17 September 2026 (identity fields dropped)."""
        a, _ = self.running()
        a.receive({'type': 'rate_limit_event', 'session_id': a.session_id, 'rate_limit_info': {
            'status': 'allowed', 'resetsAt': 1789628400, 'rateLimitType': 'five_hour', 'overageStatus': 'rejected', 'isUsingOverage': False,
            'unifiedWindows': {'five_hour': {'utilization': 0.14, 'resetsAt': 1789628400}, 'seven_day': {'utilization': 0.35, 'resetsAt': 1789740000}}}})
        self.assertEqual([d for k, d in self.events if k == 'rate_limits'], [{
            'primary': {'usedPercent': 14, 'windowDurationMins': 300, 'resetsAt': 1789628400},
            'secondary': {'usedPercent': 35, 'windowDurationMins': 10080, 'resetsAt': 1789740000}}])
        a.receive({'type': 'rate_limit_event', 'session_id': a.session_id, 'rate_limit_info': {'status': 'allowed'}})
        self.assertEqual(len([k for k, _ in self.events if k == 'rate_limits']), 1, 'an event without figures reports nothing')

    def test_auxiliary_usage_is_recorded_when_every_assistant_message_used_the_selected_model(self):
        """Live acceptance fixture: Haiku appears in modelUsage for background functionality only."""
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.assistant(a))
        a.receive(self.result(a, [BINDING['model'], 'claude-haiku-4-5-20251001']))
        a.exited(0)
        self.assertEqual(a.state, 'completed')
        terminal = self.events[-1][1]
        self.assertEqual(terminal['error'], None)
        self.assertEqual(terminal['auxiliary_models'], ['claude-haiku-4-5-20251001'])
        self.assertEqual(terminal['assistant_models'], [BINDING['model']])
        usage = [d for k, d in self.events if k == 'usage'][-1]
        self.assertEqual((usage['auxiliary_models'], usage['assistant_models'], usage['model_problem']),
                         (['claude-haiku-4-5-20251001'], [BINDING['model']], None))

    def test_selected_model_only_completes_with_no_auxiliary_models(self):
        b, _ = self.running()
        b.receive(self.assistant(b))
        b.receive(self.result(b, [BINDING['model']]))
        b.exited(0)
        self.assertEqual(b.state, 'completed')
        self.assertEqual(self.events[-1][1]['auxiliary_models'], [])

    def test_assistant_message_on_another_model_is_primary_drift_and_blocks(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        with self.assertRaises(Rejected) as caught:
            a.receive(self.assistant(a, model='claude-haiku-4-5-20251001'))
        self.assertIn('claude-haiku-4-5-20251001', str(caught.exception))
        self.assertEqual(a.state, 'blocked')
        self.assertFalse(any(k == 'terminal' for k, _ in self.events))

    def test_subagent_assistant_output_is_rejected(self):
        a, _ = self.running()
        with self.assertRaises(Rejected):
            a.receive(self.assistant(a, parent_tool_use_id='toolu_agent'))
        self.assertEqual(a.state, 'blocked')

    def test_usage_without_the_selected_model_after_a_genuine_reply_fails(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.result(a, ['claude-haiku-4-5-20251001']))
        a.exited(0)
        self.assertEqual(a.state, 'failed')
        self.assertIn('does not appear in the provider usage report', self.events[-1][1]['error'])
        self.assertIn(BINDING['model'], self.events[-1][1]['error'])
        self.assertEqual(self.events[-1][1]['auxiliary_models'], [])

    def test_usage_without_the_selected_model_and_no_reply_is_not_a_model_problem(self):
        """Task 7a4e8db1, 16 September 2026: interrupted before Fable spoke; only the background
        title call (Haiku) was in the usage report, and the run was blamed on the wrong model."""
        a, _ = self.running()
        a.receive(self.result(a, ['claude-haiku-4-5-20251001']))
        a.exited(0)
        self.assertEqual(a.state, 'failed')
        terminal = self.events[-1][1]
        self.assertNotIn('usage report', terminal['error'])
        self.assertIn('without an assistant message from ' + BINDING['model'], terminal['error'])
        self.assertEqual(terminal['auxiliary_models'], ['claude-haiku-4-5-20251001'])
        usage = [d for k, d in self.events if k == 'usage'][-1]
        self.assertEqual((usage['model_problem'], usage['assistant_models']), (None, []))

    def test_run_without_any_assistant_message_is_not_completed(self):
        a, _ = self.running()
        a.receive(self.result(a, [BINDING['model']]))
        a.exited(0)
        self.assertEqual(a.state, 'failed')
        self.assertIn('without an assistant message', self.events[-1][1]['error'])
        b, _ = self.running()
        self.assertEqual(b.stop(), 'SIGINT')
        b.receive(self.result(b, [BINDING['model']]))
        b.exited(0)
        self.assertEqual(b.state, 'interrupted', 'a stop wins over a clean-looking result with no reply')

    def test_usage_model_that_authored_assistant_messages_is_never_auxiliary(self):
        problem, auxiliary = m.effective_model_problem(
            {'modelUsage': {BINDING['model']: {}, 'claude-other-model': {}}}, BINDING['model'],
            assistant_models={BINDING['model'], 'claude-other-model'})
        self.assertIn('claude-other-model', problem)
        self.assertEqual(auxiliary, [])
        problem, auxiliary = m.effective_model_problem({'modelUsage': {BINDING['model']: {}, 'claude-other-model': {}}}, BINDING['model'])
        self.assertEqual((problem, auxiliary), (None, ['claude-other-model']))
        self.assertEqual(m.effective_model_problem({'modelUsage': 'bad'}, BINDING['model'])[0], 'Provider reported malformed model usage')
        self.assertEqual(m.effective_model_problem({}, BINDING['model']), (None, []))
        # The selected model must appear only once a genuine message was seen.
        self.assertEqual(m.effective_model_problem({'modelUsage': {'x': {}}}, BINDING['model']), (None, ['x']))
        self.assertIn('does not appear', m.effective_model_problem({'modelUsage': {'x': {}}}, BINDING['model'], {BINDING['model']})[0])

    def test_synthetic_api_error_fails_with_claude_codes_own_text(self):
        """Task 7f2a2fe7, 16 September 2026: twelve good tool calls, then "API Error: Overloaded"
        in a <synthetic> assistant message. Dervo blamed a model substitution and stranded the run."""
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.synthetic_error(a))
        self.assertEqual(a.state, 'running', 'the report is not a rejection')
        a.receive(self.result(a, [BINDING['model']], ok=False, text='API Error: Overloaded'))
        a.exited(1)
        terminal = self.events[-1][1]
        self.assertEqual((a.state, terminal['state'], terminal['error']), ('failed', 'failed', 'API Error: Overloaded'))
        self.assertEqual(terminal['assistant_models'], [BINDING['model']], 'the synthetic message never counts as the model')
        usage = [d for k, d in self.events if k == 'usage'][-1]
        self.assertEqual(usage['assistant_models'], [BINDING['model']])

    def test_synthetic_api_error_then_exit_without_result_is_failed_not_uncertain(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.synthetic_error(a, text='API Error: 529 overloaded_error'))
        a.exited(1)
        self.assertEqual((a.state, self.events[-1][1]['error']), ('failed', 'API Error: 529 overloaded_error'))

    def test_synthetic_api_error_never_completes_a_clean_looking_result(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.synthetic_error(a))
        a.receive(self.result(a, [BINDING['model']]))
        a.exited(0)
        self.assertEqual((a.state, self.events[-1][1]['error']), ('failed', 'API Error: Overloaded'))

    def test_a_usage_limit_report_is_a_usage_limit_not_a_plain_api_error(self):
        """Claude Code's own limit wording (CLI 2.1.280 prefixes, and the older form) is recorded as usage_limit, so the
        limit line, "start when it resets" and "carry on in Codex" work on Claude too (systems report, problem 2)."""
        for text in ("You've hit your session limit · resets 3pm (Australia/Brisbane)", 'You’ve hit your weekly limit · resets Sep 26',
                     "You've reached your Fable limit.", "You're out of usage credits", 'Claude AI usage limit reached|1789628400'):
            with self.subTest(text=text):
                a, _ = self.running()
                a.receive(self.assistant(a))
                a.receive(self.synthetic_error(a, text=text))
                a.receive(self.result(a, [BINDING['model']], ok=False, text=text))
                a.exited(1)
                self.assertEqual((a.state, self.events[-1][1]['provider_failure'], self.events[-1][1]['error']), ('failed', 'usage_limit', text))
        a, _ = self.running()
        a.receive(self.synthetic_error(a, text='API Error: 529 overloaded_error'))
        a.exited(1)
        self.assertEqual(self.events[-1][1]['provider_failure'], 'api_error', 'an overloaded service is not a limit')

    def test_a_rejected_rate_limit_event_makes_the_failure_a_usage_limit(self):
        a, _ = self.running()
        a.receive({'type': 'rate_limit_event', 'session_id': a.session_id, 'rate_limit_info': {
            'status': 'rejected', 'resetsAt': 1789628400, 'rateLimitType': 'five_hour'}})
        a.receive(self.synthetic_error(a, text='API Error: Request rejected'))
        a.exited(1)
        self.assertEqual(self.events[-1][1]['provider_failure'], 'usage_limit')
        self.assertIn(('rate_limits', {'primary': {'usedPercent': 100, 'windowDurationMins': 300, 'resetsAt': 1789628400},
                                       'rateLimitReachedType': 'rate_limit_reached'}), self.events)

    def test_a_rejected_window_the_run_carried_on_past_doesnt_make_a_later_failure_a_limit(self):
        """Reviews, 23 Sep: one rejected event made every later failure in the run a usage limit."""
        rejected = {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': 1789628400, 'rateLimitType': 'five_hour'}}
        for carried_on in ({'type': 'assistant'}, {'type': 'rate_limit_event', 'rate_limit_info': {'status': 'allowed'}},
                           {'type': 'rate_limit_event', 'rate_limit_info': {**rejected['rate_limit_info'], 'isUsingOverage': True}}):
            with self.subTest(carried_on=carried_on):
                a, _ = self.running()
                a.receive({**rejected, 'session_id': a.session_id})
                a.receive(self.assistant(a) if carried_on['type'] == 'assistant' else {**carried_on, 'session_id': a.session_id})
                a.receive(self.synthetic_error(a, text='API Error: 529 overloaded_error'))
                a.exited(1)
                self.assertEqual(self.events[-1][1]['provider_failure'], 'api_error')
        a, _ = self.running()
        a.receive({**rejected, 'session_id': a.session_id})
        a.receive(self.assistant(a))
        a.receive(self.result(a, [BINDING['model']], ok=False, text='The tests failed.'))
        a.exited(1)
        self.assertEqual(self.events[-1][1]['provider_failure'], None, 'an ordinary failed result is read from its words alone')

    def test_a_limit_that_comes_only_as_the_error_result_is_still_the_providers_side(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.result(a, [BINDING['model']], ok=False, text="You've hit your session limit · resets 3pm"))
        a.exited(1)
        self.assertEqual((a.state, self.events[-1][1]['provider_failure']), ('failed', 'usage_limit'))
        b, _ = self.running()
        b.receive(self.assistant(b))
        b.receive(self.result(b, [BINDING['model']], ok=False, text='The tests failed.'))
        b.exited(1)
        self.assertEqual((b.state, self.events[-1][1]['provider_failure']), ('failed', None), 'an ordinary failed result stays ours')

    def test_api_error_flag_on_a_genuine_model_name_is_still_an_api_error(self):
        a, _ = self.running()
        a.receive(self.assistant(a, isApiErrorMessage=True, content=[{'type': 'text', 'text': 'API Error: Rate limited'}]))
        a.receive(self.result(a, [BINDING['model']]))
        a.exited(0)
        self.assertEqual((a.state, self.events[-1][1]['error']), ('failed', 'API Error: Rate limited'))
        self.assertEqual(self.events[-1][1]['assistant_models'], [])

    def test_genuine_assistant_after_an_api_error_clears_it(self):
        a, _ = self.running()
        a.receive(self.assistant(a))
        a.receive(self.synthetic_error(a))
        a.receive(self.assistant(a))
        a.receive(self.result(a, [BINDING['model']]))
        a.exited(0)
        self.assertEqual(a.state, 'completed')

    def test_synthetic_api_error_leaves_the_answer_text_alone(self):
        a, _ = self.running()
        self.stream(a, 'content_block_start', index=0, content_block={'type': 'text', 'text': ''})
        self.stream(a, 'content_block_delta', index=0, delta={'type': 'text_delta', 'text': 'Working'})
        a.receive(self.synthetic_error(a))
        self.assertEqual(self.answer_text(), 'Working')
        self.assertFalse(any('API Error' in d.get('text', '') for k, d in self.events if k in ('delta', 'snapshot')))
        self.assertEqual(a.api_error, 'API Error: Overloaded')

    def test_synthetic_api_error_without_text_still_reports(self):
        a, _ = self.running()
        a.receive(self.synthetic_error(a, text=''))
        a.receive(self.synthetic_error(a, text='x' * 10000))
        a.exited(1)
        self.assertEqual(len(self.events[-1][1]['error']), 4096)
        b, _ = self.running()
        b.receive(self.synthetic_error(b, text=''))
        b.exited(1)
        self.assertEqual(self.events[-1][1]['error'], 'Claude Code reported an API error without details')

    def test_mcp_handshake_list_and_unknown_methods(self):
        a, _ = self.running()
        a.receive(self.control({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}))
        result = self.sent[-1]['response']['response']['mcp_response']['result']
        self.assertEqual(result['protocolVersion'], '2025-06-18')
        self.assertEqual(self.sent[-1]['response']['request_id'], 'req-1')
        a.receive(self.control({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, 'req-2'))
        self.assertEqual(self.sent[-1]['response'], {'subtype': 'success', 'request_id': 'req-2',
                                                     'response': {'mcp_response': {'jsonrpc': '2.0', 'result': {}}}})
        a.receive(self.control({'jsonrpc': '2.0', 'id': 'list', 'method': 'tools/list', 'params': {}}, 'req-3'))
        self.assertEqual([t['name'] for t in self.sent[-1]['response']['response']['mcp_response']['result']['tools']],
                         list(claude_policy.TOOLS))
        a.receive(self.control({'jsonrpc': '2.0', 'id': 9, 'method': 'resources/list', 'params': {}}, 'req-4'))
        self.assertEqual(self.sent[-1]['response']['response']['mcp_response']['error']['code'], -32601)
        a.receive({'type': 'control_request', 'request_id': 'req-5', 'request': {'subtype': 'hook_callback'}})
        self.assertEqual(self.sent[-1]['response']['subtype'], 'error')
        self.assertEqual(a.pending_tool_requests, ())
        with self.assertRaises(Rejected):
            a.receive(self.control({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}, 'req-6')
                      | {'request': {'subtype': 'mcp_message', 'server_name': 'other', 'message': {}}})

    def test_tool_call_streams_real_result_and_completes(self):
        a, _ = self.running()
        a.receive({'type': 'stream_event', 'session_id': a.session_id,
                   'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'Reading'}}})
        a.receive(self.call())
        pending = a.pending_tool_requests
        self.assertEqual(len(pending), 1)
        request = pending[0]
        self.assertEqual((request.task_id, request.tool, dict(request.arguments)), ('synthetic-task', 'dervo_read_file', {'path': 'approved.py'}))
        self.assertEqual((request.thread_id, request.turn_id), (a.session_id, a.turn_id))
        self.assertFalse(any(x.get('type') == 'control_response' for x in self.sent))
        a.complete_tool_result(request.token, success=False, text='Synthetic broker failure')
        self.assertEqual(self.sent[-1], {'type': 'control_response', 'response': {'subtype': 'success', 'request_id': 'req-1',
                         'response': {'mcp_response': {'jsonrpc': '2.0', 'id': 7, 'result': {
                             'content': [{'type': 'text', 'text': 'Synthetic broker failure'}], 'isError': True}}}}})
        self.assertEqual(a.pending_tool_requests, ())
        with self.assertRaises(Rejected):
            a.complete_tool_result(request.token, success=True, text='again')
        a.receive(self.assistant(a, content=[]))
        a.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': a.session_id,
                   'result': 'Finished', 'usage': {}, 'total_cost_usd': 0})
        a.exited(0)
        self.assertEqual(a.state, 'completed')
        kinds = [k for k, _ in self.events]
        self.assertEqual(kinds, ['started', 'delta', 'snapshot', 'usage', 'terminal'])
        self.assertEqual({k: self.events[-1][1][k] for k in ('state', 'error')}, {'state': 'completed', 'error': None})
        with self.assertRaises(Rejected):
            a.receive({'type': 'result'})

    def test_bad_paths_return_tool_errors_and_allow_fresh_retry(self):
        for path in ['/outside', '../outside', './approved.py', 'a//b', 'a' + chr(0) + 'b']:
            with self.subTest(path=path):
                a, _ = self.running()
                a.receive(self.call(arguments={'path': path}))
                self.assertEqual(a.state, 'running')
                self.assertEqual(a.pending_tool_requests, ())
                self.assertTrue(self.sent[-1]['response']['response']['mcp_response']['result']['isError'])
                a.receive(self.call(mid=8, rid='req-2', arguments={'path': 'approved.py'}))
                self.assertEqual(len(a.pending_tool_requests), 1)
                with self.assertRaises(Rejected):
                    a.receive(self.call(arguments={'path': path}))

    def test_bad_path_does_not_bypass_revoked_approval(self):
        a, _ = self.running()
        self.approved = False
        before = len(self.sent)
        with self.assertRaises(Rejected):
            a.receive(self.call(arguments={'path': '../outside'}))
        self.assertEqual(a.state, 'blocked')
        self.assertEqual(len(self.sent), before)

    def test_protocol_problems_fail_closed_and_bad_calls_return_errors(self):
        # 16.4 item 4: what the model got wrong comes back as an error result it can read, and the run goes on.
        patches = [
            dict(name='shell'), dict(name=[]), dict(arguments={'path': 'approved.py', 'command': 'bad'}), dict(arguments='{}'),
            dict(arguments={'path': True}), dict(arguments={}), dict(arguments={'path': 'a' * 4097}),
            dict(name='dervo_write_file', arguments={'path': 'a', 'expected_hash': 'BAD', 'content': ''}),
            dict(name='dervo_write_file', arguments={'path': 'a', 'expected_hash': 'a' * 64, 'content': 'x' * 65537}),
            dict(name='dervo_run_tests', arguments={'command': 'bad'}), dict(extra=1),
        ]
        for patch_ in patches:
            with self.subTest(patch=str(patch_)[:80]):
                a, _ = self.running()
                a.receive(self.call(**patch_))
                self.assertEqual(a.state, 'running')
                self.assertEqual(a._tools, {})
                result = self.sent[-1]['response']['response']['mcp_response']['result']
                self.assertTrue(result['isError'])
                self.assertTrue(result['content'][0]['text'])
                a.receive(self.call(mid=8, rid='req-2'))
                self.assertEqual(len(a.pending_tool_requests), 1)
        a, _ = self.running()
        a.receive(self.call(name='shell'))
        self.assertIn('This task can use: dervo_edit_file, dervo_read_file', self.sent[-1]['response']['response']['mcp_response']['result']['content'][0]['text'])
        a.receive(self.control(
            {'jsonrpc': '2.0', 'id': 8, 'method': 'tools/call', 'params': {'arguments': {}}}, 'req-2'))
        self.assertIn('name and arguments', self.sent[-1]['response']['response']['mcp_response']['result']['content'][0]['text'])
        # Protocol problems still end the run: an ID out of range, and a replayed call.
        a, _ = self.running()
        with self.assertRaises(Rejected):
            a.receive(self.call(mid=2 ** 60))
        self.assertEqual(a.state, 'blocked')
        a, _ = self.running()
        a.receive(self.call())
        with self.assertRaises(Rejected):
            a.receive(self.call())
        self.assertEqual(self.events[-1][0], 'tools_cancelled')
        a, _ = self.running()
        a.receive(self.call(name='shell'))
        with self.assertRaises(Rejected):
            a.receive(self.call(name='shell'))
        self.assertEqual(a.state, 'blocked')

    def test_call_limit_asks_the_model_to_finish_then_ends_the_run(self):
        from codex_coding import TOOL_CALL_LIMIT, TOOL_CALL_CEILING
        a, _ = self.running()
        a._seen_calls.update(f'old-{n}' for n in range(TOOL_CALL_LIMIT))
        a.receive(self.call())
        self.assertEqual(a.state, 'running')
        self.assertEqual(a._tools, {})
        self.assertIn('finish up', self.sent[-1]['response']['response']['mcp_response']['result']['content'][0]['text'])
        a._seen_calls.update(f'more-{n}' for n in range(TOOL_CALL_CEILING))
        with self.assertRaises(Rejected):
            a.receive(self.call(mid=9, rid='req-9'))
        self.assertEqual(a.state, 'blocked')

    def test_revoked_approval_blocks_and_cancels_pending_tools(self):
        a, _ = self.running()
        a.receive(self.call())
        token = a.pending_tool_requests[0].token
        self.approved = False
        with self.assertRaises(Rejected):
            a.pending_tool_requests
        self.assertEqual(a.state, 'blocked')
        self.assertIn(('tools_cancelled', {'tokens': [token]}), self.events)
        with self.assertRaises(Rejected):
            a.complete_tool_result(token, success=True, text='late')

    def test_direct_permission_requests_are_denied(self):
        a, _ = self.running()
        a.receive({'type': 'control_request', 'request_id': 'perm-1',
                   'request': {'subtype': 'can_use_tool', 'tool_name': 'Bash', 'input': {'command': 'rm'}}})
        self.assertEqual(self.sent[-1]['response']['response']['behavior'], 'deny')
        self.assertEqual(self.events[-1][0], 'approval_denied')
        self.assertEqual(a.state, 'running')

    def test_stop_then_exit_is_interrupted_and_cancels_tools(self):
        a, _ = self.running()
        a.receive(self.call())
        self.assertEqual(a.stop(), 'SIGINT')
        self.assertEqual(a.state, 'stopping')
        self.assertEqual([k for k, _ in self.events][-2:], ['tools_cancelled', 'stopping'])
        a.exited(130)
        self.assertEqual(a.state, 'interrupted')
        self.assertEqual(self.events[-1][1]['state'], 'interrupted')
        with self.assertRaises(Rejected):
            a.stop()

    def test_failure_and_unknown_exits_are_not_success(self):
        a, _ = self.running()
        a.receive({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                   'session_id': a.session_id, 'result': 'Synthetic provider failure'})
        a.exited(1)
        self.assertEqual((a.state, self.events[-1][1]['state'], self.events[-1][1]['error']),
                         ('failed', 'failed', 'Synthetic provider failure'))
        b, _ = self.running()
        b.exited(137)
        self.assertEqual(b.state, 'uncertain')
        self.assertIn('137', self.events[-1][1]['error'])
        c, _ = self.running()
        c.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': c.session_id, 'result': 'ok'})
        c.exited(1)
        self.assertEqual(c.state, 'failed')
        d, _ = self.launched()
        self.handshake(d)
        d.begin('x')
        d.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': d.session_id, 'result': 'no init'})
        d.exited(0)
        self.assertEqual(d.state, 'failed', 'success without an init acknowledgement is not completion')

    def test_cancel_request_drops_matching_pending_tool(self):
        a, _ = self.running()
        a.receive(self.call(rid='req-9'))
        token = a.pending_tool_requests[0].token
        a.receive({'type': 'control_cancel_request', 'request_id': 'req-9'})
        self.assertEqual(a.pending_tool_requests, ())
        self.assertEqual(self.events[-1], ('tools_cancelled', {'tokens': [token]}))


def verified_auth(*args, **kwargs):
    return dict(VERIFIED)


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.peers, self.events, self.contexts = [], [], 0
        self.approved = True
        test = self
        class Peer(SyntheticClaudePeer):
            def __init__(self, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(self)
        self.Peer = Peer
        self.enterContext(patch.object(m, 'OwnedProcess', Peer))
        self.enterContext(patch.object(m, 'auth_status', verified_auth))
        self.enterContext(patch.object(m, 'executable', lambda: CLAUDE))

    def turn(self, session_id=None, peer=None):
        if peer is not None:
            self.enterContext(patch.object(m, 'OwnedProcess', peer))
        binding = {**BINDING, 'session_id': session_id}
        def context():
            self.contexts += 1
            return 'Synthetic task context', 'run-1'
        return m.ClaudeCodingTurn(binding, task=TrustedTaskBinding('synthetic-task', binding, lambda t, r: self.approved),
                                  context=context, emit=lambda k, d: self.events.append((k, d)))

    def pump(self, turn, limit=200):
        for _ in range(limit):
            if turn.closed:
                return
            turn.tick()
        self.fail('turn did not settle')

    def test_full_flow_persists_session_before_context_and_completes(self):
        turn = self.turn()
        peer = self.peers[0]
        # The plan auth status reported comes first (provider_plans, A4): account metadata, not content.
        self.assertEqual([kind for kind, _ in self.events][:2], ['plan', 'session'])
        self.assertEqual(self.contexts, 0, 'context must not load before the session is persisted')
        self.assertEqual(peer.cwd, turn.neutral.name)
        self.assertNotEqual(peer.cwd, BINDING['cwd'], 'the provider runs in a neutral directory, not the project')
        self.assertEqual(set(peer.env), {'HOME', 'PATH', 'LANG', 'LC_ALL', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY', 'GH_CONFIG_DIR'})
        self.assertEqual(os.listdir(peer.env['GH_CONFIG_DIR']), [], 'gh finds no sign-in (test_agent_github)')
        self.assertTrue(peer.env['GH_CONFIG_DIR'].startswith(turn.neutral.name + os.sep))
        import pwd
        self.assertEqual(peer.env['HOME'], pwd.getpwuid(os.getuid()).pw_dir)
        self.assertEqual(peer.env['CLAUDE_CONFIG_DIR'], BINDING['home'])
        self.pump(turn)
        self.assertEqual(self.contexts, 1)
        self.assertEqual(peer.sent[0]['request'], {'subtype': 'initialize', 'hooks': None})
        self.assertEqual(peer.sent[1], claude_policy.user_message('Synthetic task context'))
        kinds = [k for k, _ in self.events]
        self.assertEqual(kinds, ['plan', 'session', 'task_sent', 'started', 'delta', 'snapshot', 'usage', 'terminal'])
        self.assertEqual(self.events[-1][1]['state'], 'completed')
        self.assertTrue(peer.process.stdin.closed, 'stdin is closed after the result so the CLI can exit')
        self.assertTrue(peer.closed)
        self.assertEqual(turn.status()['phase'], 'completed')
        self.assertEqual(self.events[1][1], {'session_id': turn.adapter.session_id, 'pid': 4242}, 'the CLI’s pid rides on the session event')

    def test_project_limits_set_the_silence_and_length_and_the_stop_names_them(self):
        """Beta.17.1: a run takes its project's limits (run_limits) and says which one ended it, with the time it last
        heard from Claude Code. No cap on length means only silence ends it."""
        class Busy(self.Peer):
            def on_prompt(peer): pass  # keeps working
        def begin(turn):
            for _ in range(50):
                turn.tick()
                if turn.begun and turn.adapter.state == 'running':
                    return
            self.fail('the run never began')
        def turn_with(limits):
            self.enterContext(patch.object(m, 'OwnedProcess', Busy))
            binding = {**BINDING, 'session_id': None}
            return m.ClaudeCodingTurn(binding, task=TrustedTaskBinding('synthetic-task', binding, lambda t, r: True, limits=limits),
                                      context=lambda: ('Synthetic task context', 'run-1'), emit=lambda k, d: self.events.append((k, d)))
        defaults = turn_with(None)
        self.assertEqual((defaults.idle_seconds, defaults.run_seconds), (7200, 86400), 'beta.17.1 defaults: 2 hours quiet, 24 hours long')
        defaults.close()
        quiet = turn_with({'idle_seconds': 3600, 'run_seconds': None})
        begin(quiet)
        self.assertIsNone(quiet.hard_deadline, 'no cap on length: only silence ends it')
        self.assertGreater(quiet.deadline, time.monotonic() + 3500)
        self.assertIsInstance(quiet.last_seen, float)
        quiet.deadline = time.monotonic() - 1
        quiet.tick()
        kind, data = self.events[-1]
        self.assertEqual((kind, data['state'], data['host_stop'], data['limit'], data['not_started']), ('terminal', 'failed', True, 'idle', False))
        self.assertEqual(data['error'], 'Claude Code sent nothing for 1 hour, so Dervo stopped the run.')
        self.assertEqual(data['last_seen'], quiet.last_seen)
        self.events.clear()
        long = turn_with({'idle_seconds': 3600, 'run_seconds': 21600})
        begin(long)
        long.hard_deadline = time.monotonic() - 1
        long.tick()
        kind, data = self.events[-1]
        self.assertEqual((data['state'], data['limit'], data['error']), ('failed', 'hard', 'The run went on for 6 hours, this project’s limit, so Dervo stopped it.'))
        # A value that isn't one of the choices falls back to the default, never to no limit at all.
        odd = turn_with({'idle_seconds': 5, 'run_seconds': 'forever'})
        self.assertEqual((odd.idle_seconds, odd.run_seconds), (7200, 86400))
        odd.close()

    def test_a_run_that_never_got_its_task_says_so(self):
        """A run that fails before the task text reaches Claude Code is marked not started (run_ending), not failed work."""
        class Silent(self.Peer):
            def on_prompt(peer): pass
        turn = self.turn(peer=Silent)
        turn.deadline = time.monotonic() - 1
        turn.tick()
        kind, data = self.events[-1]
        self.assertEqual((kind, data['state'], data['not_started'], data['host_stop']), ('terminal', 'failed', True, False))

    def test_a_second_stop_changes_nothing_and_a_stop_the_cli_ignores_is_named(self):
        """Run-lifecycle map, findings 3 and 7: a second Stop used to end a clean stop as "outcome unknown", and a CLI
        that didn't exit within 10 seconds of Stop was recorded as 30 minutes of silence."""
        class Stubborn(self.Peer):
            def on_prompt(peer): pass  # keeps working
            def interrupt(peer): peer.interrupted = getattr(peer, 'interrupted', 0) + 1  # ignores the interrupt
        def begin(turn):
            for _ in range(50):
                turn.tick()
                if turn.begun and turn.adapter.state == 'running':
                    return
            self.fail('the run never began')
        turn = self.turn(peer=Stubborn)
        begin(turn)
        peer = self.peers[-1]
        turn.stop()
        turn.stop()
        self.assertEqual([k for k, _ in self.events if k in ('stopping', 'terminal')], ['stopping'], 'the second Stop adds nothing')
        self.assertEqual(peer.interrupted, 1, 'the interrupt is sent once')
        self.assertTrue(turn.stopping and not turn.closed)
        turn.deadline = time.monotonic() - 1
        turn.tick()
        kind, data = self.events[-1]
        self.assertEqual((kind, data['state'], data['error'], data['host_stop']), ('terminal', 'failed', m.HOST_STOPS['stop'], True))
        self.assertTrue(turn.closed and peer.closed)
        turn.stop()
        self.assertEqual(self.events[-1][0], 'terminal', 'Stop after the end adds nothing either')
        # Negative controls: a run that goes quiet without Stop still reads as silence, and Stop before the run
        # started still ends it at once with the same words as before.
        self.events.clear()
        quiet = self.turn(peer=Stubborn)
        begin(quiet)
        quiet.deadline = time.monotonic() - 1
        quiet.tick()
        self.assertEqual((self.events[-1][1]['error'], self.events[-1][1]['host_stop']), (m.HOST_STOPS['idle'], True))
        self.assertEqual(self.events[-1][1]['limit'], 'idle')
        self.events.clear()
        early = self.turn(peer=Stubborn)
        early.stop()
        self.assertEqual((self.events[-1][0], self.events[-1][1]['state'], self.events[-1][1]['error']),
                         ('terminal', 'interrupted', 'Stopped before provider acknowledgement'))
        self.assertTrue(early.closed)

    def test_turn_records_auxiliary_usage_and_blocks_assistant_drift(self):
        class Aux(self.Peer):
            def on_prompt(peer):
                peer.queue.append({'type': 'assistant', 'session_id': peer.session_id, 'parent_tool_use_id': None,
                                   'message': {'role': 'assistant', 'model': peer.model, 'content': []}})
                peer.queue.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': peer.session_id,
                                   'result': 'done', 'modelUsage': {peer.model: {'inputTokens': 2}, 'claude-haiku-4-5-20251001': {'inputTokens': 1}}})
                peer.process.returncode, peer.eof = 0, True
        turn = self.turn(peer=Aux)
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'completed')
        self.assertEqual(self.events[-1][1]['auxiliary_models'], ['claude-haiku-4-5-20251001'])
        self.assertEqual(turn.status()['auxiliary_models'], ['claude-haiku-4-5-20251001'])
        self.events.clear()
        class Drift(self.Peer):
            def on_prompt(peer):
                peer.queue.append({'type': 'assistant', 'session_id': peer.session_id, 'parent_tool_use_id': None,
                                   'message': {'role': 'assistant', 'model': 'claude-haiku-4-5-20251001', 'content': []}})
                peer.finish()
        turn = self.turn(peer=Drift)
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'uncertain')
        self.assertIn('instead of the selected model', self.events[-1][1]['error'])

    def test_turn_ends_failed_with_the_api_error_text_and_waits_for_the_result(self):
        class Overloaded(self.Peer):
            def on_prompt(peer):
                peer.assistant([{'type': 'text', 'text': 'Reading the file'}])
                peer.api_error('API Error: Overloaded')
                peer.finish(ok=False, text='API Error: Overloaded', code=1, spoke=False)
        turn = self.turn(peer=Overloaded)
        self.pump(turn)
        peer = self.peers[-1]
        self.assertEqual((self.events[-1][1]['state'], self.events[-1][1]['error']), ('failed', 'API Error: Overloaded'))
        self.assertTrue(peer.process.stdin.closed, 'the turn waited for the result and closed stdin, rather than killing on a rejection')
        self.assertEqual(turn.status()['phase'], 'failed')
        self.events.clear()
        class Hung(self.Peer):
            def on_prompt(peer):
                peer.assistant()
                peer.api_error('API Error: Overloaded')
        turn = self.turn(peer=Hung)
        for _ in range(20):
            turn.tick()
        self.assertFalse(turn.closed)
        self.assertLessEqual(turn.deadline, time.monotonic() + 60, 'a CLI that hangs after its own error report gets a short deadline')
        turn.deadline = time.monotonic() - 1
        turn.tick()
        self.assertTrue(turn.closed)
        self.assertEqual((self.events[-1][1]['state'], self.events[-1][1]['error']), ('failed', 'API Error: Overloaded'))

    def test_tool_round_trip_through_the_turn(self):
        test = self
        class Peer(self.Peer):
            def on_prompt(peer): peer.tool('dervo_read_file', {'path': 'sample.py'}, 1)
            def on_tool_result(peer, mid, result):
                test.results = result
                peer.finish()
        turn = self.turn(peer=Peer)
        for _ in range(50):
            turn.tick()
            if turn.pending_tool_requests:
                break
        request = turn.pending_tool_requests[0]
        turn.complete_tool_result(request.token, success=True, text='file body')
        self.pump(turn)
        self.assertEqual(self.results, {'content': [{'type': 'text', 'text': 'file body'}], 'isError': False})
        self.assertEqual(self.events[-1][1]['state'], 'completed')

    def test_auth_failure_launches_nothing_and_delivers_no_context(self):
        with patch.object(m, 'auth_status', lambda *a, **k: {'loggedIn': False}):
            with self.assertRaises(Rejected):
                self.turn()
        self.assertEqual(self.peers, [])
        self.assertEqual(self.contexts, 0)
        self.assertEqual(self.events, [])

    def test_missing_cli_launches_nothing(self):
        with patch.object(m, 'executable', side_effect=Rejected('no cli')):
            with self.assertRaises(Rejected):
                self.turn()
        self.assertEqual(self.peers, [])

    def test_stop_while_running_is_interrupted(self):
        class Peer(self.Peer):
            def on_prompt(peer): peer.tool('dervo_run_tests', {}, 1)
        turn = self.turn(peer=Peer)
        for _ in range(50):
            turn.tick()
            if turn.pending_tool_requests:
                break
        token = turn.pending_tool_requests[0].token
        turn.stop()
        self.assertEqual(turn.adapter.state, 'stopping')
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'interrupted')
        self.assertIn(('tools_cancelled', {'tokens': [token]}), self.events)
        with self.assertRaises(Rejected):
            turn.complete_tool_result(token, success=True, text='late')

    def test_stop_before_acknowledgement_is_not_confirmed(self):
        turn = self.turn()
        turn.stop()
        self.assertEqual(self.events[-1][1], {'state': 'interrupted', 'error': 'Stopped before provider acknowledgement'})
        self.assertTrue(turn.closed)

    def test_failed_result_and_vanished_process(self):
        class Failing(self.Peer):
            def on_prompt(peer): peer.finish(ok=False, text='Synthetic failure', code=1)
        turn = self.turn(peer=Failing)
        self.pump(turn)
        self.assertEqual({k: self.events[-1][1][k] for k in ('state', 'error')}, {'state': 'failed', 'error': 'Synthetic failure'})
        self.events.clear()
        class Vanishing(self.Peer):
            def on_prompt(peer): peer.vanish()
        turn = self.turn(peer=Vanishing)
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'uncertain')

    def test_resume_uses_saved_session_and_rejects_change(self):
        turn = self.turn(session_id=SID)
        self.assertEqual(self.peers[0].argv[-1], f'--resume={SID}')
        self.assertEqual(self.events[:2], [('plan', {'plan': 'max'}), ('session', {'session_id': SID, 'pid': 4242})])
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'completed')
        class Drift(self.Peer):
            def send(peer, message):
                super().send(message)
                for event in peer.queue:
                    if event.get('type') == 'system':
                        event['session_id'] = '00000000-0000-4000-8000-000000000bad'
        self.events.clear()
        turn = self.turn(session_id=SID, peer=Drift)
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'uncertain')
        self.assertIn('conversation ID', self.events[-1][1]['error'])

    def test_revoked_approval_ends_the_turn_without_success(self):
        class Peer(self.Peer):
            def on_prompt(peer): peer.tool('dervo_read_file', {'path': 'sample.py'}, 1)
        turn = self.turn(peer=Peer)
        for _ in range(50):
            turn.tick()
            if turn.pending_tool_requests:
                break
        self.approved = False
        self.pump(turn)
        self.assertEqual(self.events[-1][1]['state'], 'uncertain')
        self.assertTrue(self.peers[0].closed)


class HostIntegrationTests(unittest.TestCase):
    """The real coordinator, evidence broker and store with a Claude profile. Provider is a double."""
    def setUp(self):
        from host import Host, ACTOR
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.host_closed = False
        self.addCleanup(self.close_host)
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.host.store.add_profile('p', 'claude', self.base / 'store/profiles/p', IDENTITY, 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-claude-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')
        self.peers = []
        self.enterContext(patch.object(m, 'auth_status', verified_auth))
        self.enterContext(patch.object(m, 'executable', lambda: CLAUDE))

    def close_host(self):
        if not self.host_closed:
            self.host_closed = True
            self.host.close()

    def create(self):
        return self.host.dispatch('task_create', {**self.scope, 'text': 'Improve the test', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})

    def settle(self, args):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            detail = self.host.dispatch('task_detail', args)
            if detail['task']['state'] != 'working':
                return detail
            time.sleep(.01)
        self.fail('Synthetic completion timed out')

    def run_task(self, task, *, mode='success', correction=False):
        test = self
        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
            def on_prompt(peer):
                if mode == 'none': peer.finish()
                elif mode == 'verify_only': peer.tool('dervo_run_tests', {}, 3)
                elif mode == 'vanish': peer.vanish()
                elif mode == 'fail': peer.finish(ok=False, text='Synthetic provider failure', code=1)
                elif mode == 'output_limit': peer.flood = True
                elif mode == 'output_limit_mid_tool': peer.tool('dervo_run_tests', {}, 9); peer.flood = True
                else: peer.tool('dervo_read_file', {'path': 'test_sample.py'}, 1)
            flood = False
            def read(peer, timeout):
                if peer.flood and not peer.queue:
                    from transport import OwnedProcess as Real
                    raise Rejected(f'The agent sent more than {1024} MiB of output in one run, so Dervo stopped it.')
                return super().read(timeout)
            def on_tool_result(peer, mid, result):
                text = result['content'][0]['text']
                if mid == 9: return
                if mid == 1:
                    body = json.loads(text)
                    content = body['content'] + '\n# current attempt\n'
                    if mode == 'failure': content = content.replace('assertTrue(True)', 'assertTrue(False)')
                    peer.tool('dervo_write_file', dict(path='test_sample.py', expected_hash=body['sha256'], content=content), 2)
                elif mid == 2 and mode in ('api_error', 'api_error_vanish'):
                    peer.assistant([{'type': 'text', 'text': 'Written. Running the checks next.'}])
                    peer.api_error('API Error: Overloaded')
                    if mode == 'api_error': peer.finish(ok=False, text='API Error: Overloaded', code=1, spoke=False)
                    else: peer.vanish(code=1)
                elif mid == 2: peer.tool('dervo_run_tests', {}, 3)
                elif mid == 3: peer.finish()
        args = {**self.scope, 'taskId': task['id']}
        with patch.object(m, 'OwnedProcess', Peer):
            self.host.dispatch('task_continue' if correction else 'task_approve',
                               {**args, **({'text': 'Correct this'} if correction else {'scopeVersion': 1})})
            return self.settle(args)

    def test_claude_task_is_created_and_no_provider_starts_before_approval(self):
        task = self.create()
        self.assertEqual((task['state'], task['contract'], task['binding']['provider']), ('needs_permission', 'dervo-dynamic-tools-v1', 'claude'))
        self.assertEqual(self.peers, [])
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})
        self.assertEqual(detail['task']['runs'], [])

    def test_completed_run_with_real_evidence(self):
        detail = self.run_task(self.create())
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual(task['runs'][-1]['state'], 'completed', task['error'])
        self.assertEqual(len(evidence['attempts']), 1)
        self.assertEqual([t['tool'] for t in task['tokens']], ['dervo_read_file', 'dervo_write_file', 'dervo_run_tests'])
        self.assertTrue(all(t['state'] == 'delivered' for t in task['tokens']))
        claude_policy.check_uuid(task['session_id'])
        self.assertEqual(task['runs'][-1]['session_id'], task['session_id'])
        if evidence['attempts'][0]['test'] is None:
            self.assertEqual(task['state'], 'blocked')
            self.assertIn('Tests were not run', task['error'])
        else:
            self.assertEqual(task['state'], 'ready_for_review')
            self.assertEqual(evidence['attempts'][0]['test']['exit_status'], 0)
        self.assertEqual([m_['role'] for m_ in task['messages']], ['user', 'assistant'])
        self.assertIn('Done', task['messages'][-1]['text'])

    def test_check_without_changes_runs_tests_and_reports_truthfully(self):
        """Live acceptance shape: 'check the file, run the test, change nothing'."""
        before = (self.project / 'test_sample.py').read_bytes()
        detail = self.run_task(self.create(), mode='verify_only')
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual((self.project / 'test_sample.py').read_bytes(), before)
        self.assertEqual([t['tool'] for t in task['tokens']], ['dervo_run_tests'])
        self.assertEqual(len(evidence['attempts']), 1)
        attempt = evidence['attempts'][0]
        self.assertEqual((attempt['kind'], attempt['state'], attempt['diff']), ('verification', 'verified', {}))
        self.assertEqual(attempt['test']['exit_status'], 0, attempt)
        self.assertEqual((task['state'], task['error'], task['runs'][-1]['state']), ('ready_for_review', None, 'completed'))
        # The tool result told the model it was a verification-only run with real output.
        reply = json.loads([r for r in self.peers[0].sent if r.get('type') == 'control_response'][-1]['response']['response']['mcp_response']['result']['content'][0]['text'])
        self.assertEqual((reply['verification_only'], reply['exit_status'], reply['attempt']), (True, 0, 1))

    def test_full_claude_answer_is_saved_and_available_in_review_evidence(self):
        from host import ACTOR
        from review_evidence import capture
        self.host.store.add_thread('review-owner', 'p', 'Review owner', 'shared', ACTOR)
        self.scope = dict(projectId='p', channelId='review-owner')
        first = 'The function takes an iterable. Example: briefing(iter(pairs)).'
        last = 'No files changed. The check passed.'
        class Peer(SyntheticClaudePeer):
            def block(peer, text):
                peer.queue.append({'type': 'stream_event', 'event': {'type': 'content_block_start',
                                  'index': 0, 'content_block': {'type': 'text', 'text': ''}}})
                peer.delta(text)
                peer.queue.append({'type': 'assistant', 'message': {'id': 'synthetic-message', 'model': peer.model,
                                  'content': [{'type': 'text', 'text': text}]}})
                peer.queue.append({'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': 0}})
            def on_prompt(peer):
                peer.block(first)
                peer.tool('dervo_run_tests', {}, 1)
            def on_tool_result(peer, mid, result):
                peer.block(last)
                peer.finish(text=last)
        task = self.create()
        args = {**self.scope, 'taskId': task['id']}
        with patch.object(m, 'OwnedProcess', Peer):
            self.host.dispatch('task_approve', {**args, 'scopeVersion': 1})
            detail = self.settle(args)
        saved = self.host.coding.read(task['id'], detail['task']['context'])
        expected = 'Working\n\n' + first + '\n\n' + last
        self.assertEqual(saved['messages'][-1]['text'], expected)
        record = capture(self.host.coding, saved, {'project_id': 'p', 'profile_id': 'p'})
        self.assertEqual(record['answers'][0]['text'], expected)
        self.assertFalse(record['answers'][0]['truncated'])

    def test_no_tool_completion_is_blocked_despite_success_prose(self):
        detail = self.run_task(self.create(), mode='none')
        self.assertEqual(detail['task']['runs'][-1]['state'], 'completed')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('No file attempt', detail['task']['error'])

    def test_failed_result_is_blocked_and_reviewable_only_as_failure(self):
        detail = self.run_task(self.create(), mode='fail')
        self.assertEqual(detail['task']['runs'][-1]['state'], 'failed')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertEqual(detail['task']['error'], 'Synthetic provider failure')
        self.assertTrue(detail['retry']['available'])

    def test_api_error_after_tool_calls_is_blocked_and_retryable(self):
        for mode in ('api_error', 'api_error_vanish'):
            with self.subTest(mode=mode):
                task = self.create()
                detail = self.run_task(task, mode=mode)
                self.assertEqual((detail['task']['state'], detail['task']['runs'][-1]['state']), ('blocked', 'failed'))
                self.assertEqual(detail['task']['error'], 'API Error: Overloaded')
                self.assertEqual([t['state'] for t in detail['task']['tokens']], ['delivered', 'delivered'])
                self.assertFalse(any(r['state'] == 'uncertain' for r in detail['task']['runs']))
                # A file was written before the API failed, so a bare retry is off (an attempt exists) and the
                # written file is offered for restoration. With an uncertain run, restoration would be refused.
                self.assertFalse(detail['retry']['available'])
                self.assertIsNone(self.host.coding.restoration_blocker(detail['task']))
                self.assertEqual([o['path'] for o in detail['restoration']], ['test_sample.py'])
                self.assertNotIn('uncertain', detail['restoration'][0]['reason'] or '')
                self.assertIn('Written. Running the checks next.', detail['task']['messages'][-1]['text'])
                self.assertNotIn('API Error', detail['task']['messages'][-1]['text'], 'the report is not the model speaking')

    def test_a_run_dervo_stops_at_its_own_limit_is_failed_with_the_reason_not_unknown(self):
        """17 Sep: a real full-access run hit the output limit and showed "Outcome not confirmed"."""
        detail = self.run_task(self.create(), mode='output_limit')
        run = detail['task']['runs'][-1]
        self.assertEqual((detail['task']['state'], run['state'], run.get('end_cause')), ('blocked', 'failed', 'host_limit'))
        self.assertEqual(detail['task']['error'], 'The agent sent more than 1024 MiB of output in one run, so Dervo stopped it.')
        # Negative: the same stop while a Dervo tool call is still in flight can't be settled, so it stays unknown.
        other = self.run_task(self.create(), mode='output_limit_mid_tool')
        self.assertEqual((other['task']['state'], other['task']['runs'][-1]['state']), ('disconnected', 'uncertain'))
        self.assertIsNone(other['task']['runs'][-1].get('end_cause'))

    def test_the_turn_deadline_is_for_silence_and_a_hard_cap_still_applies(self):
        self.assertIsNone(m.host_stop_reason(Rejected('Coding provider identity, policy or model was rejected')))
        self.assertIsNone(m.host_stop_reason(RuntimeError(m.HOST_STOPS['idle'])), 'only a Rejected from Dervo itself counts')
        self.assertEqual(m.host_stop_reason(Rejected(m.HOST_STOPS['hard'])), m.HOST_STOPS['hard'])
        self.assertGreater(m.RUN_SECONDS, m.IDLE_SECONDS)

    def test_continue_checking_first_settles_and_carries_on_in_the_same_session(self):
        """Beta.18: Continue, checking first leaves the lost run stopped and continues its own session, told that the step
        it was on has an unknown outcome."""
        task = self.create()
        lost = self.run_task(task, mode='vanish')['task']
        session = lost['session_id']
        current = json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
        current['runs'][-1]['after_close'] = {'record': {'state': 'unsure', 'provider': 'claude',
                                                         'step': {'tool': 'Bash', 'text': 'gh workflow run preview.yml', 'at': 1}}}
        self.host.coding.save(current)
        test = self
        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
            def on_prompt(peer):
                peer.finish()
        args = {**self.scope, 'taskId': task['id']}
        with self.assertRaisesRegex(Rejected, 'Unsupported task fields'):
            self.host.dispatch('task_settle', {**args, 'checking': 'yes'})
        with patch.object(m, 'OwnedProcess', Peer):
            self.host.dispatch('task_settle', {**args, 'checking': True})
            detail = self.settle(args)
        done = detail['task']
        self.assertEqual(len(done['runs']), 2)
        self.assertEqual(done['runs'][0]['end_cause'], 'settled_by_user')
        self.assertEqual(done['session_id'], session, 'the same session carries on')
        said = [msg['text'] for msg in done['messages'] if msg['role'] == 'user'][-1]
        self.assertIn('`gh workflow run preview.yml`', said)
        self.assertIn('its outcome is unknown', said)
        self.assertIn(f'--resume={session}', self.peers[-1].argv)

    def test_vanished_provider_is_unknown_not_failed_and_blocks_review(self):
        task = self.create()
        detail = self.run_task(task, mode='vanish')
        self.assertEqual(detail['task']['state'], 'disconnected')
        self.assertEqual(detail['task']['runs'][-1]['state'], 'uncertain')
        self.assertFalse(detail['retry']['available'])
        self.assertIn('uncertain', self.host.coding.restoration_blocker(detail['task']))
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': 1, 'decision': 'approved'})

    def test_you_can_settle_a_run_dervo_lost_track_of(self):
        """18 Sep: a lost run left the conversation stuck, with a shell script as the only way out."""
        task = self.create()
        detail = self.run_task(task, mode='vanish')
        args = {**self.scope, 'taskId': task['id']}
        settled = self.host.dispatch('task_settle', args)
        settled = settled.get('task', settled)
        self.assertEqual((settled['state'], settled['runs'][-1]['state'], settled['runs'][-1]['end_cause']), ('blocked', 'failed', 'settled_by_user'))
        self.assertIn('You left this stopped.', settled['error'])  # beta.18: Leave it stopped, was Mark as stopped
        self.assertIn('never confirmed what the run did', settled['error'])
        import run_ending
        self.assertEqual(run_ending.classify(settled), dict(kind='stopped', by='you', confirmed=False, left=True,
                                                            last_seen=run_ending.last_seen(settled, settled['runs'][-1]), step=None),
                         'stopped, never the failure X')
        self.assertTrue(all(token['state'] in ('delivered', 'cancelled') for token in settled['tokens']))
        self.assertNotIn('uncertain', self.host.coding.restoration_blocker(settled) or '')
        # Negative: nothing to settle on a task with a confirmed outcome, and a running task must be stopped first.
        with self.assertRaisesRegex(Rejected, 'confirmed outcome already'):
            self.host.dispatch('task_settle', args)
        done = self.create()
        self.run_task(done)
        with self.assertRaisesRegex(Rejected, 'confirmed outcome already'):
            self.host.dispatch('task_settle', {**self.scope, 'taskId': done['id']})

    def test_stop_during_tool_wait_is_interrupted(self):
        test = self
        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
            def on_prompt(peer): peer.tool('dervo_run_tests', {}, 1)   # a verification-only attempt starts running
            def on_tool_result(peer, mid, result): pass                  # never finishes on its own
        task = self.create()
        before = (self.project / 'test_sample.py').read_bytes()
        args = {**self.scope, 'taskId': task['id']}
        with patch.object(m, 'OwnedProcess', Peer):
            self.host.dispatch('task_approve', {**args, 'scopeVersion': 1})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.host.tick()
                if any(t['state'] == 'running' for t in self.host.dispatch('task_detail', args)['task']['tokens']):
                    break
                time.sleep(.005)
            self.host.dispatch('task_stop', args)
            detail = self.settle(args)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and detail['evidence']['attempts'][-1]['state'] == 'running':
                self.host.tick()
                detail = self.host.dispatch('task_detail', args)
                time.sleep(.01)
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual((attempt['kind'], attempt['state']), ('verification', 'cancelled'))
        self.assertEqual(detail['task']['runs'][-1]['state'], 'interrupted')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertTrue(self.peers[0].closed)
        self.assertFalse(detail['retry']['available'], 'cancelled verification evidence must be inspected, not retried blindly')
        self.assertEqual((self.project / 'test_sample.py').read_bytes(), before)

    def test_resume_after_no_attempt_run_keeps_session(self):
        task = self.create()
        first = self.run_task(task, mode='none')
        self.assertTrue(self.peers[0].argv[-1].startswith('--session-id='))
        retried = self.run_task(task, correction=True)
        self.assertEqual(self.peers[1].argv[-1], f"--resume={first['task']['session_id']}")
        self.assertEqual(retried['task']['session_id'], first['task']['session_id'])
        self.assertEqual(len(retried['task']['runs']), 2)
        self.assertEqual(len(retried['evidence']['attempts']), 1)

    def test_host_close_marks_open_claude_run_disconnected(self):
        test = self
        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)
            def on_prompt(peer): pass
        task = self.create()
        args = {**self.scope, 'taskId': task['id']}
        with patch.object(m, 'OwnedProcess', Peer):
            self.host.dispatch('task_approve', {**args, 'scopeVersion': 1})
            for _ in range(20): self.host.tick()
            self.assertEqual(self.host.dispatch('task_detail', args)['task']['state'], 'working')
            self.close_host()
        import sqlite3
        db = sqlite3.connect(str(self.base / 'store' / 'conversations.sqlite3'))
        try:
            row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        finally:
            db.close()
        saved = json.loads(row[0])
        # Since 16.4 a quit pauses the run: the CLI gets its interrupt and ends cleanly inside QUIT_PAUSE_SECONDS, so the
        # task reads paused and can be picked up, not lost (29 Sep).
        self.assertEqual((saved['state'], saved['runs'][-1]['state']), ('blocked', 'interrupted'))
        self.assertTrue(self.peers[0].closed)


class ConnectorWaitTests(unittest.TestCase):
    """dd #8, 23 Sep: connectors were flaky when a thread opened, because the first message could go while Claude Code was
    still connecting them. A full-access run with connectors now asks mcp_status between the handshake and the task."""

    def adapter(self, *, full_access=True, connectors=('claude.ai Slashy', 'claude.ai Claude Docs')):
        self.sent, self.events = [], []
        binding = dict(BINDING)
        task = TrustedTaskBinding('t', binding, lambda t, r: True, tools=('dervo_update_task_guide',), full_access=full_access)
        a = m.ClaudeCodingAdapter(binding, self.sent.append, lambda k, d: self.events.append((k, d)), task=task)
        a.authenticate(VERIFIED)
        a.connectors = list(connectors) if full_access else []
        a.command(CLAUDE)
        a.initialize()
        a.receive({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': self.sent[-1]['request_id'], 'response': {}}})
        return a

    def answer(self, a, servers, subtype='success'):
        request = self.sent[-1]
        self.assertEqual(request['request'], {'subtype': 'mcp_status'})
        a.receive({'type': 'control_response', 'response': {'subtype': subtype, 'request_id': request['request_id'], 'response': {'mcpServers': servers}}})

    def test_the_first_message_waits_until_no_allowed_connector_is_pending(self):
        a = self.adapter()
        self.assertEqual(self.events[-1], ('connectors', {'state': 'connecting', 'pending': ['claude.ai Slashy', 'claude.ai Claude Docs'], 'failed': []}))
        self.assertTrue(a.poll_connectors(now=0))
        asked = len(self.sent)
        self.assertTrue(a.poll_connectors(now=0.1))
        self.assertEqual(len(self.sent), asked, 'one question at a time')
        self.answer(a, [{'name': 'claude.ai Slashy', 'status': 'pending'}, {'name': 'claude.ai Claude Docs', 'status': 'connected'},
                        {'name': 'dervo', 'status': 'connected'}, {'name': 'claude.ai Hidden', 'status': 'pending'}])
        self.assertTrue(a.connecting, 'Slashy is still connecting')
        # A status line while waiting is ignored; the next question waits for the poll interval.
        a.receive({'type': 'system', 'subtype': 'status', 'session_id': a.session_id})
        self.assertTrue(a.poll_connectors(now=time.monotonic() + m.CONNECTOR_POLL_SECONDS))
        self.answer(a, [{'name': 'claude.ai Slashy', 'status': 'connected'}, {'name': 'claude.ai Claude Docs', 'status': 'needs-auth'}])
        self.assertFalse(a.connecting)
        self.assertEqual(self.events[-1], ('connectors', {'state': 'ready', 'pending': [], 'failed': ['claude.ai Claude Docs']}))
        self.assertFalse(a.poll_connectors())
        a.begin('Synthetic task context', 'client')
        self.assertEqual(self.sent[-1]['type'], 'user')

    def test_conversation_output_while_waiting_is_still_refused(self):
        a = self.adapter()
        with self.assertRaisesRegex(Rejected, r'started a turn before Dervo sent the task \(assistant\)'):
            a.receive({'type': 'assistant', 'message': {}})
        with self.assertRaisesRegex(Rejected, 'no longer active'):
            a.receive({'type': 'system', 'subtype': 'status'})

    def test_notices_before_the_task_are_let_through_and_output_is_named(self):
        # Harry, 27 Sep: a Windows run stopped with "Claude emitted conversation events before the task was sent", the
        # first with computer access on and no connector wait. Which event it was wasn't recorded.
        for notice in ({'type': 'system', 'subtype': 'hook_started', 'hook_event': 'SessionStart'},
                       {'type': 'system', 'subtype': 'informational'}, {'type': 'rate_limit_event', 'rate_limit_info': {}},
                       {'type': 'keep_alive'}):
            with self.subTest(notice=notice):
                a = self.adapter()
                a._connected('ready', [], [])
                a.receive(notice)
                a.begin('Synthetic task context', 'client')
                self.assertEqual(self.sent[-1]['type'], 'user')
        # Negative control: conversation output before the task still stops the run, and the message names it. Since
        # beta.18 that's StartedEarly with b18/runstart's wording (the run start launches once more on it).
        for output, name in (({'type': 'system', 'subtype': 'init'}, 'system init'), ({'type': 'stream_event', 'event': {}}, 'stream_event'),
                             ({'type': 'result', 'result': 'x'}, 'result')):
            with self.subTest(output=output):
                a = self.adapter()
                with self.assertRaisesRegex(m.StartedEarly, rf'started a turn before Dervo sent the task \({name}\)'):
                    a.receive(output)

    def test_the_wait_is_bounded_and_says_which_connectors_were_slow(self):
        a = self.adapter()
        start = a.connecting_until - m.CONNECTOR_WAIT_SECONDS
        a.poll_connectors(now=start)
        self.answer(a, [{'name': 'claude.ai Slashy', 'status': 'pending'}, {'name': 'claude.ai Claude Docs', 'status': 'connected'}])
        self.assertFalse(a.poll_connectors(now=start + m.CONNECTOR_WAIT_SECONDS))
        self.assertEqual(self.events[-1], ('connectors', {'state': 'slow', 'pending': ['claude.ai Slashy'], 'failed': []}))

    def test_an_older_claude_code_without_mcp_status_goes_straight_on(self):
        a = self.adapter()
        a.poll_connectors(now=0)
        self.answer(a, [], subtype='error')
        self.assertEqual((a.connecting, self.events[-1][1]['state']), (False, 'unknown'))

    def test_an_echo_of_dervos_own_question_is_not_answered(self):
        a = self.adapter()
        a.poll_connectors(now=0)
        request = self.sent[-1]
        a.receive(dict(request))  # --replay-user-messages can echo what Dervo wrote
        self.assertIs(self.sent[-1], request, 'nothing written back')

    def test_normal_mode_and_no_connectors_never_wait(self):
        for kwargs in ({'full_access': False}, {'connectors': ()}):
            with self.subTest(**kwargs):
                a = self.adapter(**kwargs)
                self.assertFalse(a.connecting)
                self.assertFalse(a.poll_connectors())
                self.assertNotIn('connectors', [kind for kind, _ in self.events])
                self.assertEqual(self.sent[-1]['request']['subtype'], 'initialize', 'no mcp_status question')

    def test_the_turn_holds_the_task_until_connectors_are_ready(self):
        from types import SimpleNamespace
        a = self.adapter()
        turn = m.ClaudeCodingTurn.__new__(m.ClaudeCodingTurn)
        turn.adapter, turn.closed, turn.begun, turn.finishing, turn.stopping, turn.error = a, False, False, False, False, None
        turn.deadline, turn.hard_deadline = time.monotonic() + 60, time.monotonic() + 600
        turn.context = lambda: ('Synthetic task context', 'client')
        turn.emit = lambda *_: None
        turn.process = SimpleNamespace(read=lambda timeout: [], process=SimpleNamespace())
        turn.tick()
        self.assertEqual(self.sent[-1]['request'], {'subtype': 'mcp_status'})
        self.assertFalse(any(message.get('type') == 'user' for message in self.sent), 'the task waits')
        self.answer(a, [{'name': 'claude.ai Slashy', 'status': 'connected'}, {'name': 'claude.ai Claude Docs', 'status': 'connected'}])
        turn.tick()
        self.assertEqual(self.sent[-1]['type'], 'user')


class FirstRunConnectorListTests(unittest.TestCase):
    """Beta.15 (Bryce, 24 Sep): a thread Home created, first message, right after Dervo opened. The account's connector
    list wasn't there yet, so the run launched with no connector allowed by name and the reply said it couldn't reach
    them; "Try again" worked because the list had arrived by then. The first run now holds its launch until the list is
    in, then does dd #8's wait. The fake provider, a real turn, no CLI or network."""
    HOME = BINDING['home']

    def setUp(self):
        import mcp_connectors
        self.mcp = mcp_connectors
        mcp_connectors.forget(); self.addCleanup(mcp_connectors.forget)
        self.peers, self.events, self.contexts = [], [], 0
        test = self

        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)

            def send(peer, message):
                if message.get('type') == 'control_request' and message['request'].get('subtype') == 'mcp_status':
                    peer.sent.append(message)
                    peer.queue.append({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                       'response': {'mcpServers': [{'name': 'claude.ai Slashy', 'status': 'connected'}]}}})
                    return
                super().send(message)

        def listing_started(claude, home, env, **_):
            with mcp_connectors._lock:
                if home in mcp_connectors._cache:
                    return list(mcp_connectors._cache[home][1])
                mcp_connectors._running[home] = 'listing'  # as names() does: a background listing that hasn't reported yet
            return []
        self.enterContext(patch.object(m, 'OwnedProcess', Peer))
        self.enterContext(patch.object(m, 'auth_status', verified_auth))
        self.enterContext(patch.object(m, 'executable', lambda: CLAUDE))
        self.enterContext(patch.object(mcp_connectors, 'names', listing_started))

    def turn(self):
        binding = dict(BINDING)
        def context():
            self.contexts += 1
            return 'Catch me up from all my connectors', 'run-1'
        turn = m.ClaudeCodingTurn(binding, task=TrustedTaskBinding('home-first', binding, lambda t, r: True, tools=('dervo_update_task_guide',), full_access=True),
                                  context=context, emit=lambda k, d: self.events.append((k, d)))
        self.addCleanup(turn.close)
        return turn

    def listed(self, names):
        with self.mcp._lock:
            if names is not None:
                self.mcp._cache[self.HOME] = (time.monotonic(), names)
            self.mcp._running.pop(self.HOME, None)

    def pump(self, turn):
        for _ in range(200):
            if turn.closed:
                return
            turn.tick()
        self.fail('turn did not settle')

    def test_a_first_message_waits_for_the_connector_list_then_for_the_connectors(self):
        turn = self.turn()
        self.assertEqual((self.peers, self.contexts), ([], 0), 'nothing launched while the list is coming')
        self.assertIn(('connectors', {'state': 'connecting', 'pending': [], 'failed': []}), self.events, 'the rail says it’s connecting')
        for _ in range(5):
            turn.tick()
        self.assertEqual((self.peers, self.contexts), ([], 0))
        self.listed(['claude.ai Slashy', 'claude.ai Gmail'])
        self.pump(turn)
        peer = self.peers[0]
        allowed = peer.argv[peer.argv.index('--allowedTools') + 1].split(',')
        self.assertIn('mcp__claude_ai_Slashy__*', allowed, 'launched with the connectors allowed by name')
        self.assertIn('mcp__claude_ai_Gmail__*', allowed)
        kinds = [(message.get('type'), (message.get('request') or {}).get('subtype')) for message in peer.sent]
        self.assertLess(kinds.index(('control_request', 'mcp_status')), kinds.index(('user', None)), 'dd #8’s wait still runs before the task')
        self.assertEqual(self.contexts, 1)
        self.assertEqual([d['state'] for k, d in self.events if k == 'connectors'], ['connecting', 'connecting', 'ready'])
        self.assertEqual(turn.adapter.state, 'completed')

    def test_a_failed_or_endless_listing_goes_on_without_connectors_and_says_so(self):
        for case in ('failed', 'endless'):
            with self.subTest(case=case):
                self.mcp.forget()
                self.peers, self.events, self.contexts = [], [], 0
                if case == 'endless':
                    self.enterContext(patch.object(m, 'CONNECTOR_LIST_WAIT_SECONDS', 0))
                turn = self.turn()
                if case == 'failed':
                    self.listed(None)  # `claude mcp list` failed: nothing cached, nothing running
                self.pump(turn)
                self.assertEqual(len(self.peers), 1)
                self.assertEqual([d['state'] for k, d in self.events if k == 'connectors'], ['connecting', 'unknown'])
                self.assertNotIn('mcp_status', [(message.get('request') or {}).get('subtype') for message in self.peers[0].sent])
                self.assertEqual(self.contexts, 1)

    def test_a_home_job_with_no_connector_named_keeps_every_server_out(self):
        # Security review S1 (24 Sep): with nothing allowed by name, dropping --strict-mcp-config would load every server
        # with no rule. The adapter launches the normal restricted command instead; with names it's connectors_argv.
        binding = dict(BINDING)
        for names, strict in (([], True), (['claude.ai Slashy'], False)):
            with self.subTest(names=names):
                task = TrustedTaskBinding('home-job', binding, lambda t, r: True, tools=('dervo_update_task_guide',), connectors_only=True)
                a = m.ClaudeCodingAdapter(binding, [].append, lambda *_: None, task=task)
                a.connectors, a.hidden_connectors = list(names), []
                a.authenticate(VERIFIED)
                argv = a.command(CLAUDE)
                self.assertEqual('--strict-mcp-config' in argv, strict)
                self.assertEqual(argv[argv.index('--permission-mode') + 1], 'manual' if strict else 'dontAsk')
                self.assertNotIn('Bash', argv[argv.index('--allowedTools') + 1].split(','))

    def test_a_list_already_there_launches_at_once_as_before(self):
        self.listed(['claude.ai Slashy'])
        turn = self.turn()
        self.assertEqual(len(self.peers), 1, 'no wait when Dervo already has the list')
        self.assertIsNone(turn.listing_until)
        self.pump(turn)
        self.assertEqual(self.contexts, 1)


class RunReliabilityTests(unittest.TestCase):
    """beta.11 run reliability at the adapter: a steered message with an image (#5) and a connector whose tool vanished
    mid-run (#9, failure A), fed the fake CLI's own scenario messages through receive()."""

    def adapter(self, full_access=True):
        self.sent, self.events = [], []
        binding = dict(BINDING)
        task = TrustedTaskBinding('t', binding, lambda t, r: True, tools=('dervo_update_task_guide',), full_access=full_access)
        a = m.ClaudeCodingAdapter(binding, self.sent.append, lambda k, d: self.events.append((k, d)), task=task)
        a.authenticate(VERIFIED)
        a.command(CLAUDE)
        a.state, a.started = 'running', True
        return a

    def test_a_steered_message_carries_its_image_to_claude(self):
        import base64
        a = self.adapter(False)
        image = 'data:image/png;base64,' + base64.b64encode(b'\x89PNG synthetic').decode()
        a.steer('Make it look like this', images=[image])
        content = self.sent[-1]['message']['content']
        self.assertEqual([block['type'] for block in content], ['image', 'text'])
        self.assertEqual(content[0]['source'], {'type': 'base64', 'media_type': 'image/png', 'data': image.removeprefix('data:image/png;base64,')})
        self.assertEqual(content[1], {'type': 'text', 'text': 'Make it look like this'})
        # Negative control: a steer without images is the plain text envelope, as before.
        a.steer('Also check the README')
        self.assertEqual(self.sent[-1]['message']['content'], 'Also check the README')
        self.assertEqual(a.steers_unread, 2)

    def test_a_vanished_connector_tool_ends_the_run_with_a_plain_reason(self):
        from fake_claude_cli import connector_drop_messages, DROPPED_TEXT
        a = self.adapter()
        for message in connector_drop_messages(a.session_id, BINDING['model']):
            a.receive(message)
        a.exited(1)
        steps = [d for k, d in self.events if k == 'native_tool']
        self.assertEqual([(d['name'], d['phase'], d.get('connector')) for d in steps],
                         [('Connector', 'started', 'Wispr Flow'), ('Connector', 'ended', 'Wispr Flow')])
        self.assertTrue(steps[-1]['is_error'] and steps[-1]['connector_dropped'] is True)
        self.assertEqual(a.connector_dropped, 'Wispr Flow')
        kind, terminal = self.events[-1]
        self.assertEqual(kind, 'terminal')
        self.assertEqual((terminal['state'], terminal['error']), ('failed', 'The Wispr Flow connector dropped during this run, so the agent couldn’t finish. Reconnect it in Claude Code and send the request again.'))
        self.assertNotIn(DROPPED_TEXT, terminal['error'], 'the CLI’s own wording is not the reason shown')
        self.assertEqual(self.sent, [], 'no retry: nothing was written back to the CLI for the vanished tool')
        # Negative controls: the same wording for Dervo's own tool and for a native tool is an ordinary failed run.
        for tool in ('mcp__dervo__dervo_read_file', 'Bash'):
            with self.subTest(tool=tool):
                b = self.adapter()
                for message in connector_drop_messages(b.session_id, BINDING['model'], tool=tool, text=f'No such tool available: {tool}'):
                    b.receive(message)
                b.exited(1)
                self.assertIsNone(b.connector_dropped)
                self.assertFalse(any(d.get('connector_dropped') for k, d in self.events if k == 'native_tool'))
                self.assertEqual((self.events[-1][1]['state'], self.events[-1][1]['error']), ('failed', f'No such tool available: {tool}'))
        # A connector tool that errors for another reason isn't a drop either, and a run that carries on to success stays completed.
        c = self.adapter()
        messages = connector_drop_messages(c.session_id, BINDING['model'], text='Rate limited, try again in a minute')
        c.receive(messages[0]); c.receive(messages[1])
        self.assertIsNone(c.connector_dropped)
        c.receive({'type': 'result', 'subtype': 'success', 'is_error': False, 'session_id': c.session_id, 'result': 'Done',
                   'usage': {}, 'modelUsage': {BINDING['model']: {'inputTokens': 1}}})
        c.exited(0)
        self.assertEqual((self.events[-1][1]['state'], self.events[-1][1]['error']), ('completed', None))


if __name__ == '__main__':
    unittest.main()
