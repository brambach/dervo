"""Usage events for the private beta: where testers go in Dervo and how their tasks end, never what's in them.

The allowlist below is the whole contract. An event that isn't on it, or a property that isn't on its event's list, or a
value that isn't one of the enumerated words or a bounded count, is dropped here, before anything is queued. Nothing a
tester writes, no names, paths, emails, model output, error text or keys can pass: every string value is one of a fixed
set of words, or a model family matched against a fixed shape, and every number is a bounded count.

What leaves the Mac, per event: the event name, its cleaned properties, the app version and a random id made for this
install. Rows go to the Dervo beta's own Supabase project (the one sign-in uses), as the signed-in user, whose id the
server takes from the session token (supabase/migrations/202609190002_dervo_usage_events.sql).

Error reports (error_reports.py) ride on the same switch, install id, queue and sends: the shape of an error, never
its text.

Delivery: events wait in memory, capped, and go at most every few minutes and once on quit, on a daemon thread so the
app never waits for them. A failed send is dropped, never retried. Signed out, they wait (capped, and at most a day) for
a sign-in, and signing out throws them away. Turning sharing off throws away what's waiting and records nothing more.
"""
import json
import re
import threading
import time
import uuid

FIELDS = {
    'usage_event': {'event', 'props'},        # the renderer's two events; the host records the rest itself
    'usage_sharing': set(),                   # the switch, and whether the one-time notice is due
    'usage_sharing_set': {'enabled'},
    'usage_notice_seen': set(),
    'usage_report': {'reload'},               # Testers page, admins only; the server checks again
    'usage_people': {'reload'},               # Testers page's People section: counts per tester, admins only
    'error_event': {'errorType', 'screen'},   # the renderer's crash screen: an error name and a screen, nothing else
    'error_report': {'reload'},               # Testers page's Errors section, admins only
}
# The notice was reworded when error reports joined usage sharing. An install that saw the earlier notice sees this one once.
NOTICE_MARK = 'notice:errors'

PROVIDERS = ('claude', 'codex')
ACCOUNT_KINDS = ('subscription', 'api_key')
OUTCOMES = ('completed', 'failed', 'stopped')
ERROR_CODES = ('api_error', 'usage_limit', 'rate_limit', 'overloaded', 'server_error', 'connection', 'host_limit',
               'start_failed', 'uncertain', 'other')
SETTINGS_PAGES = ('model', 'access', 'checks', 'helpers', 'general', 'account', 'preferences', 'accounts', 'updates', 'testers')
SCREENS = ('home', 'thread', 'knowledge', 'search', 'catch_up', 'releases') + tuple('settings.' + page for page in SETTINGS_PAGES)
FIRST_RUN_STEPS = ('sign_in', 'pending', 'folder', 'start', 'project_created')
# Model families: a Claude family with its generation, a GPT version with a known variant, or one of two words. Nothing
# else matches, so a custom model id can't carry a name out.
CLAUDE_MODEL = re.compile(r'(?:claude-)?(fable|opus|sonnet|haiku)(?:-(\d{1,2})(?:-(\d{1,2}))?)?(?:-\d{8})?(?:\[1m\])?')
GPT_MODEL = re.compile(r'gpt-(\d{1,2}(?:\.\d{1,2})?)(?:-([a-z]+))?(?:-.*)?')
GPT_VARIANTS = ('codex', 'mini', 'max', 'sol', 'terra', 'luna', 'astra')
FAMILY = re.compile(r'(?:fable|opus|sonnet|haiku)(?:-\d{1,2}(?:\.\d{1,2})?)?|gpt-\d{1,2}(?:\.\d{1,2})?(?:-(?:'
                    + '|'.join(GPT_VARIANTS) + r'))?|default|other')


def _word(*values):
    return lambda value: value if type(value) is str and value in values else None


def _count(limit):
    return lambda value: value if type(value) is int and not isinstance(value, bool) and 0 <= value <= limit else None


def _family(value):
    return value if type(value) is str and FAMILY.fullmatch(value) else None


