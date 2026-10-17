"""The handoff and catch-up eval cases from notes/smartness/handoff-catchup.md, section 2, as deterministic checks.

Each case seeds a thread's history through the real host with the scripted provider peers (ConversationCodingTests),
edits the task records where the review's probe did (review, commit, undo, guide, tokens, times), then reads what
the next agent was told (``runs[-1].told.transcript``, the review's "T" layer) or what the thread's pickup says,
through the real host process (headless_driver.HostDriver) for the pickup cases. No model is called: the "R" layer
of the review's sketch needs a real run and lives with backend/evals.

Every case has a negative control next to it (``test_eNN_control_*``): the same seed without the thing the case is
about, which must not produce the words the case looks for.
"""
import json
import time
import unittest
import uuid

from host import ACTOR
import test_conversation_coding as conversation


def _without_tests(base):
    """The suite's setUp and helpers without its own tests, so they don't run twice."""
    cls = type(base.__name__ + 'Seed', (base,), {})
    for name in dir(base):
        if name.startswith('test_'):
            setattr(cls, name, None)
    return cls


CodexSeed = _without_tests(conversation.ConversationCodingTests)
ClaudeSeed = _without_tests(conversation.ClaudeConversationCodingTests)
HOUR = 3600


class Records:
    def doc(self, task_id):
        return json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()[0])

    def edit(self, task_id, change):
        task = self.doc(task_id)
        change(task)
        self.host.coding.save(task)
        return task

    def ask(self, text, client_id, mode='none'):
        _, detail = self.run_request(text, mode=mode, client_id=client_id)
        return detail['task']

    @staticmethod
    def told(task):
        return task['runs'][-1]['told']['transcript']

    @staticmethod
    def user_blocks(text):
        return [line for line in text.splitlines() if line.startswith('User: ')]

    def answered(self, texts, prefix='m'):
        return [self.ask(text, f'{prefix}{i}') for i, text in enumerate(texts)]


