"""The state spine (8 Oct 2026, plan-v2-state-first.md): one live record per conversation, pushed to the window.

Bryce: "I can see that there are threads running, but I have no clue what they're doing." The window used to piece each
thread's state together from about 30 timers, each re-reading a whole list through the one host pipe, so rows drifted
apart and lagged. Now every write that changes what a conversation's row shows gives that conversation the next number
in one rising order (conversation_activity.bump). ``changes`` answers only the rows past the number the window already
has, and the host writes one short line on its output when the newest number moves (host.serve, ``push_line``), so the
window reads when something changed, not on a timer.

Each record is built from the activity index the task save writes in the same transaction (conversation_activity), so
it never says something the task record doesn't hold, and never from what a model says about itself. ``state`` is one
word: needs_you, working, quiet, unknown, stopped, finished or idle. ``unknown`` is a real answer: a run Dervo lost
sight of (it was running when Dervo last closed) reads as unknown, never as working from memory. ``quiet`` is a run
that's alive but hasn't said anything for QUIET_SECONDS, so a hung run doesn't look busy.
"""
import json
import sys
import time

import conversation_activity

QUIET_SECONDS = 90   # a working run with nothing from its provider for this long reads "quiet"

# Operations that change a conversation's row other than through a task save: (operation, how its threads are found).
# 'channel' is the request's own channelId; 'channels' its channelIds; 'project' every conversation in the request's
# project, found before the operation runs, so a deleted one still moves and reads as gone; 'queue' the conversation of
# the request's queued message. Task saves (conversation_activity.record), auto-naming (conversation_organisation) and
# notes left on a thread (thread_notes) move their rows themselves.
ROW_OPERATIONS = {
    'conversation_archive': 'channel', 'conversation_restore': 'channel', 'conversation_delete': 'channel',
    'conversations_archive': 'project', 'conversations_delete_archived': 'channels', 'conversations_tidy': 'project',
    'conversations_tidy_undo': 'project', 'organisation_set': 'channel', 'organisation_undo': 'channel',
    'conversation_pin': 'channel', 'coding_queue_cancel': 'queue', 'thread_note_pass': 'channel',
    'thread_note_dismiss': 'channel', 'project_archive': 'project', 'project_restore': 'project', 'project_delete': 'project',
    'conversation_model_set': 'channel', 'conversation_profile_set': 'channel', 'conversation_route_set': 'channel',
    'project_model_set': 'project', 'project_profile_set': 'project',
}


def before_operation(db, operation, args):
    """The conversations an operation will change, read before it runs (it may delete them). Never raises."""
    how = ROW_OPERATIONS.get(operation)
    if how is None or not isinstance(args, dict):
        return []
    try:
        if how == 'channel' and isinstance(args.get('channelId'), str):
            return [args['channelId']]
        if how == 'channels' and isinstance(args.get('channelIds'), list):
            return [item for item in args['channelIds'] if isinstance(item, str)]
        if how == 'project' and isinstance(args.get('projectId'), str):
            return [row[0] for row in db.execute('SELECT id FROM threads WHERE project_id=?', (args['projectId'],))]
        if how == 'queue' and isinstance(args.get('queueId'), str) and _table(db, 'coding_queue'):
            return [row[0] for row in db.execute('SELECT thread_id FROM coding_queue WHERE id=?', (args['queueId'],)) if row[0]]
    except Exception as exc:  # noqa: BLE001 - the operation itself still runs
        print(f'thread_states: could not read the rows {operation} changes: {type(exc).__name__}', file=sys.stderr)
    return []


def after_operation(db, thread_ids):
    """Move the rows an operation changed (``before_operation``'s answer). Never fails the operation: the row is a view
    of records already saved. A deleted conversation's row moves too, which is how the window learns it's gone."""
    if not thread_ids:
        return
    try:
        with db:
            for thread_id in dict.fromkeys(thread_ids):
                conversation_activity.bump(db, thread_id)
    except Exception as exc:  # noqa: BLE001 - the operation already succeeded
        print(f'thread_states: could not move {len(thread_ids)} rows: {type(exc).__name__}', file=sys.stderr)


def after_restart(db):
    """Dervo (re)started: every row that read as running moves, so a run lost with the last host reads unknown in a
    window that stayed open, instead of working until something else moves it."""
    try:
        if not _table(db, 'conversation_task_state'):
            return
        with db:
            for (thread_id,) in db.execute("SELECT DISTINCT thread_id FROM conversation_task_state WHERE status='running'").fetchall():
                conversation_activity.bump(db, thread_id)
    except Exception as exc:  # noqa: BLE001 - only the row's freshness is lost
        print(f'thread_states: could not move running rows after a restart: {type(exc).__name__}', file=sys.stderr)


class Alive:
    """When each working run last heard from its provider, kept in memory: a streamed reply sends many events a second,
    and writing each would cost more than the run. The row moves only when the run goes quiet (sweep) or speaks again
    after that, never just because it's still talking (second review, 8 Oct: a move every 5 s per run re-rendered the
    window about once a second with five runs, with nothing on screen changing)."""

    def __init__(self, db, clock=time.time):
        self.db, self.clock = db, clock
        self.seen, self.quiet = {}, set()

    def heard(self, thread_id):
        self.seen[thread_id] = self.clock()
        if thread_id in self.quiet:
            self.quiet.discard(thread_id)
            self._bump(thread_id)  # quiet, then working again

    def forget(self, thread_id):
        """Drop what was heard (a deleted thread). A new run never inherits an old one's silence anyway: state_of
        measures from the later of the last word and the run's own start."""
        self.seen.pop(thread_id, None)
        self.quiet.discard(thread_id)

    def sweep(self, now=None):
        """Move the row of each run that just went quiet, once, so "quiet" reaches the window without a poll."""
        now = self.clock() if now is None else now
        for thread_id, heard in list(self.seen.items()):
            if thread_id not in self.quiet and now - heard >= QUIET_SECONDS:
                self.quiet.add(thread_id)
                self._bump(thread_id)

    def _bump(self, thread_id):
        try:
            with self.db:
                conversation_activity.bump(self.db, thread_id)
        except Exception:  # noqa: BLE001 - liveness is a hint on top of the record
            pass


