"""App-owned adapter from the harness coordinator to the packaged ``dervo_harness`` library, public API only.

What the library gives us (see backend/dervo_harness): ``Harness`` (projects, rooms, members, messages, handoff
packets), ``Runtime`` (execution grants, requests, runs, events, cancel, reconcile), ``Supervisor`` over a
``WorkerPool`` of ``WorkerBinding`` members (one runtime owner per data dir with a lease, host-routed dispatch, and the
``request_help`` broker for model-requested delegation), and ``AgentRegistry`` (``@handle`` addressing).

How the app maps onto it. Everything below is created by the local human owner, and only when a request needs it:

- app project  -> harness project ``project:<id>``, domain = the profile category (``personal`` or ``work``);
- conversation -> harness room ``conversation:<id>`` with the conversation's own visibility;
- bound helper -> project member ``helper:<name>`` (role ``agent``), a shared home room ``helper:<name>:<project>``,
  a registered handle ``@<name>`` addressed in that home room, and one ``WorkerBinding`` per project carrying the
  helper's provider and account labels. Only helpers eligible for the conversation (``helper_allowed``) are bound
  into its project, so a project never carries another account's helper;
- execution grants: every eligible helper on its own home room (so it can be asked), and the named helper on the
  conversation room (so it can run there). A private conversation also makes the named helper a room member.

A request is ``Runtime.submit`` in the conversation room, as the owner, with the user's text as the goal and recorded
first as an ordinary message so the run's fresh session context and any ``request_help`` source ref can cite it.
Helper delegation is the library's own ``ScopedActions.request_help`` brokered by the ``Supervisor``; the helper's
answer returns through the handoff packet as a ``handoff_result`` message in the conversation room, which the next
request's session context is built from. Cancel is ``Runtime.cancel`` plus ``Supervisor.settle`` so a dependent helper
run is stopped too. Status and events are read from the runtime's own durable records.

Helpers come from a mapping or a callable returning the current mapping (the coordinator derives Claude helpers from
the app's owned profiles at request time). A helper whose port is per project (``HelperSpec.port`` is a factory) has
its port built once per project under a work dir this adapter owns; a factory that refuses (missing CLI, unmarked
config dir, bad identity) makes the request fail before any context is sent.

Restart: a new supervisor marks runs left running by a previous process ``unknown`` (the library's rule). This
adapter then asks the ports about them once through ``Supervisor.reconcile`` (the library's explicit, human-only
recovery; nothing is replayed) and requests a stop for any run still active, because the app has already reported
those requests as unknown and nothing tracks them any more. A helper that has no memory of the run makes it ``lost``;
a stop the port cannot confirm leaves the run ``cancelling`` and the room's scope owned, which the next request
reports as a refusal rather than silently taking over.

Nothing here reads credentials, starts a provider on its own, writes project files or bypasses a library check.
Private helpers of the library (underscore names) are not used.
"""
import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import dervo_harness as lib
from harness_port import HarnessRefused, check_helpers, helper_allowed, helper_bindable, is_port_factory

