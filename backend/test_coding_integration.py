"""Synthetic host integration. Temporary stores only, no live provider."""
import tempfile
import time
import unittest
from pathlib import Path
from host import Host, ACTOR
from dervo_connections import Rejected
from coding_integration import CodingTasks
import contextlib
from unittest.mock import patch


@contextlib.contextmanager
def _capture_into(bucket):
    """Collect every error_reports.capture report into ``bucket`` for the length of the block. A test host never
    installs a usage sink, so this is how these tests see what would have been reported."""
    import error_reports
    original = error_reports.capture

    def capture(error, *, source='host', operation=None, provider=None):
        report = error_reports.from_exception(error, source, operation, provider)
        if report is not None:
            bucket.append(report)
        return True
    error_reports.capture = capture
    try:
        yield bucket
    finally:
        error_reports.capture = original


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.addCleanup(lambda: self.host.close())
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.host.store.add_profile('p', 'codex', self.base / 'store/profiles/p', 'synthetic@example.test', 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')

    def create(self):
        return self.host.dispatch('task_create', {**self.scope, 'text':'Improve the test', 'files':['test_sample.py'], 'testFile':'test_sample.py'})

    def test_a_second_accept_at_nearly_the_same_moment_changes_nothing(self):
        """9 Oct (phone approvals): the Mac and a paired phone both accept the same result. The first one commits and
        marks the thread's items done; the second finds it decided and is refused, so neither happens twice. The
        evidence side is stood in for: this is the task record's own guard."""
        import json
        task = self.create()
        task.update(state='ready_for_review', review=None, runs=[])
        self.host.coding.save(task)
        args = {**self.scope, 'taskId': task['id'], 'attemptNumber': 1, 'decision': 'approved'}
        coding, db = self.host.coding, self.host.store.db
        stored = lambda task_id, ctx, rebound=False: json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()[0])
        commits, done = [], []
        with patch.object(coding, 'read', stored), patch.object(coding.evidence, 'review', lambda *a, **k: None), \
                patch.object(coding, 'commit_record', lambda *a, **k: commits.append(1) or {'sha': None, 'skipped': 'fixture'}), \
                patch.object(coding, 'items_done', lambda task: done.append(1) or []):
            self.assertEqual(self.host.dispatch('task_review', args)['state'], 'idle')
            for decision in ('approved', 'rejected'):
                with self.assertRaisesRegex(Rejected, 'reviewed already'):
                    self.host.dispatch('task_review', {**args, 'decision': decision})
            # Asking for changes twice is refused too; changing your mind to approve still goes, once.
            again = stored(task['id'], None)
            again.update(state='ready_for_review', review='rejected')
            coding.save(again)
            with self.assertRaisesRegex(Rejected, 'reviewed already'):
                self.host.dispatch('task_review', {**args, 'decision': 'rejected'})
            self.assertEqual(self.host.dispatch('task_review', args)['review'], 'approved')
            with self.assertRaisesRegex(Rejected, 'reviewed already'):
                self.host.dispatch('task_review', args)
        self.assertEqual((len(commits), len(done)), (2, 2), 'one commit per approval that went through')

    def test_a_committed_result_stays_approved_after_undo_or_merge_changes_its_files(self):
        """Polish review, 9 Oct: after Approve and commit, Undo (or Merge, which removes the thread's folder) changed the
        files, and the card offered "Approve and commit" again. A result that isn't committed still reopens."""
        task = self.create()
        task.update(state='idle', review='approved', runs=[], commit={'sha': 'a' * 40, 'how': 'approved', 'reverted': {'sha': 'b' * 40}})
        self.host.coding.save(task)
        read = self.host.coding.read(task['id'], task['context'])
        self.assertEqual((read['state'], read['review'], read.get('error')), ('idle', 'approved', None))
        task.update(commit=None)
        self.host.coding.save(task)
        read = self.host.coding.read(task['id'], task['context'])
        self.assertEqual((read['state'], read['review']), ('ready_for_review', None), 'an approval not yet committed is reopened')

    def test_rebound_lost_task_can_be_marked_stopped_without_rebinding_execution(self):
        task = self.create()
        task.update(state='disconnected', runs=[{'id': 'lost', 'state': 'uncertain'}], tokens=[{'state': 'uncertain'}])
        self.host.coding.save(task)
        args = {**self.scope, 'taskId': task['id']}
        before_file = (self.project / 'test_sample.py').read_bytes()
        before_evidence = self.host.store.db.execute('SELECT document FROM task_evidence WHERE id=?', (task['id'],)).fetchone()[0]
        self.host.dispatch('conversation_model_set', {**self.scope, 'expectedModel': 'synthetic-model', 'model': 'another-synthetic-model'})
        self.assertTrue(self.host.dispatch('task_detail', args)['rebound'])
        settled = self.host.dispatch('task_settle', args)
        self.assertEqual((settled['state'], settled['runs'][0]['end_cause']), ('blocked', 'settled_by_user'))
        self.assertEqual(settled['binding'], task['binding'])
        self.assertEqual((self.project / 'test_sample.py').read_bytes(), before_file)
        self.assertEqual(self.host.store.db.execute('SELECT document FROM task_evidence WHERE id=?', (task['id'],)).fetchone()[0], before_evidence)
        self.assertEqual(self.host.dispatch('lifecycle_blockers', {'scope': 'project', 'projectId': 'p', 'channelId': None}), [])
        for op, extra in [('task_continue', {'text': 'Retry'}), ('task_approve', {'scopeVersion': task['scope_version']})]:
            with self.assertRaises(Rejected): self.host.dispatch(op, {**args, **extra})
        with self.assertRaises(Rejected): self.host.dispatch('task_settle', {**args, 'channelId': 'wrong-thread'})

    def test_a_result_with_an_unknown_tool_delivery_is_cleared_by_mark_as_stopped_and_keeps_its_state(self):
        # The blocker calls a stray uncertain tool call a lost run and offers Mark as stopped for it (app #9). Settling
        # it cancels that record only: the result's own state and its run stay as they were.
        task = self.create()
        task.update(state='ready_for_review', runs=[{'id': 'done', 'state': 'completed'}], tokens=[{'token': 'k', 'state': 'uncertain'}])
        self.host.coding.save(task)
        args = {**self.scope, 'taskId': task['id']}
        blocker = self.host.dispatch('lifecycle_blockers', {'scope': 'project', 'projectId': 'p', 'channelId': None})
        self.assertEqual([(row['kind'], row['actions']) for row in blocker], [('lost', ['settle'])])
        settled = self.host.dispatch('task_settle', args)
        self.assertEqual((settled['state'], settled['runs'][0]['state'], settled['tokens'][0]['state']), ('ready_for_review', 'completed', 'cancelled'))
        self.assertEqual(self.host.dispatch('lifecycle_blockers', {'scope': 'project', 'projectId': 'p', 'channelId': None}), [])
        with self.assertRaisesRegex(Rejected, 'confirmed outcome'): self.host.dispatch('task_settle', args)

    def test_an_archived_conversations_lost_run_can_be_marked_stopped_but_nothing_else_happens_there(self):
        # The blocker offers Mark as stopped for it, so the host lets the settling operations through the archive gate.
        task = self.create()
        task.update(state='disconnected', runs=[{'id': 'lost', 'state': 'uncertain'}], tokens=[])
        self.host.coding.save(task)
        self.host.store.db.execute('INSERT INTO conversation_archives VALUES (?,?)', ('c', 1))
        self.host.store.db.commit()
        args = {**self.scope, 'taskId': task['id']}
        self.assertEqual(self.host.dispatch('task_settle', args)['state'], 'blocked')
        for op, extra in (('task_detail', {}), ('task_continue', {'text': 'Go on'})):
            with self.assertRaisesRegex(Rejected, 'archived'):
                self.host.dispatch(op, {**args, **extra})

    def test_settlement_refuses_live_turns_checks_and_reviews_after_a_model_change(self):
        from types import SimpleNamespace
        task = self.create()
        task.update(state='disconnected', runs=[{'id': 'lost', 'state': 'uncertain'}], tokens=[])
        self.host.coding.save(task)
        self.host.dispatch('conversation_model_set', {**self.scope, 'expectedModel': 'synthetic-model', 'model': 'another-synthetic-model'})
        args = {**self.scope, 'taskId': task['id']}
        self.host.coding.turns[task['id']] = SimpleNamespace(closed=False)
        try:
            with self.assertRaisesRegex(Rejected, 'still running'): self.host.dispatch('task_settle', args)
        finally:
            self.host.coding.turns.pop(task['id'])
        for token in [{'state': 'running'}, {'state': 'delivered', 'job_id': 'job', 'job_done': False}]:
            task['tokens'] = [token]
            self.host.coding.save(task)
            with self.assertRaisesRegex(Rejected, 'check is still running'): self.host.dispatch('task_settle', args)
        task.update(tokens=[], collaboration={'review': {'enabled': True, 'phase': 'initial'}})
        self.host.coding.save(task)
        with self.assertRaisesRegex(Rejected, 'review is still active'): self.host.dispatch('task_settle', args)

    def test_task_detail_says_whether_approving_commits(self):
        """#27: the Approve button reads "Approve and commit" only when the project folder is in a git repository."""
        import subprocess
        import task_git
        task = self.create()
        args = {**self.scope, 'taskId': task['id']}
        # Negative control first: a plain folder, where approving commits nothing.
        self.assertIs(self.host.dispatch('task_detail', args)['git'], False)
        subprocess.run([task_git.GIT, 'init', '-q', str(self.project)], check=True, capture_output=True)
        # Polish pass 2 (7 Oct 2026): the words are kept for task_git.REPO_KEPT seconds, because asking git costs three processes
        # and task_detail is asked every 700 ms while a task works. Within that time the old answer stands; after it, the new one.
        self.assertIs(self.host.dispatch('task_detail', args)['git'], False, 'kept for a few seconds')
        later = time.monotonic() + task_git.REPO_KEPT + 1
        with patch.object(task_git.time, 'monotonic', return_value=later):
            self.assertIs(self.host.dispatch('task_detail', args)['git'], True)

    def test_task_detail_asks_git_once_for_a_burst_of_reads_but_never_for_a_commit(self):
        import subprocess
        import task_git
        task = self.create()
        args = {**self.scope, 'taskId': task['id']}
        subprocess.run([task_git.GIT, 'init', '-q', str(self.project)], check=True, capture_output=True)
        task_git._REPO_SEEN.clear()
        asked = []
        real = task_git.repo_root
        def counting(*a, **k):
            asked.append(a[0])
            return real(*a, **k)
        with patch.object(task_git, 'repo_root', counting):
            for _ in range(5):
                self.assertIs(self.host.dispatch('task_detail', args)['git'], True)
            self.assertEqual(len(asked), 1, 'five reads, one question to git')
            self.assertIsNotNone(task_git.repo_root(str(self.project)))
            self.assertEqual(len(asked), 2, 'repo_root itself, which commits and runs use, always asks')

    def test_saved_structured_provider_error_is_text_after_relaunch(self):
        """Records saved before provider errors were coerced hold Codex's TurnError
        object in task.error. Relaunching Dervo rendered it and crashed the app."""
        import json
        task = self.create()
        row = self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        legacy = json.loads(row[0])
        legacy.update(state='blocked', error={
            'message': "You've hit your usage limit.",
            'additionalDetails': 'argv=--token SHOULD-NOT-LEAK',
            'codexErrorInfo': {'kind': 'usage_limit_exceeded'},
            'misalignment': None})
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(legacy), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')
        listed = self.host.dispatch('tasks', self.scope)[0]
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        for seen in (listed, detail):
            self.assertEqual(seen['state'], 'blocked')
            self.assertEqual(seen['error'], "You've hit your usage limit.")
            self.assertNotIn('SHOULD-NOT-LEAK', json.dumps(seen))
            self.assertNotIn('codexErrorInfo', json.dumps(seen))

    def test_a_working_task_reads_lost_or_unsent_after_a_relaunch_by_how_far_its_run_got(self):
        """The startup reconcile settles a task that was working when the host died without closing. A run that had
        reached the agent (any state past `checking`) reads disconnected with its run uncertain: outcome unknown. A run
        still `checking` (account check, launch, handshake) never sent the task, so it reads blocked and unsent, and
        can go again (run-lifecycle.md, findings 2 and 11). A damaged record that says working with no run at all used
        to stop the host from starting (IndexError in CodingTasks.__init__); it reads as disconnected with nothing to
        mark."""
        import json
        task = self.create()
        row = self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        saved = json.loads(row[0])
        self.assertEqual(saved['runs'], [])
        # No run at all: disconnected, nothing to mark, and it counts as a lost run.
        saved.update(state='working')
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')
        recovered = self.host.dispatch('tasks', self.scope)[0]
        self.assertEqual((recovered['state'], recovered['runs']), ('disconnected', []))
        self.assertIn('can’t say how it ended', recovered['error'])
        self.assertEqual([r['taskId'] for r in self.host.dispatch('status', {})['lostRuns']], [task['id']])
        # A run that had been sent (running): disconnected, run uncertain, lost.
        saved.update(state='working', runs=[dict(id='run-1', state='running', session_id='s', prior_attempts=0)])
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')
        recovered = self.host.dispatch('tasks', self.scope)[0]
        self.assertEqual((recovered['state'], recovered['runs'][-1]['state']), ('disconnected', 'uncertain'))
        self.assertEqual(len(self.host.dispatch('status', {})['lostRuns']), 1)
        # A run still checking: nothing was sent, so blocked and unsent, not a lost run.
        saved.update(state='working', error=None, runs=[dict(id='run-2', state='checking', session_id=None, prior_attempts=0)])
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')
        recovered = self.host.dispatch('tasks', self.scope)[0]
        self.assertEqual((recovered['state'], recovered['runs'][-1]['state']), ('blocked', 'failed'))
        self.assertEqual(recovered['error'], 'Dervo closed before this started. Nothing was sent. Send it again.')
        self.assertEqual(self.host.dispatch('status', {})['lostRuns'], [])
        # Negative: a task that wasn't working is left alone by the relaunch, and nothing reads as lost.
        saved.update(state='needs_permission', runs=[], error=None)
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')
        recovered = self.host.dispatch('tasks', self.scope)[0]
        self.assertEqual((recovered['state'], recovered['runs'], recovered['error']), ('needs_permission', [], None))
        self.assertEqual((self.host.dispatch('status', {})['lostRuns'], self.host.dispatch('status', {})['lostRunsDetail']), ([], None))

    def test_saved_text_and_absent_errors_are_unchanged_by_reading(self):
        import json
        task = self.create()
        self.assertIsNone(self.host.dispatch('tasks', self.scope)[0]['error'])
        row = self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        saved = json.loads(row[0]); saved.update(state='blocked', error='TypeScript found an invalid argument.')
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.assertEqual(self.host.dispatch('tasks', self.scope)[0]['error'], 'TypeScript found an invalid argument.')

    def run_completion(self, task, *, mode='none', correction=False):
        import json, time
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        class Peer(SyntheticCodingPeer):
            def tool(peer, name, args, number):
                peer.queue.append({'id':str(number), 'method':'item/tool/call', 'params':dict(
                    threadId=SID, turnId='turn-1', callId=str(number), tool=name, arguments=args)})
            def finish(peer):
                peer.queue.extend([
                    {'method':'item/agentMessage/delta', 'params':dict(threadId=SID, turnId='turn-1', itemId='answer', delta='Everything passed. Ready for review.')},
                    {'method':'turn/completed', 'params':{'threadId':SID, 'turn':{'id':'turn-1', 'status':'completed'}}}])
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.append({'id':message['id'], 'result':{'turn':{'id':'turn-1'}}})
                    if mode == 'none': peer.finish()
                    elif mode == 'verify_only': peer.tool('dervo_run_tests', {}, 3)
                    else: peer.tool('dervo_read_file', {'path':'test_sample.py'}, 1)
                elif message.get('id') == '1':
                    result = json.loads(message['result']['contentItems'][0]['text'])
                    content = result['content'] + '\n# current attempt\n'
                    if mode == 'failure': content = content.replace('assertTrue(True)', 'assertTrue(False)')
                    peer.tool('dervo_write_file', dict(path='test_sample.py', expected_hash=result['sha256'], content=content), 2)
                elif message.get('id') == '2':
                    if mode == 'write_only': peer.finish()
                    else: peer.tool('dervo_run_tests', {}, 3)
                elif message.get('id') == '3':
                    if mode == 'stale':
                        with (self.project/'test_sample.py').open('a') as file: file.write('# changed after tests\n')
                    peer.finish()
                else: super().send(message)
        with patch('codex_coding.OwnedProcess', Peer):
            args = {**self.scope, 'taskId':task['id']}
            self.host.dispatch('task_continue' if correction else 'task_approve',
                {**args, **({'text':'Correct this'} if correction else {'scopeVersion':1})})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', args)
                if detail['task']['state'] != 'working': return detail
                time.sleep(.01)
            self.fail('Synthetic completion timed out')

    def sample_bytes(self):
        return (self.project / 'test_sample.py').read_bytes()

    def test_verification_only_run_records_real_test_evidence_without_writing(self):
        before = self.sample_bytes()
        detail = self.run_completion(self.create(), mode='verify_only')
        task, evidence = detail['task'], detail['evidence']
        self.assertEqual(self.sample_bytes(), before, 'source bytes are unchanged')
        self.assertEqual([t['tool'] for t in task['tokens']], ['dervo_run_tests'])
        self.assertEqual(len(evidence['attempts']), 1)
        attempt = evidence['attempts'][0]
        self.assertEqual((attempt['kind'], attempt['state'], attempt['diff']), ('verification', 'verified', {}))
        self.assertEqual(attempt['before'], attempt['after'])
        self.assertEqual(attempt['test']['exit_status'], 0, attempt['test'])
        self.assertIn('Ran 1 test', attempt['test']['stderr'])
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertTrue(detail['retry']['available'] is False)
        review = self.host.dispatch('task_review', {**self.scope, 'taskId':task['id'], 'attemptNumber':1, 'decision':'approved'})
        self.assertEqual(review['state'], 'idle')
        self.assertEqual(self.sample_bytes(), before)

    def test_verification_only_failure_is_reported_and_not_approvable(self):
        (self.project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(False)\n')
        before = self.sample_bytes()
        detail = self.run_completion(self.create(), mode='verify_only')
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual((attempt['kind'], attempt['state'], attempt['test']['exit_status']), ('verification', 'verified', 1))
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertIn('Host checks failed', detail['task']['error'])
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId':detail['task']['id'], 'attemptNumber':1, 'decision':'approved'})
        self.assertEqual(self.sample_bytes(), before)

    def test_verification_only_run_never_inherits_prior_passing_evidence(self):
        task = self.create()
        first = self.run_completion(task, mode='success')
        self.assertEqual(first['evidence']['attempts'][-1]['test']['exit_status'], 0)
        self.host.dispatch('task_review', {**self.scope, 'taskId':task['id'], 'attemptNumber':1, 'decision':'rejected'})
        second = self.run_completion(task, mode='verify_only', correction=True)
        attempts = second['evidence']['attempts']
        self.assertEqual(len(attempts), 2)
        self.assertEqual((attempts[1]['kind'], attempts[1]['number'], attempts[1]['state']), ('verification', 2, 'verified'))
        self.assertIsNot(attempts[1]['test'], None, 'the second run carries its own verification evidence')
        self.assertNotEqual(attempts[1]['job_id'], attempts[0]['job_id'], 'a separate confined job produced attempt 2')
        self.assertEqual(attempts[1]['before'], attempts[0]['after'], 'verification snapshots the current files, unchanged')
        self.assertEqual(second['task']['runs'][-1]['prior_attempts'], 1)
        self.assertEqual([t['attempt'] for t in second['task']['tokens'] if t['tool'] == 'dervo_run_tests'][-1], 2)
        self.assertEqual(second['task']['state'], 'ready_for_review')
        # Files changed after a human review invalidate that review; a continue cannot ride on old evidence.
        self.host.dispatch('task_review', {**self.scope, 'taskId':task['id'], 'attemptNumber':2, 'decision':'rejected'})
        (self.project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(False)\n')
        stale = self.host.dispatch('task_detail', {**self.scope, 'taskId':task['id']})
        self.assertIsNone(stale['evidence']['review'])
        self.assertIsNone(stale['task']['review'], 'a review of evidence that no longer matches the files is cleared')
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**self.scope, 'taskId':task['id'], 'text':'Check again'})

    def test_verification_only_is_refused_when_approval_is_revoked(self):
        task = self.create()
        before = self.sample_bytes()
        ctx = dict(thread_id='c', project_id='p', actor=ACTOR)
        test = self
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        class Peer(SyntheticCodingPeer):
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.append({'id':message['id'], 'result':{'turn':{'id':'turn-1'}}})
                    evidence = test.host.coding.evidence.read(task['id'], **ctx)
                    evidence['approval'] = None
                    test.host.coding.evidence._save(evidence)
                    peer.queue.append({'id':'3', 'method':'item/tool/call', 'params':{'threadId':SID, 'turnId':'turn-1', 'callId':'3', 'tool':'dervo_run_tests', 'arguments':{}}})
                elif message.get('id') == '3':
                    test.tool_reply = message
                    peer.queue.extend([{'method':'turn/completed', 'params':{'threadId':SID, 'turn':{'id':'turn-1', 'status':'completed'}}}])
                else: super().send(message)
        args = {**self.scope, 'taskId':task['id']}
        with patch('codex_coding.OwnedProcess', Peer):
            self.host.dispatch('task_approve', {**args, 'scopeVersion':1})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', args)
                if detail['task']['state'] != 'working': break
                time.sleep(.01)
        self.assertEqual(detail['evidence']['attempts'], [], 'no verification attempt without current approval')
        # Revoking approval mid-run ends the run without success: blocked, or disconnected when the
        # provider had already begun and the outcome is uncertain. Never ready_for_review.
        self.assertIn(detail['task']['state'], ('blocked', 'disconnected'))
        self.assertNotEqual(detail['task']['runs'][-1]['state'], 'completed')
        self.assertEqual(self.sample_bytes(), before)

    def test_a_trial_ending_mid_run_lets_the_run_finish_but_signing_out_stops_it(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        test = self
        # The real check reads the account session, whichever way the account gate is set up for this run.
        self.enterContext(patch.object(self.host, 'require_beta_access', lambda: test.host.account_session.require_access()))
        def run(lapsed):
            self.host.account_session = SimpleNamespace(require_access=lambda: None, tick=lambda: None, close=lambda: None)
            task = self.create()
            class Peer(SyntheticCodingPeer):
                def send(peer, message):
                    if message.get('method') == 'turn/start':
                        peer.sent.append(message)
                        peer.queue.append({'id':message['id'], 'result':{'turn':{'id':'turn-1'}}})
                        # The send was allowed; now the trial ends (or the account signs out) before the first tool call.
                        def refuse(): raise Rejected('Your free trial has ended.')
                        test.host.account_session = SimpleNamespace(require_access=refuse, plan_lapsed=lambda: lapsed, tick=lambda: None, close=lambda: None)
                        peer.queue.append({'id':'3', 'method':'item/tool/call', 'params':{'threadId':SID, 'turnId':'turn-1', 'callId':'3', 'tool':'dervo_run_tests', 'arguments':{}}})
                    elif message.get('id') == '3':
                        peer.queue.extend([{'method':'turn/completed', 'params':{'threadId':SID, 'turn':{'id':'turn-1', 'status':'completed'}}}])
                    else: super().send(message)
            args = {**self.scope, 'taskId':task['id']}
            # The synthetic peer stands in for Codex, so the packaged runtime isn't needed (nor present off a built app).
            with patch('codex_coding.OwnedProcess', Peer), patch('codex_runtime.verified_binary', side_effect=lambda path, _: path):
                self.host.dispatch('task_approve', {**args, 'scopeVersion':1})
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    self.host.tick()
                    detail = self.host.dispatch('task_detail', args)
                    if detail['task']['state'] != 'working': break
                    time.sleep(.01)
            return detail
        finished = run(lapsed=True)
        self.assertEqual(finished['task']['runs'][-1]['state'], 'completed', 'its tool call was allowed and the turn ended normally')
        self.assertNotIn('trial', str(finished['task'].get('error')))
        stopped = run(lapsed=False)
        self.assertIn(stopped['task']['state'], ('blocked', 'disconnected'))
        self.assertNotEqual(stopped['task']['runs'][-1]['state'], 'completed')
        self.assertEqual(stopped['evidence']['attempts'], [])

    def test_verification_only_cancellation_is_truthful(self):
        task = self.create()
        before = self.sample_bytes()
        args = {**self.scope, 'taskId':task['id']}
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        class Peer(SyntheticCodingPeer):
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.append({'id':message['id'], 'result':{'turn':{'id':'turn-1'}}})
                    peer.queue.append({'id':'3', 'method':'item/tool/call', 'params':{'threadId':SID, 'turnId':'turn-1', 'callId':'3', 'tool':'dervo_run_tests', 'arguments':{}}})
                elif message.get('id') == '3': pass
                else: super().send(message)
        with patch('codex_coding.OwnedProcess', Peer):
            self.host.dispatch('task_approve', {**args, 'scopeVersion':1})
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', args)
                if any(t['state'] == 'running' for t in detail['task']['tokens']): break
                time.sleep(.005)
            self.assertTrue(any(t['state'] == 'running' for t in detail['task']['tokens']), 'verification job launched')
            self.host.dispatch('task_stop', args)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                self.host.tick()
                detail = self.host.dispatch('task_detail', args)
                attempt = detail['evidence']['attempts'][-1]
                if detail['task']['state'] != 'working' and attempt['state'] != 'running': break
                time.sleep(.01)
        self.assertEqual(attempt['kind'], 'verification')
        self.assertEqual(attempt['state'], 'cancelled')
        self.assertEqual(detail['task']['runs'][-1]['state'], 'interrupted')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertEqual(self.sample_bytes(), before)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**args, 'attemptNumber':attempt['number'], 'decision':'approved'})

    def test_no_tool_completion_is_blocked_despite_success_prose(self):
        detail = self.run_completion(self.create())
        self.assertEqual(detail['task']['runs'][-1]['state'], 'completed')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('No file attempt', detail['task']['error'])
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertIsNone(detail['evidence']['review'])
        self.assertEqual(self.host.dispatch('conversation_status', self.scope)['state'], 'blocked')

    def test_no_attempt_retry_preserves_session_and_requires_new_evidence(self):
        task = self.create()
        first = self.run_completion(task)
        # The old runtime stored this misleading state without a baseline field.
        legacy = first['task']
        legacy['state'] = 'ready_for_review'
        legacy['runs'][-1].pop('prior_attempts')
        self.host.coding.save(legacy)
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId':task['id']})
        self.assertTrue(detail.get('retry', {}).get('available'), 'Host must expose safe no-attempt retry')
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        retried = self.run_completion(task, mode='success', correction=True)
        self.assertEqual(retried['task']['session_id'], first['task']['session_id'])
        self.assertEqual(retried['task']['binding'], first['task']['binding'])
        self.assertEqual(len(retried['task']['runs']), 2)
        self.assertEqual(len(retried['evidence']['attempts']), 1)
        self.assertEqual(retried['evidence']['attempts'][0]['test']['exit_status'], 0)
        self.assertIsNone(retried['evidence']['review'])
        self.assertEqual(retried['task']['state'], 'ready_for_review')

    def test_retry_revoked_approval_saves_feedback_without_starting(self):
        from unittest.mock import patch
        task = self.create()
        first = self.run_completion(task)
        ctx = dict(thread_id='c', project_id='p', actor=ACTOR)
        evidence = self.host.coding.evidence.read(task['id'], **ctx)
        evidence['approval'] = None
        self.host.coding.evidence._save(evidence)
        args = {**self.scope, 'taskId':task['id']}
        with patch('codex_coding.OwnedProcess', side_effect=AssertionError('Revoked scope must not start')):
            pending = self.host.dispatch('task_continue', {**args, 'text':'Try the host tools again'})
        self.assertEqual(pending['state'], 'needs_permission')
        self.assertEqual(pending['session_id'], first['task']['session_id'])
        self.assertEqual(len(pending['runs']), 1)
        self.assertEqual(pending['messages'][-1]['text'], 'Try the host tools again')
        self.assertIsNone(self.host.coding.evidence.read(task['id'], **ctx)['approval'])
        resumed = self.run_completion(task, mode='success')
        self.assertEqual(resumed['task']['session_id'], first['task']['session_id'])
        self.assertEqual(resumed['task']['state'], 'ready_for_review')

    def test_retry_rejects_active_uncertain_and_changed_scope_without_mutation(self):
        task = self.create()
        finished = self.run_completion(task)['task']
        ctx = dict(thread_id='c', project_id='p', actor=ACTOR)
        args = {**self.scope, 'taskId':task['id']}
        import copy
        for state in ('working', 'disconnected'):
            current = copy.deepcopy(finished)
            current['state'] = state
            if state == 'disconnected': current['runs'][-1]['state'] = 'uncertain'
            self.host.coding.save(current)
            self.assertFalse(self.host.dispatch('task_detail', args)['retry']['available'])
            with self.assertRaises(Rejected):
                self.host.dispatch('task_continue', {**args, 'text':'Retry'})
            self.assertEqual(self.host.coding.read(task['id'], ctx), current)
        self.host.coding.save(finished)
        self.host.coding.evidence.scope(task['id'], files=['test_sample.py'], test_file='test_sample.py', **ctx)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**args, 'text':'Retry'})
        self.assertEqual(self.host.coding.read(task['id'], ctx), finished)

    def test_repeated_no_work_retry_cannot_credit_prior_success(self):
        task = self.create()
        self.run_completion(task, mode='success')
        args = {**self.scope, 'taskId':task['id'], 'attemptNumber':1}
        self.host.dispatch('task_review', {**args, 'decision':'rejected'})
        blocked = self.run_completion(task, correction=True)
        self.assertTrue(blocked['retry']['available'])
        retried = self.run_completion(task, correction=True)
        self.assertEqual(retried['task']['state'], 'blocked')
        self.assertEqual(len(retried['evidence']['attempts']), 1)
        self.assertEqual(len(retried['task']['runs']), 3)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**args, 'decision':'approved'})

    def test_retry_cannot_switch_expected_account(self):
        task = self.create()
        self.run_completion(task)
        with self.host.store.db:
            self.host.store.db.execute('UPDATE profiles SET identity=? WHERE id=?', ('other@example.test', 'p'))
        from unittest.mock import patch
        with patch('codex_coding.OwnedProcess', side_effect=RuntimeError('No real provider in this test')):
            with self.assertRaises(Rejected):
                self.host.dispatch('task_continue', {**self.scope, 'taskId':task['id'], 'text':'Retry'})

    def test_prior_success_cannot_credit_no_work_correction(self):
        task = self.create()
        detail = self.run_completion(task, mode='success')
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual(detail['evidence']['attempts'][-1]['state'], 'verified')
        self.assertEqual(detail['evidence']['attempts'][-1]['test']['exit_status'], 0)
        self.assertIsNone(detail['evidence']['review'])
        args = {**self.scope, 'taskId':task['id'], 'attemptNumber':1}
        self.host.dispatch('task_review', {**args, 'decision':'rejected'})
        detail = self.run_completion(task, correction=True)
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('No file attempt', detail['task']['error'])
        self.assertEqual(len(detail['evidence']['attempts']), 1)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**args, 'decision':'approved'})

    def test_write_without_tests_is_blocked(self):
        detail = self.run_completion(self.create(), mode='write_only')
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('Tests were not run', detail['task']['error'])
        self.assertEqual(detail['evidence']['attempts'][-1]['state'], 'prepared')
        self.assertIsNone(detail['evidence']['attempts'][-1]['test'])

    def test_changed_snapshots_cannot_claim_verified_success(self):
        detail = self.run_completion(self.create(), mode='stale')
        self.assertEqual(detail['evidence']['attempts'][-1]['test']['exit_status'], 0)
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('changed after its last check', detail['task']['error'])

    def test_actual_failed_tests_remain_reviewable_without_approval(self):
        task = self.create()
        detail = self.run_completion(task, mode='failure')
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertIn('failed', detail['task']['error'] or '')
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual(attempt['test']['exit_status'], 1)
        self.assertIn('FAILED', attempt['test']['stderr'])
        self.assertTrue(attempt['diff']['test_sample.py'])
        self.assertIsNone(detail['evidence']['review'])
        args = {**self.scope, 'taskId':task['id'], 'attemptNumber':1}
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**args, 'decision':'approved'})
        self.host.dispatch('task_review', {**args, 'decision':'rejected'})
        corrected = self.run_completion(task, mode='success', correction=True)
        self.assertEqual(len(corrected['evidence']['attempts']), 2)
        self.assertEqual(corrected['task']['state'], 'ready_for_review')

    def test_approved_session_is_separate_and_restart_never_replays(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        sid = '00000000-0000-4000-8000-000000000123'
        ordinary = '00000000-0000-4000-8000-000000000999'
        self.host.store.db.execute('UPDATE threads SET session_id=? WHERE id=?', (ordinary, 'c'))
        self.host.store.db.commit()
        calls = []
        class SyntheticTurn:
            def __init__(peer, binding, *, task, context, emit):
                calls.append(binding)
                peer.closed = False
                peer.pending_tool_requests = ()
                peer.adapter = SimpleNamespace(state='running')
                peer.emit = emit
                emit('session', {'session_id': sid})
                self.assertIn('Improve the test', context()[0])
            def tick(peer): pass
            def stop(peer): peer.closed = True
            def close(peer): peer.closed = True
        task = self.create()
        with patch('codex_coding.CodexCodingTurn', SyntheticTurn):
            try:
                started = self.host.dispatch('task_approve', {**self.scope, 'taskId':task['id'], 'scopeVersion':1})
            except Rejected as exc:
                self.fail('Task approval/start API missing: ' + str(exc))
        self.assertEqual(started['state'], 'working')
        self.assertIsNone(calls[0]['session_id'])
        self.assertEqual(started['session_id'], sid)
        self.assertEqual(self.host.store.binding('c','p',actor=ACTOR)['session_id'], ordinary)
        self.host.close()
        self.host = Host(self.base / 'store')
        recovered = self.host.dispatch('tasks', self.scope)[0]
        self.assertEqual(recovered['state'], 'disconnected')
        self.assertEqual(recovered['runs'][-1]['state'], 'uncertain')
        self.assertEqual(recovered['session_id'], sid)
        self.assertEqual(len(calls), 1)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**self.scope,'taskId':task['id'],'text':'retry'})

    def test_streamed_text_is_saved_in_batches_and_keeps_its_place_around_a_message_sent_mid_run(self):
        """Beta.16.1: each streamed delta was its own read and save of the whole record, about 30 a second per run. Text now
        waits in memory for TEXT_SAVE_SECONDS, and a mid-run message or the run's end saves it first, in order."""
        import json
        from unittest.mock import patch
        from types import SimpleNamespace
        import coding_integration
        turns = []
        class SyntheticTurn:
            def __init__(peer, binding, *, task, context, emit):
                peer.closed, peer.pending_tool_requests, peer.steered = False, (), []
                peer.adapter = SimpleNamespace(state='running')
                peer.emit = emit
                turns.append(peer)
                emit('session', {'session_id': '00000000-0000-4000-8000-000000000124'})
            def tick(peer): pass
            def steer(peer, text, images=()): peer.steered.append(text)
            def stop(peer): peer.closed = True
            def close(peer): peer.closed = True
        task = self.create()
        self.enterContext(patch.object(coding_integration, 'TEXT_SAVE_SECONDS', 60))
        self.enterContext(patch.object(self.host, 'require_beta_access', lambda: None))  # the account isn't what's tested
        with patch('codex_coding.CodexCodingTurn', SyntheticTurn):
            self.host.dispatch('task_approve', {**self.scope, 'taskId': task['id'], 'scopeVersion': 1})
        peer, coding = turns[0], self.host.coding
        def stored():
            return json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
        run_id = stored()['runs'][-1]['id']
        saves = []
        save = coding.save
        self.enterContext(patch.object(coding, 'save', lambda record: (saves.append(record['id']), save(record))[1]))
        for n in range(20):
            peer.emit('delta', {'item_id': 'answer', 'text': f'{n} '})
        coding.tick()
        self.assertEqual(saves, [], 'twenty deltas and a pump round inside the wait save nothing')
        self.assertFalse([m for m in stored()['messages'] if m.get('run_id') == run_id])
        record = stored()
        record['origin'] = 'conversation'  # a message sent mid-run goes only to a conversation's run
        save(record)
        coding.steer('c', 'Also check the README', 'client-1')
        self.assertEqual(peer.steered, ['Also check the README'])
        for n in range(20, 23):
            peer.emit('delta', {'item_id': 'answer', 'text': f'{n} '})
        peer.closed = True  # the run ended without an event: the pump saves what's waiting before it lets the run go
        coding.tick()
        self.assertEqual([(m['role'], m['text']) for m in stored()['messages'] if m.get('run_id') == run_id], [
            ('assistant', ''.join(f'{n} ' for n in range(20))), ('user', 'Also check the README'), ('assistant', '20 21 22 ')])
        self.assertNotIn(task['id'], coding.turns)
        coding.tick()
        self.assertNotIn(task['id'], coding.text_waiting)

    def test_streamed_text_still_waiting_is_kept_when_dervo_fails_following_the_run(self):
        import json
        from unittest.mock import patch
        from types import SimpleNamespace
        import coding_integration
        turns = []
        class SyntheticTurn:
            def __init__(peer, binding, *, task, context, emit):
                peer.closed, peer.pending_tool_requests = False, ()
                peer.adapter = SimpleNamespace(state='running')
                peer.emit = emit
                turns.append(peer)
                emit('session', {'session_id': '00000000-0000-4000-8000-000000000125'})
            def tick(peer): raise RuntimeError('synthetic failure in the pump')
            def stop(peer): peer.closed = True
            def close(peer): peer.closed = True
        task = self.create()
        self.enterContext(patch.object(coding_integration, 'TEXT_SAVE_SECONDS', 60))
        self.enterContext(patch.object(self.host, 'require_beta_access', lambda: None))  # the account isn't what's tested
        with patch('codex_coding.CodexCodingTurn', SyntheticTurn):
            self.host.dispatch('task_approve', {**self.scope, 'taskId': task['id'], 'scopeVersion': 1})
        turns[0].emit('delta', {'item_id': 'answer', 'text': 'Written before the failure.'})
        with _capture_into([]):
            self.host.coding.tick()
        record = json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
        self.assertEqual(record['state'], 'disconnected', 'lost observation still reads as unknown')
        self.assertIn('Written before the failure.', [m['text'] for m in record['messages'] if m.get('role') == 'assistant'])
        self.assertNotIn(task['id'], self.host.coding.text_waiting)

    def test_real_broker_synthetic_protocol_correction_and_review(self):
        import json, time
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        peers = []
        class Peer(SyntheticCodingPeer):
            def __init__(peer, *a, **kw):
                super().__init__(*a, **kw)
                peers.append(peer)
            def tool(peer, name, args, n):
                peer.queue.append({'id':'rpc'+str(n),'method':'item/tool/call','params':dict(threadId=SID,turnId='turn-1',callId='call'+str(n),tool=name,arguments=args)})
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.append({'id':message['id'],'result':{'turn':{'id':'turn-1'}}})
                    peer.tool('dervo_read_file', {'path':'test_sample.py'}, 1)
                elif message.get('id') == 'rpc1':
                    peer.sent.append(message)
                    result = json.loads(message['result']['contentItems'][0]['text'])
                    peer.tool('dervo_write_file',dict(path='test_sample.py',expected_hash=result['sha256'],content=result['content']+'\n# synthetic correction\n'),2)
                elif message.get('id') == 'rpc2':
                    peer.sent.append(message)
                    peer.tool('dervo_run_tests',{},3)
                elif message.get('id') == 'rpc3':
                    peer.sent.append(message)
                    peer.queue.extend([{'method':'item/agentMessage/delta','params':dict(threadId=SID,turnId='turn-1',itemId='answer',delta='Synthetic peer finished; inspect actual evidence.')},
                        {'method':'turn/completed','params':{'threadId':SID,'turn':{'id':'turn-1','status':'completed'}}}])
                else:
                    super().send(message)
        task = self.create()
        boundary_associations = []
        for method in ('prepare', 'launch'):
            original = getattr(self.host.coding.evidence, method)
            def inspect(*args, original=original, **kwargs):
                boundary_associations.append(self.host.dispatch('tasks', self.scope)[0]['tokens'][-1]['attempt'])
                return original(*args, **kwargs)
            self.enterContext(patch.object(self.host.coding.evidence, method, inspect))
        with patch('codex_coding.OwnedProcess', Peer):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                self.host.tick()
                saved = self.host.dispatch('tasks',self.scope)[0]
                if saved['state'] != 'working': break
                time.sleep(.01)
            self.assertEqual(saved['state'],'ready_for_review',saved)
            detail = self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})
            attempt = detail['evidence']['attempts'][-1]
            self.assertEqual(attempt['state'],'verified',attempt)
            self.assertEqual(attempt['test']['exit_status'],0)
            self.assertIn('Ran 1 test',attempt['test']['stderr'])
            self.assertIn('synthetic correction',attempt['diff']['test_sample.py'])
            self.assertEqual([t['state'] for t in saved['tokens']],['delivered']*3)
            self.assertEqual(saved['tokens'][1]['attempt'],1)
            self.assertEqual(boundary_associations, [1, 1], 'Persist token/attempt before any write or launch')
            self.assertIn('Synthetic peer finished',saved['messages'][-1]['text'])
            self.host.dispatch('task_review',{**self.scope,'taskId':task['id'],'attemptNumber':1,'decision':'rejected'})
            self.host.dispatch('task_continue',{**self.scope,'taskId':task['id'],'text':'Improve the test again'})
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                self.host.tick()
                saved=self.host.dispatch('tasks',self.scope)[0]
                if saved['state']!='working':break
                time.sleep(.01)
            self.assertEqual(saved['state'],'ready_for_review',saved)
            self.assertEqual(saved['session_id'],SID)
            self.assertEqual(len(saved['runs']),2)
            self.assertTrue(any(m.get('method')=='thread/resume' for m in peers[1].sent))
            self.host.dispatch('task_review',{**self.scope,'taskId':task['id'],'attemptNumber':2,'decision':'approved'})
            self.assertEqual(self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})['evidence']['review']['decision'],'approved')
            self.assertEqual(self.host.dispatch('tasks',self.scope)[0]['state'],'idle')
            with (self.project/'test_sample.py').open('a') as file:
                file.write('# external change\n')
            changed = self.host.dispatch('tasks', self.scope)[0]
            self.assertIsNone(changed['review'])
            self.assertEqual(changed['state'], 'ready_for_review')
            self.assertEqual(self.host.store.shared_context('p'),[])

    def test_stop_and_shutdown_cancel_sandbox_job_without_delivery(self):
        import time
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer
        task = self.create()
        service = self.host.coding.evidence
        ctx = dict(thread_id='c', project_id='p', actor=ACTOR)
        service.approve(task['id'], scope_version=1, **ctx)
        pre = service.read_file(task['id'],'test_sample.py',**ctx)
        service.prepare(task['id'],changes={'test_sample.py':dict(expected_sha256=pre['sha256'],content=b'import time\ntime.sleep(30)\n')},**ctx)
        with patch('codex_coding.OwnedProcess', SyntheticCodingPeer):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            for _ in range(20):self.host.tick()
            saved=self.host.dispatch('tasks',self.scope)[0]
            self.assertEqual(saved['tokens'][0]['state'],'running')
            try:
                self.host.dispatch('task_stop',{**self.scope,'taskId':task['id']})
            except Rejected as exc:
                self.fail('Task stop is missing: '+str(exc))
            self.host.close()
            self.host=Host(self.base/'store')
            detail=self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})
            self.assertEqual(detail['evidence']['attempts'][-1]['state'],'cancelled')
            self.assertNotEqual(detail['task']['tokens'][0]['state'],'delivered')

    def test_lost_broker_delivery_and_foreign_scope_never_replay(self):
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        class LostPeer(SyntheticCodingPeer):
            deliveries = 0
            def send(peer, message):
                if message.get('method') == 'turn/start':
                    peer.sent.append(message)
                    peer.queue.extend([{'id':message['id'],'result':{'turn':{'id':'turn-1'}}},
                        {'id':'lost','method':'item/tool/call','params':dict(threadId=SID,turnId='turn-1',callId='lost',tool='dervo_read_file',arguments={'path':'test_sample.py'})}])
                elif message.get('id') == 'lost':
                    LostPeer.deliveries += 1
                    raise BrokenPipeError('Synthetic lost result delivery')
                else:super().send(message)
        task=self.create()
        self.host.store.add_thread('foreign','p','foreign','shared',None)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_detail',{**self.scope,'channelId':'foreign','taskId':task['id']})
        with patch('codex_coding.OwnedProcess',LostPeer):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            for _ in range(20):self.host.tick()
        saved=self.host.dispatch('tasks',self.scope)[0]
        self.assertEqual(saved['state'],'disconnected')
        self.assertEqual(saved['tokens'][0]['state'],'uncertain')
        self.assertEqual(saved['runs'][-1]['state'],'uncertain')
        self.host.close()
        self.host=Host(self.base/'store')
        self.host.dispatch('tasks',self.scope)
        for _ in range(5):self.host.tick()
        self.assertEqual(LostPeer.deliveries,1)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue',{**self.scope,'taskId':task['id'],'text':'retry'})

    def test_broker_audit_is_durable_before_execution_and_delivery(self):
        import base64, hashlib, json, sqlite3
        from contextlib import closing
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        cases = [
            ('failed_read', 'dervo_read_file', {'path':'not_in_scope.py'}, False, False),
            ('stale_write', 'dervo_write_file', {'path':'test_sample.py', 'expected_hash':'0'*64, 'content':'# stale'}, False, False),
            ('read', 'dervo_read_file', {'path':'test_sample.py'}, True, False),
            ('lost_read', 'dervo_read_file', {'path':'test_sample.py'}, True, True),
            ('large_read', 'dervo_read_file', {'path':'test_sample.py'}, False, False),
        ]
        for name, tool, arguments, success, lost in cases:
            with self.subTest(name=name):
                content = b'# authorized snapshot\n' if name != 'large_read' else b'#' * 262144
                (self.project/'test_sample.py').write_bytes(content)
                task = self.create()
                detail_args = {**self.scope, 'taskId':task['id']}
                boundaries, deliveries = [], []
                def durable_record():
                    # A separate connection proves the boundary was committed.
                    with closing(sqlite3.connect(self.host.store.db.execute('PRAGMA database_list').fetchone()[2])) as db:
                        row = db.execute('SELECT document FROM coding_tool_audit WHERE task_id=?', (task['id'],)).fetchone()
                    self.assertIsNotNone(row)
                    return json.loads(row[0])
                class Peer(SyntheticCodingPeer):
                    def send(peer, message):
                        if message.get('method') == 'turn/start':
                            peer.sent.append(message)
                            peer.queue.extend([{'id':message['id'],'result':{'turn':{'id':'turn-1'}}},
                                {'id':'audit','method':'item/tool/call','params':dict(threadId=SID,turnId='turn-1',callId='audit',tool=tool,arguments=arguments)}])
                        elif message.get('id') == 'audit':
                            record = durable_record()
                            response = message['result']
                            self.assertEqual(record['response'], dict(success=response['success'], text=response['contentItems'][0]['text']))
                            deliveries.append(record)
                            if lost:
                                raise BrokenPipeError('Synthetic uncertain audit delivery')
                            peer.sent.append(message)
                        else:
                            super().send(message)
                method = 'prepare' if tool == 'dervo_write_file' else 'read_file'
                original = getattr(self.host.coding.evidence, method)
                def execute(*args, **kwargs):
                    record = durable_record()
                    self.assertEqual(record['arguments'], arguments)
                    self.assertIsNone(record['response'])
                    boundaries.append(record)
                    return original(*args, **kwargs)
                with patch('codex_coding.OwnedProcess', Peer), patch.object(self.host.coding.evidence, method, execute):
                    self.host.dispatch('task_approve', {**detail_args, 'scopeVersion':1})
                    for _ in range(20): self.host.tick()
                self.assertEqual(len(boundaries), 1)
                self.assertEqual(len(deliveries), 1)
                self.assertEqual(deliveries[0]['response']['success'], success)
                self.assertLessEqual(len(deliveries[0]['response']['text'].encode()), 60000)
                if name in ('failed_read', 'stale_write'):
                    self.assertEqual(json.loads(deliveries[0]['response']['text']),
                        {'error':'File is outside task scope' if name == 'failed_read' else 'Stale file preimage'})
                else:
                    self.assertEqual(deliveries[0]['snapshot'], dict(bytes_b64=base64.b64encode(content).decode(), sha256=hashlib.sha256(content).hexdigest()))
                self.assertEqual((self.project/'test_sample.py').read_bytes(), content)
                self.host.close()
                self.host = Host(self.base/'store')
                for _ in range(5): self.host.tick()
                detail = self.host.dispatch('task_detail', detail_args)
                self.assertEqual(detail['broker_audit'], deliveries)
                self.assertEqual(detail['evidence']['attempts'], [])
                self.assertEqual(detail['task']['tokens'][0]['state'], 'uncertain' if lost else 'delivered')
                recorded = detail['task']['tokens'][0]
                self.assertIsInstance(recorded['started_at'], float)
                if lost:
                    self.assertNotIn('ended_at', recorded, 'an unconfirmed delivery never gets an end time')
                else:
                    self.assertLessEqual(recorded['started_at'], recorded['ended_at'])
                self.assertEqual(detail['task']['tokens'][0]['audit_ref'], deliveries[0]['token'])
                self.assertNotIn('snapshot', json.dumps(self.host.dispatch('tasks', self.scope)))
                self.assertNotIn('bytes_b64', json.dumps(detail['task']))
                self.assertEqual(len(deliveries), 1)
                if lost:
                    # A delivery Dervo can't confirm is never replayed.
                    with self.assertRaises(Rejected):
                        self.host.dispatch('task_continue', {**detail_args, 'text':'retry'})
                else:
                    # Since 16.4 a quit pauses the run: it finishes its step and ends interrupted, so it can be picked
                    # up again (29 Sep). Before, every quit lost the run and this was refused too.
                    self.assertEqual((detail['task']['state'], detail['task']['runs'][-1]['state']), ('blocked', 'interrupted'))
                    self.assertTrue(detail['retry']['available'])
                with self.assertRaises(Rejected):
                    self.host.coding.read(task['id'], dict(thread_id='c', project_id='p', actor='other'))
                self.host.store.add_thread('foreign-'+name, 'p', 'foreign', 'shared', None)
                with self.assertRaises(Rejected):
                    self.host.dispatch('task_detail', {**detail_args, 'channelId':'foreign-'+name})
                self.assertEqual(self.host.store.shared_context('p'), [])

    def test_broker_audit_survives_abrupt_restart_at_both_boundaries(self):
        import json
        from unittest.mock import patch
        from test_codex_coding import SyntheticCodingPeer, SID
        class Crash(BaseException): pass
        for boundary in ('execute', 'deliver'):
            with self.subTest(boundary=boundary):
                task = self.create()
                detail_args = {**self.scope, 'taskId':task['id']}
                sent = []
                class Peer(SyntheticCodingPeer):
                    def send(peer, message):
                        if message.get('method') == 'turn/start':
                            peer.sent.append(message)
                            peer.queue.extend([{'id':message['id'],'result':{'turn':{'id':'turn-1'}}},
                                {'id':'crash','method':'item/tool/call','params':dict(threadId=SID,turnId='turn-1',callId='crash',tool='dervo_read_file',arguments={'path':'test_sample.py'})}])
                        elif message.get('id') == 'crash':
                            sent.append(message['result'])
                            raise Crash()
                        else: super().send(message)
                original = self.host.coding.evidence.read_file
                def read(*args, **kwargs):
                    if boundary == 'execute': raise Crash()
                    return original(*args, **kwargs)
                with patch('codex_coding.OwnedProcess', Peer), patch.object(self.host.coding.evidence, 'read_file', read):
                    self.host.dispatch('task_approve', {**detail_args, 'scopeVersion':1})
                    with self.assertRaises(Crash):
                        for _ in range(20): self.host.tick()
                before = self.host.dispatch('task_detail', detail_args)
                self.assertEqual(before['task']['tokens'][0]['state'], 'executing' if boundary == 'execute' else 'delivering')
                # Drop the synthetic transport without graceful host state updates.
                for turn in self.host.coding.turns.values():
                    turn.process.close()
                    turn.neutral.cleanup()
                self.host.coding.turns.clear()
                self.host.store.close()
                self.host = Host(self.base/'store')
                for _ in range(5): self.host.tick()
                after = self.host.dispatch('task_detail', detail_args)
                self.assertEqual(after['broker_audit'], before['broker_audit'])
                self.assertEqual(after['task']['tokens'][0]['state'], 'uncertain')
                self.assertEqual(after['task']['state'], 'disconnected')
                self.assertEqual(after['broker_audit'][0]['arguments'], {'path':'test_sample.py'})
                if boundary == 'execute':
                    self.assertIsNone(after['broker_audit'][0]['response'])
                    self.assertEqual(sent, [])
                else:
                    self.assertEqual(len(sent), 1)
                    self.assertEqual(after['broker_audit'][0]['response'],
                        dict(success=sent[0]['success'], text=sent[0]['contentItems'][0]['text']))
                    self.assertIn('content', json.loads(after['broker_audit'][0]['response']['text']))
                with self.assertRaises(Rejected):
                    self.host.dispatch('task_continue', {**detail_args, 'text':'retry'})

    def _working_task_with_fake_turn(self, turn, channel='c'):
        """A task saved as working with a run in thread ``channel``, and ``turn`` installed as its live provider turn."""
        import json
        if channel == 'c':
            task = self.create()
        else:
            task = self.host.dispatch('task_create', {'projectId': 'p', 'channelId': channel,
                                                      'text': 'Improve the test', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        row = self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        saved = json.loads(row[0])
        saved.update(state='working', runs=[dict(id='run-1', state='running', session_id='s', prior_attempts=0)])
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.coding.turns[task['id']] = turn
        return task['id']

    def test_an_error_in_one_runs_background_work_ends_that_run_and_never_the_host(self):
        """run-lifecycle.md, finding 4. A raise inside the per-turn pump used to leave tick(), end the whole host and
        blame "Host closed". Now the run alone ends disconnected, its type is reported, and the host and other runs go
        on."""
        from types import SimpleNamespace
        import error_reports
        captured = []
        self.enterContext(_capture_into(captured))

        class Boom(SimpleNamespace):
            def tick(self):
                raise RuntimeError('synthetic tick failure')
        closed = {'n': 0}
        boom = Boom(closed=False, close=lambda: closed.__setitem__('n', closed['n'] + 1), stop=lambda: None)
        boomed = self._working_task_with_fake_turn(boom)
        # A second, healthy run in another thread, to prove the host keeps going for it.
        self.host.store.add_thread('c2', 'p', 'c2', 'shared', None)
        healthy_ticks = {'n': 0}
        healthy = SimpleNamespace(closed=False, tick=lambda: healthy_ticks.__setitem__('n', healthy_ticks['n'] + 1),
                                  close=lambda: None, stop=lambda: None, pending_tool_requests=[])
        healthy_id = self._working_task_with_fake_turn(healthy, channel='c2')

        self.host.tick()  # must not raise

        self.assertEqual(closed['n'], 1, 'the failed run is closed')
        self.assertNotIn(boomed, self.host.coding.turns, 'the failed run is dropped')
        self.assertGreaterEqual(healthy_ticks['n'], 1, 'the other run still ticked')
        self.assertIn(healthy_id, self.host.coding.turns, 'the healthy run is untouched')
        self.assertEqual(self.host.dispatch('task_detail', {'projectId': 'p', 'channelId': 'c2', 'taskId': healthy_id})['task']['state'], 'working')
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': boomed})['task']
        self.assertEqual((detail['state'], detail['runs'][-1]['state']), ('disconnected', 'uncertain'))
        self.assertIn('can’t say how it ended', detail['error'])
        self.assertTrue(any(r['error_type'] == 'RuntimeError' and r['operation'] == 'tick' for r in captured), captured)

    def test_a_run_whose_work_does_not_raise_is_left_working(self):
        """Negative control for finding 4: an ordinary tick with no error changes nothing about the run."""
        from types import SimpleNamespace
        import error_reports
        captured = []
        self.enterContext(_capture_into(captured))
        quiet = SimpleNamespace(closed=False, tick=lambda: None, close=lambda: None, stop=lambda: None,
                                pending_tool_requests=[])
        task_id = self._working_task_with_fake_turn(quiet)
        self.host.tick()
        detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})['task']
        self.assertEqual(detail['state'], 'working')
        self.assertIn(task_id, self.host.coding.turns)
        self.assertEqual(captured, [])

    def test_a_closed_turn_with_nothing_left_to_poll_is_dropped_from_the_pump(self):
        """run-lifecycle.md, finding 5. Closed turns used to stay in ``turns`` for the rest of the session, so every
        20 ms tick re-read and parsed every task that ran, and the host got slower all day. A closed turn with no open
        job is dropped; every reader treats a missing turn as a closed one."""
        from types import SimpleNamespace
        ticks = {'n': 0}
        closed_turn = SimpleNamespace(closed=True, close=lambda: None, stop=lambda: None,
                                      tick=lambda: ticks.__setitem__('n', ticks['n'] + 1))
        task_id = self._working_task_with_fake_turn(closed_turn)
        self.host.coding.tick()
        self.assertNotIn(task_id, self.host.coding.turns)
        self.assertEqual(ticks['n'], 1, 'the closed turn was ticked once more before being dropped')
        # Negative control: a still-open turn is kept.
        open_turn = SimpleNamespace(closed=False, close=lambda: None, stop=lambda: None, tick=lambda: None,
                                    pending_tool_requests=[])
        kept = self._working_task_with_fake_turn(open_turn)
        self.host.coding.tick()
        self.assertIn(kept, self.host.coding.turns)

    def _orphan(self, *argv):
        """A real process in its own group whose parent has exited, so launchd adopts it: what a provider CLI looks like
        after its host died. Killed at cleanup if still alive."""
        import os, signal, subprocess, sys, time
        from coding_integration import process_identity
        # The interpreter's real binary, so the executable doesn't change under an exec shim after we read it.
        argv = (process_identity(os.getpid())['exe'], *argv[1:]) if argv[0] == sys.executable else argv
        spawn = ('import subprocess,sys; d=subprocess.DEVNULL; '
                 'print(subprocess.Popen(sys.argv[1:], start_new_session=True, stdin=d, stdout=d, stderr=d).pid)')
        pid = int(subprocess.run([sys.executable, '-c', spawn, *argv], capture_output=True, text=True, check=True).stdout)
        def kill():
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.addCleanup(kill)
        for _ in range(100):
            live = process_identity(pid)
            if live and live['ppid'] == 1:
                return pid, live
            time.sleep(.02)
        self.fail('the orphan never reparented')

    @staticmethod
    def _alive(pid):
        import os, time
        from coding_integration import process_identity
        for _ in range(60):
            if process_identity(pid) is None:
                return False
            time.sleep(.05)
        return True

    def test_the_restart_pass_reaps_a_provider_group_the_dead_host_left_running(self):
        """run-lifecycle.md, finding 1, and security review M2. A run saved with the CLI's group-leader pid, start time
        and executable, still alive after the host died, is SIGKILLed by the next host's restart pass. A run whose pid
        is now another process (a reused pid) is left alone."""
        import json, sys
        # The provider the dead host left behind, and an unrelated process that later got another run's saved pid. The
        # unrelated one says "claude" on its command line, as the Claude app or a Terminal session would.
        provider, provider_live = self._orphan(sys.executable, '-c', 'import time; time.sleep(60)', 'fake_claude_cli')
        reused, reused_live = self._orphan(sys.executable, '-c', 'import time; time.sleep(60)', 'claude')
        runs = {provider: dict(start=provider_live['start'], exe=provider_live['exe']),
                reused: dict(start=reused_live['start'] - 1_000_000, exe=reused_live['exe'])}  # the earlier process with that pid
        for pid, process in runs.items():
            task = self.create()
            saved = json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
            saved.update(state='working', runs=[dict(id='run-1', state='running', session_id='s', prior_attempts=0, pid=pid, process=process)])
            with self.host.store.db:
                self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        self.host = Host(self.base / 'store')  # its restart pass reaps the orphan
        self.assertFalse(self._alive(provider), 'the orphaned provider group was killed on relaunch')
        self.assertTrue(self._alive(reused), 'a reused pid is never signalled')
        recovered = self.host.dispatch('tasks', self.scope)
        self.assertEqual({(t['state'], t['runs'][-1]['state']) for t in recovered}, {('disconnected', 'uncertain')})

    def _lost_run(self, full_access, baseline=None, rollout=None):
        """A task left ``working`` by a dead host, its run sent, with a git starting point and the project folder bound.
        ``rollout`` (beta.18): lines of the Codex rollout its session left, saved where Codex keeps it in the profile."""
        import json
        task = self.create()
        saved = json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
        session = '0b5c2f9e-4d3a-4e8b-9a71-2f6c1d0e8a55' if rollout is not None else 's'
        saved.update(state='working', full_access=full_access, session_id=session,
                     runs=[dict(id='run-1', state='running', session_id=session, prior_attempts=0, baseline=baseline or dict(head='0' * 40, dirty={}))],
                     tokens=[dict(token='c2', tool='native:shell', native=True, state='running', started_at=1.0)] if rollout is not None else saved.get('tokens', []))
        saved['binding'] = dict(saved.get('binding') or {}, cwd=str(self.project))
        if rollout is not None:
            home = Path(self.host.store.binding(**saved['context'])['home'])
            folder = home / 'sessions' / '2026' / '10' / '05'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f'rollout-2026-10-05T10-40-00-{session}.jsonl').write_text('\n'.join(rollout) + '\n')
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        self.host.close()
        return task['id']

    def test_a_lost_run_its_record_shows_between_steps_is_paused_and_one_mid_shell_asks(self):
        """Beta.18 (direction B, lost_runs): after a restart Dervo reads the provider's own record. A run whose every
        step ended is Paused when Dervo restarted, with no Leave it stopped to press and no hold on the project; one cut
        off in a shell command keeps the question, naming that command."""
        import run_ending
        from test_stopping_settle import CODEX_DONE, CODEX_PATCH, CODEX_SHELL, CODEX_START
        task_id = self._lost_run(full_access=False, rollout=CODEX_START + CODEX_PATCH + CODEX_SHELL + CODEX_DONE)
        self.host = Host(self.base / 'store')
        detail = self._settled(task_id)
        self.assertEqual((detail['state'], detail['runs'][-1]['state'], detail['runs'][-1]['end_cause']), ('blocked', 'interrupted', 'restart_paused'))
        self.assertEqual(detail['error'], 'Paused when Dervo restarted.')
        self.assertEqual(detail['ending']['by'] if detail.get('ending') else run_ending.classify(detail)['by'], 'restart')
        self.assertEqual([t['state'] for t in detail['tokens']], ['cancelled'])
        status = self.host.dispatch('status', {})
        self.assertEqual([(r['taskId'], r.get('paused')) for r in status['lostRuns']], [(task_id, True)])
        self.assertIn('It was between steps, so it paused there.', status['lostRunsDetail'])
        retry = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})['retry']
        self.assertTrue(retry['available'], 'Continue picks up in the same task and session')
        # Cut off in a shell command: still unconfirmed, and the words name it.
        task_id = self._lost_run(full_access=False, rollout=CODEX_START + CODEX_PATCH + CODEX_SHELL)
        self.host = Host(self.base / 'store')
        detail = self._settled(task_id)
        self.assertEqual(detail['state'], 'disconnected')
        self.assertEqual(detail['runs'][-1]['after_close']['record']['state'], 'unsure')
        self.assertIn('“gh workflow run preview.yml”', detail['error'])
        self.assertEqual([r['taskId'] for r in self.host.dispatch('status', {})['lostRuns'] if not r.get('paused')], [task_id])

    def _saved(self, task_id):
        import json
        return json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()[0])

    def _settled(self, task_id, ticks=200):
        """The task once the relaunch's folder read has landed (it runs on a thread and is applied on the tick)."""
        for _ in range(ticks):
            self.host.tick()
            detail = self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})['task']
            if 'checking' not in (detail['runs'][-1].get('after_close') or {}):
                return detail
            time.sleep(0.05)
        self.fail('the folder read never landed')

    def _step_running(self, state):
        """A task in ``state`` whose last provider step (a Bash call) is still saved as running."""
        import json
        task = self.create()
        saved = self._saved(task['id'])
        saved.update(state=state, runs=[dict(id='run-1', state='uncertain' if state == 'disconnected' else 'running',
                                             session_id='s', prior_attempts=0)],
                     tokens=[dict(token='toolu_1', tool='native:Bash', native=True, state='running', started_at=1.0)])
        with self.host.store.db:
            self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
        return task['id']

    def _held_and_settled(self, task_id):
        """Whether an update is held by this task, then Mark as stopped on it."""
        import project_lifecycle
        held = [row for row in project_lifecycle.holds(self.host, self.scope['projectId'], running_only=True, details=True)
                if row['task'] == task_id]
        return held, self.host.dispatch('task_settle', {**self.scope, 'taskId': task_id})

    def test_a_quit_mid_step_leaves_nothing_running_so_mark_as_stopped_and_updates_work(self):
        """29 Sep. Quitting during a Bash step marked the task disconnected but left the step 'running'. The restart
        pass only looked at 'working' tasks, so the step stayed running for good: Mark as stopped refused ("A task or
        check is still running") and every update and archive was held, with no button that cleared it."""
        from coding_integration import CLOSE_LOST

        class Turn:
            closed = False
            def stop(self): pass
            def close(self): self.closed = True
        task_id = self._step_running('working')
        self.host.coding.turns[task_id] = Turn()
        self.host.close()  # the quit
        self.host = Host(self.base / 'store')
        saved = self._saved(task_id)
        self.assertEqual((saved['state'], saved['error'], saved['tokens'][0]['state']), ('disconnected', CLOSE_LOST, 'uncertain'))
        held, settled = self._held_and_settled(task_id)
        self.assertEqual(held, [])
        self.assertEqual((settled['state'], settled['tokens'][0]['state']), ('blocked', 'cancelled'))

    def test_the_restart_pass_unsticks_a_step_an_older_quit_left_running(self):
        """The same record as 16.2 and 16.3 left it: disconnected, its step still 'running'. The next start marks the
        step unknown, so the person can mark the run stopped and the update goes ahead."""
        task_id = self._step_running('disconnected')
        self.host.close()
        self.host = Host(self.base / 'store')
        self.assertEqual(self._saved(task_id)['tokens'][0]['state'], 'uncertain')
        held, settled = self._held_and_settled(task_id)
        self.assertEqual(held, [])
        self.assertEqual(settled['state'], 'blocked')

    def test_the_restart_pass_saves_a_lost_run_before_reading_its_folder_and_the_first_reply_never_waits(self):
        """Security review 20 Sep, M1. The folder read of a lost full-access run (git status in the project folder)
        used to run inside CodingTasks.__init__ with a 10 s budget, before the host answered anything, and the task was
        saved only after it. The bridge kills a host that takes 10 s to answer, so a slow folder killed the host before
        the save, the task stayed ``working``, and every relaunch hit the same wall. Now the task is saved first, the
        read runs on a thread, the first reply comes at once, and a relaunch during the read reads nothing."""
        import threading
        from unittest.mock import patch
        import native_evidence
        from coding_integration import RESTART_LOST
        task_id = self._lost_run(full_access=True)
        calls = []

        def slow_changes(cwd, base, run=None, own=None):  # a git status that takes longer than the bridge allows
            calls.append(threading.current_thread().name)
            run(['/bin/sh', '-c', 'sleep 3'], capture_output=True, text=True)  # through the capped runner
            return ['greeting.py'], [], []
        with patch.object(native_evidence, 'changes', slow_changes):
            started = time.monotonic()
            self.host = Host(self.base / 'store')
            status = self.host.dispatch('status', {})
            self.assertLess(time.monotonic() - started, 2, 'the first reply never waits for the folder read')
            self.assertEqual([r['taskId'] for r in status['lostRuns']], [task_id])
            saved = self._saved(task_id)
            self.assertEqual((saved['state'], saved['runs'][-1]['state'], saved['error']), ('disconnected', 'uncertain', RESTART_LOST))
            self.assertEqual(saved['runs'][-1]['after_close'], dict(reaped=False, changed=None, checking=True), 'saved before the read')
            # A relaunch while that read is still going (the app killed and opened again) finds the task already marked,
            # reads no folder, and answers at once: the same wall can't come back.
            self.host.close()  # the app died; the read's thread is still asleep in the fake
            started = time.monotonic()
            self.host = Host(self.base / 'store')
            self.assertEqual(self.host.dispatch('status', {})['lostRuns'], [])
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(self.host.dispatch('task_detail', {**self.scope, 'taskId': task_id})['task']['state'], 'disconnected')
            for _ in range(3):
                self.host.tick()
            self.assertEqual(calls, ['dervo-after-close'], 'one read, on the thread, from the first host only')
            self.assertEqual(self._saved(task_id)['runs'][-1]['after_close'], dict(reaped=False, changed=None, checking=True))
        # A read that does land: the host applies it on its tick, and the task's words name the files.
        task_id = self._lost_run(full_access=True)
        with patch.object(native_evidence, 'changes', lambda cwd, base, run=None, own=None: (['greeting.py'], [], [])):
            self.host = Host(self.base / 'store')
            detail = self._settled(task_id)
        after = detail['runs'][-1]['after_close']
        self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=False, changed=['greeting.py']))
        # Beta.18: the provider's record is read too (lost_runs); this fixture has none, so today's words stay.
        self.assertEqual(after['record']['state'], 'unreadable')
        self.assertIn('kept working and finished on its own: greeting.py changed', detail['error'])
        self.assertEqual(self._saved(task_id)['error'], detail['error'], 'the result is saved, not just shown')

    def test_a_lost_run_without_full_access_reads_no_folder_and_a_dealt_with_task_keeps_its_words(self):
        """Negative controls for M1. A restricted run can't have edited the folder, so nothing is read and nothing
        waits; and when the person marks a task stopped before the read lands, the read completes the run's record
        but leaves the task's words alone."""
        from unittest.mock import patch
        import native_evidence
        from coding_integration import RESTART_LOST
        task_id = self._lost_run(full_access=False)
        calls = []
        with patch.object(native_evidence, 'changes', lambda *a, **k: calls.append(a) or ([], [], [])):
            self.host = Host(self.base / 'store')
            saved = self._saved(task_id)
            # Beta.18: its provider's record is still read (lost_runs), on the thread; its folder isn't.
            self.assertEqual((saved['state'], saved['error'], saved['runs'][-1]['after_close']), ('disconnected', RESTART_LOST, dict(reaped=False, changed=None, checking=True)))
            for _ in range(3):
                self.host.tick()
            self.assertEqual(calls, [])
            after = self._saved(task_id)['runs'][-1]['after_close']
            self.assertEqual(({key: after[key] for key in ('reaped', 'changed')}, after['record']['state'], 'checking' in after),
                             (dict(reaped=False, changed=None), 'unreadable', False))
        # Dealt with before the read lands: the words stay, the run's record is completed.
        task_id = self._lost_run(full_access=True)
        gate = __import__('threading').Event()

        def gated(cwd, base, run=None, own=None):
            gate.wait(10)
            return ['greeting.py'], [], []
        with patch.object(native_evidence, 'changes', gated):
            self.host = Host(self.base / 'store')
            saved = self._saved(task_id)
            saved.update(state='blocked', error='Marked stopped by hand.')
            with self.host.store.db:
                self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (__import__('json').dumps(saved), task_id))
            gate.set()
            detail = self._settled(task_id)
            self.assertEqual((detail['state'], detail['error']), ('blocked', 'Marked stopped by hand.'))
            after = detail['runs'][-1]['after_close']
            self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=False, changed=['greeting.py']))

    def test_the_folder_read_after_a_crash_caps_each_git_call(self):
        """M1: one git call in the read is capped well under the bridge's limit, and a read past its cap answers None,
        which keeps RESTART_LOST's wording (check your folder). A quick read answers with the files."""
        from unittest.mock import patch
        import native_evidence
        from coding_integration import changes_after_close, AFTER_CLOSE_CALL_SECONDS, AFTER_CLOSE_SECONDS
        self.assertLess(AFTER_CLOSE_CALL_SECONDS, 5)
        self.assertLess(AFTER_CLOSE_SECONDS, 20)
        task = dict(full_access=True, binding=dict(cwd=str(self.project)))
        run = dict(baseline=dict(head='0' * 40, dirty={}))
        with patch.object(native_evidence, 'changes', lambda cwd, base, run=None, own=None: run(['/bin/sh', '-c', 'sleep 30'], capture_output=True)):
            started = time.monotonic()
            self.assertIsNone(changes_after_close(task, run, per_call=0.5))
            self.assertLess(time.monotonic() - started, 2)
        with patch.object(native_evidence, 'changes', lambda cwd, base, run=None, own=None: (['a.py'], ['b.py (deleted)'], ['c.py'])):
            self.assertEqual(changes_after_close(task, run, per_call=0.5), ['a.py', 'b.py', 'c.py'])
        self.assertIsNone(changes_after_close(dict(task, full_access=False), run), 'restricted: nothing to read')

    def test_the_folder_read_after_a_crash_never_runs_a_filter_the_repository_planted(self):
        """M2 on M1's path: the relaunch's git status in a full-access run's folder passes the repository's own filter
        drivers switched off, so a planted ``filter.<name>.clean`` doesn't run at launch with no click."""
        import subprocess
        import native_evidence
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.base), 'GIT_TERMINAL_PROMPT': '0'}
        git = lambda *args: subprocess.run(['/usr/bin/git', *args], cwd=self.project, capture_output=True, text=True, env=env, check=True)
        git('init', '-q'); git('config', 'user.name', 'T'); git('config', 'user.email', 't@example.test')
        (self.project / 'greeting.py').write_text('x = 1\n')
        git('add', '-A'); git('commit', '-q', '-m', 'start')
        marker = self.base / 'filtered'
        git('config', 'filter.pwn.clean', f"sh -c 'echo clean >> {marker}; cat'")
        (self.project / '.gitattributes').write_text('* filter=pwn\n')
        baseline = native_evidence.baseline(str(self.project))
        marker.unlink(missing_ok=True)
        (self.project / 'greeting.py').write_text('x = 2\n')  # same size: git has to hash it, which is when the filter runs
        task_id = self._lost_run(full_access=True, baseline=baseline)
        self.host = Host(self.base / 'store')
        detail = self._settled(task_id)
        after = detail['runs'][-1]['after_close']
        self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=False, changed=['greeting.py']))
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        # Negative control: plain git in the same folder runs the plant.
        subprocess.run(['/usr/bin/git', 'status', '--porcelain'], cwd=self.project, capture_output=True, env=env, timeout=30)
        self.assertIn('clean', marker.read_text().split())

    def test_the_reaper_needs_pid_start_time_and_executable_to_match(self):
        """Security review M2: pid reuse, with a real unrelated process."""
        import os, subprocess, sys
        from coding_integration import reap_orphan, provider_identity, process_identity
        pid, live = self._orphan(sys.executable, '-c', 'import time; time.sleep(60)', 'claude')
        exact = dict(start=live['start'], exe=live['exe'])
        for saved in (None, {}, dict(exact, start=exact['start'] - 1), dict(exact, start=exact['start'] + 1_000_000),
                      dict(exact, exe='/Applications/Claude.app/Contents/MacOS/Claude')):
            self.assertFalse(reap_orphan(pid, saved), saved)
        self.assertTrue(self._alive(pid))
        self.assertFalse(reap_orphan(os.getpid(), process_identity(os.getpid())), 'not orphaned, never signalled')
        self.assertFalse(reap_orphan(1, exact))
        # provider_identity only vouches for this host's own child leading its own group; an orphan it didn't start is None.
        self.assertIsNone(provider_identity(pid))
        self.assertIsNone(provider_identity(os.getppid()))
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
        self.addCleanup(lambda: child.poll() is None and (child.kill(), child.wait()))
        mine = provider_identity(child.pid)
        self.assertEqual(set(mine), {'start', 'exe'})
        self.assertEqual(mine['exe'], process_identity(child.pid)['exe'])
        # Negative control: the exact identity does kill the orphan's group.
        self.assertTrue(reap_orphan(pid, exact))
        self.assertFalse(self._alive(pid))

    def test_a_run_that_will_not_close_cleanly_does_not_wedge_the_host_shutdown(self):
        """The host builds the coordinator at start now (finding 2), so close() runs on every quit. One run whose
        cleanup raises must not stop the others or leave the store open."""
        from types import SimpleNamespace
        stubborn = SimpleNamespace(closed=False, stop=lambda: (_ for _ in ()).throw(RuntimeError('will not stop')),
                                   close=lambda: None)
        good = {'closed': 0}
        healthy = SimpleNamespace(closed=False, stop=lambda: None, close=lambda: good.__setitem__('closed', good['closed'] + 1))
        bad_id = self._working_task_with_fake_turn(stubborn, channel='c')
        self.host.store.add_thread('c2', 'p', 'c2', 'shared', None)
        good_id = self._working_task_with_fake_turn(healthy, channel='c2')
        self.host.close()  # must not raise, even though the first run's stop() throws
        self.assertEqual(good['closed'], 1, 'the healthy run was still closed despite the earlier failure')
        # The store still closed: a query now raises. Without the guard, the stubborn run would have stopped close()
        # before this line.
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            self.host.store.db.execute('SELECT 1')
        # Reopen so the shared tearDown's close() is a no-op path, and the healthy run reads disconnected.
        self.host = Host(self.base / 'store')
        self.assertEqual(self.host.dispatch('task_detail', {'projectId': 'p', 'channelId': 'c2', 'taskId': good_id})['task']['state'], 'disconnected')

    def test_active_chat_status_is_not_hidden_by_a_saved_task(self):
        from types import SimpleNamespace
        self.create()
        self.host.turns['synthetic'] = SimpleNamespace(binding={'id':'c'}, closed=False, begun=True,
            adapter=SimpleNamespace(state='running'), stop=lambda:None, close=lambda:None)
        status=self.host.dispatch('conversation_status',self.scope)
        self.assertEqual(status['state'],'working')
        self.assertIn('chat',status['detail'])

    def test_task_files_cannot_enter_host_store_or_profile_roots(self):
        (self.host.root/'test_sample.py').write_text('import unittest\n')
        self.host.store.add_project('protected',self.host.root,'p','synthetic-model')
        self.host.store.add_thread('protected','protected','protected','private',ACTOR)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_create',dict(projectId='protected',channelId='protected',text='Forbidden',files=['test_sample.py'],testFile='test_sample.py'))
        # An ancestor project doesn't turn a nested Store into an approved file.
        (self.base/'test_sample.py').write_text('import unittest\n')
        self.host.store.add_project('ancestor',self.base,'p','synthetic-model')
        self.host.store.add_thread('ancestor','ancestor','ancestor','private',ACTOR)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_create',dict(projectId='ancestor',channelId='ancestor',text='Forbidden',files=['test_sample.py','store/conversations.sqlite3'],testFile='test_sample.py'))

    def test_scope_requires_separate_human_approval_and_is_private(self):
        try:
            task = self.create()
        except Rejected as exc:
            self.fail('Task creation API is missing: ' + str(exc))
        self.assertEqual(task['state'], 'needs_permission')
        self.assertIsNone(task['session_id'])
        self.assertGreater(task['messages'][0].get('created_at', 0), 0)
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'needs_permission')
        self.assertEqual(status['mode'], 'Task scope pending')
        self.assertEqual(task['visibility'], 'private')
        self.assertEqual(task['messages'][0]['text'], 'Improve the test')
        self.assertEqual(self.host.store.binding('c','p',actor=ACTOR)['session_id'], None)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_create', {**self.scope, 'text':'bad','files':['../secret'],'testFile':'test_sample.py'})
        denied = self.host.dispatch('task_deny', {**self.scope, 'taskId':task['id']})
        self.assertEqual(denied['state'], 'blocked')
        self.assertEqual(self.host.dispatch('tasks', self.scope)[0]['id'], task['id'])
        self.assertEqual(self.host.store.shared_context('p'), [])
        with self.assertRaises(Rejected):
            self.host.dispatch('task_approve', {**self.scope,'taskId':task['id'],'scopeVersion':1,'argv':['bad']})


