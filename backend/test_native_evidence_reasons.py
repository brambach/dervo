"""Full access: when a run's own leftover files block approval, the reason names the files (#9, failure B).

Real git in a temporary repository. The evidence store is a small stand-in that applies the real file-name rule
(project_access.relative) and keeps one attempt's after-images, which is all native_evidence.finish asks of it.
"""
import base64
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import native_evidence
import project_access


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Evidence:
    def __init__(self, root):
        self.root, self.record = Path(root), {'files': [], 'admitted': [], 'attempts': []}

    def read(self, task_id, **ctx):
        return self.record

    def _admit(self, evidence, path):
        project_access.relative(path)  # the real rule: hidden, credential, database and generated names are refused
        evidence['admitted'].append(path)
        return True

    def _scoped(self, evidence):
        return list(evidence['files']) + list(evidence['admitted'])

    def verify_current(self, task_id, **ctx):
        after = {path: {'bytes_b64': base64.b64encode((self.root / path).read_bytes()).decode()} for path in self.record['admitted']}
        self.record['attempts'].append({'number': len(self.record['attempts']) + 1, 'before': {}, 'after': after})
        return self.record['attempts'][-1]

    def _save(self, evidence):
        pass

    # Beta.15: finish keeps each version through the store, which holds a file over 256 KiB beside the database.
    def _record(self, data):
        return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes_b64': base64.b64encode(data).decode()}

    def snapshot_bytes(self, snapshot):
        return base64.b64decode(snapshot['bytes_b64'])


class Coding:
    def __init__(self, root, tasks=()):
        self.evidence = Evidence(root)
        # Earlier tasks, as coding_tasks rows, for native_evidence.earlier_left.
        self.store = SimpleNamespace(db=sqlite3.connect(':memory:'))
        self.store.db.execute('CREATE TABLE coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        for task in tasks:
            self.store.db.execute('INSERT INTO coding_tasks VALUES (?, ?)', (task['id'], json.dumps(task)))
        # Thread names, for the block that names the thread holding a file (task_push.thread_name_for).
        self.store.db.execute('CREATE TABLE local_channels (id TEXT PRIMARY KEY, name TEXT)')

    def save(self, task):
        self.store.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?, ?)', (task['id'], json.dumps(task)))
        return task

    def task(self, task_id):
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None


class ScriptedGit:
    """Git for native_evidence without a commit, since commits are blocked inside a Dervo run: HEAD, status and the
    committed bytes of each file, answered from a dict. Anything else fails as git would."""
    def __init__(self, status, committed):
        self.status, self.committed = status, committed

    def __call__(self, argv, **kwargs):
        args = [a for a in argv[1:]]
        while args and args[0] == '-c':
            args = args[2:]
        text = kwargs.get('text', True)
        out = lambda value, code=0: subprocess.CompletedProcess(argv, code, value if text else value.encode() if isinstance(value, str) else value, '' if text else b'')
        if args[:2] == ['rev-parse', '--verify']:
            return out('abc123\n')
        if args[:2] == ['rev-parse', '--show-prefix']:
            return out('\n')
        if args[:2] == ['rev-parse', '--show-toplevel']:
            return out('/fixture\n')
        if args[:1] == ['status']:
            return out(''.join(f'{code} {path}\0' for path, code in self.status.items()))
        if args[:1] == ['show'] and args[1].startswith('HEAD:./'):
            self.shown = getattr(self, 'shown', []) + [args[1][len('HEAD:./'):]]
            data = self.committed.get(args[1][len('HEAD:./'):])
            return out(data) if data is not None else out(b'' if not text else '', 128)
        if args[:2] == ['cat-file', '-s'] and args[2].startswith('HEAD:./'):
            data = self.committed.get(args[2][len('HEAD:./'):])
            size = data if isinstance(data, int) else len(data) if data is not None else None  # an int stands for a huge blob
            return out(f'{size}\n') if size is not None else out('', 128)
        return out('', 1)


