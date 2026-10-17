# Working on Dervo

Guidance for anyone changing this code, people and coding agents alike.

## What Dervo is

A desktop workspace where one coordinator assigns work across the user's own Claude Code and Codex tools,
conversations organise and name themselves usefully, and approved project knowledge persists. See the
[README](README.md) for the layout of the Rust shell (`src/`), the Python host (`backend/`) and the React interface
(`frontend/`).

## Product rules

- Support both Codex and the published, unmodified Claude Code tool through the provider's own authentication.
  Never copy or proxy subscription tokens.
- Verify the account and the exact requested model before sending project content. Keep personal and work accounts
  separate. No silent fallback to another account or model.
- Dervo owns stable conversation, task and knowledge IDs, permissions, source links and run records. The coordinator
  calls bounded worker operations. One writer per overlapping scope.
- Show "unknown" when observation is lost. Reconcile before replaying uncertain side effects. Test stop, resume and
  late results.
- Keep approving proposed scope separate from accepting a result and from allowing installation or deployment.
- Discover executables and store user data in the platform's proper application locations. Don't hardcode paths
  from one machine.
- A fresh install must work without builder caches, credentials or manually created folders. Package required
  runtime assets, or detect and install prerequisites with explicit consent. Preserve third-party licenses.

## Design

Preserve the Dervo smiley character and the existing app composition. Layout, typography, motion, readability, focus
and error states need a rendered review in light and dark mode; passing tests don't prove a design works. Don't show
fabricated live activity.

## Development

- Tests run without real provider accounts. Use the fakes in `backend/fake_claude_cli.py` and
  `backend/fake_codex_app_server.py`. Ordinary local tests don't make model calls.
- Don't remove or loosen tests to make checks pass. Note known failures instead.
- Shared schema and bridge changes (`src/bridge.rs`, `backend/shared_operations.py`) need one owner per change.
- Native builds are staged under `target/` and mustn't replace a running copy of the app.
- Never put credentials, account emails or private project content in source, fixtures, logs or screenshots.

## Writing style

For interface copy, docs and commit messages: no em dashes, contractions, concrete language, and say what wasn't
checked.
