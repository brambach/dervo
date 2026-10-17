"""Question discovery across Home and sidebar, using disposable stores and a synthetic peer."""
import json
import unittest
from unittest.mock import patch

import conversation_activity as activity
import pickup
import task_questions
import test_conversation_coding as fixtures


class WorkerQuestionAttentionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConversationCodingTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.grant()
        self.enterContext(self.fixture.peer_patch('hang'))
        self.task_id = self.fixture.request('Investigate while I choose a preference.', 'source')['taskId']
        self.fixture.settle(self.task_id, until=self.fixture.verifying)
        self.host, self.coding = self.fixture.host, self.fixture.host.coding
        self.db = self.host.store.db
        self.task = self.coding.read(self.task_id, dict(project_id='p', thread_id='c', actor='local-user'))
        run = self.task['runs'][-1]
        self.receipt = task_questions.capture(self.coding, self.task,
            dict(token='question-tool', run_id=run['id'], session_id=run['session_id']),
            dict(question_key='preference', ask='Which approach should I use?', options=[]))

    def summary(self):
        return activity.summaries(self.db, ['c'])['c']

    def update_task(self, **changes):
        self.task.update(changes)
        self.coding.save(self.task)

    def test_question_needs_attention_while_worker_still_runs_and_home_names_it(self):
        with patch('pathlib.Path.resolve', side_effect=AssertionError('Home must only read records')):
            item = self.summary()
        self.assertEqual((item['status'], item['running'], item['attention']), ('needs', True, True))
        self.assertEqual(item['pending_question_count'], 1)
        self.assertEqual(item['pending_question'], 'Which approach should I use?')
        facts = pickup.thread_facts(dict(id='c', name='Conversation'), self.task, item, None)
        self.assertEqual((facts['kind'], facts['needs'], facts['question'], facts['next_by']),
                         ('running', True, True, 'agent'))
        self.assertEqual(facts['next'], 'Which approach should I use?')
        self.assertEqual(self.coding.read(self.task_id, dict(project_id='p', thread_id='c', actor='local-user'))['state'], 'working')

    def test_the_live_record_carries_the_question_and_the_running_flag(self):
        """The phone (PR #11) reads these from thread_states: it shows the question and Stop while the worker runs."""
        row = next(row for row in self.host.dispatch('thread_states', {'since': 0})['threads'] if row['id'] == 'c')
        self.assertEqual((row['state'], row['running'], row['pending_question'], row['pending_question_count']),
                         ('needs_you', True, 'Which approach should I use?', 1))

    def test_seen_clears_badge_but_keeps_question_and_dismiss_clears_need(self):
        activity.mark_seen(self.db, 'c')
        item = self.summary()
        self.assertEqual((item['status'], item['attention']), ('needs', False))
        activity.dismiss(self.db, 'c')
        self.assertEqual(self.summary()['status'], 'running')
        self.assertNotIn('pending_question', self.summary())

    def test_skip_clears_question_without_stopping_worker(self):
        self.host.dispatch('task_question_answer', {**self.fixture.scope, 'taskId': self.task_id,
            'questionId': self.receipt['questionId'], 'clientId': 'skip-once', 'answer': {'kind': 'skip'}})
        item = self.summary()
        self.assertEqual((item['status'], item['running']), ('running', True))
        self.assertNotIn('pending_question', item)

    def test_newer_task_wins_even_when_old_question_task_is_saved_again(self):
        newer = {**self.task, 'id': 'newer', 'state': 'answered', 'runs': [], 'messages': []}
        self.coding.save(newer)
        self.coding.save(self.task)
        self.assertNotIn('pending_question', self.summary())

    def test_replaced_stopped_and_lost_runs_dont_advertise_an_answerable_question(self):
        self.update_task(runs=[{**self.task['runs'][-1], 'id': 'replacement'}])
        self.assertNotIn('pending_question', self.summary())
        self.update_task(state='blocked')
        self.assertNotIn('pending_question', self.summary())
        self.update_task(state='working')
        with self.db:
            self.db.execute("UPDATE conversation_task_state SET changed=0 WHERE task_id=?", (self.task_id,))
        self.assertTrue(self.summary()['lost'])
        self.assertNotIn('pending_question', self.summary())

    def test_binding_and_access_changes_remove_question_cue(self):
        with self.db:
            self.db.execute("UPDATE threads SET model='another-model' WHERE id='c'")
        self.assertNotIn('pending_question', self.summary())
        with self.db:
            self.db.execute("UPDATE threads SET model=? WHERE id='c'", (self.task['binding']['model'],))
            self.db.execute('DELETE FROM coding_permissions')
        self.assertNotIn('pending_question', self.summary())

    def test_completed_source_question_still_needs_answer_and_settled_answer_clears_it(self):
        self.update_task(state='answered', runs=[{**self.task['runs'][-1], 'state': 'completed'}])
        item = self.summary()
        self.assertEqual((item['status'], item['running']), ('needs', False))
        question = json.loads(self.db.execute('SELECT document FROM task_questions WHERE id=?',
            (self.receipt['questionId'],)).fetchone()[0])
        question.update(state='answered', delivery='sent')
        with self.db:
            self.db.execute('UPDATE task_questions SET document=? WHERE id=?',
                (json.dumps(question), self.receipt['questionId']))
        self.assertNotIn('pending_question', self.summary())


if __name__ == '__main__':
    unittest.main()
