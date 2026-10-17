"""Read-only update protocol tests using the real host and synthetic peers."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from host import Host, ACTOR
from dervo_connections import Rejected
from test_codex_live import SyntheticPeer, BINDING
from conversation_feed import changes, MAX_CURSORS, MAX_CACHE_BYTES
import conversation_feed as feed_module


def apply(snapshot, patches):
    result=copy.deepcopy(snapshot)
    for patch in patches:
        path=patch['path']
        if not path:
            assert patch['op']=='set'
            result=copy.deepcopy(patch['value']);continue
        parent=result
        for part in path[:-1]:parent=parent[part]
        key=path[-1]
        if patch['op']=='set':parent[key]=copy.deepcopy(patch['value'])
        elif patch['op']=='remove':del parent[key]
        else:
            assert len(parent[key].encode('utf-16-le'))//2==patch['length']
            parent[key]+=patch['text']
    return result


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.peers=[]
        def factory(*args,**kwargs):
            peer=SyntheticPeer(*args,**kwargs,finish=False);self.peers.append(peer);return peer
        self.host=Host(Path(self.temp.name)/'data',transport_factory=factory)
        profile=self.host.dispatch('add_profile',{'provider':'codex','identity':BINDING['identity'],'category':'personal'})['id']
        project=self.host.add_picked_project(self.temp.name,profile,BINDING['model'])['id']
        channel=self.host.dispatch('create_conversation',{'projectId':project,'name':'Feed','private':False})['id']
        self.scope={'projectId':project,'channelId':channel}
    def tearDown(self):self.host.close();self.temp.cleanup()
    def feed(self,cursor=None,force=False,**scope):
        return self.host.dispatch('conversation_feed',{**self.scope,**scope,'cursor':cursor,'force':force})
    def test_initial_snapshot_and_unchanged_cursor_do_not_launch_a_provider(self):
        initial=self.feed()
        self.assertTrue(initial['reset'])
        self.assertEqual(initial['snapshot']['history'],[])
        next=self.feed(initial['cursor'])
        self.assertEqual(next['patches'],[])
        self.assertEqual(next['cursor'],initial['cursor'])
        self.assertEqual(self.peers,[])
    def test_raw_status_has_no_human_decision_and_retains_unknown_delivery(self):
        status=self.host.dispatch('conversation_status',self.scope)
        self.assertIsNone(status.get('human_decision'))
        mid=self.host.dispatch('queue',{**self.scope,'clientId':'uncertain-metadata','text':'Fixture','parentId':None})['id']
        with self.host.store.db:
            self.host.store.db.execute("UPDATE turns SET state='uncertain' WHERE id=?",(mid,))
        status=self.host.dispatch('conversation_status',self.scope)
        self.assertIsNone(status.get('human_decision'))
        self.assertEqual(status['state'],'unknown')
        self.assertEqual(self.peers,[])
    def test_text_append_and_terminal_snapshot_match_authoritative_history(self):
        before=self.feed()
        mid=self.host.dispatch('queue',{**self.scope,'clientId':'request','text':'Fixture','parentId':None})['id']
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        for _ in range(30):self.host.tick()
        current=self.feed(before['cursor'])
        state=current['snapshot'] if current['reset'] else apply(before['snapshot'],current['patches'])
        self.assertTrue(current['active'])
        self.host.store.record(mid,'delta',{'item_id':'answer','text':' plus 🌱'})
        following=self.feed(current['cursor'])
        self.assertFalse(following['reset'])
        self.assertTrue(any(p['op']=='append' for p in following['patches']))
        state=apply(state,following['patches'])
        self.assertEqual(state['history'],self.host.dispatch('history',self.scope))
        self.peers[-1].complete('completed')
        for _ in range(3):self.host.tick()
        terminal=self.feed(following['cursor'])
        state=terminal['snapshot'] if terminal['reset'] else apply(state,terminal['patches'])
        self.assertFalse(terminal['active'])
        self.assertEqual(state['history'],self.host.dispatch('history',self.scope))
        self.assertEqual(state['history'][-1]['content'],'Synthetic answer plus 🌱')
    def test_active_turn_ids_are_scoped_and_disappear_after_stop(self):
        mid=self.host.dispatch('queue',{**self.scope,'clientId':'stop-visible','text':'Fixture','parentId':None})['id']
        self.host.dispatch('start_turn',{**self.scope,'messageId':mid})
        for _ in range(30):self.host.tick()
        self.assertEqual(self.feed(force=True)['snapshot']['activity']['active_turn_ids'],[mid])
        other=self.host.dispatch('create_conversation',{'projectId':self.scope['projectId'],'name':'Other','private':False})['id']
        self.assertEqual(self.feed(channelId=other,force=True)['snapshot']['activity']['active_turn_ids'],[])
        self.host.dispatch('stop_turn',{**self.scope,'messageId':mid})
        for _ in range(8):self.host.tick()
        self.assertEqual(self.feed(force=True)['snapshot']['activity']['active_turn_ids'],[])
        self.assertEqual(self.host.store.turn(mid)['state'],'interrupted')
        self.assertTrue(self.host.dispatch('conversation_status',self.scope)['interrupted'])
        root=self.host.root
        self.host.close()
        self.host=Host(root)
        status=self.host.dispatch('conversation_status',self.scope)
        self.assertTrue(status['interrupted'])
        self.assertFalse(status['active'])
        self.host.dispatch('queue',{**self.scope,'clientId':'newer-request','text':'New request','parentId':None})
        self.assertFalse(self.host.dispatch('conversation_status',self.scope).get('interrupted',False))

    def test_cursor_cannot_cross_conversation_or_changed_account_binding(self):
        original=self.feed()['cursor']
        other=self.host.dispatch('create_conversation',{'projectId':self.scope['projectId'],'name':'Other','private':False})['id']
        with self.assertRaises(Rejected):self.feed(original,channelId=other)
        with self.host.store.db:self.host.store.db.execute('UPDATE threads SET model=? WHERE id=?',('changed-model',self.scope['channelId']))
        with self.assertRaises(Rejected):self.feed(original)
    def test_cursor_survives_the_thread_moving_into_its_worktree(self):
        # The first run on a thread's own branch moves its cwd into a worktree; the open thread keeps updating.
        cursor=self.feed()['cursor']
        import thread_folders
        with mock.patch.object(thread_folders,'cwd_for',lambda db,thread_id,project_cwd:(str(Path(project_cwd)/'wt'),None)):
            self.assertFalse(self.feed(cursor)['reset'])
    def test_private_ownership_is_checked_even_for_unchanged_cached_snapshot(self):
        with self.host.store.db:self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id=?",(ACTOR,self.scope['channelId']))
        cursor=self.feed()['cursor']
        with self.host.store.db:self.host.store.db.execute('UPDATE threads SET owner=? WHERE id=?',('another-user',self.scope['channelId']))
        with self.assertRaises(Rejected):self.feed(cursor)
    def test_missing_cursor_resets_and_cache_remains_bounded(self):
        old=self.feed()['cursor']
        for _ in range(20):self.feed(force=True)
        self.assertLessEqual(len(self.host.conversation_feed.cache),MAX_CURSORS)
        self.assertLessEqual(self.host.conversation_feed.bytes,MAX_CACHE_BYTES)
        self.assertTrue(self.feed(old)['reset'])
        self.assertTrue(self.feed('0'*32)['reset'])
    def test_utf16_append_offsets_and_structural_changes_round_trip(self):
        before={'rows':[{'text':'🌱 hello','state':'working'}],'removed':True}
        after={'rows':[{'text':'🌱 hello world','state':'completed'}],'added':1}
        patch=changes(before,after)
        self.assertEqual(next(p['length'] for p in patch if p['op']=='append'),8)
        self.assertEqual(apply(before,patch),after)
        self.assertEqual(apply(after,changes(after,{'rows':[]})),{'rows':[]})
    def test_large_removal_map_is_one_bounded_replacement(self):
        before={str(i):i for i in range(3000)}
        patch=changes(before,{})
        self.assertEqual(patch,[{'op':'set','path':[],'value':{}}])
        self.assertEqual(apply(before,patch),{})
    def test_invalid_protocol_fields_are_rejected(self):
        for cursor in [1,'bad','a'*100]:
            with self.assertRaises(Rejected):self.feed(cursor)
        with self.assertRaises(Rejected):self.feed(force='yes')
        with self.assertRaises(Rejected):self.host.dispatch('conversation_feed',{**self.scope,'cursor':None,'force':False,'actor':'someone-else'})


class HumanDecisionStatusTests(unittest.TestCase):
    """Decision metadata from real local evidence with synthetic owner output."""
    def setUp(self):
        from test_manual_review import ManualReviewTests
        self.fixture=ManualReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host,self.scope=self.fixture.host,self.fixture.scope
        detail=self.fixture.run_create()
        self.task_id=detail['task']['id']
        self.attempt=detail['evidence']['attempts'][-1]['number']
        self.initial_starts=self.fixture.starts

    def review(self,decision):
        self.host.dispatch('task_review',{**self.scope,'taskId':self.task_id,
                                         'attemptNumber':self.attempt,'decision':decision})

    def metadata(self):
        status=self.host.dispatch('conversation_status',self.scope)
        activity=self.host.dispatch('conversation_feed',{**self.scope,'cursor':None,'force':True})['snapshot']['activity']
        for key in ('human_decision','outcome_uncertain'):
            self.assertEqual(activity[key],status[key])
        self.assertEqual(self.fixture.starts,self.initial_starts)
        return status

    def test_explicit_decisions_are_reported_without_starting_a_continuation(self):
        self.assertIsNone(self.metadata()['human_decision'])
        for decision in ('rejected','approved'):
            self.review(decision)
            status=self.metadata()
            self.assertEqual(status['human_decision'],decision)
            self.assertFalse(status['outcome_uncertain'])
            self.assertEqual(status['state'],'idle' if decision=='approved' else 'ready_for_review')

    def test_only_exact_saved_decisions_are_reported_and_activity_is_not_relabelled(self):
        self.review('approved')
        original=self.host.coding.read(self.task_id,dict(thread_id=self.scope['channelId'],project_id=self.scope['projectId'],actor=ACTOR))
        for decision in ('approved','rejected',None,'passed','Approved',True,1,{},['approved']):
            with self.subTest(decision=decision):
                task=copy.deepcopy(original)
                task.update(state='working',review=decision)
                self.host.coding.save(task)
                status=self.metadata()
                expected=decision if type(decision) is str and decision in ('approved','rejected') else None
                self.assertEqual(status['human_decision'],expected)
                self.assertEqual(status['state'],'working')
                self.assertTrue(status['active'])

    def test_uncertain_run_or_token_keeps_observation_separate_from_saved_approval(self):
        self.review('approved')
        original=self.host.dispatch('tasks',self.scope)[-1]
        for kind in ('run','token','disconnected'):
            with self.subTest(kind=kind):
                task=copy.deepcopy(original)
                task['state']='disconnected' if kind=='disconnected' else 'ready_for_review'
                if kind=='run':task['runs'][0]['state']='uncertain'
                if kind=='token':task['tokens'].append(dict(state='uncertain',tool='dervo_write_file',run_id=task['runs'][-1]['id']))
                self.host.coding.save(task)
                status=self.metadata()
                self.assertEqual(status['human_decision'],'approved')
                self.assertTrue(status['outcome_uncertain'])
                self.assertFalse(status.get('interrupted',False))

    def test_external_file_change_invalidates_approval_before_status_is_returned(self):
        self.review('approved')
        self.assertEqual(self.metadata()['human_decision'],'approved')
        (self.fixture.project/'README.md').write_text('A newer external edit\n')
        status=self.metadata()
        self.assertIsNone(status['human_decision'])
        self.assertEqual(status['state'],'ready_for_review')
        self.assertIn('no longer current',status['detail'])


class MixedHistoryStatusTests(unittest.TestCase):
    """Durable task/raw ordering in disposable stores; no real provider calls."""
    def setUp(self):
        from test_manual_review import ManualReviewTests
        self.fixture = ManualReviewTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.host = self.fixture.host
        self.scope = self.fixture.scope

    def settled_task(self):
        detail = self.fixture.run_create()
        task, attempt = detail['task'], detail['evidence']['attempts'][-1]
        self.host.dispatch('task_review', {**self.scope, 'taskId': task['id'],
                                         'attemptNumber': attempt['number'], 'decision': 'approved'})
        return self.host.dispatch('tasks', self.scope)[-1]

    def raw(self, state, client_id='raw-status-fixture'):
        mid = self.host.dispatch('queue', {**self.scope, 'clientId': client_id,
                                         'text': 'Synthetic raw status fixture', 'parentId': None})['id']
        # Exercise persisted status precedence without launching a raw provider.
        with self.host.store.db:
            self.host.store.db.execute('UPDATE turns SET state=? WHERE id=?', (state, mid))
        return mid

    def status(self):
        result = self.host.dispatch('conversation_status', self.scope)
        return {key: result[key] for key in ('state', 'active', 'interrupted', 'review_active', 'detail') if key in result}

    def test_newer_interrupted_raw_turn_supersedes_older_settled_task(self):
        self.settled_task()
        self.raw('interrupted')
        status = self.status()
        self.assertEqual(status['state'], 'idle')
        self.assertTrue(status.get('interrupted'), status)
        self.assertFalse(status['active'])

    def test_newer_settled_task_supersedes_older_interrupted_raw_turn(self):
        self.raw('interrupted')
        self.settled_task()
        status = self.status()
        self.assertEqual(status['state'], 'idle')
        self.assertFalse(status.get('interrupted', False), status)

    def test_active_independent_review_is_not_hidden_by_interrupted_raw_history(self):
        task = self.settled_task()
        task['state'] = 'ready_for_review'
        task['collaboration'] = {'review': {'enabled': True, 'phase': 'initial'}}
        self.host.coding.save(task)
        self.raw('interrupted')
        status = self.status()
        self.assertEqual(status['state'], 'ready_for_review')
        self.assertTrue(status.get('review_active'), status)
        self.assertFalse(status.get('interrupted', False), status)

    def test_uncertain_raw_delivery_is_not_hidden_by_settled_task_or_stop(self):
        self.settled_task()
        self.raw('uncertain', 'uncertain-raw')
        self.raw('interrupted', 'later-stop')
        status = self.status()
        self.assertEqual(status['state'], 'unknown', status)
        self.assertFalse(status.get('interrupted', False), status)

    def test_uncertain_coding_run_is_not_replaced_by_newer_raw_stop(self):
        task = self.settled_task()
        task['state'] = 'blocked'
        task['error'] = 'Coding delivery is uncertain.'
        task['runs'][-1]['state'] = 'uncertain'
        self.host.coding.save(task)
        self.raw('interrupted')
        status = self.status()
        self.assertEqual(status['state'], 'blocked', status)
        self.assertEqual(status['detail'], 'Coding delivery is uncertain.')
        self.assertFalse(status.get('interrupted', False), status)


class PagingTests(unittest.TestCase):
    """16.4 item 1: big threads read as their newest page, and older pages join up with no gap or repeat."""
    setUp=FeedTests.setUp
    tearDown=FeedTests.tearDown
    feed=FeedTests.feed
    def say(self,n,at,size=16):
        # A settled message of your own at a known time, the shape queue writes.
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text) VALUES (?,?,?,?,?)',(f'm{n:04}',self.scope['channelId'],f'c{n}',ACTOR,'x'*size))
            self.host.store.db.execute("INSERT INTO turns(id,thread_id,state) VALUES (?,?,'completed')",(f'm{n:04}',self.scope['channelId']))
            self.host.store.db.execute('INSERT INTO local_projection VALUES (?,?,?)',(f'm{n:04}',None,at))
    def older(self,before,**scope):
        return self.host.dispatch('conversation_older',{**self.scope,**scope,'before':before})
    def walk(self,snapshot):
        """Every row id the reader ends up with: older pages, oldest first, then the snapshot's."""
        pages,before,more=[],snapshot['page']['before'],snapshot['page']['has_older']
        while more:
            page=self.older(before)
            self.assertTrue(page['history'] or page['tasks'])
            self.assertTrue(all(row['created_at']<before for row in page['history']))
            pages.insert(0,[row['id'] for row in page['history']])
            before,more=page['before'],page['has_older']
        return [row for page in pages for row in page]+[row['id'] for row in snapshot['history']]
    def test_seven_mib_of_history_reads_without_refusal_and_pages_join_up(self):
        for n in range(115):self.say(n,1000+n,size=64*1024)
        self.assertGreater(sum(len(row['content']) for row in self.host.dispatch('history',self.scope)),7*1024*1024)
        first=self.feed(force=True)
        snapshot=first['snapshot']
        self.assertLessEqual(len(feed_module.encoded(snapshot)),feed_module.MAX_SNAPSHOT_BYTES)
        self.assertTrue(snapshot['page']['has_older'])
        self.assertLess(len(snapshot['history']),115)
        ids=self.walk(snapshot)
        self.assertEqual(ids,[row['id'] for row in self.host.dispatch('history',self.scope)])
        self.assertEqual(len(ids),len(set(ids)))
    def test_a_patch_after_paging_still_applies_and_keeps_the_page_start(self):
        for n in range(200):self.say(n,1000+n)
        first=self.feed(force=True)
        self.assertEqual(len(first['snapshot']['history']),feed_module.PAGE_ROWS)
        older=self.older(first['snapshot']['page']['before'])
        self.assertEqual(older['history'][-1]['created_at'],first['snapshot']['history'][0]['created_at']-1)
        self.say(200,1200)
        self.host.conversation_feed.cache[first['cursor']]['checked_at']-=1
        following=self.feed(first['cursor'])
        self.assertFalse(following['reset'])
        state=apply(first['snapshot'],following['patches'])
        self.assertEqual(state['page'],first['snapshot']['page'])
        self.assertEqual([row['id'] for row in state['history']],[f'm{n:04}' for n in range(120,201)])
        self.assertEqual(self.walk(state),[row['id'] for row in self.host.dispatch('history',self.scope)])
    def test_a_kept_page_that_grew_too_big_starts_again_at_the_newest(self):
        for n in range(100):self.say(n,1000+n)
        first=self.feed(force=True)
        with mock.patch.object(feed_module,'KEPT_PAGE_BYTES',1):
            self.say(100,1100)
            self.host.conversation_feed.cache[first['cursor']]['checked_at']-=1
            following=self.feed(first['cursor'])
        state=following['snapshot'] if following['reset'] else apply(first['snapshot'],following['patches'])
        self.assertGreater(state['page']['before'],first['snapshot']['page']['before'])
        self.assertEqual(self.walk(state),[row['id'] for row in self.host.dispatch('history',self.scope)])
    def test_rows_that_share_a_time_stay_on_one_page(self):
        for n in range(feed_module.PAGE_ROWS+10):self.say(n,5000)
        for n in range(200,210):self.say(n,4000)
        snapshot=self.feed(force=True)['snapshot']
        self.assertEqual(len(snapshot['history']),feed_module.PAGE_ROWS+10)
        self.assertEqual(snapshot['page'],{'before':5000,'has_older':True})
        # The thread view sorts rows by time (timelineRows.orderRows); the history op lists them as they were saved.
        timed=sorted(self.host.dispatch('history',self.scope),key=lambda row:row['created_at'])
        self.assertEqual(self.walk(snapshot),[row['id'] for row in timed])
    def test_an_empty_thread_has_no_older_page(self):
        self.assertEqual(self.feed()['snapshot']['page'],{'before':None,'has_older':False})
    def test_tasks_page_by_their_first_message_and_open_ones_always_come(self):
        def task(n,at,state='completed',**extra):
            return {'id':f't{n}','state':state,'messages':[{'role':'user','text':'Ask','created_at':at}],
                    'runs':[{'id':f'r{n}','state':'completed','baseline':{'head':'a'*40,'files':{'f':'x'*4096}},'written':['a.py'],'guide':{'step':'Kept'}}],
                    'tokens':[{'token':f'k{n}','run_id':f'r{n}','tool':'dervo_read_file','state':'delivered','request_id':'q','call_id':'c','session_id':'s','turn_id':'u','audit_ref':'k','path':'a.py'}],**extra}
        tasks=[task(0,10,'working'),task(1,20,'completed',questions=[{'id':'q1','state':'pending'}]),
               *[task(n,1000+n) for n in range(2,150)]]
        real=self.host.dispatch
        def dispatch(operation,args):
            return [dict(item) for item in tasks] if operation=='tasks' else real(operation,args)
        with mock.patch.object(self.host,'dispatch',side_effect=dispatch):
            from conversation_feed import ConversationFeed
            feed=ConversationFeed(self.host)
            first=feed.read({**self.scope,'cursor':None,'force':True},ACTOR)
            snapshot=first['snapshot']
            listed=[item['id'] for item in snapshot['tasks']]
            self.assertIn('t0',listed)  # still working
            self.assertIn('t1',listed)  # waiting on your answer
            self.assertIn('t149',listed)
            self.assertNotIn('t2',listed)
            self.assertTrue(snapshot['page']['has_older'])
            shown=snapshot['tasks'][-1]
            self.assertEqual(shown['runs'][0],{'id':'r149','state':'completed','guide':{'step':'Kept'}})
            self.assertEqual(shown['tokens'][0],{'token':'k149','run_id':'r149','tool':'dervo_read_file','state':'delivered','path':'a.py'})
            seen,before,more=[],snapshot['page']['before'],True
            while more:
                page=feed.older({**self.scope,'before':before},ACTOR)
                seen=[item['id'] for item in page['tasks']]+seen
                before,more=page['before'],page['has_older']
            # An open task that settles is still delivered once more on the same cursor, so no reader keeps it working.
            tasks[0]['state']='completed'
            feed.cache[first['cursor']]['checked_at']-=1
            following=feed.read({**self.scope,'cursor':first['cursor'],'force':False},ACTOR)
            state=following['snapshot'] if following['reset'] else apply(snapshot,following['patches'])
            self.assertEqual(next(item['state'] for item in state['tasks'] if item['id']=='t0'),'completed')
            self.assertNotIn('t0',[item['id'] for item in feed.read({**self.scope,'cursor':None,'force':True},ACTOR)['snapshot']['tasks']])
        paged=[item for item in listed if item not in ('t0','t1')]
        self.assertEqual(seen+paged,[item['id'] for item in tasks])
        self.assertEqual(tasks[-1]['runs'][0]['baseline']['head'],'a'*40)  # the stored record is never trimmed
    def test_older_pages_check_the_thread_and_the_time(self):
        self.say(0,1000)
        for before in [None,'1000',True,0,-5,float('nan'),2**60]:
            with self.assertRaises(Rejected):self.older(before)
        other=self.host.dispatch('create_conversation',{'projectId':self.scope['projectId'],'name':'Other','private':False})['id']
        self.assertEqual(self.older(2000,channelId=other),{'history':[],'tasks':[],'before':None,'has_older':False})
        with self.host.store.db:self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id=?",('another-user',self.scope['channelId']))
        with self.assertRaises(Rejected):self.older(2000)
        with self.assertRaises(Rejected):self.host.dispatch('conversation_older',{**self.scope,'before':2000,'limit':5})


if __name__=='__main__':unittest.main()
