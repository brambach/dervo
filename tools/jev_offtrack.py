"""Does Jev know when a run went off the rails? The experiment the paid tier rests on.

Bryce, 20 Sep 2026: the filing eval said Jev is strong at semantic categorisation and weaker than the rules at
intent judgments they were tuned on. Off-track detection is the semantic kind, so the signal is mildly positive,
but nothing here is known until it runs. This is the only measurement that decides whether there is a paid tier:
if Jev can separate runs that stayed on task from runs that wandered, early run-killing works and it is worth
money. If it can't, filing and triage are nice small features and there is no brain to sell.

Two steps, on purpose, because step one reads your real project work and step two would send it to TypeSafe.

    python3 tools/jev_offtrack.py --extract          # local only, nothing leaves this machine
    open /tmp/.../jev-offtrack.jsonl                 # read it, set "label" on each: "on" or "off"
    TYPESAFE_API_KEY=$(pbpaste) python3 tools/jev_offtrack.py --run

``--extract`` reads ``conversations.sqlite3`` read-only through a temporary copy, never the live file, and writes
one line per candidate run. It labels nothing. The proxy signals it prints (a failed check, a wide file spread, a
run that ended without completing) are there to make your twenty minutes of labelling faster, not to stand in for
you. A proxy label would measure whether Jev agrees with a heuristic, which is not the question.

``--run`` sends the state below to ``api.typesafe.ai`` for each labelled run: what you asked, the tools used and
the paths touched, and the file names changed. Not file contents, not diffs, not message bodies beyond the first
request. Read a few lines of the jsonl before you run it and decide whether you're happy with that.
"""
import argparse
import json
import os
import shutil
import sqlite3
import statistics
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STORE = Path.home() / 'Library/Application Support/local.dervo.connected/conversations.sqlite3'
OUT = Path(os.environ.get('JEV_OUT') or Path(__file__).resolve().parent.parent / 'evidence/jev') / 'offtrack.jsonl'
URL = 'https://api.typesafe.ai/v1/systemone'
MODEL = 'jev-latest'
INPUT_PRICE = 0.042 / 1_000_000
STEP_LIMIT = 24  # actions carried into the state; a long run's tail says as much as its middle
PATH_LIMIT = 12


# ---------------------------------------------------------------------------- extract

