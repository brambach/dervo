#!/bin/sh
# Build Dervo's Dock tile plug-in (beta.15, group 7) into target/docktile/DervoDockTile.docktileplugin.
#
# tauri.conf.json runs this as beforeBundleCommand, after the Rust build and before bundling, then copies the plug-in
# into Contents/PlugIns (bundle.macOS.files). It compiles src/docktile/DervoDockTile.m with the Command Line Tools'
# clang, adds the icon choices' ICNS files from icons/choices (built by scripts/build-icons.py) and seals the plug-in
# ad hoc, so the app's own signature can include it. The release builder signs it again with the app for a Developer
# ID build. Nothing is installed or launched. Works from the app folder or from frontend/.
set -eu
case $0 in
  */*) SCRIPT_DIR=${0%/*} ;;
  *) SCRIPT_DIR=. ;;
esac
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
SOURCE=$ROOT/src/docktile
PLUGIN=$ROOT/target/docktile/DervoDockTile.docktileplugin

rm -rf "$PLUGIN"
mkdir -p "$PLUGIN/Contents/MacOS" "$PLUGIN/Contents/Resources"
cp "$SOURCE/Info.plist" "$PLUGIN/Contents/Info.plist"
/usr/bin/clang -fobjc-arc -bundle -O2 -Wall -Werror -arch arm64 -mmacosx-version-min=11.0 -framework Cocoa \
  -o "$PLUGIN/Contents/MacOS/DervoDockTile" "$SOURCE/DervoDockTile.m"
# Cream is the bundled icon, which the Dock shows when the plug-in draws nothing, so it isn't copied.
found=0
for icon in "$ROOT"/icons/choices/*.icns; do
  [ -f "$icon" ] || continue
  case ${icon##*/} in cream.icns) continue ;; esac
  cp "$icon" "$PLUGIN/Contents/Resources/"
  found=$((found + 1))
done
[ "$found" -gt 0 ] || { echo 'build-docktile.sh: no icon choices in icons/choices. Run scripts/build-icons.py.' >&2; exit 1; }
/usr/bin/codesign --force --sign - --timestamp=none "$PLUGIN"
/usr/bin/codesign --verify --strict "$PLUGIN"
echo "Built $PLUGIN with $found icons."
