"""Time a test host's first calls and show where its threads spent that time, for a runner nobody can log in to.

    python host-call-sample.py

Sets up a project the way test_run_path does (headless_driver.claude_project), prints each call's seconds, then the
functions headless_driver.sample saw most often on the host's threads. The Windows build runs it because
coding_permission_grant took 5 to 6 seconds there (run 37856853359) and under half a second on Linux. No account, no
model call.
"""
import os
from pathlib import Path
import sys
import tempfile
import time

BACKEND = Path(__file__).resolve().parents[1] / 'backend'
sys.path.insert(0, str(BACKEND))


def main():
    from headless_driver import HostDriver, make_project
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
        folder = Path(folder).resolve()
        samples = folder / 'samples.txt'
        os.environ['DERVO_HOST_SAMPLE'] = str(samples)
        started = time.perf_counter()
        host = HostDriver(folder / 'data', slow_limit=60).start()
        print(f'start and usage_sharing_set: {time.perf_counter() - started:.2f} s', flush=True)
        raw = host.raw

        def timed(operation, args):
            began = time.perf_counter()
            try:
                return raw(operation, args)
            finally:
                print(f'{operation}: {time.perf_counter() - began:.2f} s', flush=True)
        host.raw = timed
        try:
            ids = host.claude_project(make_project(folder / 'proj'))
            host.call('coding_permission', **ids.scope)
            time.sleep(2.5)  # one more write of the samples
        finally:
            host.close()
        print(samples.read_text() if samples.exists() else 'No samples were written.')


if __name__ == '__main__':
    main()
