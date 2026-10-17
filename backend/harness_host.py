"""Host-side coordinator for the packaged Dervo harness Supervisor.

A request names a bound helper and maps an existing conversation onto a harness scope (project, room, account,
model). The adapter (harness_adapter.py) submits the text to the library's runtime as the local human owner; the
helper runs in the conversation's harness room, may ask other bound helpers through the library's own
``request_help`` broker, and its result lands in that room for the next request. This module mirrors the run's own
events into the host store and reports status. Nothing here writes project files, starts a provider on its own,
reads credentials or replaces an existing conversation, turn or coding task.

Helpers are the test doubles injected through ``Host(harness_workers=...)`` plus, in the real app, one identity-verified
helper per owned personal profile and model in use: Claude (harness_claude.py) and Codex (harness_codex.py), both in
the same pool and under the same rules. A conversation runs only its own provider's helper under its own profile.

Cross-provider authorisation is an explicit owner record, never inferred: ``harness_boundary`` binds two or more owned
personal profiles (by id, whatever their emails) under one ``boundary:<id>`` label. Helpers of those profiles are then
bound into each other's projects and rendered to each other as helpers the library lets them ask; without a record,
every helper carries ``profile:<id>`` and the library's account rule keeps providers apart even for one person.

Named help: a Claude helper whose owner opted it in (``harness_actions``) and whose profile is inside a boundary record
gets the library's ``request_help`` action bridge. What it may ask is the library's own list (registered handle, bound
member, granted room, same account label): the ``catalogue`` on ``conversation_status.harness`` names those helpers
with their stable ids, ``harness_resolve`` answers an explicit ``@handle`` from the same list and, once a runtime is
open, from the library's ``resolve_agent``. Codex helpers answer only; they are never advertised as able to ask.
Every help event the library logs (requested, returned, refused) is mirrored as plain text on the request.

Coding execution is not granted through the harness. A request with mode 'execute' answers with state 'unsupported'
and points at the existing coding task path, which keeps its own scope approval and human review.
"""
import json
import re
import time
import uuid
from pathlib import Path

from dervo_connections import Rejected
from harness_port import FAKE_PROVIDER, MODES, STATES, TERMINAL, HarnessRefused, check_helpers, helper_allowed, load_supervisor_factory
from task_error import error_text

FIELDS = {
    'harness_request': {'projectId', 'channelId', 'clientId', 'text', 'mode', 'helper'},
    'harness_status': {'projectId', 'channelId', 'requestId'},
    'harness_cancel': {'projectId', 'channelId', 'requestId'},
    'harness_events': {'projectId', 'channelId', 'after'},
    'harness_requests': {'projectId', 'channelId'},
    'harness_boundary': {'profileIds'},
    'harness_actions': {'profileId', 'enabled'},
    'harness_resolve': {'projectId', 'channelId', 'mention'},
}
OPTIONAL_FIELDS = {'harness_request': {'mentions'}}
PROVIDER_LABELS = {'claude': 'Claude Code', 'codex': 'Codex'}
HANDLE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
HELP_EVENT_KINDS = ('help_requested', 'help_returned', 'help_refused', 'help_return_refused')
PENDING_DELEGATION = ('dispatched', 'cancelling')
TEXT_CLIP = 300
STORE_DIR = 'harness'
EVENT_LIMIT = 1000
EVENT_BYTES = 65536
EXECUTE_DETAIL = ('Coding execution through the harness is not granted in this slice. Use the existing coding task path: '
                  'task_create, then task_approve with an explicit scope, then human review of the recorded evidence.')
NO_HELPER_DETAIL = ' No helper is bound: no owned personal Claude or Codex profile with a usable model is in use, so coordination requests are unavailable.'
DETAILS = {
    'queued': 'Request submitted to the harness. The next poll reports the run state.',
    'running': "The named helper is working in this conversation's harness room. No project file is changed.",
    'stopping': 'Cancelling. The run ends once the helper confirms the stop.',
    'completed': "The helper returned. Its result is in this conversation's harness room and the next request's context is built from it.",
    'cancelled': 'The request was cancelled and the helper confirmed the stop.',
    'failed': 'The harness refused the request or the helper failed. Nothing was replayed.',
    'lost': 'The helper could not be found again. Nothing was replayed.',
    'unknown': 'Observation was lost. The outcome is unknown and is not replayed.',
}


