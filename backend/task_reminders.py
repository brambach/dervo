"""Reminders from a conversation's owner (beta.16.1, item 1; Bryce, 26 Sep: Home and a macOS notification).

An agent saves a reminder with a date and one line, tied to its thread and task, the way dervo_ask_user saves a
question. On the day Home lists it under what needs you, linked to its thread, and the app posts one macOS notification
that opens the thread (useNotifications.ts). A reminder is only words to show you: it never starts work, sends a
message, queues a request or grants access, and nothing here calls the conversation or coding request paths. Its text
is shown as the agent's words, never as your request. You open the thread and decide.

9 Oct (Bryce and Harrison: Home shows reminders, kept minimal): you can save one too, with Home's "Remind me tomorrow"
on a thread (``reminder_add``). It's the same row with you as the one who asked and no task behind it, one per thread
and day. ``reminders`` also lists what's still to come, so the thread's row can say a reminder is set."""
import datetime
import json
import re
import time
import uuid

from dervo_connections import Rejected
import secret_scan
import voice

TOOL = 'dervo_remind_user'
MAX_PER_TASK, MAX_WAITING, TEXT_CHARS, MAX_DAYS = 5, 50, 160, 366
PROMPT = ('Use dervo_remind_user when the user asks to be reminded, or when something needs their attention on a later '
          'day (a trial ends, a follow-up is due). Give a stable reminder_key, the day as YYYY-MM-DD and one short line. '
          'On that day Dervo shows it on Home and sends a notification that opens this thread. It never starts work, '
          'sends anything or grants access, so say what you want the user to decide. Do not include secrets.\n')
FIELDS = {'reminders': {'projectId', 'profileId'}, 'reminders_due': set(), 'reminder_notified': {'reminderId'},
          'reminder_done': {'projectId', 'profileId', 'reminderId'},
          'reminder_add': {'projectId', 'profileId', 'channelId', 'on', 'text'}}
UPCOMING = 20  # still to come, listed for the rows that show a reminder is set


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_reminders ('
               'id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, task_id TEXT NOT NULL, '
               'reminder_key TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(task_id, reminder_key))')


def today(clock=None):
    """This Mac's date. A reminder is for a day where you are, not a UTC instant."""
    return datetime.date.fromtimestamp(clock if clock is not None else time.time())


def _day(on, now=None):
    """The reminder's day as YYYY-MM-DD: today at the earliest, a year ahead at most."""
    try:
        day = datetime.date.fromisoformat(on) if type(on) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', on) else None
    except ValueError:
        day = None
    if day is None:
        raise Rejected('Give the day as YYYY-MM-DD.')
    start = today(now)
    if day < start:
        raise Rejected(f'That day has passed. Today is {start.isoformat()}.')
    if (day - start).days > MAX_DAYS:
        raise Rejected('A reminder can be at most a year ahead.')
    return day.isoformat()


def _line(text):
    """The reminder's one plain line, without credentials."""
    if type(text) is not str or '\x00' in text or len(text) > TEXT_CHARS * 2:
        raise Rejected(f'Keep the reminder to one line within {TEXT_CHARS} characters.')
    if secret_scan.redact(text) != text:
        raise Rejected('Remove credentials from the reminder.')
    line = ' '.join(voice.plain(text).split())
    if not line:
        raise Rejected('A reminder needs a line to show.')
    if len(line) > TEXT_CHARS:
        raise Rejected(f'Shorten the reminder to {TEXT_CHARS} characters.')
    return line


def validate(args, now=None):
    if type(args) is not dict or set(args) != {'reminder_key', 'on', 'text'}:
        raise Rejected('A reminder has only a reminder_key, a day and one line.')
    key = args['reminder_key']
    if type(key) is not str or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', key):
        raise Rejected('Use a stable reminder key with up to 64 letters, digits, underscores or hyphens.')
    return {'reminder_key': key, 'on': _day(args['on'], now), 'text': _line(args['text'])}


def offered(task):
    import task_questions
    return task_questions.offered(task)


def _rows(db, where, values):
    return [json.loads(row[0]) for row in db.execute(f'SELECT document FROM task_reminders WHERE {where} ORDER BY rowid', values)]


