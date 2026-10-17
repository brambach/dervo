"""Durable Catch up order, independent of task identity and settlement order."""
import hashlib
import json
import math
import sqlite3

RECORD_BYTES = 256 * 1024
BATCH_RECORDS = 32
DECODE_BYTES = 2 * 1024 * 1024


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_activity (task_id TEXT PRIMARY KEY, phase INTEGER NOT NULL, activity REAL NOT NULL, ordinal INTEGER NOT NULL, signature TEXT)')
    db.execute('CREATE INDEX IF NOT EXISTS task_activity_order ON task_activity(phase DESC, activity DESC, ordinal DESC)')
    db.execute('CREATE TABLE IF NOT EXISTS task_activity_state (id INTEGER PRIMARY KEY CHECK(id=1), before_rowid INTEGER NOT NULL, sequence INTEGER NOT NULL, ready INTEGER NOT NULL)')
    db.execute('INSERT OR IGNORE INTO task_activity_state VALUES (1,0,0,0)')


def signature(task):
    # Currentness checks and approval metadata mustn't move old answers above new work.
    messages = task.get('messages') or []
    runs = task.get('runs') or []
    state = task.get('state')
    content = [messages, [[run.get('id'), run.get('state')] for run in runs if isinstance(run, dict)],
               state if state in ('working', 'blocked', 'disconnected', 'stopping') else 'settled']
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def legacy_time(task):
    values = [item.get('created_at') for key in ('messages', 'runs') for item in (task.get(key) or []) if isinstance(item, dict)]
    return max([0] + [value for value in values if type(value) in (int, float) and math.isfinite(value) and value >= 0])


def save_task(db, task, document):
    """Save the task and its order inside the caller's transaction."""
    digest = signature(task)
    old = db.execute('SELECT signature FROM task_activity WHERE task_id=?', (task['id'],)).fetchone()
    if old is None:
        previous = db.execute('SELECT rowid,document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        if previous:
            saved = json.loads(previous['document'])
            prior = signature(saved)
            db.execute('INSERT INTO task_activity VALUES (?,0,?,?,?)', (task['id'], legacy_time(saved), previous['rowid'], prior))
            old = (prior,)
    db.execute('INSERT INTO coding_tasks VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document', (task['id'], document))
    if old and old[0] == digest:
        return
    db.execute('UPDATE task_activity_state SET sequence=sequence+1 WHERE id=1')
    sequence = db.execute('SELECT sequence FROM task_activity_state WHERE id=1').fetchone()[0]
    ordinal = db.execute('SELECT rowid FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0]
    db.execute('INSERT INTO task_activity VALUES (?,1,?,?,?) ON CONFLICT(task_id) DO UPDATE SET phase=1,activity=excluded.activity,signature=excluded.signature',
               (task['id'], sequence, ordinal, digest))


def prepare(db):
    """Index at most 32 legacy records per request. New saves already carry activity order."""
    with db:
        schema(db)
        state = db.execute('SELECT before_rowid,ready FROM task_activity_state WHERE id=1').fetchone()
        if state['ready'] and db.execute('SELECT 1 FROM coding_tasks WHERE rowid>? LIMIT 1', (state['before_rowid'],)).fetchone() is None:
            return True
        # Keep individual decoded records bounded; giant/corrupt rows stay inspectable
        # only through the existing saved-search limits, with no invented timestamp.
        before = state['before_rowid']
        decoded = 0
        size = 'octet_length(document)' if sqlite3.sqlite_version_info >= (3,43,0) else 'length(CAST(document AS BLOB))'
        for _ in range(BATCH_RECORDS):
            row = db.execute(f'''SELECT rowid,id,{size} AS size,CASE WHEN {size}<=? THEN document END AS document
                                FROM coding_tasks WHERE rowid>? ORDER BY rowid LIMIT 1''', (RECORD_BYTES, before)).fetchone()
            if row is None:
                db.execute('UPDATE task_activity_state SET ready=1,before_rowid=? WHERE id=1', (before,))
                return True
            timestamp, digest = 0, None
            if row['document'] is not None:
                decoded += row['size']
                try:
                    task = json.loads(row['document'])
                    if isinstance(task, dict):
                        timestamp, digest = legacy_time(task), signature(task)
                except (ValueError, TypeError, RecursionError, OverflowError):
                    pass
            db.execute('INSERT OR IGNORE INTO task_activity VALUES (?,0,?,?,?)', (row['id'], timestamp, row['rowid'], digest))
            before = row['rowid']
            if decoded > DECODE_BYTES - RECORD_BYTES:
                break
        db.execute('UPDATE task_activity_state SET before_rowid=?,ready=0 WHERE id=1', (before,))
        return False
