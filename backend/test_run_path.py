"""One task from Home and one in a thread, Stop mid-run, resume after it, and the account and exact-model checks, through
the real host process and a real child process (headless_driver.HostDriver, fake Claude CLI). b16/windows milestone 3.

The same tests run on a Mac and on the Windows build runner, and expect the same outcomes: on Windows the run is a
fake-claude.exe in its own Job Object, Stop is Ctrl+Break to its group, and the session file Claude Code resumes from
is found by the run folder's name. Nothing here signs in or calls a model, so what only a real Claude Code account on a
Windows PC shows (its own sign-in, its own Ctrl+Break handling, where it really keeps sessions) isn't covered.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

from headless_driver import HostDriver, HostError, make_project

OTHER_ACCOUNT = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max',
                 'email': 'someone-else@example.com'}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name).resolve()
        self.project = make_project(self.scratch / 'proj')
        self.host = HostDriver(self.scratch / 'data').start()
        self.addCleanup(self.host.close)
        self.ids = self.host.claude_project(self.project)
        # Beta.18: these runs are about a thread in the project folder, as before thread folders, so the thread is
        # flipped there the way the composer chip does it. A thread on its own branch is test_thread_folders'.
        self.host.call('thread_folder_choose', **self.ids.scope, choice='project')

    def launches(self):
        """The fake CLI's launches for this profile that were runs, not ``auth status``."""
        return [c['argv'] for c in self.host.calls(self.ids.profile) if 'auth' not in c['argv']]

    def flag(self, argv, name):
        return next((a.split('=', 1)[1] for a in argv if a.startswith(name + '=')), None)


class HomeAndThread(Base):
    def test_a_home_task_and_a_thread_task_each_answer_and_leave_nothing_running(self):
        """Each run works in its own new scratch folder here, so the thread's message only continues the Home task's
        session if Dervo moved the session file under the folder name Claude Code will look in (place_session)."""
        self.host.scenario(self.ids.profile, result_text='Nothing to change.', save_session=True)
        home = self.host.request(self.ids, 'Is greeting.py fine?', origin='home', clicked=True)
        ended = self.host.wait_for_end(self.ids, home)
        self.assertEqual((ended['state'], [r['state'] for r in ended['runs']]), ('answered', ['completed']))
        self.assertEqual(self.host.provider_children(), [])
        thread = self.host.request(self.ids, 'And test_sample.py?')
        ended = self.host.wait_for_end(self.ids, thread)
        self.assertEqual((ended['state'], [r['state'] for r in ended['runs']]), ('answered', ['completed']))
        self.assertEqual(self.host.provider_children(), [])
        runs = self.launches()
        self.assertEqual(len(runs), 2)
        for argv in runs:
            self.assertEqual(argv[argv.index('--model') + 1], 'claude-fake-model', 'the exact model the project picked')
        self.assertIn('--fork-session', runs[1], 'the thread message continues the Home task’s session')
        self.assertEqual(self.flag(runs[1], '--resume'), self.flag(runs[0], '--session-id'))
        self.assertLess(self.host.slowest[0], 5, 'no request near the bridge’s 10 s')


class AccountAndModel(Base):
    def test_another_account_is_refused_before_a_run_starts(self):
        """The account check runs on `auth status` alone: a different signed-in account never gets a run or a word of
        the task."""
        self.host.scenario(self.ids.profile, auth=OTHER_ACCOUNT)
        try:
            task = self.host.request(self.ids, 'Is greeting.py fine?')
        except HostError:
            pass  # refused on the request itself
        else:
            ended = self.host.wait_for_end(self.ids, task)
            self.assertNotEqual(ended['state'], 'answered')
            self.assertNotIn('completed', [r['state'] for r in ended['runs']])
        self.assertEqual(self.launches(), [], 'no run was launched')
        self.assertFalse((self.scratch / 'data' / 'profiles' / self.ids.profile / 'received.jsonl').exists(),
                         'nothing was sent to the CLI')
        self.assertEqual(self.host.provider_children(), [])

    def test_a_run_on_a_different_model_does_not_count_as_answered(self):
        self.host.scenario(self.ids.profile, init_model='claude-other-model')
        task = self.host.request(self.ids, 'Is greeting.py fine?')
        ended = self.host.wait_for_end(self.ids, task)
        self.assertNotEqual(ended['state'], 'answered')
        self.assertNotEqual(ended['runs'][-1]['state'], 'completed')
        self.assertEqual(self.host.provider_children(), [])


