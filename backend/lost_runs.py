"""Beta.18, stopping direction B ("Settle it for you"): after a restart, settle each lost run from the provider's own record.

Up to 17.1 a run the host died with read "Dervo closed while this was running, so it can't say how it ended", and the only
way out was Mark as stopped, which drew a failure X and made the next message start a fresh session. But both providers
write their own record as they go: Claude Code its session file (<config>/projects/<folder slug>/<session id>.jsonl, the
file session_continuity.place_session moves), Codex its rollout (<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-*-<thread>.jsonl).
Each tool call there has its result or doesn't. With the folder read Dervo already does (coding_integration.changes_after_close)
that settles most lost runs (notes/STOPPING-explore-20261005.md, sections 2 and 5 B):

- ``paused``: every step the run started in its current turn ended, or the only step left open was one that stays in the
  folder (a read, a search, a file edit, which the folder read settles) or one of Dervo's own tools (Dervo's own records
  hold it). The run becomes "Paused when Dervo restarted": its changed files are kept as its work, like a Stop, and
  Continue resumes the same session. Bryce, 6 Oct: it waits for his Continue; it never continues by itself, and in a
  thread folder nothing is committed until a run finishes.
- ``unsure``: a step that can reach outside the folder (a shell command, a connector, a sub-agent) started and its end
  isn't in the record, or the record says it was cut off. Running it again could do it twice, so the card names that
  step and offers Continue, checking first or Leave it stopped.
- ``unreadable``: no record, a record cut mid-line, a format this reader doesn't know, a step of Dervo's own that writes
  files or runs checks (those have their own reconcile path), or a folder git couldn't read. Today's card stays.

Nothing here is on the host's startup path. coding_integration's restart pass hands each lost run to a thread (``work``),
which stops a still-running orphan, reads its record and its folder, and queues the result; the tick applies it
(``apply``) to the record as it is then. Never raises.

Don't reap blindly (section 5 B): an orphan whose record shows it still working gets Stop's own signal, SIGINT, so it can
write its stop; only one that hasn't gone after STOP_SECONDS, or one that isn't working, is ended with SIGKILL as before.
"""
import json
import os
import re
import time
from pathlib import Path

# How much of a record's end is read. A turn's open steps sit at its end; a record bigger than this is read from here on.
TAIL_BYTES = 8 * 1024 * 1024
# How long a SIGINT'd orphan gets to write its stop and exit before Dervo ends it, as for Stop (claude_coding.STOP_SECONDS).
STOP_SECONDS = 10
# Claude Code tools that stay in the project folder or only read. A cut-off one is settled by the folder read.
CLAUDE_FOLDER_TOOLS = {'Read', 'Glob', 'Grep', 'LS', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'NotebookRead', 'TodoWrite',
                       'TodoRead', 'WebSearch', 'WebFetch', 'ExitPlanMode', 'ToolSearch'}
# Codex tools that stay in the folder or only read. Dervo's own tools reach Codex by their bare names (dervo_*).
CODEX_FOLDER_TOOLS = {'apply_patch', 'update_plan', 'view_image', 'read_file', 'list_dir', 'grep_files', 'web_search'}
# Dervo's own tools that write project files or run checks. Their interruption has its own reconcile path
# (task_evidence, "Reconcile interrupted creation"), so a lost run with one open keeps today's card.
DERVO_WRITES = {'dervo_write_file', 'dervo_edit_file', 'dervo_create_file', 'dervo_run_tests'}
STEP_TEXT = 120

PAUSED_LEAD = 'Paused when Dervo restarted.'


def _when(stamp):
    """Seconds since the epoch from an ISO timestamp ("2026-10-05T06:46:12.345Z"), or None."""
    if not isinstance(stamp, str):
        return None
    from datetime import datetime
    try:
        return round(datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp(), 3)
    except ValueError:
        return None


def _flat(text, limit=STEP_TEXT):
    text = ' '.join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


