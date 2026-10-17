"""Agent reminders (beta.16.1, item 1) with disposable hosts and a scripted turn only."""
import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dervo_connections import Rejected
import test_thread_fanout
import task_reminders as r


def day(offset=0):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


class ReminderTests(unittest.TestCase):
    def setUp(self):
        # The thread-fanout fixture's project, private thread and task, without running its own tests here.
        fixture = test_thread_fanout.ThreadFanoutTests('setUp')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.host, self.channel, self.task = fixture.host, fixture.channel, fixture.task
        self.task.update(state='working', session_id='fixture-session', reminder_session_id='fixture-session')
        self.task['runs'].append({'id': 'fixture-run', 'session_id': 'fixture-session', 'state': 'running', 'prior_attempts': 0})
        self.host.coding.save(self.task)
        self.host.coding.turns[self.task['id']] = SimpleNamespace(closed=False, stopping=False, steer=Mock(), stop=lambda: None, close=lambda: None)
        self.addCleanup(lambda: self.host.coding.turns.clear())
        self.token = {'run_id': 'fixture-run', 'session_id': 'fixture-session', 'token': 'fixture-token'}
        self.home = {'projectId': 'p', 'profileId': 'p'}

    def remind(self, key='trial', on=None, text='Check whether the Apple trial ended, then decide on the paid plan.'):
        return r.capture(self.host.coding, self.task, self.token, {'reminder_key': key, 'on': on or day(), 'text': text})

    def due(self):
        return self.host.dispatch('reminders', self.home)['due']

    def test_a_reminder_is_saved_for_its_thread_and_shows_on_home_on_its_day_only(self):
        today = self.remind()
        later = self.remind('later', day(3), 'Ask for the renewal quote.')
        self.assertIn('Dervo shows it on Home', today['detail'])
        shown = self.due()
        self.assertEqual([item['id'] for item in shown], [today['reminderId']], 'the later one waits for its day')
        item = shown[0]
        self.assertEqual((item['channelId'], item['taskId'], item['thread'], item['asker'], item['on'], item['notified']),
                         (self.channel, self.task['id'], 'Client tickets', 'Codex', day(), False))
        self.assertEqual(item['text'], 'Check whether the Apple trial ended, then decide on the paid plan.')
        # Three days on, both are due, oldest day first.
        future = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=3), datetime.time(9)).timestamp()
        both = r.dispatch(self.host, 'reminders', self.home, 'local-user', now=future)['due']
        self.assertEqual([item['id'] for item in both], [today['reminderId'], later['reminderId']])

    def test_a_reminder_never_starts_work_sends_anything_or_changes_access(self):
        before = self.host.coding.read(self.task['id'], self.task['context'])
        with patch.object(self.host.coding, 'start') as start, patch.object(self.host.coding, 'steer') as steer, \
                patch.object(self.host.coding.conversation, 'request') as request:
            saved = self.remind()
            self.host.dispatch('reminders', self.home)
            self.host.dispatch('reminders_due', {})
            self.host.dispatch('reminder_notified', {'reminderId': saved['reminderId']})
            self.host.dispatch('reminder_done', {**self.home, 'reminderId': saved['reminderId']})
        start.assert_not_called()
        steer.assert_not_called()
        request.assert_not_called()
        after = self.host.coding.read(self.task['id'], self.task['context'])
        self.assertEqual(after['messages'], before['messages'], 'no message in the thread, as you or as the agent')
        self.assertEqual(self.host.coding.conversation.queue.items(self.channel), [])
        self.assertEqual(after.get('full_access'), before.get('full_access'))

    def test_the_line_is_checked_and_kept_to_one_plain_line(self):
        cases = {'past': dict(on=day(-1)), 'too far': dict(on=day(400)), 'not a day': dict(on='next tuesday'),
                 'impossible day': dict(on='2026-02-30'), 'empty': dict(text='   '), 'long': dict(text='x' * 161),
                 'secret': dict(text='Rotate sk-ant-api03-' + 'A' * 90)}
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(Rejected):
                self.remind(**{'key': name.replace(' ', '-'), **change})
        with self.assertRaisesRegex(Rejected, 'only a reminder_key'):
            r.capture(self.host.coding, self.task, self.token, {'reminder_key': 'x', 'on': day(), 'text': 'Hi', 'start': True})
        saved = self.remind('lines', text='Check the trial.\n\nThen\tdecide.')
        self.assertEqual(self.due()[0]['text'], 'Check the trial. Then decide.')
        self.assertEqual(self.remind('lines', text='Check the trial.\n\nThen\tdecide.')['reminderId'], saved['reminderId'], 'a retry is the same reminder')
        with self.assertRaisesRegex(Rejected, 'different reminder'):
            self.remind('lines', text='Something else')

    def test_only_the_working_owners_current_run_can_save_one(self):
        with self.assertRaisesRegex(Rejected, 'current acknowledged run'):
            r.capture(self.host.coding, self.task, {**self.token, 'run_id': 'old-run'}, {'reminder_key': 'k', 'on': day(), 'text': 'Hi'})
        helper = dict(self.task, role='reviewer')
        with self.assertRaisesRegex(Rejected, 'conversation owner'):
            r.capture(self.host.coding, helper, self.token, {'reminder_key': 'k', 'on': day(), 'text': 'Hi'})
        self.task['state'] = 'answered'
        with self.assertRaisesRegex(Rejected, 'conversation owner'):
            self.remind()
        self.task['state'] = 'working'
        for index in range(r.MAX_PER_TASK):
            self.remind(f'k{index}')
        with self.assertRaisesRegex(Rejected, 'limit'):
            self.remind('one-more')

    def test_notified_once_and_done_takes_it_off_home(self):
        saved = self.remind()
        waiting = self.host.dispatch('reminders_due', {})['due']
        self.assertEqual([(item['id'], item['notified'], item['project']) for item in waiting], [(saved['reminderId'], False, 'project')])
        self.host.dispatch('reminder_notified', {'reminderId': saved['reminderId']})
        self.assertTrue(self.host.dispatch('reminders_due', {})['due'][0]['notified'])
        self.assertEqual(self.host.dispatch('reminder_done', {**self.home, 'reminderId': saved['reminderId']})['state'], 'done')
        self.assertEqual(self.due(), [])
        self.assertEqual(self.host.dispatch('reminders_due', {})['due'], [])
        for bad in ('../x', 'f' * 31, 42):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.host.dispatch('reminder_notified', {'reminderId': bad})
        with self.assertRaises(Rejected):
            self.host.dispatch('reminders', {'projectId': 'p', 'profileId': 'someone-else'})

    def test_a_private_thread_of_someone_else_and_an_archived_project_keep_theirs_hidden(self):
        saved = self.remind()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.channel,))
        self.assertEqual(self.host.dispatch('reminders_due', {})['due'], [])
        with self.assertRaisesRegex(Rejected, 'can’t see'):
            self.host.dispatch('reminder_notified', {'reminderId': saved['reminderId']})
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner='local-user' WHERE id=?", (self.channel,))
            self.host.store.db.execute('INSERT INTO project_archives VALUES (?,?)', ('p', 1))
        self.assertEqual(self.host.dispatch('reminders_due', {})['due'], [])

    def test_deleting_the_thread_deletes_its_reminders(self):
        self.remind()
        import project_lifecycle
        with self.host.store.db:
            project_lifecycle.delete_rows(self.host.store.db, threads={self.channel})
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM task_reminders').fetchone()[0], 0)

    def test_the_tool_is_offered_to_the_owner_with_its_instructions(self):
        from coding_integration import during_task
        from codex_coding import REMINDER_TOOL, dynamic_tools, validate_arguments
        tools = self.host.coding.tools_for(self.task)
        self.assertIn(REMINDER_TOOL, tools)
        self.assertIn('dervo_remind_user', during_task(tools))
        self.assertNotIn(REMINDER_TOOL, self.host.coding.tools_for(dict(self.task, role='reviewer')))
        # A Codex session started before reminders can't take a new tool, so it isn't offered there.
        self.assertNotIn(REMINDER_TOOL, self.host.coding.tools_for(dict(self.task, session_id='older-session')))
        spec = next(tool for tool in dynamic_tools([REMINDER_TOOL]) if tool['name'] == REMINDER_TOOL)
        self.assertEqual(set(spec['inputSchema']['properties']), {'reminder_key', 'on', 'text'})
        validate_arguments(REMINDER_TOOL, {'reminder_key': 'k', 'on': day(), 'text': 'Hi'})
        with self.assertRaises(Rejected):
            validate_arguments(REMINDER_TOOL, {'reminder_key': 'k', 'on': day(), 'text': 'Hi', 'grant': 'shell'})

    # ---------------------------------------------------------------- yours, from Home's "Remind me tomorrow" (9 Oct)
    def add(self, on=None, text='Answer its question', **extra):
        return self.host.dispatch('reminder_add', {**self.home, 'channelId': self.channel, 'on': on or day(1), 'text': text, **extra})

    def test_your_reminder_waits_for_its_day_then_shows_on_home_as_yours(self):
        saved = self.add()
        self.assertTrue(saved['added'])
        self.assertEqual((saved['channelId'], saved['taskId'], saved['asker'], saved['mine'], saved['on'], saved['thread']),
                         (self.channel, None, None, True, day(1), 'Client tickets'))
        listed = self.host.dispatch('reminders', self.home)
        self.assertEqual(listed['due'], [], 'tomorrow’s waits for tomorrow')
        self.assertEqual([item['id'] for item in listed['upcoming']], [saved['id']], 'but the row can say one is set')
        again = self.add(text='Something else')
        self.assertEqual((again['id'], again['added'], again['text']), (saved['id'], False, 'Answer its question'),
                         'one per thread and day: asking twice is the same reminder')
        tomorrow = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time(9)).timestamp()
        due = r.dispatch(self.host, 'reminders', self.home, 'local-user', now=tomorrow)
        self.assertEqual([(item['id'], item['mine'], item['asker']) for item in due['due']], [(saved['id'], True, None)])
        self.assertEqual(due['upcoming'], [])
        noted = r.dispatch(self.host, 'reminders_due', {}, 'local-user', now=tomorrow)['due']
        self.assertEqual([(item['id'], item['project']) for item in noted], [(saved['id'], 'project')], 'the notification gets it too')

    def test_done_takes_yours_off_and_asking_again_brings_it_back(self):
        saved = self.add()
        self.assertEqual(self.host.dispatch('reminder_done', {**self.home, 'reminderId': saved['id']})['state'], 'done')
        self.assertEqual(self.host.dispatch('reminders', self.home)['upcoming'], [])
        back = self.add(text='Review its result')
        self.assertEqual((back['id'], back['added'], back['text']), (saved['id'], True, 'Review its result'))
        self.assertEqual([item['id'] for item in self.host.dispatch('reminders', self.home)['upcoming']], [saved['id']])
        # Another day is another reminder, and an agent's reminder for the same thread is its own.
        other = self.add(on=day(2))
        self.assertNotEqual(other['id'], saved['id'])
        agents = self.remind('later', day(1), 'Ask for the renewal quote.')
        upcoming = self.host.dispatch('reminders', self.home)['upcoming']
        self.assertEqual([item['id'] for item in upcoming], [saved['id'], agents['reminderId'], other['id']], 'soonest first')
        self.assertEqual([item['mine'] for item in upcoming], [True, False, True])

    def test_yours_is_checked_like_an_agents_and_only_for_this_projects_threads(self):
        for name, change in {'past': dict(on=day(-1)), 'too far': dict(on=day(400)), 'not a day': dict(on='tomorrow'),
                             'empty': dict(text='  '), 'long': dict(text='x' * 161),
                             'secret': dict(text='Rotate sk-ant-api03-' + 'A' * 90)}.items():
            with self.subTest(name), self.assertRaises(Rejected):
                self.add(**change)
        with self.assertRaisesRegex(Rejected, 'fields'):
            self.add(start=True)
        with self.assertRaisesRegex(Rejected, 'this project’s threads'):
            self.host.dispatch('reminder_add', {**self.home, 'channelId': 'not-a-thread', 'on': day(1), 'text': 'Hi'})
        with self.assertRaises(Rejected):
            self.host.dispatch('reminder_add', {'projectId': 'p', 'profileId': 'someone-else', 'channelId': self.channel, 'on': day(1), 'text': 'Hi'})
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.channel,))
        with self.assertRaisesRegex(Rejected, 'this project’s threads'):
            self.add()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner='local-user' WHERE id=?", (self.channel,))
        # Archived (the fixture's run is still open, so straight into the table the Archive button writes).
        import project_lifecycle
        with self.host.store.db:
            project_lifecycle.schema(self.host.store.db)
            self.host.store.db.execute('INSERT INTO conversation_archives VALUES (?,?)', (self.channel, 1))
        with self.assertRaisesRegex(Rejected, 'archived'):
            self.add()
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM task_reminders').fetchone()[0], 0)

    def test_yours_never_starts_work_sends_anything_or_changes_access(self):
        before = self.host.coding.read(self.task['id'], self.task['context'])
        with patch.object(self.host.coding, 'start') as start, patch.object(self.host.coding, 'steer') as steer, \
                patch.object(self.host.coding.conversation, 'request') as request:
            saved = self.add()
            self.host.dispatch('reminders', self.home)
            self.host.dispatch('reminder_done', {**self.home, 'reminderId': saved['id']})
        start.assert_not_called()
        steer.assert_not_called()
        request.assert_not_called()
        after = self.host.coding.read(self.task['id'], self.task['context'])
        self.assertEqual(after['messages'], before['messages'])
        self.assertEqual(self.host.coding.conversation.queue.items(self.channel), [])
        self.assertEqual(after.get('full_access'), before.get('full_access'))

    def test_a_project_holds_fifty_waiting_reminders_at_most(self):
        for offset in range(1, r.MAX_WAITING + 1):
            self.add(on=day(offset))
        with self.assertRaisesRegex(Rejected, 'Mark some done'):
            self.add(on=day(r.MAX_WAITING + 1))
        listed = self.host.dispatch('reminders', self.home)['upcoming']
        self.assertEqual(len(listed), r.UPCOMING, 'the list for the rows stays short')
        self.assertEqual(listed[0]['on'], day(1))
        # One you marked done, brought back while the project is full, counts like a new one (backend review, 9 Oct).
        first = listed[0]
        self.host.dispatch('reminder_done', {**self.home, 'reminderId': first['id']})
        self.add(on=day(r.MAX_WAITING + 1))
        with self.assertRaisesRegex(Rejected, 'Mark some done'):
            self.add(on=day(1))
        waiting = self.host.store.db.execute("SELECT COUNT(*) FROM task_reminders WHERE json_extract(document,'$.state')='waiting'").fetchone()[0]
        self.assertEqual(waiting, r.MAX_WAITING)


if __name__ == '__main__':
    unittest.main()
