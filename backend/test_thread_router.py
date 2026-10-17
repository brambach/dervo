"""The host's side of smart thread routing (thread_router.py): with a stand-in ``thread_routing`` module, each of its
four answers reaches the code that acts on it, is checked and logged, and nothing changes when the module is missing,
raises or isn't sure. Real host and store; the coding request and Home's model are stand-ins."""
import json
import time
import types
import unittest
import unittest.mock
import uuid

import project_orchestrator as po
import thread_router
from dervo_connections import Rejected
import test_project_orchestrator as tpo
from test_project_orchestrator import NowTurns, answer


class Router:
    """A stand-in thread_routing: each answer is whatever the test sets, and every call is recorded."""
    AUTO_ROUTE, SUGGEST, NAME_APPLY = 0.9, 0.6, 0.8

    def __init__(self):
        self.calls = []
        self.route_answer = self.name_answer = self.sort_answer = self.tidy_answer = None

    def module(self):
        router = self
        found = types.ModuleType('thread_routing')
        found.AUTO_ROUTE, found.SUGGEST, found.NAME_APPLY = self.AUTO_ROUTE, self.SUGGEST, self.NAME_APPLY
        def call(name):
            def function(*args):
                router.calls.append((name, args))
                result = getattr(router, name + '_answer')
                return result(*args) if callable(result) else result
            return function
        for name in ('route', 'name', 'sort', 'tidy'):
            setattr(found, name, call(name))
        return found


class RouterFixture(tpo.HomeDispatchTests):
    # Its fixture, not its tests: those run in test_project_orchestrator.
    locals().update({name: None for name in dir(tpo.HomeDispatchTests) if name.startswith('test_')})

    def setUp(self):
        super().setUp()
        # Off a Mac the release's account gate has no Keychain and refuses Home's model call (the same 9 Home dispatch
        # tests fail on mvp1 here); these tests are about routing, so the gate stands aside.
        gate = unittest.mock.patch.object(self.host, 'require_beta_access', lambda: None)
        gate.start()
        self.addCleanup(gate.stop)
        self.router = Router()
        thread_router._module = self.router.module()
        self.addCleanup(setattr, thread_router, '_module', None)
        self.reply = lambda prompt: answer('Home answered.')

    def decisions(self, op=None):
        return [row for row in thread_router.decisions(self.host.store.db, 'p') if op is None or row['op'] == op]

    def sure(self, thread, confidence=0.95, why='follows your last message'):
        self.router.route_answer = dict(kind='existing', thread_id=thread, confidence=confidence, why=why,
                                        candidates=[dict(thread_id=thread, score=confidence)])


