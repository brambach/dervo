#!/bin/sh
# Staged test and build for Dervo Local. Output stays under $ROOT/target and $ROOT/dist.
# Nothing is installed, launched or deployed.
#
# Tool discovery
#   DERVO_NODE, DERVO_CARGO and DERVO_PYTHON name explicit executables. An override that is set
#   but empty, missing, a directory or not executable fails the build. There is no fallback to
#   PATH for an explicit override. Relative overrides resolve against the caller's directory.
#   Without an override the tool comes from the caller's PATH (node, cargo, python3).
#   Symlinks are accepted when they resolve to an executable regular file.
#   RUSTUP_HOME, CARGO_HOME and PATH are used exactly as the caller set them.
#
# Browser for the Playwright harness
#   PLAYWRIGHT_CHROMIUM_EXECUTABLE is passed through only when the caller set it, and must then
#   be an executable file. Otherwise Playwright launches its normally installed browser.
#
# The app's own Python
#   After Tauri bundles the app, scripts/python_runtime.py stages the Python 3.13 pinned in
#   runtime/python.json into it (Contents/Resources/runtime/python). The first run downloads the
#   pinned archive into ~/Library/Caches/Dervo/build-downloads and every run checks its SHA-256.
#   Then scripts/voice_runtime.py stages voice's libraries (runtime/voice.json, pinned wheels) into
#   Contents/Resources/voice/site-packages and builds the voice audio helper (voice/mac/build.sh).
#
# Separate debt this script does not change or hide
#   DERVO_PYTHON selects the interpreter for the backend unit tests here. In a source checkout the
#   backend sandbox runtime and the browser host harnesses still pin their own interpreter and the
#   Python 3.13 framework (backend/task_evidence.py, src/bridge.rs, frontend/*-ui.test.mjs). The Rust
#   toolchain version is whatever the caller's cargo provides; there is no rust-toolchain file.
set -eu
START_DIR=$PWD
# Root discovery uses only shell builtins so it works with a minimal or empty PATH.
case $0 in
  */*) SCRIPT_DIR=${0%/*} ;;
  *) SCRIPT_DIR=. ;;
esac
[ -n "$SCRIPT_DIR" ] || SCRIPT_DIR=/
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR" && pwd)

fail() {
  printf 'build.sh: %s\n' "$*" >&2
  exit 1
}

absolute() {
  case $1 in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$START_DIR/$1" ;;
  esac
}

# resolve_tool NAME VARIABLE: prints the executable to use, or fails the build.
resolve_tool() {
  tool_name=$1
  var_name=$2
  eval "is_set=\${$var_name+set}"
  eval "given=\${$var_name-}"
  if [ "${is_set:-}" = set ]; then
    [ -n "$given" ] || fail "$var_name is set but empty. Point it at a $tool_name executable or unset it."
    given=$(absolute "$given")
    if [ ! -f "$given" ] || [ ! -x "$given" ]; then
      fail "$var_name=$given is not an executable file. No PATH fallback is used for an explicit override."
    fi
    printf '%s\n' "$given"
    return 0
  fi
  found=$(command -v "$tool_name" 2>/dev/null) || fail "$tool_name is not on PATH. Set $var_name to an explicit executable."
  # A relative PATH entry yields a relative result. Anchor it to the caller's directory before cd.
  found=$(absolute "$found")
  if [ ! -f "$found" ] || [ ! -x "$found" ]; then
    fail "$tool_name resolved to $found, which is not an executable file. Set $var_name to an explicit executable."
  fi
  printf '%s\n' "$found"
}

NODE=$(resolve_tool node DERVO_NODE)
CARGO=$(resolve_tool cargo DERVO_CARGO)
PYTHON=$(resolve_tool python3 DERVO_PYTHON)

if [ "${PLAYWRIGHT_CHROMIUM_EXECUTABLE+set}" = set ]; then
  [ -n "$PLAYWRIGHT_CHROMIUM_EXECUTABLE" ] || fail "PLAYWRIGHT_CHROMIUM_EXECUTABLE is set but empty. Unset it to use the installed Playwright browser."
  # Anchor a relative override to the caller's directory so it still resolves after cd frontend.
  PLAYWRIGHT_CHROMIUM_EXECUTABLE=$(absolute "$PLAYWRIGHT_CHROMIUM_EXECUTABLE")
  if [ ! -f "$PLAYWRIGHT_CHROMIUM_EXECUTABLE" ] || [ ! -x "$PLAYWRIGHT_CHROMIUM_EXECUTABLE" ]; then
    fail "PLAYWRIGHT_CHROMIUM_EXECUTABLE=$PLAYWRIGHT_CHROMIUM_EXECUTABLE is not an executable file. Unset it to use the installed Playwright browser."
  fi
  export PLAYWRIGHT_CHROMIUM_EXECUTABLE
fi

printf 'build.sh: node=%s\n' "$NODE" >&2
printf 'build.sh: cargo=%s\n' "$CARGO" >&2
printf 'build.sh: python3=%s\n' "$PYTHON" >&2
printf 'build.sh: note: backend unit tests run with the python3 above. In a source checkout the sandbox runtime in backend/task_evidence.py, src/bridge.rs and the frontend host harnesses still pins its own interpreter and the Python 3.13 framework. That pin is separate debt this script does not change or hide. The built app carries its own Python from runtime/python.json.\n' >&2

cd "$ROOT"
"$NODE" --test titlebar.test.mjs
(cd backend && "$PYTHON" -m unittest -v)
(cd frontend && "$NODE" node_modules/typescript/bin/tsc --noEmit)
"$NODE" frontend/node_modules/vite/bin/vite.js build frontend --outDir ../dist
(cd frontend && "$NODE" --import ./test-loader.mjs --experimental-strip-types --test src/features/messages/ui/useDraftPersistSnapshot.test.mjs src/features/messages/lib/useDrafts.test.mjs src/features/messages/lib/sendChannelBinding.test.mjs)
(cd frontend && "$NODE" local-ui.test.mjs)
"$CARGO" test --offline
"$NODE" frontend/node_modules/@tauri-apps/cli/tauri.js build --bundles app -- --offline
"$PYTHON" scripts/python_runtime.py stage --app "target/release/bundle/macos/Dervo Local.app"
"$PYTHON" scripts/voice_runtime.py stage --app "target/release/bundle/macos/Dervo Local.app"
/usr/bin/codesign --force --sign - "$ROOT/target/release/bundle/macos/Dervo Local.app"
/usr/bin/codesign --verify --deep --strict "$ROOT/target/release/bundle/macos/Dervo Local.app"