class Codex(Records, CodexSeed):
    # ------------------------------------------------------------------ 1. what happened to each earlier result
    def approved_greeting(self, undone):
        self.grant()
        self.target = 'greeting.py'
        first = self.ask('Make greet return Hello with the name', 'one', mode='success')
        now = int(time.time())

        def approve(task):
            task.update(state='idle', review='approved')
            task['commit'] = {'sha': 'abc1234deadbeef', 'at': now, 'files': ['greeting.py'], 'how': 'approved',
                              'reverted': {'sha': 'fed4321', 'at': now} if undone else None}
        self.edit(first['id'], approve)
        return self.told(self.ask('Now add a farewell function next to it', 'two'))

    def test_e02_an_undone_result_is_said_to_be_undone(self):
        told = self.approved_greeting(undone=True)
        self.assertIn('undid', told)
        self.assertNotIn('(idle)', told)
        self.assertIn('since undone: greeting.py', told)

    def test_e02_control_an_approved_result_is_not_called_undone(self):
        told = self.approved_greeting(undone=False)
        self.assertNotIn('undid', told)
        self.assertNotIn('since undone', told)

    def rejected_or_waiting(self, rejected):
        self.grant()
        first = self.ask('Rename greet to hello', 'one', mode='success')
        if rejected:
            self.edit(first['id'], lambda task: task.update(review='rejected'))
        return self.told(self.ask('Try again, keep the old signature', 'two'))

    def test_e03_a_rejected_result_is_said_to_be_rejected(self):
        told = self.rejected_or_waiting(rejected=True)
        self.assertIn('rejected', told)
        self.assertNotIn('(ready_for_review)', told)

    def test_e03_control_a_result_you_didnt_decide_on_is_not_called_rejected(self):
        told = self.rejected_or_waiting(rejected=False)
        self.assertNotIn('rejected', told)

    def test_e13_a_request_retried_elsewhere_takes_one_slot_and_the_oldest_real_exchange_stays(self):
        self.grant()
        self.ask('Oldest real request', 'a')
        failed = self.ask('Retried request', 'x', mode='overloaded')
        retry = self.ask('Retried request', 'x2')
        self.edit(failed['id'], lambda task: task.setdefault('suggest', {}).update(taken=retry['id']))
        self.answered(['Second', 'Third', 'Fourth', 'Fifth'])
        told = self.told(self.ask('What have we done so far?', 'probe'))
        self.assertEqual(told.count('Retried request'), 1, told)
        self.assertIn('User: Oldest real request', told)

    def test_e13_control_seven_plain_exchanges_keep_six_in_the_window(self):
        self.grant()
        self.answered(['One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven'])
        told = self.told(self.ask('What have we done so far?', 'probe'))
        self.assertEqual(len(self.user_blocks(told)), 6, told)

    # ------------------------------------------------------------------ 2. everything the user said
    def test_e01_a_correction_on_an_older_task_is_kept(self):
        self.grant()
        first = self.ask('Add CSV export to report.py', 'one')
        self.edit(first['id'], lambda task: task['messages'].append(
            dict(role='user', text="Don't add pandas, use the csv module", created_at=int(time.time()))))
        self.answered(['Tidy the imports', 'Fix the header row'])
        told = self.told(self.ask('Add JSON export the same way', 'probe'))
        self.assertIn("Don't add pandas, use the csv module", told)
        self.assertIn('correction', told)

    def test_e01_control_a_correction_is_said_once(self):
        self.grant()
        first = self.ask('Add CSV export to report.py', 'one')
        self.edit(first['id'], lambda task: task['messages'].append(
            dict(role='user', text="Don't add pandas, use the csv module", created_at=int(time.time()))))
        told = self.told(self.ask('Add JSON export the same way', 'probe'))
        self.assertEqual(told.count("Don't add pandas"), 1, told)

    def test_e04_the_first_messages_constraint_survives_eight_messages(self):
        self.grant()
        self.answered(['Write parse_args. Python 3.9 compatible, no match statements'] + [f'Small change {n}' for n in range(7)])
        told = self.told(self.ask('Refactor parse_args', 'probe'))
        self.assertIn('Python 3.9 compatible, no match statements', told)

    def test_e04_control_old_answers_stay_out(self):
        self.grant()
        tasks = self.answered(['Write parse_args. Python 3.9 compatible, no match statements'] + [f'Small change {n}' for n in range(7)])
        self.edit(tasks[0]['id'], lambda task: task['messages'].__setitem__(-1, dict(task['messages'][-1], text='UNIQUE-OLD-ANSWER')))
        told = self.told(self.ask('Refactor parse_args', 'probe'))
        self.assertNotIn('UNIQUE-OLD-ANSWER', told)

    def test_e14_a_decision_in_the_middle_survives(self):
        self.grant()
        self.answered(['SQLite or JSON for the settings store?', 'Go with JSON, no schema dependency']
                      + [f'Unrelated change {n}' for n in range(6)])
        told = self.told(self.ask('Implement the settings store', 'probe'))
        self.assertIn('Go with JSON, no schema dependency', told)

    def test_e14_control_a_message_already_in_the_window_isnt_said_twice(self):
        self.grant()
        self.answered(['SQLite or JSON for the settings store?', 'Go with JSON, no schema dependency', 'Unrelated change'])
        told = self.told(self.ask('Implement the settings store', 'probe'))
        self.assertEqual(told.count('Go with JSON, no schema dependency'), 1, told)

    def test_budget_a_long_chat_keeps_the_first_request_and_says_what_it_left_out(self):
        """Not an eval case: the user-words list stays inside its budget."""
        self.grant()
        self.answered(['First request, the one that sets the scene'] + [f'Message {n} ' + 'x' * 380 for n in range(24)])
        told = self.told(self.ask('Carry on', 'probe'))
        self.assertIn('First request, the one that sets the scene', told)
        self.assertRegex(told, r'\[\d+ earlier messages? left out\]')
        self.assertIn('Message 17 ', told, 'the newest ones are kept')

    def test_e21_the_end_of_a_long_answer_is_kept(self):
        self.grant()
        first = self.ask('Compare the two options', 'one')
        long = 'HEAD-OF-ANSWER ' + 'filler ' * 4300 + 'Want me to use option B?'
        self.edit(first['id'], lambda task: task['messages'].__setitem__(-1, dict(task['messages'][-1], text=long)))
        told = self.told(self.ask('yes', 'probe'))
        self.assertIn('Want me to use option B?', told)

    def test_e21_control_the_start_of_a_long_answer_is_kept_too(self):
        self.grant()
        first = self.ask('Compare the two options', 'one')
        long = 'HEAD-OF-ANSWER ' + 'filler ' * 4300 + 'Want me to use option B?'
        self.edit(first['id'], lambda task: task['messages'].__setitem__(-1, dict(task['messages'][-1], text=long)))
        told = self.told(self.ask('yes', 'probe'))
        self.assertIn('HEAD-OF-ANSWER', told)
        self.assertLess(len(told), 20000)

    # ------------------------------------------------------------------ 3. what was checked
    def checked_run(self, failing):
        self.grant()
        if failing:
            self.appendix = '\nclass Broken(unittest.TestCase):\n def test_ok(self): self.fail("greeting is wrong")\n'
        first = self.ask('Change the greeting', 'one', mode='success')

        def guide(task):
            run = task['runs'][-1]
            run['guide'] = dict(goal='A new greeting', step='Running the check', why='To prove it', next='Hand it over',
                                limits='I did not try it in the browser.', test_steps='', title='Greeting',
                                run_id=run['id'], tool_token='guide-1', updated_at=int(time.time()))
        self.edit(first['id'], guide)
        return first, self.told(self.ask('Carry on', 'probe'))

    def test_e07_a_failed_check_is_handed_over_with_the_failing_test_and_what_wasnt_checked(self):
        first, told = self.checked_run(failing=True)
        self.assertNotEqual((first.get('completion') or {}).get('test_exit_status'), 0, 'seed: the check failed')
        self.assertIn('failed', told)
        self.assertIn('test_ok', told)
        self.assertIn('I did not try it in the browser.', told)

    def test_e07_control_a_passing_check_is_not_called_failed(self):
        _, told = self.checked_run(failing=False)
        self.assertNotIn('failed', told)

    def corrected_without_guide(self, guide_on_latest):
        self.grant()
        first = self.ask('Make greet take a title', 'one', mode='success')

        def runs(task):
            run = task['runs'][0]
            run['guide'] = dict(goal='greet takes a title', step='Editing greeting.py', why='w', next='n', limits='l', test_steps='',
                                title='t', run_id=run['id'], tool_token='g', updated_at=int(time.time()))
            if not guide_on_latest:
                task['runs'].append(dict(run, id='run-2', guide=None, state='completed'))
            task['messages'].append(dict(role='user', text='Use Dr as the default title', created_at=int(time.time())))
        self.edit(first['id'], runs)
        return self.told(self.ask('What was the goal again?', 'probe'))

    def test_e06_the_goal_from_an_earlier_run_survives_a_correction_run_without_a_guide(self):
        told = self.corrected_without_guide(guide_on_latest=False)
        self.assertIn('greet takes a title', told)
        self.assertIn('earlier run', told)

    def test_e06_control_the_latest_runs_guide_is_not_labelled_earlier(self):
        told = self.corrected_without_guide(guide_on_latest=True)
        self.assertIn('greet takes a title', told)
        self.assertNotIn('earlier run', told)

    def follow_up_without_guide(self, guide_on_follow_up, follow_up='Make the title optional'):
        """e6 as the eval seeds it: the guide is on an earlier message, and the follow-up message is its own task."""
        self.grant()
        first = self.ask('Let greet take a title', 'one', mode='success')

        def guided(goal, step, nxt):
            def change(task):
                run = task['runs'][-1]
                run['guide'] = dict(goal=goal, step=step, why='w', next=nxt, limits='', test_steps='', title='t',
                                    run_id=run['id'], tool_token='g', updated_at=int(time.time()))
            return change
        self.edit(first['id'], guided('greet takes a title', 'Added the title parameter', 'Update the test'))
        second = self.ask(follow_up, 'two', mode='success')
        if guide_on_follow_up:
            self.edit(second['id'], guided('The title is optional', 'Defaulted title to None', 'Run the check'))
        return self.told(self.ask('Summarise where we are', 'probe'))

    def test_e40_a_goal_you_called_off_is_not_handed_on(self):
        # Hard case e40 (20 Sep): the borrowed goal stops at words that call the work off. Your words still reach the
        # agent through the transcript, so only Dervo's own goal line goes.
        told = self.follow_up_without_guide(guide_on_follow_up=False,
                                            follow_up='Drop the title idea entirely, we are not doing it. Put greeting.py back as it was.')
        self.assertNotIn('Its goal: greet takes a title', told)
        self.assertNotIn('from an earlier message', told)
        self.assertIn('User: Drop the title idea entirely', told)

    def test_e40_control_a_request_that_only_sounds_like_calling_off_keeps_the_goal(self):
        told = self.follow_up_without_guide(guide_on_follow_up=False, follow_up='Drop the id column from the title lookup')
        self.assertIn('Its goal: greet takes a title (from an earlier message)', told)

    def test_e06_a_follow_up_message_without_a_guide_still_hands_on_the_goal(self):
        told = self.follow_up_without_guide(guide_on_follow_up=False)
        self.assertIn('Its goal: greet takes a title (from an earlier message)', told)
        # Only the goal carries over: the older message's step and next are about work that has moved on.
        self.assertNotIn('Last step it reported', told)
        self.assertNotIn('Update the test', told)

    def test_e06_control_a_follow_up_with_its_own_guide_hands_that_one(self):
        told = self.follow_up_without_guide(guide_on_follow_up=True)
        self.assertIn('Its goal: The title is optional\n', told)
        self.assertNotIn('Its goal: greet takes a title', told)
        self.assertNotIn('from an earlier message', told)

    def test_e05_the_files_it_read_are_named(self):
        self.grant()
        self.ask('Find why the greeting is wrong', 'one', mode='success')
        told = self.told(self.ask('Carry on', 'probe'))
        self.assertIn('Files it read: test_sample.py', told)

    def test_e05_control_a_text_answer_names_no_files_read(self):
        self.grant()
        self.ask('What does greet do?', 'one')
        told = self.told(self.ask('Carry on', 'probe'))
        self.assertNotIn('Files it read', told)


    # ------------------------------------------------------------------ reviewer's changes (20 Sep)
    def many_notes(self, size):
        self.grant()
        self.appendix = '\nclass Broken(unittest.TestCase):\n def test_ok(self): self.fail("' + 'z' * 900 + '")\n'
        first = self.ask('Change the greeting', 'one', mode='success')

        def fill(task):
            run = task['runs'][-1]
            run['guide'] = dict(goal='g' * 240, step='s' * 240, why='w', next='n' * 300, limits='l' * 600, test_steps='', title='t',
                                run_id=run['id'], tool_token='guide-1', updated_at=int(time.time()))
            for n in range(5):
                task['messages'].append(dict(role='user', text=f'NOTE{n} ' + 'y' * size, created_at=int(time.time()), steer='read'))
        self.edit(first['id'], fill)
        return self.told(self.ask('Carry on', 'probe'))

    def test_review_a_note_cut_from_the_handoff_still_reaches_the_next_agent(self):
        told = self.many_notes(990)
        for n in range(5):
            self.assertEqual(told.count(f'NOTE{n} '), 1, f'NOTE{n}')

    def test_review_control_short_notes_are_each_said_once(self):
        told = self.many_notes(20)
        for n in range(5):
            self.assertEqual(told.count(f'NOTE{n} '), 1, f'NOTE{n}')

    KEY = 'sk-ant-api03-AbCdEf1234567890GhIjKlMnOpQrStUv'

    def test_review_a_key_you_typed_or_a_check_printed_never_goes_back_in_the_summary(self):
        self.grant()
        self.appendix = '\nclass Broken(unittest.TestCase):\n def test_ok(self): self.fail("' + self.KEY + '")\n'
        older = self.ask('Wire up the client', 'one')
        self.edit(older['id'], lambda task: task['messages'].append(
            dict(role='user', text=f'Use this key: {self.KEY}', created_at=int(time.time()))))
        failing = self.ask('Change the greeting', 'two', mode='success')
        self.edit(failing['id'], lambda task: task['messages'].append(
            dict(role='user', text=f'Also try {self.KEY}', created_at=int(time.time()), steer='read')))
        told = self.told(self.ask('Carry on', 'probe'))
        self.assertNotIn(self.KEY, told)
        self.assertIn('Use this key: [redacted]', told)
        self.assertIn('test_ok', told, 'the rest of the check output is still there')

    def test_review_control_plain_words_are_not_redacted(self):
        self.grant()
        older = self.ask('Wire up the client', 'one')
        self.edit(older['id'], lambda task: task['messages'].append(
            dict(role='user', text='Use the csv module, not pandas', created_at=int(time.time()))))
        self.ask('Tidy the imports', 'two')
        told = self.told(self.ask('Carry on', 'probe'))
        self.assertIn('Use the csv module, not pandas', told)
        self.assertNotIn('[redacted]', told)


