"""Which Claude Code session a conversation message continues.

Claude Code keeps a conversation's memory in its own session: what was said, the files it read, the commands it ran and
what they printed. Dervo used to start a new session for every message and paste a clipped summary of earlier exchanges
into the prompt. Now a new message in a conversation continues the previous message's session with
``--resume <previous> --fork-session --session-id <new>``: the whole conversation carries over, and each message still
has its own session ID, so retries, corrections and recovery keep working per task.

Dervo stays the owner of the facts. A session can go stale (you rejected or undid a change it remembers making), so a
continued message also carries a short note of what changed since the agent last looked. A message continues only when
nothing about the run would surprise the agent: the same account, provider, folder and mode, and a previous run that
ended cleanly. Otherwise it starts fresh with the earlier summary, and the reason is recorded on the task and shown
before you send.

Grounding (checked 2026-09-17 against Claude Code 2.1.274 with an unauthenticated config folder, no model call):
``--session-id`` is accepted with ``--resume`` only together with ``--fork-session``, the forked run reports the new ID,
and ``--resume`` finds a session only under the working folder's own project folder in CLAUDE_CONFIG_DIR/projects.
File-tool runs use a new empty folder each time, so the session file is moved to where the next run looks for it.
"""
import json
import sqlite3
import os
import re
import time
from pathlib import Path

import secret_scan

SETTLED_RUNS = ('completed', 'interrupted')
SLUG_LIMIT = 200  # Claude Code shortens longer project folder names with a hash; those are left alone


def schema(db):
    # Start fresh: the next message in this conversation ignores every task saved up to after_rowid.
    db.execute('CREATE TABLE IF NOT EXISTS conversation_fresh_start (thread_id TEXT PRIMARY KEY, after_rowid INTEGER NOT NULL, '
               'actor TEXT, created_at REAL NOT NULL)')


def _conversation_tasks(db, context):
    # conversation_task_state indexes every saved task by conversation (conversation_activity.record, in the same save).
    rows = db.execute('SELECT c.rowid, c.document FROM coding_tasks c JOIN conversation_task_state s ON s.task_id=c.id '
                      'WHERE s.thread_id=? ORDER BY c.rowid', (context.get('thread_id'),)).fetchall()
    result = []
    for rowid, document in rows:
        task = json.loads(document)
        if task.get('origin') == 'conversation' and task.get('context') == context:
            result.append((rowid, task))
    return result


def _provider_name(provider):
    return 'Codex' if provider == 'codex' else 'Claude Code' if provider == 'claude' else 'another provider'


def never_started(task):
    """A message that never reached an agent: no run, or a run that ended before the provider opened a session (a
    launch failure, a permission you refused). It holds no session to continue and nothing the agent has seen."""
    return task.get('state') not in ('working', 'needs_permission') and (not task.get('session_id') or not task.get('runs'))


def previous(db, context, exclude=None):
    """The conversation's latest message task before this one that reached an agent, with its rowid, or (None, None).
    Messages after it that never started are skipped here and carried by since_note, so one launch failure doesn't
    throw away the session that came before it (handoff map, 19 Sep, H2). If nothing ever started, the newest task is
    returned so the decision can say so."""
    tasks = [(rowid, task) for rowid, task in _conversation_tasks(db, context) if task['id'] != exclude]
    started = [(rowid, task) for rowid, task in tasks if not never_started(task)]
    if started:
        return started[-1]
    return tasks[-1] if tasks else (None, None)


def skipped_after(db, context, from_task_id, exclude=None):
    """The messages of this conversation sent after ``from_task_id`` that never reached an agent, oldest first."""
    tasks = [task for _rowid, task in _conversation_tasks(db, context) if task['id'] != exclude]
    ids = [task['id'] for task in tasks]
    if from_task_id not in ids:
        return []
    return [task for task in tasks[ids.index(from_task_id) + 1:] if never_started(task)]


def fresh_marker(db, thread_id):
    schema(db)
    row = db.execute('SELECT after_rowid FROM conversation_fresh_start WHERE thread_id=?', (thread_id,)).fetchone()
    return row[0] if row else None


