"""Render a :class:`SessionContext` as one fenced, labelled text block."""

from __future__ import annotations

from typing import List

from .models import SessionContext
from .upstream_adapted import build_context_block, truncate_description

MESSAGE_PREVIEW_CHARS = 400
HELPER_NOTE_CHARS = 200


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_session_context(ctx: SessionContext) -> str:
    lines: List[str] = [
        "Dervo session context (bounded, deterministic; not a relevance claim)",
        f"Project: {ctx.project.name} [{ctx.project.project_id}] domain={ctx.project.domain}",
        f"Room: {ctx.room.name} [{ctx.room.room_id}] visibility={ctx.room.visibility}",
        f"Caller: {ctx.caller_kind}:{ctx.caller_id}",
        f"Generated: {ctx.generated_at}",
        "Bounds: " + ", ".join(f"{k}={v}" for k, v in ctx.bounds.items()),
        f"Selection: {ctx.selection_note}",
        "",
        f"## Decisions in this room (newest {ctx.bounds['max_decisions']}, oldest first)",
    ]
    if not ctx.decisions:
        lines.append("- none recorded")
    for dec in ctx.decisions:
        sources = f" sources: {', '.join(dec.source_refs)}" if dec.source_refs else ""
        lines.append(f"- [{dec.ref}] {dec.created_at} {dec.author_kind}:{dec.author_id}: {dec.title}{sources}")
        if dec.rationale.strip():
            lines.append(f"  {_clip(dec.rationale, MESSAGE_PREVIEW_CHARS)}")

    lines += ["", f"## Recent messages in this room (newest {ctx.bounds['max_messages']}, oldest first, previews)"]
    if not ctx.messages:
        lines.append("- none recorded")
    for msg in ctx.messages:
        lines.append(f"- [{msg.ref}] {msg.created_at} {msg.author_kind}:{msg.author_id} ({msg.kind}): {_clip(msg.content, MESSAGE_PREVIEW_CHARS)}")

    lines += ["", "## Open handoffs addressed to this room"]
    if not ctx.incoming_handoffs:
        lines.append("- none open")
    for packet in ctx.incoming_handoffs:
        lines.append(
            f"- [{packet.ref}] from room {packet.from_room} (project {packet.from_project}) by {packet.creator_kind}:{packet.created_by}: {packet.purpose}"
        )
        if packet.instructions.strip():
            lines.append(f"  instructions: {_clip(packet.instructions, MESSAGE_PREVIEW_CHARS)}")
        for item in packet.items:
            flag = " (truncated)" if item.truncated else ""
            lines.append(f"  - [{item.ref}] {item.author_kind}:{item.author_id}{flag}: {_clip(item.excerpt, MESSAGE_PREVIEW_CHARS)}")

    if ctx.helpers:  # rendered only under a supervisor, and only the helpers this actor is authorised to ask
        lines += ["", "## Helpers this run may ask (exact handles; only through the request_help action, never by writing their name)"]
        for addr in ctx.helpers:
            shown = f" ({addr.display_name})" if addr.display_name and addr.display_name.lower() != addr.handle else ""
            note = f": {_clip(addr.note, HELPER_NOTE_CHARS)}" if addr.note.strip() else ""
            lines.append(f"- {addr.mention}{shown} home room {addr.room_id}, member {addr.member} ({addr.provider}){note}")

    lines += ["", f"## Skills ({len(ctx.skills.skills)} selected; method: {ctx.skills.method})"]
    if not ctx.skills.skills:
        lines.append("- none selected")
    for sv in ctx.skills.skills:
        reviewed = f"approved by {sv.reviewed_by} at {sv.reviewed_at}" if sv.reviewed_by else "unreviewed"
        provenance = f" sources: {', '.join(sv.sources)}" if sv.sources else ""
        lines += [
            f"### {sv.name} v{sv.version} [{sv.ref}] score={ctx.skills.scores.get(sv.name, 0)}",
            f"{truncate_description(sv.description)} | origin={sv.origin} author={sv.author_kind}:{sv.author_id} {reviewed}{provenance}",
            sv.body.rstrip("\n"),
            "",
        ]
    return build_context_block("\n".join(lines))
