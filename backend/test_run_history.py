"""Agent history from records only: the History list, one run's record and "Why is this line here". Real host, real
store, a real git repository for the trace. The task records are shaped from the eval store of
e22-read-only-finding-carries-into-the-fix (a read-only answer, then "fix it" in a fresh session) and the full-access
Ideas storage run Home started. No model is set up anywhere: none of this needs one."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import run_history
import task_git
import task_questions
import thread_fanout
from dervo_connections import Rejected
from host import ACTOR, Host

T0 = 1789835400.0
ASK_ID = '634cd9db-bab3-40b2-9755-3ee528d2ed65'
FIX_ID = 'd8992e52-e7de-45d0-a66e-76b106e07a59'
BOARD_ID = '5a1f0c2e-7b3d-4e8a-9c61-2f4b8d0e1a37'
IDEAS_ID = '9b41977d-d3f3-4b5f-9f77-645a8aee0c04'
ORIGINAL = ('from datetime import datetime\n\n\ndef parse_date(text):\n'
            '    """Parse an ISO date. Drops the timezone: the offset is thrown away."""\n'
            '    return datetime.fromisoformat(text).replace(tzinfo=None)\n')
FIXED = ('from datetime import datetime\n\n\ndef parse_date(text):\n'
         '    """Parse an ISO date, keeping the timezone if present."""\n'
         '    return datetime.fromisoformat(text)\n')
BOARD = FIXED + '\n\ndef parse_day(text):\n    return parse_date(text).date()\n'
FIX_DIFF = ('--- before/utils/dates.py\n+++ after/utils/dates.py\n@@ -2,5 +2,5 @@\n \n \n def parse_date(text):\n'
            '-    """Parse an ISO date. Drops the timezone: the offset is thrown away."""\n'
            '-    return datetime.fromisoformat(text).replace(tzinfo=None)\n'
            '+    """Parse an ISO date, keeping the timezone if present."""\n+    return datetime.fromisoformat(text)\n')
BOARD_DIFF = ('--- before/utils/dates.py\n+++ after/utils/dates.py\n@@ -6,0 +7,4 @@\n+\n+\n'
              '+def parse_day(text):\n+    return parse_date(text).date()\n')
REPLY = ('I fixed `parse_date` in utils/dates.py: it no longer calls `.replace(tzinfo=None)`, so it keeps the timezone.\n\n'
         'I ran the project\'s test suite (`test_sample.py`) and it passed: 1 test, OK. I haven\'t checked other callers.')
LIMITS = 'Haven\'t checked other code that calls parse_date and may assume a naive (tzinfo=None) result.'
ARGV = ['/Library/Frameworks/Python.framework/Versions/3.13/Resources/Python.app/Contents/MacOS/Python', '-I', '-B', '-m', 'unittest',
        'discover', '-s', '.', '-p', 'test_sample.py']


def image(text):
    return dict(sha256='x', bytes_b64=base64.b64encode(text.encode()).decode())


def git(cwd, *args, author='Bryce', env=None):
    return subprocess.run(['/usr/bin/git', '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', '-c', f'user.name={author}',
                           '-c', 'user.email=bryce@example.test', *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={'PATH': '/usr/bin:/bin', 'HOME': str(cwd), **(env or {})})


class HistoryFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'data'
        self.project = Path(self.tmp.name) / 'proj'
        self.data.mkdir()
        (self.project / 'utils').mkdir(parents=True)
        self.host = Host(self.data)
        self.addCleanup(self.host.close)
        store = self.host.store
        store.add_profile('cs', 'claude', self.data / 'cs', 'bryce@example.test', 'personal')
        store.add_profile('cx', 'codex', self.data / 'cx', 'bryce@example.test', 'personal')
        store.add_profile('w', 'claude', self.data / 'w', 'bryce@work.test', 'work')
        with store.db:
            store.db.execute("UPDATE profiles SET name='Personal' WHERE id='cs'")
            store.db.execute("UPDATE profiles SET name='ChatGPT Plus' WHERE id='cx'")
        store.add_project('p', self.project, 'cs', 'claude-sonnet-5')
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        store.add_project('q', other, 'cs', 'claude-sonnet-5')
        self.root = store.db.execute("SELECT cwd FROM projects WHERE id='p'").fetchone()['cwd']
        self.db = store.db
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_tool_audit (task_id TEXT NOT NULL, token TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(task_id, token))')
            self.db.execute('CREATE TABLE IF NOT EXISTS coding_requests (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, client_id TEXT NOT NULL, task_id TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(thread_id, client_id))')
            self.db.execute('CREATE TABLE IF NOT EXISTS orchestrator_entries (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, profile_id TEXT NOT NULL, client_id TEXT, created_at REAL NOT NULL, document TEXT NOT NULL)')
        self.fix_thread = self.thread('Timezone fix')
        self.board_thread = self.thread('Live board', profile='cx', model='gpt-5.5')
        self.ideas_thread = self.thread('Ideas storage')

    # ---------------------------------------------------------------- records
    def thread(self, name, profile='cs', model='claude-sonnet-5', project='p', private=False):
        key = self.host.dispatch('create_conversation', dict(projectId=project, name=name, private=private))['id']
        with self.db:
            self.db.execute('UPDATE threads SET profile_id=?, model=? WHERE id=?', (profile, model, key))
        return key

    def binding(self, thread, profile='cs', provider='claude', model='claude-sonnet-5', project='p'):
        return dict(id=thread, project_id=project, channel_id=thread, profile_id=profile, model=model, visibility='shared', owner=None,
                    session_id=None, provider=provider, identity='bryce@example.test', category='personal', method='subscription', cwd=self.root)

    def save(self, task, evidence=None, audit=()):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)', (task['id'], json.dumps(task)))
            if evidence is not None:
                self.db.execute('INSERT OR REPLACE INTO task_evidence VALUES (?,?)', (task['id'], json.dumps(dict(id=task['id'], attempts=evidence))))
            for token, document in audit:
                self.db.execute('INSERT OR REPLACE INTO coding_tool_audit VALUES (?,?,?)', (task['id'], token, json.dumps(dict(token=token, **document))))
        return task

    def base(self, task_id, thread, *, project='p', **extra):
        task = dict(id=task_id, context=dict(thread_id=thread, project_id=project, actor=ACTOR), visibility='shared', origin='conversation',
                    binding=self.binding(thread, project=project), files=[], folders=[], argv=ARGV, check_profile='python-unittest',
                    tokens=[], runs=[], messages=[], error=None, route=dict(mode='fixed', retry_of=None, switched_from=None),
                    continuity=dict(state='fresh', from_task_id=None, reason=None), collaboration=dict(mode='off', review=None))
        task.update(extra)
        return task

    def answer(self):
        """The read-only question, as e22 recorded it."""
        run = dict(id='r-ask', state='completed', prior_attempts=0, process=dict(start=int((T0 + 1) * 1e6)),
                   told=dict(notes=[], skills=[]), knowledge_snapshot=dict(rules=[], repository=dict(files=[dict(path='CLAUDE.md', bytes=4100)])))
        return self.save(self.base(ASK_ID, self.fix_thread, state='answered', read_only=True, runs=[run],
                                   messages=[dict(role='user', text='Which function in utils/dates.py drops the timezone? Answer in one line, change nothing.', created_at=T0),
                                             dict(role='assistant', text='`parse_date` in utils/dates.py:5 drops the timezone via `.replace(tzinfo=None)`.', run_id='r-ask', created_at=T0 + 6)],
                                   tokens=[dict(token='k-ask-read', tool='dervo_read_file', state='delivered', run_id='r-ask', path='utils/dates.py', started_at=T0 + 3, ended_at=T0 + 3.01, success=True)]))

    def fix(self, **extra):
        """"fix it", in a fresh session told what the question found. Dervo's check ran and exited 0."""
        start = T0 + 60
        tok = lambda token, tool, at, **more: dict(dict(token=token, tool=tool, state='delivered', run_id='r-fix', started_at=start + at, ended_at=start + at + 0.5, success=True, attempt=None, path=None), **more)
        tokens = [tok('k-read', 'dervo_read_file', 3, path='utils/dates.py'), tok('k-guide-1', 'dervo_update_task_guide', 7),
                  tok('k-write', 'dervo_write_file', 9, path='utils/dates.py', attempt=1),
                  dict(tok('k-check', 'dervo_run_tests', 11, attempt=1), ended_at=start + 12.6), tok('k-guide-2', 'dervo_update_task_guide', 16)]
        run = dict(id='r-fix', state='completed', prior_attempts=0, process=dict(start=int((start + 0.5) * 1e6)),
                   told=dict(notes=[dict(id='n1', text='Tests run with python3 -m unittest.')], skills=[],
                             transcript='Earlier in this conversation:\nUser: Which function ...',
                             continuity=dict(state='fresh', reason='Switched between asking about the project and coding in it.', from_task_id=ASK_ID),
                             recent='Recent work in other chats of this project (use dervo_search_history for details):\n- 2 hours ago: Live board warnings (running)\n'),
                   knowledge_snapshot=dict(rules=[], repository=dict(files=[dict(path='CLAUDE.md', bytes=4100)])),
                   guide=dict(title='Fix parse_date', step='Done: removed the call.', limits=LIMITS),
                   guide_steps=[dict(step='Removing the .replace(tzinfo=None) call and updating the docstring.', at=start + 7),
                                dict(step='Done: removed the .replace(tzinfo=None) call and updated the docstring.', at=start + 16)])
        task = self.base(FIX_ID, self.fix_thread, state='completed', files=['greeting.py', 'test_sample.py', 'utils/dates.py'], runs=[run], tokens=tokens,
                         messages=[dict(role='user', text='fix it', created_at=start), dict(role='assistant', text=REPLY, run_id='r-fix', created_at=start + 18)],
                         continuity=dict(state='fresh', from_task_id=ASK_ID, reason='Switched between asking about the project and coding in it.'),
                         completion=dict(at=start + 21, attempt=1, run_id='r-fix', changed_files=['utils/dates.py'], test_exit_status=0,
                                         check_profile='python-unittest', human_approved=False))
        task.update(extra)
        test = dict(stdout='', stderr='.\nRan 1 test in 0.000s\n\nOK\n', exit_status=0, stopped=None, argv=ARGV, check_profile='python-unittest')
        evidence = [dict(number=1, kind='change', files=['utils/dates.py'], before={'utils/dates.py': image(ORIGINAL)},
                         after={'utils/dates.py': image(FIXED)}, diff={'utils/dates.py': FIX_DIFF}, state='verified', test=test)]
        audit = [('k-guide-1', dict(arguments=dict(title='Fix parse_date', step='Removing the .replace(tzinfo=None) call and updating the docstring.',
                                                   why='That call was discarding the parsed timezone offset.', limits=LIMITS))),
                 ('k-guide-2', dict(arguments=dict(step='Done: removed the .replace(tzinfo=None) call and updated the docstring.', why=''))),
                 ('k-write', dict(arguments=dict(path='utils/dates.py', expected_hash='x', content=FIXED))),
                 ('k-check', dict(arguments={}, response=dict(success=True, text=json.dumps(test))))]
        return self.save(task, evidence, audit)

    def board(self):
        """Live board, on Codex: added parse_day to the same file afterwards, not committed yet."""
        start = T0 + 3600
        run = dict(id='r-board', state='completed', prior_attempts=0, started_at=start, ended_at=start + 600, told=dict(notes=[], skills=[]))
        task = self.base(BOARD_ID, self.board_thread, state='ready_for_review', binding=self.binding(self.board_thread, 'cx', 'codex', 'gpt-5.5'),
                         runs=[run], messages=[dict(role='user', text='When two running threads edit the same file, show it on the board.', created_at=start)],
                         tokens=[dict(token='b-read', tool='dervo_read_file', state='delivered', run_id='r-board', path='utils/dates.py', started_at=start + 5, success=True),
                                 dict(token='b-guide', tool='dervo_update_task_guide', state='delivered', run_id='r-board', started_at=start + 8, success=True),
                                 dict(token='b-write', tool='dervo_write_file', state='delivered', run_id='r-board', path='utils/dates.py', attempt=1, started_at=start + 10, success=True)],
                         completion=dict(at=start + 600, attempt=1, run_id='r-board', changed_files=['utils/dates.py'], test_exit_status=None))
        evidence = [dict(number=1, kind='change', before={'utils/dates.py': image(FIXED)}, after={'utils/dates.py': image(BOARD)},
                         diff={'utils/dates.py': BOARD_DIFF}, state='prepared', test=None)]
        return self.save(task, evidence, [('b-guide', dict(arguments=dict(step='Adding a day parser the board can group by.', why='The board groups by day.')))])

    def ideas(self):
        """Ideas storage: Home started it from your message, in full access. Claude Code ran the tests itself twice."""
        start = T0 + 1800
        you, reply = 'you-entry', 'reply-entry'
        with self.db:
            self.db.execute('INSERT INTO orchestrator_entries VALUES (?,?,?,?,?,?)', (you, 'p', 'cs', 'c1', start - 60, json.dumps(dict(
                kind='you', text='Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.', actor=ACTOR))))
            self.db.execute('INSERT INTO orchestrator_entries VALUES (?,?,?,?,?,?)', (reply, 'p', 'cs', 'c1:reply', start - 55, json.dumps(dict(
                kind='reply', state='ready', answers=you, text='Starting Ideas storage.', parts=[dict(
                    index=0, action='start', text='Build step 1 of PLAN-orchestrator.md: ideas stored per project.', title='Ideas storage',
                    state='sent', channel_id=self.ideas_thread)]))))
            self.db.execute('INSERT INTO coding_requests VALUES (?,?,?,?,?)', ('req-ideas', self.ideas_thread, f'{reply}-0', IDEAS_ID, '{}'))
        native = lambda token, tool, at, **more: dict(token=token, tool=tool, native=True, state='delivered', run_id='r-ideas', started_at=start + at, success=True, **more)
        run = dict(id='r-ideas', state='completed', prior_attempts=0, started_at=start, ended_at=start + 840, told=dict(notes=[], skills=[]),
                   guide=dict(limits='Not checked: the frontend.'))
        task = self.base(IDEAS_ID, self.ideas_thread, state='ready_for_review', requested_by='home', clicked=True, full_access=True,
                         project_access=True, check_profile='manual-review', runs=[run],
                         messages=[dict(role='user', text='Build step 1 of PLAN-orchestrator.md: ideas stored per project.', created_at=start),
                                   dict(role='assistant', text='44 backend tests pass. With the group mirror off, 5 of the 10 new tests fail, so they bite.', run_id='r-ideas', created_at=start + 840)],
                         tokens=[native('i-read', 'native:Read', 10, path=f'{self.root}/PLAN-orchestrator.md'),
                                 native('i-write', 'native:Write', 120, path=f'{self.root}/backend/project_ideas.py'),
                                 dict(native('i-test', 'native:Bash', 480, command='python3 -m unittest test_project_ideas'), ended_at=start + 483),
                                 dict(native('i-test-2', 'native:Bash', 600, command='python3 -m unittest test_project_ideas'), ended_at=start + 604, success=False),
                                 native('i-log', 'native:Bash', 700, command='git log --oneline -- PLAN-orchestrator.md')],
                         completion=dict(at=start + 840, attempt=1, run_id='r-ideas', changed_files=['backend/project_ideas.py'], test_exit_status=None, check_profile='manual-review'))
        evidence = [dict(number=1, kind='manual-review', before={}, after={'backend/project_ideas.py': image('A = 1\nB = 2\n')},
                         diff={'backend/project_ideas.py': '--- before\n+++ after\n@@ -0,0 +1,2 @@\n+A = 1\n+B = 2\n'}, state='unverified', test=None)]
        return self.save(task, evidence)

    def listing(self, filter='all', provider='', file=''):
        return run_history.dispatch(self.host, 'history_list', dict(projectId='p', profileId='cs', filter=filter, provider=provider, file=file), ACTOR)

    def record(self, task_id, run_id=''):
        return run_history.dispatch(self.host, 'run_record', dict(projectId='p', profileId='cs', taskId=task_id, runId=run_id), ACTOR)

    def trace(self, path, line, operation='trace_line'):
        return run_history.dispatch(self.host, operation, dict(projectId='p', profileId='cs', path=path, line=line), ACTOR)

    def repository(self):
        """The project as a git repository: your first commit, then Dervo's commit of the fix, then Live board's change on
        disk, not committed."""
        (self.project / 'utils' / 'dates.py').write_text(ORIGINAL)
        (self.project / 'test_sample.py').write_text('import unittest\n')
        git(self.project, 'init', '-q', '-b', 'main')
        git(self.project, 'add', '.')
        git(self.project, 'commit', '-q', '-m', 'Start the sample', env={'GIT_AUTHOR_DATE': '1789800000 +0000', 'GIT_COMMITTER_DATE': '1789800000 +0000'})
        (self.project / 'utils' / 'dates.py').write_text(FIXED)
        git(self.project, 'add', 'utils/dates.py')
        git(self.project, 'commit', '-q', '-m', f'Dervo: Timezone fix\n\nTask {FIX_ID} approved in Dervo. Dervo made this commit; the agent ran no git.\n'
            'Request: fix it\nChanged files:\n- utils/dates.py\n', author='Dervo')
        (self.project / 'utils' / 'dates.py').write_text(BOARD)