DB_FILE = 'app-scopes.sqlite3'
EVENT_TEXT_LIMIT = 2000
EVENT_ITEMS_LIMIT = 50
PUMP_BUDGET = 1000
STATE_BY_RUN = {
    lib.RUN_RUNNING: 'running', lib.RUN_CANCELLING: 'stopping', lib.RUN_SUCCEEDED: 'completed', lib.RUN_FAILED: 'failed',
    lib.RUN_CANCELLED: 'cancelled', lib.RUN_LOST: 'lost', lib.RUN_UNKNOWN: 'unknown',
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS projects (project_id TEXT PRIMARY KEY, domain TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS grants (project_id TEXT NOT NULL, actor_id TEXT NOT NULL, room_id TEXT NOT NULL, grant_id TEXT NOT NULL,
    PRIMARY KEY (project_id, actor_id, room_id));
CREATE TABLE IF NOT EXISTS bindings (project_id TEXT NOT NULL, helper TEXT NOT NULL, PRIMARY KEY (project_id, helper));
'''


def open_supervisor(data_dir, *, owner, helpers):
    return AppSupervisor(Path(data_dir), owner, helpers)


def actor_id(name):
    return 'helper:' + name


def home_room_id(name, project_id):
    return f'helper:{name}:{project_id}'


def project_hash(project_id):
    return hashlib.sha256(project_id.encode('utf-8')).hexdigest()[:16]


def member_name(name, project_id):
    """Pool member names are per project (a binding names one project); keep them valid and deterministic."""
    return f'{name}-{project_hash(project_id)}'


def clip(value, depth=0):
    """Bound an event payload for the host record: long strings cut, long collections truncated, deep nesting dropped."""
    if isinstance(value, str):
        return value if len(value) <= EVENT_TEXT_LIMIT else value[:EVENT_TEXT_LIMIT] + '...'
    if isinstance(value, dict):
        if depth >= 4:
            return '{...}'
        return {str(k): clip(v, depth + 1) for k, v in list(value.items())[:EVENT_ITEMS_LIMIT]}
    if isinstance(value, (list, tuple)):
        if depth >= 4:
            return '[...]'
        return [clip(v, depth + 1) for v in list(value)[:EVENT_ITEMS_LIMIT]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return clip(str(value), depth)


class AppPool(lib.WorkerPool):
    """A ``WorkerPool`` that admits a later project's bindings by re-running the library's own constructor checks."""

    def __init__(self, bindings):
        self._bound = list(bindings)
        super().__init__(self._bound)

    def bind(self, binding):
        lib.WorkerPool.__init__(self, [*self._bound, binding])
        self._bound.append(binding)

    def rebind(self, binding):
        """Replace the member of the same name (its labels changed, for example a boundary record now covers its profile)."""
        kept = [b for b in self._bound if b.name != binding.name]
        lib.WorkerPool.__init__(self, [*kept, binding])
        self._bound = [*kept, binding]


class GuidedPort:
    """Add this request's saved guidance at worker start without copying it into room history."""

    def __init__(self, port, pending, access_check=None, access_denied=None, running_check=None):
        self.port, self.pending, self.access_check = port, pending, access_check
        # A run already going is pumped under running_check: a trial that ends mid-run lets it finish (host.require_run_access).
        self.running_check = running_check or access_check
        self.access_denied = access_denied
        self.runs = {}

    def __getattr__(self, name):
        return getattr(self.port, name)

    def start(self, assignment, actions):
        if self.access_check is not None:
            self.access_check()
        key = (assignment.project_id, assignment.room_id, assignment.worker_actor_id, assignment.goal)
        entry = self.pending.pop(key, None)
        if entry is not None:
            entry['consumed'] = True
            assignment = replace(assignment, goal=assignment.goal + '\n\n' + entry['text'])
        ref = self.port.start(assignment, actions)
        self.runs[ref] = assignment.run_id
        return ref

    def attach(self, worker_ref, actions):
        self.runs[worker_ref] = actions.run_id
        return self.port.attach(worker_ref, actions)

    def drain(self, worker_ref):
        if self.running_check is not None:
            try:
                self.running_check()
            except Exception:
                # Runtime cancellation retains scope until the port confirms exit.
                # Don't pump a handshake while permission is absent or uncertain.
                if self.access_denied is None:
                    raise
                self.access_denied(self.runs[worker_ref])
                return []
        return self.port.drain(worker_ref)

    def steer(self, worker_ref, note):
        if self.access_check is not None:
            self.access_check()
        return self.port.steer(worker_ref, note)


class AppSupervisor:
    def __init__(self, data_dir, owner, helpers):
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError('owner must be the local human actor id')
        if not callable(helpers):
            helpers = check_helpers(helpers)
        self._source = helpers
        self._ports = {}
        self._pending_guidance = {}
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        self.owner = lib.Caller(owner, kind=lib.CALLER_HUMAN)
        self.h = lib.Harness(self.data_dir)
        self.registry = lib.AgentRegistry(self.h)
        self.scopes = sqlite3.connect(self.data_dir / DB_FILE)
        self.scopes.executescript(SCHEMA)
        self.pool = None
        self.sup = None
        self.recovered = []
        self.degraded = []
        self.closed = False
        self.access_check = None
        self.running_check = None

    def set_access_check(self, callback, running=None):
        if not callable(callback) or (running is not None and not callable(running)):
            raise ValueError('Account access check must be callable')
        self.access_check = callback
        self.running_check = running

    def _check_access(self):
        if self.access_check is not None:
            self.access_check()

    def _check_running_access(self):
        check = self.running_check or self.access_check
        if check is not None:
            check()

    def _access_denied(self, run_id):
        self.sup.rt.cancel(self.owner, run_id, note="Dervo account access needs checking. This agent was asked to stop.")

    @property
    def helpers(self):
        source = self._source() if callable(self._source) else self._source
        try:
            return check_helpers(source)
        except ValueError as exc:
            raise HarnessRefused(f'helper configuration refused: {exc}') from exc

    # ------------------------------------------------------------------ lifecycle
    def _open(self):
        if self.closed:
            raise HarnessRefused('the harness adapter is closed')

    def _port(self, name, spec, project_id):
        key = (name, project_id)
        if key not in self._ports:
            port = spec.port
            if is_port_factory(port):
                work_dir = self.data_dir / 'work' / name / project_hash(project_id)
                try:
                    port = port(project_id, work_dir)
                except Exception as exc:
                    raise HarnessRefused(f'helper {name!r} could not be prepared for this project: {exc}') from exc
            self._ports[key] = port
        return self._ports[key]

    def _binding(self, name, spec, project_id):
        return lib.WorkerBinding(member_name(name, project_id), GuidedPort(self._port(name, spec, project_id), self._pending_guidance, self._check_access, self._access_denied, self._check_running_access), spec.provider, spec.account,
                                 project_id, (actor_id(name),))

    def _supervisor(self):
        """The one runtime owner over this data dir, opened when the first helper is bound (a pool needs a binding)."""
        if self.sup is None:
            helpers = self.helpers
            bindings = []
            for pid, name in self.scopes.execute('SELECT project_id, helper FROM bindings ORDER BY project_id, helper').fetchall():
                spec = helpers.get(name)
                if spec is None:
                    self.degraded.append({'project_id': pid, 'helper': name, 'reason': 'helper is no longer bound in this build'})
                    continue
                try:
                    bindings.append(self._binding(name, spec, pid))
                except HarnessRefused as exc:
                    self.degraded.append({'project_id': pid, 'helper': name, 'reason': str(exc)})
            if not bindings:
                raise HarnessRefused('no helper could be bound into the harness: ' + '; '.join(d['reason'] for d in self.degraded[-3:]))
            pool = AppPool(bindings)
            sup = lib.Supervisor(self.h, pool)  # StateError when another live process holds the lease
            self.pool, self.sup = pool, sup
            self._recover()
        return self.sup

    def _recover(self):
        """Runs left by a previous owner are unknown now. Ask the ports once (no replay), then stop what is still active."""
        touched = self.sup.reconcile(self.owner)
        for run in touched:
            current = self.sup.rt.get_run(self.owner, run.run_id)
            if current.active:
                current = self.sup.rt.cancel(self.owner, run.run_id, note='Dervo restarted; observation of this run was lost')
            self.recovered.append({'run_id': run.run_id, 'room_id': run.room_id, 'status': current.status})
        if touched:
            self.sup.settle()

    def describe(self):
        return {'library': lib.__version__, 'lease': self.sup.lease.token if self.sup else None,
                'members': self.sup.pool.describe() if self.sup else [], 'recovered': list(self.recovered), 'degraded': list(self.degraded)}

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.sup is not None:
                self.sup.close()
        finally:
            for port in self._ports.values():
                close = getattr(port, 'close', None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            self.scopes.close()
            self.h.close()

    # ------------------------------------------------------------------ scope mapping
    def _grant(self, project_id, actor, room_id):
        if self.scopes.execute('SELECT 1 FROM grants WHERE project_id=? AND actor_id=? AND room_id=?', (project_id, actor, room_id)).fetchone():
            return
        grant = self.sup.rt.grant_execution(self.owner, project_id, actor, [room_id], note='Dervo conversation mapping')
        with self.scopes:
            self.scopes.execute('INSERT INTO grants VALUES (?,?,?,?)', (project_id, actor, room_id, grant.grant_id))

    def ensure_scope(self, scope, helper):
        """Create or check every harness record a request in this conversation needs. Idempotent; refuses mismatches."""
        helpers = self.helpers
        spec = helpers.get(helper)
        if spec is None:
            raise HarnessRefused(f'helper {helper!r} is not bound in this build')
        reason = helper_allowed(spec, scope)
        if reason:
            raise HarnessRefused(f'helper {helper!r} cannot act in this conversation: {reason}')
        eligible = {name: each for name, each in helpers.items() if helper_bindable(each, scope)}
        owner, pid, domain, room_id, visibility = self.owner, scope['project_id'], scope['domain'], scope['room_id'], scope['visibility']
        if domain not in (lib.DOMAIN_PERSONAL, lib.DOMAIN_WORK) or visibility not in (lib.VIS_PRIVATE, lib.VIS_SHARED):
            raise HarnessRefused('scope names an unknown domain or visibility')
        try:
            project = self.h.get_project(owner, pid)
        except lib.NotFound:
            project = self.h.create_project(owner, pid, domain=domain, project_id=pid)
        if project.domain != domain:
            raise HarnessRefused(f'project {pid} is mapped to domain {project.domain}; this conversation is {domain}')
        with self.scopes:
            self.scopes.execute('INSERT OR IGNORE INTO projects VALUES (?,?)', (pid, domain))
        for name, each in eligible.items():
            aid, home = actor_id(name), home_room_id(name, pid)
            if self.h.project_role(pid, aid) is None:
                self.h.add_project_member(owner, pid, aid, lib.ROLE_AGENT)
            try:
                self.h.get_room(owner, home)
            except lib.NotFound:
                self.h.create_room(owner, pid, f'{name} home', visibility=lib.VIS_SHARED, room_id=home)
            if not any(h.handle == name for h in self.registry.list_handles(owner, pid)):
                self.registry.register(owner, pid, name, aid, home, display_name=name, note=each.note)
        try:
            room = self.h.get_room(owner, room_id)
        except lib.NotFound:
            room = self.h.create_room(owner, pid, room_id, visibility=visibility, room_id=room_id)
        if room.project_id != pid or room.visibility != visibility:
            raise HarnessRefused(f'room {room_id} is mapped as {room.visibility} in project {room.project_id}; this conversation is {visibility} in {pid}')
        aid = actor_id(helper)
        if visibility == lib.VIS_PRIVATE:
            self.h.add_room_member(owner, room_id, aid)
        with self.scopes:
            self.scopes.executemany('INSERT OR IGNORE INTO bindings VALUES (?,?)', [(pid, name) for name in eligible])
        if self.sup is None:
            self._supervisor()
        for name, each in eligible.items():
            member = member_name(name, pid)
            try:
                if member not in self.pool.members():
                    self.pool.bind(self._binding(name, each, pid))
                else:
                    current = self.pool.route(pid, actor_id(name))
                    if (current.account, current.provider) != (each.account, each.provider):
                        self.pool.rebind(self._binding(name, each, pid))  # the labels the host records changed; the port is reused
            except HarnessRefused as exc:
                if name == helper:
                    raise
                self.degraded.append({'project_id': pid, 'helper': name, 'reason': str(exc)})
        for name in eligible:
            if member_name(name, pid) in self.pool.members():
                self._grant(pid, actor_id(name), home_room_id(name, pid))
        self._grant(pid, aid, room_id)
        return aid

    # ------------------------------------------------------------------ port
    def submit(self, request_id, scope, text, *, helper, guidance=''):
        self._open()
        if type(guidance) is not str or len(guidance.encode('utf-8')) > 16384:
            raise HarnessRefused('Project guidance must fit the request delivery limit.')
        try:
            aid = self.ensure_scope(scope, helper)
            key = (scope['project_id'], scope['room_id'], aid, text.strip())
            if key in self._pending_guidance:
                raise HarnessRefused('This conversation request is already starting.')
            self.h.record_message(self.owner, scope['room_id'], text)
            entry = {'text': guidance, 'consumed': False}
            if guidance:
                self._pending_guidance[key] = entry
            try:
                # Runtime.submit starts the worker synchronously. Persist only the original goal;
                # GuidedPort consumes the exact request's guidance once at the public start boundary.
                request = self.sup.rt.submit(self.owner, scope['room_id'], text, worker_actor_id=aid, request_key='app:' + request_id)
            finally:
                self._pending_guidance.pop(key, None)
        except lib.DervoError as exc:
            raise HarnessRefused(f'{type(exc).__name__}: {exc}') from exc
        handle = {'request_id': request.request_id, 'run_id': request.current_run_id}
        if guidance and not entry['consumed']:
            # An accepted run must retain its handle so the host can observe and stop it.
            handle['guidance_error'] = "Dervo couldn't confirm project guidance was supplied. A stop was requested; this request won't be replayed."
        return handle

    def _runtime(self):
        self._open()
        if self.sup is None:
            raise HarnessRefused('no runtime is open in this process')
        return self.sup.rt

    def status(self, harness_request_id):
        rt = self._runtime()
        view = rt.status(self.owner, harness_request_id)
        state = STATE_BY_RUN.get(view['run_status'])
        if state is None:
            raise HarnessRefused(f'unrecognised run status {view["run_status"]!r}')
        request = rt.get_request(self.owner, harness_request_id)
        result = view['result']
        linked = None
        if result:
            linked = {'summary': result['summary'], 'refs': list(result['refs']), 'worker': result['worker_name'],
                      'worker_is_fake': bool(result['worker_is_fake']), 'result_message_id': result['result_message_id'], 'returned_at': result['returned_at']}
        delegations = [self._delegation(d) for d in self.sup.list_delegations(self.owner, run_id=view['run_id'])] if view['run_id'] else []
        return {'state': state, 'helper': request.worker_actor_id.split(':', 1)[1], 'linked_return': linked, 'delegations': delegations,
                'error': view['error'], 'run_id': view['run_id'], 'attempt': view['attempts'], 'run_worker': view['run_worker'],
                'rejected_events': view['rejected_events']}

    @staticmethod
    def _delegation(d):
        result = None
        if d.result:
            result = {'summary': d.result.get('summary'), 'refs': list(d.result.get('refs') or []), 'worker': d.result.get('worker'),
                      'worker_is_fake': bool(d.result.get('worker_is_fake')), 'result_message_id': d.result.get('result_message_id')}
        return {'id': d.delegation_id, 'handle': d.mention, 'status': d.status, 'purpose': d.purpose, 'depth': d.depth,
                'target_room': d.target_room, 'member': d.target_worker, 'result': result, 'error': d.error,
                'returned_at': d.result.get('returned_at') if d.result else None, 'created_at': d.created_at}

    def resolve(self, project_id, mention):
        """The library's own resolution of ``@handle`` in a mapped project: registered, bound and granted, or the exact refusal."""
        self._open()
        if self.sup is None:
            raise HarnessRefused('no runtime is open in this process yet')
        try:
            address = self.sup.resolve_agent(self.owner, project_id, mention)
        except lib.DervoError as exc:
            raise HarnessRefused(f'{type(exc).__name__}: {exc}') from exc
        return {'handle': address.mention, 'actor_id': address.actor_id, 'room_id': address.room_id, 'member': address.member,
                'provider': address.provider, 'account': address.account}

    def cancel(self, harness_request_id):
        rt = self._runtime()
        try:
            request = rt.get_request(self.owner, harness_request_id)
            run = rt.get_run(self.owner, request.current_run_id)
            if run.status in lib.RUN_TERMINAL:
                return
            rt.cancel(self.owner, run.run_id, note='cancelled from the conversation')
            self.sup.settle()
        except lib.DervoError as exc:
            raise HarnessRefused(f'{type(exc).__name__}: {exc}') from exc

    def pull_events(self, harness_request_id, after=0):
        rt = self._runtime()
        request = rt.get_request(self.owner, harness_request_id)
        if not request.current_run_id:
            return []
        out = []
        for event in rt.list_events(self.owner, request.current_run_id):
            if event.seq <= after:
                continue
            data = {**clip(event.payload), 'attempt': event.attempt, 'source': event.source, 'accepted': event.accepted, 'reason': event.reason}
            out.append({'seq': event.seq, 'kind': event.kind, 'data': data})
        return out

    def tick(self):
        self._open()
        if self.sup is not None:
            self.sup.pump(max_events=PUMP_BUDGET)
