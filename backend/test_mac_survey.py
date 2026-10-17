"""The whole-Mac look (mac_survey.py) over synthetic Claude Code and Codex homes in a temporary folder. No real home is
read. Every noise rule has a fixture, and one test checks that no transcript is opened and no content column is asked
for, with the content planted where a slip would show it."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
import unittest.mock
import uuid
from pathlib import Path

import mac_survey as ms
from session_continuity import _slug

DAY = 86400
NOW = 1_800_000_000
SECRET = 'purple-zebra-content'  # planted in transcripts and Codex's content columns; must never reach the result


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(os.path.realpath(self.tmp.name))
        self.home = self.root / 'home'
        self.claude = self.home / '.claude'
        self.codex = self.home / '.codex'
        self.temp = self.root / 'scratch'
        for folder in (self.home, self.temp):
            folder.mkdir()
        self.rows = []

    def folder(self, relative):
        path = self.home / relative
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sessions(self, path, count, days_ago=1, spread=0.0):
        """``count`` Claude Code session files for ``path``, dated ``days_ago`` (the newest) and ``spread`` days apart."""
        folder = self.claude / 'projects' / _slug(str(path))
        folder.mkdir(parents=True, exist_ok=True)
        for n in range(count):
            file = folder / f'{uuid.uuid4()}.jsonl'
            file.write_text(json.dumps(dict(type='user', cwd=str(path), message=dict(content=SECRET))) + '\n')
            at = NOW - (days_ago + n * spread) * DAY
            os.utime(file, (at, at))
        return folder

    def thread(self, cwd, *, source='vscode', days_ago=1, archived=0, origin=None, branch=None, thread_id=None):
        thread_id = thread_id or str(uuid.uuid4())
        at = NOW - days_ago * DAY
        self.rows.append((thread_id, str(cwd), source, int(at - 60), int(at), archived, origin, branch))
        return thread_id

    def write_codex(self, roots=(), copies=()):
        self.codex.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.codex / 'state_5.sqlite')
        db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT, source TEXT, created_at INTEGER, '
                   'updated_at INTEGER, archived INTEGER, git_origin_url TEXT, git_branch TEXT, first_user_message TEXT, '
                   'title TEXT, name TEXT, preview TEXT, has_user_event INTEGER)')
        for row in self.rows:
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)',
                       (row[0], '/nowhere', *row[1:], SECRET, SECRET, SECRET, SECRET))
        db.execute('CREATE TABLE projects (id TEXT, name TEXT)')
        db.execute('CREATE TABLE project_roots (project_id TEXT, path TEXT)')
        for n, root in enumerate(roots):
            db.execute('INSERT INTO projects VALUES (?,?)', (str(n), SECRET))
            db.execute('INSERT INTO project_roots VALUES (?,?)', (str(n), str(root)))
        db.commit()
        db.close()
        records = [dict(source_path=f'/x/{claude_id}.jsonl', imported_thread_id=thread_id) for thread_id, claude_id in copies]
        (self.codex / 'external_agent_session_imports.json').write_text(json.dumps(dict(records=records)))

    def look(self, **extra):
        return ms.survey(claude_home=self.claude, codex_home=self.codex, home=self.home, temp_roots=(str(self.temp),), now=NOW, **extra)

    @staticmethod
    def names(found):
        return [p['name'] for p in found['projects']]

    @staticmethod
    def pile(found, rule):
        return next((p for p in found['set_aside'] if p['rule'] == rule), None)


class FreshUserTests(Fixture):
    def test_no_history_at_all_is_an_empty_look_that_says_neither_tool_is_here(self):
        found = self.look()
        self.assertEqual(found['projects'], [])
        self.assertEqual(found['set_aside'], [])
        self.assertIsNone(found['loose_ends'])
        self.assertEqual(found['counts'], dict(files=0, folders=0, working=0, quieter=0, loose=0))
        self.assertEqual([s['present'] for s in found['sources']], [False, False])
        self.assertEqual([s['path'] for s in found['sources']], ['~/.claude/projects', '~/.codex'])

    def test_claude_code_only(self):
        app = self.folder('code/tidepool')
        self.sessions(app, 5, days_ago=2, spread=1)
        found = self.look()
        self.assertEqual(self.names(found), ['tidepool'])
        project = found['projects'][0]
        self.assertTrue(project['ticked'])
        self.assertEqual(project['tools'], ['claude'])
        self.assertEqual(project['path'], '~/code/tidepool')
        self.assertEqual(project['reason'], '5 conversations in the last 14 days, in Claude Code.')
        self.assertEqual(found['paths'][project['key']], str(app))
        self.assertEqual([s['present'] for s in found['sources']], [True, False])

    def test_codex_only(self):
        app = self.folder('code/recipes')
        for n in range(3):
            self.thread(app, days_ago=n + 1, branch='main')
        self.write_codex()
        found = self.look()
        project = found['projects'][0]
        self.assertEqual((project['name'], project['tools'], project['ticked'], project['branch']), ('recipes', ['codex'], True, 'main'))
        self.assertIn('in Codex, on branch main', project['reason'])
        self.assertEqual([s['present'] for s in found['sources']], [False, True])


class UnreadableTests(Fixture):
    """Behaviour review S3 and S4 (24 Sep): what Dervo couldn't look at is said as such, never as gone or empty."""
    def locked(self, path):
        path.chmod(0)
        self.addCleanup(path.chmod, 0o755)

    def test_a_folder_dervo_cant_look_inside_isnt_called_gone(self):
        app = self.folder('Documents/app')
        self.sessions(app, 3)
        self.sessions(self.home / 'deleted' / 'app', 2)  # negative control: really gone
        self.locked(self.home / 'Documents')
        found = self.look()
        unreadable, gone = self.pile(found, 'unreadable'), self.pile(found, 'gone')
        self.assertEqual((unreadable['count'], unreadable['label']), (1, '1 folder Dervo couldn’t look inside'))
        self.assertEqual(gone['count'], 1)
        self.assertEqual(self.names(found), [])

    def test_an_unreadable_list_isnt_read_as_empty(self):
        app = self.folder('code/app')
        self.sessions(app, 3)
        self.codex.mkdir(parents=True)
        (self.codex / 'state_5.sqlite').write_bytes(b'not a database, or a newer one Dervo can\'t open')
        found = self.look()
        self.assertEqual([(s['tool'], s['present'], s['readable']) for s in found['sources']], [('claude', True, True), ('codex', True, False)])
        self.locked(self.claude / 'projects')
        found = self.look()
        self.assertEqual([s['readable'] for s in found['sources']], [False, False])


