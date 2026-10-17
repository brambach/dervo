"""The onboarding operations on a real Host over a disposable store, with synthetic Claude Code and Codex homes
(test_mac_survey.Fixture). No real home is read, no provider is started, nothing is sent."""
import json
import os
import unittest
import unittest.mock
import uuid
from datetime import datetime, timezone

import history_import as hi
import onboarding_host
from dervo_connections import Rejected
from host import Host
from session_continuity import _slug
from test_mac_survey import Fixture, NOW

DAY = 86400


def stamp(at):
    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace('+00:00', 'Z')


class HostFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.enterContext(unittest.mock.patch.object(hi, 'CLAUDE_HOME', self.claude))
        self.enterContext(unittest.mock.patch.object(hi, 'CODEX_HOME', self.codex))
        self.enterContext(unittest.mock.patch.object(onboarding_host, 'HOME', self.home))
        self.enterContext(unittest.mock.patch.object(onboarding_host, 'TEMP_ROOTS', (str(self.temp),)))
        self.host = Host(self.root / 'store')
        self.addCleanup(self.host.close)
        self.host.store.add_profile('account', 'claude', self.host.root / 'profiles' / 'account', 'you@example.test', 'personal')
        self.host.store.add_profile('work', 'claude', self.host.root / 'profiles' / 'work', 'you@acme.test', 'work')

    def conversation(self, path, first, *, days_ago=1):
        """One Claude Code session you'd want back: something typed and an answer, so the reader keeps it."""
        folder = self.claude / 'projects' / _slug(str(path))
        folder.mkdir(parents=True, exist_ok=True)
        at = NOW - days_ago * DAY
        records = [dict(type='user', cwd=str(path), timestamp=stamp(at), message=dict(role='user', content=first)),
                   dict(type='assistant', cwd=str(path), timestamp=stamp(at + 5), message=dict(role='assistant', content=[dict(type='text', text='On it.')]))]
        file = folder / f'{uuid.uuid4()}.jsonl'
        file.write_text(''.join(json.dumps(r) + '\n' for r in records))
        os.utime(file, (at + 5, at + 5))
        return file.stem

    def call(self, operation, **args):
        return self.host.dispatch(operation, args)

    def projects(self):
        return {row['name']: dict(row) for row in self.host.store.db.execute(
            'SELECT p.*, n.name FROM projects p JOIN project_names n ON n.project_id=p.id')}

    def setup_mac(self):
        """Three projects with open conversations, one of them in a folder that looks like work."""
        self.tidepool, self.recipes = self.folder('personal/tidepool'), self.folder('personal/recipes')
        self.billing = self.folder('work/billing')
        for n, text in enumerate(['Fix the tide sync retries', 'Add offline map tiles', 'Tidy the chart colours']):
            self.conversation(self.tidepool, text, days_ago=n + 1)
        for n, text in enumerate(['Import recipes from a link', 'Scale a recipe for more people', 'Print a shopping list']):
            self.conversation(self.recipes, text, days_ago=n + 1)
        for n, text in enumerate(['Retry failed invoices', 'Add the tax report', 'Rename the billing export']):
            self.conversation(self.billing, text, days_ago=n + 1)
        return self.call('onboarding_look')

    def bring(self, found, picks, profile='account', model='claude-opus-5', work=None, moved=None):
        """Bring in ``picks``, each under the side the look guessed (or ``moved``: {name: context}), personal ones on
        ``profile`` and work ones on ``work`` ({profileId, model}) when given."""
        moved = moved or {}
        rows = [dict(key=p['key'], name=p['name'], context=moved.get(p['name'], p['context'] or 'personal'))
                for p in found['projects'] if p['name'] in picks]
        extra = {'work': work} if work is not None else {}
        return self.call('onboarding_bring_in', surveyId=found['surveyId'], projects=rows, profileId=profile, model=model, **extra)

    def key(self, found, name):
        return next(p['key'] for p in found['projects'] if p['name'] == name)

    def read(self, found, name, context='personal', profile='account'):
        return self.call('onboarding_threads', surveyId=found['surveyId'],
                         projects=[dict(key=self.key(found, name), context=context, profileId=profile)])['projects'][0]


