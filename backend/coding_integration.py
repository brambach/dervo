"""Owner-thread coding coordinator. Renderer never supplies broker results."""
import base64
import json
import os
import re
import sys
import hashlib
import time
import warm_runs
from task_evidence import TaskEvidence
from dervo_connections import Rejected
from task_error import error_text, task_error
from project_access import same_root
import check_profiles
import native_evidence
import platform_os
import secret_scan

def evidence_view(evidence):
    """What task_detail sends to the window. Stored evidence keeps every file's bytes for Undo and Restore, but the
    window only shows hashes and text diffs, and a task that commits screenshots can otherwise push one reply past the
    bridge's line limit (19 Sep: a 9.1 MB reply killed the host). Snapshots keep sha256 and a byte count; binary diffs
    become one line. The stored record is never changed."""
    def slim(value):
        if not isinstance(value, dict) or 'bytes_b64' not in value:
            return value  # a file over 256 KiB is kept beside the database: {sha256, bytes, stored} already
        kept = {k: v for k, v in value.items() if k != 'bytes_b64'}
        kept['bytes'] = len(value['bytes_b64']) * 3 // 4 - value['bytes_b64'][-2:].count('=')
        return kept
    view = dict(evidence)
    attempts = []
    for attempt in evidence.get('attempts') or []:
        attempt = dict(attempt)
        for side in ('before', 'after'):
            if isinstance(attempt.get(side), dict):
                attempt[side] = {path: slim(snap) for path, snap in attempt[side].items()}
        if isinstance(attempt.get('diff'), dict):
            attempt['diff'] = {path: ('Binary diff (hex bytes): not shown here.' if isinstance(diff, str)
                                      and diff.startswith('Binary diff') else diff)
                               for path, diff in attempt['diff'].items()}
        attempts.append(attempt)
    if 'attempts' in evidence:
        view['attempts'] = attempts
    return view


# Tools whose text Dervo keeps and sends to later chats. Their audit is redacted (secret_scan) before it's stored.
KEPT_TEXT_TOOLS = ('dervo_ask_user', 'dervo_remind_user', 'dervo_propose_thread', 'dervo_leave_note', 'dervo_remember', 'dervo_propose_skill', 'dervo_suggest_lesson')
FIELDS = {
    'task_question_answer': {'taskId', 'questionId', 'clientId', 'answer'},
    'tasks': set(), 'task_create': {'text', 'files', 'testFile'},
    'task_propose': {'text', 'messageId'}, 'proposals': set(),
    'proposal_accept': {'proposalId'}, 'proposal_dismiss': {'proposalId'},
    'task_deny': {'taskId'}, 'task_stop': {'taskId'}, 'task_approve': {'taskId', 'scopeVersion'},
    'task_continue': {'taskId', 'text'}, 'task_detail': {'taskId'},
    'task_review': {'taskId', 'attemptNumber', 'decision'},
    'task_reconcile': {'taskId'}, 'task_settle': {'taskId'},
    'task_thread_proposals': {'taskId'}, 'task_thread_start': {'taskId', 'proposalId'}, 'task_thread_dismiss': {'taskId', 'proposalId'},
    'task_commit': {'taskId'}, 'task_undo': {'taskId'},
    'task_restore_file': {'taskId', 'attemptNumber', 'scopeVersion', 'path', 'expectedHash', 'expectedAttempts'},
}


def provider_check_result(task_id, attempt):
    """Send the check outcome and bounded diagnostics; retain full host evidence."""
    test = attempt['test'] or {}
    result = {k: v for k, v in test.items() if k not in ('runtime_files', 'compiler')}
    truncated = []
    for stream in ('stdout', 'stderr'):
        value = result.get(stream, '')
        if len(value) > 4000:
            result[stream] = value[:2000] + '\n[output omitted]\n' + value[-2000:]
            truncated.append(stream)
    compiler = test.get('compiler')
    if compiler:
        result['compiler'] = {k: compiler[k] for k in ('package', 'version', 'bytes') if k in compiler}
        result['compiler'].update(file_count=len(compiler.get('files', [])),
            manifest_sha256=hashlib.sha256(json.dumps(compiler, sort_keys=True).encode()).hexdigest())
    result.update(state=attempt['state'], blocker=attempt['blocker'], attempt=attempt['number'],
        verification_only=attempt.get('kind') == 'verification', output_truncated=truncated,
        evidence={'task_id': task_id, 'attempt': attempt['number'],
                  'detail': 'Complete diagnostics and runtime/compiler provenance are retained in the task inspector.'})
    return result




# Owner coding runs open at once across the host; one per conversation. Three from 17 Sep, ten from 23 Sep (Bryce: "3 is
# way too little"). Each Claude Code run measured about 280 MB, and the file hold (parallel_holder) covers Dervo's own
# write tools only, so full-access runs in one folder can still edit the same file.
MAX_PARALLEL_OWNERS = 10
# How long a run's streamed text may wait in memory before it's saved to the task record (CodingTasks.start, emit).
TEXT_SAVE_SECONDS = 0.5
# When you start typing in a thread, its next Claude Code is started ahead of time once the thread is free, if that
# happens within this long (warm_runs). Past it, nothing is started.
WARM_WAIT_SECONDS = 120
# A send that arrives while its thread's Claude Code is still being started ahead of time waits in the thread's queue,
# as it would behind a run, and starts on that process once it's up (third review, 8 Oct: waiting for it inside the
# send failed queued follow-ups as "Didn't start"). Launching a second one beside it would race it for the session
# file. A start still going after this long is dropped, so the queue never waits on a stuck one.
WARM_START_LIMIT = 30


def check_open(token):
    """Whether a tool call still has a confined check for the pump to poll, or one queued for it to start."""
    return bool(token.get('job_id') and not token.get('job_done')) or (token.get('state') == 'queued' and token.get('tool') == 'dervo_run_tests')


def process_identity(pid):
    """What identifies a live process beyond its pid, read from libproc (no subprocess): its start time in
    microseconds, its executable path, its process group and its parent. None when the pid is gone or unreadable."""
    import ctypes
    import os
    if not isinstance(pid, int) or pid <= 1:
        return None
    import sys
    if sys.platform.startswith('linux'):
        return _linux_process_identity(pid)
    c = ctypes
    class BsdInfo(c.Structure):  # struct proc_bsdinfo, <sys/proc_info.h>
        _fields_ = [('flags', c.c_uint32), ('status', c.c_uint32), ('xstatus', c.c_uint32), ('pid', c.c_uint32), ('ppid', c.c_uint32),
                    ('uid', c.c_uint32), ('gid', c.c_uint32), ('ruid', c.c_uint32), ('rgid', c.c_uint32), ('svuid', c.c_uint32),
                    ('svgid', c.c_uint32), ('rfu_1', c.c_uint32), ('comm', c.c_char * 16), ('name', c.c_char * 32), ('nfiles', c.c_uint32),
                    ('pgid', c.c_uint32), ('pjobc', c.c_uint32), ('e_tdev', c.c_uint32), ('e_tpgid', c.c_uint32), ('nice', c.c_int32),
                    ('start_sec', c.c_uint64), ('start_usec', c.c_uint64)]
    try:
        libproc = c.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        info = BsdInfo()
        if libproc.proc_pidinfo(pid, 3, c.c_uint64(0), c.byref(info), c.sizeof(info)) != c.sizeof(info) or info.pid != pid:  # PROC_PIDTBSDINFO
            return None
        path = c.create_string_buffer(4096)  # PROC_PIDPATHINFO_MAXSIZE
        length = libproc.proc_pidpath(pid, path, c.sizeof(path))
        if length <= 0:
            return None
        return dict(start=info.start_sec * 1_000_000 + info.start_usec, exe=os.fsdecode(path.raw[:length]), pgid=info.pgid, ppid=info.ppid)
    except (OSError, AttributeError, ValueError):
        return None


def _linux_process_identity(pid):
    """process_identity from /proc: /proc/<pid>/stat for the parent, the group and the start time (clock ticks after
    boot, made absolute with the boot time in /proc/stat so it compares like the Mac's), and /proc/<pid>/exe."""
    import os
    try:
        with open(f'/proc/{pid}/stat', 'rb') as stat:
            fields = stat.read().rsplit(b')', 1)[1].split()  # after the command name, which may hold spaces or ')'
        exe = os.readlink(f'/proc/{pid}/exe')
        with open('/proc/stat', 'rb') as system:
            boot = next(int(line.split()[1]) for line in system if line.startswith(b'btime '))
        ticks = os.sysconf('SC_CLK_TCK')
        # fields[0] is the state, then ppid, pgrp, ... and starttime is the 22nd field of the whole line.
        start = boot * 1_000_000 + int(fields[19]) * 1_000_000 // ticks
        return dict(start=start, exe=exe, pgid=int(fields[2]), ppid=int(fields[1]))
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def provider_identity(pid):
    """What a run saves with its provider's pid so a later host can tell that process from a reused pid: start time and
    executable. Only for a process this host started itself (its parent is this process) and that leads its own group,
    which is what transport.OwnedProcess launches. None otherwise, and a run without one is never reaped."""
    import os
    live = process_identity(pid)
    if live is None or live['ppid'] != os.getpid() or live['pgid'] != pid:
        return None
    return dict(start=live['start'], exe=live['exe'])


def run_alive(run):
    """Whether the provider process a run saved (its pid and provider_identity) is still running: the very one, with the
    same start time and executable. A handoff waits for this to be False, so a stopped agent that's still running can't
    write after the next one starts (b16/bridge). False when the run saved no identity: that run was never full access,
    or its host didn't start it, and its turn closing is what ended it."""
    if not isinstance(run, dict) or not isinstance(run.get('pid'), int) or not isinstance(run.get('process'), dict):
        return False
    live = process_identity(run['pid'])
    return live is not None and live['start'] == run['process'].get('start') and live['exe'] == run['process'].get('exe')


def reap_orphan(pid, saved):
    """A provider CLI left running by a host that died without closing (a crash, a Force Quit, the bridge's 10 s kill).
    Providers run in their own session (transport.OwnedProcess, start_new_session), so the group outlives the host and,
    in full access, the real CLI may keep editing the project (run-lifecycle.md, finding 1). SIGKILL the group only when
    the live process is the very one the dead host started: same start time and executable as ``saved``
    (provider_identity), still its own group leader, and orphaned (its parent is launchd, because the host that started
    it is gone). A reused pid (after a reboot, say, the Claude app or a Terminal session) never matches, and a pid with
    nothing saved is never signalled. Returns True when it signalled, False otherwise. Never raises."""
    import os
    import signal
    if not isinstance(pid, int) or pid <= 1 or not isinstance(saved, dict):
        return False
    live = process_identity(pid)
    if live is None or live['start'] != saved.get('start') or live['exe'] != saved.get('exe'):
        return False  # gone, or the pid now belongs to something else
    if live['pgid'] != pid or live['ppid'] != 1:
        return False
    try:
        from process_descendants import descendants, stop_descendants
        children = descendants(pid)
        platform_os.signal_group(pid, platform_os.SIGKILL)
        stop_descendants(children)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False

AFTER_CLOSE_CALL_SECONDS = 4   # one git call of the folder read after a crash: well under the bridge's 10 s
AFTER_CLOSE_SECONDS = 12       # the whole read; past it the task keeps RESTART_LOST's wording


def folder_read_wanted(task, run):
    """Whether a lost run's folder is worth reading: full access, a recorded git starting point, a bound folder."""
    return bool(task.get('full_access') and isinstance(run, dict) and isinstance(run.get('baseline'), dict)
                and isinstance((task.get('binding') or {}).get('cwd'), str))