def decide(db, context, binding, *, full_access, read_only, exclude=None, computer_access=False):
    """Continue or start fresh, with the reason in plain words. ``state`` is 'first' when nothing came before."""
    rowid, before = previous(db, context, exclude)
    if before is None:
        return dict(state='first', reason=None, from_task_id=None, session_id=None)
    fresh = dict(state='fresh', from_task_id=before['id'], session_id=None)
    marker = fresh_marker(db, context['thread_id'])
    if marker is not None and rowid <= marker:
        return dict(fresh, reason='You chose to start fresh.')
    was = before.get('binding') or {}
    if binding['provider'] != 'claude':
        return dict(fresh, reason=f"{_provider_name(binding['provider'])} gets a summary of this chat, not the full session.")
    if was.get('provider') != 'claude':
        return dict(fresh, reason=f"Moved from {_provider_name(was.get('provider'))}. Claude gets a summary of this chat.")
    if was.get('profile_id') != binding['profile_id']:
        return dict(fresh, reason='Switched to another account. Its sessions are kept separately.')
    if was.get('cwd') != binding['cwd']:
        return dict(fresh, reason='The project folder changed.')
    if after_overview(db, context, before, read_only):
        # Your first follow-up to the onboarding overview: a fresh session that gets the overview, said nowhere. Before,
        # a new person's second message read "New session · Switched between asking about the project and coding in
        # it" (onboarding review, 9 Oct).
        return dict(fresh, reason=None, quiet=True)
    if bool(before.get('full_access')) != bool(full_access) and not home_limited(before, full_access):
        return dict(fresh, reason='Full access was turned ' + ('on.' if full_access else 'off.'))
    if bool(before.get('computer_access')) != bool(computer_access):
        return dict(fresh, reason='Computer access changed. Starting with the current permissions.')
    if bool(before.get('read_only')) != bool(read_only):
        return dict(fresh, reason='Switched between asking about the project and coding in it.')
    if before.get('state') in ('working', 'needs_permission'):
        return dict(fresh, reason='The last message is still running.')
    runs = before.get('runs') or []
    if not before.get('session_id') or not runs:
        return dict(fresh, reason='The last message never started.')
    if any(r.get('state') == 'uncertain' for r in runs) or before.get('state') == 'disconnected':
        return dict(fresh, reason='The last run ended unexpectedly, so this starts a new session with a summary of the chat.')
    tokens_settled = all(t.get('state') in ('delivered', 'cancelled') for t in before.get('tokens') or [] if isinstance(t, dict))
    if runs[-1].get('state') == 'failed' and tokens_settled and (runs[-1].get('end_cause') == 'host_limit' or provider_failed(runs[-1])):
        # Dervo stopped the run at its own limit, or the provider's side failed (an overload, a limit, an API error),
        # and every tool call had settled: the session is intact up to the stop, and since_note says how it ended. Before
        # 19 Sep every failure meant a fresh session with a six-message summary, and the loss compounded (handoff map H2).
        # If the session file turns out not to be there, the launch falls back to the summary and records why.
        return dict(state='continues', reason=None, from_task_id=before['id'], session_id=before['session_id'])
    if runs[-1].get('state') not in SETTLED_RUNS:
        return dict(fresh, reason='The last run failed partway, so this starts a new session with a summary of the chat.')
    return dict(state='continues', reason=None, from_task_id=before['id'], session_id=before['session_id'])


def after_overview(db, context, before, read_only):
    """Whether this is the first coding message after the read-only overview Dervo started in a "Project overview"
    thread (project_lifecycle.exploration_thread), the only message before it."""
    if read_only or not before.get('read_only'):
        return False
    try:
        row = db.execute("SELECT 1 FROM conversation_origins WHERE thread_id=? AND origin='overview'", (context.get('thread_id'),)).fetchone()
    except sqlite3.Error:
        return False
    tasks = _conversation_tasks(db, context)
    return bool(row) and len(tasks) >= 1 and tasks[0][1]['id'] == before['id'] and sum(not never_started(t) for _r, t in tasks) == 1


def home_limited(before, full_access):
    """Whether the earlier message is a Home job that ran without full access only because Home started it unclicked
    (conversation_coding H1), while the thread itself has full access on. That limit was the job's, not a switch the
    person made, so the next thing they type continues the session instead of hearing "Full access was turned on."
    (night 1, finding 4). A clicked Home job, or a typed message, keeps the plain rule."""
    return (bool(full_access) and not before.get('full_access')
            and before.get('requested_by') == 'home' and before.get('clicked') is not True)


def provider_failed(run):
    """Whether a run ended on the provider's side, as coding_integration records it (provider_failure.KINDS)."""
    try:
        from provider_failure import KINDS
    except ImportError:
        return False
    return run.get('provider_failure') in KINDS