class LookTests(HostFixture):
    def test_sources_say_which_lists_are_here_before_anything_is_looked_at(self):
        self.assertEqual([s['present'] for s in self.call('onboarding_sources')['sources']], [False, False])
        self.folder('code/app')
        self.conversation(self.home / 'code' / 'app', 'Hello there')
        self.assertEqual([s['present'] for s in self.call('onboarding_sources')['sources']], [True, False])

    def test_the_look_hands_back_keys_never_paths_and_makes_nothing(self):
        found = self.setup_mac()
        self.assertNotIn('paths', found)
        self.assertEqual({p['name'] for p in found['projects']}, {'tidepool', 'recipes', 'billing'})
        # Bryce, 26 Sep: Work and Personal are back, each guess with its reason.
        guesses = {p['name']: (p['context'], p['context_reason']) for p in found['projects']}
        self.assertEqual(guesses['billing'], ('work', 'guessed from ~/work'))
        self.assertEqual(guesses['tidepool'], ('personal', 'guessed from ~/personal'))
        self.assertEqual(self.projects(), {})
        self.assertTrue(found['surveyId'])

    def test_fields_are_exact(self):
        with self.assertRaises(Rejected):
            self.host.dispatch('onboarding_look', {'path': '/'})
        with self.assertRaises(Rejected):
            self.host.dispatch('onboarding_threads', {'surveyId': 'x', 'projects': [], 'path': '/'})


