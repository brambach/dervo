"""Where you left off, from records only: each state's sentence, Home's order, lead and counts, and the thread pickup.
Real host and store. No model is set up anywhere in these tests, which is the point: the pickup never needs one."""
import json
import time
import unittest
from unittest.mock import patch

import conversation_activity as ca
import pickup
from dervo_connections import Rejected
from test_project_ideas import IdeaFixture

HOUR = 3600


class PickupFixture(IdeaFixture):
    def setUp(self):
        super().setUp()
        self.db = self.host.store.db
        ca.schema(self.db)
        self.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        self.now = time.time()
        # This Dervo has been open a day: a run recorded as started within the day is one it is driving. A run that
        # started before this Dervo did is one it died with (see the restart tests below).
        with self.db:
            self.db.execute('UPDATE session_marks SET at=at-86400')

    def task(self, channel, state, *, asked='Tighten the sidebar spacing', answer=None, error=None, step=None, next_step=None,
             run_state='completed', files=(), checked=None, commit=None, status=None, moved=None, running=False,
             provider=None, failure=None, other=None):
        run = dict(id='run-' + channel, state=run_state)
        if step or next_step:
            run['guide'] = dict(run_id=run['id'], step=step, next=next_step)
        if failure:
            run['provider_failure'] = failure
        messages = [dict(role='user', text='An older request'), dict(role='user', text=asked)]
        if answer:
            messages.append(dict(role='assistant', text=answer))
        document = dict(id='task-' + channel, origin='conversation', state=state, context=dict(thread_id=channel, project_id='p'),
                        runs=[run], messages=messages, error=error, tokens=[])
        if provider:
            document['binding'] = dict(provider=provider, method='subscription')
        if failure:
            document['suggest'] = dict(kind=failure, reason=f"{'Claude Code' if provider == 'claude' else 'Codex'} was overloaded.",
                                       other=other, automatic=False, taken=None, error=None)
        if files or checked is not None:
            document['completion'] = dict(changed_files=list(files), test_exit_status=checked)
        if commit:
            document['commit'] = dict(sha=commit)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)', (document['id'], json.dumps(document)))
            # The status the index would write for this record (conversation_activity.record), unless the test says.
            self.db.execute('INSERT OR REPLACE INTO conversation_task_state VALUES (?,?,?,?,?)',
                            (document['id'], channel, status or ('running' if running else ca.status_of(document)),
                             step if running else None, moved or self.now))
        return document

    def seen(self, channel, at):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO conversation_seen VALUES (?,?)', (channel, at))

    def session(self, end):
        with self.db:
            self.db.execute('DELETE FROM session_marks')
            self.db.execute("INSERT INTO session_marks VALUES ('start', ?)", (end - 5 * HOUR,))
            self.db.execute("INSERT INTO session_marks VALUES ('end', ?)", (end,))
            self.db.execute("INSERT INTO session_marks VALUES ('start', ?)", (end + 60,))  # closed cleanly, reopened a minute later

    def home(self):
        return self.host.dispatch('project_pickup', dict(self.scope))

    def thread(self, channel):
        return self.host.dispatch('conversation_pickup', dict(projectId='p', channelId=channel))

    def facts(self, channel):
        return next(item for item in pickup.gather(self.db, pickup.project_threads(self.db, dict(project_id='p', profile_id='a'), 'local-user'))
                    if item['id'] == channel)


