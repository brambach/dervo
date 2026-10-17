"""Native backend hosting of the real Dervo harness Supervisor. Disposable stores, fake workers only.

The supervisor under test is the packaged ``dervo_harness`` library (backend/dervo_harness) driven through
backend/harness_adapter.py. No provider, credential, network or installed harness is touched. The only fake is the
worker port: a scripted ``WorkerPort`` that the library stamps ``is_fake`` on every run.
"""
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from dervo_connections import Rejected
from host import Host

BACKEND = Path(__file__).resolve().parent
IDLE = 'idle'


class FakeWorker:
    """Scripted ``dervo_harness.WorkerPort``. One step per drain; a step returning ``'idle'`` emits nothing and repeats."""
    name = 'fake-scripted-worker'
    is_fake = True
    model_name = 'fake-scripted-model'

    def __init__(self, scripts):
        self.scripts = dict(scripts)  # goal -> [step(turn) -> None | 'idle']
        self.state = {}
        self.starts, self.cancel_calls, self.steer_calls = [], [], []

    def start(self, assignment, actions):
        if assignment.goal not in self.scripts:
            raise RuntimeError(f'fake worker has no script for goal {assignment.goal!r}')
        ref = f'fake:{assignment.run_id}:{assignment.attempt}'
        self.state[ref] = {'assignment': assignment, 'actions': actions, 'step': 0, 'seq': 0, 'pending': [], 'done': False, 'memory': {}}
        self.starts.append(assignment)
        return ref

    def drain(self, ref):
        from dervo_harness import WorkerEvent
        st = self.state[ref]
        if not st['done']:
            steps = self.scripts[st['assignment'].goal]
            if st['step'] >= len(steps):
                self._emit(ref, 'failed', {'error': 'script ended without a result'})
                st['done'] = True
            else:
                try:
                    outcome = steps[st['step']](FakeTurn(self, ref))
                except Exception as exc:
                    self._emit(ref, 'failed', {'error': f'{type(exc).__name__}: {exc}'})
                    st['done'] = True
                else:
                    if outcome != IDLE:
                        st['step'] += 1
                        if not st['done']:
                            self._emit(ref, 'progress', {'note': f'fake step {st["step"]} complete'})
        return self._flush(ref)

    def steer(self, ref, note):
        self.steer_calls.append((ref, note))

    def cancel(self, ref):
        from dervo_harness import WorkerObservation
        self.cancel_calls.append(ref)
        st = self.state[ref]
        if not st['done']:
            st['done'] = True
            self._emit(ref, 'stopped', {'note': 'fake worker stopped on request'})
        return WorkerObservation('finished', events=self._flush(ref), note='fake worker exited')

    def attach(self, ref, actions):
        from dervo_harness import WorkerObservation
        st = self.state.get(ref)
        if st is None:
            return WorkerObservation('lost', note='fake worker has no record of this worker_ref')
        st['actions'] = actions
        return WorkerObservation('finished', events=self._flush(ref)) if st['done'] else WorkerObservation('running')

    def _emit(self, ref, kind, payload):
        from dervo_harness import WorkerEvent
        st = self.state[ref]
        st['seq'] += 1
        st['pending'].append(WorkerEvent(st['assignment'].run_id, st['assignment'].attempt, st['seq'], kind, payload))

    def _flush(self, ref):
        st = self.state[ref]
        events, st['pending'] = st['pending'], []
        return events


class FakeTurn:
    def __init__(self, worker, ref):
        st = worker.state[ref]
        self._worker, self._ref = worker, ref
        self.actions, self.assignment, self.memory = st['actions'], st['assignment'], st['memory']

    def result(self, summary, refs=()):
        self._worker._emit(self._ref, 'result', {'summary': summary, 'refs': list(refs)})
        self._worker.state[self._ref]['done'] = True


def idle(turn):
    return IDLE


