"""Clicks typed into Home (home_commands, 9 Oct 2026): "approve it", "stop the picker one", "use option 2", "allow it"
and "archive that thread" act through the same host operation the button calls, with its holds, and Home's reply is
ready at once with no model call. Real host, store and router fixture; the coding tasks are stand-ins, since their own
operations are covered in test_coding_integration."""
import importlib
import unittest
import unittest.mock

import home_commands
import thread_router
from dervo_connections import Rejected
from test_thread_router import RouterFixture


def task(task_id, state, **more):
    return dict(dict(id=task_id, state=state, origin=None, messages=[dict(role='user', text='Make the warning red')],
                     acceptance={}, completion={}, collaboration={}, review=None, runs=[], questions=[]), **more)


class CommandFixture(RouterFixture):
    def setUp(self):
        super().setUp()
        self.tasks, self.states, self.calls = {}, {}, []
        self.detail = lambda args: dict(task=dict(state='ready_for_review'), evidence=dict(attempts=[dict(number=2, kind='run')]),
                                        rebound=False, retry=dict(available=False), carried=None)
        self.saved = lambda args: dict(commit=dict(sha='abc123'))
        real = self.host.dispatch
        def dispatch(operation, args):
            if operation == 'tasks':
                return [dict(item) for item in self.tasks.get(args['channelId'], [])]
            if operation == 'task_detail':
                return self.detail(args)
            if operation in ('task_review', 'task_approve', 'task_stop', 'task_question_answer', 'task_undo'):
                self.calls.append((operation, dict(args)))
                return self.saved(args) if operation == 'task_review' else {}
            if operation.startswith('conversation'):
                self.calls.append((operation, dict(args)))
            return real(operation, args)
        patcher = unittest.mock.patch.object(self.host, 'dispatch', dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)
        threads = thread_router.threads
        def stated(*args, **kwargs):
            return [dict(row, state=self.states.get(row['id'], row.get('state'))) for row in threads(*args, **kwargs)]
        patcher = unittest.mock.patch.object(thread_router, 'threads', stated)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, action, thread=None, confidence=0.9):
        self.router.route_answer = dict(kind='home', thread_id=thread, confidence=confidence, why='A thread action',
                                        candidates=[], command=action)

    def acted(self, operation):
        return [args for name, args in self.calls if name == operation]


