#!/bin/sh
# Start an installed Dervo the way a person would and check it came up: the window, the backend host on Dervo's own
# Python, and its data in the XDG data folder. Run it inside `xvfb-run -a dbus-run-session --` on a desktop-less
# machine (the Linux build workflow does). Nothing here signs in, opens a project or calls a model.
#
#   sh linux-app-smoke.sh COMMAND OUT_FOLDER
#
# COMMAND is what starts Dervo: dervo-local for the .deb or .rpm, or the AppImage's path. OUT_FOLDER gets the app's
# output, the process tree and two screenshots. Exits 0 only when the host ran on the bundled Python, the window
# drew something other than one flat colour, and Dervo was still running after 30 seconds.
set -u
COMMAND=$1
OUT=$2
mkdir -p "$OUT"
DATA=${XDG_DATA_HOME:-$HOME/.local/share}/local.dervo.connected

# A Secret Service for the session, so the keyring path is the one a desktop has.
echo -n smoke | gnome-keyring-daemon --unlock --components=secrets >/dev/null 2>&1 || true

"$COMMAND" >"$OUT/app.log" 2>&1 &
APP=$!
sleep 30
import -window root "$OUT/window-30s.png" 2>/dev/null
ps -eo pid,ppid,args --forest >"$OUT/processes.txt"
sleep 15
import -window root "$OUT/window-45s.png" 2>/dev/null

status=0
if ! kill -0 "$APP" 2>/dev/null; then
  echo "Dervo exited before 45 seconds."; status=1
fi
if grep -q 'linux-python/bin/python3.13 -E -s -B .*backend/host.py' "$OUT/processes.txt"; then
  echo "The backend host is running on Dervo's own Python."
else
  echo "No backend host on Dervo's own Python."; status=1
fi
if [ -f "$DATA/conversations.sqlite3" ]; then
  echo "Its data is in $DATA."
else
  echo "No conversations database in $DATA."; status=1
fi
colours=$(convert "$OUT/window-45s.png" -resize 25% -format %k info: 2>/dev/null || echo 0)
if [ "${colours:-0}" -gt 50 ]; then
  echo "The window drew $colours colours."
else
  echo "The window is blank ($colours colours)."; status=1
fi

kill "$APP" 2>/dev/null
sleep 3
kill -9 "$APP" 2>/dev/null
pkill -f 'WebKit(Web|Network)Process' 2>/dev/null
exit $status
