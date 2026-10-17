"""Optional Independent review: one bounded, host-driven review of a finished owner task by a named read-only reviewer
agent (initial review, at most one owner correction, one final check), off by default, with an explicit reviewer binding
and a requested effort that is propagated exactly to the reviewer's own provider or refused before any content is sent.

Real Host on a disposable store and project; the providers are the synthetic Codex app-server peer and the synthetic
Claude stream-json peer from the collaboration suite. Nothing here launches a real provider or reads credentials.
"""
import base64
import json
import subprocess
import time
import unittest
from unittest.mock import patch

import test_collaboration as tc
from test_collaboration import GREETING, MODEL, sha
from dervo_connections import Rejected
import thread_folders

REVIEW_ON = {'enabled': True, 'reviewer': 'reviewer', 'effort': 'low'}
REVIEW_OFF = {'enabled': False, 'reviewer': None, 'effort': None}
CATALOG_EFFORTS = ('low', 'medium', 'high')


class IndependentReviewTests(unittest.TestCase):
    """Owner on Codex; the reviewer agent is the Codex agent in review-room (Claude reviewer added where a test needs it)."""
    owner_provider = 'codex'

    def setUp(self):
        tc.CollaborationTests.setUp(self)
        self.host.dispatch('harness_boundary', {'profileIds': ['owner', 'cx', 'cl']})
        self.agent('reviewer', 'cx', 'review-room', 'reviewer', 'Review for correctness. Point at exact lines.')
        self.agent('fixer', 'cl', 'work-room', 'worker', 'Make the smallest change that makes the tests pass.')

    def policy(self, review=None, mode='off', agents=('reviewer', 'fixer'), channels=('main',)):
        args = {**self.scope, 'mode': mode, 'agents': list(agents), 'channels': list(channels), 'maxParallel': 3, 'maxDepth': 1, 'maxCalls': 6}
        if review is not None:
            args['review'] = review
        return self.host.dispatch('collab_policy_set', args)

    def request(self, text, client_id='req-1', review=None):
        args = {**self.scope, 'clientId': client_id, 'text': text}
        if review is not None:
            args['review'] = review
        return self.host.dispatch('coding_request', args)

    def review_patch(self, script, efforts=CATALOG_EFFORTS):
        """The collaboration peers, with the Codex catalog advertising ``efforts`` for the model (the official
        ``model/list`` capability), so a requested effort is checked against what the catalog actually offers."""
        stack = self.peer_patch(script)
        import codex_coding
        Peer = codex_coding.OwnedProcess

        class CatalogPeer(Peer):
            def send(peer, message):
                if message.get('method') == 'model/list':
                    peer.sent.append(message)
                    entry = {'model': MODEL, 'hidden': False, 'defaultReasoningEffort': 'medium',
                             'supportedReasoningEfforts': [{'reasoningEffort': e, 'description': e} for e in efforts]}
                    peer.queue.append({'id': message['id'], 'result': {'data': [entry], 'nextCursor': None}})
                    return
                super().send(message)
        stack.enter_context(patch('codex_coding.OwnedProcess', CatalogPeer))
        return stack

    def fable_agent(self):
        """A Claude reviewer agent in its own conversation bound to the Claude profile; the project binding is restored."""
        store = self.host.store
        store.bind_project('p', 'cl', MODEL)
        store.add_thread('fable-room', 'p', 'fable-room', 'shared', None)
        store.bind_project('p', 'owner', MODEL)
        return self.agent('fable', 'cl', 'fable-room', 'reviewer', 'Independent reviewer.')

    def wait_reviewer_running(self, owner_task_id):
        """The initial review packet is running and the reviewer's provider turn is acknowledged, so a stop reaches a real turn."""
        detail = self.settle(owner_task_id, until=lambda d: any(p['status'] == 'running' for p in d['collaboration']['packets']))
        packet = detail['collaboration']['packets'][0]
        scope = {'projectId': 'p', 'channelId': 'review-room'}
        self.settle(packet['child_task_id'], scope=scope, until=lambda d: bool(d['task']['runs']) and d['task']['runs'][-1]['state'] == 'running')
        return detail, packet, scope

    def settle_review(self, task_id, terminal=('passed', 'unresolved', 'stale', 'failed', 'interrupted', 'skipped', None)):
        return self.settle(task_id, until=lambda d: d['task']['state'] != 'working' and (d['collaboration']['review'] or {}).get('phase', None) in terminal)

    # --- scripts -------------------------------------------------------------------------------------------------------
    def flow(self, final='pass'):
        """Owner writes and verifies; the initial review returns one finding; the owner corrects it; the final check
        returns ``final`` (pass or findings)."""
        test = self

        def script(peer, text):
            role = test.role_of(text)
            if role == 'reviewer':
                verdict = ({'verdict': 'findings', 'findings': 'greeting.py line 1: add a docstring.'} if 'Phase: initial' in text
                           else {'verdict': final, 'findings': 'greeting.py line 1: still no docstring.' if final == 'findings' else ''})
                return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('tool', 'dervo_review_verdict', verdict),
                        ('finish', 'REVIEW: ' + ('findings.' if verdict['verdict'] == 'findings' else 'clean.'))]
            current = (test.project / 'greeting.py').read_bytes()
            content = ('"""Greeting."""\n' + current.decode()) if 'Independent review findings' in text else current.decode() + '# v1\n'
            return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': content}),
                    ('tool', 'dervo_run_tests', {}), ('finish', 'Done; tests pass.')]
        return script

    def owner_only(self, peer, text):
        current = (self.project / 'greeting.py').read_bytes()
        return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': current.decode() + '# v1\n'}),
                ('tool', 'dervo_run_tests', {}), ('finish', 'Done; tests pass.')]

    # --- tests -----------------------------------------------------------------------------------------------------------
    def test_automatically_created_reviewer_runs_correction_and_final_review(self):
        from concurrent.futures import Future
        job = Future()
        job.set_result({'state': 'catalog', 'models': [{'model': MODEL,
                        'supportedReasoningEfforts': [{'reasoningEffort': 'low'}]}]})
        self.host.catalogs['owner'] = job
        policy = self.host.dispatch('collab_reviewer_setup', {**self.scope, 'profileId': 'owner', 'model': MODEL, 'effort': 'low'})
        name = policy['review']['reviewer']
        self.assertNotEqual(name, 'reviewer')
        with self.review_patch(self.flow()):
            request = self.request('Add a small improvement, then review it.')
            detail = self.settle_review(request['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        packets = detail['collaboration']['packets']
        self.assertEqual(len(packets), 2)
        self.assertTrue(all(p['agent'] == name for p in packets))

    def test_default_review_is_skipped_with_the_reason_when_the_conversation_moved_to_an_unlinked_account(self):
        # Bryce, 17 Sep: a conversation moved to his other provider's account, the project's reviewer stayed on the first,
        # and every request there was refused. The default review now steps aside, says why, and the work runs.
        self.policy(review=REVIEW_ON)
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='stranger' WHERE id='main'")
        view = self.host.dispatch('conversation_status', self.scope)['collaboration']['review']
        self.assertIn("@reviewer runs on Codex, which isn't linked to this conversation's Codex account", view['skipped_here'])
        with self.assertRaisesRegex(Rejected, 'cannot review this conversation'):
            self.request('Asked for review by name.', client_id='req-explicit', review=REVIEW_ON)
        self.assertEqual(self.host.dispatch('tasks', self.scope), [], 'an explicit review request is still refused and records nothing')
        with self.review_patch(lambda peer, text: self.owner_only(peer, text)):
            request = self.request('Add a small improvement.')
            detail = self.settle_review(request['taskId'])
        review = detail['collaboration']['review']
        self.assertEqual((review['phase'], review['packets']), ('skipped', []))
        self.assertIn('so no review was run', review['error'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        # Linked again, the same conversation is reviewed by default.
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='owner' WHERE id='main'")
        self.assertIsNone(self.host.dispatch('conversation_status', self.scope)['collaboration']['review']['skipped_here'])

    def test_manual_review_corrects_findings_without_running_checks(self):
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': [], 'testFile': None,
            'projectAccess': True, 'checkProfile': 'manual-review'})
        self.policy(review=REVIEW_ON)
        original = self.flow()
        def script(peer, text):
            return [step for step in original(peer, text) if not (step[0] == 'tool' and step[1] == 'dervo_run_tests')]
        with self.review_patch(script), patch('task_evidence.TaskEvidence.launch', side_effect=AssertionError('No checks allowed')):
            started = self.request('Improve greeting.')
            detail = self.settle_review(started['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed', detail['collaboration']['review'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual(len(detail['collaboration']['packets']), 2)
        self.assertTrue(all(a['test'] is None for a in detail['evidence']['attempts']))
        self.assertNotIn('completion', detail['task'])
        self.assertIn('Greeting.', detail['evidence']['attempts'][-1]['diff']['greeting.py'])

    def test_empty_manual_answer_skips_source_review_with_clear_reason(self):
        for path in self.project.iterdir(): path.unlink()
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': [], 'testFile': None,
            'projectAccess': True, 'checkProfile': 'manual-review'})
        self.policy(review=REVIEW_ON)
        with self.review_patch(lambda peer, text: [('finish', 'This project is empty. No changes were requested.')]):
            started = self.request('Is this project empty?')
            detail = self.settle_review(started['taskId'])
        self.assertEqual(detail['task']['state'], 'answered')
        self.assertEqual(detail['collaboration']['review']['phase'], 'skipped')
        self.assertIn('No project files', detail['collaboration']['review']['error'])
        self.assertEqual(detail['collaboration']['packets'], [])

    def test_typescript_review_inherits_check_profile_without_test_tools(self):
        from test_typescript_inputs import SEARCH_DATA, search_fixture
        files, _ = search_fixture(self.project)
        # The interface catalogs (beta.17) are over the 256 KiB a helper can be sent, and this test is about the
        # reviewer's tools, so its task leaves them out. Its host check then can't resolve them, which this test
        # doesn't look at (the same as before the catalogs were in the fixture).
        files = [f for f in files if f not in SEARCH_DATA]
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': files,
            'testFile': 'tsconfig.json', 'checkProfile': 'typescript-check'})
        self.policy(review=REVIEW_ON)
        def script(peer, text):
            if self.role_of(text) == 'reviewer':
                return [('tool', 'dervo_read_file', {'path': 'frontend/src/local/WorkspaceSearch.tsx'}),
                        ('tool', 'dervo_review_verdict', {'verdict': 'pass', 'findings': ''}),
                        ('finish', 'Read-only snapshot review. No tests executed by reviewer.')]
            return [('tool', 'dervo_run_tests', {}), ('finish', 'Host check returned.')]
        with self.review_patch(script):
            started = self.request('Check the search component with TypeScript.')
            detail = self.settle_review(started['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed', detail['collaboration']['review'])
        packet = detail['collaboration']['packets'][0]
        child = self.detail(packet['child_task_id'], {'projectId': 'p', 'channelId': 'review-room'})
        self.assertEqual(child['task']['check_profile'], 'typescript-check')
        self.assertEqual(child['task']['argv'], detail['task']['argv'])
        self.assertEqual(child['evidence']['attempts'], [], 'The reviewer must not execute a check')
        self.assertEqual([t['tool'] for t in child['task']['tokens']], ['dervo_read_file', 'dervo_review_verdict'])

    def test_toggle_is_off_by_default_validated_persisted_and_legacy_policy_calls_are_unchanged(self):
        view = self.host.dispatch('collab_policy', self.scope)
        self.assertEqual({k: view['review'][k] for k in REVIEW_OFF}, REVIEW_OFF, 'off by default')
        self.assertIn('notice', view['review'], 'the extra usage of a second agent is stated, not implied')
        legacy = self.policy(mode='automatic')  # the pre-existing call shape, no review key
        self.assertEqual((legacy['state'], legacy['mode'], {k: legacy['review'][k] for k in REVIEW_OFF}), ('automatic', 'automatic', REVIEW_OFF))
        bad = [{'enabled': 1, 'reviewer': 'reviewer', 'effort': 'low'}, {'enabled': True, 'reviewer': 'ghost', 'effort': 'low'},
               {'enabled': True, 'reviewer': 'fixer', 'effort': 'low'}, {'enabled': True, 'reviewer': 'reviewer', 'effort': 5},
               {'enabled': True, 'reviewer': 'reviewer', 'effort': ''}, {'enabled': True, 'reviewer': 'reviewer'},
               {'enabled': True, 'reviewer': None, 'effort': 'low'}, {'enabled': False, 'reviewer': 'reviewer', 'effort': 'low'},
               {'enabled': True, 'reviewer': 'reviewer', 'effort': 'low', 'model': 'other'}, 'on']
        for review in bad:
            with self.subTest(review=review):
                with self.assertRaises(Rejected):
                    self.policy(review=review)
        with self.assertRaises(Rejected):
            self.host.dispatch('collab_policy_set', {**self.scope, 'mode': 'off', 'agents': [], 'channels': ['main'], 'maxParallel': 3, 'maxDepth': 1, 'maxCalls': 6, 'review': None})
        self.assertEqual({k: self.host.dispatch('collab_policy', self.scope)['review'][k] for k in REVIEW_OFF}, REVIEW_OFF, 'a refused setting changes nothing')
        # A Claude reviewer takes only the documented CLI effort levels; nothing is coerced.
        self.fable_agent()
        for effort in ('ultra', 'LOW', 'low ', 'minimal'):
            with self.subTest(effort=effort):
                with self.assertRaises(Rejected):
                    self.policy(review={'enabled': True, 'reviewer': 'fable', 'effort': effort})
        saved = self.policy(review={'enabled': True, 'reviewer': 'fable', 'effort': 'xhigh'})
        self.assertEqual({k: saved['review'][k] for k in REVIEW_ON}, {'enabled': True, 'reviewer': 'fable', 'effort': 'xhigh'})
        self.assertEqual((saved['review']['provider'], saved['review']['model']), ('claude', MODEL), 'the binding names the exact provider and model')
        saved = self.policy(review=REVIEW_ON)
        self.assertEqual({k: saved['review'][k] for k in REVIEW_ON}, REVIEW_ON)
        self.assertEqual(saved['state'], 'off', 'review is a separate toggle; the help mode stays what it was')
        legacy = self.policy(mode='mentions')
        self.assertEqual({k: legacy['review'][k] for k in REVIEW_ON}, REVIEW_ON, 'a legacy save without the key keeps the stored review setting')
        self.reopen()
        self.assertEqual({k: self.host.dispatch('collab_policy', self.scope)['review'][k] for k in REVIEW_ON}, REVIEW_ON, 'persisted')
        self.assertEqual(self.starts, [], 'configuration launches nothing')

    def test_request_override_binds_the_task_snapshot_and_a_bad_override_sends_nothing(self):
        self.policy()
        for review in ({'enabled': True, 'reviewer': 'fixer', 'effort': 'low'}, {'enabled': True, 'reviewer': 'reviewer', 'effort': 3},
                       {'enabled': True, 'reviewer': 'reviewer', 'effort': 'low', 'extra': 1}, 'yes'):
            with self.subTest(review=review):
                with self.assertRaises(Rejected):
                    self.request('Add a comment to greeting.py', client_id='bad-' + json.dumps(review), review=review)
        self.assertEqual(self.host.dispatch('coding_requests', self.scope), [], 'a refused override records no request')
        self.assertEqual(self.host.dispatch('tasks', self.scope), [], 'and creates no task')
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py', review=REVIEW_ON)
            self.assertIsNotNone(started['id'], started)
            task = self.detail(started['taskId'])['task']
            review = task['collaboration']['review']
            self.assertEqual({k: review[k] for k in ('enabled', 'reviewer', 'effort', 'provider', 'model', 'profile_id', 'channel_id', 'source', 'phase')},
                             {'enabled': True, 'reviewer': 'reviewer', 'effort': 'low', 'provider': 'codex', 'model': MODEL, 'profile_id': 'cx',
                              'channel_id': 'review-room', 'source': 'request', 'phase': 'pending'})
            self.settle_review(started['taskId'])
        # The policy says on, the request says off: the request's explicit choice is the task's snapshot.
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.owner_only):
            started = self.request('Add another comment', client_id='req-2', review=REVIEW_OFF)
            detail = self.settle(started['taskId'])
        self.assertIsNone(detail['collaboration']['review'])
        self.assertEqual(detail['collaboration']['packets'], [])

    def test_deleting_the_reviewer_turns_review_off_and_never_blocks_a_request(self):
        # #42, 26 Sep: deleting the reviewer left the project's review pointing at it, and every coding request in the
        # project was refused ("the reviewer agent @review-... no longer exists").
        self.policy(review=REVIEW_ON)
        self.host.dispatch('collab_agent_delete', {'name': 'reviewer'})
        view = self.host.dispatch('collab_policy', self.scope)['review']
        self.assertEqual({k: view[k] for k in REVIEW_OFF}, REVIEW_OFF, 'the setting goes with the agent')
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        self.assertIsNone(detail['collaboration']['review'])

    def test_a_saved_review_naming_a_deleted_reviewer_is_skipped_with_the_reason(self):
        # A store saved before 17.1 can still name an agent that's gone, and a composer can still send it as its override.
        self.policy(review=REVIEW_ON)
        with self.host.store.db:
            self.host.store.db.execute("DELETE FROM collab_agents WHERE name='reviewer'")
        status = self.host.dispatch('conversation_status', self.scope)['collaboration']['review']
        self.assertIn('The reviewer was deleted', status['skipped_here'])
        with self.review_patch(self.owner_only):
            for client_id, override in (('req-default', None), ('req-explicit', REVIEW_ON)):
                with self.subTest(override=override):
                    started = self.request('Add a comment to greeting.py', client_id=client_id, review=override)
                    detail = self.settle_review(started['taskId'])
                    review = detail['collaboration']['review']
                    self.assertEqual((review['phase'], review['packets'], review['reviewer'], review['effort']), ('skipped', [], 'reviewer', 'low'))
                    self.assertIn('The reviewer was deleted, so no review was run', review['error'])
                    self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        self.assertNotIn(('codex', 'cx'), self.starts, 'no reviewer ran')

    def test_legacy_default_behaviour_is_unchanged_when_review_is_off(self):
        self.policy(mode='off')
        with self.review_patch(self.owner_only):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle(started['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        self.assertIsNone(detail['collaboration']['review'])
        self.assertEqual(detail['collaboration']['packets'], [])
        self.assertEqual(self.starts, [('codex', 'owner')])
        self.assertNotIn('effort', self.peers[0].sent[-1].get('params', {}) if self.peers[0].sent else {})
        self.assertFalse(any('effort' in m.get('params', {}) for m in self.peers[0].sent if m.get('method') == 'turn/start'))

    def test_effort_is_propagated_exactly_and_an_unadvertised_codex_effort_fails_before_content(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
        owner = [p for p in self.peers if p.env.get('CODEX_HOME', '').endswith('/owner')]
        reviewers = [p for p in self.peers if p.env.get('CODEX_HOME', '').endswith('/cx')]
        self.assertTrue(owner and reviewers, self.starts)
        for peer in owner:
            for m in peer.sent:
                if m.get('method') == 'turn/start':
                    self.assertNotIn('effort', m['params'], 'the owner keeps its own settings; effort is the reviewer request only')
        for peer in reviewers:
            turn = next(m for m in peer.sent if m.get('method') == 'turn/start')
            self.assertEqual(turn['params']['effort'], 'low', 'the official turn/start effort field, exactly the requested value')
            self.assertEqual(turn['params']['model'], MODEL)
            thread = next(m for m in peer.sent if m.get('method') in ('thread/start', 'thread/resume'))
            self.assertEqual(thread['params']['model'], MODEL)
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        # The catalog does not advertise the requested effort: the reviewer is refused before turn/start, with the reason.
        self.policy(review={'enabled': True, 'reviewer': 'reviewer', 'effort': 'high'})
        self.peers.clear()
        with self.review_patch(self.flow('pass'), efforts=('low', 'medium')):
            started = self.request('Add a second comment', client_id='req-2')
            detail = self.settle_review(started['taskId'])
        review = detail['collaboration']['review']
        self.assertEqual(review['phase'], 'failed', review)
        self.assertIn('high', review['error'])
        self.assertIn('effort', review['error'].lower())
        reviewers = [p for p in self.peers if p.env.get('CODEX_HOME', '').endswith('/cx')]
        self.assertTrue(reviewers, 'the reviewer process was opened to read the catalog')
        self.assertFalse(any(m.get('method') in ('thread/start', 'thread/resume', 'turn/start') for p in reviewers for m in p.sent), 'no content was sent')
        packets = detail['collaboration']['packets']
        self.assertEqual([p['status'] for p in packets], ['failed'])
        self.assertIn('high', packets[0]['error'])
        # A Claude reviewer receives the documented --effort flag and the exact model, under its own profile.
        self.fable_agent()
        self.policy(review={'enabled': True, 'reviewer': 'fable', 'effort': 'xhigh'})
        self.peers.clear()
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a third comment', client_id='req-3')
            detail = self.settle_review(started['taskId'])
        claude = [p for p in self.peers if 'CLAUDE_CONFIG_DIR' in p.env]
        self.assertTrue(claude, self.starts)
        for peer in claude:
            self.assertTrue(peer.env['CLAUDE_CONFIG_DIR'].endswith('/cl'))
            self.assertEqual(peer.argv[peer.argv.index('--effort') + 1], 'xhigh')
            self.assertEqual(peer.argv[peer.argv.index('--model') + 1], MODEL)
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed', detail['collaboration']['review'])
        self.assertEqual([p['destination']['provider'] for p in detail['collaboration']['packets']], ['claude', 'claude'])

    def test_reviewer_is_read_only_by_tool_contract_not_by_prompt(self):
        self.policy(review=REVIEW_ON)
        test = self

        def script(peer, text):
            if test.role_of(text) == 'reviewer':
                current = (test.project / 'greeting.py').read_bytes()
                return [('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': '# reviewer wrote this\n'}),
                        ('tool', 'dervo_review_verdict', {'verdict': 'pass', 'findings': ''}), ('finish', 'I changed the file.')]
            return test.owner_only(peer, text)
        with self.review_patch(script):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
        reviewer = next(p for p in self.peers if p.env.get('CODEX_HOME', '').endswith('/cx'))
        thread = next(m for m in reviewer.sent if m.get('method') == 'thread/start')
        self.assertEqual([t['name'] for t in thread['params']['dynamicTools']], ['dervo_read_file', 'dervo_review_verdict'], 'the bridge offers no write or test tool')
        self.assertEqual((self.project / 'greeting.py').read_text(), GREETING + '# v1\n', 'the reviewer changed nothing')
        # Since 16.4 (item 4) the write is refused with an error result the reviewer reads, not by ending its run, so its
        # verdict afterwards stands. Read-only comes from the contract: the file and the attempts above and below.
        ok, reply = reviewer.results[0]
        self.assertEqual((ok, reply['error']), (False, 'Unknown tool. This task can use: dervo_read_file, dervo_review_verdict.'))
        child = detail['collaboration']['packets'][0]['child_task_id']
        evidence = self.host.dispatch('task_detail', {'projectId': 'p', 'channelId': 'review-room', 'taskId': child})['evidence']
        self.assertEqual(evidence['attempts'], [], 'no file attempt exists for the reviewer')
        owner = next(p for p in self.peers if p.env.get('CODEX_HOME', '').endswith('/owner'))
        self.assertEqual([t['name'] for t in next(m for m in owner.sent if m.get('method') == 'thread/start')['params']['dynamicTools']],
                         ['dervo_ask_user', 'dervo_remind_user', 'dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_run_tests', 'dervo_update_task_guide', 'dervo_remember', 'dervo_search_history', 'dervo_propose_skill', 'dervo_propose_thread', 'dervo_check_chat', 'dervo_leave_note'], "the owner may ask questions and propose skills and threads; the reviewer keeps its read-only contract")

    def test_bounded_workflow_initial_findings_one_correction_final_check_and_human_acceptance_stays_separate(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
        task, review, packets = detail['task'], detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual(review['phase'], 'passed', review)
        self.assertEqual([p['phase'] for p in packets], ['initial', 'final'])
        self.assertEqual([p['status'] for p in packets], ['returned', 'returned'])
        self.assertEqual([p['kind'] for p in packets], ['review', 'review'])
        self.assertEqual(review['packets'], [p['id'] for p in packets])
        self.assertEqual(self.starts.count(('codex', 'owner')), 2, 'one initial run and exactly one correction')
        self.assertEqual(self.starts.count(('codex', 'cx')), 2, 'one initial review and one final check')
        self.assertEqual(review['findings'], 'greeting.py line 1: add a docstring.')
        self.assertIsNone(review['unresolved'])
        self.assertIn('Independent review findings', task['messages'][-2]['text'] if task['messages'][-1]['role'] == 'assistant' else task['messages'][-1]['text'])
        corrections = [m for m in task['messages'] if m.get('source') == 'independent_review']
        self.assertEqual(len(corrections), 1)
        self.assertEqual(corrections[0]['role'], 'user')
        self.assertIn('Independent review findings', corrections[0]['text'])
        self.assertIn('greeting.py line 1: add a docstring.', self.inputs['owner'], 'the owner received the findings automatically')
        self.assertEqual((self.project / 'greeting.py').read_text(), '"""Greeting."""\n' + GREETING + '# v1\n')
        self.assertEqual(task['state'], 'ready_for_review', task['error'])
        self.assertIsNone(task.get('review'), 'a passed independent review is not human acceptance')
        self.assertIsNone(detail['evidence']['review'])
        # The final packet describes the exact current files.
        final = packets[1]
        self.assertEqual(final['stale'], False)
        self.assertEqual(review['snapshot'], {f['path']: f['sha256'] for f in final['files']})
        self.assertEqual(review['snapshot']['greeting.py'], sha((self.project / 'greeting.py').read_bytes()))
        self.assertIn('Phase: initial', packets[0]['payload_text'])
        self.assertIn('Phase: final', final['payload_text'])
        self.assertEqual(self.inputs['cx'], final['payload_text'])
        # Human acceptance is its own step, after the bound.
        attempt = detail['evidence']['attempts'][-1]['number']
        accepted = self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'], 'attemptNumber': attempt, 'decision': 'approved'})
        self.assertEqual((accepted['review'], accepted['state']), ('approved', 'idle'))

    def test_unresolved_findings_are_returned_after_the_bound_with_no_third_review(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('findings')):
            started = self.request('Add a comment to greeting.py')
            detail = self.settle_review(started['taskId'])
            for _ in range(20):
                self.host.tick()
                time.sleep(.01)
            detail = self.detail(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual(review['phase'], 'unresolved', review)
        self.assertEqual(review['unresolved'], 'greeting.py line 1: still no docstring.')
        self.assertEqual([p['phase'] for p in packets], ['initial', 'final'])
        self.assertEqual(self.starts.count(('codex', 'owner')), 2)
        self.assertEqual(self.starts.count(('codex', 'cx')), 2)
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertIsNone(detail['task'].get('review'))

    def test_stale_snapshot_blocks_the_correction(self):
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            started = self.request('Add a comment to greeting.py')
            self.settle(started['taskId'], until=lambda d: any(p['status'] == 'running' for p in d['collaboration']['packets']))
            (self.project / 'greeting.py').write_text(GREETING + '# changed outside while the review ran\n')
            detail = self.settle_review(started['taskId'])
        review, packets = detail['collaboration']['review'], detail['collaboration']['packets']
        self.assertEqual(review['phase'], 'stale', review)
        self.assertEqual(review['unresolved'], 'greeting.py line 1: add a docstring.')
        self.assertEqual([p['phase'] for p in packets], ['initial'])
        self.assertTrue(packets[0]['stale'])
        self.assertEqual(self.starts.count(('codex', 'owner')), 1, 'no correction was started against files the review did not see')

    def test_restart_stop_late_result_and_reopen_never_replay_a_review_or_a_write(self):
        self.policy(review=REVIEW_ON)
        test = self

        def hang(peer, text):
            if test.role_of(text) == 'reviewer':
                return [('hang',)]  # the reviewer's turn is acknowledged and then never answers
            return test.owner_only(peer, text)
        with self.review_patch(hang):
            started = self.request('Add a comment to greeting.py')
            detail, packet, review_scope = self.wait_reviewer_running(started['taskId'])
            self.assertEqual(detail['collaboration']['review']['phase'], 'initial')
            # Stopping the reviewer from its own conversation ends the review: no correction, no second review.
            self.host.dispatch('task_stop', {**review_scope, 'taskId': packet['child_task_id']})
            detail = self.settle_review(started['taskId'])
            review = detail['collaboration']['review']
            self.assertEqual(review['phase'], 'interrupted', review)
            self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['failed'])
            self.assertEqual(self.starts.count(('codex', 'owner')), 1)
            # Reopening by the human is a separate, explicit act: it runs the owner again and starts no review.
            attempt = detail['evidence']['attempts'][-1]['number']
            self.host.dispatch('task_review', {**self.scope, 'taskId': started['taskId'], 'attemptNumber': attempt, 'decision': 'rejected'})
            self.host.dispatch('task_continue', {**self.scope, 'taskId': started['taskId'], 'text': 'Add one more comment line.'})
            detail = self.settle(started['taskId'])
            self.assertEqual(self.starts.count(('codex', 'owner')), 2)
            self.assertEqual(self.starts.count(('codex', 'cx')), 1, 'the bound is per task; a reopen replays no review')
            self.assertEqual(len(detail['collaboration']['packets']), 1)
            self.assertEqual(detail['collaboration']['review']['phase'], 'interrupted')
        # A restart while the initial review is in flight disconnects it; nothing is replayed afterwards.
        self.peers.clear()
        self.starts.clear()
        with self.review_patch(hang):
            started = self.request('Add a comment again', client_id='req-2')
            self.wait_reviewer_running(started['taskId'])
            self.reopen()
            detail = self.detail(started['taskId'])  # the reopened host loads its coding records here: in-flight help is disconnected
            self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['disconnected'])
            for _ in range(10):
                self.host.tick()
            detail = self.detail(started['taskId'])
            review = detail['collaboration']['review']
            self.assertEqual(review['phase'], 'interrupted', review)
            self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['disconnected'])
            self.assertEqual(self.starts, [('codex', 'owner'), ('codex', 'cx')], 'nothing started after the restart')
            self.assertEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual((self.project / 'greeting.py').read_text(), GREETING + '# v1\n' * 3, 'three owner runs wrote; no reviewer and no replay ever did')


class FullAccessReviewTests(unittest.TestCase):
    """17.1 (#42): a full-access run is reviewed. Until then attach() dropped the review for it, so with review on a
    full-access thread was quietly never reviewed. Claude owner with full access in a scratch git repository, Codex reviewer."""
    owner_provider = 'claude'

    def setUp(self):
        # Beta.18: these tests are about a thread in the project folder, as in beta.17. Own branches are
        # test_thread_folders'.
        self.enterContext(patch.object(thread_folders, 'pick', return_value=('project', 'explore')))
        IndependentReviewTests.setUp(self)
        import mcp_connectors
        for name, stub in (('names', lambda *a, **k: []), ('cached', lambda home: []), ('warm', lambda home: None)):
            self.enterContext(patch.object(mcp_connectors, name, stub))
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'f@example.test'),
                     ('add', '-A'), ('commit', '-qm', 'start')):
            subprocess.run(['/usr/bin/git', *args], cwd=self.project, check=True, capture_output=True)
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': [], 'testFile': None, 'projectAccess': True,
                                                       'checkProfile': 'manual-review'})
        self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': True, 'warning': 'beta.15'})
        self.policy(review=REVIEW_ON)

    def script(self, peer, text):
        """The reviewer finds one thing, then passes the correction. The owner edits greeting.py itself, as Claude Code does
        with full access, and Dervo records the change from git when the run ends."""
        if self.role_of(text) == 'reviewer':
            verdict = ({'verdict': 'findings', 'findings': 'greeting.py line 1: add a docstring.'} if 'Phase: initial' in text
                       else {'verdict': 'pass', 'findings': ''})
            return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('tool', 'dervo_review_verdict', verdict), ('finish', 'Reviewed.')]
        target = self.project / 'greeting.py'
        correcting = 'Independent review findings' in text
        target.write_text('"""Greeting."""\n' + target.read_text() if correcting else target.read_text() + '# v1\n')
        return [('finish', 'Added the docstring.' if correcting else 'Added a comment to greeting.py.')]

    def run_reviewed(self):
        with self.review_patch(self.script):
            started = self.request('Add a comment to greeting.py.')
            return self.settle_review(started['taskId'])

    def test_a_full_access_run_is_reviewed_from_the_changes_recorded_from_git(self):
        detail = self.run_reviewed()
        task, review = detail['task'], detail['collaboration']['review']
        self.assertTrue(task['full_access'])
        self.assertIsNotNone(review, 'review was dropped for a full-access run')
        self.assertEqual(review['phase'], 'passed', review)
        packets = detail['collaboration']['packets']
        self.assertEqual([(p['phase'], [f['path'] for f in p['files']]) for p in packets],
                         [('initial', ['greeting.py']), ('final', ['greeting.py'])])
        first = base64.b64decode(self.host.coding.collaboration.snapshots(packets[0]['id'])['greeting.py']['bytes_b64']).decode()
        final = base64.b64decode(self.host.coding.collaboration.snapshots(packets[1]['id'])['greeting.py']['bytes_b64']).decode()
        self.assertEqual(first, GREETING + '# v1\n', 'the reviewer reads the run as git recorded it')
        self.assertEqual(final, '"""Greeting."""\n' + GREETING + '# v1\n', 'and the correction as the second run left it')
        self.assertEqual(packets[0]['review_evidence']['request']['text'], 'Add a comment to greeting.py.')
        self.assertEqual(packets[0]['review_evidence']['checks'][0]['kind'], 'manual-review')
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertIsNone(task.get('review'), 'a passed review is not your approval')
        self.assertIsNone(task.get('commit'))

    def test_automatic_commit_waits_for_the_review_to_pass(self):
        self.host.dispatch('coding_auto_commit_set', {**self.scope, 'enabled': True})
        detail = self.run_reviewed()
        task, review = detail['task'], detail['collaboration']['review']
        self.assertEqual(review['phase'], 'passed', review)
        self.assertEqual(len(detail['collaboration']['packets']), 2, 'committing first would have ended the review as skipped')
        self.assertEqual((task['state'], task['review'], task['commit']['how']), ('idle', 'approved', 'automatic'))
        shown = subprocess.run(['/usr/bin/git', 'show', 'HEAD:greeting.py'], cwd=self.project, check=True, capture_output=True, text=True).stdout
        self.assertEqual(shown, '"""Greeting."""\n' + GREETING + '# v1\n', 'the commit holds the reviewed result')


for _name in ('close_host', 'reopen', 'agent', 'configure', 'detail', 'packets', 'peer_patch', 'role_of', 'settle'):
    setattr(IndependentReviewTests, _name, tc.CollaborationTests.__dict__[_name])
    setattr(FullAccessReviewTests, _name, tc.CollaborationTests.__dict__[_name])
for _name in ('policy', 'request', 'review_patch', 'settle_review'):
    setattr(FullAccessReviewTests, _name, IndependentReviewTests.__dict__[_name])


if __name__ == '__main__':
    unittest.main()
