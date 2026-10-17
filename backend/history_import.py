"""One project folder's Claude Code and Codex history, read for the blank-project import. Step one: a reader and a
plan, nothing written anywhere.

Bryce, 20 Sep 2026 (notes/PLAN-import.md), narrowed on 21 Sep: when you add a project and it's a blank workspace,
Dervo can offer to read the history that folder already has and fill it with sections and threads. Per project, not
the whole Mac: the 280 harness folders the plan calls the hard part never come into it, because the person names one
folder and only that folder's transcripts are read.

What's read, and only read:

- ``~/.claude/projects/<slug>/<session>.jsonl``: Claude Code's own transcripts for a cwd. The slug is the encoding
  ``session_continuity.place_session`` already writes into, so it's imported from there and not reinvented. Subagent
  transcripts sit in a directory beside each session and are counted, never parsed. The slug turns every character
  but letters and digits into a hyphen, so ``dd-app``, ``dd.app`` and ``dd_app`` share one folder (security review L3,
  23 Sep). A session counts only when the first ``cwd`` its records carry is this folder, by realpath or on disk. One
  whose records carry no ``cwd`` at all is skipped as well: nothing says it's this folder's (``ran_here``).
- ``~/.codex/state_5.sqlite``: Codex's thread list, opened read-only and queried by cwd. It carries the created and
  updated times and whether you archived the thread. The first thing you typed comes from the thread's rollout file.
  ``thread_history_1.sqlite`` (1 GB) is never opened: for every thread of the folders tried its turn table was empty,
  and the rollout has what's needed.

Nothing here writes, moves or deletes under ``~/.claude`` or ``~/.codex``. No network, no model. ``plan`` runs the
result through the sorting Dervo already ships (``conversation_organisation.suggest`` for the name,
``project_ideas.file_by_rules`` for the workstream) in a store the caller hands it, which for the dry run is a temporary
one. The same privacy checks a typed first message meets apply here: a first message with a path, a URL, code or a
key in it abstains and the thread stays unnamed and unfiled, exactly as it would have in the app.

What counts as still open (``status``), and why:

- Nothing you typed, or you typed once and nothing answered: skipped. Not a thread, an aborted start.
- Archived in Codex: finished. You said so.
- Your last message is a sign-off ("thanks, that's all", "perfect, done"): finished. The rule is a short message
  made only of sign-off words, so "ok now fix the tests" stays open.
- Idle for more than OPEN_DAYS measured from the folder's own newest activity, not from today: finished. A folder
  you come back to after three weeks away still shows where you left off; a thread that went quiet while the folder
  stayed busy is done.
- Otherwise open. Your last message with no reply after it is open and says "waiting on a reply".

Timestamps decide most of it, as the plan expected ("alive or dead, from timestamps"). Message text is read for two
things only: the first message, which names the thread, and whether the last one was a sign-off.
"""
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from session_continuity import _slug as project_slug  # the cwd encoding Claude Code uses for its project folders

CLAUDE_HOME = Path.home() / '.claude'
CODEX_HOME = Path.home() / '.codex'
CODEX_STATE = 'state_5.sqlite'
OPEN_DAYS = 14  # a thread this far behind the folder's newest activity is finished
TEXT_LIMIT = 4000  # what the naming rules read of a first message; suggest() abstains past 4096 anyway
SIGN_OFF_LIMIT = 8  # words; longer than this and it's a message, not a goodbye
SIGN_OFF_WORDS = frozenset('''
thanks thank you ta cheers ok okay great perfect done cool nice good brilliant excellent awesome lovely
that's thats all that is it this for now the works worked fine ship shipped merged close closing finished wrap up
bye night later see you'''.split())
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
TITLE_LIMIT = 80  # a title longer than this, or on more than one line, is the message itself and is not kept
# Codex threads a person started. ``exec`` is ``codex exec``, a run some tool or script drove (Dervo's own harness
# leaves hundreds); ``mcp`` is an app-server client, Dervo among them. Neither is a thread you opened.
CODEX_OWN_SOURCES = frozenset({'cli', 'vscode'})
COPY_REASON = 'a copy of a Claude Code session here'


