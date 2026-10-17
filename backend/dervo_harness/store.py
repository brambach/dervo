"""SQLite-backed store rooted at an explicit, Dervo-owned data directory.

There is no environment-variable or home-directory fallback: the caller passes the path.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Union

from .errors import ValidationError

SCHEMA_VERSION = 5  # 2 added the runtime tables; 3 added requests.resource; 4 added dispatches; 5 added agent_handles and delegations. Additive only.
# Columns added after a table first shipped: (table, column, DDL type). Applied with ALTER TABLE when missing.
ADDED_COLUMNS = (("requests", "resource", "TEXT"),)
DB_FILENAME = "dervo.sqlite3"
MARKER_FILENAME = "DERVO_DATA_DIR.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_members (
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    actor_id TEXT NOT NULL,
    role TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, actor_id)
);
CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    name TEXT NOT NULL,
    visibility TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS room_members (
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    actor_id TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (room_id, actor_id)
);
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    project_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, seq);
CREATE TABLE IF NOT EXISTS decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id TEXT NOT NULL UNIQUE,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    project_id TEXT NOT NULL,
    author_id TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    message_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoff_grants (
    grant_id TEXT PRIMARY KEY,
    from_project TEXT NOT NULL,
    to_project TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS handoffs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id TEXT NOT NULL UNIQUE,
    from_project TEXT NOT NULL,
    from_room TEXT NOT NULL,
    to_project TEXT NOT NULL,
    to_room TEXT NOT NULL,
    created_by TEXT NOT NULL,
    creator_kind TEXT NOT NULL,
    purpose TEXT NOT NULL,
    instructions TEXT NOT NULL,
    items_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_message_id TEXT NOT NULL,
    received_message_id TEXT NOT NULL,
    result_json TEXT,
    closed_at TEXT,
    closed_by TEXT
);
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    name TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    current_version INTEGER,
    UNIQUE (project_id, name)
);
CREATE TABLE IF NOT EXISTS skill_versions (
    skill_id TEXT NOT NULL REFERENCES skills(skill_id),
    version INTEGER NOT NULL,
    author_id TEXT NOT NULL,
    author_kind TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    body TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    sources_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    PRIMARY KEY (skill_id, version)
);
CREATE TABLE IF NOT EXISTS skill_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id TEXT NOT NULL,
    version INTEGER,
    event TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_kind TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- schema 2: runtime (execution grants, requests, runs, sessions, run events)
CREATE TABLE IF NOT EXISTS execution_grants (
    grant_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    actor_id TEXT NOT NULL,
    rooms_json TEXT NOT NULL,
    granted_by TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS requests (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    request_key TEXT NOT NULL,
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    scope TEXT NOT NULL,
    goal TEXT NOT NULL,
    submitted_by TEXT NOT NULL,
    submitter_kind TEXT NOT NULL,
    worker_actor_id TEXT NOT NULL,
    terms_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    status TEXT NOT NULL,
    current_run_id TEXT,
    attempts INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    resource TEXT,
    UNIQUE (room_id, request_key)
);
CREATE TABLE IF NOT EXISTS runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    attempt INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    worker_actor_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    worker_name TEXT NOT NULL,
    worker_is_fake INTEGER NOT NULL,
    worker_ref TEXT,
    owner_token TEXT,
    status TEXT NOT NULL,
    last_event_seq INTEGER NOT NULL,
    produced_refs_json TEXT NOT NULL,
    handoff_ids_json TEXT NOT NULL,
    error TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_scope ON runs(room_id, status);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    context_text TEXT NOT NULL,
    context_hash TEXT NOT NULL,
    skill_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    event_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- schema 4: supervisor linkage (one open handoff packet -> one request in its target room). The runtime
-- ownership lease lives in meta under key 'runtime_lease'; no other state was added.
CREATE TABLE IF NOT EXISTS dispatches (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    dispatch_id TEXT NOT NULL UNIQUE,
    handoff_id TEXT NOT NULL UNIQUE REFERENCES handoffs(handoff_id),
    origin_project TEXT NOT NULL,
    origin_room TEXT NOT NULL,
    origin_run_id TEXT,
    target_project TEXT NOT NULL,
    target_room TEXT NOT NULL,
    target_actor_id TEXT NOT NULL,
    target_worker TEXT NOT NULL,
    provider TEXT NOT NULL,
    account TEXT NOT NULL,
    goal TEXT NOT NULL,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    dispatched_by TEXT NOT NULL,
    dispatcher_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    run_id TEXT,
    error TEXT,
    result_json TEXT,
    omitted_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
-- schema 5: named-agent addressing (human-registered handles: one stable project/actor/room identity per handle)
-- and model-requested delegation (one structured, validated, budgeted ask -> one packet -> one dispatch).
CREATE TABLE IF NOT EXISTS agent_handles (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    handle_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    handle TEXT NOT NULL,
    display_name TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    room_id TEXT NOT NULL REFERENCES rooms(room_id),
    registered_by TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_handles_active ON agent_handles(project_id, handle) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS delegations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    delegation_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    root_request_id TEXT NOT NULL,
    parent_request_id TEXT NOT NULL,
    parent_run_id TEXT NOT NULL,
    parent_attempt INTEGER NOT NULL,
    depth INTEGER NOT NULL,
    requested_by TEXT NOT NULL,
    origin_room TEXT NOT NULL,
    target_handle TEXT NOT NULL,
    target_actor_id TEXT NOT NULL,
    target_room TEXT NOT NULL,
    target_worker TEXT NOT NULL,
    purpose TEXT NOT NULL,
    instructions TEXT NOT NULL,
    source_refs_json TEXT NOT NULL,
    handoff_id TEXT NOT NULL UNIQUE REFERENCES handoffs(handoff_id),
    dispatch_id TEXT NOT NULL UNIQUE REFERENCES dispatches(dispatch_id),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delegations_root ON delegations(root_request_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(text: Any) -> Any:
    return json.loads(text) if text else None


class Store:
    """Owns one SQLite connection under ``data_dir``."""

    def __init__(self, data_dir: Union[str, "os.PathLike[str]"]) -> None:
        if data_dir is None or not str(data_dir).strip():
            raise ValidationError("data_dir must be an explicit path; the harness has no default location")
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / DB_FILENAME
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._init_schema()
        except Exception:
            self.conn.close()
            raise

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA)
            row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                self.conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
                )
            elif int(row["value"]) > SCHEMA_VERSION:
                raise ValidationError(
                    f"data dir {self.data_dir} has schema version {row['value']}, this build expects {SCHEMA_VERSION}"
                )
            elif int(row["value"]) < SCHEMA_VERSION:
                # Forward-only, additive migration: every statement above is CREATE IF NOT EXISTS,
                # so an older data dir gains the new tables and keeps every existing row. Columns added
                # to existing tables are appended with ALTER TABLE, nullable, so old rows stay valid.
                for table, column, ddl in ADDED_COLUMNS:
                    present = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    if column not in present:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                self.conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))
        marker = self.data_dir / MARKER_FILENAME
        if not marker.exists():
            marker.write_text(
                dumps({"owner": "dervo-harness", "schema_version": SCHEMA_VERSION, "created_at": now_iso()}) + "\n",
                encoding="utf-8",
            )

    def close(self) -> None:
        self.conn.close()