class RuleTests(Fixture):
    def test_temporary_folders_are_set_aside_from_both_tools(self):
        scratch = self.temp / 'dervo' / 'run1'
        scratch.mkdir(parents=True)
        self.sessions(scratch, 2)
        self.thread(scratch)
        self.write_codex()
        found = self.look()
        self.assertEqual(found['projects'], [])
        self.assertEqual(self.pile(found, 'temp')['count'], 2, 'one folder each from the slug and the Codex cwd')

    def test_a_folder_that_is_gone_is_set_aside(self):
        self.sessions(self.home / 'code' / 'deleted-checkout', 4)
        self.thread(self.home / 'code' / 'other-gone')
        self.write_codex()
        found = self.look()
        self.assertEqual(found['projects'], [])
        self.assertEqual(self.pile(found, 'gone')['count'], 2)

    def test_a_tools_own_dot_folder_is_set_aside(self):
        self.sessions(self.folder('.claude/mem/observer/sessions'), 40)
        found = self.look()
        self.assertEqual(found['projects'], [])
        self.assertEqual(self.pile(found, 'tool_folder')['count'], 1)
        self.assertEqual(self.pile(found, 'tool_folder')['folders'][0]['conversations'], 40)

    def test_set_aside_claude_code_folders_are_listed_by_a_path_you_can_read(self):
        scratch = self.temp / 'run1'
        scratch.mkdir()
        self.sessions(scratch, 1)
        self.sessions(self.folder('.claude/mem/observer'), 2)
        self.sessions(self.home / 'code' / 'deleted-checkout', 1)
        found = self.look()
        self.assertEqual(self.pile(found, 'temp')['folders'][0]['path'], str(scratch), 'still on disk: its real path')
        self.assertEqual(self.pile(found, 'tool_folder')['folders'][0]['path'], '~/.claude/mem/observer')
        self.assertEqual(self.pile(found, 'gone')['folders'][0]['path'], '~/code-deleted-checkout', 'gone: the slug, home written as ~')

    def test_codex_threads_a_tool_ran_are_set_aside_by_thread(self):
        app = self.folder('code/app')
        self.thread(app, source='exec')
        self.thread(app, source='mcp')
        self.thread(app, source='{"subagent":{"parent_thread_id":"x"}}')
        self.write_codex()
        found = self.look()
        self.assertEqual(found['projects'], [])
        self.assertEqual(self.pile(found, 'tool_run')['count'], 3)
        self.assertEqual(self.pile(found, 'tool_run')['label'], '3 threads a tool ran')

    def test_codex_copies_of_claude_code_sessions_are_counted_once(self):
        app = self.folder('code/app')
        self.sessions(app, 3)
        copies = [(self.thread(app), str(uuid.uuid4())) for _ in range(3)]
        native = self.thread(app)
        self.write_codex(copies=copies)
        found = self.look()
        project = found['projects'][0]
        self.assertEqual(project['conversations'], 4, 'three Claude Code sessions and one native Codex thread, not seven')
        self.assertEqual(project['tools'], ['claude', 'codex'])
        self.assertEqual(self.pile(found, 'copy')['count'], 3)
        self.assertEqual(self.pile(found, 'copy')['label'], '3 Codex copies of Claude Code sessions, counted once')
        self.assertTrue(native)

    def test_a_worktree_folds_into_its_repository(self):
        repo = self.folder('code/app')
        (repo / '.git' / 'worktrees' / 'feature').mkdir(parents=True)
        tree = self.folder('code/app-wt/feature')
        (tree / '.git').write_text(f'gitdir: {repo}/.git/worktrees/feature\n')
        self.sessions(repo, 2)
        self.sessions(tree, 3)
        found = self.look()
        self.assertEqual(self.names(found), ['app'])
        self.assertEqual(found['projects'][0]['conversations'], 5)
        self.assertEqual(self.pile(found, 'folded')['count'], 1)

    def test_a_worktree_whose_repo_has_no_history_brings_the_repo_in(self):
        repo = self.folder('code/site')
        (repo / '.git' / 'worktrees' / 'fix').mkdir(parents=True)
        tree = self.folder('trees/fix')
        (tree / '.git').write_text(f'gitdir: {repo}/.git/worktrees/fix\n')
        self.sessions(tree, 4)
        found = self.look()
        self.assertEqual(self.names(found), ['site'])
        self.assertEqual(found['projects'][0]['path'], '~/code/site')

    def test_a_subfolder_folds_into_its_repository(self):
        repo = self.folder('code/app')
        (repo / '.git').mkdir()
        self.sessions(self.folder('code/app/frontend'), 3)
        self.sessions(repo, 1)
        found = self.look()
        self.assertEqual(self.names(found), ['app'])
        self.assertEqual(found['projects'][0]['conversations'], 4)

    def test_the_home_folder_is_one_loose_ends_item_that_is_off(self):
        self.sessions(self.home, 14, days_ago=4)
        found = self.look()
        self.assertEqual(found['projects'], [])
        loose = found['loose_ends']
        self.assertEqual((loose['name'], loose['path'], loose['conversations'], loose['ticked']), ('Loose ends', '~', 14, False))
        self.assertIn('14 conversations started in your home folder', loose['reason'])
        self.assertEqual(found['counts']['loose'], 14)
        # Said, never offered (Bryce, 24 Sep): no key, and the home folder isn't among the paths a key can reach.
        self.assertIsNone(loose['key'])
        self.assertNotIn(os.path.realpath(self.home), {os.path.realpath(p) for p in found['paths'].values()})

    def test_a_one_off_is_set_aside_and_one_tap_away(self):
        once = self.folder('code/tried-once')
        self.sessions(once, 1, days_ago=40)
        found = self.look()
        self.assertEqual(found['projects'], [])
        item = self.pile(found, 'one_off')['folders'][0]
        self.assertEqual(item['path'], '~/code/tried-once')
        self.assertEqual(found['paths'][item['key']], str(once), 'a restorable folder carries a key the bring-in accepts')

    def test_a_folder_already_in_dervo_is_skipped(self):
        app = self.folder('code/app')
        self.sessions(app, 6)
        found = self.look(dervo_projects=[str(app)])
        self.assertEqual(found['projects'], [])
        self.assertEqual(self.pile(found, 'dervo')['count'], 1)

    def test_ticked_needs_recent_activity_and_three_conversations_or_a_codex_project(self):
        busy, quiet, old = self.folder('code/busy'), self.folder('code/quiet'), self.folder('code/old')
        root = self.folder('code/rooted')
        self.sessions(busy, 3, days_ago=1)
        self.sessions(quiet, 2, days_ago=1)
        self.sessions(old, 9, days_ago=30)
        self.thread(root, days_ago=60)
        self.write_codex(roots=[root])
        found = self.look()
        ticked = {p['name']: p['ticked'] for p in found['projects']}
        self.assertEqual(ticked, dict(busy=True, quiet=False, old=False, rooted=True))
        self.assertEqual(self.names(found)[:2], ['busy', 'rooted'], 'ticked first, newest first')
        rooted = next(p for p in found['projects'] if p['name'] == 'rooted')
        self.assertTrue(rooted['reason'].endswith('You made it a Codex project.'))
        self.assertEqual((found['counts']['working'], found['counts']['quieter']), (2, 2))

    def test_the_slug_resolves_through_folder_names_with_hyphens_and_dots(self):
        dotted = self.folder('code/dd.app')
        self.folder('code/dd')
        self.sessions(dotted, 3)
        found = self.look()
        self.assertEqual(found['projects'][0]['path'], '~/code/dd.app')