class Claude(Records, ClaudeSeed):
    def place_session(self, task):
        home = self.base / 'store/profiles/p'
        (home / 'projects/-private-tmp-dervo-claude-gone').mkdir(parents=True, exist_ok=True)
        (home / f"projects/-private-tmp-dervo-claude-gone/{task['session_id']}.jsonl").write_text('{}\n')

    def age(self, task_id, seconds):
        def older(task):
            for message in task['messages']:
                message['created_at'] = int(message.get('created_at') or time.time()) - seconds
        return self.edit(task_id, older)

    # ------------------------------------------------------------------ 4. how old the memory is
    def continued_after(self, seconds):
        self.grant()
        first = self.ask('Does greeting.py look right to you?', 'one')
        if seconds:
            self.age(first['id'], seconds)
        self.place_session(first)
        second = self.ask('Carry on', 'two')
        self.assertEqual(second['continuity']['state'], 'continues', 'seed: the session continues')
        return self.told(second)

    def test_e09_a_session_three_days_old_is_told_so(self):
        told = self.continued_after(3 * 24 * HOUR + 60)
        self.assertIn('3 days ago', told)

    def test_e09_control_a_session_minutes_old_gets_no_age_line(self):
        told = self.continued_after(0)
        self.assertNotIn(' ago', told)

    def other_chat_changed_greeting(self, category, visibility='shared'):
        self.grant()
        self.host.store.add_thread('b', 'p', 'b', visibility, ACTOR)
        first = self.ask('Does greeting.py look right to you?', 'one')

        def read_it(task):
            task['tokens'].append(dict(token='read-1', tool='dervo_read_file', state='delivered', run_id=task['runs'][-1]['id'],
                                       path='greeting.py', started_at=time.time(), ended_at=time.time()))
        self.edit(first['id'], read_it)
        self.age(first['id'], 2 * HOUR)
        other = self.doc(first['id'])
        now = int(time.time())
        other.update(id=uuid.uuid4().hex, state='idle', review='approved', session_id=None, request_id=None,
                     context=dict(other['context'], thread_id='b'),
                     binding=dict(other['binding'], visibility=visibility, category=category),
                     messages=[dict(role='user', text='Make greet say Hello', created_at=now - 600)],
                     completion=dict(at=now - 300, changed_files=['greeting.py'], test_exit_status=0),
                     commit=dict(sha='b0b0b0b0b0', at=now - 200, files=['greeting.py'], how='approved', reverted=None))
        self.host.coding.save(other)
        self.place_session(first)
        second = self.ask('Add a farewell next to greet', 'two')
        self.assertEqual(second['continuity']['state'], 'continues', 'seed: the session continues')
        return self.told(second)

    def test_e08_a_file_another_chat_changed_is_named(self):
        told = self.other_chat_changed_greeting('personal')
        self.assertIn('greeting.py (approved and committed', told)
        self.assertIn('Read them again before editing', told)

    def test_review_control_a_private_other_chat_is_neither_named_nor_quoted(self):
        told = self.other_chat_changed_greeting('personal', visibility='private')
        self.assertNotIn('other chats', told)
        self.assertNotIn('greeting.py (', told)
        self.assertNotIn('Make greet say Hello', told)

    def test_e20_control_a_chat_on_a_work_account_isnt_named(self):
        told = self.other_chat_changed_greeting('work')
        self.assertNotIn('other chats', told)
        self.assertNotIn('greeting.py (', told)


