"""Observation reads reuse validated evidence without weakening task scope checks."""
import json
import unittest
from unittest.mock import patch

from dervo_connections import Rejected
import test_collaboration


class ObservationReadsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_collaboration.CollaborationTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.host = self.fixture.host
        self.task = self.host.dispatch('task_create', {
            **self.fixture.scope, 'text': 'Check the greeting',
            'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py',
        })

    def test_observation_validates_evidence_once(self):
        coding = self.host.coding
        with patch.object(coding.evidence, 'read', wraps=coding.evidence.read) as read:
            result = coding.read(self.task['id'], self.task['context'])
        self.assertEqual(result['id'], self.task['id'])
        read.assert_called_once_with(self.task['id'], **self.task['context'])

    def test_stored_identity_and_context_must_match_caller(self):
        coding = self.host.coding
        for field in ('id', 'actor', 'project_id', 'thread_id'):
            with self.subTest(field=field):
                document = json.loads(json.dumps(self.task))
                if field == 'id':
                    document['id'] = 'different-task'
                else:
                    document['context'][field] = 'different-scope'
                with self.host.store.db:
                    self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?',
                                               (json.dumps(document), self.task['id']))
                with patch.object(coding.evidence, 'read', wraps=coding.evidence.read) as read:
                    with self.assertRaises(Rejected):
                        coding.read(self.task['id'], self.task['context'])
                    read.assert_not_called()

    def test_other_conversation_is_filtered_before_evidence_work(self):
        coding = self.host.coding
        with patch.object(coding.evidence, 'read', wraps=coding.evidence.read) as read:
            result = self.host.dispatch('tasks', {'projectId': 'p', 'channelId': 'private'})
        self.assertEqual(result, [])
        read.assert_not_called()

    def test_interruption_is_display_metadata_not_a_new_task_state(self):
        binding = self.host.store.binding(**self.task['context'])
        cases = [('blocked', ['interrupted'], [], True), ('blocked', ['failed'], [], False),
                 ('blocked', [], [], False), ('blocked', ['interrupted', 'failed'], [], False),
                 ('blocked', ['uncertain', 'interrupted'], [], False),
                 ('blocked', ['interrupted'], ['uncertain'], False),
                 ('disconnected', ['uncertain'], [], False), ('working', ['interrupted'], [], False)]
        for state, runs, tokens, expected in cases:
            with self.subTest(state=state, runs=runs, tokens=tokens):
                task = {**self.task, 'state': state, 'runs': [{'state': r} for r in runs],
                        'tokens': [{'state': t} for t in tokens], 'error': None}
                self.host.coding.save(task)
                status = self.host._conversation_status(binding, self.fixture.scope)
                self.assertEqual(status['state'], state)
                self.assertEqual(status['interrupted'], expected)
        task.update(state='blocked', runs=[{'state': 'interrupted'}], tokens=[],
                    error='Stopped before provider acknowledgement')
        self.host.coding.save(task)
        status = self.host._conversation_status(binding, self.fixture.scope)
        self.assertTrue(status['interrupted'])
        self.assertEqual(status['detail'], task['error'])


if __name__ == '__main__':
    unittest.main()