class StateTests(PickupFixture):
    def test_each_state_reads_as_short_plain_sentences_from_its_records(self):
        review, stopped, lost, running, answered, approved = (self.conversation(name) for name in
                                                              ('Picker', 'Design sweep', 'Releases', 'Sidebar', 'Question', 'Done'))
        self.task(review, 'ready_for_review', files=('a.py', 'b.py', 'c.py'), checked=0, next_step='You try it in beta.6')
        self.task(stopped, 'blocked', run_state='interrupted', step='Editing Knowledge.tsx', next_step='Releases after Knowledge',
                  error='Stopped before completion It had changed ConnectedApp.tsx and 3 more. Those edits are in your project folder, not committed.')
        self.task(lost, 'disconnected', run_state='uncertain', step='Writing BetaPlan.tsx',
                  error='Dervo beta access needs confirmation. Open Account in Settings.')
        self.task(running, 'working', running=True, step='Editing DervoSidebar.css', next_step='The command palette')
        self.task(answered, 'answered', answer='Your next meeting is Monday at 10:30. Nothing else today.')
        self.task(approved, 'idle', commit='abc1234', next_step='Nothing')
        facts = self.facts(review)
        self.assertEqual((facts['kind'], facts['status'], facts['needs'], facts['checks']), ('review', 'finished, needs your review', True, 'passed'))
        self.assertEqual(facts['asked'], 'Tighten the sidebar spacing', 'what you last asked, not the first message')
        self.assertEqual(facts['line'], 'Finished. 3 files changed. Checks passed.')
        self.assertEqual(facts['did'], facts['line'])
        self.assertEqual((facts['next'], facts['next_by'], facts['question']), ('You try it in beta.6.', 'agent', False))
        facts = self.facts(stopped)
        self.assertEqual(facts['line'], 'You paused this. 4 files changed, not committed.')
        self.assertEqual(facts['did'], 'You paused this. 4 files changed, not committed. It was on: Editing Knowledge.tsx.')
        self.assertEqual((facts['failed'], facts['handoff'], facts['next']), (False, None, 'Releases after Knowledge.'))
        facts = self.facts(lost)
        self.assertEqual(facts['line'], 'Stopped. Dervo couldn’t confirm how far it got.')
        self.assertEqual(facts['did'], 'Stopped. Dervo couldn’t confirm how far it got. The reason: Dervo beta access needs confirmation. '
                                       'It was on: Writing BetaPlan.tsx.')
        # Beta.17.1 (run_ending): Dervo lost track, so the outcome is unknown, never a failure.
        self.assertEqual((facts['failed'], facts['ending']), (False, 'unknown'))
        facts = self.facts(running)
        self.assertEqual((facts['kind'], facts['now'], facts['needs']), ('running', 'Editing DervoSidebar.css', False))
        self.assertEqual((facts['line'], facts['did']), ('Editing DervoSidebar.css.', 'Still working: Editing DervoSidebar.css.'))
        self.assertEqual(self.facts(answered)['line'], 'Answered: Your next meeting is Monday at 10:30.')
        facts = self.facts(approved)
        self.assertEqual((facts['line'], facts['next']), ('Approved and committed.', None),
                         'an approved result has no next step, whatever the agent last said')

    def test_an_answer_in_the_old_report_headings_reads_as_prose(self):
        """Bryce, beta.11: "Answered: Changed Saved a project memory note" and "Answered: Changed Nothing." Replies
        saved before #24 carry Changed, Checked, Not checked and Next headings."""
        cases = {
            'Memory': ('## Changed\n- Saved a project memory note\n\n## Checked\n- Nothing run\n\n## Not checked\n- The app\n\n'
                       '## Next\n- Nothing', 'Answered: Saved a project memory note.'),
            'Port': ('Changed\nNothing.\n\nChecked\nRead the config and confirmed the port is 8818.\n\nNot checked\nThe app.\n\n'
                     'Next\nNothing.', 'Answered: Read the config and confirmed the port is 8818.'),
            'Empty': ('**Changed:** Nothing.\n**Checked:** Nothing.\n**Not checked:** Everything.\n**Next:** Nothing.',
                      'Answered in text.'),
            # Negative controls: prose, including prose that starts with a heading word, reads as it did before.
            'Prose': ('Your next meeting is Monday at 10:30. Nothing else today.', 'Answered: Your next meeting is Monday at 10:30.'),
            'Week': ('Next week we ship. Changed my mind on colours.', 'Answered: Next week we ship.'),
            'Logs': ('Checked the logs and the port is 8818.\n\nNothing else changed.', 'Answered: Checked the logs and the port is 8818.'),
            # Reviewer, 19 Sep: a heading word followed by another word before the colon is prose, not a heading.
            'Steps': ('Next steps: run tests.', 'Answered: Next steps: run tests.'),
        }
        for name, (answer, line) in cases.items():
            conversation = self.conversation(name)
            self.task(conversation, 'answered', answer=answer)
            facts = self.facts(conversation)
            self.assertEqual((facts['line'], facts['did']), (line, line), name)
            self.assertNotIn('Changed', facts['line'].replace('Changed my', ''), name)

    def test_a_run_that_hit_a_usage_limit_says_so_and_names_the_other_provider(self):
        # The morning-after mockup: "Stopped at Claude’s usage limit. 3 of 5 files changed, not committed." Records hold the
        # provider, the failure kind, the files it had changed and the account that could carry on; not how many it planned.
        picker = self.conversation('Connector picker')
        self.task(picker, 'blocked', run_state='failed', provider='claude', failure='usage_limit',
                  other=dict(provider='codex', label='Codex', profile_id='cx'),
                  error='Claude Code reached its usage limit. It had changed ConnectorPicker.tsx, settings.py, test_settings.py. Those edits are in your project folder, not committed.')
        facts = self.facts(picker)
        self.assertEqual(facts['line'], 'Stopped at Claude’s usage limit. 3 files changed, not committed.')
        # It left changes, so Continue in Codex would be refused (conversation_coding.retry_on): the row asks for a review.
        # Beta.17.1: the provider's usage limit is a limit, not failed work (run_ending), so no X. It still needs you.
        self.assertEqual((facts['failed'], facts['ending'], facts['needs'], facts['handoff'], facts['left_changes'], facts['question']), (False, 'limit', True, None, True, False))
        overloaded = self.conversation('Live board')
        self.task(overloaded, 'blocked', run_state='failed', provider='codex', failure='overloaded', error='The server is overloaded.')
        facts = self.facts(overloaded)
        self.assertEqual(facts['line'], 'Stopped. Codex was overloaded.')
        self.assertIsNone(facts['handoff'], 'no other account on record, no handoff')
        bare = self.conversation('Bare')
        self.task(bare, 'blocked', run_state='failed', error='')
        self.assertEqual(self.facts(bare)['line'], 'Stopped. No reason was recorded.')
        # Claude Code's own limit words carry the reset; the line keeps it (product review, 23 Sep, 3).
        session = self.conversation('Session limit')
        self.task(session, 'blocked', run_state='failed', provider='claude', failure='usage_limit',
                  error="You've hit your session limit · resets 3pm (Australia/Brisbane)")
        self.assertEqual(self.facts(session)['line'], 'Stopped at Claude’s usage limit. Resets 3pm (Australia/Brisbane).')

    def test_continue_on_the_other_account_is_offered_only_where_the_handoff_goes_through(self):
        """UX review, 23 Sep, item 4: "Continue in Codex" was offered whenever another account existed, and the handoff
        then refused with "This run left file changes or is still settling". The row offers it only when retry_on
        would take it, and otherwise asks you to review what the run left."""
        other = dict(provider='codex', label='Codex', profile_id='cx')
        self.db.execute('CREATE TABLE IF NOT EXISTS task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')

        def stopped(name, *, error='Claude Code reached its usage limit.', attempts=0, prior=0, tokens=(), runs=None):
            channel = self.conversation(name)
            document = self.task(channel, 'blocked', run_state='failed', provider='claude', failure='usage_limit', other=other, error=error)
            document['runs'][-1]['prior_attempts'] = prior
            if runs:
                document['runs'] = runs + document['runs']
            document['tokens'] = list(tokens)
            with self.db:
                self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(document), document['id']))
                if attempts is not None:
                    self.db.execute('INSERT OR REPLACE INTO task_evidence VALUES (?,?)',
                                    (document['id'], json.dumps(dict(id=document['id'], attempts=[{}] * attempts))))
            return channel

        clean = stopped('Clean stop')
        left = stopped('Left files', error='Claude Code reached its usage limit. It had changed a.py, b.py. Those edits are in your project folder, not committed.')
        attempted = stopped('Attempted', attempts=2, prior=1)
        delivering = stopped('Delivering', tokens=[dict(state='executing')])
        job = stopped('Job running', tokens=[dict(state='delivered', job_id='j1', job_done=False)])
        uncertain = stopped('Uncertain', runs=[dict(id='r0', state='uncertain')])
        unknown = stopped('No evidence', attempts=None)

        facts = self.facts(clean)
        self.assertEqual((facts['handoff'], facts['left_changes']), ('Codex', False), 'a clean stop can carry on')
        facts = self.facts(left)
        self.assertEqual((facts['handoff'], facts['left_changes']), (None, True))
        self.assertEqual(facts['line'], 'Stopped at Claude’s usage limit. 2 files changed, not committed.')
        for channel, why in ((attempted, 'a file attempt this run recorded'), (delivering, 'a tool call still running'),
                             (job, 'a host check still running'), (uncertain, 'a run whose outcome is uncertain'),
                             (unknown, 'no evidence record, which retry_on refuses as an unknown task')):
            facts = self.facts(channel)
            self.assertIsNone(facts['handoff'], why)
            self.assertFalse(facts['left_changes'], why)
        # A handoff someone already took, or a stop you pressed yourself, offers nothing, as before.
        self.assertIsNone(pickup.thread_facts(dict(id='x', name='x'), dict(self.facts_task(clean), suggest=dict(other=other, taken='t2')),
                                              {}, None, 0)['handoff'])

        # Negative control: with the check taken out, which is today's rule (another account on record and not taken),
        # every one of these rows offers Continue in Codex.
        with patch.object(pickup, 'hand_off_ready', lambda *_args: True):
            for channel in (left, attempted, delivering, job, uncertain, unknown):
                self.assertEqual(self.facts(channel)['handoff'], 'Codex', channel)

        # The handoff that carries the work (23 Sep, bet 3): in a git repository Dervo snapshots the folder first, so a
        # run that left changes or file attempts offers Continue again. One still settling, or unknown, still doesn't.
        with patch.object(pickup, 'in_repo', lambda tasks: {(task.get('binding') or {}).get('cwd'): True for task in tasks}):
            for channel in (left, attempted, clean):
                self.assertEqual(self.facts(channel)['handoff'], 'Codex', channel)
            self.assertTrue(self.facts(left)['left_changes'], 'the row still knows it left changes')
            for channel in (delivering, job, uncertain, unknown):
                self.assertIsNone(self.facts(channel)['handoff'], channel)

    def facts_task(self, channel):
        import chat_links
        return chat_links.latest_tasks(self.db, [channel])[channel]

    def test_a_check_that_failed_or_never_ran_is_said_plainly(self):
        failed, unchecked = self.conversation('Failed check'), self.conversation('No check')
        self.task(failed, 'ready_for_review', files=('a.py',), checked=1)
        self.task(unchecked, 'ready_for_review', files=())
        self.assertEqual(self.facts(failed)['line'], 'Finished. 1 file changed. A check failed.')
        self.assertEqual(self.facts(unchecked)['line'], 'Finished. No checks ran.')

    def test_a_question_the_agent_reported_as_its_next_step_is_what_needs_you(self):
        ideas = self.conversation('Ideas storage')
        self.task(ideas, 'ready_for_review', files=('project_ideas.py', 'test_project_ideas.py'), checked=0,
                  next_step='Should an archived idea keep its threads?')
        facts = self.facts(ideas)
        self.assertEqual((facts['question'], facts['next']), (True, 'Should an archived idea keep its threads?'))
        self.assertTrue(pickup.asking(facts))
        plain_review = self.conversation('Live board')
        self.task(plain_review, 'ready_for_review', checked=0, next_step='Review the same-file warning, then commit')
        self.assertFalse(pickup.asking(self.facts(plain_review)), 'a review with a plain next step is finished work, not a question')
        # Hard case s45 (21 Sep): a run that changed a file and ended asking is still a review, and the question is its Next.
        asked = self.conversation('Greeting')
        self.task(asked, 'ready_for_review', files=('greeting.py',), checked=1,
                  answer='Changed greeting.py to return Hi, but test_sample expects Hello with the name. Should the greeting be Hi or Hello?')
        facts = self.facts(asked)
        self.assertEqual((facts['kind'], facts['question'], facts['next'], facts['next_by']), ('review', True, 'Should the greeting be Hi or Hello?', 'agent'))
        self.assertTrue(pickup.asking(facts))

    def test_an_answer_that_ends_on_a_real_question_is_what_needs_you(self):
        # Auto-sorting #5, eval s13: a chat-only answer ending on a question put to you is blocked on you, not done.
        rows = self.conversation('Old rows')
        self.task(rows, 'answered', answer='Migrated the settings rows.\n\nShould I also update the tests, or leave them?')
        facts = self.facts(rows)
        self.assertEqual((facts['needs'], facts['question'], facts['next'], facts['next_by']),
                         (True, True, 'Should I also update the tests, or leave them?', 'agent'))
        self.assertTrue(pickup.asking(facts))
        # Negative controls: a stock offer, and a question inside the code the answer ends on.
        offer, code = self.conversation('Offer'), self.conversation('Query')
        self.task(offer, 'answered', answer='Migrated the rows. Want me to add a test for this too?')
        self.task(code, 'answered', answer='Migrated the rows. The query was:\n```sql\nSELECT * FROM rows WHERE old = ?\n```')
        for channel in (offer, code):
            facts = self.facts(channel)
            self.assertEqual((facts['needs'], facts['question'], facts['kind']), (False, False, 'answered'))

    def test_with_no_reported_next_step_dervo_says_what_it_knows_comes_next(self):
        review, stopped = self.conversation('Picker'), self.conversation('Sweep')
        self.task(review, 'ready_for_review')
        self.task(stopped, 'blocked', run_state='interrupted', error='Stopped before completion')
        self.assertEqual((self.facts(review)['next'], self.facts(review)['next_by']), ('Your review, then commit.', 'dervo'))
        self.assertEqual(self.facts(stopped)['next'], 'Resume it, or look at what it left.')
        self.assertEqual(self.facts(stopped)['line'], 'You paused this.')

    def test_your_request_loses_its_markdown_and_fits_one_line(self):
        # 18 Sep, Bryce's thread screen: the pickup quoted the whole request, raw backticks included.
        sweep = self.conversation('Design sweep')
        self.task(sweep, 'ready_for_review', asked='## Plan\n\nRead `PLAN-orchestrator.md` first. Start with **step 1** only: ideas, '
                  'project-scoped, with [the record](docs/ideas.md) as the group key, and then keep going through every remaining step in order',
                  step='Editing `project_ideas.py`', next_step='Wire `ideas_list` into *Home*')
        facts = self.facts(sweep)
        self.assertEqual(facts['asked'], 'Plan Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped, with…')
        self.assertLessEqual(len(facts['asked']), pickup.ASKED_LIMIT)
        self.assertEqual(facts['next'], 'Wire ideas_list into Home.')
        self.assertEqual(pickup.plain('Wrote `project_ideas.py` and `test_project_ideas.py`. All 44 pass.'),
                         'Wrote project_ideas.py and test_project_ideas.py. All 44 pass.', 'identifiers keep their underscores')
        self.assertEqual(pickup.plain('- one\n- two\n> quoted\n1. three'), 'one\ntwo\nquoted\nthree')
        self.assertEqual(pickup.plain('```python\nx = 1\n```'), '\nx = 1\n')