class HistoryListTests(HistoryFixture):
    def test_each_run_is_a_row_newest_first_with_how_it_ended_and_what_it_did(self):
        self.answer(); self.fix()
        rows = self.listing()['runs']
        self.assertEqual([row['taskId'] for row in rows], [FIX_ID, ASK_ID])
        fix, ask = rows
        self.assertEqual((fix['glyph'], fix['ended'], fix['name']), ('done', 'Finished', 'Timezone fix'))
        self.assertEqual(fix['did'], 'Changed dates.py. Dervo’s check ran test_sample.py: exit 0. Not committed.')
        self.assertEqual(fix['line'], 'Finished. ' + fix['did'])
        self.assertEqual((fix['provider'], fix['providerLabel'], fix['model'], fix['account'], fix['access']),
                         ('claude', 'Claude Code', 'claude-sonnet-5', 'Personal', 'Chosen files'))
        self.assertEqual((fix['startedAt'], fix['endedAt']), (T0 + 60.5, T0 + 81))
        self.assertEqual(fix['asked'], dict(text='fix it', at=T0 + 60, where='thread'))
        self.assertEqual(fix['told'], 'CLAUDE.md, 1 project note, what happened earlier in this thread and recent work in 1 other thread.')
        self.assertEqual((ask['ended'], ask['did'], ask['access']), ('Answered', 'Changed no files.', 'Read only'))
        fresh = next(link for link in fix['links'] if link['kind'] == 'fresh')
        self.assertEqual(fresh['taskId'], ASK_ID)
        self.assertTrue(fresh['text'].startswith('Started fresh after “Which function in utils/dates.py drops the timezone?'))

    def test_what_the_agent_said_is_kept_apart_and_never_becomes_the_line(self):
        self.fix()
        row = self.listing()['runs'][0]
        self.assertIn(dict(text='I ran the project\'s test suite (`test_sample.py`) and it passed: 1 test, OK.', source='reply'), row['said'])
        self.assertEqual(row['said'][-1], dict(text=LIMITS, source='limits'))
        for said in row['said']:
            self.assertNotIn(said['text'], row['line'])
            self.assertNotIn(said['text'], row['did'])

    def test_a_full_access_run_says_no_check_ran_in_dervo_and_lists_the_commands_it_ran_itself(self):
        self.ideas()
        row = next(row for row in self.listing()['runs'] if row['kind'] == 'run')
        self.assertEqual((row['glyph'], row['ended'], row['access']), ('needs', 'Waiting for your review', 'Full access'))
        self.assertEqual(row['did'], 'Changed project_ideas.py. No check ran in Dervo.')
        self.assertEqual([(own['command'], own['error'], own['seconds']) for own in row['own']],
                         [('python3 -m unittest test_project_ideas', False, 3), ('python3 -m unittest test_project_ideas', True, 4)])
        self.assertEqual(row['said'][0], dict(text='44 backend tests pass.', source='reply'))

    def test_home_started_it_from_your_words_and_its_reply_is_a_row_linked_to_the_run(self):
        self.ideas()
        rows = self.listing()['runs']
        run = next(row for row in rows if row['kind'] == 'run')
        home = next(row for row in rows if row['kind'] == 'home')
        self.assertEqual(run['startedBy'], 'home')
        self.assertEqual(run['asked'], dict(text='Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.', at=T0 + 1740, where='home'))
        self.assertEqual(run['links'][0], dict(kind='home', name='Home', at=T0 + 1740, text='Started by Home from your message'))
        self.assertEqual((home['name'], home['line'], home['startedAt']), ('Home', 'Started Ideas storage from your message.', T0 + 1745))
        self.assertIn(run['key'], home['linked'])
        self.assertIn(home['key'], run['linked'])

    def test_runs_group_into_one_piece_of_work_per_thread_with_its_git_line(self):
        # Beta.16 (b16/git): one card per thread. Home's reply that started a thread is its first line; the git line
        # says each commit, whether this Mac knows it's on GitHub, and the pull request as Dervo last saw it.
        import github_host
        self.answer(); self.fix(); self.board(); self.ideas()
        sha = 'a' * 40
        task = json.loads(self.db.execute('SELECT document FROM coding_tasks WHERE id=?', (FIX_ID,)).fetchone()[0])
        self.save(dict(task, state='idle', commit=dict(sha=sha, at=T0 + 100, reverted=None)))
        github_host.ensure_tables(self.db)
        with self.db:
            self.db.execute('INSERT INTO pr_records VALUES (?,?,?,?)', ('r1', 'p', self.fix_thread, json.dumps(dict(
                id='r1', project_id='p', thread_id=self.fix_thread, state='opened', number=214, url='https://github.com/o/r/pull/214', repo='o/r',
                head='dervo/timezone-fix', base='main', mode='thread', draft=True, checked_at=T0 + 200, checked_as='brambach',
                seen=dict(state='open', draft=True, review='changes_requested', checks=dict(passing=2, failing=0, pending=0),
                          comments=[dict(author='ana-l', text='Why drop it?', path='utils/dates.py', line=5, at=T0 + 150)])))))
        with patch.object(github_host, 'on_remotes', lambda cwd, shas, **_: {sha: False}):
            listing = self.listing()
        work = {item['channelId']: item for item in listing['work']}
        fix = work[self.fix_thread]
        self.assertEqual((fix['name'], fix['runCount'], fix['agents']), ('Timezone fix', 2, ['Claude Code']))
        self.assertEqual(fix['commits'], [dict(sha=sha, short='aaaaaaa', at=T0 + 100, reverted=False, onGitHub=False)])
        self.assertEqual(fix['where'], 'github', 'its commits went up on the pull request’s branch')
        self.assertEqual((fix['pr']['number'], fix['pr']['review'], fix['pr']['checkedAs']), (214, 'changes_requested', 'brambach'))
        self.assertEqual(fix['pr']['comments'][0]['text'], 'Why drop it?')
        self.assertEqual(set(fix['runs']), {row['key'] for row in listing['runs'] if row['channelId'] == self.fix_thread})
        ideas = next(item for item in listing['work'] if item['name'] == 'Ideas storage')
        self.assertEqual((ideas['glyph'], ideas['state'], ideas['where']), ('needs', 'Ready for review', None))
        self.assertTrue(ideas['runs'][0].startswith('home:'), 'Home’s reply that started it is its first line')
        self.assertEqual(ideas['reviewTaskId'], IDEAS_ID)
        self.assertEqual({key: listing['counts'][key] for key in ('work', 'waiting', 'prs')}, dict(work=3, waiting=2, prs=1))
        self.assertEqual([item['channelId'] for item in self.listing('prs')['work']], [self.fix_thread])
        self.assertEqual({item['name'] for item in self.listing('waiting')['work']}, {'Ideas storage', 'Live board'})
        # Without the pull request, the unpushed commit reads as Not on GitHub.
        with self.db:
            self.db.execute('DELETE FROM pr_records')
        with patch.object(github_host, 'on_remotes', lambda cwd, shas, **_: {sha: False}):
            self.assertEqual([item['channelId'] for item in self.listing('local')['work']], [self.fix_thread])
        with patch.object(github_host, 'on_remotes', lambda cwd, shas, **_: {}):
            self.assertIsNone(next(item for item in self.listing()['work'] if item['channelId'] == self.fix_thread)['where'], 'git can’t say: no guess')

    def test_a_thread_dervo_tidied_away_says_so(self):
        self.fix()
        with self.db:
            self.db.execute('INSERT INTO conversation_archives VALUES (?,?)', (self.fix_thread, int(T0 + 900)))
            self.db.execute('CREATE TABLE IF NOT EXISTS conversation_tidied (thread_id TEXT PRIMARY KEY, batch TEXT NOT NULL, tidied_at INTEGER NOT NULL, quiet_since INTEGER NOT NULL)')
            self.db.execute('INSERT INTO conversation_tidied VALUES (?,?,?,?)', (self.fix_thread, 'b1', int(T0 + 900), int(T0)))
        item = self.listing()['work'][0]
        self.assertEqual((item['archived'], item['tidied']), (True, dict(at=T0 + 900, quietSince=T0)))
        self.assertEqual(self.record(FIX_ID)['taskId'], FIX_ID, 'its record still opens')

    def test_runs_in_two_threads_on_one_file_are_linked_in_words(self):
        self.fix(); self.board()
        rows = {row['taskId']: row for row in self.listing()['runs']}
        self.assertIn(dict(kind='after', taskId=FIX_ID, channelId=self.fix_thread, name='Timezone fix', path='utils/dates.py',
                           text='Edited dates.py after Timezone fix'), rows[BOARD_ID]['links'])
        self.assertIn(dict(kind='later', taskId=BOARD_ID, channelId=self.board_thread, name='Live board', path='utils/dates.py',
                           text='Live board edited the same file later, on Codex'), rows[FIX_ID]['links'])
        self.assertIn(rows[BOARD_ID]['key'], rows[FIX_ID]['linked'])

    def test_filters_count_and_narrow_the_list(self):
        self.answer(); self.fix(); self.board(); self.ideas()
        everything = self.listing()
        self.assertEqual({key: everything['counts'][key] for key in ('all', 'changed', 'home')}, dict(all=5, changed=3, home=2))
        self.assertEqual(everything['providers'], ['claude', 'codex'])
        self.assertEqual({row['taskId'] for row in self.listing('changed')['runs']}, {FIX_ID, BOARD_ID, IDEAS_ID})
        self.assertEqual({row['kind'] for row in self.listing('home')['runs']}, {'run', 'home'})
        self.assertEqual([row['taskId'] for row in self.listing(provider='codex')['runs']], [BOARD_ID])
        self.assertEqual({row['taskId'] for row in self.listing(file='dates')['runs']}, {FIX_ID, BOARD_ID})
        for bad in (dict(filter='mine'), dict(provider='gemini'), dict(file=7)):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.listing(**bad)

    def test_only_runs_you_can_open_in_this_project_show_and_archived_threads_stay_marked(self):
        self.fix()
        private = self.host.dispatch('create_conversation', dict(projectId='p', name='Theirs', private=True))['id']
        work = self.thread('Work thread', profile='w')
        archived = self.thread('Old spike')
        elsewhere = self.thread('Other project', project='q')
        with self.db:
            self.db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (private,))
            self.db.execute('INSERT INTO conversation_archives VALUES (?,?)', (archived, int(T0)))
        for number, thread in enumerate((private, work, archived)):
            task = self.base(f'00000000-0000-0000-0000-00000000000{number}', thread, state='completed', runs=[dict(id='r', state='completed')],
                             messages=[dict(role='user', text='hidden', created_at=T0)])
            if thread == work:
                task['binding'] = dict(self.binding(thread), profile_id='w')
            self.save(task)
        self.save(self.base('00000000-0000-0000-0000-000000000009', elsewhere, project='q', state='completed', runs=[dict(id='r', state='completed')]))
        # Beta.16 (b16/git with b16/lifecycle, 25 Sep): an archived thread keeps its place in History, marked Archived.
        listing = self.listing()
        self.assertEqual([row['taskId'] for row in listing['runs']], [FIX_ID, '00000000-0000-0000-0000-000000000002'])
        self.assertEqual({item['channelId']: item['archived'] for item in listing['work']}, {self.fix_thread: False, archived: True})
        self.assertIsNone(next(item for item in listing['work'] if item['archived'])['tidied'], 'archived by hand, not by the tidy')
        with self.assertRaises(Rejected):
            run_history.dispatch(self.host, 'history_list', dict(projectId='p', profileId='cx', filter='all', provider='', file=''), ACTOR)
        with self.assertRaises(Rejected):
            self.record('00000000-0000-0000-0000-000000000000')

    def test_a_correction_is_a_second_run_of_the_same_task(self):
        task = self.fix()
        second = dict(task['runs'][0], id='r-fix-2', prior_attempts=1, process=dict(start=int((T0 + 200) * 1e6)))
        task['runs'].append(second)
        task['messages'].insert(2, dict(role='user', text='Keep the docstring short.', created_at=T0 + 199))
        task['completion']['run_id'] = 'r-fix-2'
        self.save(task)
        rows = [row for row in self.listing()['runs'] if row['taskId'] == FIX_ID]
        self.assertEqual([(row['runIndex'], row['runCount']) for row in rows], [(2, 2), (1, 2)])
        self.assertEqual(rows[0]['asked']['text'], 'Keep the docstring short.')
        self.assertIn(dict(kind='correction', text='Second run in this task, after you asked for changes'), rows[0]['links'])
        self.assertEqual(rows[1]['ended'], 'Finished')


