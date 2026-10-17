"""Text-only Codex app-server worker port. Disabled by default; one app-server process per run; no tools of any kind.

What it does: for one run it checks a Dervo-owned ``CODEX_HOME`` for custom configuration, launches the pinned Codex
``app-server`` over stdio with every execution feature switched off by explicit ``-c`` overrides under ``--strict-config``,
and then walks the official protocol in a fixed order before any text is sent: ``initialize`` (pinned version, owned
home), ``initialized``, ``config/read`` (the effective configuration must equal the launch policy exactly), ``account/read``
(a signed-in ChatGPT subscription whose email is the configured identity), ``model/list`` (the selected model must be a
visible catalog entry), ``thread/start`` (the effective model, provider, sandbox, approval policy and reviewer must be
the selected ones, no instruction sources, no active turn). Only then does it send the run's fresh session context plus
goal as one ``turn/start`` with a read-only, no-network sandbox policy, follows the turn's notifications, and reports the
final agent message as the run result once ``turn/completed`` says ``completed``. Every deviation fails the run closed.
It never calls :class:`ScopedActions`.

What it does not do: it never reads or copies ``auth.json`` or any credential, never falls back to another home or
account, never resumes a previous thread (each run is a new thread; a restart replays nothing), never writes inside a
project checkout (the process and thread ``cwd`` is an empty directory under the Dervo work dir), and never steers a
turn already sent.

Evidence for each launch option and wire shape, so nothing here rests on a guess:

- ``reference/dervo_codex_policy.py.txt`` (the native app's pinned Codex 0.154.0 text-turn policy): the ``-c`` override
  set (``DISABLED_FEATURES``, ``LAUNCH_POLICY``), ``app-server --listen stdio:// --strict-config``, ``check_profile`` and
  ``verify_config``. The app's own tests (``test_local_codex.py``) pin that policy against the installed binary.
- ``reference/dervo_codex_adapter.py.txt`` (the app's ``CodexAdapter``, the proven protocol driver): the request order,
  ``clientInfo``, the ``initialize`` checks (``userAgent`` prefix, ``codexHome``), the ``initialized`` notification, the
  ``config/read``, ``account/read``, ``model/list`` and ``thread/start`` parameters and result checks, the ``turn/start``
  envelope, ``turn/interrupt``, the declined approval shapes, the ``-32601`` reply to anything else, and the notification
  scoping rule (``threadId``/``turnId`` must match).
- ``reference/dervo_codex_live.py.txt`` and ``reference/dervo_transport.py.txt``: the owned ``HOME``/``CODEX_HOME`` environment
  allowlist, the neutral process cwd, the process group, SIGINT/SIGTERM/SIGKILL handling and the JSONL decoder.
- ``reference/dervo_codex_synthetic_peer.py.txt``: the app's explicit in-memory server double; ``tests/fake_codex_app_server.py``
  is its stdio port and the only thing the tests here ever launch.
- The app's schema evidence (``schemas/codex-app-server/codex_app_server_protocol.v2.schemas.json``, read
  2026-09-12): ``Account`` (``chatgpt`` carries ``email`` and ``planType``; ``apiKey`` carries neither), ``ThreadStartParams``
  and ``ThreadStartResponse`` (required ``approvalPolicy``, ``approvalsReviewer``, ``cwd``, ``model``, ``modelProvider``,
  ``sandbox``, ``thread``), ``TurnStartParams`` (``input``, ``threadId``, ``cwd``, ``model``, ``sandboxPolicy``, ``approvalPolicy``,
  ``approvalsReviewer``, ``clientUserMessageId``), ``TurnInterruptParams``, ``TurnCompletedNotification``, ``SandboxPolicy``
  (``readOnly`` with ``networkAccess``).

Tool restrictions, stated plainly and enforced through the official protocol rather than the prompt: every execution
feature is disabled by launch override and the effective configuration is read back and compared before the account is
even consulted; the thread and the turn both carry the read-only, no-network sandbox and the ``on-request`` approval policy
with the user as reviewer, and the thread response is compared to that; no ``dynamicTools`` are offered; any approval
request the server issues is declined over the protocol and then fails the run; any ``item/tool/call`` gets a JSON-RPC
``-32601`` and fails the run. What this worker cannot observe is anything the app-server does entirely inside its own
process; the parent's gated no-inference handshake script is the step that checks the installed binary, and until it has
run this worker is for context verification only and stays disabled unless ``CodexAppServerConfig.enabled`` is set.

Observed on the installed binary by the parent's no-inference handshake (``reference/codex-handshake-finding.json``, Codex
0.154.0, ``initialize`` and ``config/read`` only; no account, thread, turn or model call):

- ``initialize`` reports ``codexHome`` as the canonical real path (``/private/var/...``) even when ``CODEX_HOME`` was given
  through the ``/var`` symlink. The owned-home check therefore compares real paths (``os.path.realpath`` on both sides);
  a different directory is still refused, marker or not, and the marker must still sit in the configured home.
- Under ``--strict-config`` a ``.codex/config.toml`` in an untrusted working directory is returned as a ``project`` layer
  whose ``config`` holds the file's parsed content and whose ``disabledReason`` says it was not loaded ("add ... as a
  trusted project in <home>/config.toml"). That layer is metadata about a file Codex refused, not an effective setting.
  ``verify_launch_config`` accepts a ``project`` layer only when its ``disabledReason`` is a non-empty string and still
  requires the effective configuration to equal the launch policy on every controlled key; a project layer without a
  ``disabledReason`` and any other non-empty layer are refused as before. Trust for a project could only come from the
  owned ``config.toml`` (refused by ``check_profile``) or a managed layer (refused when non-empty), so an active project
  layer is never accepted silently.

Not verified against the installed binary in this build (see BUILD-STATE): whether the app-server exits on stdin EOF
after a completed turn (the app never relied on it; it terminates the group, and so does this worker after
``finish_timeout``). The ``userAgent`` prefix ``dervo/0.154.0 `` was observed in the parent's handshake.

Codex 0.156.1 (0.8.2, 25 Sep; the app's evidence/codex-local-upgrade): this worker's exact launch policy passes
``--strict-config`` and ``verify_launch_config``, and ``userAgent`` starts ``dervo/0.156.1 ``, both measured signed out.
On a signed-in profile 0.156.1 sends one ``account/updated`` (``authMode`` chatgpt) while ``account/read`` is pending;
0.154.0 sent none. That one is accepted and the ``account/read`` reply is still what's verified; any other
``account/updated`` still fails the run.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .claude_worker import ENV_PATH, INPUT_LIMIT, OUTPUT_LIMIT, CliProtocolError, _JsonLines
from .errors import AccessDenied, StateError, ValidationError
from .models import EVENT_FAILED, EVENT_PROGRESS, EVENT_RESULT, EVENT_STOPPED, WorkAssignment, WorkerEvent, WorkerObservation

CODEX_HOME_MARKER = "DERVO_CODEX_HOME.json"
PINNED_VERSION = "0.156.1"
CLIENT_INFO = {"name": "dervo", "title": "Dervo", "version": "0.1.0"}  # identical to the app's, so the userAgent check is the app's check
PROVIDER = "codex-app-server"

# Disabling shell is not enough: apps, hooks and host-side tools are independent features (reference policy).
DISABLED_FEATURES: Tuple[str, ...] = tuple(
    "shell_tool unified_exec shell_snapshot shell_zsh_fork unified_exec_zsh_fork "
    "apps connectors plugins remote_plugin plugin_hooks codex_hooks hooks "
    "browser_use browser_use_external computer_use in_app_browser image_generation imagegenext "
    "view_image js_repl code_mode code_mode_host code_mode_only multi_agent multi_agent_v2 collab "
    "memories memory_tool skill_search skill_mcp_dependency_install skill_env_var_dependency_prompt "
    "tool_search tool_suggest search_tool web_search web_search_cached web_search_request "
    "standalone_web_search remote_control workspace_dependencies external_migration "
    "external_agent_memory_import request_permissions_tool request_permissions "
    "code_mode_prewarm unified_exec_tty shell_snapshot_v2 in_app_local_automation "
    "worktrees sleep_tool step_model_switching".split()
)
LAUNCH_POLICY: Dict[str, Any] = {
    "cli_auth_credentials_store": "file", "mcp_oauth_credentials_store": "file",
    "forced_login_method": "chatgpt", "model_provider": "openai", "approval_policy": "on-request",
    "approvals_reviewer": "user", "sandbox_mode": "read-only", "web_search": "disabled",
    "allow_login_shell": False, "project_doc_max_bytes": 0,
    "features": {**{key: False for key in DISABLED_FEATURES}, "skip_host_skill_discovery": True},
    "shell_environment_policy": {"inherit": "none"}, "mcp_servers": {},
}
SANDBOX_POLICY = {"type": "readOnly", "networkAccess": False}
THREAD_EXPECTED_BASE = {"modelProvider": "openai", "approvalPolicy": "on-request", "approvalsReviewer": "user", "sandbox": SANDBOX_POLICY}
APPROVAL_DECISIONS = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
}
PROFILE_FORBIDDEN = ("config.toml", ".codex/config.toml", "hooks.json", "hooks", "plugins", ".agents")
MAX_CATALOG_PAGES = 20
TEXT_LIMIT = 1024 * 1024


def toml_value(value: Any) -> str:
    """The app's inline-TOML encoder for ``-c key=value`` overrides (reference policy)."""
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    return json.dumps(value)


