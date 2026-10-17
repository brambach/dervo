# Dervo harness integration (backend)

The app backend hosts the real Dervo harness library, `dervo_harness` from harness-core, for helper coordination
inside a conversation. The library is vendored unmodified into `backend/dervo_harness`, verified against its
provenance before import, and driven through its public API by `backend/harness_adapter.py`. The native frontend is
unchanged. No conversation, turn or coding task is replaced or migrated. Nothing here reads credentials or writes
project files. The only provider process it can start is the published Claude Code CLI, text only, in an owned
profile whose identity the CLI itself verifies first.

## Files

| File | Role |
| --- | --- |
| `backend/dervo_harness/` | The library, byte for byte, plus `PROVENANCE.json`, `NOTICE.md`, `THIRD_PARTY_NOTICES.md` and `LICENSE-Hermes.txt`. Written only by `scripts/vendor-harness.py`. |
| `scripts/vendor-harness.py` | Copies the library from a harness-core checkout (a build-time argument, not recorded) and writes the provenance with a sha256 per file. |
| `backend/harness_port.py` | Port contract `dervo-harness-port-v2`, `HelperSpec`, the helper authority rule, and the loader that verifies digests before importing anything. |
| `backend/harness_adapter.py` | The adapter: maps app projects and conversations onto harness projects and rooms, binds eligible helpers into a `WorkerPool` per project, submits through `Runtime.submit`, reads status and events from the runtime's records. |
| `backend/harness_claude.py` | Derives one identity-verified Claude helper per owned personal Claude profile and model in use; builds the library's `ClaudeCliWorker` per project. |
| `backend/harness_codex.py` | The same for Codex: one helper per owned personal Codex profile and model; builds the library's `CodexAppServerWorker` per project. Excludes every Codex profile while the packaged library predates 0.7.1. |
| `backend/fake_codex_app_server.py` | Test-only fake of the pinned app-server protocol. Never used by the app. |
| `backend/test_harness_codex.py` | Host to the same real Supervisor to `CodexAppServerWorker` over the fake app-server: protocol checks before text, refusals, provider and profile separation, the boundary record, cancel by interrupt and exit. Skips itself, with the reason, on a library without the Codex worker. |
| `backend/harness_host.py` | `HarnessCoordinator`: request/status/cancel/events operations, availability with helper details and exclusions, host-store mirroring, one writer per conversation, unknown-state handling. |
| `backend/host.py` | Wires the coordinator into `dispatch`, `tick`, `close` and adds `harness` to `status`. Test seams `supervisor_factory`, `harness_workers`, `harness_claude_executable`. |
| `backend/fake_claude_cli.py` | Test-only fake of the published CLI's stream-json and `auth status` protocol. Never used by the app. |
| `backend/test_harness_host.py` | Real Supervisor and Runtime over disposable stores with scripted fake worker ports; provenance loader tests. |
| `backend/test_harness_claude.py` | Host to real Supervisor to the library's `ClaudeCliWorker` over the fake CLI: identity check, text-only launch, refusals, Codex unsupported, cancel by process exit. |
| `src/bridge.rs` | Native allowlist: `harness_request`, `harness_status`, `harness_cancel`, `harness_events` (unchanged). |
| `tauri.conf.json` | Bundle resources include `backend/dervo_harness/*`. |

## How the library is used

Everything below is the library's own model, created by the local human owner (`local-user`) only when a request
needs it, and only with Dervo-owned ids:

- app project `<id>` becomes harness project `project:<id>` whose domain is the profile category (`personal` or `work`);
- a conversation becomes room `conversation:<id>` with the conversation's own visibility;
- each helper eligible for the conversation becomes project member `helper:<name>` (role `agent`), gets a shared home
  room `helper:<name>:<project>`, a registered handle `@<name>` addressed in that room, and one pool binding per
  project carrying the helper's provider and account labels. A project never carries another account's helper;
- execution grants: each eligible helper on its own home room, and the named helper on the conversation room. A
  private conversation also makes the named helper a room member.

