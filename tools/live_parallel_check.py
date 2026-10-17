"""Live check for parallel runs: N short real Claude Code runs at once on this Mac (Bryce, 23 Sep). It uses your own
signed-in Claude Code and its usage, sends only the one-line prompt below from an empty temporary folder, and never
touches Dervo, a project or its data. Run it yourself; nothing in Dervo runs it.

    python3 tools/live_parallel_check.py                   # 10 runs, Haiku 4.5
    python3 tools/live_parallel_check.py --runs 10 --model claude-sonnet-5

It records, like tools/load_parallel_runs.py: peak memory, CPU and processes for the runs (sampled every 250 ms), how
long each took, how each ended, and every rate-limit event or rate-limit error Claude Code reported. The result is
printed and saved to evidence/load-parallel-runs/live-<time>.json.
"""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = 'Reply with the single word: ok'


def claude_path():
    for candidate in ('~/.local/bin/claude', '/opt/homebrew/bin/claude', '/usr/local/bin/claude'):
        path = Path(os.path.expanduser(candidate))
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return shutil.which('claude')


def processes(pids):
    """(count, rss MB, cpu %) for these pids and everything below them."""
    out = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss=,pcpu='], capture_output=True, text=True).stdout
    rows = {}
    for line in out.splitlines():
        pid, ppid, rss, cpu = line.split()
        rows[int(pid)] = (int(ppid), int(rss), float(cpu))
    keep, grew = set(pids) & set(rows), True
    while grew:
        grew = False
        for pid, row in rows.items():
            if row[0] in keep and pid not in keep:
                keep.add(pid)
                grew = True
    return len(keep), sum(rows[p][1] for p in keep) / 1024, sum(rows[p][2] for p in keep)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--runs', type=int, default=10)
    parser.add_argument('--model', default='claude-haiku-4-5-20251001')
    parser.add_argument('--claude', default=None, help='Claude Code to use (default: the first of ~/.local/bin, Homebrew, /usr/local)')
    parser.add_argument('--out', type=Path, default=ROOT / 'evidence/load-parallel-runs')
    args = parser.parse_args()
    claude = args.claude or claude_path()
    if not claude:
        raise SystemExit('Claude Code wasn’t found. Install it or pass --claude.')
    folder = Path(tempfile.mkdtemp(prefix='dervo-live-parallel-'))
    command = [claude, '-p', PROMPT, '--model', args.model, '--output-format', 'stream-json', '--verbose',
               '--strict-mcp-config', '--max-turns', '1']
    started = time.monotonic()
    runs = []
    for index in range(args.runs):
        runs.append({'index': index + 1, 'process': subprocess.Popen(command, cwd=folder, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                                                     stderr=subprocess.PIPE, text=True), 'started': time.monotonic()})
    peak = {'processes': 0, 'rss_mb': 0.0, 'cpu_percent': 0.0}
    done = threading.Event()

    def sample():
        while not done.is_set():
            count, rss, cpu = processes([run['process'].pid for run in runs])
            peak.update(processes=max(peak['processes'], count), rss_mb=max(peak['rss_mb'], rss), cpu_percent=max(peak['cpu_percent'], cpu))
            done.wait(.25)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    for run in runs:
        out, err = run['process'].communicate(timeout=600)
        run['seconds'] = round(time.monotonic() - run['started'], 1)
        run['exit'] = run['process'].returncode
        events = []
        for line in out.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        result = next((event for event in events if event.get('type') == 'result'), {})
        run['ended'] = 'ok' if result.get('subtype') == 'success' and not result.get('is_error') else (result.get('subtype') or 'no result')
        run['answer'] = str(result.get('result') or '')[:80]
        run['rate_limit_events'] = [event.get('rate_limit_info') for event in events if event.get('type') == 'rate_limit_event']
        text = (out + err).lower()
        run['rate_limited'] = any(word in text for word in ('rate limit', 'rate_limit_error', 'overloaded', 'usage limit', '429'))
        run['error'] = err.strip()[-300:] if run['exit'] else ''
        del run['process'], run['started']
    done.set()
    sampler.join(timeout=2)
    shutil.rmtree(folder, ignore_errors=True)
    report = {
        'at': datetime.now().isoformat(timespec='seconds'), 'runs': args.runs, 'model': args.model, 'claude': claude,
        'claude_version': subprocess.run([claude, '--version'], capture_output=True, text=True).stdout.strip(),
        'wall_seconds': round(time.monotonic() - started, 1), 'peak': {key: round(value, 1) for key, value in peak.items()},
        'ok': sum(1 for run in runs if run['ended'] == 'ok'), 'rate_limited': sum(1 for run in runs if run['rate_limited']),
        'rate_limit_statuses': sorted({str((info or {}).get('status')) for run in runs for info in run['rate_limit_events']}),
        'each': runs,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f'live-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
    path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'each'}, indent=2))
    print(f'\n{report["ok"]} of {args.runs} finished, {report["rate_limited"]} hit a rate limit. Saved to {path}')
    return 0 if report['ok'] == args.runs and not report['rate_limited'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
