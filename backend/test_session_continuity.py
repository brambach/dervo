"""Which session a message continues, after a run that didn't end cleanly. Handoff map, 19 Sep 2026, H2 and H5.

The older continuity tests (clean ends, a Stop, account and mode changes, Start fresh) are in test_project_memory
SessionContinuityTests and still hold. These cover what changed on 19 Sep: a provider-side failure with every tool call
settled continues the session, a message that never started is walked past and named, and an unread mid-run note is
carried by the since-note. No model, no host: a disposable store only."""
import unittest

import session_continuity
from test_project_memory import Coding, OLD, task


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.coding = Coding()
        self.binding = dict(provider='claude', profile_id='claude-1', cwd='/project')
        self.context = dict(project_id='p', thread_id='c', actor='owner')

    def decide(self):
        return session_continuity.decide(self.coding.store.db, self.context, self.binding, full_access=False, read_only=False)

    def note(self, from_task_id):
        return session_continuity.since_note(self.coding.store.db, from_task_id, dict(id='next', context=self.context))

    def failed(self, id, *, kind='overloaded', tokens=(), error='Claude Code was overloaded.'):
        record = task(self.coding, id, state='blocked', run_state='failed', error=error)
        if kind:
            record['runs'][-1]['provider_failure'] = kind
        record['tokens'] = [dict(token=f't{n}', state=state, run_id=record['runs'][-1]['id']) for n, state in enumerate(tokens)]
        return self.coding.save(record)

    def test_computer_access_change_starts_fresh_in_both_directions(self):
        previous = task(self.coding, 'one', state='answered', run_state='completed', full_access=True)
        for old, new in ((False, True), (True, False)):
            previous['computer_access'] = old
            self.coding.save(previous)
            decision = session_continuity.decide(self.coding.store.db, self.context, self.binding,
                full_access=True, read_only=False, computer_access=new)
            self.assertEqual(decision['state'], 'fresh')
            self.assertIn('Computer access changed', decision['reason'])

    def test_the_first_coding_message_after_the_overview_starts_fresh_without_a_word(self):
        """Onboarding review, 9 Oct: a new person's first follow-up in "Project overview" read "New session · Switched
        between asking about the project and coding in it" and "a summary of the last 6 messages" after two."""
        import pickup
        db = self.coding.store.db
        db.execute("CREATE TABLE conversation_origins (thread_id TEXT PRIMARY KEY, origin TEXT NOT NULL)")
        task(self.coding, 'overview', state='answered', run_state='completed', read_only=True)
        decide = lambda **kw: session_continuity.decide(db, self.context, self.binding, read_only=False, **kw)
        # Negative control: an ordinary thread still says why.
        self.assertIn('asking about the project', decide(full_access=False)['reason'])
        db.execute("INSERT INTO conversation_origins VALUES ('c','overview')")
        for full_access in (False, True):
            decision = decide(full_access=full_access)
            self.assertEqual((decision['state'], decision['reason'], decision.get('quiet')), ('fresh', None, True))
            self.assertIsNone(pickup.continuity_line(dict(continuity=decision)))
        # Only the first follow-up: after a coding message, the plain rules apply again.
        task(self.coding, 'code', state='answered', run_state='completed')
        task(self.coding, 'ask', state='answered', run_state='completed', read_only=True)
        self.assertIn('asking about the project', decide(full_access=False)['reason'])

    def test_the_fresh_line_says_how_many_messages_the_summary_really_holds(self):
        import pickup
        line = lambda size: pickup.continuity_line(dict(continuity=dict(state='fresh', reason='You chose to start fresh.', summary_size=size)))
        self.assertEqual(line(1), 'You started fresh, so it got a summary of the last message.')
        self.assertEqual(line(2), 'You started fresh, so it got a summary of the last 2 messages.')

    def test_a_provider_side_failure_with_settled_tool_calls_continues_the_session(self):
        # Bryce, 19 Sep: "a thread on Claude hits a limit" used to mean the next message got a six-message summary and
        # the loss compounded. The session file holds everything up to the failure, as it does after a Stop.
        self.failed('one', kind='usage_limit', tokens=('delivered', 'cancelled'), error='Claude Code reached its usage limit.')
        decision = self.decide()
        self.assertEqual(decision, dict(state='continues', reason=None, from_task_id='one', session_id=OLD))
        note = self.note('one')
        self.assertIn('stopped partway when the provider failed: Claude Code reached its usage limit.', note)
        self.assertIn('carry on from where you stopped', note)
        for kind in ('overloaded', 'rate_limit', 'server_error', 'connection'):
            self.failed('two', kind=kind)
            self.assertEqual(self.decide()['state'], 'continues', kind)

    def test_a_failure_with_a_tool_call_still_open_or_without_a_provider_cause_starts_fresh(self):
        # Negative controls. An unsettled tool call means Dervo can't say what the session saw; a failure with no
        # recorded provider cause is the rule test_project_memory still pins.
        self.failed('one', kind='overloaded', tokens=('delivered', 'uncertain'))
        decision = self.decide()
        self.assertEqual(decision['state'], 'fresh')
        self.assertIn('failed partway', decision['reason'])
        self.failed('two', kind=None, error='The check runner crashed.')
        decision = self.decide()
        self.assertEqual(decision['state'], 'fresh')
        self.assertIn('failed partway', decision['reason'])
        self.assertIn('ended without a reviewable result: The check runner crashed.', self.note('two'))

    def test_a_message_that_never_started_is_walked_past_and_named(self):
        task(self.coding, 'one', state='answered', run_state='completed')
        never = task(self.coding, 'two', state='blocked', session=None, text='Also rename greet to hello', error='Claude Code is not installed.')
        never['runs'] = []
        self.coding.save(never)
        decision = self.decide()
        self.assertEqual(decision, dict(state='continues', reason=None, from_task_id='one', session_id=OLD),
                         'the launch failure holds no session: the clean message before it does')
        note = self.note('one')
        self.assertIn('never reached an agent, so you have not seen it: Also rename greet to hello', note)
        # Negative controls: a message that did reach an agent is never skipped, and one waiting for permission is
        # still "running" rather than skipped.
        task(self.coding, 'three', state='answered', run_state='completed')
        self.assertEqual(self.decide()['from_task_id'], 'three')
        self.assertNotIn('never reached', self.note('three'))
        pending = task(self.coding, 'four', state='needs_permission', session=None)
        pending['runs'] = []
        self.coding.save(pending)
        self.assertEqual(self.decide()['reason'], 'The last message is still running.')

    def test_when_nothing_ever_started_the_next_message_is_fresh_and_says_so(self):
        never = task(self.coding, 'one', state='blocked', session=None)
        never['runs'] = []
        self.coding.save(never)
        self.assertEqual(self.decide()['reason'], 'The last message never started.')
        self.assertEqual(session_continuity.skipped_after(self.coding.store.db, self.context, 'missing'), [])

    def test_a_note_the_run_never_read_goes_with_the_next_message(self):
        # Handoff map H5: a message steered into a Claude run that ends before its next step stayed steer 'sent' and
        # was never mentioned again. A note the run did read is not repeated.
        stopped = task(self.coding, 'one', state='blocked', run_state='interrupted', error='Stopped by you.')
        stopped['messages'] += [dict(role='user', text='Also rename greet to hello', steer='sent', run_id='run-one'),
                                dict(role='user', text='Keep the tests green', steer='read', run_id='run-one')]
        self.coding.save(stopped)
        self.assertEqual(self.decide()['state'], 'continues')
        note = self.note('one')
        self.assertIn('the run ended before you read it: Also rename greet to hello', note)
        self.assertNotIn('Keep the tests green', note)
        read = task(self.coding, 'two', state='answered', run_state='completed')
        read['messages'].append(dict(role='user', text='One more thing', steer='read', run_id='run-two'))
        self.coding.save(read)
        self.assertNotIn('ended before you read it', self.note('two'))


