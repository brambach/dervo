"""Owner effort: one optional, strictly validated effort for the project's own coding model, carried exactly to the
actual provider adapter on the initial request and on the one owner correction, kept separate from the reviewer's effort.

Real Host on a disposable store and project; providers are the synthetic Codex app-server peer (its ``model/list``
catalog decides which efforts exist) and the synthetic Claude stream-json peer (the exact ``--effort`` argv). Nothing here
launches a real provider or reads credentials. The Independent review safety suite is untouched.
"""
import unittest

import test_independent_review as ir
from host import Host
from dervo_connections import Rejected

# The review suite's fixtures without re-collecting its tests.
Base = type('Base', (ir.IndependentReviewTests,), {name: None for name in dir(ir.IndependentReviewTests) if name.startswith('test_')})
REVIEW_ON = ir.REVIEW_ON


class OwnerEffortCodexTests(Base):
    owner_provider = 'codex'

    def set_effort(self, effort, scope=None):
        return self.host.dispatch('coding_effort_set', {**(scope or self.scope), 'effort': effort})

    def view(self, scope=None):
        return self.host.dispatch('coding_permission', scope or self.scope)

    def reopen(self):
        self.host.close()
        self.host = Host(self.base / 'store')

    def owner_starts(self):
        return [m['params'] for p in self.peers for m in getattr(p, 'sent', []) if m.get('method') == 'turn/start']

    def test_legacy_unset_sends_no_effort(self):
        self.assertIsNone(self.view()['owner_effort'], 'unset by default: the provider default applies, nothing is invented')
        with self.review_patch(self.owner_only):
            result = self.request('Add a version comment.')
            detail = self.settle(result['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        starts = self.owner_starts()
        self.assertEqual(len(starts), 1)
        self.assertNotIn('effort', starts[0], 'legacy shape: no effort key when none was configured')

    def test_round_trip_reopen_and_exact_turn_payload_on_request_and_correction(self):
        saved = self.set_effort('high')
        self.assertEqual(saved['owner_effort'], 'high')
        self.reopen()
        self.assertEqual(self.view()['owner_effort'], 'high', 'persisted across a host restart')
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            result = self.request('Add a version comment.')
            detail = self.settle_review(result['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        starts = self.owner_starts()
        owner = [s for s in starts if 'Role:' not in s['input'][0]['text']]
        reviewer = [s for s in starts if 'Role:' in s['input'][0]['text']]
        self.assertEqual(len(owner), 2, 'the initial request and the one owner correction')
        self.assertEqual([s['effort'] for s in owner], ['high', 'high'], 'the exact configured owner effort, both times')
        self.assertEqual(len(reviewer), 2)
        self.assertEqual({s['effort'] for s in reviewer}, {'low'}, "the reviewer keeps its own effort, never the owner's")

    def test_strict_rejection(self):
        # Whitespace-surrounded tokens arrive from the UI untrimmed and are rejected here, never silently normalised.
        for effort in (5, '', '   ', 'hi gh', 'x' * 65, ' low', 'low\n', ' high ', '\thigh', True):
            with self.subTest(effort=effort):
                with self.assertRaises(Rejected):
                    self.set_effort(effort)
        self.assertIsNone(self.view()['owner_effort'])
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_effort_set', {**self.scope})
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_effort_set', {**self.scope, 'effort': 'low', 'extra': 1})

    def test_unset_clears(self):
        self.set_effort('low')
        self.assertEqual(self.view()['owner_effort'], 'low')
        self.assertIsNone(self.set_effort(None)['owner_effort'])
        self.assertIsNone(self.view()['owner_effort'])

    def test_detail_claims_only_what_each_provider_establishes(self):
        detail = self.set_effort('high' if self.owner_provider == 'codex' else 'xhigh')['owner_effort_detail']
        if self.owner_provider == 'codex':
            self.assertIn('catalog', detail)
            self.assertIn('refused before any content', detail)
        else:
            self.assertIn('exact --effort token', detail)
            self.assertIn('not checked before content', detail)
            self.assertNotIn('refused before any content', detail, 'no preflight claim exists for Claude Code')

    def test_unavailable_catalog_effort_is_refused_before_any_content(self):
        self.set_effort('xhigh')  # a well-formed token the catalog does not advertise for this model
        with self.review_patch(self.owner_only, efforts=('low', 'medium', 'high')):
            result = self.request('Add a version comment.')
            detail = self.settle(result['taskId'])
        self.assertEqual(detail['task']['state'], 'blocked')
        self.assertIn('catalog', detail['task']['error'])
        self.assertIn('xhigh', detail['task']['error'])
        self.assertEqual(self.owner_starts(), [], 'no turn/start: nothing was sent, nothing substituted')
        self.assertFalse((self.project / 'greeting.py').read_bytes().endswith(b'# v1\n'))

    def test_model_change_does_not_retain_the_selection(self):
        self.set_effort('high')
        store = self.host.store
        store.bind_project('p', 'owner', 'other-model')
        store.add_thread('c2', 'p', 'c2', 'shared', None)
        scope = {'projectId': 'p', 'channelId': 'c2'}
        view = self.view(scope)
        self.assertIsNone(view['owner_effort'], 'a selection made for one provider and model never follows another model silently')
        self.assertIn('other-model', view['owner_effort_detail'])
        self.assertEqual(self.view()['owner_effort'], 'high', 'the conversation still bound to the original model keeps it')

    def test_no_execution_from_configuration(self):
        with self.review_patch(self.owner_only):
            self.set_effort('low')
            self.view()
        self.assertEqual(self.peers, [], 'configuration launches nothing')


class ThinkingLevelTests(Base):
    """16.4 item 3 (Bryce, 28 Sep): thread, project, personal, provider. The first one set wins."""
    owner_provider = 'codex'

    def setUp(self):
        super().setUp()
        self.host.store.add_thread('side', 'p', 'side', 'shared', None)
        self.side = dict(projectId='p', channelId='side')
        self.settings = dict(projectId='p', channelId=None)

    def set(self, scope, effort, level=None):
        return self.host.dispatch('coding_effort_set', {**scope, 'effort': effort, **({'level': level} if level else {})})

    def efforts(self, scope):
        return self.host.dispatch('coding_permission', scope)['efforts']

    def test_two_threads_in_one_project_keep_different_levels(self):
        self.set(self.scope, 'high', 'thread')
        self.set(self.side, 'low', 'thread')
        self.assertEqual((self.efforts(self.scope)['effective'], self.efforts(self.scope)['source']), ('high', 'thread'))
        self.assertEqual((self.efforts(self.side)['effective'], self.efforts(self.side)['source']), ('low', 'thread'))
        self.assertIsNone(self.efforts(self.settings)['project'], 'a thread level never writes the project row')
        self.assertEqual(self.host.dispatch('coding_permission', self.side)['owner_effort'], 'low')

    def test_a_thread_with_none_falls_back_to_project_then_personal_then_provider(self):
        self.assertEqual(self.efforts(self.scope), {'thread': None, 'project': None, 'personal': None, 'effective': None, 'source': None})
        self.set(self.settings, 'medium', 'personal')
        self.assertEqual((self.efforts(self.scope)['effective'], self.efforts(self.scope)['source']), ('medium', 'personal'))
        self.set(self.settings, 'high')  # no level: the project's, as before 16.4
        self.assertEqual((self.efforts(self.scope)['effective'], self.efforts(self.scope)['source']), ('high', 'project'))
        self.set(self.scope, 'low', 'thread')
        self.assertEqual((self.efforts(self.scope)['effective'], self.efforts(self.scope)['source']), ('low', 'thread'))
        self.set(self.scope, None, 'thread')  # Back to default
        self.assertEqual(self.efforts(self.scope)['source'], 'project')
        self.set(self.settings, None, 'project')  # Use my default
        self.assertEqual((self.efforts(self.scope)['effective'], self.efforts(self.scope)['source']), ('medium', 'personal'))
        self.set(self.settings, None, 'personal')
        self.assertEqual(self.efforts(self.scope)['source'], None)

    def test_use_for_new_threads_writes_the_default_it_falls_back_to_and_clears_the_thread(self):
        self.set(self.scope, 'high', 'thread')
        self.set(self.scope, 'high', 'promote')
        after = self.efforts(self.scope)
        self.assertEqual((after['thread'], after['project'], after['personal'], after['source']), (None, None, 'high', 'personal'),
                         'no project level: it becomes your default')
        self.assertEqual(self.efforts(self.side)['effective'], 'high', 'and other threads use it')
        self.set(self.settings, 'medium')
        self.set(self.scope, 'low', 'thread')
        self.set(self.scope, 'low', 'promote')
        after = self.efforts(self.scope)
        self.assertEqual((after['thread'], after['project'], after['personal'], after['source']), (None, 'low', 'high', 'project'),
                         'the project has its own: the project’s changes, yours stays')

    def test_use_for_new_threads_refuses_a_stale_picker_and_project_settings(self):
        self.set(self.scope, 'high', 'thread')
        before = self.efforts(self.scope)
        for scope, effort in ((self.scope, 'low'), (self.scope, None), (self.side, 'high'), (self.settings, 'high')):
            with self.subTest(scope=scope['channelId'], effort=effort):
                with self.assertRaises(Rejected):
                    self.set(scope, effort, 'promote')
        self.assertEqual(self.efforts(self.scope), before)
        with self.assertRaises(Rejected):
            self.set(self.settings, 'high', 'thread')
        with self.assertRaises(Rejected):
            self.set(self.scope, 'high', 'everyone')

    def test_personal_and_work_accounts_keep_separate_defaults(self):
        conversation = self.host.coding_tasks().conversation
        store = self.host.store
        self.set(self.settings, 'high', 'personal')  # the project's account, owner
        owner = self.efforts(self.scope)
        self.assertEqual((owner['personal'], owner['source']), ('high', 'personal'))
        from host import ACTOR
        for thread, profile in (('second-cx', 'cx'), ('work-channel', 'work')):
            with self.subTest(thread=thread):
                binding = store.binding(thread, 'p', actor=ACTOR)
                self.assertEqual((binding['profile_id'], binding['model']), (profile, ir.MODEL))
                self.assertIsNone(conversation.efforts(binding)['personal'], 'another account, same model: not your default there')
        work = store.binding('work-channel', 'p', actor=ACTOR)
        with store.db:
            conversation._write_effort('personal', work, 'low')
        self.assertEqual(conversation.efforts(work)['personal'], 'low')
        self.assertEqual(self.efforts(self.scope)['personal'], 'high', 'the work default never reaches the personal account')

    def test_a_store_with_only_project_rows_keeps_every_level(self):
        self.set(self.settings, 'high')
        self.reopen()
        with self.host.store.db:
            self.host.store.db.execute('DROP TABLE coding_thread_efforts')  # as a 16.3 store has it
            self.host.store.db.execute('DROP TABLE coding_personal_efforts')
        self.reopen()
        for scope in (self.scope, self.side, self.settings):
            with self.subTest(scope=scope['channelId']):
                view = self.host.dispatch('coding_permission', scope)
                self.assertEqual((view['owner_effort'], view['efforts']['source']), ('high', 'project'))

    def test_a_deleted_thread_takes_its_level_with_it(self):
        self.set(self.side, 'low', 'thread')
        self.host.dispatch('conversation_delete', dict(projectId='p', channelId='side', confirmation='DELETE side'))
        self.assertEqual(self.host.store.db.execute("SELECT COUNT(*) FROM coding_thread_efforts WHERE thread_id='side'").fetchone()[0], 0)

    def test_the_run_sends_the_thread_level(self):
        self.set(self.settings, 'high')
        self.set(self.scope, 'low', 'thread')
        with self.review_patch(self.owner_only):
            result = self.request('Add a version comment.')
            self.settle(result['taskId'])
        starts = [m['params'] for p in self.peers for m in getattr(p, 'sent', []) if m.get('method') == 'turn/start']
        self.assertEqual([s['effort'] for s in starts], ['low'])


class OwnerEffortClaudeTests(OwnerEffortCodexTests):
    owner_provider = 'claude'

    def owner_argvs(self):
        return [p.argv for p in self.peers if hasattr(p, 'argv') and 'Role:' not in getattr(p, 'text', 'Role:')]

    def test_legacy_unset_sends_no_effort(self):
        self.assertIsNone(self.view()['owner_effort'])
        with self.review_patch(self.owner_only):
            result = self.request('Add a version comment.')
            detail = self.settle(result['taskId'])
        self.assertEqual(detail['task']['state'], 'ready_for_review')
        argvs = self.owner_argvs()
        self.assertEqual(len(argvs), 1)
        self.assertNotIn('--effort', argvs[0])

    def test_round_trip_reopen_and_exact_turn_payload_on_request_and_correction(self):
        self.set_effort('xhigh')
        self.reopen()
        self.assertEqual(self.view()['owner_effort'], 'xhigh')
        self.policy(review=REVIEW_ON)
        with self.review_patch(self.flow('pass')):
            result = self.request('Add a version comment.')
            detail = self.settle_review(result['taskId'])
        self.assertEqual(detail['collaboration']['review']['phase'], 'passed')
        argvs = self.owner_argvs()
        self.assertEqual(len(argvs), 2, 'the initial request and the one owner correction')
        for argv in argvs:
            self.assertEqual(argv[argv.index('--effort') + 1], 'xhigh')
        reviewer = [s for s in self.owner_starts() if 'Role:' in s['input'][0]['text']]
        self.assertEqual({s['effort'] for s in reviewer}, {'low'})

    def test_strict_rejection(self):
        for effort in (5, '', '   ', 'ultra', 'LOW', 'low ', ' xhigh ', 'minimal', 'xhigh\n'):
            with self.subTest(effort=effort):
                with self.assertRaises(Rejected):
                    self.set_effort(effort)
        self.assertIsNone(self.view()['owner_effort'])

    def test_unavailable_catalog_effort_is_refused_before_any_content(self):
        # Claude has no catalog: an undocumented --effort level is refused at configuration, so nothing can reach argv.
        with self.assertRaises(Rejected):
            self.set_effort('ultra')
        self.assertEqual(self.peers, [])


if __name__ == '__main__':
    unittest.main()