A request records the user's text as an ordinary message in the conversation room, then calls `Runtime.submit`
there as the owner with the text as the goal and the named helper as the worker. The helper's fresh session context
is the library's bounded, deterministic view of that room. If a helper asks another bound helper through
`ScopedActions.request_help`, the `Supervisor` brokers it under the library's rules (registered handle, budgets, loop
and duplicate guards, pool binding, grant, same account label) and the answer comes back through the handoff packet
as a `handoff_result` message in the conversation room. The next request's context is built from the room, so it
sees the answer. A private conversation cannot hand its text to a shared helper room: the library refuses that
transfer (`PolicyViolation`), the refusal is logged as `help_refused`, and nothing is created.

Cancel is `Runtime.cancel` followed by `Supervisor.settle`, so a delegation the run opened is withdrawn and the
dependent helper run is stopped too. The run is `cancelled` only once the port confirms (for the Claude worker, only
once the CLI process has exited after the signal).

One owner per data dir, twice over: the app store already refuses a second `Host` on the same root, and below it the
library's runtime lease refuses a second supervisor on the same harness dir while the holder's process is alive. A
request that hits the lease answers `failed` with the lease message, never a takeover.

Restart: the library marks runs left running by a previous process `unknown`. The adapter asks the ports about them
once through `Supervisor.reconcile` (no replay) and requests a stop for any run still active, because the app has
already reported those requests as `unknown` and nothing tracks them any more. A port with no memory of the run makes
it `lost`; a stop the port cannot confirm leaves the run `cancelling` and the room's scope owned, which the next
request in that conversation reports as a refusal.

## Helpers and authority

Helpers are `HelperSpec(port, provider, account, note, model)`. `port` implements the library's `WorkerPort` protocol,
or is a factory `port_for(project_id, work_dir)` for a helper whose worker serves exactly one harness project.
`provider`, `account` and `model` are labels the app chooses and the library records on every run and result.
They are not authority:

- A fake port (`port.is_fake`) is labelled exactly `fake`/`none` with no model. It may serve any conversation because
  every run and result it produces is stamped fake by the library. It is a test double, injected only through the
  `Host(harness_workers=...)` seam.
- A real port names its provider (`claude`), the profile account it acts under (`profile:<id>`) and the exact model.
  It may only serve conversations of that provider, profile and model; anything else is rejected before any store opens.
- There is no default helper and no fallback. A request names one bound helper or is rejected.

### The Claude helper (real, text only)

`harness_claude.derive` builds one helper per (owned personal Claude profile, model that profile's conversations
are bound to), named `claude-<12 hex>` from the pair. Its port is the library's `ClaudeCliWorker` in text-only mode,
built per harness project with:

- `config_dir` = the profile's owned home (`<root>/profiles/<id>`), the same `CLAUDE_CONFIG_DIR` the app signs in to.
  The library needs its marker file there; the app writes it into its own signed-in profile dir (the library claims
  an empty dir itself and still refuses the CLI's personal default dir);
- `identity` = the profile's account email. The worker runs the CLI's own `claude --safe-mode auth status` in that dir
  and refuses to launch unless it reports that exact email on a first-party claude.ai subscription login. A different
  account, a signed-out dir or a console/API login fails the run with no context sent;
- `model` = the conversation's model. The worker requires it on the CLI's init message, every assistant message and
  the usage report, and fails the run otherwise;
- `executable` = the published CLI from `claude_policy.executable` (standard locations only; tests inject a fake);
- `work_dir` under `<root>/harness/work/`, with an empty per-run cwd. The worker never runs inside a project checkout.

Excluded and never launched, with the reason reported in `status.harness.excluded`: a work profile (`owned_profile`
refuses it; no personal/work crossing), a profile whose identity is not an email, a conversation whose model is not a
Claude model id.

What the Claude helper cannot do: no tools, no MCP servers, no file or shell access, no `request_help` (the action
bridge is not enabled), no steering, no resume. It receives the conversation room's context plus the request text
and returns text. The library logs the observed auth metadata (email included) as one `auth_observed` progress event
on the run for audit; the email never enters the prompt, a message or a room record.

### The Codex helper (real, text only, same pool)

`harness_codex.derive` builds one helper per (owned personal Codex profile, model), named `codex-<12 hex>`. Its port
is the library's `CodexAppServerWorker` in text-only mode, built per harness project with:

- `codex_home` = the profile's owned home (`<root>/profiles/<id>`), the same `CODEX_HOME` the app signs in to and, as
  in the app's own transport, the child's `HOME`. The library needs its marker file there; the app writes it into its
  own signed-in profile dir (the library claims an empty dir itself and refuses the personal `~/.codex`);
- `identity` = the profile's account email; `model` = the conversation's model; `executable` = the app's pinned binary
  from `codex_policy.EXECUTABLE` (tests inject a fake); `work_dir` under `<root>/harness/work/`.

Before any text is sent the worker refuses a home with custom configuration (`config.toml`, hooks, plugins, skills;
the same files the app's `check_profile` refuses; nothing is launched), then walks the official protocol in the
app's proven order: `initialize` (pinned version, owned home), `config/read` (the effective configuration must equal
the launch policy exactly; the fake in the tests rebuilds that policy from the `-c` overrides it was launched with),
`account/read` (a ChatGPT subscription whose email is the profile identity; signed-out and API-key accounts are
refused), `model/list` (the model must be a visible catalog entry), `thread/start` (read-only, no network, on-request
approvals, user reviewer, no instruction sources). Only then one `turn/start` carries the context and the request.
Any approval or tool request from the server is declined by protocol and fails the run. Cancel sends
`turn/interrupt`, then a signal; the run is `cancelled` only on observed exit.

Excluded with the reason: work profiles, non-email identities, non-catalog-shaped model ids, and every Codex
profile while the packaged library predates the Codex worker (0.7.1). No fallback is selected in any of these cases.

### Cross-provider authorisation: the boundary record

The library compares the raw `account` label of an asking member and a helper member and refuses anything but
equality. By default every helper carries `profile:<id>`, so a Claude profile and a Codex profile stay apart even for
one person with one email on both. That is not relaxed to "same email" or "all personal profiles".

`harness_boundary` (`{profileIds: [...]}`) lets the local owner record, explicitly and by profile id, that two or more
owned personal profiles form one boundary. Every profile must be owned (`owned_profile`: right directory, personal
category) and in at most one record. From then on those profiles' helpers and conversations carry
`boundary:<id>`: the helpers are bound into each other's projects, the library renders each as a helper the other
may ask, and a run's `routed` event shows the label. What the record does not do: it never lets a conversation run
another provider's helper (provider and model must still match), it never touches profiles left out of it (a third
profile with the same email keeps `profile:<id>`), and it grants nothing to the text-only workers, which cannot call
`request_help` anyway. `status.harness.boundaries` lists the records.

### Named help: catalogue, resolution and the opt-in action port

The library's own rules decide who may ask whom: an active registered handle, a bound pool member, an execution
grant on the helper's home room, and the same account label. The app exposes exactly that, with stable ids:

- `conversation_status.harness.catalogue`: the helpers this conversation's own helper could ask, each as
  `{handle: "@codex-<12 hex>", provider, model, can_ask}`. The handle is the helper's stable id (a hash of profile id
  and model), registered under that name in the harness project. Fakes and the helper itself are never listed.
- `conversation_status.harness.can_ask`: whether this conversation's helper carries the action port.
- `harness_resolve` (`projectId`, `channelId`, `mention`): resolves one explicit `@handle` against the catalogue and,
  once a runtime is open for the project, through the library's `resolve_agent` (`verified` carries actor, room,
  member, provider and account, or `verification` states the refusal). Anything not in the catalogue is
  `resolved: false` with the reason; a helper cannot ask itself.
- `harness_actions` (`profileId`, `enabled`): the owner switches one Claude account into the library's `request_help`
  action bridge. Off by default. Codex accounts are refused: a Codex helper answers, it never initiates help. The port
  is actually configured with `actions=True` only when the account is also inside a boundary record, because only
  then is there any linked helper the library would let it ask; the answer says `linked` either way.
- `harness_request` accepts an optional `mentions` list of `@handles`. Each is resolved as above before anything is
  recorded, and the asker must carry the action port; otherwise the request is rejected with the reason. Mentions are
  recorded on the request and the `submitted` event. The worker still only receives the context and the text; whether
  it calls the tool is the model's turn, and the request shows what actually happened.
