"""Replay the routing suites through thread_routing and score them. No model, no host, no I/O beyond reading the suites.

    cd backend
    python3 -m evals.routing                 # every suite, a table, and each wrong route
    python3 -m evals.routing --suite dev     # one suite
    python3 -m evals.routing --json out.json # scores and every decision, for comparing two runs

A route is right when its kind and thread match the expectation. A wrong confident route (a misroute: the message
lands in the wrong thread, or starts a thread it shouldn't) costs more than ``unsure``, which only means the slower model
decides, so both are counted.
"""
import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

import thread_routing  # noqa: E402

SUITES = ('dev', 'heldout', 'test', 'learned')
START = 1_800_000_000.0  # a fixed clock, so a replay never depends on today


def load(suite):
    path = HERE / f'streams_{suite}.json'
    return json.loads(path.read_text()) if path.exists() else []


def thread_record(raw, now):
    record = dict(raw)
    for key, field in (('since_ago', 'since'), ('at_ago', 'at')):
        if key in record:
            record[field] = now - float(record.pop(key))
    record.setdefault('at', record.get('since', now - 3600))
    record.setdefault('since', record['at'])
    record['messages'] = list(record.get('messages') or [])
    return record


def accepted(expect):
    return [part.strip() for part in str(expect).split('|') if part.strip()]


def created_ref(target, created):
    """The thread an '@N' names: the Nth one this stream made (N from 1), or None."""
    number = target[1:]
    if not number.isdigit() or not 1 <= int(number) <= len(created):
        return None
    return created[int(number) - 1]


def judge(decision, expect, created):
    """'right', 'unsure' (deferred to the model when a route was expected) or 'wrong'."""
    options = accepted(expect)
    got = decision['kind']
    for option in options:
        kind, _, target = option.partition(':')
        if kind != got:
            continue
        if kind == 'existing':
            if target.startswith('@'):
                target = created_ref(target, created)
            if decision['thread_id'] == target:
                return 'right'
            continue
        return 'right'
    return 'unsure' if got == 'unsure' else 'wrong'


def replay(stream):
    now = START
    threads = [thread_record(raw, now) for raw in stream.get('threads') or []]
    history = [dict(text=h.get('text'), at=now - float(h.get('ago', 60)), thread_id=h.get('thread')) for h in stream.get('home') or []]
    created, results = [], []
    for number, message in enumerate(stream.get('stream') or []):
        now += float(message.get('gap', 5))
        began = time.perf_counter()
        decision = thread_routing.route(message['text'], history, threads, now)
        took = time.perf_counter() - began
        verdict = judge(decision, message['expect'], created)
        named = None
        if decision['kind'] == 'new':
            named = decision.get('name') or ''
            wanted = [w.casefold() for w in message.get('name_has') or []]
            # None: the privacy rules abstained (a key, a path, code), and the agent's title names it later.
            name_ok = None if not named else not wanted or any(w in named.casefold() for w in wanted)
        else:
            name_ok = None
        results.append(dict(stream=stream['id'], index=number, text=message['text'], expect=message['expect'],
                            kind=decision['kind'], thread_id=decision['thread_id'], name=named, confidence=decision['confidence'],
                            why=decision['why'], verdict=verdict, name_ok=name_ok, ms=round(took * 1000, 3)))
        # The world moves on the way Home would move it: the message lands where it was routed.
        target = decision['thread_id']
        if verdict == 'wrong':
            # The person taps Move: the message lands where it should have, so one mistake isn't scored twice.
            decision = dict(decision, kind='unsure')
        if decision['kind'] == 'new':
            target = f"{stream['id']}-new{len(created) + 1}"
            created.append(target)
            threads.append(dict(id=target, name=decision.get('name') or 'New thread', state='working', request=message['text'],
                                messages=[], since=now, at=now))
        elif decision['kind'] == 'unsure':
            # The model decides; the replay takes the expected route so the rest of the stream stays meaningful.
            first = (accepted(message['expect']) or ['unsure'])[0]
            kind, _, want = first.partition(':')
            if kind == 'new':
                target = f"{stream['id']}-new{len(created) + 1}"
                created.append(target)
                threads.append(dict(id=target, name=(message.get('name_has') or ['New'])[0].capitalize(), state='working',
                                    request=message['text'], messages=[], since=now, at=now))
            elif kind == 'existing':
                target = created_ref(want, created) if want.startswith('@') else want
            else:
                target = None
        if decision['kind'] == 'home':
            target = None  # the host records a message Home answered as going to no thread
        for thread in threads:
            if thread['id'] == target:
                thread['messages'].append(message['text'])
                thread['at'] = now
                if thread.get('archived'):
                    thread['archived'] = False
                    thread['state'] = 'working'
        history.append(dict(text=message['text'], at=now, thread_id=target))
    return results


