"""Pending question cues from saved records. Home polling never opens a provider or project file."""
import json


def pending(db, newest_task):
    if not newest_task or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_questions'").fetchone():
        return {}
    from task_questions import BINDING_KEYS
    from thread_fanout import access_stamp
    marks = ','.join('?' * len(newest_task))
    rows = db.execute(
        f"SELECT q.thread_id,q.document,k.document,t.profile_id,t.model,t.visibility,t.owner,"
        f"p.provider,p.identity,p.method,j.cwd FROM task_questions q "
        f"JOIN coding_tasks k ON k.id=q.task_id JOIN threads t ON t.id=q.thread_id AND t.project_id=q.project_id "
        f"JOIN profiles p ON p.id=t.profile_id JOIN projects j ON j.id=t.project_id "
        f"WHERE q.task_id IN ({marks}) AND json_extract(q.document,'$.state')='pending' ORDER BY q.rowid",
        list(newest_task.values()))
    found, stamps = {}, {}
    for thread, document, task_document, profile_id, model, visibility, owner, provider, identity, method, cwd in rows:
        question, task = json.loads(document), json.loads(task_document)
        ctx = task.get('context') or {}
        run = (task.get('runs') or [{}])[-1]
        if (question['task_id'] != newest_task.get(thread) or question['run_id'] != run.get('id')
                or ctx != dict(project_id=question['project_id'], thread_id=thread, actor=question['actor'])
                or bool(task.get('read_only')) != question['read_only']):
            continue
        if not ((task.get('state') == 'working' and run.get('state') == 'running')
                or (task.get('state') in ('answered', 'ready_for_review', 'completed') and run.get('state') == 'completed')):
            continue
        binding = dict(profile_id=profile_id, model=model, visibility=visibility, owner=owner,
                       provider=provider, identity=identity, method=method, cwd=cwd)
        if any(binding.get(key) != question['binding'].get(key) for key in BINDING_KEYS):
            continue
        pid = question['project_id']
        if pid not in stamps:
            stamps[pid] = access_stamp(db, pid)
        if stamps[pid] != question['access_stamp']:
            continue
        found.setdefault(thread, []).append(dict(ask=question['ask'], at=question['created_at']))
    return found
