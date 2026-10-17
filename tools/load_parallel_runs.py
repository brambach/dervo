"""Offline load test for parallel coding runs (Bryce, 23 Sep: "prove 10 runs at once holds up" before the limit merges).

The real host runs in its own process (tools/load_parallel_host.py), as the app runs it, on a throwaway store, and every
Claude Code run is a real subprocess of backend/fake_claude_load.py. No model, account or network is touched.

What it does, with ``--limit N`` (default 10):
  1. Two projects, N + 2 threads. N + 2 requests are sent at once: N start, and the last two wait with "Waiting for a slot".
  2. While they run, the renderer's polling is played the whole time (the sidebar's conversation list and Home's list for
     both projects, one thread's status and tasks) and every answer's time is kept. A second SQLite connection reads the store every
     20 ms, as another process would, and counts "database is locked".
  3. Two running tasks are stopped. The two waiting requests must start, and only then.
  4. The host is shut down mid-run and started again on the same store. Runs it was driving come back as Disconnected,
     never restarted; nothing else starts on its own.
  5. N new requests run to the end on the restarted host.
Every start the fake CLI makes is logged, so a task that started twice shows. Peak memory, CPU and processes (host and
fake CLIs) are sampled every 250 ms.

    python3 tools/load_parallel_runs.py [--limit 10] [--seconds 6] [--out evidence/load-parallel-runs]
    python3 tools/load_parallel_runs.py --try 10,8,6,5   # stops at the first limit that passes

The fake CLI is a small Python process (about 15 MB), so memory here is Dervo's side; Claude Code itself measured about
280 MB a run on 23 Sep, which only the live check shows. CPU here is Dervo's side too: the fake does no model work.
"""
import argparse
import json
import math
import os
import select
import sqlite3
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


class Host:
    """One host process and its JSON-lines channel, one request at a time, each answer timed."""

    def __init__(self, data, wrapper, limit, errors, timings):
        self.errors = errors
        self.process = subprocess.Popen([PYTHON, '-B', str(ROOT / 'tools/load_parallel_host.py'), str(data), str(wrapper), str(limit)],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(errors, 'ab'), cwd=BACKEND)
        self.timings = timings  # shared across a restart, so both hosts' answers count
        self.lock = threading.Lock()

    def call(self, operation, args=None, *, timed=None):
        with self.lock:
            started = time.monotonic()
            self.process.stdin.write((json.dumps({'operation': operation, 'args': args or {}}) + '\n').encode())
            self.process.stdin.flush()
            ready, _, _ = select.select([self.process.stdout], [], [], 15)
            if not ready:
                raise RuntimeError(f'{operation} got no answer in 15 s')
            answer = json.loads(self.process.stdout.readline())
            took = time.monotonic() - started
            if timed:
                self.timings.append((timed, took))
        if 'error' in answer:
            raise RuntimeError(f'{operation}: {answer["error"]}')
        return answer['result']

    def shutdown(self):
        with self.lock:
            self.process.stdin.write(b'{"operation":"_shutdown","args":{}}\n')
            self.process.stdin.flush()
        self.process.wait(timeout=60)


def tree(root_pid):
    """{pid: (ppid, rss_kb, cpu_percent, cpu_seconds, command)} for root_pid and everything below it."""
    rows = {}
    out = subprocess.run(['ps', '-axo', 'pid=,ppid=,rss=,pcpu=,time=,command='], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        pid, ppid, rss, pcpu, cputime, command = parts
        minutes, _, seconds = cputime.rpartition(':')
        hours, _, minutes = minutes.rpartition(':')
        rows[int(pid)] = (int(ppid), int(rss), float(pcpu), (int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds)), command)
    keep, grew = {root_pid}, True
    while grew:
        grew = False
        for pid, row in rows.items():
            if row[0] in keep and pid not in keep:
                keep.add(pid)
                grew = True
    return {pid: rows[pid] for pid in keep if pid in rows}


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.host_pid, self.stop, self.samples, self.cpu = None, threading.Event(), [], {}

    def run(self):
        while not self.stop.is_set():
            if self.host_pid:
                rows = tree(self.host_pid)
                fakes = [pid for pid, row in rows.items() if 'fake_claude_load.py' in row[4]]
                for pid, row in rows.items():
                    self.cpu[pid] = max(self.cpu.get(pid, 0), row[3])
                self.samples.append({'at': time.time(), 'fakes': len(fakes), 'processes': len(rows),
                                     'rss_mb': sum(row[1] for row in rows.values()) / 1024,
                                     'host_rss_mb': rows.get(self.host_pid, (0, 0))[1] / 1024,
                                     'cpu_percent': sum(row[2] for row in rows.values()),
                                     'host_cpu_percent': rows.get(self.host_pid, (0, 0, 0))[2]})
            self.stop.wait(.25)