class EarlierRunAndDeletionTests(unittest.TestCase):
    """Beta.15 (Bryce, 24 Sep): a file Dervo's own earlier run left unapproved isn't your edit, and a deletion is a change.
    Real files, scripted git (no commit is made), the real native_evidence."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def base(self, dirty):
        return {'head': 'abc123', 'dirty': {path: native_evidence.fingerprint(self.root, path) for path in dirty}}

    def test_a_file_an_earlier_run_left_is_recorded_and_your_own_edit_still_isnt(self):
        (self.root / 'PLAN.md').write_text('# Plan\nfirst run\n')
        (self.root / 'notes.md').write_text('mine\n')
        base = self.base(['PLAN.md', 'notes.md'])
        left = native_evidence.left_prints(self.root, ['PLAN.md'])
        (self.root / 'PLAN.md').write_text('# Plan\nfirst run\nsecond run\n')
        (self.root / 'notes.md').write_text('mine, then the agent\n')
        git = ScriptedGit({'PLAN.md': '??', 'notes.md': '??'}, {})
        detail = {}
        changed, problems, _ = native_evidence.changes(self.root, base, run=git, earlier={'PLAN.md': {left['PLAN.md']: 't-first'}}, detail=detail)
        self.assertEqual(changed, ['PLAN.md'])
        self.assertEqual(detail['inherited'], {'PLAN.md': 't-first'})
        self.assertEqual(problems, ['notes.md (it already had uncommitted changes when this run started)'], 'your edit still blocks')
        # Negative control: you touched PLAN.md after the first run, so it's yours again.
        changed, problems, _ = native_evidence.changes(self.root, base, run=git, earlier={'PLAN.md': {'0' * 64: 't-first'}}, detail={})
        self.assertEqual(changed, [])
        self.assertIn('PLAN.md (it already had uncommitted changes when this run started)', problems)

    def test_deleting_a_committed_file_is_a_change_and_the_old_callers_see_what_they_did(self):
        git = ScriptedGit({'old.md': ' D'}, {'old.md': b'# Old\n'})
        detail = {}
        self.assertEqual(native_evidence.changes(self.root, self.base([]), run=git, detail=detail), ([], [], []))
        self.assertEqual(detail['deleted'], ['old.md'])
        # Without ``detail`` (a restart's changed list, the stopped-run note), a deletion is still named as before.
        self.assertEqual(native_evidence.changes(self.root, self.base([]), run=git)[1], ['old.md (deleted)'])
        # An earlier run's new file that this run removed again leaves the folder matching HEAD: nothing to record.
        (self.root / 'draft.md').write_text('draft\n')
        base = self.base(['draft.md'])
        printed = native_evidence.fingerprint(self.root, 'draft.md')
        (self.root / 'draft.md').unlink()
        detail = {}
        self.assertEqual(native_evidence.changes(self.root, base, run=ScriptedGit({}, {}), earlier={'draft.md': {printed: 't'}}, detail=detail), ([], [], []))
        self.assertEqual(detail, {'inherited': {}} if detail else {})

    def test_a_deleted_secret_database_or_huge_file_stays_unrecordable_and_its_bytes_are_never_read(self):
        # Security review M1 (24 Sep): the deletion path used to skip the name rule and the 16 MiB cap, so a deleted .env
        # put its committed contents into the task record and the diff.
        committed = {'.env': b'OPENAI_API_KEY=sk-live-example\n', 'secrets/prod.json': b'{"token": "x"}\n',
                     'data.sqlite': b'SQLite format 3\x00', 'config/credentials.yml': b'key: x\n',
                     'assets/huge.bin': 16777217, 'notes.md': b'# Notes\n'}
        git = ScriptedGit({path: ' D' for path in committed}, committed)
        detail = {}
        changed, problems, _ = native_evidence.changes(self.root, self.base([]), run=git, detail=detail)
        self.assertEqual((changed, detail['deleted']), ([], ['notes.md']), 'only the ordinary file is a reviewable deletion')
        rows = {row['path']: row['why'] for row in native_evidence.unrecorded(problems)}
        # 16.2: each row names the rule that refused it.
        self.assertEqual({path: rows[path] for path in ('.env', 'secrets/prod.json', 'data.sqlite', 'config/credentials.yml')},
                         {'.env': 'A hidden file or folder, its name starts with a dot', 'secrets/prod.json': 'Inside a secrets folder',
                          'data.sqlite': 'A database file', 'config/credentials.yml': 'Named like a credentials or secrets file'})
        self.assertEqual(rows['assets/huge.bin'], 'Deleted, and over 16 MiB')
        self.assertEqual(getattr(git, 'shown', []), [], 'sizes come from cat-file: no committed bytes are read here')
        # And through finish: nothing secret reaches the attempt, and the result can't be approved.
        task = {'id': 't', 'context': {'project_id': 'p', 'thread_id': 'c', 'actor': 'me'}, 'binding': {'cwd': str(self.root)},
                'runs': [{'id': 'r', 'baseline': self.base([])}]}
        coding = Coding(self.root)
        self.assertEqual(native_evidence.finish(coding, task, run=git), 1)
        attempt = coding.evidence.record['attempts'][-1]
        self.assertEqual(sorted(attempt['diff']), ['notes.md'])
        self.assertEqual(sorted(attempt['deleted']), ['notes.md'])
        self.assertNotIn('sk-live', json.dumps(attempt))
        self.assertEqual(git.shown, ['notes.md'], 'only the recordable deletion is read back from git')
        self.assertEqual(task['error'], 'Dervo couldn’t record 5 files from this run, so it can’t be approved yet.')

    def test_only_a_waiting_run_and_the_newest_print_count_as_dervos(self):
        # Security review S2 (24 Sep): a committed task left nothing waiting, and an older print loses to a newer one.
        (self.root / 'a.md').write_text('old\n')
        old = native_evidence.fingerprint(self.root, 'a.md')
        new = '1' * 64
        def task(id, left, at, **extra):
            return {'id': id, 'context': {'project_id': 'p', 'thread_id': 'c'}, 'binding': {'cwd': str(self.root)},
                    'runs': [{'id': 'r-' + id, 'left': left, 'left_at': at}], **extra}
        now = {'id': 'now', 'context': {'project_id': 'p', 'thread_id': 'c'}, 'binding': {'cwd': str(self.root)}, 'runs': [{'id': 'r-now'}]}
        committed = task('done', {'a.md': old, 'gone.md': None}, 10, commit={'sha': 'abc', 'reverted': None})
        self.assertEqual(native_evidence.earlier_left(Coding(self.root, [committed]), now), {})
        # Two waiting runs left a.md: only the newer print is Dervo's, so going back to the older one by hand is yours.
        tasks = [task('first', {'a.md': old}, 10), task('second', {'a.md': new}, 20)]
        self.assertEqual(native_evidence.earlier_left(Coding(self.root, tasks), now), {'a.md': {new: 'second'}})
        # One task's earlier run is superseded by its own later run.
        both = {**task('t', {'a.md': old}, 10), 'runs': [{'id': 'r1', 'left': {'a.md': old}, 'left_at': 10},
                                                         {'id': 'r2', 'left': {'a.md': new}, 'left_at': 20}]}
        self.assertEqual(native_evidence.earlier_left(Coding(self.root, [both]), now), {'a.md': {new: 't'}})

    def test_finish_records_the_deletion_and_the_carried_file_and_what_it_left(self):
        (self.root / 'PLAN.md').write_text('first\n')
        first = native_evidence.left_prints(self.root, ['PLAN.md'])
        earlier = {'id': 't-first', 'context': {'project_id': 'p', 'thread_id': 'c'}, 'binding': {'cwd': str(self.root)}, 'runs': [{'id': 'r0', 'left': first}]}
        task = {'id': 't', 'context': {'project_id': 'p', 'thread_id': 'c', 'actor': 'me'}, 'binding': {'cwd': str(self.root)},
                'runs': [{'id': 'r', 'baseline': self.base(['PLAN.md'])}]}
        (self.root / 'PLAN.md').write_text('first\nsecond\n')
        coding = Coding(self.root, [earlier])
        git = ScriptedGit({'PLAN.md': '??', 'gone.md': ' D'}, {'gone.md': b'bye\n'})
        self.assertEqual(native_evidence.finish(coding, task, run=git), 1)
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertEqual(task['inherited'], [{'path': 'PLAN.md', 'task_id': 't-first'}])
        attempt = coding.evidence.record['attempts'][-1]
        self.assertEqual(sorted(attempt['diff']), ['PLAN.md', 'gone.md'])
        self.assertIn('-bye', attempt['diff']['gone.md'])
        self.assertEqual(attempt['deleted']['gone.md']['sha256'], hashlib.sha256(b'bye\n').hexdigest())
        self.assertNotIn('gone.md', attempt['after'], 'every reader of after takes it as bytes, so a deletion stays out')
        self.assertIsNone(attempt['before']['PLAN.md'], 'never committed: a creation, so the diff shows the whole file')
        self.assertEqual(task['runs'][-1]['left'], {'PLAN.md': native_evidence.fingerprint(self.root, 'PLAN.md'), 'gone.md': None})
        # And the next run starting from exactly that records PLAN.md again rather than calling it your edit.
        self.assertEqual(native_evidence.earlier_left(Coding(self.root, [task]), {**task, 'id': 'next', 'runs': [{'id': 'r2'}]})['PLAN.md'],
                         {native_evidence.fingerprint(self.root, 'PLAN.md'): 't'})

    def test_a_stopped_run_keeps_what_it_left_too(self):
        (self.root / 'a.md').write_text('a\n')
        task = {'id': 't', 'binding': {'cwd': str(self.root)}, 'runs': [{'id': 'r', 'baseline': self.base([])}]}
        note = native_evidence.unfinished_note(task, run=ScriptedGit({'a.md': '??'}, {}))
        self.assertEqual(note, 'It had changed a.md. Those edits are in your project folder, not committed.')
        self.assertEqual(task['runs'][-1]['left'], {'a.md': native_evidence.fingerprint(self.root, 'a.md')})


class ThreadFollowUpTests(unittest.TestCase):
    """16.2 (Bryce, 27 Sep, #25): a thread's follow-up builds on its own unapproved result, another thread's stays
    protected and the block names it. Real files, scripted git (no commit is made), the real native_evidence."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def base(self, dirty):
        return {'head': 'abc123', 'dirty': {path: native_evidence.fingerprint(self.root, path) for path in dirty}}

    def earlier(self, task_id, thread, paths):
        return {'id': task_id, 'context': {'project_id': 'p', 'thread_id': thread}, 'binding': {'cwd': str(self.root)},
                'runs': [{'id': 'r-' + task_id, 'left': native_evidence.left_prints(self.root, paths), 'left_at': 10}]}

    def task(self, dirty):
        return {'id': 't', 'context': {'project_id': 'p', 'thread_id': 'c', 'actor': 'me'}, 'binding': {'cwd': str(self.root)},
                'runs': [{'id': 'r', 'baseline': self.base(dirty)}]}

    def test_a_follow_up_covers_its_threads_earlier_result_and_approving_it_settles_both(self):
        (self.root / 'PLAN.md').write_text('first\n')
        (self.root / 'notes.md').write_text('notes from the first run\n')
        first = self.earlier('t-first', 'c', ['PLAN.md', 'notes.md', 'old.md'])  # old.md: the first run deleted it
        task = self.task(['PLAN.md', 'notes.md', 'old.md'])
        (self.root / 'PLAN.md').write_text('first\nsecond\n')
        coding = Coding(self.root, [first])
        git = ScriptedGit({'PLAN.md': '??', 'notes.md': '??', 'old.md': ' D'}, {'old.md': b'old\n'})
        self.assertEqual(native_evidence.finish(coding, task, run=git), 1)
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertNotIn('unrecorded', task)
        attempt = coding.evidence.record['attempts'][-1]
        self.assertEqual(sorted(attempt['diff']), ['PLAN.md', 'notes.md', 'old.md'], 'the untouched file and the deletion come along')
        self.assertIn('+notes from the first run', attempt['diff']['notes.md'], 'diffed against HEAD, not against the first run')
        self.assertEqual(sorted(attempt['deleted']), ['old.md'])
        self.assertEqual(task['inherited'], [{'path': p, 'task_id': 't-first'} for p in ('PLAN.md', 'notes.md', 'old.md')])
        # Approving the newest: once it's committed, the first result stops waiting and says what committed it.
        task['commit'] = {'sha': 'f00d', 'branch': 'main', 'files': ['PLAN.md', 'notes.md', 'old.md'], 'at': 5, 'reverted': None}
        coding.save(task)
        self.assertEqual(native_evidence.settle_carried(coding, task), ['t-first'])
        settled = coding.task('t-first')
        self.assertEqual((settled['state'], settled['review']), ('idle', 'approved'))
        self.assertEqual({k: settled['commit'][k] for k in ('sha', 'how', 'with_task_id', 'files')},
                         {'sha': 'f00d', 'how': 'with_later', 'with_task_id': 't', 'files': ['PLAN.md', 'notes.md', 'old.md']})
        self.assertEqual(native_evidence.settle_carried(coding, task), [], 'already settled: nothing more to do')

    def test_an_earlier_result_with_files_outside_the_commit_keeps_waiting(self):
        (self.root / 'PLAN.md').write_text('first\n')
        first = self.earlier('t-first', 'c', ['PLAN.md', 'extra.md'])
        coding = Coding(self.root, [first])
        task = {**self.task([]), 'inherited': [{'path': 'PLAN.md', 'task_id': 't-first'}],
                'commit': {'sha': 'f00d', 'files': ['PLAN.md'], 'at': 5, 'reverted': None}}
        self.assertEqual(native_evidence.settle_carried(coding, task), [])
        self.assertNotIn('commit', coding.task('t-first'))
        # Nor does an uncommitted or undone result settle anything.
        for commit in ({'sha': None, 'error': 'x'}, {'sha': 'f00d', 'files': ['PLAN.md', 'extra.md'], 'reverted': {'sha': 'b'}}):
            self.assertEqual(native_evidence.settle_carried(coding, {**task, 'commit': commit}), [])

    def test_a_follow_up_that_changes_nothing_leaves_the_earlier_result_as_it_is(self):
        (self.root / 'PLAN.md').write_text('first\n')
        earlier = {'PLAN.md': {native_evidence.fingerprint(self.root, 'PLAN.md'): 't-first'}}
        detail = {}
        result = native_evidence.changes(self.root, self.base(['PLAN.md']), run=ScriptedGit({'PLAN.md': '??'}, {}), earlier=earlier, detail=detail)
        self.assertEqual((result, detail), (([], [], []), {}))

    def test_another_threads_waiting_file_stays_protected_and_the_block_names_that_thread(self):
        (self.root / 'PLAN.md').write_text('home thread\n')
        (self.root / 'mine.md').write_text('by hand\n')
        other = self.earlier('t-home', 'home', ['PLAN.md'])
        coding = Coding(self.root, [other])
        coding.store.db.execute("INSERT INTO local_channels VALUES ('home', '16.2 Home panel')")
        task = self.task(['PLAN.md', 'mine.md'])
        (self.root / 'PLAN.md').write_text('home thread\nthis thread\n')
        (self.root / 'mine.md').write_text('by hand\nand the agent\n')
        git = ScriptedGit({'PLAN.md': '??', 'mine.md': '??'}, {})
        self.assertIsNone(native_evidence.finish(coding, task, run=git))
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(task['unrecorded'], [
            {'path': 'PLAN.md', 'why': 'Waiting in 16.2 Home panel’s result. Approve that one first', 'count': 1,
             'task_id': 't-home', 'thread_id': 'home', 'thread': '16.2 Home panel'},
            {'path': 'mine.md', 'why': 'You had uncommitted changes in it before the run', 'count': 1}])
        self.assertNotIn('inherited', task, 'another thread’s file is never taken over')
        # An unnamed thread still gets a plain row, and no thread's file is held once that thread's result is committed.
        rows = native_evidence.unrecorded([f'PLAN.md ({native_evidence.HELD})'], {'PLAN.md': {'task_id': 'x', 'thread_id': 'y', 'thread': 'another thread'}})
        self.assertEqual(rows[0]['why'], 'Waiting in another thread’s result. Approve that one first')
        committed = {**other, 'commit': {'sha': 'abc', 'reverted': None}}
        self.assertEqual(native_evidence.waiting_left(Coding(self.root, [committed]), task), ({}, {}))


class NameRuleTests(unittest.TestCase):
    """16.2: one file-name rule for reading, writing and recording, matching real secrets, not the first word of a name."""
    ORDINARY = ['data.js', 'docs/data-model.md', 'backend/auth_flow.py', 'backend/token_count.py', 'frontend/src/profile.tsx',
                'src/tokens.css', 'tooling/run.sh', 'panel.html', 'DervoTokens.css', 'docs/secrets.md', 'id_rsa.pub',
                'evidence/design-tests/b162-home/data.js']
    REFUSED = {'.env': 'A hidden file or folder, its name starts with a dot', '.env.local': 'A hidden file or folder, its name starts with a dot',
               '.git/config': 'A hidden file or folder, its name starts with a dot', 'config/.ssh/id': 'A hidden file or folder, its name starts with a dot',
               'server.pem': 'A private key or certificate store', 'certs/app.p12': 'A private key or certificate store',
               'app.db': 'A database file', 'state/data.sqlite3-wal': 'A database file',
               'secrets/prod.json': 'Inside a secrets folder', 'deploy/credentials/aws.txt': 'Inside a secrets folder',
               'credentials.json': 'Named like a credentials or secrets file', 'config/secrets.yaml': 'Named like a credentials or secrets file',
               'client_secret_123.json': 'Named like a credentials or secrets file', 'keys/id_ed25519': 'Named like a credentials or secrets file',
               'token.json': 'Named like a credentials or secrets file', 'auth-token.json': 'Named like a credentials or secrets file',
               'config/db_password.txt': 'Named like a credentials or secrets file'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root_path = Path(self.temp.name).resolve()

    def test_ordinary_names_pass_and_each_refusal_names_its_rule(self):
        import project_access
        from task_evidence import TaskEvidence
        for path in self.ORDINARY:
            self.assertIsNone(project_access.refusal(path), path)
            self.assertEqual(TaskEvidence._relative(path), path.split('/'), 'the scoped rule is the same rule')
        for path, why in self.REFUSED.items():
            self.assertEqual(project_access.refusal(path), why, path)
            with self.assertRaisesRegex(Exception, why):
                TaskEvidence._relative(path)
        for path in ('', 'a//b', '../x', 'a\\b', './a'):
            self.assertEqual(project_access.refusal(path), 'Invalid relative project file', path)
        for why in self.REFUSED.values():
            self.assertNotIn(' (', why, 'the result card splits a row at its last parenthesis')

    def test_a_generated_folder_is_refused_only_when_git_ignores_it(self):
        import project_access
        from task_evidence import TaskEvidence
        subprocess.run(['git', 'init', '-q'], cwd=self.root_path, check=True)
        (self.root_path / '.gitignore').write_text('dist/\nnode_modules/\n__pycache__/\n')
        (self.root_path / 'evidence/b16-work/build').mkdir(parents=True)
        (self.root_path / 'evidence/b16-work/build/fixtures.js').write_text('x\n')
        subprocess.run(['git', 'add', 'evidence'], cwd=self.root_path, check=True)  # tracked: not generated output
        root = str(self.root_path)
        self.assertIsNone(project_access.refusal('evidence/b16-work/build/fixtures.js', root))
        self.assertIsNone(project_access.refusal('build/new.js', root), 'git doesn’t ignore build/ here')
        for path in ('dist/app.js', 'frontend/node_modules/react/index.js', 'backend/__pycache__/a.pyc'):
            self.assertEqual(project_access.refusal(path, root), project_access.IGNORED, path)
        # Without a folder to ask git about, a generated name is refused as before; the scoped rule never refused one.
        self.assertEqual(project_access.refusal('build/new.js'), project_access.GENERATED_FOLDER)
        self.assertEqual(TaskEvidence._relative('build/new.js'), ['build', 'new.js'])
        with self.assertRaisesRegex(Exception, 'folder git ignores'):
            TaskEvidence._relative('dist/app.js', root)
        # Outside any repository git can't say, so a generated folder stays refused.
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(['rm', '-rf', outside]))
        self.assertEqual(project_access.refusal('build/x.js', outside), project_access.IGNORED)


class LeftoverFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'config', 'user.email', 'f@example.test')
        (self.root / 'a.py').write_text('a = 1\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', 'start')

    def task(self):
        return {'id': 't', 'context': {'project_id': 'p', 'thread_id': 'c', 'actor': 'me'}, 'binding': {'cwd': str(self.root)},
                'runs': [{'id': 'r', 'baseline': native_evidence.baseline(self.root)}]}

    def test_a_leftover_file_blocks_approval_and_names_itself(self):
        task = self.task()
        (self.root / '.env.local').write_text('TOKEN=1\n')
        (self.root / 'link.py').symlink_to('a.py')
        self.assertIsNone(native_evidence.finish(Coding(self.root), task))
        self.assertEqual(task['state'], 'blocked')
        # Beta.15: one short line that still names the files, and a row per file with a plain reason for the card.
        self.assertEqual(task['error'], 'Dervo couldn’t record 2 files this run left (.env.local, link.py), so there’s nothing to approve.')
        self.assertEqual(task['unrecorded'], [{'path': '.env.local', 'why': 'A hidden file or folder, its name starts with a dot', 'count': 1},
                                              {'path': 'link.py', 'why': 'A link or special file, not a regular file', 'count': 1}])
        self.assertNotIn('\u2014', task['error'])
        # Negative control: a run that leaves only an ordinary edit is reviewable, with nothing unrecorded and no error.
        (self.root / '.env.local').unlink()
        (self.root / 'link.py').unlink()
        task = self.task()
        (self.root / 'a.py').write_text('a = 2\n')
        coding = Coding(self.root)
        self.assertEqual(native_evidence.finish(coding, task), 1)
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertNotIn('unrecorded', task)
        self.assertEqual((coding.evidence.record['attempts'][-1]['unrecorded'], sorted(coding.evidence.record['attempts'][-1]['diff'])), ([], ['a.py']))

    def test_files_beyond_what_one_attempt_records_are_named(self):
        limit = native_evidence.CHANGE_LIMIT
        task = self.task()
        for i in range(limit + 5):
            (self.root / f'new{i:03d}.py').write_text('x = 1\n')
        coding = Coding(self.root)
        self.assertEqual(native_evidence.finish(coding, task), 1)
        self.assertEqual(task['state'], 'ready_for_review', 'the first files are recorded and reviewable; approval isn’t')
        self.assertEqual(task['error'], 'Dervo couldn’t record 5 files from this run, so it can’t be approved yet.')
        self.assertEqual(task['unrecorded'], [{'path': f'new{limit:03d}.py, new{limit + 1:03d}.py, new{limit + 2:03d}.py and 2 more',
                                               'why': f'More files than one result holds ({limit})', 'count': 5}])
        self.assertEqual(len(coding.evidence.record['attempts'][-1]['diff']), limit)
        # Negative control: exactly the limit is recorded whole, with no error.
        for path in self.root.glob('new*.py'):
            path.unlink()
        task = self.task()
        for i in range(limit):
            (self.root / f'ok{i:03d}.py').write_text('x = 1\n')
        coding = Coding(self.root)
        self.assertEqual(native_evidence.finish(coding, task), 1)
        self.assertEqual((task['state'], task['error']), ('ready_for_review', None))
        self.assertEqual(len(coding.evidence.record['attempts'][-1]['diff']), limit)