def ask_dates(turn):
    """The lead helper asks @dates through the structured action, citing its own message as the source."""
    note = turn.actions.say('Need the date for the plan')
    delegation = turn.actions.request_help('@dates', 'Which date?', [note.ref], instructions='Answer with one date.')
    turn.memory['delegation'] = delegation.delegation_id


def ask_dates_slowly(turn):
    """Same structured ask, but the helper's script for this purpose never answers, so the delegation stays open."""
    note = turn.actions.say('Need the date, no hurry')
    turn.actions.request_help('@dates', 'Which date, slowly?', [note.ref])


DATE_ANSWER = 'Next Friday is the 18th.'
SCRIPTS = {
    'What date is next Friday?': [lambda t: t.result(DATE_ANSWER)],
    'Which date?': [lambda t: t.result(DATE_ANSWER)],
    'Which date, slowly?': [idle],
    'Idle work': [idle],
    'Plan the week': [ask_dates, lambda t: t.result('Plan drafted; the date is on its way.')],
    'Plan then wait': [ask_dates_slowly, idle],
    'Summarise the plan': [lambda t: t.result('Summary written.')],
}


def fake_helpers():
    from harness_port import HelperSpec
    return {'dates': HelperSpec(FakeWorker(SCRIPTS), 'fake', 'none', note='answers date questions'),
            'lead': HelperSpec(FakeWorker(SCRIPTS), 'fake', 'none', note='plans and asks dates')}


def harness_db(root):
    return sqlite3.connect(f'file:{root / "harness" / "dervo.sqlite3"}?mode=ro', uri=True)


class HarnessHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.helpers = fake_helpers()
        self.hosts = []
        self.addCleanup(lambda: [self.close_host(h) for h in list(self.hosts)])
        self.host = self.open_host()

    def open_host(self, *, root=None, helpers=None, factory=True):
        from harness_adapter import open_supervisor
        host = Host(root or self.base / 'store', supervisor_factory=open_supervisor if factory else None,
                    harness_workers=self.helpers if helpers is None else helpers)
        self.hosts.append(host)
        return host

    def close_host(self, host):
        if host in self.hosts:
            self.hosts.remove(host)
            host.close()

    def seed(self, host=None, *, private=False, category='personal'):
        host = host or self.host
        profile = host.dispatch('add_profile', {'provider': 'codex', 'identity': 'expected@example.test', 'category': category})['id']
        project = host.add_picked_project(str(self.project), profile, 'unverified-model')['id']
        channel = host.dispatch('create_conversation', {'projectId': project, 'name': 'Planning', 'private': private})['id']
        return {'projectId': project, 'channelId': channel}

    def ask(self, scope, client_id, text, helper, *, host=None):
        host = host or self.host
        return host.dispatch('harness_request', {**scope, 'clientId': client_id, 'text': text, 'mode': 'coordinate', 'helper': helper})

    def settle(self, scope, request_id, *, host=None, states=('completed', 'cancelled', 'failed', 'lost', 'unknown')):
        host = host or self.host
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            status = host.dispatch('harness_status', {**scope, 'requestId': request_id})
            if status['state'] in states:
                return status
            time.sleep(.001)
        self.fail('Harness request did not settle')

    def events(self, scope, request_id=None, *, host=None):
        rows = (host or self.host).dispatch('harness_events', {**scope, 'after': 0})
        return [e for e in rows if request_id is None or e['request_id'] == request_id]

    def test_named_helper_runs_in_the_conversation_room_and_the_result_links_back(self):
        scope = self.seed()
        before = self.host.dispatch('history', scope)
        # A fake helper is never what the composer coordinates with. With a Codex-capable library the personal Codex profile
        # resolves its own real helper; with an older library there is none. Either way the fakes are never offered.
        conversation = self.host.dispatch('conversation_status', scope)['harness']
        self.assertEqual(conversation['provider'], 'codex')
        self.assertNotIn(conversation['helper'], ('dates', 'lead'))
        self.assertEqual(conversation['state'], 'available' if conversation['helper'] else 'unavailable')
        if conversation['helper']:
            self.assertTrue(conversation['helper'].startswith('codex-'))
        started = self.ask(scope, 'ask-1', 'What date is next Friday?', 'dates')
        self.assertEqual((started['state'], started['helper'], started['provider']), ('queued', 'dates', 'fake'))
        self.assertEqual(self.ask(scope, 'ask-1', 'What date is next Friday?', 'dates')['id'], started['id'])
        with self.assertRaises(Rejected):
            self.ask(scope, 'ask-1', 'What date is next Friday?', 'lead')
        status = self.settle(scope, started['id'])
        self.assertEqual((status['state'], status['helper']), ('completed', 'dates'))
        self.assertEqual(status['linked_return']['summary'], DATE_ANSWER)
        self.assertTrue(status['linked_return']['worker_is_fake'])
        self.assertEqual(status['delegations'], [])
        kinds = [e['kind'] for e in self.events(scope, started['id'])]
        self.assertEqual(kinds[:3], ['submitted', 'started', 'routed'])
        self.assertEqual(kinds[-2:], ['result', 'succeeded'])
        routed = next(e for e in self.events(scope) if e['kind'] == 'routed')
        self.assertEqual((routed['data']['is_fake'], routed['data']['provider'], routed['data']['account']), (True, 'fake', 'none'))
        self.assertEqual(len(self.helpers['dates'].port.starts), 1)
        self.assertEqual(self.helpers['lead'].port.starts, [])
        # The library's own store under the host root, separate from conversations; scopes mapped with Dervo-owned ids only.
        db = harness_db(self.base / 'store')
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT project_id, domain, owner_id FROM projects').fetchall(),
                         [('project:' + scope['projectId'], 'personal', 'local-user')])
        rooms = {r[0]: r[1] for r in db.execute('SELECT room_id, visibility FROM rooms').fetchall()}
        self.assertEqual(rooms['conversation:' + scope['channelId']], 'shared')
        self.assertEqual(db.execute('SELECT role FROM project_members WHERE actor_id=?', ('helper:dates',)).fetchone()[0], 'agent')
        run = db.execute('SELECT status, worker_is_fake, worker_name, room_id FROM runs').fetchone()
        self.assertEqual(run, ('succeeded', 1, 'fake-scripted-worker', 'conversation:' + scope['channelId']))
        kinds_in_room = [r[0] for r in db.execute('SELECT kind FROM messages WHERE room_id=? ORDER BY seq', (run[3],)).fetchall()]
        self.assertEqual(kinds_in_room, ['message', 'run', 'run_result'])
        self.assertNotIn(b'expected@example.test', (self.base / 'store' / 'harness' / 'dervo.sqlite3').read_bytes())
        self.assertNotIn(b'unverified-model', (self.base / 'store' / 'harness' / 'dervo.sqlite3').read_bytes())
        # Existing conversations are untouched: no message, turn or replay.
        self.assertEqual(self.host.dispatch('history', scope), before)
        self.assertEqual(self.host.store.db.execute('SELECT COUNT(*) FROM turns').fetchone()[0], 0)
        harness = self.host.dispatch('status', {})['harness']
        self.assertEqual(harness['state'], 'available')
        # The injected fakes are bound; a Codex-capable library also derives a real helper for the seeded Codex profile.
        self.assertTrue({'dates', 'lead'} <= set(harness['helpers']), harness['helpers'])
        for name in harness['helpers']:
            self.assertEqual(harness['details'][name]['fake'], name.split('-')[0] not in ('claude', 'codex'), name)

    def test_helper_delegation_goes_through_the_packet_and_the_next_session_sees_the_return(self):
        scope = self.seed()
        first = self.ask(scope, 'ask-1', 'Plan the week', 'lead')['id']
        status = self.settle(scope, first)
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(len(status['delegations']), 1)
        delegation = status['delegations'][0]
        self.assertEqual((delegation['handle'], delegation['status'], delegation['result']['summary']), ('@dates', 'returned', DATE_ANSWER))
        kinds = [e['kind'] for e in self.events(scope, first)]
        self.assertIn('help_requested', kinds)
        self.assertIn('help_returned', kinds)
        self.assertLess(kinds.index('help_requested'), kinds.index('help_returned'))
        self.assertEqual(len(self.helpers['dates'].port.starts), 1)
        dates_run = self.helpers['dates'].port.starts[0]
        self.assertNotEqual(dates_run.room_id, 'conversation:' + scope['channelId'])
        self.assertEqual(dates_run.goal, 'Which date?')
        db = harness_db(self.base / 'store')
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT status FROM handoffs').fetchall(), [('returned',)])
        self.assertEqual(db.execute('SELECT status FROM dispatches').fetchall(), [('returned',)])
        returned = db.execute("SELECT content FROM messages WHERE room_id=? AND kind='handoff_result'", ('conversation:' + scope['channelId'],)).fetchall()
        self.assertEqual(len(returned), 1)
        self.assertIn(DATE_ANSWER, returned[0][0])
        # The next fresh session in the same conversation is built from the room, so it carries the linked return.
        second = self.ask(scope, 'ask-2', 'Summarise the plan', 'lead')['id']
        self.assertEqual(self.settle(scope, second)['state'], 'completed')
        context = self.helpers['lead'].port.starts[-1].context_text
        self.assertIn(DATE_ANSWER, context)
        self.assertIn('handoff_result', context)
        # Another conversation in the same project has its own room and sees nothing of it.
        other = {**scope, 'channelId': self.host.dispatch('create_conversation', {'projectId': scope['projectId'], 'name': 'Other', 'private': False})['id']}
        third = self.ask(other, 'ask-3', 'Summarise the plan', 'lead')['id']
        self.assertEqual(self.settle(other, third)['state'], 'completed')
        self.assertNotIn(DATE_ANSWER, self.helpers['lead'].port.starts[-1].context_text)
        self.assertTrue(all(e['request_id'] == third for e in self.events(other)))

    def test_cancel_stops_the_run_and_withdraws_its_delegation(self):
        scope = self.seed()
        first = self.ask(scope, 'ask-1', 'Plan then wait', 'lead')['id']
        self.host.tick()
        status = self.host.dispatch('harness_status', {**scope, 'requestId': first})
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['delegations'][0]['status'], 'dispatched')
        with self.assertRaises(Rejected):
            self.ask(scope, 'ask-2', 'Idle work', 'dates')
        cancelled = self.host.dispatch('harness_cancel', {**scope, 'requestId': first})
        self.assertIn(cancelled['state'], ('stopping', 'cancelled'))
        status = self.settle(scope, first)
        self.assertEqual((status['state'], status['linked_return']), ('cancelled', None))
        self.assertEqual(status['delegations'][0]['status'], 'cancelled')
        self.assertEqual(len(self.helpers['lead'].port.cancel_calls), 1)
        self.assertEqual(len(self.helpers['dates'].port.cancel_calls), 1)
        kinds = [e['kind'] for e in self.events(scope, first)]
        self.assertIn('cancel_requested', kinds)
        self.assertIn('cancelled', kinds)
        self.assertEqual(self.host.dispatch('harness_cancel', {**scope, 'requestId': first})['state'], 'cancelled')
        db = harness_db(self.base / 'store')
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT status FROM handoffs').fetchall(), [('cancelled',)])
        self.assertEqual(sorted(r[0] for r in db.execute('SELECT status FROM runs').fetchall()), ['cancelled', 'cancelled'])
        # The ordinary chat queue keeps working beside the harness.
        queued = self.host.dispatch('queue', {**scope, 'clientId': 'chat-1', 'text': 'Plain chat draft', 'parentId': None})
        self.assertEqual(queued['state'], 'queued')

    def test_refusals_fail_closed_before_and_inside_the_library(self):
        from harness_port import HelperSpec
        scope = self.seed()
        request = {**scope, 'clientId': 'ask-1', 'text': 'What date?', 'mode': 'coordinate', 'helper': 'dates'}
        for bad in ({'projectId': 'wrong'}, {'mode': 'anything'}, {'text': '   '}, {'helper': 'nobody'}, {'helper': ''}, {'extra': True}):
            with self.assertRaises(Rejected):
                self.host.dispatch('harness_request', {**request, **bad})
        private = self.host.dispatch('create_conversation', {'projectId': scope['projectId'], 'name': 'Mine', 'private': True})['id']
        with self.host.store.db:
            self.host.store.db.execute('UPDATE threads SET owner=? WHERE id=?', ('someone-else', private))
        with self.assertRaises(Rejected):
            self.host.dispatch('harness_request', {**request, 'channelId': private})
        execute = self.host.dispatch('harness_request', {**request, 'mode': 'execute'})
        self.assertEqual((execute['state'], execute['id']), ('unsupported', None))
        self.assertIn('task_create', execute['detail'])
        self.assertFalse((self.base / 'store' / 'harness').exists())
        with self.assertRaises(Rejected):
            self.host.dispatch('harness_status', {**scope, 'requestId': 'missing'})
        # A real (non-fake) helper is only ever bound under the conversation's own profile and provider.
        foreign = FakeWorker(SCRIPTS)
        foreign.is_fake = False
        helpers = {**self.helpers, 'other': HelperSpec(foreign, 'codex', 'profile:someone-else')}
        host = self.open_host(root=self.base / 'store-foreign', helpers=helpers)
        scope2 = self.seed(host)
        with self.assertRaises(Rejected) as refused:
            self.ask(scope2, 'ask-1', 'What date?', 'other', host=host)
        self.assertIn('account', str(refused.exception).lower())
        self.assertFalse((self.base / 'store-foreign' / 'harness').exists())
        self.assertEqual(foreign.starts, [])
        # A fake helper may not claim a real account, and a real helper may not hide behind the fake labels.
        for spec in (HelperSpec(FakeWorker(SCRIPTS), 'fake', 'profile:x'), HelperSpec(foreign, 'fake', 'none'), HelperSpec(object(), 'fake', 'none')):
            with self.assertRaises(Rejected):
                self.open_host(root=self.base / 'store-bad', helpers={'bad': spec}).dispatch('status', {})
        # Inside the library: a private conversation may not hand its text to a shared helper room.
        mine = {**scope, 'channelId': self.host.dispatch('create_conversation', {'projectId': scope['projectId'], 'name': 'Private plan', 'private': True})['id']}
        started = self.ask(mine, 'ask-2', 'Plan the week', 'lead')['id']
        status = self.settle(mine, started)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('PolicyViolation', status['error'])
        refused = next(e for e in self.events(mine, started) if e['kind'] == 'help_refused')
        self.assertEqual(refused['data']['error_type'], 'PolicyViolation')
        self.assertEqual(self.helpers['dates'].port.starts, [])
        self.assertEqual(status['delegations'], [])

    def test_discovery_reports_the_verified_library_and_unavailable_without_package_or_helper(self):
        from unittest import mock
        from harness_port import LoadedSupervisor
        # No injected factory: the host discovers the packaged, verified library next to it and binds the fake helpers.
        found = self.open_host(root=self.base / 'store-discovered', factory=False)
        scope = self.seed(found)
        harness = found.dispatch('status', {})['harness']
        self.assertEqual(harness['state'], 'available')
        self.assertTrue({'dates', 'lead'} <= set(harness['helpers']), harness['helpers'])
        self.assertIn('verified', harness['library'])
        self.assertFalse((self.base / 'store-discovered' / 'harness').exists())  # reading status never opens the store
        # Not packaged: the loader's answer is reported as is and nothing opens.
        missing = LoadedSupervisor('unavailable', 'The Dervo harness is not packaged with this build. Coordination requests are unavailable.')
        with mock.patch('harness_host.load_supervisor_factory', return_value=missing):
            host = self.open_host(root=self.base / 'store-without-harness', factory=False)
            scope = self.seed(host)
            self.assertEqual(host.dispatch('status', {})['harness']['state'], 'unavailable')
        result = self.ask(scope, 'ask-1', 'What date?', 'dates', host=host)
        self.assertEqual((result['state'], result['id']), ('unavailable', None))
        self.assertFalse((self.base / 'store-without-harness' / 'harness').exists())
        self.assertEqual(host.dispatch('harness_events', {**scope, 'after': 0}), [])
        bare = self.open_host(root=self.base / 'store-no-helpers', helpers={})
        # Work profiles aren't eligible for automatic personal-helper binding.
        scope = self.seed(bare, category='work')
        harness = bare.dispatch('status', {})['harness']
        self.assertEqual((harness['state'], harness['helpers']), ('unavailable', []))
        self.assertIn('no helper', harness['detail'].lower())
        self.assertEqual(self.ask(scope, 'ask-1', 'What date?', 'dates', host=bare)['state'], 'unavailable')
        self.assertFalse((self.base / 'store-no-helpers' / 'harness').exists())

    def test_one_owner_per_store_and_restart_reports_unknown_without_replay(self):
        scope = self.seed()
        first = self.ask(scope, 'ask-1', 'What date is next Friday?', 'dates')['id']
        self.assertEqual(self.settle(scope, first)['state'], 'completed')
        # The app store already refuses a second Host on the same root. Below that, the library's runtime lease refuses a
        # second supervisor on the same harness dir while this one is alive: probed through the adapter directly.
        from harness_adapter import open_supervisor
        from harness_port import HarnessRefused
        second = open_supervisor(self.base / 'store' / 'harness', owner='local-user', helpers=self.helpers)
        self.addCleanup(second.close)
        with self.assertRaises(HarnessRefused) as refused:
            second.submit('lease-probe', self.host.harness.requests()[0]['scope'], 'What date is next Friday?', helper='dates')
        self.assertIn('leased', str(refused.exception))
        second.close()
        self.assertEqual(len(self.helpers['dates'].port.starts), 1)
        self.assertEqual(self.settle(scope, self.ask(scope, 'ask-1b', 'What date is next Friday?', 'dates')['id'])['state'], 'completed')
        # An active run survives a host restart only as unknown: no replay, no worker call.
        second = self.ask(scope, 'ask-2', 'Idle work', 'dates')['id']
        self.host.tick()
        self.assertEqual(self.host.dispatch('harness_status', {**scope, 'requestId': second})['state'], 'running')
        self.close_host(self.host)
        reopened = self.open_host()
        status = reopened.dispatch('harness_status', {**scope, 'requestId': second})
        self.assertEqual(status['state'], 'unknown')
        self.assertTrue('closed' in status['error'].lower() or 'restart' in status['error'].lower(), status['error'])
        self.assertEqual([e['kind'] for e in self.events(scope, second, host=reopened)][-1], 'unknown')
        self.assertEqual(reopened.dispatch('harness_status', {**scope, 'requestId': first})['state'], 'completed')
        # The reopened runtime finds the orphaned run, asks the port once (no replay) and stops it, freeing the room.
        third = self.ask(scope, 'ask-3', 'What date is next Friday?', 'dates', host=reopened)['id']
        self.assertEqual(self.settle(scope, third, host=reopened)['state'], 'completed')
        db = harness_db(self.base / 'store')
        self.addCleanup(db.close)
        rows = db.execute('SELECT status, error FROM runs ORDER BY seq').fetchall()
        self.assertEqual([r[0] for r in rows], ['succeeded', 'succeeded', 'cancelled', 'succeeded'])
        self.assertIn('restarted', rows[2][1])
        self.assertEqual(len(self.helpers['dates'].port.starts), 4)
        self.assertEqual(len(self.helpers['dates'].port.cancel_calls), 1)
        recovered = reopened.harness.supervisor.describe()['recovered']
        self.assertEqual([r['status'] for r in recovered], ['cancelled'])