class EvidenceViewTests(unittest.TestCase):
    """task_detail's reply leaves out file bytes and binary diffs. The stored record keeps them for Undo and Restore."""
    def evidence(self):
        import base64, json
        png = bytes(range(256)) * 40000  # about 10 MB, like a task that committed screenshots
        snap = {'sha256': 'abc', 'bytes_b64': base64.b64encode(png).decode()}
        return {'id': 't', 'attempts': [{'number': 1, 'before': {'a.png': None, 'b.py': {'sha256': 'b0', 'bytes_b64': 'eA=='}},
                'after': {'a.png': snap, 'b.py': {'sha256': 'b1', 'bytes_b64': 'eQ=='}},
                'diff': {'a.png': 'Binary diff (hex bytes):\n' + 'ff ' * 1000000, 'b.py': '--- before/b.py\n+++ after/b.py\n-x\n+y\n'}}]}

    def test_reply_drops_bytes_and_binary_diffs_but_keeps_hashes_and_text(self):
        import json
        from coding_integration import evidence_view
        stored = self.evidence()
        before = json.dumps(stored)
        view = evidence_view(stored)
        attempt = view['attempts'][0]
        self.assertLess(len(json.dumps(view)), 10000)
        self.assertNotIn('bytes_b64', json.dumps(view))
        self.assertEqual(attempt['after']['a.png'], {'sha256': 'abc', 'bytes': 10240000})
        self.assertEqual(attempt['after']['b.py'], {'sha256': 'b1', 'bytes': 1})
        self.assertIsNone(attempt['before']['a.png'])
        self.assertTrue(attempt['diff']['a.png'].startswith('Binary diff'))
        self.assertEqual(attempt['diff']['b.py'], stored['attempts'][0]['diff']['b.py'])
        self.assertEqual(json.dumps(stored), before, 'the stored record is not changed')

    def test_negative_control_the_stored_record_is_over_the_old_limit(self):
        import json
        self.assertGreater(len(json.dumps(self.evidence())), 8 * 1024 * 1024)