PROVIDER, ACCOUNT_KIND, FAMILY_VALUE = _word(*PROVIDERS), _word(*ACCOUNT_KINDS), _family
SCOPE = _word('thread', 'project')
EVENTS = {
    'app_opened': {},
    'update_installed': {},
    'screen_viewed': {'screen': _word(*SCREENS)},
    'first_run_step': {'step': _word(*FIRST_RUN_STEPS), 'status': _word('reached', 'completed')},
    'model_picker_opened': {},
    'account_switched': {'provider': PROVIDER, 'account_kind': ACCOUNT_KIND, 'model_family': FAMILY_VALUE, 'scope': SCOPE},
    'model_changed': {'provider': PROVIDER, 'model_family': FAMILY_VALUE, 'scope': SCOPE},
    'home_request': {'outcome': _word('completed', 'failed'), 'threads_created': _count(20)},
    'thread_created': {},
    'task_started': {'provider': PROVIDER, 'model_family': FAMILY_VALUE, 'account_kind': ACCOUNT_KIND},
    'task_ended': {'provider': PROVIDER, 'model_family': FAMILY_VALUE, 'account_kind': ACCOUNT_KIND, 'outcome': _word(*OUTCOMES),
                   'seconds': _count(7 * 86400), 'error_code': _word(*ERROR_CODES)},
    'continue_in_other': {'provider': PROVIDER},
    'knowledge_rule_added': {},
    'lesson_approved': {},
    'lesson_dismissed': {},
    'feedback_opened': {},
}
# An event is dropped whole when one of these is missing or off the list: a screen view with no screen says nothing.
REQUIRED = {'screen_viewed': {'screen'}, 'first_run_step': {'step', 'status'}, 'task_ended': {'outcome'}}
FROM_APP = ('screen_viewed', 'model_picker_opened')


def clean(event, props):
    """The event's allowed properties with allowed values, or None when the event itself isn't allowed."""
    if type(event) is not str or event not in EVENTS or not isinstance(props, dict):
        return None
    allowed = EVENTS[event]
    kept = {}
    for key, value in props.items():
        check = allowed.get(key) if type(key) is str else None
        if check is not None and check(value) is not None:
            kept[key] = value
    if not REQUIRED.get(event, set()) <= set(kept):
        return None
    return kept


def model_family(model):
    """``claude-opus-5`` is opus-5, ``claude-haiku-4-5-20251001`` is haiku-4.5, ``gpt-5.6-sol`` is gpt-5.6-sol. An empty
    model is the account's default; anything else that doesn't match a known shape is other."""
    if model is None or model == '' or model == 'default':
        return 'default'
    if type(model) is not str or len(model) > 120:
        return 'other'
    model = model.lower()
    found = CLAUDE_MODEL.fullmatch(model)
    if found:
        name, major, minor = found.groups()
        return name + ('-' + major + ('.' + minor if minor else '') if major else '')
    found = GPT_MODEL.fullmatch(model)
    if found:
        version, variant = found.groups()
        return 'gpt-' + version + ('-' + variant if variant in GPT_VARIANTS else '')
    return 'other'


def _version(value):
    return value if type(value) is str and re.fullmatch(r'[A-Za-z0-9.+_-]{1,40}', value) else None


def running_version():
    try:
        from pathlib import Path
        from beta_update_host import running_version as read
        return _version(read(Path(__file__).resolve().parent.parent)['releaseId'])
    except Exception:
        return None  # development builds have no release metadata; rows go without a version


def emit(host, event, props=None):
    """Record an event from the host's own code. Never raises: usage is never worth a failed task or request."""
    try:
        usage = getattr(host, 'usage', None)
        if isinstance(usage, UsageEvents):
            usage.record(event, props or {})
    except Exception:
        pass


def task_started(host, run_id, binding):
    try:
        usage = getattr(host, 'usage', None)
        if isinstance(usage, UsageEvents):
            usage.task_started(run_id, binding)
    except Exception:
        pass


def task_ended(host, run_id, binding, state, cause=None):
    try:
        usage = getattr(host, 'usage', None)
        if isinstance(usage, UsageEvents):
            usage.task_ended(run_id, binding, state, cause)
    except Exception:
        pass


def _supabase_send(client, session, rows):
    client.record_usage(session, rows)