class PackagedLibraryTests(unittest.TestCase):
    """The packaged library is verified against its provenance and loaded next to the host, never from a builder path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = Path(self.temp.name) / 'backend'
        self.backend.mkdir()

    def copy_package(self):
        shutil.copytree(BACKEND / 'dervo_harness', self.backend / 'dervo_harness', ignore=shutil.ignore_patterns('__pycache__'))
        (self.backend / 'harness_adapter.py').write_text('def open_supervisor(data_dir, *, owner, helpers):\n    return ("opened", data_dir, owner, helpers)\n')
        return self.backend / 'dervo_harness'

    def test_vendored_package_matches_its_provenance_and_loads(self):
        from harness_port import PROVENANCE, load_supervisor_factory
        import dervo_harness
        provenance = json.loads((BACKEND / 'dervo_harness' / PROVENANCE).read_text())
        self.assertEqual(provenance['version'], dervo_harness.__version__)
        self.assertEqual(provenance['source'], 'harness-core')
        self.assertNotIn('/Users/', json.dumps(provenance))
        for name, digest in provenance['files'].items():
            self.assertEqual(hashlib.sha256((BACKEND / 'dervo_harness' / name).read_bytes()).hexdigest(), digest, name)
        listed = set(provenance['files'])
        present = {p.name for p in (BACKEND / 'dervo_harness').iterdir() if p.suffix == '.py'}
        self.assertEqual(present, {n for n in listed if n.endswith('.py')})
        self.assertIn(provenance['license'], listed)
        for notice in provenance['notices']:
            self.assertIn(notice, listed)
        self.assertTrue({'__init__.py', 'supervisor.py', 'runtime.py', 'harness.py', 'THIRD_PARTY_NOTICES.md'} <= listed)
        before = list(sys.path)
        loaded = load_supervisor_factory(BACKEND)
        self.assertEqual(sys.path, before)
        self.assertEqual(loaded.state, 'available', loaded.detail)
        self.assertEqual(loaded.factory.__name__, 'open_supervisor')
        self.assertEqual(loaded.provenance['version'], dervo_harness.__version__)

    def test_missing_package_is_unavailable_not_an_error(self):
        from harness_port import load_supervisor_factory
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertEqual(loaded.state, 'unavailable')
        self.assertIn('not packaged', loaded.detail)

    def test_tampered_or_incomplete_package_is_refused(self):
        from harness_port import PROVENANCE, load_supervisor_factory
        package = self.copy_package()
        with (package / 'errors.py').open('a') as f:
            f.write('\n# tampered\n')
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertIn('errors.py', loaded.detail)
        self.assertIn('digest', loaded.detail)
        shutil.rmtree(package)
        package = self.copy_package()
        (package / 'rogue.py').write_text('x = 1\n')
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertIn('rogue.py', loaded.detail)
        (package / 'rogue.py').unlink()
        (package / 'THIRD_PARTY_NOTICES.md').unlink()
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertIn('THIRD_PARTY_NOTICES.md', loaded.detail)
        (package / PROVENANCE).unlink()
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertIn(PROVENANCE, loaded.detail)

    def test_adapter_without_entry_point_is_refused(self):
        from harness_port import load_supervisor_factory
        self.copy_package()
        (self.backend / 'harness_adapter.py').write_text('VERSION = 1\n')
        loaded = load_supervisor_factory(self.backend)
        self.assertIsNone(loaded.factory)
        self.assertIn('open_supervisor', loaded.detail)


if __name__ == '__main__':
    unittest.main()