class RunRecordTests(HistoryFixture):
    def test_the_record_puts_each_call_under_the_step_it_reported(self):
        self.answer(); self.fix()
        record = self.record(FIX_ID)
        self.assertEqual((record['runIndex'], record['runCount'], record['name']), (1, 1, 'Timezone fix'))
        self.assertEqual(record['why'], dict(you=dict(text='fix it', at=T0 + 60, where='thread'), home=None, origin=None, steers=[]))
        first, removing, done = record['steps']
        self.assertEqual((first['text'], [(c['tool'], c['target'], c['out']) for c in first['calls']]), (None, [('Read', 'utils/dates.py', '')]))
        self.assertEqual(removing['text'], 'Removing the .replace(tzinfo=None) call and updating the docstring.')
        self.assertEqual(removing['why'], 'That call was discarding the parsed timezone offset.')
        self.assertEqual([(c['tool'], c['target'], c['out']) for c in removing['calls']], [('Write', 'utils/dates.py', '+2 −2'), ('Check', '', '2 s · exit 0')])
        self.assertEqual(done['calls'], [])

    def test_checked_puts_what_dervo_saw_beside_what_it_said(self):
        self.fix()
        record = self.record(FIX_ID)
        self.assertEqual([check['text'] for check in record['checks']['dervo']], ['Dervo’s check ran test_sample.py: exit 0.'])
        self.assertEqual(record['checks']['dervo'][0]['at'], T0 + 72.6)
        self.assertIsNone(record['checks']['note'])
        self.assertEqual(record['said'][-1], dict(text=LIMITS, source='limits'))
        self.assertEqual(record['files'], [dict(path='utils/dates.py', added=2, removed=2)])

    def test_what_it_was_told_comes_from_the_run_record(self):
        self.answer(); self.fix()
        told = self.record(FIX_ID)['told']
        self.assertEqual(told['repository'], [dict(path='CLAUDE.md', bytes=4100, cut=False)])
        self.assertEqual(told['notes'], ['Tests run with python3 -m unittest.'])
        self.assertEqual(told['recent'], ['2 hours ago: Live board warnings (running)'])
        self.assertEqual(told['earlier'], dict(state='fresh', reason='Switched between asking about the project and coding in it.',
                                              taskId=ASK_ID, name='Which function in utils/dates.py drops the timezone? Answer…'))

    def test_a_home_job_shows_your_words_first_and_homes_instruction_as_homes(self):
        self.ideas()
        record = self.record(IDEAS_ID)
        self.assertEqual(record['why']['you'], dict(text='Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped.', at=T0 + 1740, where='home'))
        self.assertEqual(record['why']['home'], dict(text='Build step 1 of PLAN-orchestrator.md: ideas stored per project.', at=T0 + 1745))
        self.assertTrue(record['canReview'])
        self.assertEqual(record['checks']['note'], 'Dervo ran no check of its own, so it can’t see a test count.')
        self.assertEqual([(c['tool'], c['target'], c['out']) for c in record['steps'][0]['calls']],
                         [('Read', 'PLAN-orchestrator.md', ''), ('Write', 'backend/project_ideas.py', ''),
                          ('Bash', 'python3 -m unittest test_project_ideas', '3 s · no error'),
                          ('Bash', 'python3 -m unittest test_project_ideas', '4 s · error'),
                          ('Bash', 'git log --oneline -- PLAN-orchestrator.md', 'no error')])
        self.assertEqual(record['files'], [dict(path='backend/project_ideas.py', added=2, removed=0)])
        self.assertEqual(record['said'], [dict(text='44 backend tests pass.', source='reply'),
                                          dict(text='With the group mirror off, 5 of the 10 new tests fail, so they bite.', source='reply'),
                                          dict(text='Not checked: the frontend.', source='limits')])

    def test_linked_names_the_run_it_came_from_and_the_run_that_touched_its_file_later(self):
        self.answer(); self.fix(); self.board()
        links = self.record(FIX_ID)['links']
        self.assertEqual([link['kind'] for link in links], ['fresh', 'later'])
        self.assertEqual(links[1]['text'], 'Codex edited dates.py after this run')
        continued = self.record(ASK_ID)['links']
        self.assertEqual(continued[0]['kind'], 'continued_by')
        self.assertEqual(continued[0]['text'], 'The next run in this thread carried on from this, “Fix parse_date”')

    def test_an_unknown_run_or_task_is_refused(self):
        self.fix()
        with self.assertRaises(Rejected):
            self.record(FIX_ID, 'r-nope')
        with self.assertRaises(Rejected):
            self.record('not-a-task')