def clean_title(value):
    """A title a tool kept for the session, when it is one: one line, TITLE_LIMIT characters at most. Codex's
    ``title`` column is the first message itself when nothing shorter was set, and that is never shown."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or '\n' in value or len(value) > TITLE_LIMIT:
        return None
    return value


@dataclass
class Session:
    provider: str  # 'claude' or 'codex'
    id: str
    path: str  # the transcript, for the report
    first_text: str = None  # the first thing you typed, cut at TEXT_LIMIT; names the thread
    first_at: float = None
    last_at: float = None
    turns: int = 0  # messages you typed
    replies: int = 0  # messages the agent wrote
    answered: bool = False  # the agent wrote after your last message
    last_text: str = None  # your last message, read for the sign-off rule and never reported
    last_reply: str = None  # bounded excerpt, shown as historical context rather than a new answer
    archived: bool = False  # Codex: you archived it
    title: str = None  # the title the tool itself kept: Claude Code's ai-title (else its custom-title), Codex's name
    named_by: str = None  # where ``title`` came from: 'ai-title', 'custom-title', 'codex name'; None when there is none
    custom_title: str = None  # Claude Code's custom-title record, kept apart because it isn't known to be user-set
    source: str = None  # Codex: cli, vscode, exec, mcp
    cwd: str = None  # Claude Code: the folder its records say it ran in, from the first one that carries a cwd
    extras: int = 0  # subagent transcripts beside the session, counted not read
    copy_of: str = None  # Codex: the Claude Code session this thread is Codex's copy of
    folded_into: str = None  # the session this one is a fork or a copy of, when it was skipped for that
    # Filled by plan():
    state: str = None  # open, finished, skipped
    reason: str = None
    name: str = None  # the thread's name: the tool's title tidied, else the rules' short name; None when both fail
    opener_name: str = None  # what short_name makes of the first message, for comparison; None when it abstains
    group: str = None  # the workstream file_by_rules put it in
    rule: str = None  # which step of the ladder did it


# ---------------------------------------------------------------- Claude Code
def claude_project_dir(cwd, home=CLAUDE_HOME):
    return Path(home) / 'projects' / project_slug(os.path.realpath(cwd))


def ran_here(recorded, cwd):
    """Whether a transcript's own ``cwd`` is this folder: the same realpath, or the same folder on disk (a case or
    link spelling the realpath doesn't fold). False when nothing was recorded, since then nothing says it's this one."""
    if not isinstance(recorded, str) or not recorded:
        return False
    if os.path.realpath(recorded) == os.path.realpath(cwd):
        return True
    try:
        return os.path.samefile(recorded, cwd)
    except OSError:
        return False


def _when(value):
    """Seconds since the epoch from Claude Code's and Codex's ISO stamps, or None."""
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def typed_text(record):
    """What you typed in a Claude Code user record, or None: not a tool result, not a meta or compaction record, not
    a subagent's turn, not a slash command or a pasted system block (they start with a tag)."""
    if record.get('type') != 'user' or record.get('isMeta') or record.get('isSidechain') or record.get('isCompactSummary'):
        return None
    message = record.get('message') or {}
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(part, dict) and part.get('type') == 'tool_result' for part in content):
            return None
        text = '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text')
    else:
        return None
    text = REMINDER.sub('', text).strip()
    if not text or text.startswith('<'):
        return None
    return text