def plan(coding, task, binding):
    """The decision for a task that is about to start its first run. A continued decision also names the session the
    previous message itself continued from (``parent_session_id``), for the launch to try when the previous message's
    own session file was never written (phase 2, item 6)."""
    decision = dict(decide(coding.store.db, task['context'], binding, full_access=task.get('full_access') is True,
                           read_only=task.get('read_only') is True, exclude=task['id'], computer_access=task.get('computer_access') is True), decided_at=int(time.time()))
    if decision.get('state') == 'fresh':
        # How many earlier messages the summary holds, so the line after it says the real number (onboarding review, 9 Oct).
        from conversation_coding import TRANSCRIPT_TASKS
        decision['summary_size'] = min(TRANSCRIPT_TASKS, _earlier(coding.store.db, task['context'].get('thread_id'), exclude=task['id']))
    if decision.get('state') == 'continues' and decision.get('from_task_id'):
        row = coding.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (decision['from_task_id'],)).fetchone()
        before = (json.loads(row[0]).get('continuity') or {}) if row else {}
        parent = before.get('session_id') if before.get('state') == 'continues' else None
        if isinstance(parent, str) and re.fullmatch(r'[0-9a-f-]{36}', parent) and parent != decision.get('session_id'):
            decision['parent_session_id'] = parent
    return decision


def _earlier(db, thread_id, exclude=None):
    return db.execute(
        "SELECT count(*) FROM coding_tasks WHERE json_extract(document, '$.origin')='conversation' "
        "AND json_extract(document, '$.context.thread_id')=? AND id IS NOT ?", (thread_id, exclude)).fetchone()[0]


def preview(coding, binding, ctx, permission):
    """What the next message from the composer would do, for the line under it. Nothing is recorded."""
    full_access = bool((permission.get('full_access') or {}).get('active'))
    read_only = permission['state'] != 'granted' and (permission.get('reading') or {}).get('state') == 'available'
    decision = decide(coding.store.db, dict(project_id=ctx['project_id'], thread_id=ctx['thread_id'], actor=ctx['actor']),
                      binding, full_access=full_access, read_only=read_only, computer_access=bool((permission.get('full_access') or {}).get('computer_access')))
    decision['fresh_chosen'] = fresh_marker(coding.store.db, ctx['thread_id']) is not None and decision['reason'] == 'You chose to start fresh.'
    # What a fresh session would get, said before you send (phase 2, item 9): how many earlier messages the summary
    # holds, and the one line for the composer. Continuing and a chat's first message say nothing.
    from conversation_coding import TRANSCRIPT_TASKS
    earlier = _earlier(coding.store.db, ctx['thread_id'])
    decision['summary_size'] = min(TRANSCRIPT_TASKS, earlier) if decision['state'] == 'fresh' else None
    decision['line'] = preview_line(decision)
    return decision


def preview_line(decision):
    """The composer's line for a fresh next message, in the map's section 5 words, or None."""
    reason, size = decision.get('reason'), decision.get('summary_size')
    if decision.get('state') != 'fresh' or not reason or not size:
        return None
    summary = 'a summary of the last message' if size == 1 else f'a summary of the last {size} messages'
    if reason == 'You chose to start fresh.':
        return f'Next message starts a new session with {summary}.'
    if reason.endswith('gets a summary of this chat, not the full session.'):
        return f"{reason.split(' gets ')[0]} starts fresh. It gets {summary}."
    if reason.startswith('Moved from'):
        return f"{reason.split('.')[0]}. Claude starts fresh with {summary}."
    if reason.startswith('The last run'):
        return f'The last run didn’t end cleanly, so the next message starts with {summary}.'
    return f'{reason} The next message starts fresh with {summary}.'


def start_fresh(db, thread_id, actor, enabled):
    """Start fresh (or undo it before sending): the next message ignores the sessions of everything saved so far."""
    schema(db)
    with db:
        if enabled:
            last = db.execute('SELECT MAX(rowid) FROM coding_tasks').fetchone()[0] or 0
            db.execute('INSERT INTO conversation_fresh_start VALUES (?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET '
                       'after_rowid=excluded.after_rowid, actor=excluded.actor, created_at=excluded.created_at',
                       (thread_id, last, actor, time.time()))
        else:
            db.execute('DELETE FROM conversation_fresh_start WHERE thread_id=?', (thread_id,))