def launch_argv(executable: str) -> List[str]:
    args = [executable]
    for key, value in LAUNCH_POLICY.items():
        args += ["-c", key + "=" + toml_value(value)]
    return args + ["app-server", "--listen", "stdio://", "--strict-config"]


def check_profile(home: Any) -> None:
    """Refuse custom owned configuration before startup, including executable hooks (reference policy ``check_profile``).
    Codex itself installs ``skills/.system`` during metadata initialization; that is allowed."""
    root = Path(home)
    skills = root / "skills"
    if skills.exists() or skills.is_symlink():
        if skills.is_symlink() or not skills.is_dir() or any(p.name != ".system" or p.is_symlink() for p in skills.iterdir()):
            raise ValidationError(f"custom Codex skills under {skills} need review before sending; refusing to launch")
    for relative in PROFILE_FORBIDDEN:
        p = root / relative
        if p.exists() or p.is_symlink():
            raise ValidationError(f"custom Codex profile configuration needs review: {relative} exists under {root}; refusing to launch")


def same_directory(reported: Any, owned: str) -> bool:
    """True when ``reported`` names the owned directory: byte-equal, or the same real path once symlinks are resolved
    (Codex reports ``codexHome`` canonically, ``/private/var/...`` for a ``/var/...`` ``CODEX_HOME`` on macOS). Anything
    that is not an absolute path string, or resolves to a different directory, is not the owned home."""
    if not isinstance(reported, str) or not reported or not os.path.isabs(reported):
        return False
    return reported == owned or os.path.realpath(reported) == os.path.realpath(owned)


