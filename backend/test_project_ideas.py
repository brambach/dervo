"""Disposable project ideas store. No provider, no host wiring, no conversation text read."""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from dervo_connections import Rejected
from host import Host, ACTOR
from project_ideas import ProjectIdeas, NAME_LIMIT, STATES


class IdeaFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.host = Host(self.root)
        self.addCleanup(lambda: self.host.close())
        for profile in ('a', 'b'):
            self.host.store.add_profile(profile, 'codex', self.root / profile, profile + '@example.test', 'personal')
        self.host.store.add_project('p', self.root, 'a', 'fixture')
        self.host.store.add_project('other', self.root, 'b', 'fixture')
        self.ideas = ProjectIdeas(self.host.store)
        self.scope = dict(projectId='p', profileId='a')

    # ---------------------------------------------------------------- helpers
    def call(self, operation, **args):
        return self.ideas.dispatch('ideas_' + operation, {**self.scope, **args}, ACTOR)

    def new(self, name='Releases', summary='', state='planned', target=None):
        return self.call('save', ideaId=None, expectedRevision=0, name=name, summary=summary, state=state, target=target)

    def named(self, name, listing=None):
        listing = listing or self.call('list')
        return next(idea for idea in listing['ideas'] + listing['archived'] if idea['name'] == name)

    def conversation(self, name='', private=False, project='p'):
        return self.host.dispatch('create_conversation', dict(
            projectId=project, name=name, private=private, **({'automaticName': True} if not name else {})))['id']

    def group(self, channel_id, project='p'):
        return self.host.dispatch('organisation_get', dict(projectId=project, channelId=channel_id))['group']


class IdeaTests(IdeaFixture):
    def test_an_idea_is_saved_on_the_project_and_survives_a_restart(self):
        listing = self.new('Releases', 'The screen that says what this beta holds.', 'building', '0.4.0')
        self.assertEqual([idea['name'] for idea in listing['ideas']], ['Releases'])
        idea = listing['ideas'][0]
        self.assertEqual((idea['state'], idea['target'], idea['revision']), ('building', '0.4.0', 1))
        self.assertEqual(idea['conversations'], [])
        self.assertEqual(listing['states'], list(STATES))
        self.host.close()
        self.host = Host(self.root)
        self.ideas = ProjectIdeas(self.host.store)
        self.assertEqual(self.call('list')['ideas'], [idea], 'an idea belongs to the project, not to this process')
        self.assertEqual(self.call('list')['ideas'], self.ideas.dispatch('ideas_list', dict(self.scope), ACTOR)['ideas'])

    def test_a_thread_moved_to_another_personal_account_stays_in_the_projects_listing(self):
        """Phase 2, item 4 (H1): Move to Codex or the account picker took a thread off Home, its model and workstreams."""
        idea = self.named('Releases', self.new('Releases'))
        moved, kept = self.conversation('Moved one'), self.conversation('Work one')
        self.call('assign', channelId=moved, ideaId=idea['id'])
        self.host.store.add_profile('w', 'codex', self.root / 'w', 'w@example.test', 'work')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='b' WHERE id=?", (moved,))
            self.host.store.db.execute("UPDATE threads SET profile_id='w' WHERE id=?", (kept,))
        rows = {row['id']: row['idea_id'] for row in self.ideas.conversations(dict(project_id='p', profile_id='a'))}
        self.assertEqual(rows.get(moved), idea['id'], 'still listed, still in its workstream')
        self.assertNotIn(kept, rows, 'negative control: a thread on a work account stays out')
        self.assertEqual([c['id'] for c in self.named('Releases')['conversations']], [moved])
        # The project's account changed: the thread is still listed, and an old account's workstream isn't claimed.
        rows = {row['id']: row['idea_id'] for row in self.ideas.conversations(dict(project_id='p', profile_id='b'))}
        self.assertEqual(rows.get(moved, 'missing'), None)

    def test_ideas_are_scoped_to_their_project_and_account(self):
        self.new('Releases')
        self.assertEqual(self.ideas.dispatch('ideas_list', dict(projectId='other', profileId='b'), ACTOR)['ideas'], [])
        for scope in (dict(projectId='p', profileId='b'), dict(projectId='missing', profileId='a')):
            with self.assertRaises(Rejected):
                self.ideas.dispatch('ideas_list', scope, ACTOR)
        with self.host.store.db:
            self.host.store.db.execute("UPDATE profiles SET category='work' WHERE id='a'")
        with self.assertRaises(Rejected):
            self.call('list')

    def test_the_idea_is_the_group_of_the_conversations_in_it(self):
        self.new('Releases')
        idea = self.call('list')['ideas'][0]
        channel = self.conversation('Beta screen')
        self.assertIsNone(self.group(channel))
        listing = self.call('assign', channelId=channel, ideaId=idea['id'])
        self.assertEqual(listing['ideas'][0]['conversations'], [dict(id=channel, name='Beta screen')])
        self.assertEqual(listing['unassigned'], [])
        self.assertEqual(self.group(channel), 'Releases', 'the sidebar group comes from the idea')
        state = self.host.dispatch('organisation_get', dict(projectId='p', channelId=channel))
        self.assertTrue(state['groupLocked'], 'a conversation in an idea is not regrouped by the topic guess')
        self.assertTrue(state['canUndo'], 'the existing undo still covers the change')
        # Assigning twice writes nothing further, and the organisation record stays where it was.
        self.assertEqual(self.call('assign', channelId=channel, ideaId=idea['id']), listing)
        self.assertEqual(self.host.dispatch('organisation_get', dict(projectId='p', channelId=channel)), state)

    def test_a_renamed_idea_regroups_its_conversations_and_keeps_a_hand_picked_group(self):
        self.new('Releases')
        idea = self.call('list')['ideas'][0]
        followed, hand_picked = self.conversation('Beta screen'), self.conversation('Mockup')
        for channel in (followed, hand_picked):
            self.call('assign', channelId=channel, ideaId=idea['id'])
        state = self.host.dispatch('organisation_get', dict(projectId='p', channelId=hand_picked))
        self.host.dispatch('organisation_set', dict(projectId='p', channelId=hand_picked, expectedRevision=state['revision'],
                                                    name=state['name'], group='My own heading', nameLocked=state['nameLocked'],
                                                    groupLocked=True))
        self.call('save', ideaId=idea['id'], expectedRevision=1, name='Releases page', summary='', state='planned', target=None)
        self.assertEqual(self.group(followed), 'Releases page', 'the name is a mirror, the membership is the reference')
        self.assertEqual(self.group(hand_picked), 'My own heading', 'a group the user chose is never overwritten')
        self.assertEqual(sorted(c['id'] for c in self.call('list')['ideas'][0]['conversations']),
                         sorted([followed, hand_picked]), 'both are still in the idea')

    def test_leaving_an_idea_clears_the_group(self):
        self.new('Releases')
        first = self.call('list')['ideas'][0]
        self.new('Orchestrator')
        second = self.named('Orchestrator')
        channel = self.conversation('Beta screen')
        self.call('assign', channelId=channel, ideaId=first['id'])
        listing = self.call('assign', channelId=channel, ideaId=second['id'])
        self.assertEqual(self.group(channel), 'Orchestrator', 'a conversation is in one idea at a time')
        self.assertEqual(self.named('Releases', listing)['conversations'], [])
        listing = self.call('assign', channelId=channel, ideaId=None)
        self.assertIsNone(self.group(channel))
        self.assertEqual(listing['unassigned'], [dict(id=channel, name='Beta screen')])

    def test_archiving_takes_the_group_away_and_restoring_gives_it_back(self):
        self.new('Releases')
        idea = self.call('list')['ideas'][0]
        channel = self.conversation('Beta screen')
        self.call('assign', channelId=channel, ideaId=idea['id'])
        listing = self.call('archive', ideaId=idea['id'], expectedRevision=1)
        self.assertEqual(listing['ideas'], [])
        self.assertEqual([i['name'] for i in listing['archived']], ['Releases'])
        self.assertIsNone(self.group(channel), 'an archived idea is not a heading in the sidebar')
        self.assertEqual(listing['archived'][0]['conversations'], [dict(id=channel, name='Beta screen')],
                         'membership is kept, so a restore can undo this')
        with self.assertRaises(Rejected):
            self.call('assign', channelId=self.conversation('Another'), ideaId=idea['id'])
        with self.assertRaises(Rejected):
            self.call('archive', ideaId=idea['id'], expectedRevision=2)
        self.call('restore', ideaId=idea['id'], expectedRevision=2)
        self.assertEqual(self.group(channel), 'Releases')
        self.assertEqual([i['name'] for i in self.call('list')['ideas']], ['Releases'])

    def test_a_stale_revision_and_a_duplicate_name_are_refused(self):
        self.new('Releases')
        idea = self.call('list')['ideas'][0]
        with self.assertRaises(Rejected):
            self.call('save', ideaId=idea['id'], expectedRevision=0, name='Renamed', summary='', state='planned', target=None)
        with self.assertRaises(Rejected):
            self.new('releases')
        self.new('Orchestrator')
        with self.assertRaises(Rejected):
            self.call('save', ideaId=self.named('Orchestrator')['id'], expectedRevision=1, name='Releases  ',
                      summary='', state='planned', target=None)
        self.call('archive', ideaId=idea['id'], expectedRevision=1)
        self.new('Releases')  # an archived name is free again
        with self.assertRaises(Rejected):
            self.call('restore', ideaId=idea['id'], expectedRevision=2)

    def test_field_checks(self):
        for bad in ({'name': ''}, {'name': ' '}, {'name': 'x' * (NAME_LIMIT + 1)}, {'name': 'Line\nbreak'},
                    {'name': None}, {'state': 'shipped'}, {'state': None}, {'summary': 5}, {'target': ''},
                    {'target': 'v' * 33}):
            with self.assertRaises(Rejected, msg=bad):
                self.new(**bad)
        with self.assertRaises(Rejected):
            self.ideas.dispatch('ideas_list', dict(self.scope, extra=1), ACTOR)
        listing = self.new('Releases  ', summary='  Direction.\nOver two lines.  ', target=' 0.4.0 ')
        self.assertEqual(listing['ideas'][0]['name'], 'Releases')
        self.assertEqual(listing['ideas'][0]['summary'], 'Direction.\nOver two lines.')
        self.assertEqual(listing['ideas'][0]['target'], '0.4.0')
        self.assertEqual(self.new('Second', summary=None)['ideas'][1]['summary'], '')

    def test_a_workstream_the_rules_make_meets_the_same_name_check_as_one_you_type(self):
        # 19 Sep review, finding 5: insert_idea skipped name(), so a title an agent chose could head a workstream with
        # characters organisation_set refuses in a name you type.
        scope = dict(project_id='p', profile_id='a')
        with self.host.store.db:
            for bad in ('x\x00y', 'a\x1b[31mred\x1b[0m', '', '   ', 'n' * (NAME_LIMIT + 1)):
                with self.assertRaises(Rejected, msg=repr(bad)):
                    self.ideas.insert_idea(scope, bad, ACTOR)
            made = self.ideas.insert_idea(scope, '  Connectors ', ACTOR)
            with self.assertRaises(Rejected):
                self.ideas.insert_idea(scope, 'connectors', ACTOR)
        self.assertEqual(made['name'], 'Connectors')
        self.assertEqual([idea['name'] for idea in self.call('list')['ideas']], ['Connectors'])
        # Filing by rules treats a refused name as no new workstream, not as a failure of the filing.
        channel = self.conversation()
        binding = dict(id=channel, project_id='p', profile_id='a')
        with patch('thread_names.workstream_name', return_value='bad\x00name'):
            with self.host.store.db:
                self.assertEqual(self.ideas.file_by_rules(binding, 'Tighten the composer padding', 'Composer padding'), 'Connectors',
                                 'the most recent workstream takes it instead')
        self.assertEqual([idea['name'] for idea in self.call('list')['ideas']], ['Connectors'])

    def test_a_private_conversation_stays_out_of_ideas(self):
        self.new('Releases')
        idea = self.call('list')['ideas'][0]
        private = self.conversation(private=True)
        with self.assertRaises(Rejected):
            self.call('assign', channelId=private, ideaId=idea['id'])
        self.assertEqual(self.call('list')['ideas'][0]['conversations'], [])
        self.assertEqual(self.call('list')['unassigned'], [], 'a private conversation is not offered either')
        for channel in ('missing', self.conversation(project='other')):
            with self.assertRaises(Rejected):
                self.call('assign', channelId=channel, ideaId=idea['id'])

    def test_the_topic_a_request_names_becomes_its_workstream(self):
        # Before beta.8 the nine-topic guess only set the sidebar group. Now the topic is the workstream the thread is filed
        # in, so nothing sits in "Not filed yet"; a hand move to another idea still wins.
        channel = self.conversation()
        self.host.dispatch('queue', dict(projectId='p', channelId=channel, clientId='one',
                                         text='Fix login authentication', parentId=None))
        self.assertEqual(self.group(channel), 'Authentication')
        listing = self.call('list')
        self.assertEqual(listing['unassigned'], [])
        self.assertEqual([(idea['name'], [c['name'] for c in idea['conversations']]) for idea in listing['ideas']],
                         [('Authentication', ['Login authentication'])])
        self.new('Releases')
        self.call('assign', channelId=channel, ideaId=self.named('Releases')['id'])
        self.assertEqual(self.group(channel), 'Releases', 'an explicit idea wins over the rules')
        self.assertFalse(self.ideas.filed_by_rules(channel), 'and Home may not move it again')