def since_note(db, from_task_id, task):
    """What changed about the previous message's result since the session last saw it. Facts from Dervo's records."""
    row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (from_task_id,)).fetchone()
    if not row:
        return ''
    before = json.loads(row[0])
    facts = []
    if (task.get('continuity') or {}).get('parent'):
        # The previous message's own session wasn't saved, so this one continues from the session before it: the
        # previous exchange is said here, or the agent would never see it (phase 2, item 6).
        asked = next((m.get('text') for m in before.get('messages') or [] if isinstance(m, dict) and m.get('role') == 'user'), '')
        answer = next((m.get('text') for m in reversed(before.get('messages') or []) if isinstance(m, dict) and m.get('role') == 'assistant'), '')
        facts.append('Your session is from before the last message, which wasn’t saved. The user asked: ' + _flat(asked, 1500)
                     + ' You answered: ' + (_flat(answer, 3000) if answer else '(no answer)'))
    commit = before.get('commit') or {}
    if commit.get('reverted'):
        facts.append('The user undid the changes from your last message. Those edits are no longer in the project, so check the files before building on them.')
    elif before.get('state') == 'idle' or before.get('review') == 'approved':
        facts.append('The user approved your last result' + (f" and Dervo committed it ({str(commit['sha'])[:7]})." if commit.get('sha') else '.'))
    elif before.get('review') == 'rejected':
        facts.append('The user rejected your last result.')
    elif before.get('state') == 'ready_for_review':
        facts.append('Your last changes are in the project folder but still waiting for the user’s review.')
    elif before.get('state') == 'completed':
        facts.append('Your last result passed its checks and counts as completed.')
    elif before.get('state') == 'blocked' and (before.get('runs') or [{}])[-1].get('end_cause') == 'host_limit':
        facts.append('Dervo stopped your last message partway: ' + str(before.get('error') or '')[:400]
                     + ' Check the files before building on those edits, then carry on from where you stopped.')
    elif before.get('state') == 'blocked' and provider_failed((before.get('runs') or [{}])[-1]):
        facts.append('Your last message stopped partway when the provider failed: ' + str(before.get('error') or '')[:400]
                     + ' Check the files before building on those edits, then carry on from where you stopped.')
    elif before.get('state') == 'blocked' and (before.get('runs') or [{}])[-1].get('end_cause') == 'restart_paused':
        # Beta.18: Dervo restarted mid-run and settled it as paused, from the session's own record: every step it had
        # started had finished (lost_runs.settle). Not the person's Pause.
        facts.append('Dervo restarted while your last message was running. Every step you had started had finished, so '
                     'Dervo paused it there. Carry on from where you left off; any edits it made are already in the project folder.')
    elif before.get('state') == 'blocked' and (before.get('runs') or [{}])[-1].get('state') == 'interrupted':
        # The person's own Pause (Escape, Pause, or quitting Dervo): not a failure. Before 16.4 this read as "ended without
        # a reviewable result: Stopped before completion", which made agents start over instead of carrying on.
        facts.append('The user paused your last message before it finished. Carry on from where you left off; '
                     'any edits it made are already in the project folder.')
    elif before.get('state') == 'blocked' and before.get('error'):
        facts.append('Your last message ended without a reviewable result: ' + str(before['error'])[:300])
    elif before.get('state') == 'answered' and (before.get('runs') or [{}])[-1].get('state') == 'interrupted':
        facts.append('The user stopped your last message before it finished.')
    # A message sent into the run that the run ended before reading (steer 'sent', never 'read'): the session never saw
    # it, so it is said here rather than lost (handoff map, 19 Sep, H5).
    unread = [m.get('text') for m in before.get('messages') or []
              if isinstance(m, dict) and m.get('role') == 'user' and m.get('steer') == 'sent' and isinstance(m.get('text'), str)]
    for text in unread[:5]:
        facts.append('The user sent this while you were working and the run ended before you read it: ' + _flat(text, 1000))
    # Messages after that which never reached an agent (a launch failure, a refused permission): the session holds
    # nothing of them, so they are said here too (H2).
    for skipped in skipped_after(db, task.get('context') or {}, from_task_id, exclude=task.get('id'))[:5]:
        first = next((m.get('text') for m in skipped.get('messages') or [] if isinstance(m, dict) and m.get('role') == 'user'), '')
        facts.append('The user then sent a message that never reached an agent, so you have not seen it: ' + _flat(first, 1000))
    # How old the session's memory is, and files other chats changed under it (handoff review, #4).
    facts.extend(since_elsewhere(db, task, before))
    note ='This message continues your session from the previous message in this chat, so you already have its context.\n'
    if facts:
        note += 'Since then, from Dervo’s records:\n' + ''.join(f'- {fact}\n' for fact in facts)
    return note


def _flat(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit] + ' [shortened by Dervo]'


