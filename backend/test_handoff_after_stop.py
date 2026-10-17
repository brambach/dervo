"""Continue after your own Stop (behaviour review N1; Bryce, 26 Sep: "yes"). Until now Continue in Codex or Claude was
offered only after a failure on the provider's side. Now it's offered after you stop a run yourself too, with the same
rules: nothing starts until you click, not while the stopped run is still settling or its agent is still alive, the
next agent gets the changed files and the end of the old session, the thread says it picked up, and the stopped run
can't write once the next one has started. After a Stop nothing ever switches by itself.

The same real Host, git repository and scripted providers as test_handoff_snapshot.HandoffFlowTests. No model, account
or network. Run it with the audited Python 3.13 (/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13).
"""
import json
import subprocess
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import coding_integration
import test_handoff_snapshot as flow
from dervo_connections import Rejected
from host import ACTOR, Host

WORDS = 'I added farewell() to greeting.py. Next I was going to write its test in test_sample.py, starting with the empty string.'
CTX = dict(thread_id='c', project_id='p', actor=ACTOR)


class StopThenContinueTests(unittest.TestCase):
    provider, other = flow.HandoffFlowTests.provider, flow.HandoffFlowTests.other
    settle, finished, peer_patch = (flow.HandoffFlowTests.__dict__[name] for name in ('settle', 'finished', 'peer_patch'))
    tasks, carry_on, thread_account = flow.HandoffFlowTests.tasks, flow.HandoffFlowTests.carry_on, flow.HandoffFlowTests.thread_account

    def setUp(self):
        # The beta-access gate isn't what's under test here (as in test_handoff_messy_stop).
        self.enterContext(patch('account_config.create', lambda *args, **kwargs: None))
        flow.HandoffFlowTests.setUp(self)

    def working_claude(self, write=True):
        """Claude reads greeting.py, adds a farewell (``write``), says where it got to and reports its next step, then
        keeps working, saying nothing more, until you press Stop."""
        from test_claude_coding import SyntheticClaudePeer, verified_auth, CLAUDE
        import claude_coding
        test = self

        class Peer(SyntheticClaudePeer):
            def __init__(peer, argv, **kwargs):
                super().__init__(argv, **kwargs)
                test.peers.append(peer)

            def on_prompt(peer):
                peer.tool('dervo_read_file', {'path': 'greeting.py'}, 1)

            def on_tool_result(peer, mid, result):
                if mid == 1 and write:
                    body = json.loads(result['content'][0]['text'])
                    peer.tool('dervo_write_file', dict(path='greeting.py', expected_hash=body['sha256'], content=body['content'] + flow.CLAUDE_EDIT), 2)
                elif mid in (1, 2):
                    peer.delta(WORDS)
                    peer.tool('dervo_update_task_guide', dict(goal='Add a farewell', step='Writing the farewell', why='You asked for one.',
                                                              next='Add a test for farewell in test_sample.py.', test_steps='Run the tests.',
                                                              limits='The test for farewell isn’t written yet.'), 4)
                # After 4 it keeps going and says nothing more: only your Stop ends it.
        stack = ExitStack()
        stack.enter_context(patch.object(claude_coding, 'auth_status', verified_auth))
        stack.enter_context(patch.object(claude_coding, 'executable', lambda: CLAUDE))
        stack.enter_context(patch.object(claude_coding, 'OwnedProcess', Peer))
        return stack

    def reported(self, detail):
        return any(t['tool'] == 'dervo_update_task_guide' and t['state'] == 'delivered' for t in detail['task']['tokens'])

    def stop_mid_change(self, write=True, before_stop=None):
        with self.working_claude(write):
            started = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'req-1', 'text': 'Add a farewell to greeting.py'})
            working = self.settle(started['taskId'], until=self.reported)['task']
            if before_stop:
                before_stop(working)
            self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            task = self.settle(started['taskId'], until=lambda d: d['task']['state'] != 'working')['task']
        self.assertEqual((task['state'], task['runs'][-1]['state']), ('blocked', 'interrupted'))
        self.assertNotIn('provider_failure', task['runs'][-1], 'nothing failed: you stopped it')
        return task

    def preview(self, task):
        return self.host.dispatch('coding_handoff_preview', {**self.scope, 'taskId': task['id']})

    # ------------------------------------------------------------------ stop
    def test_your_own_stop_offers_continue_and_the_next_agent_picks_up_the_files_and_the_end_of_the_session(self):
        task = self.stop_mid_change()
        self.assertEqual(task['suggest'], dict(reason='The user stopped it.', kind='stopped', other=task['suggest']['other'],
                                               automatic=False, taken=None, error=None))
        self.assertEqual(task['suggest']['other']['profile_id'], 'q')
        preview = self.preview(task)
        self.assertEqual((preview['here'], preview['to'], preview['stopped'], preview['kind']), ('Claude', 'Codex', 'The user stopped it.', 'stopped'))
        self.assertEqual(preview['files'], ['greeting.py'])
        self.assertTrue(preview['said'].endswith(WORDS), 'the confirm shows the end of what it last said')
        self.assertEqual(preview['next'], 'Add a test for farewell in test_sample.py.')
        done = self.carry_on(task, preview['id'])
        told = done['task']['runs'][-1]['told']['transcript']
        for said in ('It ran on Claude Code and stopped: The user stopped it.',
                     'Files it changed, uncommitted in the project folder now: greeting.py (changed)',
                     'Its last steps, oldest first: Read greeting.py; Wrote greeting.py.', WORDS):
            self.assertIn(said, told)
        self.assertIn('after you stopped', done['task']['route']['why'])
        self.assertEqual(done['task']['binding']['provider'], 'codex')
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(): return "hi"\n' + flow.CLAUDE_EDIT + self.appendix,
                         'Codex carried on in place from Claude’s change')
        original = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual(original['suggest']['picked_up'], {'line': 'Picked up from a run that stopped mid-change', 'files': ['greeting.py']})
        self.assertEqual(original['suggest']['taken'], done['task']['id'])
        # Home offers it too, and names who can carry on.
        item = next(i for i in self.host.dispatch('project_pickup', {'projectId': 'p', 'profileId': 'p'})['items'] if i['id'] == 'c')
        self.assertIsNone(item['handoff'], 'taken, so not offered again')

    def test_home_offers_continue_after_your_stop_and_says_you_stopped_it(self):
        self.stop_mid_change()
        item = next(i for i in self.host.dispatch('project_pickup', {'projectId': 'p', 'profileId': 'p'})['items'] if i['id'] == 'c')
        self.assertEqual((item['kind'], item['handoff'], item['failed']), ('stopped', 'Codex', False))

    def test_a_stop_with_nothing_changed_sends_the_request_again_there(self):
        task = self.stop_mid_change(write=False)
        preview = self.preview(task)
        self.assertEqual((preview['files'], preview['snapshot']), ([], None), 'nothing to save first')
        done = self.carry_on(task, preview['id'])
        self.assertEqual((done['task']['binding']['provider'], done['task']['carried']['snapshot']), ('codex', None))
        self.assertEqual(flow.git(self.project, 'for-each-ref', 'refs/dervo/'), b'', 'no snapshot was taken')
        self.assertNotIn('picked_up', self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']['suggest'])

    def test_after_a_stop_nothing_switches_by_itself_even_where_the_project_switches_automatically(self):
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        task = self.stop_mid_change()
        for _ in range(20):
            self.host.tick()
        self.assertEqual(len(self.tasks()), 1, 'nothing started without your click')
        self.assertFalse(task['suggest']['automatic'])
        self.assertEqual(self.thread_account(), 'p')

    def test_with_no_account_on_the_other_provider_nothing_is_offered(self):
        # Negative control: the other provider's account is only known from a thread that used it.
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='p' WHERE id='d'")
        task = self.stop_mid_change()
        self.assertNotIn('suggest', task)
        with self.assertRaisesRegex(Rejected, 'no other account to try'):
            self.preview(task)

    def test_nothing_is_offered_or_sent_while_the_run_is_still_working(self):
        def while_working(working):
            with self.assertRaisesRegex(Rejected, 'no other account to try'):
                self.preview(working)
            with self.assertRaisesRegex(Rejected, 'no other account to try'):
                self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': working['id']})
            self.assertNotIn('suggest', working)
        self.stop_mid_change(before_stop=while_working)
        self.assertEqual(len(self.tasks()), 1)

    def test_nothing_starts_while_the_stopped_agent_is_still_running(self):
        task = self.stop_mid_change()
        preview = self.preview(task)
        agent = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(lambda: (agent.kill(), agent.wait()))
        saved = coding_integration.process_identity(agent.pid)
        record = self.host.coding.read(task['id'], CTX, rebound=True)
        record['runs'][-1].update(pid=agent.pid, process=dict(start=saved['start'], exe=saved['exe']))
        self.host.coding.save(record)
        still = 'Claude’s agent from that run is still running on this Mac, so nothing was sent.'
        with self.assertRaisesRegex(Rejected, still):
            self.preview(task)
        with self.assertRaisesRegex(Rejected, still):
            self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(len(self.tasks()), 1, 'nothing started')
        agent.kill(); agent.wait()
        done = self.carry_on(task, self.preview(task)['id'])
        self.assertEqual(done['task']['carried']['from_task_id'], task['id'])

    # ------------------------------------------------------------------ resume
    def test_after_dervo_restarts_the_offer_is_still_there_and_continue_works(self):
        task = self.stop_mid_change()
        self.host.close()
        self.host = Host(self.base / 'store')
        again = self.host.dispatch('task_detail', {**self.scope, 'taskId': task['id']})['task']
        self.assertEqual((again['suggest']['kind'], again['suggest']['taken']), ('stopped', None))
        done = self.carry_on(again, self.preview(again)['id'])
        self.assertEqual((done['task']['binding']['provider'], done['task']['carried']['from_task_id']), ('codex', task['id']))
        self.assertIn('farewell', (self.project / 'greeting.py').read_text())

    def test_a_second_click_on_the_same_stop_starts_nothing_new(self):
        task = self.stop_mid_change(write=False)
        preview = self.preview(task)
        done = self.carry_on(task, preview['id'])
        again = self.host.dispatch('coding_retry_on', {**self.scope, 'taskId': task['id'], 'handoffId': preview['id']})
        self.assertEqual(again['taskId'], done['task']['id'], 'the task that took over, not another')
        self.assertEqual(len(self.tasks()), 2)
        with self.assertRaisesRegex(Rejected, 'already carried on'):
            self.preview(task)

    # ------------------------------------------------------------------ late result
    def test_the_stopped_run_cant_write_after_the_next_one_starts(self):
        import claude_coding
        turns, real = [], claude_coding.ClaudeCodingTurn.__init__

        def keep(turn, *args, **kwargs):
            turns.append(turn)
            real(turn, *args, **kwargs)
        with patch.object(claude_coding.ClaudeCodingTurn, '__init__', keep):
            task = self.stop_mid_change()
        stopped_turn = turns[0]
        self.assertTrue(stopped_turn.closed)
        done = self.carry_on(task, self.preview(task)['id'])
        after = (self.project / 'greeting.py').read_text()
        self.assertNotIn(task['id'], self.host.coding.turns, 'its turn is gone, so nothing of it is pumped')
        with self.assertRaises(Rejected):
            stopped_turn.adapter._approved(None)  # a late tool call from the stopped session
        self.assertEqual((self.project / 'greeting.py').read_text(), after, 'the folder is as the new run left it')
        self.assertEqual(done['task']['state'], 'ready_for_review')
        self.assertNotEqual(done['task']['id'], task['id'])


if __name__ == '__main__':
    unittest.main()