class HomeJobTests(unittest.TestCase):
    """Night 1, finding 4. In a full-access project an unclicked Home job runs without full access (H1). The next
    thing you type in that thread runs with it, and the flip used to read as "Full access was turned on." and throw
    the session away. Nobody turned anything on: the limit was the job's."""
    def setUp(self):
        self.coding = Coding()
        self.binding = dict(provider='claude', profile_id='claude-1', cwd='/project')
        self.context = dict(project_id='p', thread_id='c', actor='owner')

    def decide(self, full_access=True):
        return session_continuity.decide(self.coding.store.db, self.context, self.binding, full_access=full_access, read_only=False)

    def test_typing_after_an_unclicked_home_job_continues_its_session(self):
        task(self.coding, 'job', state='answered', run_state='completed', requested_by='home', clicked=False)
        self.assertEqual(self.decide(), dict(state='continues', reason=None, from_task_id='job', session_id=OLD))
        # A Home job saved before ``clicked`` existed on the record reads the same way.
        task(self.coding, 'older', state='answered', run_state='completed', requested_by='home')
        self.assertEqual(self.decide()['state'], 'continues')

    def test_a_real_switch_still_starts_fresh(self):
        # Negative controls: a typed message that ran without full access, a clicked Home job (it had full access if
        # the project did), and turning full access off after a Home job all keep the plain rule.
        task(self.coding, 'typed', state='answered', run_state='completed')
        decision = self.decide()
        self.assertEqual((decision['state'], decision['reason']), ('fresh', 'Full access was turned on.'))
        task(self.coding, 'clicked', state='answered', run_state='completed', requested_by='home', clicked=True)
        self.assertEqual(self.decide()['reason'], 'Full access was turned on.')
        task(self.coding, 'job', state='answered', run_state='completed', requested_by='home', clicked=False, full_access=True)
        self.assertEqual(self.decide(full_access=False)['reason'], 'Full access was turned off.')
        self.assertFalse(session_continuity.home_limited(dict(requested_by='home', clicked=False, full_access=False), False))