class ApproveTests(CommandFixture):
    def test_approve_it_approves_the_one_result_waiting_like_catch_up_s_button_and_offers_undo(self):
        _, picker, sidebar = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        self.command('approve')
        reply = self.say('approve it', client='client-cmd00001')['entries'][-1]
        self.assertEqual(self.prompts, [], 'no model call')
        self.assertEqual(reply['state'], 'ready')
        self.assertEqual(self.acted('task_review'), [dict(projectId='p', channelId=picker, taskId='t1', attemptNumber=2, decision='approved')])
        change = reply['changes'][0]
        self.assertEqual(change['line'], 'Approved and committed the result in Connector picker (Make the warning red).')
        self.assertTrue(change['can_undo'])
        # Undo reverts that commit through task_undo, as the thread's own Undo does.
        index = next(n for n, entry in enumerate(self.orch('list')['entries']) if entry['id'] == reply['id'])
        self.orch('undo', entryId=reply['id'], change=0)
        self.assertEqual(self.acted('task_undo'), [dict(projectId='p', channelId=picker, taskId='t1')])
        self.assertEqual(self.orch('list')['entries'][index]['changes'][0]['line'], 'Reverted that commit in Connector picker.')
        logged = self.decisions('route')
        self.assertTrue(logged[-1]['applied'])

    def test_holds_the_button_has_hold_here_too(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.command('approve', picker)
        for held in (dict(completion=dict(stale=True)), dict(origin='collaboration'), dict(review='rejected'),
                     dict(acceptance=dict(required=False)), dict(collaboration=dict(review=dict(phase='initial'))),
                     dict(runs=[dict(state='uncertain')])):
            with self.subTest(held=held):
                self.tasks[picker] = [task('t1', 'ready_for_review', **held)]
                reply = self.say('approve the picker one', client=f'client-hold{len(self.calls):04d}x{id(held) % 1000:03d}')['entries'][-1]
                self.assertEqual(reply['text'], 'Nothing in Connector picker is waiting for your approval right now.')
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        self.detail = lambda args: dict(task=dict(state='ready_for_review'), evidence=dict(attempts=[dict(number=2, kind='run')]),
                                        rebound=False, retry=dict(available=False), carried=dict(here='the stopped run'))
        reply = self.say('approve the picker one', client='client-carried1')['entries'][-1]
        self.assertIn('includes files the stopped run changed before the handoff', reply['text'])
        self.assertEqual(self.acted('task_review'), [], 'nothing approved')

    def test_two_waiting_asks_which_and_approves_nothing(self):
        _, picker, sidebar = self.world()
        self.states.update({picker: 'needs_you', sidebar: 'needs_you'})
        self.tasks.update({picker: [task('t1', 'ready_for_review')], sidebar: [task('t2', 'needs_permission', scope_version=1)]})
        self.command('approve')
        reply = self.say('approve it', client='client-which01')['entries'][-1]
        self.assertEqual(reply['text'], 'Which one should I approve: Connector picker or Sidebar polish? Open the one you mean.', 'the stand-in router names nothing')
        self.assertEqual(self.calls, [])
        # Named, the scope is approved and the run starts, as "Approve and start" does.
        self.command('approve', sidebar)
        reply = self.say('approve the sidebar one', client='client-which02')['entries'][-1]
        self.assertEqual(self.acted('task_approve'), [dict(projectId='p', channelId=sidebar, taskId='t2', scopeVersion=1)])
        self.assertEqual(reply['changes'][0]['line'], 'Approved the plan in Sidebar polish (Make the warning red) and started it.')

    def test_merge_and_commit_aren_t_approve_and_push_and_delete_never_act(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        for text, action in (('merge it', 'approve'), ('commit that', 'approve'), ('push it', 'push'), ('delete that thread', 'delete')):
            self.command(action, picker)
            self.say(text, client='client-' + text.replace(' ', '-')[:12].ljust(8, 'x'))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.prompts), 4, "Home's model answers each, as before")

    def test_a_host_refusal_is_the_reply(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        def refuse(args):
            raise Rejected('Review requires a confirmed stopped provider')
        self.saved = refuse
        self.command('approve', picker)
        reply = self.say('approve it', client='client-refuse1')['entries'][-1]
        self.assertEqual(reply['text'], "I couldn’t do that in Connector picker: Review requires a confirmed stopped provider.")


class OtherCommandTests(CommandFixture):
    def test_stop_pauses_every_run_in_the_named_thread(self):
        _, picker, _ = self.world()
        self.states[picker] = 'working'
        self.tasks[picker] = [task('t1', 'working'), task('t2', 'idle'), task('t3', 'idle', collaboration=dict(review=dict(phase='final')))]
        self.command('stop', picker)
        reply = self.say('stop the picker one', client='client-stop0001')['entries'][-1]
        self.assertEqual([args['taskId'] for args in self.acted('task_stop')], ['t1', 't3'])
        self.assertEqual(reply['changes'][0]['line'], 'Paused 2 runs in Connector picker.')
        self.assertFalse(reply['changes'][0]['can_undo'])

    def test_option_n_answers_the_one_open_question(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        question = dict(id='q1', canAnswer=True, options=[dict(id='o1', label='Monthly'), dict(id='o2', label='Yearly')])
        self.tasks[picker] = [task('t1', 'working', questions=[question])]
        self.router.route_answer = None
        reply = self.say('use option 2', client='client-option01')['entries'][-1]
        answered = self.acted('task_question_answer')
        self.assertEqual(len(answered), 1)
        self.assertEqual((answered[0]['questionId'], answered[0]['answer']), ('q1', dict(kind='option', optionId='o2')))
        self.assertEqual(reply['changes'][0]['line'], 'Answered Connector picker with option 2: Yearly.')
        reply = self.say('option 3', client='client-option02')['entries'][-1]
        self.assertEqual(reply['text'], 'That question in Connector picker has 2 options: 1. Monthly; 2. Yearly.')

    def test_allow_it_starts_the_scope_waiting_and_nothing_waiting_says_so(self):
        _, picker, _ = self.world()
        self.router.route_answer = None
        reply = self.say('allow it', client='client-allow001')['entries'][-1]
        self.assertEqual(reply['text'], 'Nothing needs your go-ahead right now.')
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'needs_permission', scope_version=3)]
        self.say('yes, allow it', client='client-allow002')
        self.assertEqual(self.acted('task_approve'), [dict(projectId='p', channelId=picker, taskId='t1', scopeVersion=3)])

    def test_archive_pin_and_their_undo_use_the_sidebar_s_operations(self):
        _, picker, _ = self.world()
        self.command('archive', picker)
        reply = self.say('archive the picker thread', client='client-arch0001')['entries'][-1]
        self.assertEqual(reply['changes'][0]['line'], 'Archived Connector picker.')
        self.assertIn(picker, {row['id'] for row in self.host.dispatch('conversations_archived', dict(projectId='p'))})
        self.orch('undo', entryId=reply['id'], change=0)
        self.assertNotIn(picker, {row['id'] for row in self.host.dispatch('conversations_archived', dict(projectId='p'))})
        self.command('pin', picker)
        reply = self.say('pin that thread', client='client-pin00001')['entries'][-1]
        self.assertEqual(reply['changes'][0]['line'], 'Pinned Connector picker.')
        self.assertIn(picker, self.host.dispatch('conversation_pins', dict(projectId='p')))
        self.command('archive')
        reply = self.say('archive it', client='client-arch0002')['entries'][-1]
        self.assertEqual(reply['text'], 'Say which thread to archive, like "archive the login thread".')

    def test_a_tampered_undo_runs_nothing_else(self):
        _, picker, _ = self.world()
        self.command('pin', picker)
        reply = self.say('pin that thread', client='client-tamper01')['entries'][-1]
        db = self.host.store.db
        import json
        row = db.execute('SELECT document FROM orchestrator_entries WHERE id=?', (reply['id'],)).fetchone()
        document = json.loads(row['document'])
        document['changes'][0]['undo'] = dict(op='conversation_delete', args=dict(channelId=picker))
        with db:
            db.execute('UPDATE orchestrator_entries SET document=? WHERE id=?', (json.dumps(document), reply['id']))
        with self.assertRaises(Rejected):
            self.orch('undo', entryId=reply['id'], change=0)

    def test_while_home_is_thinking_only_a_named_thread_acts(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        with unittest.mock.patch('project_orchestrator.Orchestrator.ahead', lambda _self, _scope: [dict(id='x')]):
            self.command('approve', picker)
            self.say('approve it', client='client-ahead001')
            self.assertEqual(self.calls, [], '"it" may mean what the answer ahead is about to start')
            self.say('approve the picker one', client='client-ahead002')
            self.assertEqual(len(self.acted('task_review')), 1)

    def test_an_open_home_question_keeps_option_n_for_home(self):
        # Home's own last reply asked something: "use option 2" answers that, so the model reads it.
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        question = dict(id='q1', canAnswer=True, options=[dict(id='o1', label='Monthly'), dict(id='o2', label='Yearly')])
        self.tasks[picker] = [task('t1', 'working', questions=[question])]
        self.router.route_answer = None
        found = home_commands.Commands(self.home(), dict(project_id='p', profile_id='a'), 'you').answer(
            'use option 2', [], None, False, asking=True)
        self.assertIsNone(found)

    def home(self):
        from project_orchestrator import Orchestrator
        return Orchestrator(self.host.store, host=self.host)


class ReviewFixTests(CommandFixture):
    def test_a_thread_the_router_reads_it_as_for_keeps_it_when_nothing_there_is_a_click(self):
        # Review, 9 Oct: "allow it" read as for Picker, with nothing waiting there, never starts Sidebar's plan.
        _, picker, sidebar = self.world()
        self.states.update({picker: 'needs_you', sidebar: 'needs_you'})
        self.tasks[sidebar] = [task('t2', 'needs_permission', scope_version=1)]
        self.sure(picker)
        self.say('allow it', client='client-guess001')
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.prompts), 1, 'it goes on as before: Home reads it with the picker as the guess')

    def test_a_chat_turn_running_is_never_called_nothing_running(self):
        _, picker, _ = self.world()
        self.states[picker] = 'working'
        threads = thread_router.threads
        with unittest.mock.patch.object(thread_router, 'threads', lambda *a, **k: [dict(row, running=row['id'] == picker) for row in threads(*a, **k)]):
            self.command('stop', picker)
            reply = self.say('stop the picker one', client='client-turn0001')['entries'][-1]
        self.assertEqual(reply['text'], 'Connector picker is running something Home can’t pause. Use Stop in the thread.')

    def test_while_home_asks_or_offers_only_a_named_thread_acts(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        self.command('approve', picker)
        found = home_commands.Commands(self.home(), dict(project_id='p', profile_id='a'), 'you').answer(
            'approve it', thread_router.threads(self.host.store.db, 'p', 'you'), dict(kind='home', thread_id=picker, command='approve', confidence=0.9),
            False, asking=True)
        self.assertIsNone(found)

    def test_stop_everything_pauses_every_thread_s_runs(self):
        _, picker, sidebar = self.world()
        self.states.update({picker: 'working', sidebar: 'working'})
        self.tasks.update({picker: [task('t1', 'working')], sidebar: [task('t2', 'working')]})
        self.router.route_answer = None
        reply = self.say('stop everything', client='client-every001')['entries'][-1]
        self.assertEqual(sorted(args['taskId'] for args in self.acted('task_stop')), ['t1', 't2'])
        self.assertEqual(reply['changes'][0]['line'], 'Paused 2 runs in Connector picker and Sidebar polish.')

    def test_a_plan_reaching_past_its_files_needs_its_thread_named(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'needs_permission', scope_version=1, project_access=True)]
        self.router.route_answer = None
        reply = self.say('approve', client='client-reach001')['entries'][-1]
        self.assertEqual(self.calls, [])
        self.assertTrue(reply['text'].startswith('Connector picker wants to work beyond its own files.'))

    def test_an_answer_s_request_id_comes_from_the_send(self):
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'working', questions=[dict(id='q1', canAnswer=True, options=[dict(id='o1', label='A')])])]
        self.router.route_answer = None
        self.say('use option 1', client='client-ident001')
        self.assertEqual(self.acted('task_question_answer')[0]['clientId'], 'home-client-ident001')

    def home(self):
        from project_orchestrator import Orchestrator
        return Orchestrator(self.host.store, host=self.host)


