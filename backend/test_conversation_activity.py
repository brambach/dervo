"""Sidebar status per conversation: running, needs you, unread. Disposable stores only; no providers."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from host import Host
import conversation_activity as ca


def task(tid, thread, state, **extra):
    return {'id': tid, 'state': state, 'context': {'thread_id': thread, 'project_id': 'one'}, 'runs': [{'id': 'r1', 'state': 'running'}],
            'tokens': [], 'messages': [], **extra}


class StatusTests(unittest.TestCase):
    def test_status_and_step_come_from_the_record(self):
        self.assertEqual(ca.status_of(task('a', 't', 'working')), 'running')
        self.assertEqual(ca.status_of(task('a', 't', 'completed', collaboration={'review': {'phase': 'initial'}})), 'running')
        for state in ('blocked', 'disconnected', 'needs_permission', 'ready_for_review'):
            self.assertEqual(ca.status_of(task('a', 't', state)), 'needs', state)
        self.assertEqual(ca.status_of(task('a', 't', 'completed')), 'settled')
        self.assertEqual(ca.status_of(task('a', 't', 'completed', runs=[{'id': 'r1', 'state': 'uncertain'}])), 'needs')
        live = task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_write_file', 'state': 'executing', 'path': '/secret/src/app.ts'},
                                                  {'run_id': 'old', 'tool': 'dervo_read_file', 'state': 'executing', 'path': 'x.py'}])
        self.assertEqual(ca.step_of(live), 'Editing app.ts')
        self.assertIsNone(ca.step_of(task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'shell', 'state': 'executing'}])))
        self.assertIsNone(ca.step_of(task('a', 't', 'completed')))
        # Bryce, 17 Sep: the sidebar said Starting long after the run had read files, because nothing was running that instant.
        self.assertIsNone(ca.step_of(task('a', 't', 'working')), 'no step yet: the sidebar says Starting')
        # dd #8: while the first message waits for connectors, the thread says so; once it's working, its steps win.
        waiting = task('a', 't', 'working', runs=[{'id': 'r1', 'state': 'sending', 'connectors': {'state': 'connecting', 'pending': ['claude.ai Slashy'], 'failed': []}}])
        self.assertEqual(ca.step_of(waiting), 'Connecting connectors')
        self.assertIsNone(ca.step_of({**waiting, 'runs': [{'id': 'r1', 'state': 'running', 'connectors': {'state': 'ready', 'pending': [], 'failed': []}}]}))
        self.assertEqual(ca.step_of({**waiting, 'tokens': [{'run_id': 'r1', 'tool': 'dervo_read_file', 'state': 'delivered', 'path': 'a.py'}]}), 'Working')
        between = task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_read_file', 'state': 'delivered', 'path': 'a.py'}])
        self.assertEqual(ca.step_of(between), 'Working')
        # Full access: Claude Code's own tools read as steps too, with its own short description of a command.
        native = task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'native:Bash', 'state': 'running', 'command': 'Run  the\ntests'},
                                                    ])
        self.assertEqual(ca.step_of(native), 'Running Run the tests')
        self.assertEqual(ca.step_of(task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'native:Edit', 'state': 'running', 'path': 'src/app.ts'}])), 'Editing app.ts')
        self.assertIsNone(ca.step_of(task('a', 't', 'working', tokens=[{'run_id': 'old', 'tool': 'dervo_read_file', 'state': 'delivered'}])), 'an earlier run doesn\'t count')


    def test_the_brief_is_the_current_runs_own_report_and_only_while_it_runs(self):
        guide = {'run_id': 'r1', 'goal': 'Archive channels', 'why': 'The sidebar has no\narchive action.', 'next': 'Add the menu item', 'step': 's'}
        running = task('a', 't', 'working', runs=[{'id': 'r1', 'state': 'running', 'guide': guide}],
                       messages=[{'role': 'user', 'text': 'first'}, {'role': 'assistant', 'text': 'x'}, {'role': 'user', 'text': 'Let me archive channels'}])
        self.assertEqual(ca.brief_of(running), ('Archive channels', 'The sidebar has no archive action.', 'Add the menu item', 'Let me archive channels'))
        older = task('a', 't', 'working', runs=[{'id': 'r0', 'state': 'failed', 'guide': dict(guide, run_id='r0')}, {'id': 'r1', 'state': 'running', 'guide': dict(guide, run_id='r0')}])
        self.assertEqual(ca.brief_of(older), (None, None, None, None), "an earlier run's plan can't explain a retry")
        self.assertEqual(ca.brief_of(task('a', 't', 'completed', runs=[{'id': 'r1', 'state': 'completed', 'guide': guide}])), (None, None, None, None), 'a settled task has no live brief')
        long = ca.brief_of(task('a', 't', 'working', runs=[{'id': 'r1', 'state': 'running', 'guide': dict(guide, why='w' * 600)}]))
        self.assertEqual(len(long[1]), 240)


class ClosingQuestionTests(unittest.TestCase):
    """Auto-sorting #5: an answer that ends on a real question for you reads 'asking'. Every other row is a control."""
    ASKING = [
        'Should I also update the tests?',
        'Migrated the settings rows. Should I also migrate the old rows, or leave them?',
        'Done.\n\nDo you want the old rows kept?',
        'I found two configs. Which one do you use in production?',
        'Here is the diff:\n```\nx = 1\n```\n\nShould I apply it to `main.py` too?',
        '**Should I also update the tests?**',
        # Hard cases, 21 Sep: a question put to you with no "you" in it (a yes-or-no opening, or a choice), and a
        # question followed by what the agent did for now, pending your word.
        'Changed greeting.py to return Hi, but test_sample expects Hello with the name. Should the greeting be Hi or Hello?',
        'Apple Pay is wired on checkout. Should Apple Pay also show on the cart page?',
        'Two configs are read at startup. Hi or Hello for the default greeting?',
        'Should I migrate the old rows too, or leave them? For now I migrated only the new rows and left the old table alone.',
        'Do you want the old rows kept? I have not touched them so far.',
    ]
    NOT_ASKING = [
        'Migrated the rows. The query was:\n```sql\nSELECT * FROM rows WHERE old = ?\n```',  # the question is code
        'Migrated the rows.\n```\nShould I also update the tests?',  # ends inside an unclosed fence
        'The error said "Should I retry the upload?"',  # quoted
        'The prompt reads:\n\n> Do you want to overwrite the file?',  # a block quote
        'Why does this fail? Because the path is relative, so I made it absolute.',  # rhetorical, answered
        'Why does this fail?',  # a question, but not put to you
        'What could go wrong here?',  # thinking aloud: a wh-question with no "you" and no choice
        'Should I also update the tests? I went ahead and did it anyway.',  # answered by the agent, not provisional
        'Migrated the settings rows. Keep the old rows? I kept them, since nothing reads the new table yet.',  # eval s44: asked and answered itself
        'Should I also update the tests?\n\nI updated them anyway.',  # not the last paragraph
        'Should I migrate the old rows too? For now I did. Then I ran the suite. Everything passed and the old table is dropped.',  # too much after it
        'Migrated the settings rows and checked them. Want me to add a test for this too?',  # stock offer
        'Anything else?',
        'Would you like me to open a pull request?',
        'Does that help?',
        'Fixed it. Run `git status` or `are you sure?` to see.',  # inline code
        'Updated the tests.',
        '',
        None,
    ]

    def test_only_a_real_closing_question_counts(self):
        for text in self.ASKING:
            with self.subTest(text=text):
                self.assertTrue(ca.closing_question(text), text)
        for text in self.NOT_ASKING:
            with self.subTest(text=text):
                self.assertIsNone(ca.closing_question(text))
        self.assertEqual(ca.closing_question('Migrated the rows. Should I also migrate the old rows, or leave them?'),
                         'Should I also migrate the old rows, or leave them?')
        self.assertEqual(ca.closing_question('Should I migrate the old rows too, or leave them? For now I migrated only the new rows.'),
                         'Should I migrate the old rows too, or leave them?', 'the question before the for-now sentence is the one reported')

    def test_an_answered_task_ending_on_a_question_needs_you_and_reads_asking(self):
        said = lambda text: [{'role': 'user', 'text': 'Migrate the settings rows'}, {'role': 'assistant', 'text': text}]
        asking = task('a', 't', 'answered', messages=said('Migrated them. Should I also update the tests?'))
        self.assertEqual((ca.status_of(asking), ca.end_of(asking)), ('needs', 'asking'))
        done = task('a', 't', 'completed', messages=said('Migrated them. Should I also update the tests?'))
        self.assertEqual(ca.end_of(done), 'asking')
        # Negative controls: files changed (that's review), a stock offer, and you've spoken since.
        changed = dict(done, completion={'changed_files': ['rows.py']})
        self.assertEqual((ca.status_of(changed), ca.end_of(changed)), ('settled', 'completed'))
        self.assertEqual(ca.end_of({'state': 'idle', 'review': 'approved', 'runs': []}), 'approved', 'an approved task says so, not idle')
        self.assertEqual(ca.end_of({'state': 'idle', 'runs': []}), 'idle')
        offer = task('a', 't', 'answered', messages=said('Migrated them. Want me to add a test too?'))
        self.assertEqual((ca.status_of(offer), ca.end_of(offer)), ('settled', 'answered'))
        replied = task('a', 't', 'answered', messages=said('Should I also update the tests?') + [{'role': 'user', 'text': 'yes'}])
        self.assertEqual(ca.end_of(replied), 'answered')
        stopped = task('a', 't', 'blocked', runs=[{'id': 'r1', 'state': 'interrupted'}], messages=said('Should I also update the tests?'))
        self.assertEqual(ca.end_of(stopped), 'stopped')


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'source').mkdir()
        self.host = Host(root / 'data'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', root / 'source', 'account', 'test-model')
        self.a = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'A', 'private': False})['id']
        self.b = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'B', 'private': False})['id']
        from coding_integration import CodingTasks
        if not hasattr(self.host, 'coding'):
            self.host.coding = CodingTasks(self.host)

    def activity(self):
        return {c['id']: c['activity'] for c in self.host.dispatch('conversations', {'projectId': 'one'})}

    def reopen(self):
        root = Path(self.host.root)
        self.host.close()
        self.host = Host(root)
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)

    def test_conversations_carry_the_running_agents_goal_why_and_next(self):
        guide = {'run_id': 'r1', 'goal': 'Archive channels', 'why': 'No archive action yet', 'next': 'Add the menu item'}
        self.host.coding.save(task('t1', self.a, 'working', runs=[{'id': 'r1', 'state': 'running', 'guide': guide}], messages=[{'role': 'user', 'text': 'Archive please'}]))
        self.host.coding.save(task('t2', self.b, 'completed', runs=[{'id': 'r1', 'state': 'completed', 'guide': guide}]))
        live = self.activity()
        self.assertEqual({k: live[self.a][k] for k in ('goal', 'why', 'next', 'request')}, {'goal': 'Archive channels', 'why': 'No archive action yet', 'next': 'Add the menu item', 'request': 'Archive please'})
        self.assertIsNotNone(live[self.a]['since'])
        self.assertEqual({k: live[self.b][k] for k in ('goal', 'why', 'next', 'since')}, {'goal': None, 'why': None, 'next': None, 'since': None}, 'settled work shows no live brief')
        # A new report updates the index without a status change.
        self.host.coding.save(task('t1', self.a, 'working', runs=[{'id': 'r1', 'state': 'running', 'guide': dict(guide, next='Run the checks')}], messages=[{'role': 'user', 'text': 'Archive please'}]))
        self.assertEqual(self.activity()[self.a]['next'], 'Run the checks')

    def test_needs_you_clears_when_you_move_on_open_it_or_dismiss_it(self):
        """Bryce, 17 Sep: "notifications are working but no way to clear them"."""
        self.host.coding.save(task('t1', self.a, 'disconnected'))
        first = self.activity()[self.a]
        self.assertEqual((first['status'], first['attention']), ('needs', True))
        # Opening the conversation clears the sidebar mark; Catch up still lists it until you act.
        time.sleep(.01)
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.a})
        opened = self.activity()[self.a]
        self.assertEqual((opened['status'], opened['attention'], opened['unread']), ('needs', False, 0))
        # A newer message in the same conversation moves on from the stopped task.
        time.sleep(.01)
        self.host.coding.save(task('t2', self.a, 'working'))
        self.assertEqual(self.activity()[self.a]['status'], 'running')
        time.sleep(.01)
        self.host.coding.save(task('t2', self.a, 'answered'))
        self.assertIsNone(self.activity()[self.a]['status'], 'the stopped task no longer needs you')
        # Dismiss clears a need in B until something new happens there.
        self.host.coding.save(task('t3', self.b, 'blocked'))
        self.assertEqual(self.activity()[self.b]['status'], 'needs')
        time.sleep(.01)
        self.assertEqual(self.host.dispatch('conversation_dismiss', {'projectId': 'one', 'channelId': self.b}), {'id': self.b})
        dismissed = self.activity()[self.b]
        self.assertEqual((dismissed['status'], dismissed['attention'], dismissed['unread']), (None, False, 0))
        self.assertEqual(self.host.dispatch('session_summary', {})['needs'], [])
        time.sleep(.01)
        self.host.coding.save(task('t4', self.b, 'blocked'))
        self.assertEqual(self.activity()[self.b]['status'], 'needs', 'a new need after Dismiss shows again')
        with self.assertRaises(Exception):
            self.host.dispatch('conversation_dismiss', {'projectId': 'one', 'channelId': 'not-a-conversation'})

    def test_an_older_dervo_can_still_write_the_activity_index_after_this_one_ran(self):
        # 17 Sep review: the update keeps the previous app to go back to, and that app inserts five columns by position.
        self.activity()
        columns = [row[1] for row in self.host.store.db.execute('PRAGMA table_info(conversation_task_state)')]
        self.assertEqual(columns, ['task_id', 'thread_id', 'status', 'step', 'changed'])
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)', ('old', self.a, 'settled', None, 1.0))

    def test_the_session_summary_names_unopened_results_and_what_needs_you_and_never_someone_elses_private_work(self):
        """Bryce's 17 September decision: a good-morning note from the activity index. A result stays news until its
        conversation is opened; a private conversation of someone else is never named or counted."""
        self.assertEqual(self.host.dispatch('session_summary', {}), {'since': None, 'finished': [], 'needs': []}, 'no earlier close, nothing saved')
        self.host.dispatch('project_rename', {'projectId': 'one', 'name': 'Dervo'})
        hidden = 'hidden'
        self.host.store.add_thread(hidden, 'one', hidden, 'private', 'someone-else')
        self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (hidden, 'Theirs')); self.host.store.db.commit()
        self.host.coding.save(task('t0', self.a, 'completed'))
        time.sleep(.01)
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.a})  # you read that one
        self.reopen()
        since = ca.last_session_end(self.host.store.db)
        self.assertIsNotNone(since)
        self.host.coding.save(task('t1', self.a, 'completed'))
        self.host.coding.save(task('t2', self.a, 'answered'))
        self.host.coding.save(task('t3', self.b, 'blocked'))
        self.host.coding.save(task('t4', hidden, 'completed'))
        self.host.coding.save(task('t5', self.b, 'working'))  # still running at the close: disconnected at the next start, so it needs you
        self.reopen()
        summary = self.host.dispatch('session_summary', {})
        self.assertEqual(summary['since'], ca.last_session_end(self.host.store.db))
        self.assertGreater(summary['since'], since, 'the anchor is the most recent close')
        self.assertEqual([(f['thread_id'], f['name'], f['project'], f['count']) for f in summary['finished']], [(self.a, 'A', 'Dervo', 2)])
        # t3 was stopped and t5 came after it: only the newest task in a conversation can need you.
        self.assertEqual([(n['thread_id'], n['name'], n['count']) for n in summary['needs']], [(self.b, 'B', 1)])
        self.assertNotIn('Theirs', str(summary))
        # Opening A and settling B's task leaves nothing to say; a result read before it landed would still count, one that
        # landed before the read does not.
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.a})
        self.host.coding.save(task('t3', self.b, 'completed'))
        time.sleep(.01)
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.b})
        self.host.coding.save(task('t5', self.b, 'completed'))
        self.reopen()
        summary = self.host.dispatch('session_summary', {})
        self.assertEqual([(f['thread_id'], f['count']) for f in summary['finished']], [(self.b, 1)], 't5 landed after you last opened B, so it is news; t3 is not')
        self.assertEqual(summary['needs'], [])
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.b})
        self.assertEqual((self.host.dispatch('session_summary', {})['finished'], self.host.dispatch('session_summary', {})['needs']), ([], []))
        with self.host.store.db:
            self.host.store.db.execute("DELETE FROM session_marks WHERE kind='end' AND at=(SELECT max(at) FROM session_marks WHERE kind='end')")
        self.assertIsNotNone(ca.last_session_end(self.host.store.db), 'without a clean close the previous start bounds the window')


    def test_the_project_picker_counts_what_needs_you_and_what_runs_per_project(self):
        """Design sweep, 19 Sep: picker A says "1 needs you" and "1 running" beside each project. Needs you is Home's
        own count (shared, not opened since), running is any thread you can see, and archived work or someone else's
        private thread never counts."""
        root = Path(self.temp.name); (root / 'two').mkdir(); (root / 'gone').mkdir()
        self.host.store.add_project('two', root / 'two', 'account', 'test-model')
        self.host.store.add_project('gone', root / 'gone', 'account', 'test-model')
        c = self.host.dispatch('create_conversation', {'projectId': 'two', 'name': 'C', 'private': False})['id']
        mine = self.host.dispatch('create_conversation', {'projectId': 'two', 'name': 'Mine', 'private': True})['id']
        g = self.host.dispatch('create_conversation', {'projectId': 'gone', 'name': 'G', 'private': False})['id']
        hidden = 'hidden'
        self.host.store.add_thread(hidden, 'two', hidden, 'private', 'someone-else')
        self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (hidden, 'Theirs')); self.host.store.db.commit()
        self.assertEqual(self.host.dispatch('projects_activity', {}), {'one': {'needs': 0, 'running': 0}, 'two': {'needs': 0, 'running': 0},
                                                                         'gone': {'needs': 0, 'running': 0}})
        self.host.coding.save(task('t1', self.a, 'blocked'))
        self.host.coding.save(task('t2', self.b, 'working'))
        self.host.coding.save(task('t3', c, 'working'))
        self.host.coding.save(task('t4', mine, 'working'))
        self.host.coding.save(task('t6', hidden, 'working'))
        self.host.coding.save(task('t7', hidden, 'blocked'))
        self.host.coding.save(task('t8', g, 'blocked'))
        self.host.dispatch('project_archive', {'projectId': 'gone'})
        counts = self.host.dispatch('projects_activity', {})
        self.assertEqual(counts, {'one': {'needs': 1, 'running': 1}, 'two': {'needs': 0, 'running': 2}}, 'the archived project and their private thread are left out')
        # Opening the thread that needs you clears it from the count, as it clears Home's; an archived thread's need
        # isn't counted.
        d = self.host.dispatch('create_conversation', {'projectId': 'two', 'name': 'D', 'private': False})['id']
        self.host.coding.save(task('t9', d, 'blocked'))
        self.assertEqual(self.host.dispatch('projects_activity', {})['two'], {'needs': 1, 'running': 2})
        time.sleep(.01)
        self.host.dispatch('conversation_seen', {'projectId': 'one', 'channelId': self.a})
        self.host.dispatch('conversation_archive', {'projectId': 'two', 'channelId': d})
        self.assertEqual(self.host.dispatch('projects_activity', {}), {'one': {'needs': 0, 'running': 1}, 'two': {'needs': 0, 'running': 2}})
        with self.assertRaises(Exception):
            self.host.dispatch('projects_activity', {'projectId': 'one'})


