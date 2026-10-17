"""Bounded execution layer: durable requests, runs and sessions over the harness, driven by worker events.

The runtime owns no model and no process. It talks to one replaceable :class:`WorkerPort`
(which may wrap a :class:`ModelPort`) and does five things: submit, status, steer, cancel,
reconcile. Every side effect a worker produces goes through :class:`ScopedActions`, which
re-checks on each call that the run is still the live attempt owned by this runtime and that
the target room is inside the worker's execution grant, and then calls the harness as the
worker's ``agent`` principal so every access and transfer rule applies unchanged.

Ownership and recovery rules:

- One owner per active work scope: a second request in the same ``(room, scope)`` is refused
  while a run there is running, unknown or cancelling. An optional ``resource`` key is exclusive
  across every room and project. Resubmitting the same ``request_key`` with the same goal, worker,
  scope and resource returns the existing request; the same key with any of those changed is refused.
- Events carry ``(run_id, attempt, event_seq)``. Anything for an unknown run, a stale attempt, a
  run that is neither running nor cancelling, a run owned by another runtime, or a repeated sequence
  number is rejected and logged, never applied.
- Cancel is a request, not a fact. :meth:`Runtime.cancel` moves the run to ``cancelling``, which
  blocks scoped actions, steering and overlapping runs while keeping ownership of the scope, and asks
  the port to stop the worker. The run becomes ``cancelled`` only when the port confirms termination:
  a ``finished`` observation from ``cancel`` or ``attach``, or a terminal worker event (``stopped``,
  ``failed`` or a discarded late ``result``). A stop the port cannot perform leaves the run
  ``cancelling``; calling cancel again retries.
- A new runtime marks runs left ``running`` by a previous runtime as ``unknown``. Nothing happens
  to them until a human calls :meth:`Runtime.reconcile`, which asks the worker port. A worker the
  port positively reports missing becomes ``lost``; it is never replayed automatically, only by an
  explicit :meth:`Runtime.resubmit`. A port that raises during ``attach`` proves nothing, so the run
  keeps its status and the failure is logged.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Set

from .errors import AccessDenied, NotFound, StateError, ValidationError
from .harness import Harness, parse_ref
from .models import (
    CALLER_AGENT, CALLER_HUMAN, EVENT_FAILED, EVENT_PROGRESS, EVENT_RESULT, EVENT_STOPPED, HANDOFF_OPEN, KIND_RUN,
    KIND_RUN_RESULT, REQUEST_ACTIVE, REQUEST_CLOSED, ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER, RUN_ACTIVE, RUN_CANCELLED,
    RUN_CANCELLING, RUN_FAILED, RUN_LOST, RUN_RUNNING, RUN_SUCCEEDED, RUN_UNKNOWN, WORKER_EVENT_KINDS,
    WORKER_TERMINAL_EVENT_KINDS, Caller, Decision, EventOutcome, ExecutionGrant, HandoffPacket, Message, Request, Run,
    RunEvent, RunResult, Session, SkillVersion, WorkAssignment, WorkerEvent, WorkerObservation,
)
from .store import dumps, loads, new_id, now_iso
from .upstream_adapted import strip_invisible_unicode

RESULT_SUMMARY_MAX_CHARS = 4000


class ModelPort(Protocol):
    """Raw text in, raw text out. The runtime never calls this; a worker may."""

    name: str
    is_fake: bool

    def respond(self, prompt: str) -> str: ...


class WorkerPort(Protocol):
    """Something that executes a :class:`WorkAssignment` and reports :class:`WorkerEvent` s."""

    name: str
    is_fake: bool
    model_name: str

    def start(self, assignment: WorkAssignment, actions: "ScopedActions") -> str:
        """Begin work; return an opaque ``worker_ref`` the port can find again later."""

    def drain(self, worker_ref: str) -> List[WorkerEvent]:
        """Return events produced since the last drain (a fake may do one unit of work here)."""

    def steer(self, worker_ref: str, note: str) -> None:
        """Deliver a note to a running worker, or raise if this port cannot (the runtime then refuses the steer)."""

    def cancel(self, worker_ref: str) -> Optional[WorkerObservation]:
        """Ask the worker to stop. Return ``finished`` only after observing termination; ``None`` or ``running``
        means the stop was requested but not confirmed (the runtime keeps the run ``cancelling``). Raise if the
        request could not even be delivered."""

    def attach(self, worker_ref: str, actions: "ScopedActions") -> WorkerObservation:
        """Re-bind a fresh runtime to an existing worker and say whether it is running, finished or lost."""


class DelegationBroker(Protocol):
    """What :meth:`ScopedActions.request_help` hands a structured ask to. The :class:`~dervo_harness.supervisor.Supervisor`
    attaches itself as the runtime's broker; a runtime without one refuses the action (there is no fallback)."""

    def request_help(self, actions: "ScopedActions", run: Run, target: str, purpose: str, source_refs: Sequence[str], instructions: str) -> Any: ...

    def helpers_for(self, project_id: str, actor_id: str) -> Sequence[Any]:
        """The helpers ``actor_id`` may ask in ``project_id`` right now, from durable authority only; rendered into its context."""


