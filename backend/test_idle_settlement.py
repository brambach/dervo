"""Provider-free controls for background settlement cost and pending record recovery."""
import json
import tempfile
import time
import unittest
import test_collaboration
from pathlib import Path
from unittest.mock import patch

from host import Host
from coding_integration import CodingTasks
from dervo_connections import Rejected


class IdleSettlementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'store'
        self.host = Host(self.root)
        self.host.coding = CodingTasks(self.host)
        self.addCleanup(lambda: self.host.close())

    def history(self, count):
        for number in range(count):
            task_id = f'past-{number}'
            self.host.coding.save(dict(id=task_id, state='completed', acceptance=dict(required=False),
                collaboration=dict(review=dict(enabled=True, phase='passed')), runs=[], tokens=[],
                messages=[dict(role='assistant', text='historical result ' * 500)]))
            self.host.coding.collaboration.save(dict(id=task_id, owner_task_id=task_id, root_task_id=task_id,
                status='returned', result=dict(text='historical helper ' * 500)))

    def measure_idle(self):
        loads = 0
        original = json.loads

        def counted(*args, **kwargs):
            nonlocal loads
            loads += 1
            return original(*args, **kwargs)

        start = time.process_time()
        with patch('json.loads', counted):
            for _ in range(100):
                self.host.tick()
        return dict(json_documents_decoded=loads, cpu_seconds=round(time.process_time() - start, 6))

    def test_terminal_history_doesnt_add_repeated_idle_decoding(self):
        empty = self.measure_idle()
        self.history(200)
        historical = self.measure_idle()
        print(json.dumps(dict(empty=empty, historical_200=historical)), flush=True)
        self.assertEqual(historical['json_documents_decoded'], empty['json_documents_decoded'])

    def test_restart_doesnt_resume_scanning_terminal_history(self):
        self.history(20)
        self.host.close()
        self.host = Host(self.root)
        self.host.coding = CodingTasks(self.host)
        self.assertEqual(self.measure_idle()['json_documents_decoded'], 0)

    def test_new_and_rearmed_reviews_settle_after_idle_and_restart(self):
        self.history(20)
        self.host.tick()
        task = dict(id='new-review', state='blocked', error='Stopped before finishing',
            collaboration=dict(review=dict(enabled=True, phase='pending')), runs=[], tokens=[])
        self.host.coding.save(task)
        self.host.tick()
        task = self.host.coding.collaboration.raw_task(task['id'])
        self.assertEqual(task['collaboration']['review']['phase'], 'skipped')
        self.assertEqual(task['collaboration']['review']['error'], 'The owner ended blocked. No independent review was sent.', 'the task error has one home, the task')
        self.assertEqual(self.measure_idle()['json_documents_decoded'], 0)
        task['collaboration']['review']['phase'] = 'correction'
        self.host.coding.save(task)
        self.host.close()
        self.host = Host(self.root)
        self.host.coding = CodingTasks(self.host)
        self.host.tick()
        task = self.host.coding.collaboration.raw_task(task['id'])
        self.assertEqual(task['collaboration']['review']['phase'], 'interrupted')
        self.assertEqual(self.measure_idle()['json_documents_decoded'], 0)

    def test_withdrawn_late_result_survives_restart_and_is_never_delivered(self):
        coding = self.host.coding
        child = dict(id='late-child', state='answered', runs=[], tokens=[],
            context=dict(thread_id='missing', project_id='missing', actor='local-user'),
            messages=[dict(role='assistant', text='A result after stop')])
        coding.save(child)
        coding.collaboration.save(dict(id='withdrawn', owner_task_id='past-owner', root_task_id='past-owner',
            status='withdrawn', child_task_id=child['id'], late_result=None, kind='review', files=[]))
        self.host.close()
        self.host = Host(self.root)
        self.host.coding = CodingTasks(self.host)
        collab = self.host.coding.collaboration
        with patch.object(collab, 'deliver') as deliver:
            self.host.tick()
        deliver.assert_not_called()
        packet = collab.packet('withdrawn')
        self.assertEqual(packet['status'], 'withdrawn')
        self.assertEqual(packet['late_result']['summary'], 'A result after stop')
        self.assertFalse(packet['late_result']['verified'])
        self.assertEqual(self.measure_idle()['json_documents_decoded'], 0)

    def test_rearmed_records_keep_original_order(self):
        coding = self.host.coding
        for task_id in ('first', 'second'):
            coding.save(dict(id=task_id, state='ready_for_review', acceptance=dict(required=False),
                collaboration=dict(review=dict(enabled=True, phase='pending')), runs=[], tokens=[]))
            coding.collaboration.save(dict(id=task_id, owner_task_id=task_id, root_task_id=task_id, status='queued'))
        first = coding.collaboration.raw_task('first')
        first.update(state='completed', collaboration=None)
        coding.save(first)
        first.update(state='ready_for_review', collaboration=dict(review=dict(enabled=True, phase='correction')))
        coding.save(first)
        packet = coding.collaboration.packet('first')
        packet['status'] = 'returned'
        coding.collaboration.save(packet)
        packet['status'] = 'queued'
        coding.collaboration.save(packet)
        for kind in ('review', 'acceptance'):
            self.assertEqual([t['id'] for t in coding.pending_tasks(kind)], ['first', 'second'])
        self.assertEqual([p['id'] for p in coding.collaboration.pending_packets()], ['first', 'second'])

    def test_review_must_pass_before_background_file_validation(self):
        fixture = test_collaboration.CollaborationTests(methodName='runTest')
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        task = fixture.host.dispatch('task_create', {**fixture.scope, 'text': 'Check greeting',
            'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
        coding = fixture.host.coding
        coding.evidence.approve(task['id'], scope_version=1, **task['context'])
        coding.evidence.verify_current(task['id'], **task['context'])
        evidence = coding.evidence.read(task['id'], **task['context'])
        # Recorded test completion is fixture data. This test starts no compiler or provider.
        evidence['attempts'][-1].update(state='verified', test={'exit_status': 0, 'stopped': None})
        coding.evidence._save(evidence)
        task.update(state='ready_for_review', acceptance={'required': False},
                    runs=[{'id': 'fixture-run', 'state': 'completed', 'prior_attempts': 0}])
        snapshot = {path: value['sha256'] for path, value in evidence['attempts'][-1]['after'].items()}
        for phase in ('stopped', 'failed', 'unresolved', 'passed'):
            with self.subTest(phase=phase):
                task['collaboration'] = {'review': {'enabled': True, 'phase': phase, 'snapshot': {}}}
                coding.save(task)
                with patch.object(coding.evidence, '_current', wraps=coding.evidence._current) as current:
                    for _ in range(3):
                        fixture.host.tick()
                current.assert_not_called()
                self.assertEqual(coding.collaboration.raw_task(task['id'])['state'], 'ready_for_review')
        task['collaboration']['review']['snapshot'] = snapshot
        coding.save(task)
        with patch.object(coding.evidence, '_current', wraps=coding.evidence._current) as current:
            fixture.host.tick()
        current.assert_called_once()
        self.assertEqual(coding.collaboration.raw_task(task['id'])['state'], 'completed')

    def test_task_blockers_skip_evidence_reads_and_can_clear_after_restart(self):
        clean = dict(id='blocked-acceptance', state='ready_for_review', acceptance={'required': False},
            context=dict(thread_id='missing', project_id='missing', actor='local-user'),
            runs=[{'id': 'run', 'state': 'completed'}], tokens=[], error=None, review=None)
        cases = [
            {'error': 'Files changed after completion'},
            {'review': 'rejected'},
            {'acceptance': {'required': True}},
            {'runs': [{'id': 'run', 'state': 'uncertain'}]},
            {'tokens': [{'state': 'pending'}]},
            {'tokens': [{'state': 'delivered', 'job_id': 'job', 'job_done': False}]},
        ] + [{'collaboration': {'review': {'phase': phase}}}
             for phase in ('pending', 'stopped', 'failed', 'unresolved')]
        for changes in cases:
            with self.subTest(changes=changes):
                task = {**clean, **changes}
                self.host.coding.save(task)
                self.host.close()
                self.host = Host(self.root)
                self.host.coding = CodingTasks(self.host)
                coding = self.host.coding
                with patch.object(coding.evidence, 'read', side_effect=Rejected('No binding')) as read:
                    for _ in range(10):
                        coding.acceptance.settle()
                    read.assert_not_called()
                    self.assertEqual(coding.collaboration.raw_task(task['id']), task)
                    # A fresh saved correction is eligible again. Scope rejection still prevents completion.
                    coding.save(clean)
                    coding.acceptance.settle()
                    read.assert_called_once_with(clean['id'], **clean['context'])
                self.assertEqual(coding.collaboration.raw_task(clean['id'])['state'], 'ready_for_review')

    def test_withdrawn_blocked_child_waits_without_reading_owner_files(self):
        coding = self.host.coding
        child = dict(id='blocked-child', state='blocked', runs=[], tokens=[],
            context=dict(thread_id='missing', project_id='missing', actor='local-user'),
            messages=[dict(role='assistant', text='Late answer')])
        coding.collaboration.save(dict(id='withdrawn-blocked', owner_task_id='past-owner', root_task_id='past-owner',
            status='withdrawn', child_task_id=child['id'], late_result=None, kind='review', files=[]))
        for state in ('blocked', 'disconnected'):
            child['state'] = state
            coding.save(child)
            with patch.object(coding.collaboration, 'result', wraps=coding.collaboration.result) as result:
                for _ in range(3):
                    self.host.tick()
            result.assert_not_called()
            self.assertIsNone(coding.collaboration.packet('withdrawn-blocked')['late_result'])
        child['state'] = 'answered'
        coding.save(child)
        with patch.object(coding.collaboration, 'deliver') as deliver:
            self.host.tick()
        deliver.assert_not_called()
        self.assertEqual(coding.collaboration.packet('withdrawn-blocked')['late_result']['summary'], 'Late answer')


if __name__ == '__main__':
    unittest.main()
