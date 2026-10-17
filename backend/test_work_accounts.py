"""Work and Personal, both working (Bryce, 26 Sep 2026: "it's okay to separate work and personal, just make it very
obvious WHY and make sure both sides work"). A work account runs only Work projects and a personal one only Personal
projects: its own sign-in folder, its own identity check, never a fallback across. Memory, notes, rules and records stay
on their project's side. Real Host and store; the providers are the scripted peers the coding suites use, so no CLI,
model, account or network is touched.

Run with the audited Python 3.13 (/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13), with the beta
gate patched as /tmp/b16-nogate.py does, like test_conversation_coding."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import claude_coding
import connections_ui
import conversation_route
import project_ideas
from adapters import ClaudeAdapter
from dervo_connections import Rejected, Store, project_context, require_context, same_side
from host import ACTOR, Host
import test_conversation_coding as base

WORK = 'you@acme.test'
PERSONAL = 'personal@example.test'
TEAM = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': WORK, 'subscriptionType': 'team'}
MAX = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'email': PERSONAL, 'subscriptionType': 'max'}


def profile(category, identity, provider='claude', **extra):
    return dict(id='x', provider=provider, identity=identity, category=category, method='subscription', home='/tmp/x', **extra)


class IdentityTests(unittest.TestCase):
    """connections_ui.signed_in_as: the one rule the check, the sign-in and every run share."""

    def test_a_work_account_is_its_own_work_sign_in_and_nothing_else(self):
        work = profile('work', WORK)
        self.assertTrue(connections_ui.signed_in_as(work, TEAM))
        self.assertTrue(connections_ui.signed_in_as(work, dict(TEAM, subscriptionType='enterprise')))
        self.assertFalse(connections_ui.signed_in_as(work, MAX), 'your personal sign-in is never the work account')
        self.assertFalse(connections_ui.signed_in_as(work, dict(TEAM, email=WORK.upper() + 'x')))
        self.assertFalse(connections_ui.signed_in_as(work, dict(TEAM, authMethod='api_key')))

    def test_personal_rules_are_unchanged(self):
        personal = profile('personal', PERSONAL)
        self.assertTrue(connections_ui.signed_in_as(personal, MAX))
        self.assertFalse(connections_ui.signed_in_as(personal, dict(MAX, subscriptionType='enterprise')))
        self.assertFalse(connections_ui.signed_in_as(personal, TEAM))

    def test_codex_sides(self):
        work, personal = profile('work', WORK, 'codex'), profile('personal', PERSONAL, 'codex')
        account = lambda email, plan: {'account': {'type': 'chatgpt', 'email': email, 'planType': plan}}
        self.assertTrue(connections_ui.signed_in_as(work, account(WORK, 'team')))
        self.assertTrue(connections_ui.signed_in_as(work, account(WORK, 'business')))
        self.assertFalse(connections_ui.signed_in_as(work, account(PERSONAL, 'plus')))
        self.assertTrue(connections_ui.signed_in_as(personal, account(PERSONAL, 'plus')))
        self.assertFalse(connections_ui.signed_in_as(personal, account(PERSONAL, 'team')), 'personal Codex stays Plus or Pro')

    def test_codex_on_the_100_dollar_pro_plan_passes_on_both_sides(self):
        # Josh, feedback #9 on 16.2 (27 Sep 2026): ChatGPT's $100 Pro plan reads "prolite" and setup refused it.
        # signed_in_as is also what every run checks (adapters.CodexAdapter), so passing here passes both.
        work, personal = profile('work', WORK, 'codex'), profile('personal', PERSONAL, 'codex')
        account = lambda email, plan: {'account': {'type': 'chatgpt', 'email': email, 'planType': plan}}
        self.assertTrue(connections_ui.signed_in_as(personal, account(PERSONAL, 'prolite')))
        self.assertTrue(connections_ui.signed_in_as(work, account(WORK, 'prolite')))
        self.assertFalse(connections_ui.signed_in_as(personal, account(WORK, 'prolite')), 'the email still has to match')
        for plan in ('free', 'go', 'unknown', 'self_serve_business_prolite', 'Prolite', None):
            self.assertFalse(connections_ui.signed_in_as(personal, account(PERSONAL, plan)), plan)

    def test_an_unmarked_account_signs_in_as_nothing(self):
        self.assertFalse(connections_ui.signed_in_as(profile('both', WORK), TEAM))
        self.assertFalse(connections_ui.signed_in_as(profile(None, WORK), TEAM))

    def test_the_check_names_the_expected_work_email_when_it_is_the_wrong_account(self):
        result = connections_ui.classify(profile('work', WORK), MAX)
        self.assertEqual(result['state'], 'identity_mismatch')
        self.assertIn(WORK, result['detail'])
        self.assertIn('work email', result['detail'])
        self.assertEqual(connections_ui.classify(profile('work', WORK), TEAM)['state'], 'identity_verified')

    def test_the_run_time_checks_use_the_same_rule(self):
        work = dict(profile('work', WORK), cwd='/tmp/p', model='m', session_id=None)
        adapter = ClaudeAdapter(work, lambda *a: None)
        adapter.authenticate(TEAM)
        self.assertEqual(adapter.state, 'ready')
        adapter = ClaudeAdapter(work, lambda *a: None)
        with self.assertRaisesRegex(Rejected, 'Work Claude subscription identity'):
            adapter.authenticate(MAX)
        self.assertEqual(adapter.state, 'blocked')


class StoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.folder = self.base / 'project'
        self.folder.mkdir()
        self.store = Store(self.base / 'store' / 'db.sqlite3')
        self.addCleanup(self.store.close)
        self.store.add_profile('me', 'claude', self.base / 'store/profiles/me', PERSONAL, 'personal')
        self.store.add_profile('acme', 'claude', self.base / 'store/profiles/acme', WORK, 'work')

    def test_one_email_is_never_both_a_work_and_a_personal_account(self):
        with self.assertRaisesRegex(Rejected, 'already your personal account'):
            self.store.add_profile('again', 'claude', self.base / 'store/profiles/again', PERSONAL.upper(), 'work')
        with self.assertRaisesRegex(Rejected, 'already your work account'):
            self.store.add_profile('again', 'claude', self.base / 'store/profiles/again', WORK, 'personal')
        # The same email on the other provider, or on the same side, is fine.
        self.store.add_profile('acme-codex', 'codex', self.base / 'store/profiles/acme-codex', PERSONAL, 'work')
        self.store.add_profile('acme-two', 'claude', self.base / 'store/profiles/acme-two', WORK, 'work')

    def test_a_project_is_marked_once_and_only_takes_accounts_of_its_side(self):
        self.store.add_project('w', self.folder, 'acme', 'm')
        self.assertEqual(project_context(self.store.db, 'w'), 'work')
        with self.assertRaisesRegex(Rejected, 'Work project'):
            self.store.bind_project('w', 'me', 'm')
        self.store.add_profile('acme-two', 'codex', self.base / 'store/profiles/acme-two', 'other@acme.test', 'work')
        self.store.bind_project('w', 'acme-two', 'm')
        with self.assertRaisesRegex(Rejected, 'Personal project'):
            self.store.add_project('p', self.folder, 'acme', 'm', context='personal')

    def test_every_binding_carries_its_projects_side_and_reading_one_never_refuses(self):
        self.store.add_project('w', self.folder, 'acme', 'm')
        self.store.add_thread('t', 'w', 't', 'shared')
        self.assertEqual(self.store.binding('t', 'w', actor=ACTOR)['context'], 'work')
        with self.store.db:  # however a thread's account got changed, it still opens, and says which side it's on
            self.store.db.execute("UPDATE threads SET profile_id='me' WHERE id='t'")
        binding = self.store.binding('t', 'w', actor=ACTOR)
        self.assertEqual((binding['context'], binding['category']), ('work', 'personal'))
        # Every run start checks it (connections_ui.owned_profile with the binding's context).
        with self.assertRaisesRegex(Rejected, 'only a work account can run it'):
            require_context(self.store.db, binding['context'], binding['profile_id'])

    def test_removing_or_signing_out_the_account_never_turns_a_work_project_personal(self):
        self.store.add_project('w', self.folder, 'acme', 'm')
        with self.store.db:
            self.store.db.execute("UPDATE profiles SET category='personal' WHERE id='acme'")  # as if the account were swapped under it
        self.assertEqual(project_context(self.store.db, 'w'), 'work', 'the mark is the project’s, not the account’s')
        with self.assertRaises(Rejected):
            require_context(self.store.db, 'work', 'acme')

    def test_an_older_store_gets_each_project_marked_from_its_account(self):
        path = self.base / 'old' / 'db.sqlite3'
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.executescript('''CREATE TABLE profiles (id TEXT PRIMARY KEY, provider TEXT NOT NULL, home TEXT UNIQUE NOT NULL,
            identity TEXT NOT NULL, category TEXT NOT NULL CHECK(category IN ('personal','work')));
            CREATE TABLE projects (id TEXT PRIMARY KEY, cwd TEXT NOT NULL, profile_id TEXT NOT NULL REFERENCES profiles(id), model TEXT NOT NULL);''')
        db.execute("INSERT INTO profiles VALUES ('me','claude','/tmp/me',?,'personal')", (PERSONAL,))
        db.execute("INSERT INTO profiles VALUES ('acme','claude','/tmp/acme',?,'work')", (WORK,))
        db.execute("INSERT INTO projects VALUES ('p','/tmp','me','m')")
        db.execute("INSERT INTO projects VALUES ('w','/tmp','acme','m')")
        db.commit(); db.close()
        store = Store(path)
        self.addCleanup(store.close)
        self.assertEqual(dict(store.db.execute('SELECT id, context FROM projects').fetchall()), {'p': 'personal', 'w': 'work'})

    def test_same_side(self):
        self.assertTrue(same_side('work', 'work'))
        self.assertTrue(same_side('personal'))
        self.assertFalse(same_side('work', 'personal'))
        self.assertFalse(same_side())
        self.assertFalse(same_side(None, None))


class HostTests(unittest.TestCase):
    """Account and project operations on a real Host: each side only ever reaches its own accounts."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.addCleanup(self.host.close)
        for name in ('personal', 'work'):
            (self.base / name).mkdir()
        self.me = self.host.dispatch('add_profile', dict(provider='claude', identity=PERSONAL, category='personal'))['id']
        self.me_codex = self.host.dispatch('add_profile', dict(provider='codex', identity=PERSONAL, category='personal'))['id']
        self.acme = self.host.dispatch('add_profile', dict(provider='claude', identity=WORK, category='work'))['id']
        self.acme_codex = self.host.dispatch('add_profile', dict(provider='codex', identity=WORK, category='work'))['id']
        import project_setup
        self.personal, _ = project_setup.make(self.host, str(self.base / 'personal'), self.me, 'claude-opus-5', 'tidepool')
        self.work, self.starter = project_setup.make(self.host, str(self.base / 'work'), self.acme, 'claude-opus-5', 'billing-api')

    def test_a_work_account_has_its_own_sign_in_folder(self):
        work = connections_ui.owned_profile(self.host.store, self.host.root, self.acme, context='work')
        me = connections_ui.owned_profile(self.host.store, self.host.root, self.me, context='personal')
        self.assertEqual(Path(work['home']), self.host.root / 'profiles' / self.acme)
        self.assertNotEqual(work['home'], me['home'])
        self.assertTrue(Path(work['home']).is_dir())
        with self.assertRaisesRegex(Rejected, 'only a personal account'):
            connections_ui.owned_profile(self.host.store, self.host.root, self.acme, context='personal')
        with self.assertRaisesRegex(Rejected, 'only a work account'):
            connections_ui.owned_profile(self.host.store, self.host.root, self.me, context='work')

    def test_a_work_account_never_uses_your_existing_claude_code_folder(self):
        import claude_policy
        with self.host.store.db:
            self.host.store.db.execute('UPDATE profiles SET home=? WHERE id=?', (str(claude_policy.system_config_dir()), self.acme))
        with self.assertRaisesRegex(Rejected, 'its own sign-in folder'):
            connections_ui.owned_profile(self.host.store, self.host.root, self.acme)

    def test_the_sign_in_command_runs_in_the_work_folder_and_names_the_work_email(self):
        with patch('claude_policy.executable', return_value='/fixture/claude'):
            told = self.host.dispatch('connection_instructions', {'profileId': self.acme})
        self.assertIn(str(self.host.root / 'profiles' / self.acme), told['command'])
        self.assertIn(WORK, told['detail'])
        self.assertIn('work account', told['detail'])

    def test_a_thread_or_project_only_moves_to_an_account_on_its_side(self):
        with self.assertRaisesRegex(Rejected, 'only a work account'):
            self.host.dispatch('conversation_profile_set', dict(projectId=self.work, channelId=self.starter, profileId=self.me,
                                                                 expectedProfileId=self.acme, model='claude-opus-5', expectedModel='claude-opus-5'))
        moved = self.host.dispatch('conversation_profile_set', dict(projectId=self.work, channelId=self.starter, profileId=self.acme_codex,
                                                                    expectedProfileId=self.acme, model='gpt-5.5', expectedModel='claude-opus-5'))
        self.assertEqual(moved['profile_id'], self.acme_codex)
        with self.assertRaisesRegex(Rejected, 'only a work account'):
            self.host.dispatch('project_profile_set', dict(projectId=self.work, profileId=self.me_codex, expectedProfileId=self.acme,
                                                           model='gpt-5.5', expectedModel='claude-opus-5'))
        with self.assertRaisesRegex(Rejected, 'only a personal account'):
            self.host.dispatch('create_agent_conversation', dict(projectId=self.personal, profileId=self.acme, model='claude-opus-5', name='Reviewer'))

    def test_switching_provider_on_a_limit_stays_on_the_projects_side(self):
        binding = self.host.store.binding(self.starter, self.work, actor=ACTOR)
        # A personal Codex thread is the most recent other-provider thread on this Mac; a work project still never picks it.
        personal_thread = self.host.store.db.execute('SELECT id FROM threads WHERE project_id=?', (self.personal,)).fetchone()[0]
        with self.host.store.db:
            self.host.store.db.execute('UPDATE threads SET profile_id=? WHERE id=?', (self.me_codex, personal_thread))
        self.assertIsNone(conversation_route.alternative(self.host.store.db, binding))
        work_thread = self.host.dispatch('create_agent_conversation', dict(projectId=self.work, profileId=self.acme_codex, model='gpt-5.5', name='Codex'))
        self.assertEqual(conversation_route.alternative(self.host.store.db, binding)['profile_id'], self.acme_codex)
        self.assertTrue(work_thread['id'])

    def test_a_work_sign_in_that_comes_back_as_your_personal_account_says_so_by_name(self):
        work = connections_ui.owned_profile(self.host.store, self.host.root, self.acme)
        said = self.host._checked(work, connections_ui.classify(work, MAX))
        self.assertEqual((said['state'], said['otherSide'], said['signedInAs']), ('identity_mismatch', 'personal', PERSONAL))
        self.assertIn('That’s your personal account', said['detail'])
        self.assertIn('work email', said['detail'])
        stranger = self.host._checked(work, connections_ui.classify(work, dict(MAX, email='someone@else.test')))
        self.assertEqual(stranger['state'], 'identity_mismatch')
        self.assertNotIn('otherSide', stranger)
        # Beta.18 (account_signin.py): a valid sign-in on this side is named only with the offer to use it instead, so you
        # know whose it is before you click. The line itself still never repeats an email you never added here.
        self.assertNotIn('someone@else.test', stranger['detail'])
        self.assertEqual((stranger['signedInAs'], stranger['canUseSignedIn']), ('someone@else.test', True))
        free = self.host._checked(work, connections_ui.classify(work, dict(MAX, email='someone@else.test', subscriptionType='free')))
        self.assertNotIn('someone@else.test', json.dumps(free), 'a sign-in that can’t be used is never named')
        me = connections_ui.owned_profile(self.host.store, self.host.root, self.me_codex)
        codex = self.host._checked(me, connections_ui.classify(me, {'account': {'type': 'chatgpt', 'email': WORK, 'planType': 'team'}}))
        self.assertEqual((codex['otherSide'], codex['signedInAs']), ('work', WORK))
        self.assertIn('chatgpt.com', codex['detail'])
        self.assertNotIn('seen', self.host._checked(work, connections_ui.classify(work, TEAM)))

    def test_projects_list_says_which_side_each_is_on(self):
        listed = {p['name']: p['context'] for p in self.host.dispatch('projects', {})}
        self.assertEqual(listed, {'tidepool': 'personal', 'billing-api': 'work'})

    def test_ideas_and_records_stay_on_their_side(self):
        # Two accounts on one side share a project's items; across sides they never do (security review M3).
        db = self.host.store.db
        self.assertTrue(project_ideas.same_scope(db, self.acme, self.acme_codex))
        self.assertTrue(project_ideas.same_scope(db, self.me, self.me_codex))
        self.assertFalse(project_ideas.same_scope(db, self.acme, self.me))
        self.assertFalse(project_ideas.same_scope(db, self.me_codex, self.acme_codex))
        from project_record_scope import project_record_scope
        self.assertEqual(project_record_scope(self.host.store, self.work, self.acme)['context'], 'work')
        with self.assertRaises(Rejected):
            project_record_scope(self.host.store, self.work, self.me)


