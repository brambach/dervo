"""Synthetic protocol peers, never live model evidence."""
import unittest
from adapters import CodexAdapter
from dervo_connections import Rejected
from codex_policy import POLICY, for_folder

BINDING = dict(provider='codex', identity='synthetic@example.test', category='personal',
               home='/private/tmp/synthetic-home', cwd='/private/tmp/synthetic-project', model='synthetic-model', session_id=None)
LAUNCHED=for_folder(POLICY,BINDING['cwd'])
CONFIG={'config':LAUNCHED,'layers':[{'name':{'type':'sessionFlags'},'config':LAUNCHED}]}

INIT={'userAgent':'dervo/0.156.1 (synthetic peer)','codexHome':BINDING['home']}

class PreflightTests(unittest.TestCase):
    def test_identity_then_catalog_before_any_thread(self):
        sent, events = [], []
        adapter = CodexAdapter(BINDING, sent.append, lambda k,d: events.append((k,d)))
        adapter.connect()
        adapter.receive({'id':sent[-1]['id'], 'result':INIT})
        adapter.receive({'id':sent[-1]['id'], 'result':CONFIG})
        adapter.receive({'id':sent[-1]['id'], 'result':{'account':{'type':'chatgpt','email':BINDING['identity'],'planType':'plus'}}})
        self.assertEqual(sent[-1]['method'], 'model/list')
        adapter.receive({'id':sent[-1]['id'], 'result':{'data':[{'model':'wrong','hidden':False}], 'nextCursor':None}})
        self.assertNotIn('thread/start', [m.get('method') for m in sent])
        self.assertEqual(adapter.state, 'blocked')

class ConfigTests(unittest.TestCase):
    def test_provider_generated_system_skills_do_not_block_second_preflight(self):
        from codex_policy import check_profile
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'skills'/'.system').mkdir(parents=True)
            check_profile(root)
            (Path(root)/'skills'/'custom').mkdir()
            with self.assertRaises(Rejected):check_profile(root)

    def test_catalog_checks_effective_project_config_before_thread(self):
        sent=[]
        a=CodexAdapter(BINDING,sent.append,lambda *_:None)
        a.connect()
        a.receive({'id':sent[-1]['id'],'result':INIT})
        self.assertEqual(sent[-1]['method'],'config/read')
        self.assertEqual(sent[-1]['params']['cwd'],BINDING['cwd'])
        with self.assertRaises(Rejected):
            a.receive({'id':sent[-1]['id'],'result':{'config':{},'layers':[{'name':{'type':'project'},'config':{'hooks':{'x':'bad'}}}]}})
        self.assertNotIn('thread/start',[m.get('method') for m in sent])

class LifecycleTests(unittest.TestCase):
    def ready(self, session_id=None, result_patch=None):
        from codex_policy import POLICY, SANDBOX
        sent,events=[],[]
        a=CodexAdapter({**BINDING,'session_id':session_id},sent.append,lambda k,d:events.append((k,d)))
        a.connect()
        for result in [INIT, CONFIG, {'account':{'type':'chatgpt','email':BINDING['identity'],'planType':'plus'}},
                       {'data':[{'model':BINDING['model'],'hidden':False}],'nextCursor':None}]:
            a.receive({'id':sent[-1]['id'],'result':result})
        self.assertEqual(sent[-1]['params']['approvalPolicy'],'on-request')
        self.assertEqual(sent[-1]['method'],'thread/resume' if session_id else 'thread/start')
        a.receive({'id':sent[-1]['id'],'result':{'model':BINDING['model'],'cwd':BINDING['cwd'],'modelProvider':'openai',
            'approvalPolicy':'on-request','approvalsReviewer':'user','sandbox':SANDBOX,'instructionSources':[],
            'thread':{'id':session_id or 'synthetic-thread','turns':[]},**(result_patch or {})}})
        return a,sent,events

    def test_stream_deny_stop_and_resume(self):
        a,sent,events=self.ready()
        a.begin('Synthetic request','synthetic-client')
        a.receive({'id':sent[-1]['id'],'result':{'turn':{'id':'synthetic-turn'}}})
        scope={'threadId':a.session_id,'turnId':a.turn_id}
        a.receive({'method':'item/agentMessage/delta','params':{**scope,'itemId':'answer','delta':'Synthetic'}})
        a.receive({'method':'item/commandExecution/requestApproval','id':100,'params':scope})
        self.assertEqual(sent[-1]['result'],{'decision':'decline'})
        a.stop()
        self.assertEqual(sent[-1]['method'],'turn/interrupt')
        a.receive({'method':'turn/completed','params':{'threadId':a.session_id,'turn':{'id':a.turn_id,'status':'interrupted'}}})
        self.assertEqual(a.state,'interrupted')
        self.assertIn(('delta',{'item_id':'answer','text':'Synthetic'}),events)
        self.ready(a.session_id)