if __name__ == '__main__':
    unittest.main()


class HostWiringTests(IdeaFixture):
    """The module was complete and unreachable until 18 Sep. These pin the route from the renderer to it."""

    def test_the_host_dispatches_every_idea_operation_and_refuses_anything_else_idea_shaped(self):
        listing = self.host.dispatch('ideas_list', dict(self.scope))
        self.assertEqual(listing['ideas'], [])
        self.assertEqual(listing['states'], list(STATES))
        saved = self.host.dispatch('ideas_save', dict(self.scope, ideaId=None, expectedRevision=0, name='Orchestrator',
                                                      summary='The room and the board.', state='building', target='0.1.0-beta.4'))
        idea = self.named('Orchestrator', saved)
        channel = self.conversation('Bridge and dispatch')
        assigned = self.host.dispatch('ideas_assign', dict(self.scope, channelId=channel, ideaId=idea['id']))
        self.assertEqual([entry['name'] for entry in self.named('Orchestrator', assigned)['conversations']], ['Bridge and dispatch'])
        self.assertEqual(self.group(channel), 'Orchestrator', 'the idea name is the sidebar group')
        current = self.named('Orchestrator', assigned)['revision']
        archived = self.host.dispatch('ideas_archive', dict(self.scope, ideaId=idea['id'], expectedRevision=current))
        self.assertEqual([entry['name'] for entry in archived['archived']], ['Orchestrator'])
        restored = self.host.dispatch('ideas_restore', dict(self.scope, ideaId=idea['id'],
                                                            expectedRevision=self.named('Orchestrator', archived)['revision']))
        self.assertEqual([entry['name'] for entry in restored['ideas']], ['Orchestrator'])
        # Negative control: the host routes on the exact operation name and on the exact field set, nothing near it.
        for operation in ('ideas_delete', 'idea_list', 'ideas_assign_all'):
            with self.assertRaises(Rejected):
                self.host.dispatch(operation, dict(self.scope))
        with self.assertRaises(Rejected):
            self.host.dispatch('ideas_list', dict(self.scope, extra=1))

    def test_the_route_carries_the_project_scope_and_not_another_projects_ideas(self):
        self.host.dispatch('ideas_save', dict(self.scope, ideaId=None, expectedRevision=0, name='Mine',
                                              summary='', state='planned', target=None))
        other = self.host.dispatch('ideas_list', dict(projectId='other', profileId='b'))
        self.assertEqual(other['ideas'], [], 'ideas are project and account scoped through the host, not global')
        with self.assertRaises(Rejected):
            self.host.dispatch('ideas_list', dict(projectId='p', profileId='b'))