class HomeTests(PickupFixture):
    def test_home_says_when_the_mac_is_held_awake_for_open_runs(self):
        from types import SimpleNamespace
        import keep_awake
        self.assertIsNone(self.home()['awake'], 'nothing held before any run')
        self.host.keep_awake = SimpleNamespace(holding=True)
        self.assertEqual(self.home()['awake'], keep_awake.LINE)
        self.host.keep_awake = SimpleNamespace(holding=False)
        self.assertIsNone(self.home()['awake'], 'caffeinate not running: Home says nothing about it')
        del self.host.keep_awake

    def test_blocked_on_you_first_then_running_then_your_review_then_what_you_were_in(self):
        asked, stopped, running, review, middle, older, quiet = (self.conversation(name) for name in
                                                                 ('Asked', 'Stopped', 'Running', 'Review', 'Middle', 'Older', 'Quiet'))
        self.conversation('Never used')
        self.task(review, 'ready_for_review', moved=self.now - 5 * HOUR)
        self.task(asked, 'ready_for_review', moved=self.now - 6 * HOUR, next_step='Keep the old name?')
        self.task(stopped, 'blocked', run_state='failed', error='Claude Code was overloaded.', moved=self.now - HOUR)
        self.task(running, 'working', running=True, step='Editing rank.ts', moved=self.now - 10 * HOUR)
        self.task(middle, 'answered', moved=self.now - 9 * HOUR)
        self.seen(middle, self.now - 2 * HOUR)
        self.task(older, 'answered', moved=self.now - 3 * HOUR)
        self.task(quiet, 'answered', moved=self.now - 10 * 86400)
        found = self.home()
        self.assertEqual([item['name'] for item in found['items']], ['Asked', 'Stopped', 'Running', 'Review', 'Middle'],
                         'a question for you before a stopped run, even though the run stopped later')
        self.assertEqual(found['more'], 2, 'the sixth shown and a thread quiet for over a week fold into "more"; one never used isn’t counted')
        self.assertEqual(found['middle']['name'], 'Middle')
        self.assertEqual(found['counts'], dict(threads=7, running=1, moved=0, finished=0, stopped=0, asking=1),
                         'no session mark yet, so nothing counts as moved')

    def test_a_dismissed_need_is_not_first_any_more(self):
        need, recent = self.conversation('Need'), self.conversation('Recent')
        self.task(need, 'ready_for_review', moved=self.now - 5 * HOUR)
        self.task(recent, 'answered', moved=self.now - HOUR)
        self.assertEqual([item['name'] for item in self.home()['items']], ['Need', 'Recent'])
        ca.dismiss(self.db, need)
        items = self.home()['items']
        self.assertEqual([(item['name'], item['needs']) for item in items], [('Need', False), ('Recent', False)],
                         'dismissed, it no longer needs you; you looked at it just now, so it leads what you were in')

    def test_the_lead_names_each_thread_that_moved_and_what_it_did(self):
        # The morning-after mockup, from records: a question, a usage limit, a finished run.
        ideas, picker, board = self.conversation('Ideas storage'), self.conversation('Connector picker'), self.conversation('Live board')
        end = self.now - 8 * HOUR
        self.session(end)
        self.task(ideas, 'ready_for_review', moved=end + HOUR, checked=0, next_step='Should an archived idea keep its threads?')
        self.task(picker, 'blocked', run_state='failed', provider='claude', failure='usage_limit', moved=end + 2 * HOUR,
                  error='Claude Code reached its usage limit. It had changed a.py, b.py, c.py. Those edits are in your project folder, not committed.')
        self.task(board, 'ready_for_review', moved=end + 3 * HOUR, files=('board.tsx',), checked=0)
        found = self.home()
        self.assertEqual(found['lead'], '**Ideas storage** has a question for you. **Connector picker** stopped at Claude’s usage limit. '
                                        '**Live board** finished and passed its checks.')
        self.assertEqual([item['name'] for item in found['items']], ['Ideas storage', 'Connector picker', 'Live board'])
        self.assertEqual(found['counts'], dict(threads=3, running=0, moved=3, finished=2, stopped=1, asking=1))

    def test_more_than_three_moved_threads_are_counted_after_the_named_ones(self):
        end = self.now - 8 * HOUR
        self.session(end)
        for n in range(4):
            self.task(self.conversation(f'Done {n}'), 'answered', moved=end + HOUR + n)
        self.task(self.conversation('Broke'), 'blocked', run_state='failed', error='The server is overloaded.', moved=end + 5 * HOUR)
        lead = self.home()['lead']
        self.assertEqual(lead, '**Broke** stopped: The server is overloaded. **Done 3** answered. **Done 2** answered. 2 more finished.')

    def test_a_review_waiting_from_before_your_last_visit_still_needs_you(self):
        """Design review, 8 Oct: Home files a review under "Waiting on you", so the head can't say nothing needs you."""
        end = self.now - 8 * HOUR
        self.task(self.conversation('Ideas storage'), 'ready_for_review', moved=end - HOUR, checked=0)
        self.session(end)
        self.task(self.conversation('Live board'), 'working', running=True, step='Editing the board', moved=self.now - HOUR)
        self.assertEqual(self.home()['lead'], 'One run is still working.')

    def test_a_run_with_no_task_yet_reads_as_running_like_thread_states(self):
        """Design review, 8 Oct: the sidebar showed it running while the pickup had no running row."""
        channel = self.conversation('Warm start')
        with self.db:
            self.db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)', ('task-none', channel, 'running', 'Reading the repo', self.now))
        item = next(row for row in self.home()['items'] if row['id'] == channel)
        self.assertEqual((item['kind'], item['line']), ('running', 'Reading the repo.'))
        import thread_states
        record = next(row for row in thread_states.changes(self.db, 'local-user', 0)['threads'] if row['id'] == channel)
        self.assertEqual((record['running'], record['state']), (True, 'working'))

    def test_mid_flight_says_what_is_working_and_that_nothing_needs_you(self):
        end = self.now - 8 * HOUR
        self.session(end)
        for name in ('Connector picker', 'Live board'):
            self.task(self.conversation(name), 'working', running=True, step='Editing ' + name, moved=self.now - HOUR)
        found = self.home()
        self.assertEqual(found['lead'], 'Two runs are working and nothing needs you yet.')
        self.assertEqual(found['counts']['running'], 2)
        finished = self.conversation('Ideas storage')
        self.task(finished, 'ready_for_review', moved=end + HOUR, checked=0)
        found = self.home()
        self.assertEqual(found['lead'], '**Ideas storage** finished and passed its checks. Two runs are still working.')
        self.assertEqual([item['name'] for item in found['items']], ['Connector picker', 'Live board', 'Ideas storage'],
                         'running before finished work that waits for review')

    def test_a_quiet_morning_says_nothing_moved_and_what_you_were_in_the_middle_of(self):
        board = self.conversation('Live board')
        end = self.now - 8 * HOUR
        self.session(end)
        self.task(board, 'ready_for_review', moved=end - HOUR, checked=0, next_step='Review the same-file warning, then commit')
        self.seen(board, end - 60)
        found = self.home()
        self.assertRegex(found['lead'], r'^Nothing moved since \d{1,2}:\d\d [AP]M\. You were in the middle of \*\*Live board\*\*\.$')
        self.assertEqual([item['name'] for item in found['items']], ['Live board'], 'shown even when nothing changed')
        self.assertEqual(found['counts'], dict(threads=1, running=0, moved=0, finished=0, stopped=0, asking=0))

    def test_a_thread_named_after_a_long_first_message_is_cut_short_in_the_lead(self):
        # 18 Sep, Bryce's real project: "You were in the middle of I think it would be good to do a full design sweep as a
        # really critical design person who is…" read as a paragraph.
        long = self.conversation('I think it would be good to do a full design sweep as a really critical design person who is')
        self.task(long, 'blocked', run_state='failed', error='Claude Code was overloaded.', next_step='Frontend fixes in Orchestrator.tsx: ' + 'x ' * 120)
        self.seen(long, self.now - 60)
        lead = self.home()['lead']
        self.assertEqual(lead, '**I think it would be good to do a full design…** stopped: Claude Code was overloaded.')

    def test_your_private_threads_are_yours_and_nobody_elses_are(self):
        mine = self.conversation('Pricing notes', private=True)
        self.task(mine, 'answered')
        other = self.conversation('Theirs', private=True)
        with self.db:
            self.db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (other,))
        self.task(other, 'answered')
        items = self.home()['items']
        self.assertEqual([(item['name'], item['private']) for item in items], [('Pricing notes', True)])
        with self.assertRaises(Rejected):
            self.thread(other)

    def test_it_works_with_no_account_signed_in_and_no_model(self):
        # The fixture's accounts are Codex and none of them is signed in; Home's model can't run here at all.
        sidebar = self.conversation('Sidebar polish')
        self.task(sidebar, 'answered')
        with patch('project_orchestrator.ask', side_effect=AssertionError('the pickup must never call a model')), \
                patch('project_orchestrator.Orchestrator.account', side_effect=AssertionError('nor look up an account')):
            self.assertEqual(self.home()['items'][0]['name'], 'Sidebar polish')
            self.assertTrue(self.thread(sidebar)['lines'])

    def test_an_empty_project_says_so(self):
        found = self.home()
        self.assertEqual((found['items'], found['more'], found['middle']), ([], 0, None))
        self.assertEqual(found['lead'], 'Nothing here yet. Start a thread, or ask below.')
        self.assertEqual(found['counts']['threads'], 0)