class StopAndResume(Base):
    """Without full access (milestone 4), as a Windows task runs today. The fake's late write goes to an absolute path
    outside the run's own folder, so any process of the stopped run still alive 4 s in leaves a file behind."""

    def test_stop_mid_run_ends_the_whole_run_its_late_write_never_lands_and_the_next_message_resumes(self):
        late = self.scratch / 'late-write.txt'
        self.host.scenario(self.ids.profile, mode='hang', hang_seconds=40, result_text='Working on it.', save_session=True,
                           hang_write=dict(path=str(late), text='written after Stop', after=4))
        task = self.host.request(self.ids, 'Shorten the greeting')
        self.host.wait_for_run(self.ids, task)
        stopped_at = time.monotonic()
        self.host.call('task_stop', **self.ids.scope, taskId=task)
        ended = self.host.wait_for_end(self.ids, task, timeout=20)
        took = time.monotonic() - stopped_at
        self.assertEqual((ended['state'], ended['runs'][-1]['state']), ('blocked', 'interrupted'))
        self.assertLess(took, 8, 'the CLI took the Stop itself, before Dervo’s 10 s kill')
        self.host.wait_until(lambda: not self.host.provider_children(), timeout=10, what='the run and its children gone')
        time.sleep(max(0.0, 6 - (time.monotonic() - stopped_at)))  # past the fake's 4 s write, with room
        self.assertFalse(late.exists(), 'nothing from the stopped run wrote anything after Stop')

        first = self.launches()[-1]
        self.host.scenario(self.ids.profile, result_text='Picked up where we stopped.', save_session=True)
        follow = self.host.request(self.ids, 'Carry on, but keep the greeting as it is')
        ended = self.host.wait_for_end(self.ids, follow)
        self.assertEqual((ended['state'], [r['state'] for r in ended['runs']]), ('answered', ['completed']), ended.get('error'))
        second = self.launches()[-1]
        self.assertIn('--fork-session', second, 'the next message continues the stopped session')
        self.assertEqual(self.flag(second, '--resume'), self.flag(first, '--session-id'))
        self.assertEqual(self.host.provider_children(), [])


class OwnBranch(unittest.TestCase):
    """A thread on its own branch (the default) runs in its worktree. Its first run moves it there, and the open
    thread has to keep reading and say the next message continues (polisher, 8 Oct)."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        scratch = Path(self.temp.name).resolve()
        self.host = HostDriver(scratch / 'data').start()
        self.addCleanup(self.host.close)
        self.ids = self.host.claude_project(make_project(scratch / 'proj'))

    def test_the_first_run_keeps_the_feed_and_the_next_message_continues(self):
        self.host.scenario(self.ids.profile, result_text='Nothing to change.', save_session=True)
        cursor = self.host.call('conversation_feed', **self.ids.scope, cursor=None, force=False)['cursor']
        before = self.host.call('binding', **self.ids.scope)['cwd']
        self.host.request(self.ids, 'Is greeting.py fine?')  # queued until the worktree is ready, so no task id yet
        tasks = self.host.wait_until(lambda: (t := self.host.call('tasks', **self.ids.scope)) and t[0]['state'] != 'working' and t)
        self.assertEqual(tasks[0]['state'], 'answered')
        after = self.host.call('binding', **self.ids.scope)['cwd']
        if sys.platform == 'win32':  # threads work in the project folder on Windows for now (thread_folders.NOT_ON_WINDOWS)
            self.assertEqual(after, before, 'a Windows thread stays in the project folder')
        else:
            self.assertNotEqual(after, before, 'the run moved into its worktree')
        self.host.call('conversation_feed', **self.ids.scope, cursor=cursor, force=False)  # no "Reload before continuing"
        continuity = self.host.call('conversation_status', **self.ids.scope)['coding']['continuity']
        self.assertEqual((continuity['state'], continuity['reason']), ('continues', None))


if __name__ == '__main__':
    unittest.main()
