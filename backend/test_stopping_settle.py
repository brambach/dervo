"""Beta.18, stopping direction B ("Settle it for you"): Leave it stopped reads as stopped, a lost run settles from the
provider's own record after a restart, an orphan that's still working gets Stop's SIGINT, and a commit from outside a run
no longer ends it.

The records below are saved fixtures in the shapes Claude Code 2.1 and Codex write (a session file, a rollout), one per
case the settle rules separate: a step boundary, a cut-off read, a cut-off Bash call, an interrupted Bash call, an
unreadable record. notes/STOPPING-explore-20261005.md, sections 2 and 5 B."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lost_runs
import native_evidence
import run_ending
from dervo_connections import Rejected

SID = '0b5c2f9e-4d3a-4e8b-9a71-2f6c1d0e8a55'
CWD = '/Users/someone/dervo/app'


def _line(kind, content, at, **extra):
    role = 'assistant' if kind == 'assistant' else 'user'
    return json.dumps({'parentUuid': None, 'isSidechain': False, 'userType': 'external', 'cwd': CWD, 'sessionId': SID,
                       'version': '2.1.284', 'type': kind, 'message': {'role': role, 'content': content}, 'uuid': f'u-{at}',
                       'timestamp': f'2026-10-05T00:{at:02d}:00.000Z', **extra})


def _use(tool_id, name, data, at):
    return _line('assistant', [{'type': 'tool_use', 'id': tool_id, 'name': name, 'input': data}], at)


def _result(tool_id, text, at, error=False):
    return _line('user', [{'tool_use_id': tool_id, 'type': 'tool_result', 'content': text, 'is_error': error}], at)


PROMPT = _line('user', 'Fold the sidebar when something beside the chat leaves it under 600 px.', 40)
# Every step it started ended, and it said what it did.
BOUNDARY = [PROMPT,
            _use('toolu_1', 'Read', {'file_path': f'{CWD}/frontend/src/local/DervoSidebar.tsx'}, 41), _result('toolu_1', '1\timport', 41),
            _use('toolu_2', 'Edit', {'file_path': f'{CWD}/frontend/src/local/DervoSidebar.tsx', 'old_string': 'a', 'new_string': 'b'}, 42),
            _result('toolu_2', 'The file has been updated.', 42),
            _use('toolu_3', 'Bash', {'command': 'pnpm test', 'description': 'Run the tests'}, 46), _result('toolu_3', 'ok', 47),
            _line('assistant', [{'type': 'text', 'text': 'Folded the sidebar and ran the tests.'}], 47)]
# The model was answering the last tool result when the host died: still working, but nothing is open.
BOUNDARY_THINKING = BOUNDARY[:-1]
# Cut off in a Read: the folder read settles it.
CUT_READ = BOUNDARY[:3] + [_use('toolu_9', 'Read', {'file_path': f'{CWD}/frontend/src/local/DervoSidebar.css'}, 46)]
# Cut off in a Bash call that reaches outside the folder: the one case that asks.
CUT_BASH = BOUNDARY[:5] + [_use('toolu_7', 'Bash', {'command': 'gh workflow run preview.yml\necho done', 'description': 'Start the preview'}, 46)]
# The same call after Stop's SIGINT: Claude Code wrote its interrupted result and its own note. The step still started.
INTERRUPTED_BASH = CUT_BASH + [_result('toolu_7', '[Request interrupted by user for tool use]', 47, error=True),
                               _line('user', [{'type': 'text', 'text': '[Request interrupted by user for tool use]'}], 47)]
# An interrupted call in an earlier turn was settled then (you pressed Stop and sent Resume): only this turn counts.
EARLIER_INTERRUPT = INTERRUPTED_BASH + [_line('user', 'Carry on from where you paused.', 50),
                                        _use('toolu_8', 'Read', {'file_path': f'{CWD}/a.py'}, 51), _result('toolu_8', 'a', 51),
                                        _line('assistant', [{'type': 'text', 'text': 'Done.'}], 52)]
CONNECTOR = BOUNDARY[:3] + [_use('toolu_5', 'mcp__github__create_issue', {'title': 'x'}, 44)]
DERVO_NOTE = BOUNDARY[:3] + [_use('toolu_6', 'mcp__dervo__dervo_update_task_guide', {'step': 'x'}, 44)]
DERVO_WRITE = BOUNDARY[:3] + [_use('toolu_6', 'mcp__dervo__dervo_write_file', {'path': 'a.py'}, 44)]


def _codex(kind, payload, at):
    return json.dumps({'timestamp': f'2026-10-05T00:{at:02d}:00.000Z', 'type': kind, 'payload': payload})


CODEX_START = [_codex('session_meta', {'id': SID, 'cwd': CWD}, 40),
               _codex('response_item', {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Fold the sidebar.'}]}, 40)]
CODEX_PATCH = [_codex('response_item', {'type': 'custom_tool_call', 'call_id': 'c1', 'name': 'apply_patch', 'input': '*** Begin Patch'}, 41),
               _codex('response_item', {'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': 'Done'}, 41)]
CODEX_SHELL = [_codex('response_item', {'type': 'function_call', 'call_id': 'c2', 'name': 'shell',
                                        'arguments': json.dumps({'command': ['bash', '-lc', 'gh workflow run preview.yml']})}, 46)]
CODEX_DONE = [_codex('response_item', {'type': 'function_call_output', 'call_id': 'c2', 'output': 'ok'}, 47),
              _codex('event_msg', {'type': 'task_complete'}, 47)]


class Records(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'claude-profile'

    def claude(self, lines, cut=False):
        import session_continuity
        folder = self.home / 'projects' / session_continuity._slug(CWD)
        folder.mkdir(parents=True, exist_ok=True)
        text = '\n'.join(lines) + '\n'
        if cut:
            text = text[:-25]  # a SIGKILL mid-write: the last line stops halfway
        (folder / f'{SID}.jsonl').write_text(text)
        return lost_runs.read('claude', str(self.home), SID, CWD)

    def codex(self, lines):
        folder = Path(self.temp.name) / 'codex-home' / 'sessions' / '2026' / '10' / '05'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'rollout-2026-10-05T10-40-00-{SID}.jsonl').write_text('\n'.join(lines) + '\n')
        return lost_runs.read('codex', str(Path(self.temp.name) / 'codex-home'), SID)

    def test_a_step_boundary_is_a_pause(self):
        record = self.claude(BOUNDARY)
        self.assertEqual((record['state'], record['step'], record['working']), ('paused', None, False))
        thinking = self.claude(BOUNDARY_THINKING)
        self.assertEqual((thinking['state'], thinking['working']), ('paused', True), 'answering a result: working, nothing open')

    def test_a_cut_off_read_is_settled_by_the_folder(self):
        record = self.claude(CUT_READ)
        self.assertEqual((record['state'], record['working']), ('paused', True))
        self.assertEqual(self.claude(DERVO_NOTE)['state'], 'paused', 'Dervo’s own records hold its own tools')

    def test_a_cut_off_bash_call_is_the_one_thing_to_ask_about(self):
        record = self.claude(CUT_BASH)
        self.assertEqual(record['state'], 'unsure')
        self.assertEqual(record['step']['tool'], 'Bash')
        self.assertEqual(record['step']['text'], 'gh workflow run preview.yml', 'its first line, named')
        self.assertEqual(record['step']['at'], run_ending_time(46))
        self.assertTrue(record['working'])
        # After Stop's SIGINT the call has a result, but it says it was cut off: still unsure.
        self.assertEqual(self.claude(INTERRUPTED_BASH)['state'], 'unsure')
        connector = self.claude(CONNECTOR)
        self.assertEqual((connector['state'], connector['step']['text']), ('unsure', 'github create_issue'))
        # Negative control: the same interruption in an earlier turn was settled then.
        self.assertEqual(self.claude(EARLIER_INTERRUPT)['state'], 'paused')

    def test_an_unreadable_record_keeps_todays_card(self):
        self.assertEqual(lost_runs.read('claude', str(self.home), SID, CWD)['state'], 'unreadable', 'no file')
        cut = self.claude(BOUNDARY, cut=True)
        self.assertEqual((cut['state'], cut['reason']), ('unreadable', 'cut'))
        self.assertEqual(self.claude(DERVO_WRITE)['reason'], 'dervo_step', 'a Dervo file write has its own reconcile')
        self.assertEqual(lost_runs.read('claude', str(self.home), 'not-a-session', CWD)['state'], 'unreadable')
        self.assertEqual(lost_runs.read('openrouter', str(self.home), SID, CWD)['state'], 'unreadable')

    def test_a_session_file_is_found_under_its_folder_or_any_other(self):
        self.claude(BOUNDARY)
        exact = lost_runs.claude_session_path(str(self.home), SID, CWD)
        self.assertTrue(str(exact).endswith(f'{SID}.jsonl'))
        # A run without full access worked in a temporary folder that's gone: any project folder holding the id.
        self.assertEqual(lost_runs.claude_session_path(str(self.home), SID, '/private/tmp/gone'), exact)
        self.assertIsNone(lost_runs.claude_session_path(str(self.home), '11111111-2222-3333-4444-555555555555', CWD))

    def test_codex_rollouts_read_the_same_way(self):
        self.assertEqual(self.codex(CODEX_START + CODEX_PATCH + CODEX_SHELL + CODEX_DONE)['state'], 'paused')
        record = self.codex(CODEX_START + CODEX_PATCH + CODEX_SHELL)
        self.assertEqual((record['state'], record['step']['text'], record['working']), ('unsure', 'gh workflow run preview.yml', True))
        self.assertEqual(self.codex(CODEX_START + CODEX_PATCH)['state'], 'paused')


def run_ending_time(minute):
    return lost_runs._when(f'2026-10-05T00:{minute:02d}:00.000Z')


def lost_task(full_access=True, provider='claude'):
    return {'id': 'task-1', 'state': 'disconnected', 'full_access': full_access, 'session_id': SID,
            'error': __import__('coding_integration').RESTART_LOST, 'binding': {'provider': provider, 'cwd': CWD},
            'context': {'thread_id': 't', 'project_id': 'p', 'actor': 'local'}, 'messages': [],
            'tokens': [{'token': 'toolu_9', 'tool': 'native:Read', 'native': True, 'state': 'uncertain'}],
            'runs': [{'id': 'run-1', 'state': 'uncertain', 'session_id': SID, 'after_close': {'reaped': False, 'changed': None}}]}


class Settle(unittest.TestCase):
    def apply(self, task, result):
        from coding_integration import lost_run_text
        changed = lost_runs.apply(None, task, task['runs'][-1], result, lost_run_text)
        return changed, task

    def test_a_pause_reads_as_paused_and_continues_the_same_session(self):
        changed, task = self.apply(lost_task(), {'record': {'state': 'paused', 'provider': 'claude', 'step': None},
                                                 'changed': ['frontend/src/local/DervoSidebar.tsx'], 'stopped': 'killed',
                                                 'left': {'frontend/src/local/DervoSidebar.tsx': 'abc'}, 'left_at': 1.0,
                                                 'note': 'It had changed DervoSidebar.tsx. Those edits are in your project folder, not committed.'})
        self.assertTrue(changed)
        run = task['runs'][-1]
        self.assertEqual((task['state'], run['state'], run['end_cause']), ('blocked', 'interrupted', 'restart_paused'))
        self.assertTrue(task['error'].startswith('Paused when Dervo restarted. It had changed DervoSidebar.tsx.'))
        self.assertEqual(run['left'], {'frontend/src/local/DervoSidebar.tsx': 'abc'}, 'the next run records them as its own')
        self.assertEqual([t['state'] for t in task['tokens']], ['cancelled'])
        self.assertEqual((run['after_close']['reaped'], run['after_close']['stopped_with']), (True, 'SIGKILL'))
        ending = run_ending.classify(task)
        self.assertEqual((ending['kind'], ending['by']), ('stopped', 'restart'))
        self.assertEqual(run_ending.glyph_words(ending), ('stopped', 'Paused when Dervo restarted'))
        # Continue resumes the same session: continuity counts an interrupted run as settled, never a fresh start.
        import session_continuity
        self.assertIn(run['state'], session_continuity.SETTLED_RUNS)
        import conversation_activity
        self.assertEqual((conversation_activity.end_of(task), conversation_activity.status_of(task)), ('restart_paused', 'settled'))

    def test_a_step_outside_the_folder_names_it_and_waits(self):
        changed, task = self.apply(lost_task(), {'record': {'state': 'unsure', 'provider': 'claude',
                                                            'step': {'tool': 'Bash', 'text': 'gh workflow run preview.yml', 'at': 1.0}},
                                                 'changed': [], 'stopped': 'interrupted'})
        self.assertFalse(changed)
        self.assertEqual(task['state'], 'disconnected')
        self.assertIn('every step ended except “gh workflow run preview.yml”', task['error'])
        self.assertIn('Continue, checking first, or leave it stopped', task['error'])
        self.assertEqual(task['runs'][-1]['after_close']['stopped_with'], 'SIGINT')
        text = lost_runs.checking_text(task)
        self.assertIn('`gh workflow run preview.yml`', text)
        self.assertIn('Check whether it did before you run it again', text)

    def test_an_unreadable_record_or_folder_keeps_todays_words(self):
        from coding_integration import lost_run_text
        _, task = self.apply(lost_task(), {'record': {'state': 'unreadable', 'reason': 'cut'}, 'changed': ['a.py']})
        self.assertEqual((task['state'], task['error']), ('disconnected', lost_run_text(['a.py'], False)))
        # A full-access pause needs the folder read: without it, nothing can be recorded, so today's card.
        _, task = self.apply(lost_task(), {'record': {'state': 'paused', 'provider': 'claude'}, 'changed': None})
        self.assertEqual((task['state'], task['runs'][-1]['after_close']['record']['reason']), ('disconnected', 'folder'))
        # Without full access there's no folder to read, and the record alone settles it.
        _, task = self.apply(lost_task(full_access=False), {'record': {'state': 'paused', 'provider': 'claude'}, 'changed': None})
        self.assertEqual(task['state'], 'blocked')

    def test_a_run_you_already_dealt_with_keeps_its_words(self):
        task = lost_task()
        task.update(state='blocked', error='You left this stopped.')
        changed, task = self.apply(task, {'record': {'state': 'paused'}, 'changed': []})
        self.assertFalse(changed)
        self.assertEqual((task['state'], task['error']), ('blocked', 'You left this stopped.'))
        self.assertEqual(task['runs'][-1]['after_close']['record'], {'state': 'paused'})


class Orphans(unittest.TestCase):
    """Don't reap blindly: an orphan whose record shows it working gets SIGINT, Stop's own signal, and SIGKILL only if
    it's still there after the wait."""
    def stop(self, working, alive_after):
        import signal
        import platform_os
        import coding_integration
        sent, alive = [], iter([True] + alive_after)
        with patch.object(lost_runs, '_alive', lambda pid, saved: next(alive, alive_after[-1] if alive_after else False)), \
             patch.object(platform_os, 'signal_group', lambda pid, sig: sent.append(sig)), \
             patch.object(coding_integration, 'reap_orphan', lambda pid, saved: sent.append('SIGKILL') or True):
            outcome = lost_runs.stop_orphan(4242, {'start': 1, 'exe': '/x'}, working, wait=0.3, sleep=lambda s: None)
        return outcome, [signal.Signals(s).name if isinstance(s, int) else s for s in sent]

    def test_a_working_orphan_gets_sigint_and_writes_its_stop(self):
        self.assertEqual(self.stop(True, [False]), ('interrupted', ['SIGINT']))

    def test_one_that_ignores_sigint_is_ended(self):
        self.assertEqual(self.stop(True, [True]), ('killed', ['SIGINT', 'SIGKILL']))

    def test_an_idle_orphan_is_ended_as_before(self):
        self.assertEqual(self.stop(False, []), ('killed', ['SIGKILL']))

    def test_the_record_is_read_again_after_a_sigint(self):
        reads = []
        def reader(provider, home, sid, cwd):
            reads.append(sid)
            return {'state': 'unsure' if len(reads) == 1 else 'paused', 'provider': provider, 'working': len(reads) == 1}
        task = lost_task()
        task['runs'][-1].update(pid=4242, process={'start': 1, 'exe': '/x'})
        stops = []
        _, _, result = lost_runs.work({'task': task, 'home': '/h'}, folder_read=lambda t, r: [], reader=reader,
                                      stop=lambda pid, saved, working: stops.append(working) or 'interrupted')
        self.assertEqual((stops, len(reads), result['record']['state'], result['stopped']), ([True], 2, 'paused', 'interrupted'))
        self.assertNotIn('working', result['record'])