# ------------------------------------------------------------------ what a next agent is told (handoff review, 19 Sep)
def _first_sentence(text, limit=200):
    text = ' '.join(str(text or '').split())
    found = re.match(r'(.+?[.?!])(\s|$)', text)
    text = found.group(1) if found else text
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def outcome(task):
    """What happened to a message's result, in the words the next agent reads. From the task record only. The fresh
    summary used to print Dervo's state word ('idle', 'ready_for_review'), which never said an approved change had
    been undone or that a result was rejected (handoff review, 19 Sep, #1)."""
    commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    last = runs[-1] if runs else {}
    state = task.get('state')
    if commit.get('reverted'):
        return 'approved, then the user undid it, so the edits are gone'
    if state == 'idle' or task.get('review') == 'approved':
        sha = str(commit.get('sha') or '')[:7]
        return f'approved and committed {sha}' if sha else 'approved'
    if task.get('review') == 'rejected':
        return 'the user rejected it'
    if state == 'ready_for_review':
        return 'waiting for the user’s review'
    if state == 'completed':
        return 'completed, its checks passed'
    if state == 'answered':
        return 'the user stopped it' if last.get('state') == 'interrupted' else 'answered, no files changed'
    if state == 'blocked':
        from conversation_activity import stopped_by_user
        if last.get('end_cause') == 'restart_paused':
            return 'paused when Dervo restarted, between steps'
        if stopped_by_user(task):
            return 'the user stopped it'
        return f"stopped: {_first_sentence(task['error'])}" if task.get('error') else 'stopped without a result'
    if state == 'disconnected':
        return 'outcome not confirmed'
    if state == 'working':
        return 'still running'
    if state == 'needs_permission':
        return 'not started'
    return 'outcome not recorded'


def said_label(message):
    """How a later message of a task reached the agent: a note during its run (read or not), or a correction after
    its result. The first message of a task is the request itself."""
    if message.get('steer') == 'sent':
        return 'The user sent this during the run, and the run ended before reading it'
    if message.get('steer'):
        return 'The user also said during it'
    return 'The user then asked for a correction'


# Words that call off what an earlier message set out to do. A goal handed on from an earlier message (handoff's
# fallback, eval e6) stops at one of these (hard case e40: "Drop the title idea entirely" was still followed by "Its
# goal: greet takes a title"). A verb like "drop" or "cancel" needs an object that names the work (it, that, this, the
# ... idea), so "drop the id column" is a request, not a change of heart. False positives cost only the borrowed goal
# line; the messages themselves stay in the transcript.
_ABANDON = re.compile(
    r"\b(?:(?:drop|forget|scrap|ditch|abandon|shelve|cancel|scratch)\s+(?:it|that|this|about\s+(?:it|that|this)|"
    r"the\s+(?:\w+[\s-]+){0,4}(?:idea|plan|goal|approach|change|changes|feature|work|thing|refactor|rewrite))"
    r"|never\s?mind|not\s+doing\s+(?:it|that|this)|no\s+longer|change\s+of\s+plan|let'?s\s+not\b|don'?t\s+bother"
    r"|put\s+(?:it|that|this|[\w./-]+)\s+back|back\s+(?:as|the\s+way|how)\s+(?:it|they)\s+(?:was|were)"
    r"|revert\s+(?:it|that|this|the\b|[\w./-]+\.\w+)|roll\s+(?:it|that|this)\s+back|roll\s+back\s+(?:it|that|this|the\b)"
    r"|undo\s+(?:it|that|this))\b", re.I)


def abandons(text):
    """True when the user's words call off the work an earlier message set out to do."""
    return isinstance(text, str) and _ABANDON.search(text) is not None


def retried(task):
    """A request sent again on the other provider after a failure: the retry carries the same words."""
    suggest = task.get('suggest') if isinstance(task.get('suggest'), dict) else {}
    return bool(suggest.get('taken'))


USER_WORDS_BUDGET = 6000
USER_WORDS_CLIP = 400
FIRST_REQUEST_CLIP = 1500


