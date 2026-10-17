"""Project settings and the model picker, design sweep 19 Sep: the project's default account, Show in Finder, Fable's
paid credits confirmed once per account, and every project setting read and changed without a thread. Disposable
stores and fakes only; no provider, no CLI, no model."""
import json
import platform_os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import project_lifecycle
from dervo_connections import Rejected
from host import Host, ACTOR

SAMPLE = 'import unittest\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n'


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'; self.source.mkdir()
        (self.source / 'greeting.py').write_text('def greet(): return "hi"\n')
        (self.source / 'test_sample.py').write_text(SAMPLE)
        (self.source / 'fixtures').mkdir()
        self.host = Host(self.root / 'data'); self.addCleanup(self.host.close)
        add = lambda provider, identity, category='personal': self.host.dispatch('add_profile', {'provider': provider, 'identity': identity, 'category': category})['id']
        self.personal = add('claude', 'bryce@trydervo.com')
        self.team = add('claude', 'bryce@acme.com')
        self.codex = add('codex', 'bryce@trydervo.com')
        self.work = add('claude', 'work@example.test', 'work')
        for pid in ('one', 'two'):
            self.host.store.add_project(pid, self.source, self.personal, 'claude-opus-5')

    def project(self, pid='one'):
        return tuple(self.host.store.db.execute('SELECT profile_id, model FROM projects WHERE id=?', (pid,)).fetchone())


