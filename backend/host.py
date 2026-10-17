"""Local conversation host. Stdio belongs only to the supervising native process."""
import json
import warm_runs
import sys
import time
import uuid
from pathlib import Path
from dervo_connections import ON_PROJECT_SIDE, Store, Rejected

ACTOR = 'local-user'
BLOCKERS = {
    'claude': 'Chat turns are unavailable for Claude Code in Dervo. Coding tasks are available: they run unmodified Claude Code with Dervo host tools only, after official sign-in, exact identity and model verification.',
    'codex': 'Connection required. Official sign-in, exact identity/model and effective permissions need verification.'
}

def identifier():
    return uuid.uuid4().hex


def lost_runs_line(lost):
    """One calm line for a relaunch that found runs left open, or None. Each run's own thread says the same in full.

    Beta.18: a run Dervo settled as paused from the provider's own record (``paused``, lost_runs.settle) isn't lost: the line
    says it paused, and Continue in its thread picks it up. Only runs still unconfirmed get the open-the-thread line."""
    if not lost:
        return None
    unsure = [item for item in lost if not item.get('paused')]
    if not unsure:
        if len(lost) == 1:
            where = f' in {lost[0]["name"]}' if lost[0].get('name') else ''
            return f'Dervo restarted while a run was going{where}. It was between steps, so it paused there. Continue picks it up.'
        return f'Dervo restarted while {len(lost)} runs were going. Each was between steps, so it paused there. Continue picks each one up.'
    lost = unsure
    if len(lost) == 1:
        where = f' in {lost[0]["name"]}' if lost[0].get('name') else ''
        return f'Dervo closed while a run was going{where}, so it can’t say how it ended. Open that thread to see where it got to.'
    return (f'Dervo closed while {len(lost)} runs were going, so it can’t say how they ended. '
            'Open each thread to see where it got to.')


TICK_LOG_SECONDS = 60  # the same background error is written to stderr at most this often
# How long the request loop waits for a request before background work gets another round. It used to be 20 ms, forever,
# so an idle host woke 42 times a second (efficiency review, 23 Sep). Now it's 20 ms only while something is in flight
# (Host.in_flight), until the next timed job otherwise (Host.tick_wait), and never longer than IDLE_WAIT, a backstop for
# background work nobody listed. The app asks the host something every few seconds while it's open anyway.
BUSY_WAIT = .02
IDLE_WAIT = 10


def guarded_tick(host, noted, now=time.monotonic):
    """One round of background work. An error here used to leave the request loop, end the host and mark every open run
    "Host closed", with the cause only in host-errors.log (run-lifecycle.md, finding 4). Now the error is logged (the
    same one at most once a minute), reported by type and place, and the host carries on: the run it belongs to has
    already been ended by CodingTasks.turn_failed, and a request that needed the failed work answers on its own."""
    try:
        host.tick()
    except Exception as error:  # noqa: BLE001 - background work never takes the host down
        import error_reports
        import traceback
        key = (type(error).__name__, error_reports.location(error))
        at = now()
        if at - noted.get(key, float('-inf')) >= TICK_LOG_SECONDS:
            noted[key] = at
            print(f'Background work hit an error and the host carried on: {type(error).__name__}', file=sys.stderr)
            traceback.print_exception(error, file=sys.stderr)
        error_reports.capture(error, operation='tick')


# The operations that only end or decline work, allowed in an archived conversation (Host.dispatch).
SETTLING = frozenset({'task_stop', 'task_settle', 'task_deny', 'coding_queue_cancel'})

# What the screen says when a request fails inside Dervo itself. The raw text of such an error can quote a path, a SQL
# statement or a provider's output, and it reached about 33 places in the app as it was (review, 23 Sep).
UNEXPECTED = 'Something went wrong in Dervo’s workspace{ref}. Try again, and send feedback if it repeats.'


def error_ref(error, operation=None):
    """A short reference for an error, or None: six hex characters of the SHA-256 of the fields error_reports groups a
    report by (source, type, Dervo file, function, line, operation, provider). The same error gives the same reference,
    so a tester's "ref 3be21c" matches one row of the admin's error report. Never raises."""
    try:
        import error_reports
        import hashlib
        report = error_reports.from_exception(error, 'host', operation)
        if report is None:
            return None
        key = '|'.join('' if report[field] is None else str(report[field]) for field in error_reports.FIELDS)
        return hashlib.sha256(key.encode()).hexdigest()[:6]
    except Exception:  # noqa: BLE001 - a reference is never worth a second failure
        return None


def error_answer(error, operation=None):
    """The {"error"} text for a request that raised. A Rejected is a refusal written for people, answered as it is. So is
    a RuntimeError from a native-only operation (a leading underscore): the version switch and update steps word their
    refusals for the window (release_switch, app_update). Anything else gets one plain line and a reference."""
    if isinstance(error, Rejected):
        return str(error)
    if isinstance(error, RuntimeError) and isinstance(operation, str) and operation.startswith('_'):
        return str(error)
    ref = error_ref(error, operation)
    return UNEXPECTED.format(ref=f' (ref {ref})' if ref else '')


def note_request_error(error, operation, ref, noted, now=time.monotonic):
    """The whole error goes to stderr (host-errors.log, on this Mac only) with its reference, since the screen no longer
    shows it. The same error at most once a minute, like background work."""
    import error_reports
    import traceback
    key = ('request', type(error).__name__, error_reports.location(error))
    at = now()
    if at - noted.get(key, float('-inf')) < TICK_LOG_SECONDS:
        return
    noted[key] = at
    print(f'{clock()} A {operation or "host"} request failed (ref {ref or "none"}): {type(error).__name__}', file=sys.stderr)
    traceback.print_exception(error, file=sys.stderr)


def clock(when=None):
    """The local date and time a log line happened, so a report's "Host error on A thread at 12:46" finds its line."""
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(when))


def note_request_refusal(error, operation, noted, now=time.monotonic):
    """A refusal is answered in words written for people, and the window reports it as a "Host error" with only its
    time (BugCapture.errorKind). None of them reached host-errors.log, so the three on 23 Sep (12:46, 15:06, 17:11)
    couldn't be traced. Each is written as one line: the time, the operation, where Dervo refused and the first 200
    characters of what it said. The same refusal at most once a minute, since some reads repeat every few seconds."""
    import error_reports
    where = error_reports.location(error)
    key = ('refusal', operation, where)
    at = now()
    if at - noted.get(key, float('-inf')) < TICK_LOG_SECONDS:
        return
    noted[key] = at
    words = ' '.join(str(error).split())[:200]
    place = f' at {where[0]}:{where[2]}' if isinstance(where, tuple) and len(where) >= 3 and where[0] else ''
    print(f'{clock()} A {operation or "host"} request was refused{place}: {words}', file=sys.stderr, flush=True)


def request_id(request):
    """The bridge's id for a request (a whole number), or None for a request sent without one."""
    found = request.get('id') if isinstance(request, dict) else None
    return found if isinstance(found, int) and not isinstance(found, bool) else None


def serve(host, stdin=None, stdout=None, heartbeat=None):
    """The request loop: one JSON object per line in, one per line out, strictly in order; background work between
    requests; ``_shutdown`` or EOF ends it. Errors a request raises are answered as {"error"} and reported by shape,
    except a Rejected, which is an ordinary refusal the app shows the user (run-lifecycle.md, finding 12). Any other
    error is answered with error_answer's plain line, never its own text.

    Beta.18: a request's ``id`` comes back first in its reply, so the bridge can drop a reply that arrives after it
    stopped waiting instead of reading it as the next request's answer. A heartbeat then beats on stdout with what the
    loop is on (host_heartbeat.py), so a slow request no longer looks like a dead host. It starts with the first request
    that carries an id, which only the bridge sends, so a harness that reads every line as a reply (the Windows host
    smoke, tools/load_*.py, local-ui.test.mjs) sees none. ``heartbeat`` is seconds between beats: a test can start it
    at once with a short one, or pass 0 for none."""
    import error_reports
    import os
    import platform_os
    import threading
    from host_heartbeat import EVERY, Heartbeat
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    fd = stdin.fileno()
    buffer = b''
    noted = {}
    writing = threading.Lock()
    beats = Heartbeat(stdout, writing, every=heartbeat or EVERY) if heartbeat != 0 else None
    pushed = [None]
    def push():
        # The state spine: one short line when the newest change moved, once the bridge is listening (it sends ids,
        # which starts the heartbeat). A harness that reads every line as a reply never sees one.
        if not (beats and beats.started) or not hasattr(host, 'thread_version'):
            return
        try:
            newest = host.thread_version()
        except Exception:  # noqa: BLE001 - the window still reads on its own
            return
        if newest != pushed[0]:
            pushed[0] = newest
            import thread_states
            with writing:
                stdout.write(thread_states.push_line(newest) + '\n')
                stdout.flush()
    def background():
        if beats: beats.doing('tick')
        guarded_tick(host, noted)
        if beats: beats.idle()
        push()
    if beats and heartbeat:
        beats.start()
    try:
        while True:
            background()
            if b'\n' not in buffer:
                wait = host.tick_wait() if hasattr(host, 'tick_wait') else BUSY_WAIT
                started = time.monotonic()
                if not platform_os.wait_readable(fd, wait):
                    continue
                if time.monotonic() - started > BUSY_WAIT:
                    # Woken by a request after a longer wait: background work catches up first, so the request never
                    # sees state older than the old 20 ms loop allowed (a finished job not collected, a check that fell due).
                    background()
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > 262144:
                    raise Rejected('Host input buffer exceeded limit')
                if b'\n' not in buffer:
                    continue
            raw, buffer = buffer.split(b'\n', 1)
            if not serve_one(host, raw, stdout, writing, beats, noted, error_reports):
                break
            push()
    finally:
        if beats:
            beats.stop()


