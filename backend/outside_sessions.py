"""Catch up's "Outside Dervo" group: the Claude Code and Codex sessions already on this Mac for one project folder, and
where each one left off. Read only, per project, and only once you've turned it on for that project.

Why (reports/review-20260923/market-fit.md, section 6, bet 1): Catch up only knew runs Dervo started, so a new
tester's first Catch up was empty. The sessions they ran in the terminal, the Claude app or the Codex app were already
on their Mac. This reads them the way ``history_import`` does, and says what the records say and nothing more.

What's read, and only read:

- Claude Code: the session files in this folder's directory under ``~/.claude/projects`` (``history_import`` finds
  it). The directory's name is a slug, and ``/x/dd.app``, ``/x/dd-app`` and ``/x/dd_app`` share one, so a session is
  kept only when the ``cwd`` it recorded resolves to this project's folder. A session with no ``cwd`` is skipped.
- Codex: its thread list, ``state_5.sqlite`` opened read-only, queried by ``cwd`` and checked the same way, and each
  thread's rollout file, only when it sits inside the Codex folder.

Nothing is written under either folder. No model, no network. Each file is read at its head (the first thing you
asked, which titles the row, and the ``cwd``) and its tail (how it stands), never whole, and a file that hasn't
changed isn't read again. One refresh reads at most BYTE_BUDGET bytes and stops starting new files after TIME_BUDGET
seconds; what's left comes on the next refresh and the answer says ``partial``. A line that can't be parsed is
skipped. A file that can't be read is counted as "couldn't read" and never stops the rest.

How a session stands comes from its records alone, in STATES. When the tail doesn't say, it's "unknown", never a
guess. Session text is untrusted: it's redacted with ``secret_scan`` before it leaves this module and it's only ever
shown or quoted as context. It isn't stored, and nothing here goes into Home's prompt or off the Mac.

Sessions Dervo started are left out: their ids are on its threads (a chat turn, or a session the history import
brought in) and its coding tasks. Codex threads another tool drove (``exec``, ``mcp``, a subagent) are left out as
``history_import`` leaves them out, and so are Codex's own copies of Claude Code sessions.
"""
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit

import history_import as hi
import secret_scan
from dervo_connections import Rejected

FIELDS = {
    'outside_sessions': {'projectId'},
    'outside_sessions_consent': {'projectId'},
    'outside_sessions_consent_set': {'projectId', 'enabled'},
}
# finished: the agent's turn ended. limit: it stopped at a usage or rate limit. approval: its last step asked to run or
# change something and no answer is recorded. question: it asked you something and no answer is recorded. stopped:
# you stopped it. error: it stopped on an error. unknown: the records don't say.
STATES = ('finished', 'limit', 'approval', 'question', 'stopped', 'error', 'unknown')
SHOWN = 8  # rows in one answer, newest first
CANDIDATES = 24  # newest files per tool looked at, before the cwd check and Dervo's own set some aside
WINDOW_DAYS = 30  # a session quieter than this isn't where you left off
HEAD_BYTES = 64 * 1024
TAIL_STEPS = (256 * 1024, 1024 * 1024)  # a tail with nothing you asked in it is read once more, further back
BYTE_BUDGET = 8 * 1024 * 1024
TIME_BUDGET = 0.25
CACHE_LIMIT = 256
VERDICT_SECONDS = 60  # how long "Dervo didn't start this one" is trusted before the store is asked again
TITLE_LIMIT = 72
ASK_LIMIT = 400
TARGET_LIMIT = 90
DETAIL_LIMIT = 200
FILES_LIMIT = 8

INTERRUPT = re.compile(r'^\s*\[Request interrupted by user')
REFUSED = re.compile(r"^\s*The user doesn(?:'|’)t want to (?:proceed|take this action)")
LIMIT = re.compile(r'usage limit|limit reached|hit your (?:usage )?limit|rate limit|usage_limit|rate_limit|'
                   r'usageLimitExceeded|rateLimitExceeded|resets? (?:at )?\d', re.I)