class PreviewLineTests(unittest.TestCase):
    def test_the_composer_says_what_a_fresh_session_gets_and_nothing_when_it_continues(self):
        from session_continuity import preview_line
        fresh = lambda reason, size=6: preview_line(dict(state='fresh', reason=reason, summary_size=size))
        self.assertEqual(fresh('Codex gets a summary of this chat, not the full session.'), 'Codex starts fresh. It gets a summary of the last 6 messages.')
        self.assertEqual(fresh('You chose to start fresh.', 1), 'Next message starts a new session with a summary of the last message.')
        self.assertEqual(fresh('The last run failed partway, so this starts a new session with a summary of the chat.', 3),
                         'The last run didn’t end cleanly, so the next message starts with a summary of the last 3 messages.')
        self.assertEqual(fresh('The project folder changed.'), 'The project folder changed. The next message starts fresh with a summary of the last 6 messages.')
        # Negative controls: continuing, a first message, and a fresh start with nothing earlier say nothing.
        self.assertIsNone(preview_line(dict(state='continues', reason=None, summary_size=None)))
        self.assertIsNone(preview_line(dict(state='first', reason=None, summary_size=None)))
        self.assertIsNone(fresh('You chose to start fresh.', 0))


if __name__ == '__main__':
    unittest.main()


