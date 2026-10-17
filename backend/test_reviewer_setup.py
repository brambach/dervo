"""Automatic setup on disposable stores. No real provider or personal credentials."""
import json
import sqlite3
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from test_collab_channel import AgentConversationTests
from dervo_connections import Rejected


class ReviewerSetupTests(unittest.TestCase):
    setUp = AgentConversationTests.setUp

    def catalog(self):
        job = Future()
        job.set_result({'state': 'catalog', 'models': [{'model': 'owner-model',
                       'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, {'reasoningEffort': 'high'}]}]})
        self.host.catalogs['owner'] = job

    def setup_review(self, **changes):
        return self.host.dispatch('collab_reviewer_setup', dict(projectId='p', channelId='main',
                                  profileId='owner', model='owner-model', effort='low', **changes))

    def snapshot(self):
        return {table: [tuple(r) for r in self.host.store.db.execute('SELECT * FROM ' + table)]
                for table in ('threads', 'local_channels', 'collab_agents', 'collab_policies')}

    def init(self):
        self.host.dispatch('collab_agents', {})
        self.catalog()

    def test_atomic_setup_and_retry_preserve_binding_routing_and_grants(self):
        self.init()
        before = dict(self.host.store.thread('main', 'p'))
        result = self.setup_review()
        again = self.setup_review()
        self.assertEqual(result, again)
        self.assertEqual(result['mode'], 'off')
        self.assertEqual(result['agents'], [])
        agent, = self.host.dispatch('collab_agents', {})
        self.assertEqual(agent['tools'], ['dervo_read_file'])
        self.assertEqual(agent['profile_id'], 'owner')
        self.assertEqual(agent['model'], 'owner-model')
        self.assertNotEqual(agent['channel_id'], 'main')
        self.assertEqual(before, dict(self.host.store.thread('main', 'p')))
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM threads').fetchone()[0], 2)
        binding = self.host.store.binding('main', 'p', actor='local-user')
        plan = self.host.coding.collaboration.review_plan(binding)
        self.assertEqual(plan['reviewer'], agent['name'])
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM coding_tasks').fetchone()[0], 0)

    def test_unverified_catalog_and_invalid_inputs_create_nothing(self):
        self.init()
        base = dict(projectId='p', channelId='main', profileId='owner', model='owner-model', effort='low')
        before = self.snapshot()
        for changed in ({'profileId': 'work'}, {'model': 'another-model'}, {'effort': 'invented'}, {'effort': None},
                        {'effort': []}, {'extra': True}, {'projectId': 'missing'}):
            with self.subTest(changed=changed), self.assertRaises(Rejected):
                self.host.dispatch('collab_reviewer_setup', {**base, **changed})
            self.assertEqual(before, self.snapshot())
        self.host.catalogs.clear()
        with self.assertRaises(Rejected):
            self.setup_review()
        self.assertEqual(before, self.snapshot())

    def test_private_and_work_contexts_are_refused(self):
        self.init()
        db = self.host.store.db
        for profile, visibility in [('owner', 'private'), ('work', 'shared')]:
            with db:
                db.execute('UPDATE threads SET profile_id=?, visibility=?, owner=? WHERE id=?',
                           (profile, visibility, 'local-user' if visibility == 'private' else None, 'main'))
            before = self.snapshot()
            with self.assertRaises(Rejected):
                self.host.dispatch('collab_reviewer_setup', dict(projectId='p', channelId='main', profileId=profile,
                                                               model='owner-model', effort='low'))
            self.assertEqual(before, self.snapshot())

    def test_policy_write_failure_rolls_back_agent_and_conversation(self):
        self.init()
        before = self.snapshot()
        self.host.store.db.execute("CREATE TRIGGER fail_policy BEFORE INSERT ON collab_policies BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.setup_review()
        self.assertEqual(before, self.snapshot())

    def test_changed_reviewer_is_not_silently_replaced(self):
        self.init()
        self.setup_review()
        agent, = self.host.coding.collaboration.agents()
        agent['role'] = 'worker'
        with self.host.store.db:
            self.host.store.db.execute('UPDATE collab_agents SET document=? WHERE name=?', (json.dumps(agent), agent['name']))
        before = self.snapshot()
        with self.assertRaises(Rejected):
            self.setup_review()
        self.assertEqual(before, self.snapshot())

    def test_existing_custom_review_policy_is_preserved(self):
        self.init()
        made = self.host.dispatch('create_agent_conversation', dict(projectId='p', profileId='owner', model='owner-model', name='Custom'))
        self.host.dispatch('collab_agent_save', dict(projectId='p', profileId='owner', channelId=made['id'], name='custom', role='reviewer', instructions=''))
        self.host.dispatch('collab_policy_set', dict(projectId='p', channelId='main', mode='mentions', agents=['custom'], channels=['main'],
                          maxParallel=2, maxDepth=1, maxCalls=2, review={'enabled': True, 'reviewer': 'custom', 'effort': 'high'}))
        before = self.snapshot()
        with self.assertRaises(Rejected):
            self.setup_review()
        self.assertEqual(before, self.snapshot())

    def test_deleted_allowed_thread_does_not_block_turning_review_off(self):
        # Bug #1: a deleted thread stayed in the saved list, and Settings sends that list back, so review couldn't be turned off.
        self.init()
        made = self.host.dispatch('create_agent_conversation', dict(projectId='p', profileId='owner', model='owner-model', name='Gone'))
        self.host.dispatch('collab_agent_save', dict(projectId='p', profileId='owner', channelId=made['id'], name='custom', role='reviewer', instructions=''))
        self.host.store.add_thread('gone', 'p', 'gone', 'shared', None)
        on = {'enabled': True, 'reviewer': 'custom', 'effort': 'high'}
        self.host.dispatch('collab_policy_set', dict(projectId='p', channelId=None, mode='automatic', agents=[], channels=['gone'],
                          maxParallel=2, maxDepth=1, maxCalls=2, review=on))
        self.host.dispatch('conversation_delete', dict(projectId='p', channelId='gone', confirmation='DELETE gone'))
        saved = self.host.dispatch('collab_policy', dict(projectId='p', channelId=None))
        self.assertEqual(saved['channels'], ['gone'])
        off = self.host.dispatch('collab_policy_set', dict(projectId='p', channelId=None, mode=saved['mode'], agents=saved['agents'],
                                 channels=saved['channels'], maxParallel=2, maxDepth=1, maxCalls=2,
                                 review={'enabled': False, 'reviewer': None, 'effort': None}))
        self.assertFalse(off['review']['enabled'])
        self.assertEqual(off['channels'], [])
        self.assertEqual(off['mode'], 'off')
        # The next save sends the emptied list back and still works.
        again = self.host.dispatch('collab_policy_set', dict(projectId='p', channelId=None, mode=off['mode'], agents=off['agents'],
                                   channels=off['channels'], maxParallel=2, maxDepth=1, maxCalls=2, review=on))
        self.assertTrue(again['review']['enabled'])
        # An id the saved list never had is still refused.
        with self.assertRaisesRegex(Rejected, 'does not belong'):
            self.host.dispatch('collab_policy_set', dict(projectId='p', channelId=None, mode='automatic', agents=[], channels=['never'],
                              maxParallel=2, maxDepth=1, maxCalls=2))

    def test_claude_setup_uses_exact_account_and_model_without_catalog_or_send(self):
        self.init()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='cl', model='claude-fable-5-1' WHERE id='main'")
        result = self.host.dispatch('collab_reviewer_setup', dict(projectId='p', channelId='main', profileId='cl', model='claude-fable-5-1', effort='low'))
        self.assertEqual(result['review']['provider'], 'claude')
        self.assertEqual(result['review']['model'], 'claude-fable-5-1')


if __name__ == '__main__':
    unittest.main()
