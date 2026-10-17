"""Per-conversation task status for the sidebar: running, needs you, unread. Read from saved task records only.

The index is written inside the same transaction that saves a task, so the sidebar never shows a state the task
record doesn't hold. "Needs you" means the task is waiting on a human decision or failed; "running" means the owner
or its independent review is still being driven by the host. Unread counts settled task results newer than the last
time the conversation was opened on this Mac.
"""
import json
import sqlite3
import re
import time
from chat_links import your_words

LIVE_TOKENS = ('executing', 'running', 'queued', 'delivering')
REVIEW_OPEN = ('pending', 'initial', 'correction', 'final')
NEEDS_STATES = ('blocked', 'disconnected', 'needs_permission', 'ready_for_review')
FILE_TOOLS = ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_create_file')
STEP_WORDS = {
    'dervo_read_file': 'Reading', 'dervo_write_file': 'Editing', 'dervo_edit_file': 'Editing', 'dervo_create_file': 'Creating',
    'dervo_list_files': 'Listing files', 'dervo_search_files': 'Searching project files',
    'dervo_run_tests': 'Running the check', 'dervo_request_help': 'Asking a helper',
    'dervo_suggest_lesson': 'Suggesting a lesson', 'dervo_update_task_guide': 'Updating the task guide',
    'dervo_remember': 'Saving to project memory', 'dervo_propose_skill': 'Proposing a skill', 'dervo_search_history': 'Looking through earlier chats',
    # Full access: Claude Code's own tools.
    'native:Bash': 'Running', 'native:Read': 'Reading', 'native:Edit': 'Editing', 'native:Write': 'Writing',
    'native:Glob': 'Finding files', 'native:Grep': 'Searching', 'native:WebFetch': 'Fetching',
    'native:Connector': 'Using',  # one of your MCP servers or claude.ai connectors, named on the token
    'native:WebSearch': 'Searching the web',  # either provider, any mode (PLAN-native A5)
}
NATIVE_FILES = ('native:Read', 'native:Edit', 'native:Write')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS conversation_task_state (task_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, '
               'status TEXT NOT NULL, step TEXT, changed REAL NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS conversation_task_state_thread ON conversation_task_state(thread_id, changed)')
    # What the running agent last reported: its goal, why this step matters and what comes next. Its own table, so an
    # older Dervo opened on this workspace still writes conversation_task_state with the five columns it knows.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_task_brief (task_id TEXT PRIMARY KEY, goal TEXT, why TEXT, next TEXT, request TEXT)')
    # The one sentence a settled task is worth, written when the task is saved so every screen that shows a conversation
    # says the same thing without re-reading the task document. Its own table for the same reason the brief has one.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_task_summary (task_id TEXT PRIMARY KEY, summary TEXT)')
    # How the task stands in its own words (ready_for_review, blocked, disconnected, answered..., or 'asking', see
    # end_of), so the sidebar can
    # draw a finished check or a failed cross instead of one "needs you" mark for all of them (19 Sep). Its own table,
    # so beta.7 opened on this workspace still writes the tables it knows.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_task_end (task_id TEXT PRIMARY KEY, state TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS conversation_seen (thread_id TEXT PRIMARY KEY, seen REAL NOT NULL)')
    # When you were last in a conversation before this visit, for the pickup at its top ("you last opened this at 10:50
    # PM"). conversation_seen moves on every look while it's on screen, so it can't say that by itself.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_visits (thread_id TEXT PRIMARY KEY, previous REAL)')
    # "Needs you" you've cleared by hand: a task that needed you before this time no longer does.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_dismissed (thread_id TEXT PRIMARY KEY, at REAL NOT NULL)')
    # When Dervo was open: one row per start and close, for "since you were last here".
    # The state spine (8 Oct 2026): each conversation's place in one rising order of changes. Every write that changes
    # what a conversation's row shows takes the next number, so the window asks for "everything after N" (changes) and
    # gets only the rows that moved, instead of re-reading every list on a timer.
    db.execute('CREATE TABLE IF NOT EXISTS thread_versions (thread_id TEXT PRIMARY KEY, version INTEGER NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS thread_versions_version ON thread_versions(version)')
    # The newest number itself, so it never goes back when a deleted conversation takes its row with it.
    db.execute('CREATE TABLE IF NOT EXISTS thread_version_seq (id INTEGER PRIMARY KEY CHECK (id = 1), value INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS session_marks (kind TEXT NOT NULL CHECK(kind IN (\'start\',\'end\')), at REAL NOT NULL)')


def _basename(path):
    if not isinstance(path, str):
        return None
    name = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    name = ''.join(c for c in name if c.isprintable())
    return name[:48] if name and name not in ('.', '..') else None


def stopped_by_user(task):
    """You ended the run yourself (Esc, Stop): its newest run says interrupted, or the error is Claude Code's own stop
    sentence. Nothing waits on you after your own stop, so it isn't a need (orchestrator map, 19 Sep, F5)."""
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    error = task.get('error') if isinstance(task.get('error'), str) else ''
    return (bool(runs) and runs[-1].get('state') == 'interrupted') or error.startswith('Stopped before completion')