class ProjectAccountTests(Base):
    def change(self, **changes):
        return self.host.dispatch('project_profile_set', {'projectId': 'one', 'expectedProfileId': self.personal, 'profileId': self.team,
                                                          'expectedModel': 'claude-opus-5', 'model': 'claude-sonnet-5', **changes})

    def test_only_new_threads_take_the_new_account_and_model(self):
        before = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Before', 'private': False})['id']
        result = self.change()
        self.assertEqual(result, {'id': 'one', 'profile_id': self.team, 'provider': 'claude', 'model': 'claude-sonnet-5'})
        self.assertEqual(self.project('one'), (self.team, 'claude-sonnet-5'))
        self.assertEqual(self.project('two'), (self.personal, 'claude-opus-5'), 'another project keeps its account')
        thread = lambda cid: tuple(self.host.store.db.execute('SELECT profile_id, model FROM threads WHERE id=?', (cid,)).fetchone())
        self.assertEqual(thread(before), (self.personal, 'claude-opus-5'), 'a thread you have keeps its account')
        after = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'After', 'private': False})['id']
        self.assertEqual(thread(after), (self.team, 'claude-sonnet-5'), 'a new thread starts on the project account')
        # Across providers, the model moves in the same write.
        self.change(expectedProfileId=self.team, expectedModel='claude-sonnet-5', profileId=self.codex, model='gpt-5.6-sol')
        self.assertEqual(self.project('one'), (self.codex, 'gpt-5.6-sol'))

    def test_stale_invalid_unowned_or_misshaped_requests_leave_the_project_as_it_was(self):
        for change in ({'expectedProfileId': 'stale'}, {'expectedModel': 'stale'}, {'profileId': 'missing'}, {'profileId': self.work},
                       {'profileId': ''}, {'profileId': None}, {'expectedProfileId': None}, {'projectId': 'missing'}, {'projectId': None},
                       {'model': ''}, {'model': ' bad '}, {'model': 'bad\nmodel'}, {'model': None}, {'model': 'a' * 257}, {'extra': 1}):
            with self.subTest(change=change), self.assertRaises(Rejected):
                self.change(**change)
        with self.assertRaises(Rejected):
            self.host.dispatch('project_profile_set', {'projectId': 'one', 'profileId': self.team, 'model': 'claude-sonnet-5'})
        self.assertEqual(self.project('one'), (self.personal, 'claude-opus-5'))

    def test_an_archived_project_or_active_work_refuses_the_change(self):
        self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'Restore this project before changing its account'):
            self.change()
        self.host.dispatch('project_restore', {'projectId': 'one'})
        channel = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Busy', 'private': False})['id']
        mid = self.host.store.send(channel, 'one', 'pending', 'keep', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, 'The default account didn’t change. Busy still has work running. Wait for the reply in Busy to finish, or cancel it. Then try again.'):
            self.change()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE turns SET state='completed' WHERE id=?", (mid,))
        self.host.turns['live'] = SimpleNamespace(binding={'id': channel, 'project_id': 'one'}, closed=False)
        with self.assertRaisesRegex(Rejected, 'default account'):
            self.change()
        self.host.turns.clear()
        self.assertEqual(self.project('one'), (self.personal, 'claude-opus-5'))
        self.change()
        self.assertEqual(self.project('one'), (self.team, 'claude-sonnet-5'), 'settled work does not hold the project')

    def task(self, task_id, channel, **fields):
        record = {'id': task_id, 'state': 'blocked', 'context': dict(thread_id=channel, project_id='one', actor=ACTOR), 'runs': [], 'tokens': [], **fields}
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO coding_tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                                       (task_id, json.dumps(record)))

    def test_the_refusal_names_the_running_threads_and_what_to_do_in_each(self):
        # Bug #4, 20 Sep: the refusal named nothing. Now it names the threads with a run still going, and what to do.
        # Two runs in one thread, one in another, plus a lost run in a third that the account switch no longer counts.
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)
        left = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Where did we leave off', 'private': False})['id']
        releases = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'releases', 'private': False})['id']
        sweep = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Full design sweep', 'private': False})['id']
        self.task('t1', left, state='working', runs=[{'id': 'r1', 'state': 'running'}])
        self.task('t2', left, state='working', runs=[{'id': 'r2', 'state': 'running'}])
        self.task('t3', releases, state='working', runs=[{'id': 'r3', 'state': 'running'}])
        self.task('t4', sweep, state='disconnected', runs=[{'id': 'r4', 'state': 'uncertain'}])
        with self.assertRaises(Rejected) as refused:
            self.change()
        self.assertEqual(str(refused.exception), 'The default account didn’t change. 2 threads still have work running. '
                         'Wait for the runs in Where did we leave off (2) and releases to finish, or stop them. Then try again.')
        self.assertEqual(self.project('one'), (self.personal, 'claude-opus-5'))
        # A reply still being answered elsewhere gets its own instruction, and the thread count covers both.
        busy = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Fix login', 'private': False})['id']
        mid = self.host.store.send(busy, 'one', 'pending', 'keep', actor=ACTOR)
        with self.assertRaisesRegex(Rejected, '3 threads still have work running. Wait for the reply in Fix login to finish, or cancel it. '
                                              'Wait for the runs in Where did we leave off \\(2\\) and releases to finish, or stop them. Then try again.'):
            self.change()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE turns SET state='completed' WHERE id=?", (mid,))
        # More than three threads: the first three are named, the rest counted.
        for n in range(3):
            more = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': f'More {n}', 'private': False})['id']
            self.task(f'm{n}', more, state='working', runs=[{'id': f'rm{n}', 'state': 'running'}])
        with self.assertRaisesRegex(Rejected, '5 threads still have work running. Wait for the runs in Where did we leave off \\(2\\), releases, More 0 and 2 more to finish'):
            self.change()
        # The default model has the same rule and says so the same way.
        with self.assertRaisesRegex(Rejected, 'The default model didn’t change. 5 threads still have work running.'):
            self.host.dispatch('project_model_set', {'projectId': 'one', 'expectedModel': 'claude-opus-5', 'model': 'claude-sonnet-5'})
        self.assertEqual(self.project('one'), (self.personal, 'claude-opus-5'))

    def test_lost_runs_hold_archive_and_delete_but_not_the_default_account_or_model(self):
        # Bryce, 20 Sep: "just block on running stuff". The eight runs Dervo had lost in his gmail project held the
        # switch for nothing, since the default account reaches new threads only. Archive keeps the full rule. Delete
        # doesn't since 23 Sep (app #12): typing the name is enough, and nothing here is running to stop first.
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)
        left = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Where did we leave off', 'private': False})['id']
        releases = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'releases', 'private': False})['id']
        self.task('t1', left, state='disconnected', runs=[{'id': 'r1', 'state': 'uncertain'}], tokens=[{'token': 'k', 'state': 'uncertain'}])
        self.task('t3', releases, state='disconnected', runs=[{'id': 'r3', 'state': 'uncertain'}])
        self.task('t5', releases, state='needs_permission', runs=[])
        with self.assertRaisesRegex(Rejected, 'This project can’t be archived yet. 2 threads still have unfinished work. '
                                              'Mark the lost runs in Where did we leave off and releases as stopped. '
                                              'Approve or deny the permission request in releases. Then try again.'):
            self.host.dispatch('project_archive', {'projectId': 'one'})
        self.assertEqual(project_lifecycle.stop_project_work(self.host, 'one'), 0, 'nothing to stop before a delete')
        self.assertEqual([kind for kind, _ in project_lifecycle.holds(self.host, 'one')], ['lost', 'lost', 'permission'])
        self.assertEqual(project_lifecycle.holds(self.host, 'one', running_only=True), [])
        self.host.dispatch('project_model_set', {'projectId': 'one', 'expectedModel': 'claude-opus-5', 'model': 'claude-sonnet-5'})
        self.change(expectedModel='claude-sonnet-5')
        self.assertEqual(self.project('one'), (self.team, 'claude-sonnet-5'))
        thread = lambda cid: self.host.store.db.execute('SELECT profile_id FROM threads WHERE id=?', (cid,)).fetchone()[0]
        self.assertEqual({thread(left), thread(releases)}, {self.personal}, 'the threads with lost runs keep their account')
        # A check the host is still polling is running work, and so is a live tool call. Both still hold the switch.
        for tokens in ([{'token': 'k', 'state': 'delivered', 'job_id': 'job', 'job_done': False}], [{'token': 'k', 'state': 'executing'}]):
            with self.subTest(tokens=tokens):
                self.task('t6', left, state='blocked', runs=[{'id': 'r6', 'state': 'completed'}], tokens=tokens)
                with self.assertRaisesRegex(Rejected, 'Wait for the check in Where did we leave off to finish, or stop its task.'):
                    self.change(expectedProfileId=self.team, expectedModel='claude-sonnet-5', profileId=self.personal, model='claude-opus-5')
        self.task('t6', left, state='blocked', runs=[{'id': 'r6', 'state': 'completed'}], tokens=[{'token': 'k', 'state': 'delivered', 'job_id': 'job', 'job_done': True}])
        self.change(expectedProfileId=self.team, expectedModel='claude-sonnet-5', profileId=self.personal, model='claude-opus-5')
        self.assertEqual(self.project('one'), (self.personal, 'claude-opus-5'))


