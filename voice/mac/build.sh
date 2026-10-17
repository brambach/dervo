#!/bin/bash
# Build the voice audio helper for the app bundle: Contents/Resources/voice/dervo-voice-audio.
#   voice/mac/build.sh OUT_DIR
# Needs Xcode's command line tools. macOS 14 or later, like voice's libraries (runtime/voice.json). The release builder signs it like the bundled Python (hardened runtime, the app's
# identity); its microphone use is attributed to Dervo, which needs NSMicrophoneUsageDescription in the app's Info.plist
# and, when signed with the hardened runtime, com.apple.security.device.audio-input on the helper.
set -euo pipefail
out=${1:?output folder}
mkdir -p "$out"
here=$(cd "$(dirname "$0")" && pwd)
swiftc -O -target arm64-apple-macos14.0 "$here/main.swift" -o "$out/dervo-voice-audio" -framework AVFoundation
echo "$out/dervo-voice-audio"