class ThreadTests(PickupFixture):
    def test_three_lines_and_your_visit_before_this_one(self):
        sweep = self.conversation('Design sweep')
        self.task(sweep, 'blocked', run_state='interrupted', step='Editing Knowledge.tsx', next_step='Releases after Knowledge',
                  asked='do a full design sweep', error='Stopped before completion', moved=self.now - HOUR)
        with patch('conversation_activity.time.time', return_value=self.now - 2 * HOUR):
            ca.mark_seen(self.db, sweep)
        ca.mark_seen(self.db, sweep)  # opening it now
        ca.mark_seen(self.db, sweep)  # and a look a moment later, while it's on screen
        found = self.thread(sweep)
        self.assertEqual(found['lines'], ['Last time: you asked “do a full design sweep”.',
                                          'You paused this. It was on: Editing Knowledge.tsx.',
                                          'Next: Releases after Knowledge.'])
        self.assertAlmostEqual(found['seen'], self.now - 2 * HOUR, delta=1, msg='the visit before this one, not this one')
        self.assertTrue(found['changed'], 'it stopped after you were last in it')

    def test_the_asked_line_is_one_readable_line_without_markdown(self):
        sweep = self.conversation('Design sweep')
        self.task(sweep, 'answered', answer='Done.', asked='Add a retry to `upload_client.py` and **test it** against the slow path, '
                  'the fast path, the timeout path and the path where the server closes the socket halfway through')
        line = self.thread(sweep)['lines'][0]
        self.assertEqual(line, 'Last time: you asked “Add a retry to upload_client.py and test it against the slow path, the fast path, the…”',
                         'cut at a whole word, no full stop after the ellipsis')
        self.assertNotIn('`', line)
        self.assertNotIn('*', line)

    def test_your_words_are_quoted_so_they_read_as_yours(self):
        self.assertEqual(pickup.quoted('Add a retry to the upload client.'), '“Add a retry to the upload client”.')
        self.assertEqual(pickup.quoted('why is it slow?'), '“why is it slow?”')

    def test_a_thread_with_no_work_has_no_pickup_and_other_projects_are_refused(self):
        empty = self.conversation('Empty')
        self.assertEqual(self.thread(empty)['lines'], [])
        elsewhere = self.conversation('Elsewhere', project='other')
        with self.assertRaises(Rejected):
            self.thread(elsewhere)
        with self.assertRaises(Rejected):
            self.host.dispatch('conversation_pickup', dict(projectId='p', channelId=empty, extra=1))


