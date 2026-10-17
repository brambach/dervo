"""Bounded saved-record reads. No provider, credential or project-file access."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from dervo_connections import Rejected
from task_error import task_error

BASE = {'projectId', 'channelId', 'profileId', 'scope'}
FIELDS = {'workspace_search': BASE | {'query', 'limit'},
          'project_catchup': BASE | {'limit'}, 'workspace_source': BASE | {'source'}}
BINDING_KEYS = ('id', 'project_id', 'profile_id', 'identity', 'category', 'provider', 'model', 'visibility', 'owner')
TASK_BINDING_KEYS = ('profile_id', 'identity', 'provider', 'model', 'cwd', 'visibility')
SCAN_RECORDS = 128
RECORD_BYTES = 256 * 1024
DECODE_BYTES = 2 * 1024 * 1024
SQL_STEPS = 100_000
CURSOR_TTL = 900
CURSOR_CAP = 128


def _stamp(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _scope(store, args, actor):
    if args.get('scope') == 'project' and args.get('channelId') == '':
        from project_record_scope import project_record_scope
        project = project_record_scope(store, args['projectId'], args['profileId'])
        return {'scope':'project', 'project':project, 'actor':actor}
    for key in BASE:
        if type(args[key]) is not str or not args[key] or len(args[key]) > 256:
            raise Rejected('Choose a saved conversation and search scope')
    if args['scope'] not in ('conversation', 'project'):
        raise Rejected('Choose conversation or project search')
    anchor = store.binding(args['channelId'], args['projectId'], actor=actor)
    from dervo_connections import same_side
    if anchor['profile_id'] != args['profileId'] or not same_side(anchor['category'], anchor.get('context', anchor['category'])):
        raise Rejected('Search account binding changed or is on the other side of Work and Personal')
    project = store.db.execute('SELECT j.profile_id, p.category FROM projects j JOIN profiles p ON p.id=j.profile_id WHERE j.id=?',
                               (args['projectId'],)).fetchone()
    # The project's own account must be on the anchor's side. It needn't be the anchor's: a thread you moved to Codex, or
    # one left on the old account after the project's default changed, still searches its project (handoff map, 19 Sep, H1).
    if args['scope'] == 'project' and (project is None or not same_side(project['category'], anchor['category'])):
        raise Rejected("Shared search needs the project's own account")
    return {'scope':args['scope'], 'anchor':{k:anchor[k] for k in BINDING_KEYS},
            'projectProfile':project['profile_id']}


def _binding(store, channel, args, actor):
    if args['scope'] == 'conversation' and channel != args['channelId']:
        return None
    try:
        binding = store.binding(channel, args['projectId'], actor=actor)
    except Rejected:
        return None
    from dervo_connections import same_side
    if not same_side(binding['category'], binding.get('context', binding['category'])):
        return None
    if args['scope'] == 'conversation' and binding['profile_id'] != args['profileId']:
        return None
    if args['scope'] == 'project' and binding['visibility'] != 'shared':
        return None
    return binding


def _same_work(store, old, binding):
    """Whether a saved task or coordination record still belongs to this thread's work. On the thread's current account
    the saved binding must match it exactly, as before. On another account it must be a personal one of this Mac, in
    the same folder and visibility: a thread moved to Codex keeps its earlier Claude tasks in Catch up, and each row
    names the account it was made on. A record from a work account, another folder or another visibility never shows
    here (handoff map, 19 Sep, H1)."""
    if old['profile_id'] == binding['profile_id']:
        return all(old[k] == binding[k] for k in TASK_BINDING_KEYS)
    if old['cwd'] != binding['cwd'] or old['visibility'] != binding['visibility']:
        return False
    profile = store.db.execute('SELECT category FROM profiles WHERE id=?', (old['profile_id'],)).fetchone()
    return bool(profile) and profile['category'] == binding['category']


def _read(store, kind, *, before=None, source_id=None):
    # Primary-key source lookup or rowid keyset seek. No JSON SQL functions, OFFSET,
    # sorting or full-table predicate scans. Catch up supplies exact indexed IDs.
    table, column = {'task':('coding_tasks','document'),'message':('messages','text'),'coordination':('harness_requests','document')}[kind]
    if source_id is not None:
        where, values = 'id=?', (source_id,)
    elif before is None:
        where, values = '1', ()
    else:
        where, values = 'rowid<?', (before,)
    # SQLite 3.43+ reads byte length from record metadata without loading giant text.
    byte_length = f'octet_length({column})' if sqlite3.sqlite_version_info >= (3,43,0) else f'length(CAST({column} AS BLOB))'
    fields = 'id' if kind == 'task' else 'id,thread_id' if kind == 'coordination' else 'id,thread_id,client_id,actor,role'
    return store.db.execute(f'''SELECT rowid AS position,{fields},
        {byte_length} AS size,
        CASE WHEN {byte_length}<=? THEN {column} END AS content
        FROM {table} WHERE {where} ORDER BY rowid DESC LIMIT 1''', (RECORD_BYTES, *values)).fetchone()


def _row(store, kind, saved, args, actor, tasks_only=False):
    if kind == 'task':
        task = json.loads(saved['content'])
        if not isinstance(task, dict) or not isinstance(task.get('context'), dict):
            raise ValueError('Malformed saved task context')
        context = task['context']
        if task.get('id') != saved['id'] or any(
                not isinstance(context.get(k), str) or not context[k]
                for k in ('actor', 'project_id', 'thread_id')):
            raise ValueError('Malformed saved task identity')
        if context['actor'] != actor or context['project_id'] != args['projectId']:
            return None
        channel = context['thread_id']
    elif kind == 'coordination':
        task=json.loads(saved['content'])
        if not isinstance(task,dict) or task.get('id')!=saved['id'] or not isinstance(task.get('thread_id'),str) or task['thread_id']!=saved['thread_id']:
            raise ValueError('Malformed saved coordination identity')
        if task.get('project_id')!=args['projectId']:return None
        channel=task['thread_id']
    else:
        if saved['role'] not in ('user', 'assistant'):
            return None
        channel = saved['thread_id']
    binding = _binding(store, channel, args, actor)
    if binding is None:
        return None
    name = store.db.execute('SELECT substr(name,1,256) FROM local_channels WHERE id=?', (channel,)).fetchone()
    row = dict(channelId=channel, channelName=name[0] if name else 'Conversation', kind=kind, id=saved['id'])
    if kind == 'message':
        content = {k:saved[k] for k in ('id','thread_id','client_id','actor','role')}
        content['text'] = saved['content']
        row.update(text=saved['content'], role=saved['role'])
        origin = saved['client_id'].split(':', 1)[0] if saved['role'] == 'assistant' else saved['id']
        turn = store.db.execute('SELECT state,substr(error,1,1000) AS error FROM turns WHERE id=? AND thread_id=?', (origin, channel)).fetchone()
        content['turn'] = dict(turn) if turn else None
        row.update(matchRole=saved['role'], status=turn['state'] if turn else 'saved',
                   blocker=turn['error'] if turn else None)
    elif kind == 'coordination':
        old=task.get('binding')
        if not isinstance(old,dict) or any(not isinstance(old.get(k),str) or not old[k] for k in TASK_BINDING_KEYS):
            raise ValueError('Older coordination record has no verifiable account snapshot')
        if not _same_work(store, old, binding):return None
        from coordination_search import messages as coordination_messages
        messages=coordination_messages(task)
        row.update(text='\n\n'.join(m['text'] for m in messages),
                   title=messages[0]['text'][:120],
                   hasAnswer=len(messages)>1,matchRole=messages[-1]['role'],status=str(task.get('state') or 'unknown')[:120],
                   blocker=(task_error(task.get('error')) or '')[:1000],owner=old['provider'])
        content=task
    else:
        old = task.get('binding')
        # Malformed records cannot prove absence. ValueError marks every remaining
        # page incomplete and makes exact-source lookup refuse the record.
        if not isinstance(old, dict) or any(
                not isinstance(old.get(k), str) or not old[k] for k in TASK_BINDING_KEYS):
            raise ValueError('Malformed saved task binding')
        if not _same_work(store, old, binding):
            return None
        messages = task.get('messages')
        if not isinstance(messages, list) or any(
                not isinstance(m, dict) or not isinstance(m.get('role'), str)
                or not m['role'] or not isinstance(m.get('text'), str) for m in messages):
            raise ValueError('Malformed saved task messages')
        messages = [m for m in messages if isinstance(m, dict) and m.get('role') in ('user','assistant') and isinstance(m.get('text'),str)]
        helper_title = None
        if task.get('origin') == 'collaboration' and isinstance(task.get('agent'), str):
            role = 'Review' if task.get('role') == 'reviewer' else 'Help'
            helper_title = f"{role} by @{task['agent'][:64].lstrip('@')}"
        acceptance = task.get('acceptance') if isinstance(task.get('acceptance'), dict) else {}
        completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
        collaboration = task.get('collaboration') if isinstance(task.get('collaboration'), dict) else {}
        review = collaboration.get('review') if isinstance(collaboration.get('review'), dict) else {}
        from task_outcome import outcome_facts
        from catchup_summary import task_facts, task_summary
        facts = outcome_facts(task)
        detail = task_facts(task, facts)
        row.update(text='\n\n'.join(m['text'] for m in messages),
                   title=helper_title or next((m['text'][:120] for m in messages if m['role']=='user'), 'Saved task'),
                   hasAnswer=any(m['role']=='assistant' and m['text'].strip() for m in messages),
                   helper=task.get('origin') == 'collaboration',
                   humanDecision=facts['human_decision'], outcomeUncertain=facts['outcome_uncertain'],
                   humanApprovalRequired=acceptance.get('required') is not False,
                   reviewPending=review.get('phase') in ('pending','initial','correction','final'),
                   stale=completion.get('stale') is True,
                   status=str(task.get('state') or 'unknown')[:120],
                   owner=f"{old['provider']} · {str(task.get('agent') or row['channelName'])[:256]}",  # the account it was made on
                   blocker=(task_error(task.get('error')) or 'Unknown: no blocker recorded')[:1000],
                   summary=task_summary(task, detail)[:600],
                   **{k: v for k, v in detail.items() if k not in ('outcomeUncertain', 'humanDecision')})
        content = task
    offset = 0
    if kind in ('task','coordination') and tasks_only:
        position = 0
        for message in messages:
            if message['role'] == 'assistant':
                offset = position
            position += len(message['text']) + 2
    return row, binding, content, offset


def _source(row, binding, content, stamp, offset=0):
    return dict(kind=row['kind'], id=row['id'], channelId=row['channelId'], offset=offset,
                revision=_stamp([stamp, {k:binding[k] for k in BINDING_KEYS}, row['kind'], row['id'], content]))


def _position(text, query):
    match = re.search(re.escape(query), text, re.IGNORECASE)
    return match.start() if match else -1


def _snippet(text, query=''):
    # Keep the complete match and prefer a sentence or line over a clipped word.
    # The saved source and its offsets remain untouched.
    position = max(0, _position(text, query)) if query else 0
    match_end = position + len(query)
    start = max(0, position - 100)
    if start:
        prefix = text[start:position]
        boundaries = list(re.finditer(r'[.!?]\s+|\n+', prefix))
        whitespace = re.search(r'\s+', prefix)
        if boundaries:
            start += boundaries[-1].end()
        elif whitespace:
            start += whitespace.end()
    end = min(len(text), start + 420)
    if end < len(text):
        suffix = text[match_end:end]
        boundaries = list(re.finditer(r'[.!?](?=\s)|\n+', suffix))
        whitespace = list(re.finditer(r'\s+', suffix))
        if boundaries:
            end = match_end + boundaries[-1].end()
        elif whitespace:
            end = match_end + whitespace[-1].start()
    while start < position and text[start].isspace():
        start += 1
    while end > match_end and text[end-1].isspace():
        end -= 1
    return ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')


def _search_excerpt(kind, content, text, query, position):
    """Prefer the latest matching answer. Keep its opening when the match fits the reader."""
    if kind in ('task','coordination'):
        matches, start = [], 0
        if kind=='coordination':
            from coordination_search import messages
            entries=messages(content)
        else:entries=content['messages']
        for message in entries:
            if message['role'] not in ('user', 'assistant'):
                continue
            match = _position(message['text'], query)
            if match >= 0:
                matches.append((message['role'] == 'assistant', start, message, match))
            start += len(message['text']) + 2
        if matches:
            _, start, message, match = max(matches, key=lambda item: (item[0], item[1]))
            offset = start if match + len(query) <= 16000 else start + max(0, match - 60)
            return offset, _snippet(message['text'], query), message['role']
    offset = 0 if position + len(query) <= 16000 else max(0, position - 60)
    return offset, _snippet(text, query), content.get('role') if kind == 'message' else None


def _dispatch(host, operation, args, actor):
    allowed = FIELDS[operation]
    if type(args) is not dict or set(args) not in (allowed, allowed | ({'cursor'} if operation != 'workspace_source' else set())):
        raise Rejected('Unsupported search fields')
    if operation != 'workspace_source':
        if type(args['limit']) is not int or not 1 <= args['limit'] <= 50:
            raise Rejected('Choose between 1 and 50 results')
    query = args.get('query', '')
    if operation == 'workspace_search' and (type(query) is not str or not query.strip() or len(query) > 256):
        raise Rejected('Enter search text between 1 and 256 characters')
    if operation == 'project_catchup' and args['scope'] != 'project':
        raise Rejected('Catch-up uses shared project scope')
    store = host.store
    stamp = _scope(store, args, actor)
    has_tasks = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone()
    has_coordination = store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='harness_requests'").fetchone()
    if operation == 'workspace_source':
        source = args['source']
        if type(source) is not dict or set(source) != {'kind','id','channelId','revision','offset'} or any(type(source[k]) is not str or not source[k] or len(source[k])>256 for k in ('kind','id','channelId','revision')) or type(source['offset']) is not int or not 0 <= source['offset'] <= RECORD_BYTES or source['kind'] not in ('task','message','coordination'):
            raise Rejected('Choose an exact saved source')
        exists={'message':True,'task':bool(has_tasks),'coordination':bool(has_coordination)}[source['kind']]
        if _binding(store, source['channelId'], args, actor) is not None and exists:
            saved = _read(store, source['kind'], source_id=source['id'])
            if saved and saved['content'] is not None:
                try:
                    found = _row(store, source['kind'], saved, args, actor)
                except (ValueError, RecursionError):
                    found = None
                if found:
                    row, binding, content, _ = found
                    if _source(row, binding, content, stamp, source['offset']) == source and source['offset'] <= len(row['text']):
                        start = source['offset']
                        return {**row, 'source':source, 'text':row['text'][start:start+16000],
                                'excerptStart':start, 'truncated':start > 0 or len(row['text'])>16000}
        raise Rejected('Source changed or is outside this scope. Search again.')

    if operation == 'project_catchup' and 'cursor' not in args:
        from task_activity import prepare
        from message_activity import prepare as prepare_messages
        messages_ready = prepare_messages(store.db)
        tasks_ready = not has_tasks or (messages_ready and prepare(store.db))
        from coordination_search import prepare as prepare_coordination
        coordination_ready = not has_coordination or (messages_ready and tasks_ready and prepare_coordination(store.db))
        if not messages_ready or not tasks_ready or not coordination_ready:
            return {'results':[], 'cursor':None, 'incomplete':True, 'indexing':True}

    seen, since, lessons_pending, skills_pending = None, None, None, None
    if operation == 'project_catchup' and 'cursor' not in args:
        # What you had seen by your last visit, and what is newest now. The row flags compare against the old
        # mark; the new mark is written before the cursor version below is read, so paging stays valid.
        from message_activity import position as activity_position
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS catchup_seen (project_id TEXT NOT NULL, actor TEXT NOT NULL, phase INTEGER NOT NULL, '
                             'activity REAL NOT NULL, ordinal INTEGER NOT NULL, seen_at INTEGER NOT NULL, PRIMARY KEY (project_id, actor))')
            previous = store.db.execute('SELECT phase, activity, ordinal, seen_at FROM catchup_seen WHERE project_id=? AND actor=?', (args['projectId'], actor)).fetchone()
            if previous:
                seen, since = (previous['phase'], previous['activity'], previous['ordinal']), previous['seen_at']
            newest = activity_position(store.db, None, bool(has_tasks), bool(has_coordination))
            if newest:
                top = newest[0][:3]
                if previous is None or tuple(previous)[:3] != top:
                    store.db.execute('INSERT INTO catchup_seen VALUES (?,?,?,?,?,?) ON CONFLICT(project_id, actor) DO UPDATE SET '
                                     'phase=excluded.phase, activity=excluded.activity, ordinal=excluded.ordinal, seen_at=excluded.seen_at',
                                     (args['projectId'], actor, *top, int(time.time())))
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_lessons'").fetchone():
            lessons_pending = store.db.execute("SELECT count(*) FROM project_lessons WHERE project_id=? AND profile_id=? AND status='pending'",
                                               (args['projectId'], args['profileId'])).fetchone()[0]
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_skills'").fetchone():
            # Skill versions an agent proposed or an import brought in, waiting for you in Knowledge.
            skills_pending = 0
            for (document,) in store.db.execute('SELECT document FROM project_skills WHERE project_id=? AND profile_id=?',
                                                (args['projectId'], args['profileId'])):
                record = json.loads(document)
                if not record.get('archived_at'):
                    skills_pending += sum(1 for version in record.get('versions', []) if version.get('status') == 'proposed')

    # Tokens contain no offsets, IDs or counts. A write anywhere invalidates them,
    # including external commits. This deliberately refuses more than binding changes.
    version = (store.db.total_changes, store.db.execute('PRAGMA data_version').fetchone()[0])
    request = (operation, {k:v for k,v in args.items() if k != 'cursor'}, actor, stamp, version)
    now = time.monotonic()
    cache = getattr(host, '_workspace_cursors', {})
    cache = {k:v for k,v in cache.items() if now-v['created'] < CURSOR_TTL}
    host._workspace_cursors = cache
    state = dict(phase=0, before=None, skipped=False, seen=list(seen) if seen else None, since=since, lessons=lessons_pending, skills=skills_pending)
    if 'cursor' in args:
        token = args['cursor']
        if type(token) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{32}', token) or token not in cache or cache[token]['request'] != request:
            raise Rejected('Search continuation expired or scope changed. Search again.')
        state = dict(cache[token]['state'])
    kinds = ['activity'] if operation == 'project_catchup' else ['message'] + (['task'] if has_tasks else []) + (['coordination'] if has_coordination else [])
    results, scanned, decoded = [], 0, 0
    while state['phase'] < len(kinds) and scanned < SCAN_RECORDS and len(results) < args['limit']:
        kind = kinds[state['phase']]
        if kind == 'activity':
            from message_activity import position as activity_position
            position = activity_position(store.db, state['before'], bool(has_tasks), bool(has_coordination))
            if position:
                order, kind, source_id = position
                saved = _read(store, kind, source_id=source_id)
                saved = {**dict(saved), 'position':order} if saved else dict(position=order, content=None)
            else:
                saved = None
        else:
            saved = _read(store, kind, before=state['before'])
        if saved is None:
            state.update(phase=state['phase']+1, before=None)
            continue
        # Reserve a full record before fetching the next one on subsequent iterations.
        scanned += 1
        state['before'] = saved['position']
        if saved['content'] is None:
            state['skipped'] = True
            continue
        decoded += saved['size']
        try:
            found = _row(store, kind, saved, args, actor, operation == 'project_catchup')
        except (ValueError, RecursionError):
            state['skipped'] = True
            found = None
        if found:
            row, binding, content, offset = found
            position = _position(row['text'], query) if query else 0
            if position >= 0:
                snippet = None
                if query:
                    offset, snippet, role = _search_excerpt(kind, content, row['text'], query, position)
                    if role:
                        row['matchRole'] = role
                row['source'] = _source(row, binding, content, stamp, offset)
                if operation == 'project_catchup':
                    row['isNew'] = bool(state.get('seen')) and saved['position'][0] == 1 and tuple(saved['position'][:3]) > tuple(state['seen'])
                text = row.pop('text')
                row['snippet'] = ('No answer recorded.' if operation == 'project_catchup' and kind=='task' and row.get('hasAnswer') is False
                                  else snippet if query else _snippet(text[offset:]))
                results.append(row)
        if decoded > DECODE_BYTES - RECORD_BYTES:
            break
    more = state['phase'] < len(kinds)
    token = None
    if more:
        while len(cache) >= CURSOR_CAP:
            del cache[next(iter(cache))]
        token = secrets.token_urlsafe(24)
        cache[token] = dict(request=request, state=state, created=now)
    # No totals or private aggregates. Oversize/corrupt records cannot prove absence.
    page = {'results':results, 'cursor':token, 'incomplete':more or state['skipped']}
    if operation == 'project_catchup':
        # When you last looked, and how many lesson suggestions wait in Knowledge: both are records, not totals of this page.
        page.update(since=state.get('since'), lessonsPending=state.get('lessons'), skillsPending=state.get('skills'))
    return page


def dispatch(host, operation, args, actor):
    ticks = 0
    def progress():
        nonlocal ticks
        ticks += 1000
        return ticks >= SQL_STEPS
    host.store.db.set_progress_handler(progress, 1000)
    try:
        return _dispatch(host, operation, args, actor)
    except sqlite3.OperationalError as error:
        if 'interrupted' in str(error):
            raise Rejected('Saved search reached its database work limit. Search is incomplete. Try a narrower scope.') from error
        raise
    finally:
        host.store.db.set_progress_handler(None, 0)
