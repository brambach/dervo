"""Markdown tables from a run's results.json and real-calls.jsonl, for notes/smartness/baseline.md.

    python3 -m evals.report <out-dir>
"""
import json
import sys
from collections import Counter
from pathlib import Path

from evals.case import SYSTEMS


def load(out):
    out = Path(out)
    results = json.loads((out / 'results.json').read_text())
    log = out / 'real-calls.jsonl'
    calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()] if log.exists() else []
    return results, calls


def cost(calls):
    started = [c for c in calls if c.get('state') == 'started']
    ended = {c['id']: c for c in calls if c.get('state') == 'ended'}
    refused = [c for c in calls if c.get('state') == 'refused']
    by_model = Counter(f"{c['provider']}:{c['model']}" for c in started)
    seconds = sum(ended[c['id']].get('seconds', 0) for c in started if c['id'] in ended)
    return dict(calls=len(started), refused=len(refused), minutes=round(seconds / 60, 1), by_model=dict(by_model),
                slowest=max((ended[c['id']].get('seconds', 0), c['case']) for c in started if c['id'] in ended) if ended else (0, None))


def markdown(results, calls):
    lines = []
    first = [r for r in results if not r.get('rerun')]
    reruns = [r for r in results if r.get('rerun')]
    lines.append('| System | Passed | Scored | Expected to fail today | Of which failed | Skipped or errored | Real calls |')
    lines.append('|---|---|---|---|---|---|---|')
    for system in SYSTEMS:
        mine = [r for r in first if r['system'] == system]
        scored = [r for r in mine if r['status'] in ('pass', 'fail')]
        expected = [r for r in mine if r['expect_fail']]
        lines.append(f"| {system} | {sum(1 for r in scored if r['status'] == 'pass')} | {len(scored)} | {len(expected)} | "
                     f"{sum(1 for r in expected if r['status'] == 'fail')} | {sum(1 for r in mine if r['status'] in ('skip', 'error'))} | "
                     f"{sum(r.get('calls', 0) for r in mine)} |")
    lines.append('')
    for system in SYSTEMS:
        mine = [r for r in first if r['system'] == system]
        if not mine:
            continue
        lines.append(f'### {system}')
        lines.append('')
        lines.append('| Case | Result | Reason | Model | Source |')
        lines.append('|---|---|---|---|---|')
        for r in mine:
            flag = ' (expected today)' if r['expect_fail'] and r['status'] == 'fail' else (' (review said it would fail today)' if r['expect_fail'] and r['status'] == 'pass' else '')
            model = ', '.join(m.split(':', 1)[1] for m in r.get('models') or []) or ('none' if r['provider'] == 'none' else 'fake')
            reason = (r['reason'] or '').replace('|', '\\|').replace('\n', ' ')
            if len(reason) > 140:
                reason = reason[:137] + '...'
            lines.append(f"| {r['id']} | {r['status']}{flag} | {reason} | {model} | {r.get('source', '')} |")
        lines.append('')
    if reruns:
        lines.append('### Flakiness: failing model cases re-run once')
        lines.append('')
        lines.append('| Case | First run | Re-run | Re-run reason |')
        lines.append('|---|---|---|---|')
        for r in reruns:
            original = next((o for o in first if o['id'] == r['id']), {})
            reason = (r['reason'] or '').replace('|', '\\|')[:120]
            lines.append(f"| {r['id']} | {original.get('status')} | {r['status']} | {reason} |")
        lines.append('')
    c = cost(calls)
    lines.append('### Cost')
    lines.append('')
    lines.append(f"- Real model calls: {c['calls']} ({c['refused']} refused by the cap), {c['minutes']} model minutes in total.")
    lines.append('- By model: ' + ', '.join(f'{k} {v}' for k, v in sorted(c['by_model'].items())) + '.')
    if c['slowest'][1]:
        lines.append(f"- Slowest call: {c['slowest'][0]} s in {c['slowest'][1]}.")
    wall = round(sum(r.get('seconds', 0) for r in results) / 60, 1)
    lines.append(f'- Wall clock for the cases: {wall} minutes.')
    return '\n'.join(lines)


if __name__ == '__main__':
    results, calls = load(sys.argv[1])
    print(markdown(results, calls))
