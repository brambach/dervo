"""Run the smartness eval cases and write results.

    python3 -m evals.runner --mode fake [--system memory] [--only ID ...] [--out DIR]
    python3 -m evals.runner --mode real --cap 60 --case-cap 6 [--rerun-failing 3] --out DIR

Fake mode never launches a model: every case runs through the fake CLI, rules-only cases must pass, and model cases
just have to run end to end (their answers are scripted, so their scores mean nothing). Real mode runs each case on
its own throwaway host with real_cli.py in front of the CLIs, which enforces the caps itself. The runner also skips a
case whose declared calls would cross the cap. ``results.json`` in the output dir holds every case's status, reason,
calls and seconds, and the call log is ``real-calls.jsonl`` beside it.
"""
import argparse
import importlib
import json
import shutil
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from evals.case import SYSTEMS  # noqa: E402
from evals.harness import EvalHost  # noqa: E402

MODULES = {system: f'evals.cases_{system}' for system in SYSTEMS}


def load_cases(systems):
    cases = []
    for system in systems:
        module = importlib.import_module(MODULES[system])
        cases.extend(module.CASES)
    return cases


def run_case(case, *, mode, out, run_log, cap, case_cap, label=None, agent_provider=None, home_provider=None):
    """One case on a fresh host. Returns the result dict."""
    label = label or case.id
    root = out / label.replace('#', '-rerun')
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    result = dict(id=case.id, label=label, system=case.system, title=case.title, provider=case.provider, source=case.source,
                  expect_fail=case.expect_fail, status='error', reason='', calls=0, seconds=0.0, models=[],
                  agent_provider=agent_provider if case.provider in ('claude', 'codex') else None,
                  home_provider=(home_provider or 'claude') if case.provider == 'home' else None)
    started = time.monotonic()
    host = None
    try:
        host = EvalHost(root / 'data', real=(mode == 'real'), run_log=run_log, cap=cap, case_cap=case_cap, case=label, scratch=root,
                        agent_provider=agent_provider, home_provider=home_provider)
        host.start()
        ctx = {}
        case.setup(host, ctx)
        outcome = case.action(host, ctx)
        passed, reason = case.check(host, ctx, outcome)
        result.update(status='pass' if passed else 'fail', reason=str(reason)[:300])
        try:
            (root / 'outcome.json').write_text(json.dumps(outcome, indent=1, default=str)[:400000])
        except (TypeError, ValueError):
            pass
    except Exception as exc:  # a case that can't run is a result too, never a crash of the run
        result.update(status='error', reason=f'{type(exc).__name__}: {str(exc)[:240]}')
        (root / 'traceback.txt').write_text(traceback.format_exc())
    finally:
        if host is not None:
            try:
                host.close()
            except Exception as exc:
                result['reason'] += f' (close: {exc})'
    result['seconds'] = round(time.monotonic() - started, 1)
    if mode == 'real' and run_log.exists():
        mine = [r for r in (json.loads(l) for l in run_log.read_text().splitlines() if l.strip()) if r.get('case') == label]
        result['calls'] = sum(1 for r in mine if r.get('state') == 'started')
        result['refused'] = sum(1 for r in mine if r.get('state') == 'refused')
        result['models'] = sorted({f"{r.get('provider')}:{r.get('model')}" for r in mine if r.get('state') == 'started'})
        result['model_seconds'] = round(sum(r.get('seconds') or 0 for r in mine if r.get('state') == 'ended'), 1)
    return result


def calls_used(run_log):
    if not run_log.exists():
        return 0
    return sum(1 for line in run_log.read_text().splitlines() if line.strip() and json.loads(line).get('state') == 'started')


