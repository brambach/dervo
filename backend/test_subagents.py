"""Claude Code's own sub-agents in full-access runs (beta.16.2): the Agent tool is offered and allowed there only, every
sub-agent runs on the run's exact model, and the thread records each Agent call, its task events and its steps. Synthetic
peers and the real coordinator. No CLI, model, account or network.

The event shapes are the Agent SDK's (sdk.d.ts 0.3.220: SDKTaskStartedMessage, SDKTaskProgressMessage,
SDKTaskNotificationMessage, SDKTaskUpdatedMessage) and messages carrying parent_tool_use_id. No live sub-agent run was
recorded for these fixtures."""
import json
import time
import unittest
from unittest.mock import patch

import claude_coding as m
import claude_policy
import coding_integration
import test_full_access as full
from codex_coding import TrustedTaskBinding
from dervo_connections import Rejected
from test_claude_coding import BINDING, CLAUDE, SID, verified_auth

MODEL = BINDING['model']


def assistant(content, parent=None, model=MODEL):
    return {'type': 'assistant', 'session_id': None, 'parent_tool_use_id': parent,
            'message': {'role': 'assistant', 'model': model, 'content': content}}


def results(*ids, parent=None, error=False):
    return {'type': 'user', 'session_id': None, 'parent_tool_use_id': parent,
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': i, 'is_error': error, 'content': 'CHILD-OUTPUT'} for i in ids]}}


def agent_call(tool_id='a1', **extra):
    return {'type': 'tool_use', 'id': tool_id, 'name': 'Agent',
            'input': {'description': 'Find the greeting', 'prompt': 'SECRET-PROMPT', 'subagent_type': 'Explore', **extra}}


def task(subtype, tool_id='a1', task_id='t1', **extra):
    return {'type': 'system', 'subtype': subtype, 'task_id': task_id, 'session_id': None,
            **({'tool_use_id': tool_id} if tool_id else {}), **extra}


class PolicyTests(unittest.TestCase):
    def test_full_access_offers_and_allows_the_agent_tool_and_keeps_every_other_rule(self):
        launch = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, tools=('dervo_update_task_guide',))
        offered = launch[launch.index('--tools') + 1].split(',')
        allowed = launch[launch.index('--allowedTools') + 1].split(',')
        self.assertIn('Agent', offered)
        self.assertIn('Agent', allowed)
        denied = launch[launch.index('--disallowedTools') + 1]
        for rule in ('Bash(git commit:*)', 'Bash(git push:*)', 'Bash(git branch:*)', 'Bash(gh:*)'):
            self.assertIn(rule, denied)
        self.assertEqual(launch[launch.index('--permission-mode') + 1], 'dontAsk')
        self.assertIn('--restricted', launch)
        rules = launch[launch.index('--append-system-prompt') + 1]
        self.assertIn('Agent tool', rules)
        self.assertIn('Never give one isolation "worktree"', rules)
        self.assertNotIn('—', rules)
        # Agent commands off: no Bash, and sub-agents still, since they get only the run's own tools.
        quiet = claude_policy.full_access_argv(CLAUDE, 'model', SID, resume=False, commands=False)
        self.assertEqual(quiet[quiet.index('--tools') + 1], 'Read,Edit,Write,Glob,Grep,WebFetch,WebSearch,Agent,ToolSearch')
        self.assertIn('your own Read', quiet[quiet.index('--append-system-prompt') + 1])
        # Negative: the normal and connectors-only launches never offer it, and Agent isn't a recorded native step.
        for other in (claude_policy.argv(CLAUDE, 'model', SID, resume=False),
                      claude_policy.connectors_argv(CLAUDE, 'model', SID, resume=False)):
            self.assertNotIn('Agent', other[other.index('--tools') + 1].split(','))
            self.assertNotIn('Agent', other[other.index('--allowedTools') + 1].split(','))
        self.assertNotIn('Agent', claude_policy.NATIVE_TOOLS)

    def test_sub_agents_are_pinned_to_the_runs_exact_model(self):
        self.assertEqual(claude_policy.subagent_environment('claude-opus-5-5'),
                         {'CLAUDE_CODE_SUBAGENT_MODEL': 'claude-opus-5-5', 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE': '1',
                          'CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS': '4'})
        for bad in ('', None, 'best'):
            with self.assertRaises(Rejected):
                claude_policy.subagent_environment(bad)


