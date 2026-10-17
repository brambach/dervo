"""Turn the host's decision log into new eval cases, so every real mistake becomes a test.

The host logs each thread_routing decision it applies, one JSON object per line, and fills in what happened next:

    {"id": "...", "at": 1800000000.0, "op": "route",
     "input": {"text": "...", "history": [{"text", "at", "thread_id"}], "threads": [spine records]},
     "decision": {route()'s answer}, "outcome": "kept" | "moved:<thread id>" | "moved:new" | "moved:home"}
    {"op": "name", "input": {"thread": {...}, "others": [...]}, "decision": {"title": ...}, "outcome": "kept" | "renamed:<title>"}
    {"op": "tidy", "input": {"threads": [...]}, "decision": {"thread_id": ..., "action": "archive"}, "outcome": "kept" | "unarchived"}

Only corrections become cases: a Move after a route, a rename after a name, an unarchive after a tidy. Each route case
is one stream holding the threads and Home history the router saw and the message, expecting where you moved it.

    python3 -m evals.routing.learn --log ~/Library/Application\\ Support/<Dervo>/routing-decisions.jsonl

writes evals/routing/streams_learned.json and learned_cases.json (both ignored by git: they hold your real messages and
thread names). The scorer replays them with the other suites. To keep a case for good, rewrite it without anything
private and move it into streams_dev.json or cases.py.
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _relative(threads, history, at):
    """Records with absolute times as the suite format's ``*_ago`` and Home history as ``home``."""
    out = []
    for thread in threads or []:
        if not isinstance(thread, dict) or not thread.get('id'):
            continue
        record = {k: v for k, v in thread.items() if k not in ('since', 'at', 'version', 'aliveAt')}
        for key, field in (('since', 'since_ago'), ('at', 'at_ago')):
            if isinstance(thread.get(key), (int, float)):
                record[field] = max(0.0, at - thread[key])
        out.append(record)
    home = [dict(text=h.get('text'), ago=max(0.0, at - h['at']) if isinstance(h.get('at'), (int, float)) else 60,
                 thread=h.get('thread_id')) for h in history or [] if isinstance(h, dict)]
    return out, home


def cases(entries):
    streams, names, tidies = [], [], []
    for entry in entries:
        outcome = str(entry.get('outcome') or '')
        given = entry.get('input') or {}
        at = entry.get('at') if isinstance(entry.get('at'), (int, float)) and not isinstance(entry.get('at'), bool) else None
        if at is None:
            continue  # without the time it was decided, every thread would replay as active "just now"
        if entry.get('op') == 'route' and outcome.startswith('moved:'):
            where = outcome.split(':', 1)[1]
            expect = 'new' if where == 'new' else 'home' if where == 'home' else f'existing:{where}'
            threads, home = _relative(given.get('threads'), given.get('history'), at)
            streams.append(dict(id=f"learned-{entry.get('id') or len(streams) + 1}",
                                about=f"Moved after the router said {(entry.get('decision') or {}).get('kind')}", threads=threads,
                                home=home, stream=[dict(text=given.get('text') or '', gap=0, expect=expect)]))
        elif entry.get('op') == 'name' and outcome.startswith('renamed:'):
            title = outcome.split(':', 1)[1]
            names.append(dict(thread=given.get('thread') or {}, others=given.get('others') or [],
                              rejected=(entry.get('decision') or {}).get('title'), wanted=title))
        elif entry.get('op') == 'tidy' and outcome == 'unarchived' and (entry.get('decision') or {}).get('action') == 'archive':
            threads, _ = _relative(given.get('threads'), [], at)
            tidies.append(dict(threads=threads, keep=(entry.get('decision') or {}).get('thread_id')))
    return streams, names, tidies


def read(path):
    entries = []
    for line in pathlib.Path(path).expanduser().read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut off by a crash
    return entries


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--log', required=True, help='the host decision log (JSON lines)')
    parser.add_argument('--out', default=str(HERE), help='folder for streams_learned.json and learned_cases.json')
    args = parser.parse_args(argv)
    streams, names, tidies = cases(read(args.log))
    out = pathlib.Path(args.out)
    (out / 'streams_learned.json').write_text(json.dumps(streams, indent=1))
    (out / 'learned_cases.json').write_text(json.dumps(dict(naming=names, tidy=tidies), indent=1))
    print(f'{len(streams)} routing, {len(names)} naming and {len(tidies)} cleanup cases from your corrections.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