class TraceTests(HistoryFixture):
    def setUp(self):
        super().setUp()
        self.repository()
        self.answer(); self.fix(); self.board()

    def test_a_dervo_commit_leads_back_to_the_run_and_your_request(self):
        why = self.trace('utils/dates.py', 6)
        self.assertEqual(why['text'], '    return datetime.fromisoformat(text)')
        self.assertTrue(why['committed'])
        self.assertEqual(why['commit']['taskId'], FIX_ID)
        self.assertEqual(why['change'], f'Committed as {why["commit"]["short"]} when you approved it in Dervo.')
        self.assertEqual((why['run']['name'], why['run']['providerLabel']), ('Timezone fix', 'Claude Code'))
        self.assertEqual(why['written'], dict(at=T0 + 69, how='through Dervo’s write tool'))
        self.assertEqual(why['step'], dict(text='Removing the .replace(tzinfo=None) call and updating the docstring.', at=T0 + 67))
        self.assertEqual(why['before'], 'It read this file. Dervo had told it about recent work in 1 other thread.')
        self.assertEqual(why['asked'], dict(text='fix it', at=T0 + 60, where='thread'))
        self.assertEqual(why['checked'], dict(text='Dervo’s check ran test_sample.py: exit 0. It can’t tell which lines the check ran.', at=T0 + 72.6))
        self.assertIsNone(why['nobody'])

    def test_an_uncommitted_line_leads_to_the_run_whose_change_holds_it(self):
        why = self.trace('utils/dates.py', 9)
        self.assertFalse(why['committed'])
        self.assertEqual(why['change'], 'Not committed. Part of Live board’s result, waiting for your review.')
        self.assertEqual((why['run']['name'], why['run']['provider']), ('Live board', 'codex'))
        self.assertEqual(why['step'], dict(text='Adding a day parser the board can group by.', at=T0 + 3608))
        self.assertEqual(why['checked'], dict(text='Nothing ran a check in Dervo after this change.', at=None))

    def test_a_line_nobody_wrote_in_dervo_says_so_with_gits_author(self):
        why = self.trace('utils/dates.py', 1)
        self.assertEqual(why['nobody'], run_history.NOBODY)
        self.assertEqual(why['change'], f'Committed as {why["commit"]["short"]}.')
        self.assertEqual(why['author'], dict(name='Bryce', at=1789800000.0, subject='Start the sample'))
        self.assertIsNone(why['run'])

    def test_the_file_view_labels_each_block_with_the_run_that_wrote_it(self):
        view = self.trace('utils/dates.py', 0, 'trace_file')
        self.assertEqual((view['name'], view['folder'], view['total'], view['first'], view['last']), ('dates.py', 'utils', 10, 1, 10))
        self.assertEqual([(b['first'], b['last'], b['label'], b['committed']) for b in view['blocks']],
                         [(1, 4, 'Bryce', True), (5, 6, 'Timezone fix', True), (7, 10, 'Live board', False)])
        self.assertEqual([(run['name'], run['what']) for run in view['runs']],
                         [('Live board', 'wrote lines 7 to 10'), ('Timezone fix', 'wrote lines 5 to 6'), ('Timezone fix', 'read it')])
        self.assertEqual((view['changes'], view['uncommitted']), (2, 2))
        self.assertEqual(view['focus'], 7)

    def test_with_no_line_named_the_window_opens_on_the_newest_change(self):
        (self.project / 'utils' / 'dates.py').write_text('# filler\n' * 200 + BOARD)
        task = self.board()
        diff = BOARD_DIFF.replace('@@ -6,0 +7,4 @@', '@@ -206,0 +207,4 @@')
        self.save(task, [dict(number=1, kind='change', before={'utils/dates.py': image(FIXED)}, after={'utils/dates.py': image(BOARD)},
                              diff={'utils/dates.py': diff}, state='prepared', test=None)])
        view = self.trace('utils/dates.py', 0, 'trace_file')
        self.assertEqual((view['focus'], view['first'], view['last']), (207, 131, 210))
        self.assertEqual(view['blocks'][-1]['label'], 'Live board')

    def test_a_folder_outside_git_traces_from_dervos_records_only(self):
        subprocess.run(['/bin/rm', '-rf', str(self.project / '.git')], check=True)
        view = self.trace('utils/dates.py', 0, 'trace_file')
        self.assertIn('isn’t in a git repository', view['note'])
        self.assertEqual(view['blocks'][-1]['label'], 'Live board')
        why = self.trace('utils/dates.py', 1)
        self.assertEqual((why['nobody'], why['change']), (run_history.NOBODY, None))

    def test_only_regular_files_inside_the_project_can_be_traced(self):
        outside = Path(self.tmp.name) / 'secret.txt'
        outside.write_text('key\n')
        os.symlink(outside, self.project / 'link.txt')
        os.symlink(Path(self.tmp.name), self.project / 'up')
        for path in ('../secret.txt', str(outside), 'link.txt', 'up/secret.txt', '.git/config', '.GIT/config', '.Git/HEAD', 'utils', 'missing.py', '', 'a\0b'):
            with self.subTest(path=path), self.assertRaises(Rejected):
                self.trace(path, 1)
        self.assertEqual(self.trace(f'{self.root}/utils/dates.py', 1)['path'], 'utils/dates.py')
        for line in (11, -1, True, '3'):
            with self.subTest(line=line), self.assertRaises(Rejected):
                self.trace('utils/dates.py', line)


