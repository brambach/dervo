"""Saved briefing metadata uses scoped records, with no live evidence or provider calls."""
import json
import unittest
import test_workspace_search as fixtures
from workspace_search import _snippet
from dervo_connections import Rejected


class WorkspaceBriefingTests(unittest.TestCase):
    setUp = fixtures.WorkspaceSearchTests.setUp
    tearDown = fixtures.WorkspaceSearchTests.tearDown

    def save(self, **values):
        db = self.host.store.db
        task = json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        task.update(values)
        db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'", (json.dumps(task),))
        db.commit()

    def row(self):
        result = self.host.dispatch('project_catchup', {**self.scope, 'limit':20})
        self.assertEqual({row['id'] for row in result['results']}, {'task-current','msg-current','msg-shared','literal','msg-other','task-other'})
        self.assertFalse(hasattr(self.host, 'coding'))
        return next(row for row in result['results'] if row['id']=='task-current')

    def test_ready_result_doesnt_invent_human_approval_or_current_verification(self):
        self.save(state='ready_for_review', acceptance=dict(required=False), completion=dict(stale=True))
        row = self.row()
        self.assertFalse(row['humanApprovalRequired'])
        self.assertTrue(row['stale'])
        self.assertFalse(row['helper'])

    def test_explicit_approval_and_helper_origin_are_saved_facts(self):
        self.save(acceptance=dict(required=True), origin='collaboration', role='reviewer', agent='reviewer')
        row = self.row()
        self.assertTrue(row['humanApprovalRequired'])
        self.assertTrue(row['helper'])
        self.assertEqual(row['title'], 'Review by @reviewer')
        self.assertEqual(self.host.turns, {})

    def test_legacy_or_malformed_policy_never_disables_required_approval(self):
        self.assertTrue(self.row()['humanApprovalRequired'])
        for acceptance, completion in ((None,None), ({},None), ('yes','yes'), ({'required':0},{'stale':0}), ({'required':1},{'stale':1})):
            with self.subTest(acceptance=acceptance):
                self.save(acceptance=acceptance, completion=completion)
                row = self.row()
                self.assertTrue(row['humanApprovalRequired'])
                self.assertFalse(row['stale'])

    def test_open_review_is_distinct_from_a_human_decision(self):
        for phase in ('pending','initial','correction','final','passed','unresolved'):
            with self.subTest(phase=phase):
                self.save(state='ready_for_review', acceptance=dict(required=True), collaboration=dict(review=dict(enabled=True,phase=phase)))
                row = self.row()
                self.assertEqual(row['reviewPending'], phase in ('pending','initial','correction','final'))

    def test_human_decision_is_only_the_exact_saved_value_across_rows_and_excerpts(self):
        for decision in ('approved', 'rejected', None, 'passed', 'Approved', '', True, 1, {}, ['approved']):
            with self.subTest(decision=decision):
                self.save(state='idle', review=decision, runs=[], tokens=[],
                          collaboration=dict(review=dict(phase='passed')))
                expected = decision if type(decision) is str and decision in ('approved', 'rejected') else None
                for row in (self.row(), self.search_row('needle')):
                    self.assertEqual(row['humanDecision'], expected)
                    self.assertFalse(row['outcomeUncertain'])
                    opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
                    self.assertEqual(opened['humanDecision'], expected)
                    self.assertFalse(opened['outcomeUncertain'])
        self.assertEqual(self.host.turns, {})
        self.assertFalse(hasattr(self.host, 'coding'))

    def test_saved_decision_doesnt_hide_current_activity_or_uncertain_observation(self):
        cases = [
            ('working', [], [], False),
            ('ready_for_review', [], [], False),
            ('disconnected', [], [], True),
            ('unknown', [], [], True),
            ('idle', [{'state':'uncertain'}, {'state':'completed'}], [], True),
            ('ready_for_review', [], [{'state':'uncertain'}, {'state':'delivered'}], True),
            ('blocked', [{'state':'interrupted'}], [{'state':'cancelled'}], False),
            ('idle', 'invalid runs', [], True),
            ('idle', [], {'state':'delivered'}, True),
            ('idle', [None], [], True),
            ('idle', [], [1], True),
        ]
        for state, runs, tokens, uncertain in cases:
            with self.subTest(state=state, runs=runs, tokens=tokens):
                self.save(state=state, review='approved', runs=runs, tokens=tokens)
                row = self.row()
                self.assertEqual(row['status'], state)
                self.assertEqual(row['humanDecision'], 'approved')
                self.assertIs(row['outcomeUncertain'], uncertain)
                opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
                self.assertIs(opened['outcomeUncertain'], uncertain)

    def test_decision_uncertainty_and_staleness_changes_require_a_fresh_source(self):
        self.save(state='idle', review='approved', runs=[], tokens=[], completion={})
        row = self.row()
        for changes in (dict(review='rejected', state='ready_for_review'),
                        dict(tokens=[dict(state='uncertain')]), dict(completion=dict(stale=True))):
            with self.subTest(changes=changes):
                self.save(**changes)
                with self.assertRaises(Rejected):
                    self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
                fresh = self.row()
                self.assertNotEqual(fresh['source']['revision'], row['source']['revision'])
                opened = self.host.dispatch('workspace_source', {**self.scope, 'source':fresh['source']})
                for key in ('humanDecision', 'outcomeUncertain', 'stale'):
                    self.assertEqual(opened[key], fresh[key])
                row = fresh
        self.assertEqual(row['humanDecision'], 'rejected')
        self.assertTrue(row['outcomeUncertain'])
        self.assertTrue(row['stale'])
        self.assertFalse(hasattr(self.host, 'coding'))

    def search_row(self, query):
        rows = self.host.dispatch('workspace_search', {**self.scope, 'query':query, 'limit':20})['results']
        return next(row for row in rows if row['id'] == 'task-current')

    def test_search_prefers_latest_matching_answer_and_opens_its_start(self):
        self.save(messages=[dict(role='user',text='Explain orchard storage'),
                            dict(role='assistant',text='First orchard answer'),
                            dict(role='user',text='Clarify orchard storage'),
                            dict(role='assistant',text='Here is the explanation. ' * 12 + 'Orchard storage is local.')])
        row = self.search_row('orchard')
        self.assertEqual(row['matchRole'], 'assistant')
        self.assertIn('Orchard storage is local.', row['snippet'])
        opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
        self.assertTrue(opened['text'].startswith('Here is the explanation.'))
        self.assertNotIn('First orchard answer', opened['text'])

    def test_search_labels_request_only_matches_and_keeps_long_answer_match_reachable(self):
        self.save(messages=[dict(role='user',text='Explain orchard storage'),
                            dict(role='assistant',text='The answer uses a different term.')])
        row = self.search_row('orchard')
        self.assertEqual(row['matchRole'], 'user')
        self.assertTrue(self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})['text'].startswith('Explain orchard'))
        self.save(messages=[dict(role='user',text='Explain orchard storage'),
                            dict(role='assistant',text='Opening. ' + 'x' * 20000 + ' orchard ending')])
        row = self.search_row('orchard')
        self.assertEqual(row['matchRole'], 'assistant')
        opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
        self.assertIn('orchard ending', opened['text'])
        self.assertTrue(opened['truncated'])

    def test_search_preview_keeps_the_complete_maximum_length_phrase(self):
        phrase = 'storage ' * 31 + 'retained'
        self.assertEqual(len(phrase), 256)
        answer = 'Earlier background. ' * 30 + phrase + ' Follow-up details. ' * 30
        self.save(messages=[dict(role='assistant', text=answer)])
        row = self.search_row(phrase)
        self.assertIn(phrase, row['snippet'])
        self.assertLessEqual(len(row['snippet']), 422)
        opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
        self.assertEqual(opened['text'], answer)

    def test_search_preview_starts_at_the_matching_sentence_when_available(self):
        text = 'Unrelated background. ' * 30 + 'Storage failures keep the query in memory. ' + 'More context. ' * 40
        preview = _snippet(text, 'Storage')
        self.assertTrue(preview.startswith('…Storage failures'))
        self.assertTrue(preview.endswith('.…'))
        self.assertLessEqual(len(preview), 422)

    def test_search_preview_keeps_words_and_multilingual_matches_with_bounded_long_tokens(self):
        text = 'background ' * 40 + 'storage ' + 'information ' * 50
        preview = _snippet(text, 'storage')
        self.assertTrue(preview.startswith('…background '))
        self.assertTrue(preview.endswith('information…'))
        for phrase in ('Mémoire locale', '保存した検索', 'STORAGE', 'x' * 256):
            with self.subTest(phrase=phrase):
                preview = _snippet('z' * 2000 + phrase + 'z' * 2000, phrase)
                self.assertIn(phrase, preview)
                self.assertLessEqual(len(preview), 422)

    def test_search_preview_preserves_whitespace_inside_the_matched_phrase(self):
        for phrase in (' needle ', '\nneedle\n', 'needle ', ' needle'):
            with self.subTest(phrase=repr(phrase)):
                self.save(messages=[dict(role='assistant', text=phrase)])
                row = self.search_row(phrase)
                self.assertEqual(row['snippet'], phrase)
                opened = self.host.dispatch('workspace_source', {**self.scope, 'source':row['source']})
                self.assertEqual(opened['text'], phrase)


