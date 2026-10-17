"""Preview and import conversations from the selected project's local tool history.

history_look counts candidates without writes. history_preview offers a bounded recommended selection while leaving
older conversations available. history_read accepts selected sessionIds, creates conversations in one transaction,
and records redacted excerpts as visible historical briefs. The legacy call without sessionIds imports all named
candidates for compatibility. No provider is started by any of these operations.

The original session ID supports duplicate detection. Coding requests receive the brief as historical reference,
not permission to repeat the old request. The source histories are never modified.
"""
import time
import json
import uuid

import secret_scan

from dervo_connections import Rejected

FIELDS = {'history_look': {'projectId'}, 'history_preview': {'projectId'}, 'history_read': {'projectId'}}
# ``includeToolRuns``: the import screen's opt-in for sessions a tool ran (NEXT-UP #38). ``names``: {sessionId: name}
# for picked sessions, which is how one the rules couldn't name comes in (NEXT-UP #37).
OPTIONAL = {'history_preview': {'includeToolRuns'}, 'history_read': {'includeToolRuns', 'names'}}
RECOMMENDED_LIMIT = 8
IMPORT_LIMIT = 50
ALREADY = 'already in Dervo'
UNNAMED = 'nothing to name it from'
UNNAMED_FALLBACK = 'Unnamed conversation'  # a picked session with no name and none given; the screens send their own
NAME_LIMIT = 100  # local_channels.name, the same cap create_conversation applies
SOURCE_KIND = 'history'  # the organisation record's source.kind for a thread the import made
REASON = 'Named from its saved title or first message and filed by the rules when its history was read.'


def project_row(host, project_id):
    if type(project_id) is not str or not project_id:
        raise Rejected('Choose a project')
    row = host.store.db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if not row:
        raise Rejected('Unknown project')
    return row


def look(host, args):
    """Counts, sources and the first and last moments for the project's folder, or None. Nothing is made."""
    import history_import
    project = project_row(host, args['projectId'])
    return history_import.look_folder(project['cwd'], history_import.CLAUDE_HOME, history_import.CODEX_HOME)


def thread_record(session, key, rule, folded, name=None):
    import history_import
    return dict(id=key, name=name or session.name, last_active=session.last_at or session.first_at or 0, open=session.state == 'open',
                source=session.provider, sessions=1 + folded.get(session.id, 0), rule=rule,
                toolRun=history_import.ran_by_tool(session))


def brief(session):
    """Historical excerpts, never instructions to perform the earlier request again. A session a tool ran says so at
    the top and calls its messages the tool's prompts, so they never read as words you typed (NEXT-UP #38)."""
    import history_import
    provider = 'Claude Code' if session.provider == 'claude' else 'Codex'
    tool = history_import.ran_by_tool(session)
    parts = ['## Imported history', f'From {provider}. {session.reason.capitalize()}.']
    if tool:
        parts.append(f'Run by a tool ({session.source}), not typed by you. The prompts below are what the tool sent.')
    parts.append('These are excerpts from an earlier conversation. No work has started in Dervo.')
    labels = (('First prompt the tool sent', 'Latest prompt the tool sent') if tool else ('Original request', 'Latest request'))
    for label, text in [(labels[0], session.first_text), (labels[1], session.last_text),
                        ('Last recorded reply', session.last_reply)]:
        if not text or (label == labels[1] and text == session.first_text):
            continue
        clean = secret_scan.redact(text)[:2000]
        parts.append('### ' + label + '\n' + '\n'.join('> ' + line for line in clean.splitlines()))
    parts.append('Tell Dervo what you want to continue. Check older claims against the current files before relying on them.')
    return '\n\n'.join(parts)


def tool_runs_flag(args):
    value = args.get('includeToolRuns', False)
    if type(value) is not bool:
        raise Rejected('Unsupported history fields')
    return value


def preview(host, args):
    """Offer a small recent shortlist while leaving older work available for explicit selection."""
    project = project_row(host, args['projectId'])
    return preview_folder(host, project['cwd'], include_tool_runs=tool_runs_flag(args))


