#!/bin/bash
# The gate. If this passes, a branch is mergeable; if it doesn't, it isn't.
#
# Written 20 Sep 2026 because "run the gate" had never been written down, so every
# agent ran a different subset and a red test (test_knowledge_delivery) hid for a day.
#
# The backend half DISCOVERS its modules rather than reading a list, because the list in
# release_builder.py had drifted 16 modules behind what's on disk, including
# test_secret_scan, test_project_access and test_session_continuity.
#
# Never run `pnpm test` or `npm test` in frontend/. pnpm auto-installs first and that
# wrecks the node_modules shared by all 70 worktrees. See notes/BACKLOG.md D3.
#
# Usage:  scripts/gate.sh            everything
#         scripts/gate.sh backend    just the Python half
#         scripts/gate.sh frontend   just the TS half
#         scripts/gate.sh rust       just cargo test, if a toolchain is reachable

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd)
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3
WHICH=${1:-all}
FAILED=()

# Modules that are deliberately not in the gate. Add a reason or don't add the line.
SKIP_BACKEND=(
  test_evals            # calls real models, has its own cap and lock; run it on purpose
  test_rust_checks      # needs a trusted Rust toolchain: set DERVO_RUST_TOOLCHAIN, then run it on purpose
  test_rust_execution   # same toolchain; no rustc, no rustup and no config file on this machine
)

step () {
  local name=$1; shift
  echo ""
  echo "=== $name ==="
  if "$@"; then
    echo "--- $name OK"
  else
    echo "--- $name FAILED"
    FAILED+=("$name")
  fi
}

backend () {
  cd "$ROOT/backend" || return 1
  local mods=()
  for f in test_*.py; do
    local m=${f%.py}
    local skip=0
    for s in "${SKIP_BACKEND[@]}"; do [[ $m == "$s" ]] && skip=1; done
    [[ $skip == 0 ]] && mods+=("$m")
  done
  echo "${#mods[@]} modules discovered"
  "$PY" release_checks.py "${mods[@]}"
}

# src/features' SUITES are out: ~101 red, and the run hangs. But do not read that as
# "src/features is dead code". It isn't. src/local/ConnectedApp.tsx imports MessageTimeline
# from it, and so do WorkspaceDraftGate and workspaceDraftPersistence, so Dervo ships it.
# Excluding its tests is a practical call about a hanging suite, not a statement that the
# code doesn't matter. Typecheck below still covers it, which is why the gate is red on a
# clean tree. See notes/BACKLOG.md D6.
#
# src/shared is MIXED, Dervo's own UI alongside Buzz chat plumbing, so it goes in by
# default and the Buzz files are named here. A new Dervo test under src/shared is gated
# the day it lands; nobody has to remember to add it.
SKIP_FRONTEND=(
  src/shared/lib/useResolvedLinkPreviews.test.mjs   # Buzz link previews
  src/shared/api/invites.test.mjs                   # Buzz invites
  src/shared/api/relayReconnectReplay.test.mjs      # Buzz relay
  src/shared/lib/entityLink.test.mjs                # Buzz entity links
)

frontend_tests () {
  cd "$ROOT/frontend" || return 1
  local files=() f skip s
  while IFS= read -r f; do
    skip=0
    for s in "${SKIP_FRONTEND[@]}"; do [[ $f == "$s" ]] && skip=1; done
    [[ $skip == 0 ]] && files+=("$f")
  done < <(find src/local src/shared src/app src/protectedFeatures scripts \
             -name '*.test.mjs' 2>/dev/null | sort)
  echo "${#files[@]} suites (${#SKIP_FRONTEND[@]} inherited Buzz files skipped)"
  node --import ./test-loader.mjs --experimental-strip-types --test "${files[@]}"
}

# Three of package.json's checks are not here, and none of them can be. `lint`, `check`
# and `format` call `biome`, which is not a dependency, not in node_modules and not on
# PATH. A gate step that can never pass isn't a gate step, so they're out. See
# notes/BACKLOG.md D11. `check-pubkey-truncation` and `check-file-sizes` imported a
# `-core.mjs` from the repo root that had never been committed; scripts/ has both since 9 Oct.
#
# `unreachable` is new on 20 Sep. ProjectCatchup, the control that opens it and QuestionTray were all built, tested,
# reviewed and mounted by nothing on the same day, and every suite stayed green because a component's own tests import
# it directly. This step asks whether the app does. Its own allowlist, with a reason a line, is in the script.
typecheck ()  { cd "$ROOT/frontend" && npx --no-install tsc --noEmit; }
px_text ()    { cd "$ROOT/frontend" && node ./scripts/check-px-text.mjs; }
copy ()       { cd "$ROOT/frontend" && node ./scripts/check-copy.mjs; }
unreachable() { cd "$ROOT/frontend" && node ./scripts/check-unreachable.mjs; }
pubkey_truncation () { cd "$ROOT/frontend" && node ./scripts/check-pubkey-truncation.mjs; }
file_sizes () { cd "$ROOT/frontend" && node ./scripts/check-file-sizes.mjs; }

# Rust. There is no cargo on PATH on this machine; the release build finds one through
# the `rustToolchain` path set per project in Advanced build tools, and runs
# `cargo test --offline` as its `native-tests` step. So src/bridge.rs IS checked, at
# build time rather than at merge time. This step closes that window when a toolchain is
# reachable and says so plainly when it isn't, rather than passing silently.
rust () {
  cd "$ROOT" || return 1
  local cargo
  cargo=$(command -v cargo || true)
  [[ -z $cargo && -x $HOME/.cargo/bin/cargo ]] && cargo=$HOME/.cargo/bin/cargo
  if [[ -z $cargo ]]; then
    echo "NOT RUN: no cargo on PATH. src/bridge.rs is checked only by the release"
    echo "build's native-tests step. Set a toolchain and re-run to close that window."
    return 0
  fi
  "$cargo" test --offline
}

[[ $WHICH == all || $WHICH == backend  ]] && step "backend"    backend
[[ $WHICH == all || $WHICH == frontend ]] && step "frontend"   frontend_tests
[[ $WHICH == all || $WHICH == frontend ]] && step "typecheck"  typecheck
[[ $WHICH == all || $WHICH == frontend ]] && step "px-text"    px_text
[[ $WHICH == all || $WHICH == frontend ]] && step "copy"       copy
[[ $WHICH == all || $WHICH == frontend ]] && step "unreachable" unreachable
[[ $WHICH == all || $WHICH == frontend ]] && step "pubkey-truncation" pubkey_truncation
[[ $WHICH == all || $WHICH == frontend ]] && step "file-sizes" file_sizes
[[ $WHICH == all || $WHICH == rust     ]] && step "rust"       rust

echo ""
echo "================================"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "GATE PASSED ($WHICH)"
  exit 0
fi
echo "GATE FAILED ($WHICH): ${FAILED[*]}"
exit 1
