"""Saved conversation activity shares the task sequence without reading message bodies."""
from task_activity import schema as task_schema, legacy_time, BATCH_RECORDS, RECORD_BYTES, DECODE_BYTES
import math
import json
import sqlite3


def schema(db):
    task_schema(db)
    db.execute('CREATE TABLE IF NOT EXISTS message_activity (message_id TEXT PRIMARY KEY, phase INTEGER NOT NULL, activity REAL NOT NULL, ordinal INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS message_activity_order ON message_activity(phase DESC,activity DESC,ordinal DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS message_activity_merge_order ON message_activity(phase DESC,activity DESC,ordinal DESC,message_id DESC)')
    db.execute('CREATE INDEX IF NOT EXISTS task_activity_merge_order ON task_activity(phase DESC,activity DESC,ordinal DESC,task_id DESC)')
    db.execute('CREATE TABLE IF NOT EXISTS message_activity_state (id INTEGER PRIMARY KEY CHECK(id=1), before_rowid INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO message_activity_state VALUES (1,0)')
    db.execute('CREATE TABLE IF NOT EXISTS message_activity_upgrade (id INTEGER PRIMARY KEY CHECK(id=1), boundary INTEGER NOT NULL, through INTEGER NOT NULL, clock REAL NOT NULL, ready INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO message_activity_upgrade SELECT 1,sequence,0,0,0 FROM task_activity_state WHERE id=1')
    # These triggers share the message transaction, including terminal replies and reconciled replies.
    for event, condition in [('INSERT', ''), ('UPDATE OF text', 'WHEN OLD.text IS NOT NEW.text')]:
        name = 'message_activity_insert' if event == 'INSERT' else 'message_activity_update'
        db.execute(f'''CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON messages {condition}
            BEGIN
              UPDATE task_activity_state SET sequence=sequence+1 WHERE id=1;
              INSERT INTO message_activity VALUES (NEW.id,1,(SELECT sequence FROM task_activity_state WHERE id=1),NEW.rowid)
                ON CONFLICT(message_id) DO UPDATE SET phase=1,activity=excluded.activity;
            END''')


def upgrade_tasks(db):
    """Keep old task order while merging its approximate dates with pre-upgrade messages."""
    state = db.execute('SELECT boundary,through,clock,ready FROM message_activity_upgrade WHERE id=1').fetchone()
    if state['ready']:
        return True
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        db.execute('UPDATE message_activity_upgrade SET ready=1 WHERE id=1')
        return True
    through, clock, decoded = state['through'], state['clock'], 0
    size = 'octet_length(document)' if sqlite3.sqlite_version_info >= (3,43,0) else 'length(CAST(document AS BLOB))'
    for _ in range(BATCH_RECORDS):
        position = db.execute('SELECT task_id,activity FROM task_activity WHERE phase=1 AND activity>? AND activity<=? ORDER BY activity LIMIT 1', (through,state['boundary'])).fetchone()
        if position is None:
            db.execute('UPDATE message_activity_upgrade SET ready=1 WHERE id=1')
            return True
        row = db.execute(f'SELECT {size} AS size,CASE WHEN {size}<=? THEN document END AS document FROM coding_tasks WHERE id=?', (RECORD_BYTES,position['task_id'])).fetchone()
        if row and row['document'] is not None:
            decoded += row['size']
            try:
                task = json.loads(row['document'])
                if isinstance(task, dict):
                    clock = max(clock, legacy_time(task))
            except (ValueError, TypeError, RecursionError, OverflowError):
                pass
        # Equal logical dates retain the original sequence, including backwards clocks.
        # A new save beyond the frozen watermark always keeps its exact new position.
        through = position['activity']
        db.execute('UPDATE task_activity SET phase=0,activity=?,ordinal=? WHERE task_id=? AND phase=1 AND activity=?', (clock,through,position['task_id'],through))
        db.execute('UPDATE message_activity_upgrade SET through=?,clock=? WHERE id=1', (through,clock))
        if decoded > DECODE_BYTES - RECORD_BYTES:
            break
    return False


def prepare(db):
    """Index at most 32 older message IDs per request. Don't invent their creation times."""
    with db:
        if not upgrade_tasks(db):
            return False
        before = db.execute('SELECT before_rowid FROM message_activity_state WHERE id=1').fetchone()[0]
        for _ in range(BATCH_RECORDS):
            row = db.execute('SELECT rowid,id,client_id,role FROM messages WHERE rowid>? ORDER BY rowid LIMIT 1', (before,)).fetchone()
            if row is None:
                return True
            origin = row['client_id'].split(':', 1)[0] if row['role'] == 'assistant' else row['id']
            projection = db.execute('SELECT created_at FROM local_projection WHERE id=?', (origin,)).fetchone()
            timestamp = projection[0] if projection and type(projection[0]) in (int, float) and math.isfinite(projection[0]) and projection[0] >= 0 else 0
            db.execute('INSERT OR IGNORE INTO message_activity VALUES (?,0,?,?)', (row['id'], timestamp, row['rowid']))
            before = row['rowid']
            db.execute('UPDATE message_activity_state SET before_rowid=? WHERE id=1', (before,))
        return False


def position(db, before, has_tasks, has_coordination=False):
    """Merge two indexed seeks. Kind and source ID keep legacy ties reachable."""
    candidates = []
    kinds=[('message',0,'message_activity','message_id')]+([('task',1,'task_activity','task_id')] if has_tasks else [])+([('coordination',2,'coordination_activity','request_id')] if has_coordination else [])
    for kind, rank, table, key in kinds:
        if before is None:
            where, values = '1', ()
        elif rank == before[3]:
            where, values = f'(phase,activity,ordinal,{key})<(?,?,?,?)', (*before[:3],before[4])
        else:
            where, values = '(phase,activity,ordinal)'+('<=' if rank < before[3] else '<')+'(?,?,?)', tuple(before[:3])
        row = db.execute(f'SELECT {key} AS id,phase,activity,ordinal FROM {table} WHERE {where} ORDER BY phase DESC,activity DESC,ordinal DESC,{key} DESC LIMIT 1', values).fetchone()
        if row is not None:
            pos = tuple(row[k] for k in ('phase', 'activity', 'ordinal')) + (rank,row['id'])
            candidates.append((pos, kind, row['id']))
    return max(candidates) if candidates else None
