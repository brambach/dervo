"""Task-guide delivery through real host logic and both scripted provider adapters."""
import json
import unittest
from copy import deepcopy
from test_collaboration import CollaborationTests
from dervo_connections import Rejected
from task_guide import TOOL, LIMITS, MAX_UPDATES, capture

PLAN = dict(title='Greeting failures', goal='Make greeting failures easy to understand.', step='Inspect the greeting and its test.',
            why='The existing test defines the expected response.', next='Check the result, then explain how to try it.',
            test_steps='', limits='No file has been changed or checked yet.')
HANDOFF = dict(PLAN, step='Explain how to try the existing greeting.',
              next='Run the greeting test in the project folder.',
              test_steps='1. In the project folder, run python3 -m unittest test_sample.py. Expected: one passing test.\n2. Inspect greet() in greeting.py. Expected: it returns hi.',
              limits='Source inspection only. No test execution or browser check in this run.')


class GuideMixin:
    def named(self):
        """The owner thread with the sidebar record a real conversation has, so the guide's title has somewhere to land."""
        db = self.host.store.db
        with db:
            db.execute("INSERT OR IGNORE INTO local_channels VALUES ('main', 'New conversation')")
        organisation = self.host.organisation()
        with db:
            organisation.initial('main', automatic=True)
        return organisation

    def test_the_guides_title_names_the_thread_once_and_never_over_a_chosen_name(self):
        organisation = self.named()
        second = dict(PLAN, title='Greeting fixes', step='Second title, same run.')
        with self.peer_patch(lambda *_: [('tool', TOOL, PLAN), ('tool', TOOL, second), ('finish', 'Done.')]):
            started = self.request('Explain the greeting and how to test it.')
            task = self.settle(started['taskId'])['task']
        self.assertEqual(task['runs'][-1]['guide']['title'], 'Greeting fixes', 'the guide keeps the latest title')
        state = organisation.read('main')
        self.assertEqual((state['name'], state['nameLocked'], state['source']), ('Greeting failures', False, {'kind': 'guide', 'id': task['id']}))
        self.assertEqual(state['reason'], 'Named by the coding agent from its Task guide.')
        self.assertEqual(state['canUndo'] if 'canUndo' in state else state['undo'] is not None, True)
        # A name you set by hand is locked: a later guide title never replaces it.
        self.host.dispatch('organisation_set', dict(projectId='p', channelId='main', expectedRevision=state['revision'], name='Greeting',
                                                    group=None, nameLocked=True, groupLocked=False))
        with self.peer_patch(lambda *_: [('tool', TOOL, dict(PLAN, title='Yet another')), ('finish', 'Done again.')]):
            again = self.request('And the second half.', client_id='req-2')
            self.settle(again['taskId'])
        self.assertEqual(organisation.read('main')['name'], 'Greeting')

    def test_a_guide_update_without_a_title_is_still_saved_and_its_dashes_are_plain(self):
        self.named()
        untitled = {key: value for key, value in PLAN.items() if key != 'title'}
        dashed = dict(untitled, step='Reading greeting.py \u2014 the test names the expected reply.', why='The test \u2014 not the code \u2014 defines it.')
        with self.peer_patch(lambda *_: [('tool', TOOL, dashed), ('finish', 'No changes made.')]):
            started = self.request('Explain the greeting.')
            task = self.settle(started['taskId'])['task']
        guide = task['runs'][-1]['guide']
        self.assertEqual(guide['title'], '')
        self.assertEqual(guide['step'], 'Reading greeting.py - the test names the expected reply.')
        self.assertEqual(guide['why'], 'The test - not the code - defines it.')
        state = self.host.organisation().read('main')
        self.assertEqual((state['name'], state['source']['kind']), ('Explain greeting', 'coding'), 'the request named it; a guide with no title leaves that alone')

    def test_reported_plan_and_handoff_persist_without_becoming_execution_evidence(self):
        with self.peer_patch(lambda *_:[('tool',TOOL,PLAN),('tool','dervo_read_file',{'path':'greeting.py'}),('tool',TOOL,HANDOFF),('finish','No changes made.')]):
            started=self.request('Explain the greeting and how to test it.')
            detail=self.settle(started['taskId'])
        task=detail['task'];run=task['runs'][-1]
        self.assertEqual(task['state'],'answered')
        self.assertEqual(detail['evidence']['attempts'],[])
        self.assertNotIn('completion',task)
        self.assertEqual(run['guide']['test_steps'],HANDOFF['test_steps'])
        self.assertEqual(run['guide']['run_id'],run['id'])
        self.assertEqual(run['guide_updates'],2)
        self.assertEqual(run['guide']['tool_token'],task['tokens'][-1]['token'])
        self.assertTrue(all(token['success'] for token in task['tokens']))
        self.assertIn('Before your final answer',self.peers[-1].text)
        audits=[a for a in detail['broker_audit'] if set(a['arguments'])==set(PLAN)]
        self.assertEqual([a['arguments'] for a in audits],[PLAN,HANDOFF])
        self.reopen()
        self.assertEqual(self.detail(task['id'])['task']['runs'][-1]['guide'],run['guide'])
        with self.assertRaises(Rejected):self.detail(task['id'],dict(projectId='p',channelId='review-room'))

    def test_oversized_report_can_be_corrected_and_does_not_end_the_turn(self):
        with self.peer_patch(lambda *_:[('tool',TOOL,{**PLAN,'why':'é'*301}),('tool',TOOL,PLAN),('finish','Plan recorded.')]):
            started=self.request('Inspect greeting.')
            task=self.settle(started['taskId'])['task']
        self.assertEqual(task['state'],'answered')
        self.assertEqual([t['success'] for t in task['tokens']],[False,True])
        self.assertIn('Shorten why',json.dumps(self.peers[-1].results))
        self.assertEqual(task['runs'][-1]['guide_updates'],1)

    def test_run_identity_roles_binding_grant_and_update_limit(self):
        with self.peer_patch(lambda *_:[('tool',TOOL,PLAN),('hang',)]):
            started=self.request('Inspect greeting.')
            task=self.settle(started['taskId'],until=lambda d:len(d['task']['tokens'])==1 and d['task']['tokens'][0]['state']=='delivered')['task']
            token=task['tokens'][0]
            for patch in ({'origin':'collaboration'},{'role':'reviewer'},{'review_phase':'initial'},{'state':'answered'}):
                with self.assertRaises(Rejected):capture(self.host.coding,{**deepcopy(task),**patch},token,PLAN)
            for patch in ({'origin':'collaboration'},{'role':'reviewer'},{'review_phase':'initial'}):
                self.assertNotIn(TOOL,self.host.coding.tools_for({**task,**patch}))
            with self.assertRaises(Rejected):capture(self.host.coding,deepcopy(task),{**token,'run_id':'earlier'},PLAN)
            wrong=deepcopy(task);wrong['binding']['identity']='another@example.test'
            with self.assertRaises(Rejected):capture(self.host.coding,wrong,token,PLAN)
            limited=deepcopy(task);limited['runs'][-1]['guide_updates']=MAX_UPDATES
            self.assertFalse(capture(self.host.coding,limited,token,PLAN)['saved'])
            self.assertEqual(self.detail(task['id'])['task']['runs'][-1]['guide_updates'],1)
            self.host.dispatch('coding_permission_revoke',self.scope)
            with self.assertRaises(Rejected):capture(self.host.coding,deepcopy(task),token,PLAN)

    def test_legacy_session_keeps_identity_without_prompting_unavailable_guide(self):
        with self.peer_patch(lambda *_:[('hang',)]):
            started=self.request('Inspect greeting.')
            self.settle(started['taskId'],until=lambda d:d['task']['runs'][-1]['state']=='running')
            self.host.dispatch('task_stop',{**self.scope,'taskId':started['taskId']})
            old=self.settle(started['taskId'])['task']
        old.pop('guide_session_id',None)
        self.host.coding.save(old)
        with self.peer_patch(lambda *_:[('finish','Follow-up answer.')]):
            self.host.dispatch('task_continue',{**self.scope,'taskId':old['id'],'text':'Explain the greeting again without changing anything.'})
            current=self.settle(old['id'])['task']
        self.assertEqual(current['session_id'],old['session_id'])
        self.assertEqual(current['state'],'answered')
        self.assertEqual(len(current['runs']),2)
        if self.owner_provider == 'claude':
            self.assertIn(TOOL,self.host.coding.tools_for(current))
            self.assertIn('Before your final answer',self.peers[-1].text)
        else:
            self.assertNotIn(TOOL,self.host.coding.tools_for(current))
            # The property is that a legacy session is never prompted about a guide it can't use, not that it gets no
            # finishing list at all. Those were the same thing until the list was rebuilt from the tools a task
            # actually has (f87536c): this session still has dervo_remember, so its memory step is real and belongs.
            self.assertNotIn('dervo_update_task_guide',self.peers[-1].text)
            self.assertIn('Before your final answer, go through this list',self.peers[-1].text)
            self.assertNotIn('guide_session_id',current)

    def test_new_session_retains_guide_capability_on_resume_and_restart(self):
        with self.peer_patch(lambda *_:[('tool',TOOL,PLAN),('hang',)]):
            started=self.request('Inspect greeting.')
            self.settle(started['taskId'],until=lambda d:bool(d['task']['runs'][-1].get('guide')) and d['task']['tokens'][-1]['state']=='delivered')
            self.host.dispatch('task_stop',{**self.scope,'taskId':started['taskId']})
            old=self.settle(started['taskId'])['task']
        self.assertEqual(old['guide_session_id'],old['session_id'])
        self.reopen()
        with self.peer_patch(lambda *_:[('tool',TOOL,HANDOFF),('finish','Test guidance ready.')]):
            self.host.dispatch('task_continue',{**self.scope,'taskId':old['id'],'text':'Explain how to test it.'})
            current=self.settle(old['id'])['task']
        self.assertEqual(current['session_id'],old['session_id'])
        self.assertEqual(current['runs'][-1]['guide']['test_steps'],HANDOFF['test_steps'])
        self.assertIn('Before your final answer',self.peers[-1].text)
        if self.owner_provider == 'codex':
            self.assertNotIn(TOOL,self.host.coding.tools_for({**current,'guide_session_id':'different-session'}))

    def test_stopped_run_guide_never_becomes_the_new_runs_plan(self):
        with self.peer_patch(lambda *_:[('tool',TOOL,PLAN),('hang',)]):
            started=self.request('Inspect greeting.')
            task=self.settle(started['taskId'],until=lambda d:bool(d['task']['runs'][-1].get('guide')) and d['task']['tokens'][-1]['state']=='delivered')['task']
            self.host.dispatch('task_stop',{**self.scope,'taskId':task['id']})
            stopped=self.settle(task['id'])['task']
        self.assertEqual(stopped['runs'][-1]['state'],'interrupted')
        with self.peer_patch(lambda *_:[('hang',)]):
            self.host.dispatch('task_continue',{**self.scope,'taskId':task['id'],'text':'Continue with the narrower request.'})
            resumed=self.settle(task['id'],until=lambda d:d['task']['runs'][-1]['state']=='running')['task']
            self.assertNotEqual(resumed['runs'][-1]['id'],task['runs'][-1]['id'])
            self.assertNotIn('guide',resumed['runs'][-1])
            self.assertEqual(resumed['runs'][-2]['guide'],task['runs'][-1]['guide'])


class CodexGuides(GuideMixin,CollaborationTests):pass
class ClaudeGuides(GuideMixin,CollaborationTests):owner_provider='claude'

def load_tests(loader,tests,pattern):
    names=[name for name in GuideMixin.__dict__ if name.startswith('test_')]
    return unittest.TestSuite(cls(name) for cls in (CodexGuides,ClaudeGuides) for name in names)
