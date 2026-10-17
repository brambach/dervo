"""Disposable stores only. No providers, credentials, commands or live project data."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from host import Host
from dervo_connections import Rejected

class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'README.md').write_text('keep source')
        self.host = Host(self.root / 'data')
        self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', self.root/'account', 'test@example.test', 'personal')
        for pid in ('one', 'two'):
            self.host.store.add_project(pid, self.source, 'account', 'test-model')
        self.channel = self.host.dispatch('create_conversation', {'projectId':'one','name':'Saved','private':False})['id']

    def archive(self):
        return self.host.dispatch('project_archive', {'projectId':'one'})

    def test_file_link_uses_the_selected_project_and_rejects_unknown_fields(self):
        with patch('project_files.reveal', return_value={'revealed': True}) as reveal:
            self.assertTrue(self.host.dispatch('project_file_reveal', {'projectId': 'one', 'path': 'README.md'})['revealed'])
            self.assertEqual([Path(folder).resolve() for folder in reveal.call_args.args[0]], [self.source.resolve()])
            self.assertEqual(reveal.call_args.args[1], 'README.md')
            # From a thread without its own folder it's still the project folder alone.
            self.host.dispatch('project_file_reveal', {'projectId': 'one', 'path': 'README.md', 'threadId': self.channel})
            self.assertEqual([Path(folder).resolve() for folder in reveal.call_args.args[0]], [self.source.resolve()])
            with self.assertRaises(Rejected):
                self.host.dispatch('project_file_reveal', {'projectId': 'two', 'path': 'README.md', 'threadId': self.channel})
            with self.assertRaises(Rejected):
                self.host.dispatch('project_file_reveal', {'projectId': 'one', 'path': 'README.md', 'execute': True})

    def delete(self, **extra):
        return self.host.dispatch('project_delete', {'projectId':'one','confirmation':'DELETE one', **extra})

    def test_archive_restore_survives_restart_and_preserves_history(self):
        self.archive()
        self.assertEqual([p['id'] for p in self.host.dispatch('projects', {})], ['two'])
        with self.assertRaises(Rejected):
            self.host.dispatch('create_conversation', {'projectId':'one','name':'Blocked','private':False})
        self.host.close()
        self.host = Host(self.root/'data')
        self.assertEqual(self.host.dispatch('projects_archived', {})[0]['id'], 'one')
        self.host.dispatch('project_restore', {'projectId':'one'})
        self.assertEqual(self.host.dispatch('conversations', {'projectId':'one'})[0]['id'], self.channel)
        self.assertEqual((self.source/'README.md').read_text(), 'keep source')

    def test_confirmation_is_exact_and_a_pending_message_holds_archive_but_not_delete(self):
        # Bryce, 23 Sep (app #12): typing the project's name is enough to delete it. Archive keeps the full rule.
        with self.assertRaises(Rejected): self.delete(confirmation='DELETE two')
        self.host.store.send(self.channel, 'one', 'pending', 'keep', actor='local-user')
        with self.assertRaises(Rejected): self.archive()
        with self.assertRaises(Rejected): self.delete(confirmation='DELETE two')
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 1, 'a refused delete removes nothing')
        result = self.delete()
        self.assertEqual((result['channelIds'], result['sourceFolderPreserved']), ([self.channel], True))
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
        self.assertEqual((self.source/'README.md').read_text(), 'keep source')
        self.assertEqual(self.host.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_delete_cleans_relational_and_document_records_but_keeps_other_project(self):
        db = self.host.store.db
        mid = self.host.store.send(self.channel, 'one', 'pending', 'saved message', actor='local-user')
        self.host.dispatch('cancel', {'projectId':'one','channelId':self.channel,'messageId':mid})
        with db:
            db.execute('CREATE TABLE coding_tasks(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE task_evidence(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('CREATE TABLE coding_tool_audit(task_id TEXT, document TEXT)')
            db.execute('CREATE TABLE project_knowledge(id TEXT PRIMARY KEY, project_id TEXT, document TEXT)')
            for pid in ('one', 'two'):
                task = {'id':pid+'task','context':{'project_id':pid},'state':'answered','tokens':[]}
                db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
                db.execute('INSERT INTO task_evidence VALUES (?,?)', (task['id'], json.dumps({'project_id':pid})))
                db.execute('INSERT INTO coding_tool_audit VALUES (?,?)', (task['id'], '{}'))
                db.execute('INSERT INTO project_knowledge VALUES (?,?,?)', (pid,pid,'{}'))
        self.archive()
        result = self.delete()
        self.assertEqual(result['channelIds'], [self.channel])
        self.assertTrue(result['sourceFolderPreserved'])
        self.assertEqual((self.source/'README.md').read_text(), 'keep source')
        self.assertEqual(db.execute('SELECT count(*) FROM profiles').fetchone()[0], 1)
        for table in ('projects','coding_tasks','task_evidence','coding_tool_audit','project_knowledge'):
            self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 1, table)
        for table in ('threads','messages','turns','events','local_channels','project_archives'):
            self.assertEqual(db.execute('SELECT count(*) FROM '+table).fetchone()[0], 0, table)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_deletion_detaches_closed_turns_after_commit(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE coding_tasks(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('task', json.dumps({
                'id':'task','context':{'project_id':'one'},'state':'answered','tokens':[]})))
        coding = SimpleNamespace(turns={'task':SimpleNamespace(closed=True)},
            pending={'review':{},'acceptance':{}}, collaboration=SimpleNamespace(pending={}))
        self.host.coding = coding
        try:
            self.archive()
            self.delete()
            self.assertEqual(coding.turns, {})
        finally:
            del self.host.coding

    def test_delete_rolls_back_when_cleanup_fails(self):
        self.archive()
        with patch('project_lifecycle.delete_rows', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError): self.delete()
        self.assertEqual(len(self.host.dispatch('projects_archived', {})), 1)
        self.assertTrue(self.host.store.db.execute('SELECT 1 FROM threads WHERE id=?', (self.channel,)).fetchone())

    def test_finished_state_with_unfinished_job_cannot_archive(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE coding_tasks(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('task', json.dumps({
                'context':{'project_id':'one'},'state':'answered',
                'tokens':[{'state':'delivered','job_id':'job','job_done':False}]})))
        with self.assertRaises(Rejected): self.archive()

    def test_names_persist_without_changing_binding_or_folder(self):
        before = dict(self.host.store.db.execute('SELECT * FROM projects WHERE id=?', ('one',)).fetchone())
        self.host.dispatch('project_rename', {'projectId':'one','name':'  Dervo  '})
        self.assertEqual(self.host.dispatch('projects', {})[0]['name'], 'Dervo')
        self.assertEqual(dict(self.host.store.db.execute('SELECT * FROM projects WHERE id=?', ('one',)).fetchone()), before)
        self.archive()
        self.host.close(); self.host = Host(self.root/'data')
        self.assertEqual(self.host.dispatch('projects_archived', {})[0]['name'], 'Dervo')
        self.host.dispatch('project_restore', {'projectId':'one'})
        self.assertEqual(self.host.dispatch('projects', {})[0]['name'], 'Dervo')
        for name in ('', '  ', 'a'*81, 'two\nlines', '\u202efake', None):
            with self.assertRaises(Rejected):
                self.host.dispatch('project_rename', {'projectId':'one','name':name})
        self.assertEqual(self.host.dispatch('projects', {})[0]['name'], 'Dervo')

    def test_active_project_delete_requires_confirmation_and_removes_its_name_only(self):
        for pid in ('one', 'two'):
            self.host.dispatch('project_rename', {'projectId':pid,'name':pid+' named'})
        with self.assertRaises(Rejected): self.delete(confirmation='DELETE two')
        self.delete()
        self.assertEqual([tuple(r) for r in self.host.store.db.execute('SELECT * FROM project_names')], [('two','two named')])
        self.assertEqual((self.source/'README.md').read_text(), 'keep source')
        self.assertEqual(self.host.store.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def blocker_task(self, task_id, state='working', *, project='one', thread=None, **extra):
        db = self.host.store.db
        task = {'id': task_id, 'context': {'project_id': project, 'thread_id': thread or self.channel},
                'state': state, 'tokens': [], 'messages': [{'role': 'user', 'text': 'Work on ' + task_id}], **extra}
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task_id, json.dumps(task)))

    def blockers(self, scope='release', pid=None, cid=None):
        return self.host.dispatch('lifecycle_blockers', {'scope': scope, 'projectId': pid, 'channelId': cid})

    def test_blocker_links_keep_exact_project_thread_and_task(self):
        other = self.host.dispatch('create_conversation', {'projectId': 'two', 'name': 'Another project', 'private': False})['id']
        self.blocker_task('first')
        self.blocker_task('second', project='two', thread=other)
        before = self.host.store.db.total_changes
        rows = self.blockers()
        self.assertEqual([(r['projectId'], r['channelId'], r['taskId']) for r in rows],
                         [('one', self.channel, 'first'), ('two', other, 'second')])
        self.assertEqual(rows[1]['taskName'], 'Work on second')
        self.assertEqual(rows[1]['threadName'], 'Another project')
        self.assertEqual(self.host.store.db.total_changes, before)
        self.assertEqual([r['taskId'] for r in self.blockers('conversation', 'one', self.channel)], ['first'])

    def test_release_and_thread_blockers_ignore_old_unknown_outcomes_but_keep_live_checks(self):
        self.blocker_task('old', 'disconnected', tokens=[{'state': 'uncertain'}])
        self.blocker_task('permission', 'needs_permission')
        self.blocker_task('finished', 'completed')
        self.blocker_task('check', 'answered', tokens=[{'state': 'delivered', 'job_id': 'job', 'job_done': False}])
        self.blocker_task('review', 'ready_for_review', collaboration={'review': {'enabled': True, 'phase': 'initial'}})
        self.assertEqual([(r['taskId'], r['kind']) for r in self.blockers()], [('check', 'check'), ('review', 'review')])
        self.assertEqual([r['taskId'] for r in self.blockers('conversation', 'one', self.channel)], ['check', 'review'])
        project = self.blockers('project', 'one')
        self.assertEqual([r['taskId'] for r in project], ['old', 'permission', 'check', 'review'])
        self.assertIn('Mark the lost run', project[0]['instruction'])
        self.assertIn('Approve or deny', project[1]['instruction'])

    def test_other_owners_private_blockers_never_expose_task_or_thread_identity(self):
        self.host.store.add_thread('secret-thread', 'one', 'secret-thread', 'private', owner='someone-else')
        self.blocker_task('secret-task', thread='secret-thread', messages=[{'role': 'user', 'text': 'Secret request'}])
        rows = self.blockers()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['threadName'], 'a private thread')
        self.assertIsNone(rows[0]['channelId'])
        self.assertIsNone(rows[0]['taskId'])
        self.assertEqual(rows[0]['taskName'], '')
        self.assertNotIn('secret', json.dumps(rows))
        with self.assertRaises(Rejected): self.blockers('conversation', 'one', 'secret-thread')

    def test_blocker_read_rejects_cross_project_or_malformed_scope(self):
        for args in [dict(scope='conversation', projectId='two', channelId=self.channel),
                     dict(scope='release', projectId='one', channelId=None),
                     dict(scope='project', projectId='one', channelId=self.channel),
                     dict(scope='project', projectId='missing', channelId=None),
                     dict(scope='all', projectId=None, channelId=None)]:
            with self.assertRaises(Rejected): self.host.dispatch('lifecycle_blockers', args)
        with self.assertRaises(Rejected):
            self.host.dispatch('lifecycle_blockers', dict(scope='release', projectId=None, channelId=None, stop=True))

    def test_queued_request_is_a_conversation_blocker_and_read_does_not_cancel_it(self):
        queue = SimpleNamespace(items=lambda cid: [{'id': 'q1', 'state': 'queued'}] if cid == self.channel else [])
        self.host.coding = SimpleNamespace(turns={}, conversation=SimpleNamespace(queue=queue))
        try:
            rows = self.blockers('conversation', 'one', self.channel)
            self.assertEqual([(r['kind'], r['channelId'], r['taskId']) for r in rows], [('queued', self.channel, None)])
            self.assertEqual((rows[0]['actions'], rows[0]['actionChannelId'], rows[0]['queueId']), (['cancel'], self.channel, 'q1'))
            self.assertEqual(queue.items(self.channel), [{'id': 'q1', 'state': 'queued'}])
        finally:
            del self.host.coding

    def test_each_blocker_offers_its_own_action_on_its_exact_ids(self):
        # app #9: the blocker names the task and clears it in place with the task sheet's own operations.
        self.blocker_task('running', 'working')
        self.blocker_task('lost', 'disconnected', runs=[{'state': 'uncertain'}])
        self.blocker_task('asking', 'needs_permission')
        self.blocker_task('reviewing', 'ready_for_review', collaboration={'review': {'enabled': True, 'phase': 'initial'}})
        self.blocker_task('checking', 'blocked', tokens=[{'state': 'delivered', 'job_id': 'job', 'job_done': False}])
        rows = {r['taskId']: r for r in self.blockers('project', 'one')}
        self.assertEqual({task: row['actions'] for task, row in rows.items()},
                         {'running': ['stop'], 'lost': ['settle'], 'asking': ['dismiss'], 'reviewing': ['stop'], 'checking': []})
        for task, row in rows.items():
            self.assertEqual((row['channelId'], row['taskName']), (self.channel, 'Work on ' + task))
            if row['actions']:
                self.assertEqual((row['actionChannelId'], row['actionTaskId']), (self.channel, task))
        self.assertEqual(rows['lost']['taskState'], 'disconnected')
        # An archived thread's task can't be opened from the list, but it can still be cleared where it's named.
        self.host.store.db.execute('INSERT INTO conversation_archives VALUES (?,?)', (self.channel, 1))
        self.host.store.db.commit()
        lost = next(r for r in self.blockers('project', 'one') if r['kind'] == 'lost')
        self.assertEqual((lost['channelId'], lost['taskId'], lost['actionChannelId'], lost['actionTaskId']), (None, None, self.channel, 'lost'))
        self.assertTrue(lost['instruction'].startswith('It’s in an archived conversation. Mark the lost run'), 'never asks for a Restore it doesn’t offer')

    def test_another_owners_private_blocker_offers_no_action(self):
        self.host.store.add_thread('secret-thread', 'one', 'secret-thread', 'private', owner='someone-else')
        self.blocker_task('secret-task', 'disconnected', thread='secret-thread')
        row = self.blockers('project', 'one')[0]
        self.assertEqual((row['actions'], row['actionChannelId'], row['actionTaskId'], row['taskState']), ([], None, None, None))

    def test_an_answered_task_never_holds_unless_a_tool_call_or_check_is_still_going(self):
        # app #9: threads Home shows as Answered held deletes and version switches. Answered is settled.
        self.blocker_task('answered', 'answered', tokens=[{'state': 'uncertain'}, {'state': 'failed'}])
        for scope, pid, cid in (('release', None, None), ('project', 'one', None), ('conversation', 'one', self.channel)):
            self.assertEqual(self.blockers(scope, pid, cid), [], scope)
        self.archive()
        self.host.dispatch('project_restore', {'projectId': 'one'})
        self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': self.channel})

    def test_delete_stops_running_work_first_and_waits_for_it_to_close(self):
        # app #12: running tasks are stopped first, stale records go without asking, and the folder is never touched.
        class Turn:
            def __init__(self, project=None):
                self.closed, self.stops, self.binding = False, 0, {'project_id': project, 'id': None}
            def stop(self):
                self.stops += 1
        run, reply, other = Turn(), Turn('one'), Turn()
        self.blocker_task('running', 'working')
        self.blocker_task('lost', 'disconnected', runs=[{'state': 'uncertain'}], tokens=[{'state': 'uncertain'}])
        self.blocker_task('asking', 'needs_permission')
        self.blocker_task('elsewhere', 'working', project='two')
        removed = []
        queue = SimpleNamespace(items=lambda cid: [{'id': 'q1', 'state': 'queued', 'thread_id': cid}] if cid == self.channel and not removed else [],
                                remove=removed.append)
        cancelled = []
        self.host.coding = SimpleNamespace(turns={'running': run, 'elsewhere': other}, pending={'review': {}, 'acceptance': {}},
                                           collaboration=SimpleNamespace(pending={}, review_stop=lambda task: None),
                                           evidence=SimpleNamespace(cancel=lambda *a, **k: cancelled.append(a)),
                                           conversation=SimpleNamespace(queue=queue))
        self.host.turns['m1'] = reply
        try:
            with patch('thread_fanout.cancelled'), patch('task_questions.cancelled'):
                first = self.delete()
                self.assertEqual(first, {'id': 'one', 'stopping': 2}, 'the run and the reply are still closing')
                self.assertEqual((run.stops, reply.stops, other.stops), (1, 1, 0), 'only this project’s work is stopped')
                self.assertEqual([item['id'] for item in removed], ['q1'], 'a queued request never starts in a project being deleted')
                self.assertTrue(self.host.store.db.execute('SELECT 1 FROM threads WHERE id=?', (self.channel,)).fetchone(), 'nothing is deleted yet')
                run.closed = reply.closed = True
                second = self.delete()
            self.assertEqual((second['channelIds'], second['sourceFolderPreserved']), ([self.channel], True))
            left = [json.loads(r[0])['id'] for r in self.host.store.db.execute('SELECT document FROM coding_tasks')]
            self.assertEqual(left, ['elsewhere'])
            self.assertEqual((self.source/'README.md').read_text(), 'keep source')
            self.assertNotIn('m1', self.host.turns)
            self.assertIn('elsewhere', self.host.coding.turns)
        finally:
            del self.host.coding
            self.host.turns.pop('m1', None)

    def test_a_delete_coordination_would_refuse_stops_nothing(self):
        # Lifecycle review, 23 Sep: the coordination refusal came after the stops, so a delete could stop every run and
        # then fail. It's checked first now.
        class Turn:
            closed, stops, binding = False, 0, {'project_id': None, 'id': None}
            def stop(self):
                self.stops += 1
        run = Turn()
        self.blocker_task('running', 'working')
        self.host.coding = SimpleNamespace(turns={'running': run}, pending={}, collaboration=SimpleNamespace(pending={}))
        db = self.host.store.db
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS harness_requests(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
                db.execute('INSERT INTO harness_requests VALUES (?,?)', ('h1', json.dumps({'project_id': 'one', 'thread_id': self.channel, 'state': 'running'})))
            with self.assertRaisesRegex(Rejected, 'can’t be deleted yet. Wait for coordination in Saved'):
                self.delete()
            self.assertEqual(run.stops, 0, 'nothing was stopped for a delete that was refused')
            with db:
                db.execute('DELETE FROM harness_requests')
            # A coordination store with a run it still has as unknown is refused the same way, before anything stops.
            import sqlite3
            harness = self.root / 'data' / 'harness'
            harness.mkdir(parents=True, exist_ok=True)
            store = sqlite3.connect(harness / 'dervo.sqlite3')
            store.execute('CREATE TABLE runs(run_id TEXT, project_id TEXT, room_id TEXT, status TEXT)')
            store.execute("INSERT INTO runs VALUES ('r1', 'project:one', 'conversation:x', 'unknown')")
            store.commit(); store.close()
            with self.assertRaisesRegex(Rejected, 'Coordination still has active or uncertain work'):
                self.delete()
            self.assertEqual(run.stops, 0)
            self.assertTrue(self.host.store.db.execute('SELECT 1 FROM threads WHERE id=?', (self.channel,)).fetchone())
        finally:
            del self.host.coding

    def test_a_lost_run_with_a_check_still_going_offers_no_button(self):
        self.blocker_task('lost', 'disconnected', runs=[{'state': 'uncertain'}],
                          tokens=[{'state': 'uncertain'}, {'state': 'delivered', 'job_id': 'job', 'job_done': False}])
        row = self.blockers('project', 'one')[0]
        self.assertEqual((row['kind'], row['actions']), ('lost', []), 'Mark as stopped would be refused while the check runs')

    def test_delete_stops_a_preview_build_and_drops_its_record(self):
        stopped, saved = [], []
        self.host.previews = SimpleNamespace(processes={'one': object()}, jobs={'one': {'state': 'building'}, 'two': {'state': 'built'}},
                                             read=lambda pid: None, stop=lambda pid: stopped.append(pid) or self.host.previews.processes.pop(pid, None),
                                             save=lambda: saved.append(dict(self.host.previews.jobs)), close=lambda: None, tick=lambda: None)
        try:
            with self.assertRaisesRegex(Rejected, 'preview build'):
                self.archive()
            self.delete()
            self.assertEqual(stopped, ['one'])
            self.assertEqual(list(self.host.previews.jobs), ['two'], 'another project’s build is kept')
            self.assertEqual((self.source/'README.md').read_text(), 'keep source')
        finally:
            del self.host.previews

    def test_delete_closes_the_project_terminal_and_cancels_a_forgotten_check(self):
        stopped = []
        sessions = {'s1': SimpleNamespace(project='one', active=True), 's2': SimpleNamespace(project='two', active=True)}
        def stop(chosen):
            stopped.extend(chosen)
            for session in chosen:
                session.active = False
        self.host.terminals = SimpleNamespace(sessions=sessions, stop=stop, close=lambda: None, tick=lambda: None,
                                              active=lambda pid: any(s.project == pid and s.active for s in sessions.values()))
        self.blocker_task('checked', 'answered', tokens=[{'state': 'delivered', 'job_id': 'job', 'job_done': False}])
        cancelled = []
        self.host.coding = SimpleNamespace(turns={}, pending={'review': {}, 'acceptance': {}}, collaboration=SimpleNamespace(pending={}),
                                           evidence=SimpleNamespace(cancel=lambda task_id, **k: cancelled.append((task_id, k['job_id']))))
        try:
            self.assertEqual([r['kind'] for r in self.blockers('project', 'one')], ['terminal', 'check'], 'a live check still holds archive')
            self.delete()
            self.assertEqual(stopped, [sessions['s1']])
            self.assertTrue(sessions['s2'].active, 'another project’s terminal stays open')
            self.assertEqual(cancelled, [('checked', 'job')])
        finally:
            del self.host.coding

if __name__ == '__main__':
    unittest.main()
