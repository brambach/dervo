"""Real saved-record paths in disposable stores; no providers or file tools."""
import copy
import json
import unittest
from unittest.mock import patch
from host import Host
from coding_integration import CodingTasks
from dervo_connections import Rejected
import test_workspace_search as fixtures
import task_activity


class TaskActivityTests(unittest.TestCase):
    setUp = fixtures.WorkspaceSearchTests.setUp
    tearDown = fixtures.WorkspaceSearchTests.tearDown

    def catchup(self, **extra):
        return self.host.dispatch('project_catchup', {**self.scope, 'limit':5, **extra})

    def ready(self, **extra):
        for _ in range(100):
            page = self.catchup(**extra)
            if not page.get('indexing'):
                return page
        self.fail('Activity preparation never completed')

    def template(self):
        task = json.loads(self.host.store.db.execute("SELECT document FROM coding_tasks WHERE id='task-current'").fetchone()[0])
        task.update(state='answered', runs=[], tokens=[], acceptance=dict(required=False))
        return task

    def coding(self):
        self.host.coding = CodingTasks(self.host)
        return self.host.coding

    def test_continued_older_task_leads_across_pages_and_keeps_exact_identity(self):
        coding = self.coding()
        old = self.template()
        coding.save(old)
        first = self.ready()
        old_source = first['results'][0]['source']
        ordinal = self.host.store.db.execute('SELECT rowid FROM coding_tasks WHERE id=?', (old['id'],)).fetchone()[0]
        for number in range(26):
            newer = copy.deepcopy(old)
            newer.update(id=f'newer-{number}', messages=[dict(role='assistant',text=f'New answer {number}',created_at=200)])
            coding.save(newer)
        old['messages'] += [dict(role='user',text='Shorten the invitation',created_at=100),dict(role='assistant',text='The latest shortened invitation',created_at=100)]
        coding.save(old)
        page = self.ready()
        self.assertEqual(page['results'][0]['id'], old['id'])
        self.assertEqual(self.host.store.db.execute('SELECT rowid FROM coding_tasks WHERE id=?', (old['id'],)).fetchone()[0], ordinal)
        opened = self.host.dispatch('workspace_source', {**self.scope,'source':page['results'][0]['source']})
        self.assertEqual(opened['text'], 'The latest shortened invitation')
        with self.assertRaises(Rejected):
            self.host.dispatch('workspace_source', {**self.scope,'source':old_source})
        ids = [row['id'] for row in page['results']]
        while page['cursor']:
            page = self.catchup(cursor=page['cursor'])
            ids.extend(row['id'] for row in page['results'])
        self.assertEqual(ids, [old['id']] + [f'newer-{n}' for n in reversed(range(26))] + ['literal','msg-other','task-other','msg-shared','msg-current'])
        self.assertEqual(len(ids), len(set(ids)))

    def test_unchanged_saves_and_currentness_metadata_dont_move_old_work(self):
        coding = self.coding()
        old = self.template(); coding.save(old)
        newer = copy.deepcopy(old); newer['id']='newer'; coding.save(newer)
        self.ready()
        before = [tuple(row) for row in self.host.store.db.execute('SELECT * FROM task_activity ORDER BY task_id')]
        old.update(completion=dict(stale=True), acceptance=dict(required=True), error='Files changed since this answer')
        for _ in range(5): coding.save(old)
        after = [tuple(row) for row in self.host.store.db.execute('SELECT * FROM task_activity ORDER BY task_id')]
        self.assertEqual(before, after)
        self.assertEqual(self.ready()['results'][0]['id'], 'newer')
        self.host.close(); self.host = Host(self.root); self.coding()
        self.assertEqual(self.ready()['results'][0]['id'], 'newer')

    def test_unchanged_legacy_save_before_preparation_keeps_saved_chronology(self):
        db=self.host.store.db; old=self.template()
        old['messages']=[dict(role='assistant',text='Older answer',created_at=100)]
        newer={**old,'id':'newer-legacy','messages':[dict(role='assistant',text='Newer answer',created_at=200)]}
        db.execute("UPDATE coding_tasks SET document=? WHERE id='task-current'",(json.dumps(old),))
        db.execute('INSERT INTO coding_tasks VALUES (?,?)',(newer['id'],json.dumps(newer))); db.commit()
        coding=self.coding()
        old.update(completion=dict(stale=True),error='Files changed since this answer')
        coding.save(old)
        self.assertEqual(self.ready()['results'][0]['id'],'newer-legacy')
        old['messages'].append(dict(role='assistant',text='A new answer with an earlier clock',created_at=1))
        coding.save(old)
        self.assertEqual(self.ready()['results'][0]['id'],'task-current')

    def test_concurrent_save_invalidates_page_and_same_second_activity_is_ordered(self):
        coding=self.coding(); task=self.template()
        for number in range(7):
            task=copy.deepcopy(task); task['id']=f'task-{number}'
            task['messages']=[dict(role='assistant',text='Same second',created_at=100)]
            coding.save(task)
        page=self.ready()
        self.assertEqual([row['id'] for row in page['results']], [f'task-{n}' for n in range(6,1,-1)])
        task['messages'].append(dict(role='assistant',text='Clock moved backwards',created_at=1)); coding.save(task)
        with self.assertRaises(Rejected): self.catchup(cursor=page['cursor'])
        self.assertEqual(self.ready()['results'][0]['id'], task['id'])

    def test_legacy_preparation_is_bounded_resumable_and_uses_saved_times(self):
        db=self.host.store.db; template=self.template()
        for number in range(80):
            task={**template,'id':f'legacy-{number}','messages':[dict(role='assistant',text='Saved answer',created_at=1000-number)]}
            db.execute('INSERT INTO coding_tasks VALUES (?,?)',(task['id'],json.dumps(task)))
        db.commit()
        trace=[]; db.set_trace_callback(trace.append)
        page=self.catchup(); db.set_trace_callback(None)
        self.assertTrue(page['indexing']); self.assertEqual(page['results'],[])
        reads=[sql for sql in trace if 'FROM coding_tasks WHERE rowid>' in sql]
        self.assertEqual(len(reads), task_activity.BATCH_RECORDS)
        self.assertFalse(hasattr(self.host,'coding'))
        before=db.execute('SELECT before_rowid FROM task_activity_state').fetchone()[0]
        self.host.close(); self.host=Host(self.root)
        self.assertEqual(self.host.store.db.execute('SELECT before_rowid FROM task_activity_state').fetchone()[0],before)
        page=self.ready()
        self.assertEqual([row['id'] for row in page['results']], [f'legacy-{n}' for n in range(5)])
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM coding_tasks').fetchone()[0],84)

    def test_legacy_decode_budget_and_private_exclusion_survive_preparation(self):
        db=self.host.store.db; template=self.template()
        for number in range(15):
            task={**template,'id':f'large-{number}','messages':[dict(role='assistant',text='x'*230000)]}
            db.execute('INSERT INTO coding_tasks VALUES (?,?)',(task['id'],json.dumps(task)))
        db.commit()
        decoded=[]; loads=json.loads
        with patch.object(task_activity.json,'loads',side_effect=lambda value: (decoded.append(len(value.encode())),loads(value))[1]):
            page=self.catchup()
        self.assertTrue(page['indexing'])
        self.assertLessEqual(sum(decoded),task_activity.DECODE_BYTES)
        page=self.ready()
        ids=[row['id'] for row in page['results']]
        while page['cursor']:
            page=self.catchup(cursor=page['cursor']); ids.extend(row['id'] for row in page['results'])
        self.assertFalse({'task-private','task-work'} & set(ids))
        self.assertIn('task-other', ids, 'another personal account of this Mac is in scope (handoff map H1, 19 Sep)')


if __name__ == '__main__': unittest.main()
