"""Beta.18: Claude Code's status lines before the task no longer stop a run (notes/STOPPING-explore-20261005.md, "Top
priority"). Bryce kept getting "Claude emitted conversation events before the task was sent", mostly in subthreads.
Claude Code 2.1.284 writes system/commands_changed after it answers the handshake and as connectors connect, and
system/peer_message_hold when crossSessionInbound refuses another session's message. Dervo read a whole batch before it
sent the task, so a line in the same batch as the handshake answer or the last connector-status answer ended the run.

Synthetic stream-json peers that hand Dervo whole batches, the way the real pipe does. No CLI, model, account or network."""
import contextlib
import io
import json
import os
import platform_os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import claude_coding as m
import session_continuity
from codex_coding import TrustedTaskBinding
from test_claude_coding import BINDING, CLAUDE, IDENTITY, VERIFIED, SyntheticClaudePeer, verified_auth

CONNECTOR = 'claude.ai Slashy'


def commands_changed(peer):
    # 2.1.284 writes this one without a session_id: {type:"system",subtype:"commands_changed",commands:k}
    return {'type': 'system', 'subtype': 'commands_changed', 'commands': [{'name': 'compact', 'description': '', 'argumentHint': ''}]}


def peer_message_hold(peer):
    return {'type': 'system', 'subtype': 'peer_message_hold', 'state': 'dropped', 'lane': 'socket', 'from': '',
            'outcome': 'refused', 'uuid': '00000000-0000-4000-8000-0000000000aa', 'session_id': peer.session_id}


def keep_alive(peer):
    return {'type': 'keep_alive'}  # 2.1.284: e.enqueue({type:"keep_alive"}) every 30 s while it waits


def command_lifecycle(peer):
    # The queued/refused frames for a peer or queued message: {type:"command_lifecycle",command_uuid,state,uuid,session_id}
    return {'type': 'command_lifecycle', 'command_uuid': '00000000-0000-4000-8000-0000000000cc', 'state': 'refused',
            'uuid': '00000000-0000-4000-8000-0000000000dd', 'session_id': peer.session_id}


def prompt_suggestion(peer):
    return {'type': 'prompt_suggestion', 'suggestion': 'Run the tests', 'uuid': '00000000-0000-4000-8000-0000000000ee',
            'session_id': peer.session_id}


def something_new(peer):
    return {'type': 'some_later_kind', 'subtype': 'whatever'}  # a kind a later Claude Code adds


def rate_limit(peer):
    return {'type': 'rate_limit_event', 'session_id': peer.session_id,
            'rate_limit_info': {'status': 'allowed_warning', 'rateLimitType': 'five_hour', 'utilization': 0.8,
                                'resetsAt': int(time.time()) + 3600}}


def assistant(peer):
    return {'type': 'assistant', 'session_id': peer.session_id, 'parent_tool_use_id': None,
            'message': {'role': 'assistant', 'model': peer.model, 'content': [{'type': 'text', 'text': 'hello'}]}}


def system_init(peer):
    return {'type': 'system', 'subtype': 'init', 'session_id': peer.session_id, 'model': peer.model, 'tools': [], 'mcp_servers': []}


STATUS_LINES = {'commands_changed': commands_changed, 'peer_message_hold': peer_message_hold, 'keep_alive': keep_alive,
                'command_lifecycle': command_lifecycle, 'prompt_suggestion': prompt_suggestion,
                'some_later_kind': something_new, 'rate_limit_event': rate_limit}
LOGGED = {'commands_changed': 'type=system subtype=commands_changed', 'peer_message_hold': 'type=system subtype=peer_message_hold',
          'keep_alive': 'type=keep_alive subtype=-', 'command_lifecycle': 'type=command_lifecycle subtype=-',
          'prompt_suggestion': 'type=prompt_suggestion subtype=-', 'some_later_kind': 'type=some_later_kind subtype=whatever'}
# (a) a batch of its own before the handshake answer, (b) the same batch as the answer, (c) the same batch as the
# connector-status answer that ends the wait. A run without connectors has no (c): its last batch before the task is (b).
PLACES = ('before_answer', 'with_answer', 'with_last_status')


