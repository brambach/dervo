# Architecture

Dervo is a [Tauri 2](https://tauri.app) desktop app. Three layers talk through one narrow bridge.

```text
React interface (frontend/)  <->  Rust shell (src/)  <->  Python host (backend/)  <->  Claude Code, Codex
```

## Layers

- **Interface** (`frontend/`): React and TypeScript. Screens specific to Dervo live in `frontend/src/local`. It never
  talks to a provider; it asks the host for operations and renders the answers.
- **Native shell** (`src/`): windows, menus, the system keychain, folder pickers and the bridge
  (`src/bridge.rs`). It starts the Python host and passes named operations between it and the interface.
- **Host** (`backend/`): projects, threads, runs, knowledge and the adapters that drive Claude Code
  (`backend/claude_*.py`) and Codex (`backend/codex_*.py`, `backend/dervo_harness`). User data lives in the platform's
  application folders, in SQLite.

## Ideas the code is built around

These are also the rules in [AGENTS.md](../AGENTS.md).

- **Dervo owns the record.** Conversations, tasks, knowledge and run evidence have stable IDs that belong to Dervo,
  not to a provider. A run's report comes from what Dervo observed, not only from what the agent said.
- **Unknown is a real state.** When observation is lost (a crash, a closed laptop), the run reads "unknown" and Dervo
  reconciles before replaying anything that might have had side effects.
- **Separate approvals.** Approving a scope, accepting a result and allowing an install or deploy are three
  different steps.
- **Your accounts.** Dervo verifies the signed-in account and the exact model before sending project content, and never
  falls back silently to another account or model.
- **One writer per scope.** Overlapping work in the same files is serialized; threads can use their own git worktrees.

## Testing

Providers are faked (`backend/fake_claude_cli.py`, `backend/fake_codex_app_server.py`), so tests need no real account.
Known failures per platform are listed in `.github/known-test-failures.json` and
`.github/known-frontend-failures.json`, and those lists may only shrink.