def status_of(task):
    """One word for where a saved task stands, from its record only."""
    state = task.get('state')
    review = ((task.get('collaboration') or {}).get('review') or {}).get('phase')
    if state == 'working' or review in REVIEW_OPEN or _checking(task):
        return 'running'
    if any(isinstance(r, dict) and r.get('state') == 'uncertain' for r in task.get('runs') or []):
        return 'needs'
    if state in NEEDS_STATES:
        if state == 'blocked' and not stopped_by_user(task):
            # Beta.18: a stop that's settled (Claude Code slow to exit after your Stop, Leave it stopped, a pause Dervo
            # settled after a restart) waits on nothing, the same as your own Stop (run_ending ``stopped``).
            import run_ending
            if (run_ending.classify(task) or {}).get('kind') == 'stopped':
                return 'settled'
        return 'settled' if state == 'blocked' and stopped_by_user(task) else 'needs'
    return 'needs' if asks_you(task) else 'settled'


# A closing question counts only when it's put to you. Stock offers ("Want me to add a test too?") end most answers,
# so an offer with no alternative in it doesn't count (auto-sorting #5, eval cases 13 to 16). A question is put to you
# when it says so ("you", "should I"), when it opens like a yes-or-no question ("Should the greeting be Hi?", "Is the
# cart page in scope?") or when it offers a choice ("Hi or Hello?"); a wh-question with none of those ("Why does this
# fail?") is the agent thinking aloud (hard case s45, 21 Sep).
_FENCE = re.compile(r'^[ \t]{0,3}(```|~~~)')
_TO_YOU = re.compile(r"\b(you|your|you're|yours|should i|shall i|should we|shall we|do i|can i)\b")
_YES_OR_NO = re.compile(r"^(?:should|shall|could|would|will|can|may|do|does|did|is|are|was|were|has|have|which)\b")
_OFFER = re.compile(r"\b(want me to|would you like me to|do you want me to|shall i|anything else|let me know|any (other |more )?questions|"
                    r"does (that|this) (help|make sense|work|look right)|is (that|this) (ok|okay|alright|what you wanted))\b")
_SENTENCE_END = re.compile(r'(?<=[.!?])\s+')
# What an agent says after a question it hasn't answered itself: what it did for now, pending your word (hard case
# s43, 21 Sep: "Should I migrate the old rows too, or leave them? For now I migrated only the new rows."). "I went ahead
# and did it anyway" answers the question, so it isn't in here.
_PROVISIONAL = re.compile(r"\b(for now|for the moment|in the meantime|meanwhile|until (?:you|then|i hear)|so far|not yet|"
                          r"left (?:it|them|that|this|the \w+(?: \w+)?) (?:alone|as (?:is|it was|they were)|untouched|unchanged)|"
                          r"(?:haven't|have not|didn't|did not) (?:touched|changed|migrated|moved|removed|deleted|decided)|pending your|waiting (?:on|for) you)\b")
_TRAILING_SENTENCES = 2


def _put_to_you(sentence):
    """Whether a sentence ending in "?" is a question for the reader, by its words alone."""
    lowered = sentence.casefold().replace('’', "'")
    if _OFFER.search(lowered) and not re.search(r'\bor\b', lowered):
        return False
    return bool(_TO_YOU.search(lowered) or _YES_OR_NO.match(lowered) or re.search(r'\bor\b', lowered))