class PhoneTests(CommandFixture):
    def test_a_paired_phone_s_typed_approve_never_acts(self):
        # Review of #46, 9 Oct: a phone can't approve, commit or push (companion/README.md). Its Home message goes to
        # Home's model as before; phone approvals have their own confirm flow.
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review'), task('t2', 'working')]
        for text, action in (('approve the picker one', 'approve'), ('stop the picker one', 'stop'), ('archive the picker thread', 'archive')):
            self.command(action, picker)
            self.say(text, client='phone-' + text.replace(' ', '')[:20])
        self.router.route_answer = None
        self.say('approve', client='phone-bare00001')
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.prompts), 4)

class RealRouterTests(CommandFixture):
    def test_the_real_router_reads_these_as_commands(self):
        thread_router._module = importlib.import_module('thread_routing')
        _, picker, _ = self.world()
        self.states[picker] = 'needs_you'
        self.tasks[picker] = [task('t1', 'ready_for_review')]
        reply = self.say('approve it', client='client-real0001')['entries'][-1]
        self.assertEqual(self.prompts, [])
        self.assertEqual(len(self.acted('task_review')), 1, reply)
        self.say('pin the connector picker thread', client='client-real0002')
        self.assertIn(picker, self.host.dispatch('conversation_pins', dict(projectId='p')))


    def test_the_which_reply_s_example_is_one_the_router_reads(self):
        thread_router._module = importlib.import_module('thread_routing')
        _, picker, sidebar = self.world()
        self.states.update({picker: 'needs_you', sidebar: 'needs_you'})
        self.tasks.update({picker: [task('t1', 'ready_for_review')], sidebar: [task('t2', 'ready_for_review')]})
        reply = self.say('approve it', client='client-realwh01')['entries'][-1]
        self.assertEqual(self.calls, [])
        example = reply['text'].split('Say it like "')[1].split('"')[0]
        self.say(example, client='client-realwh02')
        self.assertEqual(len(self.acted('task_review')), 1, example)

if __name__ == '__main__':
    unittest.main()