def score(results):
    total = len(results)
    right = sum(r['verdict'] == 'right' for r in results)
    wrong = sum(r['verdict'] == 'wrong' for r in results)
    deferred = sum(r['verdict'] == 'unsure' for r in results)
    named = [r for r in results if r['name_ok'] is not None]
    routed = [r for r in results if r['kind'] != 'unsure']
    auto = [r for r in routed if r['confidence'] >= thread_routing.AUTO_ROUTE]
    suggested = [r for r in routed if thread_routing.SUGGEST <= r['confidence'] < thread_routing.AUTO_ROUTE]
    return dict(messages=total, accuracy=round(right / total, 4) if total else 1.0,
                misroutes=round(wrong / total, 4) if total else 0.0, deferred=round(deferred / total, 4) if total else 0.0,
                naming=round(sum(r['name_ok'] for r in named) / len(named), 4) if named else 1.0,
                abstained=sum(1 for r in results if r['kind'] == 'new' and not r['name']),
                worst_ms=max((r['ms'] for r in results), default=0.0),
                # Of the routes applied silently (AUTO_ROUTE and up), how many were right: this must stay near 100%.
                auto=round(len(auto) / total, 4) if total else 0.0,
                auto_right=round(sum(r['verdict'] == 'right' for r in auto) / len(auto), 4) if auto else 1.0,
                # Silent routes that were wrong, out of every message: the costliest mistake. The gate holds this one.
                auto_wrong=round(sum(r['verdict'] == 'wrong' for r in auto) / total, 4) if total else 0.0,
                suggest_right=round(sum(r['verdict'] == 'right' for r in suggested) / len(suggested), 4) if suggested else 1.0)


def run(suites=SUITES):
    out = {}
    for suite in suites:
        streams = load(suite)
        if not streams:
            continue
        results = [r for stream in streams for r in replay(stream)]
        out[suite] = dict(score=score(results), results=results)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--suite', choices=SUITES, action='append')
    parser.add_argument('--json', help='write scores and every decision here')
    parser.add_argument('--quiet', action='store_true', help='the table only')
    args = parser.parse_args(argv)
    from evals.routing import cases
    out = run(args.suite or SUITES)
    print(f"{'suite':<10}{'msgs':>6}{'right':>8}{'wrong':>8}{'model':>8}{'names':>8}{'worst ms':>10}{'silent':>8}{'its acc':>9}{'sugg acc':>9}")
    for suite, data in out.items():
        s = data['score']
        print(f"{suite:<10}{s['messages']:>6}{s['accuracy']:>8.1%}{s['misroutes']:>8.1%}{s['deferred']:>8.1%}{s['naming']:>8.1%}{s['worst_ms']:>10.2f}{s['auto']:>8.1%}{s['auto_right']:>9.1%}{s['suggest_right']:>9.1%}")
    other = cases.run(learned_too=True)
    for key, (passed, total, _) in other.items():
        print(f"{key:<10}{total:>6}{passed / total if total else 1:>8.1%}")
    if not args.quiet:
        for suite, data in out.items():
            for r in data['results']:
                if r['verdict'] != 'right' or r['name_ok'] is False:
                    mark = 'NAME' if r['verdict'] == 'right' else r['verdict'].upper()
                    print(f"[{suite} {r['stream']}#{r['index']}] {mark}: {r['text']!r} expected {r['expect']}, got "
                          f"{r['kind']}{':' + r['thread_id'] if r['thread_id'] else ''}{' ' + repr(r['name']) if r['name'] else ''} "
                          f"({r['confidence']}, {r['why']})")
        for key, (_, _, failures) in other.items():
            for failure in failures:
                print(f'[{key}] FAIL: {failure}')
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(dict(routing=out, other={k: v[:2] for k, v in other.items()}), indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