class SummaryFixture(unittest.TestCase):
    """18 Sep: the project room shows a card per conversation, and a settled card had nothing honest to say. The
    sentence Catch up already writes is now recorded beside the status, so every screen says the same thing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.host = Host(Path(self.tmp.name)); self.addCleanup(self.host.close)
        self.db = self.host.store.db
        ca.schema(self.db)

    def save(self, record):
        with self.db:
            ca.record(self.db, record)
        return ca.summaries(self.db, ['t'])['t']


class SummaryTests(SummaryFixture):
    def test_a_settled_conversation_carries_one_sentence_and_a_running_one_carries_its_step(self):
        done = task('a', 't', 'completed', completion={'at': time.time(), 'changed_files': ['backend/host.py'], 'test_exit_status': 0},
                    messages=[{'role': 'user', 'text': 'Wire the ideas operations'}])
        row = self.save(done)
        self.assertTrue(row['summary'], 'a settled task says what it did')
        self.assertIsNone(row['step'], 'step belongs to a run that is still going')
        self.assertEqual(row['summary'], ca.summary_of(done), 'the stored sentence is the one the record produces')
        # A run in progress keeps its live step, and the summary moves with the newest task rather than sticking.
        running = task('b', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_write_file', 'state': 'executing', 'path': 'src/app.ts'}])
        row = self.save(running)
        self.assertEqual(row['step'], 'Editing app.ts')
        self.assertEqual(row['summary'], ca.summary_of(running))

    def test_the_newest_task_says_how_it_ended_so_the_sidebar_can_draw_it(self):
        # 19 Sep: a failed run and a finished one waiting for review both drew one "needs you" mark in the sidebar.
        self.assertEqual(self.save(task('a', 't', 'blocked', runs=[{'id': 'r1', 'state': 'failed'}]))['ended'], 'blocked')
        self.assertEqual(self.save(task('b', 't', 'ready_for_review', runs=[{'id': 'r1', 'state': 'completed'}]))['ended'], 'ready_for_review')
        # A result whose agent ended on a question is asking you, the same rule Home's pickup uses for its accent.
        asked = task('c', 't', 'ready_for_review', runs=[{'id': 'r1', 'state': 'completed', 'guide': {'run_id': 'r1', 'next': 'Should an archived idea keep its threads?'}}])
        self.assertEqual(self.save(asked)['ended'], 'asking')
        # The table is its own, so the five-column state table beta.7 writes by position is untouched.
        columns = [row[1] for row in self.db.execute('PRAGMA table_info(conversation_task_state)')]
        self.assertEqual(columns, ['task_id', 'thread_id', 'status', 'step', 'changed'])

    def test_tasks_saved_without_an_end_mark_are_filled_in(self):
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('old', json.dumps(task('old', 't', 'blocked', runs=[{'id': 'r1', 'state': 'failed'}]))))
            self.db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)', ('old', 't', 'needs', None, 1.0))
        ca.backfill(self.db)
        self.assertEqual(ca.summaries(self.db, ['t'])['t']['ended'], 'blocked')
        # 19 Sep review, finding 9: the backfill ran only on an empty table, so a task changed under beta.7 after the
        # upgrade (beta.7 writes the state table it knows and no end row) kept a stale mark until it was saved again.
        with self.db:
            self.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('under-beta7', json.dumps(task('under-beta7', 'u', 'ready_for_review', runs=[{'id': 'r1', 'state': 'completed'}]))))
            self.db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)', ('under-beta7', 'u', 'needs', None, 2.0))
        ca.backfill(self.db)
        self.assertEqual(ca.summaries(self.db, ['u'])['u']['ended'], 'ready_for_review')
        self.assertEqual(self.db.execute('SELECT count(*) FROM conversation_task_end').fetchone()[0], 2, 'the filled row was left alone')

    def test_an_approved_task_saved_as_idle_before_it_had_its_word_is_re_sorted_once(self):
        # 8 Oct polish: approving settles a task as idle, so its end was saved as 'idle' and the sidebar said Idle.
        approved = task('ok', 't', 'idle', runs=[{'id': 'r1', 'state': 'completed'}], review='approved')
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('ok', json.dumps(approved)))
            self.db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)', ('ok', 't', 'settled', None, 1.0))
            self.db.execute('INSERT INTO conversation_task_end VALUES (?,?)', ('ok', 'idle'))
            self.db.execute('CREATE TABLE IF NOT EXISTS conversation_task_end_version (version INTEGER NOT NULL)')
            self.db.execute('DELETE FROM conversation_task_end_version')
            self.db.execute('INSERT INTO conversation_task_end_version VALUES (2)')
        ca.backfill(self.db)
        self.assertEqual(ca.summaries(self.db, ['t'])['t']['ended'], 'approved')

    def test_a_run_dervo_lost_track_of_says_so_rather_than_claiming_an_outcome(self):
        lost = task('a', 't', 'disconnected', runs=[{'id': 'r1', 'state': 'uncertain'}])
        self.assertIn('unconfirmed', self.save(lost)['summary'].lower())

    def test_a_malformed_record_reads_as_unconfirmed_rather_than_as_a_clean_result(self):
        broken = task('a', 't', 'working'); broken['runs'] = 'not a list'
        self.assertIn('unconfirmed', self.save(broken)['summary'].lower())

    def test_the_index_still_records_status_when_the_sentence_itself_fails(self):
        # Negative control for the guard in summary_of. Without it, one raising summary would take the whole index
        # down inside the transaction that saves a task, and the sidebar would lose every status with it.
        import catchup_summary
        with patch.object(catchup_summary, 'task_summary', side_effect=TypeError('no sentence')):
            row = self.save(task('a', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_read_file', 'state': 'executing', 'path': 'x.py'}]))
        self.assertEqual((row['status'], row['step'], row['summary']), ('running', 'Reading x.py', None))


class HelperTests(SummaryFixture):
    """The helpers a conversation's newest task asked, for the room's cards: who, and where each ask stands."""

    def test_helpers_of_the_newest_task_show_with_plain_words_and_refusals_are_left_out(self):
        db = self.db
        db.execute('CREATE TABLE IF NOT EXISTS collab_packets (id TEXT PRIMARY KEY, owner_task_id TEXT NOT NULL, root_task_id TEXT NOT NULL, document TEXT NOT NULL)')
        self.save(task('old', 't', 'completed'))
        newest = task('new', 't', 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_request_help', 'state': 'running'}])
        for id, owner, doc in (('p0', 'old', dict(agent='fixer', status='returned')),
                               ('p1', 'new', dict(agent='fixer', role='worker', kind='work', status='running')),
                               ('p2', 'new', dict(agent='review-a05020d2dcdf', role='reviewer', kind='review', status='returned')),
                               ('p3', 'new', dict(agent='nobody', status='refused'))):
            with db:
                db.execute('INSERT INTO collab_packets VALUES (?,?,?,?)', (id, owner, owner, json.dumps(doc)))
        row = self.save(newest)
        self.assertEqual(row['helpers'], [dict(who='fixer', state='working'), dict(who='Independent review', state='answered')])

    def test_no_packet_table_means_no_helpers(self):
        self.assertEqual(self.save(task('a', 't', 'working'))['helpers'], [])


class NewestAndStopTests(SummaryFixture):
    """Orchestrator map, 19 Sep 2026, F4 and F5: the newest task is the one saved last, and a Stop you pressed is not
    a need."""

    def test_the_newest_task_is_the_one_saved_last_even_when_an_older_one_moves(self):
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        def save(record):
            with self.db:
                self.db.execute('INSERT INTO coding_tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                                (record['id'], json.dumps(record)))
            return self.save(record)
        save(task('a', 't', 'ready_for_review', runs=[{'id': 'r1', 'state': 'completed'}]))
        time.sleep(.01)
        save(task('b', 't', 'ready_for_review', runs=[{'id': 'r1', 'state': 'completed'}]))
        time.sleep(.01)
        # A is approved after B landed: B still waits on you.
        row = save(task('a', 't', 'idle', runs=[{'id': 'r1', 'state': 'completed'}]))
        self.assertEqual((row['status'], row['ended']), ('needs', 'ready_for_review'), 'B is the newest task, whatever moved last')
        self.assertEqual(ca.session_summary(self.db, 'local-user')['needs'], [], 'no thread row for t in this fixture, so nothing is named')
        # Negative control: when the newest task itself settles, the need goes.
        time.sleep(.01)
        row = save(task('b', 't', 'idle', runs=[{'id': 'r1', 'state': 'completed'}]))
        self.assertEqual((row['status'], row['ended']), (None, 'idle'))

    def test_a_stop_you_pressed_is_settled_with_its_own_word_and_a_failure_still_needs_you(self):
        stopped = task('a', 't', 'blocked', runs=[{'id': 'r1', 'state': 'interrupted'}], error='Stopped before completion')
        self.assertTrue(ca.stopped_by_user(stopped))
        row = self.save(stopped)
        self.assertEqual((row['status'], row['ended'], row['unread']), (None, 'stopped', 1), 'your own stop needs nothing from you')
        self.assertEqual(ca.status_of(stopped), 'settled')
        by_error = task('c', 't', 'blocked', runs=[{'id': 'r1', 'state': 'failed'}], error='Stopped before completion It had changed a.py.')
        self.assertTrue(ca.stopped_by_user(by_error), "Claude Code's own stop sentence counts too")
        # Negative controls: a failed run, and an interrupted run whose outcome is uncertain, still need you.
        failed = task('b', 't', 'blocked', runs=[{'id': 'r1', 'state': 'failed'}], error='Claude Code was overloaded.')
        self.assertFalse(ca.stopped_by_user(failed))
        row = self.save(failed)
        self.assertEqual((row['status'], row['ended']), ('needs', 'blocked'))
        unsure = task('d', 't', 'blocked', runs=[{'id': 'r1', 'state': 'uncertain'}, {'id': 'r2', 'state': 'interrupted'}])
        self.assertEqual(ca.status_of(unsure), 'needs')


class RestartTests(unittest.TestCase):
    """Handoff map, 19 Sep 2026, H3 and H11: after a crash, a run the host died with is not "running", and "since you
    were last here" is the crashed session's start, not an older close."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'source').mkdir()
        self.host = Host(root / 'data'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', root / 'source', 'account', 'test-model')
        self.a = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'A', 'private': False})['id']
        self.db = self.host.store.db
        ca.schema(self.db)

    def record(self, record):
        with self.db:
            ca.record(self.db, record)

    def start_again(self):
        """What a relaunch after a crash writes: a new start mark and nothing else. The coding runtime, which would
        reconcile the record, is never constructed here (Home first, the way the app opens)."""
        time.sleep(.01)
        ca.mark_session(self.db, 'start')

    def test_a_run_the_host_died_with_reads_as_lost_not_running(self):
        running = task('t1', self.a, 'working', tokens=[{'run_id': 'r1', 'tool': 'dervo_write_file', 'state': 'executing', 'path': 'a.py'}])
        self.record(running)
        alive = ca.summaries(self.db, [self.a])[self.a]
        self.assertEqual((alive['running'], alive['lost'], alive['step']), (True, False, 'Editing a.py'), 'this Dervo started it: running')
        self.assertEqual(ca.project_counts(self.db, 'local-user')['one'], {'needs': 0, 'running': 1})
        self.start_again()
        lost = ca.summaries(self.db, [self.a])[self.a]
        self.assertEqual((lost['running'], lost['lost'], lost['status'], lost['ended'], lost['step']), (False, True, 'needs', 'disconnected', None))
        self.assertEqual(lost['summary'], ca.LOST_LINE)
        self.assertTrue(lost['attention'])
        self.assertEqual(ca.project_counts(self.db, 'local-user')['one'], {'needs': 1, 'running': 0})
        summary = ca.session_summary(self.db, 'local-user')
        self.assertEqual([n['thread_id'] for n in summary['needs']], [self.a], 'the good-morning note says it stopped')
        self.assertEqual(summary['finished'], [])
        # The record itself is untouched: reconciling it is the coding runtime's job, on its first use.
        self.assertEqual(self.db.execute('SELECT status FROM conversation_task_state WHERE task_id=?', ('t1',)).fetchone()[0], 'running')
        # Negative control: a run started after this Dervo did is running, and a settled row is never "lost".
        self.record(task('t2', self.a, 'working'))
        again = ca.summaries(self.db, [self.a])[self.a]
        self.assertEqual((again['running'], again['lost']), (True, False))
        self.record(task('t2', self.a, 'completed'))
        self.start_again()
        settled = ca.summaries(self.db, [self.a])[self.a]
        self.assertEqual((settled['lost'], settled['status']), (False, None))

    def test_after_a_crash_since_is_the_crashed_sessions_start_not_an_older_close(self):
        now = time.time()
        with self.db:
            self.db.execute('DELETE FROM session_marks')
            for kind, at in (('start', now - 30 * 3600), ('end', now - 26 * 3600), ('start', now - 9 * 3600), ('start', now - 60)):
                self.db.execute('INSERT INTO session_marks VALUES (?,?)', (kind, at))
        self.assertEqual(ca.last_session_end(self.db), now - 9 * 3600, 'the session that crashed began 9 hours ago; the clean close before it is not "last time"')
        # Negative control: a clean close after that start is what "since" means.
        with self.db:
            self.db.execute('INSERT INTO session_marks VALUES (?,?)', ('end', now - 2 * 3600))
        self.assertEqual(ca.last_session_end(self.db), now - 2 * 3600)
        with self.db:
            self.db.execute('DELETE FROM session_marks')
            self.db.execute('INSERT INTO session_marks VALUES (?,?)', ('start', now))
        self.assertIsNone(ca.last_session_end(self.db), 'the first session has nothing to compare with')
