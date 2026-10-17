"""Drive a headless Dervo host from a test or a script.

The host is ``host.py <data-dir>``: one JSON object per line on stdin, ``{"operation": op, "args": {...}}``, and one line
back per request on stdout, ``{"result": ...}`` or ``{"error": "text"}``, strictly in order. ``_shutdown`` (or stdin EOF)
closes it cleanly. Stderr is free text, which this driver sends to ``<data-dir>/host-stderr.log``. Background work
(coding runs, queue drains, settlements) only moves while the host is idle between requests, so callers poll.

What this gives you:

    with HostDriver(data_dir) as host:                # starts host.py, usage sharing off
        host.call('status')                           # {'actor': ..., ...}; a {"error"} reply raises HostError
        ids = host.claude_project(project_dir)        # profile, project and thread ids, fake Claude wired in
        host.scenario(ids.profile, mode='hang', hang_seconds=40)
        task = host.call('coding_request', projectId=ids.project, channelId=ids.thread, clientId='c1', text='Hi')
        host.wait_until(lambda: host.task(ids, task['taskId'])['runs'][-1]['state'] == 'running')
        host.kill()                                   # SIGKILL: no close(), like a crash or the bridge's 10 s kill
        host.start()                                  # same data dir, fresh process
        host.provider_children()                      # pids of fake CLIs this driver's hosts launched

Fakes only. The coding path looks for Claude Code in the standard install locations (claude_policy.executable), so a
fake can't be put on PATH; instead this module, run as ``python headless_driver.py --serve <data-dir>``, is the host
process itself: it repoints ``claude_policy.executable`` and ``claude_coding.executable`` at a wrapper that runs
``fake_claude_cli.py``, then runs host.py's own main loop. Nothing here calls a model. A Codex fake is wired the same
way through ``DERVO_FAKE_CODEX`` (``codex_coding.argv`` to ``fake_codex_app_server.py``), but no test drives it yet.

Real-model mode (backend/evals only): ``HostDriver(data_dir, real=True, real_config={...})`` swaps the fake wrapper
for ``evals/real_cli.py``, one script that stands in for both CLIs. A launch whose CLAUDE_CONFIG_DIR is a Dervo-owned
profile under this data dir still runs the fake (seeds stay scripted); a launch on your existing sign-in (no
CLAUDE_CONFIG_DIR, claude_policy.is_system_home) runs the published ``claude``; Codex launches run Dervo's bundled
``runtime/codex``. The wrapper counts every model call against a hard cap it enforces itself, refuses past it, and logs
``{case, provider, model, started, seconds, exit}`` to the run log. In this mode ``serve()`` also empties
``mcp_connectors.names`` and keeps ``--strict-mcp-config`` on full-access launches, so no MCP server or claude.ai
connector of yours is ever loaded by an eval run. Nothing about the product's own launch policy changes otherwise.

Every call is timed. The native bridge kills a host that takes over 10 s to answer (src/bridge.rs), so a call over
``slow_limit`` (5 s by default) raises SlowCall, and ``slowest`` keeps the worst one for a report.

From a script (a day-in-the-life loop): ``python headless_driver.py --demo <scratch-dir>`` runs the worked example
from notes/systems-map/run-lifecycle.md and prints each step.
"""
import json
import os
import select
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
import platform_os

BACKEND = Path(__file__).resolve().parent
PYTHON = sys.executable
TEST_IDENTITY = 'dervo-test@example.com'  # what fake_claude_cli.py answers `auth status` with
MARKER = '--dervo-driver='  # trailing argv on every fake CLI launch, so provider_children() finds only this driver's


class HostError(Exception):
    """The host answered {"error": ...}. ``operation`` and ``args`` say which request."""

    def __init__(self, operation, args, text):
        super().__init__(f'{operation}: {text}')
        # ``request`` and not ``args``: BaseException.args is the message tuple, and overwriting it with the request
        # dict made str(exc) read as the dict's keys instead of the host's error text.
        self.operation, self.request, self.text = operation, args, text


