"""Beta.17.1: a run shows a failure only when the work failed (run_ending), and run limits are per project (run_limits).

Bryce, 5 Oct 2026: "too many times a 'X' appears when it shouldn't". Each test is one way a run ends, built as the record
the coordinator saves for it (coding_integration's terminal handler, the restart pass, native_evidence.finish)."""
import sqlite3
import unittest

import run_ending
import run_limits
from claude_coding import HOST_STOPS, stop_text
from task_evidence import AttemptLimit, ATTEMPT_LIMIT_LEAD, TaskEvidence


def task(state, error=None, run=None, **extra):
    run = {'id': 'run-1', 'state': 'failed', **(run or {})}
    return {'id': 'task-1', 'state': state, 'error': error, 'runs': [run], 'tokens': [], 'messages': [],
            'binding': {'provider': 'claude'}, 'context': {'thread_id': 't', 'project_id': 'p'}, **extra}


class Classify(unittest.TestCase):
    def kind(self, record):
        ending = run_ending.classify(record)
        return ending and ending['kind']

    def test_ordinary_ends_and_running_work_have_no_ending(self):
        for state in ('working', 'needs_permission', 'ready_for_review', 'completed', 'answered', 'idle'):
            self.assertIsNone(run_ending.classify(task(state)), state)

    def test_a_failure_the_provider_reported_is_failed(self):
        """(a) The agent or its provider reported an error: the one ending that's a failure."""
        self.assertEqual(self.kind(task('blocked', 'Claude Code reported an error: tool crashed')), 'failed')
        self.assertEqual(self.kind(task('blocked', 'API Error: 500', run={'provider_failure': 'server_error'})), 'failed')

    def test_a_finished_run_whose_later_dervo_step_failed_is_finished(self):
        """(b) The agent finished (its run completed) and recording or checking afterwards didn't complete."""
        cases = {'record': task('blocked', 'This run has no git starting point recorded, so Dervo can’t say what it changed.', run={'state': 'completed'}),
                 'checks': task('blocked', 'Tests were not run to completion for the current file attempt.', run={'state': 'completed'}),
                 'changed': task('blocked', 'Files this task covers changed after its last check, so that check no longer stands.', run={'state': 'completed'}),
                 'nothing': task('blocked', 'No file attempt or verification was recorded for this run. Tests were not run.', run={'state': 'completed'})}
        unrecorded = task('blocked', 'Dervo couldn’t record 2 files.', run={'state': 'completed'}, unrecorded=[{'path': 'a'}])
        for snag, record in {**cases, 'record': unrecorded}.items():
            ending = run_ending.classify(record)
            self.assertEqual((ending['kind'], ending['snag']), ('finished', snag), snag)

    def test_a_run_dervo_lost_is_unknown_with_what_was_last_seen(self):
        """(c) A restart, a lost process or Dervo's own error: nobody can say how it ended."""
        lost = task('disconnected', 'Dervo closed while this was running, so it can’t say how it ended.',
                    run={'state': 'uncertain', 'guide': {'run_id': 'run-1', 'step': 'Running the backend tests'}, 'last_seen': 100.0})
        lost['tokens'] = [{'run_id': 'run-1', 'started_at': 90.0, 'ended_at': 140.5}, {'run_id': 'other', 'ended_at': 999.0}]
        ending = run_ending.classify(lost)
        self.assertEqual((ending['kind'], ending['step'], ending['last_seen']), ('unknown', 'Running the backend tests', 140.5))
        self.assertEqual(self.kind(task('disconnected', 'Dervo hit an error while following this run (KeyError)', run={'state': 'uncertain'})), 'unknown')

    def test_your_stop_and_dervo_quitting_are_stopped(self):
        self.assertEqual(self.kind(task('blocked', 'Stopped before completion', run={'state': 'interrupted'})), 'stopped')
        quit = run_ending.classify(task('disconnected', 'Dervo quit mid-run and paused it before it could say how far it got.', run={'state': 'uncertain'}))
        self.assertEqual((quit['kind'], quit['by']), ('stopped', 'quit'))
        early = run_ending.classify(task('disconnected', 'Stopped before provider acknowledgement', run={'state': 'uncertain'}))
        self.assertEqual((early['kind'], early['confirmed']), ('stopped', False))
        # A CLI that ignored your Stop and was ended after 10 seconds: still your stop, not a limit or a failure.
        ignored = task('blocked', HOST_STOPS['stop'], run={'end_cause': 'host_limit', 'limit': 'stop'})
        self.assertEqual(self.kind(ignored), 'stopped')

    def test_each_limit_is_named(self):
        """(d) Silence, run length, output size, attempts and the provider's usage limit."""
        idle = task('blocked', stop_text('idle', 3600), run={'end_cause': 'host_limit', 'limit': 'idle'})
        hard = task('blocked', stop_text('hard', 86400), run={'end_cause': 'host_limit', 'limit': 'hard'})
        output = task('blocked', 'The agent sent more than 1 GiB of output, so Dervo stopped the run.', run={'end_cause': 'host_limit', 'limit': 'output'})
        attempts = task('blocked', str(AttemptLimit(64)), run={'state': 'completed'})
        attempts_hit = task('blocked', 'Tests were not run to completion for the current file attempt.', run={'state': 'completed', 'limit_hit': 'attempts'})
        usage = task('blocked', 'You’ve hit your limit · resets 3pm', run={'provider_failure': 'usage_limit'})
        for record, limit in ((idle, 'idle'), (hard, 'hard'), (output, 'output'), (attempts, 'attempts'), (attempts_hit, 'attempts'), (usage, 'provider')):
            ending = run_ending.classify(record)
            self.assertEqual((ending['kind'], ending['limit']), ('limit', limit), limit)

    def test_records_from_before_17_1_read_the_same_way(self):
        """Up to beta.17 the run had end_cause but no limit, and the lines were fixed."""
        old_idle = task('blocked', 'Claude Code sent nothing for 30 minutes, so Dervo stopped the run.', run={'end_cause': 'host_limit'})
        old_hard = task('blocked', 'The run reached Dervo’s 6-hour limit, so Dervo stopped it.', run={'end_cause': 'host_limit'})
        self.assertEqual(run_ending.classify(old_idle)['limit'], 'idle')
        self.assertEqual(run_ending.classify(old_hard)['limit'], 'hard')
        self.assertEqual(self.kind(task('blocked', 'Dervo closed before this started. Nothing was sent. Send it again.', run={'end_cause': 'host_restart'})), 'not_started')
        self.assertEqual(self.kind(task('blocked', 'Host restarted before this request started. Nothing was sent. Send it again.', run={})), 'not_started')

    def test_a_run_that_never_got_its_task_is_not_started(self):
        self.assertEqual(self.kind(task('blocked', 'Claude Code didn’t start within 2 minutes.', run={'end_cause': 'not_started'})), 'not_started')

    def test_glyphs_never_draw_a_failure_for_anything_but_failed(self):
        for ending in ({'kind': 'finished'}, {'kind': 'unknown'}, {'kind': 'stopped'}, {'kind': 'limit', 'limit': 'idle'}, {'kind': 'not_started'}):
            glyph, words = run_ending.glyph_words(ending)
            self.assertNotEqual(glyph, 'failed', ending)
            self.assertTrue(words)
        self.assertIsNone(run_ending.glyph_words({'kind': 'failed'}))
        self.assertEqual(run_ending.limit_words({'limit': 'idle'}, 7200), 'Stopped after 2 hours with no output')