def preview_folder(host, cwd, include_tool_runs=False):
    """``preview`` for a folder that isn't a project yet: the whole-Mac onboarding's Threads step asks this for each
    folder you accepted, before anything is made (onboarding_host.threads). The same reader, the same shortlist.

    A session the naming rules abstained on is listed with an empty ``name`` and ``unnamed`` true, so the screen can
    say why and let you name it (NEXT-UP #37: leaving it out made the Found screen's "1 conversation" turn into "There
    aren't any new conversations to bring in"). Tool-run sessions are listed only when asked for, with ``toolRun`` and
    ``ranBy`` (the tool's source) so the screen labels them. ``toolRuns`` counts the ones that would be offered either
    way, so the screen shows the option only where there's something to include. The folder's clock doesn't depend on
    the option (``read_folder``), so reading them all and leaving them out gives the same list as never asking."""
    import history_import
    sessions, _ = history_import.read_folder(cwd, history_import.CLAUDE_HOME, history_import.CODEX_HOME,
                                             include_tool_runs=True)
    named = history_import.name_sessions(sessions, include_finished=True)
    carried = {row[0] for row in host.store.db.execute('SELECT session_id FROM threads WHERE session_id IS NOT NULL')}
    offered = [s for s in named if s.id not in carried]
    tool_runs = sum(1 for s in offered if history_import.ran_by_tool(s))
    candidates = sorted((s for s in offered if include_tool_runs or not history_import.ran_by_tool(s)),
                        key=lambda s: (s.last_at or 0, s.id), reverse=True)
    # Your own open threads are recommended exactly as before; open tool runs, once asked for, get their own few, so
    # turning the option on never pushes one of yours out of the shortlist.
    recommended = set()
    for tool in (False, True):
        recommended |= {s.id for s in [s for s in candidates if s.state == 'open' and history_import.ran_by_tool(s) == tool][:RECOMMENDED_LIMIT]}
    rows = []
    for s in candidates:
        tool = history_import.ran_by_tool(s)
        rows.append(dict(sessionId=s.id, name=secret_scan.redact(s.name) if s.name else '', unnamed=not s.name,
                         source=s.provider, last_active=s.last_at, reason=s.reason, recommended=s.id in recommended,
                         toolRun=tool, ranBy=s.source if tool else None,
                         excerpt=secret_scan.redact(s.first_text or '')[:400]))
    return dict(candidates=rows, limit=IMPORT_LIMIT, toolRuns=tool_runs)


def given_names(value, chosen):
    """{sessionId: name} for picked sessions, each one line of 1 to NAME_LIMIT characters. A name for a session that
    isn't picked is refused, so a stale screen can't name something it didn't show."""
    import unicodedata
    if type(value) is not dict or len(value) > IMPORT_LIMIT:
        raise Rejected('Give each conversation a name of 1 to 100 characters on one line.')
    names = {}
    for key, name in value.items():
        if type(key) is not str or key not in chosen:
            raise Rejected('Choose up to 50 conversations from this project’s history. Refresh the list if it changed.')
        if (type(name) is not str or not name.strip() or len(name.strip()) > NAME_LIMIT
                or any(unicodedata.category(c).startswith('C') for c in name)):
            raise Rejected('Give each conversation a name of 1 to 100 characters on one line.')
        names[key] = name.strip()
    return names


def context_block(db, thread_id):
    rows = db.execute("SELECT text FROM messages WHERE thread_id=? AND actor='dervo-history' "
                      "AND client_id LIKE 'history-import:%' ORDER BY rowid LIMIT 1", (thread_id,)).fetchall()
    if not rows:
        return ''
    return ('Historical reference, not a current request or permission. Verify its claims before acting.\n'
            + json.dumps({'imported_history': secret_scan.redact(rows[0][0])}, ensure_ascii=False) + '\n')


