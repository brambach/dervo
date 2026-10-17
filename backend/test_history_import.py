"""The history reader and the import plan, on synthetic transcripts. No real home is read: every test builds its own
Claude Code and Codex layout in a temporary directory. No provider, no network."""
import json
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import history_import as hi
from session_continuity import _slug

SID_A = '11111111-1111-4111-8111-111111111111'
SID_B = '22222222-2222-4222-8222-222222222222'
SID_C = '33333333-3333-4333-8333-333333333333'
SID_D = '44444444-4444-4444-8444-444444444444'
DAY = 86400


def stamp(seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace('+00:00', 'Z')


def user(text, at, **extra):
    return dict(type='user', message=dict(role='user', content=text), timestamp=stamp(at), **extra)


def assistant(at):
    return dict(type='assistant', message=dict(role='assistant', content=[dict(type='text', text='On it.')]), timestamp=stamp(at))


def tool_result(at):
    return dict(type='user', message=dict(role='user', content=[dict(type='tool_result', tool_use_id='x', content='ok')]), timestamp=stamp(at))


class Fixture(unittest.TestCase):
    T0 = 1_800_000_000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cwd = self.root / 'work'
        self.cwd.mkdir()
        self.claude = self.root / 'claude'
        self.codex = self.root / 'codex'
        (self.claude / 'projects' / _slug(os.path.realpath(self.cwd))).mkdir(parents=True)
        self.codex.mkdir()

    def transcript(self, sid, records, beside=0):
        """Claude Code writes the folder it runs in as ``cwd`` on every message record, so these do too, unless a record
        sets its own (``cwd=None`` for one that names no folder)."""
        folder = self.claude / 'projects' / _slug(os.path.realpath(self.cwd))
        with open(folder / f'{sid}.jsonl', 'w') as handle:
            for record in records:
                if record.get('type') in ('user', 'assistant') and 'cwd' not in record:
                    record = dict(record, cwd=str(self.cwd))
                handle.write(json.dumps(record) + '\n')
        for n in range(beside):
            (folder / sid).mkdir(exist_ok=True)
            (folder / sid / f'agent-{n}.jsonl').write_text('{}\n')

    def codex_thread(self, thread_id, created, updated, steps, archived=0, source='vscode', legacy=True, name=None):
        state = self.codex / hi.CODEX_STATE
        db = sqlite3.connect(state)
        db.execute('CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER, '
                   'updated_at INTEGER, source TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL, name TEXT)')
        rollout = self.codex / f'rollout-{thread_id}.jsonl'
        with open(rollout, 'w') as handle:
            handle.write(json.dumps(dict(timestamp=stamp(created), type='session_meta', payload=dict(cwd=str(self.cwd)))) + '\n')
            for who, text, at in steps:
                if legacy:
                    kind = 'user_message' if who == 'you' else 'agent_message'
                    handle.write(json.dumps(dict(timestamp=stamp(at), type='event_msg', payload=dict(type=kind, message=text))) + '\n')
                else:
                    part = dict(type='input_text' if who == 'you' else 'output_text', text=text)
                    handle.write(json.dumps(dict(timestamp=stamp(at), type='response_item',
                                                 payload=dict(type='message', role='user' if who == 'you' else 'assistant', content=[part]))) + '\n')
                if who != 'you':
                    handle.write(json.dumps(dict(timestamp=stamp(at), type='event_msg', payload=dict(type='task_complete'))) + '\n')
        db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)',
                   (thread_id, str(rollout), created, updated, source, str(self.cwd), 'Codex title', archived, name))
        db.commit()
        db.close()

    def snapshot(self):
        return {str(p): p.stat().st_mtime_ns for p in list(self.claude.rglob('*')) + list(self.codex.rglob('*'))}