def changes_after_close(task, run, *, per_call=AFTER_CLOSE_CALL_SECONDS, budget=AFTER_CLOSE_SECONDS):
    """What a full-access run's folder holds beyond the git starting point the run recorded (runs[-1].baseline): the
    files the agent changed, whether before the host died or after it, since the project folder is its working
    directory and the real CLI keeps editing without a host (night 1, finding 2). None when the run wasn't full access,
    recorded no baseline, or git can't answer (not a repository, history moved, a slow disk). Never raises.

    Never on the startup path (security review 20 Sep, M1): CodingTasks.__init__ saves the task first and reads the
    folder on a thread, so a folder that takes longer than the bridge allows can't keep the host from answering, and
    a relaunch finds the task already marked and reads nothing again. Each git call is capped at ``per_call`` seconds
    and the whole read at ``budget``."""
    import subprocess
    base = run.get('baseline') if isinstance(run, dict) else None
    cwd = (task.get('binding') or {}).get('cwd')
    if not folder_read_wanted(task, run):
        return None
    deadline = time.monotonic() + budget
    def timed(*args, **kwargs):
        left = deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired(args[0] if args else 'git', 0)
        return subprocess.run(*args, **dict(kwargs, timeout=min(kwargs.get('timeout') or left, left, per_call)))
    try:
        import native_evidence
        changed, problems, others = native_evidence.changes(cwd, base, run=timed)
    except (Rejected, OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        return None
    # A file deleted or replaced by something odd is still a change the person should look at.
    return sorted(set(changed) | set(others) | {p.split(' (')[0] for p in problems if isinstance(p, str)})


def lost_run_text(changed, reaped):
    """The task's error after a crash, from what this host could find out on relaunch. ``changed`` is None when the
    folder couldn't be read (the wording stays RESTART_LOST), ``reaped`` whether the agent was still running and was
    stopped here."""
    if changed is None:
        return RESTART_LOST
    if not changed:
        return ('Dervo closed while this was running and stopped the agent on this relaunch. Nothing in the project folder changed. Leave it stopped, or send a message to carry on.'
                if reaped else 'Dervo closed while this was running, and the agent ended on its own. Nothing in the project folder changed. Leave it stopped, or send a message to carry on.')
    from native_evidence import named
    files = named(changed)
    how = 'until Dervo stopped it on this relaunch' if reaped else 'and finished on its own'
    return f'Dervo closed while this was running. The agent kept working {how}: {files} changed. Review them before you carry on, then leave it stopped or send a message.'


# What a task says after the host that ran it died without closing (a crash, a Force Quit, the bridge's 10 s kill) and
# this host found it still marked working. The run may have reached the agent, so its outcome is unknown.
RESTART_LOST = 'Dervo closed while this was running, so it can’t say how it ended. Check your folder, then leave it stopped.'
# The same, for a run that hadn't sent the task yet (run state ``checking``: account check, launch, session handshake).
# Nothing reached the agent, so there's nothing to check and the request can simply go again.
RESTART_UNSENT = 'Dervo closed before this started. Nothing was sent. Send it again.'
# What Leave it stopped (task_settle) adds to a lost run's error. Up to 17.1 the button was Mark as stopped and said "You
# marked this stopped".
LEFT_STOPPED = 'You left this stopped. Dervo never confirmed what the run did, so check your project folder or git before building on it.'
LEFT_CHECKING = 'You chose to continue, checking first.'


def without_leave_hint(error):
    """A lost run's words without the sentence that offers Leave it stopped ("Check your folder, then leave it stopped.",
    "Continue, checking first, or leave it stopped."), once you've chosen: the card said "then leave it stopped. You left
    this stopped." back to back. What happened stays; file names keep their dots (a sentence ends at a stop and a space)."""
    sentences = re.split(r'(?<=[.?!])\s+', (error or '').strip())
    return ' '.join(s for s in sentences if s and 'leave it stopped' not in s.lower())
# A clean quit with a run open that didn't finish its step inside QUIT_PAUSE_SECONDS: close() paused the agent, but the
# run had no chance to report how far it got. A run that did finish its step in time ends as an ordinary pause instead.
CLOSE_LOST = 'Dervo quit mid-run and paused it before it could say how far it got. Check your folder, then send a message to carry on.'
# How long a quit waits for paused runs to finish the step they're on, all runs together. The app gives the host 4 s to
# exit (src/bridge.rs, Worker::drop), and the rest of shutdown needs some of that.
QUIT_PAUSE_SECONDS = 2.0


def during_task(tools):
    """The instructions that go with the request itself, for the tools this task has: the Task guide, other chats in the
    folder, and looking up earlier work. Empty when none of those tools is offered."""
    import chat_links
    import project_memory
    from task_guide import TOOL as GUIDE_TOOL, PROMPT as GUIDE_PROMPT
    blocks = []
    if GUIDE_TOOL in tools:
        blocks.append(GUIDE_PROMPT)
    if chat_links.TOOL in tools:
        blocks.append(chat_links.PROMPT)
    if 'dervo_propose_thread' in tools:
        import thread_fanout
        blocks.append(thread_fanout.PROMPT)
    if 'dervo_leave_note' in tools:
        import thread_notes
        blocks.append(thread_notes.PROMPT)
    if 'dervo_ask_user' in tools:
        import task_questions
        blocks.append(task_questions.PROMPT)
    if 'dervo_remind_user' in tools:
        import task_reminders
        blocks.append(task_reminders.PROMPT)
    if project_memory.REMEMBER_TOOL in tools:
        blocks.append(project_memory.MESSAGE_FACTS_PROMPT)
    if project_memory.SEARCH_TOOL in tools:
        blocks.append(project_memory.SEARCH_PROMPT)
    return ''.join('\n\n' + block for block in blocks)


FINISHING_HEAD = ('Before your final answer{after_guide}, go through this list once, in this order. Each item is one tool '
                  'call or nothing; none of them is your answer, and none of them is optional because the task was small.')


def finishing_steps(db, project_id, tools):
    """The one numbered list of what an agent does before its final answer, for the tools this task has, or '' when none
    applies. Memory is first: it saves at once and it is the step agents skipped when it was the third of five separate
    trailing blocks (critical review, 20 Sep, section 10; hard case m44). Lessons and skills are proposals the person
    reviews, so they follow. Looking back (the project's own switch) closes the list."""
    import project_lessons
    import project_memory
    import project_skills
    from task_guide import TOOL as GUIDE_TOOL
    steps = []
    if project_memory.REMEMBER_TOOL in tools:
        steps.append(project_memory.PROMPT)
    if project_lessons.TOOL in tools:
        steps.append(project_lessons.PROMPT)
    if project_skills.TOOL in tools:
        steps.append(project_skills.PROMPT)
    reflect = project_memory.reflect_tail(db, project_id, tools).strip()
    if reflect:
        steps.append(reflect)
    if not steps:
        return ''
    head = FINISHING_HEAD.format(after_guide=' and after your last dervo_update_task_guide call' if GUIDE_TOOL in tools else '')
    return '\n\n' + head + '\n' + '\n'.join(f'{number}. {step}' for number, step in enumerate(steps, 1))


def place_text(messages, run_id, item_id, kind, text, now=None):
    """Put a run's streamed text into the task's messages. A provider streams one text item per answer, and Dervo used to
    grow a single message for it, created when the first words arrived. A message you sent mid-run (``steer``) comes after
    that message, so the whole answer, including what the agent wrote after reading yours, sat above it and your message
    stayed at the bottom of the thread (Bryce, 24 Sep). Text that arrives after your latest mid-run message now goes into
    a new message below it (item id ``<item>#<index of your message>``), so the thread reads in the order it happened.
    ``snapshot`` is the item's whole text so far: the part already shown above your message is taken off the front."""
    segments = [m for m in messages if m.get('role') == 'assistant' and m.get('run_id') == run_id
                and (m.get('item_id') == item_id or str(m.get('item_id', '')).startswith(f'{item_id}#'))]
    steer_at = max((index for index, m in enumerate(messages) if m.get('role') == 'user' and m.get('steer')
                    and m.get('run_id') == run_id), default=None)
    item = segments[-1] if segments else None
    if item is None or (steer_at is not None and messages.index(item) < steer_at):
        item = dict(role='assistant', text='', run_id=run_id, created_at=int(now if now is not None else time.time()),
                    item_id=item_id if item is None else f'{item_id}#{steer_at}')
        messages.append(item)
        segments.append(item)
    if kind == 'snapshot':
        before = ''.join(m['text'] for m in segments if m is not item)
        item['text'] = text[len(before):] if before and text.startswith(before) else text
    else:
        item['text'] += text
    item['text'] = item['text'][:65536]
    return item


SUBAGENT_FIELDS = ('description', 'kind', 'background', 'isolation', 'parent', 'last_tool', 'tokens', 'tool_uses', 'duration_ms', 'summary')


def record_subagent(run, data):
    """Claude Code's sub-agents in a full-access run (16.2), as ``run['subagents']``: one entry per Agent call, in order,
    for the reply to show. ``state`` goes called, running, then returned (a foreground one done, a background one only
    launched), completed, failed or stopped; refused if it asked for a worktree; unknown if the run ended first. Its steps are the run's native tokens whose
    ``parent`` is its ``id``. Never its prompt or its words, only the short summary Claude Code gives when it finishes."""
    calls = run.setdefault('subagents', [])
    call = next((c for c in calls if c['id'] == data['id']), None)
    now = round(time.time(), 3)
    if call is None:
        if len(calls) >= 100:
            return
        call = dict(id=data['id'], state='called', called_at=now)
        calls.append(call)
    call.update({key: data[key] for key in SUBAGENT_FIELDS if data.get(key) is not None})
    phase = data.get('phase')
    if phase == 'started' and call['state'] == 'called':
        call.update(state='running', started_at=now)
    elif phase == 'refused' and call['state'] == 'called':
        call.update(state='refused', ended_at=now)  # it asked for a worktree (claude_policy.subagent_hook)
    elif phase == 'returned' and call['state'] in ('called', 'running'):
        call.update(state='failed' if data.get('is_error') else 'returned', returned_at=now)
    elif phase == 'finished':
        call.update(state=data.get('status') or 'unknown', ended_at=now)


class CodingTasks:
    def __init__(self, host):
        self.host, self.store = host, host.store
        self.evidence = TaskEvidence(self.store)
        self.turns = {}
        # Claude Code started ahead of time for a thread's next message, and the threads waiting for one (warm_runs).
        self.warm = warm_runs.WarmPool()
        self.warm_wanted = {}  # thread id -> monotonic time past which it isn't started
        # The launch itself (Claude Code's version and account checks, then the spawn) runs here, never on the thread
        # that answers the window (code review 8 Oct, M2). thread id -> (future of the turn, Held).
        self.warm_starter = None
        self.warm_starting = {}
        import thread_states
        self.alive = thread_states.Alive(self.store.db)  # when each working run last heard from its provider (state spine)
        self.alive_runs = {}  # task id -> thread id of each run Alive is listening to, so an ended one is forgotten
        # {task_id: save_text} for each run started here: saves the run's streamed text that hasn't reached its record yet.
        self.text_waiting = {}
        # Task ids whose record may hold a check to poll or start (check_open). Only the pump adds checks, after a tool
        # request, so a run outside this set and with no request waiting needs no read of its record (_tick_turn).
        self.checks_open = set()
        # Runs this host found left open by the one before it, marked in the restart pass below: {task_id, thread_id,
        # project_id} each. The host's status reply shows them once, as one line, so a relaunch that lost runs says so.
        self.lost_runs = []
        # Task ids whose run shared its project folder with another open run at some point. A full-access run that
        # overlapped is credited only with the files its own edits touched (native_evidence.finish).
        self.overlapped = set()
        self.pending = {'review': {}, 'acceptance': {}}
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS coding_tool_audit (task_id TEXT NOT NULL, token TEXT NOT NULL, document TEXT NOT NULL, PRIMARY KEY(task_id, token))')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS coding_proposals (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            import thread_fanout
            thread_fanout.schema(self.store.db)
            import task_questions
            task_questions.schema(self.store.db)
            import thread_notes
            thread_notes.schema(self.store.db)
            from task_activity import schema
            schema(self.store.db)
            import conversation_activity
            conversation_activity.schema(self.store.db)
            import provider_usage
            provider_usage.schema(self.store.db)
        import thread_folders  # beta.18: a thread's own branch and folder (setup, the check before a run, Merge)
        self.folders = thread_folders.Folders(self, root=getattr(host, 'worktrees_root', None))

        # The restart pass. A task still ``working`` here was running in a host that died without closing. Home, the
        # sidebar and pickup read these same records, so the host runs this before it answers anything (Host.__init__
        # constructs CodingTasks at start; run-lifecycle.md, finding 2). Nothing slow happens here: the reap is a
        # libproc read and a signal, and the folder read of a full-access survivor (git in the project folder, up to
        # AFTER_CLOSE_SECONDS) runs on a thread after every task is saved, with its result applied on the tick. Before
        # 20 Sep the read ran here with a 10 s budget, the bridge kills a host that takes 10 s to answer, and the task
        # was saved only after it: one slow folder could keep Dervo from opening at all (security review, M1).
        folder_reads, recordings = [], []
        import queue
        self.recordings = queue.Queue()  # (task_id, run_id, prepared) from record_prepared's thread, for settle_recordings
        self.recording = {}  # task id: thread id for a finished run whose changes are still being recorded
        for rowid, document in self.store.db.execute('SELECT rowid, document FROM coding_tasks ORDER BY rowid').fetchall():
            task = json.loads(document)
            self.track_pending(task, rowid)
            last = task['runs'][-1] if task.get('runs') else None
            if task['state'] == 'working' and isinstance(last, dict) and last.get('recording') and last.get('state') == 'completed':
                # Beta.18: the run finished and its changes were being recorded when the host died. The folder still
                # holds them, so recording goes again (below, once every task is saved).
                recordings.append(task)
                continue
            if task['state'] == 'working':
                # A working task always has a run (start appends one before saving), but a damaged record with none
                # must not stop the whole host from starting: it still reads as disconnected, with nothing to mark.
                run = task['runs'][-1] if task.get('runs') else None
                if run is not None and run.get('state') == 'checking':
                    # The task text hadn't gone to the agent yet (context() moves the run to ``sending`` first), so the
                    # outcome is known: nothing happened. Blocked, not disconnected, and the request can go again.
                    task.update(state='blocked', error=RESTART_UNSENT)
                    run['state'] = 'failed'
                    run['end_cause'] = 'host_restart'
                else:
                    task.update(state='disconnected', error=RESTART_LOST)
                    if run is not None:
                        run['state'] = 'uncertain'
                        # Beta.18 (direction B, lost_runs): the thread stops the provider group the dead host left
                        # running (Stop's SIGINT when its record shows it working, SIGKILL otherwise), reads the
                        # provider's own record and the folder, and the tick settles the run from them. A full-access
                        # agent works in the project folder itself, so it may have kept editing after the host died, or
                        # finished on its own (night 1, finding 2). Until then the record says only RESTART_LOST.
                        run['after_close'] = dict(reaped=False, changed=None, checking=True)
                        folder_reads.append(self._lost_item(task))
                    context = task.get('context') or {}
                    self.lost_runs.append({'task_id': task['id'], 'thread_id': context.get('thread_id'), 'project_id': context.get('project_id')})
                for token in task['tokens']:
                    if token['state'] not in ('delivered', 'cancelled'):
                        token['state'] = 'uncertain'
                self.save(task)
            elif any(token.get('native') and token.get('state') == 'running' for token in task.get('tokens', [])):
                # A provider step (Bash, a web search) can't still be running in a host that's only now starting. Up to
                # 16.4, close() marked a quit run disconnected but left its step 'running', so Mark as stopped refused
                # and every update and archive was held for good (29 Sep). Unknown is the honest state for it.
                for token in task['tokens']:
                    if token.get('native') and token.get('state') == 'running':
                        token['state'] = 'uncertain'
                self.save(task)
        # A Dervo commit that moved HEAD but never refreshed the index (a crash between the two) is finished now,
        # before any task reads its folder (task_git.repair_pending, reviewer 20 Sep). Never stops the start.
        try:
            import task_git
            if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'").fetchone():
                folders = self.store.db.execute("SELECT cwd FROM thread_folders WHERE kind='thread' AND state='ready'").fetchall() \
                    if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_folders'").fetchone() else []
                for (cwd,) in self.store.db.execute('SELECT DISTINCT cwd FROM projects').fetchall() + folders:
                    if isinstance(cwd, str) and cwd and os.path.isdir(cwd):
                        try:
                            task_git.repair_pending(cwd)
                        except Rejected as exc:
                            print(f'Pending Dervo commit in {cwd}: {exc}', file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f'Pending commit check skipped: {type(exc).__name__}: {exc}', file=sys.stderr)
        import queue
        self.folder_reads = queue.Queue()  # (task_id, run_id, result) from the thread, applied by settle_folder_reads
        for task in recordings:
            # Recording that a restart cut off goes again, on its thread, and is applied on the tick either way.
            prepared = self.record_prepared(task, wait=0)
            if prepared is not None:
                self.recordings.put((task['id'], task['runs'][-1].get('id'), prepared))
                self.recording[task['id']] = task['context']['thread_id']
        if folder_reads:
            import threading
            threading.Thread(target=self._read_folders, args=(folder_reads,), name='dervo-after-close', daemon=True).start()
        from conversation_coding import ConversationCoding
        self.conversation = ConversationCoding(self)
        task_questions.recover(self)
        thread_notes.recover(self)
        self.recover_checks()
        from collaboration import Collaboration
        self.collaboration = Collaboration(self)
        from human_acceptance import HumanAcceptance
        self.acceptance = HumanAcceptance(self)  # optional human result approval: a project policy, snapshotted per request
        from project_knowledge import ProjectKnowledge
        self.knowledge = ProjectKnowledge(self.store)
        # Handoff snapshots whose work settled more than a week ago go at start, before anything can offer a restore.
        # Bounded, and it never stops a start (ConversationCoding.sweep_handoffs).
        self.conversation.sweep_handoffs()

    def _lost_item(self, task):
        """What the thread needs for one lost run (lost_runs.work), read here on the request thread: its own copy of the
        task, the provider's config folder where its record lives, and this thread's earlier leftovers in the folder."""
        home = earlier = None
        try:
            home = self.store.binding(**task['context']).get('home')
        except Exception:  # noqa: BLE001 - a thread or account that's gone: no record to read, today's card
            pass
        if folder_read_wanted(task, task['runs'][-1]):
            try:
                earlier = native_evidence.earlier_left(self, task)
            except Exception:  # noqa: BLE001 - without it a leftover reads as your change, as before 15
                pass
        return dict(task=json.loads(json.dumps(task)), home=home, earlier=earlier)

    def _read_folders(self, items):
        """The thread: each lost run's orphan stopped, its record and its folder read (lost_runs.work). It touches no
        record; the result queues for the tick."""
        import lost_runs
        for item in items:
            try:
                self.folder_reads.put(lost_runs.work(item))
            except Exception as exc:  # noqa: BLE001 - one run's read can't stop the others'
                run = item['task']['runs'][-1]
                self.folder_reads.put((item['task']['id'], run.get('id'), dict(record=dict(state='unreadable', reason='error'), changed=None)))
                print(f'A lost run could not be read: {type(exc).__name__}: {exc}', file=sys.stderr)

    RECORD_WAIT_SECONDS = 2  # how long a run's end waits for the recording thread before leaving it to the tick

    def record_prepared(self, task, wait=None):
        """Run the git part of recording a finished full-access run (native_evidence.prepare) on a thread. Answers its
        result when it's back within ``wait`` seconds, else None: the thread queues it for settle_recordings, and the task's
        thread counts as busy until then."""
        import threading
        import native_evidence
        try:
            inputs = native_evidence.recording_inputs(self, task)
        except Exception as exc:  # noqa: BLE001 - recorded as the reason, the way finish would have raised
            return {'error': f'Dervo couldn’t read this run’s record to save its changes: {type(exc).__name__}.'}
        lock, done = threading.Lock(), {}
        run_id, thread_id = task['runs'][-1].get('id'), task['context']['thread_id']
        def work():
            try:
                prepared = native_evidence.prepare(*inputs)
            except Exception as exc:  # noqa: BLE001 - one folder can't take the host down
                prepared = {'error': f'Dervo couldn’t read the project folder to record this run: {type(exc).__name__}.'}
            with lock:
                done['prepared'] = prepared
                if done.get('late'):
                    self.recordings.put((task['id'], run_id, prepared))
        worker = threading.Thread(target=work, name='dervo-recording', daemon=True)
        worker.start()
        worker.join(self.RECORD_WAIT_SECONDS if wait is None else wait)
        with lock:
            if 'prepared' in done:
                return done['prepared']
            done['late'] = True
            self.recording[task['id']] = thread_id
        return None

    def finish_recording(self, task, prepared):
        """Record a finished full-access run from ``prepare``'s answer (native_evidence.finish), then the project's
        automatic commit. Updates ``task`` in place; the caller saves it."""
        import native_evidence
        try:
            native_evidence.finish(self, task, prepared=prepared)
        except Rejected as exc:
            task.update(state='blocked', error=str(exc))
        else:
            if not self.start_checks(task):
                self.auto_commit(task)  # with checks, the commit waits for them (settle_checks)

    def start_checks(self, task):
        """Run the project's own checks on a full-access result that changed files, on a thread (project_checks). True
        when they started: the run reads as running its checks, and the automatic commit waits for them."""
        import project_checks
        if task.get('origin') != 'conversation' or task.get('state') != 'ready_for_review' or task.get('error'):
            return False
        commands = project_checks.read(self.store.db, task['context']['project_id'])
        run = task['runs'][-1] if task.get('runs') else None
        cwd = (task.get('binding') or {}).get('cwd')
        if not commands or not isinstance(run, dict) or not cwd or task.get('full_access') is not True:
            return False  # only a full-access result, which you allowed to change the folder, runs the folder's checks
        if task.get('commands') is False:
            # A thread that can't run commands only edits files. Running the project's checks on those edits would run
            # code it wrote (a test, a package script) all the same, so they don't run (review, 8 Oct).
            run['checks'] = {'state': 'skipped', 'ended': time.time(), 'results': [],
                             'summary': 'Checks didn’t run, because this thread can’t run commands'}
            return False
        base = (run.get('baseline') or {}).get('head')
        cached = (project_checks.cached_baseline(self.store.db, task['context']['project_id'], base, commands,
                                                 key=project_checks.dependencies_key(cwd)) if base else None)
        if not hasattr(self, 'checks_running'):
            self.checks_running, self.checks_overlapped = {}, set()
        if not hasattr(self, 'check_pool'):
            from concurrent.futures import ThreadPoolExecutor
            self.check_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-checks')  # one folder's checks at a time
        scratch = os.path.join(str(getattr(self.host, 'worktrees_root', None) or os.path.dirname(cwd)), '.checks')
        future = self.check_pool.submit(project_checks.check_run, cwd, base, commands, scratch, cached,
                                        key=project_checks.dependencies_key(cwd))
        self.checks_running[task['id']] = dict(future=future, run_id=run['id'], cwd=cwd, commands=commands,
                                               project_id=task['context']['project_id'], base=base)
        run['checks'] = {'state': 'running', 'started': time.time(), 'names': [c['name'] for c in commands]}
        return True

    def recover_checks(self):
        """Checks the last Dervo was running when it closed never reported: their runs say so, and wait for you. Clean
        checkouts it left behind are removed (project_checks.clear_scratch)."""
        import project_checks
        root = getattr(self.host, 'worktrees_root', None)
        if root:
            project_checks.clear_scratch(os.path.join(str(root), '.checks'))
        rows = self.store.db.execute(
            "SELECT document FROM coding_tasks WHERE json_extract(document, '$.runs[#-1].checks.state')='running'").fetchall()
        for (document,) in rows:
            task = json.loads(document)
            task['runs'][-1]['checks'] = dict(task['runs'][-1]['checks'], state='not_run', ended=time.time(),
                                              summary='Dervo closed before the checks finished, so they didn’t report')
            self.save(task)

    def note_check_overlap(self, cwd):
        """Another run starting in a folder whose checks are still running: those results no longer describe one run."""
        for task_id, job in getattr(self, 'checks_running', {}).items():
            if job['cwd'] == cwd:
                self.checks_overlapped.add(task_id)

    def settle_checks(self):
        """Put finished checks on their run. No new failures, and nothing else ran in the folder meanwhile: the result
        commits on its own if the project says so (auto_commit). Anything else waits for you, the failure first."""
        import project_checks
        for task_id, job in list(getattr(self, 'checks_running', {}).items()):
            if not job['future'].done():
                continue
            self.checks_running.pop(task_id)
            overlapped = task_id in self.checks_overlapped
            self.checks_overlapped.discard(task_id)
            result = job['future'].result()
            measured = result.pop('measured', None)
            if measured is not None and job['base']:
                try:
                    project_checks.keep_baseline(self.store.db, job['project_id'], job['base'], job['commands'], measured,
                                                 key=result.get('key'))
                except Exception as exc:  # noqa: BLE001 - the result itself still reaches its run
                    print(f'Checks: the clean checkout’s result wasn’t kept: {type(exc).__name__}', file=sys.stderr)
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                continue
            task = json.loads(row[0])
            run = next((r for r in task.get('runs') or [] if isinstance(r, dict) and r.get('id') == job['run_id']), None)
            if run is None:
                continue
            if overlapped:
                result['overlapped'] = True
                result['summary'] = (result.get('summary') or 'Checks ran') + '. Another run changed the folder while they ran, so check again before relying on them'
            run['checks'] = dict(result, started=(run.get('checks') or {}).get('started'))
            if result['state'] == 'passed' and not overlapped and task.get('state') == 'ready_for_review':
                self.auto_commit(task)
            self.save(task)

    def settle_recordings(self):
        """Apply recordings the thread finished late, on the request thread, to a task still waiting for one."""
        import queue
        while True:
            try:
                task_id, run_id, prepared = self.recordings.get_nowait()
            except queue.Empty:
                return
            self.recording.pop(task_id, None)
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                continue
            task = json.loads(row[0])
            run = task['runs'][-1] if task.get('runs') else None
            if not isinstance(run, dict) or run.get('id') != run_id or not run.pop('recording', None) or task.get('state') != 'working':
                continue
            task.update(state='ready_for_review', error=None)
            self.finish_recording(task, prepared)
            self.save(task)

    def settle_folder_reads(self):
        """Apply what the thread found, on the request thread, to the record as it is now (lost_runs.apply): a run still
        marked ``checking`` gets what its record and folder showed, and a task the person hasn't dealt with is settled
        from them (paused, one step unsure, or today's wording, lost_run_text). A task the person already dealt with
        (left stopped, sent again) keeps whatever it says now; only the run's ``after_close`` is completed."""
        import queue
        import lost_runs
        while True:
            try:
                task_id, run_id, result = self.folder_reads.get_nowait()
            except queue.Empty:
                return
            if not isinstance(result, dict):
                result = dict(changed=result, record=dict(state='unreadable', reason='format'))  # an older reader's shape
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                continue
            task = json.loads(row[0])
            run = task['runs'][-1] if task.get('runs') else None
            after = run.get('after_close') if isinstance(run, dict) else None
            if not isinstance(after, dict) or run.get('id') != run_id or not after.get('checking'):
                continue
            after.pop('checking')
            lost_runs.apply(self, task, run, result, lost_run_text)
            self.save(task)

    def tools_for(self, task):
        from project_lessons import TOOL
        from task_guide import TOOL as GUIDE_TOOL, offered as guide_offered
        from project_reading import TOOLS as READ_TOOLS
        tools = READ_TOOLS if task.get('read_only') else self.collaboration.tools_for(task)
        if task.get('full_access'):
            tools = ()  # Claude Code's own tools do the work; only the guide and lesson tools come from Dervo
        from manual_review import enabled as manual_review
        if manual_review(task):
            tools = tuple(tool for tool in tools if tool != 'dervo_run_tests')
        # Codex fixes dynamic tools at thread/start. A resumed legacy session can't
        # acquire new tools, even though the current host knows their contract.
        guide_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                         or task.get('guide_session_id') == task['session_id'])
        if guide_offered(task) and guide_session:
            tools += (GUIDE_TOOL,)
        import task_questions
        question_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                            or task.get('question_session_id') == task['session_id'])
        if question_session and task_questions.offered(task):
            tools += (task_questions.TOOL,)
        import task_reminders
        # Beta.16.1. A Codex session started before reminders existed can't take a new tool, like every tool above.
        reminder_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                            or task.get('reminder_session_id') == task['session_id'])
        if reminder_session and task_reminders.offered(task):
            tools += (task_reminders.TOOL,)
        import project_memory
        memory_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                          or task.get('memory_session_id') == task['session_id'])
        if memory_session and project_memory.offered(self, task):
            tools += project_memory.TOOLS
        if memory_session and task.get('origin') == 'conversation':
            import chat_links
            tools += (chat_links.TOOL,)  # other chats' state, same session rule as memory for Codex
            import thread_fanout
            thread_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                              or task.get('thread_proposal_session_id') == task['session_id'])
            if thread_session and thread_fanout.offered(self, task):
                tools += (thread_fanout.TOOL,)
            import thread_notes
            # Beta.17. A Codex session started before notes existed can't take a new tool, like every tool above.
            note_session = (task['binding']['provider'] != 'codex' or not task.get('session_id')
                            or task.get('note_session_id') == task['session_id'])
            if note_session and thread_notes.offered(task):
                tools += (thread_notes.TOOL,)
        import project_skills
        if memory_session and not task.get('read_only') and project_skills.offered(self, task):
            tools += (project_skills.TOOL,)  # a proposal only; the same session rule as memory for Codex
        try:
            binding = self.store.binding(**task['context'])
        except Rejected:
            return tools  # Existing action-time checks still deny unavailable scope for every tool.
        if task.get('read_only'):
            return tools
        return tools + ((TOOL,) if self.knowledge.lessons.offered(task,binding) else ())

    def save(self, task):
        with self.store.db:
            from task_activity import save_task
            save_task(self.store.db, task, json.dumps(task))
            import conversation_activity
            conversation_activity.record(self.store.db, task)
        self.learn(task)
        self.track_pending(task)
        return task

    def learn(self, task):
        """Project memory keeps what Dervo itself saw once a result is completed. A memory failure never costs the task
        its save: the note is decoration on a record that has already been written."""
        completion = task.get('completion') if isinstance(task.get('completion'), dict) else None
        if not completion or task.get('memory_learned') == completion.get('at'):
            return
        try:
            import project_memory
            project_memory.learn(self.store.db, task)
        except Exception as error:  # noqa: BLE001 - reported, never raised into the save path
            print(f'Project memory could not note this result: {type(error).__name__}', file=sys.stderr)
        task['memory_learned'] = completion.get('at')
        with self.store.db:
            from task_activity import save_task
            save_task(self.store.db, task, json.dumps(task))

    def track_pending(self, task, rowid=None):
        """Keep only IDs and insertion order for settlement. Authority still comes from fresh durable records."""
        from collaboration import REVIEW_OPEN
        review = (task.get('collaboration') or {}).get('review') or {}
        eligible = {
            'review': review.get('enabled') is True and review.get('phase') in REVIEW_OPEN,
            'acceptance': (task.get('acceptance') or {}).get('required') is False and task['state'] == 'ready_for_review',
        }
        for kind, needed in eligible.items():
            pending = self.pending[kind]
            if not needed:
                pending.pop(task['id'], None)
            elif task['id'] not in pending:
                if rowid is None:
                    rowid = self.store.db.execute('SELECT rowid FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0]
                pending[task['id']] = rowid

    def pending_tasks(self, kind):
        pending = self.pending[kind]
        for task_id in sorted(pending, key=pending.get):
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row:
                yield json.loads(row[0])

    def steer(self, thread_id, text, client_id, *, expected_task_id=None, expected_run_id=None, question_id=None):
        """Add a message to the run already working in this conversation, as Claude Code and Codex allow. Returns the task
        id, or raises Rejected when no run can take it (the caller queues it instead)."""
        for task_id, turn in self.turns.items():
            if expected_task_id is not None and task_id != expected_task_id:
                continue
            if turn.closed or not hasattr(turn, 'steer'):
                continue
            self.save_waiting_text(task_id)  # the answer so far goes above your message
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                continue
            task = json.loads(row[0])
            if task['context']['thread_id'] != thread_id or task.get('origin') != 'conversation' or task.get('state') != 'working':
                continue
            if expected_run_id is not None and (task.get('runs') or [{}])[-1].get('id') != expected_run_id:
                continue
            old = next((m for m in task['messages'] if m.get('role') == 'user' and m.get('client_id') == client_id), None)
            if old:
                if old['text'] != text:
                    raise Rejected('This reply request ID already belongs to different text.')
                return task_id  # the composer retried the same message
            pending = [m for m in task['messages'] if m.get('steer') in ('sent', 'question_sending', 'question_unknown')
                       and m.get('run_id') == task['runs'][-1]['id']]
            if question_id:
                import task_questions
                if pending or getattr(turn, 'stopping', False):
                    raise task_questions.NotSent('The source task is still accepting another reply.')
                message = dict(role='user', text=text, client_id=client_id, run_id=expected_run_id,
                               question_id=question_id, created_at=int(time.time()), steer='question_sending')
                task['messages'].append(message)
                self.save(task)  # saved before transport, including a send whose result is later unknown
                try:
                    turn.steer(text)
                except Exception:
                    current = self.read(task_id, task['context'], rebound=True)
                    for saved in current['messages']:
                        if saved.get('client_id') == client_id:
                            saved['steer'] = 'question_unknown'
                    self.save(current)
                    raise
                current = self.read(task_id, task['context'], rebound=True)
                for saved in current['messages']:
                    if saved.get('client_id') == client_id and saved.get('steer') == 'question_sending':
                        saved['steer'] = 'sent'
                self.save(current)
                return task_id
            if any(m.get('question_id') for m in pending):
                raise Rejected('A question answer is being delivered. This message waits for the next run.')
            turn.steer(text)
            task['messages'].append(dict(role='user', text=text, client_id=client_id, run_id=task['runs'][-1]['id'],
                                         created_at=int(time.time()), steer='sent'))
            self.save(task)
            return task_id
        if question_id:
            from task_questions import NotSent
            raise NotSent('The source run can no longer take this answer.')
        raise Rejected('No run in this conversation can take a message now.')

    def save_waiting_text(self, task_id, force=True):
        """Save a run's streamed text that is still waiting in memory (start's save_text). ``force`` False saves only once
        it has waited TEXT_SAVE_SECONDS. Raises as a save does."""
        save = self.text_waiting.get(task_id)
        if save is not None:
            save(force)

    def want_warm(self, thread_id):
        """You started typing in this thread: start its next Claude Code ahead of time, once it's free (coding_warm).
        Only the thread you're typing in is held (warm_runs.MAX_WARM), so idle threads cost nothing (code review 8 Oct,
        H1: one held process measured 214 MB before its MCP servers)."""
        if thread_id in self.warm_starting or thread_id in self.warm.held:
            return {'warm': 'held'}
        self.warm_wanted[thread_id] = time.monotonic() + WARM_WAIT_SECONDS
        return {'warm': 'wanted'}

    def take_warm(self, thread_id, binding, trusted, context, emit):
        """The Claude Code started ahead of time for this thread, handed this task, or None to launch as before. Its
        events from before any task (the plan, its session, its connectors) are replayed into this task's record."""
        self.warm_wanted.pop(thread_id, None)
        self.finish_warm_start(thread_id, taking=True)
        if thread_id in self.warm_starting:
            # Sends queue while a start is going (warm_start_going), so this is only a caller that didn't ask.
            raise Rejected('Claude Code is still starting for this thread. Send again in a moment.')
        held = self.warm.take(thread_id, warm_runs.signature(
            binding, tools=trusted.tools, full_access=trusted.full_access, computer_access=trusted.computer_access,
            connectors_allowed=trusted.connectors_allowed, commands=trusted.commands,
            connectors_only=trusted.connectors_only, limits=trusted.limits))
        if held is None:
            return None
        if any(event[0] == 'continuity' for event in held.events) or held.turn.adapter.fork_from != binding.get('fork_from'):
            # Its launch couldn't continue the session it was planned to (the file wasn't there yet). A launch now might.
            held.turn.close()
            return None
        try:
            held.turn.adopt(binding, trusted, context, emit)
        except Exception as exc:  # noqa: BLE001 - any doubt and this send launches as before
            held.turn.close()
            if getattr(exc, 'slow_account', False):
                raise  # a cold launch would wait for the same account check again, past the bridge's 10 s (review, 1)
            return None
        for kind, data in held.replay():
            emit(kind, dict(data, warm=True) if kind == 'session' else data)
        return held.turn

    def settle_warm(self):
        """Start the next Claude Code for each thread you're typing in, once the thread is free, hand finished starts to
        the pool and keep the held ones going. A start that fails is dropped: the next send launches as before."""
        now = time.monotonic()
        for thread_id, until in list(self.warm_wanted.items()):
            if now > until:
                self.warm_wanted.pop(thread_id)
            elif not self.busy_thread(thread_id):
                self.warm_wanted.pop(thread_id)
                try:
                    self.prewarm(thread_id)
                except Exception:  # noqa: BLE001 - only speed is lost
                    pass
        for thread_id in list(self.warm_starting):
            future, held = self.warm_starting[thread_id]
            if not future.done() and now - held.born > WARM_START_LIMIT:
                self.drop_warm_start(thread_id)  # stuck: the message waiting on it launches as before
                continue
            self.finish_warm_start(thread_id)
        self.warm.tick()

    def warm_start_going(self, thread_id):
        """Whether this thread's next Claude Code is being started ahead of time right now. A send then waits in the
        queue like one sent during a run, and the queue starts it once the start is done."""
        return thread_id in self.warm_starting

    def drop_warm_start(self, thread_id):
        pending = self.warm_starting.pop(thread_id, None)
        if pending is None:
            return
        future, _ = pending
        if not future.cancel():
            future.add_done_callback(lambda done: not done.cancelled() and done.exception() is None and done.result().close())

    def finish_warm_start(self, thread_id, taking=False):
        """Hand a finished start to the pool, never waiting for one still going. A start that failed, or that finished
        after the thread got busy without it, is dropped. ``taking``: a send is about to use it."""
        pending = self.warm_starting.get(thread_id)
        if pending is None:
            return
        future, held = pending
        if not future.done():
            return
        self.warm_starting.pop(thread_id, None)
        try:
            held.turn = future.result()
        except Exception as exc:  # noqa: BLE001 - only speed is lost
            if taking and getattr(exc, 'slow_account', False):
                raise  # a send is on it: launching now would wait for the same account check again
            return
        if not taking and self.busy_thread(thread_id):
            held.turn.close()  # a send launched without it while it was starting
            return
        self.warm.offer(thread_id, held)

    def close_warm(self, keep_starter=False):
        starting, self.warm_starting = self.warm_starting, {}
        self.warm_wanted.clear()
        if self.warm_starter is not None and not keep_starter:
            self.warm_starter.shutdown(wait=False, cancel_futures=True)  # one still starting closes when it's done
            self.warm_starter = None  # a shut-down pool takes no more starts; prewarm makes a new one
        for future, _ in starting.values():
            future.cancel()
            if not future.done():
                future.add_done_callback(lambda done: not done.cancelled() and done.exception() is None and done.result().close())
                continue
            try:
                if future.done() and not future.cancelled() and future.exception() is None:
                    future.result().close()
            except Exception:  # noqa: BLE001 - a stubborn process can't stop the host closing
                pass
        self.warm.close()

    def prewarm(self, thread_id):
        """Launch what this thread's next message would launch, without sending anything (warm_runs). The next task is
        predicted from the last one; take_warm only uses it if the real launch turns out exactly the same."""
        from claude_coding import ClaudeCodingTurn
        from codex_coding import TrustedTaskBinding
        from connections_ui import owned_profile
        from release_profiles import require_provider
        import run_limits
        import session_continuity
        import uuid
        last = self.last_conversation_task(thread_id)
        if last is None:
            return
        if last.get('read_only') or last['binding'].get('provider') != 'claude':
            return
        self.host.require_beta_access()
        ctx = last['context']
        binding = self.store.binding(**ctx)
        if binding['provider'] != 'claude' or binding.get('method') == 'api_key' or self.host.keys.key_for(binding) is not None:
            return
        require_provider('claude')
        owned_profile(self.store, self.host.root, binding['profile_id'], context=binding.get('context'))
        task = dict(last, id='warm-' + str(uuid.uuid4()), session_id=None, state='working', runs=[])
        self.folders.check(binding, task)
        full_access = task.get('full_access') is True
        connectors_allowed = None
        if full_access:
            if self.conversation.full_access_problem(task, binding):
                return
            connectors_allowed = self.conversation.allowed_connectors(binding)
        connectors_only = not full_access and self.conversation.home_connectors(task, binding)
        if connectors_only:
            connectors_allowed = self.conversation.allowed_connectors(binding)
        binding['session_id'] = None
        continuity = session_continuity.plan(self, task, binding)
        if continuity['state'] == 'continues':
            binding['fork_from'] = continuity['session_id']
            if continuity.get('parent_session_id'):
                binding['fork_parent'] = continuity['parent_session_id']
        effort = self.conversation.owner_effort(binding)
        if effort is not None:
            binding['effort'] = effort
        trusted = TrustedTaskBinding(task['id'], binding, lambda _task, request: request is None, tools=self.tools_for(task),
                                     full_access=full_access, connectors_allowed=connectors_allowed,
                                     computer_access=task.get('computer_access') is True, commands=task.get('commands') is not False,
                                     connectors_only=connectors_only, limits=run_limits.read(self.store.db, ctx['project_id']))
        held = warm_runs.Held(warm_runs.signature(
            binding, tools=trusted.tools, full_access=full_access, computer_access=trusted.computer_access,
            connectors_allowed=connectors_allowed, commands=trusted.commands, connectors_only=connectors_only, limits=trusted.limits))
        def refuse():
            raise Rejected('Nothing is sent to a Claude Code started ahead of time')
        if self.warm_starter is None:
            import concurrent.futures
            self.warm_starter = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-warm')
        self.warm.discard(thread_id)
        self.warm_starting[thread_id] = (self.warm_starter.submit(
            ClaudeCodingTurn, binding, task=trusted, context=refuse, emit=held.emit, hold=True), held)

    def last_conversation_task(self, thread_id):
        """This thread's newest conversation task, read through the activity index (only this thread's records are
        parsed), or by a scan on a store that has never indexed one."""
        db = self.store.db
        indexed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_task_state'").fetchone()
        rows = (db.execute('SELECT c.document FROM coding_tasks c JOIN conversation_task_state s ON s.task_id=c.id '
                           'WHERE s.thread_id=? ORDER BY c.rowid DESC', (thread_id,)) if indexed else
                db.execute("SELECT document FROM coding_tasks WHERE json_extract(document, '$.context.thread_id')=? "
                           'ORDER BY rowid DESC', (thread_id,)))
        for (document,) in rows:
            task = json.loads(document)
            if task.get('origin') == 'conversation':
                return task
        return None

    def busy_thread(self, thread_id):
        """Whether a coding task (an owner or a collaboration helper) is running in this conversation right now. A run
        whose changes are still being recorded on a thread (record_prepared) counts: the next one waits for its result."""
        if thread_id in self.recording.values():
            return True
        for task_id, turn in self.turns.items():
            if turn.closed:
                continue
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row and json.loads(row[0])['context']['thread_id'] == thread_id:
                return True
        return False

    def _origin(self, task_id):
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        return json.loads(row[0]).get('origin') if row else None

    def open_owner_runs(self):
        """Owner coding runs open across the host. Collaboration helpers and reviewers count against their own limit."""
        count = 0
        for task_id, turn in self.turns.items():
            if turn.closed:
                continue
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row and json.loads(row[0]).get('origin') != 'collaboration':
                count += 1
        return count

    def _task_document(self, task_id):
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _full_access(self, task_id):
        return self._task_document(task_id).get('full_access') is True

    def _cwd(self, task_id):
        return (self._task_document(task_id).get('binding') or {}).get('cwd')

    def owner_slot_problem(self, thread_id):
        """Why a new owner run can't start now, or None. One run per conversation, MAX_PARALLEL_OWNERS across the host."""
        if self.busy_thread(thread_id):
            return 'This conversation already has a coding task running. Wait for it to finish or stop it first.'
        if self.open_owner_runs() >= MAX_PARALLEL_OWNERS:
            return self.active_run_text()
        return None

    def parallel_holder(self, task_id, path):
        """The conversation of another running owner task that already changed this file in its current run, or None.

        Parallel tasks share one project folder, so a file one running task has written stays theirs until that run ends.
        """
        import posixpath
        if not isinstance(path, str):
            return None
        if self._origin(task_id) == 'collaboration':
            return None  # helpers and reviewers are held by their owner's packet rules, not by other owners
        want = posixpath.normpath(path.replace('\\', '/'))
        for other_id, turn in self.turns.items():
            if other_id == task_id or turn.closed:
                continue
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (other_id,)).fetchone()
            if not row:
                continue
            other = json.loads(row[0])
            if other.get('origin') == 'collaboration' or not other.get('runs'):
                continue
            if (other.get('binding') or {}).get('cwd') != self._cwd(task_id):
                continue  # beta.18: a run in another thread's own folder can't overwrite this one's files

            run_id = other['runs'][-1].get('id')
            for token in other.get('tokens') or []:
                if (token.get('run_id') == run_id and token.get('tool') in ('dervo_write_file', 'dervo_edit_file', 'dervo_create_file')
                        and token.get('state') != 'cancelled' and token.get('success') is not False and isinstance(token.get('path'), str)
                        and posixpath.normpath(token['path'].replace('\\', '/')) == want):
                    context = other.get('context') or {}
                    named = self.store.db.execute('''SELECT c.name FROM threads t JOIN local_channels c ON c.id=t.id
                        WHERE t.id=? AND (t.visibility='shared' OR t.owner=?)''', (context.get('thread_id'), context.get('actor'))).fetchone()
                    return f'#{named[0]}' if named else 'another conversation'
        return None

    def active_run(self, prefer=None):
        """The one coding run open across the host, as ids plus the conversation's name, or None.

        The name comes from the same visibility rule as the conversations list: a private
        conversation belonging to someone else is not named, only counted.
        """
        runs = []
        for task_id, turn in self.turns.items():
            if turn.closed:
                continue
            row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
            if row:
                runs.append((task_id, json.loads(row[0])))
        runs.sort(key=lambda item: (item[1].get('context') or {}).get('thread_id') != prefer)
        for task_id, task in runs:
            context = task.get('context') or {}
            named = self.store.db.execute('''SELECT c.name FROM threads t JOIN local_channels c ON c.id=t.id
                WHERE t.id=? AND t.project_id=? AND (t.visibility='shared' OR t.owner=?)''',
                                          (context.get('thread_id'), context.get('project_id'), context.get('actor'))).fetchone()
            return dict(task_id=task_id, thread_id=context.get('thread_id'), project_id=context.get('project_id'),
                        origin=task.get('origin'), agent=task.get('agent'), name=named[0] if named else None)
        return None

    def thread_name(self, task):
        """The conversation's name for a commit subject, or None. A missing or unreadable name keeps the old subject."""
        import sqlite3
        import task_push
        try:
            return task_push.thread_name_for(self.store.db, task)
        except sqlite3.Error:
            return None

    def shared_files(self, task):
        """{path: thread name} for files another thread of this project changed and hasn't committed, from Dervo's own
        records (night 2, finding 2): the commit names that thread when a shared file holds its lines too. Never raises."""
        import sqlite3
        import session_continuity
        import task_push
        context = task.get('context') or {}
        found = {}
        try:
            if not self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
                return found
            for (document,) in self.store.db.execute('SELECT document FROM coding_tasks ORDER BY rowid DESC').fetchall():
                other = json.loads(document)
                if (other.get('origin') != 'conversation' or (other.get('context') or {}).get('project_id') != context.get('project_id')
                        or (other.get('context') or {}).get('thread_id') == context.get('thread_id')):
                    continue
                commit = other.get('commit') if isinstance(other.get('commit'), dict) else {}
                if commit.get('sha') and not commit.get('reverted'):
                    continue
                name = task_push.thread_name_for(self.store.db, other) or 'another thread'
                added = {}
                try:
                    # The lines that thread's own change put in each file, so the commit can tell when they sit inside
                    # this task's approved bytes (a write during this run): task_git.theirs_inside.
                    import task_git
                    evidence = self.evidence.read(other['id'], **other['context'])
                    firsts = task_git.preimages(other, evidence)
                    last = (evidence.get('attempts') or [{}])[-1].get('after') or {}
                    for path, after in last.items():
                        before = firsts.get(path)
                        # Inline copies decode here; a file over 256 KiB is kept beside the database (beta.15).
                        read = lambda snap: base64.b64decode(snap['bytes_b64']) if snap.get('bytes_b64') else self.evidence.snapshot_bytes(snap)
                        if isinstance(after, dict) and (after.get('bytes_b64') or after.get('stored')):
                            added[path] = sorted(task_git._lines(read(after))
                                                 - (task_git._lines(read(before)) if isinstance(before, dict) and (before.get('bytes_b64') or before.get('stored')) else set()))
                except Exception:  # noqa: BLE001 - the name alone still helps
                    added = {}
                for path in session_continuity.files_changed(other):
                    if path not in found:
                        found[path] = dict(name=name, added=added.get(path) or []) if added.get(path) else name
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return found
        return found

    def commit_record(self, task, binding, *, how, carried=None):
        """Try the commit and describe what happened. Never raises: the approval is already recorded. ``carried`` is the
        checked plan of a task that carried on from a handoff snapshot: its files are committed with this task's."""
        import task_git
        try:
            # A file over 256 KiB keeps its versions beside the database; the commit reads the two it needs back inline.
            evidence = self.evidence.for_commit(self.evidence.read(task['id'], **task['context']), task)
            return task_git.commit(task, evidence, binding['cwd'], how=how, thread_name=self.thread_name(task),
                                   others=CodingTasks.shared_files(self, task), carried=carried)
        except Rejected as exc:
            reason = str(exc)
            if reason == task_git.NOT_A_REPO:
                return {'sha': None, 'skipped': 'Not a git repository. Nothing was committed.', 'at': int(time.time()), 'how': how}
            return {'sha': None, 'error': reason, 'at': int(time.time()), 'how': how}
        finally:
            # A commit moves the thread's branch (ahead) or the base (other threads' behind): the branch card reads git again.
            if getattr(self, 'folders', None) is not None:
                self.folders.forget()

    def auto_commit(self, task):
        """16.2 (Bryce, 27 Sep): with "Commit results automatically" on for the project, a full-access result that Dervo
        recorded whole is approved and committed as if you'd pressed Approve and commit, and says it was automatic
        (``commit.how`` 'automatic'). It needs the setting on both when the run started (``task['auto_commit']``) and now,
        a clean ready result with nothing unrecorded, no error and no carried handoff plan, which still asks you. Anything
        else leaves the result waiting for you as before. Undo reverts the commit like any other. Answers with the commit
        record, or None when the result waits for you.

        17.1: with Independent review on, the commit waits for the review. It happens when the review passes
        (Collaboration.review_settle calls this again); any other review outcome leaves the result waiting for you.

        Beta.18: in a thread's own folder every such result is committed to the thread's branch, whatever the setting
        says (thread_folders). The base doesn't change, so nothing is accepted yet: Merge is that, and it's your click."""
        own = self.folders.committing(task)
        if (not own and (task.get('auto_commit') is not True or task.get('full_access') is not True)) or task.get('origin') != 'conversation' \
                or task.get('state') != 'ready_for_review' or task.get('error') or task.get('unrecorded') or task.get('review') is not None \
                or task.get('carried'):
            return None
        review = (task.get('collaboration') or {}).get('review') or {}
        if review.get('phase') not in (None, 'passed', 'skipped'):
            return None
        # The project's checks (project_checks), whichever path asks for the commit (settle_checks, or a passed review):
        # only a result whose checks passed with nothing else changing the folder, or that had none, commits itself.
        checks = (task.get('runs') or [{}])[-1].get('checks') if isinstance((task.get('runs') or [{}])[-1], dict) else None
        if checks and (checks.get('state') not in ('passed', 'skipped') or checks.get('overlapped')):
            return None
        ctx = task['context']
        try:
            binding = self.store.binding(**ctx)
            if own:
                if binding['cwd'] != (task.get('binding') or {}).get('cwd'):
                    return None  # the thread's folder isn't the one this run recorded in
            elif not self.conversation.full_access(binding)['auto_commit']:
                return None  # turned off, or full access went, since the run started
            attempt = self.evidence.read(task['id'], **ctx)['attempts'][-1]
            self.evidence.review(task['id'], attempt_number=attempt['number'], decision='approved', **ctx)
        except (Rejected, KeyError, IndexError) as exc:
            print(f'Task {task.get("id")} waits for you: it couldn’t be approved automatically ({type(exc).__name__}: {exc})', file=sys.stderr)
            return None
        task.update(review='approved', state='idle')
        task['commit'] = self.commit_record(task, binding, how='automatic')
        self.items_done(task)
        native_evidence.settle_carried(self, task)
        return task['commit']

    def items_done(self, task):
        """The bugs and ideas this thread was working on are done once you approved its result and Dervo committed it
        (project_ideas.work_committed, 19 Sep). Your approval is the trigger and the commit is the proof: a task with no
        commit, or a reverted one, marks nothing, and nothing here reads what the agent said. Never fails the approval.
        Answers with the items marked."""
        commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
        context = task.get('context') or {}
        if not commit.get('sha') or commit.get('reverted'):
            return []
        try:
            ideas = self.host.ideas() if callable(getattr(self.host, 'ideas', None)) else None
            if ideas is None:
                return []
            return ideas.work_committed(context.get('project_id'), context.get('thread_id'), commit['sha'], task['id'],
                                        context.get('actor') or 'local-user')
        except Exception as exc:  # noqa: BLE001 - the approval stands; the mark is a convenience
            print(f'Bugs and ideas could not be marked done for task {task.get("id")}: {type(exc).__name__}: {exc}', file=sys.stderr)
            return []

    def items_reopened(self, task):
        """Undo this task reverted its commit, so the items that commit marked done are open again (project_ideas
        .work_reverted); the revert commit is in git, the old one is in history, and no build stamps undone work. Never
        fails the undo. Answers with the items reopened."""
        commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
        context = task.get('context') or {}
        if not commit.get('sha') or not commit.get('reverted'):
            return []
        try:
            ideas = self.host.ideas() if callable(getattr(self.host, 'ideas', None)) else None
            if ideas is None:
                return []
            return ideas.work_reverted(context.get('project_id'), context.get('thread_id'), commit['sha'], task['id'],
                                       context.get('actor') or 'local-user')
        except Exception as exc:  # noqa: BLE001 - the undo stands; the reopen is a convenience
            print(f'Bugs and ideas could not be reopened for task {task.get("id")}: {type(exc).__name__}: {exc}', file=sys.stderr)
            return []

    def active_run_text(self):
        active = self.active_run()
        if self.open_owner_runs() >= MAX_PARALLEL_OWNERS:
            return f'{MAX_PARALLEL_OWNERS} coding tasks are already running. Wait for one to finish or stop one first.'
        where = f' in #{active["name"]}' if active and active.get('name') else ' in another conversation' if active else ''
        return f'Another coding request is active{where}. Wait for it to finish or stop it first.'

    def save_audit(self, task_id, record):
        # Validated args, a 256 KiB snapshot and the bounded wire response fit here. A bigger file's snapshot is kept
        # beside the database and the audit holds only its sha256 and size.
        text = json.dumps(record, ensure_ascii=False)
        if len(text.encode()) > 1048576:
            raise Rejected('Tool audit exceeds host record budget')
        with self.store.db:
            self.store.db.execute('INSERT INTO coding_tool_audit VALUES (?,?,?) ON CONFLICT(task_id, token) DO UPDATE SET document=excluded.document',
                                  (task_id, record['token'], text))

    def protect(self, cwd, files, folders=()):
        from pathlib import Path
        if type(files) is not list or any(type(path) is not str for path in files):
            raise Rejected('Task files must be explicit relative strings')
        if type(folders) not in (list, tuple) or any(type(path) is not str for path in folders):
            raise Rejected('Task folders must be explicit relative strings')
        roots = [self.host.root.resolve()] + [Path(row[0]).resolve() for row in self.store.db.execute('SELECT home FROM profiles')]
        project = Path(cwd).resolve()
        for candidate in [project, *[(project / path).resolve() for path in list(files) + list(folders)]]:
            if any(candidate.is_relative_to(root) for root in roots):
                raise Rejected('Host Store and provider profile files are never coding task targets')
        # Containment is checked both ways for a recursive folder grant: a folder that holds the Store or a profile home
        # would let listing, search and admitted reads walk into them. An explicit file grant beside them stays allowed.
        for folder in folders:
            candidate = (project / folder).resolve()
            if any(root.is_relative_to(candidate) for root in roots):
                raise Rejected(f'{folder} contains the host Store or a provider profile and cannot be a granted folder')

    def require_grant(self, task):
        """A folder tool runs only while the project's coding permission still names this task's folders. A revoked or
        narrowed grant refuses the call with the reason; the task's own folder list is never widened or rewritten."""
        if task.get('read_only'):
            from project_reading import ProjectReader
            ProjectReader(self, task).read(task['id'], **task['context'])
            return
        evidence = self.evidence.read(task['id'], **task['context'])
        timeout = evidence['check_timeout_seconds']
        if check_profiles.check_timeout(task.get('check_timeout_seconds', 5)) != timeout:
            raise Rejected('The task check time differs from its approved evidence. Inspect the task before continuing.')
        authority = task
        if task.get('origin') == 'collaboration':
            root_id = (task.get('collaboration') or {}).get('root_task_id')
            authority = self.collaboration.raw_task(root_id) if root_id else None
            if authority is None:
                raise Rejected('The helper check has no recorded owner. Inspect the task before continuing.')
            if authority['context']['project_id'] != task['context']['project_id']:
                raise Rejected('The helper and its owner belong to different projects')
        if authority.get('origin') != 'conversation':
            return
        if authority.get('project_access'):
            problem = self.conversation.grant_problem(authority)
            if problem:
                raise Rejected(problem)
        problem = self.conversation.grant_problem(task)
        if problem:
            raise Rejected(problem)

    def require_grant_for(self, task, path):
        """A read or write reaches a file only through the folder grant when that file is not yet in the task's recorded
        evidence scope (granted, created or admitted). That first admission rechecks the project permission now, so a
        revoked or narrowed grant admits nothing new; files already in scope keep their recorded access."""
        evidence = self.evidence.read(task['id'], **task['context'])
        if task.get('project_access') or task.get('origin') == 'collaboration' or path not in self.evidence._scoped(evidence):
            self.require_grant(task)

    def read(self, task_id, ctx, rebound=False):
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise Rejected('Unknown coding task')
        task = json.loads(row[0])
        if 'error' in task:
            task['error'] = task_error(task['error'])
        if task.get('id') != task_id or task.get('context') != ctx:
            raise Rejected('Task identity changed or access is outside task scope')
        binding = self.store.binding(**ctx)
        self.protect(binding['cwd'], task['files'], ['.'] if task.get('project_access') else task.get('folders', []))
        evidence = self.evidence.read(task_id, **ctx, **({'rebound': True} if rebound else {}))
        if bool(task.get('project_access')) != bool(evidence.get('project_access')) or (task.get('project_access') and not same_root(evidence.get('root_identity'), task.get('root_identity'))):
            raise Rejected('Project access differs from this task’s recorded scope')
        if task.get('read_only') is not evidence.get('read_only'):
            raise Rejected('Task reading mode differs from its recorded scope')
        self.invalidate_restored_result(task, evidence=evidence)
        committed = task['state'] == 'idle' and bool((task.get('commit') or {}).get('sha'))
        if task.get('review') and not evidence['review'] and not committed:
            # A result Dervo committed on your approval stays decided: what changes its files after that (Dervo's own
            # Undo or Merge among them) is history, never a reason to ask you to review it again (polish review, 9 Oct).
            task['review'] = None
            if task['state'] == 'idle':
                task.update(state='ready_for_review', error='Files changed after human review. The earlier approval is no longer current.')
            self.save(task)
        if task['state'] == 'completed' and hasattr(self, 'acceptance'):
            task = self.acceptance.check_current(task, evidence)
        import thread_fanout
        thread_fanout.annotate(self, task)
        import task_questions
        task_questions.annotate(self, task)
        import thread_notes
        thread_notes.annotate(self, task)
        return task

    def start(self, task):
        self.host.require_beta_access()
        from codex_coding import CodexCodingTurn, TrustedTaskBinding
        from connections_ui import owned_profile
        import uuid
        ctx = task['context']
        binding = self.store.binding(**ctx)
        from release_profiles import require_provider
        try:
            require_provider(binding['provider'])
        except (ValueError, OSError) as exc:
            raise Rejected('The release profile does not allow this provider') from exc
        if binding['provider'] == 'claude':
            from claude_coding import ClaudeCodingTurn as CodingTurn
        elif binding['provider'] == 'codex':
            CodingTurn = CodexCodingTurn
        else:
            raise Rejected('This provider adapter is not available in this build')
        owned_profile(self.store, self.host.root, binding['profile_id'], context=binding.get('context'))  # its project's side only
        # Beta.18: a thread with its own folder runs only in the folder Dervo made, checked now, before every run.
        self.folders.check(binding, task)
        if task.get('read_only'):
            self.require_grant(task)
        if task.get('origin') == 'collaboration':
            # A helper runs beside its owner, within the project policy's parallel limit (never above three).
            limit = min(task['collaboration']['limits']['max_parallel'], 3)
            helpers = sum(1 for other_id, turn in self.turns.items() if not turn.closed and other_id != task['id']
                          and self._origin(other_id) == 'collaboration')
            if helpers >= limit:
                raise Rejected(f'Parallel helper limit of {limit} reached')
        else:
            problem = self.owner_slot_problem(task['context']['thread_id'])
            if problem:
                raise Rejected(problem)
        full_access = task.get('full_access') is True
        baseline = connectors_allowed = None
        if full_access:
            problem = self.conversation.full_access_problem(task, binding)
            if problem:
                raise Rejected(problem)
            import native_evidence
            baseline = native_evidence.baseline(binding['cwd'])
            connectors_allowed = self.conversation.allowed_connectors(binding)  # None: every connector
        connectors_only = not full_access and self.conversation.home_connectors(task, binding)
        if connectors_only:
            connectors_allowed = self.conversation.allowed_connectors(binding)  # beta.15: a Home job's connectors, no shell
        # Several chats can work in one folder at once. Each run that overlaps another is marked, so a full-access run's
        # result lists only the files its own edits touched and says which other changes it saw.
        sharing = [other_id for other_id, turn in self.turns.items()
                   if not turn.closed and other_id != task['id'] and self._cwd(other_id) == binding['cwd']]
        if sharing:
            self.overlapped.update(sharing + [task['id']])
        self.note_check_overlap(binding['cwd'])
        binding['session_id'] = task['session_id']
        if task['session_id'] is None and task.get('origin') == 'conversation':
            # A new message: continue this conversation's previous Claude Code session when nothing about the run changed.
            import session_continuity
            task['continuity'] = session_continuity.plan(self, task, binding)
            if task['continuity']['state'] == 'continues':
                binding['fork_from'] = task['continuity']['session_id']
                if task['continuity'].get('parent_session_id'):
                    binding['fork_parent'] = task['continuity']['parent_session_id']
        effort = task.get('effort')  # an independent reviewer's (or helper's) requested effort, carried to its own provider exactly
        if effort is None and task.get('origin') != 'collaboration':
            # The owner's own configured effort for exactly this provider and model: the thread's, else the project's, else
            # the account's personal default (16.4). None keeps the legacy shape (no key sent).
            effort = self.conversation.owner_effort(binding)
        if effort is not None:
            binding['effort'] = effort
        advertised_tools = self.tools_for(task)
        starting_session = task['session_id'] is None
        task.update(state='working', error=None)
        run = dict(id=str(uuid.uuid4()), state='checking', session_id=task['session_id'],
                   prior_attempts=len(self.evidence.read(task['id'], **ctx)['attempts']),
                   # When each step of starting happened (wall clock), so "Claude Code is starting" can be measured:
                   # requested, launched, connectors_<state>, sent, first_output and ended. timing.py reads them.
                   timing={'requested': round(time.time(), 3)})
        if full_access:
            run['baseline'] = baseline  # git HEAD and the fingerprints of files already changed before the agent starts
        task['runs'].append(run)
        self.save(task)
        import usage_events
        usage_events.task_started(self.host, run['id'], binding)  # provider, model family and account kind only
        def authorize(trusted, request):
            self.host.require_run_access()  # the send was checked; a trial ending now lets this turn finish
            current = self.read(task['id'], ctx)
            evidence = self.evidence.read(task['id'], **ctx)
            now = self.store.binding(**ctx)
            now['session_id'] = binding['session_id']
            for key in ('fork_from', 'fork_parent'):
                if key in binding:
                    now[key] = binding[key]
            if effort is not None:
                now['effort'] = effort
            if current.get('read_only'):
                self.require_grant(current)
            if full_access and self.conversation.full_access_problem(current, now):
                return False  # turned off, or the project changed, mid-run: the turn stops at its next step
            if connectors_only and not self.conversation.full_access(now)['active']:
                return False  # full access turned off mid-run takes a Home job's connectors with it
            return (current['state'] == 'working' and current['contract'] == 'dervo-dynamic-tools-v1'
                    and now == binding and evidence['approval'] == {'scope_version':task['scope_version'], 'actor':ctx['actor']})
        # Streamed text waits here, in the order it came, and is saved at most every TEXT_SAVE_SECONDS. Each delta used to
        # be its own read, parse and save of the whole record: about 30 a second per run, and with five runs that was over
        # a third of the backend's time (beta.16.1, tools/load_thread_scale.py). Any other event saves the waiting text
        # first, and so do a message sent mid-run (steer), the pump when a run goes quiet (_tick_turn), a failure in Dervo
        # (turn_failed) and a quit (close), so the thread reads in the order it happened.
        pending_text = []
        text_saved = [time.monotonic()]
        first_output = [None]  # when the first words of the reply arrived, saved with the text that carried them
        def apply_text(current):
            if first_output[0] is not None and current['runs'][-1]['id'] == run['id']:
                current['runs'][-1].setdefault('timing', {}).setdefault('first_output', first_output[0])
            for text_kind, item_id, text in pending_text:
                place_text(current['messages'], run['id'], item_id, text_kind, text)
            pending_text.clear()
            text_saved[0] = time.monotonic()
        def save_text(force=True):
            if pending_text and (force or time.monotonic() - text_saved[0] >= TEXT_SAVE_SECONDS):
                current = self.read(task['id'], ctx)
                apply_text(current)
                self.save(current)
        self.text_waiting[task['id']] = save_text
        self.checks_open.add(task['id'])  # the first round reads the record, in case an earlier run left a check going
        self.alive.heard(ctx['thread_id'])  # from launch, so a run that hangs before saying anything still goes quiet
        self.alive_runs[task['id']] = ctx['thread_id']
        def emit(kind, data):
            # In memory; the row moves at most every few seconds (thread_states.Alive). Keyed by thread, so a helper's
            # run ending in the same thread never makes the owner's run look silent (second review, 8 Oct).
            if kind != 'terminal':
                self.alive.heard(ctx['thread_id'])
            if kind in ('delta', 'snapshot'):
                if first_output[0] is None:
                    first_output[0] = round(time.time(), 3)
                pending_text.append((kind, data['item_id'], data['text']))
                if time.monotonic() - text_saved[0] < TEXT_SAVE_SECONDS:
                    return
            current = self.read(task['id'], ctx)
            apply_text(current)
            pending_queue = []
            step = {'session': 'launched', 'task_sent': 'sent', 'terminal': 'ended'}.get(kind)
            if kind == 'connectors' and data.get('state') in ('connecting', 'ready', 'slow', 'unknown'):
                step = 'connectors_' + data['state']
            if step and current['runs'][-1]['id'] == run['id']:
                current['runs'][-1].setdefault('timing', {}).setdefault(step, round(time.time(), 3))
                if kind == 'session' and data.get('warm'):
                    current['runs'][-1]['timing']['warm'] = True  # started ahead of time and handed this task (warm_runs)
            if kind == 'task_sent':
                pass  # timing only
            elif kind == 'session':
                sid = data['session_id']
                if current['session_id'] is not None and current['session_id'] != sid:
                    raise Rejected('Task provider session changed')
                current['session_id'] = sid
                current['runs'][-1]['session_id'] = sid
                # The provider CLI's process group leader (run-reliability adds it to the Claude session event; None
                # with a synthetic peer or a provider that doesn't report one). Saved so that if this host dies without
                # closing, the next one can reap the group the dead host left behind (finding 1, reap_orphans below).
                if data.get('pid'):
                    current['runs'][-1]['pid'] = data['pid']
                    # With its start time and executable, so a reused pid is never taken for it (security review M2).
                    current['runs'][-1]['process'] = provider_identity(data['pid'])
                from task_guide import TOOL as GUIDE_TOOL
                if starting_session and GUIDE_TOOL in advertised_tools:
                    current['guide_session_id'] = sid
                import project_memory
                if starting_session and project_memory.REMEMBER_TOOL in advertised_tools:
                    current['memory_session_id'] = sid
                if starting_session and 'dervo_propose_thread' in advertised_tools:
                    current['thread_proposal_session_id'] = sid
                if starting_session and 'dervo_leave_note' in advertised_tools:
                    current['note_session_id'] = sid
                if starting_session and 'dervo_ask_user' in advertised_tools:
                    current['question_session_id'] = sid
                if starting_session and 'dervo_remind_user' in advertised_tools:
                    current['reminder_session_id'] = sid
            elif kind == 'continuity':
                # The previous session couldn't be loaded at launch. Either this message continues from the session
                # before it (parent) and is told the missing exchange, or it starts fresh with the conversation summary.
                current['continuity'] = dict(current.get('continuity') or {}, state=data['state'], reason=data['reason'], fallback=True,
                                             session_id=data.get('session_id') if data['state'] == 'continues' else None,
                                             **({'parent': True} if data.get('parent') else {}))
            elif kind == 'started':
                current['runs'][-1].update(state='running', turn_id=data['turn_id'])
                # The identity saved at launch was read while the launcher could still be a shell about to exec the
                # CLI (a wrapper script, the eval's real_cli). Now that the CLI has spoken, its executable is settled:
                # the restart pass compares this one, so a survivor of a crash is recognised (night 1, finding 2).
                if current['runs'][-1].get('pid'):
                    settled = provider_identity(current['runs'][-1]['pid'])
                    if settled is not None:
                        current['runs'][-1]['process'] = settled
            elif kind in ('delta', 'snapshot'):
                pass  # placed by apply_text above
            elif kind == 'usage':
                # What the provider reported for this run: tokens, and Claude Code's own cost estimate. Kept on the run and
                # in provider_usage for the per-account summary. Nothing here is estimated by Dervo.
                import provider_usage
                usage = provider_usage.normalise(binding['provider'], data)
                if usage is not None:
                    current['runs'][-1]['usage'] = usage
                    provider_usage.record_usage(self.store.db, task['id'], run['id'], binding, usage)
            elif kind in ('steer_read', 'steer_failed'):
                waiting = next((msg for msg in current['messages'] if msg.get('role') == 'user'
                                and msg.get('steer') in ('sent', 'question_sending', 'question_unknown')
                                and msg.get('run_id') == run['id'] and current['runs'][-1]['id'] == run['id']), None)
                if waiting is not None:
                    if waiting.get('question_id'):
                        import task_questions
                        waiting['steer'] = 'question_receipt'
                        task_questions.delivery(self, waiting['question_id'], 'sent' if kind == 'steer_read' else 'unknown')
                    elif kind == 'steer_read':
                        waiting['steer'] = 'read'
                    else:
                        current['messages'].remove(waiting)  # shown in the queue instead, and sent when this run ends
                        pending_queue.append(waiting)
            elif kind == 'rate_limits':
                import provider_usage
                provider_usage.record_rate_limits(self.store.db, binding['profile_id'], data)
            elif kind == 'plan':
                import provider_plans
                provider_plans.record(self.store.db, binding['profile_id'], {'subscriptionType': data.get('plan')})
            elif kind == 'connectors':
                # dd #8: whether the first message waited for connectors, and which ones weren't ready when it went.
                # "connecting" while it waits; then "ready", "slow" (the wait ran out) or "unknown" (no status to read).
                if data.get('state') in ('connecting', 'ready', 'slow', 'unknown'):
                    names = lambda key: [str(name)[:100] for name in (data.get(key) or [])][:64]
                    current['runs'][-1]['connectors'] = {'state': data['state'], 'pending': names('pending'), 'failed': names('failed')}
            elif kind == 'native_tool' and (full_access or data.get('name') == 'WebSearch'):
                # Claude Code's own tool calls, as steps: the tool, a short command or file, when, and whether it errored.
                # Never the tool's output. Web search is recorded in every mode and from both providers (PLAN-native A5).
                token = next((t for t in current['tokens'] if t.get('native') and t['token'] == data['id']), None)
                if data['phase'] == 'started' and data.get('name') in ('Edit', 'Write') and isinstance(data.get('path'), str):
                    written = current['runs'][-1].setdefault('written', [])
                    if data['path'] not in written and len(written) < 2000:
                        written.append(data['path'])  # this run's own edits, for credit when another chat shares the folder
                if data['phase'] == 'started' and token is None:
                    if sum(1 for t in current['tokens'] if t.get('native') and t.get('run_id') == run['id']) < 500:
                        current['tokens'].append(dict(token=data['id'], tool='native:' + data['name'], native=True, state='running',
                                                      run_id=run['id'], path=data.get('path'), command=data.get('summary'), prose=data.get('prose') is True,
                                                      started_at=round(time.time(), 3),
                                                      **({'connector': data['connector']} if isinstance(data.get('connector'), str) else {}),
                                                      # A sub-agent's step: the Agent call it sits under, in the run's subagents (16.2).
                                                      **({'parent': data['parent']} if isinstance(data.get('parent'), str) else {})))
                elif data['phase'] == 'ended' and token is not None and token['state'] == 'running':
                    token.update(state='delivered', success=not data.get('is_error'), ended_at=round(time.time(), 3),
                                 **({'connector_dropped': True} if data.get('connector_dropped') else {}))
                    if not token.get('command') and isinstance(data.get('summary'), str) and data['summary']:
                        token['command'] = data['summary']  # Codex names a search's query only when it finishes
            elif kind == 'subagent' and full_access:
                record_subagent(current['runs'][-1], data)
            elif kind == 'tools_cancelled':
                for token in current['tokens']:
                    if token['token'] in data['tokens'] and token['state'] != 'delivered':
                        if token.get('job_id'):
                            self.evidence.cancel(current['id'], job_id=token['job_id'], **ctx)
                        token['state'] = 'cancelled'
                        token['ended_at'] = round(time.time(), 3)
                self.collaboration.cancel_tokens(current, data['tokens'])  # withdraws the help packets behind cancelled calls
            elif kind == 'terminal':
                current['runs'][-1]['state'] = data['state']
                if full_access:
                    # Beta.18: what the git guard refused to move during the run, read before the turn closes and its
                    # folder goes. [] lets a commit made outside the run move HEAD without ending it (native_evidence).
                    try:
                        tried = self.turns[task['id']].git_tried() if task['id'] in self.turns else None
                    except Exception:  # noqa: BLE001 - no answer keeps the refusal
                        tried = None
                    if isinstance(tried, list):
                        current['runs'][-1]['git_tried'] = tried
                if task['id'] in self.overlapped:
                    current['runs'][-1]['shared_folder'] = True  # another chat worked in this folder during the run
                    self.overlapped.discard(task['id'])
                if data.get('host_stop'):
                    current['runs'][-1]['end_cause'] = 'host_limit'  # Dervo's own limit: known cause, session can continue
                    current['runs'][-1]['limit'] = data.get('limit')  # beta.17.1: idle, hard, stop or output (run_ending)
                elif data.get('not_started') and data['state'] == 'failed':
                    current['runs'][-1]['end_cause'] = 'not_started'  # the task text never reached the agent
                if isinstance(data.get('last_seen'), (int, float)):
                    current['runs'][-1]['last_seen'] = round(data['last_seen'], 3)
                import provider_failure
                if data['state'] == 'failed' and data.get('provider_failure') in provider_failure.KINDS:
                    # The provider's side failed (an API error, overload, outage or limit). Kept on the run so the
                    # conversation can offer your account on the other provider; see conversation_route.
                    current['runs'][-1]['provider_failure'] = data['provider_failure']
                current.update(state='ready_for_review' if data['state'] == 'completed' else
                               'disconnected' if data['state'] == 'uncertain' else 'blocked', error=task_error(data.get('error')))
                for token in current['tokens']:
                    if token.get('native') and token['state'] == 'running':  # full access, or a web search in any mode
                        token.update(state='cancelled', ended_at=round(time.time(), 3))
                for call in current['runs'][-1].get('subagents') or []:
                    if call['state'] in ('called', 'running'):
                        call['state'] = 'unknown'  # the run ended before it said how this sub-agent finished
                # A message sent into the run that the run ended before reading (steer still 'sent'): it goes to the
                # queue and starts next, as a refused Codex steer does, unless you pressed Stop. After your own Stop it
                # waits for your next message instead (session_continuity.since_note, pickup.note_line). Handoff map H5.
                stopping = getattr(self.turns.get(task['id']), 'stopping', False)
                for message in current['messages']:
                    if message.get('question_id') and message.get('run_id') == run['id'] and message.get('steer') in ('sent', 'question_sending', 'question_unknown'):
                        import task_questions
                        message['steer'] = 'question_unknown'
                        task_questions.delivery(self, message['question_id'], 'unknown')
                unread = [msg for msg in current['messages'] if msg.get('role') == 'user' and msg.get('steer') == 'sent' and not msg.get('question_id')]
                if unread and not stopping and data['state'] != 'interrupted':
                    for waiting in unread:
                        current['messages'].remove(waiting)
                        pending_queue.append(dict(waiting, why='unread'))
                    current['runs'][-1]['requeued'] = len(unread)
                if full_access and data['state'] == 'completed':
                    import native_evidence
                    # Beta.18 (step 5): the git part of recording runs on a thread. Waited for briefly; a slow folder
                    # finishes on the tick (settle_recordings) while the task reads as still working, so the host
                    # keeps answering instead of the bridge killing it.
                    prepared = self.record_prepared(current)
                    if prepared is None:
                        current['state'] = 'working'
                        current['runs'][-1]['recording'] = True
                    else:
                        self.finish_recording(current, prepared)
                elif full_access:
                    import native_evidence
                    note = native_evidence.unfinished_note(current, earlier=native_evidence.earlier_left(self, current))
                    if note:
                        current['error'] = ((current.get('error') or '') + ' ' + note).strip()
                elif data['state'] == 'completed':
                    evidence = self.evidence.read(task['id'], **ctx)
                    if len(evidence['attempts']) <= run['prior_attempts']:
                        if current.get('origin') in ('conversation', 'collaboration'):
                            # A conversation request (or a helper's review) the model answered without tools: text only, nothing executed.
                            current.update(state='answered', error=None)
                        else:
                            current.update(state='blocked', error='No file attempt or verification was recorded for this run. Tests were not run.')
                    elif current.get('check_profile') == 'manual-review':
                        from manual_review import finish
                        try:
                            finish(self, current)
                        except Rejected as exc:
                            current.update(state='blocked', error=str(exc))
                    elif not evidence['attempts'][-1]['test']:
                        current.update(state='blocked', error='Tests were not run to completion for the current file attempt. '
                                       + (evidence['attempts'][-1]['blocker'] or 'Host verification evidence is missing.'))
                    else:
                        attempt = evidence['attempts'][-1]
                        if attempt['state'] != 'verified':
                            current.update(state='blocked', error='Host verification did not finish. Run the configured checks again.')
                        elif set(attempt.get('after') or {}) != set(self.evidence._scoped(evidence)) or attempt['scope_version'] != evidence['scope_version']:
                            current.update(state='blocked', error='The files covered by this task changed after its last check. Run the configured checks again after reading or editing files.')
                        elif not self.evidence._current(evidence, attempt):
                            # Night 2, finding 2: a file it covers was changed from outside (another thread sharing the
                            # folder, or an edit by hand). Written for the person who reads the task, not the agent.
                            current.update(state='blocked', error='Files this task covers changed after its last check, so that check no longer stands. Ask it to recheck to finish.')
                        elif attempt['test']['exit_status'] != 0 or attempt['test']['stopped']:
                            current['error'] = 'Host checks failed or stopped. Inspect the failure and request a correction. Approval is unavailable.'
                if current['runs'][-1].get('provider_failure') and current.get('origin') == 'conversation':
                    self.conversation.provider_failed(current)  # records the other-provider suggestion, or arms an automatic retry
                elif data['state'] == 'interrupted' and current.get('origin') == 'conversation':
                    from conversation_activity import stopped_by_user
                    if stopped_by_user(current):
                        self.conversation.person_stopped(current)  # your own Stop: Continue on the other provider, never automatic (N1)
                usage_events.task_ended(self.host, run['id'], binding, data['state'],
                                        'host_limit' if data.get('host_stop') else current['runs'][-1].get('provider_failure'))
            self.save(current)
            for waiting in pending_queue:
                try:
                    self.conversation.queue.add(dict(clientId=waiting['client_id'], text=waiting['text']), ctx, self.store.binding(**ctx),
                                                why=waiting.get('why'))
                except Rejected:
                    pass
        def context():
            current = self.read(task['id'], ctx)
            if not current['session_id']:
                raise Rejected('Task session must be persisted before context delivery')
            current['runs'][-1]['state'] = 'sending'
            self.save(current)
            from collaboration import prefix as context_prefix
            prefix = context_prefix(task['files'], task.get('folders', []), project_access=task.get('project_access') is True, check_profile=task.get('check_profile'))
            if full_access:
                from collaboration import FULL_ACCESS_AUTO_COMMIT_PREFIX, FULL_ACCESS_PREFIX
                prefix = FULL_ACCESS_AUTO_COMMIT_PREFIX if task.get('auto_commit') is True else FULL_ACCESS_PREFIX
            if task.get('origin') == 'collaboration':
                return (self.collaboration.context_text(task, prefix), run['id'])  # the recorded packet payload, verbatim
            from project_knowledge import ProjectKnowledge, request_context
            current = self.read(task['id'], ctx)
            saved_run = next(r for r in current['runs'] if r['id'] == run['id'])
            if 'knowledge_snapshot' not in saved_run:
                saved_run['knowledge_snapshot'] = ProjectKnowledge(self.store).snapshot(self.store.binding(**ctx), ctx['actor'],
                    previous=[r['knowledge_snapshot'] for r in current['runs'] if r['id'] != run['id'] and 'knowledge_snapshot' in r],
                    query=next((m['text'] for m in reversed(task['messages']) if m['role']=='user'), ''))
                # The repository's CLAUDE.md, AGENTS.md and rules, as text in the same record (repo_guidance.py, A6).
                import repo_guidance
                saved_run['knowledge_snapshot']['repository'] = repo_guidance.snapshot(self.store.db, self.store.binding(**ctx))
                self.save(current)  # Persist the exact guidance before handing it to either provider adapter.
            guidance = saved_run['knowledge_snapshot']['text']
            repository = (saved_run['knowledge_snapshot'].get('repository') or {}).get('text', '')
            # The saved record, not the task as it was at start: the turn may have found the earlier session gone and started fresh.
            told = {}
            base = self.conversation.context_text(dict(task, continuity=current.get('continuity'), delivering_run=run['id']), prefix, told=told) if task.get('origin') == 'conversation' else prefix + task['messages'][-1]['text']
            if told:
                # What the agent was told about the conversation so far, beside the notes and skills it was given (item 8).
                current = self.read(task['id'], ctx)
                saved_run = next(r for r in current['runs'] if r['id'] == run['id'])
                saved_run.setdefault('told', {}).update(told)
                self.save(current)
            # What the agent does during the task (the guide, other chats, earlier work), then one list of what it does
            # before its final answer. Up to 19 Sep these were five separate trailing blocks and the memory step, third
            # of them, was skipped whenever the fact wasn't what the message was about (critical review, 20 Sep, section
            # 10; hard case m44).
            from image_attachments import task_documents
            base += task_documents(self.host, current)
            tools = self.tools_for(current)
            base += during_task(tools) + finishing_steps(self.store.db, task['context']['project_id'], tools)
            import chat_links
            # Chats the message names with #: their state, before the request and labelled as records, not instructions.
            mentions = chat_links.context_block(self.store.db, current, next((m['text'] for m in reversed(task['messages']) if m['role'] == 'user'), ''))
            import thread_notes
            # A thread with subthreads knows where they stand without being told their names (beta.17, #27 d).
            children = thread_notes.children_block(self, current)
            mentions = (children + '\n' if children else '') + mentions
            return ((mentions + '\n' if mentions else '') + request_context(base, guidance, repository), run['id'])
        def images():
            from image_attachments import task_payload
            return task_payload(self.host, self.read(task['id'], ctx))
        try:
            # An API-key Claude account's key is read from Keychain for this process only. It never enters the binding or the task.
            extra = {'api_key': self.host.keys.key_for(binding)} if binding['provider'] == 'claude' else {}
            import run_limits  # beta.17.1: this project's silence and run-length limits, as the run starts
            trusted = TrustedTaskBinding(task['id'], binding, authorize, tools=advertised_tools, images=images, full_access=full_access,
                                         connectors_allowed=connectors_allowed, computer_access=task.get('computer_access') is True,
                                         commands=task.get('commands') is not False, connectors_only=connectors_only,
                                         limits=run_limits.read(self.store.db, ctx['project_id']))
            # A follow-up takes the Claude Code started for it when its run ended, if that launch is exactly this one.
            warm = (self.take_warm(ctx['thread_id'], binding, trusted, context, emit)
                    if binding['provider'] == 'claude' and extra.get('api_key') is None else None)
            self.turns[task['id']] = warm or CodingTurn(binding, task=trusted, context=context, emit=emit, **extra)
        except Exception as exc:
            import error_reports
            error_reports.capture(exc, source='task', operation='task_start', provider=binding.get('provider'))
            current = self.read(task['id'], ctx)
            current.update(state='blocked', error=str(exc))
            current['runs'][-1].update(state='failed', end_cause='not_started')
            self.save(current)
            usage_events.task_ended(self.host, run['id'], binding, 'failed', 'start_failed')
        return self.read(task['id'], ctx)

    def forget_ended(self):
        """Stop listening for a thread once its last run ended: nothing there can go quiet (third review, Low 1)."""
        for task_id in [t for t in self.alive_runs if t not in self.turns]:
            thread_id = self.alive_runs.pop(task_id)
            if thread_id not in self.alive_runs.values():
                self.alive.forget(thread_id)

    def tick(self):
        self.forget_ended()
        self.alive.sweep()  # a run that just went quiet moves its row once (thread_states)
        self.settle_folder_reads()  # what the relaunch's folder read found for a lost full-access run, if it's back
        self.settle_recordings()  # a finished run whose recording ran long on its thread (beta.18)
        self.settle_checks()  # a full-access result's own checks, back from their thread
        for task_id in [t for t in self.text_waiting if t not in self.turns]:
            self.text_waiting.pop(task_id)  # its run ended, or its conversation was deleted with it (project_lifecycle)
        self.checks_open &= set(self.turns)
        for task_id, turn in list(self.turns.items()):
            try:
                self._tick_turn(task_id, turn)
            except Exception as exc:  # noqa: BLE001 - one run's error ends that run, never the host
                self.turn_failed(task_id, turn, exc)
        # Help packets settle inside this same pump: queued children start when a slot frees, finished children return.
        self.collaboration.settle()
        self.acceptance.settle()  # Completed only from recorded facts, after the review phases above moved
        self.conversation.auto_retry()  # a provider-failed request armed to retry on the other provider starts first
        self.folders.settle()  # beta.18: a thread folder's setup that finished saves now, so its waiting message can start
        self.settle_warm()  # before the queue, so a follow-up queued behind the run that just ended starts warm
        self.conversation.queue.drain()  # a follow-up queued behind a run that ended in this pump starts now
        import thread_notes
        thread_notes.settle(self)  # a parent whose last running subthread just finished gets its review (beta.17)

    def turn_failed(self, task_id, turn, error):
        """Background work for one run raised. That exception used to leave tick(), end the whole host, and mark every
        open run "Host closed" with the real cause only in host-errors.log (run-lifecycle.md, finding 4). Now this run
        alone ends, its record names the error, the report carries its type and place, and the host and the other runs
        carry on. The provider process is closed, not stopped: a Stop would write its own outcome over this one."""
        import error_reports
        import traceback
        try:
            self.save_waiting_text(task_id)  # what the agent had written before Dervo failed is real; keep it
        except Exception:  # noqa: BLE001 - the error below is what the record must say
            pass
        self.text_waiting.pop(task_id, None)
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        task = json.loads(row[0]) if row else None
        provider = ((task or {}).get('binding') or {}).get('provider')
        error_reports.capture(error, source='task', operation='tick', provider=provider)
        print(f'A coding run ended on an error in Dervo itself ({type(error).__name__}); the host carries on.', file=sys.stderr)
        traceback.print_exception(error, file=sys.stderr)
        try:
            turn.close()
        except Exception:  # noqa: BLE001 - the record below is what matters; the process group is gone or going
            pass
        self.turns.pop(task_id, None)
        if task is None:
            return
        if task['state'] == 'working':
            task.update(state='disconnected', error=f'Dervo hit an error while following this run ({type(error).__name__}), '
                        'so it can’t say how it ended. Check your folder, then leave it stopped.')
            if task.get('runs'):
                task['runs'][-1]['state'] = 'uncertain'
            for token in task['tokens']:
                if token['state'] not in ('delivered', 'cancelled'):
                    token['state'] = 'uncertain'
            self.save(task)

    def _tick_turn(self, task_id, turn):
        """One run's share of the pump: the provider turn, its confined checks, and its tool requests."""
        import base64
        turn.tick()
        self.save_waiting_text(task_id, force=turn.closed)  # text from a run that has gone quiet, or has just ended
        if not turn.closed and task_id not in self.checks_open and not turn.pending_tool_requests:
            # No check to poll or start and no request waiting: the record isn't read. It was parsed every 20 ms for every
            # run, and its cost grew with the record (beta.16.1, tools/load_thread_scale.py).
            return
        row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        task = json.loads(row[0])
        ctx = task['context']
        for token in task['tokens']:
            if token.get('job_id') and not token.get('job_done'):
                attempt = self.evidence.poll(task_id, job_id=token['job_id'], **ctx)
                if attempt['state'] == 'running':
                    continue
                token['job_done'] = True
                self.save(task)
                if token['state'] == 'running' and not turn.closed:
                    output = provider_check_result(task_id, attempt)
                    self.deliver(task, token, turn, attempt['state'] == 'verified', output)
            elif token['state'] == 'queued' and token['tool'] == 'dervo_run_tests' and not turn.closed:
                # Waiting behind another task's confined test job. Launched here once the host runner is free.
                if self.evidence.busy():
                    continue
                try:
                    self.require_grant(task)
                    token['job_id'] = self.evidence.launch(task_id, attempt_number=token['attempt'], **ctx)
                    token['state'] = 'running'
                    self.save(task)
                except Exception as exc:
                    self.deliver(task, token, turn, False, {'error': str(exc)})
        if not any(check_open(t) for t in task['tokens']):
            self.checks_open.discard(task_id)
        if turn.closed:
            if not any(t.get('job_id') and not t.get('job_done') for t in task['tokens']):
                # Nothing left to poll for this run. A closed turn used to stay here for the rest of the session, and
                # this pump re-read and parsed its record every 20 ms, so the host got slower all day (run-lifecycle.md,
                # finding 5). Every reader of ``turns`` treats a missing turn exactly as a closed one.
                self.turns.pop(task_id, None)
            return
        requests = turn.pending_tool_requests
        allowed = self.tools_for(task) if requests else ()  # read only when a request is waiting: not every 20 ms per run
        if requests:
            self.checks_open.add(task_id)  # a request may start or queue a check: the next round reads the record again
        for request in requests:
            task = self.read(task_id, ctx)
            if any(t['token'] == request.token for t in task['tokens']):
                continue
            token = dict(token=request.token, request_id=request.request_id, call_id=request.call_id,
                session_id=request.thread_id, turn_id=request.turn_id, run_id=task['runs'][-1]['id'],
                tool=request.tool, state='executing', attempt=None, audit_ref=request.token,
                path=request.arguments.get('path', request.arguments.get('directory')),  # validated task-relative path, shown as progress
                started_at=round(time.time(), 3))  # wall clock for the Task guide's step durations, never used for ordering or recovery
            # Pending requests have passed adapter shape and size validation.
            arguments = dict(request.arguments)
            if request.tool in KEPT_TEXT_TOOLS:
                # A note, skill or lesson holding a key is refused (secret_scan); its audit never keeps the key either.
                arguments = secret_scan.redact_value(arguments)
            audit = dict(token=request.token, arguments=arguments, response=None, snapshot=None)
            self.save_audit(task_id, audit)
            task['tokens'].append(token)
            self.save(task)
            try:
                turn.adapter._approved(request)
                if request.tool not in allowed:
                    raise Rejected(f'{request.tool} is not part of this task contract ({task.get("role") or "owner"} role)')
                if request.tool == 'dervo_update_task_guide':
                    from task_guide import capture
                    result = capture(self, task, token, dict(request.arguments))
                elif request.tool == 'dervo_ask_user':
                    import task_questions
                    result = task_questions.capture(self, task, token, dict(request.arguments))
                elif request.tool == 'dervo_remind_user':
                    import task_reminders
                    result = task_reminders.capture(self, task, token, dict(request.arguments))
                elif request.tool == 'dervo_remember':
                    import project_memory
                    result = project_memory.capture(self, task, token, dict(request.arguments))
                elif request.tool == 'dervo_propose_thread':
                    import thread_fanout
                    result = thread_fanout.capture(self, task, dict(request.arguments))
                elif request.tool == 'dervo_check_chat':
                    import chat_links
                    result = chat_links.check(self, task, dict(request.arguments))
                elif request.tool == 'dervo_leave_note':
                    import thread_notes
                    result = thread_notes.capture(self, task, dict(request.arguments))
                elif request.tool == 'dervo_search_history':
                    import project_memory
                    result = project_memory.search(self, task, dict(request.arguments))
                elif request.tool == 'dervo_propose_skill':
                    import project_skills
                    result = project_skills.propose(self, task, dict(request.arguments))
                elif request.tool == 'dervo_suggest_lesson':
                    result = self.knowledge.lessons.capture(task,dict(request.arguments))
                elif request.tool == 'dervo_read_file':
                    if task.get('read_only'):
                        from project_reading import ProjectReader
                        result = ProjectReader(self, task).read_file(task_id, request.arguments['path'], **ctx)
                    elif task.get('role') == 'reviewer':
                        # A reviewer reads the exact snapshot its packet was sent with, never the moving project file.
                        result = self.collaboration.snapshot_read(task, request.arguments['path'])
                    else:
                        self.require_grant_for(task, request.arguments['path'])
                        result = self.evidence.read_file(task_id, request.arguments['path'], **ctx)
                    audit['snapshot'] = result
                    self.save_audit(task_id, audit)
                    # A line range, or a file too big for one reply, comes back in parts with its total lines (beta.15).
                    result = self.evidence.read_lines(result, request.arguments.get('start_line'), request.arguments.get('end_line'))
                    if not task.get('read_only') and task.get('role') != 'reviewer':
                        evidence = self.evidence.read(task_id, **ctx)
                        if evidence['attempts']:
                            attempt = evidence['attempts'][-1]
                            if (attempt.get('test') and
                                    set(attempt.get('after') or {}) != set(self.evidence._scoped(evidence))):
                                result['verification_required'] = (
                                    'The task now covers files added after its last check. Finish reading and editing, '
                                    'then run dervo_run_tests again before your final answer.')
                elif request.tool == 'dervo_request_help':
                    packet = self.collaboration.request(task, token, dict(request.arguments))
                    token['packet_id'] = packet['id']
                    if packet['status'] == 'refused':
                        self.save(task)
                        self.deliver(task, token, turn, False, {'error': packet['refusal'], 'packet_id': packet['id']})
                        continue
                    token['state'] = 'running'
                    self.save(task)
                    if packet['status'] in ('returned', 'failed'):
                        # The same ask was already answered in this run: the same packet, its result, no second helper.
                        self.deliver(task, token, turn, packet['status'] == 'returned', self.collaboration.body(packet))
                    continue
                elif request.tool == 'dervo_list_files':
                    # Bounded listing inside an explicit granted folder. The broker rechecks the grant now; the adapter only checked shape.
                    self.require_grant(task)
                    from project_reading import ProjectReader
                    reader = ProjectReader(self, task) if task.get('read_only') else self.evidence
                    result = reader.list_files(task_id, request.arguments['directory'], **ctx)
                elif request.tool == 'dervo_search_files':
                    self.require_grant(task)
                    from project_reading import ProjectReader
                    reader = ProjectReader(self, task) if task.get('read_only') else self.evidence
                    result = reader.search_files(task_id, request.arguments['directory'], request.arguments['query'], **ctx)
                elif request.tool == 'dervo_create_file':
                    self.require_grant(task)
                    args = request.arguments
                    holder = self.collaboration.holder(task_id, args['path'])
                    if holder:
                        raise Rejected(f"{args['path']} is being changed by helper '{holder}'. Wait for its return before creating it.")
                    other = self.parallel_holder(task_id, args['path'])
                    if other:
                        raise Rejected(f"{args['path']} is being changed by a task running in {other}. Wait for it to finish before creating it.")
                    token['attempt'] = len(self.evidence.read(task_id, **ctx)['attempts']) + 1
                    self.save(task)
                    attempt = self.evidence.create_file(task_id, args['path'], args['content'].encode('utf-8'), **ctx)
                    token['attempt'] = attempt['number']
                    result = dict(attempt=attempt['number'], state=attempt['state'], created=args['path'],
                                  sha256=attempt['after'][args['path']]['sha256'])
                elif request.tool == 'dervo_review_verdict':
                    # An independent reviewer's structured verdict: recorded once on its own task, never a write.
                    result = self.collaboration.verdict(task, dict(request.arguments))
                elif request.tool == 'dervo_edit_file':
                    # One exact, unique text swap checked against the file's hash; the change is an ordinary attempt.
                    args = request.arguments
                    holder = self.collaboration.holder(task_id, args['path'])
                    if holder:
                        raise Rejected(f"{args['path']} is being changed by helper '{holder}'. Wait for its return before editing it.")
                    other = self.parallel_holder(task_id, args['path'])
                    if other:
                        raise Rejected(f"{args['path']} is being changed by a task running in {other}. Wait for it to finish before editing it.")
                    self.require_grant_for(task, args['path'])
                    token['attempt'] = len(self.evidence.read(task_id, **ctx)['attempts']) + 1
                    self.save(task)
                    attempt = self.evidence.edit_file(task_id, args['path'], expected_sha256=args['expected_hash'],
                                                      old=args['old_string'].encode('utf-8'), new=args['new_string'].encode('utf-8'), **ctx)
                    token['attempt'] = attempt['number']
                    result = dict(attempt=attempt['number'], state=attempt['state'], sha256=attempt['after'][args['path']]['sha256'])
                elif request.tool == 'dervo_write_file':
                    args = request.arguments
                    holder = self.collaboration.holder(task_id, args['path'])
                    if holder:
                        raise Rejected(f"{args['path']} is being changed by helper '{holder}'. Wait for its return before writing it.")
                    other = self.parallel_holder(task_id, args['path'])
                    if other:
                        raise Rejected(f"{args['path']} is being changed by a task running in {other}. Wait for it to finish before writing it.")
                    self.require_grant_for(task, args['path'])
                    token['attempt'] = len(self.evidence.read(task_id, **ctx)['attempts']) + 1
                    self.save(task)
                    attempt = self.evidence.prepare(task_id, changes={args['path']:dict(
                        expected_sha256=args['expected_hash'], content=args['content'].encode('utf-8'))}, **ctx)
                    token['attempt'] = attempt['number']
                    result = dict(attempt=attempt['number'], state=attempt['state'])
                else:
                    evidence = self.evidence.read(task_id, **ctx)
                    latest = evidence['attempts'][-1] if evidence['attempts'] else None
                    prior = task['runs'][-1].get('prior_attempts', 0)
                    if latest and latest['number'] > prior and latest['state'] == 'prepared':
                        # This run wrote a file attempt that awaits verification.
                        token['attempt'] = latest['number']
                    else:
                        # No pending write from this run. Verify the current scoped files without
                        # changing them; evidence from an earlier run is never reused.
                        token['attempt'] = self.evidence.verify_current(task_id, **ctx)['number']
                    self.save(task)
                    if self.evidence.busy():
                        # Another task's confined tests are running: queue this one instead of refusing or doubling up.
                        token['state'] = 'queued'
                        self.save(task)
                        continue
                    self.require_grant(task)
                    token['job_id'] = self.evidence.launch(task_id, attempt_number=token['attempt'], **ctx)
                    token['state'] = 'running'
                    self.save(task)
                    continue
                self.deliver(task, token, turn, True, result)
            except Exception as exc:
                from task_evidence import AttemptLimit
                if isinstance(exc, AttemptLimit) and task.get('runs'):
                    task['runs'][-1]['limit_hit'] = 'attempts'  # the run's end says so (run_ending), not "failed"
                if not turn.closed:
                    self.deliver(task, token, turn, False, {'error':str(exc)})

    def deliver(self, task, token, turn, success, result):
        text = json.dumps(result, ensure_ascii=False)
        if len(text.encode()) > 60000:
            success, text = False, json.dumps({'error':'Tool result exceeds provider budget. Inspect complete host evidence.'})
        row = self.store.db.execute('SELECT document FROM coding_tool_audit WHERE task_id=? AND token=?',
                                    (task['id'], token['token'])).fetchone()
        if not row:
            raise Rejected('Tool response has no durable request audit')
        audit = json.loads(row[0])
        audit['response'] = dict(success=success, text=text)
        self.save_audit(task['id'], audit)
        token.update(state='delivering', success=success)
        self.save(task)
        try:
            turn.complete_tool_result(token['token'], success=success, text=text)
        except Exception as exc:
            # The transport may have delivered the response. Never replay it.
            current = self.read(task['id'], task['context'])
            next(t for t in current['tokens'] if t['token'] == token['token'])['state'] = 'uncertain'
            current.update(state='disconnected', error=str(exc))
            current['runs'][-1]['state'] = 'uncertain'
            self.save(current)
            turn.close()
            return
        token.update(state='delivered', ended_at=round(time.time(), 3))
        self.save(task)

    def close(self):
        import time
        try:
            self.close_warm()  # nothing was sent to these, so there's nothing to pause
        except Exception as exc:  # noqa: BLE001 - a stubborn process can't stop the host closing
            print(f'A Claude Code started ahead of time could not be closed: {type(exc).__name__}: {exc}', file=sys.stderr)
        if hasattr(self, 'check_pool'):
            import project_checks
            project_checks.stop_all()  # what a running check started ends too, so closing never waits on a test suite
            self.check_pool.shutdown(wait=False, cancel_futures=True)  # unreported checks read as not run next time (recover_checks)
        # Shutdown is best effort and must reach every step: the host now builds this coordinator at start (so a
        # relaunch reconciles before it answers, finding 2), which means close() runs on every quit, including ones
        # where nothing ran. One bad record or a stubborn turn must not wedge the quit or leave the store open, so each
        # step is guarded; the store closes whatever happens (I2, "however it exits").
        try:
            self.collaboration.close()  # in-flight help is disconnected, not withdrawn: the host is going away, nobody stopped it
        except Exception as exc:  # noqa: BLE001 - a bad packet can't stop the host closing
            print(f'A help packet could not be settled at close: {type(exc).__name__}: {exc}', file=sys.stderr)
        # Pause first (29 Sep, Bryce: quitting should pause, so you can pick up after). Every run is asked to stop, then
        # the pump runs for up to QUIT_PAUSE_SECONDS so a run that finishes its step records a clean interruption: that
        # reads as Paused and the next message continues the same session. Only what's left is closed below.
        for task_id, turn in list(self.turns.items()):
            try:
                self.save_waiting_text(task_id)
                if not turn.closed:
                    turn.stop()
            except Exception as exc:  # noqa: BLE001 - one run can't stop the others pausing
                print(f'A coding run could not be paused at quit: {type(exc).__name__}: {exc}', file=sys.stderr)
        deadline = time.monotonic() + QUIT_PAUSE_SECONDS
        while time.monotonic() < deadline and any(not turn.closed for turn in self.turns.values()):
            for task_id, turn in list(self.turns.items()):
                if turn.closed:
                    continue
                try:
                    self._tick_turn(task_id, turn)
                except Exception as exc:  # noqa: BLE001 - the close below still records this run
                    print(f'A paused run could not report at quit: {type(exc).__name__}: {exc}', file=sys.stderr)
            time.sleep(.02)
        for task_id, turn in list(self.turns.items()):
            try:
                self.save_waiting_text(task_id)
                turn.stop()
                turn.close()
                row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
                task = json.loads(row[0])
                if task['state'] == 'working':
                    task.update(state='disconnected', error=CLOSE_LOST)
                    task['runs'][-1]['state'] = 'uncertain'
                    # The step the agent was in (Bash, a web search) was cut off with it. Left 'running', it held
                    # Mark as stopped, archive and every update until the store was edited by hand (29 Sep).
                    for token in task['tokens']:
                        if token.get('native') and token.get('state') == 'running':
                            token['state'] = 'uncertain'
                    self.save(task)
                for token in task['tokens']:
                    if token.get('job_id') and not token.get('job_done'):
                        self.evidence.cancel(task_id, job_id=token['job_id'], **task['context'])
                        while self.evidence.poll(task_id, job_id=token['job_id'], **task['context'])['state'] == 'running':
                            time.sleep(.01)
            except Exception as exc:  # noqa: BLE001 - one run's cleanup can't stop the others or the host
                print(f'A coding run could not be closed cleanly: {type(exc).__name__}: {exc}', file=sys.stderr)

    def retry_status(self, task, evidence):
        runs = task['runs']
        turn = self.turns.get(task['id'])
        # A legacy run without a baseline is safe only when no attempts exist.
        available = bool(task['state'] in ('blocked', 'ready_for_review') and runs
            and runs[-1]['state'] in ('completed', 'failed', 'interrupted')
            and len(evidence['attempts']) == runs[-1].get('prior_attempts', 0)
            and not any(r['state'] == 'uncertain' for r in runs)
            and all(t['state'] in ('delivered', 'cancelled') and
                    (not t.get('job_id') or t.get('job_done')) for t in task['tokens'])
            and (not turn or turn.closed))
        if available and task.get('collaboration') is not None:
            # A helper this task asked for that Dervo lost (a quit or restart mid-help) has an outcome nobody saw. Since 16.4
            # a quit pauses the owner cleanly, so the owner's own run no longer holds this: continuing it could build on
            # help that may or may not have landed, so it waits for a new request instead (never replayed).
            if any(p.get('status') == 'disconnected' for p in self.collaboration.all_packets(owner_task_id=task['id'])):
                available = False
        return dict(available=available)

    def reconcile_blocker(self, task):
        """Why reconciliation may not run now, or None. Reconcile inspects the current files of a stopped task; while the
        provider turn, a tool delivery or a confined check is still active, nothing about the attempt is settled yet."""
        turn = self.turns.get(task['id'])
        if task['state'] == 'working' or (turn and not turn.closed):
            return 'The provider turn is still active. Stop the task or wait for it to end before reconciling.'
        if any(t['state'] in ('executing', 'running', 'queued', 'delivering') or (t.get('job_id') and not t.get('job_done')) for t in task['tokens']):
            return 'A tool call or host check is still active. Reconcile after it ends.'
        from collaboration import REVIEW_OPEN
        if ((task.get('collaboration') or {}).get('review') or {}).get('phase') in REVIEW_OPEN:
            return 'Independent review is still active. Stop the task before reconciling.'
        return None

    def restoration_blocker(self, task):
        """Restoration shares the Host writer boundary, including stopped but unconfirmed deliveries."""
        binding = self.store.binding(**task['context'])
        if any(task['binding'].get(k) != binding.get(k) for k in ('profile_id', 'provider', 'identity', 'model', 'cwd', 'visibility')):
            return 'The task account or project binding changed. Restoration is refused'
        evidence = self.evidence.read(task['id'], **task['context'])
        if any(task.get(k) != evidence.get(k) for k in ('scope_version', 'files', 'folders', 'argv')):
            return 'The task scope version changed. Inspect the current permission before restoration'
        self.require_grant(task)
        if task.get('origin') == 'conversation':
            grant = self.conversation._grant(task['context']['project_id'])
            if (not grant or grant.get('actor') != task['context']['actor']
                    or grant.get('provider') != binding['provider']
                    or set(grant['files']) != set(task['files'])
                    or set(grant.get('folders', [])) != set(task.get('folders', []))
                    or grant.get('check_profile', 'python-unittest') != evidence['check_profile']):
                return 'Coding permission changed. Restoration needs the current task file and folder grant'
        if any(not turn.closed for turn in self.turns.values()) or any(
                not turn.closed for turn in self.host.turns.values()) or self.evidence.busy():
            return 'A Host request or check is active. Stop it before restoring a file'
        for row in self.store.db.execute('SELECT document FROM coding_tasks').fetchall():
            other = json.loads(row[0])
            # Conservatively serialize across projects, including helper and review work.
            if self.reconcile_blocker(other):
                return 'A competing task, tool or review is active. Stop it before restoring a file'
            if any(r['state'] == 'uncertain' for r in other['runs']) or any(
                    t['state'] == 'uncertain' for t in other['tokens']):
                return 'A provider or tool outcome is uncertain. Reconcile before restoring a file'
        for row in self.store.db.execute('SELECT document FROM task_evidence').fetchall():
            if any(a['state'] in ('uncertain', 'running') for a in json.loads(row[0])['attempts']):
                return 'A write outcome is uncertain or a check is active. Reconcile before restoring a file'
        return None

    def restoration_options(self, task, evidence):
        try:
            busy = self.restoration_blocker(task)
        except Rejected as exc:
            busy = str(exc)
        options = []
        for attempt in evidence['attempts']:
            if attempt.get('kind', 'change') not in ('change', 'create'):
                continue
            for path, diff in attempt.get('diff', {}).items():
                if not diff:
                    continue
                after = (attempt.get('after') or {}).get(path) or {}
                reason = busy
                if not reason:
                    try:
                        self.protect(evidence['root'], [path])
                        self.evidence.restoration_target(task['id'], attempt_number=attempt['number'],
                            scope_version=evidence['scope_version'], path=path, expected_hash=after.get('sha256', ''),
                            expected_attempts=len(evidence['attempts']), **task['context'])
                    except Rejected as exc:
                        reason = str(exc)
                options.append(dict(attempt=attempt['number'], path=path, available=not reason, reason=reason,
                    scope_version=evidence['scope_version'], expected_hash=after.get('sha256', ''),
                    expected_attempts=len(evidence['attempts'])))
        return options

    def invalidate_restored_result(self, task, *, evidence=None):
        if evidence is None:
            evidence = self.evidence.read(task['id'], **task['context'])
        if not evidence['attempts'] or evidence['attempts'][-1].get('kind') != 'restoration':
            return
        event = evidence['attempts'][-1]
        if task.get('restoration_event') == event['number']:
            return
        task['restoration_event'] = event['number']
        if task.get('review'):
            task.setdefault('review_history', []).append(task['review'])
        task['review'] = None
        if task.get('completion'):
            task['completion']['stale'] = True
        review = (task.get('collaboration') or {}).get('review')
        if review:
            review.setdefault('history', []).append({k: v for k, v in review.items() if k != 'history'})
            review.update(phase='stopped', snapshot=None, error='A human requested file restoration. Fresh review is required')
        task.update(state='ready_for_review', error='File restoration was requested. Inspect its recorded outcome. Earlier checks and reviews do not verify these files.')
        self.save(task)

    def reconcile_status(self, task, evidence):
        """Whether the one user action Reconcile offers applies: an interrupted creation whose outcome is uncertain. The
        reason names what stands in the way otherwise, so a disabled action explains itself and never implies success."""
        attempt = evidence['attempts'][-1] if evidence['attempts'] else None
        if not attempt or attempt['state'] != 'uncertain' or attempt.get('kind') not in ('create', 'restoration'):
            return dict(available=False, attempt=None, path=None, reason='No interrupted creation is recorded for this task. Nothing to reconcile.')
        if attempt.get('kind') == 'restoration':
            busy = self.reconcile_blocker(task)
            return dict(available=not busy, attempt=attempt['number'], path=attempt['restores']['path'], kind='restoration',
                        reason=busy or 'File restoration has an uncertain outcome. Inspection cannot safely replay the write')
        busy = self.reconcile_blocker(task)
        if busy:
            return dict(available=False, attempt=attempt['number'], path=attempt.get('created'), reason=busy)
        return dict(available=True, attempt=attempt['number'], path=attempt.get('created'),
                    reason=f"Creation of {attempt.get('created')} was interrupted before confirmation.")

    def save_proposal(self, proposal):
        with self.store.db:
            self.store.db.execute('INSERT INTO coding_proposals VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                                  (proposal['id'], json.dumps(proposal)))
        return proposal

    def proposal(self, op, args, ctx, binding):
        """Conversation-first scope: a message becomes a reviewable proposal, then a task that still
        needs explicit execution approval. Nothing starts here."""
        import uuid
        from scope_proposal import draft
        if op == 'task_propose':
            if binding['provider'] not in ('codex', 'claude'):
                raise Rejected('Unsupported coding provider')
            text, message_id = args['text'], args['messageId']
            if type(text) is not str or not text.strip() or len(text.encode()) > 32768:
                raise Rejected('A development request needs text up to 32 KiB')
            if type(message_id) is not str or not any(m['id'] == message_id for m in self.store.messages(ctx['thread_id'], ctx['project_id'], actor=ctx['actor'])):
                raise Rejected('Propose scope from a message in this conversation')
            drafted = draft(binding['cwd'], text)
            if drafted['files']:
                self.protect(binding['cwd'], drafted['files'])
            proposal = dict(id=str(uuid.uuid4()), context=ctx, message_id=message_id, text=text, created_at=int(time.time()),
                            task_id=None, binding={k: v for k, v in binding.items() if k != 'home'}, **drafted)
            return self.save_proposal(proposal)
        if op == 'proposals':
            result = []
            for row in self.store.db.execute('SELECT document FROM coding_proposals ORDER BY rowid').fetchall():
                proposal = json.loads(row[0])
                if proposal['context'] == ctx:
                    result.append(proposal)
            return result
        row = self.store.db.execute('SELECT document FROM coding_proposals WHERE id=?', (args['proposalId'],)).fetchone()
        if not row:
            raise Rejected('Unknown scope proposal')
        proposal = json.loads(row[0])
        if proposal['context'] != ctx:
            raise Rejected('Proposal belongs to another conversation')
        if op == 'proposal_dismiss':
            if proposal['state'] == 'accepted':
                raise Rejected('An accepted proposal is already a task')
            proposal['state'] = 'dismissed'
            return self.save_proposal(proposal)
        if proposal['state'] != 'proposed':
            raise Rejected('Only a drafted proposal can be accepted')
        if any(binding[key] != proposal['binding'][key] for key in ('provider', 'profile_id', 'identity', 'model', 'cwd', 'visibility')):
            raise Rejected('Conversation binding changed since the scope was drafted. Propose again.')
        task = self.dispatch('task_create', {'projectId': ctx['project_id'], 'channelId': ctx['thread_id'],
                                             'text': proposal['text'], 'files': proposal['files'], 'testFile': proposal['test_file']})
        proposal.update(state='accepted', task_id=task['id'])
        self.save_proposal(proposal)
        return {'proposal': proposal, 'task': task}

    def dispatch(self, op, args):
        from host import ACTOR
        optional = {'folders', 'checkProfile', 'rustToolchain', 'checkTimeoutSeconds', 'projectAccess'} if op == 'task_create' else set()  # explicit granted folders and check profile; absent keeps the file-only Python shape
        if op in ('task_review', 'task_commit'):
            optional = {'carriedId'}  # the carried plan the sheet showed, for a task that carried on from a handoff snapshot
        if op == 'task_settle':
            optional = {'checking'}  # beta.18: Continue, checking first (a step that may have reached outside the folder)
        if type(args) is dict and 'checking' in args and args['checking'] is not True:
            raise Rejected('Unsupported task fields')
        if type(args) is dict and 'carriedId' in args and (type(args['carriedId']) is not str or not 0 < len(args['carriedId']) <= 64):
            raise Rejected('A carried id is the one the review gave')
        if type(args) is not dict or set(args) - optional != FIELDS[op] | {'projectId', 'channelId'}:
            raise Rejected('Unsupported task fields')
        ctx = dict(thread_id=args['channelId'], project_id=args['projectId'], actor=ACTOR)
        binding = self.store.binding(**ctx)
        if op == 'task_create':
            if binding['provider'] not in ('codex', 'claude'):
                raise Rejected('Unsupported coding provider')
            text = args['text']
            if type(text) is not str or not text.strip() or len(text.encode()) > 32768:
                raise Rejected('Task needs a request up to 32 KiB')
            folders = args.get('folders') or []
            project_access = args.get('projectAccess', False)
            if type(project_access) is not bool:
                raise Rejected('Project access must be an explicit choice')
            self.protect(binding['cwd'], args['files'], ['.'] if project_access else folders)
            evidence = self.evidence.create(**ctx, files=args['files'], test_file=args['testFile'], folders=folders,
                                            check_profile=args.get('checkProfile'), rust_toolchain=args.get('rustToolchain'),
                                            check_timeout_seconds=args.get('checkTimeoutSeconds', 5), project_access=project_access)
            return self.save(dict(id=evidence['id'], context=ctx, visibility=binding['visibility'],
                state='needs_permission', session_id=None, contract='dervo-dynamic-tools-v1',
                binding={k:v for k,v in binding.items() if k != 'home'}, scope_version=1,
                files=evidence['files'], folders=evidence['folders'], project_access=evidence.get('project_access', False), root_identity=evidence.get('root_identity'), argv=evidence['argv'], check_profile=evidence['check_profile'],
                rust_toolchain=evidence.get('rust_toolchain'), check_timeout_seconds=evidence['check_timeout_seconds'],
                messages=[dict(role='user', text=text, created_at=int(time.time()))],
                tokens=[], runs=[], error=None))
        if op == 'tasks':
            result = []
            # Creation order. Without ORDER BY, SQLite may scan the text primary key index instead. Only this thread's
            # rows are decoded (16.4 item 1); read() still checks each one's whole context.
            for row in self.store.db.execute("SELECT id FROM coding_tasks WHERE json_extract(document, '$.context.thread_id')=? "
                                             "AND json_extract(document, '$.context.project_id')=? ORDER BY rowid",
                                             (ctx['thread_id'], ctx['project_id'])).fetchall():
                try:
                    listed = self.read(row[0], ctx, rebound=True)  # earlier tasks stay listed after the conversation's account changes
                    # Raw delivery context belongs only in the exact task inspector.
                    for listed_run in listed['runs']:
                        listed_run.pop('knowledge_snapshot', None)
                        listed_run.pop('told', None)
                    result.append(listed)
                except Rejected:
                    pass
            return result
        if op in ('task_propose', 'proposals', 'proposal_accept', 'proposal_dismiss'):
            return self.proposal(op, args, ctx, binding)
        # Settling records the owner's decision about an old lost run. It doesn't execute under the new binding.
        task = self.read(args['taskId'], ctx, rebound=op in ('task_detail', 'task_settle', 'task_thread_proposals', 'task_thread_dismiss', 'task_question_answer'))
        if op == 'task_question_answer':
            import task_questions
            return task_questions.dispatch(self, task, args)
        if op in ('task_thread_proposals', 'task_thread_start', 'task_thread_dismiss'):
            import thread_fanout
            return thread_fanout.dispatch(self, op, task, args)
        if op in ('task_continue', 'task_approve') and any(
                binding[key] != task['binding'][key]
                for key in ('provider', 'profile_id', 'identity', 'model', 'cwd', 'visibility')):
            raise Rejected('Task account or execution binding changed. Retry cannot switch accounts')
        if op == 'task_approve':
            if task['state'] != 'needs_permission':
                raise Rejected('Execution scope is not awaiting approval')
            self.evidence.approve(task['id'], scope_version=args['scopeVersion'], **ctx)
            return self.start(task)
        if op == 'task_stop':
            turn = self.turns.get(task['id'])
            if turn and not turn.closed:
                turn.stop()
            self.collaboration.review_stop(task)  # an Independent review still driven for this owner ends here, its reviewer stopped
            return self.read(task['id'], ctx)
        if op == 'task_settle':
            # Dervo lost track of a run (the provider vanished, or Dervo stopped it without a result). Only you can end
            # that: this records your decision, it never claims what the run did. The files stay exactly as they are.
            turn = self.turns.get(task['id'])
            if turn and not turn.closed:
                raise Rejected('This task is still running. Stop it first.')
            if task['state'] == 'working' or any(token.get('state') in ('executing', 'running', 'queued', 'delivering')
                    or (token.get('job_id') and not token.get('job_done')) for token in task['tokens']):
                raise Rejected('A task or check is still running. Wait for it to finish before marking this stopped.')
            from collaboration import REVIEW_OPEN
            if ((task.get('collaboration') or {}).get('review') or {}).get('phase') in REVIEW_OPEN:
                raise Rejected('Independent review is still active. Stop the task before marking this stopped.')
            # A tool call whose delivery is unknown is the same kind of loss: lifecycle blockers call it a lost run and
            # offer Mark as stopped for it (project_lifecycle.ACTIONS), so it's settled here too.
            unconfirmed = task['state'] == 'disconnected' or any(isinstance(run, dict) and run.get('state') == 'uncertain' for run in task['runs']) \
                or any(token.get('state') == 'uncertain' for token in task['tokens'])
            if not unconfirmed:
                raise Rejected('This task has a confirmed outcome already.')
            # Only a lost run changes the task's state. A result whose run ended normally keeps it, with the unknown
            # tool call recorded as cancelled.
            lost_run = task['state'] == 'disconnected' or any(isinstance(run, dict) and run.get('state') == 'uncertain' for run in task['runs'])
            # Beta.18: Leave it stopped (Mark as stopped until now) reads as stopped, never a failure X: run_ending knows
            # ``settled_by_user``. The run still ends ``failed`` underneath, so retry and continuity treat it as before.
            for run in task['runs']:
                if isinstance(run, dict) and run.get('state') == 'uncertain':
                    run.update(state='failed', end_cause='settled_by_user', settled_at=int(time.time()))
            for token in task['tokens']:
                if token.get('state') not in ('delivered', 'cancelled'):
                    token.update(state='cancelled', ended_at=round(time.time(), 3))
            checking = args.get('checking') is True
            note = (LEFT_CHECKING if checking else LEFT_STOPPED) if lost_run else None
            if lost_run:
                task.update(state='blocked', error=(without_leave_hint(task.get('error')) + ' ' + note).strip())
            self.save(task)
            if checking and lost_run:
                # Continue, checking first: the same session carries on, told which step's outcome is unknown, so it
                # checks before running that step again (lost_runs.checking_text). Refused like any Continue would be.
                import lost_runs
                text = lost_runs.checking_text(task)
                return self.dispatch('task_continue', {'taskId': task['id'], 'text': text, 'projectId': ctx['project_id'],
                                                       'channelId': ctx['thread_id']})
            return self.read(task['id'], ctx, rebound=True)
        if op == 'task_restore_file':
            busy = self.restoration_blocker(task)
            if busy:
                raise Rejected(busy)
            self.protect(binding['cwd'], [args['path']])
            try:
                return self.evidence.restore_file(task['id'], attempt_number=args['attemptNumber'],
                    scope_version=args['scopeVersion'], path=args['path'], expected_hash=args['expectedHash'],
                    expected_attempts=args['expectedAttempts'], **ctx)
            finally:
                self.invalidate_restored_result(task)
        if op == 'task_reconcile':
            # Inspect an interrupted creation; only a provably absent target is settled, everything else is reported.
            # Never while the provider, a tool or a check is still active: the attempt could still change underneath.
            busy = self.reconcile_blocker(task)
            if busy:
                raise Rejected(busy)
            return self.evidence.reconcile(task['id'], **ctx)
        if op == 'task_detail':
            # The task read above enforces actor, channel and current binding.
            audit = [json.loads(row[0]) for row in self.store.db.execute(
                'SELECT document FROM coding_tool_audit WHERE task_id=? ORDER BY rowid', (task['id'],))]
            evidence = self.evidence.read(task['id'], rebound=True, **ctx)
            rebound = any(binding[k] != task['binding'].get(k) for k in ('profile_id', 'model'))
            import task_git
            # Whether approving commits: the Approve button says "Approve and commit" only inside a git repository. Words
            # only, so a few seconds old will do (task_git.in_repo); the commit itself asks git again.
            in_git = task_git.in_repo(binding['cwd'])
            # A task that carried on from a handoff snapshot commits the stopped run's files too: the sheet lists them,
            # and what it flags, before you approve (ConversationCoding.carried_plan).
            carried = None
            if task.get('carried') and not (task.get('commit') or {}).get('sha') and task['state'] in ('ready_for_review', 'completed', 'idle'):
                carried = self.conversation.carried_view(self.conversation.carried_plan(task, evidence))
            return dict(task=task, evidence=evidence_view(evidence), broker_audit=audit, rebound=rebound, git=in_git, carried=carried,
                        retry=dict(available=False) if rebound else self.retry_status(task, evidence),
                        restoration=self.restoration_options(task, evidence), reconcile=self.reconcile_status(task, evidence), collaboration=self.collaboration.task_summary(task))
        if op == 'task_review':
            if task['state'] == 'working' or any(r['state'] == 'uncertain' for r in task['runs']):
                raise Rejected('Review requires a confirmed stopped provider')
            if task.get('review') in ('approved', args['decision']):
                # Two decisions at nearly the same moment (the Mac and a paired phone, 9 Oct): the second finds the first
                # and changes nothing, so the commit and the item updates happen once. A new attempt clears the review;
                # changing your mind from "changes" to approve still works. Not by state: a carried-on task is decided
                # from completed too.
                raise Rejected('This result has been reviewed already.')
            from collaboration import REVIEW_OPEN
            phase = ((task.get('collaboration') or {}).get('review') or {}).get('phase')
            if phase in REVIEW_OPEN:
                # The host may still correct these files; a human decision now would be on a result that is not final.
                raise Rejected(f'Independent review is still running ({phase}). Wait for it to end, or stop the task, before deciding on the result.')
            if (args['decision'] == 'approved' and task['runs']
                    and args['attemptNumber'] <= task['runs'][-1].get('prior_attempts', 0)):
                raise Rejected('Approval requires verified evidence from the current run')
            carried = None
            if args['decision'] == 'approved' and task.get('carried') and task.get('origin') != 'collaboration':
                # Checked before the approval is recorded: a list that changed since the sheet showed it approves nothing.
                carried = self.conversation.carried_check(task, self.evidence.read(task['id'], **ctx), args.get('carriedId'), 'approving')
            self.evidence.review(task['id'], attempt_number=args['attemptNumber'], decision=args['decision'], **ctx)
            task['review'] = args['decision']
            if args['decision'] == 'approved':
                task['state'] = 'idle'
                # Bryce's 17 September decision: Dervo commits exactly this task's changed files when you approve. The
                # approval stands whatever git says; the outcome is recorded on the task for the guide to show.
                if task.get('origin') != 'collaboration':
                    task['commit'] = self.commit_record(task, binding, how='approved', carried=carried)
                    self.items_done(task)
                    native_evidence.settle_carried(self, task)
            saved = self.save(task)
            self.conversation.sweep_handoffs()  # a settled task starts its snapshot's grace period; older ones may go
            return saved
        if op in ('task_commit', 'task_undo'):
            import task_git
            if task.get('origin') == 'collaboration':
                raise Rejected('A helper’s work is committed with the task it helped.')
            if task['state'] == 'working' or any(r['state'] == 'uncertain' for r in task['runs']):
                raise Rejected('Wait for the task to settle before committing or undoing it.')
            if op == 'task_commit':
                if self.acceptance.stale_completed(task):
                    raise Rejected('Files changed after this result was completed. Nothing was committed. Request a correction or recheck first.')
                if task['state'] not in ('idle', 'completed'):
                    raise Rejected('Only an approved or completed result can be committed.')
                carried = None
                if task.get('carried'):
                    carried = self.conversation.carried_check(task, self.evidence.read(task['id'], **ctx), args.get('carriedId'), 'committing')
                record = self.commit_record(task, binding, how='approved' if task['state'] == 'idle' else 'completed', carried=carried)
                if record.get('error'):
                    raise Rejected(record['error'])
                if record.get('skipped'):
                    raise Rejected(record['skipped'])
                task['commit'] = record
                self.items_done(task)
                native_evidence.settle_carried(self, task)
            else:
                if (task.get('commit') or {}).get('how') == native_evidence.SETTLED_WITH:
                    raise Rejected('This result was committed together with a later result in this thread. Undo that one instead.')
                try:
                    task['commit'] = {**task.get('commit', {}), 'reverted': task_git.revert(task, binding['cwd'], thread_name=self.thread_name(task))}
                finally:
                    self.folders.forget()  # Undo's revert commit moves the branch too
                self.items_reopened(task)
            saved = self.save(task)
            self.conversation.sweep_handoffs()
            return saved
        if op == 'task_continue':
            evidence = self.evidence.read(task['id'], **ctx)
            if evidence['attempts'] and evidence['attempts'][-1].get('kind') == 'restoration' and evidence['attempts'][-1]['state'] != 'restored':
                raise Rejected('Restoration has an uncertain outcome. Reconcile and inspect it before continuing')
            retry = self.retry_status(task, evidence)['available']
            # A correction from a Completed result (or one whose files changed after it completed, so approval is unavailable):
            # the same task continues; no rejection or approval is recorded for past work.
            settled = (bool(task['session_id']) and not any(r['state'] == 'uncertain' for r in task['runs'])
                       and task['runs'] and task['runs'][-1]['state'] in ('completed', 'failed', 'interrupted'))
            from_completed = task['state'] == 'completed' and settled
            from_stale = self.acceptance.stale_completed(task) and settled
            from_restored = bool(settled and evidence['attempts'] and evidence['attempts'][-1].get('kind') == 'restoration'
                                 and evidence['attempts'][-1]['state'] == 'restored')
            if not retry and not from_completed and not from_stale and not from_restored and (task['state'] != 'ready_for_review' or not task['session_id']
                    or not evidence['review'] or evidence['review']['decision'] != 'rejected'
                    or any(r['state'] == 'uncertain' for r in task['runs'])):
                raise Rejected('Continue task requires completed evidence and a human correction request, or a confirmed no-attempt run')
            text = args['text']
            if type(text) is not str or not text.strip() or len(text.encode()) > 32768:
                raise Rejected('Correction needs text up to 32 KiB')
            if evidence['scope_version'] != task['scope_version'] or evidence['files'] != task['files'] or evidence['argv'] != task['argv']:
                raise Rejected('Task scope changed. Retry cannot change the approved target')
            problem = self.owner_slot_problem(task['context']['thread_id'])
            if problem:
                raise Rejected(problem)
            from collaboration import REVIEW_OPEN
            if ((task.get('collaboration') or {}).get('review') or {}).get('phase') in REVIEW_OPEN:
                raise Rejected('Independent review is still running. Stop the task before requesting a correction.')
            if task.get('read_only'):
                self.require_grant(task)
            elif task.get('origin') == 'conversation':
                # A conversation request runs under the project's coding permission: a revoked or changed grant starts nothing.
                permission = self.conversation.permission(binding)
                if (permission['state'] != 'granted' or set(permission['files']) != set(task['files'])
                        or set(permission.get('folders', [])) != set(task.get('folders', []))
                        or ('manual-review' in (permission.get('check_profile'), task.get('check_profile'))
                            and permission.get('check_profile') != task.get('check_profile'))
                        or (task.get('check_profile') != 'manual-review' and task.get('check_profile') not in {c['profile'] for c in permission.get('checks', [])})
                        or bool(permission.get('project_access')) != bool(task.get('project_access'))):
                    raise Rejected('Coding permission for this project is no longer current. Allow coding again before continuing this task.')
            # A note sent into the run that the run ended before reading (your own Pause or Stop kept it here): it
            # goes with this message, as the paused card says, then counts as carried (polish review, 9 Oct).
            unread = [m for m in task['messages'] if m.get('role') == 'user' and m.get('steer') == 'sent'
                      and not m.get('question_id') and isinstance(m.get('text'), str)]
            message = dict(role='user', text=text, created_at=int(time.time()))
            if unread:
                message['carries'] = [m['text'] for m in unread][:5]
                for m in unread:
                    m['steer'] = 'carried'
            task['messages'].append(message)
            if from_completed or from_stale:
                task.setdefault('corrections', []).append(dict(from_state='completed' if from_completed else 'stale', attempt=task['completion']['attempt'], created_at=int(time.time())))
            if from_restored or task.get('check_profile') == 'manual-review' or (task.get('acceptance') or {}).get('required') is False:
                # Approval is off for this request: the old verdict cannot complete changed files, so one fresh review cycle is armed.
                self.acceptance.rearm_review(task)
            task['review'] = None
            if evidence['approval'] != dict(scope_version=task['scope_version'], actor=ctx['actor']):
                task.update(state='needs_permission', error='Scope approval is no longer current. Approve execution before retrying.')
                return self.save(task)
            return self.start(task)
        if op == 'task_deny':
            if task['state'] != 'needs_permission':
                raise Rejected('Only an unapproved task can be denied')
            task.update(state='blocked', error='Execution scope denied. No task request was sent.')
            return self.save(task)
