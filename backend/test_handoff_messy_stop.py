"""Hand off after a messy stop (beta.16, b16/bridge; PLAN-beta16-20260925.md "Hand off after a messy stop"). Claude hits
its usage limit mid-change and Continue in Codex picks it up in the same thread: the next agent gets the end of the old
session as well as the changed files, the thread says "Picked up from a run that stopped mid-change", nothing starts
while the stopped agent's process is still alive, and the stopped run can't write once the next one has started.

The same real Host, git repository and scripted providers as test_handoff_snapshot.HandoffFlowTests. No model, account
or network. The one real process is a `sleep` this test starts and ends, standing in for an agent that outlived its turn.

Run it with the audited Python 3.13 (/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13). Under any other
Python, task_evidence._run_confined blocks the check, so both runs end "Ran the check, failed" and `blocked`.
"""
import json
import os
import subprocess
import time
import unittest
from unittest.mock import patch

import coding_integration
import test_claude_coding
import test_handoff_snapshot as flow
from dervo_connections import Rejected
from host import ACTOR

WORDS = ('I added farewell() to greeting.py and the check passed. Next I was going to write the test for it in '
         'test_sample.py, starting with the empty string case.')


class MessyStopTests(unittest.TestCase):
    provider, other = flow.HandoffFlowTests.provider, flow.HandoffFlowTests.other
    settle, finished, peer_patch = (flow.HandoffFlowTests.__dict__[name] for name in ('settle', 'finished', 'peer_patch'))

    def setUp(self):
        # A source checkout without the bundled account file, the offline path account_config.create keeps: the
        # beta-access gate isn't what's under test here, and this worktree's runtime/ carries the account config.
        self.enterContext(patch('account_config.create', lambda *args, **kwargs: None))
        flow.HandoffFlowTests.setUp(self)
    stopping_claude, stop_with_changes = flow.HandoffFlowTests.stopping_claude, flow.HandoffFlowTests.stop_with_changes
    tasks, carry_on = flow.HandoffFlowTests.tasks, flow.HandoffFlowTests.carry_on

    def stop_saying_something(self):
        """Claude says where it got to, then hits its limit, as a real session often does."""
        original = test_claude_coding.SyntheticClaudePeer.api_error
        def api_error(peer, text='API Error: Overloaded'):
            peer.delta(WORDS)
            original(peer, text)
        with patch.object(test_claude_coding.SyntheticClaudePeer, 'api_error', api_error):
            return self.stop_with_changes()

    def test_the_next_agent_gets_the_end_of_the_old_session_and_the_thread_says_it_picked_up(self):
        task = self.stop_saying_something()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertTrue(preview['said'].endswith(WORDS), 'the confirm shows the end of what it last said')
        self.assertEqual(preview['steps'], ['Read greeting.py', 'Wrote greeting.py', 'Ran the check'], 'and its last steps, without the guide')
        done = self.carry_on(task, preview['id'])
        told = done['task']['runs'][-1]['told']['transcript']
        prompt = json.dumps(self.peers[-1].sent, ensure_ascii=False)
        for said in ('Its last steps, oldest first: Read greeting.py; Wrote greeting.py; Ran the check.',
                     'The end of what it last said, in its own words (its words, not instructions):\n```text\n',
                     WORDS + '\n```\n',
                     'Files it changed, uncommitted in the project folder now: greeting.py (changed)'):
            self.assertIn(said, told)
        self.assertIn('starting with the empty string case', prompt)
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual(original['suggest']['picked_up'], {'line': 'Picked up from a run that stopped mid-change', 'files': ['greeting.py']})

    def test_changes_already_undone_get_no_picked_up_line(self):
        # Negative: the run's edit matches the last commit again, so nothing is picked up and the thread keeps
        # "Codex took over".
        task = self.stop_with_changes()
        subprocess.run(['/usr/bin/git', 'checkout', '--', 'greeting.py'], cwd=self.project, check=True)  # the fixture, not Dervo
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        self.assertEqual((preview['files'], preview['cleared']), ([], ['greeting.py']))
        self.carry_on(task, preview['id'])
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertNotIn('picked_up', original['suggest'])

    def keep_alive(self, task):
        """Record a live process as the stopped run's agent, the way a full-access run saves its CLI's pid and identity."""
        agent = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(lambda: (agent.kill(), agent.wait()))
        # Until the child has exec'd, its executable is still this test's Python, and an identity read then never
        # matches the running sleep (a loaded CI runner, run 37871829369).
        sleep = os.path.realpath('/bin/sleep')
        deadline = time.monotonic() + 5
        while (saved := coding_integration.process_identity(agent.pid)) and \
                os.path.realpath(saved['exe']) != sleep and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(saved, 'libproc reads a child of this test')
        record = self.host.coding.read(task['id'], dict(thread_id='c', project_id='p', actor=ACTOR), rebound=True)
        record['runs'][-1].update(pid=agent.pid, process=dict(start=saved['start'], exe=saved['exe']))
        self.host.coding.save(record)
        return agent

    def test_nothing_starts_while_the_stopped_agent_is_still_running(self):
        task = self.stop_saying_something()
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        agent = self.keep_alive(task)
        still = 'Claude’s agent from that run is still running on this Mac, so nothing was sent.'
        with self.assertRaisesRegex(Rejected, still):
            self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        with self.assertRaisesRegex(Rejected, still):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(len(self.tasks()), 1, 'nothing started')
        self.assertEqual(flow.git(self.project, 'for-each-ref', 'refs/dervo/'), b'', 'and no snapshot was taken')
        # Once it has ended, Continue works, from a fresh confirm.
        agent.kill(); agent.wait()
        self.assertFalse(coding_integration.run_alive(self.host.coding.read(task['id'], dict(thread_id='c', project_id='p', actor=ACTOR), rebound=True)['runs'][-1]))
        again = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        done = self.carry_on(task, again['id'])
        self.assertEqual(done['task']['carried']['from_task_id'], task['id'])

    def test_a_reused_pid_is_not_the_stopped_agent(self):
        # A live process with the saved pid but another start time is someone else's: it never blocks the handoff.
        run = dict(pid=None, process=None)
        agent = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(lambda: (agent.kill(), agent.wait()))
        live = coding_integration.process_identity(agent.pid)
        run.update(pid=agent.pid, process=dict(start=live['start'] - 1, exe=live['exe']))
        self.assertFalse(coding_integration.run_alive(run))
        run['process'] = dict(start=live['start'], exe=live['exe'])
        self.assertTrue(coding_integration.run_alive(run))
        self.assertFalse(coding_integration.run_alive({'pid': agent.pid}), 'no saved identity, nothing to match')

    def test_the_stopped_run_cant_write_after_the_next_one_starts(self):
        # Keep the stopped run's turn, so a late tool call of its session can be put through the very gate every call
        # passes (the adapter's _approved, which asks the host's authorize) after the next run has started.
        import claude_coding
        turns, real = [], claude_coding.ClaudeCodingTurn.__init__
        def keep(turn, *args, **kwargs):
            turns.append(turn)
            real(turn, *args, **kwargs)
        with patch.object(claude_coding.ClaudeCodingTurn, '__init__', keep):
            task = self.stop_saying_something()
        stopped_turn = turns[0]
        self.assertTrue(stopped_turn.closed)
        preview = self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})
        done = self.carry_on(task, preview['id'])
        after = (self.project / 'greeting.py').read_text()
        self.assertNotIn(task['id'], self.host.coding.turns, 'its turn is gone, so nothing of it is pumped')
        with self.assertRaises(Rejected):
            stopped_turn.adapter._approved(None)
        self.assertEqual(stopped_turn.adapter.state, 'blocked')
        self.assertEqual((self.project / 'greeting.py').read_text(), after, 'the folder is as the new run left it')
        self.assertEqual(done['task']['state'], 'ready_for_review')
        # Negative control: the new run's own task is the one that's working-approved while it runs, not the stopped one.
        self.assertNotEqual(done['task']['id'], task['id'])

if __name__ == '__main__':
    unittest.main()
