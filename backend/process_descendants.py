"""Remember owned descendants before a provider exits, including separate command sessions."""
import os
import signal
import subprocess


def descendants(pid):
    from coding_integration import process_identity
    try:
        result = subprocess.run(['/bin/ps', '-axo', 'pid=,ppid='], capture_output=True,
                                text=True, timeout=1, check=True)
        parents = dict(tuple(map(int, line.split())) for line in result.stdout.splitlines())
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    owned, pending = {}, {pid}
    while pending:
        children = {child for child, parent in parents.items() if parent in pending and child not in owned}
        pending = set()
        for child in children:
            live = process_identity(child)
            if live and live['ppid'] == parents[child]:
                owned[child] = live
                pending.add(child)
    return owned


def stop_descendants(owned):
    from coding_integration import process_identity
    for pid, saved in reversed(list(owned.items())):
        live = process_identity(pid)
        # A reused pid must never turn a remembered child into permission to stop another app.
        if not live or (live['start'], live['exe']) != (saved['start'], saved['exe']):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