class ContextTests(Fixture):
    def test_work_is_guessed_from_the_path_and_says_so(self):
        self.sessions(self.folder('work/acme-billing'), 3)
        self.sessions(self.folder('code/tidepool'), 3)
        found = {p['name']: p for p in self.look()['projects']}
        self.assertEqual((found['acme-billing']['context'], found['acme-billing']['context_reason']), ('work', 'guessed from ~/work'))
        self.assertEqual((found['tidepool']['context'], found['tidepool']['context_reason']), (None, 'nothing here says work or personal'))

    def test_personal_needs_its_own_sign_and_work_wins_over_it(self):
        self.sessions(self.folder('personal/recipes'), 3)
        self.sessions(self.folder('personal/work/notes'), 3)
        found = {p['name']: p for p in self.look()['projects']}
        self.assertEqual((found['recipes']['context'], found['recipes']['context_reason']), ('personal', 'guessed from ~/personal'))
        self.assertEqual(found['notes']['context'], 'work')

    def test_a_private_git_host_reads_as_work_and_github_says_nothing(self):
        billing, weather = self.folder('code/billing'), self.folder('code/weather')
        for _ in range(3):
            self.thread(billing, origin='git@gitlab.acme.example:payments/billing.git')
            self.thread(weather, origin='https://github.com/sam/weather.git')
        self.write_codex()
        found = {p['name']: p for p in self.look()['projects']}
        self.assertEqual(found['billing']['context_reason'], 'guessed from its remote, gitlab.acme.example')
        self.assertIsNone(found['weather']['context'])


