"""What happened to the work Home sent, said in Home as it happens (9 Oct 2026, Bryce: "Want it super fluid just like
Claude projects."). Before this, Home summarised what finished only when you opened it, at most once a launch, and the
chat itself never said "the picker is done" or "billing needs you".

Every round of the host (Host.tick) reads the state spine (thread_states) past the last number it saw. For each thread
that moved, the jobs Home sent there in the last NEWS_WINDOW are found by their own request ids (``{reply}-{index}``,
the clientId Home sends each job with), and each one's own task is read. A task that has just reached one of these gets
one line in the request that sent it, as Home's own ready reply with no model call:

- needs you: a question ("Billing has a question for you: ..."), a plan waiting for your go-ahead, a result ready for
  your review, a run that stopped at a usage limit, a run Dervo lost track of;
- failed: a run that failed or couldn't start;
- done: a run that finished with nothing waiting on you, or that Dervo committed by itself.

Only Home's own jobs: work you start in the thread yourself never posts into Home. Never for what you did yourself
(approved, stopped), and never twice for the same moment of the same task. The first round after a launch only marks
where it starts: what moved while Dervo was closed is the arrival summary's to tell.

The lines are the listing's only (Orchestrator._entries leaves them out unless asked), so a line that lands after an
offer or a question never becomes the reply a yes answers, and Home's model doesn't read them back as its own words.
"""
import json
import sys
import time
import uuid

NEWS_WINDOW = 24 * 3600   # seconds after Home sends a job that its thread's outcomes come back to Home
LINE_LIMIT = 160          # characters of a question or a failure's summary quoted in a line


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS home_news_seen (task_id TEXT PRIMARY KEY, moment TEXT NOT NULL)')


def one_line(text, limit):
    said = ' '.join(str(text or '').split())
    return said if len(said) <= limit else said[:limit - 1].rstrip() + '…'


def name_of(row):
    return one_line(row.get('name') or 'A thread', 60).rstrip(' .?!…') or 'A thread'


def line(task, name, question=None):
    """(kind, sentence) for a Home job's task worth saying, or None. kind is 'needs_you', 'done' or 'failed'."""
    import conversation_activity
    status, ended = conversation_activity.status_of(task), conversation_activity.end_of(task)
    if status == 'running' and not question:
        return None
    summary = one_line(conversation_activity.summary_of(task), LINE_LIMIT).rstrip('.')
    because = f': {summary}.' if summary else '.'
    if question:
        return ('needs_you', f'{name} has a question for you: "{one_line(question, LINE_LIMIT)}"')
    if ended == 'approved':
        commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
        return ('done', f'{name} is done, and Dervo committed it.') if commit.get('how') == 'automatic' and commit.get('sha') else None
    return {'asking': ('needs_you', f'{name} has a question for you.'),
            'needs_permission': ('needs_you', f'{name} needs your go-ahead to start.'),
            'ready_for_review': ('needs_you', f'{name} is done and ready for your review.'),
            'blocked': ('failed', f'{name} failed{because}'),
            'disconnected': ('needs_you', f'Dervo lost track of {name}. Open it to see where it got to.'),
            'limit': ('needs_you', f'{name} stopped at a usage limit.'),
            'not_started': ('failed', f'{name} couldn’t start{because}'),
            'snag': ('needs_you', f'{name} finished, but a step after it didn’t complete.'),
            'completed': ('done', f'{name} is done.'), 'answered': ('done', f'{name} is done.'),
            'idle': ('done', f'{name} is done.')}.get(ended)  # stopped, left, restart_paused: you did that, or Dervo said so