class AdapterTests(unittest.TestCase):
    def adapter(self, full_access=True):
        self.events = []
        binding = dict(BINDING)
        binding_task = TrustedTaskBinding('t', binding, lambda t, r: True, tools=('dervo_update_task_guide',), full_access=full_access)
        adapter = m.ClaudeCodingAdapter(binding, lambda message: None, lambda k, d: self.events.append((k, d)), task=binding_task)
        adapter.authenticate(verified_auth())
        adapter.command(CLAUDE)
        adapter.state, adapter.started = 'running', True
        return adapter

    def of(self, kind):
        return [d for k, d in self.events if k == kind]

    def test_a_sub_agents_call_events_and_steps_are_recorded_without_its_prompt_words_or_output(self):
        a = self.adapter()
        a.receive(assistant([agent_call(run_in_background=False)]))
        a.receive(task('task_started', description='Find the greeting', subagent_type='Explore', prompt='SECRET-PROMPT'))
        a.receive({'type': 'stream_event', 'session_id': None, 'parent_tool_use_id': 'a1',
                   'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'CHILD-WORDS'}}})
        a.receive(assistant([{'type': 'text', 'text': 'CHILD-WORDS'},
                             {'type': 'tool_use', 'id': 's1', 'name': 'Grep', 'input': {'pattern': 'greet', 'path': 'src'}},
                             {'type': 'tool_use', 'id': 's2', 'name': 'Edit', 'input': {'file_path': BINDING['cwd'] + '/greeting.py'}}], parent='a1'))
        a.receive(results('s1', 's2', parent='a1'))
        a.receive(task('task_progress', description='Find the greeting', last_tool_name='Edit',
                       usage={'total_tokens': 1200, 'tool_uses': 2, 'duration_ms': 3400}))
        a.receive(task('task_notification', status='completed', output_file='/tmp/x', summary='Found it in greeting.py and changed it.',
                       usage={'total_tokens': 1500, 'tool_uses': 2, 'duration_ms': 4000}))
        a.receive(results('a1'))
        self.assertEqual(self.of('subagent'), [
            {'id': 'a1', 'phase': 'called', 'description': 'Find the greeting', 'kind': 'Explore', 'background': False, 'isolation': None, 'parent': None},
            {'id': 'a1', 'phase': 'started', 'description': 'Find the greeting'},
            {'id': 'a1', 'phase': 'progress', 'last_tool': 'Edit', 'tokens': 1200, 'tool_uses': 2, 'duration_ms': 3400},
            {'id': 'a1', 'phase': 'finished', 'status': 'completed', 'summary': 'Found it in greeting.py and changed it.',
             'tokens': 1500, 'tool_uses': 2, 'duration_ms': 4000},
            {'id': 'a1', 'phase': 'returned', 'is_error': False}])
        steps = self.of('native_tool')
        self.assertEqual([(d['name'], d['phase'], d.get('parent')) for d in steps],
                         [('Grep', 'started', 'a1'), ('Edit', 'started', 'a1'), ('Grep', 'ended', None), ('Edit', 'ended', None)])
        self.assertEqual(steps[1]['path'], 'greeting.py')
        self.assertEqual(self.of('delta'), [], 'a sub-agent\'s words never join the reply')
        for secret in ('SECRET-PROMPT', 'CHILD-WORDS', 'CHILD-OUTPUT'):
            self.assertNotIn(secret, json.dumps(self.events))
        self.assertEqual(a.state, 'running')

    def test_a_sub_agent_on_another_model_blocks_the_run(self):
        a = self.adapter()
        a.receive(assistant([agent_call()]))
        with self.assertRaisesRegex(Rejected, 'A sub-agent used claude-haiku-4-5-20251001 instead of the selected model'):
            a.receive(assistant([{'type': 'text', 'text': 'hi'}], parent='a1', model='claude-haiku-4-5-20251001'))
        self.assertEqual(a.state, 'blocked')

    def test_claude_codes_own_error_report_for_a_sub_agent_is_not_model_drift(self):
        a = self.adapter()
        a.receive(assistant([agent_call()]))
        a.receive(assistant([{'type': 'text', 'text': 'API Error: Overloaded'}], parent='a1', model='<synthetic>'))
        self.assertEqual((a.state, a.api_error, self.of('delta')), ('running', None, []))

    def test_output_from_a_call_the_run_never_made_is_refused_and_normal_mode_refuses_all_of_it(self):
        a = self.adapter()
        with self.assertRaisesRegex(Rejected, 'Subagent'):
            a.receive(assistant([{'type': 'text', 'text': 'hi'}], parent='toolu_unknown'))
        b = self.adapter()
        with self.assertRaisesRegex(Rejected, 'Subagent'):
            b.receive(results('s1', parent='toolu_unknown'))
        normal = self.adapter(full_access=False)
        normal.receive(assistant([agent_call()]))  # never offered there, so never recorded either
        self.assertEqual(self.of('subagent'), [])
        with self.assertRaisesRegex(Rejected, 'Subagent'):
            normal.receive(assistant([{'type': 'text', 'text': 'hi'}], parent='a1'))

    def test_nested_worktree_and_stopped_sub_agents_and_other_tasks(self):
        a = self.adapter()
        a.receive(assistant([agent_call()]))
        a.receive(assistant([agent_call('a2', isolation='worktree', run_in_background=True)], parent='a1'))
        called = self.of('subagent')
        self.assertEqual((called[1]['parent'], called[1]['isolation'], called[1]['background']), ('a1', 'worktree', True))
        a.receive(assistant([{'type': 'text', 'text': 'nested'}], parent='a2'))  # the nested one's messages are its own
        # A command left running in the background is a task too, and isn't a sub-agent.
        a.receive(task('task_started', tool_id='b1', task_id='bash-1', description='npm run dev'))
        a.receive(task('task_notification', tool_id='b1', task_id='bash-1', status='completed', output_file='/tmp/o', summary='exited'))
        self.assertEqual(len(self.of('subagent')), 2)
        # Found by task_id when an event leaves out tool_use_id; Stop kills it; ambient tasks are skipped.
        a.receive(task('task_started', tool_id='a2', task_id='t2', description='Nested'))
        a.receive(task('task_updated', tool_id=None, task_id='t2', patch={'status': 'killed'}))
        a.receive(task('task_started', tool_id='a1', task_id='t1', description='Ambient', skip_transcript=True))
        self.assertEqual(self.of('subagent')[-1], {'id': 'a2', 'phase': 'finished', 'status': 'stopped', 'summary': None})
        self.assertEqual(len(self.of('subagent')), 4)

    def test_a_worktree_sub_agent_is_refused_before_it_starts_and_others_go_through(self):
        self.events, written = [], []
        binding_task = TrustedTaskBinding('t', dict(BINDING), lambda t, r: True, tools=('dervo_update_task_guide',), full_access=True)
        a = m.ClaudeCodingAdapter(dict(BINDING), written.append, lambda k, d: self.events.append((k, d)), task=binding_task)
        a.authenticate(verified_auth())
        a.command(CLAUDE)
        a.initialize()
        self.assertEqual(written[-1]['request']['hooks'],
                         {'PreToolUse': [{'matcher': 'Agent', 'hookCallbackIds': ['dervo_subagent_worktree']}]})
        a.state = 'running'
        hook = lambda request_id, tool_id, tool_input: a.receive({
            'type': 'control_request', 'request_id': request_id,
            'request': {'subtype': 'hook_callback', 'callback_id': 'dervo_subagent_worktree', 'tool_use_id': tool_id,
                        'input': {'hook_event_name': 'PreToolUse', 'tool_name': 'Agent', 'tool_use_id': tool_id, 'tool_input': tool_input}}})
        a.receive(assistant([agent_call('a1', isolation='worktree')]))
        hook('h1', 'a1', agent_call('a1', isolation='worktree')['input'])
        refused = written[-1]['response']
        self.assertEqual((refused['request_id'], refused['response']['hookSpecificOutput']['permissionDecision']), ('h1', 'deny'))
        self.assertIn('isolation "worktree"', refused['response']['hookSpecificOutput']['permissionDecisionReason'])
        self.assertEqual(self.of('subagent')[-1], {'id': 'a1', 'phase': 'refused'})
        # A nested sub-agent's call it never saw in the stream yet is recorded and refused too.
        hook('h2', 'a9', {'description': 'Nested', 'prompt': 'SECRET-PROMPT', 'isolation': 'worktree'})
        self.assertEqual([d['phase'] for d in self.of('subagent') if d['id'] == 'a9'], ['called', 'refused'])
        # Any other Agent call goes through unchanged.
        a.receive(assistant([agent_call('a2')]))
        hook('h3', 'a2', agent_call('a2')['input'])
        self.assertEqual(written[-1]['response'], {'subtype': 'success', 'request_id': 'h3', 'response': {}})
        self.assertNotIn('refused', [d['phase'] for d in self.of('subagent') if d['id'] == 'a2'])
        self.assertNotIn('SECRET-PROMPT', json.dumps(self.events))
        self.assertEqual(a.state, 'running')

    def test_normal_runs_register_no_hooks_and_answer_none(self):
        written = []
        binding_task = TrustedTaskBinding('t', dict(BINDING), lambda t, r: True, tools=('dervo_update_task_guide',))
        a = m.ClaudeCodingAdapter(dict(BINDING), written.append, lambda k, d: None, task=binding_task)
        a.authenticate(verified_auth())
        a.command(CLAUDE)
        a.initialize()
        self.assertIsNone(written[-1]['request']['hooks'])
        a.state = 'running'
        a.receive({'type': 'control_request', 'request_id': 'h1',
                   'request': {'subtype': 'hook_callback', 'callback_id': 'dervo_subagent_worktree', 'input': {}}})
        self.assertEqual(written[-1]['response']['subtype'], 'error')

    def test_progress_is_passed_on_at_most_every_two_seconds_per_sub_agent(self):
        a = self.adapter()
        a.receive(assistant([agent_call(), agent_call('a2')]))
        clock = [100.0]
        with patch.object(m.time, 'monotonic', lambda: clock[0]):
            for tool_id in ('a1', 'a1', 'a2'):
                a.receive(task('task_progress', tool_id=tool_id, task_id='t-' + tool_id, description='d', usage={'total_tokens': 1, 'tool_uses': 0, 'duration_ms': 1}))
            clock[0] += m.SUBAGENT_PROGRESS_SECONDS
            a.receive(task('task_progress', task_id='t-a1', description='d', usage={'total_tokens': 2, 'tool_uses': 1, 'duration_ms': 2}))
        self.assertEqual([(d['id'], d['tokens']) for d in self.of('subagent') if d['phase'] == 'progress'], [('a1', 1), ('a2', 1), ('a1', 2)])


class RecordTests(unittest.TestCase):
    def test_states_on_the_run(self):
        run = {}
        record = lambda **data: coding_integration.record_subagent(run, {'id': 'a1', **data})
        record(phase='called', description='Look around', kind='Explore', background=True, isolation=None, parent=None)
        self.assertEqual(run['subagents'][0]['state'], 'called')
        record(phase='started', description='Look around')
        record(phase='returned', is_error=False)
        self.assertEqual(run['subagents'][0]['state'], 'returned', 'a background one is only launched when its call returns')
        record(phase='progress', last_tool='Read', tokens=10, tool_uses=1, duration_ms=5)
        record(phase='finished', status='completed', summary='Done.', tokens=20)
        call = run['subagents'][0]
        self.assertEqual({k: call[k] for k in ('state', 'description', 'kind', 'background', 'last_tool', 'tokens', 'summary')},
                         {'state': 'completed', 'description': 'Look around', 'kind': 'Explore', 'background': True,
                          'last_tool': 'Read', 'tokens': 20, 'summary': 'Done.'})
        self.assertNotIn('isolation', call)
        coding_integration.record_subagent(run, {'id': 'a2', 'phase': 'returned', 'is_error': True})
        self.assertEqual(run['subagents'][1]['state'], 'failed')
        coding_integration.record_subagent(run, {'id': 'a3', 'phase': 'called', 'isolation': 'worktree'})
        coding_integration.record_subagent(run, {'id': 'a3', 'phase': 'refused'})
        coding_integration.record_subagent(run, {'id': 'a3', 'phase': 'returned', 'is_error': True})
        self.assertEqual(run['subagents'][2]['state'], 'refused', 'its error result is the refusal, not a failure')


class HostTests(unittest.TestCase):
    """The real coordinator with a scripted Claude Code whose sub-agent edits a file."""
    setUp = full.HostTests.setUp
    grant = full.HostTests.grant
    run_request = full.HostTests.run_request

    def test_a_sub_agents_edit_is_recorded_its_steps_and_summary_are_on_the_run_and_its_model_is_pinned(self):
        self.grant()
        def script(peer):
            peer.assistant([agent_call(run_in_background=False)])
            peer.queue.append(task('task_started', description='Find the greeting', subagent_type='Explore') | {'session_id': peer.session_id})
            peer.queue.append(assistant([{'type': 'text', 'text': 'CHILD-WORDS'},
                                         {'type': 'tool_use', 'id': 's1', 'name': 'Edit', 'input': {'file_path': str(self.project / 'greeting.py')}}],
                                        parent='a1', model=peer.model) | {'session_id': peer.session_id})
            (self.project / 'greeting.py').write_text('def greet():\n    return "hello from a helper"\n')
            peer.queue.append(results('s1', parent='a1') | {'session_id': peer.session_id})
            peer.queue.append(task('task_notification', status='completed', output_file='/tmp/o', summary='Changed the greeting.',
                                   usage={'total_tokens': 900, 'tool_uses': 1, 'duration_ms': 2500}) | {'session_id': peer.session_id})
            peer.queue.append(results('a1') | {'session_id': peer.session_id})
            peer.finish(text='A helper changed the greeting.')
        self.script = script
        detail = self.run_request()
        run_task, peer = detail['task'], self.peers[0]
        self.assertEqual((run_task['state'], run_task['error']), ('ready_for_review', None))
        self.assertEqual(sorted(detail['evidence']['attempts'][-1]['diff']), ['greeting.py'])
        self.assertEqual([(t['tool'], t['state'], t.get('path'), t.get('parent')) for t in run_task['tokens']],
                         [('native:Edit', 'delivered', 'greeting.py', 'a1')])
        self.assertEqual(run_task['runs'][-1]['written'], ['greeting.py'], 'a sub-agent\'s edit counts as the run\'s own')
        call, = run_task['runs'][-1]['subagents']
        self.assertEqual({k: call.get(k) for k in ('id', 'state', 'description', 'kind', 'background', 'summary', 'tokens', 'tool_uses')},
                         {'id': 'a1', 'state': 'completed', 'description': 'Find the greeting', 'kind': 'Explore', 'background': False,
                          'summary': 'Changed the greeting.', 'tokens': 900, 'tool_uses': 1})
        self.assertIn('Agent', peer.argv[peer.argv.index('--allowedTools') + 1].split(','))
        self.assertEqual((peer.env['CLAUDE_CODE_SUBAGENT_MODEL'], peer.env['CLAUDE_CODE_SUBAGENT_MODEL_FORCE']), (peer.model, '1'))
        self.assertNotIn('CHILD-WORDS', json.dumps(run_task['messages']))

    def test_a_sub_agent_the_run_never_heard_finish_is_unknown(self):
        self.grant()
        def script(peer):
            peer.assistant([agent_call(run_in_background=True)])
            peer.queue.append(task('task_started', description='Find the greeting') | {'session_id': peer.session_id})
            peer.finish(text='Stopped early.')
        self.script = script
        detail = self.run_request()
        self.assertEqual(detail['task']['runs'][-1]['subagents'][0]['state'], 'unknown')


if __name__ == '__main__':
    unittest.main()
