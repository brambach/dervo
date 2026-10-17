"""Disposable project and real brokers, with synthetic provider processes."""
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import test_conversation_coding as fixtures
from dervo_connections import Rejected
from project_reading import ProjectReader

class ReadingTests(unittest.TestCase):
    provider = 'codex'
    setUp = fixtures.ConversationCodingTests.setUp
    close_host = fixtures.ConversationCodingTests.close_host
    reopen = fixtures.ConversationCodingTests.reopen
    request = fixtures.ConversationCodingTests.request
    settle = fixtures.ConversationCodingTests.settle
    finished = staticmethod(fixtures.ConversationCodingTests.finished)

    def peer_patch(self, hold=False, tool=None):
        test = self
        plan = [('dervo_list_files', {'directory': '.'}), ('dervo_read_file', {'path': 'README.md'}),
                ('dervo_search_files', {'directory': 'src', 'query': 'support'})]
        if tool:
            plan = [tool]
        if self.provider == 'codex':
            from test_codex_coding import SyntheticCodingPeer, SID
            class Peer(SyntheticCodingPeer):
                def tool(peer, index):
                    name, args = plan[index]
                    peer.queue.append({'id':'read-'+str(index), 'method':'item/tool/call', 'params':dict(
                        threadId=SID, turnId='turn-1', callId='read-'+str(index), tool=name, arguments=args)})
                def send(peer, message):
                    if message.get('method') == 'turn/start':
                        test.starts += 1
                        peer.sent.append(message)
                        peer.queue.append({'id':message['id'], 'result':{'turn':{'id':'turn-1'}}})
                        if not hold: peer.tool(0)
                    elif str(message.get('id', '')).startswith('read-'):
                        test.results.append(json.loads(message['result']['contentItems'][0]['text']))
                        index = int(message['id'].split('-')[1])+1
                        if index < len(plan): peer.tool(index)
                        else: peer.queue.extend([
                            {'method':'item/agentMessage/delta', 'params':dict(threadId=SID,turnId='turn-1',itemId='answer',delta='README.md describes a support portal. No files changed.')},
                            {'method':'turn/completed','params':{'threadId':SID,'turn':{'id':'turn-1','status':'completed'}}}])
                    else: super().send(message)
            return patch('codex_coding.OwnedProcess', Peer)
        from test_claude_coding import SyntheticClaudePeer, verified_auth, CLAUDE
        class Peer(SyntheticClaudePeer):
            def on_prompt(peer):
                test.starts += 1
                if not hold: peer.tool(*plan[0], 10)
            def on_tool_result(peer, mid, result):
                test.results.append(json.loads(result['content'][0]['text']))
                index = mid-10+1
                if index < len(plan): peer.tool(*plan[index], index+10)
                else: peer.finish(text='README.md describes a support portal. No files changed.')
        stack = ExitStack()
        for target, value in [('auth_status',verified_auth), ('executable',lambda:CLAUDE), ('OwnedProcess',Peer)]:
            stack.enter_context(patch('claude_coding.'+target,value))
        return stack

    def setup_project(self):
        (self.project/'README.md').write_text('# Portal\nA customer support portal.\n')
        (self.project/'src').mkdir()
        (self.project/'src/main.ts').write_text('export const feature = "support requests";\n')
        (self.project/'test_sample.py').unlink()
        self.results=[]

    def completed(self):
        self.setup_project()
        with self.peer_patch():
            request=self.request('Summarize this project.')
            self.assertIsNotNone(request['taskId'],request)
            detail=self.settle(request['taskId'])
        self.assertEqual(detail['task']['state'],'answered',detail['task']['error'])
        return request,detail

    def test_explicit_exploration_stays_read_only_with_coding_enabled_and_retries_once(self):
        fixtures.ConversationCodingTests.grant(self)
        self.setup_project()  # Removing the check makes coding stale; explicit reading still works.
        with self.peer_patch():
            request = self.host.dispatch('project_explore', {'projectId':'p','clientId':'explore-1'})
            self.scope = {'projectId':'p','channelId':request['channelId']}
            detail = self.settle(request['taskId'])
            retry = self.host.dispatch('project_explore', {'projectId':'p','clientId':'explore-1'})
        self.assertEqual(retry['taskId'], request['taskId'])
        self.assertEqual(self.starts, 1)
        self.assertTrue(detail['task']['read_only'])
        self.assertEqual(detail['task']['state'], 'answered', detail['task']['error'])
        self.assertEqual(detail['evidence']['attempts'], [])
        self.assertNotIn('dervo_write_file', self.host.coding.tools_for(detail['task']))
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_request', {**self.scope, 'clientId':'explore-1',
                'text':detail['task']['messages'][0]['text'], 'readOnly':False})

    def test_fresh_project_status_describes_reading_without_claiming_connection(self):
        self.setup_project()
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['coding']['reading']['state'], 'available')
        self.assertEqual(status['state'], 'unchecked')
        self.assertFalse(status['active'])
        self.assertNotIn('Chat turns are unavailable', status['detail'])
        self.assertIn('checked', status['detail'])
        self.assertEqual(self.starts, 0)

    def test_unavailable_claude_reading_keeps_blocked_status(self):
        if self.provider != 'claude':
            return
        self.project.rename(self.base / 'moved-project')
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['coding']['reading']['state'], 'unavailable')
        self.assertEqual(status['state'], 'blocked')
        self.assertEqual(self.starts, 0)

    def test_failed_reading_task_keeps_its_actual_error(self):
        self.setup_project()
        with self.peer_patch(tool=('dervo_read_file', {'path': '../outside'})):
            request = self.request('Read the project.')
            detail = self.settle(request['taskId'])
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], detail['task']['state'])
        self.assertEqual(status['detail'], detail['task']['error'])
        self.assertNotEqual(status['state'], 'unchecked')

    def test_fresh_project_read_list_search_no_test_setup(self):
        request,d=self.completed()
        self.assertIn('README.md',[e['name'] for e in self.results[0]['entries']])
        self.assertIn('customer support portal',self.results[1]['content'])
        self.assertEqual(self.results[2]['hits'][0]['path'],'src/main.ts')
        self.assertTrue(d['task']['read_only'])
        self.assertEqual(d['evidence']['attempts'],[])
        self.assertEqual(d['evidence']['admitted'],['README.md'])
        self.assertIsNone(d['evidence']['argv'])
        self.assertEqual(len(d['broker_audit']),3)
        self.assertEqual(set(self.host.coding.tools_for(d['task'])),{'dervo_ask_user','dervo_remind_user','dervo_read_file','dervo_list_files','dervo_search_files','dervo_update_task_guide','dervo_remember','dervo_search_history','dervo_propose_thread','dervo_check_chat','dervo_leave_note'})
        context=self.host.coding.conversation.context_text(d['task'],'bad editing prefix')
        self.assertIn(str(self.project),context)
        self.assertNotIn('bad editing prefix',context)
        self.assertEqual(self.request('Summarize this project.')['taskId'],request['taskId'])
        self.assertEqual(self.starts,1)
        with self.assertRaises(Rejected): self.request('Different content')
        self.reopen()
        restored=self.host.dispatch('task_detail',{**self.scope,'taskId':request['taskId']})
        self.assertEqual(restored['task']['state'],'answered')
        self.assertEqual(restored['broker_audit'],d['broker_audit'])

    def test_paths_and_mutation_boundaries(self):
        _,d=self.completed(); t=d['task']; r=ProjectReader(self.host.coding,t)
        (self.project/'.env').write_text('SECRET=synthetic')
        (self.project/'outside').symlink_to(self.base)
        os.link(self.project/'README.md',self.project/'linked.md')
        (self.project/'node_modules').mkdir()
        (self.project/'node_modules/secret.txt').write_text('synthetic')
        for path in ('../project/README.md','/etc/passwd','.env','outside/README.md','linked.md','node_modules/secret.txt','.'):
            with self.subTest(path=path),self.assertRaises(Rejected): r.read_file(t['id'],path,**t['context'])
        for path in ('../project','/etc','outside','node_modules'):
            with self.subTest(directory=path),self.assertRaises(Rejected): r.list_files(t['id'],path,**t['context'])
        e=self.host.coding.evidence
        for op in (lambda:e.prepare(t['id'],changes={},**t['context']),lambda:e.create_file(t['id'],'src/new.ts',b'bad',**t['context']),
                   lambda:e.verify_current(t['id'],**t['context']),lambda:e.launch(t['id'],attempt_number=1,**t['context']),
                   lambda:e.scope(t['id'],files=['README.md'],test_file='README.md',**t['context'])):
            with self.assertRaisesRegex(Rejected,'Project reading cannot'): op()
        self.assertFalse((self.project/'src/new.ts').exists())

    def test_replaced_root_and_wrong_actor(self):
        _,d=self.completed();t=d['task'];r=ProjectReader(self.host.coding,t)
        self.project.rename(self.base/'old-project');self.project.mkdir()
        (self.project/'README.md').write_text('replacement')
        with self.assertRaisesRegex(Rejected,'replaced'): r.read_file(t['id'],'README.md',**t['context'])
        with self.assertRaisesRegex(Rejected,'replaced'): self.host.coding.require_grant(t)
        with self.assertRaises(Rejected): r.read_file(t['id'],'README.md',**{**t['context'],'actor':'someone-else'})

    def test_host_and_profile_roots_unavailable(self):
        from project_reading import availability
        self.host.dispatch('coding_permission',self.scope)
        b=self.host.store.binding(thread_id='c',project_id='p',actor=fixtures.ACTOR)
        for root in (self.base,self.host.root,self.host.root/'profiles/p'):
            self.assertEqual(availability(self.host.coding,{**b,'cwd':str(root)})['state'],'unavailable')

    def test_restart_active_reading_no_replay(self):
        self.setup_project()
        with self.peer_patch(hold=True):
            request=self.request('Read this project.')
            for _ in range(20): self.host.tick()
            self.assertEqual(self.starts,1)
            self.reopen()
        t=self.host.dispatch('task_detail',{**self.scope,'taskId':request['taskId']})['task']
        self.assertEqual(t['state'],'blocked','the quit paused it (16.4), and nothing is replayed')
        self.assertEqual(self.starts,1)

    def test_sensitive_prefixes_excluded_from_list_search_read(self):
        _,d=self.completed();t=d['task'];r=ProjectReader(self.host.coding,t)
        names=('secret_notes.py','secrets-production.json','token_backup.txt','tokens-local.json')
        for name in names:
            (self.project/name).write_text('synthetic-sensitive-marker')
            with self.assertRaises(Rejected): r.read_file(t['id'],name,**t['context'])
        listing=r.list_files(t['id'],'.',**t['context'])
        self.assertFalse(set(names)&{e['name'] for e in listing['entries']})
        search=r.search_files(t['id'],'.','synthetic-sensitive-marker',**t['context'])
        self.assertEqual(search['hits'],[])

    def test_wrong_identity_sends_no_project_prompt(self):
        self.setup_project()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE profiles SET identity='other@example.test' WHERE id='p'")
        with self.peer_patch():
            request=self.request('Read this project.')
            detail=self.settle(request['taskId'])
        self.assertEqual(self.starts,0)
        self.assertNotEqual(detail['task']['state'],'answered')
        self.assertEqual(detail['broker_audit'],[])

    def test_unadvertised_check_tool_is_denied_before_execution(self):
        self.setup_project()
        with self.peer_patch(tool=('dervo_run_tests',{})):
            request=self.request('Read this project.')
            detail=self.settle(request['taskId'])
        self.assertNotEqual(detail['task']['state'],'answered')
        self.assertEqual(detail['evidence']['attempts'],[])
        self.assertNotIn('dervo_run_tests',self.host.coding.tools_for(detail['task']))

    def test_stop_reading_keeps_project_unchanged(self):
        self.setup_project()
        before=(self.project/'README.md').read_bytes()
        with self.peer_patch(hold=True):
            request=self.request('Read this project.')
            for _ in range(20): self.host.tick()
            self.host.dispatch('task_stop',{**self.scope,'taskId':request['taskId']})
            detail=self.settle(request['taskId'])
        self.assertNotEqual(detail['task']['state'],'working')
        self.assertEqual(detail['evidence']['attempts'],[])
        self.assertEqual((self.project/'README.md').read_bytes(),before)

class ClaudeReadingTests(ReadingTests):
    provider='claude'

if __name__=='__main__': unittest.main()
