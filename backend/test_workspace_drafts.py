import copy
import tempfile
import unittest
from pathlib import Path
from host import Host, ACTOR
from dervo_connections import Rejected
from workspace_drafts import dispatch, MAX_BYTES


class WorkspaceDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'workspace'
        self.host = Host(self.root)
        profile = self.host.dispatch('add_profile', {'provider':'codex', 'identity':'fixture@example.test', 'category':'personal'})['id']
        self.project = self.host.add_picked_project(self.temp.name, profile, 'fixture')['id']
        self.channel = self.host.dispatch('create_conversation', {'projectId':self.project, 'name':'Draft trial', 'private':True})['id']
        self.draft = dict(content='Keep this draft 🌿', selectionStart=5, selectionEnd=5,
                          channelId=self.channel, createdAt='2026-09-15', updatedAt='2026-09-15',
                          pendingImeta=[], mentionRefs=[], spoileredAttachmentUrls=[], status='active')

    def tearDown(self):
        self.host.close()
        self.temp.cleanup()

    def args(self, **changes):
        return dict(projectId=self.project, channelId=self.channel, revision=0,
                    mutationId='first', draft=copy.deepcopy(self.draft)) | changes

    def save(self, **changes):
        return self.host.dispatch('draft_save', self.args(**changes))

    def entry(self):
        return self.host.dispatch('drafts_list', {})['entries'][0]

    def test_restart_preserves_draft_without_creating_provider_work(self):
        self.assertEqual(self.save(), {'revision':1})
        self.host.close(); self.host = Host(self.root)
        self.assertEqual(self.entry()['draft'], self.draft)
        for table in ['messages','turns','events']:
            self.assertEqual(self.host.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(self.host.dispatch('history', {'projectId':self.project,'channelId':self.channel}), [])

    def test_lost_ack_retry_is_idempotent_and_payload_is_fixed(self):
        self.save(); self.assertEqual(self.save(), {'revision':1})
        with self.assertRaises(Rejected):self.save(draft=self.draft | {'content':'changed'})
        self.assertEqual(self.entry()['draft'], self.draft)

    def test_clear_tombstone_prevents_old_draft_replay(self):
        self.save()
        self.save(revision=1, mutationId='clear', draft=None)
        self.assertEqual(self.entry()['revision'], 2); self.assertIsNone(self.entry()['draft'])
        with self.assertRaises(Rejected):self.save(mutationId='stale')
        self.host.close();self.host = Host(self.root)
        self.assertIsNone(self.entry()['draft'])

    def test_private_owner_and_exact_scope_are_required(self):
        self.save()
        self.assertEqual(dispatch(self.host,'drafts_list',{},'different-user'), {'entries':[]})
        with self.assertRaises(Rejected):dispatch(self.host,'draft_save',self.args(),'different-user')
        with self.assertRaises(Rejected):self.save(projectId='different-project')
        with self.assertRaises(Rejected):self.save(draft=self.draft | {'channelId':'different-conversation'})

    def test_other_workspace_has_no_drafts(self):
        self.save()
        other=Host(Path(self.temp.name)/'other')
        try:self.assertEqual(other.dispatch('drafts_list',{}), {'entries':[]})
        finally:other.close()

    def test_size_shape_and_revision_rejections_preserve_saved_text(self):
        self.save()
        for changes in [{'draft':self.draft | {'content':'x'*MAX_BYTES}}, {'revision':True},
                        {'draft':self.draft | {'selectionEnd':float('nan')}},
                        {'draft':self.draft | {'status':'sent'}}, {'draft':{'content':'invalid'}}]:
            with self.assertRaises(Rejected):self.save(**({'revision':1,'mutationId':'next'} | changes))
        self.assertEqual(self.entry()['draft'],self.draft)

    def test_deleting_project_clears_draft_rows_without_touching_other_workspace(self):
        self.save()
        from project_lifecycle import delete_rows
        with self.host.store.db:delete_rows(self.host.store.db,self.project)
        self.assertEqual(self.host.dispatch('drafts_list',{}),{'entries':[]})
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM workspace_drafts').fetchone()[0],0)


if __name__ == '__main__':unittest.main()
