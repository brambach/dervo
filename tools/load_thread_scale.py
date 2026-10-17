"""How Dervo's backend holds up as threads are added (beta.16.1 item 2, Bryce 26 Sep: "if we can't even run 4 that's
shit"). Offline, like tools/load_parallel_runs.py: the real host.py request loop in its own process on a throwaway store,
and every Claude Code run is backend/fake_claude_load.py in its busy mode (FAKE_LOAD_BUSY), which streams text and web
search steps the way a working run does. No model, account or network is touched.

For each thread count it starts that many requests at once, plays the app's polling the whole time (the sidebar, Home,
the open thread's status, tasks and feed), and writes down:
  - backend CPU (seconds of CPU per second of wall time) and peak memory, and the same for the fake CLIs;
  - how long every poll waited for its answer (the app kills the backend when one waits 10 s: src/bridge.rs);
  - inside the backend: how long each pump round (CodingTasks.tick) took, how many run records were saved and how many
    bytes were written and parsed doing it.

    python3 tools/load_thread_scale.py [--threads 1,3,5] [--seconds 60] [--out <dir>] [--profile] [--subagents N]

--subagents N (16.2) runs every thread with full access in a git folder, each with N of Claude Code's sub-agents streaming
their messages, steps and task events for the whole run. --subagents 0 is the same full-access run without them.

The fake CLI is small, so memory and CPU here are Dervo's side. A real Claude Code run measured 228-268 MB on 26 Sep.
"""
import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / 'backend'
PYTHON = sys.executable
MODEL = 'claude-opus-5'


def host_main(data, wrapper, limit, stats_path, profile):
    """The backend process: tools/load_parallel_host.py's fakes, plus counters around the pump and the record reads."""
    sys.path.insert(0, str(BACKEND))
    import account_config
    import claude_coding
    import claude_version
    import coding_integration
    identity = 'load-test@example.test'
    account_config.create = lambda store, data_root, resources=None: None
    claude_coding.executable = lambda: wrapper
    claude_coding.auth_status = lambda *a, **k: {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty',
                                                  'email': identity, 'subscriptionType': 'max'}
    claude_version.require = lambda *a, **k: None
    import mcp_connectors  # full access lists your connectors with `claude mcp list`, which the fake CLI doesn't answer
    mcp_connectors.names = lambda *a, **k: []
    mcp_connectors.cached = lambda *a, **k: []
    mcp_connectors.warm = lambda *a, **k: None
    coding_integration.MAX_PARALLEL_OWNERS = limit
    stats = {'ticks': [], 'saves': 0, 'saved_bytes': 0, 'loads': 0, 'loaded_bytes': 0, 'requests': {}}
    tasks = coding_integration.CodingTasks
    tick, save = tasks.tick, tasks.save
    def timed_tick(self):
        started = time.perf_counter()
        try:
            return tick(self)
        finally:
            if self.turns:
                stats['ticks'].append(time.perf_counter() - started)
    def counted_save(self, task):
        stats['saves'] += 1
        stats['saved_bytes'] += len(json.dumps(task))
        return save(self, task)
    tasks.tick, tasks.save = timed_tick, counted_save
    loads = json.loads
    def counted_loads(text, *args, **kwargs):
        if isinstance(text, (str, bytes)) and len(text) > 2048:
            stats['loads'] += 1
            stats['loaded_bytes'] += len(text)
        return loads(text, *args, **kwargs)
    json.loads = counted_loads
    import host
    dispatch = host.Host.dispatch
    def timed_dispatch(self, operation, args):
        started = time.perf_counter()
        try:
            return dispatch(self, operation, args)
        finally:
            stats['requests'].setdefault(operation, []).append(time.perf_counter() - started)
    host.Host.dispatch = timed_dispatch
    profiler = None
    if profile:
        import cProfile
        profiler = cProfile.Profile()
        profiler.enable()
    served = host.Host(data)
    try:
        host.serve(served)
    finally:
        if profiler:
            profiler.disable()
            profiler.dump_stats(stats_path + '.prof')
        served.close()
        Path(stats_path).write_text(json.dumps(stats))


def percentiles(values):
    values = sorted(values)
    if not values:
        return {'count': 0}
    return {'count': len(values), 'p50_ms': round(statistics.median(values) * 1000, 1),
            'p95_ms': round(values[min(len(values) - 1, math.ceil(len(values) * .95) - 1)] * 1000, 1),
            'max_ms': round(values[-1] * 1000, 1), 'total_s': round(sum(values), 2)}