class Readers(unittest.TestCase):
    """The thread feed, pickup (Home), the sidebar's end word and History all read the same ending."""
    def test_the_thread_feed_carries_the_ending(self):
        from conversation_feed import trimmed
        record = task('blocked', stop_text('idle', 7200), run={'end_cause': 'host_limit', 'limit': 'idle', 'baseline': {'x': 1}})
        shown = trimmed(record)
        self.assertEqual(shown['ending']['kind'], 'limit')
        self.assertNotIn('baseline', shown['runs'][-1])
        self.assertIsNone(trimmed(task('completed'))['ending'])

    def test_pickup_marks_only_failures_as_failed(self):
        from pickup import thread_facts, told
        thread = {'id': 't', 'name': 'Thread'}
        limit = thread_facts(thread, task('blocked', stop_text('idle', 7200), run={'end_cause': 'host_limit', 'limit': 'idle'}), {}, None)
        self.assertEqual((limit['kind'], limit['failed'], limit['ending']), ('stopped', False, 'limit'))
        self.assertIn('sent nothing for 2 hours', limit['line'])
        failed = thread_facts(thread, task('blocked', 'Claude Code reported an error.'), {}, None)
        self.assertEqual((failed['failed'], failed['ending']), (True, 'failed'))
        unsent = thread_facts(thread, task('blocked', 'Claude Code didn’t start within 2 minutes.', run={'end_cause': 'not_started'}), {}, None)
        self.assertEqual((unsent['failed'], unsent['line'], told(unsent)), (False, 'Didn’t start. Nothing was sent.', 'didn’t start. Nothing was sent.'))
        snag = thread_facts(thread, task('blocked', 'Tests were not run to completion for the current file attempt.', run={'state': 'completed'}), {}, None)
        self.assertEqual((snag['failed'], snag['ending'], snag['line']), (False, 'finished', 'Finished. Tests were not run to completion for the current file attempt.'))
        self.assertTrue(told(snag).startswith('finished, but '))
        lost = thread_facts(thread, task('disconnected', 'Dervo hit an error while following this run (KeyError).', run={'state': 'uncertain'}), {}, None)
        self.assertEqual((lost['kind'], lost['failed'], lost['ending']), ('unconfirmed', False, 'unknown'))
        paused = thread_facts(thread, task('blocked', 'Stopped before completion', run={'state': 'interrupted'}), {}, None)
        self.assertEqual((paused['failed'], paused['ending'], told(paused)), (False, 'stopped', 'was stopped by you.'))

    def test_the_sidebar_end_word_is_blocked_only_for_failures(self):
        from conversation_activity import end_of
        self.assertEqual(end_of(task('blocked', 'Claude Code reported an error.')), 'blocked')
        self.assertEqual(end_of(task('blocked', stop_text('hard', 86400), run={'end_cause': 'host_limit', 'limit': 'hard'})), 'limit')
        self.assertEqual(end_of(task('blocked', 'x', run={'end_cause': 'not_started'})), 'not_started')
        self.assertEqual(end_of(task('blocked', 'Tests were not run to completion.', run={'state': 'completed'})), 'snag')

    def test_history_draws_no_failure_for_a_limit_or_a_lost_run(self):
        from run_history import _ended
        self.assertEqual(_ended(task('blocked', stop_text('idle', 7200), run={'end_cause': 'host_limit', 'limit': 'idle'}), 0)[0], 'stopped')
        self.assertEqual(_ended(task('blocked', 'Claude Code reported an error.'), 0)[0], 'failed')
        self.assertEqual(_ended(task('disconnected', 'Dervo closed while this was running.', run={'state': 'uncertain'}), 0)[0], 'unknown')
        usage = _ended(task('blocked', 'limit', run={'provider_failure': 'usage_limit'}), 0)
        self.assertEqual(usage, ('stopped', 'Stopped at Claude’s usage limit'))


