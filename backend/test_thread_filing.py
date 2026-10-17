"""Threads organise themselves: every thread gets a short name and a workstream from records and rules, with no model
anywhere in these tests. Real Host and store. Scenario 10 of BRIEF-wedge.md: ten threads started over a week, some from
Home and some typed into a new thread, read as a tidy sidebar with no "Not filed yet" left over."""
import json
import unittest

from dervo_connections import Rejected
from project_ideas import WORKSTREAM_CAP
from test_project_ideas import IdeaFixture
from thread_names import MAX_WORDS, NAME_LIMIT, first_message_name

# Bryce's week, 18 Sep: real requests from his "dervo app" project, and the ones the design brief adds.
IDEAS = 'Read PLAN-orchestrator.md first. Start with step 1 only: ideas, project-scoped, with the idea as the group key for conversations.'
LIVE = 'Ideally I want to see this thing working and see the orchestrator working in real time so I can tell what each job is doing'
SWEEP = 'I think it would be good to do a full design sweep as a really critical design person who is looking at every screen'
PICKER = 'the connector picker should warn when a server isn’t allowed in this project'
SPACING = 'Tighten the sidebar spacing between groups and make the selected row frosted'
LOGIN = 'Fix login: the session expires after an hour and people have to sign in again'
RESTART = 'After a beta downloads, offer Update and restart in the sidebar footer instead of a dialog'
PLANNER = 'Add the release planner screen for the owner: four lists, in this beta, built for the next beta, people want, planned'
KNOWLEDGE = 'Knowledge page feels scattered, make it one clear page with guidance, lessons and starter procedures'


class FilingFixture(IdeaFixture):
    def typed(self, text, project='p', private=False):
        """A thread you open yourself and type into: Dervo names and files it at the first message."""
        channel = self.conversation(private=private, project=project)
        self.host.dispatch('queue', dict(projectId=project, channelId=channel, clientId='first-' + channel[:8], text=text, parentId=None))
        return channel

    def from_home(self, title, text, workstream=None):
        """A thread Home starts: named by its model, filed under the workstream it chose (or none), then the job is sent.
        Home makes and files as HOME_ACTOR, as project_orchestrator.apply does, so the record says Home and not you."""
        from conversation_organisation import HOME_ACTOR
        channel = self.conversation(title)
        if workstream:
            idea = self.named(workstream) if any(i['name'] == workstream for i in self.call('list')['ideas']) else None
            if idea is None:
                self.ideas.dispatch('ideas_save', dict(self.scope, ideaId=None, expectedRevision=0, name=workstream, summary='',
                                                       state='building', target=None), HOME_ACTOR)
                idea = self.named(workstream)
            self.ideas.dispatch('ideas_assign', dict(self.scope, channelId=channel, ideaId=idea['id']), HOME_ACTOR)
        self.host.dispatch('queue', dict(projectId='p', channelId=channel, clientId='home-' + channel[:8], text=text, parentId=None))
        return channel

    def sidebar(self):
        """{workstream: [thread names]} plus the loose threads, as the sidebar would group them."""
        listing = self.call('list')
        groups = {idea['name']: [c['name'] for c in idea['conversations']] for idea in listing['ideas']}
        return groups, [c['name'] for c in listing['unassigned']]

    def organisation(self, channel):
        return self.host.dispatch('organisation_get', dict(projectId='p', channelId=channel))