def read_claude_session(path):
    """One transcript, top to bottom. Every line is parsed; the top-level files are small (the bulk of a project
    folder is tool results and subagent transcripts in the directories beside them)."""
    path = Path(path)
    session = Session('claude', path.stem, str(path))
    beside = path.with_suffix('')
    if beside.is_dir():
        session.extras = sum(1 for p in beside.rglob('*.jsonl'))
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            kind = record.get('type')
            if session.cwd is None and isinstance(record.get('cwd'), str):
                session.cwd = record['cwd']
            # The tool's own title for the session. ai-title first: custom-title is rewritten on nearly every turn in
            # the files seen (about 140 per session), so it isn't known to be something the person typed. When both
            # exist the ai-title wins and the custom one is kept beside it; the last record of each kind counts.
            if kind == 'custom-title':
                session.custom_title = clean_title(record.get('customTitle')) or session.custom_title
            elif kind == 'ai-title':
                session.title = clean_title(record.get('aiTitle')) or session.title
                session.named_by = 'ai-title' if session.title else None
            if kind not in ('user', 'assistant') or record.get('isSidechain'):
                continue
            at = _when(record.get('timestamp'))
            if at is not None:
                session.last_at = max(session.last_at or at, at)
            if kind == 'assistant':
                session.replies += 1
                session.answered = session.turns > 0
                content = (record.get('message') or {}).get('content')
                if isinstance(content, list):
                    content = '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
                if isinstance(content, str) and content.strip():
                    session.last_reply = content.strip()[:TEXT_LIMIT]
                continue
            text = typed_text(record)
            if text is None:
                continue
            session.turns += 1
            session.answered = False
            session.last_text = text
            if session.first_text is None:
                session.first_text = text[:TEXT_LIMIT]
                session.first_at = at
    if not session.title and session.custom_title:
        session.title, session.named_by = session.custom_title, 'custom-title'
    return session


def read_claude(cwd, home=CLAUDE_HOME):
    """Every session Claude Code kept for this folder, oldest first. Files only; nothing is touched. A session from
    another folder with the same slug, or one that names no folder, is left out (``ran_here``)."""
    folder = claude_project_dir(cwd, home)
    if not folder.is_dir():
        return []
    sessions = []
    for path in sorted(folder.iterdir()):
        if path.suffix == '.jsonl' and path.is_file() and not path.is_symlink() and UUID.fullmatch(path.stem):
            session = read_claude_session(path)
            if ran_here(session.cwd, cwd):
                sessions.append(session)
    sessions.sort(key=lambda s: (s.first_at or s.last_at or 0, s.id))
    return sessions


# ---------------------------------------------------------------- Codex
def _codex_steps(path):
    """(when, who, text) for each message of a Codex rollout, in order. Two layouts: ``legacy`` rollouts carry
    ``user_message`` and ``agent_message`` events, ``paginated`` ones only the ``response_item`` messages (whose user
    side also carries Codex's own context blocks, which open with a tag and are dropped). The events are used when
    present, the items otherwise, so neither layout counts a message twice."""
    events, items = [], []
    with open(path, encoding='utf-8', errors='replace') as handle:
        for line in handle:
            if '"message"' not in line and '"task_complete"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            payload = record.get('payload') or {}
            if not isinstance(payload, dict):
                continue
            at, kind = _when(record.get('timestamp')), payload.get('type')
            if record.get('type') == 'event_msg' and kind == 'user_message':
                events.append((at, 'you', payload.get('message')))
            elif record.get('type') == 'event_msg' and kind == 'agent_message':
                events.append((at, 'agent', payload.get('message')))
            elif record.get('type') == 'event_msg' and kind == 'task_complete':
                events.append((at, 'done', None))
                items.append((at, 'done', None))
            elif record.get('type') == 'response_item' and kind == 'message':
                content = payload.get('content')
                if isinstance(content, list):
                    text = '\n'.join(part.get('text', '') for part in content
                                     if isinstance(part, dict) and part.get('type') in ('input_text', 'output_text'))
                else:
                    text = content if isinstance(content, str) else ''
                items.append((at, 'you' if payload.get('role') == 'user' else 'agent', text))
    return events if any(who == 'you' for _, who, _ in events) else items


def read_codex_rollout(path, session):
    """The typed messages and replies of one Codex rollout, into ``session``. A message that opens with a tag is
    Codex's own (its environment block, a scheduled automation's brief), not something you typed."""
    for at, who, text in _codex_steps(path):
        if who == 'you':
            if not isinstance(text, str) or not text.strip() or text.lstrip().startswith('<'):
                continue
            text = text.strip()
            session.turns += 1
            session.answered = False
            session.last_text = text
            if session.first_text is None:
                session.first_text = text[:TEXT_LIMIT]
                session.first_at = at or session.first_at
        elif who == 'agent':
            session.replies += 1
            session.answered = session.turns > 0
            if isinstance(text, str) and text.strip():
                session.last_reply = text.strip()[:TEXT_LIMIT]
        else:
            session.answered = session.turns > 0
        if at is not None:
            session.last_at = max(session.last_at or at, at)
    return session


