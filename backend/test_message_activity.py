"""Ordinary conversation return journeys in disposable stores, without provider calls."""
import unittest
import copy
from unittest.mock import patch
import test_workspace_search as fixtures
from coding_integration import CodingTasks
from host import Host, ACTOR
from dervo_connections import Rejected


class MessageActivityTests(unittest.TestCase):
    setUp = fixtures.WorkspaceSearchTests.setUp
    tearDown = fixtures.WorkspaceSearchTests.tearDown

    def page(self, **extra):
        for _ in range(100):
            result = self.host.dispatch('project_catchup', {**self.scope, 'limit':20, **extra})
            if not result.get('indexing'):
                return result
        self.fail('Preparation did not finish')

    def message(self, key, role='user', text='Plan the release', channel='shared', client=None):
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (key,channel,client or key,ACTOR,text,role))

    def test_request_answer_and_continued_task_share_order_and_exact_sources(self):
        self.host.coding = CodingTasks(self.host)
        coding = self.host.coding
        task = coding.collaboration.raw_task('task-current')
        coding.save(task)
        self.message('chat-request')
        with self.host.store.db:
            self.host.store.db.execute("INSERT INTO turns(id,thread_id,state) VALUES ('chat-request','shared','queued')")
        first = self.page()['results'][0]
        self.assertEqual((first['id'],first['status'],first['matchRole']), ('chat-request','queued','user'))
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':first['source']})['text'], 'Plan the release')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE turns SET state='completed' WHERE id='chat-request'")
        self.message('chat-request:answer', 'assistant', 'Start with the working signup journey.', client='chat-request:answer')
        answer = self.page()['results'][0]
        self.assertEqual((answer['id'],answer['status'],answer['matchRole']), ('chat-request:answer','completed','assistant'))
        with self.assertRaises(Rejected):
            self.host.dispatch('workspace_source',{**self.scope,'source':first['source']})
        task['messages'].append({'role':'assistant','text':'Updated task result','created_at':1})
        coding.save(task)
        self.assertEqual(self.page()['results'][0]['id'],'task-current')
        self.host.close(); self.host = Host(self.root)
        self.assertEqual(self.page()['results'][0]['id'],'task-current')
        self.assertEqual(self.host.dispatch('workspace_source',{**self.scope,'source':answer['source']})['text'], 'Start with the working signup journey.')
        self.assertEqual(self.host.turns,{})

    def test_partial_answer_preserves_failure_and_scope_changes_invalidate_source(self):
        self.message('request')
        with self.host.store.db:
            self.host.store.db.execute("INSERT INTO turns(id,thread_id,state,error) VALUES ('request','shared','interrupted','Stopped before finishing')")
        self.message('request:partial','assistant','A partial answer',client='request:partial')
        row=self.page()['results'][0]
        self.assertEqual(row['status'],'interrupted')
        self.assertEqual(row['blocker'],'Stopped before finishing')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET visibility='private',owner=? WHERE id='shared'",(ACTOR,))
        self.assertNotIn(row['id'],[r['id'] for r in self.page()['results']])
        with self.assertRaises(Rejected):
            self.host.dispatch('workspace_source',{**self.scope,'source':row['source']})

    def test_messages_without_coding_history_paginate_and_exclude_other_scopes(self):
        self.host.store.db.execute('DROP TABLE coding_tasks'); self.host.store.db.commit()
        for i in range(45): self.message(f'ordinary-{i}', 'assistant',f'Answer {i}')
        for channel in ['private','hidden','work','elsewhere']:
            self.message('excluded-'+channel,channel=channel)
        self.message('included-other',channel='other')  # another personal account of this Mac is in scope (handoff map H1, 19 Sep)
        page=self.page(limit=7);rows=page['results'][:]
        while page['cursor']:
            page=self.page(limit=7,cursor=page['cursor']);rows+=page['results']
        self.assertEqual([r['id'] for r in rows[:46]], ['included-other']+[f'ordinary-{i}' for i in reversed(range(45))])
        self.assertEqual(len(rows),50)  # the fixture's own msg-other counts now too
        self.assertEqual(len({r['id'] for r in rows}),50)
        self.assertFalse(any(r['id'].startswith('excluded-') for r in rows))

    def test_unchanged_save_doesnt_promote_but_new_text_does_and_invalidates_page(self):
        self.message('older');self.message('newer')
        page=self.page(limit=1)
        db=self.host.store.db
        with db:db.execute("UPDATE messages SET text=text WHERE id='older'")
        self.assertEqual(self.page()['results'][0]['id'],'newer')
        with db:db.execute("UPDATE messages SET text='A correction' WHERE id='older'")
        self.assertEqual(self.page()['results'][0]['id'],'older')
        with self.assertRaises(Rejected):self.page(limit=1,cursor=page['cursor'])

    def test_legacy_message_preparation_is_bounded_and_survives_restart(self):
        db=self.host.store.db
        with db:
            db.execute('DROP TRIGGER message_activity_insert')
            for i in range(90):
                db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(f'legacy-{i}','shared',f'legacy-{i}',ACTOR,'x'*10000,'user'))
                db.execute('INSERT INTO local_projection VALUES (?,?,?)',(f'legacy-{i}',None,1000+i))
        trace=[];db.set_trace_callback(trace.append)
        page=self.host.dispatch('project_catchup',{**self.scope,'limit':20})
        db.set_trace_callback(None)
        self.assertTrue(page['indexing']);self.assertEqual(page['results'],[])
        self.assertEqual(len([sql for sql in trace if 'SELECT rowid,id,client_id,role FROM messages' in sql]),32)
        self.host.close();self.host=Host(self.root)
        self.assertEqual(self.page()['results'][0]['id'],'legacy-89')

    def before_upgrade(self):
        db=self.host.store.db
        with db:
            for name in ('message_activity_insert','message_activity_update'):
                db.execute('DROP TRIGGER IF EXISTS '+name)
            for name in ('message_activity','message_activity_state','message_activity_upgrade'):
                db.execute('DROP TABLE '+name)
        self.host.close();self.host=Host(self.root)

    def test_upgrade_doesnt_hide_a_newer_chat_behind_an_indexed_old_task(self):
        self.host.coding=CodingTasks(self.host)
        task=self.host.coding.collaboration.raw_task('task-current')
        task.update(state='answered',runs=[],tokens=[],messages=[dict(role='assistant',text='Earlier task',created_at=100)])
        self.host.coding.save(task)
        self.message('later-chat')
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO local_projection VALUES (?,?,?)',('later-chat',None,200))
        self.before_upgrade()
        self.assertEqual(self.page()['results'][0]['id'],'later-chat')

    def test_partial_upgrade_keeps_task_order_and_new_saves_across_restart(self):
        self.host.coding=CodingTasks(self.host);coding=self.host.coding
        template=coding.collaboration.raw_task('task-current')
        for i in range(70):
            task=copy.deepcopy(template)
            task.update(id=f'old-{i}',state='answered',runs=[],tokens=[],messages=[dict(role='assistant',text='Saved task',created_at=1000-i)])
            coding.save(task)
        self.before_upgrade()
        first=self.host.dispatch('project_catchup',{**self.scope,'limit':50})
        self.assertTrue(first['indexing']);self.assertEqual(first['results'],[])
        self.host.close();self.host=Host(self.root);self.host.coding=CodingTasks(self.host)
        task=self.host.coding.collaboration.raw_task('old-0')
        task['messages'].append(dict(role='assistant',text='New activity with an earlier clock',created_at=1))
        self.host.coding.save(task)
        page=self.page(limit=50);rows=page['results'][:]
        while page['cursor']:
            page=self.page(limit=50,cursor=page['cursor']);rows+=page['results']
        ids=[r['id'] for r in rows if r['id'].startswith('old-')]
        self.assertEqual(ids,['old-0']+[f'old-{i}' for i in reversed(range(1,70))])

    def test_equal_legacy_positions_keep_every_exact_source_across_pages(self):
        self.host.coding=CodingTasks(self.host)
        template=self.host.coding.collaboration.raw_task('task-current')
        for key in ('tie-a','tie-b'):
            task=copy.deepcopy(template);task['id']=key;self.host.coding.save(task)
        self.page()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE task_activity SET phase=0,activity=0,ordinal=1 WHERE task_id IN ('tie-a','tie-b')")
        page=self.page(limit=1);ids=[r['id'] for r in page['results']]
        while page['cursor']:
            page=self.page(limit=1,cursor=page['cursor']);ids += [r['id'] for r in page['results']]
        self.assertEqual(ids.count('tie-a'),1);self.assertEqual(ids.count('tie-b'),1)


if __name__ == '__main__': unittest.main()