class RouteTests(RouterFixture):
    def test_a_message_the_router_is_sure_of_goes_to_its_thread_through_apply_with_no_model_call(self):
        _, picker, _ = self.world()
        self.sure(picker)
        listing = self.say('also make the warning red', client='client-route01')
        reply = listing['entries'][-1]
        self.assertEqual(self.prompts, [], 'no model call')
        self.assertEqual((reply['state'], reply['text']), ('ready', 'Added to Connector picker.'))
        self.assertEqual((reply['routed']['confidence'], reply['routed']['move']), (0.95, False))
        self.assertNotIn('not_before', reply, 'sure: no wait')
        self.ticks()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.sent[0]['channelId'], self.sent[0]['origin']), (picker, 'home'))
        # The routed brief Home's own jobs get: your words, scoped to the focus.
        self.assertIn('Home routed this request to this chat', self.sent[0]['text'])
        self.assertIn('Your request:\nalso make the warning red', self.sent[0]['text'])
        self.assertEqual(self.entry(reply['id'])['parts'][0]['state'], 'sent')
        logged = self.decisions('route')
        self.assertEqual(len(logged), 1)
        self.assertTrue(logged[0]['applied'])
        self.assertEqual(logged[0]['input']['text'], 'also make the warning red')
        self.assertIn(picker, [row['id'] for row in logged[0]['input']['threads']])
        self.assertEqual(reply['routed']['decision'], logged[0]['id'])
        self.assertEqual(logged[0]['outcome'], 'kept', 'it went where Dervo sent it')
        # The same clientId again returns the first record, and nothing is sent twice.
        self.say('also make the warning red', client='client-route01')
        self.ticks()
        self.assertEqual(len(self.sent), 1)

    def test_a_new_thread_a_list_or_start_goes_to_home_s_model_with_the_router_s_guess(self):
        _, picker, _ = self.world()
        self.router.route_answer = dict(kind='new', thread_id=None, name='Dark mode toggle', confidence=0.95, why='nothing like it')
        self.say('add a dark mode toggle to settings', client='client-new00001')
        self.assertEqual(len(self.prompts), 1, 'a new thread needs its workstream, and maybe a question: the model')
        self.assertIn('probably needs a new thread ("Dark mode toggle")', self.prompts[0])
        self.sure(picker)
        self.say('start the docs page for the picker too', client='client-new00002')
        self.say('1) make the picker warning red 2) fix the release notes', client='client-new00003')
        self.assertEqual(len(self.prompts), 3)
        self.assertEqual([row['applied'] for row in self.decisions('route')], [False, False, False])

    def test_the_router_reads_where_your_last_messages_went_and_only_threads_home_may_use(self):
        _, picker, _ = self.world()
        private = self.conversation('My private notes', private=True)
        self.sure(picker)
        self.say('make the warning red', client='client-hist0001')
        self.say('and make it bigger', client='client-hist0002')
        route_calls = [args for name, args in self.router.calls if name == 'route']
        text, history, rows, _now = route_calls[-1]
        self.assertEqual(text, 'and make it bigger')
        self.assertEqual(history[-1]['text'], 'make the warning red')
        self.assertEqual(history[-1]['thread_id'], picker, 'where it went')
        row = next(row for row in rows if row['id'] == picker)
        self.assertEqual((row['name'], row['workstream'], row['archived']), ('Connector picker', 'Connectors', False))
        self.assertNotIn(private, [row['id'] for row in rows], 'a private thread is never a route')

    def test_unsure_goes_to_home_s_model_with_the_router_s_guess_and_its_threads_shown(self):
        _, picker, sidebar = self.world()
        self.router.route_answer = dict(kind='unsure', thread_id=None, confidence=0.5, why='two threads fit',
                                        candidates=[dict(thread_id=picker, score=0.5), dict(thread_id=sidebar, score=0.4)])
        self.say('tweak the warning', client='client-unsure01')
        self.assertEqual(len(self.prompts), 1, 'Home answered')
        hint = next(line for line in self.prompts[0].splitlines() if line.startswith("Dervo's routing guess"))
        self.assertRegex(hint, r'could continue \[t\d+\] or \[t\d+\]\.$')
        self.assertNotIn('two threads fit', self.prompts[0], "the module's own words stay out of Home's prompt")
        self.assertEqual([row['applied'] for row in self.decisions('route')], [False])

    def test_a_question_or_a_command_is_home_s_to_answer(self):
        _, picker, _ = self.world()
        self.sure(picker)
        self.say('is the picker warning done?', client='client-quest001')
        self.assertEqual(len(self.prompts), 1, 'a question is never sent to an agent as work')
        # Delete never acts from words (home_commands): Home's model answers it, told it's a command.
        self.router.route_answer = dict(kind='existing', thread_id=picker, confidence=0.99, why='names it', command='delete')
        self.say('delete the picker thread', client='client-comm0001')
        self.assertEqual(len(self.prompts), 2)
        self.assertIn('is a delete command for Dervo, not work for an agent', self.prompts[1])
        # Archive does (9 Oct): the sidebar's own operation, with no model call (test_home_commands has the rest).
        self.router.route_answer = dict(kind='existing', thread_id=picker, confidence=0.99, why='names it', command='archive')
        reply = self.say('archive the picker thread', client='client-comm0002')['entries'][-1]
        self.assertEqual(len(self.prompts), 2)
        self.assertEqual(reply['changes'][0]['line'], 'Archived Connector picker.')
        self.assertEqual(self.sent, [])
        self.assertEqual([row['applied'] for row in self.decisions('route')], [True, False, False], 'newest first')

    def test_below_auto_route_it_still_routes_but_waits_for_move(self):
        _, picker, sidebar = self.world()
        self.sure(picker, confidence=0.7)
        reply = self.say('also make the warning red', client='client-hint0001')['entries'][-1]
        self.assertEqual(self.prompts, [])
        self.assertTrue(reply['routed']['move'])
        self.assertGreater(reply['not_before'], time.time())
        self.ticks()
        self.assertEqual(self.sent, [], 'it waits its few seconds')
        moved = self.orch('move', entryId=reply['id'], to=sidebar)['entries'][-1]
        self.assertEqual((moved['id'], moved['text'], moved['routed']['moved']), (reply['id'], 'Added to Sidebar polish.', True))
        self.ticks()
        self.assertEqual([request['channelId'] for request in self.sent], [sidebar], 'your Move goes at once')
        self.assertEqual(self.decisions('route')[0]['outcome'], 'moved:' + sidebar)
        with self.assertRaises(Rejected):
            self.orch('move', entryId=reply['id'], to=picker)  # gone already

    def test_a_suggested_route_goes_on_its_own_after_the_wait_and_move_to_home_asks_the_model(self):
        _, picker, _ = self.world()
        self.sure(picker, confidence=0.7)
        first = self.say('also make the warning red', client='client-wait1001')['entries'][-1]
        later = time.time() + po.Orchestrator.ROUTE_HOLD_SECONDS + 1
        with unittest.mock.patch.object(po.time, 'time', lambda: later):
            self.ticks()
        self.assertEqual([request['channelId'] for request in self.sent], [picker])
        self.assertEqual(self.decisions('route')[0]['outcome'], 'kept')
        self.assertEqual(self.entry(first['id'])['parts'][0]['state'], 'sent')
        second = self.say('and fix the hint text', client='client-wait1002')['entries'][-1]
        self.orch('move', entryId=second['id'], to='home')
        self.assertEqual(len(self.prompts), 1, 'Home read it')
        self.assertNotIn("Dervo's routing guess", self.prompts[0])
        with self.assertRaises(Rejected):
            self.orch('move', entryId=second['id'], to='nope')

    def test_nothing_changes_without_the_module_or_when_it_raises_or_answers_nonsense(self):
        _, picker, _ = self.world()
        thread_router._module = False
        self.say('fix one', client='client-none0001')
        def boom(*_):
            raise RuntimeError('bug')
        thread_router._module = self.router.module()
        self.router.route_answer = boom
        self.say('fix two', client='client-none0002')
        for nonsense in (dict(kind='existing', thread_id='nope', confidence=0.99), dict(kind='maybe', confidence=0.99),
                         dict(kind='existing', thread_id=picker, confidence=1.5), 'existing'):
            self.router.route_answer = nonsense
            self.say('fix three', client='client-' + uuid.uuid4().hex[:8])
        self.assertEqual(len(self.prompts), 6, 'Home answered every one')
        self.assertEqual(self.decisions('route'), [])

    def test_never_routed_alone_to_an_archived_thread_and_a_waiting_message_waits(self):
        _, picker, sidebar = self.world()
        self.host.dispatch('conversation_archive', dict(projectId='p', channelId=sidebar))
        self.sure(sidebar)
        self.say('polish it', client='client-arch0001')
        self.assertEqual(len(self.prompts), 1, 'an archived thread is never a route')
        turns = NowTurns(hold=True)
        self.host.home_turns = turns
        self.reply = lambda prompt: answer('Home answered.')
        # Not sure enough to go ahead of Home's answer (BusyTests has the sure ones), so it waits like any message.
        self.sure(picker, confidence=0.7)
        sure = self.router.route_answer
        self.router.route_answer = lambda text, *_: sure if text == 'make it red' else None  # Home answers the first
        self.say('first', client='client-wait0001')
        waiting = self.say('make it red', client='client-wait0002')['entries'][-1]
        self.assertTrue(waiting.get('held'), 'it waits its turn like any message')
        self.assertNotIn('routed', waiting)

    def test_a_message_for_an_archived_thread_asks_home_with_that_thread_first(self):
        _, picker, sidebar = self.world()
        self.host.dispatch('conversation_archive', dict(projectId='p', channelId=sidebar))
        self.sure(sidebar)
        self.say('polish the sidebar spacing again', client='client-arch0002')
        self.assertEqual(len(self.prompts), 1)
        (logged,) = self.decisions('route')
        self.assertEqual((logged['decision']['kind'], logged['decision']['candidates'][0]['thread_id'], logged['applied']),
                         ('unsure', sidebar, False))

    def test_a_command_the_module_didn_t_mark_still_goes_to_home(self):
        _, picker, sidebar = self.world()
        self.sure(sidebar)  # no command: the module missed it
        self.say('archive the old sidebar polish thread', client='client-cmd00001')
        self.assertEqual((len(self.prompts), self.sent), (1, []))

    def test_a_waiting_message_is_routed_once_its_turn_comes(self):
        _, picker, _ = self.world()
        turns = NowTurns(hold=True)
        self.host.home_turns = turns
        self.sure(picker)
        # Below AUTO_ROUTE it waits for the answer before it (a sure one goes at once: BusyTests).
        self.router.route_answer = lambda text, *_: dict(kind='existing', thread_id=picker, confidence=0.7, why='x') \
            if text == 'make it red' else None
        self.say('first', client='client-turn0001')
        waiting = self.say('make it red', client='client-turn0002')['entries'][-1]
        self.assertTrue(waiting.get('held'))
        turns.release()
        self.orch('list')  # Home on screen applies the first answer, and the waiting message goes
        reply = self.entry(waiting['id'])
        self.assertEqual((reply['state'], reply['routed']['kind'], reply['text']), ('ready', 'existing', 'Added to Connector picker.'))

    def test_a_message_for_a_named_agent_s_thread_goes_through_home_s_model(self):
        _, picker, _ = self.world()
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS collab_agents (name TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT INTO collab_agents VALUES (?,?)', ('reviewer', json.dumps(dict(channel_id=picker))))
        self.sure(picker)
        self.say('also make the warning red', client='client-agent001')
        self.assertEqual(len(self.prompts), 1, "an agent's thread is never given your words by the router alone")
        self.assertEqual([row['applied'] for row in self.decisions('route')], [False])
        row = next(row for row in self.decisions('route')[0]['input']['threads'] if row['id'] == picker)
        self.assertTrue(row['agent'])

    def test_a_bug_report_is_still_logged_by_code_not_routed(self):
        _, picker, _ = self.world()
        self.sure(picker)
        reply = self.say('bug: the picker flickers', client='client-bug00001')['entries'][-1]
        self.assertTrue(reply.get('logged_by_code'))
        self.assertEqual(self.decisions('route'), [])


class BusyTests(RouterFixture):
    """9 Oct (Bryce: "super fluid just like Claude projects"): while Home is answering one message, a message the router
    is sure continues a thread goes there at once. Anything that needs the model still waits its turn."""

    def busy(self):
        turns = NowTurns(hold=True)
        self.host.home_turns = turns
        self.router.route_answer = None
        first = self.say('first', client='client-first001')['entries'][-1]  # Home's model is on this one, and stays on it
        self.assertEqual(first['state'], 'thinking')
        return turns

    def test_a_sure_follow_up_goes_now_while_home_is_thinking(self):
        _, picker, _ = self.world()
        turns = self.busy()
        self.sure(picker, why='Names Connector picker')
        reply = self.say('make the connector picker warning red', client='client-busy0001')['entries'][-1]
        self.assertNotIn('held', reply)
        self.assertEqual((reply['state'], reply['text']), ('ready', 'Added to Connector picker.'))
        self.assertEqual(self.prompts, [], 'no model call for it')
        self.ticks()
        self.assertEqual([request['channelId'] for request in self.sent], [picker])
        self.assertEqual(self.orch('list')['entries'][1]['state'], 'thinking', 'the first answer is still going')
        turns.release()

    def test_a_message_that_leans_on_the_one_before_waits_even_when_it_names_a_thread(self):
        """Review, 9 Oct: "make it red in the connector picker" leans on the answer still thinking ("it"), whose
        thread the router can't see yet, so it waits for that answer."""
        _, picker, _ = self.world()
        turns = self.busy()
        self.sure(picker, why='Names Connector picker')
        reply = self.say('make it red in the connector picker', client='client-lean0001')['entries'][-1]
        self.assertTrue(reply.get('held'), reply)
        self.assertEqual(self.sent, [])
        turns.release()

    def test_chatter_that_names_a_thread_never_starts_work(self):
        """Polish review, 9 Oct: "rename nothing, just checking" became a follow-up that ran an agent. Chatter is
        Home's to answer: it waits while Home is busy, and never goes to the thread as a job."""
        _, picker, _ = self.world()
        turns = self.busy()
        self.sure(picker, why='Names Connector picker')
        reply = self.say('change nothing in the connector picker, just checking', client='client-chat0001')['entries'][-1]
        self.assertTrue(reply.get('held'), reply)
        turns.release()
        self.ticks()
        self.assertEqual(self.sent, [], 'no agent ran on it')

    def test_the_first_answer_s_offer_stays_open_after_a_follow_up_went_ahead(self):
        """Review, 9 Oct: the follow-up was said before you'd read the first answer, so it neither lapses that
        answer's offer nor becomes the reply a yes answers."""
        _, picker, _ = self.world()
        self.reply = lambda prompt: answer('The picker is waiting on you. Want me to start the docs page for connectors?')
        turns = self.busy()
        self.sure(picker, why='Names Connector picker')
        went = self.say('make the connector picker warning red', client='client-busy0002')['entries'][-1]
        self.assertEqual(went['state'], 'ready')
        turns.release()
        self.ticks()
        first = next(row for row in self.orch('list')['entries'] if row.get('text', '') and 'docs page' in row['text'])
        self.assertEqual(first['state'], 'ready')
        self.assertFalse(po.Orchestrator.lapsed(self.orch('list')['entries'], first))
        self.router.route_answer = None
        docs = dict(kind='start', title='Connectors docs page', workstream='Connectors', instruction='Write the docs page for connectors.')
        self.reply = lambda prompt: answer('Started.', actions=[docs])
        turns.hold = False
        reply = self.say('go', client='client-go000001')['entries'][-1]
        self.assertEqual([part['title'] for part in reply['parts']], ['Connectors docs page'], reply)

    def test_anything_that_needs_the_model_still_waits_its_turn(self):
        _, picker, _ = self.world()
        turns = self.busy()
        for n, (text, answer_) in enumerate((
                ('make the warning red', dict(kind='existing', thread_id=picker, confidence=0.7, why='maybe')),
                ('is the picker warning done?', dict(kind='existing', thread_id=picker, confidence=0.95, why='names it')),
                ('add a dark mode toggle', dict(kind='new', thread_id=None, name='Dark mode', confidence=0.95, why='new')))):
            with self.subTest(text=text):
                self.router.route_answer = dict(answer_, candidates=[])
                reply = self.say(text, client=f'client-wait{n:04d}')['entries'][-1]
                self.assertTrue(reply.get('held'), reply)
                self.assertEqual(reply['state'], 'thinking')
        self.assertEqual((self.prompts, self.sent), ([], []), 'nothing started ahead of the answer before it')
        turns.release()


class NameTests(RouterFixture):
    def organisation(self, thread):
        return self.host.dispatch('organisation_get', dict(projectId='p', channelId=thread))

    def submit(self, thread, text, source='m1', visibility='shared'):
        from conversation_organisation import Organisation
        binding = dict(id=thread, project_id='p', visibility=visibility, category='personal', context='personal')
        Organisation(self.host.store).submitted(binding, 'request', source, text)

    def test_the_first_message_names_the_thread_with_the_router_s_name_once_it_s_sure(self):
        thread = self.conversation()
        self.assertEqual(self.organisation(thread)['name'], 'New conversation')
        self.router.name_answer = lambda row, *_: dict(title='Fix the picker warning', confidence=0.95)
        self.submit(thread, 'the warning in the connector picker is too quiet, make it stand out')
        self.assertEqual(self.organisation(thread)['name'], 'Fix the picker warning')
        (args,) = [args for name, args in self.router.calls if name == 'name']
        self.assertEqual(args[0]['messages'], ['the warning in the connector picker is too quiet, make it stand out'])
        logged = self.decisions('name')
        self.assertEqual((len(logged), logged[0]['applied'], logged[0]['thread_id']), (1, True, thread))
        # Only the first message names it.
        self.router.name_answer = lambda row, *_: dict(title='Something else', confidence=0.99)
        self.submit(thread, 'and another thing', source='m2')
        self.assertEqual(self.organisation(thread)['name'], 'Fix the picker warning')

    def test_your_rename_teaches_the_router_and_the_agent_s_title_still_wins_once(self):
        auto = self.conversation()
        self.router.name_answer = lambda row, *_: dict(title='Router name', confidence=0.95)
        self.submit(auto, 'make the picker warning louder')
        state = self.organisation(auto)
        self.assertEqual(state['name'], 'Router name')
        self.host.dispatch('organisation_set', dict(projectId='p', channelId=auto, expectedRevision=state['revision'], name='Picker copy',
                                                    group=state['group'], nameLocked=True, groupLocked=state['groupLocked']))
        logged = next(row for row in self.decisions('name') if row['thread_id'] == auto)
        self.assertEqual(logged['outcome'], 'renamed:Picker copy')
        other = self.conversation()
        self.submit(other, 'tidy the sidebar spacing')
        from conversation_organisation import Organisation
        self.assertTrue(Organisation(self.host.store).take_title(other, 'Agent title', dict(kind='guide', id='t')))

    def test_a_name_applies_at_name_apply_below_auto_route(self):
        thread = self.conversation()
        self.router.name_answer = lambda row, *_: dict(title='Louder picker warning', confidence=0.85)
        self.submit(thread, 'make the picker warning louder')
        self.assertEqual(self.organisation(thread)['name'], 'Louder picker warning')
        self.assertEqual([row['applied'] for row in self.decisions('name')], [True])

    def test_below_auto_route_where_the_rules_abstain_or_in_a_private_thread_the_rules_name_stands(self):
        thread = self.conversation()
        self.router.name_answer = lambda row, *_: dict(title='Maybe this', confidence=0.7)
        self.submit(thread, 'make the picker warning louder')
        self.assertNotEqual(self.organisation(thread)['name'], 'Maybe this')
        self.assertEqual([row['applied'] for row in self.decisions('name')], [False])
        self.router.name_answer = lambda row, *_: dict(title='Sure name', confidence=0.99)
        secret = self.conversation()
        self.submit(secret, 'the password is hunter2, rotate it')
        self.assertEqual(self.organisation(secret)['name'], 'New conversation', 'the rules abstain, and so does the router')
        private = self.conversation(private=True)
        self.submit(private, 'make the picker warning louder', visibility='private')
        self.assertNotEqual(self.organisation(private)['name'], 'Sure name')
        self.assertEqual(len(self.decisions('name')), 1, 'neither was asked')


class SortTests(RouterFixture):
    def test_thread_order_uses_the_router_s_order_only_when_it_covers_every_thread(self):
        _, picker, sidebar = self.world()
        ids = {picker, sidebar}
        self.router.sort_answer = [dict(thread_id=sidebar, bucket='working', why='busy'), dict(thread_id=picker, bucket='idle')]
        self.assertEqual([row['thread_id'] for row in self.host.dispatch('thread_order', dict(projectId='p'))], [sidebar, picker])
        for bad in ([dict(thread_id=sidebar, bucket='working')],
                    [dict(thread_id=sidebar, bucket='working'), dict(thread_id=sidebar, bucket='idle'), dict(thread_id=picker, bucket='idle')],
                    [dict(thread_id=sidebar, bucket='soon'), dict(thread_id=picker, bucket='idle')]):
            self.router.sort_answer = bad
            order = self.host.dispatch('thread_order', dict(projectId='p'))
            self.assertEqual({row['thread_id'] for row in order}, ids)
            self.assertTrue(all(row['why'] is None for row in order), 'the plain order')
        thread_router._module = False
        self.assertEqual({row['thread_id'] for row in self.host.dispatch('thread_order', dict(projectId='p'))}, ids)
        with self.assertRaises(Rejected):
            self.host.dispatch('thread_order', dict(projectId='nope'))


class TidyTests(RouterFixture):
    def quiet(self, *threads, ago=3600):
        import project_lifecycle
        with self.host.store.db:
            project_lifecycle.schema(self.host.store.db)
            for thread in threads:
                self.host.store.db.execute('INSERT INTO conversation_touched VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET at=excluded.at',
                                           (thread, int(time.time()) - ago))

    def test_an_archive_the_router_is_sure_of_joins_the_launch_tidy_with_its_undo(self):
        _, picker, sidebar = self.world()
        self.quiet(picker, ago=2 * 86400)  # two days: too recent for the 14-day rule
        self.quiet(sidebar)  # an hour: too recent for anything, however sure the router is
        self.router.tidy_answer = [dict(thread_id=picker, action='archive', why='done and quiet', confidence=0.95),
                                   dict(thread_id=sidebar, action='archive', why='quiet', confidence=0.99)]
        tidied = self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[]))
        self.assertEqual([row['id'] for row in tidied['threads']], [picker])
        archived = {row['id'] for row in self.host.dispatch('conversations_archived', dict(projectId='p'))}
        self.assertEqual(archived, {picker})
        logged = {row['thread_id']: row for row in self.decisions('tidy')}
        self.assertEqual((logged[picker]['applied'], logged[sidebar]['applied']), (True, False))
        self.host.dispatch('conversations_tidy_undo', dict(projectId='p', batch=tidied['batch']))
        self.assertEqual(next(row for row in self.decisions('tidy') if row['thread_id'] == picker)['outcome'], 'unarchived')

    def test_never_archives_a_thread_that_needs_you_is_unread_kept_on_screen_or_while_tidy_is_off(self):
        _, picker, sidebar = self.world()
        self.quiet(picker, sidebar, ago=3 * 86400)
        self.settled(picker, 'Asked you which colour.', status='needs')
        self.router.tidy_answer = [dict(thread_id=thread, action='archive', why='quiet', confidence=0.99) for thread in (picker, sidebar)]
        tidied = self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[sidebar]))
        self.assertEqual(tidied['threads'], [])
        self.host.tidied_projects.clear()
        self.host.dispatch('project_tidy_set', dict(projectId='p', days=None))
        self.assertEqual(self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[]))['threads'], [])

    def test_with_tidy_off_only_bringing_back_stands(self):
        _, picker, sidebar = self.world()
        self.quiet(picker, ago=30 * 86400)
        self.host.dispatch('project_tidy_set', dict(projectId='p', days=None))
        seen = []
        self.router.tidy_answer = lambda rows, now, days, keep: seen.append(days) or [dict(thread_id=picker, action='archive', why='quiet', confidence=0.99)]
        self.assertEqual(self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[]))['threads'], [])
        self.assertEqual(seen, [None], 'asked with tidy off, and its archive dropped')

    def test_bring_back_and_pin_are_logged_as_suggestions_and_never_done_outside_the_batch(self):
        _, picker, sidebar = self.world()
        self.quiet(sidebar, ago=30 * 86400)
        tidied = self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[]))  # the 14-day rule puts it away
        self.assertEqual([row['id'] for row in tidied['threads']], [sidebar])
        self.host.tidied_projects.clear()
        self.router.tidy_answer = [dict(thread_id=sidebar, action='unarchive', why='active again', confidence=0.99),
                                   dict(thread_id=picker, action='pin', why='you open it daily', confidence=0.95)]
        self.host.dispatch('conversations_tidy', dict(projectId='p', keep=[]))
        self.assertIn(sidebar, {row['id'] for row in self.host.dispatch('conversations_archived', dict(projectId='p'))})
        self.assertEqual(self.host.dispatch('conversation_pins', dict(projectId='p')), [])
        self.assertEqual(sorted((row['thread_id'], row['applied']) for row in self.decisions('tidy')),
                         sorted([(sidebar, False), (picker, False)]))


