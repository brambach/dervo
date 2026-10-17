"""Question storage and delivery with disposable hosts and scripted providers only."""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dervo_connections import Rejected
from test_thread_fanout import ThreadFanoutTests
from test_collaboration import CollaborationTests
import task_questions as q

QUESTION = {'question_key': 'tone', 'ask': 'Which tone should the reply use?', 'options': [
    {'label': 'Direct', 'detail': 'Short and clear'}, {'label': 'Detailed', 'detail': 'Explain the reasoning'}]}


class QuestionTests(ThreadFanoutTests):
    def setUp(self):
        super().setUp()
        self.task.update(state='working', session_id='fixture-session')
        self.task['runs'].append({'id': 'fixture-run', 'session_id': 'fixture-session', 'state': 'running', 'prior_attempts': 0})
        self.host.coding.save(self.task)
        self.turn = SimpleNamespace(closed=False, stopping=False, steer=Mock(),
                                    stop=lambda: None, close=lambda: None)
        self.host.coding.turns[self.task['id']] = self.turn
        self.addCleanup(lambda: self.host.coding.turns.clear())
        self.token = {'run_id': 'fixture-run', 'session_id': 'fixture-session', 'token': 'fixture-token'}

    def question(self, **changes):
        receipt = q.capture(self.host.coding, self.task, self.token, {**QUESTION, **changes})
        return next(row for row in self.current()['questions'] if row['id'] == receipt['questionId'])

    def current(self):
        return self.host.coding.read(self.task['id'], self.task['context'], rebound=True)

    def answer(self, question, answer=None, client='answer-1', **scope):
        return self.host.dispatch('task_question_answer', {**self.args, 'questionId': question['id'],
            'clientId': client, 'answer': answer or {'kind': 'option', 'optionId': question['options'][0]['id']}, **scope})

    def finish(self):
        task = self.current()
        task.update(state='answered', error=None)
        task['runs'][-1]['state'] = 'completed'
        self.host.coding.save(task)
        self.turn.closed = True
        self.task = task

    def test_saved_question_is_bounded_idempotent_and_immediate(self):
        first = self.question()
        self.assertEqual(first['id'], self.question()['id'])
        self.assertEqual(first['state'], 'pending')
        self.assertIsNone(first['answerClientId'])
        self.assertTrue(first['canAnswer'])
        self.assertTrue(all(option['grant'] is None for option in first['options']))
        self.assertEqual(self.current()['state'], 'working')
        self.turn.steer.assert_not_called()
        self.question(question_key='next')
        self.question(question_key='last', options=[])
        with self.assertRaises(Rejected):
            self.question(question_key='excess')
        with self.assertRaises(Rejected):
            self.question(ask='Different question?')

    def test_tool_rejects_privilege_fields_and_invalid_unicode(self):
        from codex_coding import validate_arguments, dynamic_tools
        from claude_policy import mcp_tools
        self.assertEqual(dynamic_tools([q.TOOL])[0]['inputSchema'], mcp_tools([q.TOOL])[0]['inputSchema'])
        for changes in ({'grant': {}}, {'to': 'other'}, {'options': [{'label': 'Run it', 'detail': '', 'command': 'sh'}]},
                        {'options': [QUESTION['options'][0]]*4}, {'ask': '\ud800'}, {'ask': '😀'*201},
                        {'options': [{'label': 'a'*81, 'detail': ''}]}):
            with self.subTest(changes=changes), self.assertRaises(Rejected):
                validate_arguments(q.TOOL, {**QUESTION, **changes})
        for options in ([], QUESTION['options'][:1], QUESTION['options']):
            validate_arguments(q.TOOL, {**QUESTION, 'options': options})

    def test_late_tool_run_role_and_binding_are_rejected(self):
        for changed in ({'origin': 'collaboration'}, {'role': 'reviewer'}, {'state': 'answered'}):
            with self.assertRaises(Rejected):
                q.capture(self.host.coding, {**self.task, **changed}, self.token, QUESTION)
        for changed in ({'run_id': 'old'}, {'session_id': 'old'}):
            with self.assertRaises(Rejected):
                q.capture(self.host.coding, self.task, {**self.token, **changed}, QUESTION)
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='changed' WHERE id=?", (self.channel,))
        with self.assertRaises(Rejected):
            self.question()

    def test_answer_intent_precedes_write_and_retry_does_not_send_twice(self):
        question = self.question()
        def inspect(text):
            saved = q._rows(self.host.coding, self.task)[0]
            self.assertEqual((saved['state'], saved['delivery'], saved['answer_client_id']), ('answered', 'saved', 'answer-1'))
            message = self.current()['messages'][-1]
            self.assertEqual((message['question_id'], message['steer']), (question['id'], 'question_sending'))
            self.assertIn(question['ask'], text)
            self.assertIn('Direct', text)
        self.turn.steer.side_effect = inspect
        result = self.answer(question)
        self.assertEqual(result['delivery'], 'sent')
        self.assertEqual(result['answerClientId'], 'answer-1')
        self.assertEqual(self.answer(question)['answer'], result['answer'])
        self.assertEqual(self.turn.steer.call_count, 1)
        for changes in ({'client': 'other'}, {'answer': {'kind': 'text', 'text': 'Changed'}}):
            with self.assertRaises(Rejected):
                self.answer(question, **changes)
        self.assertEqual(self.host.coding.conversation.queue.items(self.channel), [])

    def test_uncertain_send_blocks_later_steers_and_is_not_retried(self):
        question = self.question()
        self.turn.steer.side_effect = OSError('Write may have reached provider')
        self.assertEqual(self.answer(question)['delivery'], 'unknown')
        self.assertEqual(self.answer(question)['delivery'], 'unknown')
        self.assertEqual(self.turn.steer.call_count, 1)
        self.assertEqual(self.host.coding.conversation.queue.items(self.channel), [])
        with self.assertRaises(Rejected):
            self.host.coding.steer(self.channel, 'A later composer message', 'later')
        q.delivery(self.host.coding, question['id'], 'sent')
        self.assertEqual(self.current()['questions'][0]['delivery'], 'unknown')

    def test_failed_intent_save_never_writes(self):
        question = self.question()
        with patch.object(q, '_save', side_effect=RuntimeError('Synthetic storage failure')):
            with self.assertRaises(RuntimeError):
                self.answer(question)
        self.turn.steer.assert_not_called()
        self.assertIsNone(self.current()['questions'][0]['answer'])

    def test_scope_options_and_answer_size_are_strict(self):
        question = self.question()
        for changes in ({'channelId': 'missing'}, {'projectId': 'missing'}, {'taskId': 'missing'}, {'questionId': 'missing'}):
            with self.assertRaises(Rejected):
                self.answer(question, **changes)
        for answer in ({'kind': 'option', 'optionId': 'invented'}, {'kind': 'skip', 'grant': True},
                       {'kind': 'text', 'text': '\ud800'}, {'kind': 'text', 'text': '😀'*2049},
                       {'kind': 'text', 'text': '   '}):
            with self.assertRaises(Rejected):
                self.answer(question, answer)
        result = self.answer(question, {'kind': 'text', 'text': '😀'*2048})
        self.assertEqual(len(result['answer']['text'].encode()), q.ANSWER_BYTES)

    def test_skip_stale_question_changes_no_execution_permission(self):
        question = self.question()
        before = q.access_stamp(self.host.coding, 'p')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='changed' WHERE id=?", (self.channel,))
        stale = self.current()['questions'][0]
        self.assertEqual((stale['state'], stale['canAnswer'], stale['canSkip']), ('stale', False, True))
        with self.assertRaises(Rejected):
            self.answer(question)
        self.host.require_beta_access = Mock(side_effect=Rejected('Access expired'))
        skipped = self.answer(question, {'kind': 'skip'})
        self.assertEqual((skipped['state'], skipped['delivery']), ('skipped', 'not_sent'))
        self.assertEqual(q.access_stamp(self.host.coding, 'p'), before)
        self.turn.steer.assert_not_called()

    def test_replaced_run_and_new_task_are_stale_and_carry_but_changed_access_refuses(self):
        """Beta.16.1 (Bryce, 26 Sep): a question whose run ended can still be answered, into the thread's next
        message. Before, a replaced run or a newer task refused the answer and left only Clear it."""
        question = self.question()
        current = self.current()
        current['runs'].append({'id': 'replacement', 'state': 'running'})
        self.host.coding.save(current)
        stale = self.current()['questions'][0]
        self.assertEqual((stale['state'], stale['canAnswer'], stale['canCarry']), ('stale', False, True))
        self.assertIn('replaced', stale['detail'])
        self.host.coding.save(self.task)
        with self.host.store.db:
            doc = deepcopy(self.task)
            doc['id'] = 'newer-task'
            self.host.store.db.execute('INSERT INTO coding_tasks VALUES (?,?)', (doc['id'], json.dumps(doc)))
        stale = self.current()['questions'][0]
        self.assertEqual((stale['state'], stale['canCarry']), ('stale', True))
        self.assertIn('newer task', stale['detail'])
        with self.host.store.db:
            self.host.store.db.execute('DELETE FROM coding_tasks WHERE id=?', ('newer-task',))
        self.host.dispatch('coding_permission_revoke', self.scope)
        stale = self.current()['questions'][0]
        self.assertEqual((stale['state'], stale['canCarry'], stale['canSkip']), ('stale', False, True))
        with self.assertRaisesRegex(Rejected, 'access changed'):
            self.answer(question)
        self.turn.steer.assert_not_called()

    def test_a_question_left_by_a_blocked_run_is_answered_into_the_next_message(self):
        """The 26 Sep report: a question asked near the end of a run that ended "Nothing to approve" (blocked, because
        Dervo couldn't record a file) showed only "Claude's run ended before this was answered. Clear it"."""
        question = self.question()
        task = self.current()
        task.update(state='blocked', error='Dervo couldn’t record 1 file this run left (notes.md), so there’s nothing to approve.')
        task['runs'][-1]['state'] = 'completed'
        self.host.coding.save(task)
        self.turn.closed = True
        self.task = task
        stale = self.current()['questions'][0]
        self.assertEqual((stale['state'], stale['canAnswer'], stale['canCarry'], stale['canSkip']), ('stale', False, True, True))
        self.assertEqual([option['label'] for option in stale['options']], ['Direct', 'Detailed'])
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            result = self.answer(question)
        self.assertEqual((result['state'], result['carried']), ('answered', True))
        self.turn.steer.assert_not_called()
        request = self.host.coding.conversation.find(self.channel, q.PREFIX + question['id'])
        self.assertIsNotNone(request, 'the answer went in as the thread’s next message')
        self.assertEqual(request['text'], f"Answer to question {question['id']} asked before its run ended:\n"
                                          'Which tone should the reply use?\n\nMy answer:\nDirect')
        following = self.host.coding.read(request['task_id'], self.task['context'], rebound=True)
        self.assertEqual((following['requested_by'], following['clicked']), ('home', self.task.get('full_access') is True),
                         'it starts with the asking task’s access and no more')
        # Answered once: the same answer again is the saved receipt, and a different one is refused.
        self.assertEqual(self.answer(question)['answerClientId'], 'answer-1')
        with self.assertRaisesRegex(Rejected, 'already has a saved answer'):
            self.answer(question, {'kind': 'text', 'text': 'Something else'}, client='answer-2')

    def test_a_carried_answer_still_refuses_after_the_account_changes(self):
        question = self.question()
        self.finish()
        task = self.current()
        task['state'] = 'blocked'
        self.host.coding.save(task)
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks running'):
            self.assertEqual(self.answer(question)['delivery'], 'queued')
        item = self.host.coding.conversation.queue.items(self.channel)[0]
        binding = self.host.store.binding(self.channel, 'p', actor='local-user')
        launch = {'clientId': item['client_id'], **item['args']}
        self.assertIn('asked before its run ended', launch['text'])
        self.assertIsNotNone(q.validate_launch(self.host.coding, binding, launch))
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='changed' WHERE id=?", (self.channel,))
        with self.assertRaisesRegex(Rejected, 'account, model'):
            q.validate_launch(self.host.coding, self.host.store.binding(self.channel, 'p', actor='local-user'), launch)

    def test_existing_steer_queues_answer_and_cancel_keeps_saved_answer(self):
        question = self.question()
        self.host.coding.steer(self.channel, 'Ordinary reply', 'normal-1')
        result = self.answer(question)
        self.assertEqual(result['delivery'], 'queued')
        self.assertEqual(self.turn.steer.call_count, 1)
        item = self.host.coding.conversation.queue.items(self.channel)[0]
        self.assertEqual(item['client_id'], q.PREFIX+question['id'])
        self.assertEqual((item['args']['origin'], item['args']['clicked']), ('home', False), 'it waits with its source’s access')
        self.host.dispatch('coding_queue_cancel', {**self.scope, 'queueId': item['id']})
        self.assertEqual(self.current()['questions'][0]['delivery'], 'cancelled')
        self.assertIsNotNone(self.current()['questions'][0]['answer'])

    def test_finished_answer_pins_route_and_queued_launch_rechecks(self):
        question = self.question()
        self.finish()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks running'):
            result = self.answer(question)
        self.assertEqual(result['delivery'], 'queued')
        item = self.host.coding.conversation.queue.items(self.channel)[0]
        binding = self.host.store.binding(self.channel, 'p', actor='local-user')
        self.assertIsNone(q.validate_launch(self.host.coding, binding, {'clientId': item['client_id'], **item['args']})['switch_to'])
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET model='changed' WHERE id=?", (self.channel,))
        with patch.object(self.host.coding, 'start') as start:
            self.host.coding.conversation.queue.drain()
        start.assert_not_called()
        self.assertEqual(self.current()['questions'][0]['delivery'], 'not_sent')

    def running_source(self, client, **fields):
        """A new source task in this thread, mid-run with the fixture turn, started the way the composer or Home starts one."""
        self.host.coding.turns.clear()  # the earlier task's fixture turn would make this request wait or join it
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            reply = self.host.dispatch('coding_request', {**self.scope, 'clientId': client, 'text': 'Check the fixtures failures.', **fields})
        task =self.host.coding.read(reply['taskId'], dict(project_id='p', thread_id=self.channel, actor='local-user'))
        task.update(state='working', session_id='fixture-session')
        task['runs'].append({'id': 'fixture-run', 'session_id': 'fixture-session', 'state': 'running', 'prior_attempts': 0})
        self.host.coding.save(task)
        self.turn.closed = False
        self.host.coding.turns[task['id']] = self.turn
        self.task = task
        self.args['taskId'] = task['id']
        return task

    def answered_after_run(self, question):
        self.finish()
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            self.answer(question)
        request = self.host.coding.conversation.find(self.channel, q.PREFIX + question['id'])
        self.assertIsNotNone(request, 'the answer started as its own request once the source finished')
        return self.host.coding.read(request['task_id'], self.task['context'], rebound=True)

    def test_an_answer_after_the_run_gets_only_the_access_its_source_had(self):
        """Security review H1, 23 Sep: an answer sent after the asking task finished went out as a message you typed,
        so a Home job nobody clicked, kept off the shell in a full-access project, got full access through its answer."""
        import conversation_coding
        self.host.coding.turns.clear()  # full access can't be turned on while the fixture turn runs
        self.full_access_project()
        source = self.running_source('home-job', origin='home')
        self.assertNotEqual(source.get('full_access'), True)
        self.assertEqual(source['notice'], conversation_coding.HOME_LIMITED)
        following = self.answered_after_run(self.question())
        self.assertNotEqual(following.get('full_access'), True, 'the answer runs as restricted as the job that asked')
        self.assertEqual((following['requested_by'], following['clicked'], following['notice']), ('home', False, conversation_coding.HOME_LIMITED))
        # Negative control: an answer to a task you typed, which had full access, keeps it.
        typed = self.running_source('typed')
        self.assertIs(typed['full_access'], True)
        following = self.answered_after_run(self.question(question_key='typed'))
        self.assertIs(following['full_access'], True)
        self.assertEqual((following['requested_by'], following['clicked'], following.get('notice')), ('home', True, None))

    def test_a_queued_answer_launches_only_with_its_sources_access(self):
        """An answer waiting in the queue, including one saved before 23 Sep with no origin, can't start with more
        access than the task that asked."""
        question = self.question()
        self.finish()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks running'):
            self.answer(question)
        item = self.host.coding.conversation.queue.items(self.channel)[0]
        self.assertEqual((item['args']['origin'], item['args']['clicked']), ('home', False))
        binding = self.host.store.binding(self.channel, 'p', actor='local-user')
        launch = {'clientId': item['client_id'], **item['args']}
        self.assertIsNotNone(q.validate_launch(self.host.coding, binding, launch))
        legacy = {key: value for key, value in launch.items() if key not in ('origin', 'clicked')}
        for changed in (legacy, {**launch, 'clicked': True}):
            with self.subTest(changed=changed), self.assertRaisesRegex(Rejected, 'access'):
                q.validate_launch(self.host.coding, binding, changed)

    def test_read_only_answer_keeps_read_only_at_queue_launch(self):
        question = self.question()
        saved = q._rows(self.host.coding, self.task)[0]
        saved['read_only'] = True
        q._save(self.host.coding, saved)
        self.finish()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks running'):
            self.answer(question)
        item = self.host.coding.conversation.queue.items(self.channel)[0]
        self.assertTrue(item['args']['readOnly'])
        binding = self.host.store.binding(self.channel, 'p', actor='local-user')
        with self.assertRaises(Rejected):
            q.validate_launch(self.host.coding, binding, {'clientId': item['client_id'], **item['args'], 'readOnly': False})

    def test_recovery_preserves_answers_and_never_replays_incomplete_send(self):
        question = self.question()
        self.answer(question)
        q.recover(self.host.coding)
        self.assertEqual(self.current()['questions'][0]['delivery'], 'unknown')
        self.assertEqual(self.answer(question)['delivery'], 'unknown')
        self.assertEqual(self.turn.steer.call_count, 1)
        self.assertEqual(self.host.coding.conversation.queue.items(self.channel), [])

    def test_tool_offer_is_owner_only_and_preserves_fanout_session_marker(self):
        from coding_integration import during_task
        task = deepcopy(self.task)
        task.update(question_session_id=task['session_id'], thread_proposal_session_id=task['session_id'], memory_session_id=task['session_id'])
        tools = self.host.coding.tools_for(task)
        self.assertIn(q.TOOL, tools)
        self.assertIn('dervo_propose_thread', tools)
        self.assertIn('continue independent work', during_task(tools).lower())
        for changes in ({'role': 'reviewer'}, {'origin': 'collaboration'}, {'question_session_id': 'legacy'}):
            self.assertNotIn(q.TOOL, self.host.coding.tools_for({**task, **changes}))
        for changes in ({'read_only': True}, {'full_access': True}):
            self.assertIn(q.TOOL, self.host.coding.tools_for({**task, **changes}))