# ---------------------------------------------------------------- finding a record
def claude_session_path(home, session_id, cwd=None):
    """The Claude Code session file for ``session_id`` in the account's config folder ``home``: the one under ``cwd``'s
    project folder when it's there, else any project folder (a run without full access works in a temporary folder
    that's gone now). None when there isn't one. Opens nothing."""
    if not isinstance(home, str) or not re.fullmatch(r'[0-9a-f-]{36}', session_id or ''):
        return None
    projects = Path(home) / 'projects'
    if cwd:
        import session_continuity
        exact = projects / session_continuity._slug(os.path.realpath(cwd)) / f'{session_id}.jsonl'
        if exact.is_file() and not exact.is_symlink():
            return exact
    try:
        found = [p for p in projects.glob(f'*/{session_id}.jsonl') if p.is_file() and not p.is_symlink()]
    except OSError:
        return None
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def codex_rollout_path(home, thread_id):
    """Codex's rollout for ``thread_id`` under CODEX_HOME ``home``, or None. Opens nothing."""
    if not isinstance(home, str) or not re.fullmatch(r'[0-9A-Za-z-]{8,64}', thread_id or ''):
        return None
    try:
        found = [p for p in (Path(home) / 'sessions').glob(f'*/*/*/rollout-*-{thread_id}.jsonl') if p.is_file() and not p.is_symlink()]
    except OSError:
        return None
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _lines(path):
    """The record's parsed lines from its last TAIL_BYTES, or None when it can't be read or a line isn't JSON (a record
    cut mid-line, which a SIGKILL can leave, counts as unreadable: nobody can say what the cut line held)."""
    try:
        with open(path, 'rb') as handle:
            size = handle.seek(0, os.SEEK_END)
            start = max(0, size - TAIL_BYTES)
            handle.seek(start)
            data = handle.read()
    except OSError:
        return None
    parts = data.split(b'\n')
    if start:
        parts = parts[1:]  # the first line of a window that starts mid-file is cut
    records = []
    for part in parts:
        if not part.strip():
            continue
        try:
            record = json.loads(part)
        except ValueError:
            return None
        if isinstance(record, dict):
            records.append(record)
    return records


# ---------------------------------------------------------------- reading a record
def _outcome(open_steps, cut, provider):
    """The record's verdict from the steps left open (no result) and those it shows cut off by an interrupt."""
    for step in open_steps + cut:
        if step['reach'] == 'dervo-write':
            return dict(state='unreadable', provider=provider, reason='dervo_step', step=_public(step))
    outside = [step for step in open_steps + cut if step['reach'] == 'outside']
    if outside:
        return dict(state='unsure', provider=provider, step=_public(outside[0]))
    return dict(state='paused', provider=provider, step=None)


def _public(step):
    return {'tool': step['tool'], 'text': step.get('text'), 'at': step.get('at')}


def _claude_reach(name):
    if not isinstance(name, str):
        return 'outside'
    if name.startswith('mcp__dervo__'):
        return 'dervo-write' if name[len('mcp__dervo__'):] in DERVO_WRITES else 'folder'
    return 'folder' if name in CLAUDE_FOLDER_TOOLS else 'outside'


def _claude_text(name, data):
    data = data if isinstance(data, dict) else {}
    if name == 'Bash' and isinstance(data.get('command'), str):
        return _flat(data['command'].strip().splitlines()[0] if data['command'].strip() else '')
    if isinstance(name, str) and name.startswith('mcp__'):
        parts = name.split('__')
        return _flat(f'{parts[1]} {parts[-1]}' if len(parts) >= 3 else name)
    if name in ('Agent', 'Task') and isinstance(data.get('description'), str):
        return _flat(data['description'])
    return None


def _records(path):
    """(records, why not): the parsed lines, or [] and 'missing', 'cut' (a line isn't JSON) or 'empty'."""
    if path is None:
        return [], 'missing'
    records = _lines(path)
    if records is None:
        return [], 'cut'
    return records, 'empty'


def _claude_prompt(record):
    """A line that starts a turn: something you typed (the task, a message sent mid-run). Not a tool result, not Claude
    Code's own notes (``isMeta``, "[Request interrupted by user]", which it writes after a stop), not a sub-agent's."""
    if record.get('type') != 'user' or record.get('isSidechain') or record.get('isMeta'):
        return False
    message = record.get('message') if isinstance(record.get('message'), dict) else {}
    content = message.get('content')
    if isinstance(content, list):
        if any(isinstance(p, dict) and p.get('type') == 'tool_result' for p in content):
            return False
        content = ' '.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
    return isinstance(content, str) and bool(content.strip()) and not content.lstrip().startswith('[Request interrupted')


