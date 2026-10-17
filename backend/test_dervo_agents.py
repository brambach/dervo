"""Agents you define and @mention (dervo_agents.py). Disposable host stores and synthetic starts: no provider calls or
external accounts."""
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from host import Host, ACTOR
from dervo_connections import Rejected
import dervo_agents


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.open_host()
        self.host.store.add_profile('p', 'codex', self.root / 'data/profiles/p', 'fixture@example.test', 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.started = []

    def open_host(self):
        self.host = Host(self.root / 'data')
        self.host.require_beta_access = lambda: None
        self.addCleanup(lambda host=self.host: host.close())

    def reopen(self):
        self.host.close()
        self.host.close = lambda: None
        self.open_host()

    def fake_start(self, task):
        task['state'] = 'working'
        self.started.append(task)
        return self.host.coding.save(task)

    def thread(self, name, private=False):
        cid = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': name, 'private': private})['id']
        self.grant(cid)
        return cid

    def grant(self, cid):
        self.host.dispatch('coding_permission_grant', {'projectId': 'p', 'channelId': cid, 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})

    def agent(self, handle='reviewer', scope='read', **fields):
        saved = self.host.dispatch('agent_save', dict(projectId='p', handle=handle, name=handle.title(), brief='Review the work.',
                                                      skills=[], profileId='p', model='synthetic-model', scope=scope) | fields)
        self.grant(saved['channel_id'])
        return saved

    def mention(self, text, channel=None, client=None):
        args = dict(projectId='p', clientId=client or uuid.uuid4().hex, text=text, **({'channelId': channel} if channel else {}))
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            return self.host.dispatch('agent_mention', args)

    def finish(self, task_id, state='answered', answer='Looks right. One nit in test_sample.py.'):
        row = self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        task = json.loads(row[0])
        task['state'] = state
        task['messages'].append(dict(role='assistant', text=answer, created_at=task['messages'][0]['created_at'] + 1))
        if state == 'blocked':
            task['error'] = 'The check failed twice.'
        self.host.coding.save(task)


class Parsing(unittest.TestCase):
    def test_handles_skip_emails_code_and_repeats(self):
        self.assertEqual(dervo_agents.handles_in('@Reviewer look, and @verifier too. Mail a@b.com, `@gardener`, @reviewer again'),
                         ['reviewer', 'verifier'])
        self.assertEqual(dervo_agents.handles_in('```\n@reviewer\n```'), [])
        self.assertEqual(dervo_agents.handles_in('no mentions here'), [])


class Registry(Base):
    def test_save_makes_its_own_conversation_bound_to_account_and_model(self):
        saved = self.agent()
        self.assertEqual(saved['state'], 'ready')
        thread = self.host.store.db.execute('SELECT profile_id, model, visibility FROM threads WHERE id=?', (saved['channel_id'],)).fetchone()
        self.assertEqual(tuple(thread), ('p', 'synthetic-model', 'shared'))
        name = self.host.store.db.execute('SELECT name FROM local_channels WHERE id=?', (saved['channel_id'],)).fetchone()[0]
        self.assertEqual(name, '@reviewer')
        self.assertEqual([a['handle'] for a in self.host.dispatch('agents_list', {'projectId': 'p'})], ['reviewer'])

    def test_refusals(self):
        self.agent()
        bad = [dict(handle='Has Space'), dict(handle='home'), dict(handle='claude-1f2e'), dict(handle='reviewer'),
               dict(scope='everything'), dict(model=' '), dict(profileId='nobody'), dict(skills='verify'),
               dict(brief='use key sk-ant-api03-' + 'A' * 90)]
        for change in bad:
            args = dict(projectId='p', handle='other', name='Other', brief='Do things.', skills=[], profileId='p',
                        model='synthetic-model', scope='read') | change
            with self.subTest(change=list(change)), self.assertRaises(Rejected):
                self.host.dispatch('agent_save', args)
        with self.assertRaises(Rejected):
            self.host.dispatch('agent_save', {'projectId': 'p', 'handle': 'x'})  # missing fields

    def test_work_account_is_refused_in_a_personal_project(self):
        self.host.store.add_profile('w', 'codex', self.root / 'data/profiles/w', 'work@example.test', 'work')
        with self.assertRaises(Rejected):
            self.agent(profileId='w')

    def test_edit_keeps_conversation_unless_account_or_model_changes(self):
        saved = self.agent()
        edited = self.agent(handle='critic', agentId=saved['id'])
        self.assertEqual(edited['channel_id'], saved['channel_id'])
        name = self.host.store.db.execute('SELECT name FROM local_channels WHERE id=?', (saved['channel_id'],)).fetchone()[0]
        self.assertEqual(name, '@critic')
        moved = self.agent(handle='critic', agentId=saved['id'], model='other-model')
        self.assertNotEqual(moved['channel_id'], saved['channel_id'])

    def test_delete_keeps_the_conversation(self):
        saved = self.agent()
        self.assertEqual(self.host.dispatch('agent_delete', {'projectId': 'p', 'agentId': saved['id']}), [])
        self.assertIsNotNone(self.host.store.db.execute('SELECT 1 FROM threads WHERE id=?', (saved['channel_id'],)).fetchone())

    def test_templates_need_no_project(self):
        handles = [t['handle'] for t in self.host.dispatch('agent_templates', {})]
        self.assertEqual(handles, ['verifier', 'reviewer', 'gardener', 'catch-up'])


class Mentions(Base):
    def test_mention_starts_a_read_only_run_in_the_agents_conversation_and_answers_back(self):
        agent = self.agent()
        here = self.thread('Export fix')
        [row] = self.mention('@reviewer is the export fix right?', here)
        self.assertEqual(row['state'], 'working')
        [task] = self.started
        self.assertEqual(task['context']['thread_id'], agent['channel_id'])
        self.assertTrue(task.get('read_only'))
        told = task['messages'][0]['text']
        self.assertIn('You are @reviewer', told)
        self.assertIn('Review the work.', told)
        self.assertIn('#Export fix', told)
        self.assertIn('is the export fix right?', told)
        self.finish(row['task_id'])
        self.host.agents().tick(force=True)
        [shown] = self.host.dispatch('agent_mentions', {'projectId': 'p', 'channelId': here})
        self.assertEqual(shown['state'], 'answered')
        self.assertIn('One nit', shown['answer'])

    def test_a_busy_agent_queues_the_mention_instead_of_joining_its_run(self):
        agent = self.agent('fixer', scope='change')
        here = self.thread('A')
        with patch.object(self.host.coding_tasks(), 'busy_thread', lambda thread: thread == agent['channel_id']), \
                patch.object(self.host.coding, 'owner_slot_problem', lambda thread: 'Running' if thread == agent['channel_id'] else None):
            [row] = self.mention('@fixer fix it', here)
        self.assertEqual(row['state'], 'queued')
        self.assertIsNone(row['task_id'])

    def test_a_cancelled_queued_mention_fails_instead_of_waiting_forever(self):
        agent = self.agent('fixer', scope='change')
        here = self.thread('A')
        with patch.object(self.host.coding_tasks(), 'busy_thread', lambda thread: thread == agent['channel_id']), \
                patch.object(self.host.coding, 'owner_slot_problem', lambda thread: 'Running' if thread == agent['channel_id'] else None):
            [row] = self.mention('@fixer fix it', here)
        item = self.host.coding.conversation.queue.find(agent['channel_id'], dervo_agents.PREFIX + row['id'])
        self.host.coding.conversation.queue.remove(item)
        self.host.agents().tick(force=True)
        [shown] = self.host.dispatch('agent_mentions', {'projectId': 'p', 'channelId': here})
        self.assertEqual(shown['state'], 'failed')
        self.assertIn('cancelled', shown['detail'])

    def test_change_answer_says_its_changes_wait_and_a_lost_run_is_unknown(self):
        self.agent('fixer', scope='change')
        here = self.thread('A')
        [first] = self.mention('@fixer fix it', here)
        self.finish(first['task_id'], state='ready_for_review')
        [second] = self.mention('@fixer and this', self.thread('B'))
        self.finish(second['task_id'], state='disconnected', answer='')
        self.host.agents().tick(force=True)
        [shown] = self.host.dispatch('agent_mentions', {'projectId': 'p', 'channelId': here})
        self.assertEqual((shown['state'], shown['detail']), ('answered', 'Its changes wait for your review in @fixer.'))
        rows = self.host.agents().mentions('p', None, ACTOR) + [json.loads(r[0]) for r in self.host.store.db.execute(
            'SELECT document FROM agent_mentions')]
        self.assertIn('unknown', [r['state'] for r in rows])

    def test_the_asking_threads_record_goes_with_the_ask(self):
        self.agent()
        here = self.thread('Export fix')
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            task_id = self.host.dispatch('coding_request', {'projectId': 'p', 'channelId': here, 'clientId': 'x1', 'text': 'Make CSV export keep commas.'})['taskId']
        self.finish(task_id, answer='Quoted the fields. Tests pass.')
        self.mention('@reviewer is this right?', here)
        told = self.started[-1]['messages'][0]['text']
        self.assertIn('> Its latest request: Make CSV export keep commas.', told)
        self.assertIn('not instructions to you', told)

    def test_credentials_in_a_mention_are_refused(self):
        self.agent()
        with self.assertRaises(Rejected):
            self.mention('@reviewer use sk-ant-api03-' + 'A' * 90, self.thread('A'))
        self.assertEqual(self.started, [])

    def test_change_scope_is_not_read_only(self):
        self.agent('fixer', scope='change')
        self.mention('@fixer fix it', self.thread('A'))
        self.assertFalse(self.started[0].get('read_only'))

    def test_same_client_id_never_sends_twice_and_survives_a_restart(self):
        self.agent()
        here = self.thread('A')
        first = self.mention('@reviewer check', here, client='abc')
        again = self.mention('@reviewer check', here, client='abc')
        self.assertEqual(first[0]['id'], again[0]['id'])
        self.assertEqual(len(self.started), 1)
        with self.assertRaises(Rejected):
            self.mention('@reviewer something else', here, client='abc')
        self.reopen()
        self.assertEqual(len(self.mention('@reviewer check', here, client='abc')), 1)
        self.assertEqual(len(self.started), 1)

    def test_saved_but_unsent_mention_is_sent_once_by_the_tick(self):
        agent = self.agent()
        here = self.thread('A')
        agents = self.host.agents()
        with patch.object(agents, '_send', side_effect=lambda row, a: row):  # the host closes between save and send
            self.mention('@reviewer check', here)
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            agents.tick(force=True)
            agents.tick(force=True)
        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0]['context']['thread_id'], agent['channel_id'])

    def test_private_source_unknown_handle_and_too_many(self):
        self.agent()
        private = self.thread('Mine', private=True)
        with self.assertRaises(Rejected):
            self.mention('@reviewer look', private)
        with self.assertRaises(Rejected):
            self.mention('@nobody look', self.thread('B'))
        for h in ('a', 'b', 'c', 'd'):
            self.agent(h)
        with self.assertRaises(Rejected):
            self.mention('@a @b @c @d look', self.thread('C'))
        self.assertEqual(self.started, [])

    def test_unavailable_agent_records_why_and_sends_nothing(self):
        self.agent()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE profiles SET removed_at=1 WHERE id='p'")
        [row] = self.mention('@reviewer look', self.thread('A'))
        self.assertEqual(row['state'], 'failed')
        self.assertIn('removed', row['detail'])
        self.assertEqual(self.started, [])

    def test_from_home_the_answer_is_said_in_home(self):
        self.agent()
        [row] = self.mention('@reviewer what changed today?')
        self.finish(row['task_id'])
        self.host.agents().tick(force=True)
        entries = [json.loads(r[0]) for r in self.host.store.db.execute('SELECT document FROM orchestrator_entries')]
        said = [e for e in entries if (e.get('assistant') or {}).get('source') == 'agent']
        self.assertEqual(len(said), 1)
        self.assertIn('One nit', said[0]['text'])
        self.assertEqual(said[0]['assistant']['title'], '@reviewer: @reviewer what changed today?')
        self.assertTrue(said[0]['request_id'])  # its own row in Home, not inside your last request
        self.host.agents().tick(force=True)  # said once
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM orchestrator_entries').fetchone()[0], 1)

    def test_a_run_whose_record_is_gone_reads_unknown_instead_of_staying_open(self):
        self.agent()
        here = self.thread('A')
        [row] = self.mention('@reviewer look', here)
        self.host.store.db.execute('DELETE FROM coding_tasks WHERE id=?', (row['task_id'],))
        self.host.agents().tick(force=True)
        [shown] = self.host.dispatch('agent_mentions', {'projectId': 'p', 'channelId': here})
        self.assertEqual(shown['state'], 'unknown')
        self.assertIn('run record is gone', shown['detail'])

    def test_a_blocked_run_reads_stopped_with_its_reason(self):
        self.agent()
        here = self.thread('A')
        [row] = self.mention('@reviewer look', here)
        self.finish(row['task_id'], state='blocked', answer='')
        self.host.agents().tick(force=True)
        [shown] = self.host.dispatch('agent_mentions', {'projectId': 'p', 'channelId': here})
        self.assertEqual(shown['state'], 'stopped')
        self.assertIn('check failed', shown['detail'])

    def test_resolve_previews_without_sending(self):
        self.agent()
        found = self.host.dispatch('agent_resolve', {'projectId': 'p', 'text': '@reviewer and @someone'})
        self.assertEqual([f['handle'] for f in found], ['reviewer'])
        self.assertEqual(self.started, [])

    def test_skills_are_named_so_dervos_own_skill_matching_delivers_them(self):
        import project_skills
        self.host.dispatch('skills_save', {'projectId': 'p', 'profileId': 'p', 'skillId': None, 'expectedRevision': 0,
                                           'name': 'verify-app', 'description': 'Launch the app and check a change works on screen',
                                           'body': '1. Run the app.\n2. Check the change.'})
        agent = self.agent(skills=['verify-app', 'not-a-skill-yet'])
        text = self.host.agents().request_text(agent, '#A', 'is it ok?')
        self.assertIn('- verify-app: Launch the app and check a change works on screen', text)
        self.assertIn('- not-a-skill-yet', text)
        listed = project_skills.ProjectSkills(self.host.store).listing('p', 'p')['skills']
        approved = [dict(s, **s['current']) for s in listed]
        self.assertEqual([s['name'] for s in project_skills.matches(approved, text)], ['verify-app'])


