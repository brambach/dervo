"""Named-agent addressing: a human-registered registry of stable agent handles, and mention extraction that never
treats quoted or copied text as an address.

- :class:`AgentRegistry` maps ``@handle`` to exactly one ``(project, actor_id, home room)`` per project. A human
  project owner registers and revokes handles. Resolution is exact on the handle; a display name resolves only
  while it is unique, otherwise the mention is *ambiguous* and the candidates are listed, never chosen. A revoked
  or unregistered name is *unknown*. Handles are per project: a handle in another project is unknown here.
- :func:`extract_mentions` finds ``@name`` tokens in text and marks every token that sits inside a fenced block,
  inline code, a blockquote line, double quotes or a ``<dervo-context>`` fence as *quoted*: it is reported so a
  reader can see it, but it is never actionable.
- :meth:`AgentRegistry.resolve_message_mentions` resolves the mentions of one stored message, and only for the
  message's own author (or a human owner/editor of the project), and only for records the author wrote
  (``message``, ``note``, ``decision``). Handoff copies, run notes and run results are text generated or copied by
  the harness, so they are refused as addressing sources outright.

A resolved mention is an address, not a permission: whether the named agent may execute is decided by the
execution grant on its room and the pool binding, checked by :meth:`dervo_harness.supervisor.Supervisor.resolve_agent`.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from .errors import AccessDenied, NotFound, PolicyViolation, StateError, ValidationError
from .harness import Harness
from .models import (
    CALLER_HUMAN, KIND_DECISION, MENTION_AMBIGUOUS, MENTION_QUOTED, MENTION_RESOLVED, MENTION_UNKNOWN, ROLE_EDITOR, ROLE_OWNER,
    ROLE_VIEWER, ROLES, USER_MESSAGE_KINDS, AgentHandle, Caller, Mention, MentionResolution,
)
from .store import new_id, now_iso

HANDLE_MAX_CHARS = 32
DISPLAY_NAME_MAX_CHARS = 64
_HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
# A mention starts at a word boundary that is not part of an email, path or another mention, and ends before any
# further handle character. Over-long tokens do not match at all.
MENTION_RE = re.compile(r"(?<![\w.@/])@([A-Za-z0-9][A-Za-z0-9-]{0,31})(?![\w@-])")
# Untrusted spans, in priority order. A mention inside any of them is reported as quoted and never acted on.
_UNTRUSTED: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("dervo-context fence", re.compile(r"<dervo-context>.*?(?:</dervo-context>|\Z)", re.DOTALL | re.IGNORECASE)),
    ("fenced block", re.compile(r"```.*?(?:```|\Z)", re.DOTALL)),
    ("inline code", re.compile(r"`[^`\n]*`")),
    ("blockquote", re.compile(r"^[ \t]*>.*$", re.MULTILINE)),
    ("double quotes", re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")),
)
ADDRESSABLE_MESSAGE_KINDS = (*USER_MESSAGE_KINDS, KIND_DECISION)  # records the author wrote; harness-generated kinds are refused


def normalize_handle(value: Any) -> str:
    """``'@Answers-Bot '`` -> ``'answers-bot'``; anything that is not one plain handle token is refused."""
    if not isinstance(value, str):
        raise ValidationError("handle must be a string like '@name'")
    text = value.strip()
    if text.startswith("@"):
        text = text[1:]
    text = text.lower()
    if not _HANDLE_RE.match(text):
        raise ValidationError(f"{value!r} is not a valid handle: '@' plus 1 to {HANDLE_MAX_CHARS} lowercase letters, digits or hyphens, "
                              "starting with a letter or digit; quotes, spaces and other characters are not part of a handle")
    return text


def _display_key(display_name: str) -> str:
    return "-".join(str(display_name or "").lower().split())


def _untrusted_spans(text: str) -> List[Tuple[int, int, str]]:
    spans: List[Tuple[int, int, str]] = []
    for reason, pattern in _UNTRUSTED:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), reason))
    return spans


def extract_mentions(text: Any) -> List[Mention]:
    """Every ``@name`` token in ``text`` in order; tokens inside untrusted spans are marked ``quoted`` with the reason."""
    if not isinstance(text, str) or not text:
        return []
    spans = _untrusted_spans(text)
    out: List[Mention] = []
    for match in MENTION_RE.finditer(text):
        reason = next((why for start, end, why in spans if start <= match.start() and match.end() <= end), "")
        out.append(Mention(match.group(1).lower(), match.start(), match.end(), bool(reason), reason))
    return out


class AgentRegistry:
    """Human-registered ``@handle`` -> stable agent identity, per project. Durable in the harness data dir."""

    def __init__(self, harness: Harness) -> None:
        self.h = harness
        self._conn = harness._conn

    # ------------------------------------------------------------------ writes (human project owner only)

    def register(self, caller: Caller, project_id: str, handle: str, actor_id: str, room_id: str, *, display_name: str = "",
                 note: str = "") -> AgentHandle:
        """Bind ``@handle`` to ``actor_id`` with ``room_id`` as the room it is addressed in. Owner only; one active handle per name."""
        self.h._require_human(caller, "register agent handles")
        self.h._require_role(caller, project_id, (ROLE_OWNER,), "register agent handles")
        handle = normalize_handle(handle)
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValidationError("actor_id must be a non-empty string")
        actor_id = actor_id.strip()
        role = self.h._role(project_id, actor_id)
        if role is None or role == ROLE_VIEWER:
            raise ValidationError(f"{actor_id} must be a non-viewer member of project {project_id} before it can be addressed as @{handle}")
        room = self.h._room(room_id)
        if room.project_id != project_id:
            raise ValidationError(f"room {room_id} is not in project {project_id}; a handle's home room must be in its own project")
        if not isinstance(display_name, str):
            raise ValidationError("display_name must be a string")
        display = " ".join(display_name.split())
        if len(display) > DISPLAY_NAME_MAX_CHARS:
            raise ValidationError(f"display_name exceeds {DISPLAY_NAME_MAX_CHARS} characters")
        if self._active(project_id, handle) is not None:
            raise ValidationError(f"@{handle} is already registered in project {project_id}; revoke it before re-registering")
        hid, ts = new_id("agh"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO agent_handles (handle_id, project_id, handle, display_name, actor_id, room_id, registered_by, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (hid, project_id, handle, display, actor_id, room.room_id, caller.actor_id, str(note or ""), ts),
            )
        return self._handle(hid)

    def revoke(self, caller: Caller, project_id: str, handle: str, *, note: str = "") -> AgentHandle:
        self.h._require_human(caller, "revoke agent handles")
        self.h._require_role(caller, project_id, (ROLE_OWNER,), "revoke agent handles")
        handle = normalize_handle(handle)
        current = self._active(project_id, handle)
        if current is None:
            raise StateError(f"@{handle} is not an active handle in project {project_id}")
        with self._conn:
            self._conn.execute(
                "UPDATE agent_handles SET revoked_at = ?, revoked_by = ?, note = ? WHERE handle_id = ?",
                (now_iso(), caller.actor_id, (current.note + (f" | revoked: {note}" if note else "")).strip(" |"), current.handle_id),
            )
        return self._handle(current.handle_id)

    # ------------------------------------------------------------------ reads (any project member)

    def list_handles(self, caller: Caller, project_id: str, *, include_revoked: bool = False) -> List[AgentHandle]:
        self.h._require_role(caller, project_id, ROLES, "list agent handles")
        sql = "SELECT * FROM agent_handles WHERE project_id = ?" + ("" if include_revoked else " AND revoked_at IS NULL") + " ORDER BY seq"
        return [self._from_row(r) for r in self._conn.execute(sql, (project_id,)).fetchall()]

    def resolve(self, caller: Caller, project_id: str, name: Any) -> MentionResolution:
        """Never raises for a bad name: the outcome (resolved, unknown, ambiguous) is in the result, with the reason."""
        self.h._require_role(caller, project_id, ROLES, "resolve agent mentions")
        try:
            handle = normalize_handle(name)
        except ValidationError as exc:
            return MentionResolution(str(name), MENTION_UNKNOWN, reason=f"malformed mention: {exc}")
        mention = f"@{handle}"
        exact = self._active(project_id, handle)
        if exact is not None:
            return MentionResolution(mention, MENTION_RESOLVED, exact, [exact], "handle")
        by_display = [h for h in self.list_handles(caller, project_id) if _display_key(h.display_name) == handle]
        if len(by_display) == 1:
            return MentionResolution(mention, MENTION_RESOLVED, by_display[0], by_display, "display_name")
        if by_display:
            names = ", ".join(sorted(f"@{h.handle}" for h in by_display))
            return MentionResolution(mention, MENTION_AMBIGUOUS, None, sorted(by_display, key=lambda h: h.handle), "",
                                     f"{mention} is ambiguous in project {project_id}: display name matches {names}; use an exact handle")
        revoked = self._conn.execute("SELECT 1 FROM agent_handles WHERE project_id = ? AND handle = ? AND revoked_at IS NOT NULL LIMIT 1",
                                     (project_id, handle)).fetchone()
        if revoked:
            return MentionResolution(mention, MENTION_UNKNOWN, reason=f"{mention} was revoked in project {project_id}; it is no longer an address")
        return MentionResolution(mention, MENTION_UNKNOWN, reason=f"unknown mention {mention}: no active handle or display name matches in project {project_id}")

    def require(self, caller: Caller, project_id: str, name: Any) -> AgentHandle:
        """Strict form: the exact handle or a unique display name, or an error that says why not (nothing is guessed)."""
        normalize_handle(name)  # malformed input is the caller's error, not an unknown name
        res = self.resolve(caller, project_id, name)
        if res.status == MENTION_RESOLVED and res.handle is not None:
            return res.handle
        if res.status == MENTION_AMBIGUOUS:
            raise ValidationError(res.reason)
        raise NotFound(res.reason)

    def resolve_message_mentions(self, caller: Caller, message_id: str) -> List[MentionResolution]:
        """Mentions of one stored message, resolved in its project. Only the author, or a human owner/editor, may ask;
        only author-written kinds are addressing sources; quoted tokens come back ``quoted`` and are never resolved."""
        msg = self.h._message(message_id)
        room = self.h._require_read_room(caller, msg.room_id)
        if msg.kind not in ADDRESSABLE_MESSAGE_KINDS:
            raise PolicyViolation(f"message {message_id} is a {msg.kind!r} record: text copied or generated by the harness is never an addressing source")
        is_author = caller.actor_id == msg.author_id and caller.kind == msg.author_kind
        if not is_author and not (caller.kind == CALLER_HUMAN and self.h._role(room.project_id, caller.actor_id) in (ROLE_OWNER, ROLE_EDITOR)):
            raise AccessDenied(f"{caller.label()} may not resolve mentions in message {message_id}: not its author or a human owner/editor")
        out: List[MentionResolution] = []
        for m in extract_mentions(msg.content):
            if m.quoted:
                out.append(MentionResolution(f"@{m.name}", MENTION_QUOTED, reason=f"inside {m.reason}; quoted text is never an address"))
            else:
                out.append(self.resolve(caller, room.project_id, m.name))
        return out

    # ------------------------------------------------------------------ internals

    def _active_all(self, project_id: str) -> List[AgentHandle]:
        """Every active handle of a project, by handle. Internal: the supervisor filters these by authority before rendering."""
        rows = self._conn.execute("SELECT * FROM agent_handles WHERE project_id = ? AND revoked_at IS NULL ORDER BY handle", (project_id,)).fetchall()
        return [self._from_row(r) for r in rows]

    def _active(self, project_id: str, handle: str) -> Optional[AgentHandle]:
        row = self._conn.execute("SELECT * FROM agent_handles WHERE project_id = ? AND handle = ? AND revoked_at IS NULL", (project_id, handle)).fetchone()
        return self._from_row(row) if row else None

    def _handle(self, handle_id: str) -> AgentHandle:
        row = self._conn.execute("SELECT * FROM agent_handles WHERE handle_id = ?", (handle_id,)).fetchone()
        if row is None:
            raise NotFound(f"agent handle {handle_id} not found")
        return self._from_row(row)

    @staticmethod
    def _from_row(row: Any) -> AgentHandle:
        return AgentHandle(row["handle_id"], row["project_id"], row["handle"], row["display_name"], row["actor_id"], row["room_id"],
                           row["registered_by"], row["note"], row["created_at"], row["revoked_at"], row["revoked_by"])
