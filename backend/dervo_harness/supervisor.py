"""Host-routed cross-room execution over the runtime: a configured worker pool, one runtime owner per data dir,
and durable linkage from one open handoff packet to one request in its target room, with the validated result
returned to the origin room through the packet.

What "host-routed" means, stated plainly: a human or the origin conversation's own code picks the packet, the
target actor and the pool member by name. No model plans, chooses or triggers a dispatch. The text-only Claude
worker cannot call :meth:`dispatch`; it only ever receives context and returns text. This module is the
plumbing that lets one room's saved facts be executed on by another room's worker and come back linked.

Three pieces:

- :class:`WorkerPool` is a :class:`~dervo_harness.runtime.WorkerPort` that routes every runtime call to exactly one
  configured :class:`WorkerBinding` by ``(project_id, worker_actor_id)``. A binding names its pool member, the port,
  and the ``provider``/``account`` labels the host chose. An actor with no binding is refused; there is no default
  member and no fallback. ``worker_ref`` values are prefixed with the member name so a later runtime can route
  ``drain``/``cancel``/``attach`` to the same member; a ref naming an unconfigured member makes the pool raise
  (the runtime then keeps the run's status and logs the error) rather than guess.
- :class:`Supervisor` owns exactly one :class:`~dervo_harness.runtime.Runtime` over the pool and takes a lease on the
  data dir (``meta.runtime_lease``): a second supervisor on the same data dir is refused while the holder's process
  is alive, unless the caller passes ``takeover=True`` explicitly. This is what stops two runtimes from marking each
  other's runs ``unknown``.
- :meth:`Supervisor.dispatch` links one **open** handoff packet to one request in the packet's target room, submitted
  through the runtime under all of its rules (execution grant, one owner per scope, idempotent ``request_key``
  ``dispatch:<handoff_id>``). :meth:`Supervisor.settle` (called by :meth:`pump` and :meth:`reconcile`) follows the
  target run: a ``succeeded`` run's result is returned through the packet as the target worker's agent principal,
  so every downgrade rule applies and a refused return leaves nothing in the origin room; ``failed``/``lost`` keep
  the packet open for an explicit resubmit; ``cancelled`` withdraws the packet. If the origin withdraws the packet
  (for example the origin run is cancelled and the runtime closes its open handoffs) while the target run is still
  active, the supervisor asks the runtime to cancel that run and reports ``cancelling`` until the port confirms.

There is no broadcast: a dispatch names one packet and one target. Whole-workspace fan-out does not exist here.
"""

from __future__ import annotations

import os
import re
import socket
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .addressing import AgentRegistry
from .delegation import ADMIT_EXISTING, ADMIT_WAIT, Ask, DelegationRules, Lineage, PriorAsk
from .errors import AccessDenied, NotFound, PolicyViolation, StateError, ValidationError
from .harness import Harness, parse_ref
from .models import (
    CALLER_AGENT, CALLER_HUMAN, DISPATCH_CANCELLED, DISPATCH_CANCELLING, DISPATCH_DISPATCHED, DISPATCH_FAILED, DISPATCH_FINAL,
    DISPATCH_LOST, DISPATCH_PENDING, DISPATCH_RETURN_REFUSED, DISPATCH_RETURNED, HANDOFF_OPEN, ROLE_EDITOR, ROLE_OWNER, RUN_ACTIVE,
    RUN_CANCELLED, RUN_CANCELLING, RUN_FAILED, RUN_LOST, RUN_RUNNING, RUN_SUCCEEDED, AgentAddress, AgentHandle, Caller, Delegation,
    DelegationPolicy, Dispatch, HandoffPacket, Run, WorkAssignment, WorkerEvent, WorkerObservation,
)
from .runtime import Runtime, ScopedActions, WorkerPort
from .store import dumps, loads, new_id, now_iso

_MEMBER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
REF_SEP = "::"
LEASE_KEY = "runtime_lease"


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


# ---------------------------------------------------------------------------- worker pool


@dataclass(frozen=True)
class WorkerBinding:
    """One pool member: a port plus the explicit labels and the exact actors it serves in one project.

    ``provider`` and ``account`` are host-chosen labels (for example ``"claude-cli"`` and the account email the
    worker is configured to verify, or ``"fake"`` and ``"none"``). They are recorded on every routed run so a
    reader can see which configured identity did the work. They are labels, not credentials.
    """

    name: str
    port: WorkerPort
    provider: str
    account: str
    project_id: str
    actor_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _MEMBER_RE.match(self.name):
            raise ValidationError(f"pool member name {self.name!r} must be lowercase letters, digits and hyphens")
        for field_name in ("provider", "account", "project_id"):
            _nonempty(getattr(self, field_name), f"WorkerBinding.{field_name}")
        actors = tuple(dict.fromkeys(str(a).strip() for a in (self.actor_ids or ()) if str(a).strip()))
        if not actors:
            raise ValidationError(f"pool member {self.name!r} must list the actor ids it serves explicitly")
        object.__setattr__(self, "actor_ids", actors)
        for method in ("start", "drain", "steer", "cancel", "attach"):
            if not callable(getattr(self.port, method, None)):
                raise ValidationError(f"pool member {self.name!r}: port lacks WorkerPort.{method}")
        for attr in ("name", "is_fake"):
            if not hasattr(self.port, attr):
                raise ValidationError(f"pool member {self.name!r}: port lacks WorkerPort.{attr}")

    def describe(self) -> Dict[str, Any]:
        return {"member": self.name, "provider": self.provider, "account": self.account, "project_id": self.project_id,
                "actor_ids": list(self.actor_ids), "port": self.port.name, "is_fake": bool(self.port.is_fake),
                "model": getattr(self.port, "model_name", "")}


