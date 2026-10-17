"""Independent review safety regressions: a terminal verdict never trusts a stale snapshot, the human's stop of the
owner ends the review loop (reviewer child stopped, no correction, no late verdict), review packets spend the request's
configured delegation budget like any other ask, and human acceptance is refused while the host still drives the review.

Same disposable Host, project and synthetic peers as test_independent_review; nothing here launches a real provider.
"""
import time
import unittest

import test_collaboration as tc
import test_independent_review as ir
from test_collaboration import GREETING, sha
from test_independent_review import REVIEW_ON
from dervo_connections import Rejected

OUTSIDE = '# changed outside while the review ran\n'


class IndependentReviewSafetyTests(unittest.TestCase):
    owner_provider = 'codex'

    # --- scripts -------------------------------------------------------------------------------------------------------
    def passing_reviewer(self):
        """Owner writes and verifies; every review phase reads greeting.py and records pass."""
        test = self

        def script(peer, text):
            if test.role_of(text) == 'reviewer':
                return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('tool', 'dervo_review_verdict', {'verdict': 'pass', 'findings': ''}),
                        ('finish', 'REVIEW: clean.')]
            return test.owner_only(peer, text)
        return script

    def hang_at(self, phase):
        """flow('pass'), except that the party working in ``phase`` (the reviewer in initial or final, the owner in its
        correction) is acknowledged and then never answers."""
        test = self
        base = self.flow('pass')

        def script(peer, text):
            role = test.role_of(text)
            if role == 'reviewer' and f'Phase: {phase}' in text:
                return [('hang',)]
            if role == 'owner' and phase == 'correction' and 'Independent review findings' in text:
                return [('hang',)]
            return base(peer, text)
        return script

    def owner_asks_fixer(self):
        """The owner spends one ordinary helper ask on the fixer before it finishes; the reviewer would pass."""
        test = self

        def script(peer, text):
            role = test.role_of(text)
            if role == 'reviewer':
                return test.passing_reviewer()(peer, text)
            if role == 'worker':
                current = (test.project / 'greeting.py').read_bytes()
                return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': current.decode() + '# fixed by helper\n'}),
                        ('tool', 'dervo_run_tests', {}), ('finish', 'FIX: added a comment; tests pass.')]
            return [('tool', 'dervo_request_help', {'agent': 'fixer', 'purpose': 'Add a trailing comment to greeting.py', 'instructions': 'Append one comment line.', 'files': ['greeting.py']}),
                    ('tool', 'dervo_run_tests', {}), ('finish', 'Integrated the fix; tests pass.')]
        return script

    # --- helpers -------------------------------------------------------------------------------------------------------
    def edit_outside(self):
        with open(self.project / 'greeting.py', 'a') as handle:
            handle.write(OUTSIDE)

    def wait_phase_running(self, owner_id, phase):
        """The review packet of ``phase`` is running and its reviewer's provider turn is acknowledged."""
        def ready(d):
            packets = d['collaboration']['packets']
            return any(p['phase'] == phase and p['status'] == 'running' for p in packets)
        detail = self.settle(owner_id, until=ready)
        packet = next(p for p in detail['collaboration']['packets'] if p['phase'] == phase)
        scope = {'projectId': 'p', 'channelId': 'review-room'}
        self.settle(packet['child_task_id'], scope=scope, until=lambda d: bool(d['task']['runs']) and d['task']['runs'][-1]['state'] == 'running')
        return self.detail(owner_id), packet, scope

    def tick(self, count=20):
        for _ in range(count):
            self.host.tick()
            time.sleep(.01)

    def stop_owner(self, owner_id):
        return self.host.dispatch('task_stop', {**self.scope, 'taskId': owner_id})

    # --- 1. stale evidence never yields a terminal verdict -------------------------------------------------------------
    def test_initial_pass_after_an_outside_edit_is_stale_not_passed(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.passing_reviewer()):
            started = self.request('Add a comment to greeting.py')
            self.wait_reviewer_running(started['taskId'])
            self.edit_outside()
            detail = self.settle_review(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual([p['phase'] for p in packets], ['initial'])
        self.assertTrue(packets[0]['stale'], packets[0])
        self.assertNotEqual(review['phase'], 'passed', review)
        self.assertEqual(review['phase'], 'stale', review)
        self.assertIn('greeting.py', review['error'] or '')
        self.assertIsNone(detail['task'].get('review'))
        self.assertEqual(self.starts.count(('codex', 'owner')), 1)
        self.assertEqual((self.project / 'greeting.py').read_text(), GREETING + '# v1\n' + OUTSIDE)

    def final_after_outside_edit(self, final):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow(final)):
            started = self.request('Add a comment to greeting.py')
            self.wait_phase_running(started['taskId'], 'final')
            self.edit_outside()
            detail = self.settle_review(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual([p['phase'] for p in packets], ['initial', 'final'])
        self.assertTrue(packets[1]['stale'], packets[1])
        self.assertNotIn(review['phase'], ('passed', 'unresolved'), review)
        self.assertEqual(review['phase'], 'stale', review)
        self.assertIn('greeting.py', review['error'] or '')
        self.assertEqual(review['findings'], 'greeting.py line 1: add a docstring.', 'the corrected findings stay on the record')
        self.assertEqual(self.starts.count(('codex', 'owner')), 2)
        self.assertEqual(self.starts.count(('codex', 'cx')), 2)
        return review

    def test_final_pass_after_an_outside_edit_is_stale_not_passed(self):
        review = self.final_after_outside_edit('pass')
        self.assertIsNone(review['unresolved'])

    def test_final_findings_after_an_outside_edit_are_stale_not_unresolved(self):
        review = self.final_after_outside_edit('findings')
        self.assertEqual(review['unresolved'], 'greeting.py line 1: still no docstring.')

    # --- 2. the human's stop of the owner ends the review loop ---------------------------------------------------------
    def test_owner_stop_while_the_initial_review_runs_cancels_the_reviewer_and_ends_the_review(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.hang_at('initial')):
            started = self.request('Add a comment to greeting.py')
            detail, packet, review_scope = self.wait_reviewer_running(started['taskId'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'initial')
            self.assertEqual(detail['task']['state'], 'ready_for_review', "the owner's own provider turn already ended")
            self.stop_owner(started['taskId'])
            self.tick()
            detail = self.detail(started['taskId'])
            review = detail['collaboration']['review']
            self.assertEqual(review['phase'], 'interrupted', review)
            self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['withdrawn'])
            child = self.settle(packet['child_task_id'], scope=review_scope)['task']
            self.assertNotEqual(child['state'], 'working', 'the reviewer child was stopped')
            self.assertEqual(detail['task']['state'], 'ready_for_review', "the owner's finished result stays as it was")
            # Nothing later moves it: no correction, no second review, no late verdict.
            self.tick()
            detail = self.detail(started['taskId'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'interrupted')
            self.assertEqual(len(detail['collaboration']['packets']), 1)
            self.assertEqual(self.starts.count(('codex', 'owner')), 1, 'no correction after the stop')
            self.assertEqual(self.starts.count(('codex', 'cx')), 1)

    def test_owner_stop_during_the_correction_ends_the_review_with_no_final_check(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.hang_at('correction')):
            started = self.request('Add a comment to greeting.py')
            self.settle(started['taskId'], until=lambda d: d['collaboration']['review']['phase'] == 'correction'
                        and bool(d['task']['runs']) and d['task']['runs'][-1]['state'] == 'running')
            self.stop_owner(started['taskId'])
            detail = self.settle_review(started['taskId'])
            self.tick()
            detail = self.detail(started['taskId'])
            review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
            self.assertEqual(review['phase'], 'interrupted', review)
            self.assertEqual([p['phase'] for p in packets], ['initial'], 'no final check after the stop')
            self.assertEqual(self.starts.count(('codex', 'owner')), 2)
            self.assertEqual(self.starts.count(('codex', 'cx')), 1)
            self.assertNotEqual(detail['task']['state'], 'working')

    def test_owner_stop_while_the_final_check_runs_cancels_the_reviewer_and_ends_the_review(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.hang_at('final')):
            started = self.request('Add a comment to greeting.py')
            detail, packet, review_scope = self.wait_phase_running(started['taskId'], 'final')
            self.stop_owner(started['taskId'])
            detail = self.settle_review(started['taskId'])
            self.tick()
            detail = self.detail(started['taskId'])
            review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
            self.assertEqual(review['phase'], 'interrupted', review)
            self.assertEqual([p['status'] for p in packets], ['returned', 'withdrawn'])
            child = self.settle(packet['child_task_id'], scope=review_scope)['task']
            self.assertNotEqual(child['state'], 'working')
            self.assertEqual(self.starts.count(('codex', 'owner')), 2)
            self.assertEqual(self.starts.count(('codex', 'cx')), 2)

    # --- 3. review packets spend the configured delegation budget ------------------------------------------------------
    def test_review_packets_are_bounded_by_the_configured_call_budget(self):
        self.host.dispatch('collab_policy_set', {**self.scope, 'mode': 'off', 'agents': ['reviewer'], 'channels': ['main'], 'maxParallel': 3, 'maxDepth': 1,
                                                 'maxCalls': 1, 'review': REVIEW_ON})
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
            self.tick()
            detail = self.detail(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertNotEqual(review['phase'], 'passed', review)
        self.assertLessEqual(len([p for p in packets if p['status'] != 'refused']), 1, 'one ask per request means one review packet')
        self.assertEqual([p['phase'] for p in packets], ['initial'])
        self.assertEqual(self.starts.count(('codex', 'cx')), 1, 'no final check beyond the budget')
        self.assertEqual(self.starts.count(('codex', 'owner')), 1, 'no correction that the budget cannot let the reviewer check')
        self.assertEqual(review['phase'], 'unresolved', review)
        self.assertEqual(review['unresolved'], 'greeting.py line 1: add a docstring.')
        self.assertIn('budget', (review['error'] or '').lower())
        self.assertEqual(detail['task']['state'], 'ready_for_review')

    def test_an_ordinary_helper_ask_spends_the_same_budget_as_the_review(self):
        self.host.dispatch('collab_policy_set', {**self.scope, 'mode': 'automatic', 'agents': ['reviewer', 'fixer'], 'channels': ['main'], 'maxParallel': 3,
                                                 'maxDepth': 1, 'maxCalls': 1, 'review': REVIEW_ON})
        with self.review_patch(self.owner_asks_fixer()):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
            self.tick()
            detail = self.detail(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual([p['kind'] for p in packets if p['status'] != 'refused'], ['work'], 'the one ask went to the fixer; no review packet fits the budget')
        self.assertEqual(self.starts.count(('codex', 'cx')), 0, 'the reviewer was never started')
        self.assertEqual(review['phase'], 'failed', review)
        self.assertIn('budget', (review['error'] or '').lower())

    # --- 4. human acceptance waits for the review loop to end ----------------------------------------------------------
    def test_human_acceptance_is_refused_while_the_review_runs_and_available_once_it_ended(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.hang_at('initial')):
            started = self.request('Add a comment to greeting.py')
            detail, packet, review_scope = self.wait_reviewer_running(started['taskId'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'initial')
            attempt = detail['evidence']['attempts'][-1]['number']
            for decision in ('approved', 'rejected'):
                with self.subTest(decision=decision):
                    with self.assertRaises(Rejected):
                        self.host.dispatch('task_review', {**self.scope, 'taskId': started['taskId'], 'attemptNumber': attempt, 'decision': decision})
            detail = self.detail(started['taskId'])
            self.assertIsNone(detail['task'].get('review'), 'no decision was recorded')
            self.assertIsNone(detail['evidence']['review'])
            self.assertEqual(detail['task']['state'], 'ready_for_review')
            self.assertEqual(detail['collaboration']['review']['phase'], 'initial')
            # Once the loop is terminal and the evidence is still current, acceptance is the human's ordinary separate step.
            self.stop_owner(started['taskId'])
            detail = self.settle_review(started['taskId'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'interrupted')
            accepted = self.host.dispatch('task_review', {**self.scope, 'taskId': started['taskId'], 'attemptNumber': attempt, 'decision': 'approved'})
            self.assertEqual((accepted['review'], accepted['state']), ('approved', 'idle'))
            self.tick()
            detail = self.detail(started['taskId'])
            self.assertEqual((detail['task']['review'], detail['task']['state']), ('approved', 'idle'), 'an approval is never followed by a correction')
            self.assertEqual(self.starts.count(('codex', 'owner')), 1)


for _name in ('setUp', 'policy', 'request', 'review_patch', 'wait_reviewer_running', 'settle_review', 'flow', 'owner_only'):
    setattr(IndependentReviewSafetyTests, _name, ir.IndependentReviewTests.__dict__[_name])
for _name in ('close_host', 'reopen', 'agent', 'configure', 'detail', 'packets', 'peer_patch', 'role_of', 'settle'):
    setattr(IndependentReviewSafetyTests, _name, tc.CollaborationTests.__dict__[_name])


if __name__ == '__main__':
    unittest.main()