import tempfile
from pathlib import Path
from host import Host, ACTOR

class SyntheticPeer:
    """Explicit in-memory Codex server double. No credentials or network."""
    def __init__(self, argv, *, cwd, env, signed_in=True, finish=True):
        self.argv,self.cwd,self.env=argv,cwd,env
        self.queue,self.sent=[],[]
        self.signed_in,self.finish=signed_in,finish
        self.closed=False
    def send(self,m):
        from codex_policy import POLICY,SANDBOX
        self.sent.append(m)
        method=m.get('method'); p=m.get('params',{})
        if method=='initialize':r={**INIT,'codexHome':self.env['CODEX_HOME']}
        elif method=='initialized':return
        elif method=='account/read':r={'account':{'type':'chatgpt','email':BINDING['identity'],'planType':'plus'} if self.signed_in else None}
        elif method=='model/list':r={'data':[{'model':BINDING['model'],'hidden':False}],'nextCursor':None}
        elif method=='config/read':
            from test_codex_policy import config_read
            r=config_read(self.argv)
        elif method in ('thread/start','thread/resume'):
            self.project=p['cwd']
            r={'model':BINDING['model'],'cwd':p['cwd'],'modelProvider':'openai','approvalPolicy':'on-request',
               'approvalsReviewer':'user','sandbox':SANDBOX,'instructionSources':[],'thread':{'id':'synthetic-thread','turns':[]}}
        elif method=='turn/start':
            r={'turn':{'id':'synthetic-turn','status':'inProgress'}}
            self.queue.append({'id':m['id'],'result':r})
            self.queue.append({'method':'item/agentMessage/delta','params':{'threadId':'synthetic-thread','turnId':'synthetic-turn','itemId':'answer','delta':'Synthetic answer'}})
            if self.finish:self.complete('completed')
            return
        elif method=='turn/interrupt':
            r={};self.complete('interrupted')
        elif method=='thread/read':
            r={'thread':{'id':'synthetic-thread','cwd':self.project,'modelProvider':'openai','turns':[{'id':'synthetic-turn','status':'completed','items':[{'type':'agentMessage','id':'answer','text':'Recovered synthetic answer'}]}]}}
        else:raise AssertionError(method)
        self.queue.append({'id':m['id'],'result':r})
    def complete(self,status):
        self.queue.append({'method':'turn/completed','params':{'threadId':'synthetic-thread','turn':{'id':'synthetic-turn','status':status}}})
    def read(self,timeout):
        if not self.queue:raise TimeoutError()
        return [self.queue.pop(0)]
    def close(self):self.closed=True

class HostLiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.peers=[]
        def factory(*a,**kw):
            peer=SyntheticPeer(*a,**kw);self.peers.append(peer);return peer
        self.host=Host(Path(self.tmp.name)/'data',transport_factory=factory)
        p=self.host.dispatch('add_profile',{'provider':'codex','identity':BINDING['identity'],'category':'personal'})['id']
        project=self.host.add_picked_project(self.tmp.name,p,BINDING['model'])['id']
        channel=self.host.dispatch('create_conversation',{'projectId':project,'name':'Synthetic','private':False})['id']
        self.scope={'projectId':project,'channelId':channel}
    def tearDown(self):self.host.close();self.tmp.cleanup()
    def queue(self,client='synthetic-client'):
        return self.host.dispatch('queue',{**self.scope,'clientId':client,'text':'Synthetic request','parentId':None})['id']
    def settle(self):
        for _ in range(30):self.host.tick()
    def test_explicit_send_stream_persistence_and_no_auto_send(self):
        mid=self.queue()
        self.settle();self.assertEqual(self.peers,[])
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.settle()
        self.assertEqual(self.host.store.turn(mid)['state'],'completed')
        self.assertNotEqual(self.peers[0].cwd,self.tmp.name)
        self.assertEqual(self.peers[0].project,str(Path(self.tmp.name).resolve()))
        history=self.host.dispatch('history',self.scope)
        self.assertEqual(history[-1]['content'],'Synthetic answer')
        self.assertEqual(history[-1]['pubkey'],'provider')
        self.assertEqual(len([m for m in self.peers[0].sent if m.get('method')=='turn/start']),1)
        with self.assertRaises(Rejected):self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        mid2=self.queue('second');self.settle()
        self.assertEqual(self.host.store.turn(mid2)['state'],'queued')
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid2});self.settle()
        self.assertIn('thread/resume',[m.get('method') for m in self.peers[-1].sent])

    def test_stream_is_visible_before_terminal_and_stop_is_confirmed(self):
        mid=self.queue()
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.peers[0].finish=False
        self.settle()
        self.assertEqual(self.host.store.turn(mid)['state'],'running')
        rows=self.host.dispatch('history',self.scope)
        self.assertEqual(rows[-1]['content'],'Synthetic answer')
        self.assertGreater(rows[-1]['created_at'],0)
        self.host.dispatch('stop_turn',{**self.scope,'messageId':mid})
        self.settle()
        self.assertEqual(self.host.store.turn(mid)['state'],'interrupted')
        after=self.host.dispatch('history',self.scope)
        self.assertEqual(after[-1]['id'],rows[-1]['id'])

    def test_signed_out_preserves_queue_and_never_sends_after_check(self):
        mid=self.queue()
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.peers[0].signed_in=False
        self.settle()
        self.assertEqual(self.host.store.turn(mid)['state'],'queued')
        self.assertNotIn('model/list',[m.get('method') for m in self.peers[0].sent])
        self.assertTrue(self.host.dispatch('turn_status',{**self.scope,'messageId':mid})['error'])
        self.peers[0].signed_in=True
        self.settle()
        self.assertNotIn('turn/start',[m.get('method') for m in self.peers[0].sent])

    def test_protocol_runner_uses_the_same_executable_codex_path(self):
        from runner import ProtocolRunner
        mid=self.queue()
        events=[]
        result=ProtocolRunner(self.host.store,transport_factory=self.host.transport_factory).run(
            mid,self.scope['channelId'],self.scope['projectId'],actor=ACTOR,on_event=lambda k,d:events.append(k))
        self.assertEqual(result['state'],'completed')
        self.assertIn('delta',events)
        self.assertIn('terminal',events)

    def test_failed_initialize_write_closes_owned_child(self):
        class BrokenPeer(SyntheticPeer):
            def send(self,message):raise BrokenPipeError('Synthetic initialization write failed')
        peer=None
        def factory(*a,**kw):
            nonlocal peer
            peer=BrokenPeer(*a,**kw);return peer
        self.host.transport_factory=factory
        mid=self.queue()
        with self.assertRaises(BrokenPipeError):self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.assertTrue(peer.closed)
        self.assertEqual(self.host.store.turn(mid)['state'],'queued')

    def test_real_stdio_transport_and_restart_with_explicit_protocol_double(self):
        import sys,time
        from transport import OwnedProcess
        processes=[]
        def factory(argv,*,cwd,env):
            from codex_policy import EXECUTABLE
            self.assertEqual(argv[0],EXECUTABLE)
            p=OwnedProcess([sys.executable,str(Path(__file__).resolve().parents[1]/'scripts'/'codex-protocol-peer.py'),*argv[1:]],cwd=cwd,env=env)
            processes.append(p)
            return p
        self.host.transport_factory=factory
        for client in ('first-stdio','after-restart'):
            mid=self.queue(client)
            self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and not self.host.turns[mid].closed:
                self.host.tick();time.sleep(.005)
            self.assertEqual(self.host.store.turn(mid)['state'],'completed',self.host.turns[mid].error)
            self.assertTrue(processes[-1].closed)
            self.assertIsNotNone(processes[-1].process.poll())
            self.assertEqual(self.host.dispatch('binding',self.scope)['session_id'],'synthetic-thread')
            self.host.close()
            self.host=Host(Path(self.tmp.name)/'data',transport_factory=factory)
        self.assertEqual(len([r for r in self.host.dispatch('history',self.scope) if r['pubkey']=='provider']),2)

    def test_recovery_matches_client_id_when_acknowledgement_was_lost(self):
        mid=self.queue()
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.peers[0].finish=False
        self.settle()
        self.host.turns[mid].deadline=0;self.host.tick()
        with self.host.store.db:self.host.store.db.execute('UPDATE turns SET provider_turn_id=NULL WHERE id=?',(mid,))
        binding=self.host.store.binding(self.scope['channelId'],self.scope['projectId'],actor=ACTOR)
        history={'thread':{'id':binding['session_id'],'cwd':binding['cwd'],'modelProvider':'openai','turns':[{
            'id':'synthetic-turn','status':'completed','items':[{'type':'userMessage','id':'u','clientId':'synthetic-client',
                'content':[{'type':'text','text':'Synthetic request'}]},{'type':'agentMessage','id':'answer','text':'Recovered'}]}]}}
        self.host.store.reconcile_codex(mid,self.scope['projectId'],actor=ACTOR,verified_binding=binding,history=history)
        self.assertEqual(self.host.store.turn(mid)['state'],'completed')
        self.assertEqual(self.host.store.turn(mid)['provider_turn_id'],'synthetic-turn')

    def test_host_catalog_requires_explicit_check_without_draining_queue(self):
        mid=self.queue()
        profile=self.host.dispatch('binding',self.scope)['profile_id']
        self.assertEqual(self.host.dispatch('model_status',{'profileId':profile})['state'],'unchecked')
        self.host.dispatch('model_check',{'profileId':profile})
        self.host.catalogs[profile].result(timeout=2)
        result=self.host.dispatch('model_status',{'profileId':profile})
        self.assertEqual(result['models'][0]['model'],BINDING['model'])
        self.assertFalse(result['canSend'])
        self.assertEqual(self.host.store.turn(mid)['state'],'queued')

    def test_reconcile_after_disconnect_never_replays_turn(self):
        mid=self.queue()
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        self.peers[0].finish=False
        self.settle()
        self.host.turns[mid].deadline=0
        self.host.tick()
        self.assertEqual(self.host.store.turn(mid)['state'],'uncertain')
        self.host.dispatch('reconcile_turn',{**self.scope,'messageId':mid})
        # The recovery peer's saved project represents its persisted rollout.
        self.peers[-1].project=str(Path(self.tmp.name).resolve())
        self.settle()
        self.assertEqual(self.host.store.turn(mid)['state'],'completed')
        self.assertEqual(self.host.dispatch('history',self.scope)[-1]['content'],'Recovered synthetic answer')
        self.assertNotIn('turn/start',[m.get('method') for m in self.peers[-1].sent])
        self.assertNotIn('thread/resume',[m.get('method') for m in self.peers[-1].sent])

