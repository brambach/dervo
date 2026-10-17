"""The Dervo harness facade: durable rooms, decisions, bounded handoffs, versioned skills.

Every public method takes an explicit :class:`Caller`. Access is decided from persisted
project roles, room memberships and cross-project grants; nothing is inferred from the
environment. This is a context and learning substrate, not an execution loop.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .errors import AccessDenied, NotFound, PolicyViolation, StateError, ValidationError
from .models import (
    CALLER_AGENT, CALLER_HUMAN, CALLER_KINDS, DOMAIN_PERSONAL, DOMAIN_WORK, DOMAINS,
    HANDOFF_CANCELLED, HANDOFF_OPEN, HANDOFF_RETURNED, KIND_DECISION, KIND_HANDOFF_RECEIVED,
    KIND_HANDOFF_RESULT, KIND_HANDOFF_SENT, KIND_MESSAGE, ORIGIN_AGENT_SUGGESTION, ORIGIN_HUMAN,
    ORIGIN_HUMAN_PROPOSAL, ORIGIN_IMPORT, ROLE_AGENT, ROLE_EDITOR, ROLE_OWNER, ROLE_VIEWER, ROLES,
    SKILL_APPROVED, SKILL_PROPOSED, SKILL_REJECTED, USER_MESSAGE_KINDS, VIS_PRIVATE, VIS_SHARED,
    VISIBILITIES, Caller, Decision, HandoffItem, HandoffPacket, HandoffPolicy, HandoffResult,
    Message, Project, Room, SessionContext, SkillEvent, SkillSelection, SkillVersion,
)
from .store import Store, dumps, loads, new_id, now_iso
from .upstream_adapted import (
    is_valid_skill_name, parse_frontmatter, render_frontmatter, strip_invisible_unicode,
)

_REF_RE = re.compile(r"^(message|decision|handoff|skill):(.+)$")
_SKILL_REF_RE = re.compile(r"^([^/@]+)/([^/@]+)@(\d+)$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

SELECTION_METHOD = (
    "deterministic: score = 3 x (query tag matches) + 1 x (query term matches in name, description, tags); "
    "candidates are the project's current approved skill versions; ties broken by newest approval then name; "
    "with no query, newest approved first. No semantic or learned relevance is claimed."
)


def parse_ref(ref: Any) -> Tuple[str, str]:
    match = _REF_RE.match(str(ref or ""))
    if not match:
        raise ValidationError(f"malformed ref {ref!r}; expected 'message:<id>', 'decision:<id>', 'handoff:<id>' or 'skill:<project>/<name>@<version>'")
    return match.group(1), match.group(2)


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 3}


def _norm_tags(tags: Iterable[Any]) -> List[str]:
    out = sorted({" ".join(str(t).lower().split()).replace(" ", "-") for t in tags if str(t).strip()})
    return out


class Harness:
    """Facade over one explicit data directory."""

    def __init__(self, data_dir: Union[str, "os.PathLike[str]"]) -> None:
        self.store = Store(data_dir)
        self._conn: sqlite3.Connection = self.store.conn

    @property
    def data_dir(self):
        return self.store.data_dir

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "Harness":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ callers

    @staticmethod
    def _check_caller(caller: Caller) -> Caller:
        # Caller validates its own fields; this guards against look-alike objects. A Caller is a trusted
        # in-process claim, never authenticated here (see models.Caller).
        if not isinstance(caller, Caller) or not caller.actor_id or caller.kind not in CALLER_KINDS:
            raise ValidationError("caller must be a Caller with actor_id and kind in ('human', 'agent')")
        return caller

    def _require_human(self, caller: Caller, action: str) -> None:
        self._check_caller(caller)
        if caller.kind != CALLER_HUMAN:
            raise AccessDenied(f"{caller.label()} may not {action}: agent callers cannot; this is a review boundary")

    # ------------------------------------------------------------------ projects

    def create_project(self, caller: Caller, name: str, *, domain: str = DOMAIN_WORK, project_id: Optional[str] = None) -> Project:
        self._require_human(caller, "create projects")
        if domain not in DOMAINS:
            raise ValidationError(f"domain must be one of {DOMAINS}")
        name = _nonempty(name, "project name")
        pid = project_id or new_id("prj")
        ts = now_iso()
        with self._conn:
            if self._conn.execute("SELECT 1 FROM projects WHERE project_id = ?", (pid,)).fetchone():
                raise ValidationError(f"project {pid} already exists")
            self._conn.execute(
                "INSERT INTO projects (project_id, name, domain, owner_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (pid, name, domain, caller.actor_id, ts),
            )
            self._conn.execute(
                "INSERT INTO project_members (project_id, actor_id, role, granted_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (pid, caller.actor_id, ROLE_OWNER, caller.actor_id, ts),
            )
        return self._project(pid)

    def get_project(self, caller: Caller, project_id: str) -> Project:
        self._require_role(caller, project_id, ROLES, "read the project")
        return self._project(project_id)

    def add_project_member(self, caller: Caller, project_id: str, actor_id: str, role: str) -> None:
        self._require_role(caller, project_id, (ROLE_OWNER,), "add project members")
        if role not in ROLES:
            raise ValidationError(f"role must be one of {ROLES}")
        actor_id = _nonempty(actor_id, "actor_id")
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO project_members (project_id, actor_id, role, granted_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, actor_id, role, caller.actor_id, now_iso()),
            )

    def project_role(self, project_id: str, actor_id: str) -> Optional[str]:
        return self._role(project_id, actor_id)

    # ------------------------------------------------------------------ rooms

    def create_room(self, caller: Caller, project_id: str, name: str, *, visibility: str = VIS_SHARED, room_id: Optional[str] = None) -> Room:
        self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR, ROLE_AGENT), "create rooms")
        if visibility not in VISIBILITIES:
            raise ValidationError(f"visibility must be one of {VISIBILITIES}")
        name = _nonempty(name, "room name")
        rid = room_id or new_id("room")
        ts = now_iso()
        with self._conn:
            if self._conn.execute("SELECT 1 FROM rooms WHERE room_id = ?", (rid,)).fetchone():
                raise ValidationError(f"room {rid} already exists")
            self._conn.execute(
                "INSERT INTO rooms (room_id, project_id, name, visibility, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (rid, project_id, name, visibility, caller.actor_id, ts),
            )
            self._conn.execute(
                "INSERT INTO room_members (room_id, actor_id, granted_by, created_at) VALUES (?, ?, ?, ?)",
                (rid, caller.actor_id, caller.actor_id, ts),
            )
        return self._room(rid)

    def get_room(self, caller: Caller, room_id: str) -> Room:
        return self._require_read_room(caller, room_id)

    def add_room_member(self, caller: Caller, room_id: str, actor_id: str) -> None:
        """Room creator or project owner adds a project member to the room (audited)."""
        self._check_caller(caller)
        room = self._room(room_id)
        role = self._role(room.project_id, caller.actor_id)
        if not (room.created_by == caller.actor_id or role == ROLE_OWNER):
            raise AccessDenied(f"{caller.label()} may not add members to room {room_id}: not its creator or the project owner")
        if self._role(room.project_id, actor_id) is None:
            raise ValidationError(f"{actor_id} is not a member of project {room.project_id}; add them to the project first")
        with self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO room_members (room_id, actor_id, granted_by, created_at) VALUES (?, ?, ?, ?)",
                (room_id, actor_id, caller.actor_id, now_iso()),
            )

    def list_rooms(self, caller: Caller, project_id: str) -> List[Room]:
        self._require_role(caller, project_id, ROLES, "list rooms")
        rows = self._conn.execute("SELECT * FROM rooms WHERE project_id = ? ORDER BY created_at, room_id", (project_id,)).fetchall()
        rooms = [self._room_from_row(r) for r in rows]
        return [r for r in rooms if self._can_read_room(caller, r)]

    # ------------------------------------------------------------------ cross-project grants

    def grant_cross_project_handoff(self, caller: Caller, from_project: str, to_project: str) -> str:
        """Source-project owner explicitly permits handoffs into ``to_project``."""
        self._require_human(caller, "grant cross-project handoffs")
        self._require_role(caller, from_project, (ROLE_OWNER,), "grant handoffs out of the project")
        self._project(to_project)
        if from_project == to_project:
            raise ValidationError("a cross-project grant needs two different projects")
        gid = new_id("grant")
        with self._conn:
            self._conn.execute(
                "INSERT INTO handoff_grants (grant_id, from_project, to_project, granted_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (gid, from_project, to_project, caller.actor_id, now_iso()),
            )
        return gid

    def revoke_cross_project_handoff(self, caller: Caller, grant_id: str) -> None:
        self._require_human(caller, "revoke cross-project handoffs")
        row = self._conn.execute("SELECT * FROM handoff_grants WHERE grant_id = ?", (grant_id,)).fetchone()
        if row is None:
            raise NotFound(f"grant {grant_id} not found")
        self._require_role(caller, row["from_project"], (ROLE_OWNER,), "revoke handoff grants")
        with self._conn:
            self._conn.execute(
                "UPDATE handoff_grants SET revoked_at = ?, revoked_by = ? WHERE grant_id = ? AND revoked_at IS NULL",
                (now_iso(), caller.actor_id, grant_id),
            )

    def _active_grant(self, from_project: str, to_project: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM handoff_grants WHERE from_project = ? AND to_project = ? AND revoked_at IS NULL LIMIT 1",
            (from_project, to_project),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------ messages and decisions

    def record_message(self, caller: Caller, room_id: str, content: str, *, kind: str = KIND_MESSAGE, refs: Sequence[str] = ()) -> Message:
        room = self._require_write_room(caller, room_id)
        if kind not in USER_MESSAGE_KINDS:
            raise ValidationError(f"kind must be one of {USER_MESSAGE_KINDS}; other kinds are written by the harness")
        content = _nonempty(content, "content")
        ref_list = self._validate_refs(refs)
        return self._insert_message(room, caller, kind, content, ref_list)

    def record_decision(self, caller: Caller, room_id: str, title: str, rationale: str = "", *, source_refs: Sequence[str] = ()) -> Decision:
        room = self._require_write_room(caller, room_id)
        title = " ".join(_nonempty(title, "title").split())
        rationale = rationale or ""
        refs = self._validate_refs(source_refs)
        for ref in refs:
            self.resolve_ref(caller, ref)  # cited sources must exist and be readable by the author
        did = new_id("dec")
        content = f"DECISION: {title}\n{rationale}".rstrip()
        msg = self._insert_message(room, caller, KIND_DECISION, content, [f"decision:{did}", *refs])
        with self._conn:
            self._conn.execute(
                "INSERT INTO decisions (decision_id, room_id, project_id, author_id, author_kind, title, rationale, source_refs_json, message_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (did, room.room_id, room.project_id, caller.actor_id, caller.kind, title, rationale, dumps(refs), msg.message_id, msg.created_at),
            )
        return self._decision(did)

    def list_messages(self, caller: Caller, room_id: str, *, limit: int = 50, kinds: Optional[Sequence[str]] = None) -> List[Message]:
        """Newest ``limit`` messages of the room, oldest first."""
        room = self._require_read_room(caller, room_id)
        limit = max(1, int(limit))
        if kinds:
            marks = ",".join("?" for _ in kinds)
            rows = self._conn.execute(
                f"SELECT * FROM messages WHERE room_id = ? AND kind IN ({marks}) ORDER BY seq DESC LIMIT ?",
                (room.room_id, *kinds, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE room_id = ? ORDER BY seq DESC LIMIT ?", (room.room_id, limit)
            ).fetchall()
        return [self._message_from_row(r) for r in reversed(rows)]

    def list_decisions(self, caller: Caller, room_id: str, *, limit: int = 20) -> List[Decision]:
        room = self._require_read_room(caller, room_id)
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE room_id = ? ORDER BY seq DESC LIMIT ?", (room.room_id, max(1, int(limit)))
        ).fetchall()
        return [self._decision_from_row(r) for r in reversed(rows)]

    def resolve_ref(self, caller: Caller, ref: str) -> Union[Message, Decision, HandoffPacket, SkillVersion]:
        """Dereference a ref, enforcing the caller's access to the record it points at."""
        kind, ident = parse_ref(ref)
        if kind == "message":
            msg = self._message(ident)
            self._require_read_room(caller, msg.room_id)
            return msg
        if kind == "decision":
            dec = self._decision(ident)
            self._require_read_room(caller, dec.room_id)
            return dec
        if kind == "handoff":
            return self.get_handoff(caller, ident)
        match = _SKILL_REF_RE.match(ident)
        if not match:
            raise ValidationError(f"malformed skill ref {ref!r}; expected skill:<project>/<name>@<version>")
        return self.get_skill(caller, match.group(1), match.group(2), version=int(match.group(3)))

    # ------------------------------------------------------------------ handoffs

    def create_handoff(
        self, caller: Caller, from_room: str, to_room: str, purpose: str, item_refs: Sequence[str], *,
        instructions: str = "", policy: Optional[HandoffPolicy] = None,
    ) -> HandoffPacket:
        """Copy explicitly listed messages/decisions from one room into a bounded packet for another."""
        policy = policy or HandoffPolicy()
        src = self._require_read_room(caller, from_room)
        dst = self._require_write_room(caller, to_room)
        if src.room_id == dst.room_id:
            raise ValidationError("a handoff needs two different rooms")
        purpose = " ".join(_nonempty(purpose, "purpose").split())
        src_project, dst_project = self._project(src.project_id), self._project(dst.project_id)
        self._check_handoff_policy(caller, src, src_project, dst, dst_project, policy)
        items = self._build_handoff_items(src, item_refs, policy)
        hid = new_id("hdo")
        item_refs_clean = [item.ref for item in items]
        sent = self._insert_message(
            src, caller, KIND_HANDOFF_SENT,
            f"Handoff {hid} sent to room {dst.room_id} ({dst_project.name}): {purpose}",
            [f"handoff:{hid}", *item_refs_clean],
        )
        received = self._insert_message(
            dst, caller, KIND_HANDOFF_RECEIVED,
            f"Handoff {hid} received from room {src.room_id} ({src_project.name}): {purpose}",
            [f"handoff:{hid}"],
        )
        with self._conn:
            self._conn.execute(
                "INSERT INTO handoffs (handoff_id, from_project, from_room, to_project, to_room, created_by, creator_kind, purpose, instructions, "
                "items_json, policy_json, status, created_at, sent_message_id, received_message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hid, src.project_id, src.room_id, dst.project_id, dst.room_id, caller.actor_id, caller.kind, purpose,
                    strip_invisible_unicode(instructions or ""), dumps([item.__dict__ for item in items]), dumps(policy.to_dict()),
                    HANDOFF_OPEN, sent.created_at, sent.message_id, received.message_id,
                ),
            )
        return self.get_handoff(caller, hid)

    def get_handoff(self, caller: Caller, handoff_id: str) -> HandoffPacket:
        self._check_caller(caller)
        row = self._conn.execute("SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
        if row is None:
            raise NotFound(f"handoff {handoff_id} not found")
        src, dst = self._room(row["from_room"]), self._room(row["to_room"])
        if not (self._can_read_room(caller, src) or self._can_read_room(caller, dst)):
            raise AccessDenied(f"{caller.label()} may not read handoff {handoff_id}: no access to its source or target room")
        return self._handoff_from_row(row)

    def list_handoffs(self, caller: Caller, room_id: str, *, direction: str = "incoming", status: Optional[str] = None) -> List[HandoffPacket]:
        room = self._require_read_room(caller, room_id)
        column = {"incoming": "to_room", "outgoing": "from_room"}.get(direction)
        if column is None:
            raise ValidationError("direction must be 'incoming' or 'outgoing'")
        sql, params = f"SELECT * FROM handoffs WHERE {column} = ?", [room.room_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        rows = self._conn.execute(sql + " ORDER BY seq", params).fetchall()
        return [self._handoff_from_row(r) for r in rows]

    def return_handoff(
        self, caller: Caller, handoff_id: str, summary: str, result_refs: Sequence[str] = (), *,
        policy: Optional[HandoffPolicy] = None,
    ) -> HandoffPacket:
        """Close a handoff with a bounded summary plus refs; the source room gets a linked result message.

        The summary is a copy of text out of the target room into the origin, so it is checked
        like a transfer in that direction: private -> shared and work -> personal are refused
        unless an authorized human passes ``policy`` with the matching override. The packet itself
        is the only authorization for writing this one result back into the origin room.
        """
        packet = self.get_handoff(caller, handoff_id)
        if packet.status != HANDOFF_OPEN:
            raise StateError(f"handoff {handoff_id} is {packet.status}, not open")
        dst = self._require_write_room(caller, packet.to_room)
        src = self._room(packet.from_room)
        self._check_downgrade(caller, dst, self._project(dst.project_id), src, self._project(src.project_id), policy or HandoffPolicy(), "return")
        refs = self._validate_refs(result_refs)
        for ref in refs:
            kind, ident = parse_ref(ref)
            if kind == "message":
                if self._message(ident).room_id != dst.room_id:
                    raise PolicyViolation(f"result ref {ref} is not in the handoff target room {dst.room_id}")
            elif kind == "decision":
                if self._decision(ident).room_id != dst.room_id:
                    raise PolicyViolation(f"result ref {ref} is not in the handoff target room {dst.room_id}")
            elif kind == "skill":
                sv = self.resolve_ref(caller, ref)
                if sv.project_id != dst.project_id:  # type: ignore[union-attr]
                    raise PolicyViolation(f"result ref {ref} is not a skill of the target project")
            else:
                raise ValidationError("result refs must be message:, decision: or skill: refs")
        summary = strip_invisible_unicode(_nonempty(summary, "summary"))
        truncated = len(summary) > packet.policy.max_summary_chars
        summary = summary[: packet.policy.max_summary_chars]
        result_msg = self._insert_message(
            src, caller, KIND_HANDOFF_RESULT, f"Handoff {handoff_id} returned: {summary}", [f"handoff:{handoff_id}", *refs]
        )
        result = HandoffResult(summary, refs, caller.actor_id, caller.kind, result_msg.created_at, result_msg.message_id, truncated)
        with self._conn:
            self._conn.execute(
                "UPDATE handoffs SET status = ?, result_json = ?, closed_at = ?, closed_by = ? WHERE handoff_id = ?",
                (HANDOFF_RETURNED, dumps(result.__dict__), result_msg.created_at, caller.actor_id, handoff_id),
            )
        return self.get_handoff(caller, handoff_id)

    def cancel_handoff(self, caller: Caller, handoff_id: str, note: str = "") -> HandoffPacket:
        packet = self.get_handoff(caller, handoff_id)
        if packet.status != HANDOFF_OPEN:
            raise StateError(f"handoff {handoff_id} is {packet.status}, not open")
        if packet.created_by != caller.actor_id:
            raise AccessDenied("only the handoff creator can cancel it")
        with self._conn:
            self._conn.execute(
                "UPDATE handoffs SET status = ?, closed_at = ?, closed_by = ?, instructions = instructions || ? WHERE handoff_id = ?",
                (HANDOFF_CANCELLED, now_iso(), caller.actor_id, f"\n[cancelled: {note}]" if note else "", handoff_id),
            )
        return self.get_handoff(caller, handoff_id)

    def _check_handoff_policy(self, caller: Caller, src: Room, sp: Project, dst: Room, dp: Project, policy: HandoffPolicy) -> None:
        if sp.project_id != dp.project_id and not self._active_grant(sp.project_id, dp.project_id):
            raise AccessDenied(
                f"no active cross-project handoff grant from project {sp.project_id} to {dp.project_id}; "
                "the source project owner must call grant_cross_project_handoff first"
            )
        self._check_downgrade(caller, src, sp, dst, dp, policy, "handoff")

    def _check_downgrade(self, caller: Caller, src: Room, sp: Project, dst: Room, dp: Project, policy: HandoffPolicy, what: str) -> None:
        """Refuse copying text from ``src`` into ``dst`` when that lowers its protection, unless an authorized human overrides.

        ``src`` is wherever the text comes from: the handoff source on create, the handoff target on return.
        """
        src_owner = caller.kind == CALLER_HUMAN and (src.created_by == caller.actor_id or self._role(sp.project_id, caller.actor_id) == ROLE_OWNER)
        if src.visibility == VIS_PRIVATE and dst.visibility == VIS_SHARED:
            if not policy.allow_private_to_shared:
                raise PolicyViolation(
                    f"{what} from private room {src.room_id} to shared room {dst.room_id} is refused by default; "
                    "pass HandoffPolicy(allow_private_to_shared=True) as the creator of the private room or its project owner"
                )
            if not src_owner:
                raise AccessDenied(f"{caller.label()} may not override private-to-shared: only a human creator of the private room or its project owner can")
        if sp.domain == DOMAIN_WORK and dp.domain == DOMAIN_PERSONAL:
            if not policy.allow_work_to_personal:
                raise PolicyViolation(
                    f"{what} from work project {sp.project_id} to personal project {dp.project_id} is refused by default; "
                    "pass HandoffPolicy(allow_work_to_personal=True) as the work project owner"
                )
            if not (caller.kind == CALLER_HUMAN and self._role(sp.project_id, caller.actor_id) == ROLE_OWNER):
                raise AccessDenied(f"{caller.label()} may not override work-to-personal: only the human work project owner can")

    def _build_handoff_items(self, src: Room, item_refs: Sequence[str], policy: HandoffPolicy) -> List[HandoffItem]:
        refs = list(dict.fromkeys(str(r) for r in (item_refs or ())))
        if not refs:
            raise ValidationError("a handoff carries explicit source refs; none were given (transcripts are never sent wholesale)")
        if len(refs) > policy.max_items:
            raise PolicyViolation(f"handoff lists {len(refs)} items but policy.max_items is {policy.max_items}; handoffs are bounded")
        items: List[HandoffItem] = []
        for ref in refs:
            kind, ident = parse_ref(ref)
            if kind == "message":
                msg = self._message(ident)
                if msg.room_id != src.room_id:
                    raise PolicyViolation(f"{ref} is not in source room {src.room_id}")
                text, author, author_kind, ts = msg.content, msg.author_id, msg.author_kind, msg.created_at
            elif kind == "decision":
                dec = self._decision(ident)
                if dec.room_id != src.room_id:
                    raise PolicyViolation(f"{ref} is not in source room {src.room_id}")
                text, author, author_kind, ts = f"{dec.title}\n{dec.rationale}".rstrip(), dec.author_id, dec.author_kind, dec.created_at
            else:
                raise ValidationError(f"handoff items must be message: or decision: refs from the source room, got {ref}")
            text = strip_invisible_unicode(text)
            truncated = len(text) > policy.max_excerpt_chars
            items.append(HandoffItem(ref, kind, src.room_id, author, author_kind, ts, text[: policy.max_excerpt_chars], truncated))
        return items

    # ------------------------------------------------------------------ skills

    def save_skill(
        self, caller: Caller, project_id: str, name: str, description: str, body: str, *,
        tags: Sequence[str] = (), sources: Sequence[str] = (), note: str = "", allow_private_sources: bool = False,
    ) -> SkillVersion:
        """Save a new version. Human owners/editors get it approved and current at once; agents get a proposal for review.

        Skills are readable project-wide, so ``sources`` must be records of this project's shared
        rooms (or this project's skills). A private-room source is a private -> shared transfer:
        refused unless a human creator of that room or the project owner passes ``allow_private_sources``.
        """
        role = self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR, ROLE_AGENT), "save skills")
        needs_review = caller.kind == CALLER_AGENT or role == ROLE_AGENT
        return self._write_skill_version(
            caller, project_id, name, description, body, tags, sources, note,
            status=SKILL_PROPOSED if needs_review else SKILL_APPROVED,
            origin=ORIGIN_AGENT_SUGGESTION if needs_review else ORIGIN_HUMAN,
            allow_private_sources=allow_private_sources,
        )

    def propose_skill(
        self, caller: Caller, project_id: str, name: str, description: str, body: str, *,
        tags: Sequence[str] = (), sources: Sequence[str] = (), note: str = "", allow_private_sources: bool = False,
    ) -> SkillVersion:
        """Always lands at the review boundary, whoever proposes it. Same source rules as ``save_skill``."""
        self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR, ROLE_AGENT), "propose skills")
        origin = ORIGIN_AGENT_SUGGESTION if caller.kind == CALLER_AGENT else ORIGIN_HUMAN_PROPOSAL
        return self._write_skill_version(
            caller, project_id, name, description, body, tags, sources, note, status=SKILL_PROPOSED, origin=origin,
            allow_private_sources=allow_private_sources,
        )

    def _check_skill_sources(self, caller: Caller, project_id: str, refs: List[str], allow_private_sources: bool) -> None:
        """Provenance boundary for project-wide skills. Checks declared sources only; it does not inspect body text."""
        for ref in refs:
            record = self.resolve_ref(caller, ref)  # must exist and be readable by the author
            if isinstance(record, SkillVersion):
                room: Optional[Room] = None
                record_project = record.project_id
            elif isinstance(record, HandoffPacket):
                room = self._room(record.to_room)  # a packet lives where it was received
                record_project = record.to_project
            else:
                room = self._room(record.room_id)
                record_project = record.project_id
            if record_project != project_id:
                raise PolicyViolation(
                    f"skill source {ref} belongs to project {record_project}, not {project_id}; skills are project-wide, "
                    "so cite a record received in this project (for example the handoff) instead"
                )
            if room is not None and room.visibility == VIS_PRIVATE:
                if not allow_private_sources:
                    raise PolicyViolation(
                        f"skill source {ref} is in private room {room.room_id}; a project-wide skill would expose it. "
                        "Pass allow_private_sources=True as the creator of that room or the project owner"
                    )
                authorized = caller.kind == CALLER_HUMAN and (
                    room.created_by == caller.actor_id or self._role(project_id, caller.actor_id) == ROLE_OWNER
                )
                if not authorized:
                    raise AccessDenied(
                        f"{caller.label()} may not cite private room {room.room_id} in a project-wide skill: "
                        "only a human creator of that room or the project owner can, and agents never can"
                    )

    def approve_skill_version(self, caller: Caller, project_id: str, name: str, version: int, *, note: str = "") -> SkillVersion:
        self._require_human(caller, "approve skill versions")
        self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR), "approve skill versions")
        sv = self._skill_version_by_name(project_id, name, version)
        if sv.status != SKILL_PROPOSED:
            raise StateError(f"{sv.ref} is {sv.status}, only proposed versions can be approved")
        ts = now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE skill_versions SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE skill_id = ? AND version = ?",
                (SKILL_APPROVED, caller.actor_id, ts, note, sv.skill_id, version),
            )
            self._conn.execute("UPDATE skills SET current_version = ? WHERE skill_id = ?", (version, sv.skill_id))
            self._event(sv.skill_id, version, "approved", caller, note, ts)
        return self._skill_version(sv.skill_id, version)

    def reject_skill_version(self, caller: Caller, project_id: str, name: str, version: int, *, note: str = "") -> SkillVersion:
        self._require_human(caller, "reject skill versions")
        self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR), "reject skill versions")
        sv = self._skill_version_by_name(project_id, name, version)
        if sv.status != SKILL_PROPOSED:
            raise StateError(f"{sv.ref} is {sv.status}, only proposed versions can be rejected")
        ts = now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE skill_versions SET status = ?, reviewed_by = ?, reviewed_at = ?, review_note = ? WHERE skill_id = ? AND version = ?",
                (SKILL_REJECTED, caller.actor_id, ts, note, sv.skill_id, version),
            )
            self._event(sv.skill_id, version, "rejected", caller, note, ts)
        return self._skill_version(sv.skill_id, version)

    def rollback_skill(self, caller: Caller, project_id: str, name: str, to_version: int, *, note: str = "") -> SkillVersion:
        """Point the skill's current version at an earlier approved version. History is kept; the move is audited."""
        self._require_human(caller, "roll back skills")
        self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR), "roll back skills")
        target = self._skill_version_by_name(project_id, name, to_version)
        if target.status != SKILL_APPROVED:
            raise StateError(f"{target.ref} is {target.status}; only approved versions can become current")
        skill_row = self._conn.execute("SELECT current_version FROM skills WHERE skill_id = ?", (target.skill_id,)).fetchone()
        previous = skill_row["current_version"]
        if previous == to_version:
            raise StateError(f"{target.ref} is already current")
        ts = now_iso()
        with self._conn:
            self._conn.execute("UPDATE skills SET current_version = ? WHERE skill_id = ?", (to_version, target.skill_id))
            self._event(target.skill_id, to_version, "rollback", caller, f"current {previous} -> {to_version}. {note}".strip(), ts)
        return self._skill_version(target.skill_id, to_version)

    def get_skill(self, caller: Caller, project_id: str, name: str, *, version: Optional[int] = None) -> SkillVersion:
        """Current approved version by default; any version explicitly."""
        self._require_role(caller, project_id, ROLES, "read skills")
        row = self._conn.execute("SELECT * FROM skills WHERE project_id = ? AND name = ?", (project_id, name)).fetchone()
        if row is None:
            raise NotFound(f"skill {name} not found in project {project_id}")
        if version is None:
            if row["current_version"] is None:
                raise NotFound(f"skill {name} has no approved version yet")
            version = int(row["current_version"])
        return self._skill_version(row["skill_id"], int(version))

    def list_skills(self, caller: Caller, project_id: str) -> List[SkillVersion]:
        """Current approved versions in the project, by name."""
        self._require_role(caller, project_id, ROLES, "list skills")
        rows = self._conn.execute(
            "SELECT v.*, s.project_id, s.name FROM skills s JOIN skill_versions v ON v.skill_id = s.skill_id AND v.version = s.current_version "
            "WHERE s.project_id = ? ORDER BY s.name",
            (project_id,),
        ).fetchall()
        return [self._skill_version_from_row(r, True) for r in rows]

    def pending_skill_reviews(self, caller: Caller, project_id: str) -> List[SkillVersion]:
        self._require_role(caller, project_id, ROLES, "list pending skill reviews")
        rows = self._conn.execute(
            "SELECT v.*, s.project_id, s.name, s.current_version FROM skills s JOIN skill_versions v ON v.skill_id = s.skill_id "
            "WHERE s.project_id = ? AND v.status = ? ORDER BY v.created_at, s.name, v.version",
            (project_id, SKILL_PROPOSED),
        ).fetchall()
        return [self._skill_version_from_row(r, False) for r in rows]

    def skill_history(self, caller: Caller, project_id: str, name: str) -> Tuple[List[SkillVersion], List[SkillEvent]]:
        self._require_role(caller, project_id, ROLES, "read skill history")
        row = self._conn.execute("SELECT * FROM skills WHERE project_id = ? AND name = ?", (project_id, name)).fetchone()
        if row is None:
            raise NotFound(f"skill {name} not found in project {project_id}")
        current = row["current_version"]
        versions = [
            self._skill_version_from_row(r, r["version"] == current)
            for r in self._conn.execute(
                "SELECT v.*, s.project_id, s.name FROM skill_versions v JOIN skills s ON s.skill_id = v.skill_id WHERE v.skill_id = ? ORDER BY v.version",
                (row["skill_id"],),
            ).fetchall()
        ]
        events = [
            SkillEvent(r["seq"], r["skill_id"], r["version"], r["event"], r["actor_id"], r["actor_kind"], r["note"], r["created_at"])
            for r in self._conn.execute("SELECT * FROM skill_events WHERE skill_id = ? ORDER BY seq", (row["skill_id"],)).fetchall()
        ]
        return versions, events

    def select_skills(self, caller: Caller, project_id: str, *, terms: Sequence[str] = (), tags: Sequence[str] = (), limit: int = 5) -> SkillSelection:
        """Deterministic selection over current approved versions. See ``SELECTION_METHOD``."""
        candidates = self.list_skills(caller, project_id)
        query_terms = sorted({t for t in _tokens(" ".join(str(x) for x in terms))})
        query_tags = _norm_tags(tags)
        scored: List[Tuple[int, str, str, SkillVersion]] = []
        for sv in candidates:
            tag_hits = len(set(query_tags) & set(sv.tags))
            term_hits = len(set(query_terms) & (_tokens(sv.name) | _tokens(sv.description) | set(sv.tags)))
            score = 3 * tag_hits + term_hits
            if (query_terms or query_tags) and score == 0:
                continue
            scored.append((score, sv.reviewed_at or sv.created_at, sv.name, sv))
        ordered = sorted(scored, key=lambda t: (-t[0], _desc(t[1]), t[2]))
        chosen = ordered[: max(0, int(limit))]
        return SkillSelection(
            method=SELECTION_METHOD, query_terms=query_terms, query_tags=query_tags,
            skills=[t[3] for t in chosen], scores={t[3].name: t[0] for t in chosen},
        )

    def export_skill_markdown(self, caller: Caller, project_id: str, name: str, *, version: Optional[int] = None) -> str:
        sv = self.get_skill(caller, project_id, name, version=version)
        fields = {
            "name": sv.name, "description": sv.description, "version": sv.version, "status": sv.status,
            "author": sv.author_id, "author_kind": sv.author_kind, "origin": sv.origin,
            "project": sv.project_id, "tags": sv.tags, "sources": sv.sources, "content_hash": sv.content_hash,
        }
        return render_frontmatter(fields) + "\n" + sv.body.rstrip("\n") + "\n"

    def import_skill_markdown(
        self, caller: Caller, project_id: str, text: str, *, sources: Sequence[str] = (), note: str = "",
        allow_private_sources: bool = False,
    ) -> SkillVersion:
        """Import a SKILL.md-style document as a new version (same review and source rules as ``save_skill``)."""
        fields, body = parse_frontmatter(text)
        name = fields.get("name")
        if not name:
            raise ValidationError("skill markdown needs a 'name' in frontmatter")
        tags = fields.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        role = self._require_role(caller, project_id, (ROLE_OWNER, ROLE_EDITOR, ROLE_AGENT), "import skills")
        needs_review = caller.kind == CALLER_AGENT or role == ROLE_AGENT
        return self._write_skill_version(
            caller, project_id, str(name), str(fields.get("description") or ""), body, tags, sources,
            note or "imported from markdown", status=SKILL_PROPOSED if needs_review else SKILL_APPROVED, origin=ORIGIN_IMPORT,
            allow_private_sources=allow_private_sources,
        )

    def _write_skill_version(
        self, caller: Caller, project_id: str, name: str, description: str, body: str, tags: Sequence[str],
        sources: Sequence[str], note: str, *, status: str, origin: str, allow_private_sources: bool = False,
    ) -> SkillVersion:
        if not is_valid_skill_name(name):
            raise ValidationError(f"skill name {name!r} must be lowercase letters, digits and hyphens, max 64 chars")
        description = " ".join(strip_invisible_unicode(_nonempty(description, "description")).split())
        body = strip_invisible_unicode(_nonempty(body, "body")).strip("\n") + "\n"
        tag_list = _norm_tags(tags)
        source_list = self._validate_refs(sources)
        self._check_skill_sources(caller, project_id, source_list, allow_private_sources)
        content_hash = hashlib.sha256("\x00".join([name, description, body, ",".join(tag_list)]).encode("utf-8")).hexdigest()
        ts = now_iso()
        with self._conn:
            row = self._conn.execute("SELECT skill_id FROM skills WHERE project_id = ? AND name = ?", (project_id, name)).fetchone()
            if row is None:
                skill_id = new_id("skl")
                self._conn.execute(
                    "INSERT INTO skills (skill_id, project_id, name, created_by, created_at, current_version) VALUES (?, ?, ?, ?, ?, NULL)",
                    (skill_id, project_id, name, caller.actor_id, ts),
                )
            else:
                skill_id = row["skill_id"]
            last = self._conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM skill_versions WHERE skill_id = ?", (skill_id,)).fetchone()["v"]
            version = int(last) + 1
            reviewed = (caller.actor_id, ts, note) if status == SKILL_APPROVED else (None, None, None)
            self._conn.execute(
                "INSERT INTO skill_versions (skill_id, version, author_id, author_kind, origin, status, description, body, tags_json, sources_json, "
                "content_hash, note, created_at, reviewed_by, reviewed_at, review_note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (skill_id, version, caller.actor_id, caller.kind, origin, status, description, body, dumps(tag_list), dumps(source_list),
                 content_hash, note or "", ts, *reviewed),
            )
            if status == SKILL_APPROVED:
                self._conn.execute("UPDATE skills SET current_version = ? WHERE skill_id = ?", (version, skill_id))
                self._event(skill_id, version, "saved_approved", caller, note or "", ts)
            else:
                self._event(skill_id, version, "proposed", caller, note or "", ts)
        return self._skill_version(skill_id, version)

    def _event(self, skill_id: str, version: Optional[int], event: str, caller: Caller, note: str, ts: str) -> None:
        self._conn.execute(
            "INSERT INTO skill_events (skill_id, version, event, actor_id, actor_kind, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (skill_id, version, event, caller.actor_id, caller.kind, note, ts),
        )

    # ------------------------------------------------------------------ session context

    def build_session_context(
        self, caller: Caller, room_id: str, *, terms: Sequence[str] = (), tags: Sequence[str] = (),
        max_skills: int = 5, max_decisions: int = 10, max_messages: int = 20, max_handoffs: int = 10,
    ) -> SessionContext:
        """Bounded, source-linked context for a fresh session in ``room_id``. Selection is deterministic and labelled."""
        room = self._require_read_room(caller, room_id)
        project = self._project(room.project_id)
        decisions = self.list_decisions(caller, room_id, limit=max_decisions)
        messages = self.list_messages(caller, room_id, limit=max_messages)
        incoming = [h for h in self.list_handoffs(caller, room_id, direction="incoming", status=HANDOFF_OPEN)][-max_handoffs:]
        skills = self.select_skills(caller, room.project_id, terms=terms, tags=tags, limit=max_skills)
        return SessionContext(
            project=project, room=room, caller_id=caller.actor_id, caller_kind=caller.kind, generated_at=now_iso(),
            decisions=decisions, messages=messages, incoming_handoffs=incoming, skills=skills,
            bounds={"max_skills": max_skills, "max_decisions": max_decisions, "max_messages": max_messages, "max_handoffs": max_handoffs},
            selection_note="messages/decisions/handoffs: newest N from this room only; skills: " + SELECTION_METHOD,
        )

    # ------------------------------------------------------------------ access helpers

    def _role(self, project_id: str, actor_id: str) -> Optional[str]:
        row = self._conn.execute("SELECT role FROM project_members WHERE project_id = ? AND actor_id = ?", (project_id, actor_id)).fetchone()
        return row["role"] if row else None

    def _require_role(self, caller: Caller, project_id: str, allowed: Sequence[str], action: str) -> str:
        self._check_caller(caller)
        self._project(project_id)
        role = self._role(project_id, caller.actor_id)
        if role is None or role not in allowed:
            raise AccessDenied(f"{caller.label()} may not {action} in project {project_id}: role {role!r} not in {tuple(allowed)}")
        return role

    def _is_room_member(self, room_id: str, actor_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM room_members WHERE room_id = ? AND actor_id = ?", (room_id, actor_id)).fetchone() is not None

    def _can_read_room(self, caller: Caller, room: Room) -> bool:
        role = self._role(room.project_id, caller.actor_id)
        if role is None:
            return False
        if room.visibility == VIS_SHARED:
            return True
        return self._is_room_member(room.room_id, caller.actor_id)

    def _can_write_room(self, caller: Caller, room: Room) -> bool:
        return self._can_read_room(caller, room) and self._role(room.project_id, caller.actor_id) != ROLE_VIEWER

    def _require_read_room(self, caller: Caller, room_id: str) -> Room:
        self._check_caller(caller)
        room = self._room(room_id)
        if not self._can_read_room(caller, room):
            raise AccessDenied(f"{caller.label()} may not read room {room_id} ({room.visibility}, project {room.project_id})")
        return room

    def _require_write_room(self, caller: Caller, room_id: str) -> Room:
        room = self._require_read_room(caller, room_id)
        if not self._can_write_room(caller, room):
            raise AccessDenied(f"{caller.label()} may not write to room {room_id}: viewer role")
        return room

    def _validate_refs(self, refs: Sequence[str]) -> List[str]:
        out: List[str] = []
        for ref in refs or ():
            parse_ref(ref)
            if ref not in out:
                out.append(str(ref))
        return out

    # ------------------------------------------------------------------ row loaders

    def _project(self, project_id: str) -> Project:
        row = self._conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        if row is None:
            raise NotFound(f"project {project_id} not found")
        return Project(row["project_id"], row["name"], row["domain"], row["owner_id"], row["created_at"])

    def _room(self, room_id: str) -> Room:
        row = self._conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
        if row is None:
            raise NotFound(f"room {room_id} not found")
        return self._room_from_row(row)

    @staticmethod
    def _room_from_row(row: sqlite3.Row) -> Room:
        return Room(row["room_id"], row["project_id"], row["name"], row["visibility"], row["created_by"], row["created_at"])

    def _insert_message(self, room: Room, caller: Caller, kind: str, content: str, refs: List[str]) -> Message:
        mid, ts = new_id("msg"), now_iso()
        with self._conn:
            self._conn.execute(
                "INSERT INTO messages (message_id, room_id, project_id, author_id, author_kind, kind, content, refs_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, room.room_id, room.project_id, caller.actor_id, caller.kind, kind, content, dumps(refs), ts),
            )
        return self._message(mid)

    def _message(self, message_id: str) -> Message:
        row = self._conn.execute("SELECT * FROM messages WHERE message_id = ?", (message_id,)).fetchone()
        if row is None:
            raise NotFound(f"message {message_id} not found")
        return self._message_from_row(row)

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> Message:
        return Message(
            row["message_id"], row["seq"], row["room_id"], row["project_id"], row["author_id"], row["author_kind"],
            row["kind"], row["content"], list(loads(row["refs_json"]) or []), row["created_at"],
        )

    def _decision(self, decision_id: str) -> Decision:
        row = self._conn.execute("SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        if row is None:
            raise NotFound(f"decision {decision_id} not found")
        return self._decision_from_row(row)

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> Decision:
        return Decision(
            row["decision_id"], row["seq"], row["room_id"], row["project_id"], row["author_id"], row["author_kind"],
            row["title"], row["rationale"], list(loads(row["source_refs_json"]) or []), row["message_id"], row["created_at"],
        )

    @staticmethod
    def _handoff_from_row(row: sqlite3.Row) -> HandoffPacket:
        items = [HandoffItem(**item) for item in (loads(row["items_json"]) or [])]
        result_raw = loads(row["result_json"])
        return HandoffPacket(
            handoff_id=row["handoff_id"], from_project=row["from_project"], from_room=row["from_room"],
            to_project=row["to_project"], to_room=row["to_room"], created_by=row["created_by"], creator_kind=row["creator_kind"],
            purpose=row["purpose"], instructions=row["instructions"], items=items, policy=HandoffPolicy(**(loads(row["policy_json"]) or {})),
            status=row["status"], created_at=row["created_at"], sent_message_id=row["sent_message_id"],
            received_message_id=row["received_message_id"], result=HandoffResult(**result_raw) if result_raw else None,
            closed_at=row["closed_at"], closed_by=row["closed_by"],
        )

    def _skill_version_by_name(self, project_id: str, name: str, version: int) -> SkillVersion:
        row = self._conn.execute("SELECT skill_id FROM skills WHERE project_id = ? AND name = ?", (project_id, name)).fetchone()
        if row is None:
            raise NotFound(f"skill {name} not found in project {project_id}")
        return self._skill_version(row["skill_id"], int(version))

    def _skill_version(self, skill_id: str, version: int) -> SkillVersion:
        row = self._conn.execute(
            "SELECT v.*, s.project_id, s.name, s.current_version FROM skill_versions v JOIN skills s ON s.skill_id = v.skill_id "
            "WHERE v.skill_id = ? AND v.version = ?",
            (skill_id, version),
        ).fetchone()
        if row is None:
            raise NotFound(f"skill version {version} not found")
        return self._skill_version_from_row(row, row["current_version"] == version)

    @staticmethod
    def _skill_version_from_row(row: sqlite3.Row, is_current: bool) -> SkillVersion:
        return SkillVersion(
            skill_id=row["skill_id"], project_id=row["project_id"], name=row["name"], version=int(row["version"]),
            author_id=row["author_id"], author_kind=row["author_kind"], origin=row["origin"], status=row["status"],
            description=row["description"], body=row["body"], tags=list(loads(row["tags_json"]) or []),
            sources=list(loads(row["sources_json"]) or []), content_hash=row["content_hash"], note=row["note"],
            created_at=row["created_at"], reviewed_by=row["reviewed_by"], reviewed_at=row["reviewed_at"],
            review_note=row["review_note"], is_current=bool(is_current),
        )


class _desc(str):
    """Wrapper so an ISO timestamp sorts newest-first inside an ascending sort key."""

    def __lt__(self, other: object) -> bool:  # type: ignore[override]
        return str.__gt__(self, str(other))

    def __gt__(self, other: object) -> bool:  # type: ignore[override]
        return str.__lt__(self, str(other))
