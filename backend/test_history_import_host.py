"""history_look and history_read on a real Host over a disposable store, against synthetic Claude Code and Codex
homes (test_history_import.Fixture). No real home is read, no provider, no network."""
import sqlite3
import tempfile
import unittest.mock
from pathlib import Path

import history_import as hi
from dervo_connections import Rejected
from host import Host
from test_history_import import Fixture, SID_A, SID_B, SID_C, SID_D, assistant, user

CODEX_ID = '01a06e9e-9c4c-7020-9378-dbd3052f3645'


class HostFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.cwd = self.cwd.resolve()  # the store keeps the resolved path (/private/var on a Mac), and Codex's rows must match it
        self.enterContext(unittest.mock.patch.object(hi, 'CLAUDE_HOME', self.claude))
        self.enterContext(unittest.mock.patch.object(hi, 'CODEX_HOME', self.codex))
        self.host = Host(self.root / 'store')
        self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'claude', self.root / 'account', 'you@example.test', 'personal')
        self.project = self.host.add_picked_project(str(self.cwd), 'account', 'claude-opus-5')['id']
        self.scope = dict(projectId=self.project, profileId='account')

    def history(self):
        """Four Claude Code sessions and one Codex thread: two open on the login page, one signed off, one old, and a
        Codex follow-up. The same folder test_history_import files into Authentication."""
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page after the session expires', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Add a sign in button to the login page', t + 100), assistant(t + 101)])
        self.transcript(SID_C, [user('Design the connector picker', t + 200), assistant(t + 201), user('perfect, thanks', t + 202), assistant(t + 203)])
        self.transcript(SID_D, [user('Old sidebar work', t - 40 * hi.OPEN_DAYS * 86400), assistant(t - 40 * hi.OPEN_DAYS * 86400 + 1)])
        self.codex_thread(CODEX_ID, t + 300, t + 310, [('you', 'Also make the picker frosted', t + 300), ('agent', 'Done.', t + 310)])

    def threads(self):
        return self.host.store.db.execute('SELECT id, session_id FROM threads WHERE project_id=? ORDER BY rowid', (self.project,)).fetchall()

    def conversations(self):
        return self.host.dispatch('conversations', {'projectId': self.project})


class LookTests(HostFixture):
    def test_a_folder_with_no_history_looks_like_nothing_and_the_offer_is_not_made(self):
        self.assertIsNone(self.host.dispatch('history_look', {'projectId': self.project}))

    def test_the_look_counts_sessions_and_conversations_with_their_first_and_last_moments_and_makes_nothing(self):
        self.history()
        self.transcript('55555555-5555-4555-8555-555555555555', [dict(type='queue-operation', operation='enqueue')])  # nothing typed
        before = self.snapshot()
        look = self.host.dispatch('history_look', {'projectId': self.project})
        self.assertEqual(before, self.snapshot(), 'the homes are read, never written')
        self.assertEqual(look['folder'], str(self.cwd.resolve()))
        self.assertEqual((look['sessions'], look['conversations']), (6, 5))
        self.assertEqual(look['sources'], ['claude', 'codex'])
        self.assertEqual(look['first'], self.T0 - 40 * hi.OPEN_DAYS * 86400)
        self.assertGreaterEqual(look['last'], self.T0 + 310)
        self.assertEqual(self.threads(), [], 'a look makes nothing')
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM project_ideas').fetchone()[0], 0)

    def test_the_look_reads_only_the_head_of_a_transcript(self):
        t = self.T0
        records = [user('Fix the login page', t), assistant(t + 1)] + [assistant(t + 2 + n) for n in range(hi.LOOK_LINES * 2)]
        self.transcript(SID_A, records)
        with unittest.mock.patch.object(hi, 'typed_text', wraps=hi.typed_text) as typed:
            look = self.host.dispatch('history_look', {'projectId': self.project})
        self.assertEqual(look['conversations'], 1)
        self.assertLessEqual(typed.call_count, 2, 'the look stops at the first thing you typed')

    def test_the_fields_are_exact(self):
        with self.assertRaises(Rejected):
            self.host.dispatch('history_look', {'projectId': self.project, 'extra': 1})
        with self.assertRaises(Rejected):
            self.host.dispatch('history_read', {})
        with self.assertRaises(Rejected):
            self.host.dispatch('history_look', {'projectId': 'nope'})