class Pickup(Records, CodexSeed):
    """Section 5: the Next row, read through the real host process."""

    def seeded(self, change):
        self.grant()
        first = self.ask('Change the greeting', 'one', mode='success')
        self.assertEqual(first['state'], 'ready_for_review', 'seed')
        task = self.edit(first['id'], change)
        self.close_host()
        from headless_driver import HostDriver
        driver = HostDriver(self.base / 'store').start()
        try:
            facts = driver.call('conversation_pickup', **self.scope)
        finally:
            driver.close()
        import conversation_activity
        return facts, conversation_activity.end_of(task)

    @staticmethod
    def guide(task, next_text, *, after_check):
        run = task['runs'][-1]
        tokens = [t for t in task['tokens'] if t.get('run_id') == run['id']]
        if after_check:
            token = dict(token='guide-final', tool='dervo_update_task_guide', state='delivered', run_id=run['id'])
            task['tokens'].append(token)
        else:
            token = tokens[0]  # the read, before the edit and the check
        run['guide'] = dict(goal='A new greeting', step='Checking', why='w', next=next_text, limits='l', test_steps='', title='t',
                            run_id=run['id'], tool_token=token['token'], updated_at=int(time.time()))

    def test_e15_a_next_written_before_the_check_gives_way_to_dervos(self):
        facts, _ = self.seeded(lambda task: self.guide(task, 'Run the tests to confirm the new greeting.', after_check=False))
        self.assertEqual(facts['next'], 'Your review, then commit.')

    def test_e15_control_a_next_written_after_the_check_is_kept(self):
        facts, _ = self.seeded(lambda task: self.guide(task, 'Try the greeting in the terminal.', after_check=True))
        self.assertEqual((facts['next'], facts['next_by']), ('Try the greeting in the terminal.', 'agent'))

    def test_e17_a_question_you_answered_since_is_not_asked_again(self):
        def change(task):
            self.guide(task, 'Should I also update the README?', after_check=True)
            task['messages'].append(dict(role='user', text='No README', created_at=int(time.time()) + 5, steer='read'))
        facts, ended = self.seeded(change)
        self.assertNotEqual(facts['next'], 'Should I also update the README?')
        self.assertFalse(facts['question'])
        self.assertNotEqual(ended, 'asking')

    def test_e16_control_a_fresh_question_still_asks(self):
        facts, ended = self.seeded(lambda task: self.guide(task, 'Should I also update the README?', after_check=True))
        self.assertEqual(facts['next'], 'Should I also update the README?')
        self.assertTrue(facts['question'])
        self.assertEqual(ended, 'asking')


if __name__ == '__main__':
    unittest.main()
