"""Archive, restore and delete one conversation. Disposable stores only; no providers, credentials or live project data."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from host import Host, ACTOR
from dervo_connections import Rejected


class ConversationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.source.mkdir()
        (self.source / 'README.md').write_text('keep source')
        self.host = Host(self.root / 'data'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', self.root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', self.source, 'account', 'test-model')
        self.channel = self.conversation('Design notes')
        self.other = self.conversation('Keep me')
        self.private = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Mine', 'private': True})['id']

    def conversation(self, name):
        return self.host.dispatch('create_conversation', {'projectId': 'one', 'name': name, 'private': False})['id']

    def listed(self):
        return [c['id'] for c in self.host.dispatch('conversations', {'projectId': 'one'})]

    def archive(self, cid=None):
        return self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': cid or self.channel})

    def delete(self, cid=None, **extra):
        cid = cid or self.channel
        return self.host.dispatch('conversation_delete', {'projectId': 'one', 'channelId': cid, 'confirmation': 'DELETE ' + cid, **extra})

    def test_archive_hides_lists_restores_and_survives_restart(self):
        self.assertEqual(self.listed(), [self.channel, self.other, self.private])
        self.assertEqual(self.archive(), {'id': self.channel, 'visibility': 'shared'})
        self.assertEqual(self.listed(), [self.other, self.private])
        archived = self.host.dispatch('conversations_archived', {'projectId': 'one'})
        self.assertEqual([(c['id'], c['name']) for c in archived], [(self.channel, 'Design notes')])
        self.assertTrue(archived[0]['archived_at'] > 0)
        for op, args in (('binding', {}), ('history', {'limit': 5}), ('conversation_status', {})):
            with self.subTest(op=op), self.assertRaisesRegex(Rejected, 'archived'):
                self.host.dispatch(op, {'projectId': 'one', 'channelId': self.channel, **args})
        self.host.close(); self.host = Host(self.root / 'data')
        self.assertEqual(self.listed(), [self.other, self.private])
        self.host.dispatch('conversation_restore', {'projectId': 'one', 'channelId': self.channel})
        self.assertEqual(self.listed(), [self.channel, self.other, self.private])
        self.assertEqual(self.host.dispatch('conversations_archived', {'projectId': 'one'}), [])
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        # A personal conversation of the owner's archives too.
        self.archive(self.private)
        self.assertEqual(self.listed(), [self.channel, self.other])
        self.assertEqual([c['visibility'] for c in self.host.dispatch('conversations_archived', {'projectId': 'one'})], ['private'])

    def test_a_host_made_thread_keeps_its_origin_through_archive_and_an_older_store_is_marked_on_start(self):
        # 20 Sep: conversation_origins marks the threads the host makes with a project (project_lifecycle.schema). The
        # mark outlives archiving, goes with the thread on delete, and never depends on the name.
        from project_lifecycle import exploration_thread
        db = self.host.store.db
        overview = exploration_thread(self.host, 'one')
        origins = lambda rows: {c['id']: c['origin'] for c in rows}
        listed = origins(self.host.dispatch('conversations', {'projectId': 'one'}))
        self.assertEqual(listed[overview], 'overview')
        self.assertEqual([listed[cid] for cid in (self.channel, self.other, self.private)], [None, None, None], 'threads you started carry no origin')
        self.archive(overview)
        self.assertEqual(db.execute('SELECT count(*) FROM project_explorations').fetchone()[0], 0, 'the overview slot is released on archive, as before')
        archived = origins(self.host.dispatch('conversations_archived', {'projectId': 'one'}))
        self.assertEqual(archived, {overview: 'overview'}, 'the archived list carries the mark too')
        self.host.dispatch('conversation_restore', {'projectId': 'one', 'channelId': overview})
        self.assertEqual(origins(self.host.dispatch('conversations', {'projectId': 'one'}))[overview], 'overview', 'restoring keeps it')
        self.archive(overview)
        self.delete(overview)
        self.assertEqual(db.execute('SELECT count(*) FROM conversation_origins').fetchone()[0], 0, 'deleting the thread takes its mark')
        # A store from before the table: its current overview is only on record in project_explorations. The next start
        # marks it from there. A thread with no record at all stays unmarked; nothing guesses from 'Project overview'.
        replacement = exploration_thread(self.host, 'one')
        lookalike = self.conversation('Project overview')
        with db:
            db.execute('DROP TABLE conversation_origins')
        self.host.close(); self.host = Host(self.root / 'data')
        listed = origins(self.host.dispatch('conversations', {'projectId': 'one'}))
        self.assertEqual((listed[replacement], listed[lookalike]), ('overview', None))

    def test_a_conversation_with_a_queued_request_is_not_archived_or_deleted(self):
        # 19 Sep, map F7: a Home job waiting for a free slot (queueIfBusy) was not an "active" check, so the
        # conversation could be archived and the job would start in it later, off the sidebar and off Home.
        self.host.dispatch('tasks', {'projectId': 'one', 'channelId': self.channel})  # builds the coding host lazily
        queue = self.host.coding.conversation.queue
        binding = self.host.store.binding(self.channel, 'one', actor=ACTOR)
        ctx = dict(thread_id=self.channel, project_id='one', actor=ACTOR)
        item = queue.add({'clientId': 'home-1', 'text': 'A job from Home, waiting for a slot'}, ctx, binding)
        with self.assertRaisesRegex(Rejected, 'queued request first'):
            self.archive()
        with self.assertRaisesRegex(Rejected, 'queued request first'):
            self.delete()
        self.assertEqual(self.listed(), [self.channel, self.other, self.private], 'nothing changed')
        # Negative: another conversation archives fine, and once the item is cancelled so does this one.
        self.assertEqual(self.archive(self.other)['id'], self.other)
        self.host.dispatch('coding_queue_cancel', {'projectId': 'one', 'channelId': self.channel, 'queueId': item['id']})
        self.assertEqual(self.archive()['id'], self.channel)

    def test_foreign_agent_and_active_conversations_are_refused_without_change(self):
        db = self.host.store.db
        db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.private,)); db.commit()
        with self.assertRaises(Rejected):
            self.archive(self.private)
        with self.assertRaises(Rejected):
            self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': 'missing'})
        with self.assertRaises(Rejected):
            self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': None})
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS collab_agents (name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO collab_agents VALUES (?,?)', ('reviewer', json.dumps({'name': 'reviewer', 'channel_id': self.other, 'project_id': 'one'})))
        with self.assertRaisesRegex(Rejected, "agent's conversation"):
            self.archive(self.other)
        with self.assertRaisesRegex(Rejected, "agent's conversation"):
            self.delete(self.other)
        mid = self.host.store.send(self.channel, 'one', 'pending', 'keep', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, 'pending message'):
            self.archive()
        with self.assertRaisesRegex(Rejected, 'pending message'):
            self.delete()
        self.host.dispatch('cancel', {'projectId': 'one', 'channelId': self.channel, 'messageId': mid})
        self.host.turns['live'] = SimpleNamespace(binding={'id': self.channel, 'project_id': 'one'}, closed=False)
        with self.assertRaisesRegex(Rejected, 'request to finish'):
            self.archive()
        self.host.turns.clear()
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(
                {'id': 't1', 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR}, 'state': 'working', 'tokens': []})))
        with self.assertRaisesRegex(Rejected, 'task to finish'):
            self.archive()
        # Historical unknown outcomes don't require approval to archive or delete their records.
        with db:
            db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(
                {'id': 't1', 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR}, 'state': 'disconnected', 'tokens': []}), 't1'))
        self.archive()
        self.assertEqual(self.listed(), [self.other])
        self.host.dispatch('conversation_restore', {'projectId': 'one', 'channelId': self.channel})
        with db:
            db.execute("DELETE FROM coding_tasks")
        with self.assertRaisesRegex(Rejected, 'exact conversation'):
            self.delete(confirmation='DELETE ' + self.other)
        self.assertEqual(self.listed(), [self.channel, self.other], 'the reassigned private conversation is not the owner’s to see')
        self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 1)

    def test_old_lost_runs_do_not_trap_a_finished_conversation(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            for index, state in enumerate(('disconnected', 'needs_permission', 'answered')):
                task = {'id': str(index), 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR},
                        'state': state, 'tokens': [{'state': 'uncertain'}] if index == 0 else []}
                db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
        before = db.execute('SELECT document FROM coding_tasks ORDER BY id').fetchall()
        self.archive()
        self.assertEqual(db.execute('SELECT document FROM coding_tasks ORDER BY id').fetchall(), before)
        with self.assertRaisesRegex(Rejected, 'exact conversation'):
            self.delete(confirmation='DELETE wrong')
        self.delete()
        self.assertEqual(db.execute('SELECT count(*) FROM coding_tasks').fetchone()[0], 0)
        self.assertEqual((self.source / 'README.md').read_text(), 'keep source')

    def test_finished_task_cannot_hide_a_live_tool_or_check(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        for token in ({'state': 'running'}, {'state': 'delivering'}, {'state': 'uncertain', 'job_id': 'job', 'job_done': False}):
            with db:
                db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)', ('old', json.dumps({
                    'id': 'old', 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR},
                    'state': 'answered', 'tokens': [token]})))
            with self.subTest(token=token):
                with self.assertRaisesRegex(Rejected, 'task to finish'):
                    self.archive()
                with self.assertRaisesRegex(Rejected, 'task to finish'):
                    self.delete()

    def test_delete_removes_only_that_conversation_and_keeps_the_rest(self):
        db = self.host.store.db
        for cid in (self.channel, self.other):
            mid = self.host.store.send(cid, 'one', 'pending', 'saved message', actor=ACTOR)
            self.host.dispatch('cancel', {'projectId': 'one', 'channelId': cid, 'messageId': mid})
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS coding_tool_audit (task_id TEXT NOT NULL, token TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(task_id, token))')
            db.execute('CREATE TABLE IF NOT EXISTS coding_requests (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS project_knowledge (id TEXT PRIMARY KEY, project_id TEXT, document TEXT)')
            for cid in (self.channel, self.other):
                task = {'id': cid + '-task', 'context': {'thread_id': cid, 'project_id': 'one', 'actor': ACTOR}, 'state': 'answered', 'tokens': []}
                db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
                db.execute('INSERT INTO task_evidence VALUES (?,?)', (task['id'], json.dumps({'thread_id': cid, 'project_id': 'one'})))
                db.execute('INSERT INTO coding_tool_audit VALUES (?,?,?)', (task['id'], 'tok', '{}'))
                db.execute('INSERT INTO coding_requests VALUES (?,?,?)', (cid + '-req', cid, json.dumps({'task_id': task['id']})))
            db.execute('INSERT INTO project_knowledge VALUES (?,?,?)', ('one', 'one', '{}'))
            db.execute('INSERT INTO project_explorations VALUES (?,?)', ('one', self.channel))
        # Archived first, like a project, though archiving is not required.
        self.archive()
        self.assertEqual(db.execute('SELECT count(*) FROM project_explorations').fetchone()[0], 0, 'the overview conversation is released on archive')
        result = self.delete()
        self.assertEqual(result, {'id': self.channel, 'channelIds': [self.channel]})
        self.assertEqual((self.source / 'README.md').read_text(), 'keep source')
        self.assertEqual(self.listed(), [self.other, self.private])
        self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
        for table, remaining in (('threads', 2), ('local_channels', 2), ('messages', 1), ('turns', 1), ('coding_tasks', 1), ('task_evidence', 1),
                                 ('coding_tool_audit', 1), ('coding_requests', 1), ('conversation_archives', 0), ('project_knowledge', 1)):
            self.assertEqual(db.execute('SELECT count(*) FROM ' + table).fetchone()[0], remaining, table)
        self.assertEqual(db.execute("SELECT thread_id FROM coding_requests").fetchone()[0], self.other)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        # A second delete of the same id is an unknown conversation now.
        with self.assertRaises(Rejected):
            self.delete()

    def test_deleting_a_private_chat_deletes_its_memory_notes_and_a_shared_chats_notes_stay(self):
        import project_memory
        db = self.host.store.db
        project_memory.schema(db)
        with db:
            for note, cid in (('p1', self.private), ('s1', self.other)):
                db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', (note, 'one', 'personal', 'active',
                           json.dumps({'id': note, 'text': note, 'source': {'thread_id': cid}})))
        self.delete(self.private)
        self.assertEqual([r[0] for r in db.execute('SELECT id FROM project_memory')], ['s1'])
        self.delete(self.other)
        self.assertEqual([r[0] for r in db.execute('SELECT id FROM project_memory')], ['s1'], 'a shared chat’s notes were for the project')

    def test_delete_rolls_back_when_cleanup_fails_and_detaches_closed_turns_after_commit(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('task', json.dumps(
                {'id': 'task', 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR}, 'state': 'answered', 'tokens': []})))
        with patch('project_lifecycle.delete_rows', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                self.delete()
        self.assertEqual(self.listed(), [self.channel, self.other, self.private])
        self.assertEqual(db.execute('SELECT count(*) FROM coding_tasks').fetchone()[0], 1)
        coding = SimpleNamespace(turns={'task': SimpleNamespace(closed=True)}, pending={'review': {}, 'acceptance': {}},
                                 collaboration=SimpleNamespace(pending={'task': 1}))
        self.host.coding = coding
        try:
            self.delete()
            self.assertEqual((coding.turns, coding.collaboration.pending), ({}, {}))
        finally:
            del self.host.coding
        self.assertEqual(db.execute('SELECT count(*) FROM coding_tasks').fetchone()[0], 0)

    def archive_many(self, *ids):
        return self.host.dispatch('conversations_archive', {'projectId': 'one', 'channelIds': list(ids)})

    def delete_archived(self, *ids, confirmation=None):
        return self.host.dispatch('conversations_delete_archived', {'projectId': 'one', 'channelIds': list(ids),
                                                                    'confirmation': confirmation or f'DELETE {len(ids)} ARCHIVED'})

    def test_archiving_several_takes_what_it_can_and_says_why_the_rest_stayed(self):
        # Beta.16.1 item 3 (Bryce, 26 Sep): multi-select in the sidebar, archived in one go with one Undo.
        busy = self.conversation('Busy')
        self.host.turns['live'] = SimpleNamespace(binding={'id': busy, 'project_id': 'one'}, closed=False)
        result = self.archive_many(self.channel, busy, self.private)
        self.assertEqual(result['ids'], [self.channel, self.private])
        self.assertEqual([(r['id'], r['name']) for r in result['refused']], [(busy, 'Busy')])
        self.assertIn('request to finish', result['refused'][0]['reason'])
        self.assertEqual(self.listed(), [self.other, busy])
        # One already archived isn't archived again, so Undo wouldn't bring back something archived earlier.
        self.host.turns.clear()
        self.assertEqual(self.archive_many(self.channel, busy)['ids'], [busy])
        for bad in ([], ['x'] * 2, [None], [self.other] * 201, 'one'):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.host.dispatch('conversations_archive', {'projectId': 'one', 'channelIds': bad})
        with self.assertRaises(Rejected):
            self.archive_many(self.other, 'missing')
        self.assertEqual(self.listed(), [self.other], 'an unknown id refuses the lot')

    def test_delete_all_archived_deletes_exactly_what_was_confirmed_and_measures_what_it_freed(self):
        db = self.host.store.db
        extra = self.conversation('Old spike')
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            for cid in (self.channel, extra, self.other):
                task = {'id': cid + '-task', 'context': {'thread_id': cid, 'project_id': 'one', 'actor': ACTOR}, 'state': 'answered',
                        'tokens': [], 'messages': [{'role': 'assistant', 'text': 'x' * 200_000}]}
                db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
        self.archive_many(self.channel, extra)
        with self.assertRaisesRegex(Rejected, 'Confirm permanent deletion'):
            self.delete_archived(self.channel, extra, confirmation='DELETE 1 ARCHIVED')
        with self.assertRaisesRegex(Rejected, 'archived list changed'):
            self.delete_archived(self.channel, self.other)
        self.assertEqual(db.execute('SELECT count(*) FROM coding_tasks').fetchone()[0], 3, 'a refusal deletes nothing')
        result = self.delete_archived(self.channel, extra)
        self.assertEqual((result['ids'], result['count']), ([self.channel, extra], 2))
        # Two 200 KB task records went: measured from the database's pages, not guessed from the rows.
        self.assertGreater(result['databaseBytes'], 400_000)
        self.assertEqual(result['freedBytes'], result['databaseBytes'] + result['fileBytes'])
        self.assertEqual(self.listed(), [self.other, self.private])
        self.assertEqual(self.host.dispatch('conversations_archived', {'projectId': 'one'}), [])
        self.assertEqual(db.execute('SELECT count(*) FROM coding_tasks').fetchone()[0], 1)
        self.assertEqual((self.source / 'README.md').read_text(), 'keep source')
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_delete_all_archived_is_all_or_nothing_when_one_still_has_work(self):
        db = self.host.store.db
        self.archive_many(self.channel, self.other)
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(
                {'id': 't1', 'context': {'thread_id': self.other, 'project_id': 'one', 'actor': ACTOR}, 'state': 'working', 'tokens': []})))
        with self.assertRaisesRegex(Rejected, 'Nothing was deleted. Keep me: .*task to finish'):
            self.delete_archived(self.channel, self.other)
        self.assertEqual(len(self.host.dispatch('conversations_archived', {'projectId': 'one'})), 2)

    def test_the_file_versions_a_deleted_thread_kept_are_measured_as_they_leave(self):
        import task_evidence
        db = self.host.store.db
        folder = task_evidence.versions_folder(db)
        digest = 'a' * 64
        (folder / digest[:2]).mkdir(parents=True, exist_ok=True)
        (folder / digest[:2] / digest).write_bytes(b'v' * 5000)
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(
                {'id': 't1', 'context': {'thread_id': self.channel, 'project_id': 'one', 'actor': ACTOR}, 'state': 'answered', 'tokens': []})))
            db.execute('INSERT INTO task_evidence VALUES (?,?)', ('t1', json.dumps(
                {'id': 't1', 'files': [{'sha256': digest, 'bytes': 5000, 'stored': True}]})))
        self.archive()
        result = self.delete_archived(self.channel)
        self.assertEqual(result['fileBytes'], 5000)
        self.assertFalse((folder / digest[:2] / digest).exists())


if __name__ == '__main__':
    unittest.main()