def read_codex(cwd, home=CODEX_HOME):
    """Codex's threads for this folder from its state database, opened read-only, oldest first. Subagent spawns (their
    ``source`` is a JSON blob naming the parent) stay out: they're the parent's work, like Claude Code's sidechains.
    A missing database or rollout means fewer threads, never an error."""
    state = Path(home) / CODEX_STATE
    if not state.is_file():
        return []
    real = os.path.realpath(cwd)
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
    except sqlite3.Error:
        return []
    sessions = []
    try:
        columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
        named = 'name' in columns and 'title' in columns
        rows = db.execute('SELECT id, rollout_path, created_at, updated_at, archived, source'
                          + (', name, title' if named else ", NULL, NULL")
                          + ' FROM threads WHERE cwd IN (?, ?) ORDER BY created_at', (real, str(cwd))).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    for thread_id, rollout, created, updated, archived, source, name, title in rows:
        if not isinstance(source, str) or source.startswith('{'):
            continue
        # Codex's ``name`` is the thread's own title; its ``title`` column is the first message when nothing shorter
        # was set, so it names nothing here.
        session = Session('codex', thread_id, rollout or '', first_at=_when(created), last_at=_when(updated),
                          archived=bool(archived), source=source, title=clean_title(name))
        session.named_by = 'codex name' if session.title else None
        if rollout and Path(rollout).is_file():
            read_codex_rollout(rollout, session)
        sessions.append(session)
    sessions.sort(key=lambda s: (s.first_at or s.last_at or 0, s.id))
    return sessions


# ---------------------------------------------------------------- the look, before anything is agreed to
LOOK_LINES = 400  # how far into a transcript the look reads for the first thing you typed


def look_claude(cwd, home=CLAUDE_HOME):
    """(sessions, conversations, first, last) for Claude Code's files: the head of each up to LOOK_LINES lines for the
    first thing you typed and its time, the file's own modification time for the last. Never a whole file: a
    folder's biggest transcript here is 290 MB (Bryce's ~/dervo, 21 Sep), and the look runs before anything is agreed
    to, on every visit to a blank project. The head's first ``cwd`` leaves out what the read leaves out: another
    folder's session with the same slug, and one with something typed but no folder named. A head with neither a cwd
    nor anything typed still counts as a session, as it always has. There's nothing in it to read."""
    folder = claude_project_dir(cwd, home)
    if not folder.is_dir():
        return 0, 0, None, None
    sessions = conversations = 0
    first = last = None
    for path in sorted(folder.iterdir()):
        if not (path.suffix == '.jsonl' and path.is_file() and not path.is_symlink() and UUID.fullmatch(path.stem)):
            continue
        typed_at = recorded = None
        with open(path, encoding='utf-8', errors='replace') as handle:
            for number, line in enumerate(handle):
                if number >= LOOK_LINES:
                    break
                if '"user"' not in line and (recorded is not None or '"cwd"' not in line):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if recorded is None and isinstance(record.get('cwd'), str):
                    recorded = record['cwd']
                if typed_text(record) is None:
                    continue
                typed_at = _when(record.get('timestamp'))
                break
        if (recorded is not None or typed_at is not None) and not ran_here(recorded, cwd):
            continue
        sessions += 1
        if typed_at is None:
            continue
        conversations += 1
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = typed_at
        first = typed_at if first is None else min(first, typed_at)
        last = modified if last is None else max(last, modified)
    return sessions, conversations, first, last


def look_codex(cwd, home=CODEX_HOME):
    """(sessions, conversations, first, last) for Codex's threads of this folder, from its state database alone: the
    created and updated times, and whether the first message was yours (``first_user_message``, where the schema has
    it). No rollout is opened."""
    state = Path(home) / CODEX_STATE
    if not state.is_file():
        return 0, 0, None, None
    real = os.path.realpath(cwd)
    try:
        db = sqlite3.connect(f'file:{state}?mode=ro', uri=True)
    except sqlite3.Error:
        return 0, 0, None, None
    try:
        columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
        typed = 'first_user_message' in columns
        rows = db.execute('SELECT source, created_at, updated_at' + (', first_user_message' if typed else ', NULL')
                          + ' FROM threads WHERE cwd IN (?, ?)', (real, str(cwd))).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    sessions = conversations = 0
    first = last = None
    for source, created, updated, message in rows:
        if source not in CODEX_OWN_SOURCES:
            continue
        sessions += 1
        if typed and not (isinstance(message, str) and message.strip() and not message.lstrip().startswith('<')):
            continue
        conversations += 1
        created, updated = _when(created), _when(updated)
        if created is not None:
            first = created if first is None else min(first, created)
        if updated is not None:
            last = updated if last is None else max(last, updated)
    return sessions, conversations, first, last