def closing_question(text):
    """The question an answer ends on, when it's a real one put to you: the last sentence of the last paragraph, outside
    code fences, block quotes and inline code, ends in "?" (not a quoted "?"), is put to you (``_put_to_you``) and isn't
    a stock offer. A question one or two sentences before the end still counts when everything after it only says what
    the agent did for now (``_PROVISIONAL``); a question the agent went on to answer doesn't. None otherwise. "Why does
    this fail? Because ..." ends on the answer, so it's None."""
    if not isinstance(text, str) or '?' not in text:
        return None
    paragraphs, current, fenced = [], [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            if current:
                paragraphs.append((False, current))
            if not fenced:
                paragraphs.append((True, [line]))  # a code block counts as one paragraph of its own
            current, fenced = [], not fenced
            continue
        if fenced:
            continue
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append((False, current))
            current = []
    if current:
        paragraphs.append((False, current))
    if fenced or not paragraphs or paragraphs[-1][0]:
        return None  # the answer ends in (or inside) code
    lines = paragraphs[-1][1]
    if any(line.lstrip().startswith('>') for line in lines):
        return None  # a quote, not the agent speaking
    prose = re.sub(r'`[^`]*`', ' ', ' '.join(line.strip() for line in lines))
    prose = prose.strip().rstrip('*_ ').strip()
    sentences = [s.lstrip('-*+ ').strip().rstrip('*_ ').strip() for s in _SENTENCE_END.split(prose)]
    sentences = [s for s in sentences if s]
    if not sentences:
        return None
    if not sentences[-1].endswith('?'):
        # The question may sit just before a "for now I ..." sentence or two; anything else after it is its answer.
        trailing = []
        while sentences and not sentences[-1].endswith('?') and len(trailing) < _TRAILING_SENTENCES:
            trailing.append(sentences.pop())
        if not sentences or not sentences[-1].endswith('?') or not all(_PROVISIONAL.search(s.casefold().replace('’', "'")) for s in trailing):
            return None
    last = sentences[-1]
    if last[:1] in '"“‘\'' or len(last) < 8:
        return None
    if not _put_to_you(last):
        return None
    return last


def asks_you(task):
    """A finished answer that ends on a real question for you (closing_question) and changed no files: the thread is
    waiting on you, so it reads 'asking' rather than answered. A run that changed files is ready for review instead,
    whatever it ends on."""
    if task.get('state') not in ('answered', 'completed'):
        return False
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    if any(isinstance(f, str) and f for f in completion.get('changed_files') or []):
        return False
    messages = [m for m in task.get('messages') or [] if isinstance(m, dict)]
    if not messages or messages[-1].get('role') != 'assistant':
        return False  # you've said something since
    return closing_question(messages[-1].get('text')) is not None


def _checking(task):
    """A full-access result whose own checks are still running (project_checks)."""
    runs = task.get('runs') or []
    last = runs[-1] if runs and isinstance(runs[-1], dict) else {}
    return task.get('state') != 'working' and isinstance(last.get('checks'), dict) and last['checks'].get('state') == 'running'


def step_of(task):
    """The live step of the current run, as the rail would word it, or None."""
    if status_of(task) != 'running':
        return None
    if _checking(task):
        return 'Running checks'
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    run_id = runs[-1].get('id') if runs else None
    run_tokens = [t for t in task.get('tokens') or [] if isinstance(t, dict) and t.get('run_id') == run_id]
    tokens = [t for t in run_tokens if t.get('state') in LIVE_TOKENS]
    connectors = runs[-1].get('connectors') if runs and isinstance(runs[-1].get('connectors'), dict) else {}
    if connectors.get('state') == 'connecting' and not run_tokens:
        return 'Connecting connectors'  # dd #8: the first message waits for them (claude_coding.poll_connectors)
    if not tokens:
        phase = ((task.get('collaboration') or {}).get('review') or {}).get('phase')
        if phase in REVIEW_OPEN and task.get('state') != 'working':
            return 'Independent review in progress'
        guide = runs[-1].get('guide') if runs and isinstance(runs[-1].get('guide'), dict) else {}
        if run_tokens and guide.get('run_id') == run_id and isinstance(guide.get('step'), str) and guide['step'].strip():
            return _clean(guide['step'], 120)
        # Between tool calls the provider is thinking; once it has taken a step it isn't starting any more.
        return 'Working' if run_tokens else None
    # What the agent says it's doing, in its own words, beats the name of the tool call it's in the middle of.
    guide = runs[-1].get('guide') if isinstance(runs[-1].get('guide'), dict) else {}
    if guide.get('run_id') == run_id and isinstance(guide.get('step'), str) and guide['step'].strip():
        return _clean(guide['step'], 120)
    token = tokens[-1]
    word = STEP_WORDS.get(token.get('tool'))
    if not word:
        return None
    if token.get('tool') == 'native:Connector':
        return f"{word} {_clean(token.get('connector'), 60) or 'a connector'}"
    if token.get('tool') == 'native:Bash' and isinstance(token.get('command'), str):
        return f"{word} {' '.join(token['command'].split())[:60]}"
    name = _basename(token.get('path')) if token.get('tool') in FILE_TOOLS + NATIVE_FILES else None
    return f'{word} {name}' if name else word


def _clean(text, limit):
    if not isinstance(text, str):
        return None
    import voice
    text = ' '.join(''.join(c if c.isprintable() else ' ' for c in voice.plain(text)).split())
    return (text[:limit - 1] + '…' if len(text) > limit else text) or None


def brief_of(task):
    """While a task runs: the agent's own report of its goal, why its current step matters and what comes next, from
    dervo_update_task_guide for the current run only, plus your request. Reported words, never execution status."""
    if status_of(task) != 'running':
        return None, None, None, None
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    run = runs[-1] if runs else {}
    guide = run.get('guide') if isinstance(run.get('guide'), dict) else {}
    current = bool(run.get('id')) and guide.get('run_id') == run.get('id')
    request = next((m.get('text') for m in reversed(task.get('messages') or []) if isinstance(m, dict) and m.get('role') == 'user'), None)
    pick = lambda key, limit: _clean(guide.get(key), limit) if current else None
    return pick('goal', 160), pick('why', 240), pick('next', 240), _clean(your_words(request), 160)


def summary_of(task):
    """The sentence Catch up and the Progress footer already use, from the record only. Never a guess: a task whose
    outcome Dervo could not confirm says exactly that."""
    try:
        from catchup_summary import task_facts, task_summary
        from task_outcome import outcome_facts
        line = task_summary(task, task_facts(task, outcome_facts(task)))
        runs = task.get('runs') or []
        checks = runs[-1].get('checks') if runs and isinstance(runs[-1], dict) and isinstance(runs[-1].get('checks'), dict) else {}
        if checks.get('state') in ('passed', 'new_failures', 'unclear', 'not_run') and isinstance(checks.get('summary'), str):
            # What the checks found, in words, after what the run did (project_checks): "Checks pass", "1 new failure: x".
            # A new failure leads, so it's never the part a short row cuts off.
            base = line.rstrip('. ') + '.' if isinstance(line, str) and line.strip() else ''
            line = (checks['summary'] + '. ' + base if checks['state'] in ('new_failures', 'unclear') else (base + ' ' if base else '') + checks['summary'] + '.').strip()
        return _clean(line, 200)
    except Exception:
        # A summary is decoration on an index that must keep working. A task that can't be summarised still records
        # its status, which is what the sidebar and the room are built on.
        return None


def has_index(db):
    """Whether this store has the tables indexed reads need. A store that has never run a task has none of them."""
    return db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN "
                      "('coding_tasks', 'conversation_task_state', 'threads')").fetchone()[0] == 3