class WorkerPool:
    """A :class:`WorkerPort` that routes by ``(project_id, worker_actor_id)`` to exactly one configured member."""

    name = "worker-pool"
    model_name = ""

    def __init__(self, bindings: Sequence[WorkerBinding]) -> None:
        self._by_name: Dict[str, WorkerBinding] = {}
        self._routes: Dict[Tuple[str, str], WorkerBinding] = {}
        for binding in bindings:
            if not isinstance(binding, WorkerBinding):
                raise ValidationError("WorkerPool takes WorkerBinding instances")
            if binding.name in self._by_name:
                raise ValidationError(f"pool member {binding.name!r} is bound twice")
            self._by_name[binding.name] = binding
            for actor in binding.actor_ids:
                key = (binding.project_id, actor)
                if key in self._routes:
                    raise ValidationError(f"actor {actor} in project {binding.project_id} is bound to both "
                                          f"{self._routes[key].name!r} and {binding.name!r}; routes must be unambiguous")
                self._routes[key] = binding
        if not self._by_name:
            raise ValidationError("a worker pool needs at least one binding")

    @property
    def is_fake(self) -> bool:
        """Conservative pool-level label: fake if any member is. The runtime restamps each run with its routed member."""
        return any(b.port.is_fake for b in self._by_name.values())

    def members(self) -> List[str]:
        return sorted(self._by_name)

    def describe(self) -> List[Dict[str, Any]]:
        return [self._by_name[n].describe() for n in self.members()]

    def route(self, project_id: str, actor_id: str) -> WorkerBinding:
        binding = self._routes.get((project_id, actor_id))
        if binding is None:
            raise AccessDenied(f"no pool member is bound to actor {actor_id} in project {project_id}; "
                               "the host must bind one explicitly (there is no default member and no fallback)")
        return binding

    def identify(self, worker_ref: str) -> Dict[str, Any]:
        binding, _ = self._split(worker_ref)
        return {"member": binding.name, "name": binding.port.name, "is_fake": bool(binding.port.is_fake),
                "model": getattr(binding.port, "model_name", ""), "provider": binding.provider, "account": binding.account}

    # -- WorkerPort --------------------------------------------------------------------
    def start(self, assignment: WorkAssignment, actions: ScopedActions) -> str:
        binding = self.route(assignment.project_id, assignment.worker_actor_id)
        inner = str(binding.port.start(assignment, actions))
        return f"{binding.name}{REF_SEP}{inner}"

    def drain(self, worker_ref: str) -> List[WorkerEvent]:
        binding, inner = self._split(worker_ref)
        return list(binding.port.drain(inner))

    def steer(self, worker_ref: str, note: str) -> None:
        binding, inner = self._split(worker_ref)
        binding.port.steer(inner, note)

    def cancel(self, worker_ref: str) -> Optional[WorkerObservation]:
        binding, inner = self._split(worker_ref)
        return binding.port.cancel(inner)

    def attach(self, worker_ref: str, actions: ScopedActions) -> WorkerObservation:
        binding, inner = self._split(worker_ref)
        return binding.port.attach(inner, actions)

    def _split(self, worker_ref: str) -> Tuple[WorkerBinding, str]:
        name, sep, inner = str(worker_ref or "").partition(REF_SEP)
        if not sep or name not in self._by_name:
            raise StateError(f"worker_ref {worker_ref!r} names no configured pool member; the pool cannot say what became of it")
        return self._by_name[name], inner


# ---------------------------------------------------------------------------- runtime lease


@dataclass(frozen=True)
class RuntimeLease:
    token: str
    pid: int
    host: str
    acquired_at: str
    previous: Optional[Dict[str, Any]] = None
    takeover_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"token": self.token, "pid": self.pid, "host": self.host, "acquired_at": self.acquired_at,
                "previous": self.previous, "takeover_reason": self.takeover_reason}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except (OverflowError, ValueError, OSError):
        return False
    return True


def read_lease(conn: sqlite3.Connection) -> Optional[RuntimeLease]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (LEASE_KEY,)).fetchone()
    if row is None:
        return None
    raw = loads(row["value"]) or {}
    return RuntimeLease(str(raw.get("token", "")), int(raw.get("pid", 0) or 0), str(raw.get("host", "")), str(raw.get("acquired_at", "")),
                        raw.get("previous"), str(raw.get("takeover_reason", "")))


# ---------------------------------------------------------------------------- supervisor


