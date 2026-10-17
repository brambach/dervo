# Contributing to Dervo

Thanks for taking a look. Bug reports, fixes and ideas are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the approach.
- Be kind: see the [code of conduct](CODE_OF_CONDUCT.md).
- Read [AGENTS.md](AGENTS.md). It holds the product rules the code follows, and it's what coding agents working in
  this repo read too.

## Setting up

Follow [Getting started](README.md#getting-started) in the README. You'll need Rust, Node.js 22 with pnpm, Python 3.13,
and Claude Code or Codex if you want to try real runs.

## Making a change

1. Fork the repo and branch from `main`.
2. Keep the change focused. One fix or feature per pull request.
3. Add or update tests next to the code you touched:
   - Host: `backend/test_<module>.py` (`unittest`). Fake providers live in `backend/fake_claude_cli.py` and
     `backend/fake_codex_app_server.py`, so tests never need a real account.
   - Interface: `*.test.mjs` beside the module, and Playwright specs in `frontend/tests/e2e`.
   - Native shell: `#[test]` functions in `src/`.
4. Run the checks:

   ```sh
   (cd backend && python3 -m unittest)
   (cd frontend && pnpm test && pnpm typecheck && pnpm check)
   cargo test
   ```

5. For interface changes, look at the result in the running app in light and dark mode, and include a screenshot in
   the pull request.

Don't skip, delete or loosen a test to get a change through. If a test was already failing before your change, say so
in the pull request.

## Style

- Python: standard library first, small modules, docstrings that say why.
- TypeScript: Biome formats and lints (`pnpm format`, `pnpm check`).
- Rust: `cargo fmt`.
- Interface copy: plain, concrete words, contractions, no em dashes. Copy goes through `frontend/src/i18n`.

## Never commit

API keys, tokens, account emails, signing material or private project content. Test fixtures use `example.test`
addresses and keys that are clearly fake.

## License

By contributing, you agree that your contributions are licensed under the [Functional Source License (FSL-1.1-ALv2)](LICENSE.md).