class VoiceTests(unittest.TestCase):
    def test_pickup_lines_reach_the_screen_without_em_dashes(self):
        import pickup
        self.assertEqual(pickup.clip('Wrote the store \u2014 tests pass', 80), 'Wrote the store - tests pass')
        self.assertNotIn('\u2014', pickup.clip('A \u2014 B \u2014 C', 80))


if __name__ == '__main__':
    unittest.main()


class AccountTests(PickupFixture):
    """Handoff map, 19 Sep 2026, H1: a thread you moved to another account, or one left behind when the project's
    default account changed, is still on Home and says where it is."""

    def setUp(self):
        super().setUp()
        self.host.store.add_profile('c', 'claude', self.root / 'c', 'c@example.test', 'personal')
        self.host.store.add_profile('w', 'codex', self.root / 'w', 'w@example.test', 'work')

    def move(self, channel, profile):
        with self.db:
            self.db.execute('UPDATE threads SET profile_id=? WHERE id=?', (profile, channel))

    def test_a_thread_moved_to_another_account_stays_on_home_and_says_so(self):
        self.session(self.now - 8 * HOUR)
        alpha, beta, work = (self.conversation(name) for name in ('Alpha', 'Beta', 'Work'))
        self.task(alpha, 'ready_for_review', files=('a.py',), checked=0)
        self.task(beta, 'answered', answer='Done.')
        self.task(work, 'answered', answer='Never here.')
        self.move(work, 'w')
        found = self.home()
        self.assertEqual([(item['name'], item['account']) for item in found['items']], [('Alpha', None), ('Beta', None)],
                         'a work account is never on Home; the project\'s own account carries no word')
        self.move(beta, 'c')  # "Move to Claude" from the account picker
        found = self.home()
        beta_item = next(item for item in found['items'] if item['name'] == 'Beta')
        self.assertEqual((beta_item['account'], beta_item['line'], beta_item['did']), ('Claude', 'On Claude. Answered: Done.', 'On Claude. Answered: Done.'))
        self.assertIn('**Beta** answered.', found['lead'])
        self.move(beta, 'b')  # another account of the same provider as the project's
        beta_item = next(item for item in self.home()['items'] if item['name'] == 'Beta')
        self.assertEqual((beta_item['account'], beta_item['line']), ('another Codex account', 'On another Codex account. Answered: Done.'))
        # The thread's own recap carries no account word: its chip already says which account it is on.
        self.assertEqual((self.thread(beta)['account'], self.thread(beta)['line']), (None, 'Answered: Done.'))

    def test_changing_the_projects_default_account_keeps_every_thread_on_home(self):
        alpha, beta = self.conversation('Alpha'), self.conversation('Beta')
        self.task(alpha, 'ready_for_review', files=('a.py',), checked=0)
        self.task(beta, 'answered', answer='Done.')
        self.host.store.bind_project('p', 'c', 'claude-model')  # project_profile_set: only future threads start on it
        found = self.host.dispatch('project_pickup', dict(projectId='p', profileId='c'))
        self.assertEqual([(item['name'], item['account']) for item in found['items']], [('Alpha', 'Codex'), ('Beta', 'Codex')])
        self.assertNotEqual(found['lead'], 'Nothing here yet. Start a thread, or ask below.')
        self.assertEqual(found['counts']['threads'], 2)
        with self.assertRaises(Rejected):
            self.host.dispatch('project_pickup', dict(projectId='p', profileId='a'))  # the old account no longer opens the project


