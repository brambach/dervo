"""Catch up's Outside Dervo group on a real Host over a disposable store, against synthetic Claude Code and Codex homes
built in a temporary folder from the shapes test_history_import uses. No real home is read, nothing is written under
either home, no provider, no network."""
import json
import os
import sqlite3
import unittest
import unittest.mock
from pathlib import Path

import history_import as hi
import outside_sessions as outside
from dervo_connections import Rejected
from session_continuity import _slug
from test_history_import import Fixture, SID_A, SID_B, SID_C, SID_D, stamp

SID_E = '55555555-5555-4555-8555-555555555555'
SID_F = '66666666-6666-4666-8666-666666666666'
CODEX_A = '01a06e9e-9c4c-7020-9378-dbd3052f3645'
CODEX_B = '01a06e9e-9c4c-7020-9378-dbd3052f3646'
CODEX_C = '01a06e9e-9c4c-7020-9378-dbd3052f3647'
KEY = 'sk-ant-api03-' + 'Zx7Qp2Lm9Rt4Vb8Nc1Hj6Kd3Fs5Gw0Ye' * 2


class Clock:
    def __init__(self, step=0.0):
        self.at, self.step = 1000.0, step

    def __call__(self):
        self.at += self.step
        return self.at


class OutsideFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.cwd = self.cwd.resolve()
        from host import Host
        self.host = Host(self.root / 'store')
        self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'claude', self.root / 'account', 'you@example.test', 'personal')
        self.project = self.host.add_picked_project(str(self.cwd), 'account', 'claude-opus-5')['id']
        self.clock = Clock()
        self.reader = outside.Reader(self.claude, self.codex, clock=self.clock, now=lambda: self.T0 + 3600)
        self.host.outside_sessions = self.reader

    # ------------------------------------------------------------ building transcripts
    def ask(self, text, at, **extra):
        return dict(type='user', cwd=str(self.cwd), message=dict(role='user', content=text), timestamp=stamp(at), **extra)

    def say(self, at, text='Done.', **extra):
        return dict(type='assistant', cwd=str(self.cwd), message=dict(role='assistant', content=[dict(type='text', text=text)]), timestamp=stamp(at), **extra)

    def use(self, name, args, at, call='toolu_1'):
        return dict(type='assistant', cwd=str(self.cwd), timestamp=stamp(at),
                    message=dict(role='assistant', content=[dict(type='tool_use', id=call, name=name, input=args)]))

    def result(self, at, call='toolu_1', content='ok', error=False):
        return dict(type='user', cwd=str(self.cwd), timestamp=stamp(at),
                    message=dict(role='user', content=[dict(type='tool_result', tool_use_id=call, content=content, is_error=error)]))

    def api_error(self, text, at):
        return dict(type='assistant', cwd=str(self.cwd), timestamp=stamp(at), isApiErrorMessage=True,
                    message=dict(role='assistant', model='<synthetic>', content=[dict(type='text', text=text)]))

    def slug_dir(self, cwd=None):
        return self.claude / 'projects' / _slug(os.path.realpath(cwd or self.cwd))

    def write(self, sid, records, folder=None, tail=''):
        folder = folder or self.slug_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'{sid}.jsonl'
        with open(path, 'w') as handle:
            for record in records:
                handle.write((record if isinstance(record, str) else json.dumps(record)) + '\n')
            handle.write(tail)
        os.utime(path, (self.T0 + 60, self.T0 + 60))
        return path

    def rollout(self, thread_id, lines, source='cli', cwd=None, archived=0, updated=None, first=None, where=None):
        state = self.codex / hi.CODEX_STATE
        db = sqlite3.connect(state)
        db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER, '
                   'updated_at INTEGER, source TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL, '
                   'name TEXT, first_user_message TEXT)')
        path = (where or self.codex / 'sessions') / f'rollout-{thread_id}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as handle:
            handle.write(json.dumps(dict(timestamp=stamp(self.T0), type='session_meta', payload=dict(cwd=str(cwd or self.cwd)))) + '\n')
            for line in lines:
                handle.write(json.dumps(line) + '\n')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?)', (thread_id, str(path), self.T0, updated or self.T0 + 100, source,
                                                                        str(cwd or self.cwd), 'Codex title', archived, None, first))
        db.commit()
        db.close()
        return path

    @staticmethod
    def event(kind, at, **payload):
        return dict(timestamp=stamp(at), type='event_msg', payload=dict(type=kind, **payload))

    @staticmethod
    def item(kind, at, **payload):
        return dict(timestamp=stamp(at), type='response_item', payload=dict(type=kind, **payload))

    # ------------------------------------------------------------ asking
    def turn_on(self):
        return outside.dispatch(self.host, 'outside_sessions_consent_set', {'projectId': self.project, 'enabled': True})

    def sessions(self):
        return outside.dispatch(self.host, 'outside_sessions', {'projectId': self.project})

    def rows(self):
        return {row['id']: row for row in self.sessions()['sessions']}