class BatchPeer(SyntheticClaudePeer):
    """Hands over everything queued since the last read in one batch, as transport.OwnedProcess.read does. ``early`` is
    a list of (place, make) for this launch: ``make(peer)`` builds the message to slip in at ``place``."""
    early = ()

    def __init__(self, argv, **kwargs):
        super().__init__(argv, **kwargs)
        self.batches = []
        self.peers.append(self)
        self.early = list(type(self).plan.pop(0)) if type(self).plan else []

    def slip(self, place):
        return [make(self) for where, make in self.early if where == place]

    def send(self, message):
        request = (message.get('request') or {}).get('subtype') if message.get('type') == 'control_request' else None
        if request == 'initialize':
            self.sent.append(message)
            self.initialized = True
            before = self.slip('before_answer')
            if before:
                self.batches.append(before)
            answer = {'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                                'response': {'commands': [], 'output_style': 'default'}}}
            self.batches.append([answer] + self.slip('with_answer'))
            return
        if request == 'mcp_status':
            self.sent.append(message)
            answer = {'type': 'control_response', 'response': {'subtype': 'success', 'request_id': message['request_id'],
                                                                'response': {'mcpServers': [{'name': CONNECTOR, 'status': 'connected'}]}}}
            self.batches.append([answer] + self.slip('with_last_status'))
            return
        super().send(message)  # the task and replies to tool calls fill self.queue
        if self.queue:
            self.batches.append(self.queue[:])
            self.queue.clear()

    def read(self, timeout):
        if self.batches:
            return self.batches.pop(0)
        if self.eof:
            return None
        raise TimeoutError()


class RunStartTests(unittest.TestCase):
    def setUp(self):
        import mcp_connectors
        self.events, self.contexts, self.peers = [], 0, []
        self.temp = tempfile.TemporaryDirectory(dir=platform_os.scratch_root())
        self.addCleanup(self.temp.cleanup)
        self.home = os.path.join(self.temp.name, 'claude home')
        test = self

        class Peer(BatchPeer):
            peers = test.peers
            plan = []
        self.Peer = Peer
        self.enterContext(patch.object(m, 'OwnedProcess', Peer))
        self.enterContext(patch.object(m, 'auth_status', verified_auth))
        self.enterContext(patch.object(m, 'executable', lambda: CLAUDE))
        self.enterContext(patch.object(mcp_connectors, 'names', lambda *a, **k: [CONNECTOR]))
        self.enterContext(patch.object(mcp_connectors, 'cached', lambda home: [CONNECTOR]))
        self.stderr = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def turn(self, *, connectors, plan):
        """``plan`` is one list of (place, make) per launch, in order."""
        self.Peer.plan = [list(each) for each in plan]
        binding = {**BINDING, 'home': self.home, 'session_id': None}
        def context():
            self.contexts += 1
            return 'Synthetic task context', 'run-1'
        task = TrustedTaskBinding('run-start', binding, lambda t, r: True, tools=('dervo_update_task_guide',), full_access=connectors)
        turn = m.ClaudeCodingTurn(binding, task=task, context=context, emit=lambda k, d: self.events.append((k, d)))
        self.addCleanup(turn.close)
        return turn

    def pump(self, turn):
        for _ in range(200):
            if turn.closed:
                return
            turn.tick()
        self.fail('turn did not settle')

    def terminal(self):
        return [d for k, d in self.events if k == 'terminal'][-1]

    def places(self, connectors):
        return PLACES if connectors else PLACES[:2]

    def test_status_lines_before_the_task_reach_running(self):
        for connectors in (False, True):
            for place in self.places(connectors):
                for name, make in STATUS_LINES.items():
                    with self.subTest(connectors=connectors, place=place, line=name):
                        self.events, self.contexts = [], 0
                        self.peers.clear()
                        self.stderr.seek(0); self.stderr.truncate()
                        turn = self.turn(connectors=connectors, plan=[[(place, make)]])
                        self.pump(turn)
                        self.assertEqual(len(self.peers), 1, 'no relaunch needed')
                        peer = self.peers[0]
                        if connectors:
                            self.assertIn('mcp_status', [(s.get('request') or {}).get('subtype') for s in peer.sent], 'dd #8’s wait ran')
                        self.assertEqual([s['type'] for s in peer.sent].count('user'), 1, 'the task went once')
                        self.assertEqual((self.terminal()['state'], turn.adapter.state, self.contexts), ('completed', 'completed', 1))
                        if name == 'rate_limit_event':
                            limits = [d for k, d in self.events if k == 'rate_limits']
                            self.assertEqual(len(limits), 1, 'its limit reading is kept, as after the task')
                            self.assertEqual(limits[0]['primary']['usedPercent'], 80)
                        logged = ([f'Claude Code wrote {LOGGED[name]} before Dervo sent the task; skipped, it starts no turn.']
                                  if name in LOGGED else [])
                        self.assertEqual(self.stderr.getvalue().splitlines(), logged, 'skipped lines are logged by kind, a limit reading isn’t')

    def test_every_status_line_at_once_in_every_place(self):
        """A busy Mac can bunch them up: all of them, everywhere, in one run."""
        for connectors in (False, True):
            with self.subTest(connectors=connectors):
                self.events = []
                self.peers.clear()
                plan = [(place, make) for place in self.places(connectors) for make in STATUS_LINES.values()]
                self.stderr.seek(0); self.stderr.truncate()
                self.pump(self.turn(connectors=connectors, plan=[plan]))
                self.assertEqual((len(self.peers), self.terminal()['state']), (1, 'completed'))
                log = self.stderr.getvalue().splitlines()
                self.assertEqual(len(log), len(LOGGED), 'one line per kind however often it came')
                self.assertEqual(len(set(log)), len(LOGGED))

    def test_turn_output_before_the_task_is_still_refused_and_named(self):
        """The guard that proves no turn ran before Dervo's task (PLAN-native A1) stays. Both launches refused: the second
        refusal ends the run, not started, with what arrived named."""
        for connectors in (False, True):
            for place in self.places(connectors):
                for make, label in ((assistant, 'assistant'), (system_init, 'system init')):
                    with self.subTest(connectors=connectors, place=place, label=label):
                        self.events, self.contexts = [], 0
                        self.peers.clear()
                        self.stderr.seek(0); self.stderr.truncate()
                        turn = self.turn(connectors=connectors, plan=[[(place, make)], [(place, make)]])
                        self.pump(turn)
                        self.assertEqual(len(self.peers), 2, 'one relaunch, no more')
                        data = self.terminal()
                        self.assertEqual((data['state'], data['not_started'], data['host_stop']), ('failed', True, False))
                        self.assertEqual(data['error'], f'Claude Code started a turn before Dervo sent the task ({label})')
                        self.assertEqual(self.contexts, 0, 'the task text was never loaded')
                        for peer in self.peers:
                            self.assertNotIn('user', [s['type'] for s in peer.sent], 'nothing was sent to either launch')
                            self.assertTrue(peer.closed)
                        kind, _, subtype = label.partition(' ')
                        log = self.stderr.getvalue().splitlines()
                        self.assertEqual(log, [f'Claude Code wrote type={kind} subtype={subtype or "-"} before Dervo sent the task, so Dervo launched it once more.',
                                               f'Claude Code wrote type={kind} subtype={subtype or "-"} before Dervo sent the task, so the run stopped.'])

    def test_one_relaunch_then_the_run_goes_on_with_the_same_session(self):
        for connectors in (False, True):
            with self.subTest(connectors=connectors):
                self.events, self.contexts = [], 0
                self.peers.clear()
                turn = self.turn(connectors=connectors, plan=[[('with_answer', assistant)], []])
                self.pump(turn)
                first, second = self.peers
                self.assertEqual(second.session_id, first.session_id, 'the session ID Dervo saved stays the run’s')
                self.assertEqual(second.argv, first.argv, 'the same command')
                self.assertNotIn('user', [s['type'] for s in first.sent])
                self.assertEqual([s['type'] for s in second.sent].count('user'), 1)
                self.assertEqual((self.terminal()['state'], self.contexts), ('completed', 1))
                sessions = [d for k, d in self.events if k == 'session']
                self.assertEqual([d['session_id'] for d in sessions], [first.session_id] * 2, 'the record gets the new pid under the same session')
                self.assertNotIn('terminal', [k for k, _ in self.events][:-1], 'nothing ended between the launches')

    def test_no_relaunch_when_claude_code_saved_the_session_file(self):
        """Claude Code refuses --session-id for a session that already has a file, so the relaunch isn't tried."""
        class Saving(self.Peer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                folder = Path(peer.env['CLAUDE_CONFIG_DIR']) / 'projects' / session_continuity._slug(os.path.realpath(peer.cwd))
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f'{peer.session_id}.jsonl').write_text('')
        self.enterContext(patch.object(m, 'OwnedProcess', Saving))
        self.Peer = Saving
        os.makedirs(self.home)
        self.pump(self.turn(connectors=False, plan=[[('before_answer', assistant)]]))
        self.assertEqual(len(self.peers), 1)
        self.assertEqual(self.terminal()['error'], 'Claude Code started a turn before Dervo sent the task (assistant)')
        self.assertIn('so the run stopped.', self.stderr.getvalue())


class HostRelaunchTests(unittest.TestCase):
    """What the thread's record says during and after the relaunch: the real host, coordinator and store."""
    def setUp(self):
        from host import Host, ACTOR
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.host = Host(base / 'store')
        self.addCleanup(self.host.close)
        project = base / 'project'
        project.mkdir()
        (project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.host.store.add_profile('p', 'claude', base / 'store/profiles/p', IDENTITY, 'personal')
        self.host.store.add_project('p', project, 'p', 'synthetic-claude-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')
        self.peers = []
        test = self

        class Peer(BatchPeer):
            peers = test.peers
            plan = []
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                peer.process.pid = 4242 + len(test.peers)  # each launch its own pid, so the record shows which one it holds
        self.Peer = Peer
        self.enterContext(patch.object(m, 'OwnedProcess', Peer))
        self.enterContext(patch.object(m, 'auth_status', verified_auth))
        self.enterContext(patch.object(m, 'executable', lambda: CLAUDE))
        self.stderr = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def start(self, plan):
        self.Peer.plan = [list(each) for each in plan]
        task = self.host.dispatch('task_create', {**self.scope, 'text': 'Improve the test', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        args = {**self.scope, 'taskId': task['id']}
        self.host.dispatch('task_approve', {**args, 'scopeVersion': 1})
        return args

    def detail(self, args):
        return self.host.dispatch('task_detail', args)['task']

    def tick_until(self, args, done):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            self.host.tick()
            task = self.detail(args)
            if done(task):
                return task
            time.sleep(.01)
        self.fail('timed out')

    def test_the_thread_keeps_working_through_the_relaunch(self):
        args = self.start([[('before_answer', assistant)], []])
        during = self.tick_until(args, lambda task: len(self.peers) == 2)
        self.assertEqual((during['state'], during['error']), ('working', None), 'nothing ended, nothing to show')
        self.assertEqual(len(during['runs']), 1, 'the same run')
        self.assertEqual(during['runs'][-1]['pid'], 4244, 'the record holds the new process, so a restart reaps the right one')
        self.assertEqual(during['runs'][-1]['session_id'], self.peers[0].session_id)
        after = self.tick_until(args, lambda task: task['state'] != 'working')
        self.assertEqual((len(after['runs']), after['runs'][-1]['state']), (1, 'completed'), after['error'])
        self.assertNotIn('end_cause', after['runs'][-1])

    def test_a_second_refusal_reads_as_not_started_with_what_arrived(self):
        import run_ending
        args = self.start([[('with_answer', assistant)], [('with_answer', assistant)]])
        after = self.tick_until(args, lambda task: task['state'] != 'working')
        self.assertEqual(len(self.peers), 2)
        self.assertEqual((after['state'], after['runs'][-1]['state'], after['runs'][-1]['end_cause']), ('blocked', 'failed', 'not_started'))
        self.assertEqual(after['error'], 'Claude Code started a turn before Dervo sent the task (assistant)')
        self.assertEqual(run_ending.classify(after)['kind'], 'not_started', 'no failure X: "Didn’t start. Nothing was sent."')
        self.assertEqual(len(after['runs']), 1)


class AdapterGuardTests(unittest.TestCase):
    """receive() on its own, in both states before the task."""

    def setUp(self):
        self.stderr = io.StringIO()
        self.enterContext(contextlib.redirect_stderr(self.stderr))

    def test_what_is_logged_is_plain_and_once_per_kind(self):
        a = self.adapter()
        for _ in range(3):
            a.receive({'type': 'keep_alive'})
        a.receive({'type': 'assistant"; rm -rf', 'subtype': 'x\ny'})
        self.assertEqual(self.stderr.getvalue().splitlines(), [
            'Claude Code wrote type=keep_alive subtype=- before Dervo sent the task; skipped, it starts no turn.',
            'Claude Code wrote type=assistantrmrf subtype=xy before Dervo sent the task; skipped, it starts no turn.'])

    def adapter(self):
        self.sent, self.events = [], []
        binding = dict(BINDING)
        task = TrustedTaskBinding('t', binding, lambda t, r: True, tools=('dervo_update_task_guide',))
        a = m.ClaudeCodingAdapter(binding, self.sent.append, lambda k, d: self.events.append((k, d)), task=task)
        a.authenticate(dict(VERIFIED))
        a.command(CLAUDE)
        a.initialize()
        return a

    def answer(self, a):
        a.receive({'type': 'control_response', 'response': {'subtype': 'success', 'request_id': self.sent[-1]['request_id'], 'response': {}}})

    def test_only_turn_output_is_refused(self):
        for answered in (False, True):
            for kind, message, refused in (
                    ('status', {'type': 'system', 'subtype': 'status', 'status': None}, None),
                    ('api_retry', {'type': 'system', 'subtype': 'api_retry'}, None),
                    ('notification', {'type': 'system', 'subtype': 'notification'}, None),
                    ('no subtype', {'type': 'system'}, None),
                    ('keep_alive', {'type': 'keep_alive'}, None),
                    ('command_lifecycle', {'type': 'command_lifecycle', 'state': 'queued'}, None),
                    ('prompt_suggestion', {'type': 'prompt_suggestion', 'suggestion': 'x'}, None),
                    ('unknown', {'type': 'something_new'}, None),
                    ('untyped', {'subtype': 'x'}, None),
                    ('odd', {'type': 'assistant"; rm', 'subtype': 7}, None),
                    ('init', {'type': 'system', 'subtype': 'init'}, 'system init'),
                    ('stream_event', {'type': 'stream_event', 'event': {}}, 'stream_event'),
                    ('assistant', {'type': 'assistant', 'message': {}}, 'assistant'),
                    ('user', {'type': 'user', 'message': {}}, 'user'),
                    # Before the task Dervo has written no user message, so no replay can be its own.
                    ('replay', {'type': 'user', 'isReplay': True, 'message': {}}, 'user'),
                    ('result', {'type': 'result', 'subtype': 'success'}, 'result success')):
                with self.subTest(answered=answered, kind=kind):
                    a = self.adapter()
                    if answered:
                        self.answer(a)
                    if refused is None:
                        a.receive(message)
                        self.assertEqual(a.state, 'initialized' if answered else 'initializing')
                    else:
                        with self.assertRaises(m.StartedEarly) as caught:
                            a.receive(message)
                        self.assertEqual(str(caught.exception), f'Claude Code started a turn before Dervo sent the task ({refused})')
                        self.assertEqual(a.state, 'blocked')

    def test_a_status_line_never_changes_the_saved_session(self):
        a = self.adapter()
        with self.assertRaisesRegex(m.Rejected, 'changed the saved conversation ID'):
            a.receive({'type': 'system', 'subtype': 'peer_message_hold', 'session_id': '00000000-0000-4000-8000-0000000000bb'})


if __name__ == '__main__':
    unittest.main()
