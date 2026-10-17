"""Run the backend unit tests one module at a time, each in its own process with a time cap, and write what happened.

    python backend-tests-by-module.py [--known FILE] [--shard K/N] OUT_FOLDER [MODULE ...]

Writes OUT_FOLDER/backend-tests.log (every module's output, in order) and OUT_FOLDER/backend-tests.json (one entry per
module: exit code, tests run, failures, errors, skipped, and whether it hit the cap), and prints a short summary. With no
MODULE it runs every backend/test_*.py. BACKEND_TEST_CAP sets the cap in seconds (300) and BACKEND_TEST_WORKERS how
many modules run at once (1). A module that hangs is stopped at the cap and recorded as timed out, so one hang
never hides the rest.

The Windows build workflow runs this on the runner (b16/windows): its JSON says module by module how far the Mac
backend's own tests get on Windows. Run it on a Mac too for the same numbers to compare. Without --known it always
exits 0: it reports. The modules scripts/gate.sh leaves out on purpose (SKIP) aren't run.

With --known FILE (.github/known-test-failures.json) it's a check: FILE lists, per platform (sys.platform), the modules
known to fail there and why. It exits 1 when any other module fails or times out, so a new failure turns the build red,
and names each listed module that now passes, so the list only shrinks.

With --shard K/N it runs every Nth module from the Kth, so N jobs on N runners share one run.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

CAP_SECONDS = int(os.environ.get('BACKEND_TEST_CAP', '300'))  # seconds one module may take
WORKERS = int(os.environ.get('BACKEND_TEST_WORKERS', '1'))  # modules at once, each in its own process
COUNTS = re.compile(r'^Ran (\d+) tests? in', re.M)
OUTCOME = re.compile(r'^(OK|FAILED)(?: \((.*)\))?\s*$', re.M)
# The same modules scripts/gate.sh leaves out (SKIP_BACKEND), for the same reasons.
SKIP = {'test_evals': 'calls real models', 'test_rust_checks': 'needs a trusted Rust toolchain',
        'test_rust_execution': 'needs a trusted Rust toolchain'}


def stop_tree(process):
    """End a module's test process and everything it started."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(process.pid)], capture_output=True)
    else:
        try:
            os.killpg(process.pid, 9)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()  # taskkill didn't manage: the module's own process at least
        process.wait(timeout=30)


def run(module, backend):
    # The output goes to a file, not a pipe: a process the tests left running would hold a pipe open, and on Windows
    # subprocess.run then waits on it forever after its own timeout (CPython gh-81605).
    started = time.monotonic()
    # Windows: each module gets its own hidden console. A test that sends Ctrl+C or Ctrl+Break through a shared console
    # reaches every process on it, which ended this runner partway twice (KeyboardInterrupt, runs 37856853359 and
    # 37867811889) and could end the modules running beside it.
    group = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {'start_new_session': True}
    with tempfile.TemporaryFile() as sink:
        process = subprocess.Popen([sys.executable, '-B', 'release_checks.py', module], cwd=backend, stdout=sink,
                                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **group)
        try:
            code, timed_out = process.wait(timeout=CAP_SECONDS), False
        except subprocess.TimeoutExpired:
            stop_tree(process)
            code, timed_out = None, True
        sink.seek(0)
        output = sink.read().decode(errors='replace')
    entry = dict(module=module, exit=code, timed_out=timed_out, seconds=round(time.monotonic() - started, 1),
                 ran=0, failures=0, errors=0, skipped=0)
    ran = COUNTS.findall(output)
    if ran:
        entry['ran'] = int(ran[-1])
    outcome = OUTCOME.findall(output)
    if outcome and outcome[-1][1]:
        for part in outcome[-1][1].split(','):
            name, _, value = part.strip().partition('=')
            if name in ('failures', 'errors', 'skipped') and value.isdigit():
                entry[name] = int(value)
    if not ran and not timed_out:
        entry['errors'] = max(entry['errors'], 1)  # it didn't even import
    return entry, output


CAUSE = re.compile(r'^(?:[\w.]+(?:Error|Exception|Exit|Rejected|SlowCall|Unavailable|Blocked)|AssertionError)\b.*', re.M)