# Saved tasks this index doesn't file yet. A new conversation task's first save (conversation_coding.save) writes the
# record before CodingTasks.save indexes it, and a task with no thread is never indexed, so a read through the index
# always takes these too. There are normally none, and finding them is one lookup a task.
UNINDEXED = 'NOT EXISTS (SELECT 1 FROM conversation_task_state u WHERE u.task_id=c.id)'


def indexed_tasks(db, where, params=(), newest_first=False):
    """[(rowid, document)] of the saved tasks the index files under ``where`` (a condition on ``s``, the index row, and
    ``t``, its thread, which can be missing), plus every task the index doesn't file yet, in save order. Callers still
    check every condition on what comes back: this only spares them parsing every record (8 Oct: each full pass over
    1,300 records took 100 to 500 ms, several times before every message went to an agent). A store without the index
    gets every saved task, in the same order."""
    if not has_index(db):
        return db.execute(f'SELECT rowid, document FROM coding_tasks ORDER BY rowid {"DESC" if newest_first else "ASC"}').fetchall()
    return db.execute(
        'SELECT c.rowid, c.document FROM coding_tasks c JOIN conversation_task_state s ON s.task_id=c.id '
        f'LEFT JOIN threads t ON t.id=s.thread_id WHERE {where} '
        f'UNION ALL SELECT c.rowid, c.document FROM coding_tasks c WHERE {UNINDEXED} '
        f'ORDER BY 1 {"DESC" if newest_first else "ASC"}', params).fetchall()
def bump(db, thread_id):
    """Give this conversation the next number in the order of changes, inside the caller's transaction. Callers outside
    this module may run before the index exists: its tables are made then."""
    try:
        _bump(db, thread_id)
    except sqlite3.OperationalError:
        schema(db)
        _bump(db, thread_id)


def _bump(db, thread_id):
    db.execute('INSERT INTO thread_version_seq VALUES (1, (SELECT coalesce(max(version), 0) + 1 FROM thread_versions)) '
               'ON CONFLICT(id) DO UPDATE SET value=value + 1')
    db.execute('INSERT INTO thread_versions VALUES (?, (SELECT value FROM thread_version_seq WHERE id=1)) '
               'ON CONFLICT(thread_id) DO UPDATE SET version=excluded.version', (thread_id,))


def version(db):
    """The newest change's number, 0 before any."""
    row = db.execute('SELECT value FROM thread_version_seq WHERE id=1').fetchone()
    return row[0] if row else 0


def record(db, task):
    """Update the index inside the caller's transaction. `changed` moves only when the status or step moves. Anything
    the conversation's row shows that moved takes the next change number (bump)."""
    context = task.get('context') or {}
    thread = context.get('thread_id')
    if not isinstance(thread, str):
        return
    if _record(db, task, thread):
        bump(db, thread)


def _record(db, task, thread):
    """True when anything the row shows moved."""
    moved = False
    status, step = status_of(task), step_of(task)
    brief = brief_of(task)
    old_brief = db.execute('SELECT goal, why, next, request FROM conversation_task_brief WHERE task_id=?', (task['id'],)).fetchone()
    if (tuple(old_brief) if old_brief else (None, None, None, None)) != brief:
        moved = True
        db.execute('INSERT INTO conversation_task_brief VALUES (?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
                   'goal=excluded.goal, why=excluded.why, next=excluded.next, request=excluded.request', (task['id'], *brief))
    summary = summary_of(task)
    old_summary = db.execute('SELECT summary FROM conversation_task_summary WHERE task_id=?', (task['id'],)).fetchone()
    if (old_summary[0] if old_summary else None) != summary:
        moved = True
        db.execute('INSERT INTO conversation_task_summary VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET '
                   'summary=excluded.summary', (task['id'], summary))
    moved = _record_end(db, task) or moved
    old = db.execute('SELECT status, step FROM conversation_task_state WHERE task_id=?', (task['id'],)).fetchone()
    if old and old[0] == status and old[1] == step:
        return moved
    changed = time.time() if old is None or old[0] != status else db.execute(
        'SELECT changed FROM conversation_task_state WHERE task_id=?', (task['id'],)).fetchone()[0]
    db.execute('INSERT INTO conversation_task_state VALUES (?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET '
               'thread_id=excluded.thread_id, status=excluded.status, step=excluded.step, changed=excluded.changed',
               (task['id'], thread, status, step, changed))
    return True


