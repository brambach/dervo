"""The run lifecycle through the real host process (headless_driver.HostDriver), with the fake Claude CLI.

These start ``host.py`` as a subprocess, so what they see is what the app sees: a request's reply, in order, with
background work only between requests. Nothing calls a model. Each test leaves no host and no fake CLI running.
"""
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path

from headless_driver import HostDriver, HostError, make_project


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)  # Windows: a file still open can't be removed
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name).resolve()
        self.project = make_project(self.scratch / 'proj')
        self.host = HostDriver(self.scratch / 'data').start()
        self.addCleanup(self.host.close)
        self.ids = self.host.claude_project(self.project)
        # Beta.18: these runs are about a thread in the project folder, as before thread folders, so the thread is
        # flipped there the way the composer chip does it (as test_run_path does).
        self.host.call('thread_folder_choose', **self.ids.scope, choice='project')

    def running_view(self):
        """What the three read-only surfaces say about the thread, with no coding op sent: the sidebar's activity
        index, the thread's pickup and Home's project pickup."""
        activity = self.host.call('conversations', projectId=self.ids.project)[0]['activity']
        thread = self.host.call('conversation_pickup', **self.ids.scope)
        home = self.host.call('project_pickup', projectId=self.ids.project, profileId=self.ids.profile)
        return activity, thread, home


class RelaunchAfterKill(Base):
    def test_the_first_listing_after_a_kill_and_relaunch_already_shows_the_run_as_not_running(self):
        """run-lifecycle.md, finding 2. Before: the restart pass waited for the first coding op, so `conversations`
        said running, pickup said running and Home said "One run is working" for a run whose host was gone."""
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40)
        task = self.host.request(self.ids, 'Take your time')
        self.host.wait_for_run(self.ids, task)
        # Negative control: while the run is live, the same three reads do say running.
        activity, thread, home = self.running_view()
        self.assertTrue(activity['running'])
        self.assertEqual(thread['kind'], 'running')
        self.assertIn('working', home['lead'])
        self.host.kill()
        self.host.start()
        activity, thread, home = self.running_view()  # before any coding op
        self.assertFalse(activity['running'])
        self.assertEqual(activity['ended'], 'disconnected')
        self.assertNotEqual(thread['kind'], 'running')
        self.assertNotIn('working', home['lead'])
        status = self.host.call('status')
        self.assertEqual([r['taskId'] for r in status['lostRuns']], [task])
        self.assertEqual(status['lostRuns'][0]['channelId'], self.ids.thread)
        self.assertEqual(status['lostRuns'][0]['name'], 'probe')
        self.assertIn('Dervo closed while a run was going in probe', status['lostRunsDetail'])
        detail = self.host.task(self.ids, task)
        self.assertEqual((detail['state'], detail['runs'][-1]['state']), ('disconnected', 'uncertain'))
        self.assertIn('Dervo closed while this was running', detail['error'])
        # The line is about this host's start: the next relaunch, with nothing left open, has none.
        self.host.shutdown()
        self.host.start()
        status = self.host.call('status')
        self.assertEqual((status['lostRuns'], status['lostRunsDetail']), ([], None))

    def test_a_run_killed_before_the_task_was_sent_reads_as_unsent_and_can_go_again(self):
        """run-lifecycle.md, finding 11. A run still `checking` (account check, launch, handshake) when the host died
        never got the task text, so it isn't an unknown outcome: blocked, nothing sent, send it again."""
        import json
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40)
        task = self.host.request(self.ids, 'Take your time')
        self.host.wait_for_run(self.ids, task)
        self.host.kill()
        # Rewind the record to what a kill during the account check leaves: working, run still checking.
        import sqlite3
        db = sqlite3.connect(self.scratch / 'data' / 'conversations.sqlite3')
        saved = json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?', (task,)).fetchone()[0])
        saved['runs'][-1]['state'] = 'checking'
        db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task))
        db.commit()
        db.close()
        self.host.start()
        detail = self.host.task(self.ids, task)
        self.assertEqual((detail['state'], detail['runs'][-1]['state']), ('blocked', 'failed'))
        self.assertEqual(detail['error'], 'Dervo closed before this started. Nothing was sent. Send it again.')
        self.assertEqual(self.host.call('status')['lostRuns'], [])  # nothing was lost; nothing to check
        activity = self.host.call('conversations', projectId=self.ids.project)[0]['activity']
        self.assertFalse(activity['running'])


@unittest.skipIf(os.name == 'nt', 'a Windows host takes its runs with it (Job Objects), so nothing survives to reap: '
                                   'NoSurvivorOnWindows')