class RoutingTests(IdeaFixture):
    """18 Sep: the room offers an existing conversation for what you type, and suggests an idea for a conversation
    filed under none. Both read only Dervo's own words about a conversation, never its messages."""

    def settled(self, channel, summary):
        import conversation_activity as ca
        ca.schema(self.host.store.db)
        task = {'id': 'task-' + channel, 'state': 'completed', 'context': {'thread_id': channel, 'project_id': 'p'},
                'runs': [], 'tokens': [], 'messages': []}
        with patch.object(ca, 'summary_of', lambda _task: summary), self.host.store.db:
            ca.record(self.host.store.db, task)

    def route(self, text, idea=None):
        return self.host.dispatch('ideas_route', dict(self.scope, ideaId=idea, text=text))

    def test_your_words_go_to_the_conversation_that_already_owns_that_work(self):
        idea = self.named('Orchestrator', self.new('Orchestrator', 'The room, routing and the board.'))
        bridge = self.conversation('Bridge and dispatch for ideas')
        board = self.conversation('Project room board layout')
        for channel in (bridge, board):
            self.host.dispatch('ideas_assign', dict(self.scope, channelId=channel, ideaId=idea['id']))
        self.settled(board, 'Changed ProjectRoom.tsx and its cards. Checks passed.')
        found = self.route('The board cards in the project room need more spacing', idea['id'])
        self.assertEqual(found['conversation'], dict(id=board, name='Project room board layout'))
        self.assertIn('board', found['matched'])
        # With no idea chosen the whole project is searched, and the same conversation still wins.
        self.assertEqual(self.route('project room board cards')['conversation']['id'], board)

    def test_a_weak_or_tied_match_offers_nothing_so_the_room_starts_a_new_conversation(self):
        self.conversation('Release notes')
        self.conversation('Sidebar spacing')
        # One common word is not enough to add your words to a conversation that was doing something else.
        self.assertIsNone(self.route('Fix the sidebar colours and the dock icon')['conversation'])
        # Two conversations fitting equally well is a coin toss, not a route.
        self.conversation('Sidebar badge')
        self.assertIsNone(self.route('sidebar')['conversation'])
        self.assertIsNone(self.route('')['conversation'])
        with self.assertRaises(Rejected):
            self.route('x' * 4001)

    def test_routing_reads_what_dervo_recorded_and_never_the_messages(self):
        channel = self.conversation('Payments')
        # Words that only ever appeared inside a message can't pull a route: Dervo never reads them here.
        self.assertIsNone(self.route('refund webhook signature retries')['conversation'])
        self.settled(channel, 'Added refund webhook signature checks. Checks passed.')
        self.assertEqual(self.route('refund webhook signature retries')['conversation']['id'], channel)

    def test_a_conversation_in_no_idea_gets_one_suggested_idea_or_none(self):
        releases = self.named('Releases', self.new('Releases', 'What each beta holds and what ships next.'))
        self.new('Orchestrator', 'The room and the board.')
        notes = self.conversation('Beta notes for testers')
        self.settled(notes, 'Wrote what each beta holds for the release page.')
        vague = self.conversation('Misc')
        listing = self.call('list')
        self.assertEqual(listing['suggestions'].get(notes), releases['id'])
        self.assertNotIn(vague, listing['suggestions'], 'nothing in common suggests nothing')
        # Filed conversations and archived ideas are never suggested.
        self.host.dispatch('ideas_assign', dict(self.scope, channelId=notes, ideaId=releases['id']))
        self.assertNotIn(notes, self.call('list')['suggestions'])


class SteeringTests(IdeaFixture):
    """18 Sep: conversations in one idea hear what the others are doing, at their next request, from Dervo's records.
    Pull, not push: nothing is sent into another conversation, and nothing is repeated to a session that has it."""

    def setUp(self):
        super().setUp()
        import json as _json
        db = self.host.store.db
        db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        outer = self
        class Coding:
            store = outer.host.store
            def save(self, task):
                with db:
                    db.execute('INSERT INTO coding_tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document', (task['id'], _json.dumps(task)))
                return task
            def read(self, task_id, ctx=None):
                return _json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()[0])
        self.coding = Coding()
        self.idea_id = self.named('Orchestrator', self.new('Orchestrator', 'The room and the board.', 'building', '0.1.0-beta.4'))['id']

    def join(self, name):
        channel = self.conversation(name)
        self.host.dispatch('ideas_assign', dict(self.scope, channelId=channel, ideaId=self.idea_id))
        return channel

    def task(self, id, channel, *, state='working', asked=None, written=(), continues=None, **extra):
        import time as clock
        record = dict(id=id, origin='conversation', state=state, context=dict(project_id='p', thread_id=channel, actor=ACTOR),
                      binding=dict(provider='claude', profile_id='a', visibility='shared'),
                      messages=[dict(role='user', text='do the thing', created_at=asked or int(clock.time()))],
                      runs=[dict(id='r-' + id, state='running', written=list(written))], tokens=[], **extra)
        if continues:
            record['continuity'] = dict(state='continues', from_task_id=continues)
        return self.coding.save(record)

    def block(self, record):
        import project_ideas
        return project_ideas.context_block(self.coding, record)

    def test_a_conversation_hears_its_idea_and_what_the_others_in_it_are_editing(self):
        me, other = self.join('Room composer'), self.join('Bridge and dispatch')
        self.task('o1', other, written=['src/bridge.rs', 'backend/host.py'])
        text = self.block(self.task('m1', me))
        self.assertIn('part of the idea "Orchestrator" (building, aimed at 0.1.0-beta.4)', text)
        self.assertIn("What it's for: The room and the board.", text)
        self.assertIn('#Bridge and dispatch: working now', text)
        self.assertIn('src/bridge.rs, backend/host.py', text)
        self.assertIn('dervo_check_chat', text)
        self.assertNotIn('#Room composer', text, 'a conversation is not told about itself')

    def test_a_continued_session_hears_it_again_only_when_something_changed(self):
        me, other = self.join('Room composer'), self.join('Bridge and dispatch')
        self.task('o1', other, written=['src/bridge.rs'])
        self.block(self.task('m1', me))
        self.assertEqual(self.block(self.task('m2', me, continues='m1')), '', 'nothing new since the session last heard it')
        self.task('o1', other, state='completed', written=['src/bridge.rs'])
        self.assertIn('#Bridge and dispatch: finished', self.block(self.task('m3', me, continues='m2')))

    def test_a_siblings_hold_reaches_the_conversation_that_carries_on(self):
        # Night 1, finding 3: what the other conversation was asked is the user's instruction to it, not agreement.
        me, plan = self.join('Config sections'), self.join('parser')
        held = self.task('a2', plan, state='answered')
        held['messages'] = [dict(role='user', text="Don't build it yet, just tell me how you'd do it.", created_at=held['messages'][0]['created_at']),
                            dict(role='assistant', text='Nested dicts. Open questions: case? repeated sections?')]
        self.coding.save(held)
        text = self.block(self.task('m1', me))
        self.assertIn('The user held work back there: "Don\'t build it yet, just tell me how you\'d do it."', text)
        self.assertIn('questions they haven’t answered yet', text)
        self.assertIn('a hold there ("don’t build it yet", "just tell me how") covers you too', text)
        # Negative control: a sibling asked plainly carries no hold line.
        plain = self.join('Docs')
        self.task('d1', plain, state='answered')
        self.coding.save(dict(self.task('a3', plan, state='answered'), messages=[dict(role='user', text='Add the headers.', created_at=int(__import__('time').time()))]))
        text = self.block(self.task('m2', me))
        self.assertNotIn('held work back', text)

    def test_nothing_is_said_outside_an_idea_to_helpers_or_about_old_and_private_work(self):
        import time as clock
        loose = self.conversation('Not filed')
        self.assertEqual(self.block(self.task('l1', loose)), '')
        me, stale = self.join('Room composer'), self.join('Old spike')
        self.task('s1', stale, state='completed', asked=int(clock.time()) - 9 * 86400)
        self.assertIn('No other conversation in it is active right now.', self.block(self.task('m1', me)))
        self.assertEqual(self.block(self.task('h1', me, role='reviewer')), '', 'a reviewer keeps its own packet only')
        private = self.task('p1', me); private['binding']['visibility'] = 'private'
        self.assertEqual(self.block(private), '')


class OverlapTests(SteeringTests):
    def test_two_running_conversations_editing_one_file_show_up_and_finished_ones_do_not(self):
        import conversation_activity as ca
        one, two, three = self.join('Room composer'), self.join('Bridge and dispatch'), self.join('Docs')
        for id, channel, files in (('a', one, ['backend/host.py', 'ProjectRoom.tsx']), ('b', two, ['backend/host.py', 'src/bridge.rs']),
                                   ('c', three, ['README.md'])):
            record = self.task(id, channel, written=files)
            with self.host.store.db:
                ca.record(self.host.store.db, record)
        overlaps = self.call('list')['overlaps']
        self.assertEqual(overlaps, [dict(idea_id=self.idea_id, file='backend/host.py', conversations=['Bridge and dispatch', 'Room composer'])])
        finished = self.task('b', two, state='completed', written=['backend/host.py'])
        with self.host.store.db:
            ca.record(self.host.store.db, finished)
        self.assertEqual(self.call('list')['overlaps'], [], 'a finished run is not a collision')

    # the steering tests don't need to run twice under this class
    test_a_conversation_hears_its_idea_and_what_the_others_in_it_are_editing = None
    test_a_continued_session_hears_it_again_only_when_something_changed = None
    test_nothing_is_said_outside_an_idea_to_helpers_or_about_old_and_private_work = None