class HostDied(Exception):
    """The host process ended before it answered."""


class SlowCall(Exception):
    """A request took longer than the driver's slow limit (the bridge kills the host at 10 s)."""


@dataclass
class Ids:
    profile: str
    project: str
    thread: str

    @property
    def scope(self):
        return {'projectId': self.project, 'channelId': self.thread}


class HostDriver:
    def __init__(self, data_dir, *, python=PYTHON, slow_limit=5.0, reply_timeout=30.0, ignore_stop=False, mcp_list=None,
                 real=False, real_config=None):
        """``ignore_stop`` makes the fake CLI ignore SIGINT (a Stop the provider doesn't take). ``mcp_list`` is text the
        wrapper prints for ``claude mcp list`` after an 8 s sleep, for the connector scenarios; None means the fake's own
        crash on that command, which the host treats as no connectors.

        ``real`` turns on real-model mode (module docstring). ``real_config`` keys, all optional: ``cap`` (model calls
        the whole run may make, default 60), ``case_cap`` (calls this host may make, default 6), ``case`` (a label for
        the log), ``run_log`` (the shared real-calls.jsonl; default inside this data dir), ``codex_model`` (logged with
        Codex launches, whose argv carries no model)."""
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.python, self.slow_limit, self.reply_timeout = python, slow_limit, reply_timeout
        self.token = f'{os.getpid()}-{int(time.time() * 1000) % 100000000}'
        self.wrapper = self.data_dir / 'fake-claude'
        self._write_wrapper(ignore_stop, mcp_list)
        self.real = bool(real)
        self.real_wrappers = {}
        if self.real:
            self._write_real_wrappers(dict(real_config or {}))
        self.process = None
        self.buffer = b''
        self.launches = 0
        self.slowest = (0.0, None)  # seconds, operation
        self.log_path = self.data_dir / 'host-stderr.log'

    # ------------------------------------------------------------------ process
    def _write_wrapper(self, ignore_stop, mcp_list):
        if platform_os.WINDOWS:
            self._write_windows_wrapper(ignore_stop, mcp_list)
            return
        lines = ['#!/bin/sh']
        if mcp_list is not None:
            lines += ['if [ "$1" = "mcp" ]; then', '  sleep 8', f'  printf %s\\\\n {json.dumps(mcp_list)}', '  exit 0', 'fi']
        if ignore_stop:
            lines.append("trap '' INT")
        lines.append(f'exec {json.dumps(self.python)} {json.dumps(str(BACKEND / "fake_claude_cli.py"))} "$@" {MARKER}{self.token}')
        self.wrapper.write_text('\n'.join(lines) + '\n')
        self.wrapper.chmod(self.wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _write_windows_wrapper(self, ignore_stop, mcp_list):
        """Windows runs no shell scripts as programs, and a .cmd wrapper would put every argument through cmd.exe's
        parsing. So the fake is a real fake-claude.exe, the way pip makes console scripts: distlib's console launcher,
        a #! line naming this Python, then a zip whose __main__.py runs fake_claude_cli.py. The launcher starts that
        Python with the same arguments and pipes, and both stay in the run's Job Object. ``ignore_stop`` ignores
        Ctrl+Break and Ctrl+C, the Windows side of a Stop the provider doesn't take."""
        import io
        import zipfile
        from pip._vendor import distlib
        launcher = (Path(distlib.__file__).parent / 't64.exe').read_bytes()
        fake = str(BACKEND / 'fake_claude_cli.py')
        main = ['import runpy, signal, sys, time']
        if mcp_list is not None:
            main.append(f"if sys.argv[1:2] == ['mcp']: time.sleep(8); print({mcp_list!r}); sys.exit(0)")
        if ignore_stop:
            main.append('signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGBREAK, signal.SIG_IGN)')
        main += [f'sys.argv = [{fake!r}] + sys.argv[1:] + [{MARKER + self.token!r}]',
                 f'runpy.run_path({fake!r}, run_name="__main__")']
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.writestr('__main__.py', '\n'.join(main) + '\n')
        self.wrapper = self.data_dir / 'fake-claude.exe'
        self.wrapper.write_bytes(launcher + f'#!"{self.python}"\r\n'.encode() + archive.getvalue())

    def _write_real_wrappers(self, config):
        """``eval-config.json`` and one shell wrapper per provider for evals/real_cli.py. The CLI environment Dervo
        builds is an allowlist with nothing of ours in it, so the wrapper script carries the config path itself."""
        import claude_policy
        import codex_runtime
        self.run_log = Path(config.get('run_log') or self.data_dir / 'real-calls.jsonl').resolve()
        settings = {
            'dir': str(self.data_dir), 'run_log': str(self.run_log), 'lock': str(self.run_log) + '.lock',
            'cap': int(config.get('cap', 60)), 'case_cap': int(config.get('case_cap', 6)), 'case': str(config.get('case', 'run')),
            'claude': claude_policy.executable(), 'codex': str(codex_runtime.executable()),
            'python': self.python, 'fake': str(BACKEND / 'fake_claude_cli.py'), 'marker': MARKER + self.token,
            'codex_model': config.get('codex_model'),
        }
        self.real_config_path = self.data_dir / 'eval-config.json'
        self.real_config_path.write_text(json.dumps(settings, indent=1))
        for provider in ('claude', 'codex'):
            path = self.data_dir / f'eval-{provider}'
            # The marker rides in the wrapper's own argv (not the CLI's), so provider_children() still finds its launches.
            path.write_text('#!/bin/sh\n' + f'exec {json.dumps(self.python)} {json.dumps(str(BACKEND / "evals" / "real_cli.py"))} '
                            f'--config={json.dumps(str(self.real_config_path))} {MARKER}{self.token} {provider} "$@"\n')
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            self.real_wrappers[provider] = str(path)

    def real_calls(self):
        """Every logged real model call of this run so far (real mode), newest last."""
        log = getattr(self, 'run_log', None)
        if not log or not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

    def start(self):
        """Start (or start again, after kill() or a clean shutdown) the host on the same data dir."""
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError('The host is already running; call shutdown() or kill() first')
        if self.real:
            env = dict(os.environ, DERVO_EVAL_CLI_CLAUDE=self.real_wrappers['claude'], DERVO_EVAL_CLI_CODEX=self.real_wrappers['codex'],
                       PYTHONUNBUFFERED='1')
            env.pop('DERVO_FAKE_CLAUDE', None)
        else:
            env = dict(os.environ, DERVO_FAKE_CLAUDE=str(self.wrapper), PYTHONUNBUFFERED='1')
        log = open(self.log_path, 'ab')
        try:
            # Started as the app starts it, so on Windows Stop's Ctrl+Break reaches a run the same way.
            self.process = subprocess.Popen([self.python, str(BACKEND / 'headless_driver.py'), '--serve', str(self.data_dir)],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0, cwd=str(BACKEND), env=env,
                                            **platform_os.hidden_console())
        finally:
            log.close()
        self.buffer = b''
        self.launches += 1
        # The first reply includes the host's own start. A Windows runner with three test modules at once took 6.1 s
        # there (run 36125278729), so on Windows the first call gets the bridge's own 10 s, not the driver's 5 s.
        limit = self.slow_limit
        if platform_os.WINDOWS:
            self.slow_limit = max(limit, 9.5)
        try:
            self.call('usage_sharing_set', enabled=False)  # nothing leaves the Mac from a test host
        except BaseException:
            # The caller never gets this driver back to close, so the host would outlive the test and hold its files.
            self.kill()
            raise
        finally:
            self.slow_limit = limit
        return self

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    @property
    def pid(self):
        return self.process.pid if self.process else None

    def kill(self):
        """SIGKILL the host: no close(), no _shutdown. What a crash, a Force Quit or the bridge's 10 s kill does."""
        if self.process is None:
            return
        if self.process.poll() is None:
            os.kill(self.process.pid, platform_os.SIGKILL)  # SIGKILL on a Mac; TerminateProcess on Windows
            self.process.wait(timeout=10)
        self._release_pipes()

    def shutdown(self, timeout=10.0):
        """The app's quit: `_shutdown`, then wait. Returns the exit code, or raises if the host didn't exit in time."""
        if self.process is None:
            return None
        if self.process.poll() is None:
            try:
                self.process.stdin.write(b'{"operation":"_shutdown","args":{}}\n')
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                raise HostDied(f'The host did not exit within {timeout} s of _shutdown')
        code = self.process.returncode
        self._release_pipes()
        return code

    def _release_pipes(self):
        for pipe in (self.process.stdin, self.process.stdout):
            try:
                pipe.close()
            except OSError:
                pass

    def close(self):
        """Shutdown if the host runs, then kill any fake CLI this driver's hosts left behind, so a test leaves nothing."""
        try:
            if self.alive:
                self.shutdown()
        finally:
            for pid in self.provider_children():
                # Each CLI is its own process group leader (transport.OwnedProcess starts a new session), so the group
                # takes the real CLI along with the eval wrapper that launched it.
                for send in (platform_os.signal_group, os.kill):
                    try:
                        send(pid, platform_os.SIGKILL)
                        break
                    except ProcessLookupError:
                        break
                    except OSError:
                        continue

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ protocol
    def raw(self, operation, args=None):
        """One request, the whole reply line as a dict ({"result"} or {"error"})."""
        if not self.alive:
            raise HostDied(f'{operation}: the host is not running')
        line = json.dumps({'operation': operation, 'args': args or {}}).encode() + b'\n'
        started = time.monotonic()
        try:
            self.process.stdin.write(line)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise HostDied(f'{operation}: the host closed its stdin ({exc})') from exc
        reply = self._read_line(operation, started)
        took = time.monotonic() - started
        if took > self.slowest[0]:
            self.slowest = (took, operation)
        if took > self.slow_limit:
            raise SlowCall(f'{operation} took {took:.1f} s (limit {self.slow_limit} s; the bridge kills at 10 s)')
        return json.loads(reply)

    def _read_line(self, operation, started):
        fd = self.process.stdout.fileno()
        while b'\n' not in self.buffer:
            remaining = self.reply_timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise HostDied(f'{operation}: no reply within {self.reply_timeout} s')
            if not platform_os.wait_readable(fd, min(remaining, 0.25)):
                if self.process.poll() is not None:
                    raise HostDied(f'{operation}: the host exited with {self.process.returncode} before answering')
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                raise HostDied(f'{operation}: the host closed stdout before answering (exit {self.process.poll()})')
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b'\n', 1)
        return line

    def call(self, operation, **args):
        """One request; the result, or HostError for an {"error"} reply."""
        reply = self.raw(operation, args)
        if 'error' in reply:
            raise HostError(operation, args, reply['error'])
        return reply['result']

    def wait_until(self, condition, timeout=15.0, interval=0.1, what='condition'):
        """Poll ``condition()`` until it returns a truthy value, which is returned. Every poll is a request, so the
        host's background work moves between them."""
        deadline = time.monotonic() + timeout
        while True:
            value = condition()
            if value:
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError(f'{what} not met within {timeout} s')
            time.sleep(interval)

    # ------------------------------------------------------------------ fixtures
    def add_claude_profile(self, identity=TEST_IDENTITY):
        profile = self.call('add_profile', provider='claude', identity=identity, category='personal')['id']
        (self.data_dir / 'profiles' / profile).mkdir(parents=True, exist_ok=True)
        return profile

    def scenario(self, profile, **scenario):
        """What the fake CLI does on its next launches for this profile: result_text, mode ('ok' or 'hang'),
        hang_seconds, auth, init_model, tool_calls (fake_claude_cli.py)."""
        home = self.data_dir / 'profiles' / profile
        home.mkdir(parents=True, exist_ok=True)
        (home / 'fake-scenario.json').write_text(json.dumps(scenario))

    def calls(self, profile):
        """Every fake CLI launch for this profile so far, newest last (argv, cwd, env)."""
        path = self.data_dir / 'profiles' / profile / 'calls.jsonl'
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def claude_project(self, path, *, name='probe', files=('greeting.py', 'test_sample.py'), test_file='test_sample.py',
                       full_access=False, model='claude-fake-model', result_text='Nothing to change.'):
        """A Claude profile, a project at ``path`` (see make_project), one shared thread, and coding permission for
        ``files`` (or full access). Returns Ids."""
        profile = self.add_claude_profile()
        self.scenario(profile, result_text=result_text)
        project = self.call('_picked_project', path=str(Path(path).resolve()), profile_id=profile, model=model)['id']
        thread = self.call('create_conversation', projectId=project, name=name, private=False)['id']
        ids = Ids(profile, project, thread)
        if platform_os.WINDOWS:
            # Checks run only on a Mac for now (check_profiles.WINDOWS_DETAIL), so on Windows the grant dialog's one
            # choice is Review changes myself, which it sends with no test file (grantPayload in frontend/src/local/api.ts).
            check = dict(testFile=None, checkProfile='manual-review')
            files = () if full_access else files
        else:
            check = dict(testFile=test_file)
            files = [test_file] if full_access else files
        # The grant is the host's first coding call, which loads the coding modules and their tables. A Windows runner
        # took 5.6 s there (run 37871829356, compiling the backend from source under -B) where another run took 1.1 s,
        # so on Windows it gets the bridge's own 10 s like the host's first reply, not the driver's 5 s.
        limit = self.slow_limit
        if platform_os.WINDOWS:
            self.slow_limit = max(limit, 9.5)
        try:
            if full_access:
                self.call('coding_permission_grant', **ids.scope, projectAccess=True, files=list(files), **check)
            else:
                self.call('coding_permission_grant', **ids.scope, files=list(files), **check)
        finally:
            self.slow_limit = limit
        if full_access:
            self.call('coding_full_access_set', **ids.scope, enabled=True, warning='beta.15')  # the confirm's token (security N4)
        return ids

    def request(self, ids, text, client_id=None, **extra):
        """A coding request in the thread; the task id."""
        client_id = client_id or f'c{int(time.time() * 1000) % 1000000}'
        return self.call('coding_request', **ids.scope, clientId=client_id, text=text, **extra)['taskId']

    def task(self, ids, task_id):
        return self.call('task_detail', **ids.scope, taskId=task_id)['task']

    def wait_for_run(self, ids, task_id, timeout=15.0):
        """Until the task's latest run is running (the fake CLI has started and sent its first word)."""
        return self.wait_until(lambda: (t := self.task(ids, task_id))['runs'] and t['runs'][-1]['state'] == 'running' and t,
                               timeout, what='run running')

    def wait_for_end(self, ids, task_id, timeout=30.0):
        """Until the task has left ``working``; the task."""
        return self.wait_until(lambda: (t := self.task(ids, task_id))['state'] != 'working' and t, timeout, what='task ended')

    # ------------------------------------------------------------------ processes
    def provider_children(self):
        """Pids of fake CLIs launched by any host this driver started, alive now. They run in their own session, so a
        dead host doesn't take them along (run-lifecycle.md, finding 1).

        Windows: the fake-claude.exe launchers this driver wrote, found by their exe path (the pid a run saves). A host
        that ends takes its runs with it there (Job Objects, platform_os.contain), so after a kill this is empty."""
        if platform_os.WINDOWS:
            return self._windows_children()
        try:
            out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        pids = []
        for line in out.splitlines():
            if ('fake_claude_cli.py' in line or 'real_cli.py' in line) and MARKER + self.token in line:
                try:
                    pids.append(int(line.split(None, 1)[0]))
                except ValueError:
                    pass
        return pids

    def _windows_children(self):
        query = ("Get-CimInstance Win32_Process -Filter \"Name='fake-claude.exe'\" | "
                 "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)\" }")
        try:
            out = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', query],
                                 capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        mine = os.path.normcase(str(self.wrapper))
        pids = []
        for line in out.splitlines():
            pid, _, path = line.strip().partition('\t')
            if path and os.path.normcase(path) == mine and pid.isdigit():
                pids.append(int(pid))
        return pids

    def stderr(self):
        return self.log_path.read_text() if self.log_path.exists() else ''