def _interrupted(content):
    """Whether a tool result is Claude Code's own "interrupted" result: the step started and was cut off by the stop."""
    if isinstance(content, list):
        content = ' '.join(part.get('text', '') for part in content if isinstance(part, dict))
    return isinstance(content, str) and 'interrupted' in content.lower()


def read_claude(path):
    """What a Claude Code session file says about its current turn (the steps after the last thing you typed):
    {state, provider, step, working}. ``working`` is True when the turn hadn't ended: a step was open, or the last line
    is a prompt or a tool result the model hadn't answered yet."""
    records, why = _records(path)
    if not records:
        return dict(state='unreadable', provider='claude', reason=why, working=False)
    turn = []
    for record in records:
        if _claude_prompt(record):
            turn = []  # a prompt or a message you sent: what came before it was settled then
        turn.append(record)
    started, results, cut, last = {}, set(), [], None
    for record in turn:
        kind = record.get('type')
        message = record.get('message') if isinstance(record.get('message'), dict) else {}
        content = message.get('content')
        if kind in ('user', 'assistant'):
            last = record
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if kind == 'assistant' and part.get('type') == 'tool_use' and isinstance(part.get('id'), str):
                name = part.get('name')
                started[part['id']] = dict(tool=name if isinstance(name, str) else '?', reach=_claude_reach(name),
                                           text=_claude_text(name, part.get('input')), at=_when(record.get('timestamp')))
            elif kind == 'user' and part.get('type') == 'tool_result' and isinstance(part.get('tool_use_id'), str):
                results.add(part['tool_use_id'])
                step = started.get(part['tool_use_id'])
                if step is not None and part.get('is_error') and _interrupted(part.get('content')):
                    cut.append(step)
    open_steps = [step for key, step in started.items() if key not in results]
    verdict = _outcome(open_steps, cut, 'claude')
    message = (last or {}).get('message') if isinstance((last or {}).get('message'), dict) else {}
    verdict['working'] = bool(open_steps) or (last is not None and last.get('type') == 'user') or (
        last is not None and message.get('stop_reason') not in (None, 'end_turn', 'stop_sequence', 'max_tokens'))
    return verdict


def _codex_reach(name):
    if not isinstance(name, str):
        return 'outside'
    if name.startswith('dervo_'):
        return 'dervo-write' if name in DERVO_WRITES else 'folder'
    return 'folder' if name in CODEX_FOLDER_TOOLS else 'outside'


def _codex_text(name, arguments):
    data = arguments
    if isinstance(arguments, str):
        try:
            data = json.loads(arguments)
        except ValueError:
            data = {}
    data = data if isinstance(data, dict) else {}
    command = data.get('command') if 'command' in data else data.get('cmd')
    if isinstance(command, list) and command:
        return _flat(str(command[-1]).strip().splitlines()[0] if str(command[-1]).strip() else '')
    if isinstance(command, str) and command.strip():
        return _flat(command.strip().splitlines()[0])
    return None


def read_codex(path):
    """What a Codex rollout says about its last turn: the same verdict as read_claude. A turn starts at a user message
    (event_msg user_message, or a response_item message from the user) and ends at task_complete or turn_aborted."""
    records, why = _records(path)
    if not records:
        return dict(state='unreadable', provider='codex', reason=why, working=False)
    # Two layouts (history_import._codex_steps): older rollouts mark each message you typed with an event_msg
    # user_message as well as the response_item; newer ones only have the response_item.
    events = any(r.get('type') == 'event_msg' and (r.get('payload') or {}).get('type') == 'user_message' for r in records)
    turn, ended = [], False
    for record in records:
        payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
        kind, ptype = record.get('type'), payload.get('type')
        if (events and kind == 'event_msg' and ptype == 'user_message') or (
                not events and kind == 'response_item' and ptype == 'message' and payload.get('role') == 'user'
                and _codex_message(payload).strip() and not _codex_message(payload).lstrip().startswith('<')):
            turn, ended = [], False
        if kind == 'event_msg' and ptype in ('task_complete', 'turn_aborted'):
            ended = True
        turn.append(record)
    started, results, aborted = {}, set(), False
    for record in turn:
        payload = record.get('payload') if isinstance(record.get('payload'), dict) else {}
        kind, ptype = record.get('type'), payload.get('type')
        if kind == 'response_item' and ptype in ('function_call', 'custom_tool_call', 'local_shell_call') and isinstance(payload.get('call_id'), str):
            name = 'local_shell' if ptype == 'local_shell_call' else payload.get('name')
            arguments = payload.get('action') if ptype == 'local_shell_call' else payload.get('arguments', payload.get('input'))
            started[payload['call_id']] = dict(tool=name if isinstance(name, str) else '?', reach=_codex_reach(name),
                                               text=_codex_text(name, arguments), at=_when(record.get('timestamp')))
        elif kind == 'response_item' and ptype in ('function_call_output', 'custom_tool_call_output') and isinstance(payload.get('call_id'), str):
            results.add(payload['call_id'])
        elif kind == 'event_msg' and ptype == 'turn_aborted':
            aborted = True
    open_steps = [step for key, step in started.items() if key not in results]
    # A turn Codex aborted (an interrupt) with a step still open: that step was cut off, never settled by an output.
    verdict = _outcome(open_steps, [], 'codex')
    verdict['working'] = not ended and not aborted
    return verdict


