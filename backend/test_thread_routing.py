"""thread_routing: the gate on the replayable evals, plus the rules that must never bend.

The gate replays every committed suite (evals/routing) and fails when a change routes worse than the committed
baseline: fewer right, or more misroutes. When a change scores better, raise the baseline in the same commit
(python3 -m evals.routing.baseline) so the next change has to keep it.
"""
import json
import pathlib
import unittest

import thread_routing as tr
from evals.routing import cases, score

BASELINE = pathlib.Path(__file__).resolve().parent / 'evals' / 'routing' / 'baseline.json'
NOW = 1_800_000_000.0


def thread(tid, name, state='idle', **extra):
    return dict(id=tid, name=name, state=state, since=NOW - 600, at=NOW - 60, **extra)


class Gate(unittest.TestCase):
    def test_suites_hold_the_baseline(self):
        baseline = json.loads(BASELINE.read_text())
        results = score.run(tuple(baseline))
        for suite, wanted in baseline.items():
            got = results[suite]['score']
            with self.subTest(suite=suite):
                self.assertGreaterEqual(got['accuracy'], wanted['accuracy'] - 1e-9, f'{suite} routes fewer messages right')
                self.assertLessEqual(got['misroutes'], wanted['misroutes'] + 1e-9, f'{suite} misroutes more messages')
                self.assertGreaterEqual(got['naming'], wanted['naming'] - 1e-9, f'{suite} names new threads worse')
                self.assertLessEqual(got['auto_wrong'], wanted.get('auto_wrong', 1) + 1e-9,
                                     f'{suite} gets more silent routes wrong')

    def test_naming_sorting_and_cleanup_cases(self):
        for group, (passed, total, failures) in cases.run().items():
            with self.subTest(group=group):
                self.assertEqual(passed, total, '\n'.join(failures))

    def test_routing_stays_instant_with_200_threads(self):
        import time
        threads = [thread(f't{i}', f'Topic {i} work', request=f'do the topic {i} thing with widgets and gadgets {i}',
                          messages=[f'also handle case {j} of topic {i}' for j in range(5)]) for i in range(200)]
        tr.route('warm the caches', [], threads, NOW)
        began = time.perf_counter()
        for text in ('also make it blue', 'add csv export for invoices', 'how is topic 7 going?', 'fix topic 3 and also add a search box'):
            tr.route(text, [dict(text='x', at=NOW - 5, thread_id='t3')], threads, NOW)
        self.assertLess((time.perf_counter() - began) / 4, 0.05)


