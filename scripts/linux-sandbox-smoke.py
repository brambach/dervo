"""Start Dervo's check sandbox once (bubblewrap with its seccomp filter, backend/linux_confinement.py) and say what
happened, for the Linux install job. Run it on the installed app's own Python with the installed backend:

    PYTHON -E -s -B scripts/linux-sandbox-smoke.py BACKEND [--expect started|refused|either]

It prints "started" when a sandboxed /bin/true ran, or "refused" when Linux refused the user namespace with the message
Dervo shows for that (an AppImage on Ubuntu 23.10 or later, which no AppArmor profile can name). Anything else fails.
"""
import os
import subprocess
import sys
import tempfile

backend, expect = sys.argv[1], (sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == '--expect' else 'either')
sys.path.insert(0, backend)
import linux_confinement  # noqa: E402

with tempfile.TemporaryDirectory() as root:
    read, write = os.pipe()
    os.write(write, linux_confinement.seccomp_filter())
    os.close(write)
    argv = linux_confinement.command(['/bin/true'], root, os.path.dirname(os.path.realpath(sys.executable)), read)
    done = subprocess.run(argv, pass_fds=(read,), capture_output=True, text=True, timeout=30)
    os.close(read)
if done.returncode == 0:
    result = 'started'
elif linux_confinement.namespace_refused(done.stderr):
    result = 'refused'
    print('bubblewrap said: ' + done.stderr.strip())
    print('Dervo would say: ' + linux_confinement.NAMESPACE_REFUSED)
else:
    print(f'bubblewrap failed some other way ({done.returncode}): {done.stderr.strip()}')
    sys.exit(1)
print(result)
sys.exit(0 if expect in ('either', result) else 1)
