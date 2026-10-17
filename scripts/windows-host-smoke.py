"""Start Dervo's backend the way the app does: the version probe it runs first, then the host, which is asked for its
status and shut down.

    python windows-host-smoke.py PYTHON DATA_DIR

PYTHON is the interpreter the app would bundle, DATA_DIR an empty scratch folder. The host gets the same flags and the
same cleared environment as src/launch.rs gives it (on Windows, only the folders Python needs to find itself, the home
folder and temp: WINDOWS_ENVIRONMENT in launch.rs). It prints both answers and their stderr, and exits 0 only when the
probe found no other version selected (version_switch.rs, redirect_selected) and the status request came back with a
result.

The Windows build workflow runs this on the runner before packaging (b16/windows milestone 2). Its data goes in
DATA_DIR, and it opens no project.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# The same names as WINDOWS_ENVIRONMENT in src/launch.rs. None of them is a secret. A Mac passes nothing.
WINDOWS_ENVIRONMENT = ('SYSTEMROOT', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP')
REQUESTS = b'{"operation":"status","args":{}}\n{"operation":"_shutdown","args":{}}\n'


def main(python, data):
    backend = Path(__file__).resolve().parents[1] / 'backend'
    names = WINDOWS_ENVIRONMENT if os.name == 'nt' else ()
    env = {name: os.environ[name] for name in names if name in os.environ}
    # The app sends its own exe here. A plain path beside the data folder stands in for it: the probe refuses links.
    current = str(Path(data).parent / 'Dervo Local.exe')
    probe = json.dumps({'workspace': data, 'currentExecutable': current, 'dataOverride': False}).encode() + b'\n'
    process = subprocess.run([python, '-E', '-s', '-B', str(backend / 'release_selection.py')], input=probe,
                             capture_output=True, env=env, timeout=60)
    print(f'version probe exit code: {process.returncode}')
    print(process.stdout.decode(errors='replace').rstrip())
    print(process.stderr.decode(errors='replace').rstrip())
    try:
        selected = json.loads(process.stdout)
    except ValueError:
        selected = None
    if selected != {'executable': None}:
        print('The version probe did not answer as a fresh install should, so the app would stop at its setup screen.')
        return 1
    # The whole exchange is timed: the host's start plus its first answer is what the app's 10 s limit per request sees
    # first (src/bridge.rs). A runner is a rough guide to a PC, not a measure of one.
    started = time.monotonic()
    process = subprocess.run([python, '-E', '-s', '-B', str(backend / 'host.py'), data], input=REQUESTS,
                             capture_output=True, env=env, timeout=120)
    took = time.monotonic() - started
    out, err = process.stdout.decode(errors='replace'), process.stderr.decode(errors='replace')
    print(f'host exit code: {process.returncode}')
    print(f'host start, status and shutdown took {took:.1f} s')
    print('--- host stdout ---')
    print(out.rstrip())
    print('--- host stderr ---')
    print(err.rstrip())
    lines = out.splitlines()
    try:
        answer = json.loads(lines[0]) if lines else {}
    except ValueError:
        answer = {}
    if 'result' not in answer:
        print('The host did not answer its first status request.')
        return 1
    print('The host started and answered status.')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