def user_words(tasks, shown, budget=USER_WORDS_BUDGET):
    """Everything the user said in this chat that the summary above doesn't already show, oldest first: earlier
    requests, corrections and notes during a run. The user's own words only, never an agent's. The chat's first request
    is always kept; over budget the middle goes and the line says how many were left out. ``shown`` holds
    (task id, message index) pairs already quoted (handoff review, 19 Sep, #2)."""
    items, first_seen = [], False
    for task in tasks:
        if retried(task):
            continue
        users = [(index, m) for index, m in enumerate(task.get('messages') or [])
                 if isinstance(m, dict) and m.get('role') == 'user' and isinstance(m.get('text'), str) and m['text'].strip()]
        for position, (index, message) in enumerate(users):
            first = not first_seen
            first_seen = True
            if (task.get('id'), index) in shown:
                continue
            if position == 0:
                label = 'Asked, and it never reached an agent' if never_started(task) else 'Asked'
            else:
                label = said_label(message)
            # A key typed into the chat never goes back to a model from this summary (reviewer, 20 Sep).
            items.append(f"- {label}: {secret_scan.redact(_flat(message['text'], FIRST_REQUEST_CLIP if first else USER_WORDS_CLIP))}")
    if not items:
        return ''
    kept, left_out = list(items), 0
    while len(kept) > 1 and sum(len(item) + 1 for item in kept) > budget:
        kept.pop(1)  # the middle goes first: the first request and the newest messages stay
        left_out += 1
    if left_out:
        kept.insert(1, f"- [{left_out} earlier message{'s' if left_out != 1 else ''} left out]")
    return 'Everything else the user said in this chat, oldest first (their own words):\n' + '\n'.join(kept) + '\n'


def check_facts(db, task):
    """The host check of the task's last checked attempt, and what changed after it: (result, the check's file or None,
    the end of its output when it failed). ``result`` is 'passed', 'failed (exit N)' or 'stopped before it finished',
    with ", but utils.py changed after it ran and isn’t checked" on the end when a later attempt wrote files, or None
    when no check ran. From the evidence record Dervo keeps (handoff review, #3; critical review 20 Sep, H1)."""
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    test, tail, name, later, complete = None, '', None, [], True
    if db is not None and task.get('id'):
        try:
            row = db.execute('SELECT document FROM task_evidence WHERE id=?', (task['id'],)).fetchone()
        except Exception:  # noqa: BLE001 - a store without evidence has nothing to add
            row = None
        evidence = json.loads(row[0]) if row else {}
        name = evidence.get('test_file') if isinstance(evidence.get('test_file'), str) else None
        attempts = [a for a in evidence.get('attempts') or [] if isinstance(a, dict)]
        # The last attempt that ran a check, and what changed after it. A check only covers the files as they were when
        # it ran: a write after it is unchecked, and "passed" said about it would be untrue (critical review, 20 Sep,
        # H1: write, check, write, usage limit was handed on as "Files it changed: greeting.py, utils.py / Check: passed").
        checked = next((n for n in range(len(attempts) - 1, -1, -1) if isinstance(attempts[n].get('test'), dict)
                        and isinstance(attempts[n]['test'].get('exit_status'), int)), None)
        if checked is not None:
            test = attempts[checked]['test']
            later, complete = changed_after(attempts[checked + 1:])
    exit_status = test['exit_status'] if test else completion.get('test_exit_status')
    if not isinstance(exit_status, int) or isinstance(exit_status, bool):
        return None, name, ''
    if later or not complete:
        unchecked = _unchecked_clause(later, complete)
    elif completion.get('stale'):
        # The host already found the checked files changed since (human_acceptance.check_current); the evidence can't
        # name which.
        unchecked = ', but files changed after it ran and aren’t checked'
    else:
        unchecked = ''
    if exit_status == 0 and not (test or {}).get('stopped'):
        return 'passed' + unchecked, name, ''
    if test:
        output = '\n'.join(part for part in (str(test.get('stdout') or '').strip(), str(test.get('stderr') or '').strip()) if part)
        tail = output[-1000:]
    return (f'failed (exit {exit_status})' if exit_status else 'stopped before it finished') + unchecked, name, tail


def changed_after(attempts):
    """(The files these evidence attempts changed, in the order they changed; whether that list is complete.) A path
    changed when its after snapshot differs from its before, only one side has it, or it has a recorded diff. The
    list is incomplete when an attempt was cut off before its after snapshot was taken, so nothing can say which files
    it touched: named files beside a cut-off attempt read as "utils.py and possibly more", never as just utils.py
    (reviewer, 20 Sep)."""
    names, complete = [], True
    for attempt in attempts:
        before, after, diff = attempt.get('before') or {}, attempt.get('after'), attempt.get('diff') or {}
        if after is None and attempt.get('kind') in ('change', 'create'):
            complete = False
            continue
        after = after or {}
        for path in list(before) + [p for p in after if p not in before]:
            left, right = before.get(path), after.get(path)
            changed = bool(diff.get(path)) or (left is None) != (right is None) or (
                isinstance(left, dict) and isinstance(right, dict) and left.get('sha256') != right.get('sha256'))
            if changed and isinstance(path, str) and path not in names:
                names.append(path)
    return names, complete


