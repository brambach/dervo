"""Offline regressions for bounded file-index retention and Home's optional older-thread context."""
import json
import unittest
from unittest.mock import patch

import conversation_activity
import project_memory as memory
import project_orchestrator as home
from project_record_scope import project_record_scope
from test_project_orchestrator import HomeFixture, answer, ref


class FileIndexRetentionTests(unittest.TestCase):
    def setUp(self):
        memory.forget_file_index()
        self.addCleanup(memory.forget_file_index)
        self.now = 0
        for mock in (patch('os.path.isdir', return_value=True),
                     patch.object(memory, '_repository_names', return_value={'example.ts'}),
                     patch.object(memory.time, 'monotonic', side_effect=lambda: self.now)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_expired_roots_are_freed_even_on_an_invalid_read(self):
        for number in range(20):
            memory.file_index('/fixture/' + str(number))
        self.now = memory.INDEX_CACHE_SECONDS
        self.assertIsNone(memory.file_index(None))
        self.assertEqual(len(memory._INDEX_CACHE), 0)

    def test_root_cap_evicts_least_recently_used_without_extending_expiry(self):
        with patch.object(memory, 'INDEX_CACHE_ROOTS', 2):
            memory.file_index('/first')
            memory.file_index('/second')
            self.now = 10
            memory.file_index('/first')
            memory.file_index('/third')
            self.assertEqual(list(memory._INDEX_CACHE), ['/first', '/third'])
            self.now = 20
            memory.file_index('/third')
            self.assertEqual(list(memory._INDEX_CACHE), ['/third'])

    def test_size_cap_and_oversized_listing_preserve_the_answer(self):
        memory.file_index('/first')
        cost = memory._INDEX_CACHE['/first'][2]
        with patch.object(memory, 'INDEX_CACHE_BYTES', cost + 10):
            memory.file_index('/second')
            self.assertEqual(list(memory._INDEX_CACHE), ['/second'])
            large = {'file-' + str(i) + '.ts' for i in range(1000)}
            with patch.object(memory, '_repository_names', return_value=large):
                self.assertEqual(memory.file_index('/large'), large)
            self.assertNotIn('/large', memory._INDEX_CACHE)
            self.assertLessEqual(sum(cached[2] for cached in memory._INDEX_CACHE.values()), memory.INDEX_CACHE_BYTES)

    def test_expiry_refreshes_names_and_clear_removes_every_entry(self):
        self.assertEqual(memory.file_index('/first'), {'example.ts'})
        self.now = memory.INDEX_CACHE_SECONDS
        with patch.object(memory, '_repository_names', return_value={'replacement.ts'}):
            self.assertEqual(memory.file_index('/first'), {'replacement.ts'})
        memory.forget_file_index()
        self.assertFalse(memory._INDEX_CACHE)

    def test_unreliable_listing_stays_unknown_when_cached(self):
        with patch.object(memory, '_repository_names', return_value=None), \
                patch('os.walk', return_value=[('/first', [], ['a', 'b'])]), \
                patch.object(memory, 'FILE_INDEX_LIMIT', 1):
            self.assertIsNone(memory.file_index('/first'))
            self.assertIsNone(memory.file_index('/first'))


class HomeContextSelectionTests(HomeFixture):
    def rows(self, count):
        """Fixture rows only. No host requests, provider calls, real chat text or production database."""
        db = self.host.store.db
        conversation_activity.schema(db)
        with db:
            for number in range(count):
                thread = f'fixture-{number:04d}'
                db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
                           (thread, 'p', thread, 'a', 'fixture', 'shared', None, None))
                db.execute('INSERT INTO local_channels VALUES (?,?)',
                           (thread, f'Feature {number:04d}: improve the project settings and chat interactions'))
                db.execute('INSERT INTO conversation_seen VALUES (?,?)', (thread, number + 1))

    def snapshot(self, text):
        orchestrator = home.Orchestrator(self.host.store, host=self.host)
        return orchestrator.snapshot(project_record_scope(self.host.store, 'p', 'a'), text)

    def rename(self, number, name):
        with self.host.store.db:
            self.host.store.db.execute('UPDATE local_channels SET name=? WHERE id=?', (name, f'fixture-{number:04d}'))

    def test_two_thousand_unrelated_threads_keep_a_bounded_optional_index(self):
        self.rows(2000)
        prompt, refs, *_ = self.snapshot('What needs my attention?')
        self.assertEqual(len(refs), home.THREADS + home.OLDER_THREADS)
        self.assertLess(len(prompt.encode()), 20000)
        self.assertIn('1930 other older shared threads omitted', prompt)
        self.assertNotIn('fixture-0000', refs.values())
        self.assertIn('exact thread name', prompt)

    def test_exact_old_title_and_id_remain_available_with_full_context(self):
        self.rows(100)
        self.rename(0, 'Retired parser migration')
        prompt, refs, *_ = self.snapshot('Continue #retired-parser-migration')
        self.assertIn('fixture-0000', refs.values())
        self.assertIn('#Retired parser migration:', prompt)
        prompt, refs, *_ = self.snapshot('Continue thread fixture-0000')
        self.assertIn('fixture-0000', refs.values())
        self.assertIn('#Retired parser migration:', prompt)

    def test_two_similar_old_matches_remain_available_without_guessing(self):
        self.rows(100)
        self.rename(0, 'Parser migration alpha')
        self.rename(1, 'Parser migration beta')
        prompt, refs, *_ = self.snapshot('Continue the parser migration')
        self.assertTrue({'fixture-0000', 'fixture-0001'} <= set(refs.values()))
        self.assertIn('Parser migration alpha', prompt)
        self.assertIn('Parser migration beta', prompt)
        self.assertIn('If a reference is ambiguous or missing', prompt)

    def test_common_subject_words_dont_pin_all_old_threads(self):
        self.rows(100)
        _, refs, *_ = self.snapshot('Improve the project settings and chat interactions')
        self.assertEqual(len(refs), home.THREADS + home.OLDER_THREADS)

    def test_request_for_a_new_conversation_doesnt_pin_all_unnamed_chats(self):
        self.rows(100)
        with self.host.store.db:
            self.host.store.db.execute("UPDATE local_channels SET name='New conversation'")
        _, refs, *_ = self.snapshot('Start a new conversation about the sidebar')
        self.assertEqual(len(refs), home.THREADS + home.OLDER_THREADS)

    def test_duplicate_exact_titles_stay_visible_as_ambiguous_candidates(self):
        self.rows(100)
        self.rename(0, 'Parser migration')
        self.rename(1, 'Parser migration')
        prompt, refs, *_ = self.snapshot('Continue #parser-migration')
        self.assertTrue({'fixture-0000', 'fixture-0001'} <= set(refs.values()))
        self.assertEqual(prompt.count('#Parser migration:'), 2)

    def test_optional_byte_budget_doesnt_cut_the_request_or_rules(self):
        self.rows(100)
        rule = 'Ask me before starting any job.'
        request = 'Keep every word of this request. ' * 50 + 'Do not deploy.'
        with patch.object(home, 'OLDER_THREAD_BYTES', 150), \
                patch.object(home.Orchestrator, 'approved_rules', return_value=([rule], False)):
            prompt, refs, _, holds, _ = self.snapshot(request)
        self.assertLessEqual(len(refs), home.THREADS + 1)
        self.assertIn(request, prompt)
        self.assertIn(rule, prompt)
        self.assertIn('Ask me before starting any job', holds)

    def test_running_and_needs_user_survive_but_an_old_settled_state_doesnt_pin(self):
        self.rows(100)
        db = self.host.store.db
        with db:
            for number, status in enumerate(('running', 'needs', 'running')):
                db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)',
                           (f'task-{number}', f'fixture-{number:04d}', status, None, 0.1))
            db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)',
                       ('task-2-finished', 'fixture-0002', 'settled', None, 0.2))
        _, refs, *_ = self.snapshot('What needs my attention?')
        self.assertTrue({'fixture-0000', 'fixture-0001'} <= set(refs.values()))
        self.assertNotIn('fixture-0002', refs.values())

    def test_task_creation_order_survives_late_reconciliation_of_an_older_task(self):
        self.rows(100)
        db = self.host.store.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            for number, newest in enumerate(('running', 'needs', 'settled')):
                thread = f'fixture-{number:04d}'
                # The older task was reconciled later. Its changed time isn't the thread's newest task.
                for age, status, changed in [('old', 'needs' if newest == 'settled' else 'settled', 2), ('new', newest, 1)]:
                    task_id = f'{thread}-{age}'
                    task = dict(id=task_id, origin='conversation', context=dict(thread_id=thread, project_id='p'),
                                state={'running': 'working', 'needs': 'needs_permission', 'settled': 'answered'}[status],
                                runs=[], messages=[], tokens=[])
                    db.execute('INSERT INTO coding_tasks VALUES (?,?)', (task_id, json.dumps(task)))
                    db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?)',
                               (task_id, thread, status, None, changed))
        with patch.object(conversation_activity, 'session_start', return_value=None):
            activity = conversation_activity.summaries(db, [f'fixture-{number:04d}' for number in range(3)])
        self.assertEqual(activity['fixture-0000']['status'], 'running')
        self.assertEqual(activity['fixture-0001']['status'], 'needs')
        _, refs, *_ = self.snapshot('What needs my attention?')
        self.assertTrue({'fixture-0000', 'fixture-0001'} <= set(refs.values()))
        self.assertNotIn('fixture-0002', refs.values(), 'an older need must not replace the newer settled task')

    def test_private_and_work_account_titles_never_get_pinned_by_a_request(self):
        self.rows(100)
        self.rename(0, 'Secret parser migration')
        self.rename(1, 'Work payroll migration')
        self.host.store.add_profile('w', 'codex', self.root / 'w', 'w@example.test', 'work')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET visibility='private', owner='local-user' WHERE id='fixture-0000'")
            self.host.store.db.execute("UPDATE threads SET profile_id='w' WHERE id='fixture-0001'")
        prompt, refs, *_ = self.snapshot('Continue Secret parser migration and Work payroll migration')
        thread_block = prompt.split('The person says:')[0]
        self.assertNotIn('Secret parser migration', thread_block)
        self.assertNotIn('Work payroll migration', thread_block)
        self.assertFalse({'fixture-0000', 'fixture-0001'} & set(refs.values()))

    def test_recent_home_target_remains_available_after_it_falls_out_of_recency(self):
        self.rows(100)
        self.rename(0, 'Parser migration')
        self.reply = lambda prompt: answer('It stopped.', mentions=[ref(prompt, 'Parser migration')])
        self.say('How did Parser migration finish?')
        prompt, refs, *_ = self.snapshot('Continue that thread')
        self.assertIn('fixture-0000', refs.values())
        self.assertIn('#Parser migration:', prompt)

    def test_omitted_thread_refs_cannot_be_used_for_actions(self):
        self.rows(100)
        self.reply = lambda prompt: answer('Added.', actions=[dict(kind='add', thread='t100', instruction='Improve settings.')])
        reply = self.say('Improve settings.')['entries'][-1]
        self.assertEqual(reply['parts'], [])
        self.assertNotIn('t100', reply.get('refs', {}))