class TenThreadsTests(FilingFixture):
    def test_ten_threads_over_a_week_read_as_a_tidy_sidebar(self):
        # Monday: Home starts the ideas work under a workstream it names; Bryce types three more himself.
        ideas = self.from_home('Ideas storage', IDEAS, workstream='Orchestrator')
        live = self.typed(LIVE)
        sweep = self.typed(SWEEP)
        picker = self.typed(PICKER)
        # Wednesday: Home files one under Design; the sidebar spacing request is typed.
        polish = self.from_home('Sidebar polish', 'Frosted selected row, calmer groups.', workstream='Design')
        spacing = self.typed(SPACING)
        login = self.typed(LOGIN)
        # Friday: Home starts a job without choosing a workstream; two more typed.
        restart = self.from_home('Update and restart', RESTART)
        planner = self.typed(PLANNER)
        knowledge = self.typed(KNOWLEDGE)

        groups, loose = self.sidebar()
        self.assertEqual(loose, [], 'no "Not filed yet" left over')
        self.assertEqual(groups, {
            'Orchestrator': ['Ideas storage', 'Orchestrator real time'],
            'Design': ['Full design sweep', 'Sidebar polish', 'Sidebar spacing between groups'],
            'Connector': ['Connector picker'],
            'Authentication': ['Fix login'],
            'Releases': ['Update and restart', 'Release planner screen'],
            'Knowledge': ['Knowledge page scattered'],
        })
        self.assertLessEqual(len(groups), WORKSTREAM_CAP)
        for workstream in groups:
            self.assertLessEqual(len(workstream.split()), 3, workstream)
        for names in groups.values():
            for name in names:
                self.assertTrue(1 <= len(name.split()) <= MAX_WORDS and len(name) <= NAME_LIMIT and not first_message_name(name), name)
        # How each landed: the rules' filings can be moved by Home, the ones Home chose stay.
        self.assertEqual({c: self.organisation(c).get('filedBy') for c in (live, sweep, picker, spacing, login, restart, planner, knowledge)},
                         {c: 'rules' for c in (live, sweep, picker, spacing, login, restart, planner, knowledge)})
        self.assertNotIn('filedBy', self.organisation(ideas))
        self.assertNotIn('filedBy', self.organisation(polish))
        self.assertEqual(self.organisation(live)['reason'], 'Local title extracted from submitted prose. No unambiguous local group match.')

    def test_naming_and_filing_are_one_change_and_undo_takes_both_back(self):
        channel = self.typed(LOGIN)
        state = self.organisation(channel)
        self.assertEqual((state['name'], state['group'], state['revision'], state['canUndo']), ('Fix login', 'Authentication', 1, True))
        undone = self.host.dispatch('organisation_undo', dict(projectId='p', channelId=channel, expectedRevision=1))
        self.assertEqual((undone['name'], undone['group']), ('New conversation', None))
        groups, loose = self.sidebar()
        # The rules made Authentication for this thread alone, so undoing the filing archives it too (review of
        # b11/sorting-t1: it stayed listed, empty, for good). Restorable like any archive.
        self.assertEqual(groups, {}, 'the undone filing leaves the workstream, and the empty guess goes with it')
        self.assertEqual([idea['name'] for idea in self.call('list')['archived']], ['Authentication'])
        self.assertEqual(loose, [channel and 'New conversation'])
        # Locked by the undo: the next message names and files nothing.
        self.host.dispatch('queue', dict(projectId='p', channelId=channel, clientId='second', text=KNOWLEDGE, parentId=None))
        self.assertEqual((self.organisation(channel)['name'], self.organisation(channel)['group']), ('New conversation', None))

    def test_a_follow_up_joins_the_workstream_that_moved_last(self):
        # With no thread opened in the last half hour the rules have nothing better than "moved last", so this stays.
        # When you had the picker thread open, the follow-up goes there (AutoSortingCaseTests, case 3).
        self.typed(PICKER)
        self.typed(LOGIN)
        follow = self.typed('also show which server refused, right in the warning')
        self.assertEqual(self.organisation(follow)['group'], 'Authentication')
        self.assertEqual(self.organisation(follow)['name'], 'Server refused')

    def test_at_the_cap_a_new_thread_joins_the_most_recent_workstream_and_nothing_is_made(self):
        for n in range(WORKSTREAM_CAP):
            self.new(f'Stream {n}')
        extra = self.typed(KNOWLEDGE)
        groups, loose = self.sidebar()
        self.assertEqual(len(groups), WORKSTREAM_CAP)
        self.assertEqual(groups[f'Stream {WORKSTREAM_CAP - 1}'], ['Knowledge page scattered'])
        self.assertEqual(loose, [])
        self.assertEqual(self.organisation(extra)['filedBy'], 'rules')

    def test_near_duplicate_workstreams_are_one(self):
        self.new('Connectors')
        picker = self.typed(PICKER)
        self.assertEqual(self.organisation(picker)['group'], 'Connectors', '"Connector" would be a near-duplicate, so the thread joins Connectors')
        self.assertEqual(self.ideas.find_similar(dict(project_id='p', profile_id='a'), 'Orchestrator work'), None)
        self.new('Orchestrator')
        self.assertEqual(self.ideas.find_similar(dict(project_id='p', profile_id='a'), 'Orchestrator work')['name'], 'Orchestrator')
        self.assertEqual(self.ideas.find_similar(dict(project_id='p', profile_id='a'), 'connectors')['name'], 'Connectors')

    def test_a_private_thread_is_named_but_never_filed(self):
        private = self.typed(LOGIN, private=True)
        state = self.organisation(private)
        self.assertEqual((state['name'], state['group']), ('Fix login', None))
        self.assertEqual(state['reason'], 'Private conversation: named from your request, never filed.')
        groups, loose = self.sidebar()
        self.assertEqual((groups, loose), ({}, []), 'no workstream was made for it, and it is not "Not filed yet" either')
        with self.assertRaises(Rejected):
            self.new('Somewhere')
            self.call('assign', channelId=private, ideaId=self.named('Somewhere')['id'])

    def test_a_message_with_no_work_in_it_waits_for_the_next_one(self):
        channel = self.typed('hi')
        self.assertEqual((self.organisation(channel)['name'], self.organisation(channel)['group']), ('New conversation', None))
        self.assertEqual(self.sidebar(), ({}, ['New conversation']))
        self.host.dispatch('queue', dict(projectId='p', channelId=channel, clientId='second', text=SWEEP, parentId=None))
        self.assertEqual((self.organisation(channel)['name'], self.organisation(channel)['group']), ('Full design sweep', 'Design'))

    def test_archiving_a_workstream_unfiles_its_threads_and_the_rules_file_them_again(self):
        # 19 Sep, map F3: archive left the membership pointing at the archived workstream and locked the thread, so it
        # was never refiled, never listed as unfiled, and Home's filing of it was dropped.
        spacing = self.typed(SPACING)
        login = self.typed(LOGIN)
        self.assertEqual((self.organisation(spacing)['group'], self.organisation(login)['group']), ('Design', 'Authentication'))
        design = self.named('Design')
        self.call('archive', ideaId=design['id'], expectedRevision=design['revision'])
        groups, loose = self.sidebar()
        self.assertEqual((groups, loose), ({'Authentication': ['Fix login']}, ['Sidebar spacing between groups']), 'it reads as Not filed yet')
        state = self.organisation(spacing)
        self.assertEqual((state['group'], state['groupLocked'], state.get('filedBy'), state['canUndo']), (None, False, None, True))
        self.assertEqual(state['reason'], 'Its workstream, Design, was archived. Dervo files it again at its next message.')
        self.assertEqual(self.organisation(login)['group'], 'Authentication', 'negative: another workstream\'s thread is untouched')
        self.assertEqual([c['name'] for c in self.named('Design')['conversations']], ['Sidebar spacing between groups'],
                         'the archived workstream still shows what a restore would bring back')
        # The next message files it by the rules, into a live workstream.
        self.host.dispatch('queue', dict(projectId='p', channelId=spacing, clientId='second', text='also make the selected row frosted', parentId=None))
        state = self.organisation(spacing)
        self.assertEqual((state['group'], state['filedBy']), ('Authentication', 'rules'), 'a follow-up joins the workstream that moved last')
        # Restoring Design brings back only what nothing refiled since.
        design = next(idea for idea in self.call('list')['archived'] if idea['name'] == 'Design')
        self.call('restore', ideaId=design['id'], expectedRevision=design['revision'])
        self.assertEqual(self.organisation(spacing)['group'], 'Authentication', 'refiled since, so restore leaves it')
        # Negative: a thread nothing refiled comes back with a restore, and Undo of the unfiling puts the group back
        # as one revision.
        planner = self.typed(PLANNER)
        self.assertEqual(self.organisation(planner)['group'], 'Releases')
        releases = self.named('Releases')
        self.call('archive', ideaId=releases['id'], expectedRevision=releases['revision'])
        state = self.organisation(planner)
        self.assertEqual((state['group'], state['groupLocked']), (None, False))
        releases = next(idea for idea in self.call('list')['archived'] if idea['name'] == 'Releases')
        self.call('restore', ideaId=releases['id'], expectedRevision=releases['revision'])
        self.assertEqual((self.organisation(planner)['group'], self.organisation(planner)['groupLocked']), ('Releases', True))
        releases = self.named('Releases')
        self.call('archive', ideaId=releases['id'], expectedRevision=releases['revision'])
        state = self.organisation(planner)
        undone = self.host.dispatch('organisation_undo', dict(projectId='p', channelId=planner, expectedRevision=state['revision']))
        self.assertEqual((undone['group'], undone['groupLocked']), ('Releases', True))

    def test_a_hand_move_is_one_action_and_locks_the_thread_against_home(self):
        picker = self.typed(PICKER)
        self.new('Integrations')
        self.call('assign', channelId=picker, ideaId=self.named('Integrations')['id'])
        state = self.organisation(picker)
        self.assertEqual((state['group'], state['groupLocked']), ('Integrations', True))
        self.assertNotIn('filedBy', state)
        self.assertFalse(self.ideas.filed_by_rules(picker))