class ScopedActions:
    """The only way a worker touches durable state. Bound to one run attempt and one runtime."""

    def __init__(self, runtime: "Runtime", run_id: str, attempt: int, caller: Caller, project_id: str, room_id: str) -> None:
        self._rt = runtime
        self.run_id, self.attempt, self.caller, self.project_id, self.room_id = run_id, attempt, caller, project_id, room_id

    # -- guards ----------------------------------------------------------------------
    def _live(self) -> Run:
        run = self._rt._run(self.run_id)
        if run.status != RUN_RUNNING or run.attempt != self.attempt or run.owner_token != self._rt.owner_token:
            raise StateError(f"run {self.run_id} attempt {self.attempt} is not live ({run.status}); action refused")
        return run

    def _room_allowed(self, room_id: str) -> str:
        if room_id not in self._rt._allowed_rooms(self.project_id, self.caller.actor_id):
            raise AccessDenied(f"room {room_id} is outside the execution grant for {self.caller.actor_id}")
        return room_id

    # -- reads -------------------------------------------------------------------------
    def context(self) -> str:
        """The fresh-session context assembled at start, verbatim."""
        return self._rt._session_for_run(self.run_id).context_text

    def steer_notes(self) -> List[str]:
        return [e.payload.get("note", "") for e in self._rt._events(self.run_id) if e.kind == "steer" and e.attempt == self.attempt]

    # -- writes ------------------------------------------------------------------------
    def say(self, text: str, refs: Sequence[str] = (), *, room_id: Optional[str] = None) -> Message:
        self._live()
        msg = self._rt.h.record_message(self.caller, self._room_allowed(room_id or self.room_id), text, refs=refs)
        self._rt._track(self.run_id, msg.ref)
        return msg

    def decide(self, title: str, rationale: str = "", source_refs: Sequence[str] = (), *, room_id: Optional[str] = None) -> Decision:
        self._live()
        dec = self._rt.h.record_decision(self.caller, self._room_allowed(room_id or self.room_id), title, rationale, source_refs=source_refs)
        self._rt._track(self.run_id, dec.ref)
        return dec

    def delegate(self, to_room: str, purpose: str, item_refs: Sequence[str], *, instructions: str = "") -> HandoffPacket:
        """Bounded handoff from the run room into another room inside the grant; harness policy still applies."""
        self._live()
        self._room_allowed(to_room)
        packet = self._rt.h.create_handoff(self.caller, self.room_id, to_room, purpose, item_refs, instructions=instructions)
        self._rt._track(self.run_id, packet.ref, handoff_id=packet.handoff_id)
        return packet

    def request_help(self, target: str, purpose: str, source_refs: Sequence[str], *, instructions: str = "") -> Any:
        """Ask a named agent (``@handle``) for help through the structured delegation contract, not transcript text.

        Validated and guarded by the runtime's broker (the supervisor): exact registered target, bounded purpose and
        instructions, one to N source refs from this run's own room, depth/per-run/per-root budgets, loop and
        duplicate guards, pool binding and execution grant of the target, and a free scope in its room. On success
        the packet and dispatch exist and a :class:`~dervo_harness.models.Delegation` is returned; on refusal nothing
        is created and the refusal is logged on this run. A runtime without a broker refuses outright.
        """
        run = self._live()
        broker = self._rt.broker
        if broker is None:
            raise StateError("no delegation broker is attached to this runtime: model-requested help needs a Supervisor over a "
                             "WorkerPool, and a bare Runtime has no fallback path")
        return broker.request_help(self, run, target, purpose, source_refs, instructions)

    def complete_delegation(self, handoff_id: str, summary: str, refs: Sequence[str] = ()) -> HandoffPacket:
        self._live()
        packet = self._rt.h.get_handoff(self.caller, handoff_id)
        self._room_allowed(packet.to_room)
        return self._rt.h.return_handoff(self.caller, handoff_id, summary, refs)

    def propose_skill(self, name: str, description: str, body: str, *, tags: Sequence[str] = (), sources: Sequence[str] = ()) -> SkillVersion:
        """Always a proposal: the run acts as an agent, so a human must approve before it is retrievable."""
        self._live()
        sv = self._rt.h.propose_skill(self.caller, self.project_id, name, description, body, tags=tags, sources=sources, note=f"run {self.run_id}")
        self._rt._track(self.run_id, sv.ref)
        return sv