class ResultNoticeWords(unittest.TestCase):
    """Beta.15 (Bryce, 23 Sep): the result card lays these out, so the task carries rows and the error is one line. No git."""
    def test_each_unrecorded_file_is_a_row_with_a_plain_reason(self):
        rows = native_evidence.unrecorded(['b.py (deleted)', 'a.md (it already had uncommitted changes when this run started)',
                                           'x.bin (an odd reason from the store)'])
        self.assertEqual(rows, [{'path': 'a.md', 'why': 'You had uncommitted changes in it before the run', 'count': 1},
                                {'path': 'b.py', 'why': 'Deleted', 'count': 1},
                                {'path': 'x.bin', 'why': 'An odd reason from the store', 'count': 1}])
        self.assertEqual(native_evidence.blocked_text(['b.py (deleted)']), 'Dervo couldn’t record 1 file this run left (b.py), so there’s nothing to approve.')
        for text in (native_evidence.blocked_text(['a (deleted)', 'b (deleted)', 'c (deleted)']), native_evidence.problem_text(['a (deleted)'])):
            self.assertNotIn(' (deleted)', text, 'reasons live on the rows, not in the sentence')
            self.assertNotIn('\u2014', text)
            self.assertLess(len(text), 120)

    def test_a_file_name_with_its_own_parenthesis_keeps_its_name(self):
        """"logo (1).png" is a browser's second download: the reason is the last parenthesis, not the first."""
        self.assertEqual(native_evidence.unrecorded(['assets/logo (1).png (deleted)', 'plain']),
                         [{'path': 'assets/logo (1).png', 'why': 'Deleted', 'count': 1},
                          {'path': 'plain', 'why': 'Dervo couldn’t read it', 'count': 1}])
        over = native_evidence.unrecorded([f'a (1).md, b.md and 3 more (5 files beyond the {native_evidence.CHANGE_LIMIT} one attempt records)'])
        self.assertEqual((over[0]['path'], over[0]['count']), ('a (1).md, b.md and 3 more', 5))

    def test_another_chats_files_are_a_short_line_never_a_file_list(self):
        self.assertIsNone(native_evidence.shared_note([]))
        line = native_evidence.shared_note(['PLAN.md', 'evidence/a.css', 'evidence/b.svg', 'c', 'd', 'e', 'f'])
        self.assertEqual(line, 'Another chat changed 7 files in this folder during the run. This result has only its own.')
        self.assertNotIn('PLAN.md', line)


if __name__ == '__main__':
    unittest.main()
