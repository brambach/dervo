"""Optional human result approval, separate from execution permission and from Independent review.

Real Host on a disposable store and project; providers are the synthetic Codex peers of the collaboration suite.
Nothing here launches a real provider, reads credentials or sends network traffic.
"""
import time
import unittest

import test_collaboration as tc
import test_independent_review as ir
import test_independent_review_safety as irs
from test_collaboration import GREETING, sha
from test_independent_review import REVIEW_ON
from dervo_connections import Rejected


class HumanAcceptanceTests(unittest.TestCase):
    """Borrows the disposable Host, project, peers and scripts of the review suites without re-running their tests."""
    owner_provider = 'codex'
    approval_required = None  # this suite tests the new default: no policy is written, the source stays ``default``
    setUp = ir.IndependentReviewTests.setUp
    for _name in ('policy', 'request', 'review_patch', 'flow', 'owner_only', 'settle_review', 'wait_reviewer_running'):
        locals()[_name] = getattr(ir.IndependentReviewTests, _name)
    for _name in ('close_host', 'reopen', 'agent', 'detail', 'packets', 'peer_patch', 'settle'):
        locals()[_name] = getattr(tc.CollaborationTests, _name)
    role_of = staticmethod(tc.CollaborationTests.role_of)
    hang_at = irs.IndependentReviewSafetyTests.hang_at
    del _name

    def acceptance(self):
        return self.host.dispatch('coding_permission', self.scope)['acceptance']

    def set_required(self, required):
        return self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': required})['acceptance']

    def failing_owner(self, peer, text):
        current = (self.project / 'greeting.py').read_bytes()
        return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': 'def greet(): return "bye"\n'}),
                ('tool', 'dervo_run_tests', {}), ('finish', 'Done.')]

    def text_only(self, peer, text):
        return [('finish', 'No change is needed.')]

    # --- policy storage ------------------------------------------------------------------------------------------------
    def test_new_project_defaults_off_legacy_grant_stays_on_and_the_toggle_is_independent_of_the_permission(self):
        policy = self.acceptance()
        self.assertEqual((policy['required'], policy['source']), (False, 'default'), policy)
        self.assertIn('does not authorise', policy['detail'])
        for bad in ('yes', 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected):
                    self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': bad})
        saved = self.set_required(True)
        self.assertEqual((saved['required'], saved['source']), (True, 'explicit'))
        # Revoking and re-granting the execution permission changes nothing about result approval.
        self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertEqual(self.acceptance()['required'], True)
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
        self.assertEqual(self.acceptance()['required'], True)
        self.reopen()
        self.assertEqual((self.acceptance()['required'], self.acceptance()['source']), (True, 'explicit'), 'persisted')
        self.assertEqual(self.set_required(False)['required'], False)
        # A store whose coding grant predates the approval policy keeps explicit approval on until changed.
        self.close_host()
        import sqlite3
        db = sqlite3.connect(self.base / 'store' / 'conversations.sqlite3')
        db.execute('DROP TABLE coding_acceptance_policy')
        db.commit()
        db.close()
        self.host_closed = False
        self.host = tc.Host(self.base / 'store')
        legacy = self.acceptance()
        self.assertEqual((legacy['required'], legacy['source']), (True, 'legacy'), legacy)
        self.assertEqual(self.starts, [], 'configuration launches nothing')

    # --- completion without human approval ----------------------------------------------------------------------------
    def test_reading_new_file_after_checks_requires_recheck_then_completes(self):
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['test_sample.py'],
                           'testFile': 'test_sample.py', 'projectAccess': True})
        (self.project / 'recovery.md').write_text('Saved checkpoint\n')
        self.policy(review=REVIEW_ON)

        def script(peer, text):
            if self.role_of(text) == 'reviewer':
                return [('tool', 'dervo_review_verdict', {'verdict': 'pass', 'findings': ''}),
                        ('finish', 'Reviewed the current snapshot.')]
            steps = [('tool', 'dervo_read_file', {'path': 'recovery.md'}),
                     ('tool', 'dervo_run_tests', {})]
            if 'Recheck after reading' not in text:
                steps.reverse()
            return steps + [('finish', 'Checked the saved checkpoint.')]

        with self.review_patch(script):
            started = self.request('Check the saved checkpoint')
            detail = self.settle_review(started['taskId'])
            self.assertEqual(detail['task']['state'], 'blocked', detail['task'].get('error'))
            self.assertIn('Run the configured checks again', detail['task']['error'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'skipped')
            self.assertEqual(detail['collaboration']['packets'], [], 'No review of stale checks')
            import json
            audits = [json.loads(row[0]) for row in self.host.store.db.execute(
                'SELECT document FROM coding_tool_audit WHERE task_id=?', (started['taskId'],))]
            read_response = next(json.loads(a['response']['text']) for a in audits
                                 if a.get('arguments', {}).get('path') == 'recovery.md')
            self.assertIn('dervo_run_tests again', read_response['verification_required'])
            followup = self.request('Recheck after reading the saved checkpoint.', client_id='req-2')
            detail = self.settle_review(followup['taskId'])
        self.assertEqual(detail['task']['state'], 'completed', detail['task'].get('error'))
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        self.assertIsNone(detail['task'].get('review'), 'Completion is not human approval')
        self.assertEqual((self.project / 'recovery.md').read_text(), 'Saved checkpoint\n')

    def test_checked_work_completes_without_human_approval_and_a_correction_never_rejects_past_work(self):
        self.policy(mode='off')
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] not in ('working', 'ready_for_review'))
        task = detail['task']
        self.assertEqual(task['state'], 'completed', task['error'])
        self.assertEqual(task['acceptance'], {'required': False, 'source': 'default'}, 'the policy is snapshotted on the task')
        self.assertIsNone(task.get('review'), 'never marked human-approved')
        self.assertIsNone(detail['evidence']['review'], 'no approval record is written')
        self.assertEqual(task['completion']['attempt'], detail['evidence']['attempts'][-1]['number'])
        self.assertEqual(task['completion']['review_phase'], None)
        self.assertEqual(task['completion']['changed_files'], ['greeting.py'])
        self.assertEqual(task['completion']['check_profile'], 'python-unittest', 'the completion records which check profile exited 0')
        self.assertEqual(task['completion']['test_exit_status'], 0)
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'completed')
        self.assertIn('Completed', status['detail'])
        view = self.host.dispatch('coding_requests', self.scope)[0]
        self.assertEqual(view['state'], 'completed')
        self.assertIn('Completed', view['detail'])
        self.assertEqual((self.project / 'greeting.py').read_text(), GREETING + '# v1\n')
        # Restart keeps the completed record.
        self.reopen()
        self.assertEqual(self.detail(task['id'])['task']['state'], 'completed')
        # A natural correction from the completed result starts the same task again; nothing is recorded as rejected.
        with self.review_patch(self.owner_only):
            corrected = self.host.dispatch('task_continue', {**self.scope, 'taskId': task['id'], 'text': 'Add one more comment line.'})
            self.assertEqual(corrected['state'], 'working')
            detail = self.settle(task['id'], until=lambda d: d['task']['state'] not in ('working', 'ready_for_review'))
        self.assertEqual(detail['task']['state'], 'completed', detail['task']['error'])
        self.assertIsNone(detail['evidence']['review'])
        self.assertEqual([c['from_state'] for c in detail['task']['corrections']], ['completed'])
        self.assertEqual(detail['task']['completion']['attempt'], 2)

    def test_failed_checks_text_answers_and_legacy_tasks_are_never_completed_automatically(self):
        self.policy(mode='off')
        with self.review_patch(self.failing_owner):
            started = self.request('Break the greeting')
            detail = self.settle(started['taskId'])
            for _ in range(5):
                self.host.tick()
                time.sleep(.01)
            detail = self.detail(started['taskId'])
        self.assertNotEqual(detail['task']['state'], 'completed', detail['task'])
        self.assertTrue(detail['task']['error'])
        with self.review_patch(self.text_only):
            started = self.request('Is the greeting fine?', client_id='req-2')
            detail = self.settle(started['taskId'])
            self.host.tick()
        self.assertEqual(detail['task']['state'], 'answered', 'a text-only reply stays a text answer')
        # A hand-entered task without a policy snapshot keeps the explicit human review.
        (self.project / 'greeting.py').write_text(GREETING)
        with self.review_patch(self.owner_only):
            task = self.host.dispatch('task_create', {**self.scope, 'text': 'Add a comment', 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py'})
            self.host.dispatch('task_approve', {**self.scope, 'taskId': task['id'], 'scopeVersion': 1})
            detail = self.settle(task['id'])
            for _ in range(5):
                self.host.tick()
                time.sleep(.01)
            detail = self.detail(task['id'])
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])

    def test_policy_changes_never_alter_pending_tasks_or_existing_records(self):
        self.policy(mode='off')
        self.set_required(True)
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual(detail['task']['acceptance']['required'], True)
        self.set_required(False)
        for _ in range(5):
            self.host.tick()
            time.sleep(.01)
        detail = self.detail(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review', 'turning approval off does not complete a pending task')
        attempt = detail['evidence']['attempts'][-1]['number']
        accepted = self.host.dispatch('task_review', {**self.scope, 'taskId': started['taskId'], 'attemptNumber': attempt, 'decision': 'approved'})
        self.assertEqual((accepted['review'], accepted['state']), ('approved', 'idle'))
        self.set_required(True)
        self.set_required(False)
        self.host.tick()
        self.assertEqual(self.detail(started['taskId'])['task']['review'], 'approved', 'policy flips leave the approval record unchanged')
        with self.review_patch(self.owner_only):
            second = self.request('Add another comment', client_id='req-2')
            detail = self.settle(second['taskId'], until=lambda d: d['task']['state'] not in ('working', 'ready_for_review'))
        self.assertEqual(detail['task']['state'], 'completed')
        self.set_required(True)
        for _ in range(5):
            self.host.tick()
            time.sleep(.01)
        self.assertEqual(self.detail(second['taskId'])['task']['state'], 'completed', 'turning approval on does not reopen completed work')

    # --- independent review outcomes ----------------------------------------------------------------------------------
    def test_only_a_clean_independent_review_completes_the_task(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
            self.host.tick()
            detail = self.detail(started['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        self.assertEqual(detail['task']['state'], 'completed', detail['task']['error'])
        self.assertEqual(detail['task']['completion']['review_phase'], 'passed')
        self.assertIsNone(detail['task'].get('review'))
        for name, script, expected in (('findings', self.flow('findings'), 'unresolved'), ('stale', self.flow('pass'), 'stale')):
            with self.subTest(name=name):
                (self.project / 'greeting.py').write_text(GREETING)
                self.peers.clear()
                with self.review_patch(script):
                    started = self.request('Add a comment ' + name, client_id='req-' + name)
                    if name == 'stale':
                        self.settle(started['taskId'], until=lambda d: any(p['status'] == 'running' for p in d['collaboration']['packets']))
                        (self.project / 'greeting.py').write_text(GREETING + '# outside\n')
                    detail = self.settle_review(started['taskId'])
                    for _ in range(20):
                        self.host.tick()
                        time.sleep(.01)
                    detail = self.detail(started['taskId'])
                self.assertEqual(detail['collaboration']['review']['phase'], expected)
                self.assertEqual(detail['task']['state'], 'ready_for_review', 'an unclean review needs the human')
        # A review that fails before content is sent is not clean either.
        (self.project / 'greeting.py').write_text(GREETING)
        self.policy(review={'enabled': True, 'reviewer': 'reviewer', 'effort': 'high'})
        with self.review_patch(self.flow('pass'), efforts=('low',)):
            started = self.request('Add a comment failed', client_id='req-failed')
            detail = self.settle_review(started['taskId'])
            self.host.tick()
            detail = self.detail(started['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'failed')
        self.assertEqual(detail['task']['state'], 'ready_for_review')

    def test_a_created_file_is_in_the_review_packet_and_the_completion_hashes_and_a_partial_snapshot_never_completes(self):
        (self.project / 'src').mkdir()
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'notes.py', 'test_sample.py'],
                                                       'testFile': 'test_sample.py', 'folders': ['src']})
        self.policy(review=REVIEW_ON)
        seen = {}

        def script(peer, text):
            if self.role_of(text) == 'reviewer':
                seen['text'] = text
                return [('tool', 'dervo_read_file', {'path': 'src/new_module.py'}), ('tool', 'dervo_review_verdict', {'verdict': 'pass', 'findings': ''}),
                        ('finish', 'REVIEW: clean.')]
            return [('tool', 'dervo_create_file', {'path': 'src/new_module.py', 'content': 'VALUE = 2\n'}),
                    ('tool', 'dervo_run_tests', {}), ('finish', 'Created; tests pass.')]
        with self.review_patch(script):
            started = self.request('Add src/new_module.py')
            detail = self.settle_review(started['taskId'])
            self.host.tick()
            detail = self.detail(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual(review['phase'], 'passed', review)
        self.assertEqual([p['phase'] for p in packets], ['initial'])
        sent = {f['path']: f['sha256'] for f in packets[0]['files']}
        self.assertEqual(sent['src/new_module.py'], sha(b'VALUE = 2\n'), 'the created deliverable is in the reviewer packet')
        self.assertIn('src/new_module.py ' + sha(b'VALUE = 2\n'), seen['text'])
        reviewer = self.detail(packets[0]['child_task_id'], dict(projectId='p', channelId='review-room'))
        self.assertEqual(reviewer['broker_audit'][0]['snapshot']['sha256'], sha(b'VALUE = 2\n'), 'the read-only reviewer read the created file')
        self.assertEqual(detail['task']['state'], 'completed', detail['task']['error'])
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual(set(review['snapshot']), set(attempt['after']), 'the review snapshot is the complete evidence scope')
        self.assertEqual(detail['task']['completion']['review_snapshot']['src/new_module.py'], sha(b'VALUE = 2\n'))
        # A verdict snapshot missing the created file is a subset of the evidence: it never completes the task.
        partial = {k: v for k, v in review['snapshot'].items() if k != 'src/new_module.py'}
        task = dict(detail['task'], state='ready_for_review', completion=None)
        task['collaboration']['review']['snapshot'] = partial
        evidence = self.host.coding.evidence.read(task['id'], **task['context'])
        self.assertEqual(self.host.coding.acceptance.blocker(task, evidence), 'independent review snapshot is not current')

    def test_a_correction_after_a_reviewed_completion_needs_a_fresh_review_of_the_current_files(self):
        """A changed task never completes on the old verdict: the correction re-arms one bounded review cycle (history kept)."""
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] == 'completed')
        first = detail['collaboration']['review']
        self.assertEqual((first['phase'], self.starts.count(('codex', 'cx'))), ('passed', 2))
        self.assertEqual(detail['task']['completion']['review_snapshot'], first['snapshot'])
        # The correction runs the owner again; the reviewer sees the corrected files before anything completes.
        with self.review_patch(self.flow('pass')):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': started['taskId'], 'text': 'Add one more comment line.'})
            reviewing = self.settle(started['taskId'], until=lambda d: d['task']['state'] != 'working')
            self.assertIn(reviewing['collaboration']['review']['phase'], ('pending', 'initial'), 'the old verdict is not reused')
            self.assertNotEqual(reviewing['task']['state'], 'completed')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] == 'completed')
        review = detail['collaboration']['review']
        self.assertEqual(review['phase'], 'passed')
        self.assertEqual(self.starts.count(('codex', 'cx')), 4, 'a fresh initial review and final check ran on the corrected files')
        self.assertEqual(review['snapshot']['greeting.py'], sha((self.project / 'greeting.py').read_bytes()), 'the verdict is about the current files')
        self.assertEqual(detail['task']['completion']['review_snapshot'], review['snapshot'])
        self.assertEqual([h['phase'] for h in review['history']], ['passed'], 'the earlier verdict stays recorded')
        self.assertEqual(review['history'][0]['snapshot'], first['snapshot'])
        self.assertEqual(review['history'][0]['packets'], first['packets'])
        self.assertEqual([c['from_state'] for c in detail['task']['corrections']], ['completed'])
        self.assertEqual([c['attempt'] for c in detail['task']['completions']], [detail['task']['corrections'][0]['attempt']], 'the first outcome record is kept')

    def test_a_correction_with_a_spent_review_budget_ends_explicit_and_never_completes(self):
        self.host.dispatch('collab_policy_set', {**self.scope, 'mode': 'off', 'agents': ['reviewer', 'fixer'], 'channels': ['main'],
                                                  'maxParallel': 3, 'maxDepth': 1, 'maxCalls': 2, 'review': REVIEW_ON})
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] == 'completed')
        self.assertEqual(self.starts.count(('codex', 'cx')), 2)
        with self.review_patch(self.flow('pass')):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': started['taskId'], 'text': 'Add one more comment line.'})
            detail = self.settle_review(started['taskId'])
            for _ in range(5):
                self.host.tick()
                time.sleep(.01)
            detail = self.detail(started['taskId'])
        review = detail['collaboration']['review']
        self.assertEqual(review['phase'], 'failed', review)
        self.assertIn('budget', review['error'])
        self.assertEqual(self.starts.count(('codex', 'cx')), 2, 'no review above the request budget')
        self.assertEqual(detail['task']['state'], 'ready_for_review', 'the human decides; nothing completed on the old verdict')
        self.assertEqual([h['phase'] for h in review['history']], ['passed'])

    def test_a_stale_completed_result_offers_a_correction_that_keeps_later_human_edits(self):
        self.policy(mode='off')
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py')
            self.settle(started['taskId'], until=lambda d: d['task']['state'] == 'completed')
        (self.project / 'greeting.py').write_text(GREETING + '# v1\n# human edit\n')
        detail = self.detail(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertTrue(detail['task']['completion']['stale'])
        self.assertIn('correction', detail['task']['error'])
        attempt = detail['evidence']['attempts'][-1]['number']
        for decision in ('approved', 'rejected'):
            with self.assertRaises(Rejected):
                self.host.dispatch('task_review', {**self.scope, 'taskId': started['taskId'], 'attemptNumber': attempt, 'decision': decision})
        with self.review_patch(self.owner_only):
            corrected = self.host.dispatch('task_continue', {**self.scope, 'taskId': started['taskId'], 'text': 'Add one more comment line.'})
            self.assertEqual(corrected['state'], 'working')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] not in ('working', 'ready_for_review'))
        self.assertEqual(detail['task']['state'], 'completed', detail['task']['error'])
        self.assertEqual((self.project / 'greeting.py').read_text(), GREETING + '# v1\n# human edit\n# v1\n', 'the human edit is preserved')
        self.assertEqual([c['from_state'] for c in detail['task']['corrections']], ['stale'])
        self.assertFalse(detail['task']['completion'].get('stale'))
        self.assertEqual([c['attempt'] for c in detail['task']['completions']], [1], 'the earlier outcome record is kept with its stale mark')
        self.assertTrue(detail['task']['completions'][0]['stale'])

    def test_completion_scope_is_cumulative_and_permission_currency_is_checked_before_completion_and_continuation(self):
        self.policy(mode='off')
        test = self

        def two_files_then_noop(peer, text):
            greeting = (test.project / 'greeting.py').read_bytes()
            notes = (test.project / 'notes.py').read_bytes()
            return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(greeting), 'content': greeting.decode() + '# v1\n'}),
                    ('tool', 'dervo_write_file', {'path': 'notes.py', 'expected_hash': sha(notes), 'content': notes.decode() + '# touched\n'}),
                    ('tool', 'dervo_write_file', {'path': 'notes.py', 'expected_hash': sha(notes.decode() + '# touched\n'), 'content': notes.decode()}),
                    ('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(greeting.decode() + '# v1\n'), 'content': greeting.decode() + '# v1\n'}),
                    ('tool', 'dervo_run_tests', {}), ('finish', 'Done; tests pass.')]
        with self.review_patch(two_files_then_noop):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] == 'completed')
        completion = detail['task']['completion']
        self.assertEqual(completion['attempt_changed_files'], [], 'the final write was a no-op')
        self.assertEqual(completion['changed_files'], ['greeting.py'], 'cumulative: notes.py was reverted, greeting.py changed')
        # Revoked execution permission: no continuation starts and nothing completes from it.
        self.host.dispatch('coding_permission_revoke', self.scope)
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': started['taskId'], 'text': 'Once more.'})
        self.assertEqual(self.starts.count(('codex', 'owner')), 1)
        self.assertEqual(self.detail(started['taskId'])['task']['state'], 'completed', 'the earlier outcome stays as recorded')

    def test_a_running_or_stopped_review_never_completes_and_stop_reaches_the_reviewer_after_the_owner_turn(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.hang_at('initial')):
            started = self.request('Add a comment to greeting.py')
            detail, packet, scope = self.wait_reviewer_running(started['taskId'])
            self.assertEqual(detail['task']['state'], 'ready_for_review', 'the owner turn ended; the reviewer is still active')
            self.assertEqual(detail['collaboration']['review']['phase'], 'initial')
            stopped = self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            self.assertEqual(stopped['collaboration']['review']['phase'], 'interrupted')
            for _ in range(10):
                self.host.tick()
                time.sleep(.01)
            detail = self.detail(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertIsNone(detail['task'].get('review'))


class ChangedScopeTests(unittest.TestCase):
    def changed(self, attempts):
        from human_acceptance import HumanAcceptance
        return HumanAcceptance.changed_scope({'attempts': attempts}, 1, attempts[-1])

    def test_later_read_is_not_a_file_change(self):
        original, edited, read_only = ({'sha256': value} for value in ('original', 'edited', 'read-only'))
        first = {'number': 1, 'before': {'code.py': original}, 'after': {'code.py': edited}}
        checked = {'number': 2, 'before': {'code.py': edited, 'notes.md': read_only},
                   'after': {'code.py': edited, 'notes.md': read_only}}
        self.assertEqual(self.changed([first, checked]), ['code.py'])

    def test_later_creation_is_still_a_change(self):
        existing, created = {'sha256': 'existing'}, {'sha256': 'new'}
        first = {'number': 1, 'before': {'code.py': existing}, 'after': {'code.py': existing}}
        create = {'number': 2, 'before': {'code.py': existing},
                  'after': {'code.py': existing, 'new.py': created}}
        checked = {'number': 3, 'before': create['after'], 'after': create['after']}
        self.assertEqual(self.changed([first, create, checked]), ['new.py'])

    def test_later_admitted_edit_and_revert_uses_its_first_preimage(self):
        original, edited = {'sha256': 'original'}, {'sha256': 'edited'}
        first = {'number': 1, 'before': {}, 'after': {}}
        write = {'number': 2, 'before': {'notes.md': original}, 'after': {'notes.md': edited}}
        self.assertEqual(self.changed([first, write]), ['notes.md'])
        revert = {'number': 3, 'before': write['after'], 'after': {'notes.md': original}}
        self.assertEqual(self.changed([first, write, revert]), [])


if __name__ == '__main__':
    unittest.main()