class OutcomeTests(RouterFixture):
    def test_an_outcome_is_recorded_once_and_checked(self):
        _, picker, sidebar = self.world()
        self.sure(picker)
        reply = self.say('also make it red', client='client-outc0001')['entries'][-1]
        decision = reply['routed']['decision']
        self.assertEqual(self.host.dispatch('routing_outcome', dict(projectId='p', decisionId=decision, outcome='moved:' + sidebar)), {'recorded': True})
        self.assertEqual(self.host.dispatch('routing_outcome', dict(projectId='p', decisionId=decision, outcome='kept')), {'recorded': False})
        self.assertEqual(self.decisions('route')[0]['outcome'], 'moved:' + sidebar)
        with self.assertRaises(Rejected):
            self.host.dispatch('routing_outcome', dict(projectId='p', decisionId=decision, outcome='deleted'))

    def test_the_log_is_written_out_for_learn_py_after_an_outcome(self):
        import os, stat
        thread_router.log(self.host.store.db, 'p', 'route', dict(kind='home'), dict(text='hi', history=[], threads=[]))
        decision = thread_router.decisions(self.host.store.db, 'p')[0]['id']
        thread_router.outcome(self.host.store.db, decision, 'kept')
        with unittest.mock.patch.object(thread_router, '_exported_at', None):
            self.host.tick()
        path = os.path.join(str(self.host.root), thread_router.EXPORT_NAME.format(side='personal'))
        self.assertFalse(os.path.exists(os.path.join(str(self.host.root), thread_router.EXPORT_NAME.format(side='work'))))
        with open(path) as handle:
            (line,) = handle.read().splitlines()
        row = json.loads(line)
        self.assertEqual((row['op'], row['input']['text'], row['outcome']), ('route', 'hi', 'kept'))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_deleting_a_thread_forgets_every_decision_that_read_it(self):
        _, picker, sidebar = self.world()
        self.sure(picker)
        self.say('also make the warning red', client='client-forg0001')
        self.assertEqual(len(self.decisions('route')), 1)
        self.host.dispatch('conversation_archive', dict(projectId='p', channelId=sidebar))
        self.host.dispatch('conversation_delete', dict(projectId='p', channelId=sidebar, confirmation='DELETE ' + sidebar))
        self.assertEqual(self.decisions('route'), [], 'it read the deleted thread, so it goes too')

    def test_a_secret_is_never_logged(self):
        _, picker, _ = self.world()
        self.sure(picker)
        self.say('also rotate it, the password is hunter2', client='client-secr0001')
        self.assertEqual(self.decisions('route'), [])
        kept = thread_router._kept(dict(text='hi', thread=dict(id='t1', name='token: abc123', messages=['token: abc123', 'make it red'])), {})
        self.assertEqual(kept['thread']['messages'], [thread_router.WITHHELD, 'make it red'])
        self.assertEqual(kept['thread']['name'], thread_router.WITHHELD)
        thread_router.log(self.host.store.db, 'p', 'name', dict(title='the password is hunter2', confidence=0.9), {})
        self.assertEqual(self.decisions('name')[0]['decision']['title'], thread_router.WITHHELD, 'the decision is scanned too')

    def test_a_logged_decision_keeps_a_bounded_copy_of_what_it_read(self):
        rows = [dict(id=f't{n}', at=n, messages=['x' * 5000]) for n in range(100)]
        thread_router.log(self.host.store.db, 'p', 'route', dict(kind='existing', thread_id='t3', candidates=[]),
                          dict(text='hi', history=[], threads=rows))
        kept = thread_router.decisions(self.host.store.db, 'p')[0]['input']['threads']
        self.assertEqual(len(kept), thread_router.LOG_THREADS)
        self.assertEqual(kept[0]['id'], 't3', 'the one it named first')
        self.assertNotIn('messages', kept[0], 'ids, names and state only, never a thread\'s text')

    def test_the_export_appends_new_outcomes_and_rewrites_after_a_forget(self):
        import os
        db = self.host.store.db
        path = os.path.join(str(self.host.root), thread_router.EXPORT_NAME.format(side='personal'))
        def outcome(text, thread):
            thread_router.log(db, 'p', 'route', dict(kind='existing', thread_id=thread), dict(text=text, history=[], threads=[dict(id=thread)]),
                              thread_id=thread)
            thread_router.outcome(db, thread_router.decisions(db, 'p')[0]['id'], 'kept')
            with unittest.mock.patch.object(thread_router, '_exported_at', None):
                self.host.tick()
            with open(path) as handle:
                return [json.loads(line)['input']['text'] for line in handle.read().splitlines()]
        self.assertEqual(outcome('one', 'ta'), ['one'])
        self.assertEqual(outcome('two', 'tb'), ['one', 'two'], 'appended, not rewritten')
        thread_router.forget(db, ['ta'])
        with unittest.mock.patch.object(thread_router, '_exported_at', None):
            self.host.tick()
        with open(path) as handle:
            self.assertEqual([json.loads(line)['input']['text'] for line in handle.read().splitlines()], ['two'], 'a forget rewrites the file')

    def test_the_log_keeps_the_newest_per_project(self):
        with unittest.mock.patch.object(thread_router, 'LOG_KEEP', 3):
            for n in range(5):
                thread_router.log(self.host.store.db, 'p', 'route', dict(n=n), {})
        self.assertEqual([row['decision']['n'] for row in self.decisions()], [4, 3, 2])


if __name__ == '__main__':
    unittest.main()
