"""Polisher host: the real Dervo host (headless_driver --serve, fake Claude CLI) behind a JSON-lines pipe.

stdin: {"id": n, "operation": op, "args": {...}}  stdout: {"id": n, "result": ...} or {"id": n, "error": text, "ms": ...}
Polisher-only operations start with "polish_": polish_seed (a git project, a Claude profile, one thread with
coding permission), polish_fresh (nothing seeded, only a project folder on disk), polish_scenario (what the fake CLI does next), polish_calls (fake CLI launches), polish_new_project.
Nothing here calls a model.
"""
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))
from headless_driver import HostDriver, HostError, make_project  # noqa: E402

data = Path(sys.argv[1])
host = HostDriver(data / 'host', slow_limit=60.0, reply_timeout=60.0)
# Home's one-shot calls get scripted answers (fake_home_cli.py); coding runs still use the backend's fake CLI.
text = host.wrapper.read_text()
host.wrapper.write_text(text.replace(json.dumps(str(BACKEND / 'fake_claude_cli.py')), json.dumps(str(Path(__file__).with_name('fake_home_cli.py')))))
host.start()


def polish(op, args):
    if op == 'polish_seed':
        project = make_project(data / 'projects' / args.get('name', 'probe'))
        ids = host.claude_project(project, name=args.get('thread', 'First thread'), full_access=args.get('full_access', False),
                                  result_text=args.get('result_text', 'Nothing to change.'))
        return dict(profile=ids.profile, project=ids.project, thread=ids.thread, path=str(project))
    if op == 'polish_fresh':
        # A fresh install: nothing seeded. A project folder is made on disk for the folder picker to hand back.
        project = make_project(data / 'projects' / args.get('name', 'first-project'))
        return dict(profile='', project='', thread='', path=str(project))
    if op == 'polish_new_project':
        project = make_project(data / 'projects' / args['name'])
        return host.call('_picked_project', path=str(project), profile_id=args['profile'], model=args.get('model', 'claude-fake-model'))
    if op == 'polish_scenario':
        host.scenario(args.pop('profile'), **args)
        return None
    if op == 'polish_home_rules':
        home = data / 'host' / 'profiles' / args['profile']
        home.mkdir(parents=True, exist_ok=True)
        (home / 'home-rules.json').write_text(json.dumps(args['rules']))
        return None
    if op == 'polish_calls':
        return host.calls(args['profile'])
    raise ValueError('unknown polish op ' + op)


try:
    for line in sys.stdin:
        request = json.loads(line)
        started = time.monotonic()
        op, args = request['operation'], request.get('args') or {}
        try:
            result = polish(op, args) if op.startswith('polish_') else host.call(op, **args)
            reply = dict(id=request['id'], result=result)
        except HostError as error:
            reply = dict(id=request['id'], error=error.text)
        except Exception as error:  # noqa: BLE001 a harness failure is reported, not fatal
            reply = dict(id=request['id'], error=f'{type(error).__name__}: {error}')
        reply['ms'] = round((time.monotonic() - started) * 1000)
        print(json.dumps(reply), flush=True)
finally:
    host.shutdown() if hasattr(host, 'shutdown') else None
