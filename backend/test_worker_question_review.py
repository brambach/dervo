"""Independent delivery regressions with temporary stores and synthetic provider peers."""
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import patch

import task_questions
import test_conversation_coding as fixtures


class WorkerQuestionReviewTests(unittest.TestCase):
    provider = 'codex'

    def setUp(self):
        self.fixture = fixtures.ConversationCodingTests()
        self.fixture.provider = self.provider
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.grant()
        self.enterContext(self.fixture.peer_patch('hang'))
        first = self.fixture.request('Investigate this project and ask a preference.', 'source')
        self.task_id = first['taskId']
        self.fixture.settle(self.task_id, until=self.fixture.verifying)
        self.host = self.fixture.host
        self.coding = self.host.coding
        self.turn = self.coding.turns[self.task_id]
        self.task = self.detail()
        run = self.task['runs'][-1]
        receipt = task_questions.capture(self.coding, self.task,
            dict(token='question-tool', run_id=run['id'], session_id=run['session_id']),
            dict(question_key='preference', ask='Which local approach should I use?', options=[]))
        self.question_id = receipt['questionId']
        self.args = {**self.fixture.scope, 'taskId': self.task_id, 'questionId': self.question_id,
                     'clientId': 'answer-once', 'answer': {'kind': 'text', 'text': 'Keep the current approach.'}}

    def detail(self):
        return self.fixture.host.dispatch('task_detail', {**self.fixture.scope, 'taskId': self.task_id})['task']

    def answer(self):
        return self.fixture.host.dispatch('task_question_answer', self.args)

    def question(self):
        return next(q for q in self.detail()['questions'] if q['id'] == self.question_id)

    def test_answer_and_transport_intent_are_committed_before_provider_write(self):
        observed = []
        database = self.coding.store.db.execute('PRAGMA database_list').fetchone()[2]

        def inspect_before_write(text):
            with closing(sqlite3.connect(database)) as independent:
                question = json.loads(independent.execute('SELECT document FROM task_questions WHERE id=?',
                    (self.question_id,)).fetchone()[0])
                task = json.loads(independent.execute('SELECT document FROM coding_tasks WHERE id=?',
                    (self.task_id,)).fetchone()[0])
            observed.append((question['delivery'], question['answer_client_id'],
                next(m['steer'] for m in task['messages'] if m.get('question_id') == self.question_id)))

        with patch.object(self.turn, 'steer', side_effect=inspect_before_write) as transport:
            result = self.answer()
            again = self.answer()
        self.assertEqual(observed, [('saved', 'answer-once', 'question_sending')])
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result['answerClientId'], again['answerClientId'])

    def test_uncertain_write_retains_its_receipt_slot_before_later_composer_input(self):
        original = self.turn.steer

        def accepted_then_lost(text):
            original(text)
            raise OSError('Synthetic transport acknowledgement loss')

        with patch.object(self.turn, 'steer', side_effect=accepted_then_lost):
            self.assertEqual(self.answer()['delivery'], 'unknown')
        later = self.fixture.request('A separate composer follow-up.', 'ordinary-after-question')
        self.assertEqual(later['state'], 'queued')
        self.assertEqual(len(self.coding.conversation.queue.items('c')), 1)
        self.turn.emit('steer_read', {})
        self.assertEqual(self.question()['delivery'], 'unknown')
        task = self.detail()
        self.assertFalse(any(m.get('client_id') == 'ordinary-after-question' for m in task['messages']))
        question_message = next(m for m in task['messages'] if m.get('question_id') == self.question_id)
        self.assertEqual(question_message['steer'], 'question_receipt')

    def test_late_receipt_cannot_settle_an_answer_after_its_run_was_replaced(self):
        self.answer()
        current = self.detail()
        current['runs'].append({**current['runs'][-1], 'id': 'replacement-run'})
        self.coding.save(current)
        self.turn.emit('steer_failed', {'error': 'Late refusal from the old run'})
        question_message = next(m for m in self.detail()['messages'] if m.get('question_id') == self.question_id)
        self.assertEqual(question_message['steer'], 'sent')
        self.assertEqual(self.question()['delivery'], 'sent')
        self.assertEqual(self.coding.conversation.queue.items('c'), [])

    def test_restart_before_transport_receipt_keeps_unknown_and_never_resends(self):
        # No quit pause (16.4), so the receipt can't arrive before the host goes: the case a crash or Force Quit leaves.
        # With the pause, a provider that confirms the answer while pausing makes it truly sent.
        from unittest.mock import patch as patch_
        self.answer()
        with patch_('coding_integration.QUIT_PAUSE_SECONDS', 0):
            self.fixture.reopen()
        recovered = self.question()
        self.assertEqual(recovered['delivery'], 'unknown')
        with patch.object(self.fixture.host.coding, 'steer') as steer:
            self.assertEqual(self.answer()['delivery'], 'unknown')
        steer.assert_not_called()
        self.assertEqual(self.fixture.host.coding.conversation.queue.items('c'), [])

    def start_queued_answer_with_lost_cleanup(self):
        self.turn.close()
        self.coding.turns.pop(self.task_id)
        current = self.detail()
        current['state'] = 'answered'
        current['runs'][-1]['state'] = 'completed'
        self.coding.save(current)
        with patch.object(self.coding, 'owner_slot_problem', return_value='Other tasks are running'):
            self.assertEqual(self.answer()['delivery'], 'queued')

        def started(task):
            task['state'] = 'working'
            task['runs'].append({'id': 'following-run', 'state': 'running', 'prior_attempts': 0,
                                 'session_id': 'synthetic-following-session'})
            return self.coding.save(task)

        with patch.object(self.coding, 'start', side_effect=started), patch.object(
                self.coding.conversation.queue, 'remove', side_effect=OSError('Synthetic queue cleanup loss')):
            with self.assertRaisesRegex(OSError, 'queue cleanup loss'):
                self.coding.conversation.queue.drain()
        request = self.coding.conversation.find('c', task_questions.PREFIX + self.question_id)
        self.assertIsNotNone(request)

    def test_started_queued_answer_never_claims_not_sent_after_queue_cleanup_is_lost(self):
        self.start_queued_answer_with_lost_cleanup()
        self.fixture.reopen()
        self.assertIn(self.question()['delivery'], ('sent', 'unknown'))

    def test_cancelling_leftover_queue_never_claims_an_executed_answer_was_cancelled(self):
        from dervo_connections import Rejected
        self.start_queued_answer_with_lost_cleanup()
        self.fixture.reopen()
        item = self.fixture.host.coding.conversation.queue.items('c')[0]
        try:
            self.fixture.host.dispatch('coding_queue_cancel', {**self.fixture.scope, 'queueId': item['id']})
        except Rejected:
            pass
        self.assertIn(self.question()['delivery'], ('sent', 'unknown'))


class ClaudeWorkerQuestionReviewTests(WorkerQuestionReviewTests):
    provider = 'claude'


if __name__ == '__main__':
    unittest.main()