class CommitSubjectWiringTests(unittest.TestCase):
    """Approved commits carry the conversation's name (#29 part 5): commit_record passes it to task_git."""

    def owner(self, db):
        import types
        from unittest import mock
        owner = types.SimpleNamespace(store=types.SimpleNamespace(db=db), evidence=mock.Mock())
        owner.evidence.read.return_value = {}
        owner.thread_name = lambda task: CodingTasks.thread_name(owner, task)
        return owner

    def test_commit_record_passes_the_thread_name_and_falls_back_without_one(self):
        import sqlite3
        from unittest import mock
        import task_git
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE local_channels (id TEXT PRIMARY KEY, name TEXT)')
        db.execute("INSERT INTO local_channels VALUES ('t1', 'Connector picker')")
        task = {'id': 'task-1', 'context': {'thread_id': 't1'}}
        with mock.patch.object(task_git, 'commit', return_value={'sha': 'abc'}) as commit:
            CodingTasks.commit_record(self.owner(db), task, {'cwd': '/tmp'}, how='approved')
            self.assertEqual(commit.call_args.kwargs['thread_name'], 'Connector picker')
            # Negative control: a thread with no row, and a store with no channels table, both give None, the old subject.
            CodingTasks.commit_record(self.owner(db), {'id': 'task-2', 'context': {'thread_id': 'other'}}, {'cwd': '/tmp'}, how='approved')
            self.assertIsNone(commit.call_args.kwargs['thread_name'])
            CodingTasks.commit_record(self.owner(sqlite3.connect(':memory:')), task, {'cwd': '/tmp'}, how='approved')
            self.assertIsNone(commit.call_args.kwargs['thread_name'])