# Tools that stop and ask before they run in each tool's default mode. A call to one with no result after it is
# "approval"; a call to anything else with no result is "unknown" (still running, or the session closed).
CLAUDE_ASKS = frozenset({'Bash', 'Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'WebFetch', 'ExitPlanMode'})
CODEX_ASKS = frozenset({'shell', 'exec_command', 'local_shell', 'apply_patch', 'write_stdin'})
QUESTION_TOOLS = frozenset({'AskUserQuestion', 'request_user_input'})
EDITS = frozenset({'Edit', 'MultiEdit', 'Write', 'NotebookEdit'})
VERBS = {'Read': 'Read', 'Edit': 'Edited', 'MultiEdit': 'Edited', 'Write': 'Wrote', 'NotebookEdit': 'Edited',
         'Bash': 'Ran', 'Grep': 'Searched for', 'Glob': 'Looked for', 'WebFetch': 'Fetched', 'WebSearch': 'Searched the web for',
         'Task': 'Handed work to a helper', 'Agent': 'Handed work to a helper', 'TodoWrite': 'Updated its to-do list',
         'AskUserQuestion': 'Asked you', 'ExitPlanMode': 'Proposed a plan', 'BashOutput': 'Checked a command',
         'shell': 'Ran', 'exec_command': 'Ran', 'local_shell': 'Ran', 'write_stdin': 'Typed into a command',
         'update_plan': 'Updated its plan', 'view_image': 'Looked at', 'request_user_input': 'Asked you'}
PATCH_FILE = re.compile(r'^\*\*\* (Update|Add|Delete) File: (.+)$', re.M)


# ---------------------------------------------------------------- consent, per project, off by default
def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS project_outside_sessions (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')


def consent(db, project_id):
    """{enabled, asked}: off until you turn it on. ``asked`` is whether you've answered the offer either way, so Catch
    up offers it once. The row has a project_id, so deleting the project removes it (project_lifecycle.delete_rows)."""
    schema(db)
    row = db.execute('SELECT document FROM project_outside_sessions WHERE project_id=?', (project_id,)).fetchone()
    try:
        document = json.loads(row[0]) if row else {}
    except ValueError:
        document = {}
    return dict(enabled=isinstance(document, dict) and document.get('enabled') is True, asked=row is not None)


def set_consent(db, project_id, enabled, actor):
    if type(enabled) is not bool:
        raise Rejected('Reading sessions from outside Dervo is exactly on (true) or off (false)')
    schema(db)
    with db:
        db.execute('INSERT INTO project_outside_sessions VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                   (project_id, json.dumps(dict(enabled=enabled, actor=actor, at=int(time.time())))))
    return consent(db, project_id)


# ---------------------------------------------------------------- what the records say, in order
def _clip(text, limit):
    text = ' '.join(text.split()) if isinstance(text, str) else ''
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0] if ' ' in text[:limit] else text[:limit]
    return cut.rstrip(' ,.;:') + '…'


def _shown(text, limit):
    """Session text on its way to the screen: secrets redacted first, then one line, clipped."""
    return _clip(secret_scan.redact(text), limit) if isinstance(text, str) and text.strip() else None


def _place(path, real):
    """A file path as the row says it: from the project folder when it's inside, from ~ when it's in your home."""
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip()
    full = os.path.normpath(os.path.join(real, path)) if not os.path.isabs(path) else os.path.normpath(path)
    if full == real or full.startswith(real + os.sep):
        return os.path.relpath(full, real)
    home = str(Path.home())
    return '~' + full[len(home):] if full.startswith(home + os.sep) else full


def _command(value):
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        if len(value) >= 3 and value[1] in ('-lc', '-c') and os.path.basename(value[0]) in ('bash', 'zsh', 'sh'):
            return value[2]
        return ' '.join(value)
    return value if isinstance(value, str) else None


def _arguments(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {'input': value}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _patched(text):
    """[(verb, path)] from an apply_patch body, in order."""
    if not isinstance(text, str):
        return []
    verbs = {'Update': 'Edited', 'Add': 'Wrote', 'Delete': 'Deleted'}
    return [(verbs[kind], name.strip()) for kind, name in PATCH_FILE.findall(text)]


def step_of(name, args, real):
    """(verb, target, files) for one tool call: what the row says it did last, and the files it changed."""
    args = _arguments(args)
    if name == 'apply_patch':
        patched = _patched(args.get('input') if isinstance(args.get('input'), str) else args.get('patch'))
        files = [_place(path, real) for _, path in patched]
        if patched:
            return patched[-1][0], files[-1], files
        return 'Edited', None, []
    verb = VERBS.get(name)
    if name in ('Read', 'Edit', 'MultiEdit', 'Write', 'NotebookEdit', 'view_image'):
        target = _place(args.get('file_path') or args.get('notebook_path') or args.get('path'), real)
        return verb, target, [target] if target and name in EDITS else []
    if name in ('Bash', 'shell', 'exec_command', 'local_shell'):
        return verb, _command(args.get('command') or args.get('cmd')), []
    if name in ('Grep', 'Glob'):
        return verb, args.get('pattern'), []
    if name == 'WebFetch':
        return verb, urlsplit(args.get('url') or '').netloc or None, []
    if name == 'WebSearch':
        return verb, args.get('query'), []
    if verb:
        return verb, None, []
    if isinstance(name, str) and name.startswith('mcp__'):
        return 'Used', name.split('__', 2)[-1], []
    return 'Used', name if isinstance(name, str) else None, []


def _question(name, args):
    args = _arguments(args)
    questions = args.get('questions')
    if isinstance(questions, list) and questions and isinstance(questions[0], dict):
        return questions[0].get('question')
    for key in ('question', 'prompt', 'message'):
        if isinstance(args.get(key), str):
            return args[key]
    return None


class Trail:
    """The end of one transcript, fed in order. Each event moves ``stand``; ``standing`` reads it at the end."""

    def __init__(self, provider, real):
        self.provider, self.real = provider, real
        self.asked = self.asked_at = self.last_at = self.cwd = None
        self.stand, self.detail = None, None
        self.pending = {}
        self.last_kind = None
        self.step = None
        self.files = []
        self.errored = 0

    def seen(self, at):
        if at is not None:
            self.last_at = max(self.last_at or at, at)

    def ask(self, text, at):
        self.asked, self.asked_at = text, at
        self.stand, self.detail, self.last_kind = 'unknown', None, 'ask'
        self.pending.clear()

    def stop(self):
        self.stand, self.detail, self.last_kind = 'stopped', None, 'stop'
        self.pending.clear()

    def text(self):
        if not self.pending:
            self.stand, self.detail, self.last_kind = 'finished', None, 'text'

    def done(self):
        if not self.pending:
            self.stand, self.detail, self.last_kind = 'finished', None, 'done'

    def call(self, call_id, name, args):
        self.pending[call_id if isinstance(call_id, str) else f'call-{len(self.pending)}'] = (name, args)
        self.stand, self.detail, self.last_kind = 'working', None, 'call'
        verb, target, files = step_of(name, args, self.real)
        self.step = dict(verb=verb, target=target)
        for path in files:
            if path in self.files:
                self.files.remove(path)
            self.files.insert(0, path)

    def result(self, call_id, refused=False):
        self.pending.pop(call_id, None)
        if refused:
            self.stop()
        else:
            self.stand, self.last_kind = 'working', 'result'

    def error(self, text, kind=None):
        self.stand = 'limit' if LIMIT.search(f'{kind or ""} {text or ""}') else 'error'
        self.detail, self.last_kind = text, 'error'
        self.pending.clear()

    def standing(self):
        """(state, detail) from what came last. A call with no result is the only thing read into more than one way,
        and only by which tool it was: see CLAUDE_ASKS."""
        if self.stand in (None, 'working'):
            if self.pending and self.last_kind == 'call':
                name, args = list(self.pending.values())[-1]
                if name in QUESTION_TOOLS:
                    return 'question', _question(name, args)
                asks = CLAUDE_ASKS if self.provider == 'claude' else CODEX_ASKS
                if name in asks or (isinstance(name, str) and name.startswith('mcp__')):
                    return 'approval', None
            return 'unknown', None
        return self.stand, self.detail


def _text_parts(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and isinstance(part.get('text'), str))
    return ''


def claude_record(trail, record):
    """One Claude Code transcript line into ``trail``. What you typed is decided by ``history_import.typed_text``."""
    if not isinstance(record, dict):
        return
    if isinstance(record.get('cwd'), str) and record['cwd'] and not record.get('isSidechain'):
        trail.cwd = trail.cwd or record['cwd']
    kind = record.get('type')
    if kind not in ('user', 'assistant') or record.get('isSidechain'):
        return
    trail.seen(hi._when(record.get('timestamp')))
    message = record.get('message') if isinstance(record.get('message'), dict) else {}
    content = message.get('content')
    if kind == 'assistant':
        if record.get('isApiErrorMessage') is True or message.get('model') == '<synthetic>':
            # Claude Code's own report of a failed API call ("API Error: 529", a usage limit). Its other synthetic
            # notes ("No response requested.") say nothing about the session.
            text = _text_parts(content).strip()
            if record.get('isApiErrorMessage') is True or text.startswith('API Error') or LIMIT.search(text):
                trail.error(text, record.get('error'))
            return
        blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content}] if isinstance(content, str) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                trail.call(block.get('id'), block.get('name'), block.get('input'))
            elif block.get('type') == 'text' and isinstance(block.get('text'), str) and block['text'].strip():
                trail.text()
        return
    if isinstance(content, list) and any(isinstance(p, dict) and p.get('type') == 'tool_result' for p in content):
        for part in content:
            if isinstance(part, dict) and part.get('type') == 'tool_result':
                refused = part.get('is_error') is True and bool(REFUSED.match(_text_parts(part.get('content'))))
                trail.result(part.get('tool_use_id'), refused)
        return
    if INTERRUPT.match(_text_parts(content)):
        trail.stop()
        return
    text = hi.typed_text(record)
    if text is not None:
        trail.ask(text, hi._when(record.get('timestamp')))


def codex_record(trail, record, events):
    """One Codex rollout line into ``trail``. ``events`` says which layout carries the messages, as
    ``history_import._codex_steps`` decides it: the user_message and agent_message events when the window has any,
    the response_item messages otherwise. A message that opens with a tag is Codex's own context, not yours."""
    if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
        return
    payload, kind = record['payload'], record.get('type')
    at = hi._when(record.get('timestamp'))
    ptype = payload.get('type')
    if kind in ('session_meta', 'turn_context') and isinstance(payload.get('cwd'), str):
        trail.cwd = trail.cwd or payload['cwd']
        return
    trail.seen(at)
    if kind == 'event_msg':
        if ptype == 'user_message' and events:
            text = payload.get('message')
            if isinstance(text, str) and text.strip() and not text.lstrip().startswith('<'):
                trail.ask(text.strip(), at)
        elif ptype == 'agent_message' and events:
            trail.text()
        elif ptype == 'task_complete':
            trail.done()
        elif ptype == 'turn_aborted':
            trail.stop()
        elif ptype == 'error':
            info = payload.get('codex_error_info')
            info = next(iter(info), None) if isinstance(info, dict) else info
            trail.error(payload.get('message') if isinstance(payload.get('message'), str) else '', info if isinstance(info, str) else None)
    elif kind == 'response_item':
        if ptype == 'message' and not events:
            text = _text_parts(payload.get('content')).strip()
            if payload.get('role') == 'user':
                if text and not text.startswith('<'):
                    trail.ask(text, at)
            elif text:
                trail.text()
        elif ptype in ('function_call', 'custom_tool_call'):
            trail.call(payload.get('call_id'), payload.get('name'), payload.get('arguments', payload.get('input')))
        elif ptype == 'local_shell_call':
            action = payload.get('action') if isinstance(payload.get('action'), dict) else {}
            trail.call(payload.get('call_id'), 'local_shell', {'command': action.get('command')})
        elif ptype in ('function_call_output', 'custom_tool_call_output'):
            trail.result(payload.get('call_id'))


# ---------------------------------------------------------------- reading files, a head and a tail
def _records(data, partial_start):
    """Parsed lines of a byte window. The first line of a window that starts mid-file is cut, so it's dropped; so is
    anything that isn't JSON."""
    lines = data.split(b'\n')
    if partial_start and lines:
        lines = lines[1:]
    for line in lines:
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


class Unreadable(Exception):
    pass


class Budget:
    def __init__(self, clock, byte_budget=BYTE_BUDGET, time_budget=TIME_BUDGET):
        self.clock, self.bytes, self.until = clock, byte_budget, clock() + time_budget

    def open(self):
        return self.bytes > 0 and self.clock() < self.until

    def spend(self, count):
        self.bytes -= count


def _window(path, start, length, budget):
    try:
        with open(path, 'rb') as handle:
            handle.seek(start)
            data = handle.read(length)
    except OSError as error:
        raise Unreadable(str(path)) from error
    budget.spend(len(data))
    return data


class Reader:
    """Per host: the cache of what each file said, keyed by path and checked against its inode, size and mtime."""

    def __init__(self, claude_home=None, codex_home=None, clock=time.monotonic, now=time.time):
        self._claude_home, self._codex_home = claude_home, codex_home
        self.clock, self.now = clock, now
        self.files = {}
        self.verdicts = {}
        self.copies = (None, {})

    @property
    def claude_home(self):
        return Path(self._claude_home) if self._claude_home is not None else hi.CLAUDE_HOME

    @property
    def codex_home(self):
        return Path(self._codex_home) if self._codex_home is not None else hi.CODEX_HOME

    # ------------------------------------------------------------ one file
    def _read(self, key, path, stat, provider, real, head_ask=None, budget=None):
        """The row fields for one transcript, from the cache when the file is as it was. None when it isn't a
        session of this folder or holds nothing you asked."""
        stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self.files.get(key)
        if cached and cached['stamp'] == stamp:
            return cached['row']
        if not budget.open():
            raise TimeoutError
        head = cached['head'] if cached and cached['stamp'][0] == stat.st_ino and cached['stamp'][1] <= stat.st_size else None
        if head is None and head_ask:
            head = dict(cwd=None, ask=None, at=None)  # Codex's thread list already holds the first thing you typed
        if head is None:
            head = self._head(path, stat.st_size, provider, real, budget)
        trail = None
        for step in TAIL_STEPS:
            start = max(0, stat.st_size - step)
            trail = Trail(provider, real)
            records = list(_records(_window(path, start, stat.st_size - start, budget), start > 0))
            events = provider == 'codex' and any(isinstance(r, dict) and r.get('type') == 'event_msg' and isinstance(r.get('payload'), dict)
                                                  and r['payload'].get('type') in ('user_message', 'agent_message') for r in records)
            for record in records:
                (claude_record(trail, record) if provider == 'claude' else codex_record(trail, record, events))
            if trail.asked or start == 0 or not budget.open():
                break
        cwd = head['cwd'] or trail.cwd
        row = None
        if provider == 'codex' or (cwd and os.path.realpath(cwd) == real):
            first = head_ask or head['ask']
            asked = trail.asked or (first if stat.st_size <= HEAD_BYTES else None)
            if first or asked:
                state, detail = trail.standing()
                row = dict(first=first, first_at=head['at'], asked=asked,
                           asked_at=trail.asked_at if trail.asked else head['at'] if asked else None,
                           last_at=trail.last_at or stat.st_mtime, state=state, detail=detail, step=trail.step, files=trail.files[:FILES_LIMIT])
        self.files[key] = dict(stamp=stamp, head=head, row=row)
        if len(self.files) > CACHE_LIMIT:
            self.files.pop(next(iter(self.files)))
        return row

    def _head(self, path, size, provider, real, budget):
        """The session's cwd and the first thing you asked, from its first HEAD_BYTES."""
        data = _window(path, 0, min(size, HEAD_BYTES), budget)
        trail = Trail(provider, real)
        first = None
        records = list(_records(data, False))
        events = provider == 'codex' and any(isinstance(r, dict) and isinstance(r.get('payload'), dict)
                                             and r['payload'].get('type') == 'user_message' for r in records)
        for record in records:
            (claude_record(trail, record) if provider == 'claude' else codex_record(trail, record, events))
            if first is None and trail.asked:
                first = (trail.asked, trail.asked_at)
            if first and trail.cwd:
                break
        return dict(cwd=trail.cwd, ask=first[0] if first else None, at=first[1] if first else None)

    # ------------------------------------------------------------ the two tools
    def claude_candidates(self, real, since):
        folder = hi.claude_project_dir(real, self.claude_home)
        found = []
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return found
        for entry in entries:
            if not (entry.name.endswith('.jsonl') and hi.UUID.fullmatch(entry.name[:-6])):
                continue
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                stat = entry.stat()
            except OSError:
                continue
            if stat.st_mtime >= since:
                found.append(dict(provider='claude', id=entry.name[:-6], path=Path(entry.path), stat=stat, at=stat.st_mtime))
        found.sort(key=lambda c: c['at'], reverse=True)
        return found[:CANDIDATES]

    def codex_candidates(self, real, given, since):
        """(candidates, readable): Codex threads of this folder a person started, newest first. ``readable`` is False
        when the thread list exists and couldn't be opened."""
        state = self.codex_home / hi.CODEX_STATE
        if not state.is_file():
            return [], True
        try:
            db = sqlite3.connect(f'file:{state}?mode=ro', uri=True, timeout=0.2)
        except sqlite3.Error:
            return [], False
        try:
            columns = {row[1] for row in db.execute('PRAGMA table_info(threads)')}
            first = 'first_user_message' if 'first_user_message' in columns else 'NULL'
            rows = db.execute(f'SELECT id, rollout_path, updated_at, archived, source, cwd, {first} FROM threads '
                              'WHERE cwd IN (?, ?) ORDER BY updated_at DESC LIMIT ?', (real, given, CANDIDATES * 4)).fetchall()
        except sqlite3.Error:
            return [], False
        finally:
            db.close()
        home = os.path.realpath(self.codex_home)
        copies = self._copies()
        found = []
        for thread_id, rollout, updated, archived, source, cwd, typed in rows:
            if (not isinstance(thread_id, str) or archived or source not in hi.CODEX_OWN_SOURCES or thread_id in copies
                    or not isinstance(cwd, str) or os.path.realpath(cwd) != real):
                continue
            at = hi._when(updated) or 0
            if at < since or not isinstance(rollout, str):
                continue
            path = os.path.realpath(rollout)
            if not path.startswith(home + os.sep):
                continue  # only rollouts inside the Codex folder are opened
            ask = typed.strip() if isinstance(typed, str) and typed.strip() and not typed.lstrip().startswith('<') else None
            found.append(dict(provider='codex', id=thread_id, path=Path(path), at=at, ask=ask))
            if len(found) >= CANDIDATES:
                break
        return found, True

    def _copies(self):
        """Codex thread ids that are Codex's copies of Claude Code sessions (history_import.codex_imports), read again
        only when the file changes."""
        path = self.codex_home / 'external_agent_session_imports.json'
        try:
            stat = path.stat()
            stamp = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            return {}
        if self.copies[0] != stamp:
            self.copies = (stamp, hi.codex_imports(self.codex_home))
        return self.copies[1]

    # ------------------------------------------------------------ Dervo's own
    def dervo_started(self, db, ids):
        """The ids among ``ids`` that Dervo holds: a thread's session (a chat turn, or one the history import brought
        in) or a coding task's. Threads are asked every time; the task store is asked again after VERDICT_SECONDS."""
        ids = [i for i in dict.fromkeys(ids) if isinstance(i, str)]
        if not ids:
            return set()
        marks = ','.join('?' * len(ids))
        found = {row[0] for row in db.execute(f'SELECT session_id FROM threads WHERE session_id IN ({marks})', ids)}
        now = self.clock()
        stale = [i for i in ids if i not in self.verdicts or now - self.verdicts[i][1] > VERDICT_SECONDS]
        if stale:
            owned = _task_sessions(db, stale)
            for i in stale:
                self.verdicts[i] = (i in owned, now)
            if len(self.verdicts) > CACHE_LIMIT * 4:
                for key in list(self.verdicts)[:len(self.verdicts) - CACHE_LIMIT * 4]:
                    self.verdicts.pop(key)
        return found | {i for i in ids if self.verdicts.get(i, (False,))[0]}

    # ------------------------------------------------------------ one refresh
    def read(self, cwd, db):
        """dict(sessions, partial, unreadable) for one folder: up to SHOWN rows, newest first."""
        real = os.path.realpath(cwd)
        since = self.now() - WINDOW_DAYS * 86400
        budget = Budget(self.clock)
        codex, codex_readable = self.codex_candidates(real, str(cwd), since)
        candidates = sorted(self.claude_candidates(real, since) + codex, key=lambda c: c['at'], reverse=True)
        mine = self.dervo_started(db, [c['id'] for c in candidates])
        rows, partial, unreadable = [], False, 0 if codex_readable else 1
        for candidate in candidates:
            if len(rows) >= SHOWN:
                break
            if candidate['id'] in mine:
                continue
            key = f"{candidate['provider']}:{candidate['path']}"
            try:
                stat = candidate.get('stat') or os.stat(candidate['path'])
                row = self._read(key, candidate['path'], stat, candidate['provider'], real, candidate.get('ask'), budget)
            except TimeoutError:
                partial = True
                continue
            except (Unreadable, OSError):
                unreadable += 1
                continue
            if row:
                rows.append(self._row(candidate, row))
        rows = _without_forks(rows)
        rows.sort(key=lambda r: r['last_at'] or 0, reverse=True)
        return dict(sessions=rows, partial=partial, unreadable=unreadable)

    @staticmethod
    def _row(candidate, row):
        """What leaves this module: every piece of session text redacted and clipped."""
        step = row['step']
        return dict(id=f"{candidate['provider']}:{candidate['id']}", provider=candidate['provider'],
                    title=_shown(row['first'] or row['asked'], TITLE_LIMIT), asked=_shown(row['asked'], ASK_LIMIT),
                    asked_at=row['asked_at'], last_at=max(row['last_at'] or 0, candidate['at'] if candidate['provider'] == 'codex' else 0) or None,
                    state=row['state'], detail=_shown(row['detail'], DETAIL_LIMIT),
                    step=dict(verb=step['verb'], target=_shown(step['target'], TARGET_LIMIT)) if step else None,
                    files=[_shown(path, TARGET_LIMIT) for path in row['files']],
                    _start=(row['first'], row['first_at']))


def _task_sessions(db, ids):
    """The ids among ``ids`` that a coding task or one of its runs carries as its provider session. One pass over the
    task store in SQLite; a store the JSON functions can't read is scanned in Python, and only the documents that
    mention an id at all are parsed."""
    marks = ','.join('?' * len(ids))
    valid = "CASE WHEN json_valid({0}) THEN {0} ELSE '{{}}' END"
    run = "CASE WHEN r.type='object' THEN r.value ELSE '{}' END"
    try:
        found = {row[0] for row in db.execute(
            f"SELECT json_extract({valid.format('document')}, '$.session_id') AS s FROM coding_tasks WHERE s IN ({marks})", ids)}
        found |= {row[0] for row in db.execute(
            f"SELECT json_extract({run}, '$.session_id') AS s FROM coding_tasks c, json_each({valid.format('c.document')}, '$.runs') r "
            f"WHERE s IN ({marks})", ids)}
        return found
    except sqlite3.OperationalError as error:
        if 'no such table' in str(error):
            return set()  # a store without coding tasks yet
    except sqlite3.Error:
        pass
    found = set()
    try:
        rows = db.execute('SELECT document FROM coding_tasks WHERE ' + ' OR '.join('instr(document, ?) > 0' for _ in ids), ids)
        for (document,) in rows:
            try:
                task = json.loads(document)
            except (TypeError, ValueError):
                continue
            if not isinstance(task, dict):
                continue
            carried = [task.get('session_id')] + [r.get('session_id') for r in task.get('runs') or [] if isinstance(r, dict)]
            found |= {i for i in carried if i in ids}
    except sqlite3.Error:
        return set()
    return found


def _without_forks(rows):
    """A resumed-and-forked Claude Code session is a second file that opens with the same first message at the same
    moment (history_import.fold_forks). Only the newest of them is a row."""
    newest = {}
    for row in rows:
        start = row.pop('_start')
        key = start if row['provider'] == 'claude' and start[0] and start[1] else (row['id'],)
        if key not in newest or (row['last_at'] or 0) > (newest[key]['last_at'] or 0):
            newest[key] = row
    return list(newest.values())


# ---------------------------------------------------------------- the operations
def _project(host, project_id):
    if type(project_id) is not str or not project_id:
        raise Rejected('Choose a project')
    row = host.store.db.execute('SELECT id, cwd FROM projects WHERE id=?', (project_id,)).fetchone()
    if not row:
        raise Rejected('Unknown project')
    return row[0], row[1]


def dispatch(host, operation, args, actor='local-user'):
    if type(args) is not dict or set(args) != FIELDS.get(operation):
        raise Rejected('Unsupported outside sessions fields')
    project_id, cwd = _project(host, args['projectId'])
    db = host.store.db
    if operation == 'outside_sessions_consent':
        return consent(db, project_id)
    if operation == 'outside_sessions_consent_set':
        return set_consent(db, project_id, args['enabled'], actor)
    agreed = consent(db, project_id)
    if not agreed['enabled']:
        return dict(agreed, sessions=[], partial=False, unreadable=0, at=time.time())  # nothing is read until it's on
    if not hasattr(host, 'outside_sessions'):
        host.outside_sessions = Reader()
    return dict(agreed, **host.outside_sessions.read(cwd, db), at=time.time())