def _codex_message(payload):
    parts = payload.get('content') if isinstance(payload.get('content'), list) else []
    return ''.join(p.get('text', '') for p in parts if isinstance(p, dict))


def read(provider, home, session_id, cwd=None):
    """The verdict for one lost run's record, by provider."""
    try:
        if provider == 'claude':
            return read_claude(claude_session_path(home, session_id, cwd))
        if provider == 'codex':
            return read_codex(codex_rollout_path(home, session_id))
    except Exception:  # noqa: BLE001 - a record Dervo can't read keeps today's card
        pass
    return dict(state='unreadable', provider=provider, reason='format', working=False)


# ---------------------------------------------------------------- the thread
def _alive(pid, saved):
    """The orphan the dead host left: the very process (start time and executable), its own group leader, parent launchd."""
    from coding_integration import process_identity
    if not isinstance(pid, int) or pid <= 1 or not isinstance(saved, dict):
        return False
    live = process_identity(pid)
    return (live is not None and live['start'] == saved.get('start') and live['exe'] == saved.get('exe')
            and live['pgid'] == pid and live['ppid'] == 1)


def stop_orphan(pid, saved, working, *, wait=STOP_SECONDS, sleep=time.sleep):
    """Stop the provider the dead host left running. Working (its record shows a step or a reply in progress): Stop's own
    SIGINT to its group, so it can write its stop, then SIGKILL if it's still there after ``wait`` seconds. Not working:
    SIGKILL straight away, as before (coding_integration.reap_orphan). Returns 'interrupted', 'killed' or None (nothing
    to stop, or it isn't the process that run saved)."""
    import platform_os
    from coding_integration import reap_orphan
    if not _alive(pid, saved):
        return None
    if working:
        import signal
        try:
            platform_os.signal_group(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError, OSError):
            return None
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not _alive(pid, saved):
                return 'interrupted'
            sleep(.1)
    return 'killed' if reap_orphan(pid, saved) else ('interrupted' if working else None)


def work(item, *, folder_read=None, stop=stop_orphan, reader=read):
    """The thread's part for one lost run: stop its orphan, read its record, read its folder. ``item`` is what the restart
    pass copied for it: the task, the provider's config folder (``home``) and this thread's earlier leftovers
    (native_evidence.earlier_left, read on the request thread). Returns (task_id, run_id, result) for ``apply``."""
    task, home = item['task'], item.get('home')
    run = task['runs'][-1]
    provider = (task.get('binding') or {}).get('provider')
    cwd = (task.get('binding') or {}).get('cwd') if task.get('full_access') else None
    session_id = run.get('session_id') or task.get('session_id')
    result = dict(stopped=None)
    try:
        first = reader(provider, home, session_id, cwd)
        if run.get('pid'):
            result['stopped'] = stop(run['pid'], run.get('process'), bool(first.get('working')))
        # After a SIGINT the provider may have written its stop, so the record is read again.
        record = reader(provider, home, session_id, cwd) if result['stopped'] == 'interrupted' else first
    except Exception:  # noqa: BLE001 - never stops the other runs' reads
        record = dict(state='unreadable', provider=provider, reason='format')
    record.pop('working', None)
    result['record'] = record
    if folder_read is None:
        from coding_integration import changes_after_close as folder_read
    result['changed'] = folder_read(task, run)
    if record['state'] == 'paused' and task.get('full_access'):
        result.update(_left(task, item.get('earlier')))
    return task['id'], run.get('id'), result