PROPOSAL_ID = '7c0e5b1a-3f2d-4b8e-9a61-0d4c2b8e1f55'
ANSWER_ID = '2b6d9e0f-1a4c-4e7b-8d3f-5c9a0e2b7d14'
PROPOSED = 'Purpose: Group the board by day\n\nAdd parse_day to utils/days.py so the board can group runs by day.'
QUESTION = 'Should parse_date keep naive datetimes for the old callers?'


class AgentWordsTests(HistoryFixture):
    """Since the H1 fix a started proposal and an answer sent after a run arrive as Home jobs (requested_by='home'). Their
    words are an agent's: the proposal, the question. History says where they came from and quotes those words as the
    agent's, never under what you asked (merge security pass, 23 Sep, F2)."""

    def setUp(self):
        super().setUp()
        thread_fanout.schema(self.db)
        task_questions.schema(self.db)
        self.fix(); self.board()

    def launched(self, task_id, thread, client, text, *, start=T0 + 7200, path='utils/days.py', content='def parse_day(text):\n    return text[:10]\n'):
        """A run Dervo started for you with an agent's words: a Home job with the given client id, which wrote one file."""
        (self.project / path).write_text(content)
        diff = '--- before\n+++ after\n@@ -0,0 +1,2 @@\n' + ''.join('+' + line + '\n' for line in content.splitlines())
        task = self.base(task_id, thread, state='ready_for_review', requested_by='home', clicked=False,
                         runs=[dict(id='r-' + task_id[:4], state='completed', prior_attempts=0, started_at=start, ended_at=start + 300, told=dict(notes=[], skills=[]))],
                         messages=[dict(role='user', text=text, created_at=start)],
                         tokens=[dict(token='w-' + task_id[:4], tool='dervo_create_file', state='delivered', run_id='r-' + task_id[:4], path=path, attempt=1,
                                      started_at=start + 20, success=True)],
                         completion=dict(at=start + 300, attempt=1, run_id='r-' + task_id[:4], changed_files=[path], test_exit_status=None))
        with self.db:
            self.db.execute('INSERT INTO coding_requests VALUES (?,?,?,?,?)', ('req-' + task_id[:4], thread, client, task_id, '{}'))
        return self.save(task, [dict(number=1, kind='change', before={}, after={path: image(content)}, diff={path: diff}, state='prepared', test=None)])

    def proposal(self):
        child = self.thread('Day parser')
        with self.db:
            self.db.execute('INSERT INTO thread_proposals VALUES (?,?,?,?,?,?,?)', ('prop1', 'p', self.board_thread, BOARD_ID, 'days', child, json.dumps(dict(
                id='prop1', project_id='p', thread_id=self.board_thread, task_id=BOARD_ID, title='Day parser', purpose='Group the board by day',
                request='Add parse_day to utils/days.py so the board can group runs by day.', state='started', child_thread_id=child))))
            self.db.execute('INSERT INTO thread_fanout_children VALUES (?,?,?,?,?)', (child, 'p', self.board_thread, BOARD_ID, 'Group the board by day'))
        return self.launched(PROPOSAL_ID, child, 'thread-proposal-prop1', PROPOSED)

    def question(self, answer=None):
        answer = answer or dict(kind='text', text='No, always keep the timezone.')
        with self.db:
            self.db.execute('INSERT INTO task_questions VALUES (?,?,?,?,?,?)', ('q1', 'p', self.fix_thread, FIX_ID, 'naive', json.dumps(dict(
                id='q1', project_id='p', thread_id=self.fix_thread, task_id=FIX_ID, ask=QUESTION, answer=answer, state='answered',
                options=[dict(id='o1', label='Keep the timezone')]))))
        text = f"Answer to question q1 from this task:\n{QUESTION}\n\nMy answer:\n{answer['text']}"
        return self.launched(ANSWER_ID, self.fix_thread, task_questions.PREFIX + 'q1', text)

    def test_a_started_proposal_says_another_thread_proposed_it_and_quotes_the_proposal_as_its(self):
        self.proposal()
        rows = self.listing()
        row = next(row for row in rows['runs'] if row['taskId'] == PROPOSAL_ID)
        self.assertEqual((row['startedBy'], row['asked']), ('proposal', None))
        self.assertEqual(row['origin']['line'], 'Another thread proposed this, and you started it.')
        self.assertEqual(row['said'][0], {'text': PROPOSED, 'source': 'proposal', 'from': 'Proposal from Live board'})
        self.assertEqual([link['text'] for link in row['links'] if link['kind'] in ('proposed', 'home')], ['Proposed by Live board, started by you'])
        self.assertEqual(rows['counts']['home'], 0)
        why = self.record(PROPOSAL_ID)['why']
        self.assertEqual((why['you'], why['home']), (None, None))
        self.assertEqual((why['origin']['kind'], why['origin']['name'], why['origin']['quote']), ('proposal', 'Live board', PROPOSED))
        line = self.trace('utils/days.py', 1)
        self.assertIsNone(line['asked'])
        self.assertEqual((line['origin']['kind'], line['origin']['quote'], line['origin']['quoteFrom']), ('proposal', PROPOSED, 'Proposal from Live board'))

    def test_an_answer_is_your_answer_and_the_question_stays_the_agents(self):
        self.question()
        row = next(row for row in self.listing()['runs'] if row['taskId'] == ANSWER_ID)
        self.assertEqual(row['startedBy'], 'answer')
        self.assertEqual(row['asked'], dict(text='No, always keep the timezone.', at=T0 + 7200, where='answer', picked=False))
        self.assertEqual(row['origin']['line'], 'You answered a question from Timezone fix.')
        self.assertEqual(row['said'][0], {'text': QUESTION, 'source': 'question', 'from': 'Its question'})
        why = self.record(ANSWER_ID)['why']
        self.assertEqual((why['you']['text'], why['home'], why['origin']['quote']), ('No, always keep the timezone.', None, QUESTION))
        line = self.trace('utils/days.py', 1)
        self.assertEqual(line['asked']['text'], 'No, always keep the timezone.')
        self.assertNotIn(QUESTION, line['asked']['text'])
        self.assertEqual(line['origin']['quote'], QUESTION)

    def test_a_picked_option_is_marked_and_a_lost_question_record_is_read_from_what_was_sent(self):
        self.question(dict(kind='option', optionId='o1', text='Keep the timezone'))
        row = next(row for row in self.listing()['runs'] if row['taskId'] == ANSWER_ID)
        self.assertEqual((row['asked']['text'], row['asked']['picked']), ('Keep the timezone', True))
        with self.db:
            self.db.execute('DELETE FROM task_questions')
        row = next(row for row in self.listing()['runs'] if row['taskId'] == ANSWER_ID)
        self.assertEqual((row['asked']['text'], row['said'][0]['text']), ('Keep the timezone', QUESTION))

    def test_an_answer_steered_into_a_running_run_keeps_the_question_apart(self):
        task = self.board()
        task['messages'].append(dict(role='user', text=f'Answer to question q1 from this task:\n{QUESTION}\n\nMy answer:\nNo.',
                                     client_id=task_questions.PREFIX + 'q1', question_id='q1', steer='sent', created_at=T0 + 3700))
        self.save(task)
        self.question(dict(kind='text', text='No.'))
        steers = self.record(BOARD_ID)['why']['steers']
        self.assertEqual(steers, [dict(text='No.', at=T0 + 3700, question=QUESTION, picked=False)])

    def test_a_home_job_with_no_message_on_record_quotes_home_and_never_says_you_asked(self):
        self.launched(PROPOSAL_ID, self.ideas_thread, 'missing-reply-0', 'Build the ideas store.')
        row = next(row for row in self.listing()['runs'] if row['taskId'] == PROPOSAL_ID)
        self.assertEqual((row['startedBy'], row['asked']), ('home', None))
        self.assertEqual(row['origin']['line'], 'Started by Home. Dervo’s records don’t hold the message it came from.')
        self.assertEqual(row['said'][0], {'text': 'Build the ideas store.', 'source': 'home', 'from': 'Home wrote'})


    def test_the_overview_dervo_starts_is_dervos_and_never_shown_as_yours(self):
        """The overview run in a new project's overview thread carries Dervo's own instruction (RC review, 9 Oct)."""
        from project_lifecycle import OVERVIEW_PROMPT
        overview = '7c1d2e3f-0a4b-4c5d-8e6f-1a2b3c4d5e6f'
        self.save(self.base(overview, self.fix_thread, state='answered', read_only=True,
                            runs=[dict(id='r-ov', state='completed', prior_attempts=0, process=dict(start=int((T0 + 1) * 1e6)), told=dict(notes=[], skills=[]))],
                            messages=[dict(role='user', text=OVERVIEW_PROMPT, created_at=T0)]))
        # Negative control: the same words in an ordinary thread are what you typed.
        row = next(row for row in self.listing()['runs'] if row['taskId'] == overview)
        self.assertEqual(row['asked']['text'][:30], OVERVIEW_PROMPT[:30])
        with self.db:
            self.db.execute("INSERT INTO conversation_origins VALUES (?,'overview')", (self.fix_thread,))
        row = next(row for row in self.listing()['runs'] if row['taskId'] == overview)
        self.assertIsNone(row['asked'])
        self.assertEqual(row['origin']['kind'], 'overview')
        self.assertIn('Dervo started this', row['origin']['line'])