class RestartTests(PickupFixture):
    """Handoff map, 19 Sep 2026, H3: after a crash, Home never says a dead run is still working."""

    def test_a_run_dervo_died_with_reads_as_unconfirmed_and_first(self):
        gamma, alpha = self.conversation('Gamma'), self.conversation('Alpha')
        self.task(alpha, 'ready_for_review', files=('a.py',), checked=0)
        self.task(gamma, 'working', running=True, step='Editing greeting.py', moved=self.now - 20 * 60)
        found = self.home()
        self.assertEqual([item['kind'] for item in found['items']], ['running', 'review'], 'this Dervo is driving it')
        with self.db:  # Dervo dies and comes back; the coding runtime hasn't reconciled the record yet
            self.db.execute("INSERT INTO session_marks VALUES ('start', ?)", (self.now - 60,))
        found = self.home()
        item = found['items'][0]
        self.assertEqual((item['name'], item['kind'], item['needs'], item['failed'], item['line']), ('Gamma', 'unconfirmed', True, False, pickup.RESTARTED))  # unknown, not failed (17.1)
        self.assertEqual(item['did'], 'Dervo restarted. It can’t tell how far this got. It was on: Editing greeting.py.')
        self.assertEqual(item['next'], 'Look at what it left, then resume it.')
        self.assertEqual(found['counts']['running'], 0)
        self.assertIn('**Gamma** stopped, and Dervo couldn’t confirm how far it got.', found['lead'])
        self.assertNotIn('working', found['lead'])
        recap = self.thread(gamma)
        self.assertEqual(recap['lines'][1], pickup.RESTARTED + ' It was on: Editing greeting.py.')
        # Once the coding runtime has reconciled it, the same words come from the record itself.
        self.task(gamma, 'disconnected', run_state='uncertain', step='Editing greeting.py', moved=self.now,
                  error='Host restarted before task confirmation. Inspect evidence. No automatic replay.')
        self.assertEqual(self.facts(gamma)['line'], pickup.RESTARTED)
        # Negative control: a run that started after this Dervo did is running, and Alpha is untouched.
        delta = self.conversation('Delta')
        self.task(delta, 'working', running=True, step='Reading README', moved=self.now)
        kinds = {item['name']: item['kind'] for item in self.home()['items']}
        self.assertEqual((kinds['Delta'], kinds['Alpha']), ('running', 'review'))


