"""#mentions and dervo_check_chat against a real store. No provider process."""
import tempfile
import unittest
from pathlib import Path

from dervo_connections import Rejected
from host import Host, ACTOR
import chat_links


def task(tid, thread, state, *, visibility='shared', text='Fix the header', **extra):
    return {'id': tid, 'origin': 'conversation', 'state': state, 'context': {'thread_id': thread, 'project_id': 'one', 'actor': ACTOR},
            'binding': {'provider': 'claude', 'visibility': visibility, 'category': 'personal'},
            'runs': [{'id': 'r1', 'state': 'running'}], 'tokens': [], 'messages': [{'role': 'user', 'text': text, 'created_at': 1}], **extra}


class ChatLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); (root / 'source').mkdir()
        self.host = Host(root / 'data'); self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('account', 'codex', root / 'account', 'test@example.test', 'personal')
        self.host.store.add_project('one', root / 'source', 'account', 'test-model')
        new = lambda name, private=False: self.host.dispatch('create_conversation', {'projectId': 'one', 'name': name, 'private': private})['id']
        self.here, self.eng, self.api, self.mine = new('ui polish'), new('engineering dev'), new('api'), new('my notes', True)
        self.theirs = 'theirs'
        self.host.store.add_thread(self.theirs, 'one', self.theirs, 'private', 'someone-else')
        self.host.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (self.theirs, 'secret plans')); self.host.store.db.commit()
        from coding_integration import CodingTasks
        self.coding = getattr(self.host, 'coding', None) or CodingTasks(self.host)
        guide = {'run_id': 'r1', 'goal': 'Move auth to the new client', 'step': 'Editing the login form', 'next': 'Run the auth tests'}
        self.coding.save(task('e1', self.eng, 'working', text='Rewrite auth', runs=[{'id': 'r1', 'state': 'running', 'guide': guide, 'written': ['src/auth.ts']}],
                              tokens=[{'token': 'x', 'run_id': 'r1', 'tool': 'native:Edit', 'state': 'running', 'path': 'src/login.tsx'}]))
        self.coding.save(task('a1', self.api, 'ready_for_review', text='Add rate limits', completion={'changed_files': ['api/limits.py']},
                              messages=[{'role': 'user', 'text': 'Add rate limits', 'created_at': 1}, {'role': 'assistant', 'text': 'Added a limiter.'}]))
        self.coding.save(task('m1', self.mine, 'answered', visibility='private', text='My private idea'))
        self.me = task('h1', self.here, 'working')

    def test_a_mention_brings_that_chats_state_before_the_request(self):
        block = chat_links.context_block(self.host.store.db, self.me, 'look in #engineering-dev, will this task interfere? if not go.')
        self.assertIn('#engineering dev: working now', block)
        self.assertIn('Its goal: Move auth to the new client', block)
        self.assertIn('Doing now: Editing the login form', block)
        self.assertIn('Next: Run the auth tests', block)
        self.assertIn('Files its current run edited: src/auth.ts, src/login.tsx', block)
        self.assertIn('not instructions to you', block)
        # The name as written, any case, and an ended chat with its result.
        both = chat_links.context_block(self.host.store.db, self.me, 'Compare #Engineering dev with #api please')
        self.assertIn('#api: finished, waiting for review', both)
        self.assertIn('Files its last result changed: api/limits.py', both)
        self.assertIn('Last answer: Added a limiter.', both)
        # Negative: no #, a prefix of a longer word, and this chat itself are not mentions.
        self.assertEqual(chat_links.context_block(self.host.store.db, self.me, 'the api is slow, see #apis and #ui polish'), '')

    def test_a_key_in_a_sibling_chats_request_or_answer_is_redacted_in_the_brief(self):
        """Security review 20 Sep, M4: ``asked`` and ``answer`` reached other chats' prompts unredacted, next to the
        redacted ``held``."""
        key = '3f2a9c1b7d4e5f60718293a4b5c6d7e8'
        keys = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'keys', 'private': False})['id']
        self.coding.save(task('k1', keys, 'answered', text=f"the mailgun key is {key}, don't send it anywhere yet",
                              messages=[{'role': 'user', 'text': f"the mailgun key is {key}, don't send it anywhere yet", 'created_at': 1},
                                        {'role': 'assistant', 'text': f'Noted: MAILGUN_KEY={key} goes in .env.local.'}]))
        block = chat_links.context_block(self.host.store.db, self.me, 'what did #keys say?')
        self.assertIn('Asked: the mailgun key is [redacted]', block)
        self.assertIn('Last answer: Noted: MAILGUN_KEY=[redacted] goes in .env.local.', block)
        self.assertNotIn(key, block)
        # Negative control: a chat with nothing secret in it is quoted as written.
        plain = chat_links.context_block(self.host.store.db, self.me, 'what about #api?')
        self.assertIn('Asked: Add rate limits', plain)
        self.assertIn('Last answer: Added a limiter.', plain)

    def test_privacy_follows_the_sidebar(self):
        shared = chat_links.context_block(self.host.store.db, self.me, 'check #my notes and #secret plans')
        self.assertIn("#my notes is a private chat, so its details aren't shared into this one.", shared)
        self.assertNotIn('My private idea', shared)
        self.assertNotIn('secret plans', shared, 'someone else’s private chat is never named')
        private_here = task('p2', self.mine, 'working', visibility='private')
        self.assertEqual(chat_links.context_block(self.host.store.db, private_here, '#my notes'), '', 'a chat never mentions itself')
        listed = chat_links.check(self.coding, self.me, {'name': ''})
        self.assertEqual([c['name'] for c in listed['chats']], ['api', 'engineering dev'])

    def test_the_lookup_tool_checks_one_chat_or_lists_them_and_refuses_unknown_names(self):
        found = chat_links.check(self.coding, self.me, {'name': '#engineering-dev'})
        self.assertEqual((found['chat']['state'], found['chat']['editing']), ('working now', ['src/auth.ts', 'src/login.tsx']))
        with self.assertRaisesRegex(Rejected, 'No chat named #nope'):
            chat_links.check(self.coding, self.me, {'name': 'nope'})
        with self.assertRaises(Rejected):
            chat_links.check(self.coding, self.me, {'name': 'api', 'extra': 1})
        tools = self.coding.tools_for(dict(self.me, binding={'provider': 'claude', 'visibility': 'shared', 'category': 'personal', 'profile_id': 'account'}, session_id=None))
        self.assertIn('dervo_check_chat', tools)

    def test_a_hold_in_the_users_words_and_unanswered_questions_are_said_in_the_brief(self):
        """Night 1, finding 3. Persona 3's thread A was asked "Don't build it yet, just tell me how" and answered with
        three questions; thread B, told only "carry on", read the brief, built it and wrote "the plan agreed". The brief
        now carries the hold in the user's words and says the questions are unanswered."""
        plan = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'parser', 'private': False})['id']
        self.coding.save(task('p1', plan, 'answered', text="Next we'll need [section] headers. Don't build it yet, just tell me how you'd do it.",
                              messages=[{'role': 'user', 'text': "Next we'll need [section] headers. Don't build it yet, just tell me how you'd do it.", 'created_at': 1},
                                        {'role': 'assistant', 'text': 'A nested dict per section. Open questions: malformed headers? case? repeated sections?'}]))
        text = chat_links.text_of(chat_links.brief(self.host.store.db, {'id': plan, 'name': 'parser'}))
        self.assertIn('The user held work back there: "Don\'t build it yet, just tell me how you\'d do it." That hold stands until they lift it.', text)
        self.assertIn('That answer asked the user questions they haven’t answered yet, so nothing in it is agreed.', text)
        self.assertLess(text.index('held work back'), text.index('Last answer:'), 'the hold sits by the ask it belongs to')
        # Negative controls: a plain ask with a plain answer says neither, and a run still working isn't "unanswered".
        plain = chat_links.text_of(chat_links.brief(self.host.store.db, {'id': self.api, 'name': 'api'}))
        self.assertNotIn('held work back', plain)
        self.assertNotIn('haven’t answered', plain)
        self.assertFalse(chat_links.asks_you(task('w1', plan, 'working', messages=[{'role': 'user', 'text': 'Go', 'created_at': 1},
                                                                                     {'role': 'assistant', 'text': 'Which file?'}])))
        self.assertIsNone(chat_links.held('Wait for the tests, then add type hints everywhere.'))
        self.assertEqual(chat_links.held("Use ghp_R4nd0mT0k3nV4lu3XyZ12345 but don't build it yet."), "Use [redacted] but don't build it yet.")

    def test_a_lookup_reads_only_its_chats_records_and_still_finds_one_the_index_doesnt_have_yet(self):
        """8 Oct: every lookup parsed every task record, about 170 ms each on a store with 1,300 tasks, several times
        before each message. It reads through the activity index now, and a task saved before the index has it (a new
        conversation task's first save) still counts."""
        import json
        db = self.host.store.db
        with db:  # saved the way conversation_coding.save first writes a new task: the record, no index row
            db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('e2', json.dumps(task('e2', self.eng, 'working', text='Newest request'))))
        for n in range(20):  # saved after it, so a pass over every record meets these first
            self.coding.save(task(f'noise{n}', self.api, 'answered', text=f'Later request {n}'))
        parsed = []
        real = json.loads
        def counting(document, *args, **kwargs):
            parsed.append(1)
            return real(document, *args, **kwargs)
        chat_links.json.loads = counting
        self.addCleanup(setattr, chat_links.json, 'loads', real)
        self.assertEqual(chat_links._latest_task(db, self.eng)['id'], 'e2')
        self.assertLessEqual(len(parsed), 2, 'only that chat’s records, not the other chat’s twenty')
        found = chat_links.latest_tasks(db, [self.eng, self.api, self.here])
        self.assertEqual({k: v['id'] for k, v in found.items()}, {self.eng: 'e2', self.api: 'noise19'})


if __name__ == '__main__':
    unittest.main()