class Runtime:
    """Durable request/run/session records plus event handling over one :class:`Harness` and one worker port."""

    def __init__(self, harness: Harness, worker: WorkerPort, *, owner_token: Optional[str] = None) -> None:
        self.h = harness
        self._conn: sqlite3.Connection = harness._conn
        self.worker = worker
        self.owner_token = owner_token or new_id("rt")
        self.broker: Optional[DelegationBroker] = None  # set by a Supervisor; without it request_help is refused
        self._actions: Dict[str, ScopedActions] = {}
        self._orphan_previous_runs()

    def describe_worker(self) -> Dict[str, Any]:
        return {"name": self.worker.name, "is_fake": bool(self.worker.is_fake), "model": getattr(self.worker, "model_name", "")}

    # ------------------------------------------------------------------ grants

    def grant_execution(self, caller: Caller, project_id: str, actor_id: str, rooms: Sequence[str], *, note: str = "") -> ExecutionGrant:
        """Human project owner lets ``actor_id`` run and delegate inside exactly ``rooms`` with no per-run approval."""
        self.h._require_human(caller, "grant execution")
        self.h._require_role(caller, project_id, (ROLE_OWNER,), "grant execution")
        role = self.h._role(project_id, actor_id)
        if role is None or role == ROLE_VIEWER:
            raise ValidationError(f"{actor_id} must be a non-viewer member of project {project_id} before it can be granted execution")
        room_ids = list(dict.fromkeys(str(r) for r in rooms if str(r).strip()))
        if not room_ids:
            raise ValidationError("an execution grant lists explicit rooms; none were given")
        for rid in room_ids:
            if self.h._room(rid).project_id != project_id:
                raise ValidationError(f"room {rid} is not in project {project_id}")
        gid, ts = new_id("xg"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO execution_grants (grant_id, project_id, actor_id, rooms_json, granted_by, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (gid, project_id, actor_id, dumps(room_ids), caller.actor_id, note or "", ts),
            )
        return self._grant(gid)

    def revoke_execution(self, caller: Caller, grant_id: str) -> ExecutionGrant:
        self.h._require_human(caller, "revoke execution grants")
        grant = self._grant(grant_id)
        self.h._require_role(caller, grant.project_id, (ROLE_OWNER,), "revoke execution grants")
        with self._conn:
            self._conn.execute(
                "UPDATE execution_grants SET revoked_at = ?, revoked_by = ? WHERE grant_id = ? AND revoked_at IS NULL",
                (now_iso(), caller.actor_id, grant_id),
            )
        return self._grant(grant_id)

    def _allowed_rooms(self, project_id: str, actor_id: str) -> Set[str]:
        rows = self._conn.execute(
            "SELECT rooms_json FROM execution_grants WHERE project_id = ? AND actor_id = ? AND revoked_at IS NULL", (project_id, actor_id)
        ).fetchall()
        allowed: Set[str] = set()
        for row in rows:
            allowed.update(loads(row["rooms_json"]) or [])
        return allowed

    def _grant(self, grant_id: str) -> ExecutionGrant:
        row = self._conn.execute("SELECT * FROM execution_grants WHERE grant_id = ?", (grant_id,)).fetchone()
        if row is None:
            raise NotFound(f"execution grant {grant_id} not found")
        return ExecutionGrant(
            row["grant_id"], row["project_id"], row["actor_id"], list(loads(row["rooms_json"]) or []), row["granted_by"],
            row["note"], row["created_at"], row["revoked_at"], row["revoked_by"],
        )

    # ------------------------------------------------------------------ submit

    def submit(
        self, caller: Caller, room_id: str, goal: str, *, worker_actor_id: str, request_key: str,
        scope: Optional[str] = None, resource: Optional[str] = None, terms: Sequence[str] = (), tags: Sequence[str] = (),
    ) -> Request:
        """Record a request and start its first run.

        Idempotent on ``(room_id, request_key)`` only for an identical ask: the same key with a different
        goal, worker, scope or resource is refused rather than silently returning the older request.
        ``resource`` names a shared external thing; only one active run may hold it anywhere in the data dir.
        """
        room = self.h._require_write_room(caller, room_id)
        if not isinstance(goal, str) or not goal.strip():
            raise ValidationError("goal must be a non-empty string")
        if not isinstance(request_key, str) or not request_key.strip():
            raise ValidationError("request_key must be a non-empty string; it makes submit idempotent")
        goal = goal.strip()
        scope = (scope or room_id).strip()
        resource = resource.strip() if isinstance(resource, str) and resource.strip() else None
        existing = self._conn.execute("SELECT request_id FROM requests WHERE room_id = ? AND request_key = ?", (room_id, request_key)).fetchone()
        if existing:
            prior = self.get_request(caller, existing["request_id"])
            asked = {"goal": goal, "worker_actor_id": worker_actor_id, "scope": scope, "resource": resource}
            had = {"goal": prior.goal, "worker_actor_id": prior.worker_actor_id, "scope": prior.scope, "resource": prior.resource}
            changed = sorted(k for k in asked if asked[k] != had[k])
            if changed:
                raise ValidationError(
                    f"request_key {request_key!r} in room {room_id} already names request {prior.request_id} with a different "
                    f"{', '.join(changed)}; use a new request_key for a different ask"
                )
            return prior
        if room_id not in self._allowed_rooms(room.project_id, worker_actor_id):
            raise AccessDenied(f"{worker_actor_id} has no active execution grant covering room {room_id}; a human project owner must grant it")
        self._require_scope_free(room_id, scope, resource)
        rid, ts = new_id("req"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO requests (request_id, request_key, project_id, room_id, scope, goal, submitted_by, submitter_kind, worker_actor_id, "
                "terms_json, tags_json, status, current_run_id, attempts, created_at, resource) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)",
                (rid, request_key, room.project_id, room_id, scope, goal, caller.actor_id, caller.kind, worker_actor_id,
                 dumps([str(t) for t in terms]), dumps([str(t) for t in tags]), REQUEST_ACTIVE, ts, resource),
            )
        self._start_run(self._request(rid))
        return self._request(rid)

    def resubmit(self, caller: Caller, request_id: str) -> Run:
        """Explicit new attempt for a lost or failed run. Nothing calls this automatically."""
        request = self.get_request(caller, request_id)
        self._require_control(caller, request)
        if request.status != REQUEST_ACTIVE:
            raise StateError(f"request {request_id} is {request.status}")
        current = self._run(request.current_run_id) if request.current_run_id else None
        if current is not None and current.status not in (RUN_LOST, RUN_FAILED):
            raise StateError(f"run {current.run_id} is {current.status}; only lost or failed runs can be resubmitted")
        self._require_scope_free(request.room_id, request.scope, request.resource)
        return self._start_run(request)

    def _require_scope_free(self, room_id: str, scope: str, resource: Optional[str] = None) -> None:
        """No active run (running, unknown or cancelling) may hold the same ``(room, scope)`` or the same ``resource``."""
        marks = ", ".join("?" for _ in RUN_ACTIVE)
        row = self._conn.execute(
            "SELECT r.run_id, r.status, q.request_id FROM runs r JOIN requests q ON q.request_id = r.request_id "
            f"WHERE q.room_id = ? AND q.scope = ? AND r.status IN ({marks}) LIMIT 1",
            (room_id, scope, *RUN_ACTIVE),
        ).fetchone()
        if row:
            raise StateError(f"scope {scope!r} in room {room_id} is owned by request {row['request_id']} (run {row['run_id']} is {row['status']})")
        if resource:
            row = self._conn.execute(
                "SELECT r.run_id, r.status, q.request_id, q.room_id FROM runs r JOIN requests q ON q.request_id = r.request_id "
                f"WHERE q.resource = ? AND r.status IN ({marks}) LIMIT 1",
                (resource, *RUN_ACTIVE),
            ).fetchone()
            if row:
                raise StateError(f"resource {resource!r} is held by request {row['request_id']} in room {row['room_id']} (run {row['run_id']} is {row['status']})")

    def _start_run(self, request: Request) -> Run:
        attempt = request.attempts + 1
        run_id, session_id, ts = new_id("run"), new_id("ses"), now_iso()
        worker_caller = Caller(request.worker_actor_id, kind=CALLER_AGENT, session_id=session_id)
        ctx = self.h.build_session_context(worker_caller, request.room_id, terms=request.terms, tags=request.tags)
        if self.broker is not None:  # who this run may ask, decided by the broker from durable authority; nothing without one
            ctx.helpers = list(self.broker.helpers_for(request.project_id, request.worker_actor_id))
        text = ctx.render()
        skill_refs = [sv.ref for sv in ctx.skills.skills]
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (session_id, run_id, room_id, actor_id, context_text, context_hash, skill_refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, run_id, request.room_id, request.worker_actor_id, text, hashlib.sha256(text.encode("utf-8")).hexdigest(), dumps(skill_refs), ts),
            )
            self._conn.execute(
                "INSERT INTO runs (run_id, request_id, attempt, project_id, room_id, worker_actor_id, session_id, worker_name, worker_is_fake, worker_ref, "
                "owner_token, status, last_event_seq, produced_refs_json, handoff_ids_json, error, result_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, '[]', '[]', NULL, NULL, ?, ?)",
                (run_id, request.request_id, attempt, request.project_id, request.room_id, request.worker_actor_id, session_id,
                 self.worker.name, int(bool(self.worker.is_fake)), self.owner_token, RUN_RUNNING, ts, ts),
            )
            self._conn.execute("UPDATE requests SET attempts = ?, current_run_id = ? WHERE request_id = ?", (attempt, run_id, request.request_id))
        self._log(run_id, attempt, 0, "started", {"session_id": session_id, "worker": self.describe_worker()}, "runtime", True, "")
        self._note(run_id, f"Run {run_id} attempt {attempt} started for request {request.request_id} by worker {self._worker_label(self._run(run_id))}: {request.goal}")
        actions = ScopedActions(self, run_id, attempt, worker_caller, request.project_id, request.room_id)
        assignment = WorkAssignment(
            run_id, attempt, session_id, request.project_id, request.room_id, request.worker_actor_id, request.goal, text, skill_refs,
            sorted(self._allowed_rooms(request.project_id, request.worker_actor_id)),
        )
        try:
            worker_ref = str(self.worker.start(assignment, actions))
        except Exception as exc:  # a worker fault must become a durable failed run, not a crash
            self._finish(run_id, RUN_FAILED, error=f"worker start failed: {type(exc).__name__}: {exc}")
            return self._run(run_id)
        with self._conn:
            self._conn.execute("UPDATE runs SET worker_ref = ?, updated_at = ? WHERE run_id = ?", (worker_ref, now_iso(), run_id))
        self._stamp_routed_worker(run_id, attempt, worker_ref)
        self._actions[run_id] = actions
        return self._run(run_id)

    def _stamp_routed_worker(self, run_id: str, attempt: int, worker_ref: str) -> None:
        """A routing port (see :class:`~dervo_harness.supervisor.WorkerPool`) may answer ``identify(worker_ref)`` with the
        member that actually took the run. The run row then carries that member's name and fake label, so a fake
        member of a mixed pool can never be mistaken for a real one; the ``routed`` event records the whole choice."""
        identify = getattr(self.worker, "identify", None)
        if not callable(identify):
            return
        info = dict(identify(worker_ref) or {})
        name, is_fake = str(info.get("name") or self.worker.name), bool(info.get("is_fake", self.worker.is_fake))
        with self._conn:
            self._conn.execute("UPDATE runs SET worker_name = ?, worker_is_fake = ?, updated_at = ? WHERE run_id = ?",
                               (name, int(is_fake), now_iso(), run_id))
        self._log(run_id, attempt, 0, "routed", info, "runtime", True, "")

    # ------------------------------------------------------------------ events

    def handle_event(self, event: WorkerEvent) -> EventOutcome:
        """Apply one worker event if, and only if, it belongs to the live attempt this runtime owns."""
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (event.run_id,)).fetchone()
        if row is None:
            self._log(event.run_id, event.attempt, event.event_seq, event.kind, event.payload, "worker", False, "unknown run")
            return EventOutcome(False, "unknown run", None)
        run = self._run_from_row(row)
        reason = ""
        if event.kind not in WORKER_EVENT_KINDS:
            reason = f"unknown event kind {event.kind!r}"
        elif event.attempt != run.attempt:
            reason = f"stale attempt {event.attempt}; current attempt is {run.attempt}"
        elif run.status not in (RUN_RUNNING, RUN_CANCELLING):
            reason = f"run is {run.status}"
        elif run.owner_token != self.owner_token:
            reason = "run is owned by another runtime; reconcile first"
        elif event.event_seq <= run.last_event_seq:
            reason = f"event_seq {event.event_seq} not after {run.last_event_seq}"
        elif event.kind == EVENT_STOPPED and run.status != RUN_CANCELLING:
            reason = "stopped without a cancel request"
        if reason:
            self._log(run.run_id, event.attempt, event.event_seq, event.kind, event.payload, "worker", False, reason)
            return EventOutcome(False, reason, run.status)
        self._log(run.run_id, event.attempt, event.event_seq, event.kind, event.payload, "worker", True, "")
        with self._conn:
            self._conn.execute("UPDATE runs SET last_event_seq = ?, updated_at = ? WHERE run_id = ?", (event.event_seq, now_iso(), run.run_id))
        if run.status == RUN_CANCELLING:
            # Any terminal event is the worker's own confirmation that it stopped. A late result is discarded, not applied.
            if event.kind in WORKER_TERMINAL_EVENT_KINDS:
                self._finish(run.run_id, RUN_CANCELLED, error=self._cancel_note(run, f"worker confirmed stop with {event.kind!r} event"))
        elif event.kind == EVENT_FAILED:
            self._finish(run.run_id, RUN_FAILED, error=str(event.payload.get("error") or "worker reported failure"))
        elif event.kind == EVENT_RESULT:
            self._apply_result(run, event.payload)
        return EventOutcome(True, "", self._run(run.run_id).status)

    def pump(self, *, max_events: int = 1000) -> int:
        """Drain the port for every running or cancelling run this runtime owns and apply the events. Returns events seen."""
        seen = 0
        while seen < max_events:
            progressed = False
            rows = self._conn.execute(
                "SELECT run_id, status, worker_ref FROM runs WHERE status IN (?, ?) AND owner_token = ? ORDER BY seq",
                (RUN_RUNNING, RUN_CANCELLING, self.owner_token),
            ).fetchall()
            for row in rows:
                if not row["worker_ref"]:
                    continue
                try:
                    events = list(self.worker.drain(row["worker_ref"]))
                except Exception as exc:
                    if row["status"] == RUN_CANCELLING:  # a broken drain is not proof the worker stopped
                        self._log(row["run_id"], self._run(row["run_id"]).attempt, 0, "drain_port_error", {"error": f"{type(exc).__name__}: {exc}"}, "runtime", True, "")
                        continue
                    self._finish(row["run_id"], RUN_FAILED, error=f"worker drain failed: {type(exc).__name__}: {exc}")
                    progressed = True
                    continue
                for event in events:
                    self.handle_event(event)
                    seen += 1
                    progressed = True
            if not progressed:
                break
        return seen

    def _apply_result(self, run: Run, payload: Dict[str, Any]) -> None:
        worker_caller = self._actions[run.run_id].caller if run.run_id in self._actions else Caller(run.worker_actor_id, kind=CALLER_AGENT, session_id=run.session_id)
        try:
            summary = strip_invisible_unicode(str(payload.get("summary") or "")).strip()
            if not summary:
                raise ValidationError("result summary is empty")
            summary = summary[:RESULT_SUMMARY_MAX_CHARS]
            refs = self._validate_result_refs(run, worker_caller, payload.get("refs") or [])
        except Exception as exc:
            self._finish(run.run_id, RUN_FAILED, error=f"invalid result: {exc}")
            return
        msg = self.h._insert_message(
            self.h._room(run.room_id), worker_caller, KIND_RUN_RESULT,
            f"Run {run.run_id} result (worker {self._worker_label(run)}): {summary}", refs,
        )
        result = RunResult(summary, refs, run.worker_name, bool(run.worker_is_fake), msg.message_id, msg.created_at)
        self._finish(run.run_id, RUN_SUCCEEDED, result=result)

    def _validate_result_refs(self, run: Run, caller: Caller, refs: Iterable[Any]) -> List[str]:
        allowed = self._allowed_rooms(run.project_id, run.worker_actor_id)
        out: List[str] = []
        for ref in refs:
            ref = str(ref)
            kind, _ = parse_ref(ref)
            record = self.h.resolve_ref(caller, ref)  # must be dereferenceable by the worker principal
            if kind in ("message", "decision") and record.room_id not in allowed:  # type: ignore[union-attr]
                raise AccessDenied(f"result ref {ref} points outside the execution grant rooms")
            if kind == "handoff" and record.from_room not in allowed and record.to_room not in allowed:  # type: ignore[union-attr]
                raise AccessDenied(f"result ref {ref} points outside the execution grant rooms")
            if kind == "skill" and record.project_id != run.project_id:  # type: ignore[union-attr]
                raise AccessDenied(f"result ref {ref} is a skill of another project")
            if ref not in out:
                out.append(ref)
        return out

    # ------------------------------------------------------------------ steer / cancel

    def steer(self, caller: Caller, run_id: str, note: str) -> RunEvent:
        run = self.get_run(caller, run_id)
        self._require_control(caller, self._request(run.request_id))
        if not isinstance(note, str) or not note.strip():
            raise ValidationError("steer note must be a non-empty string")
        if run.status != RUN_RUNNING or run.owner_token != self.owner_token:
            raise StateError(f"run {run_id} is {run.status} and not steerable by this runtime; reconcile first if it is unknown")
        note = strip_invisible_unicode(note.strip())
        self.worker.steer(run.worker_ref or "", note)  # a port that cannot deliver raises, and nothing is logged as delivered
        self._log(run_id, run.attempt, 0, "steer", {"note": note, "by": caller.label()}, "runtime", True, "")
        return self._events(run_id)[-1]

    def cancel(self, caller: Caller, run_id: str, note: str = "") -> Run:
        """Request a stop. The run is ``cancelling`` (scope still owned, actions and steering refused, open handoffs
        closed) until the port confirms the worker terminated; only then is it ``cancelled`` and the request closed.
        Calling cancel on a ``cancelling`` run retries the stop. A ``lost`` run has no worker to stop, so cancel
        closes it on the strength of the port's earlier lost report."""
        run = self.get_run(caller, run_id)
        self._require_control(caller, self._request(run.request_id))
        if run.status not in (*RUN_ACTIVE, RUN_LOST):
            raise StateError(f"run {run_id} is already {run.status}")
        self._log(run_id, run.attempt, 0, "cancel", {"note": note, "by": caller.label(), "from_status": run.status}, "runtime", True, "")
        if run.status == RUN_LOST:
            self._finish(run_id, RUN_CANCELLED, error=self._cancel_note(run, "no worker to stop: the port reported it lost", note))
            return self._run(run_id)
        if run.status != RUN_CANCELLING:
            with self._conn:  # take ownership of the stop; an unknown run is adopted for this purpose only
                self._conn.execute("UPDATE runs SET status = ?, owner_token = ?, error = ?, updated_at = ? WHERE run_id = ?",
                                   (RUN_CANCELLING, self.owner_token, note or None, now_iso(), run_id))
            self._actions.pop(run_id, None)
            self._cancel_open_handoffs(run)
            self._note(run_id, f"Run {run_id} cancelling" + (f": {note}" if note else ""))
        if not run.worker_ref:
            self._finish(run_id, RUN_CANCELLED, error=self._cancel_note(run, "worker never started (no worker_ref)", note))
            return self._run(run_id)
        try:
            obs = self.worker.cancel(run.worker_ref)
        except Exception as exc:  # the stop was not delivered; the run stays cancelling and keeps the scope
            self._log(run_id, run.attempt, 0, "cancel_port_error", {"error": f"{type(exc).__name__}: {exc}"}, "runtime", True, "")
            return self._run(run_id)
        self._apply_observation(self._run(run_id), obs, source="cancel")
        return self._run(run_id)

    def _cancel_note(self, run: Run, how: str, note: Optional[str] = None) -> str:
        reason = note if note is not None else (run.error or "")
        return f"{reason}; {how}" if reason else how

    def _apply_observation(self, run: Run, obs: Optional[WorkerObservation], *, source: str) -> None:
        """Turn a port observation into a durable status. Only ``finished`` (or its terminal events) ends a cancelling run."""
        if obs is None:
            obs = WorkerObservation("running", note="port returned no observation")
        self._log(run.run_id, run.attempt, 0, "observation", {"source": source, "state": obs.state, "note": obs.note}, "runtime", True, "")
        if obs.state == "finished":
            for event in obs.events:
                self.handle_event(event)
            current = self._run(run.run_id)
            if current.status == RUN_CANCELLING:
                self._finish(run.run_id, RUN_CANCELLED, error=self._cancel_note(current, f"port observed termination ({source})"))
            elif current.status == RUN_RUNNING:
                self._finish(run.run_id, RUN_FAILED, error="worker reported finished without a terminal event")
        elif obs.state == "lost" and source == "reconcile":
            self._finish(run.run_id, RUN_LOST, error=obs.note or "worker not found at reconciliation; resubmit explicitly to retry")
        # 'running', or 'lost' answered to a cancel, changes nothing: the stop is still unconfirmed.

    # ------------------------------------------------------------------ reconcile

    def _orphan_previous_runs(self) -> None:
        rows = self._conn.execute(
            "SELECT run_id, attempt, owner_token FROM runs WHERE status = ? AND (owner_token IS NULL OR owner_token != ?)",
            (RUN_RUNNING, self.owner_token),
        ).fetchall()
        for row in rows:
            with self._conn:
                self._conn.execute("UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?", (RUN_UNKNOWN, now_iso(), row["run_id"]))
            self._log(row["run_id"], row["attempt"], 0, "observation_lost",
                      {"previous_owner": row["owner_token"], "new_runtime": self.owner_token}, "runtime", True, "")

    def reconcile(self, caller: Caller) -> List[Run]:
        """Ask the worker port about every ``unknown`` or ``cancelling`` run in projects the human caller owns or edits.

        ``running`` re-adopts an unknown run (a cancelling one gets the stop re-sent), ``finished`` applies pending
        events, ``lost`` is the port's positive claim and is recorded as such. An ``attach`` that raises proves nothing
        about the old worker: the run keeps its status and ownership, and the error is logged.
        """
        self.h._require_human(caller, "reconcile runs")
        rows = self._conn.execute("SELECT * FROM runs WHERE status IN (?, ?) ORDER BY seq", (RUN_UNKNOWN, RUN_CANCELLING)).fetchall()
        touched: List[Run] = []
        for row in rows:
            run = self._run_from_row(row)
            if self.h._role(run.project_id, caller.actor_id) not in (ROLE_OWNER, ROLE_EDITOR):
                continue
            actions = ScopedActions(self, run.run_id, run.attempt, Caller(run.worker_actor_id, kind=CALLER_AGENT, session_id=run.session_id), run.project_id, run.room_id)
            try:
                obs = self.worker.attach(run.worker_ref or "", actions) if run.worker_ref else WorkerObservation("lost", note="no worker_ref recorded")
            except Exception as exc:
                self._log(run.run_id, run.attempt, 0, "reconcile_port_error", {"error": f"attach failed: {type(exc).__name__}: {exc}", "by": caller.label()}, "runtime", True, "")
                touched.append(run)
                continue
            self._log(run.run_id, run.attempt, 0, "reconcile", {"observation": obs.state, "note": obs.note, "by": caller.label()}, "runtime", True, "")
            if obs.state in ("running", "finished"):
                with self._conn:  # adopt: an unknown run becomes running again, a cancelling one stays cancelling under this owner
                    self._conn.execute("UPDATE runs SET status = ?, owner_token = ?, updated_at = ? WHERE run_id = ?",
                                       (RUN_CANCELLING if run.status == RUN_CANCELLING else RUN_RUNNING, self.owner_token, now_iso(), run.run_id))
                if run.status != RUN_CANCELLING:
                    self._actions[run.run_id] = actions
            self._apply_observation(self._run(run.run_id), obs, source="reconcile")
            if obs.state == "running" and self._run(run.run_id).status == RUN_CANCELLING:
                try:
                    self._apply_observation(self._run(run.run_id), self.worker.cancel(run.worker_ref or ""), source="cancel")
                except Exception as exc:
                    self._log(run.run_id, run.attempt, 0, "cancel_port_error", {"error": f"{type(exc).__name__}: {exc}"}, "runtime", True, "")
            touched.append(self._run(run.run_id))
        return touched

    # ------------------------------------------------------------------ reads

    def get_request(self, caller: Caller, request_id: str) -> Request:
        request = self._request(request_id)
        self.h._require_read_room(caller, request.room_id)
        return request

    def get_run(self, caller: Caller, run_id: str) -> Run:
        run = self._run(run_id)
        self.h._require_read_room(caller, run.room_id)
        return run

    def get_session(self, caller: Caller, run_id: str) -> Session:
        self.get_run(caller, run_id)
        return self._session_for_run(run_id)

    def list_runs(self, caller: Caller, request_id: str) -> List[Run]:
        self.get_request(caller, request_id)
        rows = self._conn.execute("SELECT * FROM runs WHERE request_id = ? ORDER BY attempt", (request_id,)).fetchall()
        return [self._run_from_row(r) for r in rows]

    def list_events(self, caller: Caller, run_id: str) -> List[RunEvent]:
        self.get_run(caller, run_id)
        return self._events(run_id)

    def status(self, caller: Caller, request_id: str) -> Dict[str, Any]:
        """One-call view: request status, current run status, attempts, and the last event reason."""
        request = self.get_request(caller, request_id)
        run = self._run(request.current_run_id) if request.current_run_id else None
        events = self._events(run.run_id) if run else []
        return {
            "request_id": request.request_id, "request_status": request.status, "attempts": request.attempts,
            "run_id": run.run_id if run else None, "run_status": run.status if run else None,
            "worker": self.describe_worker(), "error": run.error if run else None,
            "run_worker": {"name": run.worker_name, "is_fake": run.worker_is_fake} if run else None,
            "result": run.result.__dict__ if run and run.result else None,
            "last_event": (events[-1].kind if events else None),
            "rejected_events": sum(1 for e in events if not e.accepted),
        }

    # ------------------------------------------------------------------ internals

    def _require_control(self, caller: Caller, request: Request) -> None:
        if caller.actor_id == request.submitted_by and caller.kind == request.submitter_kind:
            return
        if caller.kind == CALLER_HUMAN and self.h._role(request.project_id, caller.actor_id) in (ROLE_OWNER, ROLE_EDITOR):
            return
        raise AccessDenied(f"{caller.label()} may not control request {request.request_id}: not its submitter or a human owner/editor")

    @staticmethod
    def _worker_label(run: Run) -> str:
        """The label stamped on the run row (the routed member for a pool), never the port's generic name."""
        return f"{run.worker_name}{' (fake)' if run.worker_is_fake else ''}"

    def _finish(self, run_id: str, status: str, *, error: Optional[str] = None, result: Optional[RunResult] = None) -> None:
        run = self._run(run_id)
        ts = now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE runs SET status = ?, error = ?, result_json = ?, updated_at = ?, ended_at = ? WHERE run_id = ?",
                (status, error, dumps(result.__dict__) if result else None, ts, ts, run_id),
            )
            if status in (RUN_SUCCEEDED, RUN_CANCELLED):
                self._conn.execute("UPDATE requests SET status = ?, closed_at = ? WHERE request_id = ?", (REQUEST_CLOSED, ts, run.request_id))
        self._log(run_id, run.attempt, 0, status, {"error": error} if error else {}, "runtime", True, "")
        if status in (RUN_CANCELLED, RUN_FAILED, RUN_LOST):
            self._cancel_open_handoffs(run)  # idempotent: already-closed handoffs are skipped
            self._note(run_id, f"Run {run_id} {status}" + (f": {error}" if error else ""))
        self._actions.pop(run_id, None)

    def _cancel_open_handoffs(self, run: Run) -> None:
        caller = Caller(run.worker_actor_id, kind=CALLER_AGENT, session_id=run.session_id)
        for hid in run.handoff_ids:
            try:
                if self.h.get_handoff(caller, hid).status == HANDOFF_OPEN:
                    self.h.cancel_handoff(caller, hid, note=f"run {run.run_id} ended")
            except Exception as exc:  # never let cleanup mask the run's own terminal state
                self._log(run.run_id, run.attempt, 0, "handoff_cleanup_error", {"handoff_id": hid, "error": str(exc)}, "runtime", True, "")

    def _note(self, run_id: str, text: str) -> None:
        run = self._run(run_id)
        self.h._insert_message(self.h._room(run.room_id), Caller(run.worker_actor_id, kind=CALLER_AGENT, session_id=run.session_id), KIND_RUN, text, [])

    def _track(self, run_id: str, ref: str, *, handoff_id: Optional[str] = None) -> None:
        run = self._run(run_id)
        refs = run.produced_refs + ([ref] if ref not in run.produced_refs else [])
        hids = run.handoff_ids + ([handoff_id] if handoff_id and handoff_id not in run.handoff_ids else [])
        with self._conn:
            self._conn.execute("UPDATE runs SET produced_refs_json = ?, handoff_ids_json = ?, updated_at = ? WHERE run_id = ?",
                               (dumps(refs), dumps(hids), now_iso(), run_id))

    def _log(self, run_id: str, attempt: int, event_seq: int, kind: str, payload: Dict[str, Any], source: str, accepted: bool, reason: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO run_events (run_id, attempt, event_seq, kind, payload_json, source, accepted, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, int(attempt), int(event_seq), kind, dumps(dict(payload or {})), source, int(bool(accepted)), reason, now_iso()),
            )

    def _events(self, run_id: str) -> List[RunEvent]:
        rows = self._conn.execute("SELECT * FROM run_events WHERE run_id = ? ORDER BY seq", (run_id,)).fetchall()
        return [RunEvent(r["seq"], r["run_id"], r["attempt"], r["event_seq"], r["kind"], loads(r["payload_json"]) or {}, r["source"],
                         bool(r["accepted"]), r["reason"], r["created_at"]) for r in rows]

    def _request(self, request_id: str) -> Request:
        row = self._conn.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise NotFound(f"request {request_id} not found")
        return Request(
            row["request_id"], row["request_key"], row["project_id"], row["room_id"], row["scope"], row["goal"], row["submitted_by"],
            row["submitter_kind"], row["worker_actor_id"], list(loads(row["terms_json"]) or []), list(loads(row["tags_json"]) or []),
            row["status"], row["current_run_id"], int(row["attempts"]), row["created_at"], row["closed_at"], row["resource"],
        )

    def _run(self, run_id: str) -> Run:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"run {run_id} not found")
        return self._run_from_row(row)

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> Run:
        result_raw = loads(row["result_json"])
        return Run(
            run_id=row["run_id"], request_id=row["request_id"], attempt=int(row["attempt"]), project_id=row["project_id"], room_id=row["room_id"],
            worker_actor_id=row["worker_actor_id"], session_id=row["session_id"], worker_name=row["worker_name"], worker_is_fake=bool(row["worker_is_fake"]),
            worker_ref=row["worker_ref"], owner_token=row["owner_token"], status=row["status"], last_event_seq=int(row["last_event_seq"]),
            produced_refs=list(loads(row["produced_refs_json"]) or []), handoff_ids=list(loads(row["handoff_ids_json"]) or []), error=row["error"],
            result=RunResult(**result_raw) if result_raw else None, created_at=row["created_at"], updated_at=row["updated_at"], ended_at=row["ended_at"],
        )

    def _session_for_run(self, run_id: str) -> Session:
        row = self._conn.execute("SELECT * FROM sessions WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"session for run {run_id} not found")
        return Session(row["session_id"], row["run_id"], row["room_id"], row["actor_id"], row["context_text"], row["context_hash"],
                       list(loads(row["skill_refs_json"]) or []), row["created_at"])