class RevealTests(Base):
    def test_show_in_finder_reveals_the_projects_own_folder_and_takes_no_path(self):
        opened = []
        with patch.object(project_lifecycle.subprocess, 'Popen', lambda argv, **kwargs: opened.append((argv, kwargs))):
            self.assertEqual(self.host.dispatch('project_reveal', {'projectId': 'one'}), {'id': 'one', 'opened': True})
            for args in ({'projectId': 'one', 'path': '/etc'}, {'projectId': 'missing'}, {'projectId': None}, {}):
                with self.subTest(args=args), self.assertRaises(Rejected):
                    self.host.dispatch('project_reveal', args)
        # -R selects the folder in Finder and never launches it, even if it's been swapped for an app bundle (security
        # review, 19 Sep, finding 3). Its output goes nowhere near the host's stdout, the bridge's channel.
        self.assertEqual([argv for argv, _ in opened], [platform_os.reveal_command(str(self.source))])
        kwargs = opened[0][1]
        for stream in ('stdin', 'stdout', 'stderr'):
            self.assertIs(kwargs.get(stream), project_lifecycle.subprocess.DEVNULL, stream)
        self.assertIs(kwargs.get('start_new_session'), True)

    def test_a_folder_that_is_gone_says_so_and_opens_nothing(self):
        opened = []
        with self.host.store.db:
            self.host.store.db.execute('UPDATE projects SET cwd=? WHERE id=?', (str(self.root / 'moved'), 'one'))
        with patch.object(project_lifecycle.subprocess, 'Popen', lambda argv, **kwargs: opened.append(argv)):
            with self.assertRaisesRegex(Rejected, 'isn’t there any more'):
                self.host.dispatch('project_reveal', {'projectId': 'one'})
        self.assertEqual(opened, [])