def run(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    run_log = out / 'real-calls.jsonl'
    systems = SYSTEMS if args.system == 'all' else (args.system,)
    cases = load_cases(systems)
    if args.only:
        wanted = set(args.only)
        cases = [c for c in cases if c.id in wanted]
    if args.skip_model:
        cases = [c for c in cases if not c.needs_model]
    home = getattr(args, 'home_provider', None)  # a caller's own Namespace may predate the flag (test_evals)
    results = []
    for case in cases:
        if args.mode == 'real' and case.needs_model and calls_used(run_log) + case.calls > args.cap:
            results.append(dict(id=case.id, label=case.id, system=case.system, title=case.title, provider=case.provider, source=case.source,
                                expect_fail=case.expect_fail, status='skip', reason=f'budget: {calls_used(run_log)} used, case needs {case.calls}, cap {args.cap}',
                                calls=0, seconds=0.0, models=[]))
            print(f'{case.system:8} {case.id:28} SKIP   budget', flush=True)
            continue
        result = run_case(case, mode=args.mode, out=out, run_log=run_log, cap=args.cap, case_cap=args.case_cap, agent_provider=args.agent_provider,
                          home_provider=home)
        results.append(result)
        print(f"{case.system:8} {case.id:28} {result['status'].upper():6} {result['calls']:2} calls {result['seconds']:6.1f}s  {result['reason'][:110]}", flush=True)
        (out / 'results.json').write_text(json.dumps(results, indent=1))
    if args.mode == 'real' and args.rerun_failing:
        failing = [c for c in cases if c.needs_model and any(r['id'] == c.id and r['status'] == 'fail' for r in results)][:args.rerun_failing]
        for case in failing:
            if calls_used(run_log) + case.calls > args.cap:
                break
            result = run_case(case, mode=args.mode, out=out, run_log=run_log, cap=args.cap, case_cap=args.case_cap, label=case.id + '#2',
                              agent_provider=args.agent_provider, home_provider=home)
            result['rerun'] = True
            results.append(result)
            print(f"{case.system:8} {case.id + '#2':28} {result['status'].upper():6} {result['calls']:2} calls {result['seconds']:6.1f}s  {result['reason'][:110]}", flush=True)
            (out / 'results.json').write_text(json.dumps(results, indent=1))
    (out / 'results.json').write_text(json.dumps(results, indent=1))
    print(summary(results, run_log))
    return results


def summary(results, run_log=None):
    lines = []
    for system in SYSTEMS:
        mine = [r for r in results if r['system'] == system and not r.get('rerun')]
        if not mine:
            continue
        passed = sum(1 for r in mine if r['status'] == 'pass')
        scored = sum(1 for r in mine if r['status'] in ('pass', 'fail'))
        lines.append(f"{system}: {passed}/{scored} passed ({sum(1 for r in mine if r['status'] == 'skip')} skipped, "
                     f"{sum(1 for r in mine if r['status'] == 'error')} errors), {sum(r['calls'] for r in mine)} real calls")
    if run_log is not None and run_log.exists():
        records = [json.loads(l) for l in run_log.read_text().splitlines() if l.strip()]
        ended = [r for r in records if r.get('state') == 'ended']
        lines.append(f"real calls: {sum(1 for r in records if r.get('state') == 'started')} started, "
                     f"{sum(1 for r in records if r.get('state') == 'refused')} refused, "
                     f"{round(sum(r.get('seconds', 0) for r in ended) / 60, 1)} model minutes")
    return '\n'.join(lines)


def out_refused(out, *, anywhere=False):
    """Why ``--out`` can't be used, or None. Every case's host keeps its data under ``<out>/<case>/data``, so an out
    folder under ~/Library/Application Support would sit next to (or on) the real app's store, and one inside a
    project would litter it. Only backend/evals/out and the system's temp folders are taken without ``--out-anywhere``;
    the app's data folder is refused even with it (security review 20 Sep, L5)."""
    import tempfile
    path = Path(out).expanduser().resolve()
    home = Path.home().resolve()
    support = (home / 'Library' / 'Application Support').resolve()
    if path == support or support in path.parents:
        return f'--out {out} is under ~/Library/Application Support, where the real app keeps its data. Choose another folder.'
    if anywhere:
        return None
    allowed = [(BACKEND / 'evals' / 'out').resolve(), Path(tempfile.gettempdir()).resolve(), Path('/tmp').resolve(), Path('/private/tmp')]
    if any(path == root or root in path.parents for root in allowed):
        return None
    return f'--out {out} is outside backend/evals/out and the temp folders. Pass --out-anywhere to use it.'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=('fake', 'real'), default='fake')
    parser.add_argument('--system', choices=('all',) + SYSTEMS, default='all')
    parser.add_argument('--only', action='append', help='case id; repeatable')
    parser.add_argument('--skip-model', action='store_true', help='rules and fake cases only')
    parser.add_argument('--cap', type=int, default=60, help='real model calls the whole run may make')
    parser.add_argument('--case-cap', type=int, default=6, help='real model calls one case may make')
    parser.add_argument('--rerun-failing', type=int, default=0, help='re-run this many failing model cases once (flakiness)')
    parser.add_argument('--agent-provider', choices=('claude', 'codex'), default=None,
                        help='run every agent case on this provider whatever the case asks for (Home cases keep --home-provider)')
    parser.add_argument('--home-provider', choices=('claude', 'codex'), default='claude',
                        help='the account Home cases run on: Claude (Sonnet) or Codex (home_codex.MODEL), beta.17')
    parser.add_argument('--out', default=str(BACKEND / 'evals' / 'out'))
    parser.add_argument('--out-anywhere', action='store_true', help='allow an --out folder outside backend/evals/out and the temp folders')
    args = parser.parse_args(argv)
    refused = out_refused(args.out, anywhere=args.out_anywhere)
    if refused:
        parser.error(refused)
    results = run(args)
    return 0 if all(r['status'] in ('pass', 'fail', 'skip') for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