class ReaderTests(Fixture):
    def test_the_project_folder_is_the_one_session_continuity_writes_into(self):
        self.assertEqual(hi.claude_project_dir(self.cwd, self.claude),
                         self.claude / 'projects' / _slug(os.path.realpath(self.cwd)))

    def test_the_first_message_is_the_first_thing_you_typed(self):
        t = self.T0
        self.transcript(SID_A, [
            dict(type='queue-operation', operation='enqueue'),
            user('<command-name>/clear</command-name>', t),
            user('caveat: the messages below were generated', t + 1, isMeta=True),
            user('Fix the sidebar scroll position', t + 2),
            assistant(t + 3),
            tool_result(t + 4),
            assistant(t + 5),
            user('thanks, that is all', t + 6),
            assistant(t + 7),
            dict(type='custom-title', customTitle='Sidebar scroll'),
        ], beside=2)
        [session] = hi.read_claude(self.cwd, self.claude)
        self.assertEqual(session.first_text, 'Fix the sidebar scroll position')
        self.assertEqual((session.turns, session.replies, session.answered), (2, 3, True))
        self.assertEqual(session.last_text, 'thanks, that is all')
        self.assertEqual((session.first_at, session.last_at), (t + 2, t + 7))
        self.assertEqual((session.title, session.named_by, session.custom_title), ('Sidebar scroll', 'custom-title', 'Sidebar scroll'))
        self.assertEqual(session.extras, 2, 'subagent transcripts beside the session are counted, not read')

    def test_the_ai_title_wins_over_the_custom_title_and_the_last_of_each_counts(self):
        t = self.T0
        self.transcript(SID_A, [dict(type='custom-title', customTitle='First custom'), user('Fix the login page', t), assistant(t + 1),
                                dict(type='ai-title', aiTitle='Login page repair'), dict(type='custom-title', customTitle='Second custom'),
                                dict(type='ai-title', aiTitle='Login repair')])
        [session] = hi.read_claude(self.cwd, self.claude)
        self.assertEqual((session.title, session.named_by, session.custom_title), ('Login repair', 'ai-title', 'Second custom'))

    def test_a_title_that_is_the_message_itself_is_not_kept(self):
        self.transcript(SID_A, [dict(type='custom-title', customTitle='Line one\nline two'), user('Fix the login page', self.T0), assistant(self.T0 + 1),
                                dict(type='ai-title', aiTitle='x' * (hi.TITLE_LIMIT + 1))])
        [session] = hi.read_claude(self.cwd, self.claude)
        self.assertIsNone(session.title)
        self.assertEqual(hi.clean_title('  Login page  '), 'Login page')

    def test_a_system_reminder_inside_a_message_is_not_what_you_typed(self):
        self.transcript(SID_A, [user('<system-reminder>ignore</system-reminder>Rename the archive button', self.T0), assistant(self.T0 + 1)])
        [session] = hi.read_claude(self.cwd, self.claude)
        self.assertEqual(session.first_text, 'Rename the archive button')

    def test_sidechains_and_files_that_are_not_sessions_stay_out(self):
        self.transcript(SID_A, [user('Sidechain only', self.T0, isSidechain=True), assistant(self.T0 + 1)])
        folder = self.claude / 'projects' / _slug(os.path.realpath(self.cwd))
        (folder / 'notes.jsonl').write_text(json.dumps(user('Not a session', self.T0)) + '\n')
        sessions = hi.read_claude(self.cwd, self.claude)
        self.assertEqual([s.id for s in sessions], [SID_A])
        self.assertIsNone(sessions[0].first_text)

    def test_another_folders_transcripts_under_the_same_name_stay_out(self):
        """Security review L3, 23 Sep: Claude Code names its folder of transcripts by turning every character but letters
        and digits into a hyphen, so dd-app, dd.app and dd_app share one. A session counts only when its own records say
        it ran in this folder. One whose records name no folder at all is left out too: nothing says it's this one's."""
        t = self.T0
        self.cwd, other, link = self.root / 'dd-app', self.root / 'dd.app', self.root / 'dd-link'
        self.cwd.mkdir()
        other.mkdir()
        link.symlink_to(self.cwd)
        self.assertEqual(hi.claude_project_dir(self.cwd, self.claude), hi.claude_project_dir(other, self.claude))
        hi.claude_project_dir(self.cwd, self.claude).mkdir()
        self.transcript(SID_A, [user('Fix the login page', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Rotate the dd.app signing key', t + 10, cwd=str(other)), assistant(t + 11)])
        self.transcript(SID_C, [user('Nobody knows where this ran', t + 20, cwd=None), dict(assistant(t + 21), cwd=None)])
        self.transcript(SID_D, [user('Tidy the sidebar', t + 30, cwd=str(link)), assistant(t + 31)])
        self.assertEqual([s.id for s in hi.read_claude(self.cwd, self.claude)], [SID_A, SID_D], 'the same folder, even through a link')
        self.assertEqual([s.id for s in hi.read_claude(other, self.claude)], [SID_B])
        self.assertEqual([s.id for s in hi.read_folder(self.cwd, self.claude, self.codex)[0]], [SID_A, SID_D])
        self.assertEqual(hi.look_claude(self.cwd, self.claude)[:2], (2, 2), 'the look leaves out the same sessions')
        self.assertEqual(hi.look_claude(other, self.claude)[:2], (1, 1))

    def test_a_folder_with_no_history_reads_as_nothing(self):
        other = self.root / 'elsewhere'
        other.mkdir()
        self.assertEqual(hi.read_claude(other, self.claude), [])
        self.assertEqual(hi.read_codex(other, self.codex), [])
        self.assertEqual(hi.read_codex(other, self.root / 'no-codex'), [])

    def test_codex_threads_come_from_the_state_database_and_their_rollouts(self):
        t = self.T0
        self.codex_thread('c1', t, t + 10, [('you', 'Add a connector picker', t), ('agent', 'Done.', t + 10)])
        self.codex_thread('c2', t + 20, t + 30, [('you', '<environment_context>x</environment_context>', t + 20),
                                                  ('you', 'Tighten the composer spacing', t + 21), ('agent', 'Sure.', t + 30)], legacy=False, archived=1)
        self.codex_thread('c3', t + 40, t + 41, [('you', 'Spawned', t + 40)], source='{"subagent":{"thread_spawn":{}}}')
        sessions = hi.read_codex(self.cwd, self.codex)
        self.assertEqual([s.id for s in sessions], ['c1', 'c2'], 'a subagent spawn is its parent thread\'s work')
        first, second = sessions
        self.assertEqual((first.first_text, first.turns, first.replies, first.answered, first.archived), ('Add a connector picker', 1, 1, True, False))
        self.assertEqual((second.first_text, second.turns, second.archived), ('Tighten the composer spacing', 1, True))
        self.assertIsNone(second.title, "Codex's title column is the first message when nothing shorter was set, so it names nothing")
        self.assertEqual(second.last_at, t + 30)
        self.codex_thread('c4', t + 50, t + 51, [('you', 'Spacing again', t + 50), ('agent', 'Ok.', t + 51)], name='Composer spacing')
        named = next(s for s in hi.read_codex(self.cwd, self.codex) if s.id == 'c4')
        self.assertEqual((named.title, named.named_by), ('Composer spacing', 'codex name'))


class StatusTests(unittest.TestCase):
    def session(self, **fields):
        base = dict(provider='claude', id='x', path='', first_text='Fix login', first_at=0.0, last_at=100.0, turns=2, replies=2,
                    answered=True, last_text='now the tests')
        base.update(fields)
        return hi.Session(**base)

    def test_the_rule(self):
        latest = 100.0
        self.assertEqual(hi.status(self.session(first_text=None), latest), ('skipped', 'nothing you typed'))
        self.assertEqual(hi.status(self.session(provider='codex', source='exec'), latest), ('skipped', 'run by a tool, not you (exec)'))
        self.assertEqual(hi.status(self.session(provider='codex', source='vscode'), latest), ('open', 'recent'))
        # NEXT-UP #38: asked for, a tool's run meets the same rules as yours.
        self.assertEqual(hi.status(self.session(provider='codex', source='exec'), latest, include_tool_runs=True), ('open', 'recent'))
        self.assertEqual(hi.status(self.session(turns=1, replies=0), latest), ('skipped', 'no reply'))
        self.assertEqual(hi.status(self.session(archived=True), latest), ('finished', 'archived in Codex'))
        self.assertEqual(hi.status(self.session(last_text="Perfect, thanks. That's all for now!"), latest), ('finished', 'signed off'))
        self.assertEqual(hi.status(self.session(last_text='ok now fix the tests'), latest), ('open', 'recent'))
        self.assertEqual(hi.status(self.session(last_at=latest - (hi.OPEN_DAYS + 1) * DAY), latest), ('finished', f'idle {hi.OPEN_DAYS + 1} days'))
        self.assertEqual(hi.status(self.session(last_at=latest - (hi.OPEN_DAYS - 1) * DAY), latest), ('open', 'recent'))
        self.assertEqual(hi.status(self.session(answered=False), latest), ('open', 'waiting on a reply'))

    def test_idle_is_measured_from_the_folders_newest_activity_not_today(self):
        old = 1_000_000.0
        self.assertEqual(hi.status(self.session(last_at=old - 3 * DAY), old), ('open', 'recent'))


class FloorTests(Fixture):
    def test_a_stores_newer_activity_moves_the_clock_and_nothing_else_does(self):
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page', t), assistant(t + 1)])
        sessions, latest = hi.read_folder(self.cwd, self.claude, self.codex)
        self.assertEqual((latest, sessions[0].state), (t + 1, 'open'), 'alone, the folder is its own clock')
        sessions, latest = hi.read_folder(self.cwd, self.claude, self.codex, latest_floor=t + 30 * DAY)
        self.assertEqual((latest, sessions[0].state, sessions[0].reason), (t + 30 * DAY, 'finished', 'idle 29 days'))
        sessions, latest = hi.read_folder(self.cwd, self.claude, self.codex, latest_floor=t - 30 * DAY)
        self.assertEqual((latest, sessions[0].state), (t + 1, 'open'), 'an older floor changes nothing')

    def test_sign_off_words(self):
        self.assertTrue(hi.signed_off('thanks'))
        self.assertTrue(hi.signed_off('Great, done. Cheers!'))
        self.assertFalse(hi.signed_off('thanks, now ship the beta'))
        self.assertFalse(hi.signed_off(''))
        self.assertFalse(hi.signed_off(None))
        self.assertFalse(hi.signed_off(' '.join(['ok'] * (hi.SIGN_OFF_LIMIT + 1))))


class PlanTests(Fixture):
    def test_open_threads_are_named_and_filed_by_the_apps_own_rules_and_nothing_is_written_to_the_homes(self):
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page after the session expires', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Add a sign in button to the login page', t + 100), assistant(t + 101)])
        self.transcript(SID_C, [user('Design the connector picker', t + 200), assistant(t + 201), user('perfect, thanks', t + 202), assistant(t + 203)])
        self.transcript(SID_D, [user('Old sidebar work', t - 40 * DAY), assistant(t - 40 * DAY + 1)])
        self.codex_thread('c1', t + 300, t + 310, [('you', 'Also make the picker frosted', t + 300), ('agent', 'Done.', t + 310)])
        before = self.snapshot()
        with tempfile.TemporaryDirectory() as store_dir:
            result = hi.plan(self.cwd, store_dir, claude_home=self.claude, codex_home=self.codex)
        self.assertEqual(before, self.snapshot(), 'the homes are read, never written')
        by_id = {s.id: s for s in result['sessions']}
        self.assertEqual(by_id[SID_A].state, 'open')
        self.assertEqual((by_id[SID_C].state, by_id[SID_C].reason), ('finished', 'signed off'))
        self.assertEqual(by_id[SID_D].state, 'finished')
        self.assertIsNone(by_id[SID_C].group, 'a finished thread is not filed unless asked')
        names = {stream['name']: [s.id for s in stream['threads']] for stream in result['workstreams']}
        self.assertIn('Authentication', names, 'the topic step files a login thread under Authentication')
        self.assertEqual(names['Authentication'], [SID_A, SID_B, 'c1'])
        self.assertEqual(by_id[SID_A].rule, 'topic, new workstream')
        self.assertEqual(by_id[SID_B].rule, 'topic')
        self.assertEqual(by_id['c1'].rule, 'follow-up', 'a request that leans on the one before joins the workstream that moved last')
        counts = result['counts']
        self.assertEqual((counts['claude'], counts['codex'], counts['sessions'], counts['open'], counts['finished']), (4, 1, 5, 3, 2))
        self.assertEqual(counts['workstreams'], len(result['workstreams']))
        self.assertEqual(counts['filed'], 3)

    def test_the_tools_own_title_names_the_thread_and_the_first_message_is_kept_for_comparison(self):
        t = self.T0
        # The brief mentions instructions, so the first-message rules abstain; the ai-title names it anyway.
        self.transcript(SID_A, [user('Read the instructions in the handoff, then fix the composer spacing', t), assistant(t + 1),
                                dict(type='ai-title', aiTitle='Composer spacing after the handoff review')])
        self.transcript(SID_B, [user('Tighten the composer padding', t + 10), assistant(t + 11)])
        with tempfile.TemporaryDirectory() as store_dir:
            result = hi.plan(self.cwd, store_dir, claude_home=self.claude, codex_home=self.codex)
        by_id = {s.id: s for s in result['sessions']}
        titled, plain = by_id[SID_A], by_id[SID_B]
        from thread_names import workstream_name
        self.assertEqual((titled.name, titled.named_by, titled.opener_name), ('Composer spacing', 'ai-title', None),
                         'tidy_title shortens a six-word title the way it shortens a Task guide title')
        self.assertEqual((titled.group, titled.rule), (workstream_name('Composer spacing'), 'new workstream'), 'the workstream is named from the title')
        self.assertEqual((plain.name, plain.named_by, plain.opener_name), ('Composer padding', 'first message', 'Composer padding'))
        self.assertEqual((plain.group, plain.rule), (titled.group, 'name overlap'))
        self.assertEqual(result['counts']['named_by'], {'ai-title': 1, 'custom-title': 0, 'codex name': 0, 'first message': 1})
        self.assertEqual(result['counts']['abstained'], 0)

    def test_finished_threads_can_be_filed_too(self):
        t = self.T0
        self.transcript(SID_A, [user('Build the connector picker', t), assistant(t + 1), user('perfect, thanks', t + 2), assistant(t + 3)])
        with tempfile.TemporaryDirectory() as store_dir:
            result = hi.plan(self.cwd, store_dir, claude_home=self.claude, codex_home=self.codex, include_finished=True)
        [session] = result['sessions']
        self.assertEqual((session.state, session.name, session.group, session.rule), ('finished', 'Connector picker', 'Connector', 'new workstream'))

    def test_a_first_message_the_app_would_abstain_on_stays_unnamed_and_unfiled(self):
        t = self.T0
        self.transcript(SID_A, [user('Read /Users/someone/notes.txt and fix it', t), assistant(t + 1)])
        with tempfile.TemporaryDirectory() as store_dir:
            result = hi.plan(self.cwd, store_dir, claude_home=self.claude, codex_home=self.codex)
        [session] = result['sessions']
        self.assertEqual((session.state, session.name, session.group), ('open', None, None))
        self.assertEqual(result['counts']['abstained'], 1)
        self.assertEqual(hi.why_abstained(session.first_text), 'a path')
        self.assertEqual(hi.why_abstained('Rotate the secrets file'), 'a sensitive word')
        self.assertEqual(hi.why_abstained('see https://example.test'), 'a URL')
        self.assertEqual(hi.why_abstained('Fix the login page'), 'no words to name it')

    def test_codexs_copy_of_a_claude_code_session_counts_once(self):
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page', t), assistant(t + 1)])
        self.codex_thread('c1', t + 5, t + 6, [('you', 'Fix the login page', t + 5), ('agent', 'Done.', t + 6)])
        self.codex_thread('c2', t + 7, t + 8, [('you', 'Fix the logout page', t + 7), ('agent', 'Done.', t + 8)])
        source = self.claude / 'projects' / _slug(os.path.realpath(self.cwd)) / f'{SID_A}.jsonl'
        (self.codex / 'external_agent_session_imports.json').write_text(json.dumps(dict(records=[
            dict(source_path=str(source), imported_thread_id='c1', content_sha256='x'),
            dict(source_path='/elsewhere/not-a-uuid.jsonl', imported_thread_id='c2')])))
        self.assertEqual(hi.codex_imports(self.codex), {'c1': SID_A})
        sessions, _ = hi.read_folder(self.cwd, self.claude, self.codex)
        by_id = {s.id: s for s in sessions}
        self.assertEqual((by_id['c1'].state, by_id['c1'].reason, by_id['c1'].copy_of), ('skipped', hi.COPY_REASON, SID_A))
        self.assertEqual(by_id['c2'].state, 'open')
        self.assertEqual(by_id[SID_A].state, 'open')

    def test_a_forked_session_counts_once(self):
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Fix the login page', t), assistant(t + 1), user('and the logout', t + 50), assistant(t + 51)])
        sessions, _ = hi.read_folder(self.cwd, self.claude, self.codex)
        by_id = {s.id: s for s in sessions}
        self.assertEqual((by_id[SID_A].state, by_id[SID_A].reason), ('skipped', f'a fork of {SID_B[:8]}'))
        self.assertEqual(by_id[SID_B].state, 'open')


class DryRunTests(Fixture):
    def test_the_tool_prints_the_plan_and_the_three_numbers(self):
        import io
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
        import import_dry_run
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page after the session expires', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Read /tmp/x first', t + 10), assistant(t + 11)])
        out = io.StringIO()
        before = self.snapshot()
        with unittest.mock.patch('sys.stdout', out):
            import_dry_run.main([str(self.cwd), '--claude-home', str(self.claude), '--codex-home', str(self.codex)])
        self.assertEqual(before, self.snapshot())
        text = out.getvalue()
        self.assertIn('(nothing written)', text)
        self.assertIn('Authentication  (1)', text)
        self.assertIn('topic, new workstream', text)
        self.assertIn('Open but not filed (1)', text)
        self.assertIn('Named from: ai-title 0, custom-title 0, Codex name 0, first message 1; no name 1.', text)
        self.assertIn('2 transcripts read. 2 sessions you started (2 open, 0 finished). 1 things being worked on.', text)


if __name__ == '__main__':
    unittest.main()
