"""Actual CodingTasks callback through both adapters and fake fresh provider processes."""
import json
from test_conversation_coding import ConversationCodingTests

class DeliveryMixin:
    def test_task_lesson_is_linked_unapproved_then_delivered_in_a_fresh_session(self):
        self.host.store.add_thread('lesson-shared','p','lesson-shared','shared')
        self.scope={**self.scope,'channelId':'lesson-shared'}
        scope={**self.scope,'profileId':'p'}
        task=self.host.dispatch('task_create',{**self.scope,'text':'Inspect search query storage.','files':['test_sample.py'],'testFile':'test_sample.py'})
        with self.peer_patch('none'):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            original=self.settle(task['id'])['task']
            source=dict(channelId=self.scope['channelId'],taskId=task['id'],runId=original['runs'][-1]['id'])
            lesson=self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,
                'text':'Update visible search state before sessionStorage and catch storage exceptions.',
                'provenance':'Lesson drafted after the saved search inspection.','source':source})
            self.assertIsNone(lesson['active_version_id'])
            self.host.dispatch('knowledge_approve',{**scope,'ruleId':lesson['id'],'versionId':lesson['versions'][0]['id'],'expectedRevision':1})
            next_task=self.host.dispatch('task_create',{**self.scope,'text':'Improve sessionStorage query retention.','files':['test_sample.py'],'testFile':'test_sample.py'})
            self.host.dispatch('task_approve',{**self.scope,'taskId':next_task['id'],'scopeVersion':1})
            fresh=self.settle(next_task['id'])['task']
            if self.provider=='codex':
                # The fake Codex peer returns a constant ID; verify the actual start protocol.
                methods=[m.get('method') for m in self.peers[-1].sent]
                self.assertIn('thread/start',methods)
                self.assertNotIn('thread/resume',methods)
            else:
                self.assertNotEqual(fresh['session_id'],original['session_id'])
            delivered=fresh['runs'][-1]['knowledge_snapshot']['rules'][0]
            self.assertEqual(delivered['source'],source)
            self.assertIn('catch storage exceptions',delivered['text'])
            self.assertIn(source['runId'],json.dumps(self.peers[-1].sent))

    def test_request_relevance_reaches_each_provider_and_changes_on_followup(self):
        scope={**self.scope,'profileId':'p'}
        def approved(text):
            r=self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,'text':text,'provenance':'Project decision','source':None})
            self.host.dispatch('knowledge_approve',{**scope,'ruleId':r['id'],'versionId':r['versions'][0]['id'],'expectedRevision':1})
            return r['id']
        invoice=approved('Invoice exports use the approved accounting template.')
        for i in range(13): approved(f'Preserve the documented typography convention number {i}.')
        search=approved('Search query retention must handle sessionStorage exceptions after updating visible state.')
        task=self.host.dispatch('task_create',{**self.scope,'text':'Inspect search query retention and sessionStorage exceptions.','files':['test_sample.py'],'testFile':'test_sample.py'})
        with self.peer_patch('none'):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            first=self.settle(task['id'])['task']['runs'][-1]['knowledge_snapshot']
            self.assertEqual(first['rules'][0]['rule_id'],search)
            self.assertIn(search,json.dumps(self.peers[-1].sent))
            self.host.dispatch('task_continue',{**self.scope,'taskId':task['id'],'text':'Now inspect invoice exports against the accounting template.'})
            second=self.settle(task['id'])['task']['runs'][-1]['knowledge_snapshot']
            self.assertEqual(second['rules'][0]['rule_id'],invoice)
            self.assertNotIn(search,[r['rule_id'] for r in second['rules']])
            self.assertIn(search,[r['rule_id'] for r in second['retired_versions']])
            self.assertIn(invoice,json.dumps(self.peers[-1].sent))
            self.assertEqual(self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})['task']['runs'][0]['knowledge_snapshot'],first)

    def test_resumed_replacement_archive_and_rollback(self):
        scope={**self.scope,'profileId':'p'}
        self.host.store.add_thread('shared','p','shared','shared')
        source_id=self.host.store.send('shared','p','source','Shared evidence',actor='local-user')
        source={'channelId':'shared','messageId':source_id}
        r=self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,'text':'RULE_A','provenance':'Human','source':source})
        a=r['versions'][0]['id']
        r=self.host.dispatch('knowledge_approve',{**scope,'ruleId':r['id'],'versionId':a,'expectedRevision':r['revision']})
        task=self.host.dispatch('task_create',{**self.scope,'text':'Inspect','files':['test_sample.py'],'testFile':'test_sample.py'})
        with self.peer_patch('none'):
            self.host.dispatch('task_approve',{**self.scope,'taskId':task['id'],'scopeVersion':1})
            detail=self.settle(task['id'])
            original=detail['task']['runs'][0]['knowledge_snapshot']
            sid=detail['task']['session_id']
            r=self.host.dispatch('knowledge_save',{**scope,'ruleId':r['id'],'expectedRevision':r['revision'],'text':'RULE_B','provenance':'Human correction','source':source})
            b=r['versions'][-1]['id']
            for operation,version,retired in [('knowledge_approve',b,a),('knowledge_approve',a,b),('knowledge_archive',None,a)]:
                with self.subTest(operation=operation, version=version):
                    args={**scope,'ruleId':r['id'],'expectedRevision':r['revision']}
                    if version: args['versionId']=version
                    r=self.host.dispatch(operation,args)
                    self.host.dispatch('task_continue',{**self.scope,'taskId':task['id'],'text':'Continue inspection'})
                    detail=self.settle(task['id'])
                    snap=detail['task']['runs'][-1]['knowledge_snapshot']
                    self.assertEqual(snap['semantics'],'replace_project_account_rules')
                    self.assertEqual(snap['project_id'],'p'); self.assertEqual(snap['profile_id'],'p')
                    self.assertEqual([x['version_id'] for x in snap['rules']],[version] if version else [])
                    self.assertIn({'rule_id':r['id'],'version_id':retired,'source':source},snap['retired_versions'])
                    self.assertIn('Retire',snap['text'])
                    def strings(value):
                        if isinstance(value,str): yield value
                        elif isinstance(value,dict):
                            for v in value.values(): yield from strings(v)
                        elif isinstance(value,list):
                            for v in value: yield from strings(v)
                    self.assertTrue(any(snap['text'] in x for x in strings(self.peers[-1].sent)))
                    self.assertEqual(detail['task']['session_id'],sid)
                    if self.provider=='codex':
                        self.assertTrue(any(m.get('method')=='thread/resume' for m in self.peers[-1].sent))
                    else: self.assertIn('--resume='+sid,self.peers[-1].argv)
                    self.assertEqual(detail['task']['runs'][0]['knowledge_snapshot'],original)
                    self.assertNotIn('knowledge_snapshot',json.dumps(self.host.dispatch('tasks',self.scope)))
            self.reopen()
            self.assertEqual(self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})['task']['runs'][-1]['knowledge_snapshot'],snap)

    def test_fresh_knowledge_snapshot(self):
        scope={**self.scope,'profileId':'p'}
        def save(text): return self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,'text':text,'provenance':'Human original','source':None})
        self.host.store.add_thread('shared','p','shared','shared')
        source_id=self.host.store.send('shared','p','source','Shared source evidence',actor='local-user')
        r=self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,'text':'RULE_APPROVED_SENTINEL','provenance':'Human original','source':{'channelId':'shared','messageId':source_id}}); v=r['versions'][0]['id']
        self.host.dispatch('knowledge_approve',{**scope,'ruleId':r['id'],'versionId':v,'expectedRevision':1})
        save('PROPOSED_SENTINEL')
        self.host.dispatch('knowledge_save',{**scope,'ruleId':r['id'],'expectedRevision':2,'text':'PROPOSED_EDIT_SENTINEL','provenance':'Human edit','source':None})
        archived=save('ARCHIVED_SENTINEL')
        self.host.dispatch('knowledge_approve',{**scope,'ruleId':archived['id'],'versionId':archived['versions'][0]['id'],'expectedRevision':1})
        self.host.dispatch('knowledge_archive',{**scope,'ruleId':archived['id'],'expectedRevision':2})
        self.host.store.add_profile('other','codex',self.base/'store/profiles/other','other@example.test','personal')
        self.host.store.add_project('other',self.project,'other','synthetic-model')
        self.host.store.add_thread('other','other','other','shared')
        other_scope=dict(projectId='other',channelId='other',profileId='other')
        other=self.host.dispatch('knowledge_save',{**other_scope,'ruleId':None,'expectedRevision':0,'text':'OTHER_ACCOUNT_SENTINEL','provenance':'Human','source':None})
        self.host.dispatch('knowledge_approve',{**other_scope,'ruleId':other['id'],'versionId':other['versions'][0]['id'],'expectedRevision':1})
        self.host.store.add_thread('other-in-project','p','other-in-project','shared')
        with self.host.store.db: self.host.store.db.execute("UPDATE threads SET profile_id='other' WHERE id='other-in-project'")
        same_project=dict(projectId='p',channelId='other-in-project',profileId='other')
        separate=self.host.dispatch('knowledge_save',{**same_project,'ruleId':None,'expectedRevision':0,'text':'SAME_PROJECT_OTHER_ACCOUNT','provenance':'Human','source':None})
        self.host.dispatch('knowledge_approve',{**same_project,'ruleId':separate['id'],'versionId':separate['versions'][0]['id'],'expectedRevision':1})
        self.grant()
        with self.peer_patch('none'):
            task={'id':self.request('Inspect the greeting.')['taskId']}
            detail=self.settle(task['id'])
        run=detail['task']['runs'][-1]
        snap=run['knowledge_snapshot']
        self.assertIn(v,snap['text']); self.assertIn('RULE_APPROVED_SENTINEL',snap['text'])
        self.assertIn('Human original',snap['text']); self.assertNotIn('PROPOSED_SENTINEL',snap['text'])
        self.assertIn('permissions',snap['text'])
        def strings(value):
            if isinstance(value,str): yield value
            elif isinstance(value,dict):
                for item in value.values(): yield from strings(item)
            elif isinstance(value,list):
                for item in value: yield from strings(item)
        self.assertTrue(any(snap['text'] in text for text in strings(self.peers[0].sent)), 'Exact saved guidance must occur in the actual provider payload')
        delivered=next(text for text in strings(self.peers[0].sent) if snap['text'] in text)
        self.assertTrue(delivered.startswith('Dervo saved project guidance for this request (approved by the user):\n'))
        self.assertLess(delivered.index(snap['text']), delivered.index('Inspect the greeting.'))
        self.assertIn('the user\'s own message is the Request at the end', delivered)
        sent=json.dumps(self.peers[0].sent)
        self.assertIn(v,sent); self.assertIn('RULE_APPROVED_SENTINEL',sent); self.assertIn(source_id,sent)
        for excluded in ('PROPOSED_SENTINEL','PROPOSED_EDIT_SENTINEL','ARCHIVED_SENTINEL','OTHER_ACCOUNT_SENTINEL','SAME_PROJECT_OTHER_ACCOUNT'):
            self.assertNotIn(excluded,sent)
        if self.provider=='codex':
            start=next(m for m in self.peers[0].sent if m.get('method')=='thread/start')
            self.assertEqual(start['params']['sandbox'],'read-only')
            self.assertEqual(start['params']['approvalPolicy'],'on-request')
            self.assertEqual({t['name'] for t in start['params']['dynamicTools']},{'dervo_ask_user','dervo_remind_user','dervo_read_file','dervo_write_file','dervo_edit_file','dervo_run_tests','dervo_update_task_guide','dervo_remember','dervo_search_history','dervo_propose_thread','dervo_check_chat','dervo_leave_note'})
        else:
            from claude_policy import TOOLS
            self.assertEqual(tuple(TOOLS),('dervo_read_file','dervo_write_file','dervo_edit_file','dervo_run_tests'))
            argv=self.peers[0].argv
            self.assertIn('--restricted',argv)
            self.assertEqual(argv[argv.index('--tools')+1],'WebSearch')
            self.assertEqual(set(argv[argv.index('--allowedTools')+1].split(',')),{'WebSearch','mcp__dervo__dervo_ask_user','mcp__dervo__dervo_remind_user','mcp__dervo__dervo_read_file','mcp__dervo__dervo_write_file','mcp__dervo__dervo_edit_file','mcp__dervo__dervo_run_tests','mcp__dervo__dervo_update_task_guide','mcp__dervo__dervo_remember','mcp__dervo__dervo_search_history','mcp__dervo__dervo_propose_thread','mcp__dervo__dervo_check_chat','mcp__dervo__dervo_leave_note'})
            self.assertTrue(any(a.startswith('--session-id=') for a in argv))
            self.assertFalse(any(a.startswith('--resume=') for a in argv))
            self.assertNotIn('bypassPermissions',sent)
        self.assertEqual(len(detail['task']['runs']),1)
        self.host.dispatch('knowledge_archive',{**scope,'ruleId':r['id'],'expectedRevision':3})
        self.reopen()
        self.assertEqual(self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})['task']['runs'][-1]['knowledge_snapshot'],snap)

    def test_setup_facts_are_asked_for_in_the_request_and_the_tool_and_not_while_memory_is_off(self):
        # Eval m1: the agent saw pnpm-lock.yaml and saved nothing. The request itself asks for one memory step that
        # names setup facts, and the tool says what a durable fact is. Negative control: memory off, neither is sent.
        import project_memory
        from codex_coding import dynamic_tools
        remember=next(t for t in dynamic_tools(('dervo_remember',)) if t['name']=='dervo_remember')
        self.assertIn('package manager',remember['description'])
        self.assertIn('Not task progress',remember['description'])
        self.grant()
        self.run_request('Add left-pad to package.json', mode='none', client_id='on')
        sent=json.dumps(self.peers[-1].sent)
        for words in ('one memory step','package manager',"instructions aren't facts",'small talk'):
            self.assertIn(words,sent)
        project_memory.set_enabled(self.host.store.db,'p',False,'owner')
        self.run_request('Add right-pad to package.json', mode='none', client_id='off')
        sent=json.dumps(self.peers[-1].sent)
        self.assertNotIn('one memory step',sent)
        self.assertNotIn('dervo_remember',sent)

class CodexDelivery(DeliveryMixin,ConversationCodingTests): pass
class ClaudeDelivery(DeliveryMixin,ConversationCodingTests): provider='claude'


def load_tests(loader, tests, pattern):
    # Reuse the disposable provider fixtures without repeating their unrelated inherited tests.
    import unittest
    return unittest.TestSuite(cls(name) for cls in (CodexDelivery,ClaudeDelivery) for name in ('test_fresh_knowledge_snapshot','test_resumed_replacement_archive_and_rollback','test_request_relevance_reaches_each_provider_and_changes_on_followup','test_task_lesson_is_linked_unapproved_then_delivered_in_a_fresh_session','test_setup_facts_are_asked_for_in_the_request_and_the_tool_and_not_while_memory_is_off'))
