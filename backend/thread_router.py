"""Where Home's thread decisions meet the host (8 Oct 2026, Bryce: "very very smart at the thread creation, auto sorting,
auto naming, and auto deletion. It all needs to be very smooth").

The decisions live in ``thread_routing`` (the "Make thread routing smart" lane, PR #18, with its evaluation set in
evals/routing): pure, standard library only, no model calls, no reading or writing. This file is the host's half: it
builds the records the module reads from Dervo's own, calls it, checks its answer and logs every decision. It changes
nothing itself; each caller applies an answer through the path that thing already takes, with every rule that path
already has:

- route: project_orchestrator._start, after every check that answers a message in code. A sure answer becomes the job
  Home's model would have written, applied by apply() with all its holds, so only the model call is skipped.
- name: Organisation.submitted and tidy_once, where the rules name a thread, and only where they may.
- tidy: project_lifecycle.tidy, as candidates that must pass quiet_threads, put away in the launch's batch with its Undo.
- sort: the ``thread_order`` operation, ordering only.

Every call is optional. Without the module, or when it raises or answers something malformed, the caller does what it
did before. Below SUGGEST a decision is never applied.

The decision log (``routing_decisions`` in the store, on this Mac only, since it holds message text) keeps what the
module saw, what it decided and, later, what you did about it: kept, moved:<thread|new|home>, renamed:<title> or
unarchived. Each decision carries its project's side, Work or Personal, and ``export`` writes one file a side
(routing-decisions-personal.jsonl, routing-decisions-work.jsonl) as evals/routing/learn.py reads it. What it keeps is
cut short and secret-scanned, and deleting a thread or project deletes every decision that read it (forget).
"""
import importlib
import json
import os
import sys
import tempfile
import time
import uuid

# Used when the module doesn't export its own.
AUTO_ROUTE = 0.75
SUGGEST = 0.5
KINDS = ('existing', 'new', 'home', 'unsure')
COMMANDS = ('push', 'approve', 'stop', 'archive', 'delete', 'rename', 'pin', 'unpin', 'restore')
BUCKETS = ('needs_you', 'working', 'finished', 'idle', 'archived')
TIDY_ACTIONS = ('archive', 'unarchive', 'pin')
HISTORY = 12          # Home messages the module reads, newest last
MESSAGES = 3          # your messages per thread, oldest first
LOG_KEEP = 1000       # decisions kept per project, oldest dropped first (each about 10 KB at most: ids and state, no thread text)
LOG_KEEP_ALL = 2000   # and across every project
LOG_THREADS = 40      # thread records a logged decision keeps: the ones it names, then the most recently active
LOG_TEXT = 300        # characters of each string a logged decision keeps outside the message routed
EXPORT_EVERY = 60     # seconds between exports of the log, at most, and only after an outcome changed it
TEXT_LIMIT = 2000     # characters of a message kept
SLOW_MS = 50          # a call slower than this is reported, so a regression shows in the host log
EXPORT_NAME = 'routing-decisions-{side}.jsonl'
SIDES = ('personal', 'work')

_module = None


def module():
    """``thread_routing``, or None while it isn't installed or failed to load. Loaded once per host."""
    global _module
    if _module is None:
        try:
            _module = importlib.import_module('thread_routing')
        except Exception as exc:  # noqa: BLE001 - routing falls back to Home's model and the rules
            if not isinstance(exc, ModuleNotFoundError):
                print(f'thread_routing failed to load: {type(exc).__name__}: {exc}', file=sys.stderr)
            _module = False
    return _module or None


def name_threshold():
    """The confidence a name needs to replace the rules' one: the module's NAME_APPLY, else AUTO_ROUTE."""
    found = getattr(module(), 'NAME_APPLY', None)
    return found if _unit(found) else thresholds()[0]