class CatalogTests(unittest.TestCase):
    def test_catalog_only_is_paginated_and_never_starts_thread(self):
        sent=[]
        a=CodexAdapter(BINDING,sent.append,lambda *_:None,catalog_only=True)
        a.connect()
        a.receive({'id':sent[-1]['id'],'result':INIT})
        a.receive({'id':sent[-1]['id'],'result':CONFIG})
        a.receive({'id':sent[-1]['id'],'result':{'account':{'type':'chatgpt','email':BINDING['identity'],'planType':'plus'}}})
        a.receive({'id':sent[-1]['id'],'result':{'data':[{'model':'first','hidden':False}],'nextCursor':'page-2'}})
        self.assertEqual(sent[-1]['params']['cursor'],'page-2')
        a.receive({'id':sent[-1]['id'],'result':{'data':[{'model':'second','hidden':False}],'nextCursor':None}})
        self.assertEqual(a.state,'catalog')
        self.assertEqual([m['model'] for m in a.models],['first','second'])
        self.assertFalse(any(m.get('method','').startswith(('thread/','turn/')) for m in sent))

class SafetyTests(unittest.TestCase):
    def test_resuming_active_thread_or_loaded_instructions_blocks_new_text(self):
        for result in ({'instructionSources':['/outside/AGENTS.md']},
                {'thread':{'id':'synthetic-thread','turns':[{'id':'old','status':'inProgress'}]}}):
            with self.assertRaises(Rejected):LifecycleTests().ready('synthetic-thread',result)

    def test_effective_configuration_precedes_authenticated_model_requests(self):
        sent=[]
        a=CodexAdapter(BINDING,sent.append,lambda *_:None)
        a.connect();a.receive({'id':sent[-1]['id'],'result':INIT})
        self.assertEqual(sent[-1]['method'],'config/read')
        self.assertFalse(any(m.get('method') in ('model/list','account/read') for m in sent))

    def test_all_approval_types_deny_and_unknown_requests_error(self):
        a,sent,_=LifecycleTests().ready()
        a.begin('Synthetic','client');a.receive({'id':sent[-1]['id'],'result':{'turn':{'id':'turn'}}})
        scope={'threadId':a.session_id,'turnId':a.turn_id}
        for method,expected in [('item/commandExecution/requestApproval',{'decision':'decline'}),
                ('item/fileChange/requestApproval',{'decision':'decline'}),
                ('item/permissions/requestApproval',{'permissions':{},'scope':'turn'})]:
            a.receive({'id':100,'method':method,'params':scope})
            self.assertEqual(sent[-1]['result'],expected)
        a.receive({'id':101,'method':'item/tool/call','params':scope})
        self.assertIn('error',sent[-1])
        with self.assertRaises(Rejected):a.receive({'id':102,'method':'item/fileChange/requestApproval','params':{**scope,'threadId':'wrong'}})

    def test_different_installed_version_fails_before_authentication(self):
        sent=[]
        a=CodexAdapter(BINDING,sent.append,lambda *_:None)
        a.connect()
        with self.assertRaises(Rejected):a.receive({'id':sent[-1]['id'],'result':{'userAgent':'dervo/0.150.0','codexHome':'/wrong'}})
        self.assertNotIn('account/read',[m.get('method') for m in sent])

if __name__ == '__main__': unittest.main()