class StopAndRoomTests(PickupFixture):
    """Orchestrator map, 19 Sep 2026, F5 and F12."""

    def test_your_own_stop_is_not_blocked_on_you_but_a_failure_is(self):
        stopped, failed, review = (self.conversation(name) for name in ('Stopped', 'Failed', 'Review'))
        self.task(stopped, 'blocked', run_state='interrupted', error='Stopped before completion', moved=self.now - 60)
        self.task(failed, 'blocked', run_state='failed', error='Claude Code was overloaded.', moved=self.now - 2 * 3600)
        self.task(review, 'ready_for_review', moved=self.now - 3600)
        found = self.home()
        self.assertEqual([(item['name'], item['needs']) for item in found['items']], [('Failed', True), ('Review', True), ('Stopped', False)],
                         'the failure is first; your own stop is just what you were in most recently')
        by_name = {item['name']: item for item in found['items']}
        self.assertEqual((by_name['Stopped']['line'], by_name['Stopped']['failed']), ('You paused this.', False))
        self.assertEqual((by_name['Failed']['line'], by_name['Failed']['failed']), ('Stopped. Claude Code was overloaded.', True))
        self.assertEqual(found['counts']['asking'], 0)
        self.session(self.now - 8 * HOUR)
        self.assertIn('**Stopped** was stopped by you.', self.home()['lead'])

    def test_everything_blocked_on_you_or_running_is_shown_whatever_the_cap(self):
        for n in range(6):
            self.task(self.conversation(f'Run {n}'), 'working', running=True, step='Working', moved=self.now - n)
        self.task(self.conversation('Asked'), 'ready_for_review', next_step='Keep the old name?')
        for n in range(3):
            self.task(self.conversation(f'Done {n}'), 'answered', answer='Yes.')
        found = self.home()
        self.assertEqual(len(found['items']), 7, 'six running and one question: all shown')
        self.assertEqual(found['items'][0]['name'], 'Asked')
        self.assertEqual(found['more'], 3, 'only the answered ones fold')
        # Negative control: with nothing running or blocked, the cap is the cap.
        with self.db:
            self.db.execute('DELETE FROM coding_tasks'); self.db.execute('DELETE FROM conversation_task_state')
        for n in range(7):
            self.task(self.conversation(f'Quiet {n}'), 'answered', answer='Yes.')
        found = self.home()
        self.assertEqual((len(found['items']), found['more']), (pickup.SHOWN, 7 - pickup.SHOWN), 'the earlier threads have no task now, so they are not counted')


class ContinuityLineTests(PickupFixture):
    """Handoff map, 19 Sep 2026, H2, H4, H5: the thread says in one line what its newest message's session was given,
    and when a note went unread."""

    def with_continuity(self, name, continuity, **extra):
        channel = self.conversation(name)
        record = self.task(channel, 'answered', answer='Done.', **extra)
        record['continuity'] = continuity
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(record), record['id']))
        return channel

    def test_a_fresh_session_says_what_it_got_and_a_continued_one_says_nothing(self):
        cases = {
            'You chose to start fresh.': 'You started fresh, so it got a summary of the last 6 messages.',
            'The earlier Claude Code session isn’t saved on this Mac any more.': 'Claude couldn’t reopen the earlier session, so it got a summary of the last 6 messages instead.',
            'The last run failed partway, so this starts a new session with a summary of the chat.': 'The last run didn’t end cleanly, so this one started with a summary of the last 6 messages.',
            'The last run ended unexpectedly, so this starts a new session with a summary of the chat.': 'The last run didn’t end cleanly, so this one started with a summary of the last 6 messages.',
            'The last message never started.': 'The last run didn’t end cleanly, so this one started with a summary of the last 6 messages.',
            'Codex gets a summary of this chat, not the full session.': 'Codex started fresh with a summary of the last 6 messages.',
            'Moved from Codex. Claude gets a summary of this chat.': 'Moved from Codex, so Claude started fresh with a summary of the last 6 messages.',
            'Switched to another account. Its sessions are kept separately.': 'Switched account, so it started fresh with a summary of the last 6 messages.',
            'Full access was turned on.': 'Full access was turned on. It started fresh with a summary of the last 6 messages.',
        }
        for reason, line in cases.items():
            channel = self.with_continuity(reason[:20], dict(state='fresh', reason=reason, from_task_id='x', session_id=None))
            recap = self.thread(channel)
            self.assertEqual(recap['continuity'], line, reason)
            self.assertEqual(recap['lines'][-1], line)
            self.assertNotIn('—', line)
        for continuity in (dict(state='continues', reason=None, from_task_id='x', session_id='s'), dict(state='first', reason=None), None):
            recap = self.thread(self.with_continuity('carries on', continuity))
            self.assertIsNone(recap['continuity'], continuity)
            self.assertEqual(len(recap['lines']), 2)

    def test_a_note_the_run_never_read_is_said_and_a_read_one_is_not(self):
        stopped = self.conversation('Stopped')
        record = self.task(stopped, 'blocked', run_state='interrupted', error='Stopped before completion', provider='claude')
        record['messages'].append(dict(role='user', text='also rename greet', steer='sent', run_id=record['runs'][-1]['id']))
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(record), record['id']))
        recap = self.thread(stopped)
        self.assertEqual(recap['note'], 'Claude stopped before reading your note. It goes with your next message.')
        self.assertEqual(recap['lines'][-1], recap['note'])
        record['messages'][-1]['steer'] = 'read'
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(record), record['id']))
        self.assertIsNone(self.thread(stopped)['note'])
        running = self.conversation('Running')
        record = self.task(running, 'working', running=True, step='Working')
        record['messages'].append(dict(role='user', text='and this', steer='sent', run_id=record['runs'][-1]['id']))
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(record), record['id']))
        self.assertIsNone(self.thread(running)['note'], 'while it runs, the composer already says the note was added to the run')