def end_of(task):
    """The task's own state word, except that a task waiting on you whose agent's last reported next step is a question,
    or an answer that ends on a real question for you (asks_you), reads 'asking': the same rules Home's pickup uses for
    its one accent (pickup.question), so the sidebar's rust lands on the same thread."""
    state = task.get('state') if isinstance(task.get('state'), str) else None
    last = (task.get('runs') or [None])[-1]
    if state == 'blocked' and isinstance(last, dict) and last.get('end_cause') == 'restart_paused':
        return 'restart_paused'  # beta.18: Dervo settled it as paused after a restart (the sidebar says so, not "You paused this")
    if state == 'blocked' and stopped_by_user(task):
        return 'stopped'
    if state == 'blocked':
        # Beta.17.1 (run_ending): 'blocked' now means the work failed. A limit, a run that never started and a finished
        # run whose later Dervo step didn't complete each have their own word, so the sidebar draws no X for them.
        import run_ending
        kind = (run_ending.classify(task) or {}).get('kind')
        if kind in ('limit', 'not_started'):
            return kind
        if kind == 'stopped':
            # Beta.18: Leave it stopped says so; a Stop Claude Code was slow to honour is your stop. Not "Stopped with an error".
            return 'left' if (run_ending.classify(task) or {}).get('left') else 'stopped'
        if kind == 'finished':
            return 'snag'
    if state == 'idle' and task.get('review') == 'approved':
        return 'approved'  # approving settles the task as idle; the sidebar names it for how it ended (polish, 8 Oct)
    if asks_you(task):
        return 'asking'
    if status_of(task) == 'needs' and state != 'needs_permission':
        reported = fresh_next(task) or ''
        if state != 'idle' and reported.strip().endswith('?'):
            return 'asking'
    return state


GUIDE_TOOL = 'dervo_update_task_guide'


def fresh_next(task):
    """The agent's reported next step from its latest run's guide, only while it can still be true: nothing ran after
    the guide was written (a check that ran afterwards makes "Run the tests" stale) and you haven't said anything
    since (a reply makes its question stale). None otherwise. Home, the thread recap and the sidebar's 'asking' all
    use it, so they agree (handoff review, 19 Sep, #5)."""
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    run = runs[-1] if runs else {}
    guide = run.get('guide') if isinstance(run.get('guide'), dict) and run['guide'].get('run_id') == run.get('id') else {}
    reported = guide.get('next') if isinstance(guide.get('next'), str) else ''
    if not reported.strip():
        return None
    tokens = [t for t in task.get('tokens') or [] if isinstance(t, dict) and t.get('run_id') == run.get('id')]
    ids = [t.get('token') for t in tokens]
    updated = guide.get('updated_at') if isinstance(guide.get('updated_at'), (int, float)) else None
    if guide.get('tool_token') in ids:
        # Tool calls are saved in the order they came, so anything after the guide's own call happened after it.
        later = tokens[ids.index(guide['tool_token']) + 1:]
        if any(t.get('tool') != GUIDE_TOOL and t.get('state') == 'delivered' for t in later):
            return None
    elif updated is not None:
        # The guide is saved in whole seconds: only a call that ended a full second later is surely after it.
        if any(t.get('tool') != GUIDE_TOOL and t.get('state') == 'delivered' and isinstance(t.get('ended_at'), (int, float))
               and t['ended_at'] >= updated + 1 for t in tokens):
            return None
    if updated is not None and any(isinstance(m, dict) and m.get('role') == 'user' and isinstance(m.get('created_at'), (int, float))
                                   and m['created_at'] > updated for m in task.get('messages') or []):
        return None
    return reported


def _record_end(db, task):
    state = end_of(task)
    old = db.execute('SELECT state FROM conversation_task_end WHERE task_id=?', (task['id'],)).fetchone()
    if old is None or old[0] != state:
        db.execute('INSERT INTO conversation_task_end VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET state=excluded.state',
                   (task['id'], state))
        return True
    return False