def capture(coding, task, token, args, now=None):
    content = validate(args, now)
    if not offered(task) or task['state'] != 'working':
        raise Rejected('Only the active conversation owner can save a reminder.')
    fresh = coding.read(task['id'], task['context'])
    run = (fresh.get('runs') or [{}])[-1]
    if (fresh['state'] != 'working' or not offered(fresh) or run.get('state') != 'running'
            or token.get('run_id') != run.get('id') or token.get('session_id') != run.get('session_id')):
        raise Rejected('This reminder needs the source task’s current acknowledged run.')
    db, ctx = coding.store.db, fresh['context']
    schema(db)
    rows = _rows(db, 'task_id=?', (task['id'],))
    old = next((row for row in rows if row['reminder_key'] == content['reminder_key']), None)
    if old:
        if (old['on'], old['text']) != (content['on'], content['text']):
            raise Rejected('That reminder key already belongs to a different reminder.')
        return {'reminderId': old['id'], 'on': old['on'], 'detail': 'Reminder already saved. Continue your work.'}
    waiting = db.execute("SELECT COUNT(*) FROM task_reminders WHERE project_id=? AND json_extract(document,'$.state')='waiting'",
                         (ctx['project_id'],)).fetchone()[0]
    if len(rows) >= MAX_PER_TASK or waiting >= MAX_WAITING:
        raise Rejected('This task has reached its reminder limit. Put the rest in your reply.')
    rid = uuid.uuid4().hex
    row = {'id': rid, 'project_id': ctx['project_id'], 'thread_id': ctx['thread_id'], 'task_id': task['id'],
           'actor': ctx['actor'], 'run_id': run['id'], 'tool_token': token['token'], 'reminder_key': content['reminder_key'],
           'asker': 'Claude' if fresh['binding']['provider'] == 'claude' else 'Codex', 'on': content['on'], 'text': content['text'],
           'created_at': time.time() if now is None else now, 'state': 'waiting', 'notified_at': None, 'done_at': None}
    with db:
        db.execute('INSERT INTO task_reminders VALUES (?,?,?,?,?,?)',
                   (rid, ctx['project_id'], ctx['thread_id'], task['id'], row['reminder_key'], json.dumps(row)))
    return {'reminderId': rid, 'on': row['on'],
            'detail': f"Reminder saved for {row['on']}. Dervo shows it on Home that day. It won’t start any work."}


def _visible(db, actor, project_id=None):
    """The threads you can see, with their names and projects: shared ones and your own private ones. An archived
    thread keeps its reminders (opening it brings it back); an archived project's don't show."""
    archived = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_archives'").fetchone()
    rows = db.execute(f'''SELECT t.id, c.name, t.project_id FROM threads t JOIN local_channels c ON c.id=t.id
        WHERE (t.visibility='shared' OR t.owner=?) {"AND t.project_id=?" if project_id else ""}
        {"AND t.project_id NOT IN (SELECT project_id FROM project_archives)" if archived else ""}''',
        (actor, project_id) if project_id else (actor,)).fetchall()
    return {row[0]: {'name': row[1], 'project_id': row[2]} for row in rows}


def view(row, thread, project=None):
    """What the screen gets. ``text`` is the agent's line, labelled with ``asker``; it is never a request of yours. One
    you saved yourself is ``mine``, with no asker and no task."""
    mine = row.get('by') == 'you'
    return {'id': row['id'], 'projectId': row['project_id'], 'channelId': row['thread_id'], 'taskId': row['task_id'] or None,
            'thread': thread['name'], 'project': project, 'asker': None if mine else row['asker'], 'mine': mine,
            'text': row['text'], 'on': row['on'], 'createdAt': row['created_at'], 'notified': row.get('notified_at') is not None}


def _waiting(db, actor, project_id, keep):
    threads = _visible(db, actor, project_id)
    found = [row for row in _rows(db, "json_extract(document,'$.state')='waiting'", ())
             if row['thread_id'] in threads and keep(row['on']) and row['actor'] == actor]
    return sorted(found, key=lambda row: (row['on'], row['created_at'])), threads


def due(db, actor, project_id=None, now=None):
    """Waiting reminders whose day has come, oldest day first."""
    day = today(now).isoformat()
    return _waiting(db, actor, project_id, lambda on: on <= day)


def upcoming(db, actor, project_id, now=None):
    """Waiting reminders still to come in this project, soonest first, at most UPCOMING of them."""
    day = today(now).isoformat()
    rows, threads = _waiting(db, actor, project_id, lambda on: on > day)
    return rows[:UPCOMING], threads