class AutoSortingCaseTests(FilingFixture):
    """The rules cases of notes/smartness/auto-sorting.md's eval sketch (19 Sep), each with a negative control: the same
    path with the condition off, asserting the old behaviour where it was right. No model."""
    TIGHTEN = 'Tighten the screenshot typography'
    ASKING = ('Fix the key handling in the composer', 'Show token usage per thread in the sidebar', 'Make the sidebar 20% narrower',
              'Support Cmd+K for the command palette', 'Fix the sidebar/composer gap', 'Add a #mention picker to the composer')

    def seen(self, channel, ago=0):
        import time
        import conversation_activity
        conversation_activity.schema(self.host.store.db)
        with self.host.store.db:
            self.host.store.db.execute('INSERT OR REPLACE INTO conversation_seen VALUES (?,?)', (channel, time.time() - ago))

    def test_case_1_your_hand_filing_beats_the_topic_guess(self):
        self.new('Preview pane')
        capture = self.typed('Capture screenshot of running')
        self.call('assign', channelId=capture, ideaId=self.named('Preview pane')['id'])
        # You keep working in it: the record's source moves to the new message, and the hand filing still counts.
        self.host.dispatch('queue', dict(projectId='p', channelId=capture, clientId='second', text='and the menu bar too', parentId=None))
        self.assertEqual(self.organisation(capture)['groupedBy'], 'you')
        tighten = self.typed(self.TIGHTEN)
        state = self.organisation(tighten)
        self.assertEqual((state['name'], state['group'], state['filedBy']), ('Screenshot typography', 'Preview pane', 'rules'))
        self.assertNotIn('Design', self.sidebar()[0], 'the topic guess made nothing')

    def test_case_1_negative_with_nothing_to_overlap_the_topic_still_files_it(self):
        self.new('Preview pane')
        tighten = self.typed(self.TIGHTEN)
        self.assertEqual(self.organisation(tighten)['group'], 'Design')

    def test_case_1_negative_homes_filing_is_not_yours_and_the_topic_still_goes_first(self):
        from conversation_organisation import HOME_ACTOR
        self.new('Preview pane')
        capture = self.typed('Capture screenshot of running')
        self.ideas.dispatch('ideas_assign', dict(self.scope, channelId=capture, ideaId=self.named('Preview pane')['id']), HOME_ACTOR)
        self.assertEqual(self.organisation(capture)['groupedBy'], 'home')
        self.assertEqual(self.organisation(self.typed(self.TIGHTEN))['group'], 'Design')

    def test_case_2_a_new_request_opening_with_this_is_not_a_follow_up(self):
        self.typed('Add the connector picker warning for servers this project blocks')
        self.typed(LOGIN)
        button = self.typed('This button is misaligned in settings')
        self.assertNotEqual(self.organisation(button)['group'], 'Authentication')
        self.assertIsNotNone(self.organisation(button)['group'], 'it gets a workstream of its own under the cap')

    def test_case_2_negative_an_also_request_still_follows_the_workstream_that_moved_last(self):
        self.typed('Add the connector picker warning for servers this project blocks')
        self.typed(LOGIN)
        also = self.typed('also make the button red')
        self.assertEqual(self.organisation(also)['group'], 'Authentication')

    def test_case_3_a_follow_up_joins_the_thread_you_just_had_open(self):
        picker = self.typed(PICKER)
        self.typed(LOGIN)
        self.seen(picker, ago=60)
        follow = self.typed('also show which server refused, right in the warning')
        self.assertEqual(self.organisation(follow)['group'], 'Connector')

    def test_case_3_negative_a_look_older_than_thirty_minutes_falls_back_to_moved_last(self):
        picker = self.typed(PICKER)
        self.typed(LOGIN)
        self.seen(picker, ago=31 * 60)
        follow = self.typed('also show which server refused, right in the warning')
        self.assertEqual(self.organisation(follow)['group'], 'Authentication')

    def test_case_4_everyday_requests_get_a_name_and_a_workstream(self):
        for text in self.ASKING:
            with self.subTest(text=text):
                state = self.organisation(self.typed(text))
                self.assertTrue(2 <= len(state['name'].split()) <= MAX_WORDS, state['name'])
                self.assertNotEqual(state['name'], 'New conversation')
                for symbol in '%+#/':
                    self.assertNotIn(symbol, state['name'])
                self.assertIsNotNone(state['group'])

    def test_case_4b_negative_a_key_or_password_never_reaches_a_name_or_workstream(self):
        values = {'the API key is sk-proj-AbC123xyz789': 'sk-proj-AbC123xyz789', 'set token ghp_abc123def456ghi789': 'ghp_abc123def456ghi789',
                  'key AKIAIOSFODNN7EXAMPLE for the upload job': 'AKIAIOSFODNN7EXAMPLE', 'the API key is hunter22': 'hunter22',
                  'the token is swordfish, use it for uploads': 'swordfish', 'token swordfish': 'swordfish',
                  'my key is hunter2 please': 'hunter2', 'Set token swordfish for staging': 'swordfish'}
        for text, value in values.items():
            with self.subTest(text=text):
                state = self.organisation(self.typed(text))
                self.assertNotIn(value.casefold(), state['name'].casefold())
                self.assertNotIn(value.casefold(), (state['group'] or '').casefold())
        rotate = self.organisation(self.typed('rotate the API token'))
        self.assertEqual(rotate['name'], 'Rotate API token', 'negative: a request about a token with no value is named')
        self.assertIsNotNone(rotate['group'])
        password = self.typed('the password is hunter2')
        self.assertEqual((self.organisation(password)['name'], self.organisation(password)['group']), ('New conversation', None))
        for group in self.sidebar()[0]:
            for value in list(values.values()) + ['hunter2']:
                self.assertNotIn(value.casefold(), group.casefold())

    def test_case_5_workstreams_are_named_with_nouns(self):
        catch = self.typed('Improve the catch up screen')
        crash = self.typed('Crash when exporting invoices to CSV')
        filing = self.typed('Home keeps filing threads under the wrong workstream')
        summary = self.typed('The Home summary repeats itself')
        self.assertEqual(self.organisation(catch)['group'], 'Catch up')
        self.assertIn(self.organisation(crash)['group'], ('CSV export', 'Invoices'))
        self.assertEqual((self.organisation(filing)['group'], self.organisation(summary)['group']), ('Home', 'Home'))

    def test_case_5_negative_a_noun_that_stands_alone_still_does(self):
        self.assertEqual(self.organisation(self.typed(KNOWLEDGE))['group'], 'Knowledge')
        self.assertEqual(self.organisation(self.typed(PICKER))['group'], 'Connector')

    def six_of_yours(self):
        for name in ('Preview pane', 'Sidebar', 'Onboarding', 'Payments', 'Search', 'Knowledge'):
            self.new(name)

    def test_case_6_an_empty_rules_made_workstream_holds_no_slot(self):
        self.six_of_yours()
        capture = self.typed('Capture screenshot of running')  # the rules make a seventh, "Capture"
        self.call('assign', channelId=capture, ideaId=self.named('Preview pane')['id'])  # and you empty it by hand
        meters = self.typed('Add usage meters for Codex accounts')
        group = self.organisation(meters)['group']
        self.assertNotIn(group, ('Preview pane', 'Sidebar', 'Onboarding', 'Payments', 'Search', 'Knowledge', 'Capture'))

    def test_at_the_cap_a_word_shared_with_a_workstreams_threads_beats_moved_last(self):
        export = self.typed('Export receipts to CSV')  # the rules make "Receipts", holding it
        workstream = self.organisation(export)['group']
        self.new('Payments', summary='invoices and receipts')
        for name in ('Sidebar', 'Onboarding', 'Search', 'Knowledge', 'Preview pane'):
            self.new(name)
        blurry = self.typed('The receipts look blurry')
        self.assertEqual(self.organisation(blurry)['group'], workstream, 'a thread name shares "receipts"; a summary word ties')
        # Negative: nothing shared with any thread's name, so it joins the one that moved last, as before.
        other = self.typed('Dark mode flickers')
        self.assertEqual(self.organisation(other)['group'], 'Preview pane')

    def test_a_workstream_dervo_made_goes_when_a_move_empties_it_and_undo_brings_it_back(self):
        from conversation_organisation import HOME_ACTOR
        self.new('Preview pane')
        capture = self.typed('Capture screenshot of running')  # the rules make "Capture"
        self.call('assign', channelId=capture, ideaId=self.named('Preview pane')['id'])  # your move empties it
        self.assertNotIn('Capture', self.sidebar()[0])
        archived = self.call('list')['archived']
        self.assertEqual([idea['name'] for idea in archived], ['Capture'], 'archived, not deleted')
        # Undo of your move puts the thread back, and the workstream with it.
        state = self.organisation(capture)
        self.host.dispatch('organisation_undo', dict(projectId='p', channelId=capture, expectedRevision=state['revision']))
        self.assertEqual(self.sidebar()[0].get('Capture'), ['Capture screenshot of running'])
        # Home's re-file empties it the same way.
        self.ideas.dispatch('ideas_assign', dict(self.scope, channelId=capture, ideaId=self.named('Preview pane')['id']), HOME_ACTOR)
        self.assertNotIn('Capture', self.sidebar()[0])
        # And it can be restored by hand like any archived workstream.
        capture_ws = next(idea for idea in self.call('list')['archived'] if idea['name'] == 'Capture')
        self.call('restore', ideaId=capture_ws['id'], expectedRevision=capture_ws['revision'])
        self.assertIn('Capture', self.sidebar()[0])

    def test_a_workstream_you_made_or_named_stays_when_a_move_empties_it(self):
        self.new('Preview pane')
        self.new('Mine')
        mine = self.typed('Capture screenshot of running')
        self.call('assign', channelId=mine, ideaId=self.named('Mine')['id'])
        self.call('assign', channelId=mine, ideaId=self.named('Preview pane')['id'])
        self.assertEqual(self.sidebar()[0]['Mine'], [], 'you made it: it stays, empty')
        # A rules-made workstream you renamed is yours too.
        other = self.typed('Improve the catch up screen')
        catch = self.named('Catch up')
        self.call('save', ideaId=catch['id'], expectedRevision=catch['revision'], name='Catch up view', summary='', state='building', target=None)
        self.call('assign', channelId=other, ideaId=None)
        self.assertEqual(self.sidebar()[0]['Catch up view'], [])
        self.assertEqual([idea['name'] for idea in self.call('list')['archived']], ['Capture'],
                         'only the rules\' own "Capture", which your first move emptied')

    def test_case_6_negative_an_empty_workstream_you_made_still_holds_its_slot(self):
        self.six_of_yours()
        self.new('Capture')
        meters = self.typed('Add usage meters for Codex accounts')
        self.assertEqual(self.organisation(meters)['group'], 'Capture', 'at the cap: the one that moved last, nothing made')
        self.assertEqual(len(self.sidebar()[0]), WORKSTREAM_CAP)