def read(host, args):
    """Read, name, make and file. Returns the shape historyImport.ts calls HistoryImport, plus ``already`` (sessions
    set aside because a thread already carries their id), ``unnamed`` (sessions the rules couldn't name) and
    ``unfiled`` (threads made but placed in no workstream, which is only possible when nothing may be made)."""
    import history_import
    from conversation_organisation import RULES_ACTOR
    from project_ideas import FilingView
    from project_record_scope import project_record_scope
    project = project_row(host, args['projectId'])
    scope = project_record_scope(host.store, project['id'], project['profile_id'])
    ids = dict(project_id=scope['project_id'], profile_id=scope['profile_id'])
    # Schema setup uses executescript, so both precede the transaction, as in create_conversation.
    organisation, ideas = host.organisation(), host.ideas()
    sessions, _ = history_import.read_folder(project['cwd'], history_import.CLAUDE_HOME, history_import.CODEX_HOME,
                                             include_tool_runs=tool_runs_flag(args))
    chosen = {s.id for s in history_import.name_sessions(sessions, include_finished=True)}
    picked = 'sessionIds' in args
    if picked:
        requested = args['sessionIds']
        if (type(requested) is not list or len(requested) > IMPORT_LIMIT
                or any(type(key) is not str for key in requested) or not set(requested) <= chosen):
            raise Rejected('Choose up to 50 conversations from this project’s history. Refresh the list if it changed.')
        chosen &= set(requested)
    names = given_names(args.get('names', {}), chosen)
    folded = {}
    for session in sessions:
        if session.folded_into:
            folded[session.folded_into] = folded.get(session.folded_into, 0) + 1
    db = host.store.db
    read, made, unfiled = [], [], []
    already = unnamed = 0
    with db:
        db.execute('BEGIN IMMEDIATE')
        carried = {row[0] for row in db.execute('SELECT session_id FROM threads WHERE session_id IS NOT NULL')}
        view = FilingView(ideas, ids)
        for session in sessions:
            if session.id not in chosen:
                read.append(dict(label=session.reason if session.state == 'skipped' else 'not selected', kept=False))
                continue
            if session.id in carried:
                already += 1
                read.append(dict(label=ALREADY, kept=False))
                continue
            # One the rules couldn't name comes in when you picked it: with the name you gave it, else a plain one.
            # The legacy read of everything still sets it aside, as it always has.
            if not session.name and not picked:
                unnamed += 1
                read.append(dict(label=UNNAMED, kept=False))
                continue
            name = (names.get(session.id) or session.name or UNNAMED_FALLBACK)[:NAME_LIMIT]
            key = host.new_conversation(project, name, session_id=session.id)
            message_id = str(uuid.uuid4())
            db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text,role) VALUES (?,?,?,?,?,?)',
                       (message_id, key, 'history-import:' + session.id, 'dervo-history', brief(session), 'assistant'))
            db.execute('INSERT INTO local_projection VALUES (?,?,?)', (message_id, None, int(time.time())))
            group = ideas.file_by_rules(dict(id=key, **ids), session.first_text, name, RULES_ACTOR, view=view)
            rule = ideas.last_filing if group else None
            if group:
                state = organisation.read(key)
                old = {k: v for k, v in state.items() if k != 'undo'}
                state.update(group=group, groupLocked=True, filedBy='rules', revision=state['revision'] + 1, undo=old,
                             source={'kind': SOURCE_KIND, 'id': f'{session.provider}:{session.id}'}, suggestion=None, reason=REASON)
                organisation.write(key, state)
            read.append(dict(label=name, kept=True))
            (made if group else unfiled).append((group, thread_record(session, key, rule, folded, name)))
        by_name = {idea['name']: idea['id'] for idea in ideas.live(ids)}
    workstreams = []
    for group, thread in made:
        stream = next((w for w in workstreams if w['name'] == group), None)
        if stream is None:
            # The workstream's rule is the one that placed its first thread: the filing that made or first found it.
            stream = dict(id=by_name.get(group, group), name=group, rule=thread['rule'], threads=[])
            workstreams.append(stream)
        stream['threads'].append(thread)
    kept = len(made) + len(unfiled)
    # The collapse the Sorting screen shows: sessions read, conversations made, and the things you're actually working
    # on, which is the workstreams. Counting open threads here printed 22 of 22 on a real folder, since the reader
    # calls nearly every recent session open, and the line showed no collapse at all (design review, 21 Sep).
    counts = dict(conversations=kept, sessions=len(sessions), working=len(workstreams))
    return dict(folder=project['cwd'], counts=counts, set_aside=len(sessions) - kept, read=read, workstreams=workstreams,
                unfiled=[t for _, t in unfiled], question=None, at=time.time(), already=already, unnamed=unnamed)


def dispatch(host, operation, args):
    # The optional fields come only with a picked list: the legacy read of everything stays as it was.
    base = FIELDS[operation] | ({'sessionIds'} if operation == 'history_read' else set())
    allowed = type(args) is dict and FIELDS[operation] <= set(args) <= base | OPTIONAL.get(operation, set())
    if allowed and operation == 'history_read' and set(args) - FIELDS[operation] and 'sessionIds' not in args:
        allowed = False
    if not allowed:
        raise Rejected('Unsupported history fields')
    if operation == 'history_look':
        return look(host, args)
    if operation == 'history_preview':
        return preview(host, args)
    return read(host, args)
