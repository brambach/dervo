"""The one executable a real-model eval host launches for both CLIs (headless_driver real mode).

    real_cli.py --config=<eval-config.json> --dervo-driver=<token> claude <claude args...>
    real_cli.py --config=<eval-config.json> --dervo-driver=<token> codex <codex args...>

Claude: a launch whose CLAUDE_CONFIG_DIR is a Dervo-owned profile under the eval data dir runs fake_claude_cli.py (the
seeds stay scripted and free); a launch on your existing sign-in (no CLAUDE_CONFIG_DIR) runs the published CLI. Codex
launches run Dervo's bundled binary. Only model calls are counted: ``claude -p`` and ``codex app-server``. ``auth status``,
``--version`` and ``mcp list`` pass straight through.

The budget is enforced here, under a file lock, before the model process starts: ``cap`` for the whole run (every host
sharing the run log) and ``case_cap`` for this host's case. Past either, the launch is refused with exit 70 and a log
line saying so, and Dervo records the run as failed. Every call gets two lines in the run log, ``started`` and ``ended``,
joined by ``id``: case, provider, model, seconds, exit code.

Keychain: Claude Code's sign-in lookup needs USER (checked 20 Sep: with it ``auth status`` reports the signed-in
account, without it signed out; 21 Sep: LOGNAME alone does nothing). Since 21 Sep Dervo's clean_environment sends USER
for your existing sign-in, so the setdefault below is a fallback for the real CLI only. Nothing here reads a credential.
"""
import fcntl
import json
import os
import pwd
import signal
import subprocess
import sys
import time

REFUSED = 70


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]


def append(path, record):
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(record) + '\n')


def main(argv):
    config_path, args = None, list(argv)
    while args and args[0].startswith('--'):
        flag = args.pop(0)
        if flag.startswith('--config='):
            config_path = flag[len('--config='):]
    if not config_path or not args:
        sys.stderr.write('real_cli.py: usage: --config=<json> [--dervo-driver=token] claude|codex args...\n')
        return 2
    with open(config_path, encoding='utf-8') as fh:
        cfg = json.load(fh)
    provider, rest = args[0], args[1:]
    env = dict(os.environ)
    if provider == 'claude':
        config_dir = env.get('CLAUDE_CONFIG_DIR')
        if config_dir and os.path.realpath(config_dir).startswith(os.path.realpath(cfg['dir']) + os.sep):
            os.execv(cfg['python'], [cfg['python'], cfg['fake'], *rest, cfg['marker']])
        command = [cfg['claude'], *rest]
        model_call = '-p' in rest
        model = rest[rest.index('--model') + 1] if '--model' in rest and rest.index('--model') + 1 < len(rest) else None
        try:
            name = pwd.getpwuid(os.getuid()).pw_name
            env.setdefault('USER', name)
            env.setdefault('LOGNAME', name)
        except KeyError:
            pass
    elif provider == 'codex':
        command = [cfg['codex'], *rest]
        model_call = 'app-server' in rest
        model = cfg.get('codex_model')
    else:
        sys.stderr.write(f'real_cli.py: unknown provider {provider!r}\n')
        return 2
    if not model_call:
        os.execve(command[0], command, env)
    started = time.time()
    call_id = f'{os.getpid()}-{int(started * 1000)}'
    base = dict(id=call_id, case=cfg['case'], provider=provider, model=model, started=started)
    with open(cfg['lock'], 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = [r for r in read_log(cfg['run_log']) if r.get('state') == 'started']
            total, mine = len(existing), sum(1 for r in existing if r.get('case') == cfg['case'])
            if total >= cfg['cap'] or mine >= cfg['case_cap']:
                why = 'cap' if total >= cfg['cap'] else 'case_cap'
                append(cfg['run_log'], dict(base, state='refused', why=why, seconds=0.0, exit=REFUSED))
                sys.stderr.write(f'real_cli.py: refused, the eval {why} is reached ({total} calls this run, {mine} this case)\n')
                return REFUSED
            append(cfg['run_log'], dict(base, state='started'))
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    # Dervo stops a run with SIGINT to the process group, which reaches the CLI itself; this wrapper only waits.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    child = subprocess.Popen(command, env=env)
    signal.signal(signal.SIGTERM, lambda *_: child.terminate())
    code = child.wait()
    append(cfg['run_log'], dict(base, state='ended', seconds=round(time.time() - started, 2), exit=code))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