if __name__ == '__main__':
    unittest.main()


class CatchUpFactsTests(unittest.TestCase):
    """Bryce's 16 September direction: Catch up should say what each conversation is doing and where it's at, from records."""
    setUp = fixtures.WorkspaceSearchTests.setUp
    tearDown = fixtures.WorkspaceSearchTests.tearDown
    save = WorkspaceBriefingTests.save

    def page(self, **extra):
        return self.host.dispatch('project_catchup', {**self.scope, 'limit': 20, **extra})

    def row(self):
        page = self.page()
        self.assertFalse(hasattr(self.host, 'coding'), 'Catch up never opens the coordinator')
        return next(row for row in page['results'] if row['id'] == 'task-current')

    def test_a_completed_task_row_carries_its_saved_facts_and_one_sentence(self):
        self.save(state='completed', check_profile='python-unittest',
                  messages=[dict(role='user', text='Fix fixture', created_at=1700000000), dict(role='assistant', text='needle result', created_at=1700000600)],
                  runs=[dict(id='r', state='completed')], tokens=[],
                  completion=dict(at=1700000700, attempt=1, run_id='r', review_phase='passed', changed_files=['a.py', 'b.py'], test_exit_status=0, check_profile='python-unittest', human_approved=False),
                  collaboration=dict(review=dict(enabled=True, phase='passed')))
        row = self.row()
        self.assertEqual((row['askedAt'], row['answeredAt'], row['completedAt']), (1700000000, 1700000600, 1700000700))
        self.assertEqual((row['changedFiles'], row['changedFileCount'], row['testExitStatus'], row['checkProfile'], row['reviewPhase'], row['runState']), (['a.py', 'b.py'], 2, 0, 'python-unittest', 'passed', 'completed'))
        self.assertEqual(row['summary'], 'Changed a.py, b.py. Host tests exit 0. Independent review passed. Not marked as approved by you.')
        self.assertNotIn('nextAction', row)
        excerpt = self.host.dispatch('workspace_source', {**self.scope, 'source': row['source']})
        self.assertEqual(excerpt['summary'], row['summary'])

    def test_summaries_come_from_the_record_and_never_claim_more(self):
        cases = [
            (dict(state='working', runs=[dict(id='r', state='running')], tokens=[dict(run_id='r', tool='dervo_write_file', state='executing', path='/private/project/src/auth.py')]), 'Still working: editing auth.py.'),
            (dict(state='working', runs=[dict(id='r', state='running')], tokens=[dict(run_id='r', tool='dervo_run_tests', state='running')]), 'Still working: running the configured check.'),
            (dict(state='working', runs=[dict(id='r', state='running')], tokens=[]), 'Still working.'),
            (dict(state='answered', runs=[], tokens=[]), 'Answered in text. No project file was changed.'),
            (dict(state='blocked', error='API Error: Overloaded', runs=[], tokens=[]), 'Couldn’t finish: API Error: Overloaded'),
            (dict(state='blocked', error={'message': 'Usage limit', 'additionalDetails': 'argv=--token SHOULD-NOT-LEAK'}, runs=[], tokens=[]), 'Couldn’t finish: Usage limit'),
            (dict(state='disconnected', error='Host restarted', runs=[dict(id='r', state='uncertain')], tokens=[]), 'Outcome unconfirmed. Dervo lost confirmation of a run or tool result.'),
            (dict(state='ready_for_review', runs=[], tokens=[], acceptance=dict(required=True), collaboration=dict(review=dict(enabled=True, phase='initial'))), 'Independent review is running.'),
            (dict(state='ready_for_review', runs=[], tokens=[], review='rejected'), 'Waiting for your correction feedback.'),
            (dict(state='ready_for_review', runs=[], tokens=[], completion=dict(stale=True)), 'The files changed after this result. It needs a fresh check.'),
            (dict(state='ready_for_review', runs=[], tokens=[], error=None, review=None, completion=None, collaboration=dict(review=dict(enabled=True, phase='passed'))), 'Ready for review. Independent review passed.'),
            (dict(state='idle', review='approved', runs=[], tokens=[]), 'Result approved by you.'),
            (dict(state='needs_permission', runs=[], tokens=[]), 'Waiting for file permission before it can start.'),
            (dict(state='completed', completion=dict(at=5, changed_files=[], test_exit_status=0, check_profile='typescript-check'), runs=[dict(id='r', state='completed')], tokens=[]), 'No file changed. Host type check exit 0. No independent review was configured. Not marked as approved by you.'),
        ]
        for values, expected in cases:
            with self.subTest(state=values['state'], expected=expected):
                self.save(**{**dict(error=None, review=None, completion=None, collaboration=None, acceptance=None), **values})
                row = self.row()
                self.assertEqual(row['summary'], expected)
                self.assertNotIn('SHOULD-NOT-LEAK', json.dumps(row))

    def test_new_since_your_last_visit_is_a_watermark_and_paging_survives_it(self):
        first = self.page()
        self.assertIsNone(first['since'], 'a first visit has no earlier mark')
        self.assertFalse(any(row.get('isNew') for row in first['results']))
        second = self.page()
        self.assertIsNotNone(second['since'])
        self.assertFalse(any(row['isNew'] for row in second['results']), 'nothing was saved between visits')
        self.save(state='answered', messages=[dict(role='user', text='Fix fixture'), dict(role='assistant', text='needle result, revised')])
        from task_activity import save_task
        db = self.host.store.db
        with db:
            task = json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
            save_task(db, task, json.dumps(task))
        third = self.page(limit=2)
        flagged = [row['id'] for row in third['results'] if row.get('isNew')]
        self.assertEqual(flagged, ['task-current'], 'only the record saved since the last visit is new')
        self.assertIsNotNone(third['cursor'], 'a two-row page leaves more to load')
        more = self.page(limit=2, cursor=third['cursor'])
        self.assertTrue(all(row.get('isNew') is False for row in more['results']), 'the continuation keeps the same mark and stays valid after the watermark write')
        self.assertEqual(more['since'], third['since'], 'a continuation page reports the same visit mark')
        fourth = self.page()
        self.assertFalse(any(row['isNew'] for row in fourth['results']), 'seen once, not new again')

    def test_pending_lesson_suggestions_are_counted_from_the_index_only(self):
        db = self.host.store.db
        self.assertIsNone(self.page()['lessonsPending'])
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS project_lessons (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, profile_id TEXT NOT NULL, task_id TEXT, run_id TEXT, status TEXT NOT NULL, document TEXT NOT NULL)')
            for i, (project, profile, status) in enumerate([('project', 'personal', 'pending'), ('project', 'personal', 'pending'), ('project', 'personal', 'kept'), ('elsewhere', 'personal', 'pending'), ('project', 'other', 'pending')]):
                db.execute('INSERT INTO project_lessons VALUES (?,?,?,?,?,?,?)', (f'l{i}', project, profile, None, None, status, '{}'))
        self.assertEqual(self.page()['lessonsPending'], 2)
        self.assertFalse(hasattr(self.host, 'coding'))

    def test_an_indexing_pass_does_not_count_as_a_visit(self):
        from unittest.mock import patch
        import message_activity
        db = self.host.store.db
        real = message_activity.prepare
        with patch.object(message_activity, 'prepare', side_effect=[False, True, True, True]) as prepared:
            first = self.page()
            self.assertTrue(first.get('indexing'))
            self.assertNotIn('since', first, 'no rows were shown, so nothing was seen')
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catchup_seen'").fetchone(), 'no mark is written on an indexing pass')
            shown = self.page()
            self.assertFalse(shown.get('indexing'))
            self.assertIsNone(shown['since'], 'the first page with rows is the first visit')
            self.assertIsNotNone(self.page()['since'])
        self.assertIs(message_activity.prepare, real)