class ProviderQuestionMixin:
    def setUp(self):
        super().setUp()
        # The source-only test checkout has no bundled Codex executable. The
        # existing peer already replaces OwnedProcess; keep path discovery synthetic too.
        self.enterContext(patch('codex_coding_policy.executable', return_value='/synthetic/codex'))

    def test_provider_saves_question_then_keeps_working(self):
        with self.peer_patch(lambda *_: [('tool', q.TOOL, QUESTION), ('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('finish', 'I inspected the greeting while the question waits.')]):
            started = self.request('Review the greeting and ask which tone to use.')
            detail = self.settle(started['taskId'])
        task = detail['task']
        self.assertEqual(task['state'], 'answered')
        self.assertEqual(len(task['questions']), 1)
        self.assertTrue(task['questions'][0]['canAnswer'])
        self.assertTrue(all(token['success'] for token in task['tokens']))
        self.assertEqual(task['question_session_id'], task['session_id'])
        self.assertIn('Question saved', json.dumps(self.peers[-1].results))
        self.reopen()
        self.assertEqual(self.detail(task['id'])['task']['questions'][0]['id'], task['questions'][0]['id'])

    def test_question_answer_does_not_requeue_when_provider_ends_without_receipt(self):
        with self.peer_patch(lambda *_: [('tool', q.TOOL, QUESTION), ('hang',)]):
            started = self.request('Ask which tone to use.')
            task = self.settle(started['taskId'], until=lambda d: bool(d['task']['questions']))['task']
            question = task['questions'][0]
            # The synthetic transport can accept a steer without pretending the
            # provider consumed it. Ending then must not queue a duplicate answer.
            turn = self.host.coding.turns[task['id']]
            with patch.object(turn, 'steer', return_value=None):
                receipt = self.host.dispatch('task_question_answer', {**self.scope, 'taskId': task['id'],
                    'questionId': question['id'], 'clientId': 'answer-1', 'answer': {'kind': 'text', 'text': 'Use a direct tone.'}})
            self.assertEqual(receipt['delivery'], 'sent')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': task['id']})
            detail = self.settle(task['id'])
        self.assertEqual(detail['task']['questions'][0]['delivery'], 'unknown')
        self.assertEqual(self.host.coding.conversation.queue.items(self.scope['channelId']), [])


class CodexQuestionProviders(ProviderQuestionMixin, CollaborationTests):
    pass


class ClaudeQuestionProviders(ProviderQuestionMixin, CollaborationTests):
    owner_provider = 'claude'


def load_tests(loader, tests, pattern):
    return unittest.TestSuite([QuestionTests(name) for name in QuestionTests.__dict__ if name.startswith('test_')]
        + [cls(name) for cls in (CodexQuestionProviders, ClaudeQuestionProviders)
           for name in ProviderQuestionMixin.__dict__ if name.startswith('test_')])