def _backfill_ends(db):
    """Fill conversation_task_end for every task saved without one: the tasks from before the table existed, and any
    saved since by a Dervo that doesn't write it (beta.7 opened on this workspace). One query finds the missing ids,
    and only those documents are read. Only this table: the rest is already indexed."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        return
    missing = [row[0] for row in db.execute('SELECT t.id FROM coding_tasks t LEFT JOIN conversation_task_end e ON e.task_id=t.id '
                                            'WHERE e.task_id IS NULL')]
    if not missing:
        return
    with db:
        for task_id in missing:
            row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            try:
                task = json.loads(row[0]) if row else None
            except ValueError:
                continue
            if isinstance(task, dict) and isinstance(task.get('id'), str):
                _record_end(db, task)


# Beta.17.1: up to beta.17 every blocked task's end word was 'blocked', which the sidebar and Catch up draw as an X.
# end_of now gives a limit, a run that never started and a finished run with a Dervo step left undone their own words.
# 3 (8 Oct 2026): an approved task, saved as 'idle' until now, ends as 'approved'.
ENDS_VERSION = 3


def _resort_blocked_ends(db):
    """Once per workspace: re-sort the end words saved as 'blocked' before beta.17.1, so threads that stopped at a limit
    or never started don't keep their X until the task is saved again, and those saved as 'idle' before an approved task
    had its own word. Only those documents are read, once per version."""
    db.execute('CREATE TABLE IF NOT EXISTS conversation_task_end_version (version INTEGER NOT NULL)')
    row = db.execute('SELECT version FROM conversation_task_end_version').fetchone()
    if row and row[0] >= ENDS_VERSION:
        return
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        blocked = [r[0] for r in db.execute("SELECT task_id FROM conversation_task_end WHERE state IN ('blocked', 'idle')")]
        with db:
            for task_id in blocked:
                doc = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
                try:
                    task = json.loads(doc[0]) if doc else None
                except ValueError:
                    continue
                if isinstance(task, dict) and isinstance(task.get('id'), str):
                    _record_end(db, task)
    with db:
        db.execute('DELETE FROM conversation_task_end_version')
        db.execute('INSERT INTO conversation_task_end_version VALUES (?)', (ENDS_VERSION,))


def backfill(db):
    """Index tasks saved before this table existed. Runs once; later saves keep the index current."""
    _backfill_ends(db)
    _resort_blocked_ends(db)
    if db.execute('SELECT 1 FROM conversation_task_state LIMIT 1').fetchone():
        return
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        return
    rows = db.execute('SELECT document FROM coding_tasks').fetchall()
    if not rows:
        return
    with db:
        for (document,) in rows:
            try:
                task = json.loads(document)
            except ValueError:
                continue
            if isinstance(task, dict) and isinstance(task.get('id'), str):
                record(db, task)
        # Old results aren't new: treat everything indexed now as already seen.
        db.execute('INSERT OR IGNORE INTO conversation_seen SELECT DISTINCT thread_id, ? FROM conversation_task_state', (time.time(),))


def needs_you(latest_status, latest_changed, dismissed_at):
    """Only a conversation's newest task can need you. Sending another message moves on from a stopped one, and Dismiss
    clears it until something new happens."""
    return latest_status == 'needs' and latest_changed > (dismissed_at or 0)


LOST_LINE = 'Dervo restarted. It can’t tell how far this got.'


def session_start(db):
    """When this Dervo started, from the newest start mark, or None before the first mark."""
    return db.execute("SELECT max(at) FROM session_marks WHERE kind='start'").fetchone()[0]


def is_lost(status, changed, started):
    """A run the index still calls running that began before this Dervo started. Runs are child processes of the host,
    so the host that drove it is gone and nothing will ever finish it. Read this way until the coding runtime
    reconciles the record itself (coding_integration, on its first use after a start), so Home, the sidebar and the
    good-morning note never say a dead run is working (handoff map, 19 Sep, H3)."""
    return status == 'running' and started is not None and changed < started


def _state_rows(db, thread_ids=None):
    """The index rows of these conversations (all of them when None), each with ``made``: the task's place in creation
    order, so the newest task of a conversation is the one saved last, not the one that last moved. An older task that
    changes (approved, reconciled) must not take the place of a newer one that still waits on you (orchestrator map,
    19 Sep, F4). Sorted so each conversation's newest task comes last."""
    has_tasks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone()
    made = 'c.rowid' if has_tasks else 'NULL'
    join = 'LEFT JOIN coding_tasks c ON c.id=s.task_id ' if has_tasks else ''
    where, params = '', ()
    if thread_ids is not None:
        where, params = f"WHERE s.thread_id IN ({','.join('?' * len(thread_ids))}) ", tuple(thread_ids)
    rows = db.execute(f'SELECT s.thread_id, s.task_id, s.status, s.step, s.changed, b.goal, b.why, b.next, b.request, m.summary, e.state, {made} AS made '
                      f'FROM conversation_task_state s '
                      f'LEFT JOIN conversation_task_brief b ON b.task_id=s.task_id '
                      f'LEFT JOIN conversation_task_summary m ON m.task_id=s.task_id '
                      f'LEFT JOIN conversation_task_end e ON e.task_id=s.task_id {join}{where}', params).fetchall()
    return sorted(rows, key=lambda row: (row[11] if row[11] is not None else -1, row[4]))


