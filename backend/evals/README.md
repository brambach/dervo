# Smartness evals

Dervo's automatic decisions (memory and knowledge, auto-sorting, Home's reasoning, handoff summaries) scored against
the four reviews in `notes/smartness/`. One host per case, on a throwaway data dir and a scratch git repo with no
remote. Checks read records and count things; no model judges anything.

    cd backend
    python3 -m unittest test_evals                 # the gate: a smoke subset through the fake CLI, under 30 s
    python3 -m evals.runner --mode fake            # every case through the fake CLI, no model, about 2 minutes
    python3 -m evals.runner --mode real --cap 60 --case-cap 6 --rerun-failing 3 --out evals/out/today
    python3 -m evals.report evals/out/today        # the tables notes/smartness/baseline.md is built from

`--agent-provider claude|codex` runs every agent case on one provider (Home always stays on Claude). Output dirs
under `evals/out/` are ignored by git.

## What real mode touches on this Mac, and what it never touches

Real mode (`headless_driver.HostDriver(real=True)`, `evals/real_cli.py`) uses the sign-ins the Mac already has. Two
things about that are not read-only, so read them before running it on a Mac you care about:

- **Codex.** The eval profile's `CODEX_HOME` is a throwaway folder whose `auth.json` is a **symlink** to
  `~/.codex/auth.json`. The bundled Codex reads its ChatGPT token through the link, and if it refreshes the token it
  writes the new one **through the link into the real file**, exactly as a plain `codex` run would. That is the point
  (a copy would let the two files drift and one of them would log out), but it means the real file can change during
  an eval. Deleting the eval's data dir removes the link only, never `~/.codex/auth.json`. Everything else Codex writes
  (sessions, `state_*.sqlite`, logs) lands in the throwaway folder.
- **Claude.** The eval profile is Dervo's "system home" profile (`claude_policy.is_system_home`): no
  `CLAUDE_CONFIG_DIR`, so the published `claude` uses the Mac's existing sign-in and its Keychain entry, the same as
  every other launch on this Mac. Real Claude coding runs therefore **write their session transcripts into
  `~/.claude/projects/<slug of the run's temp cwd>/`**, and a continued session moves that file between project
  folders there (`session_continuity.place_session`). Home's calls pass `--no-session-persistence` and leave nothing.
  The eval never reads `~/.claude`, never opens the Keychain, and never reads or writes
  `~/Library/Application Support/local.dervo.connected`.

MCP servers and claude.ai connectors are off for every real run: restricted launches and Home already pass
`--strict-mcp-config`, and in real mode `serve()` also empties `mcp_connectors.names` and keeps `--strict-mcp-config`
on full-access launches.

The wrapper sets `USER` and `LOGNAME` as a fallback for the real `claude` only: Claude Code's Keychain lookup needs
`USER`, and since 21 Sep Dervo's `clean_environment` sends it for your existing sign-in (`auth status` reports signed
out without it).

## Budget

`real_cli.py` counts model launches (`claude -p`, `codex app-server`) under a file lock before the process starts and
refuses past `--cap` (whole run, default 60) or `--case-cap` (one case, default 6) with exit 70. Every call is logged to
`<out>/real-calls.jsonl` with case, provider, model, start time, seconds and exit code. The runner also skips a case
whose declared `calls` would cross the cap.

## Writing a case

`evals/case.py`: `@case(id, system, title, provider=..., calls=..., source=..., expect_fail=...)` on a function that
returns `(setup, action, check)`. `setup(h, ctx)` seeds through `EvalHost` (`evals/harness.py`): scripted threads on
the fake profile (`scripted`, with `guide=` and `tool_calls=` for guide updates, notes and file writes), workstreams,
bugs, notes straight into the store, rules and skills. `action(h, ctx)` sends one thing (`run`, `home`, `arrive`,
`message`) and returns the outcome. `check(h, ctx, outcome)` returns `(passed, one-line reason)`. Mark `expect_fail`
when the review says today's code gives the wrong answer; the fake-mode test asserts those still fail, so a fix that
lands shows up as a test to update.