def _unchecked_clause(names, complete):
    """", but utils.py changed after it ran and isn’t checked": the part of a check line that stops a later write from
    reading as verified. Short, because the handoff block drops whole lines past its budget and a long honest line
    must not lose to a short misleading one (reviewer, 20 Sep): up to three names, else a count, since the "Files it
    changed" line above already lists them."""
    if not names:
        return ', but files may have changed after it ran and aren’t checked'
    shown = ', '.join(names) if len(names) <= 3 else f'{len(names)} of the files above'
    more = '' if complete else ' and possibly more'
    verb = 'isn’t' if len(names) == 1 and complete else 'aren’t'
    return f', but {shown}{more} changed after it ran and {verb} checked'


def files_changed(task):
    """The files a task's result changed: the completion's list, else what its runs wrote through Dervo's tools or
    Claude Code's own."""
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    names = [f for f in completion.get('changed_files') or [] if isinstance(f, str)]
    if names:
        return names
    for run in task.get('runs') or []:
        if isinstance(run, dict):
            names += [f for f in run.get('written') or [] if isinstance(f, str) and f not in names]
    for token in task.get('tokens') or []:
        if (isinstance(token, dict) and token.get('tool') in WRITE_TOOLS and token.get('state') == 'delivered'
                and token.get('success') is not False and isinstance(token.get('path'), str) and token['path'] not in names):
            names.append(token['path'])
    return names


READ_TOOLS = ('dervo_read_file', 'native:Read')
WRITE_TOOLS = ('dervo_write_file', 'dervo_edit_file', 'dervo_create_file', 'native:Edit', 'native:Write')


def files_read(task, limit=10):
    names = []
    for token in task.get('tokens') or []:
        if (isinstance(token, dict) and token.get('tool') in READ_TOOLS and token.get('state') == 'delivered'
                and isinstance(token.get('path'), str) and token['path'] not in names):
            names.append(token['path'])
    return names[:limit]


def _touched(task):
    """Files a task read or changed, from its own records."""
    names = set(files_read(task, limit=500))
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    names.update(f for f in completion.get('changed_files') or [] if isinstance(f, str))
    for run in task.get('runs') or []:
        if isinstance(run, dict):
            names.update(f for f in run.get('written') or [] if isinstance(f, str))
    for token in task.get('tokens') or []:
        if isinstance(token, dict) and token.get('tool') in WRITE_TOOLS and isinstance(token.get('path'), str):
            names.add(token['path'])
    return names


def _changed(task):
    commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
    names = files_changed(task)
    names += [f for f in commit.get('files') or [] if isinstance(f, str) and f not in names]
    return names


def _changed_at(task):
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
    reverted = commit.get('reverted') if isinstance(commit.get('reverted'), dict) else {}
    times = [t for t in (completion.get('at'), commit.get('at'), reverted.get('at')) if isinstance(t, (int, float))]
    return max(times) if times else None


def _short_outcome(task):
    commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
    if commit.get('reverted'):
        return 'changed, then undone'
    if commit.get('sha'):
        return 'approved and committed'
    return {'idle': 'approved', 'ready_for_review': 'waiting for review', 'completed': 'completed',
            'blocked': 'stopped partway', 'disconnected': 'outcome not confirmed'}.get(task.get('state'), 'changed')


def last_said_at(task):
    times = [m.get('created_at') for m in task.get('messages') or [] if isinstance(m, dict)]
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    times.append(completion.get('at'))
    times = [t for t in times if isinstance(t, (int, float)) and not isinstance(t, bool)]
    return max(times) if times else None


