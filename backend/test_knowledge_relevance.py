"""Request-aware project guidance under the existing source and delivery limits."""
from test_project_knowledge import KnowledgeTests
from project_knowledge import ProjectKnowledge
from host import ACTOR


class RelevanceTests(KnowledgeTests):
    def approve_text(self, text, provenance='Project decision', source=None):
        rule = self.call('save', ruleId=None, expectedRevision=0, text=text,
                         provenance=provenance, source=source)
        return self.call('approve', ruleId=rule['id'], versionId=rule['versions'][0]['id'], expectedRevision=1)

    def snapshot(self, query, previous=()):
        return ProjectKnowledge(self.host.store).snapshot(
            self.host.store.binding('p', 'p', actor=ACTOR), ACTOR, previous, query=query)

    def test_later_relevant_lesson_survives_full_rule_budget(self):
        for i in range(14):
            self.approve_text(f'Invoice exports use the approved accounting template number {i}.')
        mid = self.host.store.send('p', 'p', 'source', 'Storage exceptions broke the search field.', actor=ACTOR)
        source = {'channelId': 'p', 'messageId': mid}
        lesson = self.approve_text('For search query retention, update React state before sessionStorage. Catch storage exceptions so the search field stays usable.', source=source)
        snap = self.snapshot('Fix query retention in WorkspaceSearch.tsx when sessionStorage throws.')
        self.assertEqual(snap['rules'][0]['rule_id'], lesson['id'])
        self.assertEqual(snap['rules'][0]['source'], source)
        self.assertEqual(len(snap['rules']), 12)
        self.assertEqual(snap['omitted'], 3)
        self.assertLessEqual(len(snap['text'].encode()), 16384)
        self.assertEqual(snap['selection']['method'], 'word_forms_v1')

    def test_no_match_preserves_general_guidance_and_provenance_is_not_ranked(self):
        first = self.approve_text('Keep changes inside the granted files.')
        self.approve_text('Use the accounting template.', provenance='search query sessionStorage')
        snap = self.snapshot('Improve search query sessionStorage.')
        self.assertEqual([r['rule_id'] for r in snap['rules']][0], first['id'])
        self.assertEqual(len(snap['rules']), 2)
        self.assertEqual(self.snapshot('')['rules'], snap['rules'])

    def test_ranking_revalidates_source_and_never_uses_proposed_versions(self):
        rule = self.approve_text('Use the accounting template.')
        self.call('save', ruleId=rule['id'], expectedRevision=2,
                  text='Search query sessionStorage lesson UNAPPROVED', provenance='Proposed', source=None)
        self.host.store.add_thread('source', 'p', 'source', 'shared')
        mid = self.host.store.send('source', 'p', 'source', 'Saved lesson', actor=ACTOR)
        hidden = self.approve_text('Search query sessionStorage HIDDEN', source={'channelId':'source','messageId':mid})
        before = self.snapshot('search query sessionStorage')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='source'", (ACTOR,))
        after = self.snapshot('search query sessionStorage', [before])
        self.assertNotIn('UNAPPROVED', after['text'])
        self.assertNotIn('HIDDEN', after['text'])
        self.assertEqual([r['rule_id'] for r in after['rules']], [rule['id']])
        self.assertIn(hidden['id'], [r['rule_id'] for r in after['retired_versions']])

    def test_task_source_rejects_forged_scope_active_runs_and_private_promotion(self):
        import json
        from dervo_connections import Rejected
        binding=self.host.store.binding('p','p',actor=ACTOR)
        task=dict(id='task',context=dict(project_id='p',thread_id='p',actor=ACTOR),binding=binding,
                  runs=[dict(id='run',state='completed')])
        self.host.store.db.execute('CREATE TABLE coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        def persist():
            with self.host.store.db:
                self.host.store.db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)',('task',json.dumps(task)))
        persist()
        source=dict(channelId='p',taskId='task',runId='run')
        lesson=self.approve_text('Search storage lesson.',source=source)
        self.assertEqual(self.snapshot('search')['rules'][0]['source'],source)
        for key,value in [('actor','another-user'),('project_id','other'),('thread_id','other')]:
            original=task['context'][key];task['context'][key]=value;persist()
            with self.assertRaises(Rejected): self.approve_text('Forged source',source=source)
            task['context'][key]=original
        task['runs'][0]['state']='sending';persist()
        with self.assertRaises(Rejected): self.approve_text('Active run',source=source)
        task['runs'][0]['state']='completed'
        for key,value in [('visibility','private'),('model','retired-model'),('cwd','/another/root'),('profile_id','other')]:
            original=task['binding'][key];task['binding'][key]=value;persist()
            with self.assertRaises(Rejected): self.approve_text('Changed binding',source=source)
            self.assertEqual(self.snapshot('search')['rules'],[])
            task['binding'][key]=original
        self.assertIsNotNone(lesson['active_version_id'])


def load_tests(loader, tests, pattern):
    import unittest
    return unittest.TestSuite(RelevanceTests(name) for name in RelevanceTests.__dict__ if name.startswith('test_'))
