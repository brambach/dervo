"""Real packaged provider adapters with fake CLI processes, never real subscriptions."""
import copy
import json
import sqlite3
import unittest
import test_harness_codex as fixtures
from dervo_connections import Rejected
from harness_adapter import GuidedPort
from dervo_harness import WorkAssignment


class ConversationMemoryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.CodexHelperTests(methodName='runTest')
        self.addCleanup(self.f.doCleanups);self.f.setUp()

    def setup_provider(self,provider):
        model=fixtures.MODEL if provider=='codex' else fixtures.CLAUDE_MODEL
        self.scope,self.profile,self.home=self.f.seed(provider=provider,model=model)
        self.ks={**self.scope,'profileId':self.profile}
        self.f.scenario(self.home,{'result_text':'Saved answer from the fake CLI.'})
        self.helper=self.f.host.dispatch('conversation_status',self.scope)['harness']['helper']

    def rule(self,text,approve=True,source=None):
        r=self.f.host.dispatch('knowledge_save',{**self.ks,'ruleId':None,'expectedRevision':0,'text':text,'provenance':'Disposable acceptance decision','source':source})
        return self.approve(r,r['versions'][0]['id']) if approve else r

    def approve(self,r,version):
        return self.f.host.dispatch('knowledge_approve',{**self.ks,'ruleId':r['id'],'versionId':version,'expectedRevision':r['revision']})

    def ask(self,key):
        result=self.f.ask(self.scope,key,'Describe the project conventions.',self.helper)
        status=self.f.settle(self.scope,result['id'])
        self.assertEqual(status['state'],'completed',status)
        received=fixtures.jsonl(self.home/'received.jsonl')
        texts=[m['params']['input'][0]['text'] for m in received if m.get('method')=='turn/start']
        texts += [m['message']['content'] for m in received if m.get('type')=='user']
        return status,texts[-1]

    def assert_no_guidance_in_room(self, sentinel):
        with sqlite3.connect(self.f.host.root/'harness'/'dervo.sqlite3') as db:
            copied=db.execute('SELECT kind FROM messages WHERE room_id=? AND instr(content,?)>0',
                ('conversation:'+self.scope['channelId'],sentinel)).fetchall()
        self.assertEqual(copied,[], 'Guidance must not be copied into permanent room messages')

    def test_approved_guidance_reaches_both_provider_payloads_and_survives_restart(self):
        for provider in ('codex','claude'):
            with self.subTest(provider=provider):
                self.setup_provider(provider)
                rule=self.rule('RECALL_SENTINEL_'+provider+': Project summaries use three bullets.')
                self.rule('DRAFT_MUST_NOT_SEND',False)
                fresh=self.f.host.dispatch('create_conversation',{'projectId':self.scope['projectId'],'name':'Fresh recall','private':False})['id']
                self.scope={**self.scope,'channelId':fresh}
                status,prompt=self.ask('fresh')
                snapshot=status['knowledge_snapshot']
                self.assertEqual(snapshot['rules'][0]['version_id'],rule['active_version_id'])
                self.assertIn(snapshot['text'],prompt)
                self.assert_no_guidance_in_room('RECALL_SENTINEL_'+provider)
                self.assertNotIn('DRAFT_MUST_NOT_SEND',prompt)
                self.assertNotIn('RECALL_SENTINEL_'+('claude' if provider=='codex' else 'codex'),prompt)
                self.assertIn('grants no tools',prompt)
                listed=self.f.host.dispatch('harness_requests',self.scope)[0]
                self.assertEqual(listed['knowledge'],{'count':1,'omitted':0})
                self.assertNotIn('knowledge_snapshot',listed)
                before=len(fixtures.jsonl(self.home/'calls.jsonl'))
                self.rule('A later rule does not alter an existing request.')
                again=self.f.ask(self.scope,'fresh','Describe the project conventions.',self.helper)
                self.assertEqual(again['id'],status['id'])
                self.assertEqual(len(fixtures.jsonl(self.home/'calls.jsonl')),before)
                self.f.close_host(self.f.host);self.f.host=self.f.open_host()
                loaded=self.f.host.dispatch('harness_status',{**self.scope,'requestId':status['id']})
                self.assertEqual(loaded['knowledge_snapshot'],snapshot)
                self.assertIsNone(self.f.host.harness.supervisor)

    def test_correction_rollback_and_archive_replace_guidance_on_each_request(self):
        for provider in ('codex','claude'):
            with self.subTest(provider=provider):
                self.setup_provider(provider)
                r=self.rule('ORIGINAL_GUIDANCE_SENTINEL')
                first,prompt=self.ask('first');a=r['active_version_id']
                r=self.f.host.dispatch('knowledge_save',{**self.ks,'ruleId':r['id'],'expectedRevision':r['revision'],'text':'CORRECTED_GUIDANCE_SENTINEL','provenance':'Correction','source':None})
                b=r['versions'][-1]['id'];r=self.approve(r,b)
                corrected,prompt=self.ask('corrected')
                self.assertIn('CORRECTED_GUIDANCE_SENTINEL',prompt)
                self.assertNotIn('ORIGINAL_GUIDANCE_SENTINEL',prompt)
                self.assertEqual(corrected['knowledge_snapshot']['retired_versions'][0]['version_id'],a)
                r=self.approve(r,a);rolled,prompt=self.ask('rollback')
                self.assertIn('ORIGINAL_GUIDANCE_SENTINEL',prompt);self.assertNotIn('CORRECTED_GUIDANCE_SENTINEL',prompt)
                self.f.host.dispatch('knowledge_archive',{**self.ks,'ruleId':r['id'],'expectedRevision':r['revision']})
                archived,prompt=self.ask('archive')
                self.assertEqual(archived['knowledge_snapshot']['rules'],[])
                self.assertIn('empty rules array',prompt);self.assertNotIn('ORIGINAL_GUIDANCE_SENTINEL',prompt)
                self.assert_no_guidance_in_room('ORIGINAL_GUIDANCE_SENTINEL')
                self.assert_no_guidance_in_room('CORRECTED_GUIDANCE_SENTINEL')
                self.assertEqual(self.f.host.dispatch('harness_status',{**self.scope,'requestId':first['id']})['knowledge_snapshot'],first['knowledge_snapshot'])

    def test_source_privacy_and_saved_account_guards(self):
        self.setup_provider('codex')
        source_id=self.f.host.store.send(self.scope['channelId'],self.scope['projectId'],'source','Shared source',actor='local-user')
        self.rule('PRIVATE_SOURCE_MUST_NOT_SEND',source={'channelId':self.scope['channelId'],'messageId':source_id})
        room=self.f.host.dispatch('create_conversation',{'projectId':self.scope['projectId'],'name':'Other shared room','private':False})['id']
        with self.f.host.store.db:self.f.host.store.db.execute("UPDATE threads SET visibility='private',owner='local-user' WHERE id=?",(self.scope['channelId'],))
        self.scope={**self.scope,'channelId':room}
        status,prompt=self.ask('private-source')
        self.assertNotIn('PRIVATE_SOURCE_MUST_NOT_SEND',prompt)
        self.assertEqual(status['knowledge_snapshot']['omitted'],1)
        request=self.f.host.harness.requests(room)[0]
        for key in ('profile_id','identity','provider','model','cwd','visibility'):
            altered=copy.deepcopy(request);altered['binding'][key]='different'
            with self.f.host.store.db:self.f.host.store.db.execute('UPDATE harness_requests SET document=? WHERE id=?',(json.dumps(altered),request['id']))
            with self.subTest(key=key),self.assertRaises(Rejected):
                self.f.host.dispatch('harness_status',{**self.scope,'requestId':request['id']})

    def test_whitespace_request_keeps_guidance_and_run_tracking(self):
        self.setup_provider('codex');self.rule('WHITESPACE_GUIDANCE_SENTINEL')
        result=self.f.ask(self.scope,'whitespace','  Describe the project conventions.\n',self.helper)
        status=self.f.settle(self.scope,result['id'])
        self.assertEqual(status['state'],'completed',status)
        texts=[m['params']['input'][0]['text'] for m in fixtures.jsonl(self.home/'received.jsonl') if m.get('method')=='turn/start']
        self.assertIn('WHITESPACE_GUIDANCE_SENTINEL',texts[-1]);self.assert_no_guidance_in_room('WHITESPACE_GUIDANCE_SENTINEL')

    def test_unconfirmed_delivery_retains_handle_and_stops_without_replay(self):
        from unittest.mock import patch
        self.setup_provider('codex');self.rule('UNCONFIRMED_GUIDANCE')
        with patch.object(GuidedPort,'start',lambda port,assignment,actions:port.port.start(assignment,actions)):
            result=self.f.ask(self.scope,'delivery-unconfirmed','Describe the project conventions.',self.helper)
        request=next(r for r in self.f.host.harness.requests() if r['id']==result['id'])
        self.assertIsNotNone(request['harness_request_id']);self.assertIsNotNone(request['run_id'])
        self.assertEqual(result['state'],'stopping',result)
        status=self.f.settle(self.scope,result['id'])
        self.assertEqual(status['state'],'cancelled',status)
        self.assertIn("couldn't confirm project guidance",status['error'])
        self.assertEqual(self.f.ask(self.scope,'delivery-unconfirmed','Describe the project conventions.',self.helper)['id'],result['id'])