def serve_one(host, raw, stdout, writing, beats, noted, error_reports):
    """Answer one request line. False for ``_shutdown``, which ends the loop unanswered."""
    operation = request = None
    try:
        if len(raw) > 131072:
            raise Rejected('Request too large')
        request = json.loads(raw)
        if isinstance(request, dict) and {k: v for k, v in request.items() if k != 'id'} == {'operation': '_shutdown', 'args': {}}:
            return False
        operation = request.get('operation') if isinstance(request, dict) else None
        if beats:
            beats.doing(operation if isinstance(operation, str) else 'request', request_id(request))
            if request_id(request) is not None:
                beats.start()  # once; the bridge is listening for it
        if request['operation'] == '_stage_project_folder':
            from project_setup import stage
            result = stage(host, **request['args'])
        elif request['operation'] == '_picked_project':
            result = host.add_picked_project(**request['args'])
        else:
            result = host.dispatch(request['operation'], request.get('args', {}))
        response = {'result': result}
    except Exception as error:  # noqa: BLE001 - every request is answered; the loop never ends on one
        response = {'error': error_answer(error, operation)}
        if not isinstance(error, Rejected):
            error_reports.capture(error, operation=operation)  # its type and Dervo location, never str(error)
            try:
                note_request_error(error, operation, error_ref(error, operation), noted)
            except Exception:  # noqa: BLE001 - the answer below matters more than the log line
                pass
        else:
            try:
                note_request_refusal(error, operation, noted)
            except Exception:  # noqa: BLE001 - the answer below matters more than the log line
                pass
    finally:
        if beats:
            beats.idle()
    # The id goes first, so the bridge can read it without parsing a large reply.
    found = request_id(request)
    if found is not None:
        response = {'id': found, **response}
    text = json.dumps(response)
    with writing:
        stdout.write(text + '\n')
        stdout.flush()
    return True