def make_project(path):
    """A small git repository with greeting.py and test_sample.py, one commit, for coding requests."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    (path / 'greeting.py').write_text('def greet(name):\n    return f"Hello, {name}"\n')
    (path / 'test_sample.py').write_text('import unittest\nfrom greeting import greet\n\n\nclass Sample(unittest.TestCase):\n'
                                         '    def test_ok(self):\n        self.assertEqual(greet("a"), "Hello, a")\n')
    env = dict(os.environ, GIT_AUTHOR_NAME='Dervo test', GIT_AUTHOR_EMAIL='test@example.com',
               GIT_COMMITTER_NAME='Dervo test', GIT_COMMITTER_EMAIL='test@example.com', HOME=str(path))
    for command in (['git', 'init', '-q', '-b', 'main'], ['git', 'add', '.'], ['git', 'commit', '-q', '-m', 'start']):
        subprocess.run(command, cwd=path, env=env, check=True, capture_output=True)
    return path


# ---------------------------------------------------------------------- the host process itself
def serve(data_dir):
    """Run host.py's main loop in this process with the fake CLIs wired in. Only ever called by HostDriver.start()."""
    import runpy
    sys.path.insert(0, str(BACKEND))
    # A test host's data dir has no Dervo sign-in, and since beta.15 a source checkout carries
    # runtime/dervo-account-public.json, so every request stopped at "beta access needs confirmation" (16.4 evals: 14
    # errors, the rest failed with no reply). Run it as the offline source checkout account_config.create allows.
    import account_config
    account_config.create = lambda *a, **k: None
    fake_claude = os.environ.get('DERVO_FAKE_CLAUDE')
    if fake_claude:
        import claude_policy
        import claude_coding
        claude_policy.executable = lambda *a, **k: fake_claude
        claude_coding.executable = lambda *a, **k: fake_claude
    if os.environ.get('DERVO_FAKE_CODEX'):
        import codex_coding
        codex_coding.argv = lambda *a, **k: [sys.executable, str(BACKEND / 'fake_codex_app_server.py')]
    eval_claude, eval_codex = os.environ.get('DERVO_EVAL_CLI_CLAUDE'), os.environ.get('DERVO_EVAL_CLI_CODEX')
    if eval_claude and eval_codex:
        # Real-model mode (backend/evals). The wrapper is the executable for every Claude launch: it runs the fake for a
        # Dervo-owned profile and the published CLI for your existing sign-in. Codex launches keep Dervo's exact policy
        # argv, with the wrapper in front of the bundled binary. The two guards below are the eval's own: no MCP server
        # or claude.ai connector of yours is listed or loaded, whatever a case turns on.
        import claude_policy
        import claude_coding
        import codex_coding
        import mcp_connectors
        claude_policy.executable = lambda *a, **k: eval_claude
        claude_coding.executable = lambda *a, **k: eval_claude
        codex_argv = codex_coding.argv  # the coding launch policy's own argv (codex_coding_policy), wrapper in front
        codex_coding.argv = lambda *a, **k: [eval_codex] + codex_argv(*a, **k)[1:]
        # Home's own Codex call (beta.17) the same way, so its launches count against the eval's cap like any other.
        import home_codex
        home_argv = home_codex.argv
        home_codex.argv = lambda *a, **k: [eval_codex] + home_argv(*a, **k)[1:]
        mcp_connectors.names = lambda *a, **k: []
        strict = claude_policy.full_access_argv

        def full_access_without_connectors(*a, **k):
            args = list(strict(*a, **dict(k, connectors=(), hidden=())))
            if '--strict-mcp-config' not in args:
                args.insert(args.index('--mcp-config'), '--strict-mcp-config')
            return args
        claude_coding.full_access_argv = full_access_without_connectors
        # Since 16.4 (item 7) the host sends Home's jobs itself. Evals score what Home decides, not the runs, and a worker
        # run would spend calls under the case cap, so a reply's jobs stay unsent here and it stops being queued.
        import project_orchestrator

        def hold_home_jobs(self, scope, entry_id, actor):
            row = self.db.execute('SELECT document FROM orchestrator_entries WHERE id=?', (entry_id,)).fetchone()
            if row is not None:
                document = project_orchestrator.json.loads(row['document'])
                document.pop('queued', None)
                with self.db:
                    self._update(entry_id, document)
        project_orchestrator.Orchestrator.send_next = hold_home_jobs
    if os.environ.get('DERVO_HOST_SAMPLE'):
        sample(Path(os.environ['DERVO_HOST_SAMPLE']))
    sys.argv = [str(BACKEND / 'host.py'), str(data_dir)]
    runpy.run_path(str(BACKEND / 'host.py'), run_name='__main__')