class ThreadsTests(HostFixture):
    def test_the_second_yes_reads_the_accepted_folders_with_the_existing_reader(self):
        found = self.setup_mac()
        key = self.key(found, 'tidepool')
        shortlist = self.read(found, 'tidepool')
        self.assertEqual(shortlist['key'], key)
        self.assertEqual(sorted(c['name'] for c in shortlist['candidates']), ['Chart colours', 'Offline map tiles', 'Tide sync retries'],
                         'named by the same rules the per-project import uses')
        self.assertEqual(self.projects(), {}, 'reading names makes nothing')

    def test_each_row_carries_its_side_and_an_account_on_that_side(self):
        # Security review S4 (24 Sep), kept with work accounts back: a folder's messages are read only when there's an
        # account on its own side for them to go to. Never a personal account for a work folder, or the other way round.
        found = self.setup_mac()
        key = self.key(found, 'billing')
        for row in ({'key': key}, {'key': key, 'context': 'work'}, {'key': key, 'context': 'maybe', 'profileId': 'work'},
                    {'key': key, 'context': 'work', 'profileId': ''}, {'key': key, 'context': 'work', 'profileId': 'account'},
                    {'key': key, 'context': 'personal', 'profileId': 'work'}, {'path': '/'}, key):
            with self.subTest(row=row):
                with self.assertRaises(Rejected):
                    self.call('onboarding_threads', surveyId=found['surveyId'], projects=[row])
        self.assertEqual(self.read(found, 'billing', context='work', profile='work')['key'], key)
        self.assertEqual(self.projects(), {}, 'reading names makes nothing')

    def test_a_work_folder_with_no_work_account_isnt_read(self):
        found = self.setup_mac()
        with self.assertRaisesRegex(Rejected, 'work account'):
            self.read(found, 'billing', context='work', profile='')

    def test_loose_ends_are_never_read_or_brought_in(self):
        # Bryce, 24 Sep (security review N1, behaviour review N2): the home folder stays as it is. The look gives it no
        # key, and a key that somehow reached it is refused by both steps.
        self.conversation(self.home, 'Clean up Downloads')
        found = self.setup_mac()
        self.assertIsNone(found['loose_ends']['key'])
        self.host.onboarding_survey['paths']['forged'] = str(self.home)
        with self.assertRaisesRegex(Rejected, 'home folder can’t be a project'):
            self.call('onboarding_threads', surveyId=found['surveyId'], projects=[{'key': 'forged', 'context': 'personal', 'profileId': 'account'}])
        with self.assertRaisesRegex(Rejected, 'home folder can’t be a project'):
            self.call('onboarding_bring_in', surveyId=found['surveyId'], projects=[dict(key='forged', name='Loose ends', context='personal')],
                      profileId='account', model='claude-opus-5')
        self.assertEqual(self.projects(), {})

    def test_only_keys_from_the_last_look_are_accepted(self):
        found = self.setup_mac()
        with self.assertRaises(Rejected):
            self.call('onboarding_threads', surveyId=found['surveyId'], projects=[{'key': '/Users/someone/secret', 'context': 'personal', 'profileId': 'account'}])
        with self.assertRaises(Rejected):
            self.call('onboarding_threads', surveyId='stale', projects=[{'key': found['projects'][0]['key'], 'context': 'personal', 'profileId': 'account'}])

    def test_a_look_over_an_hour_old_is_refused_and_a_fresh_look_carries_the_same_keys(self):
        # NEXT-UP #36: the screen keeps the folders you picked by key and looks again in place, which only works if a
        # second look gives the same folder the same key and replaces the old look.
        found = self.setup_mac()
        self.host.onboarding_survey['at'] -= onboarding_host.SURVEY_SECONDS + 1
        with self.assertRaisesRegex(Rejected, 'more than an hour old'):
            self.read(found, 'tidepool')
        again = self.call('onboarding_look')
        self.assertNotEqual(again['surveyId'], found['surveyId'])
        self.assertEqual(self.key(again, 'tidepool'), self.key(found, 'tidepool'))
        self.assertEqual(len(self.read(again, 'tidepool')['candidates']), 3)
        with self.assertRaisesRegex(Rejected, 'isn’t available any more'):
            self.read(found, 'tidepool')

    def test_tool_runs_are_read_only_when_asked_for(self):
        found = self.setup_mac()
        row = dict(key=self.key(found, 'tidepool'), context='personal', profileId='account')
        import history_import_host
        with unittest.mock.patch.object(history_import_host, 'preview_folder', wraps=history_import_host.preview_folder) as preview:
            self.call('onboarding_threads', surveyId=found['surveyId'], projects=[row])
            self.call('onboarding_threads', surveyId=found['surveyId'], projects=[row], includeToolRuns=True)
        self.assertEqual([call.kwargs['include_tool_runs'] for call in preview.call_args_list], [False, True])
        with self.assertRaises(Rejected):
            self.call('onboarding_threads', surveyId=found['surveyId'], projects=[row], includeToolRuns='yes')