def state_of(item, alive_at, now):
    """One word for a conversation's row, from its activity summary (conversation_activity.summaries)."""
    if item['lost']:
        return 'unknown'
    if item['status'] == 'needs':
        return 'needs_you'
    if item['running']:
        heard = max((at for at in (alive_at, item['since']) if at is not None), default=None)
        return 'quiet' if heard is not None and now - heard >= QUIET_SECONDS else 'working'
    if item['ended'] in ('stopped', 'restart_paused', 'interrupted'):
        return 'stopped'
    if item['ended']:
        return 'finished'
    return 'idle'


def changes(db, actor, since, alive=None, now=None, project_id=None):
    """{version, threads}: every conversation you can see whose row moved after ``since`` (all of them for 0 or less),
    newest change last. A conversation deleted since then comes back as {id, gone: True}, so the window drops it.
    ``project_id``: only that project's conversations (Dervo's router reads one project a message), and no gone rows."""
    conversation_activity.schema(db)
    now = time.time() if now is None else now
    newest = conversation_activity.version(db)
    since = since if isinstance(since, int) and not isinstance(since, bool) else 0
    archived_threads = 'SELECT thread_id FROM conversation_archives' if _table(db, 'conversation_archives') else 'SELECT NULL WHERE 0'
    archived_projects = 'SELECT project_id FROM project_archives' if _table(db, 'project_archives') else 'SELECT NULL WHERE 0'
    columns = (f't.id, t.project_id, t.visibility, c.name, t.id IN ({archived_threads}) AS archived, '
               f't.project_id IN ({archived_projects}) AS project_archived')
    # The same rule the conversation list reads by: shared or yours, on an account Dervo still has. (Which side an
    # account is on only groups rows there, so it isn't a filter here either: a thread the sidebar shows gets its record.)
    visible = "(t.visibility='shared' OR t.owner=?)" + (' AND t.project_id=?' if project_id is not None else '')
    scoped = (actor,) if project_id is None else (actor, project_id)
    if since <= 0:
        rows = db.execute(f'SELECT {columns}, coalesce(v.version, 0) FROM threads t LEFT JOIN local_channels c ON c.id=t.id '
                          f'LEFT JOIN thread_versions v ON v.thread_id=t.id JOIN profiles p ON p.id=t.profile_id WHERE {visible}',
                          scoped).fetchall()
        gone = []
    else:
        rows = db.execute(f'SELECT {columns}, v.version FROM thread_versions v JOIN threads t ON t.id=v.thread_id '
                          f'LEFT JOIN local_channels c ON c.id=t.id JOIN profiles p ON p.id=t.profile_id WHERE v.version>? AND {visible}',
                          (since,) + scoped).fetchall()
        gone = [] if project_id is not None else [row[0] for row in db.execute('SELECT v.thread_id FROM thread_versions v LEFT JOIN threads t ON t.id=v.thread_id '
                                             'WHERE v.version>? AND t.id IS NULL', (since,)).fetchall()]
    summaries = conversation_activity.summaries(db, [row[0] for row in rows])
    clouds = latest_cloud_runs(db, [row[0] for row in rows])
    seen = alive.seen if alive is not None else {}
    threads = []
    for thread_id, project_id, visibility, name, archived, project_archived, at in sorted(rows, key=lambda row: row[6]):
        item = summaries[thread_id]
        alive_at = seen.get(thread_id) if item['running'] else None
        # ``running`` (a run is live) and ``pending_question`` (what a worker asked you, or None) are always present:
        # the phone shows Stop on a needs row only while one runs (PR #11).
        threads.append(dict(item, pending_question=item.get('pending_question'), pending_question_count=item.get('pending_question_count', 0),
                            id=thread_id, projectId=project_id, name=name, visibility=visibility,
                            archived=bool(archived or project_archived), version=at, aliveAt=alive_at,
                            state=state_of(item, alive_at, now), cloud=clouds.get(thread_id)))
    threads += [{'id': thread_id, 'gone': True} for thread_id in gone]
    return {'version': newest, 'threads': threads}


def latest_cloud_runs(db, thread_ids):
    """{thread id: its newest Send to cloud run}, for Home (PR #12's cloud_runs). Only what Dervo last observed: a run
    it lost sight of says unknown. Empty on a store without cloud runs."""
    if not thread_ids or not _table(db, 'cloud_runs'):
        return {}
    found = {}
    for start in range(0, len(thread_ids), 500):
        part = thread_ids[start:start + 500]
        marks = ','.join('?' * len(part))
        for row in db.execute(f'SELECT conversation_id, id, provider, state, url, branch, detail, created_at, updated_at FROM cloud_runs '
                              f'WHERE conversation_id IN ({marks}) ORDER BY created_at, rowid', part):
            found[row[0]] = dict(id=row[1], provider=row[2], state=row[3], url=row[4], branch=row[5], detail=row[6],
                                 startedAt=row[7], at=row[8])
    return found


def push_line(version):
    """The line the host writes when the newest change moved. Small and fixed in shape: the window reads ``changes``."""
    return json.dumps({'event': {'kind': 'threads', 'version': version}})


def _table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
