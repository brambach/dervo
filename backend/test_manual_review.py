"""Empty-project coding through real host brokers and synthetic provider peers."""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch
import test_conversation_coding as fixture
from dervo_connections import Rejected

class ManualReviewTests(unittest.TestCase):
    provider='codex'
    close_host=fixture.ConversationCodingTests.close_host
    reopen=fixture.ConversationCodingTests.reopen
    request=fixture.ConversationCodingTests.request
    settle=fixture.ConversationCodingTests.settle
    finished=staticmethod(fixture.ConversationCodingTests.finished)

    def setUp(self):
        fixture.ConversationCodingTests.setUp(self)
        for path in self.project.iterdir():path.unlink()
        self.output=None
        self.host.dispatch('coding_permission_grant',{**self.scope,'files':[],'testFile':None,'projectAccess':True,'checkProfile':'manual-review'})

    def peer(self, operation='create', before_finish=None):
        test=self
        name,args=('dervo_create_file',{'path':'README.md','content':'# New project\n'}) if operation=='create' else ('dervo_run_tests',{})
        if self.provider=='codex':
            from test_codex_coding import SyntheticCodingPeer,SID
            class Peer(SyntheticCodingPeer):
                def send(peer,message):
                    if message.get('method')=='turn/start':
                        test.starts+=1;peer.sent.append(message)
                        peer.queue.append({'id':message['id'],'result':{'turn':{'id':'turn-1'}}})
                        peer.queue.append({'id':'manual-tool','method':'item/tool/call','params':dict(threadId=SID,turnId='turn-1',callId='manual-tool',tool=name,arguments=args)})
                    elif message.get('id')=='manual-tool':
                        test.output=message['result']
                        if before_finish:before_finish()
                        peer.queue.extend([{'method':'item/agentMessage/delta','params':dict(threadId=SID,turnId='turn-1',itemId='answer',delta='Created README.md. No automated checks ran.')},{'method':'turn/completed','params':{'threadId':SID,'turn':{'id':'turn-1','status':'completed'}}}])
                    else:super().send(message)
            return patch('codex_coding.OwnedProcess',Peer)
        from test_claude_coding import SyntheticClaudePeer,verified_auth,CLAUDE
        class Peer(SyntheticClaudePeer):
            def on_prompt(peer):
                test.starts+=1;peer.tool(name,args,10)
            def on_tool_result(peer,mid,result):
                test.output=result
                if before_finish:before_finish()
                peer.finish(text='Created README.md. No automated checks ran.')
        stack=ExitStack()
        for target,value in [('auth_status',verified_auth),('executable',lambda:CLAUDE),('OwnedProcess',Peer)]:stack.enter_context(patch('claude_coding.'+target,value))
        return stack

    def run_create(self, before_finish=None):
        with self.peer(before_finish=before_finish):
            request=self.request('Create a README for this new project.')
            return self.settle(request['taskId'])

    def test_empty_project_edits_wait_for_human_without_a_passing_check(self):
        d=self.run_create();task=d['task'];attempt=d['evidence']['attempts'][-1]
        self.assertEqual(task['state'],'ready_for_review',task['error'])
        self.assertEqual(task['acceptance'],{'required':True,'source':'manual-review'})
        self.assertFalse(self.host.coding.acceptance.policy('p')['required'])
        self.assertEqual(attempt['state'],'unverified');self.assertIsNone(attempt['test']);self.assertEqual(attempt['argv'],[])
        self.assertNotIn('dervo_run_tests',self.host.coding.tools_for(task))
        self.assertNotIn('completion',task)
        self.assertIn('+# New project',attempt['diff']['README.md'])
        self.assertEqual((self.project/'README.md').read_text(),'# New project\n')
        self.host.dispatch('task_review',{**self.scope,'taskId':task['id'],'attemptNumber':attempt['number'],'decision':'approved'})
        self.reopen();saved=self.host.dispatch('task_detail',{**self.scope,'taskId':task['id']})
        self.assertEqual(saved['task']['state'],'idle')
        self.assertEqual(saved['evidence']['review']['checks'],'not_run')
        self.assertIsNone(saved['evidence']['attempts'][-1]['test'])

    def test_read_after_write_gets_full_manual_snapshot_without_checks(self):
        (self.project / 'other.md').write_text('Reference only\n')
        def read_reference():
            coding = self.host.coding
            task_id = next(iter(coding.turns))
            task = coding.collaboration.raw_task(task_id)
            coding.evidence.read_file(task_id, 'other.md', **task['context'])
        with patch('task_evidence.TaskEvidence.launch', side_effect=AssertionError('No checks in manual mode')):
            detail = self.run_create(read_reference)
        self.assertEqual(detail['task']['state'], 'ready_for_review', detail['task']['error'])
        attempt = detail['evidence']['attempts'][-1]
        self.assertEqual(set(attempt['after']), {'README.md', 'other.md'})
        self.assertIsNone(attempt['test'])
        self.assertEqual(attempt['state'], 'unverified')
        self.assertNotIn('completion', detail['task'])
        from collaboration import prefix
        self.assertNotIn('dervo_run_tests', prefix([], project_access=True, check_profile='manual-review'))
        (self.project / 'other.md').write_text('Changed after review\n')
        with self.assertRaises(Rejected):
            self.host.dispatch('task_review', {**self.scope, 'taskId': detail['task']['id'],
                               'attemptNumber': attempt['number'], 'decision': 'approved'})

    def test_external_edit_prevents_approval(self):
        d=self.run_create();(self.project/'README.md').write_text('External edit\n')
        with self.assertRaises(Rejected):self.host.dispatch('task_review',{**self.scope,'taskId':d['task']['id'],'attemptNumber':d['evidence']['attempts'][-1]['number'],'decision':'approved'})

    def test_revocation_before_finish_does_not_make_reviewable_result(self):
        d=self.run_create(lambda:self.host.dispatch('coding_permission_revoke',self.scope))
        self.assertEqual(d['task']['state'],'blocked');self.assertNotIn('completion',d['task'])
        self.assertNotEqual(d['evidence']['attempts'][-1]['state'],'unverified')

    def test_test_tool_is_not_available_and_no_runner_launches(self):
        with self.peer('test'),patch('task_evidence.TaskEvidence.launch',side_effect=AssertionError('runner must not launch')):
            request=self.request('Run tests.')
            d=self.settle(request['taskId'])
        self.assertNotEqual(d['task']['state'],'completed')
        self.assertEqual(d['evidence']['attempts'],[])

    def test_manual_grant_rejects_check_file_and_empty_unscoped_access(self):
        for changes in [{'testFile':'test_sample.py'},{'projectAccess':False}]:
            with self.assertRaises(Rejected):self.host.dispatch('coding_permission_grant',{**self.scope,'files':[],'testFile':None,'projectAccess':True,'checkProfile':'manual-review',**changes})

class ClaudeManualReviewTests(ManualReviewTests):
    provider='claude'

if __name__=='__main__':unittest.main()