class ConsentTests(OutsideFixture):
    def test_off_by_default_and_nothing_is_read_until_it_is_on(self):
        self.write(SID_A, [self.ask('Fix the login page', self.T0), self.say(self.T0 + 1)])
        with unittest.mock.patch.object(outside.Reader, 'read', side_effect=AssertionError('read before consent')):
            answer = self.sessions()
        self.assertEqual((answer['enabled'], answer['asked'], answer['sessions']), (False, False, []))
        self.assertEqual(outside.dispatch(self.host, 'outside_sessions_consent', {'projectId': self.project}), dict(enabled=False, asked=False))

    def test_not_now_is_remembered_and_still_reads_nothing(self):
        self.write(SID_A, [self.ask('Fix the login page', self.T0), self.say(self.T0 + 1)])
        said = outside.dispatch(self.host, 'outside_sessions_consent_set', {'projectId': self.project, 'enabled': False})
        self.assertEqual(said, dict(enabled=False, asked=True))
        with unittest.mock.patch.object(outside.Reader, 'read', side_effect=AssertionError('read after Not now')):
            self.assertEqual(self.sessions()['sessions'], [])

    def test_turn_on_reads_and_turning_it_off_stops_reading(self):
        self.write(SID_A, [self.ask('Fix the login page', self.T0), self.say(self.T0 + 1)])
        self.assertEqual(self.turn_on(), dict(enabled=True, asked=True))
        self.assertEqual([row['id'] for row in self.sessions()['sessions']], [f'claude:{SID_A}'])
        outside.dispatch(self.host, 'outside_sessions_consent_set', {'projectId': self.project, 'enabled': False})
        self.assertEqual(self.sessions()['sessions'], [])

    def test_consent_is_per_project(self):
        other = self.root / 'other'
        other.mkdir()
        second = self.host.add_picked_project(str(other.resolve()), 'account', 'claude-opus-5')['id']
        self.turn_on()
        self.assertEqual(outside.dispatch(self.host, 'outside_sessions_consent', {'projectId': second}), dict(enabled=False, asked=False))

    def test_fields_values_and_projects_are_exact(self):
        for args in ({'projectId': self.project, 'enabled': 'yes'}, {'projectId': self.project, 'enabled': 1}):
            with self.assertRaises(Rejected):
                outside.dispatch(self.host, 'outside_sessions_consent_set', args)
        with self.assertRaises(Rejected):
            outside.dispatch(self.host, 'outside_sessions', {'projectId': self.project, 'extra': 1})
        with self.assertRaises(Rejected):
            outside.dispatch(self.host, 'outside_sessions', {'projectId': 'nope'})
        with self.assertRaises(Rejected):
            outside.dispatch(self.host, 'outside_sessions_consent_set', {'projectId': self.project})

    def test_deleting_the_project_removes_its_answer(self):
        self.turn_on()
        from project_lifecycle import delete_rows
        with self.host.store.db:
            delete_rows(self.host.store.db, self.project)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM project_outside_sessions').fetchone()[0], 0)