class BudgetTests(HistoryFixture):
    """One deadline per request, shared by every git call and the record scan, so a slow repository gets a partial answer
    before the bridge's ten seconds, when it would kill the host and every live run (merge security pass, 23 Sep, F1)."""

    def setUp(self):
        super().setUp()
        self.repository(); self.answer(); self.fix(); self.board()
        self.slow_git = Path(self.tmp.name) / 'slow-git'
        self.slow_git.write_text('#!' + sys.executable + '\nimport os, sys, time\ntime.sleep(1.0)\nos.execv("/usr/bin/git", ["git"] + sys.argv[1:])\n')
        self.slow_git.chmod(0o755)

    def timed(self, *args):
        with patch.object(run_history, 'BUDGET', 1.5), patch.object(task_git, 'GIT', str(self.slow_git)):
            started = time.monotonic()
            answer = self.trace(*args)
            return answer, time.monotonic() - started

    def test_a_slow_git_gives_a_plain_partial_answer_inside_the_deadline(self):
        why, took = self.timed('utils/dates.py', 6)
        self.assertLess(took, 1.5 + 0.4)
        self.assertEqual(why['note'], run_history.SLOW)
        self.assertEqual((why['committed'], why['change'], why['run'], why['nobody']), (False, None, None, None))
        view, took = self.timed('utils/dates.py', 0, 'trace_file')
        self.assertLess(took, 1.5 + 0.4)
        self.assertEqual(view['note'], run_history.SLOW)
        self.assertEqual(view['blocks'][-1]['label'], 'Live board')  # Dervo's own records still label what they can

    def test_the_real_budget_leaves_room_under_the_bridges_ten_seconds(self):
        self.assertLessEqual(run_history.BUDGET + 1.0, 10)
        with patch.object(run_history, 'BUDGET', 0.001):
            answer = self.listing()
        self.assertEqual((answer['runs'], answer['note']), ([], run_history.CUT))

    def test_a_subject_a_thread_name_wrote_cant_point_at_another_task(self):
        body = f'Dervo: Task {ASK_ID} approved in Dervo\n\nTask {FIX_ID} approved in Dervo. Dervo made this commit; the agent ran no git.\n'
        self.assertEqual(run_history._task_of(body), FIX_ID)
        self.assertIsNone(run_history._task_of(f'Dervo: Task {ASK_ID} approved in Dervo'))


class DispatchTests(HistoryFixture):
    def test_the_host_routes_the_reads_and_an_archived_project_refuses_them(self):
        self.fix()
        scope = dict(projectId='p', profileId='cs')
        self.assertEqual(self.host.dispatch('history_list', dict(scope, filter='all', provider='', file=''))['runs'][0]['taskId'], FIX_ID)
        self.assertEqual(self.host.dispatch('run_record', dict(scope, taskId=FIX_ID, runId=''))['name'], 'Timezone fix')
        with self.db:
            self.db.execute('INSERT INTO project_archives VALUES (?,?)', ('p', int(T0)))
        with self.assertRaises(Rejected):
            self.host.dispatch('history_list', dict(scope, filter='all', provider='', file=''))

    def test_fields_are_exact(self):
        with self.assertRaises(Rejected):
            run_history.dispatch(self.host, 'history_list', dict(projectId='p', profileId='cs', filter='all', provider=''), ACTOR)
        with self.assertRaises(Rejected):
            run_history.dispatch(self.host, 'run_record', dict(projectId='p', profileId='cs', taskId=FIX_ID, runId='', extra=1), ACTOR)


if __name__ == '__main__':
    unittest.main()