class HarnessCoordinator:
    def __init__(self, host, *, supervisor_factory=None, workers=None, claude_executable=None, codex_executable=None):
        self.host, self.store = host, host.store
        try:
            self.static = check_helpers(workers)
        except ValueError as exc:
            raise Rejected(f'Harness helper configuration refused: {exc}')
        self.claude_executable, self.codex_executable = claude_executable, codex_executable
        self.supervisor = None
        if supervisor_factory is None:
            loaded = load_supervisor_factory(Path(__file__).resolve().parent)
            self.factory, self.library = loaded.factory, loaded.detail
        else:
            self.factory, self.library = supervisor_factory, 'Injected supervisor factory.'
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS harness_requests (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id), document TEXT NOT NULL)')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS harness_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, '
                                  'request_id TEXT NOT NULL REFERENCES harness_requests(id), kind TEXT NOT NULL, data TEXT NOT NULL)')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS harness_boundaries (id TEXT PRIMARY KEY, profiles TEXT NOT NULL, created_at INTEGER NOT NULL)')
            self.store.db.execute('CREATE TABLE IF NOT EXISTS harness_actions (profile_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)')
        for request in self.requests():
            if request['state'] not in TERMINAL:
                self.finish(request, 'unknown', 'Host restarted before the harness confirmed the outcome. No automatic replay.')

    # Cross-provider authorisation records: explicit, by profile id, created by the local owner. Never inferred from emails.
    def boundaries(self):
        return [{'id': row[0], 'profiles': json.loads(row[1]), 'created_at': row[2]}
                for row in self.store.db.execute('SELECT id, profiles, created_at FROM harness_boundaries ORDER BY rowid').fetchall()]

    def account_label(self, profile_id):
        for boundary in self.boundaries():
            if profile_id in boundary['profiles']:
                return 'boundary:' + boundary['id']
        return 'profile:' + profile_id

    def boundary(self, args):
        from connections_ui import owned_profile
        ids = args['profileIds']
        if type(ids) is not list or len(ids) < 2 or any(type(p) is not str or not p for p in ids) or len(set(ids)) != len(ids):
            raise Rejected('A boundary names two or more distinct owned profiles by id')
        from dervo_connections import same_side
        sides = []
        for profile_id in ids:
            sides.append(owned_profile(self.store, self.host.root, profile_id)['category'])  # unknown or moved profiles are refused here
            if self.account_label(profile_id) != 'profile:' + profile_id:
                raise Rejected(f'Profile {profile_id} is already in a boundary')
        # Accounts that help each other are all work or all personal: a helper never carries one side's work to the other.
        if not same_side(*sides):
            raise Rejected('Link work accounts with work accounts and personal ones with personal ones. Nothing was linked.')
        record = {'id': uuid.uuid4().hex, 'profiles': sorted(ids), 'created_at': int(time.time())}
        with self.store.db:
            self.store.db.execute('INSERT INTO harness_boundaries VALUES (?,?,?)', (record['id'], json.dumps(record['profiles']), record['created_at']))
        return record

    # Action opt-in: the owner lets one Claude account ask linked helpers through the library's action bridge. Off by default.
    def actions(self):
        return {row[0] for row in self.store.db.execute('SELECT profile_id FROM harness_actions').fetchall()}

    def action_toggle(self, args):
        from connections_ui import owned_profile
        profile_id, enabled = args['profileId'], args['enabled']
        if type(enabled) is not bool or type(profile_id) is not str or not profile_id:
            raise Rejected('Asking linked helpers is switched on or off per owned profile id')
        profile = owned_profile(self.store, self.host.root, profile_id)
        if profile['provider'] != 'claude':
            raise Rejected('Only a Claude account can ask linked helpers. Codex helpers answer; they do not initiate help.')
        with self.store.db:
            if enabled:
                self.store.db.execute('INSERT OR IGNORE INTO harness_actions VALUES (?,?)', (profile_id, int(time.time())))
            else:
                self.store.db.execute('DELETE FROM harness_actions WHERE profile_id=?', (profile_id,))
        linked = self.account_label(profile_id).startswith('boundary:')
        return {'profileId': profile_id, 'enabled': enabled, 'linked': linked,
                'detail': ('This Claude account may ask the helpers linked to it, through the harness action tool, when a message asks for it.' if enabled and linked
                           else 'Switched on, but no accounts are linked to this one yet, so there is nobody to ask.' if enabled
                           else 'This Claude account answers in text only and asks nobody.')}

    # Helpers: injected test doubles plus the Claude and Codex helpers derived from the owned profiles right now.
    def helpers(self):
        from harness_claude import derive as derive_claude
        from harness_codex import derive as derive_codex
        claude, excluded = derive_claude(self.store, self.host.root, executable=self.claude_executable, label=self.account_label, actions=self.actions(),
                                         key_for=getattr(self.host, 'keys', None) and self.host.keys.key_for)
        codex, excluded_codex = derive_codex(self.store, self.host.root, executable=self.codex_executable, label=self.account_label)
        return {**claude, **codex, **self.static}, excluded + excluded_codex

    def availability(self, helpers=None, excluded=None):
        if helpers is None:
            helpers, excluded = self.helpers()
        if self.factory is None:
            state, detail = 'unavailable', self.library
        elif not helpers:
            state, detail = 'unavailable', self.library + NO_HELPER_DETAIL
        else:
            state, detail = 'available', self.library + ' Bound helpers: ' + ', '.join(sorted(helpers)) + '.'
        details = {name: {'provider': spec.provider, 'model': spec.model, 'account': spec.account, 'fake': spec.provider == FAKE_PROVIDER,
                          'can_ask': spec.can_ask} for name, spec in sorted(helpers.items())}
        return {'state': state, 'detail': detail, 'helpers': sorted(helpers), 'library': self.library, 'details': details,
                'excluded': list(excluded or []), 'boundaries': self.boundaries(), 'actions': sorted(self.actions())}

    # Persistence in the host store. The library keeps its own explicit store under STORE_DIR.
    def requests(self, thread_id=None):
        query, params = 'SELECT document FROM harness_requests', ()
        if thread_id is not None:
            query, params = query + ' WHERE thread_id=?', (thread_id,)
        return [json.loads(row[0]) for row in self.store.db.execute(query + ' ORDER BY rowid', params).fetchall()]

    def save(self, request):
        with self.store.db:
            from coordination_search import save
            save(self.store.db, request)
        return request

    def record(self, request, kind, data):
        if type(kind) is not str or not kind or len(kind) > 64:
            raise Rejected('Harness event kind must be a short string')
        text = json.dumps(data)
        if len(text.encode()) > EVENT_BYTES:
            raise Rejected('Harness event exceeds the host record budget')
        with self.store.db:
            self.store.db.execute('INSERT INTO harness_events(request_id,kind,data) VALUES (?,?,?)', (request['id'], kind, text))

    def finish(self, request, state, error):
        """App-detected terminal outcome. Library-reported outcomes carry their own mirrored events."""
        request.update(state=state, error=error)
        self.save(request)
        self.record(request, state, {'error': error})

    def view(self, request):
        return {'id': request['id'], 'state': request['state'], 'mode': request['mode'], 'helper': request['helper'],
                'provider': request.get('provider'), 'text': request['text'], 'mentions': list(request.get('mentions') or []),
                'linked_return': request['linked_return'], 'delegations': request['delegations'], 'error': request['error'],
                'activity': self.activity(request), 'created_at': request['created_at'], 'detail': request['error'] or DETAILS[request['state']],
                'knowledge': ({'count':len(request['knowledge_snapshot']['rules']), 'omitted':request['knowledge_snapshot']['omitted']}
                              if 'knowledge_snapshot' in request else None)}

    def activity(self, request):
        """The help events the library logged on this request's run, as plain text. No auth metadata, no payload passthrough."""
        by_id = {d.get('id'): d for d in request.get('delegations') or []}
        marks = ','.join('?' for _ in HELP_EVENT_KINDS)
        rows = self.store.db.execute(f'SELECT seq, kind, data FROM harness_events WHERE request_id=? AND kind IN ({marks}) ORDER BY seq',
                                     (request['id'], *HELP_EVENT_KINDS)).fetchall()
        out = []
        for seq, kind, raw in rows:
            data = json.loads(raw)
            target = str(data.get('target') or by_id.get(data.get('delegation_id'), {}).get('handle') or 'a helper')[:64]
            error = error_text(data.get('error'))[:TEXT_CLIP]
            if kind == 'help_requested':
                text = f'Asked {target} for help ({PROVIDER_LABELS.get(str(data.get("provider")), str(data.get("provider") or "helper"))})'
            elif kind == 'help_returned':
                text = f'{target} returned an answer'
            elif kind == 'help_refused':
                text = f'Help from {target} refused: {error}'
            else:
                text = f"{target}'s answer could not be returned: {error}"
            out.append({'seq': seq, 'kind': kind, 'text': text})
        return out

    def conversation(self, binding):
        """What the composer may do here: the one helper serving this conversation's provider, account and model, or why none.
        Read-only; never opens the harness store and never launches anything."""
        try:
            scope = self.scope(binding)
        except Rejected as exc:
            return {'state': 'unavailable', 'helper': None, 'provider': binding.get('provider'), 'model': binding.get('model'), 'detail': str(exc)}
        helpers, excluded = self.helpers()
        availability = self.availability(helpers, excluded)
        base = {'helper': None, 'provider': scope['provider'], 'model': scope['model']}
        if availability['state'] != 'available':
            return {**base, 'state': 'unavailable', 'detail': availability['detail']}
        for name, spec in sorted(helpers.items()):
            if spec.provider == scope['provider'] and helper_allowed(spec, scope) is None:
                label = PROVIDER_LABELS.get(spec.provider, spec.provider)
                # The catalogue is what the library would let this helper ask: same account label, real, not itself.
                catalogue = [{'handle': '@' + other, 'provider': s.provider, 'model': s.model, 'can_ask': s.can_ask}
                             for other, s in sorted(helpers.items()) if other != name and s.provider != FAKE_PROVIDER and s.account == spec.account]
                detail = (f'Coordination with {label} answers in text only, in this conversation, under this account and model. '
                          'No project file is changed; use a coding task for that.')
                if spec.can_ask and catalogue:
                    detail += ' It may ask the linked helpers below through the harness when your message asks for that; each ask is recorded here.'
                elif spec.can_ask:
                    detail += ' It is allowed to ask linked helpers, but none is linked to this account.'
                return {**base, 'state': 'available', 'helper': name, 'account': spec.account, 'can_ask': spec.can_ask, 'catalogue': catalogue, 'detail': detail}
        reason = next((x['reason'] for x in excluded if x['profile'] == binding.get('profile_id') and x['model'] == scope['model']), None)
        return {**base, 'state': 'unavailable', 'detail': reason or "No helper serves this conversation's account and model. Nothing else is selected instead."}

    def resolve(self, binding, mention):
        """An explicit ``@handle`` against this conversation's catalogue (stable ids), then against the library once a runtime is open."""
        if type(mention) is not str:
            raise Rejected('A mention is a string like @name')
        name = mention.strip().lstrip('@').lower()
        if not HANDLE.match(name):
            return {'resolved': False, 'mention': mention.strip(), 'reason': 'not a valid @handle: letters, digits and hyphens only'}
        conversation = self.conversation(binding)
        handle = '@' + name
        if conversation['state'] != 'available':
            return {'resolved': False, 'mention': handle, 'reason': conversation['detail']}
        entry = next((c for c in conversation['catalogue'] if c['handle'] == handle), None)
        if entry is None:
            reason = ("that is this conversation's own helper; it cannot ask itself" if name == conversation['helper']
                      else f'{handle} is not a helper linked to this conversation. Link the accounts in Settings, on Connected accounts, under Help between your accounts, first.')
            return {'resolved': False, 'mention': handle, 'reason': reason}
        result = {'resolved': True, **entry, 'asker_can_ask': conversation['can_ask'], 'verified': None}
        if not conversation['can_ask']:
            result['note'] = "This conversation's helper answers in text only; switch on asking linked helpers for its Claude account to act on mentions."
        if self.supervisor is not None:
            try:
                result['verified'] = self.supervisor.resolve(self.scope(binding)['project_id'], handle)
            except HarnessRefused as exc:
                result['verification'] = str(exc)  # not mapped yet in this project, or refused by the library; stated, not hidden
        return result

    # Scope mapping. Only Dervo-owned ids cross the port. Anything unmappable is rejected before any store opens.
    def access(self, args):
        from host import ACTOR
        return self.store.binding(args['channelId'], args['projectId'], actor=ACTOR)

    def scope(self, binding):
        if binding['provider'] not in ('claude', 'codex') or binding['category'] not in ('personal', 'work') or binding['visibility'] not in ('private', 'shared'):
            raise Rejected('Conversation account cannot be mapped to a harness scope')
        if not binding.get('profile_id'):
            raise Rejected('Conversation has no account. The harness scope cannot be mapped')
        if type(binding.get('model')) is not str or not binding['model']:
            raise Rejected('Conversation has no model. The harness scope cannot be mapped')
        if not Path(binding['cwd']).is_dir():
            raise Rejected('Project folder is missing. The harness scope cannot be mapped')
        return {'project_id': 'project:' + binding['project_id'], 'domain': binding['category'], 'room_id': 'conversation:' + binding['id'],
                'visibility': binding['visibility'], 'account': self.account_label(binding['profile_id']), 'provider': binding['provider'],
                'model': binding['model']}

    def busy(self, binding):
        """One writer per conversation: the harness yields to chat turns and coding tasks."""
        if any(r['state'] not in TERMINAL for r in self.requests(binding['id'])):
            return True
        if any(not job.closed and job.binding['id'] == binding['id'] for job in self.host.turns.values()):
            return True
        coding = getattr(self.host, 'coding', None)
        if coding is not None:
            for task_id, turn in coding.turns.items():
                if turn.closed:
                    continue
                row = self.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
                if row and json.loads(row[0])['context']['thread_id'] == binding['id']:
                    return True
        return False

    def open(self):
        if self.supervisor is None:
            from host import ACTOR
            self.supervisor = self.factory(self.host.root / STORE_DIR, owner=ACTOR, helpers=lambda: self.helpers()[0])
            if hasattr(self.supervisor, 'set_access_check'):
                self.supervisor.set_access_check(self.host.require_beta_access,
                                                 running=getattr(self.host, 'require_run_access', None))
            elif hasattr(self.host, 'account_session') and hasattr(self.host.account_session, 'require_access'):
                self.supervisor.close()
                self.supervisor = None
                raise Rejected('This coordinator runtime needs the Dervo account access check before it can start helpers.')
        return self.supervisor

    def read(self, request_id, binding):
        row = self.store.db.execute('SELECT document FROM harness_requests WHERE id=?', (request_id,)).fetchone()
        request = json.loads(row[0]) if row else None
        if not request or request['thread_id'] != binding['id'] or request['project_id'] != binding['project_id']:
            raise Rejected('Unknown harness request in this conversation')
        return request

    # Operations
    def request(self, args):
        self.host.require_beta_access()
        text, client_id, mode, helper = args['text'], args['clientId'], args['mode'], args['helper']
        if mode not in MODES:
            raise Rejected('Choose coordinate or execute')
        if type(text) is not str or not text.strip() or len(text.encode()) > 32768:
            raise Rejected('A coordination request needs text up to 32 KiB')
        if type(client_id) is not str or not client_id:
            raise Rejected('A coordination request needs a request ID')
        if type(helper) is not str:
            raise Rejected('A coordination request names one bound helper')
        binding = self.access(args)
        scope = self.scope(binding)
        if mode == 'execute':
            return {'id': None, 'state': 'unsupported', 'detail': EXECUTE_DETAIL}
        helpers, excluded = self.helpers()
        availability = self.availability(helpers, excluded)
        if availability['state'] != 'available':
            return {'id': None, 'state': 'unavailable', 'detail': availability['detail']}
        spec = helpers.get(helper)
        if spec is None:
            raise Rejected('Unknown helper. Bound helpers: ' + ', '.join(availability['helpers']))
        reason = helper_allowed(spec, scope)
        if reason:
            raise Rejected(f'Helper {helper!r} cannot act in this conversation: {reason}')
        mentions = args.get('mentions', [])
        if type(mentions) is not list or any(type(m) is not str for m in mentions):
            raise Rejected('Mentions are a list of @handles')
        resolved = []
        for mention in mentions:
            answer = self.resolve(binding, mention)
            if not answer['resolved']:
                raise Rejected(f'{answer["mention"]} cannot be asked: {answer["reason"]}')
            if not answer['asker_can_ask']:
                raise Rejected(answer['note'])
            resolved.append(answer['handle'])
        existing = next((r for r in self.requests(binding['id']) if r['client_id'] == client_id), None)
        if existing:
            if existing['text'] != text or existing['helper'] != helper:
                raise Rejected('Request ID was already used for different content')
            self.host.organise_submitted(binding, 'coordinator', existing['id'], existing['text'])
            return self.view(existing)
        if self.busy(binding):
            raise Rejected('This conversation already has an active request')
        from host import ACTOR
        from project_knowledge import ProjectKnowledge
        saved_binding={key:binding[key] for key in ('profile_id','identity','provider','model','cwd','visibility')}
        previous=next((r['knowledge_snapshot'] for r in reversed(self.requests(binding['id']))
                       if r.get('binding')==saved_binding and 'knowledge_snapshot' in r),None)
        knowledge=ProjectKnowledge(self.store).snapshot(binding,ACTOR,previous=[previous] if previous else [],query=text)
        request = dict(id=uuid.uuid4().hex, client_id=client_id, thread_id=binding['id'], project_id=binding['project_id'], scope=scope,
                       binding=saved_binding, knowledge_snapshot=knowledge,
                       helper=helper, provider=spec.provider, mode='coordinate', text=text, mentions=resolved, state='queued',
                       harness_request_id=None, run_id=None, linked_return=None, delegations=[], error=None, pulled_seq=0, created_at=int(time.time()))
        self.save(request)
        self.record(request, 'submitted', {'mode': 'coordinate', 'helper': helper, 'room_id': scope['room_id'], 'mentions': resolved, 'can_ask': spec.can_ask})
        self.host.organise_submitted(binding, 'coordinator', request['id'], text)
        try:
            handle = self.open().submit(request['id'], scope, text, helper=helper,
                                        **({'guidance':knowledge['text']} if knowledge['text'] else {}))
        except HarnessRefused as exc:
            self.finish(request, 'failed', f'The harness refused the request: {exc}')
        except Exception as exc:
            # The library may have accepted the request before raising. Never resubmit.
            self.finish(request, 'unknown', f'The harness did not confirm the request. Observation was lost: {exc}')
        else:
            request.update(harness_request_id=handle['request_id'], run_id=handle['run_id'])
            self.save(request)
            if handle.get('guidance_error'):
                request['guidance_error'] = request['error'] = handle['guidance_error']
                self.record(request, 'guidance_unconfirmed', {'error': handle['guidance_error']})
                self.save(request)
                self.cancel(request)
        return self.view(request)

    def cancel(self, request):
        if request['state'] in TERMINAL:
            return self.view(request)
        self.record(request, 'cancel_requested', {})
        request['state'] = 'stopping'
        self.save(request)
        try:
            self.open().cancel(request['harness_request_id'])
        except HarnessRefused as exc:
            self.record(request, 'cancel_refused', {'error': str(exc)})
        except Exception as exc:
            self.finish(request, 'unknown', f'The harness did not confirm cancellation. Observation was lost: {exc}')
        return self.view(request)

    def events(self, binding, after):
        if type(after) is not int or isinstance(after, bool) or after < 0:
            raise Rejected('Events need a non-negative sequence number')
        rows = self.store.db.execute('''SELECT e.seq,e.request_id,e.kind,e.data FROM harness_events e
            JOIN harness_requests r ON r.id=e.request_id WHERE r.thread_id=? AND e.seq>? ORDER BY e.seq LIMIT ?''',
            (binding['id'], after, EVENT_LIMIT)).fetchall()
        return [{'seq': row['seq'], 'request_id': row['request_id'], 'kind': row['kind'], 'data': json.loads(row['data'])} for row in rows]

    def dispatch(self, operation, args):
        if operation == 'harness_boundary':
            return self.boundary(args)
        if operation == 'harness_actions':
            return self.action_toggle(args)
        binding = self.access(args)
        if operation == 'harness_resolve':
            return self.resolve(binding, args['mention'])
        if operation == 'harness_request':
            return self.request(args)
        if operation == 'harness_events':
            return self.events(binding, args['after'])
        if operation == 'harness_requests':
            return [self.view(r) for r in self.requests(binding['id'])]
        request = self.read(args['requestId'], binding)
        if operation == 'harness_cancel':
            return self.cancel(request)
        saved=request.get('binding')
        if request.get('knowledge_snapshot') is not None and (not saved or any(saved.get(key)!=binding[key]
                for key in ('profile_id','identity','provider','model','cwd','visibility'))):
            raise Rejected('This request belongs to a different saved account or conversation scope.')
        return {**self.view(request),'knowledge_snapshot':request.get('knowledge_snapshot')}

    def tick(self):
        self.live = False  # a helper is working: the host keeps the Mac awake for it (keep_awake.py)
        if self.supervisor is None:
            return
        # A finished asking run may still have a helper working on its behalf: keep pumping and reflecting until the
        # delegation settles, so a linked answer is delivered and shown instead of stalling until the next request.
        active = [r for r in self.requests() if r['state'] not in TERMINAL
                  or (r.get('harness_request_id') and any(d.get('status') in PENDING_DELEGATION for d in r.get('delegations') or []))]
        self.live = bool(active)
        if not active:
            return
        try:
            self.supervisor.tick()
        except Exception as exc:
            for request in active:
                self.finish(request, 'unknown', f'Harness observation was lost: {exc}')
            return
        for request in active:
            if not request['harness_request_id']:
                self.finish(request, 'unknown', 'The harness never confirmed this request.')
                continue
            try:
                for event in self.supervisor.pull_events(request['harness_request_id'], request['pulled_seq']):
                    seq = event['seq']
                    if type(seq) is not int or seq <= request['pulled_seq']:
                        raise Rejected('Harness events must arrive in increasing order')
                    self.record(request, event['kind'], event['data'])
                    request['pulled_seq'] = seq
                    self.save(request)
                status = self.supervisor.status(request['harness_request_id'])
                state = status['state']
                if state not in STATES:
                    raise Rejected(f'The harness reported an unrecognized state: {state!r}')
            except Exception as exc:
                self.finish(request, 'unknown', f'Harness observation was lost: {exc}')
                continue
            request['linked_return'] = status.get('linked_return')
            request['delegations'] = status.get('delegations') or []
            request['error'] = request.get('guidance_error') or status.get('error')
            if request['state'] in TERMINAL and request['state'] != state:
                pass  # an app-side terminal verdict (unknown) is never overwritten by a later observation
            elif not (request['state'] == 'stopping' and state not in TERMINAL):
                request['state'] = state
            self.save(request)

    def close(self):
        if self.supervisor is not None:
            try:
                self.supervisor.close()
            except Exception:
                pass
            self.supervisor = None
        for request in self.requests():
            if request['state'] not in TERMINAL:
                self.finish(request, 'unknown', 'Host closed before the harness confirmed the outcome. A restart does not replay it.')