class Supervisor:
    """One runtime owner over one data dir and one worker pool; durable handoff -> request linkage."""

    def __init__(self, harness: Harness, pool: WorkerPool, *, owner_token: Optional[str] = None, takeover: bool = False,
                 delegation_policy: Optional[DelegationPolicy] = None) -> None:
        if not isinstance(pool, WorkerPool):
            raise ValidationError("Supervisor routes through a WorkerPool; wrap a single port in one WorkerBinding")
        if delegation_policy is not None and not isinstance(delegation_policy, DelegationPolicy):
            raise ValidationError("delegation_policy must be a DelegationPolicy")
        self.h = harness
        self._conn: sqlite3.Connection = harness._conn
        self.pool = pool
        self.policy = delegation_policy or DelegationPolicy()
        self.rules = DelegationRules(self.policy)  # the one delegation admission layer; request_help goes through it, nothing else decides
        self.registry = AgentRegistry(harness)
        self.lease = self._acquire_lease(owner_token or new_id("sup"), takeover)
        self.rt = Runtime(harness, pool, owner_token=self.lease.token)
        self.rt.broker = self  # ScopedActions.request_help lands in request_help below; a bare runtime has no broker
        self._closed = False

    # ------------------------------------------------------------------ lease

    def _acquire_lease(self, token: str, takeover: bool) -> RuntimeLease:
        held = read_lease(self._conn)
        previous, reason = None, ""
        if held is not None and held.token != token:
            host = socket.gethostname()
            if takeover:
                reason = "explicit takeover"
            elif held.host != host:
                raise StateError(f"data dir {self.h.data_dir} is leased by runtime {held.token} on host {held.host!r} (pid {held.pid}); "
                                 f"this host ({host!r}) cannot check whether it is alive; close it there or pass takeover=True")
            elif _pid_alive(held.pid):
                raise StateError(f"data dir {self.h.data_dir} is leased by runtime {held.token} in live process {held.pid}; "
                                 "a second runtime would orphan its runs; close it first or pass takeover=True explicitly")
            else:
                reason = f"previous holder pid {held.pid} is gone"
            previous = held.to_dict()
            previous.pop("previous", None)
        elif held is not None:
            previous = held.to_dict()
            previous.pop("previous", None)
            reason = "same token re-acquired"
        lease = RuntimeLease(token, os.getpid(), socket.gethostname(), now_iso(), previous, reason)
        with self._conn:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (LEASE_KEY, dumps(lease.to_dict())))
        return lease

    def close(self) -> None:
        """Release the lease if this supervisor still holds it. Runs are untouched; the runtime's ledger is durable."""
        if self._closed:
            return
        self._closed = True
        held = read_lease(self._conn)
        if held is not None and held.token == self.lease.token:
            with self._conn:
                self._conn.execute("DELETE FROM meta WHERE key = ?", (LEASE_KEY,))

    def __enter__(self) -> "Supervisor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _open(self) -> None:
        if self._closed:
            raise StateError("supervisor is closed; open a new one (it will take the lease)")

    def describe(self) -> Dict[str, Any]:
        return {"lease": self.lease.to_dict(), "pool": self.pool.describe(), "closed": self._closed, "delegation_policy": self.policy.to_dict()}

    # ------------------------------------------------------------------ named-agent addressing (host side)

    def resolve_agent(self, caller: Caller, project_id: str, mention: str) -> AgentAddress:
        """``@handle`` -> an identity that is authorised to execute: active handle, pool binding, execution grant on its room.

        Unknown or revoked names raise ``NotFound``; an ambiguous display name raises ``ValidationError`` listing the
        candidates; a handle whose actor is unbound in the pool or ungranted in its room raises ``AccessDenied``.
        Nothing is guessed and there is no default agent.
        """
        self._open()
        return self._authorise(self.registry.require(caller, project_id, mention))

    def _authorise(self, handle: AgentHandle) -> AgentAddress:
        binding = self.pool.route(handle.project_id, handle.actor_id)  # AccessDenied: no pool member is bound
        if handle.room_id not in self.rt._allowed_rooms(handle.project_id, handle.actor_id):
            raise AccessDenied(f"{handle.mention} ({handle.actor_id}) has no active execution grant covering its registered room "
                               f"{handle.room_id}; a human project owner must grant it")
        return AgentAddress(handle.project_id, handle.handle, handle.display_name, handle.actor_id, handle.room_id, binding.name,
                            binding.provider, binding.account, handle.note)

    def helpers_for(self, project_id: str, actor_id: str) -> List[AgentAddress]:
        """Broker entry: the helpers ``actor_id`` may ask, rendered into its fresh context. Durable authority only.

        A helper is listed when it is an active handle in the project, not the asker itself, bound in the pool, granted
        execution on its registered room, and served under the same account as the asker's own member. An asker with
        no pool binding gets nothing. This is the same rule ``request_help`` enforces, so what is shown is what is allowed.
        """
        self._open()
        try:
            asker = self.pool.route(project_id, actor_id)
        except AccessDenied:
            return []
        out: List[AgentAddress] = []
        for handle in self.registry._active_all(project_id):
            if handle.actor_id == actor_id:
                continue
            try:
                addr = self._authorise(handle)
            except AccessDenied:
                continue
            if addr.account != asker.account:
                continue  # account boundary: fail closed, same authorised account only
            out.append(addr)
        return out

    def dispatch_to(self, caller: Caller, handoff_id: str, mention: str, *, goal: str, scope: Optional[str] = None,
                    terms: Sequence[str] = (), tags: Sequence[str] = ()) -> Dispatch:
        """:meth:`dispatch` by name: the packet must already be addressed to the handle's registered room."""
        self._open()
        packet = self.h.get_handoff(caller, handoff_id)
        addr = self.resolve_agent(caller, packet.to_project, mention)
        if packet.to_room != addr.room_id:
            raise ValidationError(f"handoff {handoff_id} is addressed to room {packet.to_room}, not to {addr.mention}'s registered room "
                                  f"{addr.room_id}; create the packet for that room or call dispatch with explicit ids")
        return self.dispatch(caller, handoff_id, target_actor_id=addr.actor_id, target_worker=addr.member, goal=goal, scope=scope, terms=terms, tags=tags)

    # ------------------------------------------------------------------ model-requested delegation (broker side)

    def request_help(self, actions: ScopedActions, run: Run, target: str, purpose: str, source_refs: Sequence[str], instructions: str) -> Delegation:
        """Broker entry for :meth:`ScopedActions.request_help`. Every refusal is logged on the asking run and re-raised;
        nothing durable exists until every check has passed."""
        self._open()
        try:
            return self._request_help(actions, run, target, purpose, source_refs, instructions)
        except (ValidationError, AccessDenied, NotFound, PolicyViolation, StateError) as exc:
            self.rt._log(run.run_id, run.attempt, 0, "help_refused", {"target": str(target)[:80] if isinstance(target, str) else repr(target)[:80],
                                                                       "error_type": type(exc).__name__, "error": str(exc)}, "supervisor", True, "")
            raise

    def _request_help(self, actions: ScopedActions, run: Run, target: str, purpose: str, source_refs: Sequence[str], instructions: str) -> Delegation:
        live = self.rt._run(run.run_id)
        if live.status != RUN_RUNNING or live.attempt != run.attempt or live.owner_token != self.rt.owner_token:
            raise StateError(f"run {run.run_id} attempt {run.attempt} is not live ({live.status}); help request refused")
        principal, rules, project_id = actions.caller, self.rules, live.project_id
        # 1. Shape: the shared rules (one handle token, bounded purpose and instructions).
        handle_name, purpose, instructions = rules.shape(target, purpose, instructions)
        # 2. Target: a registered, active handle in this run's project (unknown/revoked -> NotFound, ambiguous -> ValidationError).
        handle = self.registry.require(principal, project_id, handle_name)
        # 3. Sources: each a message or decision in this run's own room, readable by the worker principal; the count is the rules'.
        if not isinstance(source_refs, (list, tuple)) or not source_refs:
            raise ValidationError("source_refs must list at least one message or decision from the run's room; a help request carries what it is about")
        for ref in source_refs:
            kind, _ = parse_ref(ref)  # ValidationError: malformed ref
            if kind not in ("message", "decision"):
                raise ValidationError(f"source ref {ref} must be a message or decision; packets carry records, not other packets or skills")
        refs: List[str] = list(rules.sources(source_refs))
        for ref in refs:
            record = self.h.resolve_ref(principal, ref)
            if record.room_id != live.room_id:  # type: ignore[union-attr]
                raise ValidationError(f"source ref {ref} is not in the run's room {live.room_id}; a run hands off only what it can see in its own room")
        # 4. Place in the chain, budgets, self, loop, repeat and admission: the one shared delegation layer, fed from this
        #    runtime's durable ledger. It raises the refusal; otherwise it says start, wait or existing.
        lineage = self._lineage(live)
        per_run = self._conn.execute("SELECT COUNT(*) AS n FROM delegations WHERE parent_run_id = ?", (live.run_id,)).fetchone()["n"]
        per_root = self._conn.execute("SELECT COUNT(*) AS n FROM delegations WHERE root_request_id = ?", (lineage["root_request_id"],)).fetchone()["n"]
        priors = [PriorAsk(d.delegation_id, d.target_actor_id, d.purpose, d.instructions, tuple(d.source_refs))
                  for d in self._delegations_where("parent_run_id = ?", (live.run_id,))]
        admission = rules.decide(
            Ask(handle.handle, handle.actor_id, purpose, instructions, tuple(refs)),
            Lineage(lineage["depth"], lineage["root_request_id"], live.run_id, tuple(lineage["ancestor_actor_ids"]), live.worker_actor_id),
            per_run=per_run, per_root=per_root, priors=priors, active=self._active_runs(), target_busy=self._scope_busy(handle.room_id),
        )
        if admission.action == ADMIT_EXISTING:
            return self._delegation(admission.existing_id or "")
        depth = admission.depth
        # 5. Authority of the target: bound in the pool, granted in its room, same account. No fallback.
        binding = self.pool.route(project_id, handle.actor_id)
        if handle.room_id not in self.rt._allowed_rooms(project_id, handle.actor_id):
            raise AccessDenied(f"{handle.mention} ({handle.actor_id}) has no active execution grant covering its registered room "
                               f"{handle.room_id}; a human project owner must grant it")
        asker = self.pool.route(project_id, live.worker_actor_id)
        if binding.account != asker.account:
            raise AccessDenied(f"{handle.mention} runs on member {binding.name!r} under a different account than the asking run's member "
                               f"{asker.name!r}; cross-account help is refused (no cross-account policy exists)")
        # 6. A waiting verdict is a refusal here: this runtime has no queue, so a busy target room or a full parallel bound
        #    ends the ask now, with the owner of the scope named. Nothing is created.
        if admission.action == ADMIT_WAIT:
            self.rt._require_scope_free(handle.room_id, handle.room_id)  # raises StateError naming the request that owns the room
            raise StateError(f"{admission.reason}; this runtime has no queue, so the ask is refused rather than kept waiting")
        # 7. Effects: the packet (as the worker principal, so every harness rule applies), then the ordinary dispatch, then the record.
        packet = self.h.create_handoff(principal, live.room_id, handle.room_id, purpose, refs, instructions=instructions)
        self.rt._track(live.run_id, packet.ref, handoff_id=packet.handoff_id)
        try:
            dispatch = self.dispatch(principal, packet.handoff_id, target_actor_id=handle.actor_id, target_worker=binding.name, goal=purpose)
        except Exception:
            self._withdraw_packet(packet, live, f"help request by run {live.run_id} could not be dispatched")
            raise
        did, ts = new_id("dlg"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO delegations (delegation_id, project_id, root_request_id, parent_request_id, parent_run_id, parent_attempt, depth, "
                "requested_by, origin_room, target_handle, target_actor_id, target_room, target_worker, purpose, instructions, source_refs_json, "
                "handoff_id, dispatch_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (did, project_id, lineage["root_request_id"], live.request_id, live.run_id, live.attempt, depth, live.worker_actor_id, live.room_id,
                 handle.handle, handle.actor_id, handle.room_id, binding.name, purpose, instructions, dumps(refs), packet.handoff_id,
                 dispatch.dispatch_id, ts),
            )
        self.rt._log(live.run_id, live.attempt, 0, "help_requested", {
            "delegation_id": did, "target": handle.mention, "target_actor_id": handle.actor_id, "target_room": handle.room_id, "member": binding.name,
            "provider": binding.provider, "account": binding.account, "handoff_id": packet.handoff_id, "dispatch_id": dispatch.dispatch_id,
            "depth": depth, "root_request_id": lineage["root_request_id"], "source_refs": refs}, "supervisor", True, "")
        return self._delegation(did)

    def _active_runs(self) -> int:
        """Runs this runtime owns that are running, unknown or cancelling: the count the shared rules bound with ``max_parallel``."""
        marks = ", ".join("?" for _ in RUN_ACTIVE)
        return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM runs WHERE owner_token = ? AND status IN ({marks})",
                                      (self.rt.owner_token, *RUN_ACTIVE)).fetchone()["n"])

    def _scope_busy(self, room_id: str) -> bool:
        """Whether an active run already owns the target room's scope (the same test ``Runtime._require_scope_free`` raises on)."""
        try:
            self.rt._require_scope_free(room_id, room_id)
        except StateError:
            return True
        return False

    def lineage(self, caller: Caller, run_id: str) -> Dict[str, Any]:
        """Where a run sits in a delegation chain: model-requested depth, the original request, the actors above it."""
        return self._lineage(self.rt.get_run(caller, run_id))

    def _lineage(self, run: Run) -> Dict[str, Any]:
        depth, root, ancestors, chain = 0, run.request_id, [], []
        current = run
        for _ in range(64):  # the chain is bounded by max_depth; this is a hard stop against corrupted rows
            drow = self._conn.execute("SELECT dispatch_id, origin_run_id FROM dispatches WHERE request_id = ?", (current.request_id,)).fetchone()
            if drow is None:
                break
            lrow = self._conn.execute("SELECT delegation_id, parent_run_id FROM delegations WHERE dispatch_id = ?", (drow["dispatch_id"],)).fetchone()
            parent_run_id = lrow["parent_run_id"] if lrow else drow["origin_run_id"]
            if lrow:
                depth += 1
                chain.insert(0, lrow["delegation_id"])
            if not parent_run_id:
                break  # a human-created packet: the chain starts here
            parent = self.rt._run(parent_run_id)
            ancestors.insert(0, parent.worker_actor_id)
            root, current = parent.request_id, parent
        return {"depth": depth, "root_request_id": root, "ancestor_actor_ids": ancestors, "delegation_ids": chain}

    def get_delegation(self, caller: Caller, delegation_id: str) -> Delegation:
        d = self._delegation(delegation_id)
        self._require_read(caller, self._dispatch(d.dispatch_id))
        return d

    def list_delegations(self, caller: Caller, *, root_request_id: Optional[str] = None, run_id: Optional[str] = None) -> List[Delegation]:
        self.h._check_caller(caller)
        where, params = ["1 = 1"], []
        if root_request_id:
            where.append("root_request_id = ?")
            params.append(root_request_id)
        if run_id:
            where.append("parent_run_id = ?")
            params.append(run_id)
        return [d for d in self._delegations_where(" AND ".join(where), tuple(params)) if self._can_read(caller, self._dispatch(d.dispatch_id))]

    def _delegations_where(self, where: str, params: Tuple[Any, ...]) -> List[Delegation]:
        rows = self._conn.execute(f"SELECT * FROM delegations WHERE {where} ORDER BY seq", params).fetchall()
        return [self._delegation_from_row(r) for r in rows]

    def _delegation(self, delegation_id: str) -> Delegation:
        row = self._conn.execute("SELECT * FROM delegations WHERE delegation_id = ?", (delegation_id,)).fetchone()
        if row is None:
            raise NotFound(f"delegation {delegation_id} not found")
        return self._delegation_from_row(row)

    def _delegation_from_row(self, row: sqlite3.Row) -> Delegation:
        dispatch = self._dispatch(row["dispatch_id"])
        return Delegation(
            delegation_id=row["delegation_id"], project_id=row["project_id"], root_request_id=row["root_request_id"],
            parent_request_id=row["parent_request_id"], parent_run_id=row["parent_run_id"], parent_attempt=int(row["parent_attempt"]),
            depth=int(row["depth"]), requested_by=row["requested_by"], origin_room=row["origin_room"], target_handle=row["target_handle"],
            target_actor_id=row["target_actor_id"], target_room=row["target_room"], target_worker=row["target_worker"], purpose=row["purpose"],
            instructions=row["instructions"], source_refs=list(loads(row["source_refs_json"]) or []), handoff_id=row["handoff_id"],
            dispatch_id=row["dispatch_id"], created_at=row["created_at"], status=dispatch.status, result=dispatch.result, error=dispatch.error,
        )

    def _note_help_outcome(self, d: Dispatch, kind: str, payload: Dict[str, Any]) -> None:
        """If this dispatch was a model-requested delegation, tell the asking run's ledger how it ended."""
        row = self._conn.execute("SELECT delegation_id, parent_run_id FROM delegations WHERE dispatch_id = ?", (d.dispatch_id,)).fetchone()
        if row is None:
            return
        parent = self.rt._run(row["parent_run_id"])
        self.rt._log(parent.run_id, parent.attempt, 0, kind, {"delegation_id": row["delegation_id"], "dispatch_id": d.dispatch_id, **payload},
                     "supervisor", True, "")

    # ------------------------------------------------------------------ dispatch

    def dispatch(
        self, caller: Caller, handoff_id: str, *, target_actor_id: str, target_worker: str, goal: str,
        scope: Optional[str] = None, terms: Sequence[str] = (), tags: Sequence[str] = (),
    ) -> Dispatch:
        """Link one open packet to one request in its target room, executed by the named pool member.

        Idempotent per packet: the same ``handoff_id`` with the same target and goal returns the existing dispatch;
        with a different target or goal it is refused. ``target_worker`` must be the member the pool binds to
        ``target_actor_id`` in the target project, otherwise the dispatch is refused before anything is submitted.
        """
        self._open()
        packet = self.h.get_handoff(caller, handoff_id)
        self._require_dispatch_control(caller, packet)
        goal = _nonempty(goal, "goal")
        target_actor_id = _nonempty(target_actor_id, "target_actor_id")
        target_worker = _nonempty(target_worker, "target_worker")
        existing = self._by_handoff(handoff_id)
        if existing is not None:
            asked = {"target_actor_id": target_actor_id, "target_worker": target_worker, "goal": goal}
            had = {"target_actor_id": existing.target_actor_id, "target_worker": existing.target_worker, "goal": existing.goal}
            changed = sorted(k for k in asked if asked[k] != had[k])
            if changed:
                raise ValidationError(f"handoff {handoff_id} is already dispatched as {existing.dispatch_id} with a different "
                                      f"{', '.join(changed)}; a packet is dispatched at most once")
            return self._settle_one(existing)
        if packet.status != HANDOFF_OPEN:
            raise StateError(f"handoff {handoff_id} is {packet.status}; only an open packet can be dispatched")
        binding = self.pool.route(packet.to_project, target_actor_id)
        if binding.name != target_worker:
            raise AccessDenied(f"target_worker {target_worker!r} is not the pool member bound to {target_actor_id} in project "
                               f"{packet.to_project} (that is {binding.name!r}); routes are explicit and there is no fallback")
        origin_run_id = self._origin_run_id(packet)
        request = self.rt.submit(caller, packet.to_room, goal, worker_actor_id=target_actor_id, request_key=f"dispatch:{handoff_id}",
                                 scope=scope, terms=terms, tags=tags)
        did, ts = new_id("dsp"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO dispatches (dispatch_id, handoff_id, origin_project, origin_room, origin_run_id, target_project, target_room, "
                "target_actor_id, target_worker, provider, account, goal, request_id, dispatched_by, dispatcher_kind, status, run_id, error, "
                "result_json, omitted_refs_json, created_at, updated_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '[]', ?, ?, NULL)",
                (did, handoff_id, packet.from_project, packet.from_room, origin_run_id, packet.to_project, packet.to_room, target_actor_id,
                 binding.name, binding.provider, binding.account, goal, request.request_id, caller.actor_id, caller.kind, DISPATCH_DISPATCHED,
                 request.current_run_id, ts, ts),
            )
        run = self.rt._run(request.current_run_id) if request.current_run_id else None
        if run is not None:
            self.rt._log(run.run_id, run.attempt, 0, "dispatched", {"dispatch_id": did, "handoff_id": handoff_id, "origin_room": packet.from_room,
                                                                     "origin_run_id": origin_run_id, "member": binding.name, "provider": binding.provider,
                                                                     "account": binding.account, "by": caller.label()}, "supervisor", True, "")
        return self._settle_one(self._dispatch(did))

    def _require_dispatch_control(self, caller: Caller, packet: HandoffPacket) -> None:
        """The packet creator, or a human owner/editor of the origin project. Nobody else routes a packet's execution."""
        if caller.actor_id == packet.created_by and caller.kind == packet.creator_kind:
            return
        if caller.kind == CALLER_HUMAN and self.h._role(packet.from_project, caller.actor_id) in (ROLE_OWNER, ROLE_EDITOR):
            return
        raise AccessDenied(f"{caller.label()} may not dispatch handoff {packet.handoff_id}: not its creator or a human owner/editor of the origin project")

    def _origin_run_id(self, packet: HandoffPacket) -> Optional[str]:
        """The run that created the packet through ScopedActions.delegate, if any (a human-created packet has none)."""
        row = self._conn.execute("SELECT run_id FROM runs WHERE room_id = ? AND handoff_ids_json LIKE ? ORDER BY seq DESC LIMIT 1",
                                 (packet.from_room, f'%"{packet.handoff_id}"%')).fetchone()
        return row["run_id"] if row else None

    # ------------------------------------------------------------------ progress

    def pump(self, *, max_events: int = 1000) -> int:
        """Drain the pool for owned runs, apply the events, then settle every pending dispatch. Returns events seen."""
        self._open()
        seen = self.rt.pump(max_events=max_events)
        self.settle()
        return seen

    def reconcile(self, caller: Caller) -> List[Run]:
        """Runtime reconciliation (human only), then settle. Nothing is replayed; lost target runs stay lost."""
        self._open()
        runs = self.rt.reconcile(caller)
        self.settle()
        return runs

    def settle(self) -> List[Dispatch]:
        """Follow every pending dispatch's target run and apply the outcome. Idempotent."""
        self._open()
        marks = ", ".join("?" for _ in DISPATCH_PENDING)
        rows = self._conn.execute(f"SELECT * FROM dispatches WHERE status IN ({marks}) ORDER BY seq", DISPATCH_PENDING).fetchall()
        return [self._settle_one(self._from_row(r)) for r in rows]

    def _settle_one(self, d: Dispatch) -> Dispatch:
        if d.status in DISPATCH_FINAL:
            return d
        request = self.rt._request(d.request_id)
        run = self.rt._run(request.current_run_id) if request.current_run_id else None
        if run is None:
            return d
        packet = self._packet(d.handoff_id)
        if run.run_id != d.run_id:  # an explicit resubmit started a new attempt; re-link to it
            self._update(d.dispatch_id, status=DISPATCH_DISPATCHED, run_id=run.run_id, error=None)
            self.rt._log(run.run_id, run.attempt, 0, "dispatched", {"dispatch_id": d.dispatch_id, "handoff_id": d.handoff_id, "relinked": True},
                         "supervisor", True, "")
            d = self._dispatch(d.dispatch_id)
        if run.status == RUN_SUCCEEDED:
            self._return(d, run, packet)
        elif run.status == RUN_FAILED:
            if d.status != DISPATCH_FAILED:
                self._update(d.dispatch_id, status=DISPATCH_FAILED, error=run.error)
        elif run.status == RUN_LOST:
            if d.status != DISPATCH_LOST:
                self._update(d.dispatch_id, status=DISPATCH_LOST, error=run.error)
        elif run.status == RUN_CANCELLED:
            self._withdraw_packet(packet, run, f"dispatch {d.dispatch_id} cancelled; run {run.run_id} stopped")
            self._update(d.dispatch_id, status=DISPATCH_CANCELLED, error=run.error, closed=True)
        elif run.status == RUN_CANCELLING:
            if d.status != DISPATCH_CANCELLING:
                self._update(d.dispatch_id, status=DISPATCH_CANCELLING, error=run.error)
        else:  # running or unknown
            if packet.status != HANDOFF_OPEN:
                self._propagate_cancel(d, run, f"origin handoff {d.handoff_id} is {packet.status}; dependent run stopped")
            elif d.status != DISPATCH_DISPATCHED:
                self._update(d.dispatch_id, status=DISPATCH_DISPATCHED)
        return self._dispatch(d.dispatch_id)

    def _return(self, d: Dispatch, run: Run, packet: HandoffPacket) -> None:
        result = run.result
        if result is None:
            self._update(d.dispatch_id, status=DISPATCH_RETURN_REFUSED, error="run succeeded without a stored result", closed=True)
            return
        result_ref = f"message:{result.result_message_id}"
        refs, omitted = [result_ref], []
        for ref in result.refs:
            (refs if self._in_target(ref, packet) else omitted).append(ref)
        if packet.status != HANDOFF_OPEN:
            error = f"handoff {packet.handoff_id} is {packet.status}; the result stays in room {packet.to_room} as {result_ref}"
            self._update(d.dispatch_id, status=DISPATCH_RETURN_REFUSED, error=error, omitted_refs=omitted, closed=True)
            self.rt._log(run.run_id, run.attempt, 0, "dispatch_return_refused", {"dispatch_id": d.dispatch_id, "error": error}, "supervisor", True, "")
            return
        principal = Caller(run.worker_actor_id, kind=CALLER_AGENT, session_id=run.session_id)
        try:
            returned = self.h.return_handoff(principal, packet.handoff_id, result.summary, refs)
        except (PolicyViolation, AccessDenied, StateError, ValidationError, NotFound) as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._update(d.dispatch_id, status=DISPATCH_RETURN_REFUSED, error=error, omitted_refs=omitted, closed=True)
            self.rt._log(run.run_id, run.attempt, 0, "dispatch_return_refused", {"dispatch_id": d.dispatch_id, "error": error, "refs": refs},
                         "supervisor", True, "")
            self._note_help_outcome(d, "help_return_refused", {"error": error})
            return
        hr = returned.result
        payload = {"summary": hr.summary, "refs": list(hr.result_refs), "result_message_id": hr.result_message_id, "returned_at": hr.returned_at,
                   "returned_by": hr.returned_by, "returned_by_kind": hr.returned_by_kind, "truncated": hr.truncated,
                   "worker": run.worker_name, "worker_is_fake": run.worker_is_fake, "target_run_id": run.run_id, "target_result_ref": result_ref}
        self._update(d.dispatch_id, status=DISPATCH_RETURNED, result=payload, omitted_refs=omitted, error=None, closed=True)
        self.rt._log(run.run_id, run.attempt, 0, "dispatch_returned", {"dispatch_id": d.dispatch_id, "origin_room": packet.from_room,
                                                                         "result_message_id": hr.result_message_id, "omitted_refs": omitted},
                     "supervisor", True, "")
        self._note_help_outcome(d, "help_returned", {"result_message_id": hr.result_message_id, "target_run_id": run.run_id, "refs": list(hr.result_refs)})

    def _in_target(self, ref: str, packet: HandoffPacket) -> bool:
        """A packet returns only refs from its target room (or the target project's skills); anything else is omitted, and listed."""
        try:
            kind, ident = parse_ref(ref)
            if kind == "message":
                return self.h._message(ident).room_id == packet.to_room
            if kind == "decision":
                return self.h._decision(ident).room_id == packet.to_room
            if kind == "skill":
                return ident.split("/", 1)[0] == packet.to_project
        except (ValidationError, NotFound):
            return False
        return False

    def _withdraw_packet(self, packet: HandoffPacket, run: Optional[Run], note: str) -> None:
        if packet.status != HANDOFF_OPEN:
            return
        creator = Caller(packet.created_by, kind=packet.creator_kind)
        try:
            self.h.cancel_handoff(creator, packet.handoff_id, note=note)
        except Exception as exc:  # never let cleanup mask the dispatch's own state
            if run is not None:
                self.rt._log(run.run_id, run.attempt, 0, "dispatch_packet_cleanup_error", {"handoff_id": packet.handoff_id, "error": str(exc)},
                             "supervisor", True, "")

    def _propagate_cancel(self, d: Dispatch, run: Run, note: str) -> None:
        dispatcher = Caller(d.dispatched_by, kind=d.dispatcher_kind)
        try:
            after = self.rt.cancel(dispatcher, run.run_id, note=note)
        except Exception as exc:
            self._update(d.dispatch_id, error=f"cancel propagation failed: {type(exc).__name__}: {exc}")
            self.rt._log(run.run_id, run.attempt, 0, "dispatch_cancel_error", {"dispatch_id": d.dispatch_id, "error": str(exc)}, "supervisor", True, "")
            return
        if after.status == RUN_CANCELLED:
            self._update(d.dispatch_id, status=DISPATCH_CANCELLED, error=after.error, closed=True)
        else:
            self._update(d.dispatch_id, status=DISPATCH_CANCELLING, error=note)

    # ------------------------------------------------------------------ cancel

    def cancel(self, caller: Caller, dispatch_id: str, note: str = "") -> Dispatch:
        """Withdraw the packet and ask the runtime to stop the target run. The dispatch is ``cancelling`` until the
        target port confirms the stop; :meth:`status` reports ``stop_confirmed`` accordingly. Calling again retries."""
        self._open()
        d = self.get_dispatch(caller, dispatch_id)
        if d.status in DISPATCH_FINAL:
            raise StateError(f"dispatch {dispatch_id} is already {d.status}")
        request = self.rt._request(d.request_id)
        run = self.rt._run(request.current_run_id)
        if run.status == RUN_FAILED:  # nothing to stop; control is still required to withdraw the packet
            self.rt._require_control(caller, request)
            self._withdraw_packet(self._packet(d.handoff_id), run, note or f"dispatch {dispatch_id} cancelled after failure")
            self._update(dispatch_id, status=DISPATCH_CANCELLED, error=note or run.error, closed=True)
            return self._dispatch(dispatch_id)
        after = self.rt.cancel(caller, run.run_id, note=note)  # runtime enforces who may control the request
        self._withdraw_packet(self._packet(d.handoff_id), after, note or f"dispatch {dispatch_id} cancelled")
        if after.status == RUN_CANCELLED:
            self._update(dispatch_id, status=DISPATCH_CANCELLED, error=after.error, closed=True)
        else:
            self._update(dispatch_id, status=DISPATCH_CANCELLING, error=note or None)
        return self._dispatch(dispatch_id)

    # ------------------------------------------------------------------ reads

    def get_dispatch(self, caller: Caller, dispatch_id: str) -> Dispatch:
        d = self._dispatch(dispatch_id)
        self._require_read(caller, d)
        return d

    def find_dispatch(self, caller: Caller, handoff_id: str) -> Optional[Dispatch]:
        d = self._by_handoff(handoff_id)
        if d is None:
            return None
        self._require_read(caller, d)
        return d

    def list_dispatches(self, caller: Caller, *, pending_only: bool = False) -> List[Dispatch]:
        self.h._check_caller(caller)
        rows = self._conn.execute("SELECT * FROM dispatches ORDER BY seq").fetchall()
        out = []
        for row in rows:
            d = self._from_row(row)
            if pending_only and not d.pending:
                continue
            if self._can_read(caller, d):
                out.append(d)
        return out

    def status(self, caller: Caller, dispatch_id: str) -> Dict[str, Any]:
        """One-call view of the linkage: dispatch, packet, target run, and whether a requested stop is confirmed."""
        d = self.get_dispatch(caller, dispatch_id)
        run = self.rt._run(d.run_id) if d.run_id else None
        packet = self._packet(d.handoff_id)
        stop_requested = d.status in (DISPATCH_CANCELLING, DISPATCH_CANCELLED) or bool(run and run.status in (RUN_CANCELLING, RUN_CANCELLED))
        return {
            "dispatch_id": d.dispatch_id, "status": d.status, "pending": d.pending, "handoff_id": d.handoff_id, "handoff_status": packet.status,
            "origin_room": d.origin_room, "origin_run_id": d.origin_run_id, "target_room": d.target_room, "target_actor_id": d.target_actor_id,
            "target_worker": d.target_worker, "provider": d.provider, "account": d.account, "request_id": d.request_id,
            "run_id": run.run_id if run else None, "run_status": run.status if run else None, "run_attempt": run.attempt if run else None,
            "run_worker": {"name": run.worker_name, "is_fake": run.worker_is_fake} if run else None,
            "stop_requested": stop_requested, "stop_confirmed": (run.status == RUN_CANCELLED) if (stop_requested and run) else None,
            "result": d.result, "omitted_refs": list(d.omitted_refs), "error": d.error,
        }

    # ------------------------------------------------------------------ internals

    def _can_read(self, caller: Caller, d: Dispatch) -> bool:
        return self.h._can_read_room(caller, self.h._room(d.origin_room)) or self.h._can_read_room(caller, self.h._room(d.target_room))

    def _require_read(self, caller: Caller, d: Dispatch) -> None:
        self.h._check_caller(caller)
        if not self._can_read(caller, d):
            raise AccessDenied(f"{caller.label()} may not read dispatch {d.dispatch_id}: no access to its origin or target room")

    def _packet(self, handoff_id: str) -> HandoffPacket:
        row = self._conn.execute("SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
        if row is None:
            raise NotFound(f"handoff {handoff_id} not found")
        return Harness._handoff_from_row(row)

    def _update(self, dispatch_id: str, *, closed: bool = False, **fields: Any) -> None:
        sets, params = ["updated_at = ?"], [now_iso()]
        for key, value in fields.items():
            if key == "result":
                sets.append("result_json = ?")
                params.append(dumps(value) if value is not None else None)
            elif key == "omitted_refs":
                sets.append("omitted_refs_json = ?")
                params.append(dumps(list(value or [])))
            elif key in ("status", "run_id", "error"):
                sets.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(key)
        if closed:
            sets.append("closed_at = ?")
            params.append(now_iso())
        params.append(dispatch_id)
        with self._conn:
            self._conn.execute(f"UPDATE dispatches SET {', '.join(sets)} WHERE dispatch_id = ?", params)

    def _dispatch(self, dispatch_id: str) -> Dispatch:
        row = self._conn.execute("SELECT * FROM dispatches WHERE dispatch_id = ?", (dispatch_id,)).fetchone()
        if row is None:
            raise NotFound(f"dispatch {dispatch_id} not found")
        return self._from_row(row)

    def _by_handoff(self, handoff_id: str) -> Optional[Dispatch]:
        row = self._conn.execute("SELECT * FROM dispatches WHERE handoff_id = ?", (handoff_id,)).fetchone()
        return self._from_row(row) if row else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Dispatch:
        return Dispatch(
            dispatch_id=row["dispatch_id"], handoff_id=row["handoff_id"], origin_project=row["origin_project"], origin_room=row["origin_room"],
            origin_run_id=row["origin_run_id"], target_project=row["target_project"], target_room=row["target_room"],
            target_actor_id=row["target_actor_id"], target_worker=row["target_worker"], provider=row["provider"], account=row["account"],
            goal=row["goal"], request_id=row["request_id"], dispatched_by=row["dispatched_by"], dispatcher_kind=row["dispatcher_kind"],
            status=row["status"], run_id=row["run_id"], error=row["error"], result=loads(row["result_json"]),
            omitted_refs=list(loads(row["omitted_refs_json"]) or []), created_at=row["created_at"], updated_at=row["updated_at"],
            closed_at=row["closed_at"],
        )