class SurvivorAfterKill(Base):
    """Night 1, finding 2. Persona 1's real Claude kept editing for 23 s after `kill -9` of the host and the relaunch
    didn't stop it: the identity saved at launch was read while the launcher was still `/bin/sh` about to exec the CLI,
    so the restart pass never matched the live process. Now the identity is refreshed once the run has started, the
    survivor is reaped, and the task says what the folder holds instead of "couldn't confirm"."""
    def full_access(self):
        self.ids = self.host.claude_project(self.project, name='fa', full_access=True)
        self.host.call('thread_folder_choose', **self.ids.scope, choice='project')
        return self.project / 'greeting.py'

    def settled_after_close(self, task):
        """The task once the relaunch's folder read has landed. Since 20 Sep (security review, M1) the read runs on a
        thread after the task is saved and lands on the host's tick, so the record says ``checking`` for a moment."""
        self.host.wait_until(lambda: 'checking' not in (self.host.task(self.ids, task)['runs'][-1].get('after_close') or {}),
                             timeout=15, what='the folder read landed')
        return self.host.task(self.ids, task)

    def test_a_full_access_survivor_is_stopped_on_relaunch_and_its_edits_are_named(self):
        target = self.full_access()
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40, result_text='Working on it.',
                           hang_write=dict(path='greeting.py', text='def greet(name):\n    return f"Hi, {name}"\n', after=2))
        task = self.host.request(self.ids, 'Shorten the greeting')
        detail = self.host.wait_for_run(self.ids, task)
        run = detail['runs'][-1]
        from coding_integration import process_identity
        self.assertEqual(run['process']['exe'], process_identity(run['pid'])['exe'], 'the saved identity is the CLI after the launcher exec')
        self.host.kill()
        self.host.wait_until(lambda: 'Hi,' in target.read_text(), timeout=10, what='the fake kept editing after the host died')
        self.assertEqual(self.host.provider_children(), [run['pid']], 'the CLI outlived its host')
        self.host.start()
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=5, what='survivor reaped on relaunch')
        detail = self.host.task(self.ids, task)
        self.assertEqual((detail['state'], detail['runs'][-1]['state']), ('disconnected', 'uncertain'), 'marked before the folder read')
        detail = self.settled_after_close(task)
        after = detail['runs'][-1]['after_close']
        self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=True, changed=['greeting.py']))
        # Beta.18: the provider's record is read too (lost_runs). The fake CLI writes no session file, so the record is
        # unreadable, the survivor gets today's SIGKILL rather than Stop's SIGINT, and today's words stay.
        self.assertEqual((after['record']['state'], after['stopped_with']), ('unreadable', 'SIGKILL'))
        self.assertIn('Dervo closed while this was running', detail['error'])
        self.assertIn('kept working until Dervo stopped it on this relaunch: greeting.py changed', detail['error'])
        self.assertNotIn('can’t say how it ended', detail['error'])
        recap = self.host.call('conversation_pickup', **self.ids.scope)
        self.assertEqual(recap['line'], 'Stopped when Dervo closed. It kept working: 1 file changed, not committed.')
        self.assertIn('Review greeting.py before you carry on.', recap['did'])
        home = self.host.call('project_pickup', projectId=self.ids.project, profileId=self.ids.profile)
        self.assertIn('stopped when Dervo closed. It kept working: 1 file changed', home['lead'])
        self.assertNotIn('couldn’t confirm', home['lead'])

    def test_a_full_access_run_that_finished_on_its_own_is_credited_with_its_edits(self):
        target = self.full_access()
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=3, result_text='Working on it.',
                           hang_write=dict(path='greeting.py', text='def greet(name):\n    return f"Hi, {name}"\n', after=1))
        task = self.host.request(self.ids, 'Shorten the greeting')
        self.host.wait_for_run(self.ids, task)
        self.host.kill()
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=10, what='the fake ended on its own')
        self.assertIn('Hi,', target.read_text())
        self.host.start()
        detail = self.settled_after_close(task)
        after = detail['runs'][-1]['after_close']
        self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=False, changed=['greeting.py']))
        self.assertEqual(after['record']['state'], 'unreadable')
        self.assertNotIn('stopped_with', after, 'nothing was left to stop')
        self.assertIn('kept working and finished on its own: greeting.py changed', detail['error'])

    def test_a_restricted_survivor_is_stopped_too_and_keeps_the_plain_words(self):
        # Negative control: without full access the CLI can't touch the folder once the host is gone (every edit goes
        # through the host), so there's no changed-files line, only the reap and the old wording.
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40, result_text='Working on it.')
        task = self.host.request(self.ids, 'Take your time')
        run = self.host.wait_for_run(self.ids, task)['runs'][-1]
        self.host.kill()
        self.assertEqual(self.host.provider_children(), [run['pid']])
        self.host.start()
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=5, what='survivor reaped on relaunch')
        detail = self.settled_after_close(task)  # beta.18: the record read lands on the tick, like the folder read
        after = detail['runs'][-1]['after_close']
        self.assertEqual({key: after[key] for key in ('reaped', 'changed')}, dict(reaped=True, changed=None))
        self.assertEqual((after['record']['state'], after['stopped_with']), ('unreadable', 'SIGKILL'))
        from coding_integration import RESTART_LOST
        self.assertEqual(detail['error'], RESTART_LOST)
        self.assertIn('couldn’t confirm how far it got', self.host.call('conversation_pickup', **self.ids.scope)['line'])
        self.assertEqual((self.project / 'greeting.py').read_text(), 'def greet(name):\n    return f"Hello, {name}"\n')