def read_only(path):
    """A copy, because the app may hold the live file and this must never write to it."""
    if not path.exists():
        raise SystemExit(f'No store at {path}. Open Dervo once, or set the path in this file.')
    copy = Path(tempfile.mkdtemp()) / 'conv.sqlite3'
    shutil.copy2(path, copy)
    db = sqlite3.connect(f'file:{copy}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def first_request(task):
    for message in task.get('messages') or []:
        if isinstance(message, dict) and message.get('role') == 'user' and str(message.get('text', '')).strip():
            return str(message['text']).strip()
    return None


def actions(task, audit):
    """What the run did, in order: the tool and the file it touched. Paths only, never contents."""
    steps = []
    for token in task.get('tokens') or []:
        if isinstance(token, dict) and token.get('tool'):
            steps.append(dict(tool=str(token['tool']), path=_short(token.get('path'))))
    for row in audit:
        try:
            document = json.loads(row['document'])
        except Exception:
            continue
        if isinstance(document, dict) and document.get('tool'):
            steps.append(dict(tool=str(document['tool']), path=_short(document.get('path') or document.get('file'))))
    return steps


def _short(path):
    if not isinstance(path, str) or not path.strip():
        return None
    return path.replace('\\', '/').strip()[-72:]


# A run that never reached a completed state carries no ``completion.changed_files``, and on 20 Sep that was all
# 24 candidates: they ended disconnected, blocked or ready_for_review. The files an agent touched are in the step
# paths instead, so take them from there and fall back to completion when it has them.
WRITING_TOOLS = ('edit', 'write', 'create', 'notebookedit', 'multiedit')


def written_paths(steps):
    seen = []
    for step in steps:
        tool = str(step.get('tool') or '').rsplit(':', 1)[-1].lower()
        if step.get('path') and any(w in tool for w in WRITING_TOOLS) and step['path'] not in seen:
            seen.append(step['path'])
    return seen


# A request with no task in it ("try again", "pick up where we left off") can't be judged for drift: there is
# nothing stated to drift from. Judging those would repeat the follow_up mistake from the first eval.
VAGUE = ('try again', 'pick up where we left off', 'continue', 'carry on', 'keep going', 'go on', 'again',
         'resume', 'yes', 'ok', 'okay', 'do it', 'go ahead')


def judgeable(asked):
    flat = ' '.join(asked.lower().split()).strip(' .!?')
    return len(flat.split()) >= 8 and flat not in VAGUE and not any(flat.startswith(v + ' ') and len(flat.split()) < 12
                                                                    for v in VAGUE)


def candidate(task_id, task, audit, end_state):
    asked = first_request(task)
    steps = actions(task, audit)
    if not asked or len(steps) < 3 or not judgeable(asked):
        return None  # no request, barely started, or nothing stated to drift from
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    changed = [_short(f) for f in (completion.get('changed_files') or []) if isinstance(f, str)]
    changed = changed or written_paths(steps)
    exit_status = completion.get('test_exit_status')
    return dict(
        task_id=task_id,
        label=None,  # you set this: "on" or "off"
        asked=asked[:600],
        steps=steps[-STEP_LIMIT:],
        step_count=len(steps),
        files_changed=changed[:PATH_LIMIT],
        file_count=len(changed),
        signals=dict(
            check_failed=bool(isinstance(exit_status, int) and exit_status != 0),
            never_completed=not completion.get('at'),
            end_state=end_state,
            distinct_dirs=len({(f or '').rsplit('/', 1)[0] for f in changed if f}),
        ),
    )


def extract(args):
    db = read_only(STORE)
    ends = {r['task_id']: r['state'] for r in db.execute('SELECT task_id, state FROM conversation_task_end')}
    audits = {}
    for row in db.execute('SELECT task_id, document FROM coding_tool_audit'):
        audits.setdefault(row['task_id'], []).append(row)

    found = []
    for row in db.execute('SELECT id, document FROM coding_tasks'):
        try:
            task = json.loads(row['document'])
        except Exception:
            continue
        if isinstance(task, dict):
            item = candidate(row['id'], task, audits.get(row['id'], []), ends.get(row['id']))
            if item:
                found.append(item)

    # Interesting first: a failed check, a run that never completed, or work spread over several directories are
    # the places a drift is most likely to be found. You still decide what each one actually was.
    def suspicion(item):
        s = item['signals']
        return (s['check_failed'], s['never_completed'], s['distinct_dirs'] >= 3, item['step_count'])

    found.sort(key=suspicion, reverse=True)
    picked = found[:args.limit]
    # Labels are the expensive part of this: they cost a person twenty minutes of reading and can't be regenerated.
    # A re-extract keeps any that are already there, matched on task_id.
    if OUT.exists():
        known = {i['task_id']: i.get('label') for i in
                 (json.loads(line) for line in OUT.read_text().splitlines() if line.strip())}
        kept = 0
        for item in picked:
            if known.get(item['task_id']):
                item['label'] = known[item['task_id']]
                kept += 1
        if kept:
            print(f'kept {kept} existing labels\n')
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(json.dumps(item) for item in picked) + '\n')

    print(f'{len(found)} runs with a request and 3+ steps. Wrote the top {len(picked)} to:\n  {OUT}\n')
    print('  #  steps  files  dirs  check  ended            asked')
    for n, item in enumerate(picked):
        s = item['signals']
        print(f'{n:>3}  {item["step_count"]:>5}  {item["file_count"]:>5}  {s["distinct_dirs"]:>4}  '
              f'{"FAIL" if s["check_failed"] else "  ok":>5}  {str(s["end_state"] or "-")[:14]:<14}  '
              f'{item["asked"][:64].replace(chr(10), " ")}')
    print(f'\nNow set "label" on each line to "on" or "off" and run --run.')
    print('"off" means the run moved onto a different problem from what was asked. A run that tried and failed at')
    print('the right problem is "on": that is the distinction the feature has to make, and the harder one.')