class SharedFilesWiringTests(unittest.TestCase):
    """Night 2, finding 2: the commit is told which other thread left uncommitted lines in a shared file, from Dervo's
    records alone, so a refusal names it and the commit message says whose change stays in the folder."""

    def owner(self):
        import sqlite3
        import types
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE local_channels (id TEXT PRIMARY KEY, name TEXT)')
        db.execute("INSERT INTO local_channels VALUES ('t1', 'Version flag'), ('t2', 'Usage section'), ('t3', 'Fix missing file')")
        db.execute('CREATE TABLE coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        import json
        def put(id, thread, files, project='p', commit=None, origin='conversation'):
            doc = dict(id=id, origin=origin, context=dict(thread_id=thread, project_id=project, actor='local-user'),
                       completion=dict(changed_files=files), runs=[dict(prior_attempts=0)], tokens=[], **({'commit': commit} if commit else {}))
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', (id, json.dumps(doc)))
        put('v', 't1', ['tally.py'])                                            # uncommitted, another thread
        put('u', 't2', ['README.md'], commit=dict(sha='a' * 40, reverted=None))  # committed: not in the way
        put('r', 't2', ['test_tally.py'], commit=dict(sha='b' * 40, reverted=dict(sha='c' * 40)))  # undone: in the way again
        put('o', 't1', ['tally.py'], project='other')                          # another project
        put('h', 't9', ['tally.py'], origin='collaboration')                    # a helper's task
        put('me', 't3', ['tally.py'])                                           # this thread's own
        owner = types.SimpleNamespace(store=types.SimpleNamespace(db=db))
        owner.shared_files = lambda task: CodingTasks.shared_files(owner, task)
        return owner

    def test_other_threads_uncommitted_files_are_named_and_the_rest_are_not(self):
        owner = self.owner()
        task = {'id': 'me', 'context': {'thread_id': 't3', 'project_id': 'p', 'actor': 'local-user'}}
        self.assertEqual(owner.shared_files(task), {'tally.py': 'Version flag', 'test_tally.py': 'Usage section'})
        # Negative controls: a task in another project sees nothing of this one; a store without tasks answers empty.
        self.assertEqual(owner.shared_files({'id': 'x', 'context': {'thread_id': 't5', 'project_id': 'elsewhere'}}), {})
        import sqlite3, types
        bare = types.SimpleNamespace(store=types.SimpleNamespace(db=sqlite3.connect(':memory:')))
        self.assertEqual(CodingTasks.shared_files(bare, task), {})

    def test_the_other_threads_added_lines_ride_along_when_its_evidence_is_readable(self):
        import base64
        from unittest import mock
        owner = self.owner()
        def snap(text):
            return dict(sha256='x', bytes_b64=base64.b64encode(text.encode()).decode())
        owner.evidence = mock.Mock()
        owner.evidence.read.side_effect = lambda task_id, **ctx: (
            dict(attempts=[dict(number=1, before={'tally.py': snap('a\n')}, after={'tally.py': snap('a\nversion line\n')})]) if task_id == 'v'
            else dict(attempts=[]))
        task = {'id': 'me', 'context': {'thread_id': 't3', 'project_id': 'p', 'actor': 'local-user'}}
        found = CodingTasks.shared_files(owner, task)
        self.assertEqual(found['tally.py'], dict(name='Version flag', added=['version line']))
        self.assertEqual(found['test_tally.py'], 'Usage section', 'no evidence: the name alone')
        # Negative control: evidence that can't be read still gives the name.
        owner.evidence.read.side_effect = RuntimeError('locked')
        self.assertEqual(CodingTasks.shared_files(owner, task)['tally.py'], 'Version flag')

    def test_commit_record_passes_the_other_threads_to_the_commit(self):
        from unittest import mock
        import task_git
        owner = self.owner()
        owner.evidence = mock.Mock()
        owner.evidence.read.return_value = {}
        owner.thread_name = lambda task: CodingTasks.thread_name(owner, task)
        task = {'id': 'me', 'context': {'thread_id': 't3', 'project_id': 'p', 'actor': 'local-user'}}
        with mock.patch.object(task_git, 'commit', return_value={'sha': 'abc'}) as commit:
            CodingTasks.commit_record(owner, task, {'cwd': '/tmp'}, how='approved')
            self.assertEqual(commit.call_args.kwargs['others'], {'tally.py': 'Version flag', 'test_tally.py': 'Usage section'})
            self.assertEqual(commit.call_args.kwargs['thread_name'], 'Fix missing file')


class ItemsDoneWiringTests(unittest.TestCase):
    """Your approval's commit marks the thread's bugs and ideas done (Bugs and ideas, 19 Sep): items_done hands the
    commit to project_ideas.work_committed and never fails the approval. No text is read: the task's record alone."""

    def owner(self, ideas):
        import types
        host = types.SimpleNamespace(ideas=(lambda: ideas) if ideas is not None else None)
        return types.SimpleNamespace(host=host, store=types.SimpleNamespace(db=None))

    def test_an_undone_task_reaches_the_list_only_with_its_reverted_commit(self):
        from unittest import mock
        ideas = mock.Mock()
        ideas.work_reverted.return_value = [{'number': 1}]
        task = {'id': 'task-1', 'context': {'thread_id': 't1', 'project_id': 'p', 'actor': 'local-user'},
                'commit': {'sha': 'a' * 40, 'reverted': {'sha': 'b' * 40, 'at': 1}}}
        self.assertEqual(CodingTasks.items_reopened(self.owner(ideas), task), [{'number': 1}])
        ideas.work_reverted.assert_called_once_with('p', 't1', 'a' * 40, 'task-1', 'local-user')
        # Negative controls: a commit that stands, no commit, no host list, a list that raises.
        for record in ({'sha': 'a' * 40, 'reverted': None}, None, {'sha': None}):
            ideas.work_reverted.reset_mock()
            self.assertEqual(CodingTasks.items_reopened(self.owner(ideas), {**task, 'commit': record}), [])
            ideas.work_reverted.assert_not_called()
        self.assertEqual(CodingTasks.items_reopened(self.owner(None), task), [])
        ideas.work_reverted.side_effect = RuntimeError('locked')
        self.assertEqual(CodingTasks.items_reopened(self.owner(ideas), task), [])

    def test_a_committed_approval_reaches_the_list_and_nothing_else_does(self):
        from unittest import mock
        ideas = mock.Mock()
        ideas.work_committed.return_value = [{'number': 1}]
        task = {'id': 'task-1', 'context': {'thread_id': 't1', 'project_id': 'p', 'actor': 'local-user'}, 'commit': {'sha': 'a' * 40, 'reverted': None},
                'messages': [{'role': 'assistant', 'text': 'Fixed bug #2 and #3 as well, all done.'}]}
        self.assertEqual(CodingTasks.items_done(self.owner(ideas), task), [{'number': 1}])
        ideas.work_committed.assert_called_once_with('p', 't1', 'a' * 40, 'task-1', 'local-user')
        # Negative controls: no commit, a commit git refused, an undone commit, a host without the list, and a list
        # that raises. The agent's "Fixed bug #2" above reaches nothing in any of them.
        for record in (None, {'sha': None, 'skipped': 'Not a git repository. Nothing was committed.'}, {'sha': 'b' * 40, 'reverted': {'sha': 'c' * 40}}):
            ideas.work_committed.reset_mock()
            self.assertEqual(CodingTasks.items_done(self.owner(ideas), {**task, 'commit': record}), [])
            ideas.work_committed.assert_not_called()
        self.assertEqual(CodingTasks.items_done(self.owner(None), task), [])
        ideas.work_committed.side_effect = RuntimeError('list is locked')
        self.assertEqual(CodingTasks.items_done(self.owner(ideas), task), [], 'a failure to mark never fails the approval')


class ItemsDoneIntegrationTests(unittest.TestCase):
    """The same through a real host: a shared thread linked to a bug, its result approved, Dervo's commit, the mark."""
    create, run_completion = IntegrationTests.create, IntegrationTests.run_completion

    def setUp(self):
        IntegrationTests.setUp(self)
        from unittest.mock import patch
        patcher = patch('project_ideas.releases_root', return_value=self.base / 'Dervo Releases')  # never the Mac's own
        patcher.start()
        self.addCleanup(patcher.stop)
        self.host.store.add_thread('d', 'p', 'd', 'shared')
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('d', 'Fix the sample test'))
        self.scope = dict(projectId='p', channelId='d')
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'), ('add', '-A'), ('commit', '-q', '-m', 'Before')):
            import subprocess
            done = subprocess.run(['/usr/bin/git', *args], cwd=self.project, capture_output=True, text=True,
                                  env={'PATH': '/usr/bin:/bin', 'HOME': str(self.base), 'GIT_TERMINAL_PROMPT': '0'})
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_approving_the_linked_threads_result_marks_the_bug_done_with_the_commit(self):
        ideas = self.host.ideas()
        listing = ideas.dispatch('ideas_item_add', dict(projectId='p', profileId='p', kind='bug', name='Sample test is thin', text='It checks nothing.'), ACTOR)
        bug, = listing['items']
        unrelated = ideas.dispatch('ideas_item_add', dict(projectId='p', profileId='p', kind='bug', name='Sidebar jumps', text=''), ACTOR)['items'][-1]
        ideas.link_work('p', bug['id'], 'd', ACTOR, 'home')
        detail = self.run_completion(self.create(), mode='change')
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        self.assertEqual(ideas.item('p', bug['id'])['state'], 'building', 'a finished run marks nothing until you approve')
        approved = self.host.dispatch('task_review', {**self.scope, 'taskId': detail['task']['id'], 'attemptNumber': 1, 'decision': 'approved'})
        self.assertTrue(approved['commit']['sha'], approved['commit'])
        record = ideas.item('p', bug['id'])
        self.assertEqual((record['state'], record['done']['sha'], record['done']['task_id'], record['done']['thread_id']),
                         ('done', approved['commit']['sha'], detail['task']['id'], 'd'))
        self.assertEqual(ideas.item('p', unrelated['id'])['state'], 'planned', 'negative control: an item this thread wasn’t on')
        [line] = self.host.dispatch('conversation_pickup', dict(projectId='p', channelId='d'))['work']
        self.assertEqual((line['kind'], line['line'], line['can_undo']), ('done', 'Marked bug #1 done.', True))
        # Undo the task: the commit is reverted and the bug is open again, as it was, with the line saying so.
        undone = self.host.dispatch('task_undo', {**self.scope, 'taskId': detail['task']['id']})
        self.assertTrue(undone['commit']['reverted']['sha'])
        record = ideas.item('p', bug['id'])
        self.assertEqual((record['state'], record['fixed_in'], record['done']['sha']), ('building', None, approved['commit']['sha']))
        self.assertIsNotNone(record['done']['reverted_at'])
        lines = self.host.dispatch('conversation_pickup', dict(projectId='p', channelId='d'))['work']
        self.assertEqual([(line['kind'], line['line'], line['can_undo']) for line in lines],
                         [('reopened', 'Reopened bug #1.', False), ('working', 'Working on bug #1, Sample test is thin.', False)])
        self.assertEqual(ideas.item('p', unrelated['id'])['state'], 'planned', 'negative control: still untouched')