def add(db, scope, actor, thread_id, on, text, now=None):
    """Your own reminder for one of this project's threads, from Home's "Remind me tomorrow". One per thread and day:
    asking again is the same reminder, and one you marked done comes back with the new line. Like an agent's, it's only
    words to show on its day: it never starts work, sends a message or grants access."""
    import pickup
    day, line = _day(on, now), _line(text)
    # The threads Home lists here: shared ones and your own private ones on this project's side, not archived.
    if type(thread_id) is not str or thread_id not in {row['id'] for row in pickup.project_threads(db, scope, actor)}:
        raise Rejected('Choose one of this project’s threads.')
    key, at = f'you:{thread_id}:{day}', time.time() if now is None else now
    record = db.execute("SELECT document FROM task_reminders WHERE task_id='' AND reminder_key=?", (key,)).fetchone()
    row = json.loads(record[0]) if record else None
    if row and row['state'] == 'waiting':
        return row, False
    # A done one brought back counts against the cap like a new one.
    waiting = db.execute("SELECT COUNT(*) FROM task_reminders WHERE project_id=? AND json_extract(document,'$.state')='waiting'",
                         (scope['project_id'],)).fetchone()[0]
    if waiting >= MAX_WAITING:
        raise Rejected(f'This project already has {MAX_WAITING} reminders waiting. Mark some done first.')
    if row:
        row.update(state='waiting', text=line, created_at=at, notified_at=None, done_at=None)
        _save(db, row)
        return row, True
    row = {'id': uuid.uuid4().hex, 'project_id': scope['project_id'], 'thread_id': thread_id, 'task_id': '', 'actor': actor,
           'run_id': None, 'tool_token': None, 'reminder_key': key, 'by': 'you', 'asker': None, 'on': day, 'text': line,
           'created_at': at, 'state': 'waiting', 'notified_at': None, 'done_at': None}
    with db:
        db.execute('INSERT INTO task_reminders VALUES (?,?,?,?,?,?)', (row['id'], row['project_id'], thread_id, '', key, json.dumps(row)))
    return row, True


def _find(db, rid):
    if type(rid) is not str or not re.fullmatch('[a-f0-9]{32}', rid):
        raise Rejected('Choose a saved reminder.')
    record = db.execute('SELECT document FROM task_reminders WHERE id=?', (rid,)).fetchone()
    if record is None:
        raise Rejected('That reminder isn’t saved any more.')
    return json.loads(record[0])


def _save(db, row):
    with db:
        db.execute('UPDATE task_reminders SET document=? WHERE id=?', (json.dumps(row), row['id']))


def dispatch(host, operation, args, actor, now=None):
    if not isinstance(args, dict) or set(args) != FIELDS[operation]:
        raise Rejected('Unsupported reminder fields')
    db = host.store.db
    schema(db)
    if operation == 'reminders':
        from project_record_scope import project_record_scope
        project_record_scope(host.store, args['projectId'], args['profileId'])
        rows, threads = due(db, actor, args['projectId'], now)
        later, _ = upcoming(db, actor, args['projectId'], now)
        return {'due': [view(row, threads[row['thread_id']]) for row in rows],
                'upcoming': [view(row, threads[row['thread_id']]) for row in later]}
    if operation == 'reminder_add':
        from project_record_scope import project_record_scope
        scope = project_record_scope(host.store, args['projectId'], args['profileId'])
        row, added = add(db, scope, actor, args['channelId'], args['on'], args['text'], now)
        return {**view(row, _visible(db, actor, args['projectId'])[row['thread_id']]), 'added': added}
    if operation == 'reminders_due':
        from next_up import project_name
        rows, threads = due(db, actor, None, now)
        return {'due': [view(row, threads[row['thread_id']], project=project_name(db, row['project_id'])) for row in rows]}
    row = _find(db, args['reminderId'])
    if row['actor'] != actor or row['thread_id'] not in _visible(db, actor):
        raise Rejected('That reminder belongs to a chat you can’t see.')
    if operation == 'reminder_notified':
        if row.get('notified_at') is None:
            row['notified_at'] = time.time() if now is None else now
            _save(db, row)
        return {'id': row['id'], 'notified': True}
    from project_record_scope import project_record_scope
    project_record_scope(host.store, args['projectId'], args['profileId'])
    if row['project_id'] != args['projectId']:
        raise Rejected('That reminder belongs to another project.')
    if row['state'] == 'waiting':
        row.update(state='done', done_at=time.time() if now is None else now)
        _save(db, row)
    return {'id': row['id'], 'state': row['state']}