class BringInTests(HostFixture):
    def test_with_no_work_account_work_folders_wait_and_never_use_the_personal_account(self):
        # N1 (security review, 25 Sep): a work repo mustn't come in on the personal account. It waits, on record, and
        # the personal ones come in anyway: nothing is held back by it (Bryce, 26 Sep).
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool', 'recipes', 'billing'})
        made = self.projects()
        self.assertEqual(set(made), {'tidepool', 'recipes'})
        self.assertEqual({row['profile_id'] for row in made.values()}, {'account'})
        self.assertEqual({row['context'] for row in made.values()}, {'personal'})
        self.assertEqual({row['model'] for row in made.values()}, {'claude-opus-5'})
        self.assertEqual(sorted(p['name'] for p in batch['projects']), ['recipes', 'tidepool'])
        self.assertEqual([w['name'] for w in batch['waiting']], ['billing'])
        self.assertTrue(batch['undoable'])
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM turns').fetchone()[0], 0, 'nothing was sent')

    def test_with_a_work_account_each_side_comes_in_on_its_own(self):
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool', 'billing'}, work=dict(profileId='work', model='claude-sonnet-5'))
        made = self.projects()
        self.assertEqual((made['billing']['profile_id'], made['billing']['model'], made['billing']['context']), ('work', 'claude-sonnet-5', 'work'))
        self.assertEqual((made['tidepool']['profile_id'], made['tidepool']['model'], made['tidepool']['context']), ('account', 'claude-opus-5', 'personal'))
        self.assertEqual({p['name']: p['context'] for p in batch['projects']}, {'billing': 'work', 'tidepool': 'personal'})
        self.assertEqual(batch['waiting'], [])
        # The starter thread is on the project's own account, so its first run checks the work account.
        starter = self.host.store.db.execute("SELECT profile_id FROM threads WHERE project_id=?", (made['billing']['id'],)).fetchone()[0]
        self.assertEqual(starter, 'work')

    def test_waiting_work_folders_come_in_once_a_work_account_is_added_and_never_before(self):
        found = self.setup_mac()
        first = self.bring(found, {'tidepool', 'billing'})
        self.assertEqual(self.call('onboarding_waiting'), {'names': ['billing']})
        with self.assertRaisesRegex(Rejected, 'only a work account'):
            self.call('onboarding_bring_waiting', work=dict(profileId='account', model='claude-opus-5'))
        self.assertNotIn('billing', self.projects(), 'never on the personal account')
        batch = self.call('onboarding_bring_waiting', work=dict(profileId='work', model='claude-opus-5'))
        made = self.projects()
        self.assertEqual((made['billing']['profile_id'], made['billing']['context']), ('work', 'work'))
        self.assertEqual([(p['name'], p['context']) for p in batch['projects']], [('billing', 'work')])
        self.assertTrue(batch['undoable'])
        self.assertEqual(self.call('onboarding_waiting'), {'names': []})
        self.assertEqual(self.call('onboarding_batch', projectId=made['tidepool']['id'])['waiting'], [], 'the first batch stops saying it waits')
        with self.assertRaisesRegex(Rejected, 'No Work projects are waiting'):
            self.call('onboarding_bring_waiting', work=dict(profileId='work', model='claude-opus-5'))
        self.call('onboarding_undo', batchId=batch['batchId'])
        self.assertEqual(set(self.projects()), {'tidepool'}, 'Undo takes the late ones back like any batch')
        self.assertTrue(self.billing.is_dir())
        self.assertTrue(first['batchId'])

    def test_only_work_folders_with_a_work_account_need_no_personal_account(self):
        # The screen sends no personal account when nothing ticked is Personal.
        found = self.setup_mac()
        rows = [dict(key=self.key(found, 'billing'), name='billing', context='work')]
        batch = self.call('onboarding_bring_in', surveyId=found['surveyId'], projects=rows, profileId=None, model=None,
                          work=dict(profileId='work', model='claude-opus-5'))
        self.assertEqual([p['context'] for p in batch['projects']], ['work'])
        again = self.call('onboarding_look')  # a look brings in once; a second Bring in needs a fresh one
        with self.assertRaisesRegex(Rejected, 'Choose the account'):
            self.call('onboarding_bring_in', surveyId=again['surveyId'], projects=[dict(key=self.key(again, 'tidepool'), name='t', context='personal')],
                      profileId=None, model=None)

    def test_a_folder_moved_to_personal_comes_in_on_the_personal_account(self):
        # The guess is only a guess: Move to Personal is the person's call, and then it's a Personal project.
        found = self.setup_mac()
        self.bring(found, {'billing'}, moved={'billing': 'personal'})
        self.assertEqual((self.projects()['billing']['profile_id'], self.projects()['billing']['context']), ('account', 'personal'))

    def test_all_work_and_no_work_account_makes_nothing_and_says_what_to_do(self):
        found = self.setup_mac()
        with self.assertRaisesRegex(Rejected, 'no work account yet'):
            self.bring(found, {'billing'})
        self.assertEqual(self.projects(), {})

    def test_each_side_only_takes_an_account_of_its_own(self):
        found = self.setup_mac()
        with self.assertRaises(Rejected):
            self.bring(found, {'billing'}, work=dict(profileId='account', model='claude-opus-5'))
        with self.assertRaises(Rejected):
            self.bring(found, {'tidepool'}, profile='work')
        with self.assertRaises(Rejected):
            self.bring(found, {'billing'}, work=dict(profileId='work'))
        self.assertEqual(self.projects(), {})

    def test_threads_come_in_through_history_read_dormant_and_counted_on_the_batch(self):
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool'})
        project = batch['projects'][0]
        shortlist = self.read(found, 'tidepool')
        ids = [c['sessionId'] for c in shortlist['candidates'] if c['recommended']]
        self.call('history_read', projectId=project['id'], sessionIds=ids)
        view = self.call('onboarding_batch', projectId=project['id'])
        self.assertEqual(view['threads'], 3)
        self.assertTrue(view['undoable'])
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM turns').fetchone()[0], 0, 'brought-in threads are dormant')

    def test_a_project_comes_in_exactly_as_add_project_makes_one(self):
        # Behaviour review N3 (24 Sep): onboarding wrote its projects its own way. Both go through project_setup.make
        # now, and every table either path writes gets the same rows, apart from the ids, folder and name.
        import project_setup
        db = self.host.store.db
        onboarding_only = {'onboarding_batches', 'onboarding_projects'}

        def snapshot():
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {t: set(tuple(r) for r in db.execute(f'SELECT * FROM "{t}"')) for t in tables if t not in onboarding_only}

        def added(before, project_id):
            channel = db.execute('SELECT id FROM threads WHERE project_id=?', (project_id,)).fetchone()[0]
            path, name = db.execute('SELECT p.cwd, n.name FROM projects p JOIN project_names n ON n.project_id=p.id WHERE p.id=?', (project_id,)).fetchone()
            swap = {project_id: '<project>', channel: '<thread>', path: '<folder>', name: '<name>'}
            return {t: sorted(tuple(swap.get(v, v) for v in row) for row in rows - before.get(t, set()))
                    for t, rows in snapshot().items() if rows - before.get(t, set())}

        found = self.setup_mac()
        before = snapshot()
        with unittest.mock.patch.object(project_setup, 'make', wraps=project_setup.make) as make:
            onboarded = self.bring(found, {'tidepool'})['projects'][0]['id']
        self.assertEqual(make.call_count, 1, 'onboarding makes its project through Add project’s writer')
        from_onboarding = added(before, onboarded)

        before = snapshot()
        picked = project_setup.stage(self.host, self.recipes)
        saved = self.call('project_setup_save', selectionId=picked['selectionId'], profileId='account', model='claude-opus-5', name='recipes')['id']
        from_add_project = added(before, saved)
        self.assertEqual(from_onboarding, from_add_project)
        self.assertIn('projects', from_add_project)
        self.assertIn('threads', from_add_project)

    def test_a_second_bring_in_for_the_same_look_returns_the_first_batch(self):
        found = self.setup_mac()
        first = self.bring(found, {'tidepool'})
        again = self.bring(found, {'tidepool', 'recipes'})
        self.assertEqual(first['batchId'], again['batchId'])
        self.assertEqual(set(self.projects()), {'tidepool'})

    def test_a_row_needs_work_or_personal_and_nothing_is_made_without_it(self):
        found = self.setup_mac()
        key = self.key(found, 'tidepool')
        for row in (dict(key=key, name='tidepool'), dict(key=key, name='tidepool', context=None), dict(key=key, name='tidepool', context='both')):
            with self.subTest(row=row):
                with self.assertRaises(Rejected):
                    self.call('onboarding_bring_in', surveyId=found['surveyId'], profileId='account', model='claude-opus-5', projects=[row])
        self.assertEqual(self.projects(), {})

    def test_the_account_and_model_are_always_required(self):
        found = self.setup_mac()
        for profile, model in (('', 'claude-opus-5'), (None, 'claude-opus-5'), ('nobody', 'claude-opus-5'), ('account', ''), ('account', ' claude-opus-5')):
            with self.subTest(profile=profile, model=model):
                with self.assertRaises(Rejected):
                    self.bring(found, {'tidepool'}, profile=profile, model=model)
        self.assertEqual(self.projects(), {})