class Reader(threading.Thread):
    """Another connection to the same store, reading every 20 ms, as a second process would."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path, self.stop, self.reads, self.locked, self.other = path, threading.Event(), 0, 0, []

    def run(self):
        while not self.stop.is_set():
            try:
                db = sqlite3.connect(self.path, timeout=5)
                db.execute('SELECT count(*) FROM coding_tasks').fetchone()
                db.close()
                self.reads += 1
            except sqlite3.OperationalError as exc:
                if 'locked' in str(exc):
                    self.locked += 1
                elif 'no such table' not in str(exc):
                    self.other.append(str(exc))
            self.stop.wait(.02)


def run(limit, seconds, out):
    temp = Path(tempfile.mkdtemp(prefix='dervo-load-'))
    data, log, errors = temp / 'data', temp / 'starts.jsonl', temp / 'host-errors.log'
    wrapper = temp / 'claude'
    wrapper.write_text(f'#!/bin/sh\nFAKE_LOAD_SECONDS={seconds} FAKE_LOAD_LOG={log} '
                       f'exec {PYTHON} -B {BACKEND / "fake_claude_load.py"} "$@"\n')
    wrapper.chmod(0o755)
    findings, notes = [], []
    sampler, reader = Sampler(), Reader(data / 'conversations.sqlite3')
    sampler.start()
    timings = []
    host = Host(data, wrapper, limit, errors, timings)
    sampler.host_pid = host.process.pid
    polling = threading.Event()
    try:
        profile = host.call('add_profile', {'provider': 'claude', 'identity': 'load-test@example.test', 'category': 'personal'})['id']
        projects, threads = [], []
        for index in range(2):
            folder = temp / f'project-{index + 1}'
            folder.mkdir()
            (folder / 'README.md').write_text('A load test project.\n')
            projects.append(host.call('_picked_project', {'path': str(folder), 'profile_id': profile, 'model': MODEL})['id'])
        count = limit + 2
        for index in range(count):
            project = projects[index % 2]
            threads.append((project, host.call('create_conversation', {'projectId': project, 'name': f'Load {index + 1}', 'private': False})['id']))
        reader.start()

        def poll():
            # The renderer's steady reads while work runs: the sidebar for both projects, and the open thread.
            while not polling.is_set():
                try:
                    for project in projects:
                        host.call('conversations', {'projectId': project}, timed='conversations')
                        host.call('orchestrator_list', {'projectId': project, 'profileId': profile}, timed='home')
                        host.call('project_pickup', {'projectId': project, 'profileId': profile}, timed='home_pickup')
                    host.call('conversation_status', {'projectId': threads[0][0], 'channelId': threads[0][1]}, timed='conversation_status')
                    host.call('tasks', {'projectId': threads[0][0], 'channelId': threads[0][1]}, timed='tasks')
                except Exception as exc:  # noqa: BLE001 - a failed poll is a finding, not a crash
                    findings.append(f'A poll failed: {exc}')
                time.sleep(.2)
        poller = threading.Thread(target=poll, daemon=True)

        # 1. N + 2 requests at once.
        sent = []
        for index, (project, thread) in enumerate(threads):
            result = host.call('coding_request', {'projectId': project, 'channelId': thread, 'clientId': f'load-{index + 1}',
                                                  'text': f'Load test request {index + 1}.', 'readOnly': True}, timed='coding_request')
            sent.append(result)
        poller.start()
        started = [r for r in sent if r.get('taskId')]
        waiting = [r for r in sent if r.get('state') == 'queued']
        if len(started) != limit:
            findings.append(f'{len(started)} requests started at once, not {limit}.')
        if len(waiting) != 2 or any(r.get('why') != 'slot' for r in waiting):
            findings.append(f'The two past the limit should wait for a slot: {[(r.get("state"), r.get("why")) for r in sent[limit:]]}')
        else:
            notes.append(f'Request {limit + 1} and {limit + 2} waited: "{waiting[0]["detail"]}"')
        # Wait for the runs to be up (a busy machine starts processes slowly), then judge the peak.
        up_by = time.monotonic() + 15
        while time.monotonic() < up_by and max((s['fakes'] for s in sampler.samples), default=0) < limit:
            time.sleep(.1)
        live = max((s['fakes'] for s in sampler.samples), default=0)
        if live != limit:
            tail = errors.read_text(errors='replace')[-600:] if errors.exists() else ''
            findings.append(f'{live} fake Claude Code processes ran at once, not {limit}.' + (f' The host said: {tail}' if tail else ''))

        # 2. Stop two running tasks: the two waiting requests start, and only then.
        for result, (project, thread) in list(zip(sent, threads))[:2]:
            host.call('task_stop', {'projectId': project, 'channelId': thread, 'taskId': result['taskId']}, timed='task_stop')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            late = [host.call('tasks', {'projectId': project, 'channelId': thread}) for project, thread in threads[limit:]]
            if all(len(tasks) == 1 for tasks in late):
                break
            time.sleep(.2)
        else:
            findings.append('The waiting requests didn’t start within 20 s of two slots freeing up.')
        notes.append('After two Stops, both waiting requests started.' if all(len(t) == 1 for t in late) else 'The waiting requests never started.')
        peak_after_stop = max((s['fakes'] for s in sampler.samples[-8:]), default=0)
        if peak_after_stop > limit:
            findings.append(f'{peak_after_stop} runs at once after the stops, over the limit of {limit}.')

        # 3. Restart the host mid-run.
        time.sleep(.5)
        polling.set()
        poller.join(timeout=20)
        before = {task['id']: task['state'] for project, thread in threads for task in host.call('tasks', {'projectId': project, 'channelId': thread})}
        starts_before = len(log.read_text().splitlines()) if log.exists() else 0
        if not starts_before:
            tail = errors.read_text(errors='replace')[-600:] if errors.exists() else ''
            findings.append('No fake Claude Code run ever started.' + (f' The host said: {tail}' if tail else ''))
        shutdown_at = time.monotonic()
        host.shutdown()
        restart_seconds = time.monotonic() - shutdown_at
        host = Host(data, wrapper, limit, errors, timings)
        sampler.host_pid = host.process.pid
        after = {task['id']: task for project, thread in threads for task in host.call('tasks', {'projectId': project, 'channelId': thread})}
        if set(after) != set(before):
            findings.append(f'Tasks changed across the restart: {sorted(set(before) ^ set(after))}')
        was_running = [task_id for task_id, state in before.items() if state == 'working']
        came_back = {after[task_id]['state'] for task_id in was_running if task_id in after}
        notes.append(f'{len(was_running)} runs were going at the restart; they came back as {sorted(came_back) or "nothing"}, and none restarted.')
        if came_back - {'disconnected', 'answered', 'blocked'}:
            findings.append(f'Runs going at the restart came back as {sorted(came_back)}.')
        time.sleep(2)
        starts_after = len(log.read_text().splitlines()) if log.exists() else 0
        if starts_after != starts_before:
            findings.append(f'{starts_after - starts_before} runs started on their own after the restart.')

        # 4. N new requests on the restarted host, polled to the end.
        polling.clear()
        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        second = []
        for index, (project, thread) in enumerate(threads[:limit]):
            second.append(host.call('coding_request', {'projectId': project, 'channelId': thread, 'clientId': f'load-again-{index + 1}',
                                                       'text': f'Second round {index + 1}.', 'readOnly': True}, timed='coding_request'))
        if sum(1 for r in second if r.get('taskId')) != limit:
            findings.append(f'After the restart only {sum(1 for r in second if r.get("taskId"))} of {limit} started.')
        deadline = time.monotonic() + seconds + 30
        while time.monotonic() < deadline:
            states = [task['state'] for project, thread in threads[:limit] for task in host.call('tasks', {'projectId': project, 'channelId': thread})]
            if 'working' not in states:
                break
            time.sleep(.3)
        else:
            findings.append('The second round didn’t finish in time.')
        polling.set()
        poller.join(timeout=20)
        final = [task for project, thread in threads for task in host.call('tasks', {'projectId': project, 'channelId': thread})]
        host.shutdown()
    finally:
        polling.set()
        reader.stop.set()
        sampler.stop.set()
        if host.process.poll() is None:
            host.process.kill()
        sampler.join(timeout=5)
        reader.join(timeout=5)

    # ---------------------------------------------------------------- what happened
    starts = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    sessions = [start['session'] for start in starts]
    doubled = sorted({s for s in sessions if sessions.count(s) > 1})
    if doubled:
        findings.append(f'{len(doubled)} runs started twice: {doubled[:3]}')
    if len(starts) != len(final):
        findings.append(f'{len(starts)} runs started for {len(final)} tasks.')
    host_log = errors.read_text(errors='replace') if errors.exists() else ''
    if 'database is locked' in host_log:
        findings.append('The host logged "database is locked".')
    if reader.locked:
        findings.append(f'A second connection saw "database is locked" {reader.locked} times in {reader.reads} reads.')
    failures = [line for line in host_log.splitlines() if 'request failed' in line]
    if failures:
        findings.append(f'{len(failures)} host requests failed: {failures[:3]}')
    answers = {}
    for name in sorted({name for name, _ in timings}):
        values = sorted(took * 1000 for kind, took in timings if kind == name)
        answers[name] = {'count': len(values), 'p50_ms': round(statistics.median(values), 1),
                         'p95_ms': round(values[min(len(values) - 1, math.ceil(len(values) * .95) - 1)], 1), 'max_ms': round(values[-1], 1)}
    for name in ('conversations', 'home', 'home_pickup', 'conversation_status', 'tasks'):
        if name in answers and (answers[name]['p95_ms'] > 250 or answers[name]['max_ms'] > 1000):
            findings.append(f'{name} was slow under load: p95 {answers[name]["p95_ms"]} ms, max {answers[name]["max_ms"]} ms.')
    samples = sampler.samples
    peak = lambda key: round(max((s[key] for s in samples), default=0), 1)
    result = {
        'limit': limit, 'run_seconds': seconds, 'passed': not findings, 'findings': findings, 'notes': notes,
        'tasks': len(final), 'starts': len(starts), 'final_states': {state: sum(1 for t in final if t['state'] == state) for state in sorted({t['state'] for t in final})},
        'peak': {'fake_processes': peak('fakes'), 'processes': peak('processes'), 'rss_mb_total': peak('rss_mb'), 'host_rss_mb': peak('host_rss_mb'),
                 'cpu_percent_total': peak('cpu_percent'), 'host_cpu_percent': peak('host_cpu_percent')},
        'cpu_seconds': {'host_and_children': round(sum(sampler.cpu.values()), 2)},
        'restart_seconds': round(restart_seconds, 2),
        'second_connection': {'reads': reader.reads, 'locked': reader.locked, 'other_errors': reader.other[:3]},
        'response_times': answers,
        'machine': {'cores': os.cpu_count(), 'memory_gb': round(int(subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True).stdout or 0) / 2**30, 1)},
    }
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f'limit-{limit}.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--try', dest='tries', default='')
    parser.add_argument('--seconds', type=float, default=6)
    parser.add_argument('--out', type=Path, default=ROOT / 'evidence/load-parallel-runs')
    args = parser.parse_args()
    limits = [int(value) for value in args.tries.split(',') if value] or [args.limit]
    for limit in limits:
        result = run(limit, args.seconds, args.out)
        print(json.dumps(result, indent=2))
        if result['passed']:
            print(f'\nPASSED at {limit}.')
            return 0
        print(f'\nFAILED at {limit}: ' + ' '.join(result['findings']))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
