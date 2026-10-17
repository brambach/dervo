"""Dervo harness core: durable project context, bounded handoffs and versioned skills.

Stdlib only. All state lives under one explicit data directory passed to :class:`Harness`.
This package is a context and learning substrate; it runs no model, no server, no loop.
"""

from .errors import AccessDenied, DervoError, NotFound, PolicyViolation, StateError, ValidationError
from .harness import SELECTION_METHOD, Harness, parse_ref
from .models import (
    CALLER_AGENT, CALLER_HUMAN, DISPATCH_CANCELLED, DISPATCH_CANCELLING, DISPATCH_DISPATCHED, DISPATCH_FAILED, DISPATCH_FINAL,
    DISPATCH_LOST, DISPATCH_PENDING, DISPATCH_RETURN_REFUSED, DISPATCH_RETURNED, DOMAIN_PERSONAL, DOMAIN_WORK, EVENT_FAILED,
    EVENT_PROGRESS, EVENT_RESULT, EVENT_STOPPED, HANDOFF_CANCELLED, HANDOFF_OPEN, HANDOFF_RETURNED, KIND_DECISION,
    KIND_HANDOFF_RECEIVED, KIND_HANDOFF_RESULT, KIND_HANDOFF_SENT, KIND_MESSAGE, KIND_NOTE, KIND_RUN, KIND_RUN_RESULT,
    REQUEST_ACTIVE, REQUEST_CLOSED, ROLE_AGENT, ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER, RUN_ACTIVE, RUN_CANCELLED, RUN_CANCELLING,
    RUN_FAILED, RUN_LOST, RUN_RUNNING, RUN_SUCCEEDED, RUN_TERMINAL, RUN_UNKNOWN, SKILL_APPROVED, SKILL_PROPOSED, SKILL_REJECTED,
    VIS_PRIVATE, VIS_SHARED, Caller, Decision, Dispatch, EventOutcome, ExecutionGrant, HandoffItem, HandoffPacket, HandoffPolicy,
    HandoffResult, Message, Project, Request, Room, Run, RunEvent, RunResult, Session, SessionContext, SkillEvent, SkillSelection,
    SkillVersion, WorkAssignment, WorkerEvent, WorkerObservation,
)
from .models import (
    MENTION_AMBIGUOUS, MENTION_QUOTED, MENTION_RESOLVED, MENTION_UNKNOWN, AgentAddress, AgentHandle, Delegation, DelegationPolicy,
    Mention, MentionResolution,
)
from .runtime import DelegationBroker, ModelPort, Runtime, ScopedActions, WorkerPort
from .delegation import ADMIT_EXISTING, ADMIT_START, ADMIT_WAIT, Admission, Ask, DelegationRules, Lineage, PriorAsk
from .claude_worker import ClaudeCliConfig, ClaudeCliWorker
from .codex_worker import CodexAppServerConfig, CodexAppServerWorker
from .addressing import AgentRegistry, extract_mentions, normalize_handle
from .supervisor import RuntimeLease, Supervisor, WorkerBinding, WorkerPool
from .upstream_adapted import ADAPTATION_MAP, UPSTREAM_LICENSE

__version__ = "0.8.2"

__all__ = [
    "ADMIT_EXISTING", "ADMIT_START", "ADMIT_WAIT", "Admission", "Ask", "DelegationRules", "Lineage", "PriorAsk",
    "AgentAddress", "AgentHandle", "AgentRegistry", "Delegation", "DelegationBroker", "DelegationPolicy", "MENTION_AMBIGUOUS",
    "MENTION_QUOTED", "MENTION_RESOLVED", "MENTION_UNKNOWN", "Mention", "MentionResolution", "extract_mentions", "normalize_handle",
    "ADAPTATION_MAP", "AccessDenied", "CALLER_AGENT", "CALLER_HUMAN", "Caller", "ClaudeCliConfig", "ClaudeCliWorker",
    "CodexAppServerConfig", "CodexAppServerWorker",
    "DISPATCH_CANCELLED", "DISPATCH_CANCELLING", "DISPATCH_DISPATCHED", "DISPATCH_FAILED", "DISPATCH_FINAL", "DISPATCH_LOST",
    "DISPATCH_PENDING", "DISPATCH_RETURNED", "DISPATCH_RETURN_REFUSED", "DOMAIN_PERSONAL", "DOMAIN_WORK", "Decision", "DervoError",
    "Dispatch", "EVENT_FAILED", "EVENT_PROGRESS", "EVENT_RESULT", "EVENT_STOPPED", "EventOutcome", "ExecutionGrant",
    "HANDOFF_CANCELLED", "HANDOFF_OPEN", "HANDOFF_RETURNED", "HandoffItem", "HandoffPacket", "HandoffPolicy", "HandoffResult",
    "Harness", "KIND_DECISION", "KIND_HANDOFF_RECEIVED", "KIND_HANDOFF_RESULT", "KIND_HANDOFF_SENT", "KIND_MESSAGE", "KIND_NOTE",
    "KIND_RUN", "KIND_RUN_RESULT", "Message", "ModelPort", "NotFound", "PolicyViolation", "Project", "REQUEST_ACTIVE",
    "REQUEST_CLOSED", "ROLE_AGENT", "ROLE_EDITOR", "ROLE_OWNER", "ROLE_VIEWER", "RUN_ACTIVE", "RUN_CANCELLED", "RUN_CANCELLING",
    "RUN_FAILED", "RUN_LOST", "RUN_RUNNING", "RUN_SUCCEEDED", "RUN_TERMINAL", "RUN_UNKNOWN", "Request", "Room", "Run", "RunEvent",
    "RunResult", "Runtime", "RuntimeLease", "SELECTION_METHOD", "SKILL_APPROVED", "SKILL_PROPOSED", "SKILL_REJECTED", "ScopedActions",
    "Session", "SessionContext", "SkillEvent", "SkillSelection", "SkillVersion", "StateError", "Supervisor", "UPSTREAM_LICENSE",
    "VIS_PRIVATE", "VIS_SHARED", "ValidationError", "WorkAssignment", "WorkerBinding", "WorkerEvent", "WorkerObservation",
    "WorkerPool", "WorkerPort", "parse_ref", "__version__",
]