def thresholds():
    """(AUTO_ROUTE, SUGGEST), the module's own when it exports sane ones."""
    found = module()
    auto, suggest = getattr(found, 'AUTO_ROUTE', AUTO_ROUTE), getattr(found, 'SUGGEST', SUGGEST)
    return (auto, suggest) if _unit(auto) and _unit(suggest) and suggest <= auto else (AUTO_ROUTE, SUGGEST)


def _unit(value):
    return type(value) in (int, float) and 0 <= value <= 1


def _call(name, *args):
    """The module's answer, or None when there's no module, no such function, or it raised."""
    found = module()
    function = getattr(found, name, None) if found else None
    if not callable(function):
        return None
    started = time.perf_counter()
    try:
        return function(*args)
    except Exception as exc:  # noqa: BLE001 - one bad decision never stops Home
        print(f'thread_routing.{name} failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return None
    finally:
        took = (time.perf_counter() - started) * 1000
        if took > SLOW_MS:
            print(f'thread_routing.{name} took {took:.0f} ms', file=sys.stderr)


# ---------------------------------------------------------------- what the module reads
def threads(db, project_id, actor, eligible=None):
    """The project's threads as the module reads them: the state spine's record (thread_states.changes) plus pinned,
    nameLocked, titledBy, workstream, touched (last opened or written in), projectArchived, tidied (Dervo put it away)
    and ``messages``, the last few things you asked in it, oldest first. ``eligible``: the only thread ids to include
    (Home's own: shared, this project, this Mac's accounts), archived ones of those included."""
    import thread_states
    rows = [row for row in thread_states.changes(db, actor, 0, project_id=project_id)['threads']
            if not row.get('gone') and (eligible is None or row['id'] in eligible)]
    ids = [row['id'] for row in rows]
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    tables = {name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    select = lambda table, sql: {row[0]: row[1] for row in db.execute(sql, ids)} if table in tables else {}
    pinned = select('conversation_pins', f'SELECT thread_id, pinned_at FROM conversation_pins WHERE thread_id IN ({marks})')
    tidied = select('conversation_tidied', f'SELECT thread_id, tidied_at FROM conversation_tidied WHERE thread_id IN ({marks})')
    organisation = {key: _json(value) for key, value in select(
        'conversation_organisation', f'SELECT id, document FROM conversation_organisation WHERE id IN ({marks})').items()}
    project_archived = 'project_archives' in tables and db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (project_id,)).fetchone()
    streams = {}
    if 'project_idea_members' in tables and 'project_ideas' in tables:
        for channel, document in db.execute(f'SELECT m.channel_id, i.document FROM project_idea_members m JOIN project_ideas i ON i.id=m.idea_id '
                                            f'WHERE m.channel_id IN ({marks})', ids):
            idea = _json(document)
            if not idea.get('archived_at'):
                streams[channel] = idea.get('name')
    asked = {}
    if 'coding_requests' in tables:
        # The last MESSAGES of each thread's requests, read as that many rows a thread, oldest first.
        for thread, document in db.execute(f'SELECT thread_id, document FROM (SELECT thread_id, document, rowid AS r, ROW_NUMBER() '
                                           f'OVER (PARTITION BY thread_id ORDER BY rowid DESC) AS n FROM coding_requests '
                                           f'WHERE thread_id IN ({marks})) WHERE n <= ? ORDER BY r', ids + [MESSAGES]):
            text = _json(document).get('text')
            if isinstance(text, str) and text.strip():
                from chat_links import your_words
                asked.setdefault(thread, []).append((your_words(text) or text)[:TEXT_LIMIT])
    # A named agent's conversation (collab_agents, dervo_agents): never given your words by the router alone.
    agents = set()
    for table in ('collab_agents', 'dervo_agents'):
        if table in tables:
            agents |= {row[0] for row in db.execute(f"SELECT json_extract(document, '$.channel_id') FROM {table}")}
    try:
        from project_lifecycle import last_touched
        touched = last_touched(db, ids)
    except Exception:  # noqa: BLE001 - an older store without the lifecycle tables
        touched = {}
    found = []
    for row in rows:
        state = organisation.get(row['id']) or {}
        record = {key: row.get(key) for key in ('state', 'since', 'at', 'goal', 'summary', 'request', 'step', 'attention',
                                                 'pending_question', 'visibility')}
        record.update(id=row['id'], name=row.get('name') or '', unread=row.get('unread') or 0, running=bool(row.get('running')),
                      archived=bool(row.get('archived')), projectArchived=bool(project_archived), tidied=row['id'] in tidied,
                      pinned=row['id'] in pinned, nameLocked=bool(state.get('nameLocked')), titledBy=state.get('titledBy'),
                      workstream=streams.get(row['id']), touched=touched.get(row['id']), agent=row['id'] in agents,
                      messages=asked.get(row['id'], [])[-MESSAGES:])
        found.append(record)
    return found


def history(entries):
    """Home's recent messages, newest last: [{text, at, thread_id}], where thread_id is the one thread its answer sent
    work to (None when it sent none, or several)."""
    replies = {entry.get('answers'): entry for entry in entries if entry.get('kind') == 'reply' and entry.get('answers')}
    found = []
    for entry in entries:
        if entry.get('kind') != 'you' or not isinstance(entry.get('text'), str):
            continue
        reply = replies.get(entry['id']) or {}
        targets = {part.get('channel_id') or (part.get('conversation') or {}).get('id') for part in reply.get('parts') or []} - {None}
        found.append(dict(text=entry['text'][:TEXT_LIMIT], at=entry.get('created_at'), thread_id=targets.pop() if len(targets) == 1 else None))
    return found[-HISTORY:]


def _json(document):
    try:
        value = json.loads(document)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


# ---------------------------------------------------------------- the module's four answers, checked
def route(text, home, rows, now=None):
    """{kind, thread_id, name, confidence, why, candidates, command}, or None. ``home`` is history(entries). thread_id is
    one of ``rows`` that isn't archived, for 'existing' (or for 'home' with a command naming a thread). A command always
    reads as 'home': "delete the sidebar thread" is never text for an agent. Below SUGGEST, 'existing' and 'new' read
    as 'unsure'."""
    answer = _call('route', text, home, rows, now if now is not None else time.time())
    if not isinstance(answer, dict) or answer.get('kind') not in KINDS or not _unit(answer.get('confidence')):
        return None
    live = {row['id'] for row in rows if not row.get('archived')}
    known = {row['id'] for row in rows}
    candidates = [dict(thread_id=item['thread_id'], score=item.get('score') if _unit(item.get('score')) else None)
                  for item in answer.get('candidates') or [] if isinstance(item, dict) and item.get('thread_id') in live][:3]
    kind, thread = answer['kind'], answer.get('thread_id')
    command = answer.get('command') if answer.get('command') in COMMANDS else None
    if command or (answer.get('command') is not None and not command):
        kind = 'home'  # a command, or something the module meant as one: Home answers, never an agent
    if kind == 'existing' and thread not in live:
        if thread not in known:
            return None
        # An archived thread the message continues: Home's model asks before anything comes back (never silently).
        kind, candidates = 'unsure', [dict(thread_id=thread, score=answer['confidence'])] + [item for item in candidates if item['thread_id'] != thread][:2]
    if kind in ('existing', 'new') and answer['confidence'] < thresholds()[1]:
        kind = 'unsure'
    name = answer.get('name') if kind == 'new' and isinstance(answer.get('name'), str) else None
    return dict(kind=kind, thread_id=thread if (kind in ('existing', 'unsure') and thread in known) or (kind == 'home' and thread in known) else None, name=name,
                confidence=answer['confidence'], why=_line(answer.get('why')), candidates=candidates, command=command)


def name(record, others=()):
    """{title, confidence} for a thread whose name is still the rules' (no lock, no agent's title), or None. ``others``:
    the project's other thread names, so two threads don't share one."""
    if record.get('nameLocked') or record.get('titledBy'):
        return None
    answer = _call('name', record, [name for name in others if isinstance(name, str)])
    if not isinstance(answer, dict) or not isinstance(answer.get('title'), str) or not _unit(answer.get('confidence')):
        return None
    from thread_names import tidy_title
    title = tidy_title(answer['title'])
    return dict(title=title, confidence=answer['confidence']) if title else None


def sort(rows, now=None):
    """[{thread_id, bucket, why}] in display order, every thread once. The module's order when it gives one that covers
    them all; otherwise the plain one: needs you, working, finished and unseen, idle, archived; pinned first in each."""
    ids = [row['id'] for row in rows]
    answer = _call('sort', rows, now if now is not None else time.time())
    if (isinstance(answer, list) and all(isinstance(item, dict) and item.get('bucket') in BUCKETS for item in answer)
            and sorted(item.get('thread_id') for item in answer) == sorted(ids)):
        return [dict(thread_id=item['thread_id'], bucket=item['bucket'], why=_line(item.get('why'))) for item in answer]
    return plain_sort(rows)


def plain_sort(rows):
    def bucket(row):
        if row.get('archived'):
            return 'archived'
        if row.get('state') in ('needs_you', 'unknown'):
            return 'needs_you'
        if row.get('state') in ('working', 'quiet'):
            return 'working'
        if row.get('state') in ('finished', 'stopped') and row.get('unread'):
            return 'finished'
        return 'idle'
    ordered = sorted(rows, key=lambda row: (BUCKETS.index(bucket(row)), not row.get('pinned'), -(row.get('at') or 0)))
    return [dict(thread_id=row['id'], bucket=bucket(row), why=None) for row in ordered]


# Never put away by a decision, whatever it says: these need you, are doing something, or Dervo can't tell.
KEEP_STATES = ('needs_you', 'working', 'quiet', 'unknown')


def tidy(rows, now=None, days=None, keep=()):
    """[{thread_id, action, why, confidence}] that pass the host's own rules too: archive only a thread that isn't
    pinned, unread, archived, waiting on you or in KEEP_STATES; unarchive only one Dervo put away, never one you
    archived or one whose project is archived; pin only one that isn't pinned or archived. ``days`` is the project's
    tidy setting (7, 14, 30, 'smart'); with it off (None) only an unarchive stands. ``keep``: the threads on screen,
    never archived."""
    by_id = {row['id']: row for row in rows}
    keep = set(keep or ())
    answer = _call('tidy', rows, now if now is not None else time.time(), days, list(keep))
    if not isinstance(answer, list):
        return []
    found, seen = [], set()
    for item in answer:
        if not isinstance(item, dict) or item.get('action') not in TIDY_ACTIONS or not _unit(item.get('confidence')):
            continue
        row = by_id.get(item.get('thread_id'))
        if row is None or row['id'] in seen:
            continue
        action = item['action']
        if action == 'archive' and (days is None or row['id'] in keep or row['archived'] or row['pinned'] or row['unread']
                                    or row['state'] in KEEP_STATES or row['pending_question']):
            continue
        if action == 'unarchive' and not (row['archived'] and row['tidied'] and not row['projectArchived']):
            continue
        if action == 'pin' and (row['pinned'] or row['archived']):
            continue
        seen.add(row['id'])
        found.append(dict(thread_id=row['id'], action=action, why=_line(item.get('why')), confidence=item['confidence']))
    return found


def _line(value, limit=300):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else None


# ---------------------------------------------------------------- the decision log
class _Writing:
    """``with db`` that joins a transaction already open (a caller's BEGIN IMMEDIATE) instead of committing it early."""
    def __init__(self, db):
        self.db, self.joined = db, db.in_transaction

    def __enter__(self):
        return self.db if self.joined else self.db.__enter__()

    def __exit__(self, *exc):
        return False if self.joined else self.db.__exit__(*exc)


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS routing_decisions (id TEXT PRIMARY KEY, at REAL NOT NULL, project_id TEXT NOT NULL, '
               'op TEXT NOT NULL, thread_id TEXT, input TEXT NOT NULL, decision TEXT NOT NULL, applied INTEGER NOT NULL, outcome TEXT, '
               'side TEXT NOT NULL, exported INTEGER NOT NULL DEFAULT 0)')
    db.execute('CREATE INDEX IF NOT EXISTS routing_decisions_thread ON routing_decisions (thread_id, op, at)')
    db.execute('CREATE INDEX IF NOT EXISTS routing_decisions_project ON routing_decisions (project_id, at)')
    db.execute('CREATE INDEX IF NOT EXISTS routing_decisions_at ON routing_decisions (at)')
    db.execute('CREATE INDEX IF NOT EXISTS routing_decisions_export ON routing_decisions (exported, side) WHERE outcome IS NOT NULL')


LOGGED_FIELDS = ('id', 'name', 'state', 'archived', 'pinned', 'unread', 'agent', 'workstream', 'at', 'touched', 'tidied',
                 'projectArchived', 'nameLocked', 'titledBy')


def _clean(value, limit=LOG_TEXT):
    """Strings cut to ``limit`` and withheld whole when they hold a secret or a private word, all the way down."""
    if isinstance(value, str):
        return WITHHELD if withheld(value) else value[:limit]
    if isinstance(value, list):
        return [_clean(item, limit) for item in value[:HISTORY]]
    if isinstance(value, dict):
        return {key: _clean(item, limit) for key, item in value.items()}
    return value


def _kept(given, decision):
    """``given`` as the log keeps it: the message routed (TEXT_LIMIT characters) and Home's last messages, and of each
    thread only its id, name and state (LOGGED_FIELDS), never its messages, goal or summary; at most LOG_THREADS of
    them, the ones the decision names first. Everything secret-scanned. About 10 KB a decision at most."""
    if not isinstance(given, dict):
        return _clean(given)
    named = {decision.get('thread_id')} | {item.get('thread_id') for item in decision.get('candidates') or [] if isinstance(item, dict)} \
        if isinstance(decision, dict) else set()
    slim = lambda row: _clean({key: row[key] for key in LOGGED_FIELDS if key in row}, 100)
    kept = {key: _clean(value) for key, value in given.items() if key not in ('threads', 'others', 'thread', 'text')}
    if 'text' in given:
        kept['text'] = _clean(given['text'], TEXT_LIMIT)
    if isinstance(given.get('thread'), dict):
        kept['thread'] = dict(slim(given['thread']), messages=_clean(given['thread'].get('messages') or []))  # what it was named from
    for key in ('threads', 'others'):
        if key in given:
            rows = [row for row in given.get(key) or [] if isinstance(row, dict)]
            kept[key] = [slim(row) for row in sorted(rows, key=lambda row: (row.get('id') not in named, -(row.get('at') or 0)))[:LOG_THREADS]]
    return kept


WITHHELD = '[withheld]'


def withheld(text):
    """Whether text is kept out of the log whole: anything the secret scan would redact, a value handed over in words
    ("the key is ...", "token: ..."), or a word the naming rules treat as private (password, salary, medical, ...)."""
    import re
    import secret_scan
    from conversation_organisation import KEY_GIVEN, SENSITIVE
    return (secret_scan.redact(text) != text or bool(KEY_GIVEN.search(text))
            or bool(SENSITIVE & set(re.findall(r"[a-z]+", text.casefold()))))


def side_of(db, project_id):
    """'personal' or 'work' for a project, or None when it's marked neither (nothing is logged for it then)."""
    try:
        from dervo_connections import project_context
        return project_context(db, project_id)
    except Exception:  # noqa: BLE001 - an unknown or unmarked project
        return None


def side_threads(db, project_id):
    """The project's shared threads on accounts of its own side: the only ones the router reads outside Home (sort,
    tidy). Home's own routing reads Home's eligible set instead."""
    from dervo_connections import same_side
    side = side_of(db, project_id)
    if side is None:
        return set()
    return {row[0] for row in db.execute("SELECT t.id, p.category FROM threads t JOIN profiles p ON p.id=t.profile_id "
                                         "WHERE t.project_id=? AND t.visibility='shared'", (project_id,)) if same_side(row[1], side)}


def log(db, project_id, op, decision, given, *, thread_id=None, applied=False):
    """One decision with what it was made from (``given``, in learn.py's shape: route {text, history, threads}, name
    {thread, others}, tidy {threads}). Returns its id. Never raises: the decision stands whether or not it's logged."""
    try:
        side = side_of(db, project_id)
        if side is None:
            return None
        given, decision = _kept(given, decision), _clean(decision)
        decision_id = uuid.uuid4().hex
        with _Writing(db):
            schema(db)
            db.execute('INSERT INTO routing_decisions VALUES (?,?,?,?,?,?,?,?,NULL,?,0)',
                       (decision_id, time.time(), project_id, op, thread_id, json.dumps(given), json.dumps(decision), int(bool(applied)), side))
            # The oldest past each cap, found through the indexes on (project_id, at) and (at).
            db.execute('DELETE FROM routing_decisions WHERE project_id=? AND at < (SELECT at FROM routing_decisions WHERE project_id=? '
                       'ORDER BY at DESC LIMIT 1 OFFSET ?)', (project_id, project_id, LOG_KEEP - 1))
            db.execute('DELETE FROM routing_decisions WHERE at < (SELECT at FROM routing_decisions ORDER BY at DESC LIMIT 1 OFFSET ?)',
                       (LOG_KEEP_ALL - 1,))
        return decision_id
    except Exception as exc:  # noqa: BLE001
        print(f'A routing decision could not be logged: {type(exc).__name__}: {exc}', file=sys.stderr)
        return None


OUTCOMES = ('kept', 'unarchived')
OUTCOME_PREFIXES = ('moved:', 'renamed:')


def outcome(db, decision_id, value, project_id=None):
    """What you did about a decision (of ``project_id``, when given). The first word stands: a later 'kept' never
    overwrites a 'moved:'."""
    global _dirty
    if not (value in OUTCOMES or (isinstance(value, str) and value.startswith(OUTCOME_PREFIXES) and 7 < len(value) <= 300)):
        raise ValueError('Unknown routing outcome')
    try:
        with _Writing(db):
            schema(db)
            changed = db.execute('UPDATE routing_decisions SET outcome=?, exported=0 WHERE id=? AND outcome IS NULL AND (? IS NULL OR project_id=?)',
                                 (value, decision_id, project_id, project_id)).rowcount == 1
        _dirty = _dirty or changed
        return changed
    except Exception as exc:  # noqa: BLE001
        print(f'A routing outcome could not be logged: {type(exc).__name__}: {exc}', file=sys.stderr)
        return False


def outcome_for(db, thread_id, op, value, action=None):
    """The outcome of the newest applied ``op`` decision about this thread that has none yet (a rename after Dervo named
    it, a restore after Dervo put it away; ``action``: only a decision to do that, 'archive' say). Never raises."""
    try:
        schema(db)
        row = db.execute("SELECT id FROM routing_decisions WHERE thread_id=? AND op=? AND applied=1 AND outcome IS NULL "
                         "AND (? IS NULL OR json_extract(decision, '$.action')=?) ORDER BY at DESC LIMIT 1",
                         (thread_id, op, action, action)).fetchone()
        return outcome(db, row[0], value) if row else False
    except Exception as exc:  # noqa: BLE001
        print(f'A routing outcome could not be logged: {type(exc).__name__}: {exc}', file=sys.stderr)
        return False


def decisions(db, project_id=None, limit=LOG_KEEP, side=None):
    """The newest decisions, newest first, in learn.py's shape: one project's, or one side's."""
    schema(db)
    where, args = ('WHERE project_id=? ', (project_id,)) if project_id is not None else ('WHERE side=? ', (side,)) if side else ('', ())
    rows = db.execute('SELECT id, at, op, thread_id, input, decision, applied, outcome, project_id, side FROM routing_decisions '
                      + where + 'ORDER BY at DESC LIMIT ?', args + (max(1, int(limit)),)).fetchall()
    return [dict(id=row[0], at=row[1], op=row[2], thread_id=row[3], input=_json(row[4]), decision=json.loads(row[5]),
                 applied=bool(row[6]), outcome=row[7], project_id=row[8], side=row[9]) for row in rows]


def forget(db, threads):
    """Every decision about these threads, or that read them, gone: called as they're deleted, inside that delete.
    Whole decisions, not just the thread's record in them, so nothing of a deleted thread is left to replay. Never
    raises."""
    global _dirty, _rewrite
    threads = [thread for thread in threads or () if isinstance(thread, str) and thread]
    if not threads:
        return
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='routing_decisions'").fetchone():
            return  # nothing logged yet, and a delete makes no table
        for thread in threads:
            pattern = '%' + thread.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            db.execute("DELETE FROM routing_decisions WHERE thread_id=? OR input LIKE ? ESCAPE '\\' OR decision LIKE ? ESCAPE '\\'",
                       (thread, pattern, pattern))
        _dirty = _rewrite = True
    except Exception as exc:  # noqa: BLE001
        print(f'Routing decisions about a deleted thread could not be removed: {type(exc).__name__}: {exc}', file=sys.stderr)


def export(db, root, whole=False):
    """Every decision you did something about, one file a side (routing-decisions-personal.jsonl and -work.jsonl) in
    Dervo's data folder on this Mac, for ``python3 -m evals.routing.learn --log <that file>``, mode 0600 (it holds your
    words). Each new outcome is appended once. ``whole`` writes each file again from the log and swaps it in, which a
    delete needs (forget). Returns the paths written."""
    schema(db)
    written = []
    for side in SIDES:
        path = os.path.join(str(root), EXPORT_NAME.format(side=side))
        query = ('SELECT id, at, op, input, decision, outcome FROM routing_decisions WHERE side=? AND outcome IS NOT NULL '
                 + ('' if whole else 'AND exported=0 ') + 'ORDER BY at')
        rows = db.execute(query, (side,)).fetchall()
        lines = [json.dumps(dict(id=row[0], at=row[1], op=row[2], input=_json(row[3]), decision=json.loads(row[4]), outcome=row[5])) + '\n'
                 for row in rows]
        if whole:
            if not lines:
                if os.path.exists(path):
                    os.unlink(path)  # its last decision was forgotten
                continue
            handle, temporary = tempfile.mkstemp(dir=str(root), prefix='.routing-', suffix='.jsonl')
            try:
                os.fchmod(handle, 0o600)
                with os.fdopen(handle, 'w', encoding='utf-8') as out:
                    out.writelines(lines)
                os.replace(temporary, path)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        elif lines:
            handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(handle, 'a', encoding='utf-8') as out:
                out.writelines(lines)
        else:
            continue
        with _Writing(db):
            db.executemany('UPDATE routing_decisions SET exported=1 WHERE id=?', [(row[0],) for row in rows])
        written.append(path)
    return written


_dirty = False        # an outcome landed since the last export
_rewrite = False      # a delete forgot decisions: the files are written again, not appended to
_exported_at = None


def tick(host):
    """From Host._tick: the log written out for learn.py once an outcome changed it, at most every EXPORT_EVERY
    seconds. Free when nothing changed. Never raises."""
    global _dirty, _rewrite, _exported_at
    if not _dirty or (_exported_at is not None and time.monotonic() - _exported_at < EXPORT_EVERY):
        return
    whole = _rewrite
    _dirty, _rewrite, _exported_at = False, False, time.monotonic()
    try:
        export(host.store.db, host.root, whole=whole)
    except Exception as exc:  # noqa: BLE001
        print(f'The routing log could not be written out: {type(exc).__name__}: {exc}', file=sys.stderr)