def look_folder(cwd, claude_home=CLAUDE_HOME, codex_home=CODEX_HOME):
    """What the Found screen says before anything is read: how many sessions and conversations the folder has on this
    Mac, from which tools, and the first and last moments. None when there is nothing. A count here is an estimate the
    read refines: it knows nothing of replies, forks or copies, only whether something was typed."""
    claude = look_claude(cwd, claude_home)
    codex = look_codex(cwd, codex_home)
    sessions = claude[0] + codex[0]
    if not sessions:
        return None
    sources = [name for name, found in (('claude', claude), ('codex', codex)) if found[0]]
    firsts = [found[2] for found in (claude, codex) if found[2] is not None]
    lasts = [found[3] for found in (claude, codex) if found[3] is not None]
    return dict(folder=str(cwd), conversations=claude[1] + codex[1], sessions=sessions, sources=sources,
                first=min(firsts) if firsts else None, last=max(lasts) if lasts else None)


# ---------------------------------------------------------------- open or finished
def signed_off(text):
    """Whether a last message is a goodbye rather than a request: SIGN_OFF_LIMIT words at most, all of them sign-off
    words. "ok now fix the tests" is a request."""
    if not isinstance(text, str):
        return False
    words = [w.strip("'’.,!:;()") for w in text.casefold().split()]
    words = [w for w in words if w]
    return 0 < len(words) <= SIGN_OFF_LIMIT and all(w in SIGN_OFF_WORDS for w in words)


def ran_by_tool(session):
    """Whether a tool or script drove this session rather than you: a Codex thread whose source isn't one you open
    (``exec``, ``mcp``). Subagent spawns never get this far (``read_codex``)."""
    return session.provider == 'codex' and session.source not in CODEX_OWN_SOURCES


def status(session, latest, include_tool_runs=False):
    """(state, reason) for one session against ``latest``: the folder's newest activity, or newer when the caller
    knows of newer work on the project (a Dervo store that already holds threads for it). See the module docstring.
    A tool-run session is skipped unless the person asked for them (``include_tool_runs``, NEXT-UP #38: someone
    running their own orchestration wants those runs); then it meets the same rules as any other."""
    if ran_by_tool(session) and not include_tool_runs:
        return 'skipped', f'run by a tool, not you ({session.source})'
    if not session.first_text:
        return 'skipped', 'nothing you typed'
    if session.replies == 0 and session.turns <= 1:
        return 'skipped', 'no reply'
    if session.archived:
        return 'finished', 'archived in Codex'
    if signed_off(session.last_text):
        return 'finished', 'signed off'
    idle = (latest - (session.last_at or 0)) / 86400 if latest and session.last_at else None
    if idle is not None and idle > OPEN_DAYS:
        return 'finished', f'idle {int(idle)} days'
    if not session.answered:
        return 'open', 'waiting on a reply'
    return 'open', 'recent'


# ---------------------------------------------------------------- the plan
def why_abstained(text):
    """Which of ``suggest``'s checks a first message fails, for the dry run's report. The checks are the ones in
    conversation_organisation.suggest, in its order; this names the first that fails and decides nothing."""
    import conversation_organisation as organisation
    if not isinstance(text, str) or not text.strip():
        return 'empty'
    if len(text) > 4096:
        return 'too long'
    words = set(re.findall(r"[^\W\d_]+", text.casefold()))
    if words & organisation.SENSITIVE:
        return 'a sensitive word'
    if organisation.KEY_GIVEN.search(text) or organisation.GLUED.search(text):
        return 'looks like a key'
    if re.search(r'https?://|www\.', text):
        return 'a URL'
    if re.search(r'(?<![\w./~\\])(?:~|\.{1,2})?/[^\s]', text) or '\\' in text:
        return 'a path'
    if '`' in text:
        return 'code marks'
    if re.search(r'[@{}\[\]=<>;$]|\d{5}', text):
        return 'brackets, symbols or a long number'
    if any(not (c.isalnum() or c.isspace() or c in ",.?!:()-_'’\"&%+#") for c in text):
        return 'other characters'
    if re.search(r"\w\(|(?im:^[ \t]*(?:def|class|import|from|return|const|let|var)\b)|(?i:\bselect\b.+\bfrom\b)", text):
        return 'a line of code'
    return 'no words to name it'


