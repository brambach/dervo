"""The actual text-only coordinator path, driven by a disposable fake worker."""
import copy
import json
import unittest
import coordination_search
import test_harness_host as fixtures
from host import ACTOR
from dervo_connections import Rejected


class CoordinationSearchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.HarnessHostTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.host=self.fixture.host
        self.room=self.fixture.seed()
        binding=self.host.store.binding(self.room['channelId'],self.room['projectId'],actor=ACTOR)
        self.scope={**self.room,'profileId':binding['profile_id'],'scope':'project'}

    def page(self):
        for _ in range(30):
            page=self.host.dispatch('project_catchup',{**self.scope,'limit':20})
            if not page.get('indexing'):return page
        self.fail('Preparation did not finish')

    def ask(self):
        request=self.fixture.ask(self.room,'request','What date is next Friday?','dates',host=self.host)
        self.fixture.settle(self.room,request['id'],host=self.host)
        return request['id']

    def test_default_text_only_result_is_in_catchup_search_and_exact_source_after_restart(self):
        key=self.ask()
        self.assertEqual(self.host.store.messages(self.room['channelId'],self.room['projectId'],actor=ACTOR),[])
        row=self.page()['results'][0]
        self.assertEqual((row['kind'],row['id'],row['snippet'],row['matchRole']),('coordination',key,fixtures.DATE_ANSWER,'assistant'))
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'],fixtures.DATE_ANSWER)
        search=self.host.dispatch('workspace_search',{**self.scope,'query':'Friday','limit':20})
        self.assertEqual(search['results'][0]['id'],key)
        self.fixture.close_host(self.host);self.host=self.fixture.open_host()
        self.assertEqual(self.page()['results'][0]['id'],key)
        self.assertFalse(hasattr(self.host,'harness'))
        self.assertFalse(hasattr(self.host,'coding'))
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'],fixtures.DATE_ANSWER)

    def test_activity_ignores_poll_bookkeeping_but_tracks_results_and_scope_changes(self):
        key=self.ask();harness=self.host.harness
        request=copy.deepcopy(harness.requests()[0]);before=list(map(tuple,self.host.store.db.execute('SELECT * FROM coordination_activity')))
        request['pulled_seq']+=10;harness.save(request)
        self.assertEqual(before,list(map(tuple,self.host.store.db.execute('SELECT * FROM coordination_activity'))))
        old=self.page()['results'][0]
        request.update(state='unknown',error='Observation was lost');harness.save(request)
        self.assertEqual(self.page()['results'][0]['status'],'unknown')
        with self.assertRaises(Rejected):self.host.dispatch('workspace_source',{**self.scope,'source':old['source']})
        source=self.page()['results'][0]['source']
        with self.host.store.db:self.host.store.db.execute("UPDATE profiles SET identity='changed@example.test' WHERE id=?",(self.scope['profileId'],))
        self.assertEqual(self.page()['results'],[])
        with self.assertRaises(Rejected):self.host.dispatch('workspace_source',{**self.scope,'source':source})

    def test_legacy_unbound_and_malformed_coordination_records_remain_incomplete(self):
        key=self.ask();request=self.host.harness.requests()[0]
        for changes in [{'binding':None},{'linked_return':{'summary':3}},{'delegations':[3]}]:
            altered={**request,**changes}
            with self.host.store.db:self.host.store.db.execute('UPDATE harness_requests SET document=? WHERE id=?',(json.dumps(altered),key))
            page=self.page();self.assertEqual(page['results'],[]);self.assertTrue(page['incomplete'])

    def test_private_coordination_doesnt_enter_project_briefing(self):
        key=self.ask();row=self.page()['results'][0]
        with self.host.store.db:self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id=?",(ACTOR,self.room['channelId']))
        self.assertEqual(self.page()['results'],[])
        with self.assertRaises(Rejected):self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})

    def test_latest_answer_uses_saved_return_dates_and_exact_source_offsets(self):
        key=self.ask();request=copy.deepcopy(self.host.harness.requests()[0])
        request['linked_return']={'summary':'Final owner answer','returned_at':'2026-09-13T02:00:00.200Z'}
        request['delegations']=[{'handle':'@dates','status':'returned','returned_at':'2026-09-13T12:00:00.100+10:00','result':{'summary':'Earlier helper answer'}}]
        self.host.harness.save(request)
        row=self.page()['results'][0]
        self.assertEqual(row['snippet'],'Final owner answer')
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'],'Final owner answer')
        request['delegations'][0]['returned_at']='2026-09-13T02:00:00.300Z'
        self.host.harness.save(request)
        row=self.page()['results'][0]
        self.assertEqual(row['snippet'],'@dates answered: Earlier helper answer')
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'],row['snippet'])

    def test_equal_or_missing_dates_have_stable_owner_last_fallback(self):
        for date in (None,'bad date','2026-09-13T02:00:00','2026-09-13T02:00:00Z'):
            with self.subTest(date=date):
                request={'text':'Question','linked_return':{'summary':'Owner','returned_at':date},'delegations':[
                    {'handle':'@first','status':'returned','returned_at':date,'result':{'summary':'First'}},
                    {'handle':'@second','status':'returned','returned_at':date,'result':{'summary':'Second'}}]}
                self.assertEqual([r['text'] for r in coordination_search.messages(request)],['Question','@first answered: First','@second answered: Second','Owner'])


if __name__=='__main__':unittest.main()
