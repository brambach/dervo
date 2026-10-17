"""Atomic scoped channel binding for named agents: ``create_agent_conversation`` makes one shared conversation of a
project bound to an explicit owned personal account and model in a single step. It replaces the bind_project dance
(bind the project to the agent's account, create a conversation, bind it back) that the runtime slice needed. The
project's own binding, every existing conversation and every credential stay untouched; a refused request creates
nothing. Real Host and Store on a temporary directory; no provider, no account, no user data."""
import tempfile
import unittest
from pathlib import Path

from host import Host, ACTOR
from dervo_connections import Rejected


class AgentConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.host = Host(base / 'store')
        self.addCleanup(self.host.close)
        project = base / 'project'
        project.mkdir()
        (project / 'test_sample.py').write_text('import unittest\n')
        store = self.host.store
        store.add_profile('owner', 'codex', base / 'store/profiles/owner', 'me@example.test', 'personal')
        store.add_profile('cl', 'claude', base / 'store/profiles/cl', 'me@example.test', 'personal')
        store.add_profile('work', 'codex', base / 'store/profiles/work', 'work@example.test', 'work')  # one email is never both sides
        store.add_project('p', project, 'owner', 'owner-model')
        store.add_thread('main', 'p', 'main', 'shared', None)
        with store.db:
            store.db.execute('INSERT INTO local_channels VALUES (?,?)', ('main', 'Engineering'))

    def rows(self, table, key):
        return [dict(r) for r in self.host.store.db.execute(f'SELECT * FROM {table} WHERE id=?', (key,))]

    def test_creates_a_shared_conversation_bound_to_the_named_account_and_changes_nothing_else(self):
        main_before = dict(self.host.store.binding('main', 'p', actor=ACTOR))
        project_before = self.rows('projects', 'p')
        made = self.host.dispatch('create_agent_conversation', {'projectId': 'p', 'profileId': 'cl', 'model': 'claude-fable-5-1', 'name': ' Review room '})
        self.assertEqual((made['name'], made['visibility'], made['project_id'], made['profile_id'], made['provider'], made['model']),
                         ('Review room', 'shared', 'p', 'cl', 'claude', 'claude-fable-5-1'))
        thread = self.host.store.thread(made['id'], 'p')
        self.assertEqual((thread['profile_id'], thread['model'], thread['visibility'], thread['owner'], thread['session_id']),
                         ('cl', 'claude-fable-5-1', 'shared', None, None))
        # The project's own binding and the existing conversation are exactly as before.
        self.assertEqual(dict(self.host.store.binding('main', 'p', actor=ACTOR)), main_before)
        self.assertEqual(self.rows('projects', 'p'), project_before)
        rows = self.host.dispatch('conversations', {'projectId': 'p'})
        self.assertEqual([(r['name'], r['profile_id'], r['model'], r['visibility']) for r in rows],
                         [('Engineering', 'owner', 'owner-model', 'shared'), ('Review room', 'cl', 'claude-fable-5-1', 'shared')])
        # The new conversation is bound to the named account and is usable as an agent channel at once.
        binding = self.host.dispatch('binding', {'projectId': 'p', 'channelId': made['id']})
        self.assertEqual((binding['provider'], binding['identity'], binding['model'], binding['category']), ('claude', 'me@example.test', 'claude-fable-5-1', 'personal'))
        agent = self.host.dispatch('collab_agent_save', dict(name='reviewer', profileId='cl', channelId=made['id'], projectId='p', role='reviewer', instructions='Be exact.'))
        self.assertEqual((agent['state'], agent['channel_id'], agent['model'], agent['provider'], agent['tools']), ('ready', made['id'], 'claude-fable-5-1', 'claude', ['dervo_read_file']))
        # Creating a conversation grants nothing: the policy is still off, and the owner's conversation cannot be an agent's own.
        self.assertEqual(self.host.dispatch('conversation_status', {'projectId': 'p', 'channelId': made['id']})['collaboration']['state'], 'off')
        with self.assertRaises(Rejected):
            self.host.dispatch('collab_agent_save', dict(name='wrong', profileId='cl', channelId='main', projectId='p', role='reviewer', instructions=''))

    def test_refuses_work_or_unknown_accounts_bad_names_missing_model_unknown_project_and_extra_fields(self):
        good = {'projectId': 'p', 'profileId': 'cl', 'model': 'm', 'name': 'Room'}
        for bad in ({'profileId': 'work'}, {'profileId': 'ghost'}, {'profileId': 7}, {'name': ''}, {'name': 'x' * 101}, {'name': 3}, {'model': ''},
                    {'model': None}, {'projectId': 'nope'}, {'private': True}):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected):
                    self.host.dispatch('create_agent_conversation', {**good, **bad})
        self.assertEqual(len(self.host.dispatch('conversations', {'projectId': 'p'})), 1, 'a refused request creates nothing')
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM threads').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