def _supabase_send_errors(client, session, rows):
    client.record_errors(session, rows)


class UsageEvents:
    FLUSH_SECONDS = 180       # at most one send every three minutes, and one on quit
    QUEUE_LIMIT = 300         # the oldest are dropped past this
    HOLD_SECONDS = 86400      # how long events wait for a sign-in
    QUIT_SECONDS = 1.5        # how long quitting waits for the last send
    ERROR_KINDS = 50          # distinct errors held between sends; a new kind past this is dropped
    ERROR_COUNT = 10000       # one kind's count stops here

    def __init__(self, host, *, clock=time.time, version=None, sender=None, error_sender=None):
        self.host, self.db, self.clock = host, host.store.db, clock
        self.version = _version(version) if version is not None else running_version()
        self.sender, self.error_sender = sender, error_sender
        self.queue = []           # (queued_at, row)
        self.errors = {}          # error_reports fields -> [first seen, row with its count]
        self.started = {}         # run id -> (monotonic start, provider, family, account kind)
        self.sending = None
        self.client = None
        self.last_flush = clock()
        self.quit_started = None
        self.closed = False
        with self.db:
            self.db.execute('create table if not exists usage_settings (id integer primary key check(id=1), document text not null)')
        row = self.db.execute('select document from usage_settings where id=1').fetchone()
        self.settings = {'schema': 1, 'enabled': True, 'install_id': str(uuid.uuid4()), 'notice_seen': False,
                         'version': None, 'marks': []}
        fresh = row is None
        if row is not None:
            try:
                saved = json.loads(row[0])
                if (type(saved) is not dict or set(saved) != set(self.settings) or saved['schema'] != 1
                        or type(saved['enabled']) is not bool or type(saved['notice_seen']) is not bool
                        or str(uuid.UUID(saved['install_id'])) != saved['install_id'] or type(saved['marks']) is not list
                        or not all(type(mark) is str for mark in saved['marks'])):
                    raise ValueError()
                self.settings = saved
            except Exception:
                # A damaged record starts over as a new install, with sharing off and the notice shown again, so it's
                # never silently on.
                self.settings['enabled'] = False
                fresh = True
        previous = self.settings['version']
        if self.version and previous != self.version:
            self.settings['version'] = self.version
        if fresh or self.version and previous != self.version:
            self._save()
        self.record('app_opened')
        if self.version and previous and previous != self.version:
            self.record('update_installed')

    # ------------------------------------------------------------------ settings
    def _save(self):
        with self.db:
            self.db.execute('insert into usage_settings values (1,?) on conflict(id) do update set document=excluded.document',
                            (json.dumps(self.settings),))

    def sharing(self):
        return {'enabled': self.settings['enabled'],
                'notice': not self.settings['notice_seen'] or NOTICE_MARK not in self.settings['marks']}

    def _answer_notice(self):
        self.settings['notice_seen'] = True
        if NOTICE_MARK not in self.settings['marks']:
            self.settings['marks'].append(NOTICE_MARK)

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            from dervo_connections import Rejected
            raise Rejected('Share usage is exactly on (true) or off (false)')
        self.settings['enabled'] = enabled
        self._answer_notice()  # choosing either way answers the notice
        if not enabled:
            self.queue.clear()   # off means nothing waiting goes either
            self.errors.clear()
            self.started.clear()
        self._save()
        return self.sharing()

    def notice_seen(self):
        if self.sharing()['notice']:
            self._answer_notice()
            self._save()
        return self.sharing()

    # ------------------------------------------------------------------ recording
    def record(self, event, props=None):
        if not self.settings['enabled'] or self.closed:
            return False
        kept = clean(event, props if props is not None else {})
        if kept is None:
            return False
        self.queue.append((self.clock(), {'install_id': self.settings['install_id'], 'event': event, 'properties': kept,
                                          'app_version': self.version}))
        if len(self.queue) > self.QUEUE_LIMIT:
            del self.queue[:len(self.queue) - self.QUEUE_LIMIT]
        return True

    def record_error(self, report):
        """One error, already reduced to its shape by error_reports. The same one again adds to its count."""
        import error_reports
        if not self.settings['enabled'] or self.closed or type(report) is not dict or set(report) != set(error_reports.FIELDS):
            return False
        report = error_reports.row(report['source'], report['error_type'],
                                   (report['file'], report['function'], report['line']) if report['file'] else None,
                                   report['operation'], report['provider'])
        if report is None:
            return False
        key = tuple(report[field] for field in error_reports.FIELDS)
        held = self.errors.get(key)
        if held is not None:
            held[1]['count'] = min(held[1]['count'] + 1, self.ERROR_COUNT)
            return True
        if len(self.errors) >= self.ERROR_KINDS:
            return False
        self.errors[key] = [self.clock(), {'install_id': self.settings['install_id'], **report, 'app_version': self.version, 'count': 1}]
        return True

    def from_app(self, event, props):
        """The renderer may send only its own two events. Anything else, or anything off the list, is dropped quietly."""
        if event in FROM_APP:
            self.record(event, props if isinstance(props, dict) else {})
        return None

    def _mark(self, step, status):
        """A first-run step, once per install."""
        mark = step + ':' + status
        if mark in self.settings['marks'] or not self.record('first_run_step', {'step': step, 'status': status}):
            return
        self.settings['marks'].append(mark)
        self._save()

    @staticmethod
    def _binding_props(binding):
        return {'provider': binding.get('provider'), 'model_family': model_family(binding.get('model')),
                'account_kind': binding.get('method') or 'subscription'}

    def task_started(self, run_id, binding):
        props = self._binding_props(binding)
        if self.record('task_started', props):
            self.started[run_id] = (time.monotonic(), props)
            if len(self.started) > 200:
                self.started.pop(next(iter(self.started)))

    def task_ended(self, run_id, binding, state, cause=None):
        begun = self.started.pop(run_id, None)
        props = dict(begun[1]) if begun else self._binding_props(binding)
        outcome = 'completed' if state == 'completed' else 'stopped' if state in ('cancelled', 'stopped', 'interrupted') else 'failed'
        props['outcome'] = outcome
        if begun:
            props['seconds'] = max(0, min(int(time.monotonic() - begun[0]), 7 * 86400))
        if outcome != 'completed':
            code = cause if cause in ERROR_CODES else 'uncertain' if state == 'uncertain' else None
            if code is None and outcome == 'failed':
                code = 'other'
            if code:
                props['error_code'] = code
        self.record('task_ended', props)

    # ------------------------------------------------------------------ what the host saw
    def observe(self, operation, args, result):
        """After a request the renderer made succeeded: the few that are events. Never raises."""
        try:
            self._observe(operation, args if isinstance(args, dict) else {}, result)
        except Exception:
            pass

    def _observe(self, operation, args, result):
        if operation == 'dervo_account_sign_out':
            self.queue.clear()  # nothing recorded under one account goes out under the next
            self.errors.clear()
            self._report = self._error_report = self._people = None
            return
        if operation in ('create_conversation', 'create_agent_conversation'):
            self.record('thread_created')
        elif operation == 'coding_retry_on':
            # The thread is on the other provider's account now: that's where it continued.
            profile = self.db.execute('select p.provider from threads t join profiles p on p.id=t.profile_id where t.id=?',
                                      (args.get('channelId'),)).fetchone()
            self.record('continue_in_other', {'provider': profile[0]} if profile else {})
        elif operation == 'knowledge_save' and not args.get('ruleId'):
            self.record('knowledge_rule_added')
        elif operation == 'knowledge_lesson_keep':
            self.record('lesson_approved')
        elif operation == 'knowledge_lesson_dismiss':
            self.record('lesson_dismissed')
        elif operation == 'beta_feedback_context':
            self.record('feedback_opened')
        elif operation in ('conversation_model_set', 'conversation_profile_set', 'project_model_set', 'project_profile_set'):
            self._model_change(operation, args)
        if operation in ('first_run_status', 'first_run_inspect', 'first_run_finish') or operation.startswith('dervo_account_'):
            self._first_run(operation)

    def _model_change(self, operation, args):
        if 'profileId' in args:
            profile = self.db.execute('select provider, method from profiles where id=?', (args['profileId'],)).fetchone()
        elif operation == 'conversation_model_set':
            profile = self.db.execute('select p.provider, p.method from threads t join profiles p on p.id=t.profile_id where t.id=?',
                                      (args.get('channelId'),)).fetchone()
        else:
            profile = self.db.execute('select p.provider, p.method from projects r join profiles p on p.id=r.profile_id where r.id=?',
                                      (args.get('projectId'),)).fetchone()
        provider, kind = (profile[0], profile[1]) if profile else (None, None)
        scope = 'thread' if operation.startswith('conversation_') else 'project'
        if operation.endswith('_profile_set'):
            self.record('account_switched', {'provider': provider, 'account_kind': kind, 'model_family': model_family(args.get('model')),
                                             'scope': scope})
        else:
            self.record('model_changed', {'provider': provider, 'model_family': model_family(args.get('model')), 'scope': scope})

    def _approval_finished(self):
        """Approval finishes when a tester who had to wait for it is approved. First run lets them set everything up
        while they wait, so that usually happens after their project exists (30 Sep: the funnel showed 1 reached, 0
        finished). It's counted whenever it happens, not only before the first project."""
        marks = self.settings['marks']
        if 'pending:reached' not in marks or 'pending:completed' in marks:
            return
        account = getattr(self.host, 'account_session', None)
        status = account.status() if account is not None and hasattr(account, 'status') else None
        if status and status.get('state') == 'signed_in' and (status.get('membership') or {}).get('allowed'):
            self._mark('pending', 'completed')

    def _first_run(self, operation):
        """The first-run funnel, from the host's own state: each step once, and only while there's no project yet.
        Approval is the exception: it finishes whenever the tester who waited for it is approved."""
        if not self.settings['enabled']:
            return
        self._approval_finished()
        if 'project_created:completed' in self.settings['marks']:
            return
        projects = self.db.execute('select count(*) from projects').fetchone()[0]
        if operation == 'first_run_finish':
            if projects == 1:
                self._mark('start', 'completed')
                self._mark('project_created', 'completed')
            return
        if projects:
            return
        if operation == 'first_run_inspect':
            self._mark('folder', 'completed')
            self._mark('start', 'reached')
            return
        account = getattr(self.host, 'account_session', None)
        status = account.status() if account is not None and hasattr(account, 'status') else None
        if status is None:
            if operation == 'first_run_status':
                self._mark('folder', 'reached')  # a build with no beta sign-in starts at the folder
            return
        membership = status.get('membership') or {}
        if status.get('state') != 'signed_in':
            if operation == 'first_run_status' or operation == 'dervo_account_status':
                self._mark('sign_in', 'reached')
            return
        self._mark('sign_in', 'completed')
        if membership.get('status') == 'pending':
            self._mark('pending', 'reached')  # its finish is _approval_finished, above
        if operation == 'first_run_status':
            self._mark('folder', 'reached')

    # ------------------------------------------------------------------ delivery
    def _target(self):
        """The signed-in session and a client for the account's own project, or None to wait."""
        account = getattr(self.host, 'account_session', None)
        if account is None or getattr(account, 'state', None) != 'signed_in':
            return None
        session, client = getattr(account, 'session', None), getattr(account, 'client', None)
        if session is None or getattr(session, 'expires_at', 0) <= self.clock() + 60:
            return None  # the account's own check refreshes the session; this never does
        if self.sender is not None or self.error_sender is not None:
            return client, session
        from supabase_client import SupabaseClient
        if type(client) is not SupabaseClient:
            return None  # only the build's real account client, never a test fake
        return self._client_for(account), session

    def _client_for(self, account):
        """A client of our own for the account's project, so a slow send never holds up sign-in. Test fakes are used as
        they are."""
        from supabase_client import SupabaseClient
        client = account.client
        if type(client) is not SupabaseClient:
            return client
        if self.client is None or self.client.project is not client.project:
            self.client = SupabaseClient(client.project)
        return self.client

    def _collect(self):
        if self.sending is not None and not self.sending.is_alive():
            self.sending = None

    def tick(self):
        self._collect()
        if self.closed or self.sending is not None or not self.queue and not self.errors:
            return
        if self.clock() - self.last_flush >= self.FLUSH_SECONDS:
            self.flush()

    def flush(self):
        """Send what's waiting, once. Taken off the queue before the send starts, so a failure drops it."""
        self.last_flush = self.clock()
        if not self.settings['enabled']:
            self.queue.clear()
            self.errors.clear()
            return None
        cutoff = self.clock() - self.HOLD_SECONDS
        self.queue = [(at, row) for at, row in self.queue if at >= cutoff]
        self.errors = {key: held for key, held in self.errors.items() if held[0] >= cutoff}
        if not self.queue and not self.errors or self.sending is not None:
            return None
        target = self._target()
        if target is None:
            return None  # signed out or checking: wait, capped and for at most a day
        rows = [row for _, row in self.queue]
        errors = [dict(held[1]) for held in self.errors.values()]
        self.queue, self.errors = [], {}
        send = self.sender or _supabase_send
        send_errors = self.error_sender or _supabase_send_errors
        def work():
            for deliver, batch in ((send, rows), (send_errors, errors)):
                if not batch:
                    continue
                try:
                    deliver(target[0], target[1], batch)
                except Exception:
                    pass  # offline, expired or refused: dropped, never retried
        self.sending = threading.Thread(target=work, name='dervo-usage', daemon=True)
        self.sending.start()
        return self.sending

    def close(self):
        """On quit: one last send, started now, waited for in settle() while the rest of the app closes."""
        if self.closed:
            return
        self.quit_started = time.monotonic()
        self._collect()
        if self.sending is None:
            self.flush()
        self.closed = True

    def settle(self):
        thread = self.sending
        if thread is not None and self.quit_started is not None:
            thread.join(max(0.0, self.QUIT_SECONDS - (time.monotonic() - self.quit_started)))

    # ------------------------------------------------------------------ requests
    def dispatch(self, operation, args):
        from dervo_connections import Rejected
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported usage fields')
        if operation == 'usage_event':
            return self.from_app(args['event'], args['props'])
        if operation == 'usage_sharing':
            return self.sharing()
        if operation == 'usage_sharing_set':
            return self.set_enabled(args['enabled'])
        if operation == 'usage_notice_seen':
            return self.notice_seen()
        if operation == 'error_event':
            import error_reports
            report = error_reports.from_renderer(args)
            if report is not None:
                self.record_error(report)
            return None
        if operation == 'error_report':
            return self.error_report(args['reload'])
        if operation == 'usage_people':
            return self.people(args['reload'])
        return self.report(args['reload'])

    # ------------------------------------------------------------------ Testers page (admins only)
    def report(self, reload):
        """Last 7 days for the Testers page. The host checks you're an admin; the server's function checks again."""
        return self._admin_read('_report', 'usage_report', reload, 'Only Dervo admins can see usage.')

    def people(self, reload):
        """The Testers page's People section: each tester's counts for the last 7 days, and when they were last seen. Admins
        only, checked here and again by the server's function."""
        return self._admin_read('_people', 'usage_people', reload, 'Only Dervo admins can see usage.')

    def error_report(self, reload):
        """The Testers page's Errors section: last 7 days, grouped by kind and place. Admins only, checked twice."""
        return self._admin_read('_error_report', 'error_report', reload, 'Only Dervo admins can see error reports.')

    def _admin_read(self, attribute, method, reload, refusal):
        from dervo_connections import Rejected
        if type(reload) is not bool:
            raise Rejected('Unsupported usage fields')
        account = getattr(self.host, 'account_session', None)
        if account is None or not hasattr(account, '_require_admin'):
            raise Rejected(refusal)
        session = account._require_admin()
        state = getattr(self, attribute, None)
        if state is None or reload and state['state'] != 'loading':
            state = {'state': 'loading', 'data': state['data'] if state else None, 'thread': None}
            setattr(self, attribute, state)
            client = self._client_for(account)
            def work():
                try:
                    state['data'] = getattr(client, method)(session, 7)
                    state['state'] = 'ready'
                except Exception:
                    state['state'] = 'error'
            state['thread'] = threading.Thread(target=work, name='dervo-' + method.replace('_', '-'), daemon=True)
            state['thread'].start()
        return {'state': state['state'], 'report': state['data']}