# ---------------------------------------------------------------------------- ask

QUESTIONS = {
    'still_related': dict(
        type='noul',
        instructions=('`steps` are the actions a coding agent took, in order, while working on `asked`. Is the '
                      'most recent action in `steps` part of doing what `asked` describes? An action can be '
                      'preparatory, such as reading a file or running a check, and still be part of the work.'),
        criteria={'true': 'The latest action serves the request in `asked`.',
                  'false': 'The latest action serves some other problem.'}),
    'drifted': dict(
        type='noul',
        instructions=('Taking `steps` and `files_changed` as a whole, has this run moved onto a different problem '
                      'from the one described in `asked`? A run that is struggling, retrying or failing at the '
                      'right problem has not drifted. A run that fixed something else it noticed on the way has.'),
        criteria={'true': 'The work has moved to a different problem.',
                  'false': 'The work is still on the problem in `asked`, however badly it is going.'}),
    'scope_creep': dict(
        type='score',
        instructions='How far has the work in `files_changed` expanded beyond what `asked` needs?',
        criteria=['Only the files the request obviously requires.',
                  'Those files plus one or two neighbours that a careful change would touch.',
                  'Several extra files in the same area, beyond what the request needs.',
                  'Work across unrelated parts of the codebase.']),
    'worth_stopping': dict(
        type='noul',
        instructions=('Would a developer who asked for `asked` want this run stopped now and handed back to them, '
                      'rather than left to continue? Judge from `steps` and `files_changed`.')),
}