class CheckFactsTests(unittest.TestCase):
    """Critical review, 20 Sep, H1: write, check, write, usage limit was handed on as "Files it changed: greeting.py,
    utils.py / Check: passed", because check_facts took the last attempt that had a test, not the last attempt. The
    check line now names what changed after the check as unchecked."""
    PASSED = dict(exit_status=0, stopped=None, stdout='', stderr='')
    FAILED = dict(exit_status=1, stopped=None, stdout='FAILED (failures=1)', stderr='')

    def setUp(self):
        import json
        self.json = json
        self.coding = Coding()
        self.db = self.coding.store.db
        self.db.execute('CREATE TABLE task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')

    @staticmethod
    def attempt(kind, *, changed=None, test=None, cut=False):
        scope = ('greeting.py', 'utils.py')
        before = {p: dict(sha256='v1-' + p) for p in scope}
        after = {p: dict(sha256='v1-' + p) for p in scope}
        if changed:
            after[changed] = dict(sha256='v2-' + changed)
        return dict(kind=kind, files=list(scope), before=before, after=None if cut else after, diff={changed: '+x'} if changed else {},
                    test=test, state='uncertain' if cut else ('verified' if test else 'prepared'))

    def told(self, attempts, **extra):
        self.db.execute('INSERT OR REPLACE INTO task_evidence VALUES (?,?)', ('t1', self.json.dumps(dict(test_file='test_sample.py', attempts=attempts))))
        record = dict(id='t1', state='blocked', messages=[dict(role='user', text='Add a farewell', created_at=1)],
                      runs=[dict(id='r1', state='failed', written=['greeting.py', 'utils.py'])], tokens=[],
                      error='Claude Code stopped: your Max plan usage limit was reached.', **extra)
        import conversation_coding
        return conversation_coding.handoff(record, db=self.db)

    def test_a_file_written_after_the_check_is_named_as_unchecked(self):
        told = self.told([self.attempt('change', changed='greeting.py'), self.attempt('verification', test=self.PASSED),
                          self.attempt('change', changed='utils.py')])
        self.assertIn('- Files it changed: greeting.py, utils.py\n', told)
        self.assertIn('- Check (test_sample.py): passed, but utils.py changed after it ran and isn’t checked\n', told)
        self.assertIn('- How it ended: Claude Code stopped', told)
        self.assertEqual(session_continuity.check_facts(self.db, dict(id='t1'))[0],
                         'passed, but utils.py changed after it ran and isn’t checked')

    def test_control_a_check_that_covered_every_write_still_says_passed(self):
        told = self.told([self.attempt('change', changed='greeting.py'), self.attempt('change', changed='utils.py'),
                          self.attempt('verification', test=self.PASSED)])
        self.assertIn('- Check (test_sample.py): passed\n', told)
        self.assertNotIn('changed after it ran', told)
        self.assertEqual(session_continuity.check_facts(self.db, dict(id='t1')), ('passed', 'test_sample.py', ''))

    def test_a_failed_check_stays_failed_and_the_later_write_is_still_named(self):
        told = self.told([self.attempt('change', changed='greeting.py'), self.attempt('verification', test=self.FAILED),
                          self.attempt('change', changed='utils.py'), self.attempt('create', changed='greeting.py')])
        self.assertIn('- Check (test_sample.py): failed (exit 1), but utils.py, greeting.py changed after it ran and aren’t checked\n', told)
        self.assertIn('FAILED (failures=1)', told, 'the failing output still comes along')

    def test_a_write_cut_off_after_the_check_says_files_may_have_changed(self):
        told = self.told([self.attempt('change', changed='greeting.py'), self.attempt('verification', test=self.PASSED),
                          self.attempt('change', cut=True)])
        self.assertIn('- Check (test_sample.py): passed, but files may have changed after it ran and aren’t checked\n', told)

    def test_a_stale_completion_with_no_evidence_still_qualifies_the_check(self):
        record = dict(id='t2', state='ready_for_review', completion=dict(test_exit_status=0, stale=True, attempt=1))
        self.assertEqual(session_continuity.check_facts(self.db, record)[0], 'passed, but files changed after it ran and aren’t checked')
        # Negative control: a completion that is not stale is plain "passed".
        self.assertEqual(session_continuity.check_facts(self.db, dict(record, completion=dict(test_exit_status=0, attempt=1)))[0], 'passed')

    def test_a_named_write_beside_a_cut_off_one_says_possibly_more(self):
        """Reviewer, 20 Sep: attempt 3 changes utils.py, attempt 4 is cut off; naming only utils.py read as exhaustive."""
        told = self.told([self.attempt('change', changed='greeting.py'), self.attempt('verification', test=self.PASSED),
                          self.attempt('change', changed='utils.py'), self.attempt('change', cut=True)])
        self.assertIn('- Check (test_sample.py): passed, but utils.py and possibly more changed after it ran and aren’t checked\n', told)

    def test_many_later_changes_are_counted_not_listed(self):
        many = [self.attempt('change', changed=f'file{n}.py') for n in range(6)]
        for n, attempt in enumerate(many):
            attempt['before'] = {f'file{n}.py': dict(sha256='a')}
            attempt['after'] = {f'file{n}.py': dict(sha256='b')}
        told = self.told([self.attempt('verification', test=self.PASSED)] + many)
        self.assertIn('- Check (test_sample.py): passed, but 6 of the files above changed after it ran and aren’t checked\n', told)
        self.assertLess(len(told), 1200)

    def test_changed_after_reads_snapshots_diffs_and_new_files_in_order(self):
        created = dict(kind='create', files=['greeting.py', 'utils.py', 'new.py'], before={'greeting.py': dict(sha256='a')},
                       after={'greeting.py': dict(sha256='a'), 'new.py': dict(sha256='n')}, diff={}, test=None)
        self.assertEqual(session_continuity.changed_after([self.attempt('change', changed='utils.py'), created]), (['utils.py', 'new.py'], True))
        self.assertEqual(session_continuity.changed_after([self.attempt('verification', test=self.PASSED)]), ([], True))
        self.assertEqual(session_continuity.changed_after([self.attempt('change', cut=True)]), ([], False))
        self.assertEqual(session_continuity.changed_after([self.attempt('change', changed='utils.py'), self.attempt('change', cut=True)]), (['utils.py'], False))