class ReadTests(HostFixture):
    def test_imported_brief_survives_reopen_and_redacts_secrets(self):
        from history_import_host import context_block
        token = 'dop_v1_' + 'ab12' * 16
        reply = assistant(self.T0 + 1)
        reply['message']['content'][0]['text'] = 'The next step is to inspect the login tests. Token: ' + token
        self.transcript(SID_A, [user('Fix the login page', self.T0), reply])
        self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [SID_A]})
        channel = self.threads()[0]['id']
        block = context_block(self.host.store.db, channel)
        self.assertIn('inspect the login tests', block)
        self.assertIn('[redacted]', block)
        self.assertNotIn(token, block)
        self.assertIn('not a current request or permission', block)
        self.assertEqual(context_block(self.host.store.db, 'another-thread'), '')
        databases = self.host.store.db.execute('PRAGMA database_list').fetchall()
        path = next(row[2] for row in databases if row[1] == 'main')
        with sqlite3.connect(path) as reopened:
            self.assertEqual(context_block(reopened, channel), block)

    def test_preview_recommends_open_work_without_creating_threads(self):
        self.history()
        result = self.host.dispatch('history_preview', {'projectId': self.project})
        self.assertEqual(self.threads(), [])
        self.assertEqual({r['sessionId'] for r in result['candidates'] if r['recommended']},
                         {SID_A, SID_B, CODEX_ID})
        chosen = [SID_A]
        made = self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': chosen})
        self.assertEqual(made['counts']['conversations'], 1)
        channel = self.threads()[0]['id']
        history = self.host.dispatch('history', {'projectId': self.project, 'channelId': channel})
        self.assertTrue(any('Fix the login page' in row['content'] and 'Imported history' in row['content'] for row in history))
        self.assertEqual(history[0]['pubkey'], 'dervo-coordinator')
        self.assertEqual(history[0]['state'], 'answer')
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM turns').fetchone()[0], 0)

    def test_selected_history_must_belong_to_this_project(self):
        self.history()
        with self.assertRaises(Rejected):
            self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': ['not-from-this-project']})
        self.assertEqual(self.threads(), [])

    def test_the_read_makes_a_conversation_per_session_files_it_and_writes_the_session_id(self):
        self.history()
        before = self.snapshot()
        result = self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual(before, self.snapshot(), 'the homes are read, never written')
        # Open and finished sessions come in; the skipped ones are set aside. Nothing typed, no reply and forks are
        # the reader's rules, tested there.
        self.assertEqual(result['counts'], dict(conversations=5, sessions=5, working=3))
        self.assertEqual((result['set_aside'], result['already'], result['unnamed'], result['unfiled']), (0, 0, 0, []))
        self.assertIsNone(result['question'])
        self.assertEqual([line['kept'] for line in result['read']], [True] * 5)
        # The session ids are on the threads: the Claude Code file stem, the Codex thread id.
        rows = self.threads()
        self.assertEqual(len(rows), 5)
        self.assertEqual({row['session_id'] for row in rows}, {SID_A, SID_B, SID_C, SID_D, CODEX_ID})
        # Each thread names its rule and its workstream; the workstream's rule is its first thread's.
        threads = {t['name']: t for ws in result['workstreams'] for t in ws['threads']}
        by_stream = {ws['name']: ws for ws in result['workstreams']}
        self.assertIn('Authentication', by_stream, 'the topic step files the login threads under Authentication')
        auth = by_stream['Authentication']
        self.assertEqual(auth['rule'], 'topic, new workstream')
        self.assertEqual([t['rule'] for t in auth['threads']], ['topic, new workstream', 'topic'])
        self.assertTrue(all(t['open'] and t['source'] == 'claude' for t in auth['threads']))
        # With the finished threads in, the Codex follow-up finds the picker thread it leans on (thread overlap) rather
        # than the workstream that moved last, which is what the dry run's open-only plan gives it.
        design = by_stream['Design']
        self.assertEqual([(t['rule'], t['source'], t['open']) for t in design['threads']],
                         [('topic, new workstream', 'claude', False), ('thread overlap', 'codex', True)])
        self.assertFalse(threads['Connector picker']['open'], 'signed off, so finished')
        self.assertEqual(threads['Connector picker']['sessions'], 1)
        self.assertEqual(by_stream['Sidebar']['threads'][0]['open'], False, 'idle past OPEN_DAYS, so finished')
        # The app's own lists agree: the sidebar's group and the workstream listing.
        listed = self.conversations()
        self.assertEqual({row['name']: row['organisationGroup'] for row in listed}, {t['name']: ws['name'] for ws in result['workstreams'] for t in ws['threads']})
        ideas = self.host.dispatch('ideas_list', self.scope)
        names = {idea['name']: idea for idea in ideas['ideas']}
        self.assertEqual(set(names), set(by_stream))
        self.assertEqual({ws['id'] for ws in result['workstreams']}, {idea['id'] for idea in ideas['ideas']})
        # The thread ids the result gives are the conversations the app now lists.
        self.assertEqual({t['id'] for t in threads.values()}, {row['id'] for row in listed})
        # The organisation record says the import did it, so the sidebar can say so and undo it.
        state = self.host.dispatch('organisation_get', {'projectId': self.project, 'channelId': auth['threads'][0]['id']})
        self.assertEqual((state['group'], state['groupLocked'], state['source']['kind']), ('Authentication', True, 'history'))

    def test_a_second_read_imports_nothing_and_says_so(self):
        self.history()
        first = self.host.dispatch('history_read', {'projectId': self.project})
        rows = self.threads()
        second = self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual(second['counts'], dict(conversations=0, sessions=5, working=0))
        self.assertEqual((second['already'], second['set_aside']), (5, 5))
        self.assertEqual(second['workstreams'], [])
        self.assertEqual([line['label'] for line in second['read']], ['already in Dervo'] * 5)
        self.assertEqual(self.threads(), rows, 'no thread was made or changed')
        self.assertEqual(len(self.host.dispatch('ideas_list', self.scope)['ideas']), len(first['workstreams']), 'no workstream was made')

    def test_the_key_is_the_session_id_so_an_archived_import_does_not_come_back(self):
        """Bryce's own case (21 Sep): every thread archived. The offer isn't made for that project, and even a read
        forced through makes nothing, because the archived threads still carry their ids."""
        self.history()
        first = self.host.dispatch('history_read', {'projectId': self.project})
        for ws in first['workstreams']:
            for thread in ws['threads']:
                self.host.dispatch('conversation_archive', {'projectId': self.project, 'channelId': thread['id']})
        self.assertEqual(self.conversations(), [])
        second = self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual((second['counts']['conversations'], second['already']), (0, 5))
        self.assertEqual(self.conversations(), [])

    def test_a_read_that_fails_partway_leaves_the_project_as_blank_as_it_was(self):
        self.history()
        from project_ideas import ProjectIdeas
        original = ProjectIdeas.file_by_rules
        calls = []

        def failing(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise sqlite3.OperationalError('disk I/O error')
            return original(self, *args, **kwargs)
        with unittest.mock.patch.object(ProjectIdeas, 'file_by_rules', failing):
            with self.assertRaises(sqlite3.OperationalError):
                self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual(len(calls), 3, 'two threads were made before the third failed')
        db = self.host.store.db
        self.assertEqual(self.threads(), [])
        self.assertEqual(db.execute('SELECT count(*) FROM local_channels').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM conversation_organisation').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM project_ideas').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM project_idea_members').fetchone()[0], 0)
        self.assertFalse(db.in_transaction)
        # And the same read works afterwards, from nothing.
        result = self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual(result['counts']['conversations'], 5)

    def test_what_the_import_made_can_be_archived_like_anything_else(self):
        self.history()
        result = self.host.dispatch('history_read', {'projectId': self.project})
        auth = next(ws for ws in result['workstreams'] if ws['name'] == 'Authentication')
        listing = self.host.dispatch('ideas_list', self.scope)
        record = next(idea for idea in listing['ideas'] if idea['id'] == auth['id'])
        # The workstream: archived, and its threads read as not filed, the way any archived workstream's do.
        after = self.host.dispatch('ideas_archive', dict(self.scope, ideaId=auth['id'], expectedRevision=record['revision']))
        self.assertIn(auth['id'], {idea['id'] for idea in after['archived']})
        self.assertNotIn(auth['id'], {idea['id'] for idea in after['ideas']})
        groups = {row['id']: row['organisationGroup'] for row in self.conversations()}
        self.assertTrue(all(groups[t['id']] is None for t in auth['threads']))
        # A thread: archived, out of the live list, in the archived list, and back on restore with its session id.
        thread = auth['threads'][0]
        self.host.dispatch('conversation_archive', {'projectId': self.project, 'channelId': thread['id']})
        self.assertNotIn(thread['id'], {row['id'] for row in self.conversations()})
        self.assertIn(thread['id'], {row['id'] for row in self.host.dispatch('conversations_archived', {'projectId': self.project})})
        self.host.dispatch('conversation_restore', {'projectId': self.project, 'channelId': thread['id']})
        self.assertIn(thread['id'], {row['id'] for row in self.conversations()})
        self.assertEqual(self.host.store.db.execute('SELECT session_id FROM threads WHERE id=?', (thread['id'],)).fetchone()[0], SID_A)

    def test_a_session_the_rules_cannot_name_is_set_aside_and_said_so(self):
        t = self.T0
        self.transcript(SID_A, [user('sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 use this', t), assistant(t + 1)])
        self.transcript(SID_B, [user('Tighten the composer padding', t + 10), assistant(t + 11)])
        result = self.host.dispatch('history_read', {'projectId': self.project})
        self.assertEqual((result['counts']['conversations'], result['unnamed']), (1, 1))
        self.assertEqual([line for line in result['read'] if not line['kept']], [dict(label='nothing to name it from', kept=False)])
        self.assertEqual(len(self.threads()), 1)


class UnnamedTests(HostFixture):
    """NEXT-UP #37: the Found screen counted one conversation and the picker said there were none, because the rules
    abstained on its first message and the preview dropped anything without a name."""

    def unnamed_history(self):
        t = self.T0
        # A path in the first message: suggest abstains (history_import.why_abstained says 'a path').
        self.transcript(SID_A, [user('Look at ~/code/app/src/main.ts and tell me why it fails', t), assistant(t + 1)])
        self.assertEqual(hi.why_abstained('Look at ~/code/app/src/main.ts and tell me why it fails'), 'a path')

    def test_the_preview_lists_an_unnamed_conversation_instead_of_dropping_it(self):
        self.unnamed_history()
        look = self.host.dispatch('history_look', {'projectId': self.project})
        self.assertEqual(look['conversations'], 1)
        result = self.host.dispatch('history_preview', {'projectId': self.project})
        self.assertEqual([(r['sessionId'], r['name'], r['unnamed'], r['recommended']) for r in result['candidates']],
                         [(SID_A, '', True, True)], 'the one conversation the look counted is in the picker')
        self.assertIn('main.ts', result['candidates'][0]['excerpt'], 'the excerpt says what it is')
        self.assertEqual(result['toolRuns'], 0, 'no tool runs here, so no option to offer')
        self.assertEqual(self.threads(), [])

    def test_a_picked_unnamed_conversation_comes_in_with_the_name_you_gave_it(self):
        self.unnamed_history()
        made = self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [SID_A], 'names': {SID_A: '  Main crash  '}})
        self.assertEqual((made['counts']['conversations'], made['unnamed']), (1, 0))
        self.assertEqual([t['name'] for ws in made['workstreams'] for t in ws['threads']] + [t['name'] for t in made['unfiled']], ['Main crash'])
        self.assertEqual([row['name'] for row in self.conversations()], ['Main crash'])
        self.assertEqual([row['session_id'] for row in self.threads()], [SID_A])

    def test_a_picked_unnamed_conversation_with_no_name_given_still_comes_in(self):
        self.unnamed_history()
        made = self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [SID_A]})
        self.assertEqual(made['counts']['conversations'], 1)
        self.assertEqual([row['name'] for row in self.conversations()], ['Unnamed conversation'])

    def test_names_must_be_for_picked_sessions_and_one_line(self):
        self.history()
        self.unnamed_history()
        for names in ({SID_B: 'Not picked'}, {SID_A: ''}, {SID_A: 'Two\nlines'}, {SID_A: 'x' * 101}, {SID_A: 7}, ['Main']):
            with self.subTest(names=names), self.assertRaises(Rejected):
                self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [SID_A], 'names': names})
        self.assertEqual(self.threads(), [])
        with self.assertRaises(Rejected, msg='names only come with a picked list'):
            self.host.dispatch('history_read', {'projectId': self.project, 'names': {SID_A: 'Main'}})