class ItemTests(IdeaFixture):
    def test_admin_report_view_includes_unmarked_reports_across_personal_accounts(self):
        from types import SimpleNamespace
        first = self.add(product='dervo')['items'][0]
        second = self.add(name='Unmarked report', projectId='other', profileId='b')['items'][0]
        found = self.call('product_list')['items']
        self.assertEqual({item['id'] for item in found}, {first['id'], second['id']})
        sources = {item['id']: item for item in found}
        self.assertEqual(sources[second['id']]['source_profile_id'], 'b')
        self.assertEqual(sources[first['id']]['source_project_id'], 'p')
        self.assertEqual({item['id'] for item in self.ideas.dispatch('ideas_product_list', {'projectId':'other','profileId':'b'}, ACTOR)['items']}, {first['id'],second['id']})
        self.call('item_product', itemId=first['id'], expectedRevision=1, product='project')
        self.assertEqual(len(self.call('product_list')['items']), 2, 'removing the Dervo mark must not hide a report')
        with self.assertRaises(Rejected):
            self.call('item_product', itemId=first['id'], expectedRevision=1, product='dervo')
        self.ideas.host = SimpleNamespace(account_session=SimpleNamespace(state='signed_in', membership={'is_admin':False}))
        for op, args in [('product_list', {}), ('item_product', dict(itemId=first['id'],expectedRevision=2,product='dervo'))]:
            with self.assertRaises(Rejected): self.call(op, **args)
        with self.assertRaises(Rejected): self.add(name='Not an admin', product='dervo')
        self.ideas.host = None
        with self.host.store.db as db:
            db.execute("UPDATE profiles SET category='work' WHERE id='b'")
        self.assertEqual([item['id'] for item in self.call('product_list')['items']], [first['id']])
        self.call('archive', ideaId=first['id'], expectedRevision=2)
        self.assertEqual(self.call('product_list')['items'], [])

    def test_long_report_is_kept_in_the_manual_fix_request_and_home_worker_brief(self):
        from project_ideas import work_request, report_notes
        from project_orchestrator import Orchestrator
        words = 'The task was already finished. ' * 90 + '\n6. Never say reconcile without showing the action.'
        item = self.add(text=words)['items'][0]
        self.assertEqual(item['notes'][0]['text'], words)
        self.assertIn(words, work_request(item))
        self.assertIn(words, Orchestrator.item_brief([item]))
        crowded = dict(item, notes=item['notes'] + [dict(text='update ' * 1000) for _ in range(20)])
        selected = report_notes(crowded)
        self.assertEqual(selected[0], words)
        self.assertIn("didn't fit", selected[-1])
        for budget in (0, 100, 512, 6000, 24000):
            brief = Orchestrator.item_brief([crowded] * 3, byte_limit=budget)
            self.assertLessEqual(len(brief.encode('utf-8')), budget)
        self.assertLess(len(work_request(crowded).encode('utf-8')), 32768)

    """The Bugs and ideas list: numbered items with notes, one list per project, never a workstream."""

    def add(self, kind='bug', name='Send arrow sits left of centre', text='From a screenshot', **scope):
        return self.ideas.dispatch('ideas_item_add', {**self.scope, **scope, 'kind': kind, 'name': name, 'text': text}, ACTOR)

    def item(self, name, listing=None):
        listing = listing or self.call('item_list')
        return next(item for item in listing['items'] + listing['archived'] if item['name'] == name)

    def test_a_bug_gets_the_next_number_and_keeps_it(self):
        self.add('bug', 'Send arrow sits left of centre')
        listing = self.add('idea', 'Voice chat with the Home orchestrator')
        bug, idea = self.item('Send arrow sits left of centre', listing), self.item('Voice chat with the Home orchestrator', listing)
        self.assertEqual((bug['number'], idea['number']), (1, 2))
        self.assertEqual((bug['kind'], idea['kind']), ('bug', 'idea'), 'the document alone says what it is')
        self.assertEqual(listing['next_number'], 3)
        archived = self.call('archive', ideaId=bug['id'], expectedRevision=bug['revision'])
        self.assertEqual([item['name'] for item in archived['archived']], ['Send arrow sits left of centre'])
        self.assertEqual(self.item('Progress bar randomly disappeared', self.add('bug', 'Progress bar randomly disappeared'))['number'], 3,
                         'an archived number stays used')
        # Even a row that is gone can't free its number: the counter only moves up.
        with self.host.store.db as db:
            db.execute('DELETE FROM project_ideas WHERE id=?', (idea['id'],))
        self.assertEqual(self.item('Old Home cards', self.add('bug', 'Old Home cards'))['number'], 4)
        restored = self.call('restore', ideaId=bug['id'], expectedRevision=self.item('Send arrow sits left of centre')['revision'])
        self.assertEqual(self.item('Send arrow sits left of centre', restored)['number'], 1, 'a restored item keeps its number')
        # Negative control: a workstream saved through ideas_save has no number and doesn't move the counter.
        workstreams = self.new('Releases')
        self.assertNotIn('number', workstreams['ideas'][0])
        self.assertEqual(self.call('item_list')['next_number'], 5)
        row = self.host.store.db.execute("SELECT kind, document FROM project_ideas WHERE project_id='p' AND kind='workstream'").fetchone()
        self.assertEqual((row['kind'], json.loads(row['document'])['kind']), ('workstream', 'workstream'))

    def test_empty_text_adds_the_item_with_no_first_note(self):
        listing = self.add('bug', 'Tray covers the send button', text='')
        self.assertEqual(self.item('Tray covers the send button', listing)['notes'], [])
        listing = self.add('idea', 'Paste a screenshot into Home', text='   ')
        self.assertEqual(self.item('Paste a screenshot into Home', listing)['notes'], [], 'spaces only is empty too')
        # Negative controls: real text is still the first note, and text that isn't text is still refused.
        listing = self.add('bug', 'Drop zone flickers', text='  On the second drag  ')
        self.assertEqual([note['text'] for note in self.item('Drop zone flickers', listing)['notes']], ['On the second drag'])
        for bad in (None, 5, 'line\x00break', 'x' * 8001):
            with self.assertRaises(Rejected):
                self.add('bug', 'Refused ' + str(len(str(bad))), text=bad)
        self.assertEqual(self.call('item_list')['next_number'], 4, 'a refused add takes no number')

    def test_items_are_one_list_per_project_whichever_account(self):
        self.add('bug', 'Send arrow sits left of centre')
        # The project moves to account b (Store.bind_project: one account per project at a time). The list comes along.
        self.host.store.bind_project('p', 'b', 'fixture')
        seen = self.ideas.dispatch('ideas_item_list', dict(projectId='p', profileId='b'), ACTOR)
        self.assertEqual([item['name'] for item in seen['items']], ['Send arrow sits left of centre'])
        added = self.add('idea', 'Voice chat with the Home orchestrator', profileId='b')
        self.assertEqual([item['number'] for item in added['items']], [1, 2], 'one counter per project, not per account')
        # The account must still own the project to read it, as for every saved record of a project.
        with self.assertRaises(Rejected):
            self.ideas.dispatch('ideas_item_list', dict(projectId='p', profileId='a'), ACTOR)
        # Negative control: another project sees nothing, and workstreams keep their account scope.
        self.assertEqual(self.ideas.dispatch('ideas_item_list', dict(projectId='other', profileId='b'), ACTOR)['items'], [])
        self.assertEqual(self.ideas.items('other'), [])
        self.assertEqual(self.ideas.dispatch('ideas_list', dict(projectId='p', profileId='b'), ACTOR)['ideas'], [])

    def test_items_never_count_toward_the_workstream_cap_or_the_sidebar(self):
        from project_ideas import WORKSTREAM_CAP, sync_membership
        from test_thread_filing import KNOWLEDGE, FilingFixture
        filing = FilingFixture('run')
        filing.setUp()
        try:
            for n in range(WORKSTREAM_CAP - 1):
                filing.new(f'Stream {n}')
            for n in range(5):
                filing.ideas.dispatch('ideas_item_add', {**filing.scope, 'kind': 'bug', 'text': 'noted',
                                                         'name': f'Knowledge page scattered {n}'}, ACTOR)
            listing = filing.call('list')
            self.assertEqual(len(listing['ideas']), WORKSTREAM_CAP - 1)
            self.assertEqual(set(listing), {'ideas', 'overlaps', 'archived', 'unassigned', 'states', 'suggestions', 'limits', 'detail'},
                             'ideas_list keeps its shape')
            self.assertFalse(any('number' in idea for idea in listing['ideas'] + listing['archived']))
            # Six workstreams and five bugs: the rules may still make a seventh workstream, so bugs don't count.
            first = filing.typed(KNOWLEDGE)
            groups, loose = filing.sidebar()
            self.assertEqual(len(groups), WORKSTREAM_CAP)
            self.assertNotIn('Knowledge page scattered 0', groups, 'a bug with the same words never takes a thread')
            self.assertEqual(loose, [])
            # Negative control: at seven workstreams the rules make no eighth, as before. The next thread joins the one
            # that moved last, never a bug.
            filing.typed('Tighten the sidebar spacing between groups and make the selected row frosted')
            groups, loose = filing.sidebar()
            self.assertEqual(len(groups), WORKSTREAM_CAP)
            self.assertEqual(loose, [])
            # An undo that restores a bug's name as a group doesn't join the thread to the bug.
            with filing.host.store.db as db:
                db.execute('DELETE FROM project_idea_members WHERE channel_id=?', (first,))
                sync_membership(db, first, 'Knowledge page scattered 1')
            self.assertIsNone(db.execute('SELECT 1 FROM project_idea_members WHERE channel_id=?', (first,)).fetchone())
            self.assertEqual(len(filing.ideas.live(dict(project_id='p', profile_id='a'))), WORKSTREAM_CAP)
        finally:
            filing.doCleanups()

    def test_notes_append_in_order_with_revision_checks_and_a_cap(self):
        from project_ideas import NOTES, NOTE_LIMIT
        bug = self.item('Send arrow sits left of centre', self.add(text='From a screenshot'))
        after = self.call('item_note', itemId=bug['id'], expectedRevision=bug['revision'], text='Still there in beta.10')
        bug = self.item('Send arrow sits left of centre', after)
        self.assertEqual([note['text'] for note in bug['notes']], ['From a screenshot', 'Still there in beta.10'])
        self.assertEqual(set(bug['notes'][0]), {'id', 'at', 'actor', 'text'})
        with self.assertRaisesRegex(Rejected, 'Ideas changed'):
            self.call('item_note', itemId=bug['id'], expectedRevision=bug['revision'] - 1, text='stale')
        for n in range(NOTES - 1):
            bug = self.item('Send arrow sits left of centre',
                            self.call('item_note', itemId=bug['id'], expectedRevision=bug['revision'], text=f'note {n}'))
        self.assertEqual(len(bug['notes']), NOTES)
        self.assertEqual(bug['notes_dropped'], 1)
        self.assertEqual(bug['notes'][0]['text'], 'Still there in beta.10', 'the oldest note drops off the front')
        self.assertEqual(bug['notes'][-1]['text'], f'note {NOTES - 2}')
        # Negative control: a note one character too long is refused and nothing is written.
        before = self.host.store.db.execute('SELECT revision, document FROM project_ideas WHERE id=?', (bug['id'],)).fetchone()
        for text in ('x' * (NOTE_LIMIT + 1), '   ', None):
            with self.assertRaises(Rejected):
                self.call('item_note', itemId=bug['id'], expectedRevision=bug['revision'], text=text)
        self.assertEqual(tuple(self.host.store.db.execute('SELECT revision, document FROM project_ideas WHERE id=?', (bug['id'],)).fetchone()),
                         tuple(before))
        self.assertEqual(len(self.call('item_note', itemId=bug['id'], expectedRevision=bug['revision'],
                                       text='x' * NOTE_LIMIT)['items'][0]['notes'][-1]['text']), NOTE_LIMIT)

    def test_item_states_include_closed_and_workstreams_do_not(self):
        bug = self.item('Send arrow sits left of centre', self.add())
        closed = self.call('item_set', itemId=bug['id'], expectedRevision=bug['revision'], name=bug['name'], state='closed',
                           target='0.1.0-beta.11')
        self.assertEqual((self.item(bug['name'], closed)['state'], self.item(bug['name'], closed)['target']), ('closed', '0.1.0-beta.11'))
        self.assertEqual(closed['states'], ['planned', 'building', 'done', 'closed'])
        with self.assertRaises(Rejected):
            self.call('item_set', itemId=bug['id'], expectedRevision=bug['revision'] + 1, name=bug['name'], state='gone', target=None)
        # Negative control: a workstream still can't be closed, and ideas_save takes exactly today's fields.
        with self.assertRaisesRegex(Rejected, 'planned, building or done'):
            self.new('Releases', state='closed')
        with self.assertRaises(Rejected):
            self.call('save', ideaId=None, expectedRevision=0, name='Releases', summary='', state='planned', target=None, kind='bug')
        # An item id is refused where a workstream is expected, and a workstream id where an item is.
        self.new('Releases')
        workstream = self.named('Releases')
        with self.assertRaises(Rejected):
            self.call('save', ideaId=bug['id'], expectedRevision=2, name='x', summary='', state='planned', target=None)
        with self.assertRaises(Rejected):
            self.call('item_note', itemId=workstream['id'], expectedRevision=workstream['revision'], text='hi')
        channel = self.conversation('Beta screen')
        with self.assertRaises(Rejected):
            self.call('assign', channelId=channel, ideaId=bug['id'])
        with self.assertRaises(Rejected):
            self.add(kind='workstream', name='Sneaky')

    def test_reopen_puts_a_closed_or_done_item_back_and_is_refused_when_open(self):
        """20 Sep, #16: "Reopen #9" is the answer Home's question asks for, and nothing could act on it. The op takes a
        done or closed item back to open: the state before the mark when a commit marked it, planned otherwise."""
        bug = self.item('Send arrow sits left of centre', self.add())
        closed = self.item(bug['name'], self.call('item_set', itemId=bug['id'], expectedRevision=bug['revision'], name=bug['name'], state='closed', target='beta.11'))
        listing = self.call('item_reopen', itemId=closed['id'], expectedRevision=closed['revision'])
        again = self.item(bug['name'], listing)
        self.assertEqual((again['state'], again['target'], again['revision'], again['events'][-1]['kind']), ('planned', 'beta.11', 3, 'reopened'))
        self.assertEqual(listing['items'][0]['id'], bug['id'], 'answers the whole listing, like every item op')
        # Refused when open, when the revision moved, and with any other field.
        with self.assertRaisesRegex(Rejected, 'open already'):
            self.call('item_reopen', itemId=again['id'], expectedRevision=again['revision'])
        done = self.item(bug['name'], self.call('item_set', itemId=again['id'], expectedRevision=again['revision'], name=bug['name'], state='done', target=None))
        with self.assertRaises(Rejected):
            self.call('item_reopen', itemId=done['id'], expectedRevision=done['revision'] - 1)
        with self.assertRaises(Rejected):
            self.call('item_reopen', itemId=done['id'], expectedRevision=done['revision'], name='x')
        self.assertEqual(self.item(bug['name'])['state'], 'done', 'nothing written by a refusal')
        reopened = self.item(bug['name'], self.call('item_reopen', itemId=done['id'], expectedRevision=done['revision']))
        self.assertEqual(reopened['state'], 'planned', 'marked done by hand: no state before the mark, so planned')
        # Marked done by a commit: back to the state it was in, and the mark is undone as Undo on the thread line does it.
        thread = self.conversation('Arrow fix')
        working = self.item(bug['name'], self.call('item_set', itemId=reopened['id'], expectedRevision=reopened['revision'], name=bug['name'], state='building', target=None))
        self.ideas.link_work('p', working['id'], thread, 'local-user', 'home')
        self.ideas.work_committed('p', thread, 'b' * 40, 'task-1')
        marked = self.item(bug['name'])
        self.assertEqual((marked['state'], marked['done']['was']), ('done', 'building'))
        back = self.item(bug['name'], self.call('item_reopen', itemId=marked['id'], expectedRevision=marked['revision']))
        self.assertEqual((back['state'], back['fixed_in'], bool(back['done']['undone_at'])), ('building', None, True))
        # A workstream id is refused where an item is expected, as for every item op.
        self.new('Releases')
        workstream = self.named('Releases')
        with self.assertRaises(Rejected):
            self.call('item_reopen', itemId=workstream['id'], expectedRevision=workstream['revision'])
        self.assertIn('ideas_item_reopen', __import__('project_ideas').FIELDS)

    def test_a_bug_may_share_a_workstreams_name_but_not_another_bugs(self):
        self.new('Releases')
        listing = self.add('bug', 'Releases')
        self.assertEqual([item['name'] for item in listing['items']], ['Releases'])
        self.add('idea', 'releases')
        with self.assertRaisesRegex(Rejected, 'Another bug'):
            self.add('bug', ' releases ')
        # Once the first is archived, the name is free again, as for workstreams.
        bug = self.item('Releases')
        self.call('archive', ideaId=bug['id'], expectedRevision=bug['revision'])
        self.add('bug', 'Releases')
        with self.assertRaisesRegex(Rejected, 'Another bug'):
            self.call('restore', ideaId=bug['id'], expectedRevision=bug['revision'] + 1)
        # Negative control: the workstream name rule is unchanged.
        with self.assertRaisesRegex(Rejected, 'Another idea of this project'):
            self.new('releases')
        self.assertEqual([idea['name'] for idea in self.call('list')['ideas']], ['Releases'])

    def test_an_old_database_reads_its_rows_as_workstreams(self):
        root = self.root / 'old'
        root.mkdir()
        old = Host(root)
        self.addCleanup(old.close)
        old.store.add_profile('a', 'codex', root / 'a', 'a@example.test', 'personal')
        old.store.add_project('p', root, 'a', 'fixture')
        record = dict(id='w1', created_at='2026-09-18T00:00:00+00:00', updated_at='2026-09-18T00:00:00+00:00', revision=1,
                      archived_at=None, events=[], name='Releases', summary='', state='planned', target=None)
        with old.store.db as db:
            # The host made today's table on start; put back the one written before kinds existed.
            db.executescript('DROP TABLE project_ideas; CREATE TABLE project_ideas (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,'
                             ' profile_id TEXT NOT NULL, revision INTEGER NOT NULL, document TEXT NOT NULL);')
            db.execute("INSERT INTO project_ideas VALUES ('w1', 'p', 'a', 1, ?)", (json.dumps(record),))
        ideas = ProjectIdeas(old.store)
        self.assertIn('kind', [row[1] for row in old.store.db.execute('PRAGMA table_info(project_ideas)')])
        listing = ideas.dispatch('ideas_list', dict(projectId='p', profileId='a'), ACTOR)
        self.assertEqual([idea['name'] for idea in listing['ideas']], ['Releases'])
        self.assertEqual(ideas.items('p'), [])
        ProjectIdeas(old.store)  # a second start adds nothing and fails on nothing
        self.assertEqual([row[1] for row in old.store.db.execute('PRAGMA table_info(project_ideas)')].count('kind'), 1)