def fold_forks(sessions):
    """A resumed-and-forked Claude Code session is a second file that opens with the same first message at the same
    moment. Only the one that went furthest counts; the other is skipped and says which it is a copy of."""
    by_start = {}
    for session in sessions:
        if session.first_text and session.first_at:
            by_start.setdefault((session.provider, session.first_at, session.first_text), []).append(session)
    for group in by_start.values():
        if len(group) < 2:
            continue
        keep = max(group, key=lambda s: (s.last_at or 0, s.id))
        for session in group:
            if session is not keep:
                session.state, session.reason, session.folded_into = 'skipped', f'a fork of {keep.id[:8]}', keep.id


def codex_imports(home=CODEX_HOME):
    """{Codex thread id: Claude Code session id} for the Claude Code sessions Codex copied into its own history
    (``external_agent_session_imports.json``; on Bryce's Mac, 1,071 of them). Empty when there is no such file."""
    path = Path(home) / 'external_agent_session_imports.json'
    if not path.is_file():
        return {}
    try:
        records = json.loads(path.read_text(encoding='utf-8')).get('records') or []
    except (ValueError, OSError, AttributeError):
        return {}
    found = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        source, thread = record.get('source_path'), record.get('imported_thread_id')
        if isinstance(source, str) and isinstance(thread, str) and UUID.fullmatch(Path(source).stem or ''):
            found[thread] = Path(source).stem
    return found


def fold_copies(sessions, copies):
    """A Codex thread that is Codex's copy of a Claude Code session in the same list is skipped: it's the same
    conversation, and the Claude Code file is the original."""
    claude = {s.id for s in sessions if s.provider == 'claude'}
    for session in sessions:
        source = copies.get(session.id) if session.provider == 'codex' else None
        if source and source in claude:
            session.state, session.reason, session.copy_of, session.folded_into = 'skipped', COPY_REASON, source, source


def read_folder(cwd, claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, latest_floor=None, include_tool_runs=False):
    """Every session of the folder from both tools, oldest first, each with its state and reason. ``latest_floor``
    is the project's newest activity known from elsewhere (a Dervo store that already has threads for the folder):
    the idle rule measures from whichever is newer, so a folder whose work moved into Dervo weeks ago doesn't read
    its last terminal sessions as open. Without it the folder's own history is the only clock. Tool-run sessions
    stay skipped unless ``include_tool_runs`` (``status``); the clock is the same either way, so asking for them
    changes nothing about your own threads."""
    sessions = read_claude(cwd, claude_home) + read_codex(cwd, codex_home)
    sessions.sort(key=lambda s: (s.first_at or s.last_at or 0, s.provider, s.id))
    stamps = [s.last_at for s in sessions if s.first_text and s.last_at]
    if latest_floor:
        stamps.append(float(latest_floor))
    latest = max(stamps) if stamps else None
    for session in sessions:
        session.state, session.reason = status(session, latest, include_tool_runs)
    fold_copies(sessions, codex_imports(codex_home))
    fold_forks(sessions)
    return sessions, latest


def open_store(store_dir, cwd):
    """A store of Dervo's own shape in ``store_dir`` with one account and this folder as its project, and the
    ProjectIdeas over it. The caller owns the directory; the dry run makes a temporary one and removes it."""
    from conversation_organisation import Organisation
    from dervo_connections import Store
    from project_ideas import ProjectIdeas
    store = Store(Path(store_dir) / 'conversations.sqlite3')
    store.db.execute('CREATE TABLE IF NOT EXISTS local_channels(id TEXT PRIMARY KEY REFERENCES threads(id), name TEXT NOT NULL)')
    store.db.commit()
    Organisation(store)
    home = Path(store_dir) / 'home'
    home.mkdir(exist_ok=True)
    store.add_profile('import', 'claude', home, 'import@local', 'personal')
    store.add_project('import', cwd, 'import', 'claude')
    return store, ProjectIdeas(store)


