"""Model defaults use disposable stores, never a provider or a live workspace."""
import tempfile
import unittest
from pathlib import Path
from host import Host
from dervo_connections import Rejected

class ProjectModelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        self.host=Host(self.root/'data');self.addCleanup(self.host.close)
        self.profile=self.host.dispatch('add_profile',{'provider':'claude','identity':'fixture@example.test','category':'personal'})['id']
        for pid in ('one','two'):self.host.store.add_project(pid,self.source,self.profile,'old-model')
        self.channel=self.conversation('one')
        self.reviewer=self.conversation('one')
    def conversation(self,pid):
        return self.host.dispatch('create_conversation',{'projectId':pid,'name':'Fixture','private':False})['id']
    def change(self,**changes):
        return self.host.dispatch('project_model_set',{'projectId':'one','expectedModel':'old-model','model':'claude-sonnet-5',**changes})
    def test_only_project_default_changes_and_new_conversation_inherits_it(self):
        db=self.host.store.db
        before={row['name']:list(db.execute('SELECT * FROM "'+row['name']+'"')) for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='projects' AND name NOT IN ('thread_versions','thread_version_seq')")}  # the state spine's change log moves by design
        self.change()
        for table,rows in before.items():self.assertEqual(list(db.execute('SELECT * FROM "'+table+'"')),rows,table)
        self.assertEqual(db.execute("SELECT model FROM projects WHERE id='two'").fetchone()[0],'old-model')
        new=self.conversation('one')
        self.assertEqual(db.execute('SELECT model FROM threads WHERE id=?',(new,)).fetchone()[0],'claude-sonnet-5')
        for channel in (self.channel,self.reviewer):self.assertEqual(db.execute('SELECT model FROM threads WHERE id=?',(channel,)).fetchone()[0],'old-model')
    def test_stale_and_invalid_requests_leave_default_unchanged(self):
        for change in ({'expectedModel':'stale'},{'projectId':'missing'},{'model':''},{'model':' bad '},{'model':'bad\nmodel'},{'model':None},{'model':'a'*257},{'profileId':'other'}):
            with self.subTest(change=change),self.assertRaises(Rejected):self.change(**change)
        self.assertEqual(self.host.store.db.execute("SELECT model FROM projects WHERE id='one'").fetchone()[0],'old-model')
    def test_archived_work_or_unowned_profile_is_rejected(self):
        self.host.dispatch('project_archive',{'projectId':'one'})
        with self.assertRaises(Rejected):self.change()
        self.host.dispatch('project_restore',{'projectId':'one'})
        db=self.host.store.db
        with db:db.execute("UPDATE profiles SET category='work' WHERE id=?",(self.profile,))
        with self.assertRaises(Rejected):self.change()
        with db:db.execute("UPDATE profiles SET category='personal',home=? WHERE id=?",(str(self.root/'elsewhere'),self.profile))
        with self.assertRaises(Rejected):self.change()
    def test_pending_message_blocks_default_change_without_launching_it(self):
        mid=self.host.store.send(self.channel,'one','pending','keep',actor='local-user')
        with self.assertRaisesRegex(Rejected,'The default model didn’t change. Fixture still has work running. Wait for the reply in Fixture to finish, or cancel it.'):self.change()
        self.assertFalse(self.host.turns)
        self.host.dispatch('cancel',{'projectId':'one','channelId':self.channel,'messageId':mid})
        self.change()
if __name__=='__main__':unittest.main()