class UndoTests(HostFixture):
    def test_undo_removes_the_whole_batch_and_leaves_the_folders_and_histories(self):
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool', 'recipes'})
        before = {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')}
        undone = self.call('onboarding_undo', batchId=batch['batchId'])
        self.assertTrue(undone['undone'])
        self.assertEqual(self.projects(), {})
        self.assertEqual(before, {str(p): p.stat().st_mtime_ns for p in self.home.rglob('*')})
        self.assertIsNone(self.call('onboarding_batch', projectId=batch['projects'][0]['id']))

    def test_undo_holds_once_something_is_done_in_a_project(self):
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool', 'recipes'})
        tidepool = next(p for p in batch['projects'] if p['name'] == 'tidepool')
        self.call('create_conversation', projectId=tidepool['id'], name='My own thread', private=False)
        view = self.call('onboarding_batch', projectId=tidepool['id'])
        self.assertFalse(view['undoable'])
        self.assertIn('tidepool', view['blocked'])
        with self.assertRaises(Rejected):
            self.call('onboarding_undo', batchId=batch['batchId'])
        self.assertEqual(set(self.projects()), {'tidepool', 'recipes'})

    def test_a_message_you_wrote_holds_undo_but_the_history_briefs_do_not(self):
        found = self.setup_mac()
        batch = self.bring(found, {'tidepool'})
        project = batch['projects'][0]
        shortlist = self.read(found, 'tidepool')
        self.call('history_read', projectId=project['id'], sessionIds=[c['sessionId'] for c in shortlist['candidates']])
        self.assertTrue(self.call('onboarding_batch', projectId=project['id'])['undoable'])
        thread = self.host.store.db.execute("SELECT thread_id FROM messages WHERE actor='dervo-history' LIMIT 1").fetchone()[0]
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text,role) VALUES (?,?,?,?,?,?)',
                                       (str(uuid.uuid4()), thread, 'typed-1', 'local-user', 'carry on', 'user'))
        self.assertFalse(self.call('onboarding_batch', projectId=project['id'])['undoable'])

    def test_old_batches_still_load_and_undo(self):
        # Beta.15 and beta.15.1 batches kept ticked work folders as ``waiting``, with no context on their projects.
        # Beta.16's first day (25 Sep) kept no waiting list at all. Both load, and Undo still works.
        for old in ('beta15', 'no-context-day'):
            with self.subTest(old=old):
                found = self.setup_mac()
                batch = self.bring(found, {'tidepool'})
                db = self.host.store.db
                document = json.loads(db.execute('SELECT document FROM onboarding_batches WHERE id=?', (batch['batchId'],)).fetchone()[0])
                for project in document['projects']:
                    project.pop('context', None)
                if old == 'beta15':
                    document['waiting'] = [dict(name='billing', path=str(self.billing))]
                else:
                    document.pop('waiting')
                with db:
                    db.execute('UPDATE onboarding_batches SET document=? WHERE id=?', (json.dumps(document), batch['batchId']))
                view = self.call('onboarding_batch', projectId=batch['projects'][0]['id'])
                self.assertEqual([(p['name'], p['context']) for p in view['projects']], [('tidepool', 'personal')])
                self.assertEqual([w['name'] for w in view['waiting']], ['billing'] if old == 'beta15' else [])
                self.assertTrue(view['undoable'])
                self.assertTrue(self.call('onboarding_undo', batchId=batch['batchId'])['undone'])
                self.assertEqual(self.projects(), {})
                self.assertTrue(self.billing.is_dir(), 'the work folder was never touched')

    def test_undo_takes_back_a_work_project_too(self):
        found = self.setup_mac()
        batch = self.bring(found, {'billing', 'tidepool'}, work=dict(profileId='work', model='claude-opus-5'))
        self.assertTrue(self.call('onboarding_undo', batchId=batch['batchId'])['undone'])
        self.assertEqual(self.projects(), {})


if __name__ == '__main__':
    unittest.main()
