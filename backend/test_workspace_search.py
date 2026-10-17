"""Public dispatch, disposable Store, no provider or source tools."""
import json
import tempfile
import unittest
from pathlib import Path
from host import Host, ACTOR
from dervo_connections import Rejected

class WorkspaceSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.host = Host(self.root)
        s = self.host.store
        for p, category in [('personal','personal'),('other','personal'),('work','work')]:
            s.add_profile(p,'codex',self.root/p,p+'@example.test',category)
        for project in ['project','elsewhere']: s.add_project(project,self.root,'personal','fixture-model')
        for channel, project, visibility, profile, owner in [
            ('current','project','shared','personal',None),('shared','project','shared','personal',None),
            ('private','project','private','personal',ACTOR),('hidden','project','private','personal','someone-else'),
            ('other','project','shared','other',None),('work','project','shared','work',None),
            ('elsewhere','elsewhere','shared','personal',None)]:
            s.add_thread(channel,project,channel,visibility,owner)
            s.db.execute('UPDATE threads SET profile_id=? WHERE id=?',(profile,channel))
            s.db.execute('INSERT INTO local_channels VALUES (?,?)',(channel,channel))
            s.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',('msg-'+channel,channel,channel,ACTOR,'needle '+channel,'assistant'))
        s.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',('literal','current','literal',ACTOR,'100% a_b literal','user'))
        s.db.execute('CREATE TABLE coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
        for channel in ['current','private','other','work']:
            task=dict(id='task-'+channel,context=dict(thread_id=channel,project_id='project',actor=ACTOR),binding=s.binding(channel,'project',actor=ACTOR),state='disconnected',error=None,messages=[dict(role='user',text='Fix fixture'),dict(role='assistant',text='needle result')])
            s.db.execute('INSERT INTO coding_tasks VALUES (?,?)',(task['id'],json.dumps(task)))
        # These fixture records represent history saved before activity tracking.
        s.db.execute('UPDATE message_activity SET phase=0,activity=0')
        s.db.commit()
        self.scope=dict(projectId='project',channelId='current',profileId='personal',scope='project')
    def tearDown(self):
        self.host.close()
        self.tmp.cleanup()
    def search(self, query='needle', **extra):
        return self.host.dispatch('workspace_search',{**self.scope,'query':query,'limit':20,**extra})
    def test_shared_privacy(self):
        # Shared threads of the project on any of this Mac's personal accounts ('other' too, handoff map H1). Never a
        # work account's, never someone else's private one, never another project's.
        result=self.search()
        self.assertEqual({r['id'] for r in result['results']},{'msg-current','msg-shared','task-current','msg-other','task-other'})
        self.assertEqual(set(result),{'results','cursor','incomplete'})
        self.assertFalse(hasattr(self.host,'coding'))
        self.assertEqual(self.host.turns,{})
    def test_project_without_selected_conversation_keeps_privacy(self):
        count=self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0]
        result=self.search(channelId='')
        self.assertEqual({r['id'] for r in result['results']},{'msg-current','msg-shared','task-current','msg-other','task-other'})
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0],count)
        self.assertEqual(self.host.turns,{})
        for row in result['results']:
            self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'channelId':'','source':row['source']})['id'],row['id'])
        for changes in [dict(scope='conversation'),dict(profileId='other'),dict(profileId='work'),dict(projectId='missing')]:
            with self.assertRaises(Rejected): self.search(channelId='',**changes)
    def test_project_source_rejects_rebinding_and_archive(self):
        row=self.search(channelId='')['results'][0]
        args={**self.scope,'channelId':'','source':row['source']}
        self.host.store.bind_project('project','other','fixture-model')
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',args)
        self.host.store.bind_project('project','personal','fixture-model')
        self.host.store.db.execute('INSERT INTO project_archives VALUES (?,?)',('project',1))
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',args)
    def test_project_knowledge_without_conversation_keeps_account_scope(self):
        self.host.store.add_project('empty-project',self.root,'personal','fixture-model')
        scope=dict(projectId='empty-project',channelId='',profileId='personal')
        saved=self.host.dispatch('knowledge_save',{**scope,'ruleId':None,'expectedRevision':0,'text':'Keep the project readable.','provenance':'Fixture preference','source':None})
        rules=self.host.dispatch('knowledge_list',scope)['rules']
        self.assertEqual(rules[0]['id'],saved['id'])
        for profile in ['other','work']:
            with self.assertRaises(Rejected): self.host.dispatch('knowledge_list',{**scope,'profileId':profile})
    def test_exact_conversation_and_private(self):
        self.assertEqual({r['id'] for r in self.search(scope='conversation')['results']},{'msg-current','task-current'})
        self.assertEqual({r['id'] for r in self.search(scope='conversation',channelId='private')['results']},{'msg-private','task-private'})
        for changes in [dict(channelId='hidden'),dict(channelId='work',profileId='work'),dict(channelId='other'),dict(projectId='elsewhere')]:
            with self.assertRaises(Rejected): self.search(**changes)
    def test_literals_and_bounds(self):
        for query in ['%','_','100%','a_b']:
            self.assertEqual([r['id'] for r in self.search(query)['results']],['literal'])
        self.assertEqual(self.search("' OR 1=1 --")['results'],[])
        self.assertEqual(len(self.search(limit=1)['results']),1)
        for changes in [dict(query=''),dict(query='  '),dict(query='x'*257),dict(limit=0),dict(limit=51),dict(limit=True),dict(scope='all'),dict(sql='anything')]:
            with self.assertRaises(Rejected): self.search(**changes)
    def test_restart_rename_and_late_navigation(self):
        row=self.search()['results'][0]
        args={**self.scope,'source':row['source']}
        self.assertEqual(self.host.dispatch('workspace_source',args)['id'],row['id'])
        self.host.close()
        self.host=Host(self.root)
        self.host.store.db.execute("UPDATE local_channels SET name='Renamed' WHERE id='current'")
        self.host.store.db.commit()
        self.assertEqual(self.host.dispatch('workspace_source',args)['id'],row['id'])
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',{**args,'channelId':'shared'})
        self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id=?",(ACTOR,row['channelId']))
        self.host.store.db.commit()
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',args)
    def test_stale_content_and_forged_id(self):
        row=self.search(scope='conversation')['results'][0]
        args={**self.scope,'scope':'conversation','source':row['source']}
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',{**args,'source':{**row['source'],'id':'msg-private'}})
        self.host.store.db.execute("UPDATE messages SET text='changed' WHERE id=?",(row['id'],))
        self.host.store.db.commit()
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',args)
    def test_catchup_unknown_and_link(self):
        result=self.host.dispatch('project_catchup',{**self.scope,'limit':20})
        self.assertEqual({r['id'] for r in result['results']},{'task-current','msg-current','msg-shared','literal','task-other','msg-other'})
        row=next(r for r in result['results'] if r['id']=='task-current')
        self.assertEqual(row['status'],'disconnected')
        self.assertEqual(row['title'],'Fix fixture')
        self.assertEqual(row['blocker'],'Unknown: no blocker recorded')
        self.assertNotIn('nextAction',row,'nothing ever wrote next_action; the field is gone rather than always unknown')
        self.assertEqual(row['summary'],'Outcome unconfirmed. Dervo lost confirmation of a run or tool result.')
        self.assertIn('codex',row['owner'])
        self.assertIn('needle result',self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'])
        self.assertFalse(hasattr(self.host,'coding'))

    def test_catchup_shows_only_the_message_of_a_saved_provider_error(self):
        """Task 1e29a5f3 in the owner workspace, 16 September 2026: a Codex TurnError object saved before errors were
        coerced. Catch up reads the raw record, never CodingTasks.read(), so the read repair from 8078d7d never ran and
        the card printed the whole dict, extras included."""
        legacy={'message':"You've hit your usage limit.",'additionalDetails':'argv=--token SHOULD-NOT-LEAK',
                'codexErrorInfo':{'kind':'usage_limit_exceeded'},'misalignment':None}
        task=json.loads(self.host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?',('task-current',)).fetchone()[0])
        task['error']=legacy
        self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?',(json.dumps(task),'task-current'))
        self.host.store.db.commit()
        page=self.host.dispatch('project_catchup',{**self.scope,'limit':20})
        row=next(r for r in page['results'] if r['id']=='task-current')
        self.assertEqual(row['blocker'],"You've hit your usage limit.")
        self.assertFalse(page['incomplete'],'a repaired error is not a malformed record')
        excerpt=self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})
        self.assertEqual(excerpt['blocker'],"You've hit your usage limit.")
        for payload in (row,excerpt):
            for leak in ('SHOULD-NOT-LEAK','additionalDetails','codexErrorInfo','misalignment'):
                self.assertNotIn(leak,json.dumps(payload))
        self.assertFalse(hasattr(self.host,'coding'))
        # A missing message still says something true rather than printing the extras.
        task['error']={'additionalDetails':'argv=--token SHOULD-NOT-LEAK'}
        self.host.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?',(json.dumps(task),'task-current'))
        self.host.store.db.commit()
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        self.assertEqual(row['blocker'],'The provider reported an error without a message.')

    def test_task_saved_binding_and_project_rebinding_are_rechecked(self):
        """A saved task's binding is checked against its thread's on every read. Since 19 Sep (handoff map H1) the
        account may differ, as long as it is a personal one of this Mac: a thread moved to Codex keeps its Claude tasks
        in Catch up. The folder, the visibility and a work account are still refused."""
        def catchup():
            return {r['id'] for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results']}
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        db=self.host.store.db
        def rebind(**changes):
            task=json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?',(row['id'],)).fetchone()[0])
            task['binding'].update(changes)
            db.execute('UPDATE coding_tasks SET document=? WHERE id=?',(json.dumps(task),row['id']))
            db.commit()
        rebind(profile_id='other',provider='codex',identity='other@example.test')
        self.assertIn('task-current',catchup(),'the task was made on another personal account of this Mac: still this thread\'s work')
        with self.assertRaises(Rejected): self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})  # the record changed: re-read
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['id'],'task-current')
        for hidden in (dict(profile_id='work'),dict(profile_id='personal',cwd='/elsewhere'),dict(profile_id='personal',visibility='private'),
                       dict(profile_id='gone')):
            rebind(**{**dict(profile_id='personal',cwd=self.host.store.binding('current','project',actor=ACTOR)['cwd'],visibility='shared'),**hidden})
            self.assertNotIn('task-current',catchup(),hidden)
            with self.assertRaises(Rejected): self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})
        # The project's default account moving to another personal account no longer hides the search from a thread
        # that stayed on the old one; a work account as the project's account still does.
        rebind(profile_id='personal',provider='codex',identity='personal@example.test')
        self.host.store.bind_project('project','other','fixture-model')
        self.assertIn('task-current',{r['id'] for r in self.search()['results']})
        # A Personal project can't be moved to a work account at all now (b16-work); one left there by an older build
        # still hides the search.
        with self.assertRaises(Rejected): self.host.store.bind_project('project','work','fixture-model')
        with self.host.store.db: self.host.store.db.execute("UPDATE projects SET profile_id='work' WHERE id='project'")
        with self.assertRaises(Rejected): self.search()

    def test_a_thread_moved_to_another_account_stays_in_catch_up(self):
        """Handoff map, 19 Sep, H1: "Move to Codex" (conversation_profile_set) used to drop the thread from Catch up
        while the sidebar still listed it. The work account's thread is the control: it never shows."""
        def rows():
            return {r['id'] for r in self.host.dispatch('project_catchup',{**self.scope,'channelId':'','limit':20})['results']}
        before=rows()
        self.assertIn('task-shared' if 'task-shared' in before else 'msg-shared',before)
        db=self.host.store.db
        db.execute("UPDATE threads SET profile_id='other', model='codex-model' WHERE id='shared'"); db.commit()
        self.assertIn('msg-shared',rows(),'moved to another personal account: still here')
        db.execute("UPDATE threads SET profile_id='work' WHERE id='shared'"); db.commit()
        self.assertNotIn('msg-shared',rows(),'a work account is not this project\'s personal work')
        self.assertNotIn('msg-work',rows())

    def test_open_matched_text_beyond_first_excerpt_window(self):
        self.host.store.db.execute('UPDATE messages SET text=? WHERE id=?',('x'*20000+' finalneedle','msg-current'))
        self.host.store.db.commit()
        row=self.search('finalneedle')['results'][0]
        opened=self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})
        self.assertIn('finalneedle',opened['text'])
        self.assertTrue(opened['truncated'])

    def test_catchup_links_latest_recorded_result_after_long_request(self):
        task=json.loads(self.host.store.db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        task['messages']=[dict(role='user',text='request '*3000),dict(role='assistant',text='Latest recorded result')]
        self.host.store.db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'",(json.dumps(task),))
        self.host.store.db.commit()
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        self.assertIn('Latest recorded result',row['snippet'])
        opened=self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})
        self.assertIn('Latest recorded result',opened['text'])

    def bulk(self, count=1100):
        db=self.host.store.db
        template=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        for i in range(count):
            task={**template, 'id':f'bulk-{i}', 'messages':[dict(role='user',text='ordinary saved record')]}
            db.execute('INSERT INTO coding_tasks VALUES (?,?)',(task['id'],json.dumps(task)))
        db.commit()

    def test_bounded_no_match_and_direct_late_source(self):
        from unittest.mock import patch
        import workspace_search as search
        self.bulk()
        with patch.object(search, '_stamp', wraps=search._stamp) as stamp:
            page=self.search('absent',limit=1)
            self.assertLess(stamp.call_count,10)
        self.assertTrue(page['incomplete'])
        self.assertIsInstance(page['cursor'],str)
        self.assertEqual(page['results'],[])
        seen=0
        while page['cursor']:
            page=self.search('absent',limit=1,cursor=page['cursor'])
            seen+=1
            self.assertLess(seen,30)
        self.assertFalse(page['incomplete'])
        page=self.search('ordinary',limit=1)
        source=page['results'][0]['source']
        with patch.object(search, '_stamp', wraps=search._stamp) as stamp:
            opened=self.host.dispatch('workspace_source',{**self.scope,'source':source})
            self.assertEqual(opened['id'],'bulk-1099')
            self.assertLess(stamp.call_count,10)

    def test_catchup_later_room_and_pages(self):
        self.bulk(25)
        db=self.host.store.db
        task=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        task.update(id='later-blocker',state='blocked',error='Needs decision')
        task['context']['thread_id']='shared'
        task['binding']=self.host.store.binding('shared','project',actor=ACTOR)
        db.execute('INSERT INTO coding_tasks VALUES (?,?)',(task['id'],json.dumps(task)))
        db.commit()
        args={**self.scope,'limit':20}
        page=self.host.dispatch('project_catchup',args)
        self.assertEqual(page['results'][0]['id'],'later-blocker')
        ids=[r['id'] for r in page['results']]
        while page['cursor']:
            page=self.host.dispatch('project_catchup',{**args,'cursor':page['cursor']})
            ids.extend(r['id'] for r in page['results'])
        self.assertEqual(len(ids),32)
        self.assertTrue({'literal','msg-shared','msg-current','msg-other','task-other'} <= set(ids))
        self.assertEqual(len(ids),len(set(ids)))
        self.assertNotIn('task-private',ids)

    def test_cursor_refuses_changes_malformed_and_scope_reuse(self):
        self.bulk(150)
        cursor=self.search('absent',limit=1)['cursor']
        for extra in [dict(cursor='bad'),dict(cursor={}),dict(cursor=3),dict(cursor=None),dict(cursor=True),dict(cursor='a'*33),dict(cursor='a'*31+'!'),dict(cursor=cursor,query='needle'),dict(cursor=cursor,channelId='shared'),dict(cursor=cursor,scope='conversation'),dict(cursor=cursor,projectId='elsewhere',channelId='elsewhere'),dict(cursor=cursor,profileId='other')]:
            with self.assertRaises(Rejected): self.host.dispatch('workspace_search',{**self.scope,'query':'absent','limit':1,**extra})
        self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='shared'",(ACTOR,))
        self.host.store.db.commit()
        with self.assertRaises(Rejected): self.search('absent',limit=1,cursor=cursor)

    def test_measured_scan_decode_and_sql_work_and_oldest_exact_id(self):
        from unittest.mock import patch
        import workspace_search as search
        self.bulk()
        reads=[]
        original=search._read
        def measured(*args, **kwargs):
            value=original(*args, **kwargs)
            if value: reads.append(value)
            return value
        decoded=[]
        original_loads=search.json.loads
        def loads(value):
            decoded.append(len(value.encode()))
            return original_loads(value)
        with patch.object(search,'_read',side_effect=measured), patch.object(search.json,'loads',side_effect=loads):
            page=self.search('absent',limit=1)
        self.assertLessEqual(len(reads),search.SCAN_RECORDS)
        self.assertLessEqual(sum(decoded),search.DECODE_BYTES)
        self.assertEqual(len(reads),128)
        db=self.host.store.db
        task=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='bulk-0'").fetchone()[0])
        task['messages']=[dict(role='assistant',text='oldest exact target')]
        db.execute("UPDATE coding_tasks SET document=? WHERE id='bulk-0'",(json.dumps(task),))
        db.commit()
        page=self.search('oldest exact target',limit=1)
        while not page['results'] and page['cursor']:
            page=self.search('oldest exact target',limit=1,cursor=page['cursor'])
        self.assertEqual(page['results'][0]['id'],'bulk-0')
        reads.clear()
        with patch.object(search,'_read',side_effect=measured):
            opened=self.host.dispatch('workspace_source',{**self.scope,'source':page['results'][0]['source']})
        self.assertEqual(opened['text'],'oldest exact target')
        self.assertEqual(len(reads),1)
        print(f'Measured no-match: 128 records, {len(decoded)} task decodes, {sum(decoded)} decoded task bytes; late source: 1 primary-key read')

    def test_size_budget_oversize_and_malformed_records(self):
        from unittest.mock import patch
        import workspace_search as search
        self.bulk(30)
        db=self.host.store.db
        for i in range(30):
            task=json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?',(f'bulk-{i}',)).fetchone()[0])
            task['messages']=[dict(role='assistant',text='x'*200000)]
            db.execute('UPDATE coding_tasks SET document=? WHERE id=?',(json.dumps(task),task['id']))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)',('oversize','x'*(search.RECORD_BYTES+1)))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)',('malformed','{'))
        db.commit()
        sizes=[]
        original=search._read
        def measured(*args, **kwargs):
            value=original(*args, **kwargs)
            if value and value['content'] is not None: sizes.append(value['size'])
            return value
        with patch.object(search,'_read',side_effect=measured):
            page=self.search('absent')
        self.assertLessEqual(sum(sizes),search.DECODE_BYTES)
        self.assertTrue(page['incomplete'])
        self.assertTrue(page['cursor'])
        while page['cursor']:
            page=self.search('absent',cursor=page['cursor'])
        self.assertTrue(page['incomplete'])
        self.assertEqual(page['results'],[])
        print(f'Measured large-record page: {sum(sizes)} transferred content bytes, cap {search.DECODE_BYTES}')

    def test_cursor_external_change_expiry_actor_and_task_privacy(self):
        from unittest.mock import patch
        import workspace_search as search
        import sqlite3
        self.bulk(150)
        page=self.search('absent',limit=1)
        token=page['cursor']
        self.assertRegex(token,r'^[A-Za-z0-9_-]{32}$')
        with self.assertRaises(Rejected):
            search.dispatch(self.host,'workspace_search',{**self.scope,'query':'absent','limit':1,'cursor':token},'someone-else')
        with patch.object(search.time,'monotonic',return_value=search.time.monotonic()+901):
            with self.assertRaises(Rejected): self.search('absent',limit=1,cursor=token)
        token=self.search('absent',limit=1)['cursor']
        path=self.host.store.db.execute('PRAGMA database_list').fetchone()[2]
        external=sqlite3.connect(path)
        try:
            with external:
                external.execute("UPDATE messages SET text='edited externally' WHERE id='literal'")
        finally:
            external.close()
        with self.assertRaises(Rejected): self.search('absent',limit=1,cursor=token)
        db=self.host.store.db
        task=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='bulk-149'").fetchone()[0])
        task['context']['actor']='someone-else'
        task['messages']=[dict(role='assistant',text='private secret needle')]
        db.execute("UPDATE coding_tasks SET document=? WHERE id='bulk-149'",(json.dumps(task),))
        db.commit()
        page=self.search('needle',limit=1)
        pages=[]
        while True:
            pages.append(page)
            if not page['cursor']: break
            page=self.search('needle',limit=1,cursor=page['cursor'])
        rendered=json.dumps(pages)
        for hidden in ['private secret','task-private','msg-private','msg-work','task-work']:
            self.assertNotIn(hidden,rendered)
        self.assertIn('task-other',rendered,'another personal account of this Mac is your own work')
        self.assertTrue(all(set(p)=={'results','cursor','incomplete'} for p in pages))

    def test_sql_interruption_is_incomplete_error_and_handler_is_removed(self):
        from unittest.mock import patch
        import workspace_search as search
        self.bulk(100)
        with patch.object(search,'SQL_STEPS',1000):
            with self.assertRaisesRegex(Rejected,'Search is incomplete'):
                self.search('absent')
        self.assertEqual(self.search('needle')['results'][0]['id'],'msg-other','the handler is gone: the newest shared row on any personal account comes back first')

    def test_malformed_task_structures_remain_incomplete_on_every_page(self):
        db=self.host.store.db
        template=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        source=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')['source']
        variants=[[], {**template,'context':[]}, {**template,'context':{}},
                  {**template,'context':{**template['context'],'thread_id':3}},
                  {**template,'id':'wrong'}, {**template,'binding':[]},
                  {**template,'binding':{}},
                  {**template,'binding':{**template['binding'],'profile_id':[]}},
                  {**template,'context':{**template['context'],'actor':[]}},
                  {**template,'messages':{}},
                  {k:v for k,v in template.items() if k != 'messages'},
                  {**template,'messages':[None]},
                  {**template,'messages':[dict(role='assistant',text=3)]},
                  {**template,'messages':[dict(text='hidden malformed text')]}]
        self.bulk(260)
        # Keep the malformed record first in task scan order.
        db.execute("DELETE FROM coding_tasks WHERE id='task-current'")
        for index, task in enumerate(variants):
            with self.subTest(variant=index):
                db.execute('INSERT OR REPLACE INTO coding_tasks VALUES (?,?)',('task-current',json.dumps(task)))
                db.commit()
                for operation in ['workspace_search','project_catchup']:
                    args={**self.scope,'limit':50}
                    if operation == 'workspace_search': args['query']='absent'
                    pages=[]
                    preparing=0
                    while True:
                        page=self.host.dispatch(operation,args)
                        if page.get('indexing'):
                            preparing+=1
                            self.assertLess(preparing,20)
                            self.assertEqual(page['results'],[])
                            self.assertIsNone(page['cursor'])
                            self.assertTrue(page['incomplete'])
                            continue
                        pages.append(page)
                        self.assertEqual(set(page),{'results','cursor','incomplete'}|({'since','lessonsPending','skillsPending'} if operation=='project_catchup' else set()))
                        self.assertTrue(page['incomplete'])
                        self.assertNotIn('task-current',[r['id'] for r in page['results']])
                        if not page['cursor']: break
                        args['cursor']=page['cursor']
                        self.assertLess(len(pages),20)
                    self.assertGreater(len(pages),1)
                    self.assertNotIn('hidden malformed text',json.dumps(pages))
                with self.assertRaises(Rejected):
                    self.host.dispatch('workspace_source',{**self.scope,'source':source})

    def test_well_formed_scope_exclusions_and_empty_messages_are_complete(self):
        db=self.host.store.db
        template=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        variants=[{**template,'messages':[]},
                  {**template,'context':{**template['context'],'actor':'someone-else'}},
                  {**template,'context':{**template['context'],'project_id':'elsewhere'}},
                  {**template,'binding':{**template['binding'],'profile_id':'other'}}]
        for task in variants:
            with self.subTest(task=task):
                db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'",(json.dumps(task),))
                db.commit()
                self.assertEqual(self.search('absent'),dict(results=[],cursor=None,incomplete=False))
                page=self.host.dispatch('project_catchup',{**self.scope,'limit':20})
                self.assertFalse(page['incomplete'])
                self.assertIsNone(page['cursor'])

    def test_helper_summary_uses_recorded_identity_without_changing_exact_source(self):
        db=self.host.store.db
        task=json.loads(db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        packet='Collaboration request with needle and a long source manifest'
        task.update(origin='collaboration',agent='reviewer',role='reviewer',state='blocked',messages=[dict(role='user',text=packet)])
        db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'",(json.dumps(task),));db.commit()
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        self.assertEqual(row['title'],'Review by @reviewer')
        self.assertEqual(row['snippet'],'No answer recorded.')
        self.assertFalse(row['hasAnswer'])
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})['text'],packet)
        searched=next(r for r in self.search()['results'] if r['id']=='task-current')
        self.assertIn('needle',searched['snippet'])
        task['messages'].append(dict(role='assistant',text='A recorded finding'))
        db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'",(json.dumps(task),));db.commit()
        row=next(r for r in self.host.dispatch('project_catchup',{**self.scope,'limit':20})['results'] if r['id']=='task-current')
        self.assertTrue(row['hasAnswer']);self.assertEqual(row['snippet'],'A recorded finding')