class FinishingStepsTests(unittest.TestCase):
    """Critical review, 20 Sep, section 10: the memory step reached the agent as the third of five separate trailing
    blocks after the request, and nothing checked afterwards that it happened. Now the during-task instructions come
    first and everything an agent does before its final answer is one numbered list with memory at the top."""
    def setUp(self):
        from test_project_memory import Coding
        import coding_integration, project_memory, project_lessons, project_skills, task_guide, chat_links
        self.ci, self.memory, self.lessons, self.skills, self.guide, self.links = coding_integration, project_memory, project_lessons, project_skills, task_guide, chat_links
        self.db = Coding().store.db
        self.tools = {task_guide.TOOL, project_memory.REMEMBER_TOOL, project_memory.SEARCH_TOOL, project_lessons.TOOL, project_skills.TOOL, chat_links.TOOL}

    def test_the_memory_step_is_the_first_item_of_one_numbered_list(self):
        tail = self.ci.finishing_steps(self.db, 'p', self.tools)
        self.assertEqual(tail.count('Before your final answer'), 1)
        self.assertIn('after your last dervo_update_task_guide call', tail)
        first, second, third = tail.index('\n1. ' + self.memory.PROMPT), tail.index('\n2. ' + self.lessons.PROMPT), tail.index('\n3. ' + self.skills.PROMPT)
        self.assertLess(first, second)
        self.assertLess(second, third)
        self.assertNotIn('\n4. ', tail, 'looking back is off unless the project turns it on')
        self.assertEqual(tail.count('dervo_remember'), 1)
        during = self.ci.during_task(self.tools)
        for block in (self.guide.PROMPT, self.links.PROMPT, self.memory.MESSAGE_FACTS_PROMPT, self.memory.SEARCH_PROMPT):
            self.assertIn(block, during)
        self.assertNotIn(self.memory.PROMPT, during, 'the end-of-task memory step is in the list, not a block of its own')
        self.assertLess(during.index(self.memory.MESSAGE_FACTS_PROMPT), len(during) - len(self.memory.SEARCH_PROMPT) - 2,
                        'facts in the message are asked for with the task, before the work')
        self.assertNotIn(self.memory.SEARCH_PROMPT, tail, 'looking up earlier work is a during-task instruction')

    def test_the_list_follows_the_tools_the_task_has(self):
        # Negative controls: no memory tool, no dervo_remember anywhere; no guide, no guide anchor; no tools, no list.
        without_memory = self.tools - {self.memory.REMEMBER_TOOL}
        self.assertNotIn('dervo_remember', self.ci.finishing_steps(self.db, 'p', without_memory))
        self.assertNotIn('dervo_remember', self.ci.during_task(without_memory))
        self.assertIn('\n1. ' + self.lessons.PROMPT, self.ci.finishing_steps(self.db, 'p', without_memory))
        self.assertNotIn('dervo_update_task_guide', self.ci.finishing_steps(self.db, 'p', self.tools - {self.guide.TOOL}))
        self.assertEqual(self.ci.finishing_steps(self.db, 'p', set()), '')
        self.assertEqual(self.ci.during_task(set()), '')
        # Looking back, when the project turns it on, closes the list rather than trailing after it.
        self.memory.set_enabled(self.db, 'p', True, 'owner')
        self.memory.set_reflect(self.db, 'p', True, 'owner')
        tail = self.ci.finishing_steps(self.db, 'p', self.tools)
        self.assertTrue(tail.endswith('4. ' + self.memory.REFLECT_PROMPT), tail[-200:])

    def test_the_memory_step_names_package_json_beside_the_lockfile_and_a_fact_told_in_passing(self):
        # m1's grant has package.json and not pnpm-lock.yaml; m44's host arrived on the way to a rename.
        self.assertIn('the lockfile or package.json tells you', self.memory.PROMPT)
        self.assertIn('stated in their message', self.memory.PROMPT)
        self.assertIn('as your first tool call, before the work', self.memory.MESSAGE_FACTS_PROMPT)
        self.assertNotIn('dervo_search_history', self.memory.PROMPT)
        self.assertIn('dervo_search_history', self.memory.SEARCH_PROMPT)