class MigrationTests(FilingFixture):
    def beta7_thread(self, first_message, name=None, locked=False, group=None, later=(), days_ago=0):
        """A thread as beta.7 left it: named after its first message, with the organisation record that version wrote,
        its messages on the timeline (local_projection), and ``source`` on the newest message as beta.7 leaves it."""
        import time
        channel = self.conversation()
        db = self.host.store.db
        with db:
            at = int(time.time() - days_ago * 86400)
            for number, text in enumerate([first_message, *later]):
                message = f'm{number}-' + channel[:8]
                db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text) VALUES (?,?,?,?,?)', (message, channel, 'c' + message, 'local-user', text))
                db.execute('INSERT INTO local_projection VALUES (?,?,?)', (message, None, at + number))
            state = dict(name=name or first_message[:93] + '…', group=group, nameLocked=locked, groupLocked=group is not None, revision=1,
                         reason='Local title extracted from submitted prose. No unambiguous local group match.',
                         source={'kind': 'chat', 'id': message}, suggestion=None, undo=None)
            db.execute('UPDATE conversation_organisation SET document=? WHERE id=?', (json.dumps(state), channel))
            db.execute('UPDATE local_channels SET name=? WHERE id=?', (state['name'], channel))
            db.execute('DELETE FROM organisation_migrations')
        return channel

    def restart(self):
        self.host.close()
        from host import Host
        self.host = Host(self.root)
        self.ideas = type(self.ideas)(self.host.store)

    def test_the_tidy_names_and_files_from_the_first_message_never_the_latest_and_through_the_same_checks(self):
        # 19 Sep review, finding 3: beta.7 leaves source on the newest message, so the tidy named a thread from the last
        # thing you wrote, with none of suggest()'s checks. A second message "Rotate the key sk-proj-AbC123xyz789 ..."
        # became the name "Rotate key sk-proj-AbC123xyz789", filed under a new workstream "Rotate".
        spacing = self.beta7_thread(SPACING, later=['Rotate the key sk-proj-AbC123xyz789 in settings_local.py please'])
        path = self.beta7_thread('See /Users/alice/clients/acme-secret/notes.md for the client notes and tidy them up')
        keyed = self.beta7_thread('Use AKIAIOSFODNN7EXAMPLE for the upload job and tell me when it works')
        self.restart()
        self.assertEqual(self.organisation(spacing)['name'], 'Sidebar spacing between groups')
        self.assertEqual(self.organisation(spacing)['group'], 'Design')
        self.assertTrue(self.organisation(path)['name'].startswith('See /Users/alice'), 'suggest() abstains on an absolute path, so the name stays')
        self.assertEqual(self.organisation(keyed)['name'], 'Upload job')
        groups, loose = self.sidebar()
        self.assertEqual(set(groups), {'Design', 'Upload'}, 'no workstream from the latest message, a key or a path')
        self.assertEqual(loose, [])
        for name in list(groups) + [self.organisation(c)['name'] for c in (spacing, keyed)]:
            for secret in ('sk-proj', 'AKIA', 'Rotate', 'alice', 'acme-secret'):
                self.assertNotIn(secret, name)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM organisation_migrations').fetchone()[0], 1)

    def test_threads_named_after_their_first_message_get_short_names_and_workstreams_once(self):
        live = self.beta7_thread(LIVE)
        sweep = self.beta7_thread(SWEEP)
        chosen = self.beta7_thread(PICKER, name='My picker thread', locked=True)
        short = self.beta7_thread(LOGIN, name='Login')
        tables_before = {row[0] for row in self.host.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.host.close()
        from host import Host
        self.host = Host(self.root)
        self.ideas = type(self.ideas)(self.host.store)
        names = {c: self.organisation(c)['name'] for c in (live, sweep, chosen, short)}
        self.assertEqual(names, {live: 'Orchestrator real time', sweep: 'Full design sweep', chosen: 'My picker thread', short: 'Login'})
        groups, loose = self.sidebar()
        self.assertEqual(loose, [], 'every shared thread from beta.7 is filed')
        self.assertEqual(groups, {'Orchestrator': ['Orchestrator real time'], 'Design': ['Full design sweep'],
                                  'Picker': ['My picker thread'], 'Authentication': ['Login']},
                         'filed from the first message when it is still on record, else from the name')
        state = self.organisation(live)
        self.assertEqual((state['canUndo'], state['source']['kind'], state['filedBy']), (True, 'migration', 'rules'))
        undone = self.host.dispatch('organisation_undo', dict(projectId='p', channelId=live, expectedRevision=state['revision']))
        self.assertTrue(undone['name'].startswith('Ideally I want to see'), 'the old name is one undo away')
        # Once: a second start changes nothing, and the schema gained one small table and lost nothing (beta.7 can open it).
        self.host.close()
        self.host = Host(self.root)
        self.assertEqual(self.organisation(sweep)['revision'], 2)
        tables_after = {row[0] for row in self.host.store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        # routing_decisions is the router's log (thread_router.schema), which a reopened Host creates on its own; it isn't the tidy's.
        self.assertEqual(tables_after - tables_before - {'routing_decisions'}, set())
        self.assertIn('organisation_migrations', tables_after)

    def test_a_fresh_workspace_records_the_tidy_and_has_nothing_to_tidy(self):
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM organisation_migrations').fetchone()[0], 1)
        self.assertIsNone(self.host.tidy_threads())

    def marker(self):
        return self.host.store.db.execute('SELECT count(*) FROM organisation_migrations').fetchone()[0]

    def test_the_tidy_stops_at_its_budget_commits_and_carries_on_at_the_next_start(self):
        # 19 Sep review, finding 2: the tidy ran whole in Host.__init__, 9 s at 900 threads, and the bridge ends the
        # backend when the first request takes 10 s, so past that Dervo couldn't open and every Retry did the same.
        threads = [self.beta7_thread(text) for text in (LIVE, SWEEP, LOGIN)]
        organisation, ideas = self.host.organisation(), self.host.ideas()
        # A budget of nothing does one thread's work per start. The marker lands only once every thread has been seen.
        first = organisation.tidy_once(ideas, budget=0)
        self.assertEqual((len(first['renamed']), len(first['filed']), first['finished']), (1, 1, False))
        self.assertEqual(self.marker(), 0, 'a partial run is not recorded as done')
        self.assertEqual(self.organisation(threads[0])['name'], 'Orchestrator real time')
        self.assertTrue(self.organisation(threads[1])['name'].startswith('I think'), 'the second waits for the next start')
        self.host.store.db.commit()
        second = organisation.tidy_once(ideas, budget=0)
        self.assertEqual(([c for c, _, _ in second['renamed']], second['finished']), ([threads[1]], False), 'it carries on, not over')
        third = organisation.tidy_once(ideas, budget=0)
        self.assertEqual(([c for c, _, _ in third['renamed']], third['finished']), ([threads[2]], True))
        self.assertEqual(self.marker(), 1)
        self.assertIsNone(organisation.tidy_once(ideas, budget=0), 'done is done')
        groups, loose = self.sidebar()
        self.assertEqual((set(groups), loose), ({'Orchestrator', 'Design', 'Authentication'}, []))

    def test_only_a_thread_active_in_the_last_thirty_days_is_filed_but_every_thread_gets_its_short_name(self):
        old = self.beta7_thread(SWEEP, days_ago=31)
        recent = self.beta7_thread(LOGIN, days_ago=29)
        self.restart()
        self.assertEqual((self.organisation(old)['name'], self.organisation(old)['group']), ('Full design sweep', None))
        self.assertEqual((self.organisation(recent)['name'], self.organisation(recent)['group']), ('Fix login', 'Authentication'))
        self.assertEqual(self.sidebar(), ({'Authentication': ['Fix login']}, ['Full design sweep']))
        self.assertEqual(self.marker(), 1)

    def test_a_tidy_or_a_filing_that_fails_for_any_reason_never_stops_dervo(self):
        # Finding 2 as well: only sqlite3.Error was caught, so any other exception in the tidy ended Host() and the
        # app didn't open, and one while filing left a saved request unstarted.
        import contextlib
        import io
        from unittest.mock import patch
        from host import Host
        live = self.beta7_thread(LIVE)
        self.host.close()
        err = io.StringIO()
        with patch('conversation_organisation.Organisation.tidy_once', side_effect=ValueError('a malformed organisation document')), \
                contextlib.redirect_stderr(err):
            self.host = Host(self.root)
        self.ideas = type(self.ideas)(self.host.store)
        self.assertIn('Threads could not be tidied at start: a malformed organisation document', err.getvalue())
        self.assertTrue(self.organisation(live)['name'].startswith('Ideally'), 'the app opened with the thread as it was')
        self.assertEqual(self.marker(), 0, 'the next start tries again')
        with patch.object(self.host.ideas(), 'file_by_rules', side_effect=KeyError('group')), contextlib.redirect_stderr(err):
            channel = self.typed(LOGIN)
        self.assertIn("Conversation organisation could not be saved: 'group'. Request submission continues.", err.getvalue())
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM messages WHERE thread_id=?', (channel,)).fetchone()[0], 1, 'the request is saved')
        self.assertEqual(self.organisation(channel)['name'], 'New conversation')


class CorrectionTests(FilingFixture):
    """dd #10, 23 Sep: Bryce still sorts threads by hand. A move of a thread Dervo filed is a correction, and the next
    thread like it goes where he put that one."""

    def corrections(self, profile='a'):
        return self.ideas.corrections(dict(project_id='p', profile_id=profile))

    def move(self, channel, workstream, actor=None):
        idea = self.named(workstream)
        if actor:
            return self.ideas.dispatch('ideas_assign', dict(self.scope, channelId=channel, ideaId=idea['id']), actor)
        return self.call('assign', channelId=channel, ideaId=idea['id'])

    def test_moving_a_rules_filing_is_a_correction_and_the_next_one_follows_it(self):
        self.new('Website')
        first = self.typed('Add scroll parallax to the marketing pages')
        guessed = self.group(first)
        self.assertNotEqual(guessed, 'Website', 'the rules miss: nothing in the request says website')
        self.assertEqual(self.organisation(first).get('filedBy'), 'rules')
        self.move(first, 'Website')
        fixes = self.corrections()
        self.assertEqual([(f['by'], f['before']['name'], f['after']['name']) for f in fixes], [('rules', guessed, 'Website')])
        self.assertEqual(fixes[0]['thread']['name'], self.organisation(first)['name'], 'the short name is kept, never the message')
        second = self.typed('Make the parallax on the pricing page gentler')
        self.assertEqual(self.group(second), 'Website')
        self.assertEqual(self.host.ideas().last_filing, 'your correction', 'placed by the correction, before any topic guess')

    def test_moving_a_home_filing_is_a_correction(self):
        self.new('Website')
        channel = self.from_home('Parallax lab', 'Try a parallax lab', workstream='Effects')
        self.move(channel, 'Website')
        self.assertEqual([(f['by'], f['before']['name']) for f in self.corrections()], [('home', 'Effects')])

    def test_your_own_filing_moved_again_is_no_correction(self):
        self.new('Website')
        self.new('Effects')
        channel = self.typed('Add scroll parallax to the marketing pages')
        self.move(channel, 'Effects')
        self.move(channel, 'Website')
        self.assertEqual([f['after']['name'] for f in self.corrections()], ['Effects'],
                         "only the first move, away from the rules' choice, is a correction")

    def test_home_moving_a_thread_and_taking_one_out_are_no_corrections(self):
        from conversation_organisation import HOME_ACTOR
        self.new('Website')
        channel = self.typed('Add scroll parallax to the marketing pages')
        self.move(channel, 'Website', actor=HOME_ACTOR)
        self.assertEqual(self.corrections(), [], "Home's own move is Dervo's filing, not yours")
        self.call('assign', channelId=channel, ideaId=None)
        self.assertEqual(self.corrections(), [], 'taking it out names nowhere it belongs')

    def test_corrections_stay_in_their_account_and_follow_a_rename(self):
        self.new('Website')
        channel = self.typed('Add scroll parallax to the marketing pages')
        self.move(channel, 'Website')
        self.assertEqual(self.corrections(profile='b'), [])
        site = self.named('Website')
        self.call('save', ideaId=site['id'], expectedRevision=site['revision'], name='Marketing site', summary='', state='building', target=None)
        self.assertEqual([f['after']['name'] for f in self.corrections()], ['Marketing site'])


if __name__ == '__main__':
    unittest.main()