class Host:
    def __init__(self, root, *, transport_factory=None, supervisor_factory=None, harness_workers=None, harness_claude_executable=None,
                 harness_codex_executable=None, dervo_account_adapter=None, account_runtime=None, worktrees_root=None):
        self.transport_factory = transport_factory
        self.dervo_account_adapter = dervo_account_adapter  # Explicit offline-test seam; no renderer or environment selection.
        # Offline-test seams for the harness coordinator, never renderer arguments.
        self.supervisor_factory, self.harness_workers = supervisor_factory, harness_workers
        self.harness_claude_executable, self.harness_codex_executable = harness_claude_executable, harness_codex_executable
        self.turns = {}
        from concurrent.futures import ThreadPoolExecutor
        self.checker = ThreadPoolExecutor(max_workers=2)
        self.checks = {}
        self.catalogs = {}
        self.logins = {}
        self.root = Path(root)
        self.store = Store(self.root/'conversations.sqlite3')
        # Beta.18 thread folders. The app passes platform_os.worktrees_dir() (main below); any other Host, a test's
        # included, keeps them inside its own folder, so it never sets one up in the real Worktrees folder.
        self.worktrees_root = Path(worktrees_root) if worktrees_root is not None else self.root / 'Worktrees'
        self.store.db.executescript('''
          CREATE TABLE IF NOT EXISTS local_channels(id TEXT PRIMARY KEY REFERENCES threads(id), name TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS local_projection(id TEXT PRIMARY KEY REFERENCES messages(id), parent_id TEXT, created_at INTEGER NOT NULL);
        ''')
        from project_lifecycle import schema as project_schema
        project_schema(self.store.db)
        self.store.db.commit()
        from message_activity import schema as activity_schema
        with self.store.db:
            activity_schema(self.store.db)
            from coordination_search import schema as coordination_schema
            coordination_schema(self.store.db)
        if account_runtime is not None:
            self.account_session = account_runtime(self.store)
        elif dervo_account_adapter is None:
            from account_config import create
            account = create(self.store, self.root)
            if account is not None:
                self.account_session = account
        import conversation_activity
        conversation_activity.schema(self.store.db)
        conversation_activity.mark_session(self.store.db, 'start')
        import thread_states
        thread_states.after_restart(self.store.db)  # a run lost with the last host now reads unknown, pushed
        self.tidy_threads()
        self.stamp_builds()
        # Usage events for the private beta (usage_events.py): records app_opened now. Never stops a start.
        try:
            from usage_events import UsageEvents
            self.usage = UsageEvents(self)
        except Exception as exc:  # noqa: BLE001 - the app opening comes first
            print(f'Usage sharing is off for this session: {type(exc).__name__}', file=sys.stderr)
        # The coding coordinator's restart pass (a run left open by a host that died reads disconnected, a queued
        # follow-up fails, an unstarted request blocks) used to wait for the first coding op. Home, the sidebar and pickup
        # read the same records and never send one, so after a crash a dead run stayed "Running" until you opened its
        # thread (run-lifecycle.md, finding 2). It runs here, before the host answers anything, in any workspace that
        # has ever run a task (the coding_tasks table is the coordinator's first act, so a store without it has nothing
        # to reconcile and stays lazy, as before). If it can't, the host still opens and the first coding op tries again.
        try:
            if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
                self.coding_tasks()
        except Exception as exc:  # noqa: BLE001 - the app opening comes first
            print(f'Coding tasks could not be reconciled at start: {type(exc).__name__}: {exc}', file=sys.stderr)
        # Send to cloud: a workspace with cloud runs still open follows them from the start (cloud_runs.CloudRuns), so a
        # launch the last host didn't finish reads unknown and Codex status keeps moving with no screen asking.
        try:
            if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_runs'").fetchone() and \
                    self.store.db.execute("SELECT 1 FROM cloud_runs WHERE state IN ('starting','working','unknown') LIMIT 1").fetchone():
                self.cloud_runs()
        except Exception as exc:  # noqa: BLE001 - the app opening comes first
            print(f'Cloud runs could not be reconciled at start: {type(exc).__name__}', file=sys.stderr)

    def cloud_runs(self):
        """Send to cloud (cloud_runs.CloudRuns), made on first use."""
        if not hasattr(self, 'cloud'):
            from cloud_runs import CloudRuns
            self.cloud = CloudRuns(self)
        return self.cloud

    def coding_tasks(self):
        """The one coding coordinator of this host (coding_integration.CodingTasks), made at start."""
        if not hasattr(self, 'coding'):
            from coding_integration import CodingTasks
            self.coding = CodingTasks(self)
        return self.coding

    def lost_runs(self):
        """Runs the host before this one left open, found by the restart pass: [{taskId, channelId, projectId, name}].
        Empty when nothing was lost, or when the coordinator isn't up."""
        found = getattr(getattr(self, 'coding', None), 'lost_runs', None) or []
        result = []
        for item in found:
            # Read records only. Polling Home mustn't recheck project files or revive settled restart notices.
            row = self.store.db.execute(
                "SELECT c.name,k.document FROM coding_tasks k JOIN threads t ON t.id=? "
                "LEFT JOIN local_channels c ON c.id=t.id JOIN profiles p ON p.id=t.profile_id "
                "WHERE k.id=? AND t.project_id=? AND " + ON_PROJECT_SIDE + " AND (t.visibility='shared' OR t.owner=?)",
                (item['thread_id'], item['task_id'], item['project_id'], ACTOR)).fetchone()
            if not row:
                continue
            task = json.loads(row['document'])
            # Beta.18: a run the record read settled as paused stays listed, marked ``paused``, so the relaunch still says
            # what happened to it; anything else the person dealt with drops out.
            last = (task.get('runs') or [None])[-1]
            paused = task.get('state') == 'blocked' and isinstance(last, dict) and last.get('end_cause') == 'restart_paused'
            if task.get('context') != dict(project_id=item['project_id'], thread_id=item['thread_id'], actor=ACTOR) or (task.get('state') != 'disconnected' and not paused):
                continue
            result.append({'taskId': item['task_id'], 'channelId': item['thread_id'], 'projectId': item['project_id'], 'name': row['name'] or 'Unnamed conversation',
                           **({'paused': True} if paused else {})})
        return result

    def tidy_threads(self):
        """Once per workspace: threads still named after their first message get short names, and shared threads in no
        workstream get filed, by the rules a new thread meets (conversation_organisation.tidy_once). Never stops a
        start: it's bounded to about two seconds a start, and any error at all (a store problem, a malformed document)
        is logged and the app opens as it is; the next start tries again."""
        try:
            return self.organisation().tidy_once(self.ideas())
        except Exception as exc:  # noqa: BLE001 - the app opening comes first; a tidy is a convenience
            print(f'Threads could not be tidied at start: {exc}', file=sys.stderr)
            return None

    def require_beta_access(self):
        if hasattr(self, 'account_session') and hasattr(self.account_session, 'require_access'):
            self.account_session.require_access()

    def require_run_access(self):
        """The check for a turn already under way. A free trial or plan that ends mid-turn lets that turn finish;
        only new sends are refused (pricing, 8 Oct). Anything else that fails the access check (signed
        out, removed, access not yet confirmed) still stops it at its next step."""
        try:
            self.require_beta_access()
        except Rejected:
            session = getattr(self, 'account_session', None)
            if not (hasattr(session, 'plan_lapsed') and session.plan_lapsed()):
                raise

    def organisation(self):
        if not hasattr(self, '_organisation'):
            from conversation_organisation import Organisation
            self._organisation = Organisation(self.store)
        return self._organisation

    def ideas(self):
        if not hasattr(self, '_ideas'):
            from project_ideas import ProjectIdeas
            self._ideas = ProjectIdeas(self.store, host=self)
        return self._ideas

    def stamp_builds(self):
        """Bugs and ideas done in a commit that a preview build now holds get "Fixed in <release>" (project_ideas
        .stamp_fixed_in). Reads the releases folder only. Never stops a start."""
        try:
            return self.ideas().stamp_fixed_in()
        except Exception as exc:  # noqa: BLE001 - the app opening comes first
            print(f'Builds could not be read for Bugs and ideas at start: {exc}', file=sys.stderr)
            return 0

    def pushes(self):
        """Push to GitHub (task_push.py): one worker and its records per host, created on first use."""
        if not hasattr(self, '_pushes'):
            from task_push import Pushes
            self._pushes = Pushes(self)
        return self._pushes

    def organise_submitted(self, binding, kind, source_id, text):
        # A metadata failure of any kind mustn't prevent a durably saved request from starting.
        try:
            organisation, ideas = self.organisation(), self.ideas()  # both set up their tables before the write below
            organisation.submitted(binding, kind, source_id, text, filer=ideas.file_by_rules)
        except Exception as exc:  # noqa: BLE001 - the request is saved; its name and group are a convenience
            print(f'Conversation organisation could not be saved: {exc}. Request submission continues.', file=sys.stderr)

    def thread_version(self):
        """The newest change number in the state spine (conversation_activity.bump), for the host's push line."""
        import sqlite3
        import conversation_activity
        try:
            return conversation_activity.version(self.store.db)
        except sqlite3.OperationalError:
            return 0  # no conversation has changed yet in this store

    def tick_wait(self):
        """Seconds serve() may wait for a request before tick() has work: BUSY_WAIT while anything is in flight or the last
        round changed a record (settling that takes more than one round finishes at the old pace), else until the
        soonest timed job (the beta access check, the usage send, a queued message waiting for a usage reset), at most
        IDLE_WAIT. Never raises: when it can't tell, it answers BUSY_WAIT, the old pace."""
        try:
            if getattr(self, 'tick_wrote', False) or self.in_flight():
                return BUSY_WAIT
            wait = IDLE_WAIT
            for due in self._timed():
                if due is not None:
                    wait = min(wait, due)
            return max(BUSY_WAIT, wait)
        except Exception:  # noqa: BLE001 - an unknown state keeps the old pace rather than stalling background work
            return BUSY_WAIT

    def in_flight(self):
        """What keeps the request loop at BUSY_WAIT, by name, or None. Everything tick() drives that has something running:
        a run's stream, its checks and tool calls, the review and acceptance settling and queue drain that follow it in
        the same pump; a sign-in; a connection or model check; a Home call; an automatic retry; a queued message; a
        relaunch's folder read; a coordination request; a push; a preview build; an open terminal (its output is read
        on the tick); an OpenRouter or update job. Cheap checks first; a database read only when nothing else is live."""
        if any(not turn.closed for turn in self.turns.values()):
            return 'chat turn'
        for login in self.logins.values():
            if not login.closed or (login.state == 'succeeded' and not login.identity_scheduled):
                return 'sign-in'
        if any(not job.done() for job in (*self.checks.values(), *self.catalogs.values())):
            return 'connection check'
        home = getattr(self, 'home_turns', None)
        if home is not None and any(not call.done() for call in list(home.running.values())):
            return 'Home call'
        for name, busy in (('terminals', lambda t: any(s.active for s in t.sessions.values())),
                           ('previews', lambda p: bool(p.processes)),
                           ('_pushes', lambda p: bool(p.futures)),
                           ('openrouter', lambda o: o.future is not None),
                           ('updates', lambda u: u.future is not None)):
            if hasattr(self, name) and busy(getattr(self, name)):
                return name.strip('_')
        coding = getattr(self, 'coding', None)
        if coding is not None:
            if coding.turns:
                return 'coding run'  # a closed run stays here until its checks are done (CodingTasks._tick_turn)
            if getattr(coding, 'warm_starting', None) or coding.warm.warming():
                return 'warm start'  # its handshake and connectors move only on a tick (warm_runs)
            if coding.conversation.pending_retries:
                return 'automatic retry'
            import threading
            if not coding.folder_reads.empty() or any(t.name == 'dervo-after-close' for t in threading.enumerate()):
                return 'folder read'
            if getattr(coding, 'recording', None):
                return 'recording'  # beta.18: a finished run's changes still being recorded on a thread
            if any(until is None for until in self._queued()):
                return 'queued message'
        harness = getattr(self, 'harness', None)
        if harness is not None and harness.supervisor is not None:
            from harness_host import TERMINAL, PENDING_DELEGATION
            for request in harness.requests():  # the same test HarnessCoordinator.tick makes
                if request['state'] not in TERMINAL or (request.get('harness_request_id') and any(
                        d.get('status') in PENDING_DELEGATION for d in request.get('delegations') or [])):
                    return 'coordination request'
        import project_orchestrator
        if project_orchestrator.waiting(self):
            return 'Home jobs'
        return None

    def _queued(self):
        """Each queued coding message's ``not_before`` (the account's usage reset it waits for), or None for one that
        starts as soon as its thread is free."""
        if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_queue'").fetchone() is None:
            return []
        return [row[0] for row in self.store.db.execute(
            "SELECT json_extract(document, '$.not_before') FROM coding_queue WHERE json_extract(document, '$.state') = 'queued'")]

    def _timed(self):
        """Seconds until each timed job tick() runs, when nothing is in flight."""
        account = getattr(self, 'account_session', None)
        if account is not None:
            from account_config import UnavailableAccount
            from dervo_account import AccountSession
            if hasattr(account, 'tick_due'):
                yield account.tick_due()
            elif isinstance(account, AccountSession):
                yield 1 if account.operation else None  # it polls its adapter every second while an operation is open
            elif not isinstance(account, UnavailableAccount):
                yield 1  # an account runtime that doesn't say when it has work: once a second
        usage = getattr(self, 'usage', None)
        if usage is not None and not usage.closed and (usage.queue or usage.errors):
            yield usage.last_flush + usage.FLUSH_SECONDS - usage.clock()
        if hasattr(self, 'coding'):
            now = time.time()
            for until in self._queued():
                yield until - now  # coding_queue.drain compares not_before with time.time()

    def tick(self):
        """One round of background work. ``tick_wrote`` says whether it changed the store (an idle round writes nothing)."""
        try:
            before = self.store.db.total_changes
        except Exception:  # noqa: BLE001 - a closed store; the round itself says what's wrong
            before = None
        try:
            self._tick()
        finally:
            try:
                self.tick_wrote = before is None or self.store.db.total_changes != before
            except Exception:  # noqa: BLE001
                self.tick_wrote = True

    def _tick(self):
        if hasattr(self, 'terminals'):
            self.terminals.tick()
        if hasattr(self, 'previews'):
            self.previews.tick()
        if hasattr(self, 'account_session'):
            self.account_session.tick()
        if hasattr(self, 'openrouter'):
            self.openrouter.tick()
        if hasattr(self, 'updates'):
            self.updates.tick()
        # Codex deadlines and cleanup keep running if the connection panel closes.
        for profile_id, login in list(self.logins.items()):
            if login.profile['provider'] == 'codex' and (not login.closed or
                    (login.state == 'succeeded' and not login.identity_scheduled)):
                try:
                    self._connection_login('connection_login_status', {'profileId': profile_id})
                except Rejected:
                    pass  # The login handler invalidates changed or unowned profiles.
        if hasattr(self, 'coding'):
            self.coding.tick()
        if hasattr(self, 'harness'):
            self.harness.tick()
        self.keep_awake_tick()
        if hasattr(self, 'cloud'):
            self.cloud.tick()  # a cloud run's launch or check settles, and Codex status is read, with no screen asking
        if hasattr(self, '_pushes'):
            self._pushes.tick()  # a finished push gets its record settled even when nobody polls push_status
        # Agents' answers and scheduled jobs settle with nobody looking. Lazy until a project has an agent: a store that
        # never made one has nothing to sweep.
        # The table lookup runs at most once a minute until a store has agents, not every tick.
        if not hasattr(self, 'dervo_agents') and time.monotonic() - getattr(self, '_agents_looked_at', -60.0) >= 60:
            self._agents_looked_at = time.monotonic()
            if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dervo_agents'").fetchone():
                self.agents()
        if hasattr(self, 'dervo_agents'):
            for part in (lambda: self.agents().tick(), lambda: self.assistant().tick()):
                try:
                    part()
                except Exception as exc:  # noqa: BLE001 - a job that can't run must never stop the host's round
                    print(f'Agents or schedules skipped a round: {type(exc).__name__}: {exc}', file=sys.stderr)
        for turn in list(self.turns.values()):
            turn.tick()
        # Home's answers, and the jobs they start, go on with Home off screen (16.4, item 7): one job a round.
        import project_orchestrator
        project_orchestrator.pump(self, ACTOR)
        try:
            if not hasattr(self, 'home_news'):
                from home_news import News
                self.home_news = News(self, ACTOR)
            self.home_news.tick()  # "the picker is done", "billing needs you" in Home as it happens
        except Exception as exc:  # noqa: BLE001 - Home's lines are a convenience; the round goes on
            print(f'Home skipped its live lines this round: {type(exc).__name__}: {exc}', file=sys.stderr)
        import thread_router
        thread_router.tick(self)  # the routing log written out for learn.py, only after an outcome changed it
        if hasattr(self, 'usage'):
            self.usage.tick()

    def keep_awake_on(self):
        """Settings, Preferences: keep the Mac awake while agents work. On unless turned off. Read once, then kept here."""
        if not hasattr(self, '_keep_awake_on'):
            import keep_awake
            self._keep_awake_on = keep_awake.enabled(self.store.db)
        return self._keep_awake_on

    def keep_awake_tick(self):
        """Hold the idle-sleep assertion while any agent run is live (keep_awake.py): a coding run, a chat turn or a
        coordinator helper. Never stops a tick."""
        try:
            coding = getattr(self, 'coding', None)
            active = self.keep_awake_on() and ((coding is not None and any(not turn.closed for turn in coding.turns.values()))
                or any(not turn.closed for turn in self.turns.values()) or getattr(getattr(self, 'harness', None), 'live', False))
            if active and not hasattr(self, 'keep_awake'):
                from keep_awake import KeepAwake
                self.keep_awake = KeepAwake()
            if hasattr(self, 'keep_awake'):
                self.keep_awake.update(active)
        except Exception as exc:  # noqa: BLE001 - staying awake is a convenience; the runs come first
            print(f'Keeping the Mac awake failed: {exc}', file=sys.stderr)

    def agents(self):
        """Agents you define and @mention (dervo_agents.Agents), created on first use."""
        if not hasattr(self, 'dervo_agents'):
            from dervo_agents import Agents
            self.dervo_agents = Agents(self)
        return self.dervo_agents

    def assistant(self):
        """Home's scheduled jobs and heartbeat (dervo_assistant.Assistant), created on first use."""
        if not hasattr(self, 'dervo_assistant'):
            from dervo_assistant import Assistant
            self.dervo_assistant = Assistant(self, self.agents())
        return self.dervo_assistant

    def _harness(self):
        """Harness coordinator, created on first use. Opening it never opens the Supervisor store."""
        if not hasattr(self, 'harness'):
            from harness_host import HarnessCoordinator
            self.harness = HarnessCoordinator(self, supervisor_factory=self.supervisor_factory, workers=self.harness_workers,
                                              claude_executable=self.harness_claude_executable, codex_executable=self.harness_codex_executable)
        return self.harness

    def close(self):
        if hasattr(self, 'keep_awake'):
            self.keep_awake.close()
        if hasattr(self, 'terminals'):
            self.terminals.close()
        if hasattr(self, 'usage'):
            self.usage.close()  # the last send starts now and is waited for, briefly, at the end
        try:
            import conversation_activity
            conversation_activity.mark_session(self.store.db, 'end')
        except Exception:
            pass  # closing never fails on the session note
        if hasattr(self, "feedback"):
            self.feedback.close()
        if hasattr(self, 'previews'):
            self.previews.close()
        if hasattr(self, 'cloud'):
            self.cloud.close()
        if hasattr(self, 'openrouter'):
            self.openrouter.close()
        if hasattr(self, 'updates'):
            self.updates.close()
        if hasattr(self, 'account_session'):
            self.account_session.close()
        if hasattr(self, 'coding'):
            self.coding.close()
        if hasattr(self, 'harness'):
            self.harness.close()
        if hasattr(self, '_pushes'):
            self._pushes.close()
        if hasattr(self, 'home_turns'):
            self.home_turns.close()
        for turn in self.turns.values():
            turn.stop()
            turn.close()
        for login in self.logins.values():
            login.cancel('Dervo closed before the sign-in finished. Connect again to retry.')
        if hasattr(self, '_signins'):
            for pending_id in list(self._signins.pending):
                self._signins._discard(pending_id)  # a sign-in that never became an account leaves no folder
        self.checker.shutdown(wait=True, cancel_futures=True)
        if hasattr(self, 'usage'):
            self.usage.settle()
        self.store.close()

    @property
    def keys(self):
        """API-key accounts: Keychain-backed for Claude, Codex's own store for Codex. Created on first use."""
        if not hasattr(self, '_keys'):
            from provider_keys import ProviderKeys
            self._keys = ProviderKeys(self)
        return self._keys

    def signins(self):
        """Beta.18 account adding by sign-in first, using another sign-in, and removing (account_signin.py)."""
        if not hasattr(self, '_signins'):
            from account_signin import AccountSignIns
            self._signins = AccountSignIns(self)
        return self._signins

    def _claude_version(self, args):
        """Connected accounts: the installed Claude Code and whether runs can use it (PLAN-native A7). ``recheck`` reads
        it again after you've run claude update."""
        import claude_version
        if type(args) is not dict or set(args) != claude_version.FIELDS['claude_version'] or type(args['recheck']) is not bool:
            raise Rejected('Unsupported operation or fields')
        return claude_version.status(fresh=args['recheck'])

    def _connection_login(self, operation, args):
        """Native Connect flow: explicit start, observable waiting, cancel, then the ordinary identity probe."""
        from connections_ui import owned_profile, probe
        # Beta.18: a sign-in that isn't an account yet (account_signin.py) uses this same flow, by its pending id.
        pending = self.signins().profile(args['profileId'])
        try:
            profile = pending or owned_profile(self.store, self.root, args['profileId'])
        except Rejected:
            existing = self.logins.get(args['profileId'])
            if existing and existing.profile['provider'] == 'codex':
                existing.cancel('Profile changed. Check the owned personal profile before retrying.')
                self.checks.pop(args['profileId'], None)
                if existing.closed: self.logins.pop(args['profileId'], None)
            raise
        if profile.get('method') == 'api_key':
            raise Rejected('This account uses an API key. There is no sign-in to run.')
        if profile['provider'] == 'codex':
            from codex_login import LoginSession, idle_status
            from codex_policy import executable
        else:
            from claude_login import LoginSession, idle_status
            from claude_policy import executable
        session = self.logins.get(profile['id'])
        if session and session.profile != profile and (profile['provider'] == 'codex' or session.profile['provider'] == 'codex'):
            session.cancel('Profile changed. Retry Connect with the expected personal identity.')
            self.checks.pop(profile['id'], None)
            if not session.closed:
                raise Rejected('Previous Codex sign-in cleanup is unconfirmed. Cancel again before retrying.')
            self.logins.pop(profile['id'], None)
            session = None
        was_active = session is not None and session.status().get('active')
        if operation == 'connection_login':
            if session is None or session.closed:
                if profile['provider'] == 'codex':
                    self.checks.pop(profile['id'], None)
                    self.catalogs.pop(profile['id'], None)
                    self.logins.pop(profile['id'], None)
                session = LoginSession(profile, executable())
                self.logins[profile['id']] = session
                was_active = session.status().get('active')
                # A new sign-in may change who is signed in, so the earlier identity observation no longer applies.
                # connection_status reports unchecked until the finished sign-in's own probe reports. Other profiles
                # keep their own entries: nothing here reads or writes another account.
                self.checks.pop(profile['id'], None)
        elif session is None:
            return {**idle_status(), 'expectedIdentity': profile['identity']}
        elif operation == 'connection_login_cancel':
            session.cancel()
        elif operation == 'connection_login_code':
            session.submit_code(args['code'])
        session.poll()
        result = session.status()
        if was_active and result['state'] in ('failed', 'cancelled', 'timed_out', 'unknown'):
            # Includes timeout cancellation inside poll(). Detach the future so late results stay obsolete.
            # Invalidate only on transition, allowing a later explicit identity retry to remain cached.
            self.checks.pop(profile['id'], None)
        if session.state == 'succeeded':
            if not session.identity_scheduled:
                session.identity_scheduled = True
                self.checks[profile['id']] = (self.checker.submit(probe, profile, session.executable_path)
                    if profile['provider'] == 'codex' else self.checker.submit(probe, profile))
            job = self.checks.get(profile['id'])
            if job is None or not job.done():
                result['identity'] = {'state':'checking', 'canSend':False, 'detail':'Checking the signed-in identity. No conversation is sent.'}
            else:
                try:
                    # A pending sign-in records nothing: it isn't an account, so its plan and limits have nowhere to go yet.
                    result['identity'] = self.signins().identity(profile, job.result()) if pending else self._checked(profile, job.result())
                except Exception:
                    result['identity'] = {'state':'unknown', 'canSend':False, 'detail':'Identity check failed. Check identity again.'}
        return result

    def _checked(self, profile, result):
        """An identity probe's result, with what it learned besides identity kept on the host: the account's rate-limit
        windows (provider_usage) and its Claude plan (provider_plans). Neither is part of the identity state returned."""
        if not isinstance(result, dict):
            return result
        if isinstance(result.get('rateLimits'), dict):
            import provider_usage
            provider_usage.record_rate_limits(self.store.db, profile['id'], result['rateLimits'])
        if 'plan' in result:
            import provider_plans
            provider_plans.record(self.store.db, profile['id'], {'subscriptionType': result['plan']})
        if (checked_org := result.get('org')) and result.get('state') == 'identity_verified' and profile.get('provider') == 'claude' \
                and profile.get('method', 'subscription') == 'subscription' and not profile.get('org'):
            # Beta.18: an account added before Dervo kept its Claude organisation takes the one its first good check
            # reports, so another organisation on the same email can be added beside it (account_signin.py).
            with self.store.db:
                self.store.db.execute('UPDATE profiles SET org=?, org_name=? WHERE id=? AND org IS NULL',
                                      (checked_org, result.get('orgName'), profile['id']))
        checked = {k: v for k, v in result.items() if k not in ('rateLimits', 'plan', 'seen', 'seenOk', 'seenPlan', 'seenDetail', 'org', 'orgName')}
        # A sign-in that came back as your account on the other side is said by name (b16-work suggest-add-same), so
        # "sign in with your work email" isn't a guess. Any other email is named only beside the offer to use it instead
        # (beta.18, below), and never in the line itself.
        seen = result.get('seen')
        if checked.get('state') == 'identity_mismatch' and isinstance(seen, str) and profile.get('category') in ('personal', 'work'):
            from dervo_connections import SAME_ORG
            org = result.get('org')
            other = self.store.db.execute("SELECT category FROM profiles WHERE provider=? AND method='subscription' AND lower(identity)=lower(?) AND category<>? "
                                          "AND removed_at IS NULL AND " + SAME_ORG, (profile['provider'], seen, profile['category'], org, org)).fetchone()
            if other:
                site = 'claude.ai' if profile['provider'] == 'claude' else 'chatgpt.com'
                checked.update(otherSide=other[0], signedInAs=seen, detail=(
                    f'That’s your {other[0]} account. {seen} already runs your {other[0].capitalize()} projects. '
                    f'Sign out of {site} in your browser, then sign in with your {profile["category"]} email. No messages were sent.'))
            elif result.get('seenOk') and profile.get('method', 'subscription') == 'subscription':
                # Beta.18: a valid sign-in on this side that isn't the email the account was added with (a typo, an alias,
                # the Google email). Named, so you can choose "Use this account instead" (account_use_signed_in) knowing
                # whose it is. Never used without that click, and never when another account here already is that email.
                import account_signin
                checked.update(account_signin.adoptable(self.store.db, profile, seen, result.get('seenPlan'), result.get('org')))
        return checked

    def add_picked_project(self, path, profile_id, model):
        key = identifier()
        self.store.add_project(key, path, profile_id, model)
        return {'id':key}

    def new_conversation(self, project, name, *, private=False, automatic=False, session_id=None):
        """The three inserts a conversation is (its thread on the project's account and model, its name, its
        organisation record), written with the caller's transaction: create_conversation opens one per conversation,
        the history import (history_import_host) one for a whole folder, so a read that fails partway leaves nothing.
        Store.add_thread commits internally, which is why it isn't used here. ``session_id`` is the provider session
        the thread carries from the start (the import's Claude Code session or Codex thread id), else none. Returns
        the new conversation's id."""
        key = identifier()
        self.store.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?)',
            (key, project['id'], key, project['profile_id'], project['model'],
             'private' if private else 'shared', ACTOR if private else None, session_id))
        self.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (key, name))
        self.organisation().initial(key, automatic=automatic)
        return key

    def dispatch(self, operation, args):
        """Every renderer request. The few that are usage events (usage_events.UsageEvents.observe) are noted after they
        succeed; noting never changes a result or raises."""
        from usage_events import FIELDS as USAGE_FIELDS
        if operation in USAGE_FIELDS:
            if not hasattr(self, 'usage'):
                raise Rejected('Usage sharing is unavailable in this session.')
            return self.usage.dispatch(operation, args)
        import thread_states
        moving = thread_states.before_operation(self.store.db, operation, args) if hasattr(self, 'store') else []
        result = Host._dispatch(self, operation, args)  # by the class, so tests that pass a stand-in host still route
        close_warm = getattr(getattr(self, 'coding', None), 'close_warm', None)  # a stand-in coding has none
        if operation in warm_runs.ENDS_HELD and close_warm is not None:
            close_warm(keep_starter=True)  # a held Claude Code never outlives its thread, project or access
        if hasattr(self, 'store'):
            if operation in ('create_conversation', 'create_agent_conversation') and isinstance(result, dict) and isinstance(result.get('id'), str):
                moving = [result['id']]
            thread_states.after_operation(self.store.db, moving)  # the rows it changed move (state spine)
        if hasattr(self, 'usage'):
            self.usage.observe(operation, args, result)
        return result

    def _dispatch(self, operation, args):
        from beta_feedback import FIELDS as FEEDBACK_FIELDS, Feedback
        if operation in FEEDBACK_FIELDS:
            if not hasattr(self, "feedback"):
                self.feedback = Feedback(self)
            return self.feedback.dispatch(operation, args)
        from beta_update_host import FIELDS as UPDATE_FIELDS, Updates
        if operation in UPDATE_FIELDS:
            if not hasattr(self, 'updates'):
                self.updates = Updates(self)
            return self.updates.dispatch(operation, args)
        from cloud_runs import FIELDS as CLOUD_FIELDS
        if operation in CLOUD_FIELDS:
            return self.cloud_runs().dispatch(operation, args)
        from app_icons import FIELDS as ICON_FIELDS, AppIcons
        if operation in ICON_FIELDS:
            if not hasattr(self, 'app_icons'):
                self.app_icons = AppIcons(self)
            return self.app_icons.dispatch(operation, args)
        from openrouter_host import FIELDS as OPENROUTER_FIELDS, Connections
        if operation in OPENROUTER_FIELDS:
            if not hasattr(self, 'openrouter'):
                self.openrouter = Connections(self)
            return self.openrouter.dispatch(operation, args)
        from workspace_drafts import FIELDS as DRAFT_FIELDS, dispatch as draft_dispatch
        if operation in DRAFT_FIELDS:
            return draft_dispatch(self, operation, args, ACTOR)
        if operation == "project_setup_save":
            from project_setup import save
            return save(self, args)
        from first_run import FIELDS as FIRST_RUN_FIELDS
        if operation in FIRST_RUN_FIELDS:
            from first_run import dispatch as first_run_dispatch
            return first_run_dispatch(self, operation, args)
        if operation == "_prepare_release_rollback":
            from release_history import prepare_rollback
            return prepare_rollback(self, args)
        if operation == "_prepare_release_switch":
            from release_switch import prepare
            return prepare(self, args)
        if operation == "_prepare_update":
            from app_update import prepare as prepare_update
            updates = getattr(self, 'updates', None)
            staged = updates.update if updates and updates.update and updates.update.get('source') == 'download' and updates.state == 'ready' else None
            return prepare_update(self, args, staged=staged)
        from beta_plan import FIELDS as PLAN_FIELDS
        if operation in PLAN_FIELDS:
            from beta_plan import dispatch as plan_dispatch
            return plan_dispatch(self, operation, args)
        from release_preview import FIELDS as PREVIEW_FIELDS, Previews
        if operation in PREVIEW_FIELDS:
            if not hasattr(self, 'previews'):
                self.previews = Previews(self)
            return self.previews.dispatch(operation, args)
        from provider_plans import FIELDS as PLAN_ACK_FIELDS
        if operation in PLAN_ACK_FIELDS:
            from provider_plans import dispatch as plan_ack_dispatch
            return plan_ack_dispatch(self, operation, args)
        from provider_keys import FIELDS as KEY_FIELDS
        if operation in KEY_FIELDS:
            return self.keys.dispatch(operation, args)
        from account_signin import FIELDS as SIGNIN_FIELDS
        if operation in SIGNIN_FIELDS:
            return self.signins().dispatch(operation, args)
        from profile_names import FIELDS as NAME_FIELDS, rename as rename_profile
        if operation in NAME_FIELDS:
            return rename_profile(self.store, args)
        from claude_version import FIELDS as VERSION_FIELDS
        if operation in VERSION_FIELDS:
            return self._claude_version(args)
        from project_lifecycle import FIELDS as PROJECT_FIELDS, dispatch as project_dispatch
        if operation in PROJECT_FIELDS:
            return project_dispatch(self, operation, args)
        from project_terminal import FIELDS as TERMINAL_FIELDS, Terminals
        if operation in TERMINAL_FIELDS:
            # Read and Close remain available after access is lost or a project
            # is archived. Opening and typing enforce their own access gates.
            if not hasattr(self, 'terminals'):
                self.terminals = Terminals(self)
            return self.terminals.dispatch(operation, args, ACTOR)
        if isinstance(args, dict) and type(args.get('projectId')) is str and self.store.db.execute(
                'SELECT 1 FROM project_archives WHERE project_id=?', (args['projectId'],)).fetchone():
            raise Rejected('This project is archived. Restore it before opening or changing its records.')
        # Beta.16's shared GitHub operations: shapes fixed by b16/bridge, handlers in b16/git's module. Each spec says
        # whether it works in an archived conversation, so it's checked there. (b16/lifecycle's go through
        # project_lifecycle.FIELDS above.)
        import shared_operations
        if operation in shared_operations.OPERATIONS:
            return shared_operations.dispatch(self, operation, args, ACTOR)
        # Stop, Mark as stopped, Don't run and Cancel stay available in an archived conversation, as Read and Close do for
        # a terminal: they only end or decline work there, never open or change it, and the lifecycle blockers offer them
        # for exactly that work (app #9, 23 Sep). Everything else waits for Restore.
        if isinstance(args, dict) and type(args.get('channelId')) is str and operation not in SETTLING and self.store.db.execute(
                'SELECT 1 FROM conversation_archives WHERE thread_id=?', (args['channelId'],)).fetchone():
            raise Rejected('This conversation is archived. Restore it before opening or changing it.')
        from project_files import FIELDS as FILE_FIELDS, dispatch as file_dispatch
        if operation in FILE_FIELDS:
            return file_dispatch(self, operation, args)
        # Beta.17: notes between threads and the review of finished subthreads (thread_notes). Passing one on starts a
        # run with the sender's access and no more; the rest only read or put a note away.
        from thread_notes import FIELDS as NOTE_FIELDS, dispatch as note_dispatch
        if operation in NOTE_FIELDS:
            return note_dispatch(self, operation, args, ACTOR)
        from image_attachments import FIELDS as IMAGE_FIELDS, dispatch as image_dispatch
        if operation in IMAGE_FIELDS:
            return image_dispatch(self, operation, args, ACTOR)
        from history_import_host import FIELDS as HISTORY_FIELDS, dispatch as history_dispatch
        if operation in HISTORY_FIELDS:
            return history_dispatch(self, operation, args)
        from onboarding_host import FIELDS as ONBOARDING_FIELDS, dispatch as onboarding_dispatch
        if operation in ONBOARDING_FIELDS:
            return onboarding_dispatch(self, operation, args)
        from outside_sessions import FIELDS as OUTSIDE_FIELDS, dispatch as outside_dispatch
        if operation in OUTSIDE_FIELDS:
            return outside_dispatch(self, operation, args, ACTOR)
        from dervo_account import AccountSession, FIELDS as ACCOUNT_FIELDS
        from supabase_account import FIELDS as SUPABASE_FIELDS
        if operation in ACCOUNT_FIELDS or operation in SUPABASE_FIELDS:
            if hasattr(self, 'feedback') and operation in (
                    'dervo_account_start', 'dervo_account_send_code', 'dervo_account_verify_code',
                    'dervo_account_cancel', 'dervo_account_restore', 'dervo_account_sign_out'):
                self.feedback.invalidate()
            if hasattr(self, 'updates') and operation in (
                    'dervo_account_start', 'dervo_account_send_code', 'dervo_account_verify_code',
                    'dervo_account_cancel', 'dervo_account_restore', 'dervo_account_sign_out'):
                self.updates.invalidate()
            if hasattr(self, 'openrouter') and operation in (
                    'dervo_account_start', 'dervo_account_send_code', 'dervo_account_verify_code',
                    'dervo_account_cancel', 'dervo_account_restore', 'dervo_account_sign_out'):
                self.openrouter.invalidate()
            if not hasattr(self, 'account_session'):
                self.account_session = AccountSession(self.store, self.dervo_account_adapter)
            return self.account_session.dispatch(operation, args)
        from conversation_organisation import FIELDS as ORGANISATION_FIELDS
        if operation in ORGANISATION_FIELDS:
            return self.organisation().dispatch(operation, args, ACTOR)
        from project_knowledge import ProjectKnowledge, FIELDS as KNOWLEDGE_FIELDS
        if operation in KNOWLEDGE_FIELDS:
            return ProjectKnowledge(self.store).dispatch(operation, args, ACTOR)
        from project_ideas import FIELDS as IDEA_FIELDS
        if operation in IDEA_FIELDS:
            return self.ideas().dispatch(operation, args, ACTOR)
        from task_push import FIELDS as PUSH_FIELDS
        if operation in PUSH_FIELDS:
            return self.pushes().dispatch(operation, args)
        from project_orchestrator import Orchestrator, FIELDS as ORCHESTRATOR_FIELDS
        if operation in ORCHESTRATOR_FIELDS:
            if not hasattr(self, 'home_turns'):
                from project_orchestrator import Turns
                self.home_turns = Turns()  # Home's model calls run here, off the request, and outlive it
            return Orchestrator(self.store, host=self).dispatch(operation, args, ACTOR)
        import pickup
        if operation in pickup.FIELDS:
            return pickup.dispatch(self, operation, args, ACTOR)
        import task_reminders  # reminders, due on Home and as a notification (beta.16.1); yours too since 9 Oct
        if operation in task_reminders.FIELDS:
            return task_reminders.dispatch(self, operation, args, ACTOR)
        import thread_fanout  # Home's Suggestions: agents' thread proposals nobody has started (9 Oct)
        if operation in thread_fanout.HOME_FIELDS:
            return thread_fanout.home_dispatch(self, operation, args, ACTOR)
        import run_history  # History: what each run did, from records only (run_history.py)
        if operation in run_history.FIELDS:
            return run_history.dispatch(self, operation, args, ACTOR)
        from project_skills import ProjectSkills, FIELDS as SKILL_FIELDS
        if operation in SKILL_FIELDS:
            return ProjectSkills(self.store).dispatch(operation, args, ACTOR)
        from workspace_search import FIELDS as SEARCH_FIELDS, dispatch as search_dispatch
        if operation in SEARCH_FIELDS:
            return search_dispatch(self, operation, args, ACTOR)
        import thread_folders  # beta.18: a thread's own branch and folder, its setup card and Merge
        if operation in thread_folders.FIELDS:
            return thread_folders.dispatch(self, operation, args, ACTOR)
        from coding_integration import FIELDS
        if operation in FIELDS:
            # Includes explicit human file restoration. Provider tools never dispatch renderer operations.
            return self.coding_tasks().dispatch(operation, args)
        from conversation_coding import FIELDS as CONVERSATION_FIELDS
        if operation in CONVERSATION_FIELDS:
            # Coding from the composer: the same CodingTasks lifecycle behind a one-time project permission.
            return self.coding_tasks().conversation.dispatch(operation, args)
        from collaboration import FIELDS as COLLAB_FIELDS
        if operation in COLLAB_FIELDS:
            # Named agents, the project policy and the packet records of the coding collaboration runtime.
            return self.coding_tasks().collaboration.dispatch(operation, args)
        import dervo_agents, dervo_assistant  # agents you @mention, and Home's schedules and heartbeat (8 Oct)
        if operation in dervo_agents.FIELDS:
            return self.agents().dispatch(operation, args, ACTOR)
        if operation in dervo_assistant.FIELDS:
            return self.assistant().dispatch(operation, args, ACTOR)
        from harness_host import FIELDS as HARNESS_FIELDS, OPTIONAL_FIELDS
        if operation in HARNESS_FIELDS:
            required, optional = HARNESS_FIELDS[operation], OPTIONAL_FIELDS.get(operation, set())
            if not isinstance(args, dict) or not (required <= set(args) <= required | optional):
                raise Rejected('Unsupported operation or fields')
            return self._harness().dispatch(operation, args)
        fields = {
            'status': set(), 'profiles': set(), 'projects': set(),
            'add_profile': {'provider','identity','category'},
            'create_conversation': {'projectId','name','private'},
            'create_agent_conversation': {'projectId','profileId','model','name'},
            'conversations': {'projectId'}, 'shared_context': {'projectId'},
            'thread_states': {'since'}, 'keep_awake_status': set(), 'keep_awake_set': {'enabled'}, 'thread_order': {'projectId'}, 'routing_outcome': {'projectId', 'decisionId', 'outcome'},
            'binding': {'projectId','channelId'}, 'history': {'projectId','channelId'},
            'conversation_status': {'projectId','channelId'},
            'conversation_seen': {'projectId','channelId'},
            'conversation_dismiss': {'projectId','channelId'},
            'usage_summary': set(), 'session_summary': set(),
            # Design sweep, surfaces (19 Sep): the project picker's per-project counts.
            'projects_activity': set(),
            'conversation_feed': {'projectId','channelId','cursor','force'},
            # 16.4: the page of a thread's history and tasks before the ones its feed carries.
            'conversation_older': {'projectId','channelId','before'},
            'queue': {'projectId','channelId','clientId','text','parentId'},
            'cancel': {'projectId','channelId','messageId'},
        }
        for op in ('start_turn', 'stop_turn', 'turn_status', 'reconcile_turn'):
            fields[op] = {'projectId','channelId','messageId'}
        for op in ('connection_instructions', 'connection_check', 'connection_status', 'model_check', 'model_status',
                   'connection_login', 'connection_login_status', 'connection_login_cancel'):
            fields[op] = {'profileId'}
        fields['connection_login_code'] = {'profileId', 'code'}
        if operation == 'create_conversation' and isinstance(args, dict) and 'automaticName' in args:
            fields[operation] = fields[operation] | {'automaticName'}
        if operation not in fields or not isinstance(args,dict) or set(args) != fields[operation]:
            raise Rejected('Unsupported operation or fields')
        if operation == 'conversation_feed':
            from conversation_feed import ConversationFeed
            if not hasattr(self, 'conversation_feed'):
                self.conversation_feed = ConversationFeed(self)
            return self.conversation_feed.read(args, ACTOR)
        if operation == 'conversation_older':
            from conversation_feed import ConversationFeed
            if not hasattr(self, 'conversation_feed'):
                self.conversation_feed = ConversationFeed(self)
            return self.conversation_feed.older(args, ACTOR)
        if operation.startswith('connection_login'):
            return self._connection_login(operation, args)
        if operation in ('start_turn', 'stop_turn', 'turn_status', 'reconcile_turn'):
            from connections_ui import owned_profile
            from codex_live import CodexLiveTurn
            binding = self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            mid = args['messageId']
            saved = self.store.turn(mid)
            if saved['thread_id'] != args['channelId']:
                raise Rejected('Message belongs to another conversation')
            job = self.turns.get(mid)
            if operation == 'start_turn':
                if job and not job.closed:
                    return job.status()
                self.require_beta_access()
                if saved['state'] != 'queued':
                    raise Rejected('Only an explicitly selected queued message can start')
                if any(not j.closed and j.binding['id']==binding['id'] for j in self.turns.values()):
                    raise Rejected('This conversation already has an active request')
                if hasattr(self, 'coding') and self.coding.busy_thread(binding['id']):
                    # One writer per conversation: a coding task here (its own, or a collaboration helper's) holds the conversation.
                    raise Rejected('This conversation has an active coding task. Wait for it to finish or stop it first.')
                if self.store.db.execute("SELECT 1 FROM turns WHERE thread_id=? AND state='uncertain'",(binding['id'],)).fetchone():
                    raise Rejected('Check interrupted delivery before sending another message')
                owned_profile(self.store,self.root,binding['profile_id'],context=binding.get('context'))  # its project's side only
                job = CodexLiveTurn(self.store,binding,mid,factory=self.transport_factory,access_check=self.require_beta_access)
                self.turns[mid] = job
            elif operation == 'reconcile_turn':
                if job and not job.closed: return job.status()
                self.require_beta_access()
                if saved['state']!='uncertain' or not binding['session_id']:
                    raise Rejected('Only uncertain delivery with a saved provider thread can be checked')
                owned_profile(self.store,self.root,binding['profile_id'],context=binding.get('context'))
                job=CodexLiveTurn(self.store,binding,mid,factory=self.transport_factory,recovery=True,access_check=self.require_beta_access)
                self.turns[mid]=job
            elif operation == 'stop_turn' and job:
                job.stop()
            return job.status() if job else {'active':False,'state':saved['state'],'error':saved['error']}
        if operation in ('connection_check', 'connection_status'):
            from connections_ui import owned_profile, probe
            if args['profileId'] in self.logins:
                login = self._connection_login('connection_login_status', args)
                if login['active'] and operation == 'connection_check':
                    raise Rejected('Wait for sign-in to finish or cancel it before checking identity.')
            profile = owned_profile(self.store, self.root, args['profileId'])
            job = self.checks.get(profile['id'])
            if operation == 'connection_check' and (job is None or job.done()):
                if profile['provider'] == 'codex':
                    from codex_policy import executable
                    job = self.checker.submit(probe, profile, executable())
                else:
                    # An API-key account's key is read here, on the host thread, and handed to this one probe.
                    job = self.checker.submit(probe, profile, api_key=self.keys.key_for({**profile, 'profile_id': profile['id']}))
                self.checks[profile['id']] = job
            if job is None:
                return {'state':'unchecked', 'canSend':False, 'detail':'Check the selected account before use.'}
            if not job.done():
                return {'state':'checking', 'canSend':False, 'detail':'Checking provider identity. No conversation is sent.'}
            try:
                result = job.result()
            except Exception:
                return {'state':'unknown', 'canSend':False, 'detail':'Identity check failed. No conversation was sent. Try checking again.'}
            return self._checked(profile, result)
        if operation in ('model_check','model_status'):
            from connections_ui import owned_profile, probe_catalog
            profile=owned_profile(self.store,self.root,args['profileId'])
            if profile['provider'] == 'codex' and profile['id'] in self.logins:
                login = self._connection_login('connection_login_status', args)
                if login['active']:
                    raise Rejected('Wait for sign-in to finish or cancel it before checking models.')
            job=self.catalogs.get(profile['id'])
            if operation=='model_check' and (job is None or job.done()):
                job=self.checker.submit(probe_catalog,profile,self.transport_factory)
                self.catalogs[profile['id']]=job
            if job is None:return {'state':'unchecked','models':[],'canSend':False,'detail':'Load the signed-in Codex catalog.'}
            if not job.done():return {'state':'checking','models':[],'canSend':False,'detail':'Checking identity and model catalog. Nothing is sent.'}
            try:return job.result()
            except Exception as exc:return {'state':'blocked','models':[],'canSend':False,'detail':str(exc)}
        if operation == 'connection_instructions':
            from connections_ui import owned_profile, instructions
            return instructions(owned_profile(self.store, self.root, args['profileId']))
        if operation == 'shared_context':
            return self.store.shared_context(args['projectId'])
        if operation == 'cancel':
            self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            turn = self.store.turn(args['messageId'])
            if turn['thread_id'] != args['channelId'] or turn['state'] not in ('queued','cancelled'):
                raise Rejected('Only a queued message in this conversation can be cancelled')
            with self.store.db:
                if turn['state'] == 'queued':
                    self.store.db.execute("UPDATE turns SET state='cancelled' WHERE id=?",(args['messageId'],))
                    self.store.db.execute("INSERT INTO events(turn_id,kind,data) VALUES (?,'cancelled','{}')",(args['messageId'],))
            return {'state':'cancelled'}
        if operation == 'conversation_status':
            binding = self.store.binding(args['channelId'], args['projectId'], actor=ACTOR)
            result = self._conversation_status(binding, args)
            result['harness'] = self._harness().conversation(binding)  # what the composer may coordinate here, or why not
            result['coding'] = self.coding.conversation.permission(binding)  # whether a message here may start coding, or what is missing
            result['collaboration'] = self.coding.collaboration.policy_view(binding)  # which agents coding here may ask, or why none
            return result
        return self._dispatch_rest(operation, args)

    def _conversation_status(self, binding, args):
        if True:
            tasks = self.coding_tasks().dispatch('tasks', args)
            latest_turn = self.store.db.execute('''SELECT t.id,t.state,a.phase,a.activity,p.created_at
                FROM turns t LEFT JOIN message_activity a ON a.message_id=t.id
                LEFT JOIN local_projection p ON p.id=t.id
                WHERE t.thread_id=? ORDER BY t.rowid DESC LIMIT 1''', (binding['id'],)).fetchone()
            uncertain = self.store.db.execute("SELECT 1 FROM turns WHERE thread_id=? AND state='uncertain'", (binding['id'],)).fetchone()
            from collaboration import REVIEW_OPEN
            from task_activity import legacy_time
            def task_order(task):
                saved = self.store.db.execute('SELECT phase,activity FROM task_activity WHERE task_id=?', (task['id'],)).fetchone()
                return tuple(saved) if saved else (0, legacy_time(task))
            def needs_attention(task):
                return (task['state'] in ('working', 'disconnected')
                        or ((task.get('collaboration') or {}).get('review') or {}).get('phase') in REVIEW_OPEN
                        or any(run.get('state') == 'uncertain' for run in task.get('runs', []))
                        or any(token.get('state') == 'uncertain' for token in task.get('tokens', [])))
            # Running work and unresolved delivery retain precedence over settled history.
            task = max(tasks, key=lambda t: (needs_attention(t), task_order(t), tasks.index(t))) if tasks else None
            raw_order = ((latest_turn['phase'], latest_turn['activity']) if latest_turn and latest_turn['phase'] is not None
                         else (0, (latest_turn['created_at'] or 0) if latest_turn else 0))
            prefer_raw = task is not None and not needs_attention(task) and (
                uncertain or (latest_turn is not None and raw_order > task_order(task)))
            if task and not prefer_raw and not any(not j.closed for j in self.turns.values() if j.binding['id'] == binding['id']):
                review_phase = ((task.get('collaboration') or {}).get('review') or {}).get('phase')
                from task_outcome import outcome_facts, review_line
                facts = outcome_facts(task)
                runs = task.get('runs') or []
                interrupted = (task['state'] == 'blocked' and bool(runs) and runs[-1].get('state') == 'interrupted'
                               and not any(run.get('state') == 'uncertain' for run in runs)
                               and not any(token.get('state') == 'uncertain' for token in task.get('tokens', [])))
                return {'state':task['state'], 'active':task['state'] == 'working', **facts,
                        # Truthful distinctions for the indicator: agents still reviewing, a result approval is not asked for, a stale completion.
                        'review_active': review_phase in REVIEW_OPEN, 'review_phase': review_phase, 'interrupted': interrupted,
                        'acceptance_required': (task.get('acceptance') or {}).get('required'),
                        'stale_completion': bool((task.get('completion') or {}).get('stale')) and task['state'] == 'ready_for_review',
                        'mode':'Coding in this project' if task.get('origin') == 'conversation' else 'Task scope pending' if task['state'] == 'needs_permission' else 'Approved task scope' if task['runs'] else 'Task execution denied',
                        # A result waiting on you reads the same everywhere. When it carries an error (files changed after
                        # your review, so the approval no longer holds), the error is the status and wins.
                        'detail':(review_line(task) if task['state'] == 'ready_for_review' and not task['error'] and not facts['outcome_uncertain'] and review_phase not in REVIEW_OPEN else None)
                                 or task['error'] or ('Dervo lost confirmation of a run or tool result. Inspect the saved activity before retrying.' if facts['outcome_uncertain'] else 'The latest run ended before completion. Read any saved output before continuing.' if interrupted else
                                                   'Answered in text. No project file was changed.' if task['state'] == 'answered' else
                                                   'Completed. The provider finished, the host checks passed and no review is open. Not marked as approved by you.' if task['state'] == 'completed' else
                                                   'Inspect task scope, file evidence and human review in the conversation.')}
            jobs = [j for j in self.turns.values() if j.binding['id'] == binding['id']]
            active = next((j for j in jobs if not j.closed), None)
            if active:
                return {'state':'working' if active.begun else 'checking', 'active':True,
                        'detail':'Stopping request.' if active.adapter.state == 'stopping' else
                        'Receiving a chat response.' if active.begun else 'Checking identity, model and chat-only permissions.'}
            if uncertain:
                return {'state':'unknown', 'active':False, 'detail':'Delivery is unknown. Check delivery before sending again.'}
            if latest_turn and latest_turn['state'] == 'interrupted':
                return {'state':'idle', 'active':False, 'interrupted':True,
                        'detail':'Response stopped. Saved text is retained; the response did not finish.'}
            if jobs:
                last = jobs[-1]
                if last.error:
                    return {'state':'blocked', 'active':False, 'detail':last.error}
                return {'state':'idle', 'active':False, 'detail':'Request finished. Identity and permissions are checked again on every send.'}
            # A new Claude project can use the read-only task broker even though legacy chat is unavailable.
            # Recorded tasks, active requests and uncertain delivery above retain their actual status.
            if binding['provider'] == 'claude':
                from project_reading import availability
                if availability(self.coding, binding)['state'] == 'available':
                    return {'state':'unchecked', 'active':False,
                            'detail':'Project questions use read-only access. Account, model and permissions are checked when you send.'}
            return {'state':'unchecked' if binding['provider'] == 'codex' else 'blocked', 'active':False,
                    'detail':'Identity, model and permissions will be checked on send. Saved queues stay local.' if binding['provider'] == 'codex' else BLOCKERS['claude']}

    def _dispatch_rest(self, operation, args):
        if operation == 'status':
            from codex_runtime import VERSION as codex_version  # the Codex Dervo ships, for the caption on Connected accounts
            lost = self.lost_runs()
            return {'actor':ACTOR, 'codexVersion':codex_version, 'liveEnabled':any(not j.closed and j.begun for j in self.turns.values()), 'relayConnected':False, 'blockers':BLOCKERS,
                    'harness':self._harness().availability(),
                    # Runs the host before this one lost (its restart pass marked them disconnected), and the one line
                    # the app shows about it. Empty and None on an ordinary start.
                    'lostRuns':lost, 'lostRunsDetail':lost_runs_line(lost),
                    'permissions':'Codex text turns use a read-only sandbox with tool network access disabled. Shell, browser, apps, MCP and hooks are disabled. Permission requests are denied. Read-only does not confine reads to this folder. Claude chat sending remains blocked. Claude coding tasks run unmodified Claude Code with built-in tools, settings, skills and external MCP servers disabled; only Dervo host tools are exposed.'}
        if operation == 'add_profile':
            key = identifier()
            self.store.add_profile(key,args['provider'],self.root/'profiles'/key,args['identity'],args['category'])
            return {'id':key}
        if operation == 'profiles':
            import provider_plans  # each account's plan and whether Fable's paid credits were confirmed, for the model picker (A4)
            # ``name`` is the name you gave the account on Connected accounts; ``plan`` comes from provider_plans, the one record of it.
            return provider_plans.annotate(self.store.db, [dict(r) for r in self.store.db.execute('SELECT id,provider,identity,category,method,monthly_limit_usd,name FROM profiles WHERE removed_at IS NULL')])
        if operation == 'projects':
            return [dict(r) for r in self.store.db.execute('SELECT p.*, n.name FROM projects p LEFT JOIN project_names n ON n.project_id=p.id WHERE p.id NOT IN (SELECT project_id FROM project_archives)')]
        if operation == 'create_conversation':
            key = identifier()
            automatic = args.get('automaticName', False)
            name = args['name'].strip() if type(args['name']) is str else ''
            if type(automatic) is not bool or (automatic and name):
                raise Rejected('Automatic naming needs an empty name and explicit choice')
            if automatic:
                name = 'New conversation'
            if not name or len(name)>100 or type(args['private']) is not bool:
                raise Rejected('Choose a name and explicit conversation visibility')
            # Schema setup uses executescript, so it must precede the creation transaction.
            self.organisation()
            with self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                project = self.store.db.execute('SELECT * FROM projects WHERE id=?', (args['projectId'],)).fetchone()
                if not project:
                    raise Rejected('Unknown project')
                key = self.new_conversation(project, name, private=args['private'], automatic=automatic)
            return {'id':key}
        if operation == 'create_agent_conversation':
            # One atomic step for a named agent's channel: a shared conversation of this project bound to an explicit owned
            # personal account and model. The project's own binding, every existing conversation and every credential stay
            # untouched; nothing is granted (the collaboration policy is separate and off by default).
            from connections_ui import owned_profile
            name = args['name'].strip() if type(args['name']) is str else ''
            model = args['model'].strip() if type(args['model']) is str else ''
            if not name or len(name) > 100:
                raise Rejected('Choose a conversation name up to 100 characters')
            if not model:
                raise Rejected("Name the exact model the agent's conversation runs")
            if type(args['profileId']) is not str:
                raise Rejected('Choose the explicit personal account by id')
            from dervo_connections import project_context
            # Unknown, moved or other-side accounts are refused here: a Work project's agent runs on a work account only.
            profile = owned_profile(self.store, self.root, args['profileId'], context=project_context(self.store.db, args['projectId']))
            key = identifier()
            with self.store.db:
                self.store.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,NULL)', (key, args['projectId'], key, profile['id'], model, 'shared', None))
                self.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (key, name))
            return {'id':key, 'name':name, 'visibility':'shared', 'project_id':args['projectId'], 'profile_id':profile['id'], 'provider':profile['provider'], 'model':model}
        if operation == 'thread_order':
            # The project's threads in display order with each one's bucket (thread_router.sort): the router's order when
            # it has one, otherwise needs you, working, finished and unseen, idle, archived.
            import thread_router
            if type(args['projectId']) is not str or not self.store.db.execute('SELECT 1 FROM projects WHERE id=?', (args['projectId'],)).fetchone():
                raise Rejected('Choose a project.')
            db = self.store.db  # shared threads on the project's own side only: never a private or other-side one
            return thread_router.sort(thread_router.threads(db, args['projectId'], ACTOR, thread_router.side_threads(db, args['projectId'])))
        if operation == 'routing_outcome':
            # What you did about one of the router's decisions (moved a message, say), for its evaluation set.
            import thread_router
            if type(args['decisionId']) is not str or not self.store.db.execute('SELECT 1 FROM projects WHERE id=?', (args['projectId'],)).fetchone():
                raise Rejected('Choose a routing decision.')
            try:
                return {'recorded': thread_router.outcome(self.store.db, args['decisionId'], args['outcome'], args['projectId'])}
            except ValueError as exc:
                raise Rejected(str(exc)) from None
        if operation in ('keep_awake_status', 'keep_awake_set'):
            import keep_awake
            return keep_awake.dispatch(self, operation, args)
        if operation == 'thread_states':
            # The state spine: every conversation whose row moved after ``since``, across projects (thread_states.py).
            import thread_states
            coding = getattr(self, 'coding', None)
            return thread_states.changes(self.store.db, ACTOR, args['since'], alive=getattr(coding, 'alive', None))
        if operation == 'conversations':
            self.organisation()  # Ensure metadata exists for older stores without scanning conversation text.
            # ``origin`` is 'starter' or 'overview' for the threads the host made with the project, null for yours
            # (conversation_origins, project_lifecycle.schema). The sidebar ignores it; the blank-project check reads it.
            rows=self.store.db.execute('''SELECT t.id,c.name,t.visibility,t.project_id,t.profile_id,t.model,g.origin,('''+ON_PROJECT_SIDE+''') AS on_side,o.document FROM threads t
                LEFT JOIN local_channels c ON c.id=t.id JOIN profiles p ON p.id=t.profile_id
                LEFT JOIN conversation_organisation o ON o.id=t.id LEFT JOIN conversation_origins g ON g.thread_id=t.id
                WHERE t.project_id=? AND (t.visibility='shared' OR t.owner=?) AND t.id NOT IN (SELECT thread_id FROM conversation_archives) ORDER BY t.rowid''',(args['projectId'],ACTOR))
            result=[]
            for row in rows:
                item=dict(row);document=item.pop('document');on_side=item.pop('on_side')
                group=None
                # Filed threads show their workstream on either side, Work or Personal, when the account is the project's side.
                if document and item['visibility']=='shared' and on_side:
                    try: group=json.loads(document).get('group')
                    except (ValueError,AttributeError): pass
                item['organisationGroup']=group if isinstance(group,str) and group.strip() and len(group)<=60 else None
                result.append(item)
            import conversation_activity
            conversation_activity.schema(self.store.db)
            conversation_activity.backfill(self.store.db)
            activity=conversation_activity.summaries(self.store.db,[item['id'] for item in result])
            for item in result:
                item['activity']=activity.get(item['id'])
            import thread_notes
            thread_notes.annotate_conversations(self.store.db,args['projectId'],result)  # parentId, notesWaiting (beta.17)
            return result
        if operation == 'projects_activity':
            import conversation_activity
            conversation_activity.schema(self.store.db)
            conversation_activity.backfill(self.store.db)
            return conversation_activity.project_counts(self.store.db, ACTOR)
        if operation == 'session_summary':
            import conversation_activity
            conversation_activity.schema(self.store.db)
            conversation_activity.backfill(self.store.db)
            return conversation_activity.session_summary(self.store.db, ACTOR)
        if operation == 'usage_summary':
            import provider_usage
            provider_usage.schema(self.store.db)
            return provider_usage.summary(self.store.db)
        if operation == 'conversation_seen':
            import conversation_activity
            self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            conversation_activity.schema(self.store.db)
            conversation_activity.mark_seen(self.store.db,args['channelId'])
            return {'id':args['channelId']}
        if operation == 'conversation_dismiss':
            import conversation_activity
            self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            conversation_activity.schema(self.store.db)
            conversation_activity.dismiss(self.store.db,args['channelId'])
            return {'id':args['channelId']}
        if operation == 'binding':
            binding = self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            return {k:v for k,v in binding.items() if k != 'home'}
        if operation == 'queue':
            self.store.binding(args['channelId'],args['projectId'],actor=ACTOR)
            parent = args.get('parentId')
            if parent and not self.store.db.execute('SELECT 1 FROM messages WHERE id=? AND thread_id=?',(parent,args['channelId'])).fetchone():
                raise Rejected('Reply parent belongs to another conversation')
            existing = self.store.db.execute('SELECT p.parent_id FROM messages m JOIN local_projection p ON p.id=m.id WHERE m.thread_id=? AND m.client_id=?',(args['channelId'],args['clientId'])).fetchone()
            if existing and existing['parent_id'] != parent:
                raise Rejected('Request ID was already used for a different reply')
            # Keep message, outbox turn and timeline metadata in one transaction.
            with self.store.db:
                old = self.store.db.execute('SELECT * FROM messages WHERE thread_id=? AND client_id=?',(args['channelId'],args['clientId'])).fetchone()
                if old:
                    if old['text'] != args['text'] or old['actor'] != ACTOR:
                        raise Rejected('Request ID was already used for different content')
                    mid = old['id']
                else:
                    if not args['clientId'] or not args['text'].strip() or len(args['text'].encode())>65536:
                        raise Rejected('Queue needs a request ID and text up to 64 KiB')
                    mid = identifier()
                    self.store.db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text) VALUES (?,?,?,?,?)',(mid,args['channelId'],args['clientId'],ACTOR,args['text']))
                    self.store.db.execute("INSERT INTO turns(id,thread_id,state) VALUES (?,?,'queued')",(mid,args['channelId']))
                    self.store.db.execute('INSERT INTO local_projection VALUES (?,?,?)',(mid,parent,int(time.time())))
            binding = self.store.binding(args['channelId'], args['projectId'], actor=ACTOR)
            self.organise_submitted(binding, 'chat', mid, args['text'])
            return {'id':mid,'state':self.store.turn(mid)['state']}
        if operation == 'history':
            messages = self.store.messages(args['channelId'],args['projectId'],actor=ACTOR)
            result = []
            for message in messages:
                projection = self.store.db.execute('SELECT * FROM local_projection WHERE id=?',(message['id'],)).fetchone()
                if not projection and message['role']=='assistant':
                    origin=message['client_id'].split(':',1)[0]
                    projection=self.store.db.execute('SELECT * FROM local_projection WHERE id=?',(origin,)).fetchone()
                tags = [['h',args['channelId']]]
                if projection and projection['parent_id']:
                    parent = projection['parent_id']
                    root = parent
                    while True:
                        ancestor = self.store.db.execute('SELECT parent_id FROM local_projection WHERE id=?',(root,)).fetchone()
                        if not ancestor or not ancestor['parent_id']: break
                        root = ancestor['parent_id']
                    tags += [['e',root,'','root'],['e',parent,'','reply']]
                author = 'dervo-coordinator' if message['actor']=='dervo-history' else message['actor']
                result.append({'id':message['id'],'content':message['text'],'pubkey':author,'kind':9,'sig':'',
                    'created_at':projection['created_at'] if projection else 0,
                    'tags':tags,'state':self.store.turn(message['id'])['state'] if message['role']=='user' else 'answer'})
            for message in messages:
                if message['role']!='user': continue
                turn=self.store.turn(message['id'])
                if turn['state'] not in ('sending','running','stopping'): continue
                source=next(row for row in result if row['id']==message['id'])
                for item in self.store.items(message['id']):
                    result.append({**source,'id':message['id']+':'+item['item_id'],
                        'pubkey':'provider','content':item['text'],'state':'streaming'})
            return result
        raise Rejected('Unsupported local operation')

if __name__ == '__main__':
    # Close-on-exec before any provider or background subprocess can start.
    import native_keychain
    native_keychain.peer()
    try:
        import platform_os
        host = Host(sys.argv[1], worktrees_root=platform_os.worktrees_dir())
    except Rejected as failure:
        # Answer the native startup request before exiting so the actual reason reaches the window.
        if sys.stdin.readline():
            print(json.dumps({'error': str(failure)}), flush=True)
        raise SystemExit(1)
    import error_reports
    # Error reports (error_reports.py) ride on usage sharing: this process's errors go to its UsageEvents, only here, so a
    # test's Host never collects them.
    if hasattr(host, 'usage'):
        error_reports.install(host.usage)
    try:
        serve(host)
    finally:
        host.close()
        error_reports.install(None)