class ApprovalRecheckTests(unittest.TestCase):
    """Beta.16.1: the pump re-checked every run's approval on every 20 ms round. It now does so at most once per
    APPROVAL_RECHECK_SECONDS, and a failed check still stops the run and is tried again on the next round."""

    def test_the_pump_rechecks_approval_at_most_once_a_second_and_a_refusal_still_raises(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import codex_coding
        from codex_coding import recheck_approval, APPROVAL_RECHECK_SECONDS
        self.assertEqual(APPROVAL_RECHECK_SECONDS, 1.0)
        checks, allowed = [], [True]
        def approved():
            checks.append(1)
            if not allowed[0]:
                raise Rejected('Task scope is no longer approved')
        turn = SimpleNamespace(approved_until=0.0, adapter=SimpleNamespace(_approved=approved, _tools={}))
        clock = [100.0]
        with patch.object(codex_coding.time, 'monotonic', lambda: clock[0]):
            recheck_approval(turn)
            for step in (.02, .3, .6):
                clock[0] = 100.0 + step
                recheck_approval(turn)
            self.assertEqual(len(checks), 1, 'rounds inside the second reuse the last check')
            clock[0] = 101.0
            allowed[0] = False
            with self.assertRaisesRegex(Rejected, 'no longer approved'):
                recheck_approval(turn)
            clock[0] = 101.02
            with self.assertRaisesRegex(Rejected, 'no longer approved'):
                recheck_approval(turn)  # a refusal isn't remembered as a pass
            self.assertEqual(len(checks), 3)
            allowed[0] = True
            recheck_approval(turn)
            turn.adapter._tools['token'] = object()  # a tool call is waiting: every round checks, as before
            for step in (.02, .04):
                clock[0] = 101.04 + step
                recheck_approval(turn)
        self.assertEqual(len(checks), 6)

    def test_both_turns_start_with_a_check_on_their_first_round(self):
        from claude_coding import ClaudeCodingTurn
        from codex_coding import CodexCodingTurn
        self.assertEqual((ClaudeCodingTurn.approved_until, CodexCodingTurn.approved_until), (0.0, 0.0))


class MidRunMessageOrderTests(unittest.TestCase):
    """Bryce, 24 Sep: a message sent mid-run stayed at the bottom of the thread, under the whole answer. Text written after
    it now goes below it (coding_integration.place_text)."""

    def test_text_after_your_mid_run_message_goes_below_it(self):
        from coding_integration import place_text
        messages = [dict(role='user', text='Run the tests', created_at=1)]
        place_text(messages, 'r1', 'answer', 'delta', 'Reading the tests. ', now=2)
        place_text(messages, 'r1', 'answer', 'delta', 'Running them. ', now=3)
        messages.append(dict(role='user', text='Also check the README', run_id='r1', steer='sent', created_at=4))
        place_text(messages, 'r1', 'answer', 'delta', 'Checked the README too.', now=5)
        self.assertEqual([(m['role'], m['text']) for m in messages], [
            ('user', 'Run the tests'), ('assistant', 'Reading the tests. Running them. '),
            ('user', 'Also check the README'), ('assistant', 'Checked the README too.')])
        place_text(messages, 'r1', 'answer', 'delta', ' Done.', now=6)
        self.assertEqual(messages[-1]['text'], 'Checked the README too. Done.', 'later text keeps growing the new part')

    def test_a_whole_answer_snapshot_is_split_at_your_message(self):
        from coding_integration import place_text
        messages = [dict(role='user', text='Run the tests', created_at=1)]
        place_text(messages, 'r1', 'answer', 'snapshot', 'Reading. ', now=2)
        messages.append(dict(role='user', text='Also the README', run_id='r1', steer='read', created_at=3))
        place_text(messages, 'r1', 'answer', 'snapshot', 'Reading. Checked the README.', now=4)
        place_text(messages, 'r1', 'answer', 'snapshot', 'Reading. Checked the README. Done.', now=5)
        self.assertEqual([m['text'] for m in messages if m['role'] == 'assistant'], ['Reading. ', 'Checked the README. Done.'])

    def test_without_a_mid_run_message_the_answer_stays_one_message(self):
        from coding_integration import place_text
        messages = [dict(role='user', text='Hi', created_at=1)]
        for piece in ('One. ', 'Two.'):
            place_text(messages, 'r1', 'answer', 'delta', piece, now=2)
        messages.append(dict(role='user', text='Earlier run note', run_id='r0', steer='read', created_at=3))
        place_text(messages, 'r1', 'answer', 'delta', ' Three.', now=4)
        self.assertEqual([m['text'] for m in messages if m['role'] == 'assistant'], ['One. Two. Three.'],
                         'a message from another run doesn\'t split this one')
