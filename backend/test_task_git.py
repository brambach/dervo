"""The git module on its own: a disposable repository, a fake task record and evidence. No provider, no live workspace."""
import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import task_git
from dervo_connections import Rejected

ENV = {'PATH': '/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0'}


class TaskGitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'repo' / 'app'
        self.project.mkdir(parents=True)
        (self.project / 'a.py').write_text('a = 1\n', newline='')
        (self.project / 'b.py').write_text('b = 1\n', newline='')
        for args in (('init', '-q'), ('config', 'user.name', 'Fixture'), ('config', 'user.email', 'fixture@example.test'), ('add', '-A'), ('commit', '-q', '-m', 'Before')):
            self.git(*args)
        (self.project / 'a.py').write_text('a = 2\n', newline='')  # the task's change, already on disk as attempts leave it
        self.task = dict(id='task-1', runs=[dict(prior_attempts=0)], messages=[dict(role='user', text='  Fix   the value in a.py, please. ')])
        self.evidence = dict(attempts=[dict(number=1, before={'a.py': self.snap('a = 1\n'), 'b.py': self.snap('b = 1\n')},
                                            after={'a.py': self.snap('a = 2\n'), 'b.py': self.snap('b = 1\n')})])

    def git(self, *args):
        result = subprocess.run([task_git.GIT, *args], cwd=self.root / 'repo', capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    @staticmethod
    def digest(text):
        return {'sha256': hashlib.sha256(text.encode()).hexdigest()}

    def test_commit_holds_exactly_the_changed_files_with_a_plain_message(self):
        record = task_git.commit(self.task, self.evidence, str(self.project), how='approved', thread_name=None)  # None: the fallback subject
        self.assertEqual((record['files'], record['how'], record['reverted'], record['branch']), (['a.py'], 'approved', None, self.git('rev-parse', '--abbrev-ref', 'HEAD').strip()))
        self.assertEqual(record['sha'], self.git('rev-parse', 'HEAD').strip())
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app/a.py'])
        body = self.git('show', '--format=%B', '--no-patch', 'HEAD')
        self.assertEqual(body.splitlines()[0], 'Dervo: Fix the value in a.py, please')
        self.assertIn('Task task-1 approved in Dervo. Dervo made this commit; the agent ran no git.', body)
        self.assertEqual(task_git.repo_root(str(self.project)), str((self.root / 'repo').resolve()))
        self.assertIsNone(task_git.repo_root(str(self.root)))
        with self.assertRaisesRegex(Rejected, 'already committed'):
            task_git.commit({**self.task, 'commit': record}, self.evidence, str(self.project))

    def test_a_file_that_differs_from_the_checked_bytes_is_never_committed(self):
        (self.project / 'a.py').write_text('a = 3\n', newline='')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Rejected, 'a.py changed after the checked result'):
            task_git.commit(self.task, self.evidence, str(self.project))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        (self.project / 'a.py').unlink()
        with self.assertRaisesRegex(Rejected, 'a.py is missing'):
            task_git.commit(self.task, self.evidence, str(self.project))
        with self.assertRaisesRegex(Rejected, 'changed no files'):
            task_git.commit(self.task, dict(attempts=[dict(number=1, before=self.evidence['attempts'][0]['before'], after=self.evidence['attempts'][0]['before'])]), str(self.project))
        with self.assertRaisesRegex(Rejected, task_git.NOT_A_REPO):
            task_git.commit(self.task, self.evidence, str(self.root))

    def test_a_deletion_the_result_recorded_is_committed_and_undone(self):
        """Beta.15 (Bryce, 24 Sep): deleting a committed file on request ended "Nothing to approve". native_evidence now
        records it as ``deleted`` on the attempt (never in ``after``), and approving commits the removal."""
        (self.project / 'a.py').write_text('a = 1\n', newline='')  # only the deletion in this one
        (self.project / 'b.py').unlink()
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.snap('b = 1\n')}, after={}, deleted={'b.py': self.snap('b = 1\n')})])
        # Negative: the file is back in the folder after the result, so nothing is committed.
        (self.project / 'b.py').write_text('b = 1\n', newline='')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Rejected, 'b.py is back in the project folder'):
            task_git.commit(self.task, evidence, str(self.project))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        (self.project / 'b.py').unlink()
        record = task_git.commit(self.task, evidence, str(self.project))
        self.assertEqual(record['files'], ['b.py'])
        self.assertEqual(self.git('show', '--name-status', '--format=', 'HEAD').split(), ['D', 'app/b.py'])
        self.assertEqual(self.git('status', '--porcelain'), '', 'the index matches: nothing left staged or deleted')
        task_git.revert({**self.task, 'commit': record}, str(self.project), thread_name=None)
        self.assertEqual((self.project / 'b.py').read_text(), 'b = 1\n', 'Undo brings it back')

    def test_a_deletion_is_refused_when_git_moved_the_file_on(self):
        (self.project / 'a.py').write_text('a = 1\n', newline='')
        (self.project / 'b.py').write_text('b = 2\n', newline='')
        self.git('commit', '-qam', 'b changed in git')
        (self.project / 'b.py').unlink()
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.snap('b = 1\n')}, after={}, deleted={'b.py': self.snap('b = 1\n')})])
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Rejected, 'b.py changed in git after this task deleted it'):
            task_git.commit(self.task, evidence, str(self.project))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_undo_restores_only_the_tasks_files_and_refuses_when_later_work_overlaps(self):
        record = task_git.commit(self.task, self.evidence, str(self.project))
        (self.project / 'b.py').write_text('b = 2\n', newline='')  # your own unsaved change elsewhere stays
        task = {**self.task, 'commit': record}
        reverted = task_git.revert(task, str(self.project), thread_name=None)  # None: the fallback subject
        self.assertEqual(reverted['sha'], self.git('rev-parse', 'HEAD').strip())
        self.assertEqual((self.project / 'a.py').read_text(), 'a = 1\n')
        self.assertEqual((self.project / 'b.py').read_text(), 'b = 2\n')
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app/a.py'])
        self.assertEqual(self.git('show', '--format=%s', '--no-patch', 'HEAD').strip(), 'Dervo: Undo Fix the value in a.py, please')
        # Beta.18: Undo carries Dervo's trailer, so it doesn't end another full-access run in this folder.
        import native_evidence
        self.assertTrue(native_evidence.dervo_commits_only(str(self.project), record['sha'], reverted['sha']))
        with self.assertRaisesRegex(Rejected, 'already undone'):
            task_git.revert({**task, 'commit': {**record, 'reverted': reverted}}, str(self.project))
        # A second task commits, then you edit its file: undo refuses and changes nothing.
        (self.project / 'a.py').write_text('a = 5\n', newline='')
        evidence = dict(attempts=[dict(number=2, before={'a.py': self.digest('a = 1\n')}, after={'a.py': self.digest('a = 5\n')})])
        second = task_git.commit({**self.task, 'id': 'task-2'}, evidence, str(self.project))
        (self.project / 'a.py').write_text('a = 6\n', newline='')
        head = self.git('rev-parse', 'HEAD')
        with self.assertRaisesRegex(Rejected, 'Later changes overlap'):
            task_git.revert({**self.task, 'id': 'task-2', 'commit': second}, str(self.project))
        self.assertEqual((self.git('rev-parse', 'HEAD'), (self.project / 'a.py').read_text()), (head, 'a = 6\n'))
        with self.assertRaisesRegex(Rejected, 'no commit to undo'):
            task_git.revert({**self.task, 'commit': {'sha': None, 'skipped': 'Not a git repository. Nothing was committed.'}}, str(self.project))

    # ------------------------------------------------------------------ 23 Sep: a handoff's work, committed as one piece
    def test_a_carried_plan_commits_the_stopped_runs_files_with_this_tasks_and_undo_takes_them_all_back(self):
        """Claude changed a.py and made new.py, then stopped; Codex carried on and changed a.py again. Your own edit to
        b.py was flagged by the plan. The commit is the whole piece of work, and nothing else."""
        import shutil
        (self.project / 'a.py').write_text('a = 2\nc = 3\n', newline='')
        (self.project / 'new.py').write_text('new\n', newline='')
        (self.project / 'b.py').write_text('b = yours\n', newline='')
        codex = dict(self.task, id='codex-task', messages=[dict(role='user', text='Add c to a.py')])
        evidence = dict(attempts=[dict(number=1, before={'a.py': self.snap('a = 2\n')}, after={'a.py': self.snap('a = 2\nc = 3\n')})])
        carried = dict(from_task_id='claude-task', here='Claude', files=['a.py', 'new.py'],
                       flagged=[dict(path='b.py', why='changed after the handoff, and not by either run')],
                       before={'a.py': self.snap('a = 1\n'), 'new.py': None}, after={'new.py': self.snap('new\n')})
        # Negative control, on a copy of the repository: today's commit takes Codex's hunk at most, never new.py or
        # Claude's line in a.py.
        shutil.copytree(self.root / 'repo', self.root / 'control', symlinks=True)
        control = self.root / 'control' / 'app'
        try:
            task_git.commit(codex, evidence, str(control))
            tree = subprocess.run([task_git.GIT, 'ls-tree', '-r', '--name-only', 'HEAD'], cwd=control, capture_output=True, text=True).stdout.split()
            shown = subprocess.run([task_git.GIT, 'show', 'HEAD:./a.py'], cwd=control, capture_output=True, text=True).stdout
            self.assertNotIn('new.py', tree)
            self.assertNotIn('a = 2', shown)
        except Rejected as exc:
            self.assertIn('a.py', str(exc))

        record = task_git.commit(codex, evidence, str(self.project), thread_name='Add c', carried=carried)
        self.assertEqual(record['files'], ['a.py', 'new.py'])
        self.assertEqual(record['carried'], dict(from_task_id='claude-task', here='Claude', files=['a.py', 'new.py'], flagged=carried['flagged']))
        self.assertEqual(self.git('show', 'HEAD:app/a.py'), 'a = 2\nc = 3\n', 'Claude’s line and Codex’s, as one change')
        self.assertEqual(self.git('show', 'HEAD:app/new.py'), 'new\n')
        self.assertEqual(self.git('show', 'HEAD:app/b.py'), 'b = 1\n', 'the flagged file isn’t committed')
        self.assertEqual(self.git('status', '--porcelain').split('\n')[0].strip(), 'M app/b.py')
        body = self.git('show', '--format=%B', '--no-patch', 'HEAD')
        self.assertIn('Task codex-task approved in Dervo.', body)
        self.assertIn("Carried on from Claude's stopped run, task claude-task. Its changes before the handoff are in this commit too: a.py, new.py.", body)
        self.assertIn('Left out: b.py (changed after the handoff, and not by either run)', body)
        (self.project / 'b.py').write_text('b = 1\n', newline='')
        task_git.revert({**codex, 'commit': record}, str(self.project))
        self.assertEqual((self.project / 'a.py').read_text(), 'a = 1\n', 'Undo takes the whole piece back')
        self.assertFalse((self.project / 'new.py').exists())

    # ------------------------------------------------------------------ night 2, finding 2 (20 Sep): shared files
    def snap(self, text):
        import base64
        return {**self.digest(text), 'bytes_b64': base64.b64encode(text.encode()).decode()}

    def task_with(self, id, path, before, after, others=None):
        """A task whose one run took ``path`` from ``before`` to ``after`` (both texts), with b.py untouched."""
        task = dict(self.task, id=id, messages=[dict(role='user', text=f'{id} change')])
        evidence = dict(attempts=[dict(number=1, before={path: self.snap(before) if before is not None else None, 'b.py': self.snap('b = 1\n')},
                                       after={path: self.snap(after), 'b.py': self.snap('b = 1\n')})])
        return task, evidence

    def test_a_commit_takes_only_this_tasks_own_hunks_when_another_thread_left_lines_in_the_file(self):
        """Persona 1: Version flag added a line to tally.py and hadn't committed; Fix missing-file crash started after,
        added its own lines, and its commit took the whole file, Version flag's line included."""
        orig = 'a = 1\n' + ''.join(f'line{n}\n' for n in range(8))
        (self.project / 'a.py').write_text(newline='', data=orig)
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'Base')
        with_version = orig.replace('a = 1\n', 'a = 1\nversion = "0.1"\n')  # Version flag's uncommitted change, at the top
        with_both = with_version + 'def missing():\n    return None\n'         # the fix thread's own lines, at the bottom
        (self.project / 'a.py').write_text(newline='', data=with_both)
        fix, fix_evidence = self.task_with('fix', 'a.py', with_version, with_both)
        record = task_git.commit(fix, fix_evidence, str(self.project), thread_name='Fix missing file', others={'a.py': 'Version flag'})
        committed = self.git('show', 'HEAD:app/a.py')
        self.assertEqual(committed, orig + 'def missing():\n    return None\n', "only the fix thread's hunk")
        self.assertNotIn('version = "0.1"', committed)
        self.assertEqual(record['left'], {'a.py': 'Version flag'})
        self.assertEqual((self.project / 'a.py').read_text(), with_both, 'the folder keeps both changes')
        self.assertEqual(self.git('status', '--porcelain').split('\n')[0].strip(), 'M app/a.py', "Version flag's line stays uncommitted")
        self.assertIn("- a.py (this task's change only; Version flag's uncommitted change stays in the folder)", self.git('show', '--format=%B', '--no-patch', 'HEAD'))
        with self.assertRaisesRegex(Rejected, 'Later changes overlap'):
            task_git.revert({**fix, 'commit': record}, str(self.project))
        # Then Version flag: its preimage was the old HEAD, the file now reads HEAD plus exactly its own line, so it
        # commits its own hunk and the file is clean afterwards.
        version, version_evidence = self.task_with('version', 'a.py', orig, with_version)
        record = task_git.commit(version, version_evidence, str(self.project), thread_name='Version flag')
        self.assertEqual(self.git('show', 'HEAD:app/a.py'), with_both)
        self.assertEqual(self.git('status', '--porcelain').strip(), '')
        self.assertNotIn('left', record)
        self.assertEqual(self.git('log', '--format=%s', '-3').split('\n')[:2], ['Dervo: Version flag', 'Dervo: Fix missing file'])

    def test_a_commit_refuses_when_the_other_threads_lines_overlap_its_own_and_names_that_thread(self):
        orig = 'a = 1\n' + ''.join(f'line{n}\n' for n in range(8))
        (self.project / 'a.py').write_text(newline='', data=orig)
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'Base')
        head = self.git('rev-parse', 'HEAD')
        theirs = orig.replace('line3\n', 'line3 theirs\n')
        both = theirs.replace('line3 theirs\n', 'line3 theirs\nline3 mine\n')
        (self.project / 'a.py').write_text(newline='', data=both)
        mine, evidence = self.task_with('mine', 'a.py', theirs, both)
        with self.assertRaisesRegex(Rejected, 'a.py also holds Version flag’s uncommitted change on the same lines.*Commit Version flag first'):
            task_git.commit(mine, evidence, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual((self.git('rev-parse', 'HEAD'), (self.project / 'a.py').read_text(), self.git('status', '--porcelain').strip()),
                         (head, both, 'M app/a.py'), 'nothing was committed or staged')
        # Without a name on record it still refuses, naming "another thread".
        with self.assertRaisesRegex(Rejected, 'another thread’s uncommitted change'):
            task_git.commit(mine, evidence, str(self.project))
        # A preimage record without bytes (an older record) can't be separated either: refused, nothing committed.
        old = dict(evidence, attempts=[dict(evidence['attempts'][0], before={'a.py': self.digest(theirs), 'b.py': self.snap('b = 1\n')})])
        with self.assertRaisesRegex(Rejected, 'can’t separate the two. Commit Version flag first'):
            task_git.commit(mine, old, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        # Negative controls: the ordinary cases are unchanged. A file whose HEAD bytes are the preimage commits whole,
        # a new file commits, an edit after the check refuses, and the executable bit survives.
        (self.project / 'a.py').write_text(newline='', data=orig)
        (self.project / 'run.sh').write_text('#!/bin/sh\necho hi\n', newline=''); (self.project / 'run.sh').chmod(0o755)
        (self.project / 'a.py').write_text(newline='', data=orig + 'tail\n')
        task = dict(self.task, id='plain')
        plain = dict(attempts=[dict(number=1, before={'a.py': self.snap(orig), 'run.sh': None}, after={'a.py': self.snap(orig + 'tail\n'), 'run.sh': self.snap('#!/bin/sh\necho hi\n')})])
        record = task_git.commit(task, plain, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual((sorted(record['files']), 'left' in record), (['a.py', 'run.sh'], False))
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app/a.py', 'app/run.sh'])
        self.assertEqual(self.git('ls-files', '--stage', 'app/run.sh').split()[0], '100755')
        self.assertEqual(self.git('status', '--porcelain').strip(), '')
        (self.project / 'a.py').write_text(newline='', data=orig + 'tail\nyour edit\n')
        with self.assertRaisesRegex(Rejected, 'a.py changed after the checked result'):
            task_git.commit(dict(task, id='again'), plain, str(self.project))

    def test_a_held_index_lock_leaves_the_commit_standing_with_a_warning_and_the_next_git_call_repairs_the_index(self):
        """Reviewer, 20 Sep: git reset failed silently on a held index.lock, so the task's change sat staged in reverse."""
        lock = self.root / 'repo' / '.git' / 'index.lock'
        lock.write_text('', newline='')
        record = task_git.commit(self.task, self.evidence, str(self.project))
        self.assertEqual(record['sha'], self.git('rev-parse', 'HEAD').strip(), 'the commit landed')
        self.assertIn('index.lock', record['index_warning'])
        self.assertIn('git reset -- :/app/a.py', record['index_warning'])
        marker = self.root / 'repo' / '.git' / task_git.PENDING
        self.assertTrue(marker.exists(), 'the marker stays for repair')
        lock.unlink()
        self.assertEqual(self.git('diff', '--cached', '--name-only').split(), ['app/a.py'], 'the index still holds the parent version')
        pending = task_git.repair_pending(str(self.project))
        self.assertEqual((pending['task_id'], pending['sha'], pending['refreshed']), ('task-1', record['sha'], True))
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')
        self.assertTrue(marker.exists(), 'the marker waits for its task to claim it')
        self.assertIsNone(task_git.repair_pending(str(self.project)), 'a second repair has nothing to do')
        # The task's retry claims it: the same commit comes back, no second one is made.
        again = task_git.commit(self.task, self.evidence, str(self.project))
        self.assertEqual((again['sha'], again['files']), (record['sha'], ['a.py']))
        self.assertFalse(marker.exists())
        self.assertIsNone(task_git.repair_pending(str(self.project)), 'nothing pending answers None')
        # While the lock is held, repair says so plainly instead of failing quietly.
        (self.project / 'b.py').write_text('b = 2\n', newline='')
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.digest('b = 1\n')}, after={'b.py': self.digest('b = 2\n')})])
        lock.write_text('', newline='')
        task_git.commit(dict(self.task, id='task-2'), evidence, str(self.project))
        with self.assertRaisesRegex(Rejected, 'git reset -- :/app/b.py'):
            task_git.repair_pending(str(self.project))
        lock.unlink()
        task_git.repair_pending(str(self.project))

    def test_a_retry_after_a_crash_never_makes_an_empty_second_commit(self):
        """Reviewer, 20 Sep: host restart repaired the index and dropped the marker, so the task's retry committed a
        tree equal to its parent's, with a message wrongly naming another thread. The marker now stays until the task
        claims it, and a tree equal to the parent's is refused whatever happened to the marker."""
        marker = self.root / 'repo' / '.git' / task_git.PENDING
        record = task_git.commit(self.task, self.evidence, str(self.project))
        self.git('reset', '-q', record['sha'] + '^', '--', 'app/a.py')
        marker.write_text(newline='', data=json.dumps(dict(task_id='task-1', sha=record['sha'], parent=None, files=['a.py'], repo_files=['app/a.py'], staged=[], at=1)))
        # The host restarts: every project folder is repaired, the marker stays.
        self.assertEqual(task_git.repair_pending(str(self.project))['refreshed'], True)
        self.assertTrue(marker.exists())
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')
        again = task_git.commit(self.task, self.evidence, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual((again['sha'], 'left' in again), (record['sha'], False))
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').strip(), '2', 'no second commit')
        self.assertFalse(marker.exists())
        # Without any marker (lost by hand), the retry is refused rather than committing an empty tree.
        with self.assertRaisesRegex(Rejected, 'HEAD already holds exactly these changes'):
            task_git.commit(dict(self.task, id='task-3'), self.evidence, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').strip(), '2')
        # Negative control: a marker whose commit never landed is dropped and changes nothing.
        marker.write_text(newline='', data=json.dumps(dict(task_id='task-9', sha='0' * 40, parent=None, files=['a.py'], repo_files=['app/a.py'], at=1)))
        self.assertIsNone(task_git.repair_pending(str(self.project)))
        self.assertFalse(marker.exists())

    def test_a_sibling_project_in_the_same_repository_repairs_the_same_index_entries(self):
        """Reviewer, 20 Sep: run from repo/other, a marker holding 'a.py' reset repo/other/a.py (nothing) and reported
        success while repo/app/a.py stayed reversed. Marker paths are the repository's own now."""
        other = self.root / 'repo' / 'other'
        other.mkdir()
        (other / 'a.py').write_text('o = 1\n', newline='')
        self.git('add', 'other/a.py'); self.git('commit', '-q', '-m', 'sibling')
        marker = self.root / 'repo' / '.git' / task_git.PENDING
        (self.project / 'a.py').write_text('a = 2\n', newline='')
        record = task_git.commit(self.task, self.evidence, str(self.project))
        self.git('reset', '-q', record['sha'] + '^', '--', 'app/a.py')
        marker.write_text(newline='', data=json.dumps(dict(task_id='task-1', sha=record['sha'], parent=None, files=['a.py'], repo_files=['app/a.py'], staged=[], at=1)))
        self.assertEqual(task_git.repair_pending(str(other))['refreshed'], True)
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '', 'repaired from the sibling folder')
        self.assertEqual((other / 'a.py').read_text(), 'o = 1\n')

    def test_a_timeout_after_head_moved_reports_the_commit_not_nothing(self):
        """Reviewer, 20 Sep: a timeout on the index refresh said "Nothing was committed" after HEAD had moved."""
        real = subprocess.run
        def slow(argv, **kw):
            if 'reset' in argv:
                raise subprocess.TimeoutExpired(argv, 30)
            return real(argv, **kw)
        record = task_git.commit(self.task, self.evidence, str(self.project), run=slow)
        self.assertEqual(record['sha'], self.git('rev-parse', 'HEAD').strip())
        self.assertIn('git timed out while refreshing your index', record['index_warning'])
        self.assertIn('git reset -- :/app/a.py', record['index_warning'])
        self.assertTrue((self.root / 'repo' / '.git' / task_git.PENDING).exists())
        self.assertEqual(task_git.repair_pending(str(self.project))['refreshed'], True)
        # Negative control: a timeout before HEAD moves is a plain refusal and HEAD stays.
        head = self.git('rev-parse', 'HEAD')
        def early(argv, **kw):
            if 'write-tree' in argv:
                raise subprocess.TimeoutExpired(argv, 30)
            return real(argv, **kw)
        (self.project / 'b.py').write_text('b = 2\n', newline='')
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.snap('b = 1\n')}, after={'b.py': self.snap('b = 2\n')})])
        with self.assertRaisesRegex(Rejected, 'Git could not run. Nothing was committed.'):
            task_git.commit(dict(self.task, id='task-2'), evidence, str(self.project), run=early)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)

    def test_a_crlf_working_tree_commits_and_merges_after_gits_own_normalisation(self):
        """Reviewer, 20 Sep: with eol=crlf or autocrlf=true every commit was refused, since the CRLF bytes the agent saw
        never matched the LF blob HEAD holds."""
        (self.project / '.gitattributes').write_text('*.py text eol=crlf\n', newline='')
        (self.project / 'a.py').write_text('a = 1\n', newline='')
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'attrs')
        blob = lambda path: subprocess.run([task_git.GIT, 'show', 'HEAD:' + path], cwd=self.root / 'repo', capture_output=True,
                                           env={**ENV, 'HOME': str(self.root)}).stdout
        (self.project / 'a.py').write_bytes(b'a = 2\r\n')
        evidence = dict(attempts=[dict(number=1, before={'a.py': self.snap('a = 1\r\n')}, after={'a.py': self.snap('a = 2\r\n')})])
        record = task_git.commit(self.task, evidence, str(self.project))
        self.assertEqual((blob('app/a.py'), 'left' in record), (b'a = 2\n', False), 'whole file, stored with LF')
        # A shared CRLF file: another thread's line at the top, this task's at the bottom, merged in normalised form.
        orig = 'a = 2\r\n' + ''.join(f'line{n}\r\n' for n in range(6))
        (self.project / 'a.py').write_bytes(orig.encode())
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'lines')
        theirs = 'v = 1\r\n' + orig
        both = theirs + 'mine = 1\r\n'
        (self.project / 'a.py').write_bytes(both.encode())
        task, evidence = self.task_with('fix', 'a.py', theirs, both)
        record = task_git.commit(task, evidence, str(self.project), others={'a.py': 'Version flag'})
        self.assertEqual(blob('app/a.py'), orig.replace('\r\n', '\n').encode() + b'mine = 1\n')
        self.assertEqual(record['left'], {'a.py': 'Version flag'})
        self.assertEqual((self.project / 'a.py').read_bytes(), both.encode(), 'the folder keeps both, CRLF as it was')

    def test_your_own_staged_change_in_a_committed_file_is_kept(self):
        """Reviewer, 20 Sep: the index refresh after a commit reset a path you had staged yourself."""
        yours = subprocess.run([task_git.GIT, 'hash-object', '-w', '--stdin'], cwd=self.root / 'repo', capture_output=True, text=True,
                               input='a = 9\n', env={**ENV, 'HOME': str(self.root)}).stdout.strip()
        self.git('update-index', '--cacheinfo', f'100644,{yours},app/a.py')
        record = task_git.commit(self.task, self.evidence, str(self.project))
        self.assertEqual(self.git('show', 'HEAD:app/a.py'), 'a = 2\n', 'the commit holds the task\'s approved bytes')
        self.assertEqual(self.git('ls-files', '--stage', 'app/a.py').split()[1], yours, 'your staged version is still staged')
        self.assertFalse((self.root / 'repo' / '.git' / task_git.PENDING).exists())
        # Negative control: b.py, staged as HEAD has it, is refreshed the ordinary way after its own commit.
        (self.project / 'b.py').write_text('b = 2\n', newline='')
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.digest('b = 1\n')}, after={'b.py': self.digest('b = 2\n')})])
        task_git.commit(dict(self.task, id='task-2'), evidence, str(self.project))
        self.assertEqual(self.git('diff', '--cached', '--name-only', '--', 'app/b.py').strip(), '')

    def test_a_crash_between_moving_head_and_refreshing_the_index_is_repaired_and_the_task_gets_its_commit(self):
        marker = self.root / 'repo' / '.git' / task_git.PENDING
        record = task_git.commit(self.task, self.evidence, str(self.project))
        # Rewind the index to the parent's version and plant the marker, as a crash right after update-ref leaves it.
        self.git('reset', '-q', record['sha'] + '^', '--', 'app/a.py')
        marker.write_text(newline='', data=json.dumps(dict(task_id='task-1', sha=record['sha'], parent=None, files=['a.py'], repo_files=['app/a.py'], staged=[], at=1)))
        self.assertEqual(self.git('diff', '--cached', '--name-only').split(), ['app/a.py'])
        again = task_git.commit(self.task, self.evidence, str(self.project))
        self.assertEqual((again['sha'], again['files']), (record['sha'], ['a.py']), 'the landed commit is this task\'s, not a second one')
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), record['sha'])
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')
        self.assertFalse(marker.exists())
        # A marker for another task's landed commit is repaired and left for that task; this task commits on top.
        (self.project / 'b.py').write_text('b = 2\n', newline='')
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.digest('b = 1\n')}, after={'b.py': self.digest('b = 2\n')})])
        marker.write_text(newline='', data=json.dumps(dict(task_id='task-other', sha=record['sha'], parent=None, files=['a.py'], repo_files=['app/a.py'], staged=[], at=1)))
        second = task_git.commit(dict(self.task, id='task-2'), evidence, str(self.project))
        self.assertNotEqual(second['sha'], record['sha'])
        self.assertTrue(marker.exists(), 'task-other still has to claim its commit')

    def test_lines_another_thread_wrote_during_this_run_are_never_committed_as_this_tasks(self):
        """Reviewer, 20 Sep: a write by another thread during this run leaves its lines inside this task's approved
        bytes while the preimage still matches HEAD, so the whole-file path would take them."""
        orig = 'a = 1\n' + ''.join(f'line{n}\n' for n in range(8))
        (self.project / 'a.py').write_text(newline='', data=orig)
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'Base')
        head = self.git('rev-parse', 'HEAD')
        theirs_and_mine = orig.replace('line3\n', 'line3\ntheir line\n') + 'my line\n'
        (self.project / 'a.py').write_text(newline='', data=theirs_and_mine)
        task, evidence = self.task_with('mine', 'a.py', orig, theirs_and_mine)
        others = {'a.py': dict(name='Version flag', added=['their line'])}
        with self.assertRaisesRegex(Rejected, 'a.py holds lines Version flag wrote while this task ran.*Commit Version flag first'):
            task_git.commit(task, evidence, str(self.project), others=others)
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        # Negative controls: their lines elsewhere (not in this task's change) don't block; a name alone judges nothing.
        (self.project / 'a.py').write_text(newline='', data=orig + 'my line\n')
        task, evidence = self.task_with('mine2', 'a.py', orig, orig + 'my line\n')
        record = task_git.commit(task, evidence, str(self.project), others={'a.py': dict(name='Version flag', added=['their line'])})
        self.assertEqual(self.git('show', 'HEAD:app/a.py'), orig + 'my line\n')
        self.assertNotIn('left', record)
        self.assertFalse(task_git.theirs_inside({'a.py': 'Version flag'}, 'a.py', None, self.snap('x')))

    def test_text_auto_stores_lf_as_a_normal_commit_would(self):
        """Reviewer, 20 Sep (item 7): hash-object --no-filters kept CRLF despite text=auto."""
        (self.project / '.gitattributes').write_text('* text=auto\n', newline='')
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'attrs')
        (self.project / 'a.py').write_text('a = 2\r\nb = 3\r\n', newline='')  # the attrs commit took a.py as 'a = 2'
        evidence = dict(attempts=[dict(number=1, before={'a.py': self.digest('a = 2\n')}, after={'a.py': self.digest('a = 2\r\nb = 3\r\n')})])
        task_git.commit(self.task, evidence, str(self.project))
        blob = lambda path: subprocess.run([task_git.GIT, 'show', 'HEAD:' + path], cwd=self.root / 'repo', capture_output=True,
                                           env={**ENV, 'HOME': str(self.root)}).stdout  # bytes: text mode would fold CRLF itself
        self.assertEqual(blob('app/a.py'), b'a = 2\nb = 3\n')
        # Negative control: without the attribute, bytes are stored as they are.
        (self.project / '.gitattributes').write_text('', newline='')
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'no attrs')
        (self.project / 'b.py').write_text('b = 2\r\n', newline='')
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.digest('b = 1\n')}, after={'b.py': self.digest('b = 2\r\n')})])
        task_git.commit(dict(self.task, id='task-2'), evidence, str(self.project))
        self.assertEqual(blob('app/b.py'), b'b = 2\r\n')

    # ------------------------------------------------------------------ the reviewer's probes (20 Sep), as tests
    def test_probe_a_child_that_dies_at_the_index_refresh_is_repaired_once_and_the_retry_is_the_same_commit(self):
        """probe.py crash_between: a real child process exits at the reset; repair is idempotent and never reverses
        anything; the task's retry answers with the landed commit, not a second one."""
        import sys
        orig = 'a = 1\n' + ''.join(f'line{n}\n' for n in range(12))
        (self.project / 'a.py').write_text(newline='', data=orig)
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'Base')
        mine = orig + 'X\n'
        (self.project / 'a.py').write_text(newline='', data=mine)
        (self.project / 'b.py').write_text('b user\n', newline='')  # your own unsaved edit elsewhere
        task, evidence = self.task_with('C', 'a.py', orig, mine)
        code = f'''
import sys, os, subprocess, json
sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})
import task_git
t, e = json.loads({json.dumps(json.dumps([task, evidence]))})
def run(argv, **kw):
    if 'reset' in argv: os._exit(9)
    return subprocess.run(argv, **kw)
task_git.commit(t, e, {str(self.project)!r}, run=run)
'''
        child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)})
        self.assertEqual(child.returncode, 9, child.stderr[-300:])
        self.assertIn('app/a.py', self.git('diff', '--cached', '--name-only'), 'the index is reversed, as the crash leaves it')
        first = task_git.repair_pending(str(self.project))
        second = task_git.repair_pending(str(self.project))
        self.assertEqual((first['task_id'], second), ('C', None), 'repaired once; the second run has nothing to do')
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')
        self.assertEqual(((self.project / 'a.py').read_text(), (self.project / 'b.py').read_text()), (mine, 'b user\n'), 'nothing reversed')
        self.assertEqual(self.git('status', '--porcelain').strip(), 'M app/b.py')
        again = task_git.commit(task, evidence, str(self.project))
        self.assertEqual(again['sha'], first['sha'], 'the retry is the landed commit')
        self.assertEqual(self.git('rev-list', '--count', 'HEAD').strip(), '3', 'Before, Base and the task\'s one commit')
        self.assertIsNone(task_git.repair_pending(str(self.project)))

    def test_probe_a_hostile_git_config_runs_nothing_through_a_shared_file_commit_an_undo_and_a_repair(self):
        """probe.py hostile_config: filter, textconv, external diff, fsmonitor, pager and gpg plants; every git call
        Dervo makes carries SAFE and the filter guard, and no planted program runs."""
        orig = 'a = 1\n' + ''.join(f'line{n}\n' for n in range(12))
        (self.project / 'a.py').write_text(newline='', data=orig)
        (self.project / '.gitattributes').write_text('* filter=evil diff=evil\n', newline='')
        self.git('add', '-A'); self.git('commit', '-q', '-m', 'Base')
        marker, script = self.root / 'ran', self.root / 'p.sh'
        script.write_text(newline='', data=f'#!/bin/sh\necho "$0 $@" >> {marker}\ncat\n'); script.chmod(0o755)
        for key in ('filter.evil.clean', 'filter.evil.smudge', 'core.fsmonitor', 'diff.evil.textconv', 'diff.external', 'core.pager', 'gpg.program'):
            self.git('config', key, str(script))
        self.git('config', 'log.showSignature', 'true'); self.git('config', 'core.useBuiltinFSMonitor', 'false')
        theirs = orig.replace('a = 1\n', 'a = 1\nTHEIRS\n'); mine = theirs + 'MINE\n'
        (self.project / 'a.py').write_text(newline='', data=mine)
        task, evidence = self.task_with('H', 'a.py', theirs, mine)
        calls = []
        def run(argv, **kw):
            calls.append(list(argv)); return subprocess.run(argv, **kw)
        task_git.commit(task, evidence, str(self.project), run=run, others={'a.py': 'Other'})
        (self.project / 'c.py').write_text('c\n', newline='')
        task2 = dict(self.task, id='H2')
        evidence2 = dict(attempts=[dict(number=1, before={'c.py': None}, after={'c.py': self.snap('c\n')})])
        record = task_git.commit(task2, evidence2, str(self.project), run=run)
        task_git.revert(dict(task2, commit=record), str(self.project), run=run)
        task_git.repair_pending(str(self.project), run=run)
        safe = list(task_git.SAFE)
        unsafe = [c for c in calls if c[1:1 + len(safe)] != safe and c[1] not in ('config', 'rev-parse')]
        self.assertEqual(unsafe, [], 'every git call that could run a program carries SAFE')
        unguarded = [c for c in calls if c[1:1 + len(safe)] == safe and 'filter.evil.clean=' not in c]
        self.assertEqual(unguarded, [], 'and the repository\'s filter switched off')
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        # Negative control: plain git in the same folder runs the plant.
        # No stdin: the plant ends in `cat`, which waits forever on a Terminal's keyboard (beta.15 build, 24 Sep).
        subprocess.run([task_git.GIT, 'status', '--porcelain'], cwd=self.root / 'repo', capture_output=True, stdin=subprocess.DEVNULL,
                       env={**ENV, 'HOME': str(self.root)}, timeout=30)
        self.assertTrue(marker.exists())

    def test_probe_a_rename_is_refused_plainly_and_nothing_is_committed(self):
        """probe.py rename: renames were refused on mvp1 too; the line says renames aren't committed yet and doesn't
        claim anything was."""
        (self.project / 'a.py').rename(self.project / 'new.py')
        (self.project / 'new.py').write_text('a = 1\n', newline='')
        head = self.git('rev-parse', 'HEAD')
        evidence = dict(attempts=[dict(number=1, before={'a.py': self.snap('a = 1\n'), 'new.py': None}, after={'new.py': self.snap('a = 1\n')})])
        task = dict(self.task, id='RN', completion={'changed_files': ['new.py', 'a.py']})
        with self.assertRaisesRegex(Rejected, 'a.py was removed or renamed by this task. Dervo doesn.t commit removals or renames yet'):
            task_git.commit(task, evidence, str(self.project))
        self.assertEqual(self.git('rev-parse', 'HEAD'), head)
        self.assertEqual(self.git('diff', '--cached', '--name-only').strip(), '')
        # Negative control: a file that vanished without the task removing it keeps the plain missing line.
        (self.project / 'b.py').unlink()
        evidence = dict(attempts=[dict(number=1, before={'b.py': self.snap('b = 1\n')}, after={'b.py': self.snap('b = 2\n')})])
        with self.assertRaisesRegex(Rejected, 'b.py is missing from the project folder. Nothing was committed.'):
            task_git.commit(dict(self.task, id='M'), evidence, str(self.project))

    def test_probe_repair_answers_none_outside_a_repository_and_is_fast_with_no_marker(self):
        """probe.py repair_scope_and_speed."""
        started = time.monotonic()
        self.assertIsNone(task_git.repair_pending(self.temp.name))
        self.assertLess(time.monotonic() - started, 1.0)
        started = time.monotonic()
        for _ in range(20):
            self.assertIsNone(task_git.repair_pending(str(self.project)))
        self.assertLess((time.monotonic() - started) / 20, 0.2)

    def test_the_subject_is_the_thread_name_and_falls_back_to_the_request(self):
        # The conversation's name is the subject; your request stays in the body since the name may be the agent's words.
        record = task_git.commit(self.task, self.evidence, str(self.project), thread_name='Connector picker')
        body = self.git('show', '--format=%B', '--no-patch', 'HEAD')
        self.assertEqual(body.splitlines()[0], 'Dervo: Connector picker')
        self.assertIn('Request: Fix the value in a.py, please.', body)
        self.assertEqual(self.git('show', '--name-only', '--format=', 'HEAD').split(), ['app/a.py'])
        reverted = task_git.revert({**self.task, 'commit': record}, str(self.project), thread_name='Connector picker')
        self.assertEqual(self.git('show', '--format=%s', '--no-patch', 'HEAD').strip(), 'Dervo: Undo Connector picker')
        self.assertEqual(reverted['sha'], self.git('rev-parse', 'HEAD').strip())
        # Negative controls: no name, the automatic placeholder, and a blank name all keep today's subject.
        for name in (None, 'New conversation', '   '):
            self.assertEqual(task_git.subject(self.task, name), 'Dervo: Fix the value in a.py, please', name)
        self.assertEqual(task_git.message(self.task, ['a.py'], 'approved').splitlines()[0], 'Dervo: Fix the value in a.py, please')
        # A long name is cut at a word boundary, not mid-word; a long request is cut the way it always was.
        long_name = 'Keep an internal log of every decision the coordinator makes across the whole week'
        self.assertEqual(task_git.subject(self.task, long_name), 'Dervo: Keep an internal log of every decision the coordinator')
        self.assertEqual(task_git.subject(self.task, ' Spaced   out\nname '), 'Dervo: Spaced out name')
        wordy = dict(self.task, messages=[dict(role='user', text='I think we should try our best to keep an internal log of everything\nsecond line')])
        self.assertEqual(task_git.subject(wordy, None), 'Dervo: I think we should try our best to keep an internal log of',
                         'a long request is cut at a word too, and only its first line counts')
        self.assertEqual(task_git.request_line(wordy), 'I think we should try our best to keep an internal log of everything')
        self.assertEqual(task_git.request_line(dict(self.task, messages=[])), 'Coding task')

    def test_the_tasks_own_guide_title_names_the_commit_before_the_thread_and_the_message(self):
        # Review, 23 Sep: real commits read "Dervo: go" and "Dervo: I'm going to leave the content free, one up to you.
        # Whatever", the raw first line of a follow-up. The agent's Task guide title for the task names it now.
        follow_up = dict(self.task, messages=[dict(role='user', text='go')],
                         runs=[dict(prior_attempts=0, guide=dict(title='Old plan')), dict(prior_attempts=0, guide=dict(title='Content page copy'))])
        self.assertEqual(task_git.subject(follow_up, 'Connector picker'), 'Dervo: Content page copy', 'the newest run with a title wins')
        self.assertEqual(task_git.subject(follow_up, None), 'Dervo: Content page copy')
        record = task_git.commit(follow_up, self.evidence, str(self.project), thread_name='New conversation')
        body = self.git('show', '--format=%B', '--no-patch', 'HEAD')
        self.assertEqual(body.splitlines()[0], 'Dervo: Content page copy')
        self.assertIn('Task task-1 approved in Dervo. Dervo made this commit; the agent ran no git.', body, 'trace-back reads this line')
        self.assertIn('Request: go', body, 'your own words stay in the body')
        task_git.revert({**follow_up, 'commit': record}, str(self.project), thread_name=None)
        self.assertEqual(self.git('show', '--format=%s', '--no-patch', 'HEAD').strip(), 'Dervo: Undo Content page copy')
        # A title is cleaned the way a thread name is: no Markdown, no folder, no key in the git log.
        dressed = dict(follow_up, runs=[dict(prior_attempts=0, guide=dict(title='**Fix** /Users/me/app/login.py'))])
        self.assertEqual(task_git.subject(dressed, None), 'Dervo: Fix login.py')
        keyed = dict(follow_up, runs=[dict(prior_attempts=0, guide=dict(title='Set sk-ant-api03-' + 'a' * 40))])
        self.assertNotIn('sk-ant', task_git.subject(keyed, None))
        # Without a title: the thread's real name, then the request, as before.
        for runs in ([], [dict(prior_attempts=0)], [dict(prior_attempts=0, guide=dict(title='   '))]):
            untitled = dict(follow_up, runs=runs)
            self.assertEqual(task_git.subject(untitled, 'Connector picker'), 'Dervo: Connector picker', runs)
            self.assertEqual(task_git.subject(untitled, 'New conversation'), 'Dervo: go', runs)

    def test_a_projects_own_git_config_never_runs_a_program_during_a_dervo_call(self):
        """Reviewer, 20 Sep: a project's .git/config is writable by anything that runs project code. A planted
        core.fsmonitor (run by git status), diff.external (run by git show and diff) and core.pager each write a marker
        when git runs them; through task_git none does."""
        marker = self.root / 'ran'
        script = self.root / 'plant.sh'
        script.write_text(newline='', data=f'#!/bin/sh\necho "$0 $@" >> {marker}\nexit 0\n')
        script.chmod(0o755)
        for key in ('core.fsmonitor', 'diff.external', 'core.pager'):
            self.git('config', key, str(script))
        self.git('config', 'core.useBuiltinFSMonitor', 'false')
        import native_evidence
        native_evidence.status(self.project)
        native_evidence._head_bytes(self.project, 'a.py')
        task_git._git(self.project, 'diff', 'HEAD', '--', 'a.py')
        task_git._git(self.project, 'status', '--porcelain')
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        # Negative control: plain git with the same config does run the planted program.
        subprocess.run([task_git.GIT, 'status', '--porcelain'], cwd=self.project, capture_output=True, text=True, env={**ENV, 'HOME': str(self.root)},
                       stdin=subprocess.DEVNULL, timeout=30)
        self.assertTrue(marker.exists(), 'the plant itself works')
        self.assertIn('plant.sh', marker.read_text())

    def test_a_planted_gpg_program_never_runs_when_dervo_reads_the_log(self):
        """Reviewer, 20 Sep: log.showSignature=true plus a planted gpg.program ran the program on every signed commit
        Dervo's git log read (native_evidence, the relaunch folder read, the push preview)."""
        marker = self.root / 'gpg-ran'
        gpg = self.root / 'gpg.sh'
        gpg.write_text(newline='', data=f"""#!/bin/sh
echo "$@" >> {marker}
case "$*" in
  *-bsau*|*--detach-sign*) cat >/dev/null; echo '[GNUPG:] SIG_CREATED D 1 8 00 0 0' 1>&2
     printf -- '-----BEGIN PGP SIGNATURE-----\\n\\nAAAA\\n-----END PGP SIGNATURE-----\\n';;
  *) exit 1;;
esac
""")
        gpg.chmod(0o755)
        self.git('config', 'gpg.program', str(gpg))
        self.git('config', 'user.signingkey', 'dervo-test')
        (self.project / 'signed.py').write_text('x = 1\n', newline='')
        self.git('add', 'app/signed.py')
        self.git('commit', '-q', '-S', '-m', 'signed')
        marker.unlink(missing_ok=True)
        self.git('config', 'log.showSignature', 'true')
        task_git._git(self.project, 'log', '-1', '--format=%H')
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        # Negative control: plain git with the same config runs it.
        subprocess.run([task_git.GIT, 'log', '-1', '--format=%H'], cwd=self.project, capture_output=True, text=True,
                       env={**ENV, 'HOME': str(self.root)}, stdin=subprocess.DEVNULL, timeout=30)
        self.assertTrue(marker.exists(), 'the plant itself works')

    def plant_filter(self, name, marker, where='config'):
        """A clean and a smudge driver called ``name`` that each write a marker line when git runs them."""
        for key in ('clean', 'smudge'):
            self.git(*where.split(), f'filter.{name}.{key}', f"sh -c 'echo {key} >> {marker}; cat'")

    def test_a_filter_the_repository_defines_never_runs_during_a_dervo_call(self):
        """Security review 20 Sep, M2. A full-access run can leave ``filter.<name>.clean`` in .git/config and
        ``* filter=<name>`` in .gitattributes. Plain git runs it on status (stat changed), diff, commit and checkout;
        every task_git call passes ``-c filter.<name>.clean=`` and friends, so through Dervo it never runs."""
        import native_evidence
        from unittest.mock import patch
        marker = self.root / 'filtered'
        self.plant_filter('pwn', marker)
        (self.root / 'repo' / '.gitattributes').write_text('* filter=pwn\n', newline='')
        self.git('add', '.gitattributes')
        self.git('commit', '-q', '-m', 'attrs')
        marker.unlink(missing_ok=True)
        self.assertEqual(task_git.repo_filters(self.project), ['pwn'])
        guards = task_git.filter_guards(self.project)
        self.assertEqual(guards, ['-c', 'filter.pwn.clean=', '-c', 'filter.pwn.smudge=', '-c', 'filter.pwn.process=', '-c', 'filter.pwn.required=false'])
        os.utime(self.project / 'a.py')
        native_evidence.status(self.project)                                   # the baseline and the after-run read
        task_git._git(self.project, 'diff', '--quiet', 'HEAD', '--', 'a.py')  # Undo's overlap check
        native_evidence._head_bytes(self.project, 'a.py')                     # the before-image
        record = task_git.commit(self.task, self.evidence, str(self.project))  # Approve
        self.assertEqual(self.git('show', 'HEAD:app/a.py'), 'a = 2\n', 'the bytes are committed as they are on disk')
        task_git.revert({**self.task, 'commit': record}, str(self.project))    # Undo: checkout of the parent's version
        self.assertEqual((self.project / 'a.py').read_text(), 'a = 1\n')
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else '')
        # Negative control: plain git with the same repository does run the planted filter, on status and on checkout.
        env = {**ENV, 'HOME': str(self.root)}
        os.utime(self.project / 'a.py')
        subprocess.run([task_git.GIT, 'status', '--porcelain'], cwd=self.project, capture_output=True, env=env, timeout=30)
        (self.project / 'a.py').write_text('a = 9\n', newline='')
        subprocess.run([task_git.GIT, 'checkout', 'HEAD', '--', 'a.py'], cwd=self.project, capture_output=True, env=env, timeout=30)
        self.assertEqual(set(marker.read_text().split()), {'clean', 'smudge'}, 'the plant itself works')
        # Negative control: a driver from the user's own global config (git-lfs is one) is theirs and keeps running.
        marker.unlink()
        (self.root / '.gitconfig').write_text(newline='', data=f"[filter \"mine\"]\n\tclean = \"sh -c 'echo global >> {marker}; cat'\"\n")  # quoted: ; starts a comment
        (self.root / 'repo' / '.gitattributes').write_text('a.py filter=pwn\nb.py filter=mine\n', newline='')
        with patch.object(task_git, '_env', lambda: {**ENV, 'HOME': str(self.root)}):
            self.assertEqual(task_git.repo_filters(self.project), ['pwn'])
            os.utime(self.project / 'b.py')
            native_evidence.status(self.project)
        self.assertEqual(marker.read_text().split(), ['global'])

    def test_filters_from_an_include_or_a_global_include_into_the_repository_count_as_the_repositorys(self):
        """M2's second way in: ``[include] path = ../tracked`` in .git/config lets a tracked file define the driver, and a
        global ``include`` pointing at a file under the repository is the repository's too. A name holding ``=`` can't
        be switched off by ``-c`` (git splits at the first one; the probe showed the driver still running), so a call
        in that repository is refused outright instead. Outside a repository there's nothing to guard."""
        from unittest.mock import patch
        (self.root / 'repo' / 'tracked.cfg').write_text('[filter "inc"]\n\tclean = cat\n', newline='')
        self.git('config', 'include.path', '../tracked.cfg')
        (self.root / 'repo' / 'far.cfg').write_text('[filter "far"]\n\tsmudge = cat\n', newline='')
        (self.root / 'elsewhere.cfg').write_text('[filter "theirs"]\n\tsmudge = cat\n', newline='')
        (self.root / '.gitconfig').write_text(newline='', data=f'[include]\n\tpath = {self.root / "repo" / "far.cfg"}\n\tpath = {self.root / "elsewhere.cfg"}\n')
        with patch.object(task_git, '_env', lambda: {**ENV, 'HOME': str(self.root)}):
            self.assertEqual(sorted(task_git.repo_filters(self.project)), ['far', 'inc'])
            self.assertEqual(task_git.filter_guards(self.root), [], 'not a repository: nothing to switch off')
            self.assertEqual(task_git.repo_root(str(self.project)), str((self.root / 'repo').resolve()), 'calls still work')
        self.git('config', 'filter.a=b.clean', 'cat')
        with self.assertRaisesRegex(Rejected, 'filter named “a=b”'):
            task_git._git(self.project, 'status', '--porcelain')
        with self.assertRaisesRegex(Rejected, 'filter named “a=b”'):
            task_git.commit(self.task, self.evidence, str(self.project))


if __name__ == '__main__':
    unittest.main()
