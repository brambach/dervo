"""Run the frontend tests and fail only on a test that isn't known to fail.

    python frontend-tests-known.py KNOWN_FILE [-- COMMAND ...]

COMMAND defaults to frontend's `pnpm test` run in frontend/ with the TAP reporter (Node 24's default is spec, even
into a pipe), two files at once, with a two-minute limit per test and
--test-force-exit: some test files leave timers running after their tests fail and would otherwise hold the run open.
On Linux a file whose process passes LIMIT_MB of memory is stopped; any of its tests not yet reported count as failed.

KNOWN_FILE (.github/known-frontend-failures.json) lists, per platform (sys.platform), the top-level test names known to
fail there and why. Like backend-tests-by-module.py --known, it exits 1 when any other test fails or the run ends
without a summary, and names each listed test that now passes, so the list only shrinks.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
TESTS = ['node', '--import', './test-loader.mjs', '--experimental-strip-types', '--test', '--test-reporter=tap',
         '--test-force-exit', '--test-timeout=120000', '--test-concurrency=2', 'src/**/*.test.mjs', 'scripts/*.test.mjs']
# A test file over this much memory is stopped; node reports whatever it hadn't finished as failed. importHistory.test.mjs
# grows past 13 GB after its last test has reported, on Node 22 and 24 alike (the others stay under 1 GB), and twice
# that shut the 16 GB runner down partway (runs 37867811892 and 37871829369) instead of failing one file.
LIMIT_MB = 4096
FAILED = re.compile(r'^not ok \d+ - (.*?)(?: # .*)?$')


def failures(output):
    """The top-level failing test names in node --test's TAP output (subtests are indented)."""
    return {match.group(1) for line in output.splitlines() if (match := FAILED.match(line))}


def watch(root, killed, every=1.0):
    """Linux: stop any process under ``root`` whose resident memory passes LIMIT_MB, noting its test file."""
    while True:
        try:
            os.kill(root, 0)
        except OSError:
            return
        children = {}
        for entry in os.scandir('/proc'):
            if not entry.name.isdigit():
                continue
            try:
                stat = Path(entry.path, 'stat').read_text()
                fields = stat[stat.rindex(')') + 2:].split()
                children.setdefault(int(fields[1]), []).append(int(entry.name))
            except (OSError, ValueError):
                continue
        pending, seen = [root], set()
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.add(pid)
            pending += children.get(pid, [])
            if pid == root:
                continue
            try:
                status = Path(f'/proc/{pid}/status').read_text()
                size = int(re.search(r'^VmRSS:\s+(\d+) kB', status, re.M).group(1)) // 1024
            except (OSError, AttributeError):
                continue
            if size > LIMIT_MB:
                try:
                    args = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
                    file = next((a.decode() for a in reversed(args) if a.endswith(b'.test.mjs')), f'process {pid}')
                    os.kill(pid, 9)
                    killed.append((file, size * 1024))
                except OSError:
                    pass
        time.sleep(every)


def summary(output):
    return re.search(r'^# (pass|fail) \d+$', output, re.MULTILINE) is not None


def main(argv):
    known_file, command = Path(argv[1]), argv[3:] if argv[2:3] == ['--'] else TESTS
    known = json.loads(known_file.read_text()).get(sys.platform, {})
    process = subprocess.Popen(command, cwd=ROOT / 'frontend', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    killed = []
    if sys.platform.startswith('linux'):
        threading.Thread(target=watch, args=(process.pid, killed), daemon=True).start()
    stdout, _ = process.communicate()
    result = subprocess.CompletedProcess(command, process.returncode, stdout, '')
    for file, size in killed:
        print(f'::warning::{file} was stopped at {size // 1024} MB, over the {LIMIT_MB} MB a test file may use.')
    output = result.stdout + result.stderr
    failed = failures(output)
    new = sorted(failed - set(known))
    for name in sorted(set(known) - failed):
        print(f'::notice::Listed as failing on {sys.platform} but passed: {name}. Remove it from {known_file.name}.')
    for name in sorted(failed & set(known)):
        print(f'Known failure: {name} ({known[name]})')
    for name in new:
        print(f'::error::New frontend test failure: {name}')
        start = output.find(f' - {name}\n')
        if start >= 0:
            print('::group::' + name)
            print('\n'.join(output[start:].splitlines()[1:60]))
            print('::endgroup::')
    if not summary(output):
        print(output[-4000:])
        print('::error::The frontend tests ended without a summary.')
        return 1
    print(f'{len(failed)} failing, {len(failed) - len(new)} of them known, {len(new)} new.')
    if result.returncode and not failed:
        print(output[-4000:])
        print(f'::error::The frontend tests exited {result.returncode} without a failing test.')
        return 1
    return 1 if new else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
