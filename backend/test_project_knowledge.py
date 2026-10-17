"""Disposable public Host knowledge contract. No provider or private store access."""
import tempfile
import unittest
from pathlib import Path
from host import Host, ACTOR
from dervo_connections import Rejected

class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.host = Host(self.root/'store')
        self.addCleanup(lambda: self.host.close())
        for p in ('p','other'):
            self.host.store.add_profile(p, 'codex', self.root/p, p+'@example.test', 'personal')
            self.host.store.add_project(p, self.root, p, 'fake')
            self.host.store.add_thread(p,p,p,'shared')
        self.scope = dict(projectId='p',channelId='p',profileId='p')
    def call(self, op, **args): return self.host.dispatch('knowledge_'+op, {**self.scope,**args})
    def draft(self, **kw):
        return self.call('save',ruleId=None,expectedRevision=0,text='Run scoped tests.',provenance='Human authored for this project',source=None,**kw)
    def test_an_adopted_starter_procedure_is_recorded_as_such_and_never_saved_twice(self):
        procedure=self.call('list')['procedures'][0]
        r=self.call('adopt',procedureId=procedure['id'])
        self.assertEqual(r['procedure_id'],procedure['id'])
        self.assertEqual(r['versions'][0]['text'],procedure['text'])
        self.assertIsNone(r['active_version_id'],'adopting saves a draft; approving it is a separate step')
        again=self.call('adopt',procedureId=procedure['id'])
        self.assertEqual(again['id'],r['id'],'a second adoption returns the same rule')
        listed=self.call('list')['rules']
        self.assertEqual([x.get('procedure_id') for x in listed],[procedure['id']])
        self.assertIsNone(self.draft().get('procedure_id'),'a hand-written rule has no procedure')
        with self.assertRaises(Rejected): self.call('adopt',procedureId='not-a-procedure')
        self.host.close(); self.host=Host(self.root/'store')
        self.assertEqual(self.call('list')['rules'][0]['procedure_id'],procedure['id'])
    def test_versions_revision_approval_archive_rollback_restart(self):
        r=self.draft(); v=r['versions'][0]['id']; self.assertIsNone(r['active_version_id'])
        r=self.call('approve',ruleId=r['id'],versionId=v,expectedRevision=1)
        with self.assertRaises(Rejected): self.call('archive',ruleId=r['id'],expectedRevision=1)
        r=self.call('save',ruleId=r['id'],expectedRevision=2,text='Inspect results.',provenance='Human correction',source=None)
        self.assertEqual(r['active_version_id'],v); self.assertEqual(len(r['versions']),2)
        original=r['versions'][0].copy()
        r=self.call('archive',ruleId=r['id'],expectedRevision=3); self.assertIsNone(r['active_version_id'])
        r=self.call('approve',ruleId=r['id'],versionId=v,expectedRevision=4)
        self.assertEqual(r['active_version_id'],v); self.assertEqual(r['versions'][0],original)
        self.host.close(); self.host=Host(self.root/'store')
        self.assertEqual(self.call('list')['rules'][0],r)
    def test_account_validation_private_and_exact_source(self):
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_list',{**self.scope,'profileId':'other'})
        self.host.store.add_thread('private','p','private','private',ACTOR)
        mid=self.host.store.send('private','p','private','secret',actor=ACTOR)
        for source in ({'channelId':'private','messageId':mid}, {'url':'file:///secret'}, {'channelId':'other','messageId':mid}):
            with self.assertRaises(Rejected): self.call('save',ruleId=None,expectedRevision=0,text='rule',provenance='Human',source=source)
        mid=self.host.store.send('p','p','shared','shared record',actor=ACTOR)
        r=self.call('save',ruleId=None,expectedRevision=0,text='rule',provenance='Human',source={'channelId':'p','messageId':mid})
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='p'",(ACTOR,))
        with self.assertRaises(Rejected): self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)
        with self.assertRaises(Rejected): self.call('save',ruleId=None,expectedRevision=0,text=' ',provenance='Human',source=None)

    def test_curated_adoption_is_unapproved_and_bounded_delivery(self):
        from project_knowledge import ProjectKnowledge
        data=self.call('list'); self.assertEqual(len(data['procedures']),3)
        for p in data['procedures']:
            self.assertEqual(p['author'],'Dervo'); self.assertIn('Original',p['provenance']); self.assertIn('No upstream',p['license'])
        adopted=self.call('adopt',procedureId=data['procedures'][0]['id'])
        self.assertIsNone(adopted['active_version_id'])
        for i in range(15):
            r=self.call('save',ruleId=None,expectedRevision=0,text=('rule '+str(i)+' ')*350,provenance='Human',source=None)
            self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)
        b=self.host.store.binding('p','p',actor=ACTOR)
        snap=ProjectKnowledge(self.host.store).snapshot(b,ACTOR)
        self.assertLessEqual(len(snap['rules']),12); self.assertLessEqual(len(snap['text'].encode()),16384)
        self.assertGreater(snap['omitted'],0)

    def test_source_is_revalidated_at_delivery_and_work_is_denied(self):
        from project_knowledge import ProjectKnowledge
        self.host.store.add_thread('source','p','source','shared')
        mid=self.host.store.send('source','p','msg','Shared source',actor=ACTOR)
        r=self.call('save',ruleId=None,expectedRevision=0,text='Rule',provenance='Human',source={'channelId':'source','messageId':mid})
        self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='source'",(ACTOR,))
        snap=ProjectKnowledge(self.host.store).snapshot(self.host.store.binding('p','p',actor=ACTOR),ACTOR)
        self.assertEqual(snap['rules'],[]); self.assertEqual(snap['omitted'],1)
        with self.host.store.db: self.host.store.db.execute("UPDATE profiles SET category='work' WHERE id='p'")
        with self.assertRaises(Rejected): self.call('list')

    def test_empty_fresh_compatibility_and_prior_scope_isolation(self):
        from project_knowledge import ProjectKnowledge
        knowledge=ProjectKnowledge(self.host.store)
        binding=self.host.store.binding('p','p',actor=ACTOR)
        fresh=knowledge.snapshot(binding,ACTOR)
        self.assertEqual(fresh['text'],'')
        for project,profile in [('other','p'),('p','other')]:
            prior=dict(project_id=project,profile_id=profile,text='OTHER_SCOPE',rules=[dict(rule_id='foreign',version_id='foreign',source=None)])
            snap=knowledge.snapshot(binding,ACTOR,[prior])
            self.assertEqual(snap['text'],''); self.assertEqual(snap['retired_versions'],[])

    def test_knowledge_reads_and_sets_agents_notes_with_no_conversation(self):
        """beta.11 (fix-memory, F1): the page mounts with channelId '' and used the conversation ops, which the host
        refused, so notes never showed and both switches stayed off. Knowledge has its own notes ops now."""
        import json, project_memory
        project_memory.schema(self.host.store.db)
        with self.host.store.db:
            item=dict(id='n1',text='Tests run one file at a time.',created_at=1,updated_at=1,provider='claude',source=dict(thread_id='p',visibility='shared'))
            self.host.store.db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)',('n1','p','personal','active',json.dumps(item)))
        page=dict(projectId='p',channelId='',profileId='p')
        with self.assertRaises(Rejected): self.host.dispatch('coding_permission',dict(projectId='p',channelId=''))  # what the page used to ask
        view=self.host.dispatch('knowledge_notes_list',page)
        self.assertEqual(([e['id'] for e in view['entries']],view['enabled'],view['reflect']),(['n1'],True,False))
        self.assertFalse(self.host.dispatch('knowledge_notes_set',{**page,'enabled':False})['enabled'])
        self.assertTrue(self.host.dispatch('knowledge_notes_set',{**page,'enabled':True})['enabled'])
        self.assertTrue(self.host.dispatch('knowledge_notes_reflect_set',{**page,'enabled':True})['reflect'])
        self.assertEqual(self.host.dispatch('knowledge_note_remove',{**page,'entryId':'n1'})['entries'],[])
        self.assertEqual([e['id'] for e in self.host.dispatch('knowledge_note_restore',{**page,'entryId':'n1'})['entries']],['n1'])
        # Negative controls: another account, an unknown note, a switch that isn't a bool, a conversation on another account.
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_notes_list',{**page,'profileId':'other'})
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_note_remove',{**page,'entryId':'nope'})
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_notes_set',{**page,'enabled':'yes'})
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_notes_list',dict(projectId='p',channelId='other',profileId='p'))
        # Notes aren't rules: a work account still reads its own (separate) notes where the rules refuse.
        with self.host.store.db: self.host.store.db.execute("UPDATE profiles SET category='work' WHERE id='p'")
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_list',page)
        self.assertEqual(self.host.dispatch('knowledge_notes_list',page)['entries'],[])

    def rule(self,text,provenance):
        r=self.call('save',ruleId=None,expectedRevision=0,text=text,provenance=provenance,source=None)
        return self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)

    def test_agent_made_rules_move_to_notes_in_one_batch_with_one_undo(self):
        """beta.17.1 (#41, step 8; Bryce, 5 Oct: "Yes, move all 12"). Up to beta.16 agents' lessons became rules once
        approved, and 33 of them went to runs ahead of true notes. Moved, a rule is archived with every version kept and
        its words are a note of the person's own; dropped, it's archived only. One Undo puts the batch back."""
        import project_memory
        from project_knowledge import ProjectKnowledge
        lesson=self.rule('Playwright frames need two requestAnimationFrame waits after a transform.','Suggested by claude during this task. Evidence: frame 213.')
        home=self.rule('Beta16 is at 8b2d79a.','You said this on Home. It waits for your approval in Knowledge before Home or any task hears it.')
        push=self.rule('Windows pushes: push only the Windows branches and never force-push.','Suggested by claude during this task. Evidence: push log.')
        mine=self.rule('Keep replies short.','Human authored for this project')
        asked=self.rule('sure. so dont dm them? reply in the server itself? seems weird to me.','You said this on Home. It waits for your approval in Knowledge before Home or any task hears it.')
        wondering=self.rule('Do the Windows tests need Rust installed first? Always check.','Suggested by claude during this task. Evidence: build log.')
        self.assertEqual(self.call('list')['move_presets'],{lesson['id']:'move',home['id']:'drop',push['id']:'keep',asked['id']:'drop',wondering['id']:'move'},
                         'a rule you wrote isn\'t offered; a push rule stays a rule, and a snapshot or a question Home caught drops, by default')
        self.call('archive',ruleId=asked['id'],expectedRevision=asked['revision'])
        self.call('archive',ruleId=wondering['id'],expectedRevision=wondering['revision'])
        page=dict(projectId='p',channelId='',profileId='p')
        done=self.host.dispatch('knowledge_rule_to_note',{**page,'undoBatch':None,'moves':[
            dict(ruleId=lesson['id'],expectedRevision=lesson['revision'],do='move',text='Playwright frames need two rAF waits after a transform.'),
            dict(ruleId=home['id'],expectedRevision=home['revision'],do='drop',text=None)]})
        notes=project_memory.entries(self.host.store.db,'p','personal')
        self.assertEqual([(n['text'],n['edited_by'],n['source']['kind'],n['source']['rule_id']) for n in notes],
                         [('Playwright frames need two rAF waits after a transform.',ACTOR,'rule',lesson['id'])])
        self.assertEqual(done['moved'],[dict(rule_id=lesson['id'],note_id=notes[0]['id'])])
        self.assertEqual(done['dropped'],[home['id']])
        binding=self.host.store.binding('p','p',actor=ACTOR)
        sent=[r['rule_id'] for r in ProjectKnowledge(self.host.store).snapshot(binding,ACTOR)['rules']]
        self.assertEqual(sorted(sent),sorted([push['id'],mine['id']]),'what moved or dropped no longer goes as a rule')
        rows={r['id']:r for r in self.call('list')['rules']}
        self.assertEqual(len(rows[lesson['id']]['versions']),1,'every version kept')
        # The moved note is the person's: never retired to make room, never held for Keep.
        self.assertTrue(project_memory._protected(notes[0]))
        self.assertNotIn('flag',notes[0])
        # One Undo puts both back.
        undone=self.host.dispatch('knowledge_rule_to_note',{**page,'moves':None,'undoBatch':done['batch']})
        self.assertEqual((sorted(undone['restored']),undone['notes_removed'],undone['changed']),(sorted([lesson['id'],home['id']]),[notes[0]['id']],[]))
        self.assertEqual(project_memory.entries(self.host.store.db,'p','personal'),[])
        sent=[r['rule_id'] for r in ProjectKnowledge(self.host.store).snapshot(binding,ACTOR)['rules']]
        self.assertEqual(len(sent),4)
        with self.assertRaisesRegex(Rejected,'already undone'):
            self.host.dispatch('knowledge_rule_to_note',{**page,'moves':None,'undoBatch':done['batch']})

    def test_a_rule_you_wrote_a_stale_revision_or_a_long_note_moves_nothing(self):
        import project_memory
        lesson=self.rule('Frames need two rAF waits.','Suggested by claude during this task. Evidence: frame 213.')
        mine=self.rule('Keep replies short.','Human authored for this project')
        page=dict(projectId='p',channelId='',profileId='p')
        move=lambda *moves: self.host.dispatch('knowledge_rule_to_note',{**page,'undoBatch':None,'moves':list(moves)})
        ok=dict(ruleId=lesson['id'],expectedRevision=lesson['revision'],do='move',text=None)
        for bad,why in ((dict(ruleId=mine['id'],expectedRevision=mine['revision'],do='move',text=None),'Archive one of yours'),
                        (dict(ok,expectedRevision=0),'Knowledge changed'),(dict(ok,text='x'*401),'Shorten'),(dict(ok,do='keep'),'moves to notes or is dropped'),
                        (dict(ok,extra=1),'Unsupported')):
            with self.assertRaisesRegex(Rejected,why):
                move(ok,bad) if bad.get('ruleId')!=lesson['id'] else move(bad)
        # Nothing half-done: the batch that failed on its second rule left the first one in use and made no note.
        self.assertEqual(project_memory.entries(self.host.store.db,'p','personal'),[])
        self.assertEqual(self.call('list')['rules'][0]['active_version_id'],lesson['active_version_id'])
        with self.assertRaises(Rejected):
            self.host.dispatch('knowledge_rule_to_note',{**page,'undoBatch':None,'moves':None})
        self.assertTrue(move(ok)['moved'], 'the same move, well formed, goes through')

    def test_only_a_rule_that_limits_a_push_deploy_or_publish_presets_to_keep(self):
        """Rendering this project's 12 agent-made rules (5 Oct) preset three to Keep as rule because a file name or a
        past event had "deploy" or "publish" in it. A limit needs the word as a word and a word that limits it."""
        from project_knowledge import move_preset
        lesson='Suggested by claude during this task.'
        for text in ('Windows pushes: push only the Windows branches and never force-push.',
                     'Never deploy the website from a thread; Bryce runs the deploy.',
                     'Don’t publish a beta until the catalog row reads back.'):
            self.assertEqual(move_preset(text,lesson),'keep',text)
        for text in ('The website repo\'s .gitignore denies everything by default, so a new website file must be added there as a "!path" line and to deploy-allowlist.json or git won\'t see it.',
                     'Release uploads use instantiate-release-publisher.py, which makes an r2-publish-<version> folder whose promote.mjs runs with node.',
                     'Prove a test fails without the fix in one shell command, so a closed run never leaves the fix out.'):
            self.assertEqual(move_preset(text,lesson),'move',text)
        # Mentioning a publish isn't a limit; a dated decision like this one drops as a snapshot.
        self.assertEqual(move_preset('On 23 Sep 2026 Bryce decided beta.14 is claude/models-20260923 at d33f8df, rebuilt and published before the invitations go out.',lesson),'drop')

    def test_a_replys_remembered_line_reads_the_brief_list_with_no_git_listing(self):
        """beta.17.1 (#41, A, step 7): every reply that kept a note read Knowledge's whole view on a thread switch, with a
        sweep, four git listings and every pair of notes. ``brief`` is an argument, so the bridge's operations don't change."""
        import json, project_memory
        from unittest.mock import patch
        project_memory.schema(self.host.store.db)
        with self.host.store.db:
            for id,extra in (('n1',{}),('n2',dict(flag=dict(kind='command'),supersedes='n0'))):
                item=dict(id=id,text=f'Note {id}.',created_at=1,updated_at=1,provider='claude',source=dict(thread_id='p',visibility='shared'),**extra)
                self.host.store.db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)',(id,'p','personal','active',json.dumps(item)))
        page=dict(projectId='p',channelId='',profileId='p')
        with patch.object(project_memory,'file_index',side_effect=AssertionError('no git listing')), \
                patch.object(project_memory,'lookalikes',side_effect=AssertionError('no pairs')):
            brief=self.host.dispatch('knowledge_notes_list',{**page,'brief':True})
        self.assertEqual(brief,dict(brief=True,entries=[dict(id='n1',text='Note n1.'),dict(id='n2',text='Note n2.',supersedes='n0',flag=dict(kind='command'))]))
        # Negative controls: without brief it's the whole view, and brief is exactly true.
        self.assertIn('lookalikes',self.host.dispatch('knowledge_notes_list',page))
        for wrong in (False,'yes',1):
            with self.assertRaises(Rejected): self.host.dispatch('knowledge_notes_list',{**page,'brief':wrong})
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_notes_set',{**page,'enabled':True,'brief':True})

    def test_knowledge_edits_keeps_marks_seen_and_pairs_notes_and_none_of_it_makes_a_rule(self):
        """beta.16 (b16/memory): Edit keeps the old words in the note's history with Undo, Keep clears a note's mark,
        Seen clears New, and Keep both stops the Out of date? card. None of them reaches the rules a run gets."""
        import json, project_memory
        from project_knowledge import ProjectKnowledge
        project_memory.schema(self.host.store.db)
        with self.host.store.db:
            for id,text,extra in (('n1','Deploys go through ./deploy.sh after every change.',dict(flag=dict(kind='command'))),
                                  ('n2','The web tests need Node 20.',{}),('n3','The web tests need Node 22.',{})):
                item=dict(id=id,text=text,created_at=10,updated_at=10,provider='claude',source=dict(thread_id='p',visibility='shared'),**extra)
                self.host.store.db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)',(id,'p','personal','active',json.dumps(item)))
        page=dict(projectId='p',channelId='',profileId='p')
        view=self.host.dispatch('knowledge_notes_list',page)
        self.assertEqual(view['seen_at'],0)
        self.assertEqual([(p['older'],p['newer'],p['why']) for p in view['lookalikes']],[('n2','n3','number')])
        self.assertEqual((view['used_tokens'],view['max_tokens']),(round(view['used_chars']/4),1500))  # 6,000 characters in full since beta.17.1
        kept=self.host.dispatch('knowledge_note_keep',{**page,'entryId':'n1'})
        n1=next(e for e in kept['entries'] if e['id']=='n1')
        self.assertNotIn('flag',n1)
        self.assertEqual(n1['flag_kept']['kind'],'command')
        edited=self.host.dispatch('knowledge_note_edit',{**page,'entryId':'n2','text':'The web tests need Node 20 or later.'})
        new=next(e for e in edited['entries'] if e['id']==edited['edited']['id'])
        self.assertEqual((new['text'],new['previous'],new['supersedes'],new['edited_by']),('The web tests need Node 20 or later.','The web tests need Node 20.','n2',ACTOR))
        self.assertNotIn('n2',[e['id'] for e in edited['entries']])
        undone=self.host.dispatch('knowledge_note_restore',{**page,'entryId':'n2'})
        self.assertEqual(sorted(e['id'] for e in undone['entries']),['n1','n2','n3'],'Undo brings the old words back and takes the edit out')
        self.assertEqual(self.host.dispatch('knowledge_note_pair_keep',{**page,'entryIds':['n3','n2']})['lookalikes'],[])
        self.assertGreater(self.host.dispatch('knowledge_notes_seen',page)['seen_at'],0)
        # Notes stay notes: nothing here reaches the rules a run is given.
        b=self.host.store.binding('p','p',actor=ACTOR)
        self.assertEqual(ProjectKnowledge(self.host.store).snapshot(b,ACTOR)['rules'],[])
        # Negative controls: empty or secret text, an unknown note, a malformed pair.
        for text in ('',' ','x'*401,'The key is sk-proj-Q7vL1pR8tZx9fK2m.'):
            with self.assertRaises(Rejected): self.host.dispatch('knowledge_note_edit',{**page,'entryId':'n1','text':text})
        with self.assertRaises(Rejected): self.host.dispatch('knowledge_note_keep',{**page,'entryId':'nope'})
        for ids in (['n1'],['n1','n1'],['n1','nope'],'n1,n2'):
            with self.assertRaises(Rejected): self.host.dispatch('knowledge_note_pair_keep',{**page,'entryIds':ids})
        self.assertNotIn('sk-proj-Q7vL1pR8tZx9fK2m','\n'.join(self.host.store.db.iterdump()))

    def test_rules_and_lessons_follow_the_project_not_the_conversations_account(self):
        """beta.11 (fix-memory, F2): a chat moved to another personal account (Move to Codex, Auto) gets the project's
        rules and can suggest lessons into the queue Knowledge opens. A work account's chat gets none, and the record
        says so."""
        import json
        from project_knowledge import ProjectKnowledge
        knowledge=ProjectKnowledge(self.host.store)
        r=self.draft(); self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)
        self.host.store.add_thread('moved','p','moved','shared')
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET profile_id='other' WHERE id='moved'")
        moved=self.host.store.binding('moved','p',actor=ACTOR)
        snap=knowledge.snapshot(moved,ACTOR)
        self.assertEqual(([x['rule_id'] for x in snap['rules']],snap['profile_id'],snap['not_sent'],snap['omitted']),([r['id']],'p',[],0))
        self.assertTrue(self.call('lessons_set',enabled=True)['enabled'])
        self.assertTrue(self.host.dispatch('knowledge_list',dict(projectId='p',channelId='',profileId='p'))['lesson_suggestions_enabled'])
        task=dict(id='t-moved',origin='conversation',state='working',context=dict(project_id='p',thread_id='moved',actor=ACTOR),binding=moved,runs=[dict(id='run',state='running')])
        self.assertTrue(knowledge.lessons.offered(task,moved))
        self.host.store.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        lesson=knowledge.lessons.capture(task,dict(lesson='The frontend tests pass when run one file at a time.',evidence='The whole suite hung in jsdom.'))
        # beta.16 (b16/memory): a lesson saves at once as the project's note, not as a queued rule.
        self.assertEqual((task['runs'][-1]['remembered'][-1]['action'],task['runs'][-1]['remembered'][-1]['kind']),('add','lesson'))
        self.assertTrue(lesson['saved'])
        notes=self.host.dispatch('knowledge_notes_list',dict(projectId='p',channelId='',profileId='p'))['entries']
        self.assertEqual([(n['text'],n['kind'],n['evidence'],n['source']['thread_id']) for n in notes],
                         [('The frontend tests pass when run one file at a time.','lesson','The whole suite hung in jsdom.','moved')])
        self.assertEqual(self.host.dispatch('knowledge_list',dict(projectId='p',channelId='',profileId='p'))['suggestions']['items'],[],
                         'nothing waits for a click')
        self.assertEqual([x['rule_id'] for x in knowledge.snapshot(moved,ACTOR)['rules']],[r['id']],'and it is not a rule')
        # Negative controls: a work account's chat gets no rules, with the reason on record, and no lesson tool.
        self.host.store.add_profile('w','codex',self.root/'w','w@example.test','work')
        self.host.store.add_thread('work','p','work','shared')
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET profile_id='w' WHERE id='work'")
        work=self.host.store.binding('work','p',actor=ACTOR)
        snap=knowledge.snapshot(work,ACTOR)
        self.assertEqual((snap['rules'],[x['reason'] for x in snap['not_sent']],snap['omitted']),([],['work_account'],1))
        self.assertFalse(knowledge.lessons.offered(dict(task,context=dict(project_id='p',thread_id='work',actor=ACTOR),binding=work),work))
        # And another project's rules never cross over.
        other=self.host.store.binding('other','other',actor=ACTOR)
        self.assertEqual(knowledge.snapshot(other,ACTOR)['rules'],[])

    def test_a_rule_outlives_its_source_chat_and_says_when_it_is_not_sent(self):
        """beta.11 (fix-memory, F3): deleting the chat a rule came from used to stop the rule quietly while Knowledge
        still showed it in use. Now the rule still goes out, Knowledge says the chat is gone, and a source that is
        private now is named in the run record and on the page."""
        from project_knowledge import ProjectKnowledge, SOURCE_DELETED
        knowledge=ProjectKnowledge(self.host.store)
        page=dict(projectId='p',channelId='',profileId='p')
        self.host.store.add_thread('source','p','source','shared')
        mid=self.host.store.send('source','p','msg','Shared source',actor=ACTOR)
        self.host.dispatch('cancel',dict(projectId='p',channelId='source',messageId=mid))
        r=self.call('save',ruleId=None,expectedRevision=0,text='Rule',provenance='Human',source={'channelId':'source','messageId':mid})
        v=r['versions'][0]['id']
        self.call('approve',ruleId=r['id'],versionId=v,expectedRevision=1)
        self.assertEqual(self.host.dispatch('knowledge_list',page)['deliveries'],{r['id']:dict(sent=True,reason=None)})
        binding=self.host.store.binding('p','p',actor=ACTOR)
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='source'",(ACTOR,))
        self.assertEqual(self.host.dispatch('knowledge_list',page)['deliveries'][r['id']],dict(sent=False,reason='source_private'))
        snap=knowledge.snapshot(binding,ACTOR)
        self.assertEqual((snap['rules'],snap['not_sent'],snap['omitted']),([],[dict(rule_id=r['id'],version_id=v,reason='source_private')],1))
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET visibility='shared',owner=NULL WHERE id='source'")
        self.host.dispatch('conversation_delete',dict(projectId='p',channelId='source',confirmation='DELETE source'))
        snap=knowledge.snapshot(binding,ACTOR)
        self.assertEqual(([x['rule_id'] for x in snap['rules']],snap['not_sent']),([r['id']],[]))
        self.assertEqual(self.host.dispatch('knowledge_list',page)['deliveries'][r['id']],dict(sent=True,reason=SOURCE_DELETED))
        with self.assertRaises(Rejected): self.call('save',ruleId=None,expectedRevision=0,text='Late',provenance='Human',source={'channelId':'source','messageId':mid})
        # Keeping it without its source is an ordinary edit, and the rule is plainly sent again.
        r=self.call('save',ruleId=r['id'],expectedRevision=2,text='Rule',provenance='Human',source=None)
        self.call('approve',ruleId=r['id'],versionId=r['versions'][-1]['id'],expectedRevision=3)
        self.assertEqual(self.host.dispatch('knowledge_list',page)['deliveries'][r['id']],dict(sent=True,reason=None))
        # The limit is named per rule too, and a rule not in use has no delivery entry.
        draft=self.draft()
        for i in range(13):
            x=self.call('save',ruleId=None,expectedRevision=0,text=f'Rule number {i}',provenance='Human',source=None)
            self.call('approve',ruleId=x['id'],versionId=x['versions'][0]['id'],expectedRevision=1)
        snap=knowledge.snapshot(binding,ACTOR)
        self.assertEqual((len(snap['rules']),[x['reason'] for x in snap['not_sent']],snap['omitted']),(12,['limit','limit'],2))
        self.assertNotIn(draft['id'],self.host.dispatch('knowledge_list',page)['deliveries'])

    def test_stale_renderer_revision_and_immutable_history(self):
        r=self.draft(); stale=self.call('list')['rules'][0]
        self.call('approve',ruleId=r['id'],versionId=r['versions'][0]['id'],expectedRevision=1)
        with self.assertRaises(Rejected): self.call('archive',ruleId=r['id'],expectedRevision=stale['revision'])
        self.assertEqual(self.call('list')['rules'][0]['versions'],r['versions'])

if __name__=='__main__': unittest.main()