class SkillsWaitingTests(unittest.TestCase):
    """18 Sep: a skill an agent proposed waits in Knowledge, and Catch up says so beside the lessons."""

    def test_catch_up_counts_skill_versions_waiting_for_you(self):
        import tempfile
        from pathlib import Path
        from host import Host
        from project_skills import ProjectSkills
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            host = Host(root / 'data')
            try:
                host.store.add_profile('me', 'claude', root / 'me', 'me@example.test', 'personal')
                (root / 'project').mkdir()
                host.store.add_project('p', root / 'project', 'me', 'claude-opus-5')
                skills = ProjectSkills(host.store)
                page = lambda: host.dispatch('project_catchup', dict(projectId='p', channelId='', profileId='me', scope='project', limit=10))
                self.assertEqual(page()['skillsPending'], 0)
                with host.store.db:
                    record = skills.new_record('cut-a-beta', 'Building a beta.')
                    skills.add_version(record, name='cut-a-beta', description='Building a beta.', body='1. Build.', status='proposed', origin='agent', author='claude')
                    skills.add_version(record, name='cut-a-beta', description='Building a beta.', body='1. Build.\n2. Sign.', status='proposed', origin='agent', author='claude')
                    skills.write('p', 'me', record)
                self.assertEqual(page()['skillsPending'], 2)
                with host.store.db:
                    record['archived_at'] = 1
                    skills.write('p', 'me', record)
                self.assertEqual(page()['skillsPending'], 0, 'an archived skill waits for nobody')
            finally:
                host.close()