@unittest.skipUnless(os.name == 'nt', 'Windows Job Objects')
class NoSurvivorOnWindows(Base):
    def test_a_killed_host_takes_its_run_with_it_and_the_relaunch_says_so(self):
        """What SurvivorAfterKill reaps on a Mac never exists on Windows: the run is in the host's Job Object, which
        ends everything in it when the host ends however it ends (platform_os.contain). A write the run would have made
        4 s in never happens."""
        late = self.scratch / 'late-write.txt'
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40, result_text='Working on it.',
                           hang_write=dict(path=str(late), text='written after the host died', after=4))
        task = self.host.request(self.ids, 'Take your time')
        self.host.wait_for_run(self.ids, task)
        killed = time.monotonic()
        self.host.kill()
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=5, what='the run ended with its host')
        time.sleep(max(0.0, 6 - (time.monotonic() - killed)))
        self.assertFalse(late.exists())
        self.host.start()
        detail = self.host.task(self.ids, task)
        self.assertEqual((detail['state'], detail['runs'][-1]['state']), ('disconnected', 'uncertain'))
        self.assertIn('Dervo closed while this was running', detail['error'])


class CleanQuit(Base):
    def test_a_quit_mid_run_stops_the_agent_and_the_relaunch_says_so(self):
        """S7: `_shutdown` with a run open. The host pauses the fake CLI before it exits. A run that finishes its step
        within QUIT_PAUSE_SECONDS reads as an ordinary pause (29 Sep); one that doesn't reads disconnected with the quit
        wording. Either way the relaunch has nothing lost to announce (close() already marked it)."""
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40)
        task = self.host.request(self.ids, 'Take your time')
        self.host.wait_for_run(self.ids, task)
        started = time.monotonic()
        self.assertEqual(self.host.shutdown(), 0)
        self.assertLess(time.monotonic() - started, 4, 'the app gives the host 4 s to exit (bridge.rs Worker::drop)')
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=5, what='fake CLI gone')
        self.host.start()
        detail = self.host.task(self.ids, task)
        ended = (detail['state'], detail['runs'][-1]['state'])
        self.assertIn(ended, [('blocked', 'interrupted'), ('disconnected', 'uncertain')])
        if ended[0] == 'disconnected':
            self.assertIn('Dervo quit mid-run and paused it', detail['error'])
        self.assertEqual(self.host.call('status')['lostRuns'], [])


class PlainAnswer(Base):
    def test_a_plain_answer_ends_answered_with_nothing_running_and_nothing_left_behind(self):
        """S1, the worked example, and the driver's own contract: timed calls, an {"error"} reply raises."""
        task = self.host.request(self.ids, 'Is greeting.py fine?')
        ended = self.host.wait_for_end(self.ids, task)
        self.assertEqual((ended['state'], [r['state'] for r in ended['runs']]), ('answered', ['completed']))
        activity, thread, home = self.running_view()
        self.assertFalse(activity['running'])
        self.assertEqual(activity['ended'], 'answered')
        self.assertEqual(self.host.provider_children(), [])
        self.assertLess(self.host.slowest[0], 5)
        with self.assertRaises(HostError) as refused:
            self.host.call('task_detail', **self.ids.scope, taskId='missing')
        self.assertEqual(refused.exception.operation, 'task_detail')
        self.assertEqual(len(self.host.calls(self.ids.profile)), 2, 'auth status, then the run')


if __name__ == '__main__':
    unittest.main()


class HostErrorText(unittest.TestCase):
    def test_str_carries_the_operation_and_the_hosts_error_text(self):
        """The request args live in ``request``: assigning them to ``args`` overwrote BaseException.args, and str()
        then read as the tuple of field names instead of what the host said."""
        error = HostError('task_review', {'projectId': 'p', 'channelId': 'c', 'decision': 'approved'}, 'Review requires current completed evidence')
        self.assertEqual(str(error), 'task_review: Review requires current completed evidence')
        self.assertEqual(error.request, {'projectId': 'p', 'channelId': 'c', 'decision': 'approved'})
        self.assertEqual((error.operation, error.text), ('task_review', 'Review requires current completed evidence'))
        # Negative control: the old reading, the field names as a tuple, must not be what str() gives.
        self.assertNotEqual(str(error), str(('projectId', 'channelId', 'decision')))
        self.assertNotIn("('projectId'", str(error))