def run(threads, seconds, out, profile=False, rate=30.0, tool_seconds=2.0, subagents=None):
    sys.path.insert(0, str(ROOT / 'tools'))
    from load_parallel_runs import tree
    temp = Path(tempfile.mkdtemp(prefix='dervo-scale-'))
    data, errors, stats_path = temp / 'data', temp / 'host-errors.log', temp / 'stats.json'
    wrapper = temp / 'claude'
    wrapper.write_text(f'#!/bin/sh\nFAKE_LOAD_BUSY=1 FAKE_LOAD_RATE={rate} FAKE_LOAD_TOOL_SECONDS={tool_seconds} '
                       f'FAKE_LOAD_SECONDS={seconds} FAKE_LOAD_SUBAGENTS={subagents or 0} exec {PYTHON} -B {BACKEND / "fake_claude_load.py"} "$@"\n')
    wrapper.chmod(0o755)
    process = subprocess.Popen([PYTHON, '-B', __file__, '--host', str(data), str(wrapper), str(max(threads, 10)), str(stats_path)]
                               + (['--profile'] if profile else []), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=open(errors, 'ab'), cwd=BACKEND)
    lock, timings, failures = threading.Lock(), [], []

    def call(operation, args=None, timed=None):
        with lock:
            started = time.monotonic()
            process.stdin.write((json.dumps({'operation': operation, 'args': args or {}}) + '\n').encode())
            process.stdin.flush()
            line = process.stdout.readline()
            took = time.monotonic() - started
        if timed:
            timings.append((timed, took))
        answer = json.loads(line)
        if 'error' in answer:
            raise RuntimeError(f'{operation}: {answer["error"]}')
        return answer['result']

    samples, stop = [], threading.Event()

    def sample():
        while not stop.is_set():
            rows = tree(process.pid)
            fakes = [row for row in rows.values() if 'fake_claude_load.py' in row[4]]
            me = rows.get(process.pid)
            if me:
                samples.append({'at': time.monotonic(), 'host_rss_mb': me[1] / 1024, 'host_cpu_s': me[3],
                                'fakes': len(fakes), 'fake_rss_mb': sum(r[1] for r in fakes) / 1024,
                                'fake_cpu_s': sum(r[3] for r in fakes)})
            stop.wait(.5)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    try:
        profile_id = call('add_profile', {'provider': 'claude', 'identity': 'load-test@example.test', 'category': 'personal'})['id']
        folder = temp / 'project'
        folder.mkdir()
        (folder / 'README.md').write_text('A load test project.\n')
        full = subagents is not None
        if full:
            # Full access needs a git repository with at least one commit (conversation_coding). Inside a Dervo agent run
            # the git guard refuses this commit, so the full-access modes run from Terminal.
            for command in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=Dervo load', '-c', 'user.email=load@dervo.invalid',
                                                            'commit', '-qm', 'start']):
                subprocess.run(['/usr/bin/git', *command], cwd=folder, check=True, capture_output=True)
        project = call('_picked_project', {'path': str(folder), 'profile_id': profile_id, 'model': MODEL})['id']
        channels = [call('create_conversation', {'projectId': project, 'name': f'Scale {n + 1}', 'private': False})['id']
                    for n in range(threads)]
        if full:
            scope = {'projectId': project, 'channelId': channels[0]}
            call('coding_permission_grant', {**scope, 'files': [], 'testFile': None, 'projectAccess': True, 'checkProfile': 'manual-review'})
            if not call('coding_full_access_set', {**scope, 'enabled': True, 'warning': 'beta.15'})['full_access']['active']:
                failures.append('Full access did not turn on')
        started = time.monotonic()
        for n, channel in enumerate(channels):
            result = call('coding_request', {'projectId': project, 'channelId': channel, 'clientId': f'scale-{n + 1}',
                                             'text': f'Scale test request {n + 1}.', **({} if full else {'readOnly': True})}, timed='coding_request')
            if not result.get('taskId'):
                failures.append(f'Request {n + 1} did not start: {result}')
        cursor = None
        ends = started + seconds + 2
        while time.monotonic() < ends:
            try:
                call('conversations', {'projectId': project}, timed='conversations')
                call('orchestrator_list', {'projectId': project, 'profileId': profile_id}, timed='home')
                call('project_pickup', {'projectId': project, 'profileId': profile_id}, timed='home_pickup')
                call('conversation_status', {'projectId': project, 'channelId': channels[0]}, timed='conversation_status')
                call('tasks', {'projectId': project, 'channelId': channels[0]}, timed='tasks')
                feed = call('conversation_feed', {'projectId': project, 'channelId': channels[0], 'cursor': cursor, 'force': False},
                            timed='conversation_feed')
                cursor = (feed or {}).get('cursor', cursor)
            except Exception as exc:  # noqa: BLE001 - a failed poll is a finding
                failures.append(f'A poll failed: {exc}')
            time.sleep(.25)
        # Let the runs finish, then read the final states.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            states = [t['state'] for c in channels for t in call('tasks', {'projectId': project, 'channelId': c})]
            if 'working' not in states:
                break
            time.sleep(.3)
        final = [t['state'] for c in channels for t in call('tasks', {'projectId': project, 'channelId': c})]
        process.stdin.write(b'{"operation":"_shutdown","args":{}}\n')
        process.stdin.flush()
        process.wait(timeout=60)
    finally:
        stop.set()
        if process.poll() is None:
            process.kill()
        sampler.join(timeout=5)
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    import sqlite3
    db = sqlite3.connect(data / 'conversations.sqlite3')
    sizes = [row[0] for row in db.execute('SELECT length(document) FROM coding_tasks')]
    recorded = []  # per task: (sub-agents on its last run, sub-agent steps among its tokens)
    errors_seen = set()  # why runs ended blocked, so a run that never started says so
    for (document,) in db.execute('SELECT document FROM coding_tasks'):
        record = json.loads(document)
        if record.get('state') == 'blocked' and record.get('error'):
            errors_seen.add(str(record['error'])[:300])
        last = (record.get('runs') or [{}])[-1]
        recorded.append((len(last.get('subagents') or []), sum(1 for t in record.get('tokens') or [] if t.get('parent'))))
    db.close()
    host_errors = errors.read_text(errors='replace')[-800:] if errors.exists() else ''
    profile_text = None
    if profile and Path(str(stats_path) + '.prof').exists():
        import pstats
        import io
        text = io.StringIO()
        pstats.Stats(str(stats_path) + '.prof', stream=text).sort_stats('cumulative').print_stats(45)
        profile_text = text.getvalue()
    import shutil
    shutil.rmtree(temp, ignore_errors=True)  # the throwaway store, the fake's wrapper and the host's log
    steady = [s for s in samples if s['fakes'] == threads]
    span = (steady[-1]['at'] - steady[0]['at']) if len(steady) > 1 else 0
    cpu = lambda key: round((steady[-1][key] - steady[0][key]) / span, 3) if span else None
    wall = seconds
    result = {
        'threads': threads, 'seconds': seconds, 'fake_rate_per_s': rate, 'fake_tool_every_s': tool_seconds,
        'full_access': subagents is not None, 'subagents_per_thread': subagents,
        'recorded_subagents_and_steps': recorded,
        'failures': failures, 'final_states': {s: final.count(s) for s in sorted(set(final))},
        'blocked_errors': sorted(errors_seen),
        'host_cpu_per_s': cpu('host_cpu_s'), 'fake_cpu_per_s': cpu('fake_cpu_s'),
        'host_peak_rss_mb': round(max((s['host_rss_mb'] for s in samples), default=0), 1),
        'fake_peak_rss_mb': round(max((s['fake_rss_mb'] for s in samples), default=0), 1),
        'steady_seconds': round(span, 1),
        'pump_round': percentiles(stats.get('ticks', [])),
        'record_saves_per_s': round(stats.get('saves', 0) / wall, 1),
        'record_mb_written_per_s': round(stats.get('saved_bytes', 0) / wall / 2**20, 2),
        'json_mb_parsed_per_s': round(stats.get('loaded_bytes', 0) / wall / 2**20, 2),
        'final_record_kb': {'max': round(max(sizes, default=0) / 1024, 1), 'mean': round(statistics.mean(sizes) / 1024, 1) if sizes else 0},
        'poll_wait': {name: percentiles([t for n, t in timings if n == name]) for name in sorted({n for n, _ in timings})},
        'backend_request_time': {name: percentiles(values) for name, values in sorted(stats.get('requests', {}).items())
                                 if name in ('conversations', 'orchestrator_list', 'project_pickup', 'conversation_status',
                                             'tasks', 'conversation_feed', 'coding_request')},
        'host_errors_tail': host_errors,
    }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        name = f'threads-{threads}' + (f'-subagents-{subagents}' if subagents is not None else '')
        (out / f'{name}.json').write_text(json.dumps(result, indent=2) + '\n')
        if profile_text:
            (out / f'{name}-profile.txt').write_text(profile_text)
    return result


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--host':
        return host_main(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5], '--profile' in sys.argv)
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--threads', default='1,3,5')
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--rate', type=float, default=30)
    parser.add_argument('--tool-seconds', type=float, default=2)
    parser.add_argument('--out', type=Path, default=None)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--subagents', type=int, default=None, help='full access, with this many sub-agents per thread')
    args = parser.parse_args()
    for count in [int(v) for v in args.threads.split(',') if v]:
        result = run(count, args.seconds, args.out, args.profile, args.rate, args.tool_seconds, args.subagents)
        print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