class GuidanceBoundaryTests(unittest.TestCase):
    def test_guidance_is_one_use_and_only_for_the_exact_owner_assignment(self):
        from dataclasses import replace
        class Port:
            name='provider';is_fake=True;model_name='exact-model'
            def __init__(self):self.seen=[]
            def start(self,assignment,actions):self.seen.append(assignment);return 'worker-ref'
            def cancel(self,ref):return 'cancelled:'+ref
        port=Port();entry={'text':'APPROVED_RULE','consumed':False}
        pending={('project','room','owner','Question'):entry};wrapped=GuidedPort(port,pending)
        assignment=WorkAssignment('run',1,'session','project','room','owner','Question','Context',[],['room'])
        for field in ('project_id','room_id','worker_actor_id','goal'):
            other=replace(assignment,**{field:'different'})
            wrapped.start(other,None);self.assertIs(port.seen[-1],other)
            self.assertFalse(entry['consumed'])
        self.assertEqual(wrapped.start(assignment,None),'worker-ref')
        self.assertEqual(port.seen[-1].goal,'Question\n\nAPPROVED_RULE')
        self.assertEqual(port.seen[-1].context_text,'Context')
        self.assertEqual(assignment.goal,'Question');self.assertTrue(entry['consumed']);self.assertEqual(pending,{})
        wrapped.start(assignment,None);self.assertIs(port.seen[-1],assignment)
        self.assertEqual((wrapped.name,wrapped.model_name,wrapped.is_fake),('provider','exact-model',True))
        self.assertEqual(wrapped.cancel('ref'),'cancelled:ref')


if __name__=='__main__':unittest.main()