GIT = '/usr/bin/git'


def git(cwd, *args, env=None):
    return subprocess.run([GIT, *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env=env or dict(os.environ, GIT_AUTHOR_NAME='F', GIT_AUTHOR_EMAIL='f@example.test',
                                          GIT_COMMITTER_NAME='F', GIT_COMMITTER_EMAIL='f@example.test')).stdout


class HandCommit(unittest.TestCase):
    """The 5 Oct "17.1: merge and test" stop: a commit made by hand while a full-access run worked moved HEAD, and the
    run was refused with "The agent changed git history". The guard now says whether the agent tried."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        git(self.project, 'init', '-q', '-b', 'mvp1')
        (self.project / 'notes.md').write_text('one\n')
        git(self.project, 'add', '-A')
        git(self.project, 'commit', '-qm', 'start')
        self.baseline = native_evidence.baseline(str(self.project))

    def hand_commit(self):
        (self.project / 'mine.md').write_text('mine\n')
        git(self.project, 'add', 'mine.md')
        git(self.project, 'commit', '-qm', 'Worktrees explore: note and mocks')
        return git(self.project, 'rev-parse', 'HEAD').strip()

    def test_a_hand_commit_no_longer_ends_the_run(self):
        (self.project / 'run.py').write_text('print(1)\n')  # the run's own change
        sha = self.hand_commit()
        detail = {}
        changed, problems, _ = native_evidence.changes(str(self.project), self.baseline, detail=detail, tried=[])
        self.assertEqual((changed, problems), (['run.py'], []), 'recorded on top of the new HEAD')
        moved = detail['moved_head']
        self.assertEqual((moved['branch'], moved['to'], [c['sha'] for c in moved['commits']]), ('mvp1', sha, [sha]))
        self.assertEqual(moved['commits'][0]['subject'], 'Worktrees explore: note and mocks')
        # The guard saw the agent try, or there was no guard to ask: today's refusal stands.
        for tried in (['HEAD'], None):
            with self.assertRaisesRegex(Rejected, 'The agent changed git history'):
                native_evidence.changes(str(self.project), self.baseline, detail={}, tried=tried)

    def test_the_guard_refuses_a_history_move_even_with_no_verify_and_says_it_saw_it(self):
        import claude_coding
        folder = self.base / 'guard'
        folder.mkdir()
        guard = claude_coding.git_guard(str(folder), str(self.project))
        env = dict(os.environ, GIT_AUTHOR_NAME='A', GIT_AUTHOR_EMAIL='a@example.test', GIT_COMMITTER_NAME='A',
                   GIT_COMMITTER_EMAIL='a@example.test', **guard)
        self.assertEqual(claude_coding.git_tried(str(folder)), [], 'nothing tried yet')
        head = git(self.project, 'rev-parse', 'HEAD').strip()
        (self.project / 'agent.py').write_text('x\n')
        subprocess.run([GIT, 'add', 'agent.py'], cwd=self.project, env=env, check=True)
        refused = subprocess.run([GIT, 'commit', '--no-verify', '-qm', 'agent'], cwd=self.project, env=env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('happen in Dervo after the user approves', refused.stderr)
        self.assertEqual(git(self.project, 'rev-parse', 'HEAD').strip(), head, 'HEAD stayed')
        # Apple's git names the branch; newer git (2.43 on Ubuntu 24.04) names HEAD too, as the commit moves it through the symref.
        self.assertIn(claude_coding.git_tried(str(folder)), (['refs/heads/mvp1'], ['HEAD', 'refs/heads/mvp1']))
        reset = subprocess.run([GIT, 'reset', '-q', '--soft', 'HEAD'], cwd=self.project, env=env, capture_output=True, text=True)
        self.assertEqual(reset.returncode, 0, 'a reset that moves nothing passes ' + reset.stderr)
        # Other refs pass: a fetch's remote branch, a new branch.
        for argv in (['update-ref', 'refs/remotes/origin/mvp1', head], ['branch', 'side']):
            done = subprocess.run([GIT, *argv], cwd=self.project, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        # A scratch repository in a temp folder still commits, and isn't counted.
        scratch = Path(tempfile.mkdtemp(dir=__import__('platform_os').scratch_root()))
        self.addCleanup(lambda: subprocess.run(['/bin/rm', '-rf', str(scratch)]))
        git(scratch, 'init', '-q')
        (scratch / 'f.txt').write_text('f')
        subprocess.run([GIT, 'add', '-A'], cwd=scratch, env=env, check=True)
        done = subprocess.run([GIT, 'commit', '--no-verify', '-qm', 'fixture'], cwd=scratch, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(claude_coding.git_tried(str(folder)), (['refs/heads/mvp1'], ['HEAD', 'refs/heads/mvp1']))
        self.assertIsNone(claude_coding.git_tried(str(self.base / 'no-guard')))

    def test_files_written_outside_the_project_are_named(self):
        home = os.path.expanduser('~')
        task = {'runs': [{'written': ['frontend/a.ts', f'{home}/dervo/app-wt/b171-merge/merge.zsh', 'screenshot shot.png', '/tmp/x.txt']}]}
        self.assertEqual(native_evidence.outside_files(task), {'count': 2, 'files': ['~/dervo/app-wt/b171-merge/merge.zsh', '/tmp/x.txt']})
        self.assertIsNone(native_evidence.outside_files({'runs': [{'written': ['a.py']}]}))


class Endings(unittest.TestCase):
    """Step 1: Leave it stopped, Don't run, and the subthread line read as what ended them, never a failure X."""
    def task(self, state, error=None, **run):
        return {'id': 't', 'state': state, 'error': error, 'runs': [{'id': 'r', 'state': 'failed', **run}], 'tokens': [], 'messages': []}

    def test_leave_it_stopped_and_dont_run_are_not_failures(self):
        left = run_ending.classify(self.task('blocked', 'Provider exited. You left this stopped.', end_cause='settled_by_user'))
        self.assertEqual((left['kind'], left.get('left')), ('stopped', True))
        self.assertEqual(run_ending.glyph_words(left), ('stopped', 'Left stopped'))
        import conversation_activity
        left_task = self.task('blocked', 'You left this stopped.', end_cause='settled_by_user')
        self.assertEqual((conversation_activity.end_of(left_task), conversation_activity.status_of(left_task)), ('left', 'settled'))
        denied = {'id': 't', 'state': 'blocked', 'error': 'Execution scope denied. No task request was sent.', 'runs': [], 'tokens': []}
        self.assertEqual((run_ending.classify(denied)['kind'], run_ending.classify(denied)['declined']), ('not_started', True))
        # A Stop Claude Code was slow to honour: stopped, and the sidebar says so (not "Stopped with an error").
        import conversation_activity
        from claude_coding import HOST_STOPS
        slow = self.task('blocked', HOST_STOPS['stop'], end_cause='host_limit', limit='stop')
        self.assertEqual((conversation_activity.end_of(slow), conversation_activity.status_of(slow)), ('stopped', 'settled'))
        failed = self.task('blocked', 'Claude Code reported an error: tool crashed')
        self.assertEqual(conversation_activity.end_of(failed), 'blocked', 'a real failure still says so')

    def test_a_subthread_line_says_what_ended_it(self):
        import sqlite3
        import thread_notes
        db = sqlite3.connect(':memory:')
        kid = {'id': 'k', 'name': 'Sidebar folding'}
        with patch('chat_links.brief', lambda db, kid, task: {}):
            limit = thread_notes.facts(db, kid, self.task('blocked', 'Claude Code sent nothing for 2 hours, so Dervo stopped the run.', end_cause='host_limit', limit='idle'))
            left = thread_notes.facts(db, kid, self.task('blocked', 'You left this stopped.', end_cause='settled_by_user'))
            failed = thread_notes.facts(db, kid, self.task('blocked', 'Claude Code reported an error.'))
            lost = thread_notes.facts(db, kid, {**self.task('disconnected', 'Dervo closed'), 'runs': [{'id': 'r', 'state': 'uncertain'}]})
        self.assertEqual((limit['glyph'], limit['line']), ('stopped', 'Stopped after a long silence.'))
        self.assertEqual((left['glyph'], left['line']), ('stopped', 'Left stopped.'))
        self.assertEqual((failed['glyph'], failed['line']), ('failed', 'Stopped with an error.'))
        self.assertEqual((lost['glyph'], lost['line']), ('unknown', 'Stopped when Dervo closed. Outcome not confirmed.'))

    def test_leaving_it_stopped_drops_the_offer_to_leave_it_stopped(self):
        """The left frame read "Check your folder, then leave it stopped. You left this stopped." once chosen."""
        import coding_integration as ci
        import lost_runs
        self.assertEqual(ci.without_leave_hint(ci.RESTART_LOST), 'Dervo closed while this was running, so it can’t say how it ended.')
        unsure = lost_runs.unsure_text({'step': {'tool': 'Bash', 'text': 'gh workflow run preview.yml'}}, 'claude')
        self.assertEqual(ci.without_leave_hint(unsure), unsure.replace(' Continue, checking first, or leave it stopped.', ''))
        kept = ci.without_leave_hint(ci.lost_run_text(['greeting.py', 'a.b.css'], True))
        self.assertEqual(kept, 'Dervo closed while this was running. The agent kept working until Dervo stopped it on this relaunch: greeting.py, a.b.css changed.')
        # Negative control: words with no such offer are kept whole.
        self.assertEqual(ci.without_leave_hint('Provider exited. It had changed a.py.'), 'Provider exited. It had changed a.py.')
        self.assertEqual(ci.without_leave_hint(None), '')

    def test_the_pickup_strip_names_the_unsure_step_like_the_card(self):
        """The thread's strip and Home's lead said "It kept working: 2 files changed" above a card saying one step may not
        have finished (seen in the unsure render). They now say what the card says."""
        import pickup
        thread = {'id': 'c', 'name': 'Sidebar folding'}
        def lost(record):
            run = {'id': 'r', 'state': 'uncertain', 'after_close': {'reaped': True, 'changed': ['a.tsx', 'b.css'], 'record': record}}
            return pickup.thread_facts(thread, {**self.task('disconnected', 'Dervo closed'), 'runs': [run]}, {'lost': True}, None)
        unsure = lost({'state': 'unsure', 'provider': 'claude', 'step': {'tool': 'Bash', 'text': 'gh workflow run preview.yml'}})
        self.assertEqual(unsure['line'], 'Stopped when Dervo closed. One step may not have finished: gh workflow run preview.yml.')
        self.assertNotIn('kept working', unsure['did'])
        self.assertEqual(lost({'state': 'unsure', 'provider': 'codex'})['line'], 'Stopped when Dervo closed. One step may not have finished.')
        # Negative control: an unreadable record keeps today's folder line.
        self.assertEqual(lost({'state': 'unreadable'})['line'], 'Stopped when Dervo closed. It kept working: 2 files changed, not committed.')

    def test_the_relaunch_line_says_paused_for_runs_the_record_settled(self):
        from host import lost_runs_line
        self.assertEqual(lost_runs_line([{'name': 'Sidebar folding', 'paused': True}]),
                         'Dervo restarted while a run was going in Sidebar folding. It was between steps, so it paused there. Continue picks it up.')
        self.assertIn('Open that thread to see where it got to.', lost_runs_line([{'name': 'A', 'paused': True}, {'name': 'B'}]))
        self.assertIn(' in B,', lost_runs_line([{'name': 'A', 'paused': True}, {'name': 'B'}]), 'only the unsettled one is named')


if __name__ == '__main__':
    unittest.main()