class CatchUp(unittest.TestCase):
    """Catch up's saved-record rows (catchup_summary.task_facts, also behind search) and the sidebar's stored end words."""
    def test_rows_carry_the_ending(self):
        from catchup_summary import task_facts
        from task_outcome import outcome_facts
        for record, kind in ((task('blocked', 'Claude Code reported an error.'), 'failed'),
                             (task('blocked', stop_text('idle', 7200), run={'end_cause': 'host_limit', 'limit': 'idle'}), 'limit'),
                             (task('ready_for_review', None, run={'state': 'completed'}), None)):
            self.assertEqual(task_facts(record, outcome_facts(record))['ending'], kind)

    def test_end_words_saved_before_17_1_are_re_sorted_once(self):
        import json
        import conversation_activity as ca
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        ca.schema(db)
        db.execute('CREATE TABLE coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        records = {'limit': task('blocked', stop_text('idle', 7200), run={'end_cause': 'host_limit', 'limit': 'idle'}),
                   'failed': task('blocked', 'Claude Code reported an error.'),
                   'unsent': task('blocked', 'x', run={'end_cause': 'not_started'})}
        with db:
            for task_id, record in records.items():
                record['id'] = task_id
                db.execute('INSERT INTO coding_tasks VALUES (?, ?)', (task_id, json.dumps(record)))
                db.execute("INSERT INTO conversation_task_end VALUES (?, 'blocked')", (task_id,))  # as beta.17 saved them
        ca.backfill(db)
        ends = dict(db.execute('SELECT task_id, state FROM conversation_task_end'))
        self.assertEqual(ends, {'limit': 'limit', 'failed': 'blocked', 'unsent': 'not_started'})
        # Once only: a later 'blocked' row (a real failure saved since) isn't read again.
        with db:
            db.execute("UPDATE conversation_task_end SET state='blocked' WHERE task_id='limit'")
        ca.backfill(db)
        self.assertEqual(db.execute("SELECT state FROM conversation_task_end WHERE task_id='limit'").fetchone()[0], 'blocked')


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')