class WorkRunTests(unittest.TestCase):
    """End to end with the scripted Claude Code peer: a Work project's request runs on the work account, with its
    identity checked in the work account's own folder, and nothing reaches a provider on the wrong account. Borrows
    test_conversation_coding's fixture and helpers, not its tests."""
    provider = 'claude'
    peer_patch, settle, grant, request, close_host = (base.ConversationCodingTests.__dict__[name]
                                                      for name in ('peer_patch', 'settle', 'grant', 'request', 'close_host'))
    finished = staticmethod(base.ConversationCodingTests.finished)

    def setUp(self):
        base.ConversationCodingTests.setUp(self)
        # A work project beside the fixture's personal one, with its own folder and a work account.
        self.work_folder = self.base / 'work-project'
        self.work_folder.mkdir()
        for name in ('test_sample.py', 'greeting.py'):
            (self.work_folder / name).write_bytes((self.project / name).read_bytes())
        self.host.store.add_profile('acme', 'claude', self.base / 'store/profiles/acme', WORK, 'work')
        self.host.store.add_project('w', self.work_folder, 'acme', 'synthetic-model')
        self.host.store.add_thread('wc', 'w', 'wc', 'private', ACTOR)
        self.checked = []

    def auth(self, executable, home, **kwargs):
        """Claude Code's auth status, per sign-in folder: the work folder is signed in to the team account, any other to
        the personal one."""
        self.checked.append(str(home))
        return dict(TEAM) if str(home) == str(self.base / 'store/profiles/acme') else dict(MAX)

    def run_in(self, scope, text='Add a farewell', client='req-w'):
        self.scope = scope
        self.grant()
        with self.peer_patch('success'), patch.object(claude_coding, 'auth_status', self.auth):
            started = self.request(text, client_id=client)
            return self.settle(started['taskId'])

    def test_a_work_request_runs_on_the_work_account_in_its_own_folder(self):
        detail = self.run_in(dict(projectId='w', channelId='wc'))
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task'].get('error'))
        self.assertEqual(self.checked, [str(self.base / 'store/profiles/acme')], 'the identity was checked in the work folder only')
        peer = self.peers[-1]
        self.assertEqual(peer.env.get('CLAUDE_CONFIG_DIR'), str(self.base / 'store/profiles/acme'))
        self.assertTrue((self.work_folder / 'test_sample.py').read_text().endswith(self.appendix), 'the work project changed')
        self.assertFalse((self.project / 'test_sample.py').read_text().endswith(self.appendix), 'the personal one didn’t')

    def test_a_personal_request_runs_on_the_personal_account_beside_it(self):
        detail = self.run_in(dict(projectId='p', channelId='c'))
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task'].get('error'))
        self.assertEqual(self.checked, [str(self.base / 'store/profiles/p')])
        self.assertEqual(self.peers[-1].env.get('CLAUDE_CONFIG_DIR'), str(self.base / 'store/profiles/p'))

    def test_the_personal_sign_in_in_the_work_folder_blocks_and_sends_nothing(self):
        self.scope = dict(projectId='w', channelId='wc')
        self.grant()
        with self.peer_patch('success'), patch.object(claude_coding, 'auth_status', lambda *a, **k: dict(MAX)):
            started = self.request('Add a farewell', client_id='req-x')
            detail = self.settle(started['taskId'])
        self.assertNotEqual(detail['task']['state'], 'ready_for_review')
        self.assertEqual(self.starts, 0, 'no prompt reached the provider')

    def test_a_work_thread_moved_to_the_personal_account_never_starts(self):
        self.scope = dict(projectId='w', channelId='wc')
        self.grant()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET profile_id='p' WHERE id='wc'")
        with self.peer_patch('success'), patch.object(claude_coding, 'auth_status', self.auth):
            with self.assertRaisesRegex(Rejected, 'only a work account can run it'):
                self.request('Add a farewell', client_id='req-y')
        self.assertEqual((self.checked, self.peers), ([], []), 'no check and no process for the wrong side')

    def test_memory_written_in_a_work_project_stays_there(self):
        import project_memory
        self.run_in(dict(projectId='w', channelId='wc'))
        db = self.host.store.db
        project_memory.schema(db)
        with db:
            db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', ('n1', 'w', 'work', 'active', json.dumps(dict(id='n1', text='Deploys go through Buildkite.'))))
        self.assertEqual([e['id'] for e in project_memory.entries(db, 'w', 'work')], ['n1'])
        self.assertEqual(project_memory.entries(db, 'p', 'personal'), [], 'the personal project never sees it')
        self.assertEqual(project_memory.entries(db, 'w', 'personal'), [], 'nor does a personal account in the work project')


if __name__ == '__main__':
    unittest.main()
