"""The state spine (thread_states.py): one record per conversation, read by "what changed since N", pushed as one line.
Disposable stores only; no providers."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path

import conversation_activity as ca
import thread_states
from host import ACTOR, Host, serve


def task(tid, thread, state, **extra):
    return {'id': tid, 'state': state, 'context': {'thread_id': thread, 'project_id': 'one'}, 'runs': [{'id': 'r1', 'state': 'running'}],
            'tokens': [], 'messages': [], **extra}


class SpineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'source').mkdir()
        self.host = Host(root / 'data'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', root / 'source', 'account', 'test-model')
        self.a = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'A', 'private': False})['id']
        self.b = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'B', 'private': False})['id']
        self.db = self.host.store.db
        ca.schema(self.db)

    def save(self, record):
        with self.db:
            ca.record(self.db, record)

    def states(self, since):
        return self.host.dispatch('thread_states', {'since': since})

    def test_the_first_read_has_every_conversation_and_later_reads_only_what_moved(self):
        first = self.states(0)
        self.assertEqual({row['id'] for row in first['threads']}, {self.a, self.b})
        self.assertEqual({row['state'] for row in first['threads']}, {'idle'})
        self.assertEqual(self.states(first['version'])['threads'], [], 'nothing moved, nothing to read')
        self.save(task('t1', self.a, 'working'))
        moved = self.states(first['version'])
        self.assertEqual([(row['id'], row['state']) for row in moved['threads']], [(self.a, 'working')])
        self.assertGreater(moved['version'], first['version'])

    def test_a_save_that_changes_nothing_shown_moves_nothing(self):
        self.save(task('t1', self.a, 'working'))
        before = ca.version(self.db)
        self.save(task('t1', self.a, 'working'))
        self.assertEqual(ca.version(self.db), before)

    def test_each_record_matches_the_run_record_it_came_from(self):
        """The record says what the saved task says: needs you, finished, stopped, never a model's own words."""
        self.save(task('t1', self.a, 'ready_for_review'))
        self.save(task('t2', self.b, 'completed', runs=[{'id': 'r1', 'state': 'completed'}]))
        rows = {row['id']: row for row in self.states(0)['threads']}
        self.assertEqual(rows[self.a]['state'], 'needs_you')
        self.assertEqual(rows[self.b]['state'], 'finished')
        self.assertEqual((rows[self.a]['running'], rows[self.a]['pending_question'], rows[self.b]['running']), (False, None, False))
        self.assertEqual(rows[self.a]['name'], 'A')
        self.assertEqual(rows[self.a]['projectId'], 'one')

    def test_each_record_carries_its_threads_newest_cloud_run(self):
        """Send to cloud (PR #12): Home shows where a thread's cloud run is, unknown included, with its link."""
        self.assertIsNone(self.states(0)['threads'][0]['cloud'], 'no cloud runs on this store')
        with self.db:
            self.db.execute('CREATE TABLE cloud_runs (id TEXT, conversation_id TEXT, provider TEXT, state TEXT, url TEXT, branch TEXT, '
                            'detail TEXT, created_at REAL, updated_at REAL)')
            self.db.execute("INSERT INTO cloud_runs VALUES ('c1', ?, 'codex', 'ready', 'https://example.test/1', 'b1', NULL, 1, 2)", (self.a,))
            self.db.execute("INSERT INTO cloud_runs VALUES ('c2', ?, 'claude', 'unknown', 'https://example.test/2', NULL, 'Lost.', 3, 4)", (self.a,))
        rows = {row['id']: row for row in self.states(0)['threads']}
        self.assertEqual(rows[self.a]['cloud'], dict(id='c2', provider='claude', state='unknown', url='https://example.test/2',
                                                      branch=None, detail='Lost.', startedAt=3, at=4))
        self.assertIsNone(rows[self.b]['cloud'])

    def test_a_run_dervo_lost_sight_of_is_unknown_not_working(self):
        self.save(task('t1', self.a, 'working'))
        with self.db:
            self.db.execute("INSERT INTO session_marks VALUES ('start', ?)", (time.time() + 5,))  # Dervo restarted after it
        row = next(row for row in self.states(0)['threads'] if row['id'] == self.a)
        self.assertEqual(row['state'], 'unknown')
        self.assertTrue(row['lost'])

    def test_a_working_run_that_has_gone_quiet_says_so_and_hearing_from_it_brings_it_back(self):
        self.save(task('t1', self.a, 'working'))
        clock = [time.time()]
        alive = thread_states.Alive(self.db, clock=lambda: clock[0])
        alive.heard(self.a)
        version = ca.version(self.db)
        row = lambda now: next(r for r in thread_states.changes(self.db, ACTOR, 0, alive=alive, now=now)['threads'] if r['id'] == self.a)
        self.assertEqual(row(clock[0] + 1)['state'], 'working')
        self.assertEqual(row(clock[0] + thread_states.QUIET_SECONDS + 1)['state'], 'quiet')
        for _ in range(5):
            clock[0] += 5
            alive.heard(self.a)
        self.assertEqual(ca.version(self.db), version, 'a run that keeps talking moves nothing: nothing on screen changed')
        alive.sweep(now=clock[0] + thread_states.QUIET_SECONDS)
        quiet = ca.version(self.db)
        self.assertGreater(quiet, version, 'going quiet moves the row')
        clock[0] += thread_states.QUIET_SECONDS + 1
        alive.heard(self.a)
        self.assertGreater(ca.version(self.db), quiet, 'speaking again after that moves it back')

    def test_archiving_dismissing_and_seeing_move_the_row_and_a_deleted_one_says_gone(self):
        self.save(task('t1', self.a, 'ready_for_review'))
        start = ca.version(self.db)
        self.host.dispatch('conversation_dismiss', {'projectId': 'one', 'channelId': self.a})
        self.assertEqual([row['id'] for row in self.states(start)['threads']], [self.a])
        start = ca.version(self.db)
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.b})
        self.assertEqual(self.states(start)['threads'], [], 'seeing a conversation with nothing new changes nothing')
        self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': self.b})
        row = next(row for row in self.states(start)['threads'] if row['id'] == self.b)
        self.assertTrue(row['archived'])
        start = ca.version(self.db)
        self.host.dispatch('conversation_delete', {'projectId': 'one', 'channelId': self.b, 'confirmation': 'DELETE ' + self.b})
        self.assertIn({'id': self.b, 'gone': True}, self.states(start)['threads'])

    def test_someone_elses_private_conversation_is_never_listed(self):
        with self.db:
            self.db.execute("UPDATE threads SET visibility='private', owner='someone-else' WHERE id=?", (self.b,))
        self.assertEqual([row['id'] for row in self.states(0)['threads']], [self.a])

    def test_deleting_archived_conversations_says_each_is_gone(self):
        self.host.dispatch('conversation_archive', {'projectId': 'one', 'channelId': self.a})
        start = ca.version(self.db)
        self.host.dispatch('conversations_delete_archived', {'projectId': 'one', 'channelIds': [self.a], 'confirmation': 'DELETE 1 ARCHIVED'})
        self.assertIn({'id': self.a, 'gone': True}, self.states(start)['threads'])

    def test_rows_found_before_an_operation_move_after_it(self):
        """A queued message's conversation is found from the queue (the request names only the item), and a project-wide
        operation finds its conversations before it runs, so one that deletes them still moves them."""
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_queue (id TEXT PRIMARY KEY, thread_id TEXT, client_id TEXT, document TEXT, created REAL)')
            self.db.execute("INSERT INTO coding_queue (id, thread_id, client_id, document) VALUES ('q1', ?, 'c', '{}')", (self.b,))
        self.assertEqual(thread_states.before_operation(self.db, 'coding_queue_cancel', {'queueId': 'q1'}), [self.b])
        self.assertEqual(set(thread_states.before_operation(self.db, 'project_delete', {'projectId': 'one'})), {self.a, self.b})
        start = ca.version(self.db)
        thread_states.after_operation(self.db, [self.b])
        self.assertEqual([row['id'] for row in self.states(start)['threads']], [self.b])

    def test_a_new_run_isnt_quiet_because_the_last_one_went_silent(self):
        clock = [time.time() - 1000]
        alive = thread_states.Alive(self.db, clock=lambda: clock[0])
        alive.heard(self.a)  # the last run, long ago
        self.save(task('t2', self.a, 'working'))
        row = next(r for r in thread_states.changes(self.db, ACTOR, 0, alive=alive)['threads'] if r['id'] == self.a)
        self.assertEqual(row['state'], 'working')

    def test_going_quiet_moves_the_row_once_so_the_window_hears_it(self):
        self.save(task('t1', self.a, 'working'))
        clock = [time.time()]
        alive = thread_states.Alive(self.db, clock=lambda: clock[0])
        alive.heard(self.a)
        before = ca.version(self.db)
        alive.sweep(now=clock[0] + 10)
        self.assertEqual(ca.version(self.db), before, 'not quiet yet')
        alive.sweep(now=clock[0] + thread_states.QUIET_SECONDS)
        moved = ca.version(self.db)
        self.assertGreater(moved, before)
        alive.sweep(now=clock[0] + thread_states.QUIET_SECONDS + 30)
        self.assertEqual(ca.version(self.db), moved, 'once')

    def test_a_thread_whose_last_run_ended_is_forgotten_so_it_never_moves_for_going_quiet(self):
        """Third review, Low 1: a finished run used to move its row 90 s after it ended, for nothing."""
        coding = self.host.coding_tasks()
        coding.alive_runs.update(owner=self.a, helper=self.a)
        coding.alive.heard(self.a)
        coding.turns['owner'] = object()
        try:
            coding.forget_ended()
        finally:
            del coding.turns['owner']
        self.assertIn(self.a, coding.alive.seen, 'the owner still runs after its helper ended')
        coding.forget_ended()
        self.assertEqual((coding.alive_runs, coding.alive.seen), ({}, {}))
        before = ca.version(self.db)
        coding.alive.sweep(now=time.time() + thread_states.QUIET_SECONDS + 1)
        self.assertEqual(ca.version(self.db), before)

    def test_a_restart_moves_every_running_row_so_a_lost_run_reads_unknown(self):
        self.save(task('t1', self.a, 'working'))
        start = ca.version(self.db)
        with self.db:
            self.db.execute("INSERT INTO session_marks VALUES ('start', ?)", (time.time() + 5,))
        thread_states.after_restart(self.db)
        rows = self.states(start)['threads']
        self.assertEqual([(row['id'], row['state']) for row in rows], [(self.a, 'unknown')])

    def test_auto_naming_moves_the_row(self):
        import conversation_organisation
        org = conversation_organisation.Organisation(self.host.store)
        start = ca.version(self.db)
        with self.db:
            org.write(self.a, {'name': 'Fix the login page'})
        rows = self.states(start)['threads']
        self.assertEqual([(row['id'], row['name']) for row in rows], [(self.a, 'Fix the login page')])

    def serve(self, requests):
        """Run the real request loop over these request lines and answer every line it wrote."""
        path = Path(self.temp.name) / 'requests'
        path.write_text(''.join(json.dumps(request) + '\n' for request in requests))
        out = io.StringIO()
        with open(path, 'rb', buffering=0) as stdin:
            serve(self.host, stdin, out)
        return [json.loads(line) for line in out.getvalue().splitlines() if line]

    def test_the_host_pushes_one_line_when_a_row_moves_and_none_when_nothing_did(self):
        """Updates arrive without polling: the window gets a line when the newest change moves, then reads once."""
        lines = self.serve([
            {'id': 1, 'operation': 'thread_states', 'args': {'since': 0}},  # an id: the bridge is listening
            {'id': 2, 'operation': 'conversation_dismiss', 'args': {'projectId': 'one', 'channelId': self.a}},  # moves A's row
            {'id': 3, 'operation': 'thread_states', 'args': {'since': 0}},  # moves nothing
            {'operation': '_shutdown', 'args': {}}])
        lines = [line for line in lines if 'heartbeat' not in line]
        order = [('event', line['event']['version']) if 'event' in line else ('reply', line['id']) for line in lines]
        first = lines[0]['result']['version']
        self.assertEqual(order, [('reply', 1), ('event', first), ('reply', 2), ('event', ca.version(self.db)), ('reply', 3)])
        self.assertGreater(ca.version(self.db), first)
        self.assertEqual(lines[1]['event'], {'kind': 'threads', 'version': first})

    def test_a_harness_that_sends_no_ids_never_sees_a_push(self):
        lines = self.serve([{'operation': 'conversation_dismiss', 'args': {'projectId': 'one', 'channelId': self.a}},
                            {'operation': '_shutdown', 'args': {}}])
        self.assertEqual([line for line in lines if 'event' in line or 'heartbeat' in line], [])

if __name__ == '__main__':
    unittest.main()