class MatchTests(OutsideFixture):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_the_slug_is_shared_so_only_sessions_whose_cwd_is_this_folder_count(self):
        base = self.root / 'x'
        folders = [base / 'dd.app', base / 'dd-app', base / 'dd_app']
        for folder in folders:
            folder.mkdir(parents=True)
        self.assertEqual(len({_slug(os.path.realpath(f)) for f in folders}), 1, 'the three folders share one slug')
        project = self.host.add_picked_project(str(folders[1].resolve()), 'account', 'claude-opus-5')['id']
        outside.dispatch(self.host, 'outside_sessions_consent_set', {'projectId': project, 'enabled': True})
        shared = self.slug_dir(folders[0])
        for sid, folder in ((SID_A, folders[0]), (SID_B, folders[1]), (SID_C, folders[2])):
            self.write(sid, [dict(self.ask(f'Work in {folder.name}', self.T0), cwd=str(folder)), dict(self.say(self.T0 + 1), cwd=str(folder))], shared)
        self.write(SID_D, [{k: v for k, v in self.ask('No folder recorded', self.T0).items() if k != 'cwd'}, self.say(self.T0 + 1)], shared)
        answer = outside.dispatch(self.host, 'outside_sessions', {'projectId': project})
        self.assertEqual([(r['id'], r['title']) for r in answer['sessions']], [(f'claude:{SID_B}', 'Work in dd-app')])

    def test_a_cwd_that_resolves_to_the_folder_counts(self):
        link = self.root / 'link'
        link.symlink_to(self.cwd)
        self.write(SID_A, [dict(self.ask('Through a link', self.T0), cwd=str(link)), self.say(self.T0 + 1)])
        self.assertIn(f'claude:{SID_A}', self.rows())

    def test_sessions_dervo_started_or_already_holds_are_left_out(self):
        for sid in (SID_A, SID_B, SID_C, SID_D, SID_E):
            self.write(sid, [self.ask(f'Session {sid[:4]}', self.T0), self.say(self.T0 + 1)])
        db = self.host.store.db
        project = db.execute('SELECT * FROM projects WHERE id=?', (self.project,)).fetchone()
        with db:
            self.host.new_conversation(project, 'Imported', session_id=SID_A)
        db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(dict(id='t1', session_id=SID_B, runs=[]))))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t2', json.dumps(dict(id='t2', session_id=None, runs=[dict(session_id=SID_C)]))))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t3', 'not json'))
        db.commit()
        self.assertEqual(set(self.rows()), {f'claude:{SID_D}', f'claude:{SID_E}'})

    def test_the_task_store_is_scanned_in_python_when_its_json_functions_fail(self):
        db = self.host.store.db
        db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(dict(runs=[dict(session_id=SID_B)]))))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t2', '{"session_id": "' + SID_C + '", broken'))
        db.commit()
        broken = unittest.mock.MagicMock(wraps=db)
        broken.execute.side_effect = lambda sql, *a: (_ for _ in ()).throw(sqlite3.DatabaseError('json off')) if 'json_' in sql else db.execute(sql, *a)
        self.assertEqual(outside._task_sessions(broken, [SID_A, SID_B, SID_C]), {SID_B})

    def test_codex_threads_another_tool_drove_archived_copied_or_elsewhere_are_left_out(self):
        done = [self.event('user_message', self.T0 + 1, message='Add the frosted picker'), self.event('agent_message', self.T0 + 2, message='Done.'),
                self.event('task_complete', self.T0 + 3)]
        self.rollout(CODEX_A, done, source='vscode')
        self.rollout(CODEX_B, done, source='exec')
        self.rollout(CODEX_C, done, source='cli', archived=1)
        self.rollout('01a06e9e-0000-7020-9378-000000000001', done, source='{"subagent": {"parent": "x"}}')
        self.rollout('01a06e9e-0000-7020-9378-000000000002', done, source='mcp')
        self.rollout('01a06e9e-0000-7020-9378-000000000003', done, cwd=self.root)
        outside_folder = self.root / 'elsewhere'
        self.rollout('01a06e9e-0000-7020-9378-000000000004', done, where=outside_folder)
        self.rollout('01a06e9e-0000-7020-9378-000000000005', done)
        (self.codex / 'external_agent_session_imports.json').write_text(json.dumps(dict(records=[
            dict(source_path=f'/somewhere/{SID_F}.jsonl', imported_thread_id='01a06e9e-0000-7020-9378-000000000005')])))
        self.assertEqual(set(self.rows()), {f'codex:{CODEX_A}'})

    def test_old_sessions_stay_out_and_the_answer_is_capped_newest_first(self):
        self.write(SID_A, [self.ask('Long ago', self.T0 - 40 * 86400), self.say(self.T0 - 40 * 86400)])
        os.utime(self.slug_dir() / f'{SID_A}.jsonl', (self.T0 - 40 * 86400,) * 2)
        with unittest.mock.patch.object(outside, 'SHOWN', 2):
            for n, sid in enumerate((SID_B, SID_C, SID_D)):
                path = self.write(sid, [self.ask(f'Recent {n}', self.T0 + n), self.say(self.T0 + n)])
                os.utime(path, (self.T0 + n,) * 2)
            self.assertEqual([r['title'] for r in self.sessions()['sessions']], ['Recent 2', 'Recent 1'])

    def test_a_forked_session_is_one_row_the_newest(self):
        opener = self.ask('Rename the archive button', self.T0)
        self.write(SID_A, [opener, self.say(self.T0 + 1)])
        self.write(SID_B, [opener, self.say(self.T0 + 1), self.ask('Also the tooltip', self.T0 + 50), self.say(self.T0 + 51)])
        rows = self.sessions()['sessions']
        self.assertEqual([(r['id'], r['asked']) for r in rows], [(f'claude:{SID_B}', 'Also the tooltip')])


class StateTests(OutsideFixture):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def one(self, records, **kw):
        self.write(SID_A, records, **kw)
        [row] = self.sessions()['sessions']
        return row

    def test_a_turn_that_ended_is_finished_with_the_last_ask_its_time_and_a_clipped_title(self):
        long_ask = 'Fix the sidebar scroll position when a thread is archived and make sure the selection stays where you left it'
        row = self.one([self.ask(long_ask, self.T0), self.say(self.T0 + 5), self.ask('Now the tests', self.T0 + 60), self.say(self.T0 + 90)])
        self.assertEqual((row['state'], row['asked'], row['asked_at'], row['last_at']), ('finished', 'Now the tests', self.T0 + 60, self.T0 + 90))
        self.assertLessEqual(len(row['title']), outside.TITLE_LIMIT + 1)
        self.assertTrue(row['title'].startswith('Fix the sidebar scroll') and row['title'].endswith('…'))
        self.assertEqual(row['provider'], 'claude')

    def test_a_usage_limit_is_its_own_state(self):
        for text in ('Claude AI usage limit reached|1800003600', '5-hour limit reached ∙ resets 3pm', 'You’ve hit your limit · resets 3am'):
            self.reader.files.clear()
            row = self.one([self.ask('Keep going', self.T0), self.use('Edit', dict(file_path=str(self.cwd / 'app.py')), self.T0 + 1),
                            self.result(self.T0 + 2), self.api_error(text, self.T0 + 3)])
            self.assertEqual(row['state'], 'limit', text)
            self.assertEqual(row['step'], dict(verb='Edited', target='app.py'))

    def test_an_api_error_is_an_error_with_its_words(self):
        row = self.one([self.ask('Keep going', self.T0), self.api_error('API Error: 529 Overloaded', self.T0 + 3)])
        self.assertEqual((row['state'], row['detail']), ('error', 'API Error: 529 Overloaded'))

    def test_a_call_that_asks_with_no_result_is_waiting_on_approval(self):
        row = self.one([self.ask('Run the tests', self.T0), self.use('Bash', dict(command='npm test\n# then more'), self.T0 + 1)])
        self.assertEqual((row['state'], row['step']), ('approval', dict(verb='Ran', target='npm test # then more')))

    def test_a_question_with_no_answer_is_a_question(self):
        row = self.one([self.ask('Plan the picker', self.T0),
                        self.use('AskUserQuestion', dict(questions=[dict(question='Per project or per thread?', options=[])]), self.T0 + 1)])
        self.assertEqual((row['state'], row['detail']), ('question', 'Per project or per thread?'))

    def test_you_stopping_it_is_stopped(self):
        interrupted = self.one([self.ask('Refactor it', self.T0), self.say(self.T0 + 1),
                                self.ask('[Request interrupted by user]', self.T0 + 2)])
        self.assertEqual((interrupted['state'], interrupted['asked']), ('stopped', 'Refactor it'))
        self.reader.files.clear()
        refused = self.one([self.ask('Refactor it', self.T0), self.use('Write', dict(file_path=str(self.cwd / 'a.py')), self.T0 + 1),
                            self.result(self.T0 + 2, content="The user doesn't want to proceed with this tool use.", error=True)])
        self.assertEqual(refused['state'], 'stopped')

    def test_what_the_records_dont_say_is_unknown(self):
        cases = [
            [self.ask('Anything', self.T0)],  # your message, no reply
            [self.ask('Anything', self.T0), self.use('Read', dict(file_path=str(self.cwd / 'x.py')), self.T0 + 1)],  # a read with no result
            [self.ask('Anything', self.T0), self.use('Bash', dict(command='ls'), self.T0 + 1), self.result(self.T0 + 2)],  # mid-task
        ]
        for records in cases:
            self.reader.files.clear()
            self.assertEqual(self.one(records)['state'], 'unknown')

    def test_the_last_step_and_the_files_it_touched(self):
        home_file = str(Path.home() / 'notes' / 'todo.md')
        row = self.one([self.ask('Tidy', self.T0),
                        self.use('Edit', dict(file_path=str(self.cwd / 'src' / 'a.ts')), self.T0 + 1, 'c1'), self.result(self.T0 + 2, 'c1'),
                        self.use('Write', dict(file_path=home_file), self.T0 + 3, 'c2'), self.result(self.T0 + 4, 'c2'),
                        self.use('Edit', dict(file_path=str(self.cwd / 'src' / 'a.ts')), self.T0 + 5, 'c3'), self.result(self.T0 + 6, 'c3'),
                        self.use('Grep', dict(pattern='TODO'), self.T0 + 7, 'c4'), self.result(self.T0 + 8, 'c4'), self.say(self.T0 + 9)])
        self.assertEqual(row['step'], dict(verb='Searched for', target='TODO'))
        self.assertEqual(row['files'], ['src/a.ts', '~/notes/todo.md'])

    def test_sidechains_are_the_helpers_work_not_the_session(self):
        row = self.one([self.ask('Look around', self.T0), self.say(self.T0 + 1),
                        dict(self.use('Bash', dict(command='rm -rf build'), self.T0 + 2), isSidechain=True)])
        self.assertEqual((row['state'], row['step']), ('finished', None))

    def test_codex_finished_stopped_limited_and_waiting(self):
        ask = self.event('user_message', self.T0 + 1, message='Wire the picker')
        cases = {
            'finished': [ask, self.event('agent_message', self.T0 + 2, message='Done.'), self.event('task_complete', self.T0 + 3)],
            'stopped': [ask, self.event('turn_aborted', self.T0 + 2, reason='interrupted')],
            'limit': [ask, self.event('error', self.T0 + 2, message='You’ve hit your usage limit. Try again at 3:00 PM.')],
            'error': [ask, self.event('error', self.T0 + 2, message='stream disconnected before completion')],
            'approval': [ask, self.item('function_call', self.T0 + 2, name='shell', call_id='c1',
                                        arguments=json.dumps(dict(command=['bash', '-lc', 'pnpm build'])))],
            'unknown': [ask, self.item('function_call', self.T0 + 2, name='shell', call_id='c1', arguments='{}'),
                        self.item('function_call_output', self.T0 + 3, call_id='c1', output='ok')],
        }
        for n, (state, lines) in enumerate(cases.items()):
            thread = f'01a06e9e-1111-7020-9378-00000000000{n}'
            self.rollout(thread, lines, updated=self.T0 + 100 + n)
        rows = self.rows()
        for n, state in enumerate(cases):
            row = rows[f'codex:01a06e9e-1111-7020-9378-00000000000{n}']
            self.assertEqual((row['state'], row['asked'], row['provider']), (state, 'Wire the picker', 'codex'), state)
        self.assertEqual(rows['codex:01a06e9e-1111-7020-9378-000000000004']['step'], dict(verb='Ran', target='pnpm build'))

    def test_codex_error_info_says_limit_even_when_the_words_dont(self):
        self.rollout(CODEX_A, [self.event('user_message', self.T0 + 1, message='Go'),
                               self.event('error', self.T0 + 2, message='Something went wrong', codex_error_info='usage_limit_exceeded')])
        self.assertEqual(self.rows()[f'codex:{CODEX_A}']['state'], 'limit')

    def test_codex_patches_and_the_paginated_layout(self):
        patch = '*** Begin Patch\n*** Update File: src/picker.ts\n@@\n-a\n+b\n*** Add File: src/picker.css\n+x\n*** End Patch'
        self.rollout(CODEX_A, [
            self.item('message', self.T0 + 1, role='user', content=[dict(type='input_text', text='<environment_context>x</environment_context>')]),
            self.item('message', self.T0 + 1, role='user', content=[dict(type='input_text', text='Frost the picker')]),
            self.item('custom_tool_call', self.T0 + 2, name='apply_patch', call_id='p1', input=patch),
            self.item('custom_tool_call_output', self.T0 + 3, call_id='p1', output='Done'),
            self.item('message', self.T0 + 4, role='assistant', content=[dict(type='output_text', text='Frosted.')]),
        ], first=None)
        row = self.rows()[f'codex:{CODEX_A}']
        self.assertEqual((row['state'], row['title'], row['asked']), ('finished', 'Frost the picker', 'Frost the picker'))
        self.assertEqual(row['step'], dict(verb='Wrote', target='src/picker.css'))
        self.assertEqual(row['files'], ['src/picker.css', 'src/picker.ts'])

    def test_codex_takes_its_title_from_the_thread_list_when_it_has_the_first_message(self):
        self.rollout(CODEX_A, [self.event('user_message', self.T0 + 1, message='Later ask'), self.event('task_complete', self.T0 + 2)],
                     first='The first thing typed')
        self.assertEqual(self.rows()[f'codex:{CODEX_A}']['title'], 'The first thing typed')


class DefensiveTests(OutsideFixture):
    def setUp(self):
        super().setUp()
        self.turn_on()

    def test_lines_that_dont_parse_are_skipped(self):
        self.write(SID_A, [self.ask('Fix it', self.T0), '{not json', '', self.say(self.T0 + 1)], tail='{"type": "user", "message": {"con')
        [row] = self.sessions()['sessions']
        self.assertEqual(row['state'], 'finished')

    def test_a_file_that_cant_be_read_is_counted_and_the_rest_still_show(self):
        self.write(SID_A, [self.ask('Readable', self.T0), self.say(self.T0 + 1)])
        locked = self.write(SID_B, [self.ask('Locked', self.T0), self.say(self.T0 + 1)])
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o600)
        answer = self.sessions()
        self.assertEqual(([r['title'] for r in answer['sessions']], answer['unreadable']), (['Readable'], 1))

    def test_a_codex_thread_list_that_cant_be_opened_is_counted(self):
        self.write(SID_A, [self.ask('Readable', self.T0), self.say(self.T0 + 1)])
        (self.codex / hi.CODEX_STATE).write_bytes(b'not a database at all' * 100)
        answer = self.sessions()
        self.assertEqual(([r['title'] for r in answer['sessions']], answer['unreadable']), (['Readable'], 1))

    def test_only_the_head_and_the_tail_of_a_big_file_are_read(self):
        filler = [self.say(self.T0 + 2, 'x' * 2000) for _ in range(1500)]  # about 3 MB
        self.write(SID_A, [self.ask('First ask', self.T0)] + filler + [self.ask('Last ask', self.T0 + 10), self.say(self.T0 + 11)])
        size = (self.slug_dir() / f'{SID_A}.jsonl').stat().st_size
        self.assertGreater(size, 3 * outside.TAIL_STEPS[-1] / 2)
        with unittest.mock.patch.object(outside, '_window', wraps=outside._window) as window:
            [row] = self.sessions()['sessions']
        self.assertEqual((row['title'], row['asked'], row['state']), ('First ask', 'Last ask', 'finished'))
        self.assertLessEqual(sum(call.args[2] for call in window.call_args_list), outside.HEAD_BYTES + outside.TAIL_STEPS[0])

    def test_a_tail_with_no_ask_is_read_once_more_further_back_then_left_at_that(self):
        filler = [self.say(self.T0 + 2, 'x' * 2000) for _ in range(1500)]
        self.write(SID_A, [self.ask('First ask', self.T0)] + filler)
        with unittest.mock.patch.object(outside, '_window', wraps=outside._window) as window:
            [row] = self.sessions()['sessions']
        self.assertEqual((row['title'], row['asked'], row['asked_at']), ('First ask', None, None))
        self.assertEqual([call.args[2] for call in window.call_args_list][1:], list(outside.TAIL_STEPS))

    def test_an_unchanged_file_isnt_read_again_and_a_grown_one_rereads_only_its_tail(self):
        path = self.write(SID_A, [self.ask('Fix it', self.T0), self.say(self.T0 + 1)])
        self.sessions()
        with unittest.mock.patch.object(outside, '_window', wraps=outside._window) as window:
            self.sessions()
        self.assertEqual(window.call_count, 0)
        with open(path, 'a') as handle:
            handle.write(json.dumps(self.ask('And the tests', self.T0 + 30)) + '\n')
        os.utime(path, (self.T0 + 90, self.T0 + 90))
        with unittest.mock.patch.object(outside, '_window', wraps=outside._window) as window:
            [row] = self.sessions()['sessions']
        self.assertEqual(window.call_count, 1, 'the head is kept')
        self.assertEqual((row['asked'], row['state']), ('And the tests', 'unknown'))

    def test_a_refresh_that_runs_out_of_time_says_partial_and_the_next_one_finishes(self):
        for n, sid in enumerate((SID_A, SID_B, SID_C)):
            path = self.write(sid, [self.ask(f'Ask {n}', self.T0), self.say(self.T0 + 1)])
            os.utime(path, (self.T0 + n,) * 2)
        self.clock.step = outside.TIME_BUDGET / 2.5
        first = self.sessions()
        self.assertTrue(first['partial'])
        self.assertLess(len(first['sessions']), 3)
        self.clock.step = 0
        second = self.sessions()
        self.assertEqual((second['partial'], len(second['sessions'])), (False, 3))

    def test_secrets_are_redacted_before_anything_leaves(self):
        row_records = [self.ask(f'Use {KEY} for the deploy', self.T0),
                       self.use('Bash', dict(command=f'curl -H "x-api-key: {KEY}" https://api.example.test'), self.T0 + 1)]
        self.write(SID_A, row_records)
        text = json.dumps(self.sessions())
        self.assertNotIn(KEY, text)
        self.assertIn('[redacted]', text)

    def test_nothing_is_written_under_either_home(self):
        self.write(SID_A, [self.ask('Fix it', self.T0), self.say(self.T0 + 1)])
        self.rollout(CODEX_A, [self.event('user_message', self.T0 + 1, message='Go'), self.event('task_complete', self.T0 + 2)])
        before = self.snapshot()
        self.sessions()
        self.sessions()
        self.assertEqual(before, self.snapshot())

    def test_the_real_homes_are_never_the_default_in_these_tests(self):
        self.assertNotEqual(self.reader.claude_home, hi.CLAUDE_HOME)
        self.assertNotEqual(self.reader.codex_home, hi.CODEX_HOME)


class HostTests(OutsideFixture):
    def test_the_host_routes_the_three_operations(self):
        self.write(SID_A, [self.ask('Fix the login page', self.T0), self.say(self.T0 + 1)])
        self.assertEqual(self.host.dispatch('outside_sessions', {'projectId': self.project})['sessions'], [])
        self.assertEqual(self.host.dispatch('outside_sessions_consent_set', {'projectId': self.project, 'enabled': True}), dict(enabled=True, asked=True))
        self.assertEqual(self.host.dispatch('outside_sessions_consent', {'projectId': self.project}), dict(enabled=True, asked=True))
        self.assertEqual([r['title'] for r in self.host.dispatch('outside_sessions', {'projectId': self.project})['sessions']], ['Fix the login page'])
        stored = self.host.store.db.execute('SELECT document FROM project_outside_sessions').fetchone()[0]
        self.assertEqual(json.loads(stored)['actor'], 'local-user')
        self.assertNotIn('Fix the login page', stored, 'the consent row holds the answer, never session text')


if __name__ == '__main__':
    unittest.main()
