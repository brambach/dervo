"""Saved check budgets, real confined deadlines and cancellation. No real providers or user stores."""
import base64
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import check_profiles
import task_evidence
import test_conversation_coding as fixtures
from dervo_connections import Rejected
from host import ACTOR


class CheckDurationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConversationCodingTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host, self.scope = self.fixture.host, self.fixture.scope
        self.ctx = dict(project_id='p', thread_id='c', actor=ACTOR)

    def grant(self, **extra):
        return self.host.dispatch('coding_permission_grant', dict(self.scope,
            files=['greeting.py', 'test_sample.py'], testFile='test_sample.py', **extra))

    def task(self, **extra):
        return self.host.dispatch('task_create', dict(self.scope, text='Check the sample',
            files=['greeting.py', 'test_sample.py'], testFile='test_sample.py', **extra))

    def test_invalid_public_values_never_change_grant_or_create_task(self):
        self.grant(checkTimeoutSeconds=30)
        before = list(self.host.store.db.execute('SELECT * FROM coding_permissions'))
        for value in [None, True, False, '30', 30.0, 0, 4, 121, [], {}]:
            with self.subTest(value=value):
                with self.assertRaisesRegex(Rejected, 'whole number'):
                    self.grant(checkTimeoutSeconds=value)
                with self.assertRaisesRegex(Rejected, 'whole number'):
                    self.task(checkTimeoutSeconds=value)
                self.assertEqual(list(self.host.store.db.execute('SELECT * FROM coding_permissions')), before)
        self.assertEqual(self.host.dispatch('tasks', self.scope), [])
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM task_evidence').fetchone()[0], 0)

    def test_legacy_restart_and_increased_grant_keep_old_task_at_five(self):
        self.grant()
        task = self.task()
        # Emulate records written before the field existed, preserving the other scope fields.
        for table, key in [('coding_tasks', task['id']), ('task_evidence', task['id']), ('coding_permissions', 'p')]:
            column = 'project_id' if table == 'coding_permissions' else 'id'
            doc = json.loads(self.host.store.db.execute(f'SELECT document FROM {table} WHERE {column}=?', (key,)).fetchone()[0])
            doc.pop('check_timeout_seconds')
            self.host.store.db.execute(f'UPDATE {table} SET document=? WHERE {column}=?', (json.dumps(doc), key))
        self.host.store.db.commit()
        self.fixture.reopen(); self.host = self.fixture.host
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['check_timeout_seconds'], 5)
        self.grant(checkTimeoutSeconds=120)
        held = self.host.coding.read(task['id'], self.ctx)
        self.assertEqual(held.get('check_timeout_seconds', 5), 5)
        self.assertEqual(self.host.coding.evidence.read(task['id'], **self.ctx)['check_timeout_seconds'], 5)

    def test_lowered_grant_and_evidence_mismatch_refuse_owner_and_rooted_helper(self):
        self.grant(checkTimeoutSeconds=60)
        task = self.task(checkTimeoutSeconds=60)
        task['origin'] = 'conversation'; self.host.coding.save(task)
        helper = self.task(checkTimeoutSeconds=60)
        helper.update(origin='collaboration', collaboration={'root_task_id': task['id']})
        self.host.coding.save(helper)
        self.host.coding.require_grant(task); self.host.coding.require_grant(helper)
        self.grant(checkTimeoutSeconds=30)
        for checked in [task, helper]:
            with self.assertRaisesRegex(Rejected, 'less check time'):
                self.host.coding.require_grant(checked)
        self.grant(checkTimeoutSeconds=120)
        self.host.coding.require_grant(task)
        task['check_timeout_seconds'] = 5
        with self.assertRaisesRegex(Rejected, 'differs from its approved evidence'):
            self.host.coding.require_grant(task)
        standalone = self.task(checkTimeoutSeconds=60)
        self.host.dispatch('coding_permission_revoke', self.scope)
        self.host.coding.require_grant(standalone)
        with self.assertRaisesRegex(Rejected, 'revoked'):
            self.host.coding.require_grant(helper)

    def test_conversation_snapshots_duration_and_worker_receives_immutable_budget(self):
        self.grant(checkTimeoutSeconds=60)
        with patch('coding_integration.CodingTasks.start'):
            result = self.fixture.request('Run checks')
        task = self.host.coding.read(result['task_id'], self.ctx)
        self.assertEqual(task['check_timeout_seconds'], 60)
        evidence = self.host.coding.evidence
        attempt = evidence.verify_current(task['id'], **self.ctx)
        self.assertEqual(attempt['check_timeout_seconds'], 60)
        seen = []
        def verify(inputs, snapshots, cancel, compiler):
            seen.append(inputs)
            return dict(exit_status=0, stopped=None, stdout='ok', stderr='', check_timeout_seconds=inputs['check_timeout_seconds'])
        with patch.object(task_evidence.TaskEvidence, '_verify', side_effect=verify):
            job = evidence.launch(task['id'], attempt_number=attempt['number'], **self.ctx)
            deadline = time.monotonic()+5
            while time.monotonic()<deadline:
                answer = evidence.poll(task['id'], job_id=job, **self.ctx)
                if answer['state']!='running':break
                time.sleep(.01)
        self.assertEqual(answer['state'], 'verified')
        self.assertEqual(seen[0]['check_timeout_seconds'], 60)
        self.assertEqual(answer['test']['check_timeout_seconds'], 60)

    def test_same_real_six_second_test_times_out_at_five_and_passes_at_ten(self):
        source = 'import time, unittest\nclass Wait(unittest.TestCase):\n def test_wait(self): time.sleep(6)\n'
        snapshot = {'test_sample.py': {'bytes_b64':base64.b64encode(source.encode()).decode()}}
        argv = self.task()['argv']
        for limit, stopped, code in [(5, 'timeout', -9), (10, None, 0)]:
            result = task_evidence.TaskEvidence._verify(dict(argv=argv, check_timeout_seconds=limit), snapshot)
            self.assertEqual((result['stopped'], result['exit_status']), (stopped, code), result)
            self.assertEqual(result['check_timeout_seconds'], limit)

    def test_cancel_longer_real_check_reaps_process_and_never_reports_timeout(self):
        source = 'import os,time,unittest\nclass Wait(unittest.TestCase):\n def test_wait(self):\n  print(os.getpid(),flush=True)\n  time.sleep(45)\n'
        root = self.fixture.project
        (root/'test_sample.py').write_text(source)
        argv = self.task()['argv']
        cancel = threading.Event()
        timer = threading.Timer(.8, cancel.set)
        timer.start()
        try:
            result = task_evidence._run_confined(root, argv, timeout=60, cancel=cancel)
        finally:
            timer.cancel(); timer.join()
        self.assertEqual((result['stopped'], result['exit_status']), ('cancelled', -9), result)
        if sys.platform.startswith('linux'):
            # The pid printed is the check's own in the sandbox's PID namespace. Nothing may still be working in the snapshot.
            for proc in Path('/proc').iterdir():
                try:
                    self.assertNotEqual(os.readlink(proc / 'cwd'), str(Path(root).resolve()), 'the cancelled check is still running')
                except OSError:
                    pass
            return
        pid = int(result['stdout'].strip())
        with self.assertRaises(ProcessLookupError):os.kill(pid, 0)
        with self.assertRaises(ProcessLookupError):os.killpg(pid, 0)

    def test_canary_keeps_five_while_each_profile_receives_saved_budget(self):
        argv = self.task()['argv']
        calls=[]
        def runner(root, args, **kw):
            calls.append(kw.get('timeout', 5))
            return dict(exit_status=0, stopped=None, stdout='host-canaries-denied', stderr='')
        with patch.object(task_evidence, '_run_confined', side_effect=runner):
            task_evidence.TaskEvidence._verify(dict(argv=argv, check_timeout_seconds=120), {})
        self.assertEqual(calls, [5,120])
        with patch('rust_checks.verify', return_value={}) as rust:
            result = task_evidence.TaskEvidence._verify(dict(check_profile='rust-check', check_timeout_seconds=30), {})
            self.assertEqual(rust.call_args.kwargs['timeout'], 30)
            self.assertEqual(result['check_timeout_seconds'], 30)
        for value in [True, float('nan'), float('inf'), 121, 0, None, '30']:
            with self.assertRaises(Rejected):check_profiles.runner_timeout(value)

    def test_created_file_attempt_keeps_budget_and_can_be_verified(self):
        (self.fixture.project/'src').mkdir()
        task=self.task(checkTimeoutSeconds=60,folders=['src'])
        evidence=self.host.coding.evidence
        evidence.approve(task['id'],scope_version=1,**self.ctx)
        attempt=evidence.create_file(task['id'],'src/answer.py',b'answer = 42\n',**self.ctx)
        self.assertEqual(attempt['check_timeout_seconds'],60)
        self.assertEqual(attempt['kind'],'create')
        held=evidence.read(task['id'],**self.ctx)
        self.assertTrue(evidence._current(held,attempt))
        job=evidence.launch(task['id'],attempt_number=attempt['number'],**self.ctx)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            result=evidence.poll(task['id'],job_id=job,**self.ctx)
            if result['state']!='running':break
            time.sleep(.01)
        self.assertEqual(result['state'],'verified',result)
        self.assertEqual(result['test']['check_timeout_seconds'],60)