class Rules(unittest.TestCase):
    def test_follow_up_goes_where_the_last_message_went(self):
        threads = [thread('t1', 'Sidebar scroll position', 'working'), thread('t2', 'CSV export', 'working')]
        got = tr.route('also make it stick on resize', [dict(text='sidebar scroll jumps', at=NOW - 4, thread_id='t1')], threads, NOW)
        self.assertEqual((got['kind'], got['thread_id']), ('existing', 't1'))
        self.assertGreaterEqual(got['confidence'], tr.AUTO_ROUTE)

    def test_a_named_thread_wins_over_the_stream(self):
        threads = [thread('t1', 'Voice push to talk', 'working'), thread('t2', 'Onboarding welcome flow', 'working')]
        got = tr.route('tell onboarding to skip the folder step', [dict(text='x', at=NOW - 3, thread_id='t1')], threads, NOW)
        self.assertEqual(got['thread_id'], 't2')

    def test_new_work_starts_a_named_thread(self):
        got = tr.route('add csv export for invoices', [], [thread('t1', 'Fix login crash')], NOW)
        self.assertEqual(got['kind'], 'new')
        self.assertIn('export', got['name'].casefold())

    def test_new_name_never_repeats_an_existing_one(self):
        got = tr.route('new thread: csv export', [], [thread('t1', 'CSV export')], NOW)
        self.assertEqual(got['kind'], 'new')
        self.assertNotEqual(got['name'].casefold(), 'csv export')

    def test_home_answers_greetings_and_status(self):
        threads = [thread('t1', 'Fix login crash', 'working')]
        for text in ('thanks!', 'nice', "what's running?", 'anything waiting on me', "how's the login fix going"):
            with self.subTest(text=text):
                self.assertEqual(tr.route(text, [], threads, NOW)['kind'], 'home')
        self.assertEqual(tr.route("how's the login fix going", [], threads, NOW)['thread_id'], 't1')

    def test_one_open_question_takes_a_bare_answer(self):
        threads = [thread('t1', 'Database choice', 'needs_you', pending_question='Postgres or SQLite?'), thread('t2', 'CSV export', 'working')]
        got = tr.route('use postgres', [], threads, NOW)
        self.assertEqual(got['thread_id'], 't1')

    def test_two_asks_go_to_the_model(self):
        threads = [thread('t1', 'Fix login crash'), thread('t2', 'CSV export')]
        self.assertEqual(tr.route('fix the login crash on safari and also add a json export', [], threads, NOW)['kind'], 'unsure')

    def test_a_lean_with_nothing_before_it_goes_to_the_model(self):
        self.assertEqual(tr.route('make it bigger', [], [thread('t1', 'CSV export'), thread('t2', 'Sidebar')], NOW)['kind'], 'unsure')

    def test_decision_shape(self):
        got = tr.route('hello', [], [], NOW)
        self.assertEqual(set(got), {'kind', 'thread_id', 'name', 'confidence', 'why', 'candidates', 'archived', 'command'})
        self.assertIn(got['kind'], tr.KINDS)
        self.assertEqual(tr.route('', [], [], NOW)['kind'], 'home')

    def test_odd_input_never_raises(self):
        for text in (None, 42, '   ', '🎉', 'x' * 20000, '\x1b[31mred', 'sk-ant-abc123def456ghi789jkl'):
            tr.route(text, [None, dict(at='x')], [None, dict(), dict(id='t1')], NOW)
        tr.sort([None, dict(), dict(id='a', since='x')], NOW)
        tr.tidy([None, dict(id='a', at='x')], NOW)
        tr.name(dict(name=None, request=None))

    def test_a_locked_or_good_name_is_never_replaced(self):
        self.assertIsNone(tr.name(dict(name='a very long first message that someone typed in full…', nameLocked=True)))
        self.assertIsNone(tr.name(dict(name='CSV export', request='add csv export')))

    def test_tidy_never_touches_live_or_unread_threads(self):
        old = NOW - 90 * 86400
        keep = [dict(id=s, state=s, at=old) for s in ('needs_you', 'working', 'quiet', 'unknown')]
        keep += [dict(id='unread', state='finished', unread=True, at=old), dict(id='pinned', state='idle', pinned=True, at=old),
                 dict(id='question', state='idle', pending_question='?', at=old), dict(id='attention', state='finished', attention=True, at=old)]
        self.assertEqual(tr.tidy(keep, NOW), [])

    def test_tidy_keeps_what_quiet_threads_refuses(self):
        old = NOW - 90 * 86400
        threads = [dict(id='screen', state='idle', at=old), dict(id='agent', state='idle', agent=True, at=old),
                   dict(id='review', state='finished', changesWaiting=True, at=old), dict(id='away', state='idle', at=old)]
        self.assertEqual([row['thread_id'] for row in tr.tidy(threads, NOW, keep=['screen'])], ['away'])
        self.assertEqual(tr.tidy(threads, NOW, days=None), [])

    def test_gate_never_reads_local_corrections(self):
        from unittest import mock
        with mock.patch.object(cases, 'learned', side_effect=AssertionError('read learned_cases.json')):
            cases.run()

    def test_tidy_puts_away_at_most_a_batch(self):
        many = [dict(id=f't{i}', state='idle', at=NOW - (30 + i) * 86400) for i in range(40)]
        got = tr.tidy(many, NOW)
        self.assertEqual(len(got), tr.BATCH)
        self.assertEqual(got[0]['thread_id'], 't39')  # oldest first

    def test_a_key_never_reaches_a_name(self):
        taken = [thread('x', 'Rotate token')]
        got = tr.route('new thread: rotate sk-ant-api03-abcdefgh1234 token now', [], taken, NOW)
        self.assertNotIn('sk-', got['name'] or '')
        self.assertNotIn('sk-', (tr.name(dict(name='New conversation', request='rotate sk-ant-api03-abcdefgh1234 token now'),
                                         ['Rotate token']) or {}).get('title', ''))

    def test_hostile_text_stays_fast(self):
        import time
        for text in ('k' * 60 + 'x', 'kk ' * 24 + 'x', 'kk ' * 400 + 'x', 'ha ' * 300 + 'x', 'fix a' + ' ' * 8000 + 'and add b', 'thanks thanks ' + 'h' * 60 + 'm x', '\n'.join('- fix thing%d' % i for i in range(3000))):
            began = time.perf_counter()
            tr.route(text, [], [thread('t1', 'Fix login')], NOW)
            tr._parts(text * 3)
            self.assertLess(time.perf_counter() - began, 0.5)

    def test_thread_commands_go_to_home(self):
        threads = [thread('t1', 'Sidebar scroll', 'working'), thread('t2', 'Billing usage')]
        for text, action in (('delete the sidebar thread', 'delete'), ('stop it', 'stop'), ('push it', 'push'),
                             ('rename the billing thread to Usage meter', 'rename'), ('archive the billing one', 'archive')):
            with self.subTest(text=text):
                got = tr.route(text, [dict(text='x', at=NOW - 5, thread_id='t1')], threads, NOW)
                self.assertEqual((got['kind'], got['command']), ('home', action))
        self.assertIsNone(tr.route('stop it crashing on launch', [], threads, NOW)['command'])

    def test_a_lone_answer_is_never_swallowed(self):
        threads = [thread('t1', 'Billing plans', 'needs_you', pending_question='Monthly or yearly?')]
        self.assertEqual(tr.route('monthly is fine', [], threads, NOW)['thread_id'], 't1')

    def test_a_start_ask_mid_burst_is_new_work(self):
        threads = [thread('t1', 'Connector picker warning', 'working',
                          request='the warning in the connector picker is too quiet, make it stand out')]
        burst = [dict(text='the warning in the connector picker is too quiet, make it stand out', at=NOW - 5, thread_id='t1')]
        got = tr.route('start the docs page for connectors too', burst, threads, NOW)
        self.assertEqual(got['kind'], 'new')
        self.assertGreaterEqual(got['confidence'], tr.AUTO_ROUTE)
        self.assertIn('docs', got['name'].casefold())
        for text in ('start it again', 'start over', 'start on the warning colour', 'start a PR for this', 'start the fix',
                     'start the vite dev server', 'start writing the tests', 'start the'):
            with self.subTest(text=text):
                self.assertNotEqual(tr.route(text, burst, threads, NOW)['kind'], 'new')

    def test_a_bare_new_thread_gets_no_placeholder_name(self):
        got = tr.route('new thread', [dict(text='x', at=NOW - 5, thread_id='t1')], [thread('t1', 'Sidebar scroll')], NOW)
        self.assertEqual((got['kind'], got['name']), ('new', None))

    def test_a_clean_first_message_names_its_thread(self):
        got = tr.name(dict(id='t1', name='New conversation',
                           request='the warning in the connector picker is too quiet, make it stand out'))
        self.assertGreaterEqual(got['confidence'], tr.NAME_APPLY)

    def test_command_reasons_read_well(self):
        for text in ('archive it', 'approve it', 'pin this'):
            with self.subTest(text=text):
                why = tr.route(text, [], [thread('t1', 'Sidebar scroll')], NOW)['why']
                self.assertNotRegex(why, r'^A [aeiou]')


if __name__ == '__main__':
    unittest.main()