def ago(seconds):
    hours = int(seconds // 3600)
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    return f'{days} days ago'


OTHER_FILES_LIMIT = 10


def _elsewhere_candidates(db, context, last):
    """The task records since_elsewhere has to look at, in save order: this chat's, and other chats' in the project whose
    completion, commit or revert is newer than ``last``. Only those are parsed. The times are read by SQLite, so the rest
    of the project's records aren't parsed at all: parsing all 1,300 took about half a second before each message (8 Oct).
    since_elsewhere still checks every condition on what comes back. A task the activity index doesn't file yet is always
    taken (conversation_activity.UNINDEXED)."""
    import conversation_activity
    if not conversation_activity.has_index(db):
        return (json.loads(document) for (document,) in db.execute('SELECT document FROM coding_tasks ORDER BY rowid').fetchall())
    times = ("json_extract(c.document, '$.completion.at'), json_extract(c.document, '$.commit.at'), "
             "json_extract(c.document, '$.commit.reverted.at')")
    wanted = []
    for rowid, always, thread, *at in db.execute(
            f'SELECT c.rowid, 0, s.thread_id, {times} FROM coding_tasks c JOIN conversation_task_state s ON s.task_id=c.id '
            'LEFT JOIN threads t ON t.id=s.thread_id WHERE t.project_id=? OR t.id IS NULL '
            f'UNION ALL SELECT c.rowid, 1, NULL, {times} FROM coding_tasks c WHERE {conversation_activity.UNINDEXED} '
            'ORDER BY 1', (context.get('project_id'),)):
        numbers = [t for t in at if isinstance(t, (int, float)) and not isinstance(t, bool)]
        if always or thread == context.get('thread_id') or (last is not None and numbers and max(numbers) > last):
            wanted.append(rowid)
    return (json.loads(document) for chunk in range(0, len(wanted), 500) for _, document in db.execute(
        f'SELECT rowid, document FROM coding_tasks WHERE rowid IN ({",".join("?" * len(wanted[chunk:chunk + 500]))}) ORDER BY rowid',
        wanted[chunk:chunk + 500]))


def since_elsewhere(db, task, before, now=None):
    """How old this chat's last exchange is, past an hour, and which files this chat read or changed that other chats
    in the project have changed since. Facts from Dervo's records: times, file names and outcome words, never another
    agent's text. Other chats follow project memory's rule: shared chats plus this one, on the same kind of account
    (handoff review, 19 Sep, #4)."""
    if not before:
        return []
    now = time.time() if now is None else now
    lines = []
    last = last_said_at(before)
    if last is not None and now - last >= 3600:
        lines.append(f'The last message in this chat was {ago(now - last)}.')
    context = task.get('context') or before.get('context') or {}
    category = (task.get('binding') or before.get('binding') or {}).get('category')
    mine, others = set(), []
    for other in _elsewhere_candidates(db, context, last):
        if not isinstance(other, dict):
            continue
        if other.get('id') == task.get('id') or other.get('origin') != 'conversation':
            continue
        where = other.get('context') or {}
        if where.get('project_id') != context.get('project_id'):
            continue
        if where.get('thread_id') == context.get('thread_id'):
            mine |= _touched(other)
            continue
        binding = other.get('binding') or {}
        if binding.get('category') != category or binding.get('visibility') != 'shared':
            continue
        at = _changed_at(other)
        if last is not None and at is not None and at > last:
            others.append(other)
    named = []
    for other in others:
        for name in _changed(other):
            if name in mine and name not in [n for n, _ in named]:
                named.append((name, _short_outcome(other)))
    if named:
        shown = named[:OTHER_FILES_LIMIT]
        more = f' and {len(named) - len(shown)} more' if len(named) > len(shown) else ''
        lines.append('Since then, other chats in this project changed files this chat worked on: '
                     + ', '.join(f'{name} ({how})' for name, how in shown) + more + '. Read them again before editing.')
    return lines


# ------------------------------------------------------------------ session files (Claude Code's own storage)
def _slug(path):
    return re.sub(r'[^A-Za-z0-9]', '-', path)


def place_session(home, session_id, cwd):
    """Make a saved session loadable from ``cwd`` by moving its file into that folder's project directory inside the
    account's own CLAUDE_CONFIG_DIR. Returns whether it is in place. Reads and moves nothing outside ``home/projects``
    and never opens the file."""
    projects = Path(home) / 'projects'
    slug = _slug(os.path.realpath(cwd))
    if not re.fullmatch(r'[0-9a-f-]{36}', session_id or '') or len(slug) > SLUG_LIMIT or not projects.is_dir():
        return False
    target_dir = projects / slug
    target = target_dir / f'{session_id}.jsonl'
    if target.is_file() and not target.is_symlink():
        return True
    found = [p for p in projects.glob(f'*/{session_id}.jsonl') if p.is_file() and not p.is_symlink()]
    if not found:
        return False
    source = max(found, key=lambda p: p.stat().st_mtime)
    try:
        target_dir.mkdir(mode=0o700, exist_ok=True)
        os.replace(source, target)
        extras = source.with_suffix('')  # tool results and subagent transcripts saved beside the session
        if extras.is_dir() and not extras.is_symlink() and not target.with_suffix('').exists():
            os.replace(extras, target.with_suffix(''))
    except OSError:
        return target.is_file()
    return True