def cause(output):
    """The most common error line in a module's output, so a known-failures reason can be written from the job log
    alone (its files can't always be kept)."""
    found = CAUSE.findall(output)
    if not found:
        return (output.strip().splitlines() or [''])[-1][:200]
    return max(set(found), key=found.count)[:200]


def failure_excerpt(output, limit=120):
    """Each FAIL or ERROR block of a unittest run, up to ``limit`` lines, or the end of the output if it has none."""
    lines = output.splitlines()
    picked, keep = [], False
    for line in lines:
        if line.startswith(('FAIL:', 'ERROR:')):
            keep = True
        elif line.startswith(('Slowest test durations', 'Ran ')):
            keep = False
        if keep:
            picked.append(line)
    return '\n'.join((picked or lines[-40:])[:limit])


def main(out, modules, known=None, shard=None):
    backend = Path(__file__).resolve().parents[1] / 'backend'
    modules = modules or sorted(p.stem for p in backend.glob('test_*.py') if p.stem not in SKIP)
    if shard:
        modules = modules[shard[0] - 1::shard[1]]
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    results, outputs = [], {}
    from concurrent.futures import ThreadPoolExecutor
    with (out / 'backend-tests.log').open('w', encoding='utf-8') as log, ThreadPoolExecutor(max(1, WORKERS)) as pool:
        # Written as each finishes, so a job that's stopped partway still leaves every finished module in the log.
        for entry, output in pool.map(lambda module: run(module, backend), modules):
            print(f'{entry["module"]}: ' + ('timed out' if entry['timed_out'] else 'ok' if entry['exit'] == 0 else
                  f'exit {entry["exit"]}') + f' in {entry["seconds"]} s', flush=True)
            results.append(entry)
            outputs[entry['module']] = output
            log.write(f'===== {entry["module"]} exit={entry["exit"]} timed_out={entry["timed_out"]}\n{output}\n')
            log.flush()
            (out / 'backend-tests.json').write_text(json.dumps(results, indent=1) + '\n', encoding='utf-8')
    (out / 'backend-tests.json').write_text(json.dumps(results, indent=1) + '\n', encoding='utf-8')
    total = {key: sum(r[key] for r in results) for key in ('ran', 'failures', 'errors', 'skipped')}
    clean = [r['module'] for r in results if r['exit'] == 0]
    print(f'{len(results)} modules on {sys.platform}: {len(clean)} pass, '
          f'{sum(r["timed_out"] for r in results)} timed out; tests ran {total["ran"]}, failures {total["failures"]}, '
          f'errors {total["errors"]}, skipped {total["skipped"]}')
    if known is None:
        return 0
    listed = json.loads(Path(known).read_text(encoding='utf-8')).get(sys.platform, {})
    failed = [r['module'] for r in results if r['exit'] != 0]
    new = [m for m in failed if m not in listed]
    fixed = [r['module'] for r in results if r['exit'] == 0 and r['module'] in listed]
    for module in fixed:
        print(f'{module} now passes on {sys.platform}: remove it from {known}.')
    for module in failed:
        print(f'{module} fails' + (f' (known: {listed[module]})' if module in listed else ', and it isn\'t a known failure')
              + f'. Its most common error: {cause(outputs.get(module, ""))}')
    if new:
        # The failures themselves, in the job's own log: a run's files can't always be kept (artifact storage).
        for module in new:
            print(f'::group::{module}')
            print(failure_excerpt(outputs.get(module, '')))
            print('::endgroup::')
        print(f'{len(new)} module(s) failed that {known} doesn\'t list for {sys.platform}: ' + ', '.join(new))
        return 1
    return 0


if __name__ == '__main__':
    # A test's output can hold any character, and a Windows console's code page can't print most of them: one such
    # excerpt ended a run's report partway (run 37871829356).
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.platform == 'win32':
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # and should a console event still get here, the run goes on
    args = sys.argv[1:]
    known, shard = None, None
    while args[:1] in (['--known'], ['--shard']) and len(args) >= 2:
        if args[0] == '--known':
            known = args[1]
        else:
            index, _, count = args[1].partition('/')
            shard = int(index), int(count)
            if not 1 <= shard[0] <= shard[1]:
                raise SystemExit('--shard is K/N with 1 <= K <= N.')
        args = args[2:]
    if not args:
        raise SystemExit(__doc__)
    raise SystemExit(main(args[0], args[1:], known, shard))