def sample(path, every=0.02):
    """Where the host's threads spend their time, for a runner nobody can log in to (scripts/host-call-sample.py).

    Every ``every`` seconds it notes each function on every thread's stack, and every two seconds writes the 40 seen
    most often to ``path``, with the share of samples each was on the stack for. Off unless DERVO_HOST_SAMPLE is set."""
    import collections
    import threading
    seen, total = collections.Counter(), [0]

    def watch():
        me, written = threading.get_ident(), time.monotonic()
        while True:
            time.sleep(every)
            for ident, frame in sys._current_frames().items():
                if ident == me:
                    continue
                total[0] += 1
                names = set()
                while frame is not None:
                    names.add(f'{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}')
                    frame = frame.f_back
                seen.update(names)
            if time.monotonic() - written > 2:
                written = time.monotonic()
                lines = [f'{count / max(1, total[0]):6.1%} {name}' for name, count in seen.most_common(40)]
                path.write_text(f'{total[0]} thread samples, {every} s apart\n' + '\n'.join(lines) + '\n')
    threading.Thread(target=watch, daemon=True).start()


def demo(scratch):
    """The worked example: one plain answer, printed step by step. Leaves nothing running."""
    scratch = Path(scratch).resolve()
    project = make_project(scratch / 'proj')
    with HostDriver(scratch / 'data') as host:
        ids = host.claude_project(project)
        print('profile', ids.profile, 'project', ids.project, 'thread', ids.thread)
        task = host.request(ids, 'Is greeting.py fine?')
        ended = host.wait_for_end(ids, task)
        print('task', ended['state'], [r['state'] for r in ended['runs']])
        print('activity', host.call('conversations', projectId=ids.project)[0]['activity'])
        print('slowest call', f'{host.slowest[0]:.2f} s', host.slowest[1])
        print('fake CLIs alive', host.provider_children())


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--serve':
        serve(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == '--demo':
        demo(sys.argv[2])
    else:
        print('usage: headless_driver.py --demo <scratch-dir>   (--serve <data-dir> is the host process)', file=sys.stderr)
        sys.exit(2)
