"""One conversation's own model. Disposable stores, never a provider or a live workspace."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from host import Host, ACTOR
from dervo_connections import Rejected


class ConversationModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.source.mkdir()
        self.host = Host(self.root / 'data'); self.addCleanup(self.host.close)
        self.profile = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'fixture@example.test', 'category': 'personal'})['id']
        for pid in ('one', 'two'):
            self.host.store.add_project(pid, self.source, self.profile, 'old-model')
        self.channel = self.conversation('one')
        self.other = self.conversation('one')
        self.private = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Mine', 'private': True})['id']

    def conversation(self, pid):
        return self.host.dispatch('create_conversation', {'projectId': pid, 'name': 'Fixture', 'private': False})['id']

    def model_of(self, channel):
        return self.host.store.db.execute('SELECT model FROM threads WHERE id=?', (channel,)).fetchone()[0]

    def change(self, **changes):
        return self.host.dispatch('conversation_model_set', {'projectId': 'one', 'channelId': self.channel, 'expectedModel': 'old-model', 'model': 'claude-sonnet-5', **changes})

    def test_only_that_conversation_changes_and_its_binding_reports_it(self):
        db = self.host.store.db
        before = {row['name']: list(db.execute('SELECT * FROM "' + row['name'] + '"')) for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='threads' AND name NOT IN ('thread_versions', 'thread_version_seq')")}  # the state spine's change log moves by design
        versions = lambda: dict(db.execute('SELECT thread_id, version FROM thread_versions WHERE thread_id!=?', (self.channel,)).fetchall())
        others = versions()
        result = self.change()
        self.assertEqual(result, {'id': self.channel, 'project_id': 'one', 'model': 'claude-sonnet-5'})
        for table, rows in before.items():
            self.assertEqual(list(db.execute('SELECT * FROM "' + table + '"')), rows, table)
        self.assertEqual(versions(), others, 'only that conversation moves on Home')
        self.assertEqual(self.model_of(self.channel), 'claude-sonnet-5')
        self.assertEqual((self.model_of(self.other), self.model_of(self.private)), ('old-model', 'old-model'))
        self.assertEqual(db.execute("SELECT model FROM projects WHERE id='one'").fetchone()[0], 'old-model', 'the project default is untouched')
        binding = self.host.dispatch('binding', {'projectId': 'one', 'channelId': self.channel})
        self.assertEqual((binding['model'], binding['provider'], binding['profile_id']), ('claude-sonnet-5', 'claude', self.profile))
        listed = {c['id']: c['model'] for c in self.host.dispatch('conversations', {'projectId': 'one'})}
        self.assertEqual((listed[self.channel], listed[self.other]), ('claude-sonnet-5', 'old-model'))
        # A private conversation of the owner's is changeable too; the provider stays fixed by construction.
        self.change(channelId=self.private, model='claude-opus-5')
        self.assertEqual(self.model_of(self.private), 'claude-opus-5')

    def test_stale_invalid_or_foreign_requests_leave_the_model_unchanged(self):
        db = self.host.store.db
        db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.private,)); db.commit()
        for change in ({'expectedModel': 'stale'}, {'projectId': 'missing'}, {'projectId': 'two'}, {'channelId': 'missing'}, {'channelId': self.private},
                       {'model': ''}, {'model': ' bad '}, {'model': 'bad\nmodel'}, {'model': None}, {'model': 'a' * 257}, {'channelId': None}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(Rejected):
                self.change(**change)
        self.assertEqual(self.model_of(self.channel), 'old-model')

    def test_archived_project_unowned_profile_or_agent_conversation_is_refused(self):
        self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'Restore this project'):
            self.change()
        self.host.dispatch('project_restore', {'projectId': 'one'})
        db = self.host.store.db
        with db:
            db.execute("UPDATE profiles SET category='work' WHERE id=?", (self.profile,))
        with self.assertRaises(Rejected):
            self.change()
        with db:
            db.execute("UPDATE profiles SET category='personal' WHERE id=?", (self.profile,))
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS collab_agents (name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO collab_agents VALUES (?,?)', ('reviewer', json.dumps({'name': 'reviewer', 'channel_id': self.other, 'project_id': 'one'})))
        with self.assertRaisesRegex(Rejected, "agent's conversation"):
            self.change(channelId=self.other)
        self.assertEqual((self.model_of(self.channel), self.model_of(self.other)), ('old-model', 'old-model'))

    def test_active_work_in_the_conversation_blocks_the_change_without_touching_it(self):
        mid = self.host.store.send(self.channel, 'one', 'pending', 'keep', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, 'pending message'):
            self.change()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE turns SET state='completed' WHERE id=?", (mid,))
        self.host.turns['live'] = SimpleNamespace(binding={'id': self.channel}, closed=False)
        with self.assertRaisesRegex(Rejected, 'active request'):
            self.change()
        self.host.turns.clear()
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(
                dict(id='t1', state='working', context=dict(thread_id=self.channel, project_id='one', actor=ACTOR), runs=[], tokens=[]))))
        with self.assertRaisesRegex(Rejected, 'coding task'):
            self.change()
        self.assertEqual(self.model_of(self.channel), 'old-model')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE coding_tasks SET document=? WHERE id='t1'", (json.dumps(
                dict(id='t1', state='blocked', context=dict(thread_id=self.channel, project_id='one', actor=ACTOR), runs=[], tokens=[])),))
        self.change()
        self.assertEqual(self.model_of(self.channel), 'claude-sonnet-5', 'a settled task does not hold the conversation')


if __name__ == '__main__':
    unittest.main()