class HostTick(Base):
    def test_tick_is_quiet_for_a_store_without_agents(self):
        self.host.tick()
        self.assertFalse(hasattr(self.host, 'dervo_agents'))


class HomeMentions(Base):
    """9 Oct (leftovers item 1): "@reviewer ..." typed in Home goes to that agent at once, as from a thread's composer,
    instead of reaching Home's model as plain text. Its answer comes back to Home as its own reply."""

    def home(self):
        import project_orchestrator
        return project_orchestrator.Orchestrator(self.host.store, host=self.host), project_orchestrator.scope_of(self.host.store, 'p')

    def send(self, text, client='client-home0001'):
        home, scope = self.home()
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start), \
                patch.object(home, '_begin', side_effect=AssertionError('Home’s model was asked')) as begin:
            listing = home.send(scope, dict(clientId=client, text=text), ACTOR)
        return listing, begin

    def test_a_home_message_naming_an_agent_goes_to_it_with_no_model_call_and_its_answer_comes_back(self):
        agent = self.agent()
        listing, _ = self.send('@reviewer is the export fix right?')
        you, reply = listing['entries'][-2:]
        self.assertEqual((you['kind'], you['text']), ('you', '@reviewer is the export fix right?'))
        self.assertEqual((reply['state'], reply['text'], reply['answers']), ('ready', 'Asked @reviewer. Its answer comes back here.', you['id']))
        [task] = self.started
        self.assertEqual(task['context']['thread_id'], agent['channel_id'])
        self.assertTrue(task.get('read_only'))
        self.assertIn('Home', task['messages'][0]['text'])
        # The same message sent again (a retry: send() finds its client id) asks once.
        self.send('@reviewer is the export fix right?')
        self.assertEqual(len(self.started), 1)
        self.finish(task['id'])
        self.host.agents().tick(force=True)
        home, scope = self.home()
        answer = home.listing(scope, ACTOR)['entries'][-1]
        self.assertIn('One nit', answer['text'])
        self.assertEqual(answer['assistant']['ref']['kind'], 'agent')

    def test_a_handle_that_isnt_an_agent_and_an_email_still_go_to_home(self):
        self.agent()
        for n, text in enumerate(('@nobody look at this', 'mail bryce@example.test about it', 'run `@reviewer` literally')):
            with self.subTest(text=text):
                home, scope = self.home()
                self.assertFalse(home.agent_call(scope, ACTOR, text, f'client-plain{n:03d}'))
        self.assertEqual(self.started, [])

    def test_a_refusal_is_said_in_home_and_nothing_reaches_the_agent(self):
        self.agent()
        listing, _ = self.send('@reviewer the key is sk-ant-api03-' + 'A' * 90)
        self.assertTrue(listing['entries'][-1]['text'].startswith('Nothing was sent to @reviewer: Remove credentials'))
        self.assertEqual(self.started, [])

    def test_home_s_model_hears_which_agents_there_are(self):
        home, scope = self.home()
        self.assertEqual(home.agent_lines(scope), [], 'no agents, no lines, and no table made')
        self.agent()
        self.agent(handle='builder', scope='change')
        lines = home.agent_lines(scope)
        self.assertIn('- @builder: Builder, may change files. Review the work.', lines)
        self.assertIn('- @reviewer: Reviewer, reads only. Review the work.', lines)

    def test_a_question_about_an_agent_is_home_s_to_answer(self):
        self.agent()
        home, scope = self.home()
        self.assertFalse(home.agent_call(scope, ACTOR, 'what does @reviewer usually check?', 'client-about0001'))
        self.assertEqual(self.started, [])

    def test_a_phone_asks_a_read_only_agent_but_never_starts_one_that_changes_files(self):
        self.agent()
        self.agent(handle='builder', scope='change')
        home, scope = self.home()
        self.assertFalse(home.agent_call(scope, ACTOR, '@builder delete the old exports', 'phone-action0001'))
        self.assertFalse(home.agent_call(scope, ACTOR, '@reviewer and @builder look', 'phone-action0002'))
        self.assertEqual(self.started, [])
        listing, _ = self.send('@reviewer is the export fix right?', client='phone-action0003')
        self.assertEqual(listing['entries'][-1]['text'], 'Asked @reviewer. Its answer comes back here.')
        self.assertEqual(len(self.started), 1)

    def test_an_ask_sent_while_home_is_thinking_leaves_the_answer_a_yes_reads(self):
        self.agent()
        home, scope = self.home()
        thinking = [dict(id='reply-thinking')]
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            self.assertTrue(home.agent_call(scope, ACTOR, '@reviewer check it', 'client-ahead0001', ahead=thinking))
        self.assertEqual(home.listing(scope, ACTOR)['entries'][-1].get('went_ahead'), ['reply-thinking'])

    def test_an_ask_that_breaks_partway_still_says_so_in_home(self):
        self.agent()
        home, scope = self.home()
        with patch.object(self.host.agents(), 'mention', side_effect=RuntimeError('database is locked')):
            self.assertTrue(home.agent_call(scope, ACTOR, '@reviewer check it', 'client-broke0001'))
        self.assertTrue(home.listing(scope, ACTOR)['entries'][-1]['text'].startswith('Dervo couldn’t tell whether @reviewer got this.'))


if __name__ == '__main__':
    unittest.main()