def summaries(db, thread_ids):
    """{thread_id: {status, step, unread, attention}} for the given conversations. ``status`` is 'needs' when the newest
    task waits on you, 'running' when one runs. ``attention`` is a need you haven't opened the conversation to see yet.
    ``lost`` is a run that was still going when Dervo last died: it reads as needing you, ended 'disconnected'."""
    if not thread_ids:
        return {}
    marks = ','.join('?' * len(thread_ids))
    seen = dict(db.execute(f'SELECT thread_id, seen FROM conversation_seen WHERE thread_id IN ({marks})', thread_ids).fetchall())
    dismissed = dict(db.execute(f'SELECT thread_id, at FROM conversation_dismissed WHERE thread_id IN ({marks})', thread_ids).fetchall())
    result = {tid: {'status': None, 'running': False, 'step': None, 'unread': 0, 'since': None, 'goal': None, 'why': None, 'next': None, 'request': None, 'summary': None, 'at': None, 'ended': None, 'helpers': [], 'attention': False, 'lost': False} for tid in thread_ids}
    started = session_start(db)
    latest, newest_task = {}, {}
    for thread, task_id, status, step, changed, goal, why, next_step, request, summary, ended, _made in _state_rows(db, list(thread_ids)):
        item = result[thread]
        lost = is_lost(status, changed, started)
        if lost:
            status, step, summary, ended = 'needs', None, LOST_LINE, 'disconnected'
        latest[thread] = (status, changed)
        newest_task[thread] = task_id
        # In creation order, so the newest task of the conversation is the one left standing. ``at`` is when anything
        # in it last moved.
        item['summary'], item['ended'], item['lost'] = summary, ended, lost
        item['at'] = max(item['at'] or 0, changed)
        if status == 'running':
            item['running'], item['step'], item['since'] = True, step, changed
            item.update(goal=goal, why=why, next=next_step, request=request, status='running')
        elif changed > seen.get(thread, 0) and changed > dismissed.get(thread, 0):
            item['unread'] += 1
    for thread, (status, changed) in latest.items():
        if needs_you(status, changed, dismissed.get(thread)):
            result[thread].update(status='needs', attention=changed > seen.get(thread, 0))
    from worker_question_activity import pending
    for thread, questions in pending(db, newest_task).items():
        item = result[thread]
        active = [question for question in questions if question['at'] > dismissed.get(thread, 0)]
        if item['lost'] or not active:
            continue
        # Asking doesn't stop the worker. Keep its running flag and current step intact.
        changed = max(question['at'] for question in active)
        item.update(status='needs', attention=item['attention'] or changed > seen.get(thread, 0),
                    pending_question=active[0]['ask'], pending_question_count=len(active),
                    next=active[0]['ask'], at=max(item['at'] or 0, changed))
    for thread, helpers in helpers_of(db, newest_task).items():
        result[thread]['helpers'] = helpers
    return result


def project_counts(db, actor):
    """The project picker's figures (design sweep, 19 Sep): per open project, how many threads need you and how many
    are running, from the activity index alone. "Needs you" counts what Home's own count does, a shared thread whose
    newest task waits on you and that you haven't opened since, so the picker and Home never disagree. "Running"
    counts every thread you can see with a run going. Archived threads and projects, and someone else's private
    threads, are never counted. {project_id: {'needs': n, 'running': n}}, with every open project listed."""
    projects = [row[0] for row in db.execute('SELECT id FROM projects WHERE id NOT IN (SELECT project_id FROM project_archives)')]
    result = {pid: {'needs': 0, 'running': 0} for pid in projects}
    rows = db.execute("""SELECT t.id, t.project_id, t.visibility FROM threads t
        WHERE (t.visibility='shared' OR t.owner=?) AND t.id NOT IN (SELECT thread_id FROM conversation_archives)""", (actor,)).fetchall()
    threads = [(tid, pid, visibility) for tid, pid, visibility in rows if pid in result]
    activity = summaries(db, [tid for tid, _, _ in threads])
    for tid, pid, visibility in threads:
        item = activity[tid]
        if item['running']:
            result[pid]['running'] += 1
        if visibility == 'shared' and item['status'] == 'needs' and item['attention']:
            result[pid]['needs'] += 1
    return result


HELPER_WORDS = {'queued': 'waiting', 'running': 'working', 'returned': 'answered', 'failed': 'failed',
                'withdrawn': 'withdrawn', 'disconnected': 'lost'}


def helpers_of(db, newest_task):
    """The helpers the newest task of each conversation asked, and where each ask stands, from the packet records. An
    independent review reads as a review rather than its generated agent name. Refused asks never ran, so they're left out."""
    if not newest_task or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='collab_packets'").fetchone():
        return {}
    by_task = {task: thread for thread, task in newest_task.items()}
    marks = ','.join('?' * len(by_task))
    found = {}
    for task_id, document in db.execute(f'SELECT owner_task_id, document FROM collab_packets WHERE owner_task_id IN ({marks}) ORDER BY rowid',
                                        list(by_task)):
        try:
            packet = json.loads(document)
        except ValueError:
            continue
        word = HELPER_WORDS.get(packet.get('status'))
        if not word:
            continue
        who = 'Independent review' if packet.get('role') == 'reviewer' or packet.get('kind') == 'review' else _clean(packet.get('agent'), 40) or 'A helper'
        found.setdefault(by_task[task_id], []).append(dict(who=who, state=word))
    return {thread: rows[-4:] for thread, rows in found.items()}