class MetadataOnlyTests(Fixture):
    def test_no_transcript_is_opened_and_no_content_column_is_asked_for(self):
        app = self.folder('code/app')
        self.sessions(app, 4)
        for _ in range(3):
            self.thread(app)
        self.write_codex(roots=[app])
        statements = []
        connect = sqlite3.connect

        def traced(*args, **kwargs):
            db = connect(*args, **kwargs)
            db.set_trace_callback(statements.append)
            return db

        real_open = open

        def guarded(file, *args, **kwargs):
            if str(file).endswith('.jsonl'):
                raise AssertionError(f'a transcript was opened: {file}')
            return real_open(file, *args, **kwargs)

        with unittest.mock.patch('sqlite3.connect', traced), unittest.mock.patch('builtins.open', guarded):
            found = self.look()
        self.assertEqual(self.names(found), ['app'])
        self.assertNotIn(SECRET, json.dumps(found))
        asked = ' '.join(statements)
        for column in ('first_user_message', 'title', 'preview', 'rollout_path', 'has_user_event'):
            self.assertNotIn(column, asked)
        self.assertNotIn('FROM projects', asked, 'Codex project names are typed by a person')
        with unittest.mock.patch('sqlite3.connect', wraps=connect) as spy:
            self.look()
        self.assertTrue(all('mode=ro' in str(call.args[0]) for call in spy.call_args_list))

    def test_the_homes_are_never_written(self):
        app = self.folder('code/app')
        self.sessions(app, 3)
        self.thread(app)
        self.write_codex()
        before = {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')}
        self.look()
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')})


class ScaleTests(Fixture):
    def test_a_bryce_sized_mac_is_looked_at_quickly(self):
        """About what's on Bryce's Mac on 24 Sep (onboarding-v2.md): 478 Claude Code folders holding 5,239 transcripts,
        447 of them temporary and one tool folder with 4,290; 1,466 Codex threads, 137 exec, 21 mcp, 44 subagents,
        1,186 copies of Claude Code sessions and 78 native in 36 folders. Folder names are made up."""
        for n in range(447):
            scratch = self.temp / f'run{n}'
            scratch.mkdir()
            self.sessions(scratch, 1)
        self.sessions(self.folder('.claude/mem/observer/sessions'), 4290)
        real = [self.folder(f'code/project{n:02d}') for n in range(30)]
        left = 5239 - 447 - 4290
        for n, path in enumerate(real):
            share = left // len(real) + (1 if n < left % len(real) else 0)
            self.sessions(path, share, days_ago=1 + n, spread=0.5)
        codex_places = real[:6] + [self.folder(f'codex/only{n:02d}') for n in range(30)]
        for n in range(78):
            self.thread(codex_places[n % len(codex_places)], days_ago=n % 40)
        for source, count in (('exec', 137), ('mcp', 21), ('{"subagent":{}}', 44)):
            for _ in range(count):
                self.thread(real[0], source=source)
        copies = [(self.thread(real[n % 30]), str(uuid.uuid4())) for n in range(1186)]
        self.write_codex(roots=real[:6], copies=copies)
        started = time.monotonic()
        found = self.look()
        took = time.monotonic() - started
        print(f'\nBryce-sized look: {took:.3f} s, {found["counts"]}')
        self.assertEqual(found['counts']['files'], 5239 + 1466)
        self.assertEqual(self.pile(found, 'temp')['count'], 447)
        self.assertEqual(self.pile(found, 'copy')['count'], 1186)
        self.assertEqual(self.pile(found, 'tool_run')['count'], 202)
        self.assertLess(took, 5.0)


if __name__ == '__main__':
    unittest.main()