TOOL_ID = '02b17f9f-1d5d-7131-a489-ecd4163a4756'


class ToolRunTests(HostFixture):
    """NEXT-UP #38: someone running their own orchestration wants the sessions a tool ran. Off unless asked for, and
    labelled as the tool's when they come in."""

    def tool_history(self):
        t = self.T0
        self.transcript(SID_A, [user('Fix the login page after the session expires', t), assistant(t + 1)])
        self.codex_thread(TOOL_ID, t + 50, t + 60, [('you', 'Run the nightly invoice reconciliation', t + 50), ('agent', 'Reconciled 12 invoices.', t + 60)],
                          source='exec')

    def test_tool_runs_stay_out_unless_asked_for(self):
        self.tool_history()
        plain = self.host.dispatch('history_preview', {'projectId': self.project})
        self.assertEqual([r['sessionId'] for r in plain['candidates']], [SID_A])
        self.assertFalse(plain['candidates'][0]['toolRun'])
        self.assertEqual(plain['toolRuns'], 1, 'the screen offers the option because there is one to include')
        asked = self.host.dispatch('history_preview', {'projectId': self.project, 'includeToolRuns': True})
        rows = {r['sessionId']: r for r in asked['candidates']}
        self.assertEqual(set(rows), {SID_A, TOOL_ID})
        self.assertEqual(asked['toolRuns'], 1)
        self.assertEqual((rows[TOOL_ID]['toolRun'], rows[TOOL_ID]['ranBy'], rows[TOOL_ID]['source']), (True, 'exec', 'codex'))
        self.assertEqual((rows[SID_A]['toolRun'], rows[SID_A]['ranBy']), (False, None))
        self.assertEqual(self.threads(), [])

    def test_asking_for_tool_runs_never_pushes_your_own_threads_out_of_the_shortlist(self):
        t = self.T0
        own = [f'{n:08d}-0000-4000-8000-000000000000' for n in range(1, 9)]
        for n, sid in enumerate(own):
            self.transcript(sid, [user(f'Polish the settings screen part {n}', t + n), assistant(t + n + 1)])
        for n in range(3):
            self.codex_thread(f'0{n}b17f9f-1d5d-7131-a489-ecd4163a4756', t + 500 + n, t + 510 + n,
                              [('you', f'Run the nightly import {n}', t + 500 + n), ('agent', 'Done.', t + 510 + n)], source='exec')
        plain = self.host.dispatch('history_preview', {'projectId': self.project})
        asked = self.host.dispatch('history_preview', {'projectId': self.project, 'includeToolRuns': True})
        mine = lambda result: {r['sessionId'] for r in result['candidates'] if r['recommended'] and not r['toolRun']}
        self.assertEqual(mine(plain), set(own))
        self.assertEqual(mine(asked), set(own), 'the newer tool runs don’t take your threads’ places')
        self.assertEqual(sum(1 for r in asked['candidates'] if r['recommended'] and r['toolRun']), 3)

    def test_a_tool_run_needs_the_option_to_come_in(self):
        self.tool_history()
        with self.assertRaises(Rejected):
            self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [TOOL_ID]})
        self.assertEqual(self.threads(), [])
        with self.assertRaises(Rejected, msg='the option only comes with a picked list'):
            self.host.dispatch('history_read', {'projectId': self.project, 'includeToolRuns': True})
        with self.assertRaises(Rejected):
            self.host.dispatch('history_preview', {'projectId': self.project, 'includeToolRuns': 'yes'})

    def test_an_imported_tool_run_says_a_tool_ran_it_not_you(self):
        self.tool_history()
        made = self.host.dispatch('history_read', {'projectId': self.project, 'sessionIds': [TOOL_ID, SID_A], 'includeToolRuns': True})
        threads = {t['name']: t for ws in made['workstreams'] for t in ws['threads']} | {t['name']: t for t in made['unfiled']}
        tool = next(t for t in threads.values() if t['source'] == 'codex')
        self.assertTrue(tool['toolRun'])
        self.assertFalse(next(t for t in threads.values() if t['source'] == 'claude')['toolRun'])
        history = self.host.dispatch('history', {'projectId': self.project, 'channelId': tool['id']})
        brief = next(row['content'] for row in history if 'Imported history' in row['content'])
        self.assertIn('Run by a tool (exec), not typed by you.', brief)
        self.assertIn('First prompt the tool sent', brief)
        self.assertNotIn('Original request', brief)
        own = self.host.dispatch('history', {'projectId': self.project, 'channelId': threads[next(n for n, t in threads.items() if t['source'] == 'claude')]['id']})
        own_brief = next(row['content'] for row in own if 'Imported history' in row['content'])
        self.assertIn('Original request', own_brief)
        self.assertNotIn('Run by a tool', own_brief)


if __name__ == '__main__':
    unittest.main()
