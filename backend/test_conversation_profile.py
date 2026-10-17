"""One conversation's own account, chosen after the conversation exists. Disposable stores, never a provider or a live workspace.

Bryce's 17 September 2026 decision: the account and model are chosen per conversation. The provider may change, so the
model changes in the same write. Everything else about the conversation, the project and the other conversations stays.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from host import Host, ACTOR
from dervo_connections import Rejected


class ConversationProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.source.mkdir()
        self.host = Host(self.root / 'data'); self.addCleanup(self.host.close)
        self.claude = self.host.dispatch('add_profile', {'provider': 'claude', 'identity': 'fixture@example.test', 'category': 'personal'})['id']
        self.codex = self.host.dispatch('add_profile', {'provider': 'codex', 'identity': 'codex@example.test', 'category': 'personal'})['id']
        self.work = self.host.dispatch('add_profile', {'provider': 'codex', 'identity': 'work@example.test', 'category': 'work'})['id']
        for pid in ('one', 'two'):
            self.host.store.add_project(pid, self.source, self.claude, 'old-model')
        self.channel = self.conversation('one')
        self.other = self.conversation('one')
        self.private = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Mine', 'private': True})['id']

    def conversation(self, pid):
        return self.host.dispatch('create_conversation', {'projectId': pid, 'name': 'Fixture', 'private': False})['id']

    def thread(self, channel):
        return tuple(self.host.store.db.execute('SELECT profile_id, model, session_id FROM threads WHERE id=?', (channel,)).fetchone())

    def change(self, **changes):
        return self.host.dispatch('conversation_profile_set', {'projectId': 'one', 'channelId': self.channel, 'expectedProfileId': self.claude,
                                                               'profileId': self.codex, 'expectedModel': 'old-model', 'model': 'gpt-5-codex', **changes})

    def test_only_that_conversation_changes_account_and_model_together_and_drops_its_old_session(self):
        db = self.host.store.db
        with db:
            db.execute("UPDATE threads SET session_id='old-claude-session' WHERE id=?", (self.channel,))
        before = {row['name']: list(db.execute('SELECT * FROM "' + row['name'] + '"')) for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='threads' AND name NOT IN ('thread_versions', 'thread_version_seq')")}  # the state spine's change log moves by design
        result = self.change()
        self.assertEqual(result, {'id': self.channel, 'project_id': 'one', 'profile_id': self.codex, 'provider': 'codex', 'model': 'gpt-5-codex'})
        for table, rows in before.items():
            self.assertEqual(list(db.execute('SELECT * FROM "' + table + '"')), rows, table)
        self.assertEqual(self.thread(self.channel), (self.codex, 'gpt-5-codex', None), 'the resumed session belonged to the old account')
        self.assertEqual((self.thread(self.other), self.thread(self.private)), ((self.claude, 'old-model', None), (self.claude, 'old-model', None)))
        self.assertEqual(tuple(db.execute("SELECT profile_id, model FROM projects WHERE id='one'").fetchone()), (self.claude, 'old-model'), 'the project default is untouched')
        binding = self.host.dispatch('binding', {'projectId': 'one', 'channelId': self.channel})
        self.assertEqual((binding['profile_id'], binding['provider'], binding['identity'], binding['model']), (self.codex, 'codex', 'codex@example.test', 'gpt-5-codex'))
        listed = {c['id']: (c['profile_id'], c['model']) for c in self.host.dispatch('conversations', {'projectId': 'one'})}
        self.assertEqual((listed[self.channel], listed[self.other]), ((self.codex, 'gpt-5-codex'), (self.claude, 'old-model')))
        # Back again, and a private conversation of the owner's is changeable too.
        self.change(expectedProfileId=self.codex, profileId=self.claude, expectedModel='gpt-5-codex', model='claude-sonnet-5')
        self.change(channelId=self.private)
        self.assertEqual((self.thread(self.channel), self.thread(self.private)), ((self.claude, 'claude-sonnet-5', None), (self.codex, 'gpt-5-codex', None)))

    def test_stale_invalid_foreign_or_unowned_requests_leave_the_conversation_unchanged(self):
        db = self.host.store.db
        db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.private,)); db.commit()
        for change in ({'expectedProfileId': 'stale'}, {'expectedModel': 'stale'}, {'profileId': 'missing'}, {'profileId': self.work},
                       {'profileId': ''}, {'profileId': None}, {'expectedProfileId': None}, {'projectId': 'missing'}, {'projectId': 'two'},
                       {'channelId': 'missing'}, {'channelId': self.private}, {'channelId': None},
                       {'model': ''}, {'model': ' bad '}, {'model': 'bad\nmodel'}, {'model': None}, {'model': 'a' * 257}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(Rejected):
                self.change(**change)
        self.assertEqual(self.thread(self.channel), (self.claude, 'old-model', None))
        with self.assertRaises(Rejected):
            self.host.dispatch('conversation_profile_set', {'projectId': 'one', 'channelId': self.channel, 'expectedProfileId': self.claude, 'profileId': self.codex})
        self.assertEqual(self.thread(self.channel), (self.claude, 'old-model', None))

    def test_archived_project_unowned_current_profile_or_agent_conversation_is_refused(self):
        self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'Restore this project before changing its account'):
            self.change()
        self.host.dispatch('project_restore', {'projectId': 'one'})
        db = self.host.store.db
        with db:
            db.execute("UPDATE profiles SET category='work' WHERE id=?", (self.claude,))
        with self.assertRaises(Rejected):
            self.change()
        with db:
            db.execute("UPDATE profiles SET category='personal' WHERE id=?", (self.claude,))
            db.execute('CREATE TABLE IF NOT EXISTS collab_agents (name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO collab_agents VALUES (?,?)', ('reviewer', json.dumps({'name': 'reviewer', 'channel_id': self.other, 'project_id': 'one'})))
        with self.assertRaisesRegex(Rejected, "agent's conversation keeps the account"):
            self.change(channelId=self.other)
        self.assertEqual((self.thread(self.channel), self.thread(self.other)), ((self.claude, 'old-model', None), (self.claude, 'old-model', None)))

    def test_active_or_queued_work_in_the_conversation_blocks_the_change_without_touching_it(self):
        mid = self.host.store.send(self.channel, 'one', 'pending', 'keep', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, 'pending message before changing its account'):
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
        with self.assertRaisesRegex(Rejected, 'coding task before changing its account'):
            self.change()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE coding_tasks SET document=? WHERE id='t1'", (json.dumps(
                dict(id='t1', state='blocked', context=dict(thread_id=self.channel, project_id='one', actor=ACTOR), runs=[], tokens=[])),))
        binding = self.host.store.binding(self.channel, 'one', actor=ACTOR)
        waiting = self.host.coding.conversation.queue.add({'clientId': 'q-1', 'text': 'Later'}, dict(thread_id=self.channel, project_id='one', actor=ACTOR), binding)
        with self.assertRaisesRegex(Rejected, 'queued messages before changing its account'):
            self.change()
        self.assertEqual(self.thread(self.channel), (self.claude, 'old-model', None))
        self.host.dispatch('coding_queue_cancel', {'projectId': 'one', 'channelId': self.channel, 'queueId': waiting['id']})
        self.change()
        self.assertEqual(self.thread(self.channel), (self.codex, 'gpt-5-codex', None), 'settled work does not hold the conversation')


if __name__ == '__main__':
    unittest.main()