class FableOnceTests(Base):
    def listed(self):
        return {p['id']: p['fable_confirmed'] for p in self.host.dispatch('profiles', {})}

    def test_the_confirmation_is_kept_per_account_and_listed_with_it(self):
        self.assertEqual(self.listed(), {self.personal: False, self.team: False, self.codex: False, self.work: False})
        self.assertEqual(self.host.dispatch('profile_fable_ack', {'profileId': self.personal}), {'id': self.personal, 'fable_confirmed': True})
        self.host.dispatch('profile_fable_ack', {'profileId': self.personal})  # asking twice keeps one record
        self.assertEqual(self.listed(), {self.personal: True, self.team: False, self.codex: False, self.work: False},
                         'saying yes for Personal says nothing about Acme')
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM fable_acks').fetchone()[0], 1)

    def test_only_this_macs_own_claude_accounts_can_be_confirmed(self):
        for args in ({'profileId': self.codex}, {'profileId': 'missing'}, {'profileId': ''}, {'profileId': None},
                     {'profileId': self.personal, 'extra': 1}, {}):
            with self.subTest(args=args), self.assertRaises(Rejected):
                self.host.dispatch('profile_fable_ack', args)
        self.assertFalse(any(self.listed().values()))
        # A work Claude account runs Work projects since 26 Sep 2026 (b16-work), so its Fable notice is its own to confirm.
        self.host.dispatch('profile_fable_ack', {'profileId': self.work})
        self.assertEqual({k for k, v in self.listed().items() if v}, {self.work})


