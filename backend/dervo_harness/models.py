"""Plain dataclasses returned by the harness. No behaviour beyond formatting helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Project roles (explicit, persisted in project_members).
ROLE_OWNER = "owner"
ROLE_EDITOR = "editor"
ROLE_AGENT = "agent"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_OWNER, ROLE_EDITOR, ROLE_AGENT, ROLE_VIEWER)

# Caller kinds. An agent caller never approves skills or overrides transfer policy.
CALLER_HUMAN = "human"
CALLER_AGENT = "agent"
CALLER_KINDS = (CALLER_HUMAN, CALLER_AGENT)

# Project domains. Work -> personal transfer is refused by default.
DOMAIN_WORK = "work"
DOMAIN_PERSONAL = "personal"
DOMAINS = (DOMAIN_WORK, DOMAIN_PERSONAL)

# Room visibility. Private -> shared transfer is refused by default.
VIS_PRIVATE = "private"
VIS_SHARED = "shared"
VISIBILITIES = (VIS_PRIVATE, VIS_SHARED)

# Message kinds. Callers may record message/note; the rest are written by the harness.
KIND_MESSAGE = "message"
KIND_NOTE = "note"
KIND_DECISION = "decision"
KIND_HANDOFF_SENT = "handoff_sent"
KIND_HANDOFF_RECEIVED = "handoff_received"
KIND_HANDOFF_RESULT = "handoff_result"
KIND_RUN = "run"  # runtime lifecycle notes (started, cancelled, failed)
KIND_RUN_RESULT = "run_result"  # the bounded, source-linked result of a run
USER_MESSAGE_KINDS = (KIND_MESSAGE, KIND_NOTE)

# Run statuses (runtime). Active = running, unknown or cancelling: the scope stays owned and nothing may
# overlap it. Nothing leaves ``lost`` without an explicit resubmit.
RUN_RUNNING = "running"
RUN_UNKNOWN = "unknown"  # a previous runtime owned it; no observation since restart
RUN_CANCELLING = "cancelling"  # stop requested; the port has not yet confirmed the worker is gone
RUN_LOST = "lost"  # the port positively reported no such worker; not replayed automatically
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"  # the port confirmed termination (exit / ack), or the run was already lost
RUN_ACTIVE = (RUN_RUNNING, RUN_UNKNOWN, RUN_CANCELLING)
RUN_TERMINAL = (RUN_LOST, RUN_SUCCEEDED, RUN_FAILED, RUN_CANCELLED)

# Request statuses.
REQUEST_ACTIVE = "active"
REQUEST_CLOSED = "closed"

# Worker event kinds accepted by the runtime.
EVENT_PROGRESS = "progress"
EVENT_RESULT = "result"
EVENT_FAILED = "failed"
EVENT_STOPPED = "stopped"  # the worker confirms it has terminated after a cancel request
WORKER_EVENT_KINDS = (EVENT_PROGRESS, EVENT_RESULT, EVENT_FAILED, EVENT_STOPPED)
WORKER_TERMINAL_EVENT_KINDS = (EVENT_RESULT, EVENT_FAILED, EVENT_STOPPED)

# Handoff statuses.
HANDOFF_OPEN = "open"
HANDOFF_RETURNED = "returned"
HANDOFF_CANCELLED = "cancelled"

# Dispatch statuses (supervisor). A dispatch links one open handoff packet to one request in the target room.
# Pending = the linkage still needs the target run to settle (a failed or lost run can be resubmitted explicitly).
DISPATCH_DISPATCHED = "dispatched"
DISPATCH_CANCELLING = "cancelling"  # stop requested on the target run; not yet confirmed by its port
DISPATCH_FAILED = "failed"  # target run failed; packet stays open; an explicit resubmit re-links
DISPATCH_LOST = "lost"  # target worker reported missing at reconciliation; never replayed automatically
DISPATCH_RETURNED = "returned"  # result posted into the origin room via the packet
DISPATCH_RETURN_REFUSED = "return_refused"  # run succeeded but the return violated a transfer rule; nothing crossed
DISPATCH_CANCELLED = "cancelled"  # the target port confirmed the stop (or the run was lost and closed)
DISPATCH_PENDING = (DISPATCH_DISPATCHED, DISPATCH_CANCELLING, DISPATCH_FAILED, DISPATCH_LOST)
DISPATCH_FINAL = (DISPATCH_RETURNED, DISPATCH_RETURN_REFUSED, DISPATCH_CANCELLED)

# Mention resolution statuses (addressing). Only ``resolved`` names a target; nothing else is ever acted on.
MENTION_RESOLVED = "resolved"
MENTION_UNKNOWN = "unknown"  # no active handle or display name matches (includes revoked handles)
MENTION_AMBIGUOUS = "ambiguous"  # more than one display name matches; the candidates are listed, none is chosen
MENTION_QUOTED = "quoted"  # found inside quoted, fenced, inline-code, blockquote or context text: never actionable

# Skill version statuses.
SKILL_PROPOSED = "proposed"
SKILL_APPROVED = "approved"
SKILL_REJECTED = "rejected"

# Skill version origins (provenance).
ORIGIN_HUMAN = "human"
ORIGIN_HUMAN_PROPOSAL = "human_proposal"
ORIGIN_AGENT_SUGGESTION = "agent_suggestion"
ORIGIN_IMPORT = "markdown_import"


@dataclass(frozen=True)
class Caller:
    """Who is acting, as asserted by trusted in-process code.

    A ``Caller`` is a principal claim, not a credential. The harness never authenticates it;
    it only decides what that principal may do from persisted roles, memberships and grants.
    Any future service layer must derive ``actor_id`` and ``kind`` from its own authentication
    and must never accept them from request input. ``actor_id`` must be a project member for
    anything to succeed.
    """

    actor_id: str
    kind: str = CALLER_HUMAN
    session_id: str = ""  # optional, recorded in provenance notes only

    def __post_init__(self) -> None:
        from .errors import ValidationError

        if not isinstance(self.actor_id, str) or not self.actor_id.strip():
            raise ValidationError("Caller.actor_id must be a non-empty string")
        if self.kind not in CALLER_KINDS:
            raise ValidationError(f"Caller.kind must be one of {CALLER_KINDS}, got {self.kind!r}")

    def label(self) -> str:
        return f"{self.kind}:{self.actor_id}"


@dataclass
class Project:
    project_id: str
    name: str
    domain: str
    owner_id: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Room:
    room_id: str
    project_id: str
    name: str
    visibility: str
    created_by: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Message:
    message_id: str
    seq: int
    room_id: str
    project_id: str
    author_id: str
    author_kind: str
    kind: str
    content: str
    refs: List[str]
    created_at: str

    @property
    def ref(self) -> str:
        return f"message:{self.message_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    decision_id: str
    seq: int
    room_id: str
    project_id: str
    author_id: str
    author_kind: str
    title: str
    rationale: str
    source_refs: List[str]
    message_id: str
    created_at: str

    @property
    def ref(self) -> str:
        return f"decision:{self.decision_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffPolicy:
    """Bounds and explicit overrides for one handoff. Defaults refuse every downgrade."""

    max_items: int = 25
    max_excerpt_chars: int = 2000
    max_summary_chars: int = 4000
    allow_private_to_shared: bool = False
    allow_work_to_personal: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HandoffItem:
    ref: str
    kind: str
    room_id: str
    author_id: str
    author_kind: str
    created_at: str
    excerpt: str
    truncated: bool


@dataclass
class HandoffResult:
    summary: str
    result_refs: List[str]
    returned_by: str
    returned_by_kind: str
    returned_at: str
    result_message_id: str
    truncated: bool


@dataclass
class HandoffPacket:
    handoff_id: str
    from_project: str
    from_room: str
    to_project: str
    to_room: str
    created_by: str
    creator_kind: str
    purpose: str
    instructions: str
    items: List[HandoffItem]
    policy: HandoffPolicy
    status: str
    created_at: str
    sent_message_id: str
    received_message_id: str
    result: Optional[HandoffResult] = None
    closed_at: Optional[str] = None
    closed_by: Optional[str] = None

    @property
    def ref(self) -> str:
        return f"handoff:{self.handoff_id}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillVersion:
    skill_id: str
    project_id: str
    name: str
    version: int
    author_id: str
    author_kind: str
    origin: str
    status: str
    description: str
    body: str
    tags: List[str]
    sources: List[str]
    content_hash: str
    note: str
    created_at: str
    reviewed_by: Optional[str]
    reviewed_at: Optional[str]
    review_note: Optional[str]
    is_current: bool

    @property
    def ref(self) -> str:
        return f"skill:{self.project_id}/{self.name}@{self.version}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillEvent:
    seq: int
    skill_id: str
    version: Optional[int]
    event: str
    actor_id: str
    actor_kind: str
    note: str
    created_at: str


@dataclass
class SkillSelection:
    """Result of deterministic skill selection. ``method`` states exactly how it was ranked."""

    method: str
    query_terms: List[str]
    query_tags: List[str]
    skills: List[SkillVersion]
    scores: Dict[str, int] = field(default_factory=dict)


@dataclass
class SessionContext:
    """Bounded, source-linked context for a fresh agent session in one room."""

    project: Project
    room: Room
    caller_id: str
    caller_kind: str
    generated_at: str
    decisions: List[Decision]
    messages: List[Message]
    incoming_handoffs: List[HandoffPacket]
    skills: SkillSelection
    bounds: Dict[str, int]
    selection_note: str
    # Helpers this session's actor may ask through ``request_help``: filled by the runtime from its broker (the supervisor),
    # from durable authority only (active handle, pool binding, execution grant, same account). Empty without a broker.
    helpers: List["AgentAddress"] = field(default_factory=list)

    def render(self) -> str:
        from .session import render_session_context

        return render_session_context(self)


# ---------------------------------------------------------------- runtime records


@dataclass
class ExecutionGrant:
    """A human project owner lets ``actor_id`` run work in exactly these rooms without per-run approval."""

    grant_id: str
    project_id: str
    actor_id: str
    rooms: List[str]
    granted_by: str
    note: str
    created_at: str
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None


@dataclass
class Request:
    """A durable ask: do ``goal`` in ``room_id`` as ``worker_actor_id``. Idempotent on ``request_key``.

    ``resource`` is an optional caller-chosen key for a shared external thing (a repository path, a
    deployment target). At most one active run may hold a given resource across every room and project
    in the data dir. ``scope`` remains per room.
    """

    request_id: str
    request_key: str
    project_id: str
    room_id: str
    scope: str
    goal: str
    submitted_by: str
    submitter_kind: str
    worker_actor_id: str
    terms: List[str]
    tags: List[str]
    status: str
    current_run_id: Optional[str]
    attempts: int
    created_at: str
    closed_at: Optional[str] = None
    resource: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Session:
    """The fresh-session context handed to one run, kept verbatim for audit."""

    session_id: str
    run_id: str
    room_id: str
    actor_id: str
    context_text: str
    context_hash: str
    skill_refs: List[str]
    created_at: str


@dataclass
class RunResult:
    summary: str
    refs: List[str]
    worker_name: str
    worker_is_fake: bool
    result_message_id: str
    returned_at: str


@dataclass
class Run:
    run_id: str
    request_id: str
    attempt: int
    project_id: str
    room_id: str
    worker_actor_id: str
    session_id: str
    worker_name: str
    worker_is_fake: bool
    worker_ref: Optional[str]
    owner_token: Optional[str]
    status: str
    last_event_seq: int
    produced_refs: List[str]
    handoff_ids: List[str]
    error: Optional[str]
    result: Optional[RunResult]
    created_at: str
    updated_at: str
    ended_at: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.status in RUN_ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunEvent:
    """Every event the runtime saw, accepted or rejected, in arrival order."""

    seq: int
    run_id: str
    attempt: int
    event_seq: int
    kind: str
    payload: Dict[str, Any]
    source: str
    accepted: bool
    reason: str
    created_at: str


@dataclass(frozen=True)
class WorkerEvent:
    """What a worker port reports back. ``event_seq`` is per (run, attempt) and strictly increasing."""

    run_id: str
    attempt: int
    event_seq: int
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Dispatch:
    """Durable linkage from one open handoff packet to one request in its target room, and the result that came back.

    Host-routed: a human or the origin conversation chose the packet, the target actor and the pool member
    explicitly. No model planned this. ``provider``/``account`` are the labels the host configured on the pool
    member that actually ran it. ``omitted_refs`` are run result refs that could not travel back because the
    packet only carries refs from its target room.
    """

    dispatch_id: str
    handoff_id: str
    origin_project: str
    origin_room: str
    origin_run_id: Optional[str]
    target_project: str
    target_room: str
    target_actor_id: str
    target_worker: str
    provider: str
    account: str
    goal: str
    request_id: str
    dispatched_by: str
    dispatcher_kind: str
    status: str
    run_id: Optional[str]
    error: Optional[str]
    result: Optional[Dict[str, Any]]
    omitted_refs: List[str]
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None

    @property
    def pending(self) -> bool:
        return self.status in DISPATCH_PENDING

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AgentHandle:
    """One human-registered, stable agent identity in one project: ``@handle`` -> (actor_id, home room).

    Registered by a human project owner. The handle is unique among active handles of the project; the display
    name is free text and may collide (which makes a display-name mention ambiguous, never guessed). A revoked
    handle resolves as unknown. This is an address, not a grant: execution still needs the actor's execution
    grant on the room and a pool binding, and every harness rule applies unchanged.
    """

    handle_id: str
    project_id: str
    handle: str
    display_name: str
    actor_id: str
    room_id: str
    registered_by: str
    note: str
    created_at: str
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None

    @property
    def mention(self) -> str:
        return f"@{self.handle}"

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Mention:
    """One ``@name`` token found in text. ``quoted`` marks tokens inside quoted/fenced/code/blockquote/context text."""

    name: str  # lowercased handle text without the @
    start: int
    end: int
    quoted: bool = False
    reason: str = ""  # why it is not actionable (empty when it is)

    @property
    def actionable(self) -> bool:
        return not self.quoted


@dataclass
class MentionResolution:
    """What one mention resolved to. Only ``resolved`` carries a handle; the rest carry an explanation."""

    mention: str
    status: str
    handle: Optional[AgentHandle] = None
    candidates: List[AgentHandle] = field(default_factory=list)
    matched_by: str = ""  # 'handle' | 'display_name' | ''
    reason: str = ""

    @property
    def actionable(self) -> bool:
        return self.status == MENTION_RESOLVED and self.handle is not None


@dataclass(frozen=True)
class AgentAddress:
    """A resolved mention that is also authorised to execute: active handle + pool binding + execution grant on its room."""

    project_id: str
    handle: str
    display_name: str
    actor_id: str
    room_id: str
    member: str
    provider: str
    account: str
    note: str = ""  # the owner's registration note (what this helper is for); rendered to the asking model, bounded

    @property
    def mention(self) -> str:
        return f"@{self.handle}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DelegationPolicy:
    """Bounds on model-requested delegation. ``depth`` counts model-requested hops only (a host dispatch is depth 0)."""

    max_depth: int = 2  # a root run may ask (depth 1) and its helper may ask once more (depth 2); deeper is refused
    max_per_run: int = 3  # asks one run attempt may make
    max_per_root: int = 6  # asks in the whole chain under one original request
    max_source_refs: int = 10
    max_purpose_chars: int = 500
    max_instruction_chars: int = 2000
    # Admission (see delegation.py): how many runs may be live at once, the asker included, before a root ask must wait
    # and a nested ask is refused; ``None`` sets no bound here (the host's own scope rules still apply).
    max_parallel: Optional[int] = None
    # Whether a run may ask the same helper about the same sources again with a new purpose or instructions. The default
    # refuses (packets carry records; a reworded ask about the same records is a duplicate). A host whose sources are file
    # snapshots that helpers work on may allow it; an identical repeat always returns the earlier ask either way.
    allow_repeat_sources: bool = False

    def __post_init__(self) -> None:
        from .errors import ValidationError

        for name in ("max_depth", "max_per_run", "max_per_root", "max_source_refs", "max_purpose_chars", "max_instruction_chars"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValidationError(f"DelegationPolicy.{name} must be a positive integer")
        if self.max_parallel is not None and (not isinstance(self.max_parallel, int) or isinstance(self.max_parallel, bool) or self.max_parallel < 1):
            raise ValidationError("DelegationPolicy.max_parallel must be a positive integer or None")
        if not isinstance(self.allow_repeat_sources, bool):
            raise ValidationError("DelegationPolicy.allow_repeat_sources must be a bool")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Delegation:
    """One model-requested delegation: a live run asked a named helper for help through the structured action.

    Ownership is explicit: ``root_request_id`` is the original request the whole chain serves, ``parent_run_id``
    the attempt that asked, ``requested_by`` its worker actor. ``depth`` is this hop's distance from the root in
    model-requested hops. The packet and the dispatch are the ordinary ones; ``status``/``result``/``error`` mirror
    the dispatch when read.
    """

    delegation_id: str
    project_id: str
    root_request_id: str
    parent_request_id: str
    parent_run_id: str
    parent_attempt: int
    depth: int
    requested_by: str
    origin_room: str
    target_handle: str
    target_actor_id: str
    target_room: str
    target_worker: str
    purpose: str
    instructions: str
    source_refs: List[str]
    handoff_id: str
    dispatch_id: str
    created_at: str
    status: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def mention(self) -> str:
        return f"@{self.target_handle}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WorkAssignment:
    """Everything a worker gets at start: the goal, the fresh context and the rooms it may touch."""

    run_id: str
    attempt: int
    session_id: str
    project_id: str
    room_id: str
    worker_actor_id: str
    goal: str
    context_text: str
    skill_refs: List[str]
    allowed_rooms: List[str]


@dataclass(frozen=True)
class WorkerObservation:
    """A worker port's answer to 'what became of this worker?' during reconciliation or after a cancel.

    ``finished`` means the port observed the worker terminate (process exit or explicit ack).
    ``running`` means it is still there. ``lost`` means the port positively has no record of it.
    A port that cannot tell raises instead, and the runtime keeps the run's current status.
    """

    state: str  # 'running' | 'finished' | 'lost'
    events: List[WorkerEvent] = field(default_factory=list)  # for 'finished': the events not yet delivered
    note: str = ""


@dataclass(frozen=True)
class EventOutcome:
    accepted: bool
    reason: str
    run_status: Optional[str] = None