def ask(key, state, latencies, usage):
    body = json.dumps(dict(state=state, model=MODEL, questions=QUESTIONS)).encode()
    request = urllib.request.Request(URL, data=body, method='POST', headers={
        'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    for attempt in range(4):
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                answer = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (429, 529) and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise SystemExit(f'Jev returned {error.code}: {error.read().decode(errors="replace")[:400]}')
        latencies.append((time.monotonic() - started) * 1000)
        usage.append((answer.get('usage') or {}).get('input_tokens') or 0)
        return answer
    raise SystemExit('Jev kept returning 429/529')


def separation(name, on, off):
    """Whether the two labelled groups actually come apart on this question. The only thing that matters."""
    if not on or not off:
        return f'  {name}: not enough of both labels'
    gap = statistics.mean(off) - statistics.mean(on)
    overlap = sum(1 for a in on for b in off if b <= a) / (len(on) * len(off))
    return (f'  {name:<14} on-task mean {statistics.mean(on):.3f}   off-task mean {statistics.mean(off):.3f}   '
            f'gap {gap:+.3f}   overlap {overlap:.0%}')


def run(args):
    key = os.environ.get('TYPESAFE_API_KEY')
    if not key:
        raise SystemExit('Set TYPESAFE_API_KEY (try: TYPESAFE_API_KEY=$(pbpaste) ...)')
    if not OUT.exists():
        raise SystemExit(f'No {OUT}. Run --extract first.')

    items = [json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    labelled = [i for i in items if i.get('label') in ('on', 'off')]
    if len(labelled) < 6:
        raise SystemExit(f'Only {len(labelled)} of {len(items)} lines are labelled. Set "label" to "on" or "off".')
    counts = Counter(i['label'] for i in labelled)
    print(f'{len(labelled)} labelled runs: {counts["on"]} on-task, {counts["off"]} off-task\n')

    latencies, usage = [], []
    states = [dict(asked=i['asked'], steps=i['steps'], files_changed=i['files_changed']) for i in labelled]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        answers = list(pool.map(lambda s: ask(key, s, latencies, usage), states))

    by_question = {name: dict(on=[], off=[]) for name in QUESTIONS}
    rows = []
    for item, answer in zip(labelled, answers):
        got = answer['answers']
        values = {}
        for name in QUESTIONS:
            a = got[name]
            values[name] = a['noul'] if a.get('type') == 'noul' or 'noul' in a else a.get('score')
            by_question[name][item['label']].append(values[name])
        rows.append((item, values))

    print('Separation between your two groups, per question:\n')
    for name in QUESTIONS:
        print(separation(name, by_question[name]['on'], by_question[name]['off']))

    print('\nPer run (label / drifted / worth_stopping / scope):')
    for item, values in sorted(rows, key=lambda r: r[1]['drifted'] or 0, reverse=True):
        print(f'  {item["label"]:<4} {values["drifted"]:.3f}  {values["worth_stopping"]:.3f}  '
              f'{(values["scope_creep"] or 0):.2f}   {item["asked"][:56].replace(chr(10), " ")}')

    ordered = sorted(latencies)
    print(f'\n{len(latencies)} calls, p50 {statistics.median(ordered):.0f} ms, '
          f'{sum(usage)} input tokens, ${sum(usage) * INPUT_PRICE:.5f}')
    print('\nRead the gap and the overlap, not the accuracy. A question whose off-task mean sits well above its')
    print('on-task mean with little overlap is one you can put a threshold on. Anything else is noise.')


def review(args):
    """Every candidate in full, so labelling is reading rather than squinting at JSON. Local, sends nothing."""
    if not OUT.exists():
        raise SystemExit(f'No {OUT}. Run --extract first.')
    items = [json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    for n, item in enumerate(items):
        runs = []
        for step in item['steps']:
            tool = str(step['tool']).rsplit(':', 1)[-1]
            name = (step.get('path') or '').rsplit('/', 1)[-1]
            token = f'{tool}({name})' if name else tool
            if runs and runs[-1][0] == token:
                runs[-1][1] += 1
            else:
                runs.append([token, 1])
        trail = ' -> '.join(t if c == 1 else f'{t} x{c}' for t, c in runs)
        print(f'\n{"=" * 100}\n[{n}]  {item["step_count"]} steps, {item["file_count"]} files, '
              f'ended {item["signals"]["end_state"]}, label={item["label"]}')
        print(f'ASKED: {item["asked"][:400]}')
        print(f'FILES: {", ".join(f.rsplit("/", 1)[-1] for f in item["files_changed"]) or "none"}')
        print(f'DID:   {trail[:700]}')


def label(args):
    """Apply labels given as 0=on,1=off,... so they can be set from a conversation without hand-editing JSON."""
    if not OUT.exists():
        raise SystemExit(f'No {OUT}. Run --extract first.')
    items = [json.loads(line) for line in OUT.read_text().splitlines() if line.strip()]
    for pair in args.label.split(','):
        index, _, value = pair.strip().partition('=')
        value = value.strip().lower()
        if value not in ('on', 'off', 'skip'):
            raise SystemExit(f'Bad label {pair!r}: use on, off or skip.')
        items[int(index)]['label'] = None if value == 'skip' else value
    OUT.write_text('\n'.join(json.dumps(i) for i in items) + '\n')
    counts = Counter(i['label'] for i in items)
    print(f'{counts.get("on", 0)} on, {counts.get("off", 0)} off, {counts.get(None, 0)} unlabelled')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--extract', action='store_true', help='read the store, write candidates, send nothing')
    parser.add_argument('--review', action='store_true', help='print every candidate in full, sends nothing')
    parser.add_argument('--label', help='apply labels, e.g. "0=off,1=on,2=skip"')
    parser.add_argument('--run', action='store_true', help='send the labelled runs to Jev')
    parser.add_argument('--limit', type=int, default=24, help='candidates to write out')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    if args.extract:
        extract(args)
    elif args.review:
        review(args)
    elif args.label:
        label(args)
    elif args.run:
        run(args)
    else:
        parser.error('Pass --extract, --review, --label or --run.')


if __name__ == '__main__':
    main()