class ImportTests(IdeaFixture):
    """The one-time copy of the Bugs and ideas thread's list. Only ever temporary databases and temporary files."""

    def imported(self, project='p'):
        return sorted(self.ideas.items(project), key=lambda item: item['number'])

    def marker(self, project='p'):
        return self.host.store.db.execute('SELECT document FROM project_ideas_imports WHERE project_id=?', (project,)).fetchone()

    def quietly(self, make):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = make()
        return result, err.getvalue()

    def test_the_import_runs_once_only_when_the_thread_exists_and_rolls_back_whole(self):
        # The default fixture has no such thread: nothing is imported.
        self.assertEqual(self.imported(), [])
        self.assertIsNone(self.marker())
        # Negative control: a private thread with that name imports nothing.
        self.conversation('Bugs and ideas', private=True)
        ProjectIdeas(self.host.store)
        self.assertEqual(self.imported(), [])
        # A broken row halfway through leaves no item, no number and no marker; the next start tries again.
        from project_ideas import IMPORT_FILE
        data = json.loads(IMPORT_FILE.read_text())
        broken = self.root / 'broken.json'
        data['items'][20]['kind'] = 'workstream'
        broken.write_text(json.dumps(data))
        self.conversation(' bugs and ideas ')  # the sidebar name compared without case and outside spaces
        result, err = self.quietly(lambda: self.ideas.import_bugs_and_ideas(broken))
        self.assertIsNone(result)
        self.assertIn('Item 21', err)
        self.assertEqual(self.imported(), [])
        self.assertIsNone(self.marker())
        self.assertIsNone(self.host.store.db.execute("SELECT 1 FROM project_item_numbers WHERE project_id='p'").fetchone())
        self.assertFalse(self.host.store.db.in_transaction)
        # With the thread there and the checked-in file: 35 items, numbered from the file.
        ProjectIdeas(self.host.store)
        items = self.imported()
        self.assertEqual([item['number'] for item in items], list(range(1, 36)))
        self.assertEqual(items[21]['state'], 'closed')
        self.assertEqual((items[0]['state'], items[6]['state']), ('closed', 'done'))
        self.assertEqual((items[29]['kind'], items[29]['name']), ('bug', "Home's send button looks enabled when the composer is empty"))
        self.assertEqual(items[0]['notes'][0]['text'], 'Decided 19 Sep: keep approval, add #27 to #29 instead (notes/decisions.md)')
        self.assertEqual(json.loads(self.marker()[0])['count'], 35)
        self.assertEqual(self.call('item_list')['next_number'], 36)
        # A third start: the marker holds, still 35.
        ProjectIdeas(self.host.store)
        self.assertEqual(len(self.imported()), 35)
        self.assertEqual(self.add_after_import()['number'], 36)

    def add_after_import(self):
        listing = self.ideas.dispatch('ideas_item_add', {**self.scope, 'kind': 'bug', 'name': 'Found after the import', 'text': 'x'}, ACTOR)
        return next(item for item in listing['items'] if item['name'] == 'Found after the import')

    def test_two_projects_with_the_thread_get_nothing(self):
        self.conversation('Bugs and ideas')
        self.conversation('Bugs and ideas', project='other')
        result, err = self.quietly(lambda: ProjectIdeas(self.host.store))
        self.assertEqual((self.imported(), self.imported('other')), ([], []))
        self.assertIn('other', err)
        self.assertIn('p', err)
        self.assertEqual(err.count('\n'), 1, 'one line')
        # Negative control: a missing file does nothing either, even with one project holding the thread.
        self.assertIsNone(self.ideas.import_bugs_and_ideas(self.root / 'missing.json'))

    def test_the_import_file_matches_the_plan(self):
        from project_ideas import IMPORT_FILE
        raw = IMPORT_FILE.read_text(encoding='utf-8')
        self.assertNotIn('\u2014', raw)
        items = json.loads(raw)['items']
        self.assertEqual(len(items), 35)
        self.assertEqual([item['number'] for item in items], list(range(1, 36)))
        for item in items:
            self.assertEqual(set(item), {'number', 'kind', 'name', 'text', 'state'})
            self.assertIn(item['kind'], ('bug', 'idea'))
            self.assertLessEqual(len(item['name']), NAME_LIMIT)
            self.assertTrue(item['text'].strip())
        self.assertEqual({item['number'] for item in items if item['state'] == 'closed'}, {1, 22})
        self.assertEqual({item['number'] for item in items if item['state'] == 'done'}, {7})
        self.assertEqual([item['kind'] for item in items[29:]], ['bug'] * 5 + ['idea'])
        # The names that had to be cut keep the owner's full wording in their first note.
        for item in items:
            if item['name'].endswith('...'):
                self.assertIn(item['name'][:-3].casefold(), item['text'].casefold())