class Limits(unittest.TestCase):
    def test_defaults_choices_and_a_bad_value(self):
        from dervo_connections import Rejected
        db = Store().db
        self.addCleanup(db.close)
        self.assertEqual(run_limits.read(db, 'p'), {'idle_seconds': 7200, 'run_seconds': 86400, 'attempts': 64})
        view = run_limits.choose(db, 'p', {'idleSeconds': 14400, 'runSeconds': None, 'attempts': None}, 'me')
        self.assertEqual((view['idle_seconds'], view['run_seconds'], view['attempts']), (14400, None, None))
        self.assertEqual(view['warning'], run_limits.NO_ATTEMPT_LIMIT_WARNING)
        self.assertEqual(run_limits.read(db, 'other')['attempts'], 64, 'each project keeps its own')
        for bad in ({'idleSeconds': None}, {'idleSeconds': 60}, {'runSeconds': 1}, {'attempts': True}, {'attempts': 17}, {}):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                run_limits.choose(db, 'p', bad, 'me')
        self.assertEqual(run_limits.read(db, 'p')['idle_seconds'], 14400, 'a refused change leaves the limits as they were')

    def test_durations_read_plainly(self):
        self.assertEqual([run_limits.duration(s) for s in (1800, 3600, 7200, 86400, None)],
                         ['30 minutes', '1 hour', '2 hours', '24 hours', 'no limit'])

    def test_attempts_follow_the_project_limit_as_it_changes(self):
        store = Store()
        self.addCleanup(store.db.close)
        evidence = TaskEvidence(store)
        context = {'project_id': 'p', 'thread_id': 't', 'actor': 'me'}
        record = {'attempts': [{}] * 64}
        with self.assertRaises(AttemptLimit) as caught:
            evidence._room_for_attempt(record, context)
        self.assertTrue(str(caught.exception).startswith(ATTEMPT_LIMIT_LEAD + '64 of its attempts'))
        evidence._room_for_attempt({'attempts': [{}] * 63}, context)
        run_limits.choose(store.db, 'p', {'attempts': None}, 'me')
        evidence._room_for_attempt({'attempts': [{}] * 500}, context)  # no limit: the same task carries on
        run_limits.choose(store.db, 'p', {'attempts': 16}, 'me')
        with self.assertRaises(AttemptLimit):
            evidence._room_for_attempt({'attempts': [{}] * 16}, context)


if __name__ == '__main__':
    unittest.main()