class WithoutAThreadTests(Base):
    """Every project setting reads and saves with channelId null, on the project's own account and model."""
    scope = {'projectId': 'one', 'channelId': None}

    def test_access_reads_and_grants_on_the_project_binding_with_no_thread_at_all(self):
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM threads').fetchone()[0], 0)
        view = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual((view['state'], view['provider'], view['continuity'], view['conversation_route']['mode']), ('missing', 'claude', None, 'fixed'))
        granted = self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['fixtures']})
        self.assertEqual((granted['state'], granted['files'], granted['folders']), ('granted', ['greeting.py', 'test_sample.py'], ['fixtures']))
        # A thread made afterwards reads the same grant: it's the project's.
        channel = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Later', 'private': False})['id']
        self.assertEqual(self.host.dispatch('coding_permission', {'projectId': 'one', 'channelId': channel})['state'], 'granted')
        self.host.dispatch('coding_acceptance_set', {**self.scope, 'required': False})
        self.assertFalse(self.host.dispatch('coding_permission', self.scope)['acceptance']['required'])
        self.host.dispatch('coding_route_set', {**self.scope, 'autoSwitch': True})
        self.assertTrue(self.host.dispatch('coding_permission', self.scope)['route']['auto_switch'])
        self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['state'], 'missing')

    def test_thinking_is_saved_for_the_projects_own_model(self):
        self.host.dispatch('coding_effort_set', {**self.scope, 'effort': 'high'})
        self.assertEqual([tuple(r) for r in self.host.store.db.execute('SELECT project_id, provider, model, effort FROM coding_efforts')],
                         [('one', 'claude', 'claude-opus-5', 'high')])
        self.assertEqual(self.host.dispatch('coding_permission', self.scope)['owner_effort'], 'high')
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_effort_set', {**self.scope, 'effort': 'ultra'})

    def test_a_stale_grant_names_what_is_gone(self):
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['greeting.py', 'test_sample.py'], 'testFile': 'test_sample.py', 'folders': ['fixtures']})
        (self.source / 'fixtures').rmdir()
        view = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual((view['state'], view['unusable_folders'], view['unusable_files']), ('stale', ['fixtures'], []))
        (self.source / 'greeting.py').unlink()
        view = self.host.dispatch('coding_permission', self.scope)
        self.assertEqual((view['state'], view['unusable_files']), ('stale', ['greeting.py']))

    def test_only_settings_take_a_null_thread(self):
        for op, extra in (('coding_request', {'clientId': 'c', 'text': 'Hello'}), ('coding_requests', {}), ('conversation_route_set', {'mode': 'auto'}),
                          ('coding_fresh_start_set', {'enabled': True}), ('coding_queue_cancel', {'queueId': 'q'})):
            with self.subTest(op=op), self.assertRaises(Rejected):
                self.host.dispatch(op, {**self.scope, **extra})
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_permission', {'projectId': 'missing', 'channelId': None})
        self.host.dispatch('project_archive', {'projectId': 'one'})
        with self.assertRaisesRegex(Rejected, 'archived'):
            self.host.dispatch('coding_permission', self.scope)

    def test_the_project_account_decides_what_full_access_needs(self):
        view = self.host.dispatch('coding_permission', self.scope)['full_access']
        self.assertEqual(view['unavailable'], 'Allow coding for the whole project folder first. Full access builds on that permission.')
        with self.host.store.db:
            self.host.store.db.execute('UPDATE projects SET profile_id=?, model=? WHERE id=?', (self.codex, 'gpt-5.6-sol', 'one'))
        codex = self.host.dispatch('coding_permission', self.scope)['full_access']
        self.assertEqual(codex['provider'], 'codex')
        self.assertEqual(codex['unavailable'], view['unavailable'])
        self.assertFalse(codex['computer_available'])
        with self.assertRaisesRegex(Rejected, 'whole project folder'):
            self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': True, 'warning': 'beta.15'})

    def test_a_running_task_anywhere_in_the_project_holds_full_access(self):
        from coding_integration import CodingTasks
        self.host.coding = CodingTasks(self.host)
        channel = self.host.dispatch('create_conversation', {'projectId': 'one', 'name': 'Busy', 'private': False})['id']
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO coding_tasks VALUES (?,?)', ('t1', json.dumps(
                dict(id='t1', state='working', context=dict(thread_id=channel, project_id='one', actor=ACTOR), runs=[], tokens=[]))))
        self.host.coding.turns['t1'] = SimpleNamespace(closed=False, stop=lambda: None, close=lambda: None)
        with self.assertRaisesRegex(Rejected, 'Finish or stop the running task before changing full access'):
            self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': False})
        self.host.coding.turns['t1'] = SimpleNamespace(closed=True, stop=lambda: None, close=lambda: None)
        self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': False})
        self.host.coding.turns.clear()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE coding_tasks SET document=? WHERE id='t1'", (json.dumps(
                dict(id='t1', state='blocked', context=dict(thread_id=channel, project_id='one', actor=ACTOR), runs=[], tokens=[])),))

    def test_the_collaboration_policy_reads_for_the_project(self):
        view = self.host.dispatch('collab_policy', self.scope)
        self.assertEqual((view['state'], view['mode']), ('off', 'off'))
        with self.assertRaises(Rejected):
            self.host.dispatch('collab_packets', {**self.scope, 'taskId': 't'})

    def test_with_asking_off_no_thread_needs_naming_but_asking_on_still_does(self):
        policy = {**self.scope, 'agents': [], 'channels': [], 'maxParallel': 1, 'maxDepth': 1, 'maxCalls': 1}
        saved = self.host.dispatch('collab_policy_set', {**policy, 'mode': 'off', 'review': {'enabled': False, 'reviewer': None, 'effort': None}})
        self.assertEqual((saved['mode'], saved['channels']), ('off', []))
        for mode in ('mentions', 'automatic'):
            with self.subTest(mode=mode), self.assertRaisesRegex(Rejected, 'Allowed conversations are 1 to'):
                self.host.dispatch('collab_policy_set', {**policy, 'mode': mode})


if __name__ == '__main__':
    unittest.main()