class WorkTests(IdeaFixture):
    """The list keeps up with the work on it (19 Sep, next-wave C): a thread is linked to an item by Home's job or from
    the item's page, your approval's commit marks it done, and a build that holds that commit stamps "Fixed in". Each
    rule has its negative control: text never links or marks, another project's or account's thread never does."""

    def setUp(self):
        super().setUp()
        # No test reads the Mac's own ~/Dervo Releases: the host reads the folder only once a done item has a commit and
        # no build, and these tests make such items, so they point it at a folder of their own.
        self.releases = self.root / 'Dervo Releases'
        patcher = patch('project_ideas.releases_root', return_value=self.releases)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add(self, kind, name, text='', state=None):
        listing = self.call('item_add', kind=kind, name=name, text=text)
        record = next(row for row in listing['items'] if row['name'] == name)
        if state:
            self.call('item_set', itemId=record['id'], expectedRevision=record['revision'], name=name, state=state, target=None)
            record = self.item(record['id'])
        return record

    def item(self, item_id):
        return self.ideas.item('p', item_id)

    def link(self, item_id, thread, by='home'):
        return self.ideas.link_work('p', item_id, thread, 'local-user', by)

    def test_starting_work_from_the_page_makes_a_linked_thread_with_the_request_drafted(self):
        bug = self.add('bug', 'Send arrow off centre', 'Seen on Home at 1052 wide.')
        reply = self.host.dispatch('ideas_item_work', dict(self.scope, itemId=bug['id'], expectedRevision=bug['revision']))
        thread = reply['thread']
        self.assertEqual(thread['name'], 'Bug #1: Send arrow off centre')
        self.assertEqual(self.host.dispatch('organisation_get', dict(projectId='p', channelId=thread['id']))['name'], thread['name'])
        [item] = reply['items']
        self.assertEqual((item['state'], item['work']['thread_id'], item['work']['by'], item['thread']), ('building', thread['id'], 'you', thread))
        self.assertEqual(reply['text'], "Fix bug #1, Send arrow off centre.\n\nFrom the project's Bugs and ideas list (Dervo's records):\n- Seen on Home at 1052 wide.")
        self.assertIn('linked', [event['kind'] for event in self.item(bug['id'])['events']])
        # Negative controls: a stale revision, a done item, and a bare store with no host.
        with self.assertRaisesRegex(Rejected, 'Reload'):
            self.host.dispatch('ideas_item_work', dict(self.scope, itemId=bug['id'], expectedRevision=bug['revision']))
        done = self.add('idea', 'Voice memos', 'Later.', state='done')
        with self.assertRaisesRegex(Rejected, 'is done'):
            self.host.dispatch('ideas_item_work', dict(self.scope, itemId=done['id'], expectedRevision=done['revision']))
        with self.assertRaisesRegex(Rejected, 'window'):
            self.ideas.dispatch('ideas_item_work', dict(self.scope, itemId=bug['id'], expectedRevision=bug['revision'] + 1), ACTOR)
        # An item a work account wrote: refused before any thread is made, so nothing empty is left behind (review 1, #3).
        self.host.store.add_profile('w', 'codex', self.root / 'w', 'w@example.test', 'work')
        theirs = self.ideas.new_item('bug', 'Their bug', 9, 'w')
        with self.host.store.db:
            self.ideas.write_item('p', 'w', theirs)
        with self.assertRaisesRegex(Rejected, "account can't work on"):
            self.host.dispatch('ideas_item_work', dict(self.scope, itemId=theirs['id'], expectedRevision=theirs['revision']))
        self.assertIsNone(self.item(theirs['id'])['work'])
        self.assertEqual(len(self.host.dispatch('conversations', dict(projectId='p'))), 1, 'no second thread from the refusals')
        # An idea's request says build; a thread that is gone leaves the item with no thread to show.
        idea = self.add('idea', 'Voice notes', '')
        self.assertEqual(self.host.dispatch('ideas_item_work', dict(self.scope, itemId=idea['id'], expectedRevision=idea['revision']))['text'],
                         'Build idea #10, Voice notes.')

    def test_a_link_stays_in_the_project_and_on_an_account_that_may_touch_the_item(self):
        bug = self.add('bug', 'Crash on export')
        mine, private, elsewhere = self.conversation('Export crash'), self.conversation('Private one', private=True), self.conversation('Other', project='other')
        with self.assertRaisesRegex(Rejected, 'not in this project'):
            self.link(bug['id'], elsewhere)
        with self.assertRaisesRegex(Rejected, 'private thread'):
            self.link(bug['id'], private)
        with self.assertRaisesRegex(Rejected, 'not in this project'):
            self.link(bug['id'], 'missing-thread')
        with self.assertRaisesRegex(Rejected, 'unavailable'):
            self.link('missing-item', mine)
        self.assertIsNone(self.item(bug['id'])['work'], 'negative controls: nothing linked')
        # A personal thread of another account of this Mac may work on it (ON_THIS_MAC); a work account's may not.
        self.host.store.add_profile('w', 'codex', self.root / 'w', 'w@example.test', 'work')
        other_personal, work_account = self.conversation('On b'), self.conversation('On w')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='b' WHERE id=?", (other_personal,))
            self.host.store.db.execute("UPDATE threads SET profile_id='w' WHERE id=?", (work_account,))
        self.assertEqual(self.link(bug['id'], other_personal)['work']['thread_id'], other_personal)
        with self.assertRaisesRegex(Rejected, "account can't work on"):
            self.link(bug['id'], work_account)
        # The other way round: an item a work account wrote isn't touched from a personal thread.
        theirs = self.ideas.new_item('bug', 'Their bug', 9, 'w')
        with self.host.store.db:
            self.ideas.write_item('p', 'w', theirs)
        with self.assertRaisesRegex(Rejected, "account can't work on"):
            self.link(theirs['id'], mine)
        self.assertEqual(self.link(theirs['id'], work_account)['work']['thread_id'], work_account, 'its own account may')
        # Linking sets a planned item building and is idempotent; a building item keeps its state.
        self.assertEqual(self.item(bug['id'])['state'], 'building')
        revision = self.item(bug['id'])['revision']
        self.link(bug['id'], other_personal)
        self.assertEqual(self.item(bug['id'])['revision'], revision, 'the same thread again changes nothing')
        self.assertEqual(self.item(bug['id'])['thread'] if 'thread' in self.item(bug['id']) else self.ideas.with_thread(self.item(bug['id']))['thread'],
                         dict(id=other_personal, name='On b'))

    def test_the_approved_commit_marks_every_linked_open_item_done_and_text_alone_never_does(self):
        bug, idea, closed = self.add('bug', 'Crash on export'), self.add('idea', 'Export as CSV'), self.add('bug', 'Old one', state='closed')
        other = self.add('bug', 'Sidebar jumps')
        thread, elsewhere = self.conversation('Export work'), self.conversation('Sidebar work')
        for item in (bug, idea, closed):
            if item is closed:
                self.host.store.db.execute('UPDATE project_ideas SET document=json_set(document, "$.work", json(?)) WHERE id=?',
                                           (json.dumps(dict(thread_id=thread, at='x', by='home')), closed['id']))
                self.host.store.db.commit()
            else:
                self.link(item['id'], thread)
        self.link(other['id'], elsewhere)
        # Planted text: a thread named like a fix, an agent's answer and a note that say "fixed #4" mark nothing.
        self.call('item_note', itemId=self.item(other['id'])['id'], expectedRevision=self.item(other['id'])['revision'], text='fixed #4 in abc1234')
        with self.host.store.db:
            self.host.store.db.execute('UPDATE local_channels SET name=? WHERE id=?', ('Fixed #4 and #1, all done', elsewhere))
        marked = self.ideas.work_committed('p', thread, 'a' * 40, 'task-1')
        self.assertEqual([record['number'] for record in marked], [1, 2])
        for record in marked:
            self.assertEqual((record['state'], record['done']['sha'], record['done']['thread_id'], record['done']['task_id'], record['done']['was'], record['done']['undone_at']),
                             ('done', 'a' * 40, thread, 'task-1', 'building', None))
        self.assertEqual(self.item(closed['id'])['state'], 'closed', 'a closed item linked by hand is left alone')
        self.assertEqual((self.item(other['id'])['state'], self.item(other['id'])['done']), ('building', None), 'another thread’s item is untouched')
        # Negative controls: a thread of another project, no sha, a thread with nothing linked, and the same commit again.
        self.assertEqual(self.ideas.work_committed('other', thread, 'b' * 40, 'task-2'), [])
        self.assertEqual(self.ideas.work_committed('p', thread, None, 'task-2'), [])
        self.assertEqual(self.ideas.work_committed('p', self.conversation('Empty'), 'b' * 40, 'task-3'), [])
        self.assertEqual(self.ideas.work_committed('p', thread, 'c' * 40, 'task-4'), [], 'done items aren’t marked twice')
        # The thread's lines: one for both items, with Undo; the other thread says what it works on.
        [line] = self.ideas.thread_lines('p', thread)
        self.assertEqual((line['kind'], line['line'], line['can_undo'], line['undone'], line['profile_id']),
                         ('done', 'Marked bug #1 and idea #2 done.', True, False, 'a'))
        self.assertEqual([(item['number'], item['was'], item['revision']) for item in line['items']], [(1, 'building', 3), (2, 'building', 3)])
        [working] = self.ideas.thread_lines('p', elsewhere)
        self.assertEqual((working['kind'], working['line'], working['can_undo']), ('working', 'Working on bug #4, Sidebar jumps.', False))
        self.assertEqual(self.ideas.thread_lines('p', self.conversation('Nothing')), [])

    def test_undo_reopens_the_item_and_a_reopened_item_is_neither_fixed_nor_marked_again_by_the_old_commit(self):
        bug = self.add('bug', 'Crash on export')
        thread = self.conversation('Export work')
        self.link(bug['id'], thread)
        [record] = self.ideas.work_committed('p', thread, 'a' * 40, 'task-1')
        record['fixed_in'] = dict(release='0.1.0-preview.1', at='x')
        with self.host.store.db:
            self.ideas.write_item('p', 'a', record)
        # Undo is the state control moving off done, from the thread's line or the page.
        listing = self.call('item_set', itemId=record['id'], expectedRevision=record['revision'], name=record['name'], state='building', target=None)
        [item] = listing['items']
        self.assertEqual((item['state'], item['fixed_in'], item['done']['sha']), ('building', None, 'a' * 40))
        self.assertIsNotNone(item['done']['undone_at'])
        self.assertEqual(item['events'][-1]['kind'], 'reopened')
        line, working = self.ideas.thread_lines('p', thread)
        self.assertEqual((line['line'], line['can_undo'], line['undone'], line['items']), ('Marked bug #1 done.', False, True, []))
        self.assertEqual(working['line'], 'Working on bug #1, Crash on export.', 'open again, and the thread is still the one on it')
        # Negative control: a save that keeps it done, or moves a never-marked item, doesn't write an undo.
        again = self.ideas.work_committed('p', thread, 'b' * 40, 'task-2')
        self.assertEqual([r['done']['sha'] for r in again], ['b' * 40], 'still linked, so the next approval marks it again')
        item = self.item(bug['id'])
        kept = self.call('item_set', itemId=item['id'], expectedRevision=item['revision'], name='Crash on export', state='done', target='beta.12')['items'][0]
        self.assertIsNone(kept['done']['undone_at'])
        other = self.add('idea', 'Never marked')
        moved = self.call('item_set', itemId=other['id'], expectedRevision=other['revision'], name=other['name'], state='closed', target=None)['items'][-1]
        self.assertIsNone(moved['done'])
        self.assertNotIn('reopened', [event['kind'] for event in moved['events']])

    def builds(self, root):
        """Three release folders like release_preview leaves them: one built before the fix, one holding it, one after."""
        from datetime import datetime, timezone
        fix = 'f' * 40
        for n, (identifier, commits, state) in enumerate([('0.1.0-preview.1', ['1' * 40], 'built'), ('0.1.0-preview.2', [fix, '1' * 40], 'built'),
                                                          ('0.1.0-preview.3', ['3' * 40, fix, '1' * 40], 'built'), ('0.1.0-preview.4', ['4' * 40, fix], 'failed')]):
            folder = root / identifier
            folder.mkdir(parents=True)
            (folder / 'release.json').write_text(json.dumps(dict(id=identifier, state=state, commits=commits,
                                                                 built_at=datetime(2026, 9, 20, 1, n, tzinfo=timezone.utc).isoformat())))
        (root / '0.1.0-preview.0').mkdir()
        (root / '0.1.0-preview.0/release.json').write_text(json.dumps(dict(id='0.1.0-preview.0', state='built')))  # before commits were recorded
        (root / 'link').symlink_to(root / '0.1.0-preview.2')
        (root / 'noise.txt').write_text('not a build')
        return fix

    def test_a_release_record_over_the_size_limit_is_skipped_before_it_is_read(self):
        """Security review 20 Sep, L4: release.json is read at host start and on list polls; a multi-gigabyte one
        planted in ~/Dervo Releases would be read whole. Over RELEASE_RECORD_LIMIT it says nothing."""
        from project_ideas import release_builds, RELEASE_RECORD_LIMIT
        releases = self.releases
        self.builds(releases)
        big = releases / '0.1.0-preview.3' / 'release.json'
        record = big.read_text()
        with big.open('a') as handle:
            handle.write(' ' * (RELEASE_RECORD_LIMIT + 1 - len(record)))  # still valid JSON, just huge
        self.assertEqual([build['id'] for build in release_builds(releases)], ['0.1.0-preview.1', '0.1.0-preview.2'])
        # Negative control: at the limit it's read.
        big.write_text(record + ' ' * (RELEASE_RECORD_LIMIT - len(record)))
        self.assertEqual([build['id'] for build in release_builds(releases)], ['0.1.0-preview.1', '0.1.0-preview.2', '0.1.0-preview.3'])

    def test_a_work_request_redacts_a_key_in_an_items_notes(self):
        """Security review 20 Sep, M4: "Work on this" from an item's page sends its notes to a model."""
        from project_ideas import work_request
        key = '3f2a9c1b7d4e5f60718293a4b5c6d7e8'
        record = dict(kind='bug', number=4, name='Login fails', notes=[dict(text=f'the mailgun key is {key}'), dict(text='Only on staging.')])
        text = work_request(record)
        self.assertIn('- the mailgun key is [redacted]', text)
        self.assertIn('- Only on staging.', text)
        self.assertNotIn(key, text)
        self.assertEqual(work_request(dict(record, notes=[])), 'Fix bug #4, Login fails.')

    def test_a_build_that_holds_the_done_commit_stamps_fixed_in_with_the_first_such_build(self):
        from project_ideas import release_builds
        releases = self.releases
        fix = self.builds(releases)
        self.assertEqual([(build['id'], fix in build['commits']) for build in release_builds(releases)],
                         [('0.1.0-preview.1', False), ('0.1.0-preview.2', True), ('0.1.0-preview.3', True)],
                         'built ones with commits, oldest first; a failed build, one without commits, a link and a stray file say nothing')
        self.assertEqual(release_builds(self.root / 'nowhere'), [])
        fixed, later, undone, elsewhere = (self.add('bug', name) for name in ('Fixed one', 'Not built yet', 'Undone one', 'Never linked'))
        thread = self.conversation('Fixes')
        for item in (fixed, later, undone):
            self.link(item['id'], thread)
        self.ideas.work_committed('p', thread, fix, 'task-1')
        for item, sha in ((later, '9' * 40), (undone, fix)):
            record = self.item(item['id'])
            record['done'] = dict(sha=sha, thread_id=thread, task_id='t', at='x', was='building', undone_at='x' if item is undone else None)
            record['state'] = 'building' if item is undone else 'done'
            with self.host.store.db:
                self.ideas.write_item('p', 'a', record)
        listing = self.call('item_list')
        by_name = {item['name']: item for item in listing['items']}
        self.assertEqual(by_name['Fixed one']['fixed_in']['release'], '0.1.0-preview.2', 'the first build that holds it, not the newest')
        self.assertEqual(by_name['Fixed one']['events'][-1]['kind'], 'fixed')
        self.assertIsNone(by_name['Not built yet']['fixed_in'], 'negative control: its commit is in no build')
        self.assertIsNone(by_name['Undone one']['fixed_in'], 'negative control: the mark was undone')
        self.assertIsNone(by_name['Never linked']['fixed_in'])
        revision = by_name['Fixed one']['revision']
        self.assertEqual(self.call('item_list')['items'][0]['revision'], revision, 'stamped once; the next listing changes nothing')
        # A host start stamps too, so the page needn't be opened first.
        record = self.item(later['id'])
        record['done']['sha'] = '3' * 40
        with self.host.store.db:
            self.ideas.write_item('p', 'a', record)
        self.host.close()
        self.host = Host(self.root)
        self.ideas = ProjectIdeas(self.host.store)
        self.assertEqual(self.host.ideas().item('p', later['id'])['fixed_in']['release'], '0.1.0-preview.3')
        with patch('project_ideas.release_builds') as read:
            empty = Host(self.root / 'empty-store')
            self.addCleanup(empty.close)
            self.assertEqual(empty.stamp_builds(), 0, 'negative control: nothing to stamp, nothing raised')
            read.assert_not_called()
            self.assertEqual(self.host.ideas().stamp_fixed_in(), 0)
            read.assert_not_called()  # every done item is stamped or undone, so the releases folder isn't opened at all
        self.assertEqual(self.item(undone['id'])['fixed_in'], None, 'still not fixed: an undone mark is never stamped')
        # A record whose id isn't its folder's name says nothing (review 1, #4): copied elsewhere, it isn't that build.
        (releases / 'copy').mkdir()
        (releases / 'copy/release.json').write_text((releases / '0.1.0-preview.2/release.json').read_text())
        self.assertEqual([build['id'] for build in release_builds(releases)], ['0.1.0-preview.1', '0.1.0-preview.2', '0.1.0-preview.3'])

    def test_undoing_the_task_reverts_the_commit_and_reopens_its_items_which_no_build_then_stamps(self):
        bug, idea, other = self.add('bug', 'Crash on export'), self.add('idea', 'Export as CSV'), self.add('bug', 'Sidebar jumps')
        thread, elsewhere = self.conversation('Export work'), self.conversation('Sidebar work')
        for item in (bug, idea):
            self.link(item['id'], thread)
        self.link(other['id'], elsewhere)
        self.ideas.work_committed('p', thread, 'a' * 40, 'task-1')
        self.ideas.work_committed('p', elsewhere, 'b' * 40, 'task-2')
        # The user undid task-1: its commit is reverted, the two items are open again as they were, and say so.
        reopened = self.ideas.work_reverted('p', thread, 'a' * 40, 'task-1')
        self.assertEqual([(r['number'], r['state'], r['fixed_in'], r['done']['sha']) for r in reopened], [(1, 'building', None, 'a' * 40), (2, 'building', None, 'a' * 40)])
        for record in reopened:
            self.assertIsNotNone(record['done']['undone_at'])
            self.assertIsNotNone(record['done']['reverted_at'])
            self.assertEqual((record['events'][-1]['kind'], record['events'][-1]['reason']), ('reopened', 'reverted'))
        [line, working] = self.ideas.thread_lines('p', thread)
        self.assertEqual((line['kind'], line['line'], line['can_undo'], line['undone'], line['items']), ('reopened', 'Reopened bug #1 and idea #2.', False, False, []))
        self.assertEqual(working['line'], 'Working on bug #1 and idea #2.')
        self.assertEqual(self.item(other['id'])['state'], 'done', 'negative control: another task’s commit stays marked')
        # Negative controls: the wrong sha, task or thread, and a mark you undid already, reopen nothing.
        self.assertEqual(self.ideas.work_reverted('p', elsewhere, 'a' * 40, 'task-2'), [])
        self.assertEqual(self.ideas.work_reverted('p', elsewhere, 'b' * 40, 'task-9'), [])
        self.assertEqual(self.ideas.work_reverted('p', thread, 'b' * 40, 'task-2'), [])
        self.assertEqual(self.ideas.work_reverted('p', thread, None, 'task-1'), [])
        self.assertEqual(self.ideas.work_reverted('p', thread, 'a' * 40, 'task-1'), [], 'already undone')
        self.assertEqual(self.item(other['id'])['state'], 'done')
        # The reverted commit is still in HEAD's history, so a build that holds it must not stamp the reopened items.
        self.assertTrue(self.ideas.stampable(), '#3 stands and is in no build yet')
        stamped = self.ideas.stamp_fixed_in(builds=[dict(id='0.1.0-preview.9', built='x', commits={'a' * 40, 'b' * 40})])
        self.assertEqual(stamped, 1, 'the standing mark on #3 only')
        self.assertFalse(self.ideas.stampable(), 'negative control: reopened items never make the folder worth reading')
        self.assertEqual((self.item(bug['id'])['fixed_in'], self.item(other['id'])['fixed_in']['release']), (None, '0.1.0-preview.9'))