def verify_launch_config(result: Any) -> Dict[str, Any]:
    """The app's ``verify_config`` with one distinction the installed binary made necessary: exactly one session-flags
    layer equal to the policy; a ``project`` layer that Codex itself reports as not loaded (``disabledReason`` is a
    non-empty string) is recorded as inert metadata; every other layer must be empty; and the effective configuration
    must equal the policy on every controlled key regardless of what any layer says. Returns what was observed about
    disabled layers so the caller can log it; raises ``CliProtocolError`` on any deviation."""
    if not isinstance(result, dict):
        raise CliProtocolError("Codex config/read returned a non-object")
    layers = result.get("layers")
    if not isinstance(layers, list) or not layers or any(not isinstance(x, dict) for x in layers):
        raise CliProtocolError("Codex did not return configuration layers")
    flags = []
    disabled: List[Dict[str, Any]] = []
    for layer in layers:
        name = layer.get("name") if isinstance(layer.get("name"), dict) else {}
        kind = name.get("type")
        if kind == "sessionFlags":
            flags.append(layer.get("config"))
        elif layer.get("config") == {}:
            continue
        elif kind == "project" and isinstance(layer.get("disabledReason"), str) and layer["disabledReason"].strip():
            disabled.append({"type": kind, "dotCodexFolder": name.get("dotCodexFolder"), "disabledReason": layer["disabledReason"],
                             "keys": sorted(layer["config"]) if isinstance(layer.get("config"), dict) else None})
        else:
            raise CliProtocolError(f"custom or managed Codex configuration needs review before sending (layer {kind!r} is not empty)")
    if flags != [LAUNCH_POLICY]:
        raise CliProtocolError("Codex launch policy was not applied exactly")
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    for k, v in LAUNCH_POLICY.items():
        if k in ("features", "shell_environment_policy"):
            continue
        if config.get(k) != v:
            raise CliProtocolError(f"Codex effective configuration differs from the launch policy at {k!r}: {config.get(k)!r}")
    for key in ("features", "shell_environment_policy"):
        effective = config.get(key) if isinstance(config.get(key), dict) else {}
        bad = sorted(k for k, v in LAUNCH_POLICY[key].items() if effective.get(k) != v)
        if bad:
            raise CliProtocolError(f"Codex effective tool controls differ from the launch policy: {key} {', '.join(bad)}")
    for key in ("hooks", "plugins", "model_providers", "notify", "model_catalog_json", "model_instructions_file"):
        if config.get(key) not in (None, {}, []):
            raise CliProtocolError(f"unexpected executable or provider configuration: {key}")
    return {"disabled_layers": disabled}


@dataclass(frozen=True)
class CodexAppServerConfig:
    """Explicit identity and execution options. ``enabled`` defaults to False: nothing launches without it."""

    executable: str        # absolute path to the pinned Codex binary (tests point it at a fake)
    model: str             # catalog model id; must be visible in model/list and effective on thread/start
    project_id: str        # the only harness project this worker serves
    codex_home: str        # Dervo-owned CODEX_HOME carrying CODEX_HOME_MARKER; never the personal default ~/.codex
    identity: str          # account email that account/read must report; anything else is refused
    work_dir: str          # Dervo-owned dir for per-run state, the persisted prompt and the empty cwd
    enabled: bool = False  # explicit opt-in to launching anything at all
    version: str = PINNED_VERSION           # initialize must report this app-server version
    account_type: str = "chatgpt"           # a ChatGPT subscription login; an apiKey account is refused (API billing)
    plan_types: Tuple[str, ...] = ("plus", "pro")
    init_timeout: float = 120.0
    turn_timeout: float = 1800.0
    finish_timeout: float = 10.0   # after turn/completed: how long to wait for exit on EOF before terminating the group
    drain_wait: float = 0.25
    stop_grace: float = 2.0        # how long cancel waits for the process to exit after a signal
    interrupt_wait: float = 2.0    # how long cancel waits for turn/completed(interrupted) after turn/interrupt

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class _Live:
    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        self.decoder = _JsonLines()
        self.stderr = bytearray()
        self.read_bytes = 0
        self.seq = 0
        self.pending: Dict[int, str] = {}
        self.account_announced = False  # the one account/updated Codex 0.156.1 sends while account/read is pending
        self.models: List[Dict[str, Any]] = []
        self.cursors: set = set()
        self.deltas: List[str] = []
        self.snapshot: Optional[str] = None
        self.stdin_closed = False
        self.deadline = time.monotonic()

    def close(self) -> None:
        try:
            self.selector.close()
        finally:
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass


class CodexAppServerWorker:
    """:class:`WorkerPort` over the pinned Codex app-server, text-only. See the module docstring for the contract."""

    name = "codex-app-server-text-only"
    is_fake = False

    def __init__(self, config: CodexAppServerConfig) -> None:
        self.config = config
        self.model_name = config.model
        self._validate(config)
        self.work_dir = Path(config.work_dir)
        self.runs_dir = self.work_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._live: Dict[str, _Live] = {}
        self._state: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ configuration

    @staticmethod
    def default_codex_home() -> Path:
        from .claude_worker import account_home

        return Path(account_home()) / ".codex"

    @classmethod
    def prepare_codex_home(cls, path: str) -> Path:
        """Claim an empty or missing directory as Dervo-owned by writing the marker. Nothing is copied into it: a human
        signs in there with the binary's own login under ``CODEX_HOME``. Refuses ``~/.codex`` and any non-empty unmarked dir."""
        target = Path(path)
        if not target.is_absolute():
            raise ValidationError("codex_home must be an absolute path")
        if target.resolve() == cls.default_codex_home().resolve():
            raise ValidationError(f"{target} is the personal default CODEX_HOME; Dervo needs its own")
        marker = target / CODEX_HOME_MARKER
        if target.exists():
            if not target.is_dir():
                raise ValidationError(f"{target} is not a directory")
            if any(target.iterdir()) and not marker.exists():
                raise ValidationError(f"{target} is not empty and carries no {CODEX_HOME_MARKER}; refusing to claim it")
        target.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            marker.write_text(json.dumps({"owner": "dervo-harness", "purpose": "CODEX_HOME for the text-only Codex worker"}) + "\n", encoding="utf-8")
        return target

    @classmethod
    def _validate(cls, c: CodexAppServerConfig) -> None:
        for field_name in ("executable", "model", "project_id", "codex_home", "identity", "work_dir", "version"):
            value = getattr(c, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"CodexAppServerConfig.{field_name} must be a non-empty string")
        exe = Path(c.executable)
        if not exe.is_absolute() or not exe.is_file() or not os.access(exe, os.X_OK):
            raise ValidationError(f"executable {c.executable} must be an absolute path to an executable file")
        if any(ch in c.identity for ch in "\r\n\x00 \t") or "@" not in c.identity:
            raise ValidationError("identity must be the account email account/read is expected to report")
        home = Path(c.codex_home)
        if not home.is_absolute() or not home.is_dir():
            raise ValidationError(f"codex_home {c.codex_home} must be an existing absolute directory")
        if home.resolve() == cls.default_codex_home().resolve():
            raise ValidationError(f"codex_home {c.codex_home} is the personal default CODEX_HOME; refusing")
        if not (home / CODEX_HOME_MARKER).is_file():
            raise ValidationError(f"codex_home {c.codex_home} carries no {CODEX_HOME_MARKER}; call CodexAppServerWorker.prepare_codex_home first")
        if not Path(c.work_dir).is_absolute():
            raise ValidationError("work_dir must be an absolute path")
        if home.resolve() == Path(c.work_dir).resolve():
            raise ValidationError("work_dir and codex_home must differ")
        if not c.plan_types:
            raise ValidationError("plan_types must name at least one accepted subscription plan")

    def environment(self) -> Dict[str, str]:
        """Allowlisted child environment. HOME is the owned home (no Keychain is needed for Codex) and CODEX_HOME the same."""
        return {"PATH": ENV_PATH, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "HOME": self.config.codex_home, "CODEX_HOME": self.config.codex_home}

    def argv(self) -> List[str]:
        return launch_argv(self.config.executable)

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "is_fake": self.is_fake, "provider": PROVIDER, "model": self.config.model, "enabled": self.config.enabled,
                "project_id": self.config.project_id, "codex_home": self.config.codex_home, "identity": self.config.identity,
                "version": self.config.version, "tools": [], "sandbox": LAUNCH_POLICY["sandbox_mode"], "approval_policy": LAUNCH_POLICY["approval_policy"],
                "disabled_features": list(DISABLED_FEATURES), "argv_shape": [a for a in self.argv() if not a.startswith(("{", '"')) or a == "{}"]}

    # ------------------------------------------------------------------ WorkerPort

    def start(self, assignment: WorkAssignment, actions: Any) -> str:
        if not self.config.enabled:
            raise StateError("Codex app-server worker is disabled by default; set CodexAppServerConfig.enabled=True to launch anything")
        if assignment.project_id != self.config.project_id:
            raise AccessDenied(f"this worker serves project {self.config.project_id}, not {assignment.project_id}")
        check_profile(self.config.codex_home)
        ref = f"codex:{assignment.run_id}:{assignment.attempt}"
        base = str(self.runs_dir / f"{assignment.run_id}.{assignment.attempt}")
        cwd = Path(base + ".cwd")
        cwd.mkdir(parents=True, exist_ok=True)
        if any(cwd.iterdir()):
            raise StateError(f"working directory {cwd} is not empty; refusing to launch over it")
        prompt = f"{assignment.context_text}\n\nTask: {assignment.goal}\n"
        prompt_path = Path(base + ".prompt.txt")
        prompt_path.write_text(prompt, encoding="utf-8")
        st = {
            "ref": ref, "run_id": assignment.run_id, "attempt": assignment.attempt, "model": self.config.model, "pid": None, "phase": "launching",
            "event_seq": 0, "pending": [], "done": False, "cancel_requested": False, "signals": [], "prompt_path": str(prompt_path),
            "cwd": str(cwd.resolve()), "transcript_path": base + ".transcript.jsonl", "started_at": time.time(), "thread_id": None, "turn_id": None,
            "turn_status": None, "turn_error": None, "interrupt": None, "interrupt_sent": False, "exit_how": None, "account": None,
            "client_message_id": f"{assignment.run_id}:{assignment.attempt}",
        }
        self._state[ref] = st
        proc = subprocess.Popen(self.argv(), cwd=st["cwd"], env=self.environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        live = _Live(proc)
        live.deadline = time.monotonic() + self.config.init_timeout
        self._live[ref] = live
        st["pid"], st["phase"] = proc.pid, "initializing"
        self._emit(ref, EVENT_PROGRESS, {"phase": "profile_checked", "codex_home": self.config.codex_home, "forbidden": list(PROFILE_FORBIDDEN)})
        try:
            self._request(ref, "initialize", {"clientInfo": dict(CLIENT_INFO), "capabilities": {"experimentalApi": False}})
        except Exception as exc:
            self._fail(ref, f"could not send initialize: {type(exc).__name__}: {exc}")
        self._save(ref)
        return ref

    def drain(self, worker_ref: str) -> List[WorkerEvent]:
        st = self._need(worker_ref)
        live = self._live.get(worker_ref)
        if live is None:
            self._observe_detached(worker_ref)
            return self._flush(worker_ref)
        if st["done"]:
            return self._flush(worker_ref)
        try:
            for message in self._read(live, self.config.drain_wait):
                self._receive(worker_ref, message)
            code = live.process.poll()
            if code is not None:
                for message in self._read(live, 1.0):
                    self._receive(worker_ref, message)
                self._exited(worker_ref, code)
            elif time.monotonic() > live.deadline:
                if st["phase"] == "finishing":
                    self._terminate_after_turn(worker_ref)  # the app never relied on EOF exit either: it closes the group
                else:
                    raise CliProtocolError(f"Codex app-server deadline exceeded in phase {st['phase']}")
        except CliProtocolError as exc:
            self._fail(worker_ref, str(exc))
        return self._flush(worker_ref)

    def steer(self, worker_ref: str, note: str) -> None:
        self._need(worker_ref)
        raise StateError("the text-only Codex worker sends one turn per run and cannot steer a turn already sent; cancel and resubmit with the note in the goal")

    def cancel(self, worker_ref: str) -> Optional[WorkerObservation]:
        st = self._need(worker_ref)
        if st["done"]:
            return WorkerObservation("finished", events=self._flush(worker_ref), note="already finished")
        st["cancel_requested"] = True
        live = self._live.get(worker_ref)
        pid = st.get("pid")
        if live is None:
            if not pid or not self._alive(pid):
                self._observe_detached(worker_ref)
                return WorkerObservation("finished", events=self._flush(worker_ref), note="process already gone") if st["done"] else None
            self._signal(worker_ref, pid)
            end = time.monotonic() + self.config.stop_grace
            while time.monotonic() < end and self._alive(pid):
                time.sleep(0.02)
            self._observe_detached(worker_ref)
            self._save(worker_ref)
            return WorkerObservation("finished", events=self._flush(worker_ref), note="process exited after signal") if st["done"] else None
        if st["phase"] == "running" and st.get("turn_id") and not st["interrupt_sent"]:
            # First the protocol: turn/interrupt, then wait briefly for the server's own turn/completed(interrupted).
            st["interrupt_sent"], st["interrupt"] = True, "requested"
            try:
                self._request(worker_ref, "turn/interrupt", {"threadId": st["thread_id"], "turnId": st["turn_id"]})
                end = time.monotonic() + self.config.interrupt_wait
                while time.monotonic() < end and st["turn_status"] is None and live.process.poll() is None:
                    for message in self._read(live, 0.1):
                        self._receive(worker_ref, message)
            except CliProtocolError:
                pass  # output after a stop request is not held against the worker; the exit is what matters
            st["interrupt"] = "confirmed" if st["turn_status"] == "interrupted" else "not_observed"
            self._save(worker_ref)
        self._signal(worker_ref, live.process.pid)
        try:
            code = live.process.wait(timeout=self.config.stop_grace)
        except subprocess.TimeoutExpired:
            self._save(worker_ref)
            return None  # requested, not confirmed: the runtime keeps the run cancelling
        try:
            for message in self._read(live, 1.0):
                self._receive(worker_ref, message)
        except CliProtocolError:
            pass
        self._exited(worker_ref, code)
        return WorkerObservation("finished", events=self._flush(worker_ref), note=f"process exited {code} after {st['signals'][-1]}")

    def attach(self, worker_ref: str, actions: Any) -> WorkerObservation:
        st = self._load(worker_ref)
        if st is None:
            return WorkerObservation("lost", note="no state file for this worker_ref in the Dervo work dir")
        if worker_ref in self._live and not st["done"]:
            return WorkerObservation("running")
        if not st["done"]:
            self._observe_detached(worker_ref)
        if st["done"]:
            return WorkerObservation("finished", events=self._flush(worker_ref))
        return WorkerObservation("running", note="process is alive but its pipes belong to a previous runtime; only cancel can act on it")

    # ------------------------------------------------------------------ protocol

    def _request(self, ref: str, method: str, params: Dict[str, Any]) -> None:
        live = self._live[ref]
        live.seq += 1
        live.pending[live.seq] = method
        self._write(ref, {"id": live.seq, "method": method, "params": params})

    def _receive(self, ref: str, message: Dict[str, Any]) -> None:
        st, live = self._state[ref], self._live[ref]
        self._transcribe(ref, message)
        if "method" in message and "id" in message:
            self._server_request(ref, message)
            return
        if "id" in message:
            self._response(ref, message)
            return
        method, params = message.get("method"), message.get("params") if isinstance(message.get("params"), dict) else {}
        if method == "account/updated":
            # Codex 0.156.1 loads the saved sign-in when account/read asks for it and announces it with one account/updated
            # just before the reply (measured 25 Sep on a signed-in profile; 0.154.0 sent none). The reply that follows is
            # the account this worker verifies, so that one announcement passes when it says chatgpt. Any other stops.
            if (st["phase"] != "authenticating" or "account/read" not in live.pending.values()
                    or live.account_announced or params.get("authMode") != "chatgpt"):
                raise CliProtocolError("Account changed during the run (account/updated); stopping so the identity can be verified again")
            live.account_announced = True
            return
        if method == "turn/started":
            if params.get("threadId") != st["thread_id"]:
                raise CliProtocolError("turn started in another conversation")
            self._started(ref, params.get("turn"))
        elif method == "item/agentMessage/delta":
            self._scope(ref, params)
            if st["phase"] not in ("running",) or st["turn_status"] is not None:
                raise CliProtocolError("late provider delta after the terminal turn")
            delta = params.get("delta")
            if not isinstance(delta, str):
                raise CliProtocolError("malformed agent message delta")
            live.deltas.append(delta)
            if sum(len(d) for d in live.deltas) > TEXT_LIMIT:
                raise CliProtocolError("agent message exceeded the text limit")
        elif method == "item/completed":
            self._scope(ref, params)
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "agentMessage":
                text = item.get("text")
                if not isinstance(text, str) or len(text) > TEXT_LIMIT:
                    raise CliProtocolError("malformed agent message item")
                live.snapshot = text
        elif method == "turn/completed":
            if st["turn_status"] is not None:
                raise CliProtocolError("Codex sent an event after the terminal turn (duplicate turn/completed)")
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self._scope(ref, {"threadId": params.get("threadId"), "turnId": turn.get("id")})
            status = turn.get("status")
            if status not in ("completed", "interrupted", "failed"):
                raise CliProtocolError(f"unknown terminal turn status {status!r}")
            st["turn_status"], st["turn_error"] = status, turn.get("error")
            st["phase"] = "finishing"
            live.deadline = time.monotonic() + self.config.finish_timeout
            self._emit(ref, EVENT_PROGRESS, {"phase": "turn_completed", "status": status, "error": self._error_text(turn.get("error"))})
            self._close_stdin(live)  # EOF: nothing more will ever be sent; the exit (or the group termination) follows
        elif method == "error":
            self._scope(ref, params)
            self._emit(ref, EVENT_PROGRESS, {"phase": "provider_error", "error": self._error_text(params.get("error")), "will_retry": bool(params.get("willRetry"))})
        # thread/started, item/started, reasoning and other informational notifications carry no action.

    def _server_request(self, ref: str, message: Dict[str, Any]) -> None:
        """The server asks the client for something. Text-only runs grant nothing: answer by protocol, then fail closed."""
        method, mid = message.get("method"), message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if method in APPROVAL_DECISIONS:
            self._refuse(ref, {"id": mid, "result": APPROVAL_DECISIONS[method]})
            raise CliProtocolError(f"Codex asked for approval ({method}) in turn {params.get('turnId')!r}; a text-only run permits no command, "
                                   "file change or permission grant; declined by protocol and the run fails closed")
        self._refuse(ref, {"id": mid, "error": {"code": -32601, "message": "Dervo does not support this request"}})
        if method == "item/tool/call":
            raise CliProtocolError(f"Codex issued a tool call ({params.get('tool')!r}); no tools exist in a text-only run; refused by protocol")
        raise CliProtocolError(f"Codex sent an unsupported server request ({method!r}); refused by protocol")

    def _refuse(self, ref: str, reply: Dict[str, Any]) -> None:
        """Send the protocol refusal, then EOF, and give the server a moment to read both before the run fails closed
        (so the refusal is delivered rather than lost to the kill that follows)."""
        live = self._live[ref]
        try:
            self._write(ref, reply)
        except Exception:
            return
        self._close_stdin(live)
        try:
            live.process.wait(timeout=min(self.config.stop_grace, 2.0))
        except subprocess.TimeoutExpired:
            pass

    def _response(self, ref: str, message: Dict[str, Any]) -> None:
        st, live = self._state[ref], self._live[ref]
        method = live.pending.pop(message.get("id"), None)
        if method is None:
            raise CliProtocolError(f"unknown or duplicate response id {message.get('id')!r}")
        if "error" in message:
            if method == "turn/interrupt":
                st["interrupt_error"] = self._error_text(message["error"])
                return
            raise CliProtocolError(f"Codex rejected {method}: {self._error_text(message['error'])}")
        result = message.get("result") if isinstance(message.get("result"), dict) else {}
        if method == "initialize":
            agent = str(result.get("userAgent", ""))
            if not agent.startswith(f"dervo/{self.config.version} "):
                raise CliProtocolError(f"Codex app-server version differs from the verified protocol (userAgent {agent!r}, expected dervo/{self.config.version})")
            if not same_directory(result.get("codexHome"), self.config.codex_home):
                raise CliProtocolError(f"Codex reported home {result.get('codexHome')!r}, not the owned home {self.config.codex_home!r}")
            self._write(ref, {"method": "initialized", "params": {}})
            st["phase"] = "configuring"
            self._emit(ref, EVENT_PROGRESS, {"phase": "initialized", "userAgent": agent, "codexHome": result.get("codexHome")})
            self._request(ref, "config/read", {"includeLayers": True, "cwd": st["cwd"]})
        elif method == "config/read":
            observed = verify_launch_config(result)
            st["phase"] = "authenticating"
            self._emit(ref, EVENT_PROGRESS, {"phase": "config_verified", "sandbox_mode": LAUNCH_POLICY["sandbox_mode"],
                                             "approval_policy": LAUNCH_POLICY["approval_policy"], "disabled_features": len(DISABLED_FEATURES),
                                             "disabled_layers": observed["disabled_layers"]})
            self._request(ref, "account/read", {"refreshToken": False})
        elif method == "account/read":
            account = result.get("account")
            if not isinstance(account, dict) or not account:
                raise CliProtocolError(f"Codex is not signed in under {self.config.codex_home}; sign in there explicitly (no fallback)")
            if account.get("type") != self.config.account_type:
                raise CliProtocolError(f"Codex account is type {account.get('type')!r}, not a {self.config.account_type} subscription login; refusing to avoid API-key billing")
            if account.get("email") != self.config.identity:
                raise CliProtocolError(f"Codex account {account.get('email')!r} is not the configured identity {self.config.identity!r}; refusing")
            if account.get("planType") not in self.config.plan_types:
                raise CliProtocolError(f"Codex plan {account.get('planType')!r} is not one of {self.config.plan_types}; refusing")
            st["account"] = {k: account.get(k) for k in ("type", "email", "planType")}
            st["phase"] = "cataloguing"
            self._emit(ref, EVENT_PROGRESS, {"phase": "account_observed", **st["account"]})
            live.models, live.cursors = [], set()
            self._request(ref, "model/list", {"includeHidden": False, "limit": 100})
        elif method == "model/list":
            data = result.get("data")
            if not isinstance(data, list):
                raise CliProtocolError("malformed model catalog")
            live.models.extend(m for m in data if isinstance(m, dict))
            cursor = result.get("nextCursor")
            if cursor:
                if cursor in live.cursors or len(live.cursors) >= MAX_CATALOG_PAGES:
                    raise CliProtocolError("invalid model catalog pagination")
                live.cursors.add(cursor)
                self._request(ref, "model/list", {"includeHidden": False, "limit": 100, "cursor": cursor})
                return
            visible = sorted(str(m.get("model")) for m in live.models if m.get("hidden") is False)
            if not any(m.get("model") == self.config.model and m.get("hidden") is False for m in live.models):
                raise CliProtocolError(f"the selected model {self.config.model!r} is not in the Codex catalog for this account (visible: {visible})")
            st["phase"] = "starting_thread"
            self._emit(ref, EVENT_PROGRESS, {"phase": "catalog_verified", "model": self.config.model, "visible_models": len(visible)})
            self._request(ref, "thread/start", {"cwd": st["cwd"], "model": self.config.model, "modelProvider": "openai", "sandbox": "read-only",
                                                "approvalPolicy": "on-request", "approvalsReviewer": "user", "ephemeral": False})
        elif method == "thread/start":
            expected = {"model": self.config.model, "cwd": st["cwd"], **THREAD_EXPECTED_BASE}
            bad = sorted(k for k, v in expected.items() if result.get(k) != v)
            if bad:
                raise CliProtocolError(f"Codex effective settings differ from the selected settings on {', '.join(bad)}: "
                                       + ", ".join(f"{k}={result.get(k)!r}" for k in bad))
            if result.get("instructionSources"):
                raise CliProtocolError(f"Codex loaded instruction sources {result.get('instructionSources')!r}; no text was sent")
            thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
            if any(isinstance(t, dict) and t.get("status") == "inProgress" for t in (thread.get("turns") or [])):
                raise CliProtocolError("Codex reports an active provider turn on the new thread; no text was sent")
            sid = thread.get("id")
            if not isinstance(sid, str) or not sid:
                raise CliProtocolError("Codex thread/start returned no thread id")
            st["thread_id"], st["phase"] = sid, "sending"
            live.deadline = time.monotonic() + self.config.turn_timeout
            self._emit(ref, EVENT_PROGRESS, {"phase": "thread_started", "thread_id": sid, "sandbox": result.get("sandbox"), "approval_policy": result.get("approvalPolicy")})
            prompt = Path(st["prompt_path"]).read_text(encoding="utf-8")
            self._request(ref, "turn/start", {"threadId": sid, "input": [{"type": "text", "text": prompt}], "clientUserMessageId": st["client_message_id"],
                                              "cwd": st["cwd"], "model": self.config.model, "approvalPolicy": "on-request", "approvalsReviewer": "user",
                                              "sandboxPolicy": dict(SANDBOX_POLICY)})
        elif method == "turn/start":
            self._started(ref, result.get("turn"))
        elif method == "turn/interrupt":
            st["interrupt_acknowledged"] = True
        else:
            raise CliProtocolError(f"unexpected response for {method!r}")

    def _started(self, ref: str, turn: Any) -> None:
        st = self._state[ref]
        if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not turn["id"]:
            raise CliProtocolError("missing provider turn id")
        if st["turn_id"]:
            if st["turn_id"] != turn["id"]:
                raise CliProtocolError("provider turn id changed")
            return
        if st["phase"] != "sending":
            raise CliProtocolError(f"unexpected provider turn in phase {st['phase']}")
        st["turn_id"], st["phase"] = turn["id"], "running"
        self._emit(ref, EVENT_PROGRESS, {"phase": "turn_started", "turn_id": turn["id"], "thread_id": st["thread_id"]})

    def _scope(self, ref: str, params: Dict[str, Any]) -> None:
        st = self._state[ref]
        if params.get("threadId") != st["thread_id"] or params.get("turnId") != st["turn_id"]:
            raise CliProtocolError("provider event belongs to another conversation or turn")

    @staticmethod
    def _error_text(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message") or json.dumps(error, sort_keys=True))[:400]
        return "" if error is None else str(error)[:400]

    # ------------------------------------------------------------------ termination

    def _terminate_after_turn(self, ref: str) -> None:
        """The turn is complete and the server did not exit on EOF within ``finish_timeout``: end the group the way the app does."""
        live = self._live[ref]
        self._kill_group(live.process.pid, signal.SIGTERM)
        how = "sigterm"
        try:
            code = live.process.wait(timeout=self.config.stop_grace)
        except subprocess.TimeoutExpired:
            self._kill_group(live.process.pid, signal.SIGKILL)
            how = "sigkill"
            code = live.process.wait(timeout=5)
        self._state[ref]["exit_how"] = how
        try:
            for message in self._read(live, 0.5):
                self._receive(ref, message)
        except CliProtocolError:
            pass
        self._exited(ref, code)

    def _exited(self, ref: str, code: int) -> None:
        st, live = self._state[ref], self._live[ref]
        if st["done"]:
            return
        try:
            live.decoder.finish()
        except CliProtocolError as exc:
            self._fail(ref, str(exc), code=code)
            return
        stderr_tail = bytes(live.stderr[-2000:]).decode("utf-8", "replace")
        status = st["turn_status"]
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": code, "signals": list(st["signals"]), "interrupt": st["interrupt"], "turn_status": status,
                                            "thread_id": st["thread_id"], "turn_id": st["turn_id"]})
        elif status is None:
            self._emit(ref, EVENT_FAILED, {"error": f"Codex app-server exited {code} without a terminal turn", "stderr": stderr_tail})
        elif status == "failed":
            self._emit(ref, EVENT_FAILED, {"error": f"Codex turn failed: {self._error_text(st['turn_error']) or 'no error detail'}", "stderr": stderr_tail})
        elif status == "interrupted":
            self._emit(ref, EVENT_FAILED, {"error": "Codex turn was interrupted without a cancel request", "stderr": stderr_tail})
        else:
            text = live.snapshot if isinstance(live.snapshot, str) else "".join(live.deltas)
            if not text.strip():
                self._emit(ref, EVENT_FAILED, {"error": "Codex turn completed with an empty agent message", "stderr": stderr_tail})
            else:
                self._emit(ref, EVENT_RESULT, {"summary": text, "refs": [], "thread_id": st["thread_id"], "turn_id": st["turn_id"], "exit_code": code,
                                               "exit_how": st["exit_how"] or "eof", "model": self.config.model, "account": st["account"],
                                               "snapshot": live.snapshot is not None, "deltas": len(live.deltas)})
        self._finish_live(ref, code)

    def _fail(self, ref: str, error: str, *, code: Optional[int] = None) -> None:
        st = self._state[ref]
        if st["done"]:
            return
        live = self._live.get(ref)
        if live is not None and code is None:
            code = live.process.poll()  # a server that already left after a refusal keeps its real exit status
            if code is None:
                self._kill_group(live.process.pid, signal.SIGKILL)
                try:
                    code = live.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    code = None
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": code, "signals": list(st["signals"]), "interrupt": st["interrupt"], "turn_status": st["turn_status"], "note": error})
        else:
            self._emit(ref, EVENT_FAILED, {"error": error, "exit_code": code})
        self._finish_live(ref, code)

    def _finish_live(self, ref: str, code: Optional[int]) -> None:
        st = self._state[ref]
        st["done"], st["phase"], st["exit_code"] = True, "exited", code
        live = self._live.pop(ref, None)
        if live is not None:
            self._kill_group(live.process.pid, signal.SIGKILL)
            live.close()
        self._save(ref)

    # ------------------------------------------------------------------ process plumbing

    def _read(self, live: _Live, wait: float) -> List[Dict[str, Any]]:
        deadline = time.monotonic() + wait
        events: List[Dict[str, Any]] = []
        while live.selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready = live.selector.select(remaining)
            if not ready:
                break
            for key, _ in ready:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    live.selector.unregister(key.fileobj)
                    continue
                live.read_bytes += len(chunk)
                if live.read_bytes > OUTPUT_LIMIT:
                    raise CliProtocolError("Codex app-server output exceeded the run limit")
                if key.data == "stderr":
                    live.stderr.extend(chunk[: max(0, 65536 - len(live.stderr))])
                else:
                    events.extend(live.decoder.feed(chunk))
            if events:
                break
        return events

    def _write(self, ref: str, message: Dict[str, Any]) -> None:
        if self._state[ref]["cancel_requested"] and message.get("method") != "turn/interrupt":
            raise CliProtocolError("worker cancellation prevents further protocol writes")
        live = self._live[ref]
        raw = (json.dumps(message) + "\n").encode("utf-8")
        if len(raw) > INPUT_LIMIT:
            raise CliProtocolError(f"input to Codex exceeds {INPUT_LIMIT} bytes; shorten the context bounds")
        self._transcribe(ref, {"_sent": message})
        fd = live.process.stdin.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + 2
        with selectors.DefaultSelector() as writable:
            writable.register(fd, selectors.EVENT_WRITE)
            while raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not writable.select(remaining):
                    raise CliProtocolError("Codex app-server did not consume input")
                try:
                    count = os.write(fd, raw)
                    raw = raw[count:]
                except BlockingIOError:
                    continue

    @staticmethod
    def _close_stdin(live: _Live) -> None:
        if not live.stdin_closed:
            live.stdin_closed = True
            try:
                live.process.stdin.close()
            except OSError:
                pass

    def _signal(self, ref: str, pid: int) -> None:
        st = self._state[ref]
        order = [signal.SIGTERM, signal.SIGKILL]  # the protocol interrupt comes first; then the app's own SIGTERM, then SIGKILL on retry
        sig = order[min(len(st["signals"]), len(order) - 1)]
        st["signals"].append(sig.name)
        self._kill_group(pid, sig)

    @staticmethod
    def _kill_group(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False
            return True
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
            return os.getpgid(pid) == pid
        except (ProcessLookupError, PermissionError):
            return False

    def _observe_detached(self, ref: str) -> None:
        st = self._state[ref]
        if st["done"]:
            return
        pid = st.get("pid")
        if pid and self._alive(pid):
            return
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": None, "signals": list(st["signals"]), "interrupt": st.get("interrupt"), "turn_status": st.get("turn_status"),
                                            "note": "process gone after signal; exit status not observed"})
        else:
            self._emit(ref, EVENT_FAILED, {"error": "Codex app-server exited while no runtime observed it; its output was lost with the previous runtime's pipes"})
        st["done"], st["phase"] = True, "exited"
        self._save(ref)

    # ------------------------------------------------------------------ durable state

    def _emit(self, ref: str, kind: str, payload: Dict[str, Any]) -> None:
        st = self._state[ref]
        st["event_seq"] += 1
        st["pending"].append({"run_id": st["run_id"], "attempt": st["attempt"], "event_seq": st["event_seq"], "kind": kind, "payload": payload})
        self._save(ref)

    def _flush(self, ref: str) -> List[WorkerEvent]:
        st = self._state[ref]
        events = [WorkerEvent(**e) for e in st["pending"]]
        st["pending"] = []
        self._save(ref)
        return events

    def _transcribe(self, ref: str, message: Dict[str, Any]) -> None:
        with open(self._state[ref]["transcript_path"], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(message, ensure_ascii=False) + "\n")

    def _state_path(self, ref: str) -> Path:
        return self.runs_dir / (ref.replace(":", ".") + ".state.json")

    def _save(self, ref: str) -> None:
        path = self._state_path(ref)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state[ref], sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    def _load(self, ref: str) -> Optional[Dict[str, Any]]:
        if ref in self._state:
            return self._state[ref]
        path = self._state_path(ref)
        if not path.is_file():
            return None
        self._state[ref] = json.loads(path.read_text(encoding="utf-8"))
        return self._state[ref]

    def _need(self, ref: str) -> Dict[str, Any]:
        st = self._load(ref)
        if st is None:
            raise StateError(f"no Codex worker state for {ref}")
        return st

    def close(self) -> None:
        """Kill every process this instance still owns. Runs stay ``cancelling``/``running`` in the ledger until observed."""
        for ref in list(self._live):
            self._fail(ref, "worker closed while the app-server was running")