def asked(db, task_id):
    """(how many questions this task asks you now, the newest one's words)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_questions'").fetchone():
        return 0, None
    rows = [json.loads(row[0]) for row in db.execute('SELECT document FROM task_questions WHERE task_id=? ORDER BY rowid', (task_id,))]
    waiting = [row for row in rows if row.get('state') == 'pending']
    return len(waiting), (waiting[-1].get('ask') if waiting else None)


def jobs(db, thread_id, project_id, since):
    """Home's jobs sent to this thread since ``since``: [(entry row, document, part)], oldest first."""
    found = db.execute(
        "SELECT e.id, e.project_id, e.profile_id, e.created_at, e.document, p.value FROM orchestrator_entries e, "
        "json_each(e.document, '$.parts') p WHERE e.project_id=? AND e.created_at>=? AND coalesce(json_extract(p.value, "
        "'$.channel_id'), json_extract(p.value, '$.conversation.id'))=? AND json_extract(p.value, '$.state')='sent' "
        "ORDER BY e.created_at", (project_id, since, thread_id)).fetchall()
    return [(row, json.loads(row['document']), json.loads(row[5])) for row in found]


class News:
    """Home's live lines. One per host; ``tick`` is cheap when nothing moved (one read of the spine's number)."""

    def __init__(self, host, actor):
        self.host, self.actor = host, actor
        self.version = None

    def tick(self, now=None):
        db = self.host.store.db
        import conversation_activity
        import thread_states
        conversation_activity.schema(db)
        newest = conversation_activity.version(db)
        if self.version is None:
            with db:
                schema(db)
            # Each launch starts from where the spine is now: what moved while Dervo was closed is the arrival
            # summary's to tell, and an old move must never read as just now. Kept in memory only, so an idle round
            # writes nothing (Host.tick_wait).
            self.version = newest
            return []
        if newest == self.version:
            return []
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='orchestrator_entries'").fetchone():
            self.version = newest  # Home has never sent anything here, so nothing it sent can have moved
            return []
        rows = thread_states.changes(db, self.actor, self.version)['threads']
        self.version = newest
        said = []
        now = time.time() if now is None else now
        for row in rows:
            if row.get('gone') or row.get('archived'):
                continue
            try:
                posted = self.say(db, row, now)
            except Exception as exc:  # noqa: BLE001 - a line Home can't write never stops the host's round
                print(f'Home could not say what happened in a thread: {type(exc).__name__}: {exc}', file=sys.stderr)
                posted = []
            said += posted
        return said

    def say(self, db, row, now):
        """The lines this thread's move gives Home's jobs there. Returns the new entries' ids."""
        said = []
        for entry, document, part in jobs(db, row['id'], row['projectId'], now - NEWS_WINDOW):
            if not document.get('request_id') or not isinstance(part.get('index'), int):
                continue
            found = db.execute('SELECT task_id FROM coding_requests WHERE thread_id=? AND client_id=?',
                               (row['id'], f"{entry['id']}-{part['index']}")).fetchone()
            record = found and db.execute('SELECT document FROM coding_tasks WHERE id=?', (found[0],)).fetchone()
            if not record:
                continue  # not started yet (queued behind a run), or gone
            task = json.loads(record[0])
            count, question = asked(db, task['id'])
            name = name_of(row)
            found = line(task, name, question)
            if found is None:
                continue
            import conversation_activity
            key = f"{conversation_activity.status_of(task)}:{conversation_activity.end_of(task)}:{count}"
            seen = db.execute('SELECT moment FROM home_news_seen WHERE task_id=?', (task['id'],)).fetchone()
            if seen is not None and seen[0] == key:
                continue
            kind, text = found
            news = dict(kind='reply', state='ready', arrival=False, text=text, parts=[], looked=[], mentions=[], detail=None,
                        refs={}, changes=[], notes=[], request_id=document['request_id'],
                        news=dict(kind=kind, thread=dict(id=row['id'], name=row.get('name') or ''), task_id=task['id'], at=now))
            news_id = str(uuid.uuid4())
            with db:
                db.execute('INSERT OR REPLACE INTO home_news_seen VALUES (?, ?)', (task['id'], key))
                db.execute('INSERT INTO orchestrator_entries VALUES (?,?,?,?,?,?)',
                           (news_id, entry['project_id'], entry['profile_id'], None, now, json.dumps(news)))
            said.append(news_id)
        return said
