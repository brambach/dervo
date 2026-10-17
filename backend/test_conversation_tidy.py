"""Quiet threads tidy themselves (beta.16, b16/lifecycle). Disposable stores only; no providers or live project data."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from host import Host
from dervo_connections import Rejected

DAY = 86400


class ConversationTidyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'; self.source.mkdir()
        self.host = self.open()
        self.host.store.add_profile('account', 'codex', self.root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', self.source, 'account', 'test-model')
        self.old = self.conversation('Usage popover', seen_days=15)
        self.fresh = self.conversation('Live board', seen_days=2)

    def open(self):
        host = Host(self.root / 'data')
        self.addCleanup(host.close)
        return host

    def conversation(self, name, seen_days=None):
        cid = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': name, 'private': False})['id']
        if seen_days is not None:
            self.seen(cid, seen_days)
        return cid

    def seen(self, cid, days):
        import conversation_activity
        db = self.host.store.db
        conversation_activity.schema(db)
        with db:
            db.execute('INSERT INTO conversation_seen VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET seen=excluded.seen',
                       (cid, time.time() - days * DAY))

    def task(self, cid, task_id, **document):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task_id, json.dumps(
                {'id': task_id, 'context': {'project_id': 'one', 'thread_id': cid}, 'tokens': [], **document})))

    def call(self, operation, **args):
        if operation == 'conversations_tidy':
            args.setdefault('keep', [])
        return self.host.dispatch(operation, {'projectId': 'one', **args})

    def listed(self):
        return [c['id'] for c in self.call('conversations')]

    def test_a_thread_quiet_for_fourteen_days_is_archived_and_named(self):
        result = self.call('conversations_tidy')
        self.assertEqual(result['count'], 1)
        self.assertEqual([t['id'] for t in result['threads']], [self.old])
        self.assertEqual(result['threads'][0]['name'], 'Usage popover')
        self.assertEqual(result['days'], 14)
        self.assertEqual(self.listed(), [self.fresh])
        archived = self.call('conversations_archived')
        self.assertEqual(archived[0]['id'], self.old)
        self.assertEqual(archived[0]['tidy_batch'], result['batch'])
        self.assertLessEqual(archived[0]['quiet_since'], time.time() - 15 * DAY + 5)

    def test_it_tidies_once_per_launch(self):
        first = self.call('conversations_tidy')
        late = self.conversation('Old after open', seen_days=20)
        self.assertEqual(self.call('conversations_tidy'), first)
        self.assertIn(late, self.listed())

    def test_undo_brings_the_batch_back_and_the_next_launch_leaves_it(self):
        batch = self.call('conversations_tidy')['batch']
        self.assertEqual(self.call('conversations_tidy_undo', batch=batch), {'ids': [self.old]})
        self.assertEqual(self.listed(), [self.old, self.fresh])
        self.assertEqual(self.call('conversations_archived'), [])
        self.host.close()
        self.host = self.open()
        self.assertEqual(self.call('conversations_tidy')['count'], 0)

    def test_restore_clears_the_tidy_record(self):
        self.call('conversations_tidy')
        self.call('conversation_restore', channelId=self.old)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM conversation_tidied').fetchone()[0], 0)
        self.assertIn(self.old, self.listed())

    def test_pinned_and_never_and_unknown_times_stay(self):
        self.assertEqual(self.call('conversation_pin', channelId=self.old, pinned=True), {'id': self.old, 'pinned': True})
        self.assertEqual(self.call('conversation_pins'), [self.old])
        untimed = self.conversation('Never looked at')
        result = self.call('conversations_tidy')
        self.assertEqual(result['count'], 0)
        self.assertIn(untimed, self.listed())

    def test_never_and_seven_days(self):
        self.assertEqual(self.call('project_tidy_set', days=None)['days'], None)
        self.assertEqual(self.call('conversations_tidy')['count'], 0)
        self.host.tidied_projects.clear()
        self.assertEqual(self.call('project_tidy_set', days=7)['days'], 7)
        self.seen(self.fresh, 8)
        self.assertEqual(self.call('conversations_tidy')['count'], 2)
        self.assertEqual(self.call('project_tidy_setting'), {'days': 7, 'default': 14, 'choices': [7, 14, 30]})

    def test_the_thread_being_opened_stays(self):
        self.assertEqual(self.call('conversations_tidy', keep=[self.old])['count'], 0)
        self.assertIn(self.old, self.listed())
        with self.assertRaises(Rejected):
            self.host.dispatch('conversations_tidy', {'projectId': 'one', 'keep': 'x'})

    def test_setting_accepts_only_the_menu_choices(self):
        for days in (0, 1, 3, 15, True, '14', 14.0, 'Smart', 'auto'):
            with self.assertRaises(Rejected):
                self.call('project_tidy_set', days=days)

    def test_work_that_waits_keeps_its_thread(self):
        cases = {
            'working': {'state': 'working'},
            'lost': {'state': 'disconnected'},
            'permission': {'state': 'needs_permission'},
            'unreviewed': {'state': 'answered', 'completion': {'changed_files': ['a.py']}},
            'stopped with files': {'state': 'blocked', 'error': 'Stopped before completion. It had changed a.py.'},
            'approved not committed': {'state': 'answered', 'review': 'approved', 'completion': {'changed_files': ['a.py']}},
        }
        # The project is a folder inside a repository, not its root (behaviour review N6, 25 Sep): its approved but
        # uncommitted result still waits.
        import subprocess
        subprocess.run(['/usr/bin/git', 'init', '-q', str(self.root)], check=True)
        threads = {}
        for index, (name, document) in enumerate(cases.items()):
            cid = self.conversation(name, seen_days=30)
            self.task(cid, f't{index}', **document)
            threads[name] = cid
        self.seen(self.fresh, 30)
        self.task(self.fresh, 'done', state='answered', review='approved', completion={'changed_files': ['b.py']}, commit={'sha': 'abc'})
        # Committed, then undone with a commit of its own: nothing waits, so the thread can go.
        undone = self.conversation('undone', seen_days=30)
        self.task(undone, 'undone-task', state='answered', review='approved', completion={'changed_files': ['c.py']},
                  commit={'sha': 'def', 'reverted': {'sha': 'fed'}})
        tidied = {t['id'] for t in self.call('conversations_tidy')['threads']}
        self.assertEqual(tidied, {self.old, self.fresh, undone})
        for name, cid in threads.items():
            self.assertIn(cid, self.listed(), name)

    def test_an_agents_conversation_and_a_pending_message_stay(self):
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS collab_agents (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO collab_agents VALUES (?,?)', ('agent', json.dumps({'channel_id': self.old})))
        self.assertEqual(self.call('conversations_tidy')['count'], 0)

    def test_pinning_an_archived_thread_is_refused(self):
        self.call('conversation_archive', channelId=self.old)
        with self.assertRaises(Rejected):
            self.call('conversation_pin', channelId=self.old, pinned=True)

    def test_today_batch_for_home(self):
        self.assertIsNone(self.call('conversations_tidied'))
        batch = self.call('conversations_tidy')['batch']
        # Home's line says the days that tidy used, even after the setting changes (behaviour review N7, 25 Sep).
        self.call('project_tidy_set', days=None)
        today = self.call('conversations_tidied')
        self.assertEqual((today['batch'], today['count'], today['days']), (batch, 1, 14))
        self.call('conversations_tidy_undo', batch=batch)
        self.assertIsNone(self.call('conversations_tidied'))

    def test_a_workstream_dervo_made_retires_with_its_threads_and_undo_restores_it(self):
        from project_ideas import ProjectIdeas
        from conversation_organisation import RULES_ACTOR
        ideas = ProjectIdeas(self.host.store)
        db = self.host.store.db
        with db:
            idea = ideas.insert_idea({'project_id': 'one', 'profile_id': 'account'}, 'Sidebar', RULES_ACTOR)
            db.execute('INSERT INTO project_idea_members VALUES (?,?)', (self.old, idea['id']))
        batch = self.call('conversations_tidy')['batch']
        stored = json.loads(db.execute('SELECT document FROM project_ideas WHERE id=?', (idea['id'],)).fetchone()[0])
        self.assertIsNotNone(stored['archived_at'])
        self.assertEqual((stored['events'][-1]['reason'], stored['events'][-1]['batch']), ('tidied', batch))
        self.call('conversations_tidy_undo', batch=batch)
        stored = json.loads(db.execute('SELECT document FROM project_ideas WHERE id=?', (idea['id'],)).fetchone()[0])
        self.assertIsNone(stored['archived_at'])

    def test_a_workstream_you_made_stays(self):
        from project_ideas import ProjectIdeas
        ideas = ProjectIdeas(self.host.store)
        db = self.host.store.db
        with db:
            idea = ideas.insert_idea({'project_id': 'one', 'profile_id': 'account'}, 'Mine', 'local-user')
            db.execute('INSERT INTO project_idea_members VALUES (?,?)', (self.old, idea['id']))
        self.call('conversations_tidy')
        stored = json.loads(db.execute('SELECT document FROM project_ideas WHERE id=?', (idea['id'],)).fetchone()[0])
        self.assertIsNone(stored['archived_at'])

    def test_smart_takes_settled_work_after_a_day_and_the_rest_after_a_week(self):
        # Beta.16.1 item 7 (Bryce, 26 Sep): settled work goes once nobody has opened it for a day; any other quiet thread
        # still goes after 7 days ("Also go after 7 days"). Every other rule is the same.
        import subprocess
        subprocess.run(['/usr/bin/git', 'init', '-q', str(self.source)], check=True)
        self.assertEqual(self.call('project_tidy_set', days='smart')['days'], 'smart')
        self.assertEqual(self.call('project_tidy_setting')['days'], 'smart')
        # An older build reads Smart as 7 days.
        self.assertEqual(self.host.store.db.execute("SELECT days FROM project_tidy WHERE project_id='one'").fetchone()[0], 7)
        settled = {
            'committed': {'state': 'ready_for_review', 'review': 'approved', 'completion': {'changed_files': ['a.py']}, 'commit': {'sha': 'abc'}},
            'rejected': {'state': 'ready_for_review', 'review': 'rejected', 'completion': {'changed_files': ['a.py']}},
            'text answer': {'state': 'answered'},
            'chat only': None,
        }
        unsettled = {
            'failed': {'state': 'blocked', 'error': 'The provider stopped.'},
            'unreviewed nothing changed': {'state': 'ready_for_review'},
        }
        waiting = {
            'approved not committed': {'state': 'ready_for_review', 'review': 'approved', 'completion': {'changed_files': ['a.py']}},
            'unreviewed files': {'state': 'ready_for_review', 'completion': {'changed_files': ['a.py']}},
        }
        threads = {}
        for index, (name, document) in enumerate({**settled, **unsettled, **waiting}.items()):
            cid = self.conversation(name, seen_days=2)
            if document:
                self.task(cid, f't{index}', **document)
            threads[name] = cid
        opened_today = self.conversation('opened today', seen_days=0.5)
        self.task(opened_today, 'today', state='answered')
        failed_old = self.conversation('failed a week ago', seen_days=8)
        self.task(failed_old, 'old-fail', state='blocked', error='The provider stopped.')
        result = self.call('conversations_tidy')
        self.assertEqual(result['days'], 0, 'a Smart tidy records 0 days')
        tidied = {t['name'] for t in result['threads']}
        # Usage popover (15 days) and Live board (a chat, 2 days) are the fixture's own: both go.
        self.assertEqual(tidied, set(settled) | {'Usage popover', 'Live board', 'failed a week ago'})
        listed = set(self.listed())
        for name in (*unsettled, *waiting):
            self.assertIn(threads[name], listed, name)
        self.assertIn(opened_today, listed, 'opened within the day')
        self.assertEqual(self.call('conversations_tidied')['days'], 0)
        # Undo is the same batch Undo, and a number or Never turns Smart off.
        self.assertEqual(len(self.call('conversations_tidy_undo', batch=result['batch'])['ids']), len(tidied))
        self.assertEqual(self.call('project_tidy_set', days=14)['days'], 14)
        self.assertEqual(self.call('project_tidy_setting')['days'], 14)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM project_tidy_smart').fetchone()[0], 0)

    def test_smart_keeps_pinned_running_and_unopened_threads(self):
        self.call('project_tidy_set', days='smart')
        pinned = self.conversation('pinned', seen_days=3)
        self.call('conversation_pin', channelId=pinned, pinned=True)
        running = self.conversation('running', seen_days=3)
        self.task(running, 'run', state='working')
        self.assertEqual({t['name'] for t in self.call('conversations_tidy')['threads']}, {'Usage popover', 'Live board'})
        self.assertIn(pinned, self.listed())
        self.assertIn(running, self.listed())

    def test_deleting_a_tidied_thread_takes_its_records(self):
        self.call('conversation_pin', channelId=self.fresh, pinned=True)
        self.call('conversations_tidy')
        self.call('conversation_delete', channelId=self.old, confirmation='DELETE ' + self.old)
        self.call('conversation_archive', channelId=self.fresh)
        self.call('conversation_delete', channelId=self.fresh, confirmation='DELETE ' + self.fresh)
        db = self.host.store.db
        for table in ('conversation_tidied', 'conversation_pins', 'conversation_touched'):
            self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)


if __name__ == '__main__':
    unittest.main()