class WorkLineTests(PickupFixture):
    """The thread pickup says which bugs and ideas the thread is on, and which its approved commit marked done (Bugs
    and ideas, 19 Sep). From the list's records; a thread on no item says nothing."""
    def test_the_thread_pickup_carries_its_items_lines_and_a_thread_on_none_carries_nothing(self):
        releases = patch('project_ideas.releases_root', return_value=self.root / 'Dervo Releases')  # never the Mac's own
        releases.start()
        self.addCleanup(releases.stop)
        ideas = self.host.ideas()
        bug = ideas.dispatch('ideas_item_add', dict(self.scope, kind='bug', name='Crash on export', text='On big files.'), 'local-user')['items'][0]
        on_it, other = self.conversation('Export crash'), self.conversation('Something else')
        self.task(on_it, 'working', running=True, step='Reading export.py')
        self.task(other, 'idle', commit='b' * 40)
        ideas.link_work('p', bug['id'], on_it, 'local-user', 'home')
        self.assertEqual([(line['kind'], line['line']) for line in self.thread(on_it)['work']], [('working', 'Working on bug #1, Crash on export.')])
        self.assertEqual(self.thread(other)['work'], [], 'negative control: a committed thread with no linked item')
        ideas.work_committed('p', on_it, 'a' * 40, 'task-' + on_it)
        [line] = self.thread(on_it)['work']
        self.assertEqual((line['kind'], line['line'], line['can_undo'], line['undone'], line['project_id'], line['profile_id']),
                         ('done', 'Marked bug #1 done.', True, False, 'p', 'a'))
        self.assertEqual([(item['id'], item['was'], item['name'], item['target']) for item in line['items']], [(bug['id'], 'building', 'Crash on export', None)])
        self.assertEqual(self.thread(other)['work'], [])


class ResultTests(PickupFixture):
    """Home's useful row (16.2, #30): the one sentence is the task guide's goal from the current run, and the files,
    checks and "not checked" are the ones Dervo recorded. No model call, and nothing borrowed from an earlier run."""
    def guide(self, document, **fields):
        document['runs'][-1]['guide'] = dict(run_id=document['runs'][-1]['id'], **fields)
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(document), document['id']))

    def test_a_finished_run_carries_its_goal_files_checks_and_limits(self):
        review = self.conversation('Platform helpers')
        document = self.task(review, 'ready_for_review', files=('backend/platform_os.py', 'backend/file_actions.py', 'backend/platform_os.py'), checked=0)
        self.guide(document, goal='Opening and revealing files works on **Windows**.', step='Done', next='Your review',
                   limits='Not run on a Windows machine.')
        facts = self.facts(review)
        self.assertEqual(facts['task_id'], 'task-' + review)
        self.assertEqual(facts['result'], 'Opening and revealing files works on Windows.', 'the goal in plain words')
        self.assertEqual((facts['files'], facts['files_count']), (['backend/platform_os.py', 'backend/file_actions.py'], 2))
        self.assertEqual((facts['checks_line'], facts['unchecked']), ('Checks passed.', 'Not run on a Windows machine.'))
        self.assertEqual(facts['line'], 'Finished. 3 files changed. Checks passed.', 'the status line is unchanged, repeat and all')

    def test_a_goal_from_an_earlier_run_is_not_this_runs_result(self):
        review = self.conversation('Old goal')
        document = self.task(review, 'ready_for_review', files=('a.py',), checked=1)
        document['runs'][-1]['guide'] = dict(run_id='an-earlier-run', goal='Something the last run did.', limits='Old limits.')
        with self.db:
            self.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(document), document['id']))
        facts = self.facts(review)
        self.assertEqual((facts['result'], facts['unchecked'], facts['checks_line']), (None, None, 'A check failed.'))

    def test_a_running_run_counts_what_it_has_written_and_names_none(self):
        running = self.conversation('Thinking row')
        document = self.task(running, 'working', running=True, step='Editing ChatMotion.css')
        document['runs'][-1]['written'] = ['/p/frontend/ChatMotion.css', '/p/frontend/ChatMotion.css']
        document['tokens'] = [dict(tool='native:Edit', path='/p/frontend/Chat.tsx', run_id=document['runs'][-1]['id'], state='delivered'),
                              dict(tool='native:Read', path='/p/frontend/Other.tsx', run_id=document['runs'][-1]['id'], state='delivered')]
        self.guide(document, goal='The thinking row shows the moment you send.', step='Editing ChatMotion.css')
        facts = self.facts(running)
        self.assertEqual((facts['result'], facts['now']), ('The thinking row shows the moment you send.', 'Editing ChatMotion.css'))
        self.assertEqual((facts['files'], facts['files_count'], facts['checks_line'], facts['unchecked']), ([], 2, None, None))

    def test_many_files_are_named_up_to_the_limit_and_counted(self):
        review = self.conversation('Many files')
        self.task(review, 'ready_for_review', files=tuple(f'f{n}.py' for n in range(12)), checked=0)
        facts = self.facts(review)
        self.assertEqual((len(facts['files']), facts['files_count'], facts['result']), (pickup.FILES_SHOWN, 12, None))

    def test_the_project_pickup_carries_the_fields_and_a_thread_with_no_task_has_them_empty(self):
        answered = self.conversation('Question')
        self.task(answered, 'answered', answer='The port is 8818.')
        [item] = [item for item in self.home()['items'] if item['id'] == answered]
        self.assertEqual((item['files'], item['files_count'], item['checks_line']), ([], 0, None))
        empty = pickup.thread_facts(dict(id='x', name='Nothing yet'), None, None, None)
        self.assertEqual((empty['task_id'], empty['result'], empty['files'], empty['files_count']), (None, None, [], 0))