def thread_name(session):
    """The thread's name and where it came from. The title the person's own tool wrote (Claude Code's ai-title, Codex's
    name) goes through ``tidy_title``, the path a coding agent's Task guide title already takes: cleaned, a key
    scrubbed, shortened by the naming rules when it runs past four words. Only a session with no such title is named
    from its first message by ``suggest``, with every abstention that implies (21 Sep: those abstentions were most of
    the history, and nearly all on "instructions" in a brief). The first-message name is kept for comparison."""
    from conversation_organisation import suggest
    from thread_names import tidy_title
    opener = suggest(session.first_text)[0]
    if session.title:
        tidied = tidy_title(session.title)
        if tidied:
            return tidied, session.named_by, opener
    return opener, 'first message' if opener else None, opener


def name_sessions(sessions, include_finished=False):
    """The sessions worth a thread, named: every one not skipped (the finished ones only when asked), with ``name``,
    ``named_by`` and ``opener_name`` filled by ``thread_name``. In the order given; a session the rules can't name
    is still returned, with ``name`` None, so the caller can say so."""
    chosen = []
    for session in sessions:
        if session.state == 'skipped' or (session.state == 'finished' and not include_finished):
            continue
        session.name, session.named_by, session.opener_name = thread_name(session)
        chosen.append(session)
    return chosen


def plan(cwd, store_dir, *, claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, include_finished=False, latest_floor=None):
    """What Dervo would create for this folder: each open session named (``thread_name``) and filed by
    ``file_by_rules`` in chronological order through one FilingView, the way the start-up tidy does it, with the first
    message as the request text the rules read, as in the app. ``include_finished`` files the finished ones too, to
    see how the groups read with everything in them. ``latest_floor`` is passed to ``read_folder``.

    Returns dict(sessions, latest, workstreams=[dict(name, threads=[Session])], counts=dict(...)).
    """
    from conversation_organisation import RULES_ACTOR
    from project_ideas import FilingView
    sessions, latest = read_folder(cwd, claude_home, codex_home, latest_floor)
    store, ideas = open_store(store_dir, cwd)
    scope = dict(project_id='import', profile_id='import')
    order = []
    try:
        view = FilingView(ideas, scope)
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            for session in name_sessions(sessions, include_finished):
                if not session.name:
                    continue
                binding = dict(id=f'{session.provider}:{session.id}', **scope)
                session.group = ideas.file_by_rules(binding, session.first_text, session.name, RULES_ACTOR, view=view)
                session.rule = ideas.last_filing if session.group else None
                if session.group and session.group not in order:
                    order.append(session.group)
    finally:
        store.close()
    workstreams = [dict(name=name, threads=[s for s in sessions if s.group == name]) for name in order]
    filed = [s for s in sessions if s.group]
    counts = dict(
        transcripts=len(sessions) + sum(s.extras for s in sessions),
        claude=sum(1 for s in sessions if s.provider == 'claude'),
        claude_extras=sum(s.extras for s in sessions if s.provider == 'claude'),
        codex=sum(1 for s in sessions if s.provider == 'codex'),
        sessions=sum(1 for s in sessions if s.state != 'skipped'),
        open=sum(1 for s in sessions if s.state == 'open'),
        finished=sum(1 for s in sessions if s.state == 'finished'),
        skipped=sum(1 for s in sessions if s.state == 'skipped'),
        named=sum(1 for s in sessions if s.name),
        named_by={key: sum(1 for s in sessions if s.name and s.named_by == key)
                  for key in ('ai-title', 'custom-title', 'codex name', 'first message')},
        abstained=sum(1 for s in sessions if s.state == 'open' and not s.name)
        + (sum(1 for s in sessions if s.state == 'finished' and not s.name) if include_finished else 0),
        filed=len(filed),
        workstreams=len(workstreams))
    return dict(sessions=sessions, latest=latest, workstreams=workstreams, counts=counts)
