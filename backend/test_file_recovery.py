"""Per-file human restoration, using disposable Host stores and no provider calls."""
import copy
import os
import threading
import unittest
from unittest.mock import patch
import test_coding_integration as fixtures
from dervo_connections import Rejected
from coding_integration import evidence_view


class FileRecoveryTests(unittest.TestCase):
    setUp = fixtures.IntegrationTests.setUp
    create = fixtures.IntegrationTests.create
    sample_bytes = fixtures.IntegrationTests.sample_bytes
    # Reuse the fixture only, not its provider/check test cases.
    def recorded(self):
        task = self.create()
        self.coding = self.host.coding
        self.ctx = task['context']
        self.coding.evidence.approve(task['id'], scope_version=1, **self.ctx)
        self.before = self.sample_bytes()
        snap = self.coding.evidence.read_file(task['id'], 'test_sample.py', **self.ctx)
        self.edit = self.coding.evidence.prepare(task['id'], changes={'test_sample.py': {
            'expected_sha256': snap['sha256'], 'content': self.before + b'# recorded edit\n'}}, **self.ctx)
        task.update(state='ready_for_review', session_id='recorded-session', runs=[{'id':'recorded-run', 'state':'completed'}])
        self.coding.save(task)
        self.task = task
        self.args = {**self.scope, 'taskId':task['id'], 'attemptNumber':1, 'scopeVersion':1,
                     'path':'test_sample.py', 'expectedHash':self.edit['after']['test_sample.py']['sha256'], 'expectedAttempts':1}
        return task

    def restore(self, **overrides):
        return self.host.dispatch('task_restore_file', {**self.args, **overrides})

    def direct_restore(self):
        return self.coding.evidence.restore_file(self.task['id'], attempt_number=1,
            scope_version=1, path='test_sample.py', expected_hash=self.args['expectedHash'],
            expected_attempts=1, **self.ctx)

    def test_endpoint_refuses_without_mutating_files_or_records(self):
        self.recorded()
        evidence = copy.deepcopy(self.coding.evidence.read(self.task['id'], **self.ctx))
        task = copy.deepcopy(self.coding.read(self.task['id'], self.ctx))
        with patch.object(self.coding, 'start') as start:
            with self.assertRaisesRegex(Rejected, 'restoration is unavailable'):
                self.restore()
            start.assert_not_called()
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')
        self.assertEqual(self.coding.evidence.read(self.task['id'], **self.ctx), evidence)
        self.assertEqual(self.coding.read(self.task['id'], self.ctx), task)

    def test_old_final_comparison_write_window_is_unreachable(self):
        self.recorded()
        human = b'human bytes at the old final comparison window\n'
        real_write = os.write
        def concurrent_write(fd, data):
            writer = threading.Thread(target=lambda: (self.project/'test_sample.py').write_bytes(human))
            writer.start()
            writer.join(5)
            self.assertFalse(writer.is_alive())
            return real_write(fd, data)
        # This exact injection overwrote human bytes on run43 (retained race-before.log).
        # The corrected boundary must reject before opening a file or saving intent.
        with patch('task_evidence.os.write', side_effect=concurrent_write) as write, \
                patch.object(self.coding.evidence, '_file') as file, \
                patch.object(self.coding.evidence, '_save') as save:
            with self.assertRaisesRegex(Rejected, 'restoration is unavailable'):
                self.direct_restore()
            write.assert_not_called()
            file.assert_not_called()
            save.assert_not_called()
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')

    def test_endpoint_preserves_external_writer_bytes(self):
        self.recorded()
        evidence = copy.deepcopy(self.coding.evidence.read(self.task['id'], **self.ctx))
        boundary = self.coding.evidence.restore_file
        human = b'external edit just before the evidence boundary\n'
        def concurrent_request(*args, **kwargs):
            writer = threading.Thread(target=lambda: (self.project/'test_sample.py').write_bytes(human))
            writer.start()
            writer.join(5)
            self.assertFalse(writer.is_alive())
            return boundary(*args, **kwargs)
        with patch.object(self.coding.evidence, 'restore_file', side_effect=concurrent_request):
            with self.assertRaisesRegex(Rejected, 'restoration is unavailable'):
                self.restore()
        self.assertEqual(self.sample_bytes(), human)
        self.assertEqual(self.coding.evidence.read(self.task['id'], **self.ctx), evidence)

    def test_valid_recorded_edit_is_never_advertised_available(self):
        self.recorded()
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId':self.task['id']})
        self.assertEqual(len(detail['restoration']), 1)
        self.assertFalse(detail['restoration'][0]['available'])
        self.assertIn('restoration is unavailable', detail['restoration'][0]['reason'])
        # The reply leaves out file bytes (evidence_view); the stored attempt is unchanged.
        self.assertEqual(detail['evidence']['attempts'][0], evidence_view({'attempts':[self.edit]})['attempts'][0])

    def test_refuses_stale_malformed_and_cross_conversation_requests(self):
        self.recorded()
        for changes in ({'scopeVersion':2}, {'expectedAttempts':0}, {'expectedHash':'bad'},
                        {'attemptNumber':True}, {'path':'../test_sample.py'},
                        {'content':'injected'}, {'channelId':'another-thread'}):
            with self.subTest(changes=changes), self.assertRaises(Rejected):
                self.restore(**changes)
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')

    def test_symlink_target_and_binary_bytes_are_untouched(self):
        self.recorded()
        path = self.project/'test_sample.py'
        path.write_bytes(b'\x00\xff\r\nhuman')
        with self.assertRaises(Rejected): self.restore()
        path.rename(self.project/'kept.py')
        path.symlink_to(self.project/'kept.py')
        with self.assertRaises(Rejected): self.restore()
        self.assertEqual((self.project/'kept.py').read_bytes(), b'\x00\xff\r\nhuman')

    def test_created_file_remains_unsupported_without_deletion(self):
        self.recorded()
        evidence = self.coding.evidence.read(self.task['id'], **self.ctx)
        evidence['attempts'][0]['kind'] = 'create'
        evidence['attempts'][0]['before']['test_sample.py'] = None
        self.coding.evidence._save(evidence)
        with self.assertRaisesRegex(Rejected, 'restoration is unavailable'): self.restore()
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId':self.task['id']})
        self.assertFalse(detail['restoration'][0]['available'])
        self.assertIn('No file will be deleted', detail['restoration'][0]['reason'])
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')

    def historical(self, state):
        # Seed the existing persisted schema without executing the retired unsafe writer.
        evidence = self.coding.evidence.read(self.task['id'], **self.ctx)
        event = dict(number=2, scope_version=1, kind='restoration', state=state,
            restores={'attempt':1, 'path':'test_sample.py'}, actor=self.ctx['actor'],
            created_at=1, files=evidence['files'], argv=evidence['argv'],
            before=copy.deepcopy(self.edit['after']), intended_after=self.edit['before'],
            after=self.edit['before'] if state == 'restored' else None,
            diff={'test_sample.py':'historical restoration diff'}, test=None,
            blocker=None if state == 'restored' else 'Restoration write has no confirmed outcome')
        evidence['attempts'].append(event)
        self.coding.evidence._save(evidence)
        return evidence

    def test_historical_restoration_records_survive_restart_and_inspection(self):
        self.recorded()
        evidence = self.historical('restored')
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId':self.task['id']})
        self.assertEqual(detail['evidence'], evidence_view(evidence))
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')
        self.assertEqual(self.host.coding.turns, {})

    def test_historical_uncertain_restoration_is_inspection_only(self):
        self.recorded()
        evidence = self.historical('uncertain')
        with patch.object(self.coding, 'start') as start:
            with self.assertRaisesRegex(Rejected, 'uncertain'):
                self.host.dispatch('task_continue', {**self.scope, 'taskId':self.task['id'], 'text':'Continue'})
            start.assert_not_called()
        report = self.host.dispatch('task_reconcile', {**self.scope, 'taskId':self.task['id']})
        self.assertEqual(report['action'], 'inspect')
        self.assertEqual(self.coding.evidence.read(self.task['id'], **self.ctx), evidence)
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')

    def test_normal_coding_can_still_record_an_edit(self):
        self.recorded()
        snap = self.coding.evidence.read_file(self.task['id'], 'test_sample.py', **self.ctx)
        content = self.sample_bytes()
        result = self.coding.evidence.prepare(self.task['id'], changes={'test_sample.py': {
            'expected_sha256':snap['sha256'], 'content':content+b'# normal follow-up\n'}}, **self.ctx)
        self.assertEqual(result['kind'], 'change')
        self.assertEqual(self.sample_bytes(), content+b'# normal follow-up\n')

    def test_refuses_active_unknown_and_competing_host_writers(self):
        self.recorded()
        for state in ('working', 'disconnected'):
            task = copy.deepcopy(self.task)
            task['state'] = state
            task['runs'][-1]['state'] = 'uncertain' if state == 'disconnected' else 'running'
            self.coding.save(task)
            with self.assertRaises(Rejected): self.restore()
        self.coding.save(self.task)
        other = self.create()
        other['state'] = 'working'
        self.coding.save(other)
        with self.assertRaises(Rejected): self.restore()


    def test_exact_account_scope_and_approval_are_required(self):
        self.recorded()
        evidence = self.coding.evidence.read(self.task['id'], **self.ctx)
        evidence['approval'] = None
        self.coding.evidence._save(evidence)
        with self.assertRaisesRegex(Rejected, 'restoration is unavailable'): self.restore()
        self.coding.evidence.approve(self.task['id'], scope_version=1, **self.ctx)
        wrong = copy.deepcopy(self.task)
        wrong['binding']['identity'] = 'another-account'
        self.coding.save(wrong)
        with self.assertRaisesRegex(Rejected, 'account'): self.restore()
        self.coding.save(self.task)
        with self.assertRaises(Rejected): self.restore(channelId='another-thread')
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')


    def test_revoked_or_narrowed_conversation_permission_refuses_restoration(self):
        self.recorded()
        task = copy.deepcopy(self.task)
        task['origin'] = 'conversation'
        self.coding.save(task)
        with self.assertRaisesRegex(Rejected, 'revoked'): self.restore()
        grant = {'project_id':'p','cwd':str(self.project),'provider':'codex','files':[], 'folders':[],
                 'test_file':'test_sample.py','check_profile':'python-unittest','actor':self.ctx['actor'],'created_at':1}
        import json
        self.host.store.db.execute('INSERT INTO coding_permissions VALUES (?,?)', ('p',json.dumps(grant)))
        self.host.store.db.commit()
        with self.assertRaisesRegex(Rejected, 'permission changed'): self.restore()
        self.assertEqual(self.sample_bytes(), self.before+b'# recorded edit\n')