- Request views carry `activity`: the library's `help_requested`, `help_returned`, `help_refused` and
  `help_return_refused` events as plain text (`Asked @codex-… for help (Codex)`, `@codex-… returned an answer`,
  `Help from @… refused: <reason>`), and `delegations` with `returned_at`. No auth metadata is ever mirrored there.
- The coordinator keeps pumping a request whose asking run has finished while a delegation is still pending, so the
  linked answer arrives and is shown instead of stalling until the next request.

What this does not do: no helper may run in another provider's conversation (provider and model must still match),
no cross-account asking without a boundary record (a same-email profile left out of the record is refused by the
library with `NotFound`), no help from a private conversation into a shared helper room (`PolicyViolation`), no
coding, no automatic delegation. Stopping a request withdraws its packet and stops the helper; a restart replays
nothing.

## Coding collaboration goes through the same library

`backend/collaboration.py` (the coding owner's `dervo_request_help`) does not reimplement the library's delegation rules.
It loads the packaged library through `harness_port.load_library` (the same provenance check, the same module object
the coordinator uses) and hands every ask to the library's `DelegationRules.decide` with the project policy expressed as
a `DelegationPolicy` (`max_depth`, `max_per_root = maxCalls`, `max_parallel`, repeats with a new purpose allowed
because sources are file paths) and the owner task's durable lineage and packets as plain values. The library decides
shape, depth, budgets, self, loop, repeat and whether the ask starts now, waits (root asks are queued by the coding
host) or is refused as a nested ask that would wait. The coding host keeps what is its own: the agent allowlist and
link check, the file scope, the held-file lock, the snapshots, the packet records and the coding engine that runs the
helper. Without the verified library `collab_policy` reports `unavailable` and no help tool is offered. What is not
shared: coding runs are not runtime runs and coding tasks are not `WorkerPort`s; see
`evidence/collaboration-unification.md` for the exact ownership.

## Operations (through `local_request`)

All four require `projectId` and `channelId`. The conversation must belong to the project and, if private, to the
local actor. Anything else is rejected with an error string, exactly like existing operations.

### `harness_request`
Args: `clientId` (idempotency key), `text` (up to 32 KiB), `mode`, `helper` (a bound helper name).

- `mode: "coordinate"` submits the text to the named helper as described above.
- `mode: "execute"` never runs. It answers `{id: null, state: "unsupported", detail}` and points at the existing coding
  task path. No coding execution is granted through the harness.
- If the library is not packaged, or no helper is bound: `{id: null, state: "unavailable", detail}`. No store is created.
- Rejected when the helper is unknown or not authorised for this conversation (provider, profile or model), or when
  the conversation already has an active chat turn, coding task or harness request. One writer per conversation.
- A library refusal before anything durable was submitted (lease held, scope owned, worker could not be prepared)
  answers `state: "failed"` with the refusal in `error`. A failure the app cannot attribute answers `unknown`.

Result: the status object below. Right after submit it reports `queued`; the next poll reports the run state.

### `harness_status`
Args: `requestId`. Result:

```json
{"id": "...", "state": "running", "mode": "coordinate", "helper": "claude-1f2e3d4c5b6a",
 "linked_return": null, "delegations": [], "error": null, "created_at": 1757000000,
 "detail": "The named helper is working in this conversation's harness room. No project file is changed."}
```

`state` is one of `queued`, `running`, `stopping`, `completed`, `cancelled`, `failed`, `lost`, `unknown`. The last
five are terminal for the app; nothing is retried automatically. `unknown` means observation was lost (host restart,
host close, or the library stopped answering). `linked_return` is set on `completed`: `summary`, `refs`, `worker`,
`worker_is_fake`, `result_message_id`, `returned_at`. `delegations` lists the helper asks this run made: `id`,
`handle`, `status` (the library's dispatch status), `purpose`, `depth`, `target_room`, `member`, `result`, `error`.

### `harness_cancel`
Args: `requestId`. Returns the status object. Non-terminal requests move to `stopping`, then `cancelled` once the port
confirms. Cancelling a terminal request returns its current status without side effects.

### `harness_events`
Args: `after` (integer sequence, 0 for everything). Result: up to 1000 events for this conversation, ascending `seq`:

```json
[{"seq": 7, "request_id": "...", "kind": "routed",
  "data": {"member": "claude-1f2e3d4c5b6a-9a8b...", "name": "claude-cli-text-only", "is_fake": false, "provider": "claude",
           "account": "profile:...", "attempt": 1, "source": "runtime", "accepted": true, "reason": ""}}]
```

App-recorded kinds: `submitted`, `cancel_requested`, `cancel_refused`, `unknown`, `failed`. Every other kind is a
mirrored run event from the library's own ledger, with its payload clipped to a bounded size plus `attempt`,
`source`, `accepted` and `reason`. Kinds seen in the tests: `started`, `routed`, `progress` (phases `auth_observed`,
`initialized`, `init_observed` for the Claude worker), `result`, `succeeded`, `failed`, `cancel`, `observation`,
`stopped`, `cancelled`, `help_requested`, `help_refused`, `help_returned`, `dispatched`, `dispatch_returned`.
Rejected events (`accepted: false`) are recorded too, with the reason.

Rendering guidance for a frontend owner: show `kind`, `source` and `accepted` as labels, and show the payload's
string values as plain text. Payload text comes from worker ports and, through them, from model output; it is data
to display, never markup to interpret and never an instruction to act on. The library's context fencing and mention
rules protect the helper's own context, not the UI; escaping and layout are the renderer's job.

### `harness_requests`
Args: `projectId`, `channelId`. Result: every coordination request of this conversation, oldest first, as the status
object above plus `text` (what was sent) and `provider` (the helper's provider label at submit time). Persisted in
the host store, so a reload or host restart shows the same rows and outcomes.

### `conversation_status.harness`
Every `conversation_status` answer carries `harness: {state, helper, provider, model, account?, detail}`: the one
helper that serves this conversation's provider, account and model (`available`), or `unavailable` with the reason
(library not packaged, no helper bound, this profile or model excluded, no helper for this account). Reading it never
opens the harness store and never launches anything. Fakes are never offered here.

### `harness_boundary`
Args: `profileIds` (two or more distinct owned personal profile ids). Result: `{id, profiles, created_at}`. Rejected
for fewer than two, duplicates, unknown or work profiles, or a profile already in a record. No channel or project
argument: the record is about profiles, not conversations.

### `status`
Includes `harness: {state, detail, helpers, library, details, excluded, boundaries}`. `helpers` is the list of bound
helper names; `details[name]` gives `provider`, `model`, `account` and `fake`; `excluded` lists profile/model pairs
that were not offered, with the reason; `boundaries` lists the owner-created records. Reading it never opens the
harness store and never launches anything.

## Conversation UI (frontend/src/local)

The existing sidebar and conversation composition are unchanged. What was added:

- `ConnectedApp.submit`: when `conversation_status.harness` is `available`, the composer sends through
  `coordinate()` in `api.ts` (`harness_request` with one persisted request id per draft, retried with the same id
  after a lost acknowledgement, never re-sent with different text). Otherwise the existing queue path runs exactly
  as before, including the explicit "Send to Codex" step and the Claude chat blocker.
- `Harness.tsx`: coordination requests render as timeline rows (your text, then the helper's answer as a provider row
  labelled "Claude Code (coordination)" or "Codex (coordination)"), merged by time with chat and task rows. The
  footer under a request states the phase plainly (`Submitted for coordination`, `Coordinating, text only`,
  `Stopping`, `Returned, text only`, `Cancelled`, `Not answered`, `Helper lost`, `Outcome unknown`), offers Stop
  only while active (`harness_cancel`), shows the refusal or the lost-observation text, lists helpers the run asked,
  and says under a returned answer that no project file was changed and that changing files is a coding task.
- The status line shows `Coordination · <provider> · text only` only when the host resolved the helper; the composer
  placeholder says the same. Nothing implies automatic collaboration: no mentions, no helper picker, because the
  text-only workers cannot ask each other and there is nothing else to resolve.
- Connections and permissions dialog: "Coordination across accounts" lists linked accounts and lets the owner link
  two personal accounts by id (`harness_boundary`). The copy states that matching emails never link anything. Work
  profiles are never offered.
- The coding path is untouched: "New coding task" in the conversation menu, task footers, scope approval and result
  review stay exactly as they were. Coordination never creates a task, edits a file or is shown as execution.
- Named help: `coordinate()` extracts `@handles` from the message and resolves each through `harness_resolve` before
  sending; an unresolvable mention keeps the draft and says why. The status line adds `may ask linked helpers` only
  when the helper carries the action port and the catalogue is not empty. The footer shows the recorded help activity
  lines and, while a linked helper is still working, says so; a linked helper's returned answer is its own provider
  row (`Codex (coordination)`, content `@codex-… answered: …`). Connections and permissions gains "Asking linked
  helpers": one checkbox per personal Claude account, disabled until that account is linked, calling
  `harness_actions`. No helper picker and no autocomplete: a mention is typed and resolved, never suggested.

Not done in the UI: no threading for coordination (a reply target is cleared on send), no rendering of the raw
`harness_events` stream (the footer shows state, error and delegations only), no mention or helper selection.

## What is verified and how

Run from `backend`:

```sh
/usr/local/bin/python3 -B -m unittest -v test_harness_host test_harness_claude test_harness_codex test_harness_help test_host test_host_stdio test_chat_slice
```

`test_harness_help` proves, with the fake Claude CLI playing the SDK MCP client and the fake Codex app-server: the
catalogue and action port are off by default and Claude only; opting in without a boundary configures nothing; a
linked, opted-in Claude helper launches with exactly `--allowedTools mcp__dervo__request_help` and no `--safe-mode`;
its scripted `tools/call` becomes a real delegation (the reply names the delegation id), the Codex helper receives the
cited message and the purpose as its task, the answer returns through the packet into the conversation room, the
request's activity reads `Asked @codex-… for help (Codex)` then `@codex-… returned an answer`, and the next request's
context contains the answer with no second ask; an ask of a same-email but unlinked helper is refused by the library
(`NotFound`) and shown as a refusal with that helper never launched; a private conversation's ask is refused
(`PolicyViolation`); stopping a request with help in flight interrupts the helper's turn, ends both runs `cancelled`
with the packet withdrawn, and a host restart replays nothing.

`test_harness_host` proves, against the real Supervisor, Runtime and Harness over disposable stores with scripted
fake worker ports: submit and pump in the conversation room with the mapped records; helper delegation through
`request_help`, the packet, the linked `handoff_result` and the next session's context; cancel with the dependent
helper run withdrawn; account and provider refusals before any store opens; the private-to-shared refusal inside the
library; the lease refusing a second owner; restart reporting `unknown` with the orphaned run stopped and nothing
replayed; discovery of the packaged, verified library; and the loader refusing a tampered, incomplete or unlisted
package. `test_harness_claude` proves, with the fake CLI standing in for the published binary: the helper is derived
from the owned profile and model with no email exposed in status; `auth status` runs first in the owned dir; a
text-only launch with the evidenced flags, the allowlisted environment, the owned config dir and an empty work cwd;
the prompt carries the fenced context and the request text; the run is stamped real and completes; identity mismatch
and signed-out refuse with no context sent; bad model, bad identity and work profiles are excluded and never
launched; a Codex conversation is unsupported and another profile's helper is rejected; cancel signals the process
and the run is `cancelled` only on its exit, after which the conversation is usable again.

The bundle resource glob was not exercised: no native build ran.

## Not done (stated, not claimed)

- No live Claude run. Everything Claude-side is proven against `fake_claude_cli.py`; the published CLI's wire shapes
  rest on harness-core's evidence and its own live acceptance runs, not on anything run here.
- No coding execution through the harness. `mode: "execute"` stays `unsupported`; the coding task path is separate.
- No live Codex run either: everything Codex-side is proven against `fake_codex_app_server.py`. The vendored library
  must be 0.7.1 or later for the Codex helper to exist at all; until the parent re-vendors, Codex profiles are
  excluded with that reason and `test_harness_codex` skips itself.
- Neither real helper can ask the other: both workers are text-only (no `request_help` bridge). The boundary record
  makes them visible to each other in context and consistent in labels; model-selected help across providers would
  need the action bridge, which is not enabled here.
- The frontend does not call these operations yet.
- Helper home rooms are shared, so a private conversation's helper cannot delegate.
- No native build or bundle verification ran in this session.