def _left(task, earlier):
    """What a paused full-access run leaves, the way a Stop's does (native_evidence.unfinished_note): its fingerprints for
    the next run to record as its own, and the sentence naming them. Git runs here, on the thread."""
    import subprocess
    import native_evidence
    from coding_integration import AFTER_CLOSE_CALL_SECONDS
    copy = json.loads(json.dumps(task))
    def timed(*args, **kwargs):
        return subprocess.run(*args, **dict(kwargs, timeout=min(kwargs.get('timeout') or AFTER_CLOSE_CALL_SECONDS, AFTER_CLOSE_CALL_SECONDS)))
    try:
        note = native_evidence.unfinished_note(copy, run=timed, earlier=earlier)
    except Exception:  # noqa: BLE001 - no note keeps the pause; the next run reads the folder itself
        return {}
    last = copy['runs'][-1]
    if not isinstance(last.get('left'), dict):
        return {'note': None}
    return {'left': last['left'], 'left_at': last.get('left_at'), 'note': note}


# ---------------------------------------------------------------- on the request thread
def unsure_text(record, provider):
    """The task's error for a run whose record names one step that may have reached outside the folder."""
    who = 'Codex' if provider == 'codex' else 'Claude Code'
    step = (record.get('step') or {})
    what = f'“{step["text"]}”' if step.get('text') else {'Bash': 'a shell command', 'shell': 'a shell command'}.get(step.get('tool'), 'a step that reaches outside the folder')
    return (f'Dervo closed while this was running. {who}’s own record shows every step ended except {what}, which started '
            'and has no end in it. Running it again could do it twice. Continue, checking first, or leave it stopped.')


def apply(coding, task, run, result, lost_text):
    """Settle one lost run from what the thread found, on the request thread. Only a run still waiting for this read (the
    person hasn't dealt with it) changes state; ``lost_text`` is coding_integration.lost_run_text. Returns whether the
    task changed state (it was settled as paused)."""
    after = run['after_close']
    record = result.get('record') if isinstance(result.get('record'), dict) else {'state': 'unreadable'}
    stopped = result.get('stopped')
    after['reaped'] = bool(after.get('reaped')) or stopped in ('killed', 'interrupted')
    if stopped:
        after['stopped_with'] = 'SIGINT' if stopped == 'interrupted' else 'SIGKILL'
    after['changed'] = result.get('changed')
    full = bool(task.get('full_access'))
    if record.get('state') == 'paused' and full and result.get('changed') is None:
        record = dict(record, state='unreadable', reason='folder')  # the folder read is what settles a full-access pause
    after['record'] = record
    import coding_integration as ci
    if task.get('state') != 'disconnected' or task.get('error') != ci.RESTART_LOST:
        return False
    if record.get('state') == 'paused':
        run.update(state='interrupted', end_cause='restart_paused', settled_at=int(time.time()))
        for token in task.get('tokens') or []:
            if token.get('state') not in ('delivered', 'cancelled'):
                token.update(state='cancelled', ended_at=round(time.time(), 3))
        if 'left' in result:
            run['left'], run['left_at'] = result['left'], result.get('left_at')
        task.update(state='blocked', error=' '.join(filter(None, [PAUSED_LEAD, result.get('note')])))
        return True
    if record.get('state') == 'unsure':
        task['error'] = unsure_text(record, record.get('provider'))
        return False
    task['error'] = lost_text(result.get('changed'), after['reaped'])
    return False


def checking_text(task):
    """What Continue, checking first sends as your message: the run carries on in its own session, told which step's
    outcome is unknown so it checks before running it again (section 2, "Really uncertain")."""
    run = (task.get('runs') or [{}])[-1]
    record = ((run.get('after_close') or {}).get('record') or {}) if isinstance(run, dict) else {}
    step = record.get('step') or {}
    what = f'`{step["text"]}`' if step.get('text') else f'your last {step.get("tool") or "step"} call'
    return (f'Carry on from where you stopped. Dervo restarted while {what} was running, and its outcome is unknown: it '
            'may or may not have happened. Check whether it did before you run it again, then carry on.')
