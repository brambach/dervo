"""Where the time goes between Enter and the reply, from the timing each run keeps (coding_integration, run['timing']).

usage: python3 tools/run_timings.py <path to Dervo's store database> [--last N]

Opens the database read only and prints medians and the worst case for each step, split by warm (started ahead of time,
warm_runs) and cold launches and by full access. Reads timestamps only, never a message's text."""
import json
import sqlite3
import statistics
import sys

STEPS = (('requested', 'launched', 'to launch'), ('launched', 'sent', 'launch to task sent'),
         ('sent', 'first_output', 'task sent to first words'), ('requested', 'first_output', 'Enter to first words'))


def runs(db, last):
    rows = db.execute("SELECT document FROM coding_tasks WHERE json_extract(document, '$.binding.provider')='claude' "
                      'ORDER BY rowid DESC LIMIT ?', (last,)).fetchall()
    for (document,) in rows:
        task = json.loads(document)
        for run in task.get('runs') or []:
            if isinstance(run.get('timing'), dict):
                yield bool(task.get('full_access')), run['timing']


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    last = int(sys.argv[sys.argv.index('--last') + 1]) if '--last' in sys.argv else 200
    db = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
    groups = {}
    for full, timing in runs(db, last):
        groups.setdefault((('warm' if timing.get('warm') else 'cold'), ('full access' if full else 'normal')), []).append(timing)
    if not groups:
        print('No runs with timing yet. Send a message in Dervo first.')
    for (kind, mode), timings in sorted(groups.items()):
        print(f'{kind}, {mode}: {len(timings)} runs')
        for start, end, label in STEPS:
            spans = [t[end] - t[start] for t in timings if start in t and end in t]
            if spans:
                print(f'  {label:<26} median {statistics.median(spans):6.2f} s   worst {max(spans):6.2f} s   ({len(spans)} runs)')


if __name__ == '__main__':
    main()