def dismiss(db, thread_id):
    """Clear "Needs you" and unread results for a conversation. Its tasks and their results are unchanged."""
    now = time.time()
    with db:
        db.execute('INSERT INTO conversation_dismissed VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET at=excluded.at', (thread_id, now))
        db.execute('INSERT INTO conversation_seen VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET seen=excluded.seen', (thread_id, now))
        bump(db, thread_id)


VISIT_GAP = 10 * 60  # a look this long after the last one starts a new visit


def mark_seen(db, thread_id):
    now = time.time()
    with db:
        row = db.execute('SELECT seen FROM conversation_seen WHERE thread_id=?', (thread_id,)).fetchone()
        newest = db.execute('SELECT max(changed) FROM conversation_task_state WHERE thread_id=?', (thread_id,)).fetchone()[0]
        if newest is not None and (row is None or row[0] < newest):
            bump(db, thread_id)  # looking clears its unread or "needs you" mark; a look that changes nothing isn't a change
        if row is None or now - row[0] > VISIT_GAP:
            db.execute('INSERT INTO conversation_visits VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET previous=excluded.previous',
                       (thread_id, row[0] if row else None))
        db.execute('INSERT INTO conversation_seen VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET seen=excluded.seen',
                   (thread_id, now))


def previous_visit(db, thread_id):
    """When you were last in this conversation before the current visit, or None."""
    row = db.execute('SELECT previous FROM conversation_visits WHERE thread_id=?', (thread_id,)).fetchone()
    return row[0] if row else None



def mark_session(db, kind):
    """Record that Dervo started or closed now. Called by the host on both, never by a task."""
    with db:
        db.execute('INSERT INTO session_marks VALUES (?,?)', (kind, time.time()))


def last_session_end(db):
    """When the previous session closed: the newest end mark before this session's start, or the previous start when
    Dervo was not closed cleanly (a crash or a bridge kill writes no end mark, and the session before that closed too
    long ago to be "last time"), or None the first time."""
    starts = [row[0] for row in db.execute("SELECT at FROM session_marks WHERE kind='start' ORDER BY at DESC LIMIT 2")]
    if not starts:
        return None
    current = starts[0]
    end = db.execute("SELECT max(at) FROM session_marks WHERE kind='end' AND at<=?", (current,)).fetchone()[0]
    previous = starts[1] if len(starts) > 1 else None
    if end is not None and (previous is None or end >= previous):
        return end
    return previous


def session_summary(db, actor):
    """Bryce's 17 September decision: a good-morning note. Results you haven't opened yet and conversations waiting on
    you, per conversation, from the activity index only. ``since`` is when Dervo was last closed, for the wording; a
    result counts as new until its conversation is opened, whenever it landed. Someone else's private conversation is
    never named or counted."""
    since = last_session_end(db)
    started = session_start(db)
    seen = dict(db.execute('SELECT thread_id, seen FROM conversation_seen').fetchall())
    dismissed = dict(db.execute('SELECT thread_id, at FROM conversation_dismissed').fetchall())
    newest = {}
    for row in _state_rows(db):  # creation order, so the newest task is the one saved last (F4)
        status = 'needs' if is_lost(row[2], row[4], started) else row[2]
        newest[row[0]] = (status, row[4])
    finished, needs = {}, {}
    rows = db.execute('''SELECT s.thread_id, s.status, s.changed, c.name, t.project_id, p.name AS project
        FROM conversation_task_state s JOIN threads t ON t.id=s.thread_id JOIN local_channels c ON c.id=t.id
        LEFT JOIN project_names p ON p.project_id=t.project_id
        WHERE (t.visibility='shared' OR t.owner=?)
        AND t.id NOT IN (SELECT thread_id FROM conversation_archives) ORDER BY s.changed DESC''', (actor,))
    for thread_id, status, changed, name, project_id, project in rows:
        if status == 'running':
            if not is_lost(status, changed, started):
                continue  # still going: not news yet
            status = 'needs'  # a run Dervo died with: it stopped, and nothing will confirm how far it got
        if status == 'needs':
            if newest.get(thread_id) != (status, changed) or not needs_you(status, changed, dismissed.get(thread_id)):
                continue  # moved on from, or dismissed
        elif changed <= max(seen.get(thread_id, 0), dismissed.get(thread_id, 0)):
            continue  # opened since it landed: not news
        bucket = needs if status == 'needs' else finished
        item = bucket.setdefault(thread_id, {'thread_id': thread_id, 'project_id': project_id, 'name': name, 'project': project, 'count': 0, 'changed': changed})
        item['count'] += 1
        item['changed'] = max(item['changed'], changed)
    order = lambda items: sorted(items.values(), key=lambda item: -item['changed'])
    return {'since': since, 'finished': order(finished), 'needs': order(needs)}
