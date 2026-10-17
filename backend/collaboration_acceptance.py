"""Bounded live acceptance runner for the coding collaboration. Prepared here; the live form is opt-in and was not run.

What it exercises: the actual ``Host``, ``CodingTasks``, ``Collaboration`` broker, evidence and confined tests, through the
current Claude and Codex coding provider paths (``claude_coding``, ``codex_coding``), on a disposable store and a disposable
fixture project. Three roles: a Claude owner (the conversation the request is sent from), a Claude worker agent ``fixer``
and a Codex reviewer agent ``reviewer``, under an ``automatic`` project policy. The prompt asks for a concrete coding
outcome; whether the model delegates, parallelises or asks nobody is its own choice and is reported as observed. Nothing
here scripts a live provider's answer, forces a tool call or retries a provider turn.

Fail closed:
- no flags: the plan is printed and the process exits 3; no directory, store, process or file is created;
- ``--allow-live`` needs the exact profile directories, identities and models, all supplied; anything missing exits 2
  before a Host exists. Profile directories are bound the official way (``CLAUDE_CONFIG_DIR`` for Claude, ``CODEX_HOME``
  for Codex, through the app's own ``clean_environment``); no credential is read, copied or proxied by this runner;
- ``--simulate`` runs the same mechanics with the synthetic provider peers of the test suite. Their answers are scripted,
  so a simulated report proves the plumbing and the criteria evaluation, never model judgement (``model_judgement_exercised``
  is false in that report);
- at most ``MAX_STARTS`` (3) provider starts: one owner, one helper, one reviewer. A fourth start is refused by a guard on
  ``CodingTasks.start`` in this process, independently of the project policy (which also allows only two asks).

Cost unit: provider starts (each one provider turn), reported exactly; never a promised share of an account.

One deliberate runner-only seam, stated: the app pins an owned profile's home to ``<store>/profiles/<id>``. A disposable
store cannot contain an existing authorized profile without copying it, which is forbidden, so in live mode this process
lets ``connections_ui.owned_profile`` accept exactly the supplied directories for exactly the profile ids it created, with
every other check (existing, not a symlink, personal category, provider) kept. The app itself is not changed.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from dervo_connections import Rejected  # noqa: E402

MAX_STARTS = 3
ROLES = ('claude-owner', 'claude-helper', 'codex-reviewer')
PROVIDER_OF = {'claude-owner': 'claude', 'claude-helper': 'claude', 'codex-reviewer': 'codex'}
SCOPE = ['greeting.py', 'test_sample.py']
TEST_FILE = 'test_sample.py'
FIXTURE = {
    'greeting.py': 'def greet():\n    return "hello"\n',
    'test_sample.py': ('import unittest\nfrom greeting import greet\n\n\nclass Sample(unittest.TestCase):\n'
                       '    def test_greet_says_hi(self):\n        self.assertEqual(greet(), "hi")\n\n\n'
                       'if __name__ == "__main__":\n    unittest.main()\n'),
    'notes.py': '# out of scope: this file must stay unchanged\n',
}
PROMPT = ('test_sample.py fails right now: test_greet_says_hi expects greet() in greeting.py to return "hi". Make that test '
          'pass with the smallest correct change to greeting.py and verify it with the host tests. The helpers listed in your '
          'context are available; use them if that genuinely helps this request, and integrate whatever comes back. Never claim '
          'a change or a test run you did not make through the tools.')
REVIEWER_INSTRUCTIONS = 'Review the sent files for correctness against the test. Point at exact lines. Answer in text only.'
FIXER_INSTRUCTIONS = 'Make the smallest change that makes the tests pass, verify with the host tests, then summarise what you changed.'
POLICY = {'mode': 'automatic', 'maxParallel': 3, 'maxDepth': 1, 'maxCalls': 2}  # two asks at most: one helper start each
ADAPTER_BOUNDS = {'tool_calls_per_provider_turn': 256, 'pending_tool_calls_per_turn': 16, 'preflight_seconds': 120,
                  'turn_seconds_after_context': 1800, 'stop_grace_seconds': 10, 'confined_test_seconds': 5,
                  'confined_test_output_bytes': 65536, 'packet_records_per_task': 16}
CRITERIA = ('owner_finished_with_verified_result', 'delegated_review_returned', 'delegated_work_returned_verified', 'three_roles_ran',
            'three_roles_concurrent', 'packets_visible_cross_channel', 'reviewer_result_consumed_by_owner', 'scoped_files_only',
            'final_tests_pass', 'provider_starts_within_cap', 'no_provider_retries')
EXIT_PASS, EXIT_FAIL, EXIT_SETUP, EXIT_PLAN = 0, 1, 2, 3


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def mask(identity):
    if not isinstance(identity, str) or '@' not in identity:
        return '***'
    local, domain = identity.split('@', 1)
    return (local[:1] + '***@' + domain) if local else '***@' + domain


def redact(value, secrets):
    """Mask the supplied account identities wherever they appear and drop bulky runtime listings. Strings only; no guessing."""
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, mask(secret))
        return value
    if isinstance(value, dict):
        return {k: ('[omitted]' if k == 'runtime_files' else redact(v, secrets)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    return value


class Evidence:
    """Incremental, redacted records so a crashed or interrupted run still leaves what happened."""

    def __init__(self, directory, secrets):
        self.dir, self.secrets = Path(directory), list(secrets)
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, name, value):
        path = self.dir / name
        path.write_text(json.dumps(redact(value, self.secrets), indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        return path

    def progress(self, event):
        with (self.dir / 'progress.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps(redact({'at': time.time(), **event}, self.secrets), ensure_ascii=False) + '\n')


class StartGuard:
    """Counts every provider start of this Host and refuses the (limit+1)th, whatever the policy says. Never retries."""

    def __init__(self, coding, limit, evidence=None):
        self.coding, self.limit, self.evidence, self.starts, self.refused = coding, limit, evidence, [], 0
        self.original = coding.start
        coding.start = self

    def __call__(self, task):
        role = task.get('role') or 'owner'
        if len(self.starts) >= self.limit:
            self.refused += 1
            if self.evidence:
                self.evidence.progress({'event': 'start_refused_by_cap', 'role': role, 'agent': task.get('agent'), 'task_id': task['id']})
            raise Rejected(f'Acceptance cap: {self.limit} provider starts already used; a further start is refused, nothing is retried')
        record = {'number': len(self.starts) + 1, 'role': role, 'agent': task.get('agent'), 'provider': task['binding']['provider'],
                  'model': task['binding']['model'], 'profile_id': task['binding']['profile_id'], 'task_id': task['id'],
                  'thread_id': task['context']['thread_id'], 'at': time.time()}
        self.starts.append(record)
        if self.evidence:
            self.evidence.progress({'event': 'provider_start', **record})
        return self.original(task)


def bind_external_profiles(mapping):
    """Runner-process-only seam (see the module docstring). ``mapping`` is {profile_id: resolved existing directory}."""
    import connections_ui
    original = connections_ui.owned_profile

    def owned_profile(store, root, profile_id):
        expected = mapping.get(profile_id)
        if expected is None:
            return original(store, root, profile_id)
        row = store.db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
        if row is None:
            raise Rejected('Unknown account profile')
        profile = dict(row)
        home = Path(profile['home'])
        if home != expected or home.is_symlink() or not home.is_dir():
            raise Rejected('Acceptance profile directory no longer matches the supplied path')
        if profile['category'] != 'personal':
            raise Rejected('Work account use needs separate authorisation. Choose a personal profile.')
        return profile
    connections_ui.owned_profile = owned_profile
    return lambda: setattr(connections_ui, 'owned_profile', original)


def parse(argv):
    parser = argparse.ArgumentParser(description='Bounded live acceptance of the coding collaboration (fail closed).')
    parser.add_argument('--allow-live', action='store_true', help='start real provider turns (at most 3) with the supplied profiles')
    parser.add_argument('--simulate', action='store_true', help='run the mechanics with the synthetic provider peers; no model, no profile')
    parser.add_argument('--preflight-only', action='store_true', help='with --allow-live: official identity status per profile, no coding turn')
    for role in ROLES:
        parser.add_argument(f'--{role}-dir', help=f'existing authorized {PROVIDER_OF[role]} profile directory for the {role}')
    parser.add_argument('--claude-identity', help='exact account email the Claude profile(s) must report')
    parser.add_argument('--codex-identity', help='exact account email the Codex profile must report')
    parser.add_argument('--claude-model', help='exact Claude model id for the owner and the helper')
    parser.add_argument('--codex-model', help='exact Codex model id for the reviewer')
    parser.add_argument('--prompt-file', help='text file with the owner request; default is the built-in concrete coding outcome')
    parser.add_argument('--evidence-dir', help='where to write the run records (default evidence/collaboration-acceptance/<stamp>-<mode>)')
    parser.add_argument('--work-dir', help='disposable store and project location (default a new directory under /private/tmp)')
    parser.add_argument('--timeout', type=float, default=None, help='seconds to wait for the owner run (default 1200 live, 60 simulated)')
    return parser.parse_args(argv)


def plan_text(args):
    return ('collaboration_acceptance: nothing was started.\n'
            f'Roles: {", ".join(ROLES)}. Provider starts: at most {MAX_STARTS} (one each). No automatic provider retries.\n'
            f'Policy: {POLICY}. Scope: {SCOPE}, test file {TEST_FILE}. Fixture files: {sorted(FIXTURE)}.\n'
            'Live invocation (opt-in, all values required):\n'
            '  /usr/local/bin/python3 -B collaboration_acceptance.py --allow-live \\\n'
            '    --claude-owner-dir <dir> --claude-helper-dir <dir> --codex-reviewer-dir <dir> \\\n'
            '    --claude-identity <email> --codex-identity <email> --claude-model <id> --codex-model <id>\n'
            'Mechanics check without any model: --simulate\n')


def validate_live(args):
    """Every live input exact and present, or a setup refusal before anything exists."""
    problems = []
    dirs = {}
    for role in ROLES:
        value = getattr(args, role.replace('-', '_') + '_dir')
        if not value:
            problems.append(f'--{role}-dir is required')
            continue
        path = Path(value)
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            problems.append(f'--{role}-dir must be an existing absolute directory that is not a symlink: {value}')
            continue
        dirs[role] = path.resolve()
    for name in ('claude_identity', 'codex_identity', 'claude_model', 'codex_model'):
        value = getattr(args, name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f'--{name.replace("_", "-")} is required')
    if not problems:
        if dirs['codex-reviewer'] in (dirs['claude-owner'], dirs['claude-helper']):
            problems.append('the Codex reviewer directory must differ from the Claude directories')
        if '@' not in args.claude_identity or '@' not in args.codex_identity:
            problems.append('identities are account emails')
    return dirs, problems


def make_fixture(project):
    project.mkdir(parents=True, exist_ok=False)
    for name, text in FIXTURE.items():
        (project / name).write_text(text, encoding='utf-8')
    return {name: sha((project / name).read_bytes()) for name in sorted(FIXTURE)}


def final_tests(project):
    """The real unittest run on the final files, outside the host: the same command shape as the confined runner."""
    try:
        result = subprocess.run([sys.executable, '-I', '-B', '-m', 'unittest', 'discover', '-s', '.', '-p', TEST_FILE], cwd=project,
                                capture_output=True, text=True, timeout=60)
        return {'exit_status': result.returncode, 'stdout': result.stdout[-4000:], 'stderr': result.stderr[-4000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'exit_status': None, 'stdout': '', 'stderr': f'final test run did not complete: {exc}'}


def simulated_script(project):
    """Scripted synthetic peers for the mechanics check. Labelled simulated in every record; never used live."""
    import test_collaboration as tc

    def script(peer, text):
        role = tc.CollaborationTests.role_of(text)
        if role == 'reviewer':
            return [('tool', 'dervo_read_file', {'path': 'greeting.py'}),
                    ('finish', 'REVIEW (simulated): greet() returns "hello"; test_sample.py expects "hi". Change line 2 of greeting.py.')]
        if role == 'worker':
            current = (project / 'greeting.py').read_bytes()
            return [('tool', 'dervo_read_file', {'path': 'greeting.py'}),
                    ('tool', 'dervo_write_file', {'path': 'greeting.py', 'expected_hash': sha(current), 'content': 'def greet():\n    return "hi"\n'}),
                    ('tool', 'dervo_run_tests', {}), ('finish', 'FIX (simulated): greet() now returns "hi"; host tests pass.')]
        return [('tool', 'dervo_read_file', {'path': 'test_sample.py'}),
                ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'Review greeting.py against test_sample.py',
                                                'instructions': 'Say exactly what must change.', 'files': ['greeting.py', 'test_sample.py']}, 'parallel'),
                ('tool', 'dervo_request_help', {'agent': 'fixer', 'purpose': 'Make test_sample.py pass by fixing greeting.py',
                                                'instructions': 'Smallest correct change, verified.', 'files': ['greeting.py']}),
                ('tool', 'dervo_run_tests', {}), ('finish', 'Integrated (simulated): the review and the fix; host tests pass.')]
    return script


class Acceptance:
    def __init__(self, args, mode, evidence, work, secrets):
        self.args, self.mode, self.evidence, self.work, self.secrets = args, mode, evidence, Path(work), secrets
        self.project = self.work / 'project'
        self.host = None
        self.guard = None
        self.cleanups = []
        self.peak, self.peak_roles = 0, []
        self.timed_out = False

    # ------------------------------------------------------------------ setup
    def setup(self, prompt):
        from host import Host
        before = make_fixture(self.project)
        self.evidence.save('fixture-before.json', before)
        self.host = Host(self.work / 'store')
        host, args = self.host, self.args
        if self.mode == 'simulated':
            import test_collaboration as tc
            claude_identity, codex_identity, claude_model, codex_model = tc.CLAUDE_IDENTITY, tc.CODEX_IDENTITY, tc.MODEL, tc.MODEL
            owner = host.dispatch('add_profile', {'provider': 'claude', 'identity': claude_identity, 'category': 'personal'})['id']
            helper = host.dispatch('add_profile', {'provider': 'claude', 'identity': claude_identity, 'category': 'personal'})['id']
            reviewer = host.dispatch('add_profile', {'provider': 'codex', 'identity': codex_identity, 'category': 'personal'})['id']
        else:
            dirs = self.dirs
            claude_identity, codex_identity, claude_model, codex_model = args.claude_identity, args.codex_identity, args.claude_model, args.codex_model
            mapping = {}
            owner, helper, reviewer = 'claude-owner', 'claude-helper', 'codex-reviewer'
            if dirs['claude-helper'] == dirs['claude-owner']:
                helper = owner  # one authorized Claude profile serving both Claude roles, two conversations
            if PROVIDER_OF['codex-reviewer'] == PROVIDER_OF['claude-owner'] and dirs['codex-reviewer'] == dirs['claude-owner']:
                reviewer = owner  # separate reviewer conversation, same authorized provider profile
            for profile_id, role in ((owner, 'claude-owner'), (helper, 'claude-helper'), (reviewer, 'codex-reviewer')):
                if profile_id in mapping:
                    continue
                host.store.add_profile(profile_id, PROVIDER_OF[role], dirs[role], claude_identity if PROVIDER_OF[role] == 'claude' else codex_identity, 'personal')
                mapping[profile_id] = dirs[role]
            self.cleanups.append(bind_external_profiles(mapping))
        project = host.add_picked_project(str(self.project), owner, claude_model)['id']
        main = host.dispatch('create_conversation', {'projectId': project, 'name': 'Acceptance owner', 'private': False})['id']
        review_room = host.dispatch('create_agent_conversation', {'projectId': project, 'profileId': reviewer, 'model': codex_model, 'name': 'Reviewer room'})['id']
        work_room = host.dispatch('create_agent_conversation', {'projectId': project, 'profileId': helper, 'model': claude_model, 'name': 'Fixer room'})['id']
        profiles = sorted({owner, helper, reviewer})
        if len(profiles) > 1:
            host.dispatch('harness_boundary', {'profileIds': profiles})
        host.dispatch('collab_agent_save', {'name': 'reviewer', 'profileId': reviewer, 'channelId': review_room, 'projectId': project, 'role': 'reviewer', 'instructions': REVIEWER_INSTRUCTIONS})
        host.dispatch('collab_agent_save', {'name': 'fixer', 'profileId': helper, 'channelId': work_room, 'projectId': project, 'role': 'worker', 'instructions': FIXER_INSTRUCTIONS})
        self.scope = {'projectId': project, 'channelId': main}
        self.rooms = {'owner': main, 'reviewer': review_room, 'fixer': work_room}
        host.dispatch('collab_policy_set', {**self.scope, 'agents': ['fixer', 'reviewer'], 'channels': [main], **POLICY})
        host.dispatch('coding_permission_grant', {**self.scope, 'files': list(SCOPE), 'testFile': TEST_FILE})
        # The criteria judge a finished result awaiting human review: result approval is configured on explicitly.
        host.dispatch('coding_acceptance_set', {**self.scope, 'required': True})
        status = host.dispatch('conversation_status', self.scope)
        self.evidence.save('setup.json', {'mode': self.mode, 'project_id': project, 'rooms': self.rooms, 'profiles': {'owner': owner, 'helper': helper, 'reviewer': reviewer},
                                          'models': {'claude': claude_model, 'codex': codex_model}, 'policy': POLICY, 'scope': SCOPE, 'test_file': TEST_FILE,
                                          'prompt': prompt, 'agents': host.dispatch('collab_agents', {}), 'collaboration': status['collaboration'],
                                          'coding': status['coding'], 'fixture_before': before})
        collab = status['collaboration']
        if collab['state'] != 'automatic' or sorted(a['name'] for a in collab['available']) != ['fixer', 'reviewer']:
            raise Rejected('Setup did not reach an automatic policy with both agents available: ' + json.dumps(collab)[:1500])
        self.guard = StartGuard(host.coding, MAX_STARTS, self.evidence)
        return before

    # ------------------------------------------------------------------ run
    def run(self, prompt, timeout):
        host = self.host
        started = host.dispatch('coding_request', {**self.scope, 'clientId': 'acceptance-1', 'text': prompt})
        self.evidence.save('request.json', started)
        if not started.get('taskId'):
            raise Rejected('The request did not start a task: ' + json.dumps(started)[:1500])
        owner_id = started['taskId']
        deadline = time.monotonic() + timeout
        last = None
        while True:
            host.tick()
            live = [tid for tid, turn in host.coding.turns.items() if not turn.closed]
            if len(live) > self.peak:
                self.peak = len(live)
                rows = [json.loads(host.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (tid,)).fetchone()[0]) for tid in live]
                self.peak_roles = sorted(t.get('agent') or 'owner' for t in rows)
                self.evidence.progress({'event': 'peak_concurrency', 'live_turns': self.peak, 'roles': self.peak_roles})
            detail = host.dispatch('task_detail', {**self.scope, 'taskId': owner_id})
            snapshot = (detail['task']['state'], [(p['agent'], p['status']) for p in detail['collaboration']['packets']],
                        [(t['tool'], t['state']) for t in detail['task']['tokens']])
            if snapshot != last:
                last = snapshot
                self.evidence.progress({'event': 'owner', 'state': snapshot[0], 'packets': snapshot[1], 'tokens': snapshot[2]})
            if detail['task']['state'] != 'working' and not live:
                break
            if time.monotonic() > deadline:
                if self.timed_out:
                    break  # the stop was not confirmed within the grace period; records show what is known
                self.timed_out = True
                self.evidence.progress({'event': 'timeout', 'seconds': timeout, 'action': 'task_stop on the owner; nothing is retried'})
                host.dispatch('task_stop', {**self.scope, 'taskId': owner_id})
                deadline = time.monotonic() + 20  # stop confirmation grace
            time.sleep(0.02)
        return owner_id

    # ------------------------------------------------------------------ collection and criteria
    def collect(self, owner_id, before):
        host = self.host
        owner = host.dispatch('task_detail', {**self.scope, 'taskId': owner_id})
        self.evidence.save('owner.json', owner)
        packets = host.dispatch('collab_packets', {**self.scope, 'taskId': owner_id})
        full = [host.dispatch('collab_packet', {**self.scope, 'packetId': p['id']}) for p in packets]
        self.evidence.save('packets.json', full)
        helpers = {}
        for agent in ('reviewer', 'fixer'):
            scope = {'projectId': self.scope['projectId'], 'channelId': self.rooms[agent]}
            tasks = host.dispatch('tasks', scope)
            helpers[agent] = {'status': host.dispatch('conversation_status', scope), 'tasks': tasks,
                              'details': [host.dispatch('task_detail', {**scope, 'taskId': t['id']}) for t in tasks],
                              'packets_readable_here': []}
            for p in packets:
                if p.get('child_task_id') and any(t['id'] == p['child_task_id'] for t in tasks):
                    try:
                        helpers[agent]['packets_readable_here'].append(host.dispatch('collab_packet', {**scope, 'packetId': p['id']})['id'])
                    except Rejected as exc:
                        helpers[agent]['packets_readable_here'].append({'id': p['id'], 'error': str(exc)})
        self.evidence.save('helpers.json', helpers)
        after = {name: sha((self.project / name).read_bytes()) for name in sorted(p.name for p in self.project.iterdir() if p.is_file())}
        tests = final_tests(self.project)
        (self.evidence.dir / 'final-tests.txt').write_text(json.dumps(tests, indent=1) + '\n', encoding='utf-8')
        return owner, packets, helpers, after, tests

    def criteria(self, owner, packets, helpers, before, after, tests):
        task = owner['task']
        attempts = owner['evidence']['attempts']
        last = attempts[-1] if attempts else None
        verified = bool(last and last['state'] == 'verified' and (last.get('test') or {}).get('exit_status') == 0 and not last['test'].get('stopped'))
        review = next((p for p in packets if p['kind'] == 'review' and p['status'] == 'returned'), None)
        work = next((p for p in packets if p['kind'] == 'work' and p['status'] == 'returned'), None)
        starts = self.guard.starts if self.guard else []
        roles_started = sorted(s['agent'] or 'owner' for s in starts)
        review_token = next((t for t in task['tokens'] if review and t.get('packet_id') == review['id']), None)
        consumed = bool(review and review_token and review_token['state'] == 'delivered' and review_token.get('success')
                        and task['runs'] and task['runs'][-1]['state'] == 'completed')
        changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
        all_tasks = [task] + [d['task'] for h in helpers.values() for d in h['details']]
        cross = (bool(packets) and all(any(d['task'].get('origin') == 'collaboration' and d['collaboration'].get('packet') for d in h['details'])
                                       and h['packets_readable_here'] and all(isinstance(x, str) for x in h['packets_readable_here'])
                                       for h in helpers.values()))
        out = {
            'owner_finished_with_verified_result': (task['state'] == 'ready_for_review' and verified,
                                                    f"owner state {task['state']}, last attempt {last['state'] if last else None}, error {task.get('error')!r}"),
            'delegated_review_returned': (review is not None, f"review packets: {[(p['agent'], p['status']) for p in packets if p['kind'] == 'review']}"),
            'delegated_work_returned_verified': (bool(work and (work.get('result') or {}).get('verified')),
                                                 f"work packets: {[(p['agent'], p['status'], (p.get('result') or {}).get('verified')) for p in packets if p['kind'] == 'work']}"),
            'three_roles_ran': (roles_started == ['fixer', 'owner', 'reviewer'], f'provider starts by role: {roles_started}'),
            'three_roles_concurrent': (self.peak == 3, f'peak live provider turns {self.peak} ({", ".join(self.peak_roles) or "none"})'),
            'packets_visible_cross_channel': (cross, f"packets in owner view: {len(packets)}; helper tasks: " + json.dumps({a: len(h['tasks']) for a, h in helpers.items()})),
            'reviewer_result_consumed_by_owner': (consumed, 'review returned and delivered into the owner turn, which then completed' if consumed
                                                  else f"review {review['status'] if review else 'none'}, token {review_token['state'] if review_token else 'none'}, owner run {task['runs'][-1]['state'] if task['runs'] else 'none'}"),
            'scoped_files_only': (bool(changed) and set(changed) <= set(SCOPE), f'changed files: {changed}'),
            'final_tests_pass': (tests['exit_status'] == 0, f"unittest exit {tests['exit_status']}: {tests['stderr'].strip().splitlines()[-1] if tests['stderr'].strip() else ''}"),
            'provider_starts_within_cap': (len(starts) <= MAX_STARTS and (self.guard.refused if self.guard else 0) == 0, f'{len(starts)} starts, cap {MAX_STARTS}, refused by cap {self.guard.refused if self.guard else 0}'),
            'no_provider_retries': (all(len(t['runs']) == 1 for t in all_tasks), f"runs per task: {[len(t['runs']) for t in all_tasks]}"),
        }
        return {name: {'pass': bool(out[name][0]), 'detail': out[name][1]} for name in CRITERIA}

    @staticmethod
    def tool_iterations(owner, helpers):
        out = {}
        for label, detail in [('owner', owner)] + [(a, d) for a, h in helpers.items() for d in h['details']]:
            tokens = detail['task']['tokens']
            counts = {}
            for t in tokens:
                counts[t['tool']] = counts.get(t['tool'], 0) + 1
            out[detail['task']['id']] = {'role': label, 'tool_calls': len(tokens), 'by_tool': counts, 'runs': len(detail['task']['runs']),
                                         'state': detail['task']['state'], 'attempts': len(detail['evidence']['attempts'])}
        return out

    def close(self):
        try:
            if self.host is not None:
                self.host.close()
        finally:
            for cleanup in reversed(self.cleanups):
                try:
                    cleanup()
                except Exception:
                    pass


def preflight(args, dirs, evidence):
    """Official identity status per supplied profile; no coding turn, no model call. Codex needs its app-server process once."""
    from connections_ui import classify, probe
    results = {}
    launches = 0
    for role in ROLES:
        provider = PROVIDER_OF[role]
        identity = args.claude_identity if provider == 'claude' else args.codex_identity
        profile = {'id': role, 'provider': provider, 'home': str(dirs[role]), 'identity': identity, 'category': 'personal'}
        try:
            if provider == 'claude':
                from claude_policy import auth_status, executable
                result = classify(profile, auth_status(executable(), str(dirs[role])))
                launches += 1
            else:
                result = probe(profile)
                launches += 1
        except Exception as exc:
            result = {'state': 'error', 'canSend': False, 'detail': str(exc)}
        results[role] = result
        evidence.progress({'event': 'preflight', 'role': role, 'state': result['state']})
    report = {'mode': 'preflight', 'results': results, 'process_launches_no_inference': launches, 'model_turns': 0,
              'passed': all(r['state'] == 'identity_verified' for r in results.values())}
    evidence.save('preflight.json', report)
    return report


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    if args.simulate and args.allow_live:
        print('Choose --simulate or --allow-live, not both.', file=sys.stderr)
        return EXIT_SETUP
    if not args.simulate and not args.allow_live:
        print(plan_text(args), end='')
        return EXIT_PLAN
    mode = 'simulated' if args.simulate else 'live'
    dirs, problems = ({}, []) if args.simulate else validate_live(args)
    if args.preflight_only and args.simulate:
        problems.append('--preflight-only needs --allow-live')
    if problems:
        print('Refused before anything was created:\n  ' + '\n  '.join(problems), file=sys.stderr)
        return EXIT_SETUP
    prompt = PROMPT
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding='utf-8').strip()
        except OSError as exc:
            print(f'Prompt file unreadable: {exc}', file=sys.stderr)
            return EXIT_SETUP
        if not prompt or len(prompt.encode()) > 32768:
            print('The prompt must be 1 byte to 32 KiB of text.', file=sys.stderr)
            return EXIT_SETUP
    secrets = [s for s in (args.claude_identity, args.codex_identity) if s]
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else BACKEND.parent / 'evidence' / 'collaboration-acceptance' / f'{stamp()}-{mode}'
    evidence = Evidence(evidence_dir, secrets)
    plan = {'mode': mode, 'roles': list(ROLES), 'max_provider_starts': MAX_STARTS, 'policy': POLICY, 'scope': SCOPE, 'test_file': TEST_FILE,
            'adapter_bounds': ADAPTER_BOUNDS, 'profile_dirs': {r: str(d) for r, d in dirs.items()}, 'identities': {'claude': mask(args.claude_identity) if args.claude_identity else None,
            'codex': mask(args.codex_identity) if args.codex_identity else None}, 'models': {'claude': args.claude_model, 'codex': args.codex_model},
            'prompt': prompt, 'model_judgement_exercised': mode == 'live', 'created_at': stamp()}
    evidence.save('plan.json', plan)
    if args.preflight_only:
        report = preflight(args, dirs, evidence)
        print(json.dumps(redact(report, secrets), indent=1))
        return EXIT_PASS if report['passed'] else EXIT_FAIL
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix='dervo-collab-acceptance-', dir='/private/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    timeout = args.timeout if args.timeout is not None else (60 if args.simulate else 1200)
    acceptance = Acceptance(args, mode, evidence, work, secrets)
    acceptance.dirs = dirs
    patches = None
    report = {'mode': mode, 'work_dir': str(work), 'evidence_dir': str(evidence_dir), 'passed': False, 'criteria': {}, 'model_judgement_exercised': mode == 'live'}
    try:
        if args.simulate:
            import test_collaboration as tc
            case = tc.CollaborationTests()
            case.peers, case.starts, case.inputs = [], [], {}
            patches = case.peer_patch(simulated_script(acceptance.project))
            patches.__enter__()
        before = acceptance.setup(prompt)
        owner_id = acceptance.run(prompt, timeout)
        owner, packets, helpers, after, tests = acceptance.collect(owner_id, before)
        criteria = acceptance.criteria(owner, packets, helpers, before, after, tests)
        report.update(criteria=criteria, passed=all(c['pass'] for c in criteria.values()), timed_out=acceptance.timed_out,
                      provider_starts=acceptance.guard.starts, provider_starts_refused_by_cap=acceptance.guard.refused, start_cap=MAX_STARTS,
                      peak_live_turns=acceptance.peak, peak_roles=acceptance.peak_roles, tool_iterations=acceptance.tool_iterations(owner, helpers),
                      adapter_bounds=ADAPTER_BOUNDS, fixture_before=before, files_after=after, final_tests=tests)
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}', provider_starts=acceptance.guard.starts if acceptance.guard else [])
        evidence.progress({'event': 'error', 'error': report['error']})
    finally:
        acceptance.close()
        if patches is not None:
            patches.__exit__(None, None, None)
        evidence.save('report.json', report)
    summary = {k: v for k, v in report.items() if k in ('mode', 'passed', 'error', 'timed_out', 'start_cap', 'peak_live_turns', 'work_dir', 'evidence_dir')}
    summary['provider_starts'] = len(report.get('provider_starts') or [])
    summary['criteria'] = {k: v['pass'] for k, v in report['criteria'].items()}
    print(json.dumps(redact(summary, secrets), indent=1))
    if 'error' in report:
        return EXIT_SETUP
    return EXIT_PASS if report['passed'] else EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
