"""Text-only Claude Code CLI worker port. Disabled by default; one subprocess per run; no tools of any kind.

What it does: for one run it checks the CLI's own ``auth status`` in a Dervo-owned config dir, launches the
published CLI in print mode with every tool, MCP server, settings source, hook, skill and slash command turned
off by explicit flags, completes the Agent SDK ``initialize`` handshake, sends the run's fresh session context
plus goal as the single user message, verifies the model on the ``system`` init message, on every ``assistant``
message and in the ``result`` usage report, and reports the CLI's result text as the run result once the process
has exited with status 0. Every deviation fails the run closed. It never calls :class:`ScopedActions`.

What it does not do: it never reads or copies credential files, never falls back to another account or config
dir, never resumes a previous session, never writes inside a project checkout (its working directory is an
empty directory under the Dervo work dir), and never steers a turn already sent.

Evidence for each launch option, so nothing here rests on a guess:

- Published CLI reference (code.claude.com/docs/en/cli-reference, read 2026-09-12): ``-p``/``--print``,
  ``--output-format stream-json``, ``--input-format stream-json``, ``--verbose`` (print mode), ``--model``,
  ``--session-id`` (UUID), ``--tools ""`` ("pass an empty string to remove all tools"), ``--disallowedTools "*"``
  ("removes every tool" from context), ``--restricted`` (no command/code tools, refuses bypassPermissions),
  ``--no-chrome``, ``--disable-slash-commands`` ("disable all skills and commands"), ``--permission-prompts none``
  (nobody answers prompts), ``claude auth status`` (JSON; exit 0 logged in, 1 not).
- ``reference/dervo_claude_policy.py.txt`` and ``reference/dervo_claude_coding.py.txt`` (Dervo's earlier adapter,
  grounded on the Claude Agent SDK): ``--setting-sources ""`` (no CLAUDE.md, settings or hooks), ``--strict-mcp-config``
  (no MCP servers beyond ``--mcp-config``, which is not passed), the ``control_request``/``initialize`` handshake before
  the first user message, the user message envelope, ``CLAUDE_CONFIG_DIR`` keying the credential, ``--safe-mode`` for
  the status call, and result/model-usage semantics.
- ``reference/dervo_transport.py.txt``: ``HOME`` must be the OS account home so macOS Keychain lookup works; the
  environment is an allowlist; the child is its own session/process group; SIGINT asks it to stop.

Isolation barrier, stated plainly: the flags above are the documented mechanism for disabling hooks and tools, and
the worker refuses any ``control_request`` (tool permission, MCP or hook callback), any ``tool_use`` block, and any
``system`` init that lists tools or MCP servers. It cannot observe a hook that runs silently inside the CLI process.
Proving that requires the parent's gated live acceptance run with an instrumented settings dir; until then this
worker is for context verification only and stays disabled unless ``ClaudeCliConfig.enabled`` is set explicitly.

Action mode (``ClaudeCliConfig.actions=True``), the narrow provider action bridge. The text-only contract above is
unchanged and remains the default. In action mode the launch drops ``--safe-mode`` (the CLI reference says it
disables MCP servers, so it cannot carry the bridge) and ``--disallowedTools "*"`` (a deny rule would also deny the
bridge tool), keeps every other restriction, and adds exactly one SDK-type MCP server named ``dervo`` with exactly one
allowed tool, ``mcp__dervo__request_help``. The CLI talks to an SDK-type server over the same stdin/stdout control
channel it already uses: ``control_request`` subtype ``mcp_message`` carrying ``server_name`` and a JSON-RPC message
(``initialize``, ``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call``), answered by ``control_response``
success with ``{"mcp_response": <json-rpc reply>}``. Evidence: ``reference/dervo_claude_policy.py.txt`` (grounded on the
Claude Agent SDK's subprocess_cli.py, query.py and client.py) and ``reference/dervo_claude_coding.py.txt``, Dervo's
earlier adapter that answered ``dervo_read_file``/``dervo_write_file``/``dervo_run_tests`` this way. That snapshot
itself notes the wire shapes still needed one live run; ``scripts/claude_action_handshake_no_inference.py`` exists to
observe them on the installed CLI without a prompt, and nothing in this module claims more than that evidence.

What the bridge does: one ``tools/call`` of ``request_help`` becomes one :meth:`ScopedActions.request_help` call, and
the broker's answer (a delegation, or the exact refusal) goes back to the model as the tool result. That is the whole
surface: no reads, no writes, no file or shell access, no other tool, no second server. The bridge never parses the
model's prose; only the structured call's arguments reach the broker, and the broker validates them again. Any other
control request, any other MCP server, any other tool name or ``tool_use`` block, an init that does not list exactly
the bridge tool and server, a permission denial, or more than :data:`MAX_TOOL_CALLS` calls fails the run closed.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .errors import AccessDenied, NotFound, PolicyViolation, StateError, ValidationError
from .models import EVENT_FAILED, EVENT_PROGRESS, EVENT_RESULT, EVENT_STOPPED, WorkAssignment, WorkerEvent, WorkerObservation

CONFIG_MARKER = "DERVO_CLAUDE_CONFIG_DIR.json"
ENV_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LINE_LIMIT = 1024 * 1024
OUTPUT_LIMIT = 8 * 1024 * 1024
INPUT_LIMIT = 131072
# Flags that switch off everything a text-only run must not have. Each is evidenced in the module docstring.
# The parent verified this exact set on an installed CLI (2026-09): `--help` lists --setting-sources and
# --strict-mcp-config, and a no-inference initialize handshake with --safe-mode and the empty MCP config exited 0.
EMPTY_MCP_CONFIG = '{"mcpServers":{}}'
TEXT_ONLY_FLAGS: Tuple[str, ...] = (
    "--safe-mode",                    # every customization off: CLAUDE.md, skills, plugins, hooks, MCP, commands (CLI reference)
    "--setting-sources", "",          # and no settings sources at all, belt and braces (reference policy, Agent SDK; in --help)
    "--disable-slash-commands",       # no skills or custom commands (CLI reference)
    "--mcp-config", EMPTY_MCP_CONFIG, # an explicit, empty MCP server set (CLI reference)
    "--strict-mcp-config",            # and nothing beyond it (reference policy, Agent SDK; in --help)
    "--tools", "",                    # remove all built-in tools (CLI reference)
    "--disallowedTools", "*",         # and deny every tool name that might remain (CLI reference)
    "--restricted",                   # no command/code tools, bypassPermissions refused (CLI reference)
    "--no-chrome",                    # no browser integration (CLI reference)
    "--permission-prompts", "none",   # nobody answers prompts; a prompt is a failure (CLI reference)
)

# Action mode: the same restrictions minus --safe-mode (it disables MCP servers, CLI reference) and minus
# --disallowedTools "*" (deny rules win over allow rules, so it would deny the bridge tool), plus exactly one
# SDK-type MCP server and exactly one allowed tool (reference policy, Agent SDK subprocess_cli.py).
TOOL_SERVER = "dervo"
TOOL_NAME = "request_help"
TOOL_FULL_NAME = f"mcp__{TOOL_SERVER}__{TOOL_NAME}"
ACTION_MCP_CONFIG = json.dumps({"mcpServers": {TOOL_SERVER: {"type": "sdk", "name": TOOL_SERVER}}}, separators=(",", ":"))
ACTION_FLAGS: Tuple[str, ...] = (
    "--setting-sources", "",           # no CLAUDE.md, settings or hooks (reference policy, Agent SDK; in --help)
    "--disable-slash-commands",        # no skills or custom commands (CLI reference)
    "--mcp-config", ACTION_MCP_CONFIG, # the one SDK-type server this worker answers itself (reference policy)
    "--strict-mcp-config",             # and nothing beyond it (reference policy, Agent SDK; in --help)
    "--tools", "",                     # remove all built-in tools (CLI reference); MCP tools are not built-in
    "--allowedTools", TOOL_FULL_NAME,  # the only tool that may run without a prompt (reference policy)
    "--restricted",                    # no command/code tools, bypassPermissions refused (CLI reference)
    "--no-chrome",                     # no browser integration (CLI reference)
    "--permission-prompts", "none",    # nobody answers prompts; a prompt is a failure (CLI reference)
)
MAX_TOOL_CALLS = 8  # hard transport cap per run; the broker's DelegationPolicy budgets are far below it
TOOL_SPEC = {
    "name": TOOL_NAME,
    "description": (
        "Ask one named helper for help through the Dervo harness. Use it only for a helper listed under 'Helpers this run may ask' "
        "in the context; 'target' is that exact @handle. 'purpose' (up to 500 characters) becomes the helper's task. 'source_refs' "
        "lists 1 to 10 refs of messages or decisions from this room exactly as they appear in the context (message:<id> or "
        "decision:<id>); they are the only records the helper receives. 'instructions' is optional (up to 2000 characters). The helper "
        "runs separately: its answer returns to this room as a handoff_result record for a later session, not in this turn. A refused "
        "request returns an error you can read; an identical re-ask returns the same delegation."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "exact @handle of a listed helper"},
            "purpose": {"type": "string", "description": "what you need from the helper; becomes its task"},
            "source_refs": {"type": "array", "items": {"type": "string"}, "description": "message:<id> or decision:<id> refs from this room"},
            "instructions": {"type": "string", "description": "optional guidance for the helper"},
        },
        "required": ["target", "purpose", "source_refs"],
        "additionalProperties": False,
    },
}
_TOOL_ARG_LIMITS = {"target": 80, "purpose": 4000, "instructions": 8000}
_BROKER_REFUSALS = (ValidationError, AccessDenied, NotFound, PolicyViolation, StateError)


class CliProtocolError(Exception):
    """The CLI did something the text-only contract does not allow. The run fails closed."""


@dataclass(frozen=True)
class ClaudeCliConfig:
    """Explicit identity and execution options. ``enabled`` defaults to False: nothing launches without it."""

    executable: str        # absolute path to the published CLI (tests point it at a fake)
    model: str             # full model ID; must appear on system init, every assistant message and in modelUsage
    project_id: str        # the only harness project this worker serves
    config_dir: str        # Dervo-owned CLAUDE_CONFIG_DIR carrying CONFIG_MARKER; never the CLI default
    identity: str          # account email that `auth status` must report; anything else is refused
    work_dir: str          # Dervo-owned dir for per-run state, the persisted prompt and the empty cwd
    enabled: bool = False  # explicit opt-in to launching the CLI at all
    actions: bool = False  # explicit opt-in to the one-tool action bridge (request_help); False is the text-only contract
    # `auth status` must report a claude.ai subscription login served first-party; anything else (console/API key,
    # Bedrock, Vertex, unknown plan) is refused so a run can never land on unexpected API billing.
    auth_method: str = "claude.ai"
    api_provider: str = "firstParty"
    subscription_types: Tuple[str, ...] = ("pro", "max")
    init_timeout: float = 120.0
    turn_timeout: float = 1800.0
    finish_timeout: float = 30.0
    drain_wait: float = 0.25   # how long one drain waits for output
    stop_grace: float = 2.0    # how long cancel waits for the process to exit after a signal
    auth_timeout: float = 8.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def account_home() -> str:
    """The OS account home from the passwd database, not from the inherited environment."""
    import pwd

    home = pwd.getpwuid(os.getuid()).pw_dir
    if not home or not Path(home).is_absolute():
        raise ValidationError("the OS account home is not an absolute path")
    return home


def effective_model_problem(result: Dict[str, Any], model: str, assistant_models: frozenset = frozenset()) -> Tuple[Optional[str], List[str]]:
    """Same rule as the reference adapter: the selected model must appear in ``modelUsage`` whenever a genuine assistant
    message was seen; any other model there is accepted as auxiliary only if no assistant message used it. A run the
    selected model never spoke in has nothing to verify here, and ``_exited`` refuses to call it a result."""
    usage = result.get("modelUsage", result.get("model_usage"))
    if usage is None:
        return None, []
    if not isinstance(usage, dict) or any(not isinstance(k, str) for k in usage):
        return "CLI reported malformed model usage", []
    if usage and assistant_models and model not in usage:
        return f"the selected model {model} does not appear in the CLI usage report", []
    others = sorted(set(usage) - {model})
    drifted = [name for name in others if name in assistant_models]
    if drifted:
        return f"assistant model {', '.join(drifted)} differs from the selected model {model}", []
    return None, others


class _JsonLines:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        self.buffer.extend(chunk)
        out: List[Dict[str, Any]] = []
        while b"\n" in self.buffer:
            line, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            if not line.strip():
                continue
            if len(line) > LINE_LIMIT:
                raise CliProtocolError("CLI output line exceeded the size limit")
            try:
                value = json.loads(line)
            except (ValueError, UnicodeError):
                raise CliProtocolError(f"CLI sent malformed JSONL: {line[:120]!r}") from None
            if not isinstance(value, dict):
                raise CliProtocolError("CLI sent a non-object event")
            out.append(value)
        if len(self.buffer) > LINE_LIMIT:
            raise CliProtocolError("CLI output line exceeded the size limit")
        return out

    def finish(self) -> None:
        if self.buffer.strip():
            raise CliProtocolError("CLI exited in the middle of an event")


class _Live:
    """In-memory side of one launched CLI process (pipes, decoder, phase). The durable side is the state file."""

    def __init__(self, process: subprocess.Popen) -> None:
        self.process = process
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        self.decoder = _JsonLines()
        self.stderr = bytearray()
        self.read_bytes = 0
        self.stdout_eof = False
        self.init_request_id = ""
        self.result: Optional[Dict[str, Any]] = None
        self.assistant_models: set = set()
        self.api_error: Optional[str] = None  # the CLI's own report of a failed API call ("API Error: Overloaded"), in its words
        self.started = False  # system init observed
        self.stdin_closed = False
        self.deadline = time.monotonic()
        self.signals: List[str] = []

    def close(self) -> None:
        try:
            self.selector.close()
        finally:
            for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass


class ClaudeCliWorker:
    """:class:`WorkerPort` over the published Claude Code CLI, text-only. See the module docstring for the contract."""

    name = "claude-cli-text-only"
    is_fake = False

    def __init__(self, config: ClaudeCliConfig) -> None:
        self.config = config
        self.model_name = config.model
        self.name = "claude-cli-actions" if config.actions else "claude-cli-text-only"
        self._validate(config)
        self.work_dir = Path(config.work_dir)
        self.runs_dir = self.work_dir / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._live: Dict[str, _Live] = {}
        self._state: Dict[str, Dict[str, Any]] = {}
        self._actions: Dict[str, Any] = {}  # action mode only: the ScopedActions of each live run

    # ------------------------------------------------------------------ configuration

    @staticmethod
    def default_config_dir() -> Path:
        return Path(account_home()) / ".claude"

    @classmethod
    def prepare_config_dir(cls, path: str) -> Path:
        """Claim an empty or missing directory as Dervo-owned by writing the marker. Nothing is copied into it: a
        human signs in there with the CLI's own ``auth login`` under ``CLAUDE_CONFIG_DIR``. Refuses the CLI default
        directory and any non-empty directory that is not already marked."""
        target = Path(path)
        if not target.is_absolute():
            raise ValidationError("config_dir must be an absolute path")
        if target.resolve() == cls.default_config_dir().resolve():
            raise ValidationError(f"{target} is the CLI's default config dir (a personal account); Dervo needs its own")
        marker = target / CONFIG_MARKER
        if target.exists():
            if not target.is_dir():
                raise ValidationError(f"{target} is not a directory")
            if any(target.iterdir()) and not marker.exists():
                raise ValidationError(f"{target} is not empty and carries no {CONFIG_MARKER}; refusing to claim it")
        target.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            marker.write_text(json.dumps({"owner": "dervo-harness", "purpose": "CLAUDE_CONFIG_DIR for the text-only worker"}) + "\n", encoding="utf-8")
        return target

    @classmethod
    def _validate(cls, c: ClaudeCliConfig) -> None:
        for field_name in ("executable", "model", "project_id", "config_dir", "identity", "work_dir"):
            value = getattr(c, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"ClaudeCliConfig.{field_name} must be a non-empty string")
        exe = Path(c.executable)
        if not exe.is_absolute() or not exe.is_file() or not os.access(exe, os.X_OK):
            raise ValidationError(f"executable {c.executable} must be an absolute path to an executable file")
        if any(ch in c.identity for ch in "\r\n\x00") or "@" not in c.identity:
            raise ValidationError("identity must be the account email the CLI is expected to report")
        cfg = Path(c.config_dir)
        if not cfg.is_absolute() or not cfg.is_dir():
            raise ValidationError(f"config_dir {c.config_dir} must be an existing absolute directory")
        if cfg.resolve() == cls.default_config_dir().resolve():
            raise ValidationError(f"config_dir {c.config_dir} is the CLI's default (personal) config dir; refusing")
        if not (cfg / CONFIG_MARKER).is_file():
            raise ValidationError(f"config_dir {c.config_dir} carries no {CONFIG_MARKER}; call ClaudeCliWorker.prepare_config_dir first")
        if not Path(c.work_dir).is_absolute():
            raise ValidationError("work_dir must be an absolute path")
        if cfg.resolve() == Path(c.work_dir).resolve():
            raise ValidationError("work_dir and config_dir must differ")

    def environment(self) -> Dict[str, str]:
        """Allowlisted child environment. HOME is the real OS account home (Keychain); CLAUDE_CONFIG_DIR is Dervo's."""
        return {"PATH": ENV_PATH, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
                "HOME": account_home(), "CLAUDE_CONFIG_DIR": str(self.config.config_dir)}

    def argv(self, session_id: str) -> List[str]:
        """Print-mode launch with everything but plain text switched off. No --mcp-config, no --allowedTools, no --resume."""
        if str(uuid.UUID(session_id)) != session_id:
            raise ValidationError("session_id must be a canonical UUID")
        flags = ACTION_FLAGS if self.config.actions else TEXT_ONLY_FLAGS
        return [self.config.executable, "-p", "--output-format", "stream-json", "--input-format", "stream-json", "--verbose",
                *flags, "--model", self.config.model, f"--session-id={session_id}"]

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "is_fake": self.is_fake, "model": self.config.model, "enabled": self.config.enabled,
                "actions": self.config.actions, "project_id": self.config.project_id, "config_dir": self.config.config_dir,
                "identity": self.config.identity, "text_only_flags": list(TEXT_ONLY_FLAGS), "action_flags": list(ACTION_FLAGS),
                "tools": [TOOL_FULL_NAME] if self.config.actions else []}

    # ------------------------------------------------------------------ auth observation

    def observe_auth(self) -> Dict[str, Any]:
        """Run the CLI's own ``auth status`` in the owned config dir and require the exact expected identity.
        Returns the observed metadata (no secrets are in it). Never a model call, never a login."""
        env = self.environment()
        cwd = self.work_dir / "auth-cwd"
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run([self.config.executable, "--safe-mode", "auth", "status"], cwd=str(cwd), env=env,
                                  capture_output=True, timeout=self.config.auth_timeout)
        except subprocess.TimeoutExpired:
            raise StateError("auth status did not answer in time; not launching") from None
        if proc.returncode not in (0, 1) or len(proc.stdout) > 65536:
            raise StateError(f"auth status exited {proc.returncode}; not launching")
        try:
            meta = json.loads(proc.stdout)
        except (ValueError, UnicodeError):
            raise StateError("auth status output was not JSON; not launching") from None
        if not isinstance(meta, dict):
            raise StateError("auth status output was not an object; not launching")
        observed = {k: meta.get(k) for k in ("loggedIn", "authMethod", "apiProvider", "subscriptionType", "email")}
        if meta.get("loggedIn") is not True or proc.returncode != 0:
            raise StateError(f"CLI is not logged in under {self.config.config_dir}; sign in there explicitly (no fallback)")
        if meta.get("email") != self.config.identity:
            raise StateError(f"CLI account {meta.get('email')!r} is not the configured identity {self.config.identity!r}; refusing")
        if meta.get("authMethod") != self.config.auth_method or meta.get("apiProvider") != self.config.api_provider:
            raise StateError(f"CLI login is authMethod {meta.get('authMethod')!r} / apiProvider {meta.get('apiProvider')!r}, not a "
                             f"{self.config.auth_method} {self.config.api_provider} subscription login; refusing to avoid API billing")
        if meta.get("subscriptionType") not in self.config.subscription_types:
            raise StateError(f"CLI subscription {meta.get('subscriptionType')!r} is not one of {self.config.subscription_types}; refusing")
        return observed

    # ------------------------------------------------------------------ WorkerPort

    def start(self, assignment: WorkAssignment, actions: Any) -> str:
        if not self.config.enabled:
            raise StateError("Claude CLI worker is disabled by default; set ClaudeCliConfig.enabled=True to launch anything")
        if assignment.project_id != self.config.project_id:
            raise AccessDenied(f"this worker serves project {self.config.project_id}, not {assignment.project_id}")
        if self.config.actions and not callable(getattr(actions, "request_help", None)):
            raise StateError("the action-mode Claude worker needs the run's ScopedActions; none were given")
        auth = self.observe_auth()
        ref = f"claude:{assignment.run_id}:{assignment.attempt}"
        session_id = str(uuid.uuid4())
        base = str(self.runs_dir / f"{assignment.run_id}.{assignment.attempt}")
        cwd = Path(base + ".cwd")
        cwd.mkdir(parents=True, exist_ok=True)
        if any(cwd.iterdir()):
            raise StateError(f"working directory {cwd} is not empty; refusing to launch over it")
        prompt = f"{assignment.context_text}\n\nTask: {assignment.goal}\n"
        prompt_path = Path(base + ".prompt.txt")
        prompt_path.write_text(prompt, encoding="utf-8")  # the exact text the model is shown, kept for audit
        st = {
            "ref": ref, "run_id": assignment.run_id, "attempt": assignment.attempt, "session_id": session_id,
            "model": self.config.model, "pid": None, "phase": "launching", "event_seq": 0, "pending": [], "done": False,
            "cancel_requested": False, "signals": [], "prompt_path": str(prompt_path), "cwd": str(cwd),
            "transcript_path": base + ".transcript.jsonl", "auth": auth, "started_at": time.time(),
            "actions": bool(self.config.actions), "tool_calls": 0, "seen_calls": [],
        }
        self._state[ref] = st
        if self.config.actions:
            self._actions[ref] = actions
        env = self.environment()
        proc = subprocess.Popen(self.argv(session_id), cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, bufsize=0)
        live = _Live(proc)
        live.deadline = time.monotonic() + self.config.init_timeout
        self._live[ref] = live
        st["pid"], st["phase"] = proc.pid, "initializing"
        self._emit(ref, EVENT_PROGRESS, {"phase": "auth_observed", **auth})
        live.init_request_id = f"req_1_{uuid.uuid4().hex[:8]}"
        try:
            self._write(ref, {"type": "control_request", "request_id": live.init_request_id, "request": {"subtype": "initialize", "hooks": None}})
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
                for message in self._read(live, 1.0):  # the rest of the pipes after exit
                    self._receive(worker_ref, message)
                self._exited(worker_ref, code)
            elif time.monotonic() > live.deadline:
                raise CliProtocolError(f"CLI deadline exceeded in phase {st['phase']}")
        except CliProtocolError as exc:
            self._fail(worker_ref, str(exc))
        return self._flush(worker_ref)

    def steer(self, worker_ref: str, note: str) -> None:
        self._need(worker_ref)
        raise StateError("the text-only Claude CLI worker sends one message per run and cannot steer a turn already sent; "
                         "cancel and resubmit with the note in the goal")

    def cancel(self, worker_ref: str) -> Optional[WorkerObservation]:
        st = self._need(worker_ref)
        if st["done"]:
            return WorkerObservation("finished", events=self._flush(worker_ref), note="already finished")
        st["cancel_requested"] = True
        live = self._live.get(worker_ref)
        pid = st.get("pid")
        if live is None:  # a process this instance did not spawn: signal by pid if it is still ours, then look again
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
            pass  # output after a stop signal is not held against the worker; the exit is what matters
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

    def _receive(self, ref: str, message: Dict[str, Any]) -> None:
        st, live = self._state[ref], self._live[ref]
        self._transcribe(ref, message)
        sid = message.get("session_id")
        if sid not in (None, "") and sid != st["session_id"]:
            raise CliProtocolError("CLI changed the session ID")
        kind = message.get("type")
        if kind == "control_response":
            response = message.get("response")
            if not isinstance(response, dict) or st["phase"] != "initializing" or response.get("request_id") != live.init_request_id:
                raise CliProtocolError("unexpected control response")
            if response.get("subtype") != "success":
                raise CliProtocolError(f"CLI rejected initialization: {response.get('error', 'unknown error')}")
            st["phase"] = "running"
            live.deadline = time.monotonic() + self.config.turn_timeout
            self._emit(ref, EVENT_PROGRESS, {"phase": "initialized"})
            prompt = Path(st["prompt_path"]).read_text(encoding="utf-8")
            self._write(ref, {"type": "user", "message": {"role": "user", "content": prompt}, "parent_tool_use_id": None, "session_id": st["session_id"]})
            return
        if kind == "control_request":
            request = message.get("request") if isinstance(message.get("request"), dict) else {}
            subtype = request.get("subtype")
            if st.get("actions") and subtype == "mcp_message":
                self._mcp(ref, message.get("request_id"), request)
                return
            try:
                self._write(ref, {"type": "control_response", "response": {"subtype": "error", "request_id": message.get("request_id"),
                                                                             "error": "Dervo text-only worker grants nothing"}})
            except Exception:
                pass
            raise CliProtocolError(f"CLI sent a control request ({subtype!r}); a text-only run permits no tools, MCP or hooks")
        if kind == "control_cancel_request":
            return
        if st["phase"] == "initializing":
            raise CliProtocolError(f"CLI emitted {kind!r} before initialization completed")
        if kind == "system" and message.get("subtype") == "init":
            if message.get("model") != self.config.model:
                raise CliProtocolError(f"CLI effective model {message.get('model')!r} differs from the selected {self.config.model!r}")
            if sid != st["session_id"]:
                raise CliProtocolError("CLI did not use the chosen session ID")
            tools = message.get("tools")
            servers = message.get("mcp_servers")
            if st.get("actions"):
                names = [s.get("name") if isinstance(s, dict) else s for s in (servers or [])] if isinstance(servers, list) else None
                bad_status = isinstance(servers, list) and any(isinstance(s, dict) and s.get("status") not in (None, "connected") for s in servers)
                if tools != [TOOL_FULL_NAME] or names != [TOOL_SERVER] or bad_status:
                    raise CliProtocolError(f"CLI init lists tools {tools!r} / MCP servers {servers!r}; the action run expects exactly "
                                           f"[{TOOL_FULL_NAME!r}] and the connected {TOOL_SERVER!r} server")
            elif tools not in (None, []) or servers not in (None, []):
                raise CliProtocolError(f"CLI init lists tools {tools!r} / MCP servers {servers!r}; text-only run refused")
            live.started = True
            self._emit(ref, EVENT_PROGRESS, {"phase": "init_observed", "model": message.get("model"), "tools": tools or [], "mcp_servers": servers or []})
            return
        if kind == "assistant":
            if live.result is not None:
                raise CliProtocolError("CLI sent an assistant message after its terminal result")
            body = message.get("message")
            if not isinstance(body, dict):
                raise CliProtocolError("malformed assistant message")
            if message.get("parent_tool_use_id") is not None:
                raise CliProtocolError("subagent output is not permitted in a text-only run")
            model = body.get("model")
            if model == "<synthetic>" or message.get("isApiErrorMessage") is True:
                # The CLI writes its own assistant message when an API call fails. It is a report, not model drift and
                # not the model's words: it never joins assistant_models, and it ends the run failed with that text.
                parts = [b["text"] for b in body.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str) and b["text"].strip()]
                text = "\n".join(parts).encode("utf-8")[:4096].decode("utf-8", "ignore").strip()
                live.api_error = text or "the CLI reported an API error without details"
                return
            if model != self.config.model:
                raise CliProtocolError(f"assistant message used {model!r} instead of the selected model {self.config.model!r}")
            live.assistant_models.add(model)
            live.api_error = None  # the turn carried on after the report
            for block in body.get("content") or []:
                if not isinstance(block, dict) or block.get("type") in ("text", "thinking", "redacted_thinking"):
                    continue
                if st.get("actions") and block.get("type") == "tool_use" and block.get("name") == TOOL_FULL_NAME:
                    continue  # the one bridge tool; the call itself arrives on the control channel and is validated there
                if st.get("actions"):
                    raise CliProtocolError(f"assistant emitted a {block.get('type')!r} block for {block.get('name')!r}; only {TOOL_FULL_NAME} is permitted")
                raise CliProtocolError(f"assistant emitted a {block.get('type')!r} block; text-only run refused")
            return
        if kind == "result":
            if live.result is not None:
                raise CliProtocolError("duplicate terminal result")
            live.result = message
            st["phase"] = "finishing"
            live.deadline = time.monotonic() + self.config.finish_timeout
            self._close_stdin(live)  # the CLI stays alive for more input; EOF lets it exit and the exit status confirms
            return
        # stream_event, user echoes (including tool_result echoes), rate limit and other informational events carry no action.

    # ------------------------------------------------------------------ action bridge (SDK-type MCP server over the control channel)

    def _mcp(self, ref: str, request_id: Any, request: Dict[str, Any]) -> None:
        """Answer one ``mcp_message`` control request for the ``dervo`` server. Anything off the evidenced shape fails closed."""
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            raise CliProtocolError("CLI sent an MCP control request without a usable request_id")
        if request.get("server_name") != TOOL_SERVER:
            raise CliProtocolError(f"CLI addressed MCP server {request.get('server_name')!r}; only {TOOL_SERVER!r} exists in this run")
        rpc = request.get("message")
        if not isinstance(rpc, dict):
            raise CliProtocolError("CLI sent a malformed MCP message")
        method, mid, params = rpc.get("method"), rpc.get("id"), rpc.get("params") if isinstance(rpc.get("params"), dict) else {}
        if method == "initialize":
            self._mcp_reply(ref, request_id, mid, result={"protocolVersion": params.get("protocolVersion") or "2024-11-05",
                                                          "capabilities": {"tools": {}}, "serverInfo": {"name": TOOL_SERVER, "version": "0.5.0"}})
        elif mid is None:
            self._mcp_reply(ref, request_id, None, result={})  # a notification: no JSON-RPC reply, but the control request wants an ack
        elif method == "ping":
            self._mcp_reply(ref, request_id, mid, result={})
        elif method == "tools/list":
            self._mcp_reply(ref, request_id, mid, result={"tools": [TOOL_SPEC]})
        elif method == "tools/call":
            self._tool_call(ref, request_id, mid, params)
        else:
            self._mcp_reply(ref, request_id, mid, error={"code": -32601, "message": f"Dervo does not support {method!r}"})

    def _tool_call(self, ref: str, request_id: str, mid: Any, params: Dict[str, Any]) -> None:
        st = self._state[ref]
        if st["phase"] != "running":
            raise CliProtocolError(f"CLI called a tool in phase {st['phase']}; a call is only valid during the turn")
        name = params.get("name")
        if name != TOOL_NAME:
            raise CliProtocolError(f"CLI called unknown tool {name!r}; only {TOOL_NAME!r} exists")
        if not (isinstance(mid, (int, str)) and not isinstance(mid, bool)) or (isinstance(mid, str) and (not mid or len(mid) > 256)):
            raise CliProtocolError("CLI tool call has no usable JSON-RPC id")
        key = f"{request_id}:{mid}"
        if key in st["seen_calls"]:
            raise CliProtocolError(f"CLI replayed tool call {key}")
        st["seen_calls"].append(key)
        st["tool_calls"] += 1
        if st["tool_calls"] > MAX_TOOL_CALLS:
            raise CliProtocolError(f"CLI exceeded {MAX_TOOL_CALLS} tool calls in one run")
        actions = self._actions.get(ref)
        if actions is None:
            raise CliProtocolError("no ScopedActions are bound to this run; the action bridge cannot serve it")
        args = params.get("arguments")
        ok, payload = self._request_help(actions, args)
        self._emit(ref, EVENT_PROGRESS, {"phase": "action", "tool": TOOL_NAME, "ok": ok, "call": st["tool_calls"],
                                         **({"delegation_id": payload["delegation_id"], "target": payload["target"]} if ok else
                                            {"error_type": payload["error_type"], "error": payload["error"][:400]})})
        self._mcp_reply(ref, request_id, mid, result={"content": [{"type": "text", "text": json.dumps(payload)}], "isError": not ok})

    @staticmethod
    def _request_help(actions: Any, args: Any) -> Tuple[bool, Dict[str, Any]]:
        """Shape-check the structured arguments, then let the broker decide. Returns (ok, payload) for the model."""
        def refuse(kind: str, text: str) -> Tuple[bool, Dict[str, Any]]:
            return False, {"ok": False, "error_type": kind, "error": text}

        if not isinstance(args, dict):
            return refuse("ValidationError", "arguments must be an object with target, purpose, source_refs and optional instructions")
        extra = sorted(set(args) - {"target", "purpose", "source_refs", "instructions"})
        if extra:
            return refuse("ValidationError", f"unexpected argument(s) {', '.join(extra)}; only target, purpose, source_refs, instructions are accepted")
        for field_name in ("target", "purpose", "instructions"):
            value = args.get(field_name, "" if field_name == "instructions" else None)
            if not isinstance(value, str) or len(value) > _TOOL_ARG_LIMITS[field_name]:
                return refuse("ValidationError", f"{field_name} must be a string of at most {_TOOL_ARG_LIMITS[field_name]} characters")
        refs = args.get("source_refs")
        if not isinstance(refs, list) or not refs or len(refs) > 32 or any(not isinstance(r, str) or not r or len(r) > 128 for r in refs):
            return refuse("ValidationError", "source_refs must be a non-empty list of ref strings (message:<id> or decision:<id>) from this room")
        try:
            dl = actions.request_help(args["target"], args["purpose"], list(refs), instructions=args.get("instructions", ""))
        except _BROKER_REFUSALS as exc:
            return refuse(type(exc).__name__, str(exc))
        return True, {"ok": True, "delegation_id": dl.delegation_id, "target": dl.mention, "target_room": dl.target_room, "handoff_id": dl.handoff_id,
                      "dispatch_id": dl.dispatch_id, "status": dl.status, "depth": dl.depth,
                      "note": "the helper runs separately; its answer will appear in this room as a handoff_result record in a later session"}

    def _mcp_reply(self, ref: str, request_id: str, mid: Any, *, result: Any = None, error: Any = None) -> None:
        body: Dict[str, Any] = {"jsonrpc": "2.0"}
        if mid is not None:
            body["id"] = mid
        body["error" if error is not None else "result"] = error if error is not None else result
        self._write(ref, {"type": "control_response", "response": {"subtype": "success", "request_id": request_id, "response": {"mcp_response": body}}})

    def _exited(self, ref: str, code: int) -> None:
        st, live = self._state[ref], self._live[ref]
        if st["done"]:
            return
        try:
            live.decoder.finish()
        except CliProtocolError as exc:
            self._fail(ref, str(exc), code=code)
            return
        r = live.result
        stderr_tail = bytes(live.stderr[-2000:]).decode("utf-8", "replace")
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": code, "signals": list(st["signals"]), "had_result": r is not None})
        elif r is None and live.api_error:
            self._emit(ref, EVENT_FAILED, {"error": live.api_error, "stderr": stderr_tail})
        elif r is None:
            self._emit(ref, EVENT_FAILED, {"error": f"CLI exited {code} without a terminal result", "stderr": stderr_tail})
        else:
            problem, auxiliary = effective_model_problem(r, self.config.model, frozenset(live.assistant_models))
            text = r.get("result") if isinstance(r.get("result"), str) else ""
            if problem:
                self._emit(ref, EVENT_FAILED, {"error": problem})
            elif live.api_error:
                self._emit(ref, EVENT_FAILED, {"error": live.api_error, "stderr": stderr_tail})
            elif not live.assistant_models:
                self._emit(ref, EVENT_FAILED, {"error": f"CLI exited {code} without an assistant message from the selected model {self.config.model}",
                                               "stderr": stderr_tail})
            elif code == 0 and r.get("subtype") == "success" and r.get("is_error") is False and live.started and text.strip():
                if r.get("permission_denials"):
                    self._emit(ref, EVENT_FAILED, {"error": f"CLI recorded permission denials {r.get('permission_denials')!r}; a tool was attempted"})
                else:
                    self._emit(ref, EVENT_RESULT, {"summary": text, "refs": [], "session_id": st["session_id"], "exit_code": code,
                                                   "usage": r.get("usage", {}), "modelUsage": r.get("modelUsage", r.get("model_usage", {})),
                                                   "total_cost_usd": r.get("total_cost_usd"), "auxiliary_models": auxiliary,
                                                   "assistant_models": sorted(live.assistant_models)})
            else:
                why = text.strip() or f"result subtype {r.get('subtype')!r}, is_error {r.get('is_error')!r}"
                self._emit(ref, EVENT_FAILED, {"error": f"CLI exited {code}: {why}", "stderr": stderr_tail})
        self._finish_live(ref, code)

    def _fail(self, ref: str, error: str, *, code: Optional[int] = None) -> None:
        st = self._state[ref]
        if st["done"]:
            return
        live = self._live.get(ref)
        if live is not None and code is None:
            self._kill_group(live.process.pid, signal.SIGKILL)
            try:
                code = live.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": code, "signals": list(st["signals"]), "note": error})
        else:
            self._emit(ref, EVENT_FAILED, {"error": error, "exit_code": code})
        self._finish_live(ref, code)

    def _finish_live(self, ref: str, code: Optional[int]) -> None:
        st = self._state[ref]
        st["done"], st["phase"], st["exit_code"] = True, "exited", code
        self._actions.pop(ref, None)
        live = self._live.pop(ref, None)
        if live is not None:
            self._kill_group(live.process.pid, signal.SIGKILL)  # descendants, if any, go with the leader
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
                    if key.data == "stdout":
                        live.stdout_eof = True
                    continue
                live.read_bytes += len(chunk)
                if live.read_bytes > OUTPUT_LIMIT:
                    raise CliProtocolError("CLI output exceeded the run limit")
                if key.data == "stderr":
                    live.stderr.extend(chunk[: max(0, 65536 - len(live.stderr))])
                else:
                    events.extend(live.decoder.feed(chunk))
            if events:
                break
        return events

    def _write(self, ref: str, message: Dict[str, Any]) -> None:
        if self._state[ref]["cancel_requested"]:
            raise CliProtocolError("worker cancellation prevents further protocol writes")
        live = self._live[ref]
        raw = (json.dumps(message) + "\n").encode("utf-8")
        if len(raw) > INPUT_LIMIT:
            raise CliProtocolError(f"input to the CLI exceeds {INPUT_LIMIT} bytes; shorten the context bounds")
        self._transcribe(ref, {"_sent": message})
        fd = live.process.stdin.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + 2
        with selectors.DefaultSelector() as writable:
            writable.register(fd, selectors.EVENT_WRITE)
            while raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not writable.select(remaining):
                    raise CliProtocolError("CLI did not consume input")
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
        order = [signal.SIGINT, signal.SIGTERM, signal.SIGKILL]
        sig = order[min(len(st["signals"]), len(order) - 1)]  # first SIGINT, then SIGTERM, then SIGKILL on every retry
        st["signals"].append(sig.name)
        self._kill_group(pid, sig)

    @staticmethod
    def _kill_group(pid: int, sig: signal.Signals) -> None:
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass

    @staticmethod
    def _alive(pid: int) -> bool:
        """True if ``pid`` is still running. Our own child is reaped with a non-blocking wait first, so an exited
        zombie is not mistaken for a live process. A pid that is not our child counts as alive only if it exists and
        still leads its own process group (how we launched it); a reused leader pid is the documented residual risk."""
        try:
            reaped, _ = os.waitpid(pid, os.WNOHANG)
            if reaped == pid:
                return False
            return True  # our child, still running
        except ChildProcessError:
            pass  # not our child (spawned by a previous runtime process)
        try:
            os.kill(pid, 0)
            return os.getpgid(pid) == pid
        except (ProcessLookupError, PermissionError):
            return False

    def _observe_detached(self, ref: str) -> None:
        """A process this instance did not spawn: alive means unobservable, gone without a result means failed."""
        st = self._state[ref]
        if st["done"]:
            return
        pid = st.get("pid")
        if pid and self._alive(pid):
            return
        if st["cancel_requested"]:
            self._emit(ref, EVENT_STOPPED, {"exit_code": None, "signals": list(st["signals"]), "note": "process gone after signal; exit status not observed"})
        else:
            self._emit(ref, EVENT_FAILED, {"error": "CLI process exited while no runtime observed it; its output was lost with the previous runtime's pipes"})
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
            raise StateError(f"no Claude CLI worker state for {ref}")
        return st

    def close(self) -> None:
        """Kill every process this instance still owns. Runs stay ``cancelling``/``running`` in the ledger until observed."""
        for ref in list(self._live):
            self._fail(ref, "worker closed while the CLI was running")
