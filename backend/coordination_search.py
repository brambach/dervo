"""Scoped saved text-only requests. Reading them never opens the coordinator runtime."""
import datetime
import hashlib
import json
import math
import sqlite3
from task_activity import BATCH_RECORDS, RECORD_BYTES, DECODE_BYTES

BINDING_KEYS = ('profile_id','identity','provider','model','cwd','visibility')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS coordination_activity (request_id TEXT PRIMARY KEY, phase INTEGER NOT NULL, activity REAL NOT NULL, ordinal INTEGER NOT NULL, signature TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS coordination_activity_order ON coordination_activity(phase DESC,activity DESC,ordinal DESC,request_id DESC)')
    db.execute('CREATE TABLE IF NOT EXISTS coordination_activity_state (id INTEGER PRIMARY KEY CHECK(id=1), before_rowid INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO coordination_activity_state VALUES (1,0)')


def signature(request):
    return hashlib.sha256(json.dumps([request.get(k) for k in ('text','state','error','linked_return','delegations')],sort_keys=True).encode()).hexdigest()


def returned_time(returned, fallback=0):
    """Use a saved timezone-aware date, never the current clock."""
    if isinstance(returned,dict) and isinstance(returned.get('returned_at'),str):
        try:
            date=datetime.datetime.fromisoformat(returned['returned_at'].replace('Z','+00:00'))
            if date.tzinfo is not None:
                value=date.timestamp()
                if math.isfinite(value) and value>=0:return value
        except (ValueError,OverflowError,OSError):pass
    return fallback


def timestamp(request):
    created=request.get('created_at')
    value=created if type(created) in (int,float) and math.isfinite(created) and created>=0 else 0
    delegations=request.get('delegations')
    for returned in [request.get('linked_return')] + (delegations if isinstance(delegations,list) else []):
        value=max(value,returned_time(returned))
    return value


def save(db, request):
    """Save content and meaningful activity inside the caller's transaction."""
    digest=signature(request)
    old=db.execute('SELECT signature FROM coordination_activity WHERE request_id=?',(request['id'],)).fetchone()
    if old is None:
        previous=db.execute('SELECT rowid,document FROM harness_requests WHERE id=?',(request['id'],)).fetchone()
        if previous:
            saved=json.loads(previous['document']);prior=signature(saved)
            db.execute('INSERT INTO coordination_activity VALUES (?,0,?,?,?)',(request['id'],timestamp(saved),previous['rowid'],prior))
            old=(prior,)
    db.execute('INSERT INTO harness_requests VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',(request['id'],request['thread_id'],json.dumps(request)))
    if old and old[0]==digest:return
    db.execute('UPDATE task_activity_state SET sequence=sequence+1 WHERE id=1')
    sequence=db.execute('SELECT sequence FROM task_activity_state WHERE id=1').fetchone()[0]
    ordinal=db.execute('SELECT rowid FROM harness_requests WHERE id=?',(request['id'],)).fetchone()[0]
    db.execute('INSERT INTO coordination_activity VALUES (?,1,?,?,?) ON CONFLICT(request_id) DO UPDATE SET phase=1,activity=excluded.activity,signature=excluded.signature',(request['id'],sequence,ordinal,digest))


def prepare(db):
    with db:
        before=db.execute('SELECT before_rowid FROM coordination_activity_state WHERE id=1').fetchone()[0]
        size='octet_length(document)' if sqlite3.sqlite_version_info>=(3,43,0) else 'length(CAST(document AS BLOB))'
        decoded=0
        for _ in range(BATCH_RECORDS):
            row=db.execute(f'SELECT rowid,id,{size} AS size,CASE WHEN {size}<=? THEN document END AS document FROM harness_requests WHERE rowid>? ORDER BY rowid LIMIT 1',(RECORD_BYTES,before)).fetchone()
            if row is None:return True
            at,digest=0,None
            if row['document'] is not None:
                decoded+=row['size']
                try:
                    saved=json.loads(row['document'])
                    if isinstance(saved,dict):at,digest=timestamp(saved),signature(saved)
                except (ValueError,TypeError,RecursionError,OverflowError):pass
            db.execute('INSERT OR IGNORE INTO coordination_activity VALUES (?,0,?,?,?)',(row['id'],at,row['rowid'],digest))
            before=row['rowid']
            db.execute('UPDATE coordination_activity_state SET before_rowid=? WHERE id=1',(before,))
            if decoded>DECODE_BYTES-RECORD_BYTES:break
        return False


def messages(request):
    text=request.get('text')
    if not isinstance(text,str):raise ValueError('Malformed coordination request')
    rows=[dict(role='user',text=text)]
    returned=[]
    answer=request.get('linked_return')
    if answer is not None:
        if not isinstance(answer,dict) or not isinstance(answer.get('summary'),str):raise ValueError('Malformed coordination answer')
        returned.append((returned_time(answer),1,0,answer['summary']))
    delegations=request.get('delegations') or []
    if not isinstance(delegations,list):raise ValueError('Malformed coordination helpers')
    for index,item in enumerate(delegations):
        if not isinstance(item,dict):raise ValueError('Malformed coordination helper')
        if item.get('status')=='returned' and item.get('result'):
            result=item['result'];handle=item.get('handle')
            if not isinstance(result,dict) or not isinstance(result.get('summary'),str) or not isinstance(handle,str):raise ValueError('Malformed helper answer')
            returned.append((returned_time(item),0,index,handle+' answered: '+result['summary']))
    # Missing dates sort first. At equal dates the owner's answer follows helpers.
    rows.extend(dict(role='assistant',text=item[3]) for item in sorted(returned))
    return rows
