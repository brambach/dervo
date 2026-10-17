"""The proactive side of Home: scheduled jobs and a quiet heartbeat (8 Oct 2026, agents-and-assistant.md).

Ported from NanoClaw's design (qwibitai/nanoclaw, MIT; see NOTICE below), with OpenClaw's heartbeat on top. Nothing
here is a second assistant with its own memory. A job runs as one of the project's agents (dervo_agents.py), as an
ordinary request in that agent's conversation, so it has a run record and shows in Home and the sidebar like any run.
What it found, and the heartbeat's notices, are said in Home's own chat. So it's one assistant: Home's.

**Jobs.** A job is a prompt, the agent that runs it, a schedule (once, every N minutes, or daily at a time on chosen
days) and a gate. Jobs are rows; a sweep from the host's tick, at most once a minute, looks for due ones. NanoClaw's
"script gates" become declarative gates checked against Dervo's own records with no model call: ``always``,
``changed`` (some other thread of the project moved since this job last ran) and ``needs_you`` (some thread waits on
you). A gate that finds nothing records a skipped run with the reason, and nothing is woken, which is what keeps an
always-on assistant cheap. Arbitrary shell gates are left out on purpose: they'd be code run with nobody watching.

**Safety.** A job starts with nobody clicking, so it runs the way Home's own unclicked jobs do (``origin: home``,
conversation_coding.HOME_LIMITED): never with full access, whatever the project's setting, and a read-only agent stays
read-only. Every run is saved, with its request ID fixed (``assistant-<job>-<slot>``), before anything is sent, so a
restart finishes the same run instead of starting a second one. Slots missed while the Mac slept run once, not once per
missed slot. At most ``STARTS_PER_SWEEP`` runs start per sweep, so waking the Mac doesn't start a dozen at once.

**Heartbeat.** Every ``minutes`` (30 by default) the sweep reads the activity index for threads that wait on you and
that you haven't opened since. Anything new since the last beat is said once in Home, in one short message; when
nothing new needs you, it says nothing. No model call, ever: the words come from the records.

**Chat-managed.** ``assistant_parse`` turns "every weekday at 8 @catch-up catch me up" into a proposed job (schedule,
agent, prompt) for Home or the composer to confirm with ``assistant_job_save``. Nothing is saved by parsing.

NOTICE: the job, sweep, recurrence and gate model follow NanoClaw, Copyright (c) Qwibit AI, MIT License. No NanoClaw
code is copied; the design is re-implemented here in Python against Dervo's own records.
"""
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta

from dervo_connections import Rejected

FIELDS = {
    'assistant_jobs': {'projectId'},
    'assistant_job_save': {'projectId', 'name', 'prompt', 'agentId', 'schedule', 'gate'},
    'assistant_job_pause': {'projectId', 'jobId', 'paused'},
    'assistant_job_delete': {'projectId', 'jobId'},
    'assistant_job_run_now': {'projectId', 'jobId'},
    'assistant_runs': {'projectId', 'jobId'},
    'assistant_parse': {'projectId', 'text'},
    'assistant_heartbeat': {'projectId'},
    'assistant_heartbeat_set': {'projectId', 'enabled', 'minutes'},
}
OPTIONAL_FIELDS = {'assistant_job_save': {'jobId'}}

PREFIX = 'assistant-'
SWEEP_SECONDS = 60.0
STARTS_PER_SWEEP = 1  # one start a round: each holds the host's lane for a coding_request (about 80 ms measured)
GATES_PER_SWEEP = 10
FAILURES_BEFORE_PAUSE = 3
MIN_EVERY, MAX_EVERY = 15, 7 * 24 * 60
HEARTBEAT_MINUTES, HEARTBEAT_MIN, HEARTBEAT_MAX = 30, 5, 24 * 60
RUNS_KEPT, JOBS_PER_PROJECT = 50, 32
NAME_CHARS, PROMPT_CHARS, SAID_CHARS = 80, 8000, 1200
GATES = ('always', 'changed', 'needs_you')
DAY_NAMES = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
WEEKDAYS, EVERY_DAY = [0, 1, 2, 3, 4], [0, 1, 2, 3, 4, 5, 6]
ENDED = ('done', 'skipped', 'failed', 'stopped', 'unknown')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS assistant_jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS assistant_runs (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, client_id TEXT NOT NULL UNIQUE, '
               'ended INTEGER NOT NULL DEFAULT 0, document TEXT NOT NULL)')
    db.execute('CREATE INDEX IF NOT EXISTS assistant_runs_job ON assistant_runs(job_id)')
    db.execute('CREATE TABLE IF NOT EXISTS assistant_heartbeat (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')


# ---------------------------------------------------------------------------------------------------------- schedules
def check_schedule(schedule, now=None):
    """The schedule as stored, checked without coercion, or Rejected with what's wrong."""
    if not isinstance(schedule, dict):
        raise Rejected('Pick when the job runs: once, every so often, or daily at a time.')
    kind = schedule.get('kind')
    if kind == 'once' and set(schedule) == {'kind', 'at'}:
        at = schedule['at']
        if type(at) not in (int, float) or at <= 0:
            raise Rejected('Pick when the one-off job runs.')
        return {'kind': 'once', 'at': float(at)}
    if kind == 'every' and set(schedule) == {'kind', 'minutes'}:
        minutes = schedule['minutes']
        if type(minutes) is not int or not MIN_EVERY <= minutes <= MAX_EVERY:
            raise Rejected(f'A repeating job runs every {MIN_EVERY} minutes to every 7 days')
        return {'kind': 'every', 'minutes': minutes}
    if kind == 'daily' and set(schedule) == {'kind', 'time', 'days'}:
        clock, days = schedule['time'], schedule['days']
        if type(clock) is not str or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', clock):
            raise Rejected('A daily job runs at a time like 08:00')
        if type(days) is not list or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise Rejected('Pick at least one day for a daily job.')
        return {'kind': 'daily', 'time': clock, 'days': sorted(set(days))}
    raise Rejected('Pick when the job runs: once, every so often, or daily at a time.')


def next_due(schedule, after, last=None):
    """The first time this schedule is due strictly after ``after`` (local time for daily), or None when it's done.
    ``last`` is when an every-N job was last due, so it keeps its rhythm instead of drifting with each sweep."""
    kind = schedule['kind']
    if kind == 'once':
        return schedule['at'] if schedule['at'] > after else None
    if kind == 'every':
        step = schedule['minutes'] * 60
        if last is None:
            return after + step
        missed = max(0, int((after - last) // step))
        return last + (missed + 1) * step  # slots missed while the Mac slept are run once, not once each
    hour, minute = (int(part) for part in schedule['time'].split(':'))
    start = datetime.fromtimestamp(after)
    for offset in range(8):
        day = (start + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day.weekday() in schedule['days'] and day.timestamp() > after:
            return day.timestamp()
    return None


def describe(schedule):
    if schedule['kind'] == 'once':
        return 'once, ' + datetime.fromtimestamp(schedule['at']).strftime('%a %d %b at %H:%M')
    if schedule['kind'] == 'every':
        minutes = schedule['minutes']
        return f'every {minutes // 60} hours' if minutes % 60 == 0 and minutes > 60 else 'every hour' if minutes == 60 \
            else f'every {minutes} minutes'
    days = schedule['days']
    when = ('every day' if days == EVERY_DAY else 'weekdays' if days == WEEKDAYS else
            'weekends' if days == [5, 6] else ', '.join(DAY_NAMES[d].capitalize() + 's' for d in days))
    return f"{when} at {schedule['time']}"


_CLOCK = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?'
# "Mondays" on its own is a schedule; "Monday" on its own ("sunday best") needs "every", "on" or "each" in front.
_PLURAL_DAY = r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s'
_DAY = r'(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)s?'


def _clock(hour, minute, half):
    hour, minute = int(hour), int(minute or 0)
    half = (half or '').replace('.', '')
    if half == 'pm' and hour < 12:
        hour += 12
    if half == 'am' and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f'{hour:02d}:{minute:02d}'


_TIME_WORDS = {'morning': '09:00', 'noon': '12:00', 'midday': '12:00', 'afternoon': '14:00', 'evening': '18:00',
               'night': '21:00', 'tonight': '21:00', 'midnight': '00:00'}
_EVERY_N = re.compile(r'\b(?:every|each)\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b')
_HOURLY = re.compile(r'\b(?:every\s+hour|hourly)\b')
_PART = r'(?:\s+(morning|afternoon|evening|night))?'  # "every weekday morning", "tomorrow evening"
_DAILY = re.compile(rf'\b(?:every|each)\s+(weekday|weekend|day|night|morning|evening|afternoon)s?{_PART}\b|\b(weekdays|weekends|daily|nightly){_PART}\b')
_DAYS_AFTER = re.compile(rf'\b(?:every|on|each)\s+({_DAY}(?:\s*(?:,|and|&)\s*{_DAY})*)\b')
_DAYS_FIRST = re.compile(rf'^\s*({_PLURAL_DAY}(?:\s*(?:,|and|&)\s*{_DAY})*)\b')  # "Fridays, @gardener ..." at the start only
_TOMORROW = re.compile(rf'\btomorrow{_PART}\b')
_IN = re.compile(r'\bin\s+(\d+)\s*(minutes?|mins?|hours?|hrs?)\b')
_AT = re.compile(rf'(?<![\w@])(?:at|@)\s+{_CLOCK}(?![\w:])')
_BARE = re.compile(r'(?<![\w:@])(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)(?!\w)|(?<![\w:@])(\d{1,2}):(\d{2})(?![\w:])')
_WORD = re.compile(r'\b(?:at|in\s+the|every|each)\s+(noon|midday|midnight|morning|afternoon|evening|night)\b|\b(tonight)\b')


def _find_clock(lower, taken, loose):
    """(time "HH:MM", span) of the time of day the words give, outside ``taken``, or None. ``loose`` lets a bare "at 8"
    count, which it can only after a repeat ("every weekday at 8"); on its own, "look at 2 files" isn't a time."""
    def free(match):
        return not any(match.start() < b and a < match.end() for a, b in taken)
    for match in _AT.finditer(lower):
        if free(match) and (loose or match.group(2) or match.group(3)):
            clock = _clock(*match.groups())
            if clock:
                return clock, match.span()
    for match in _BARE.finditer(lower):
        if free(match):
            clock = _clock(match.group(1), match.group(2), match.group(3)) if match.group(1) else _clock(match.group(4), match.group(5), None)
            if clock:
                return clock, match.span()
    for match in _WORD.finditer(lower):
        if free(match):
            return _TIME_WORDS[match.group(1) or match.group(2)], match.span()
    return None


def parse_schedule(text, now=None):
    """(schedule, the rest of the text) from plain words, or (None, text) when it names no schedule. Covers what people
    say: "every weekday at 8", "at 8am every weekday", "daily at 7:30am", "every night at 11pm", "every day at noon",
    "on mondays and fridays at 5pm", "every 2 hours", "hourly", "tomorrow at 9", "at 6pm" (the next 6pm), "in 2 hours".
    Anything else is left for the person to set, and the confirm card shows what was read before anything is saved."""
    now = time.time() if now is None else now
    lower = text.lower()  # the same length as text, so a span here cuts the same words out of the person's own casing
    schedule, spans = None, []
    every = _EVERY_N.search(lower)
    hourly = _HOURLY.search(lower)
    daily = _DAILY.search(lower)
    named = _DAYS_AFTER.search(lower) or _DAYS_FIRST.search(lower)
    if every:
        amount, unit = int(every.group(1)), every.group(2)
        schedule, spans = {'kind': 'every', 'minutes': max(MIN_EVERY, min(MAX_EVERY, amount * 60 if unit.startswith('h') else amount))}, [every.span()]
    elif hourly:
        schedule, spans = {'kind': 'every', 'minutes': 60}, [hourly.span()]
    elif daily or named:
        if daily:
            word, part = daily.group(1) or daily.group(3), daily.group(2) or daily.group(4)
            days = WEEKDAYS if word.startswith('weekday') else [5, 6] if word.startswith('weekend') else EVERY_DAY
            spans, implied = [daily.span()], _TIME_WORDS.get(part or ('night' if word == 'nightly' else word))
        else:
            names = re.findall(_DAY, named.group(1))
            days, spans, implied = sorted({[n[:3] for n in DAY_NAMES].index(name[:3]) for name in names}), [named.span()], None
        found = _find_clock(lower, spans, loose=True)
        if found:
            spans.append(found[1])
        clock = found[0] if found else implied or '09:00'
        schedule = {'kind': 'daily', 'time': clock, 'days': days}
    else:
        tomorrow, later = _TOMORROW.search(lower), _IN.search(lower)
        if later:
            amount, unit = int(later.group(1)), later.group(2)
            schedule, spans = {'kind': 'once', 'at': now + amount * (3600 if unit.startswith('h') else 60)}, [later.span()]
        else:
            found = _find_clock(lower, [tomorrow.span()] if tomorrow else [], loose=bool(tomorrow))
            if found or tomorrow:
                clock = found[0] if found else _TIME_WORDS.get(tomorrow.group(1) or 'morning')
                hour, minute = (int(p) for p in clock.split(':'))
                base = datetime.fromtimestamp(now) + timedelta(days=1 if tomorrow else 0)
                at = base.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
                if not tomorrow and at <= now:
                    at = next_due({'kind': 'daily', 'time': clock, 'days': EVERY_DAY}, now)
                schedule = {'kind': 'once', 'at': at}
                spans = ([tomorrow.span()] if tomorrow else []) + ([found[1]] if found else [])
    if schedule is None:
        return None, text
    rest = text
    for a, b in sorted(spans, reverse=True):
        rest = rest[:a] + ' ' + rest[b:]
    return schedule, re.sub(r'\s{2,}', ' ', rest).strip(' ,.:;-')


# ---------------------------------------------------------------------------------------------------------- Home
def agent_threads(db):
    """Every agent's own conversation. Gates and the heartbeat leave these out: an agent's run is reported by the mention
    or job that started it, and counting it as "something moved" would let two jobs wake each other forever."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dervo_agents'").fetchone():
        return set()
    return {json.loads(r[0]).get('channel_id') for r in db.execute('SELECT document FROM dervo_agents')}


def home_post(host, project_id, text, *, source, title, ref=None):
    """Say one thing in this project's Home chat as Home itself, with no model call. The narrow seam to Home: the
    Home coordinator's owner can swap this for its own push without anything here changing. False when Home can't take
    it now (the project is gone or archived)."""
    import project_orchestrator
    try:
        scope = project_orchestrator.scope_of(host.store, project_id)
        home = project_orchestrator.Orchestrator(host.store, host=host)
        # Its own request id, so Home lists it as its own row with its own title, never inside the last thing you asked.
        document = dict(kind='reply', state='ready', arrival=False, text=text[:SAID_CHARS * 4], parts=[], looked=[], mentions=[],
                        detail=None, refs={}, changes=[], notes=[], request_id=str(uuid.uuid4()),
                        assistant=dict(source=source, title=_clip(title, 80), ref=ref or {}))
        with host.store.db:
            home._add(scope, document)
        return True
    except Rejected:
        return False


def _clip(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


# ---------------------------------------------------------------------------------------------------------- the host side
class Assistant:
    def __init__(self, host, agents=None):
        self.host, self.store, self.db = host, host.store, host.store.db
        with self.db:
            schema(self.db)
        self.agents = agents
        self._swept_at, self._more = None, False

    def _agents(self):
        if self.agents is None:
            from dervo_agents import Agents
            self.agents = getattr(self.host, 'dervo_agents', None) or Agents(self.host)
        return self.agents

    # ---------------------------------------------------------------- records
    def jobs(self, project_id):
        return [json.loads(r[0]) for r in self.db.execute('SELECT document FROM assistant_jobs WHERE project_id=? ORDER BY rowid',
                                                         (project_id,))]

    def job(self, project_id, job_id):
        row = self.db.execute('SELECT document FROM assistant_jobs WHERE project_id=? AND id=?', (project_id, job_id)).fetchone()
        if row is None:
            raise Rejected('Unknown job')
        return json.loads(row[0])

    def _put_job(self, job):
        with self.db:
            self.db.execute('INSERT INTO assistant_jobs VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                            (job['id'], job['project_id'], json.dumps(job)))

    @staticmethod
    def _ended(run):
        """Off the sweep's list: finished and, unless there's nothing to say, said in Home. Kept on the list until Home has
        it, so a restart between the two can't lose the result."""
        return run['state'] in ENDED and (run.get('posted_home') or run['state'] == 'skipped' or run.get('quiet'))

    def _put_run(self, run, before=None):
        if before is not None and json.dumps(run, sort_keys=True) == before:
            return  # nothing changed: no write, so an idle minute stays an idle tick
        ended = int(bool(self._ended(run)))
        with self.db:
            self.db.execute('INSERT INTO assistant_runs VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ended=excluded.ended, '
                            'document=excluded.document', (run['id'], run['job_id'], run['client_id'], ended, json.dumps(run)))
            if ended:  # keep the newest RUNS_KEPT of this job
                self.db.execute('DELETE FROM assistant_runs WHERE job_id=? AND ended=1 AND rowid NOT IN (SELECT rowid FROM assistant_runs '
                                'WHERE job_id=? ORDER BY rowid DESC LIMIT ?)', (run['job_id'], run['job_id'], RUNS_KEPT))

    def runs(self, job_id, limit=RUNS_KEPT):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT document FROM assistant_runs WHERE job_id=? ORDER BY rowid DESC LIMIT ?', (job_id, limit))]

    def job_view(self, job):
        agent = self._agents().get(job['project_id'], job['agent_id'])
        last = self.runs(job['id'], 1)
        return dict(job, when=describe(job['schedule']), agent=agent and dict(handle=agent['handle'], name=agent['name']),
                    detail=job.get('paused_why') or (None if agent else 'Its agent was deleted. Pick another agent to run it.'),
                    last_run=last[0] if last else None)

    @staticmethod
    def _terms(agent):
        """What you approved a job to run as. A job runs only while its agent still matches, so editing an agent from
        read-only to change, or onto another account, never turns an approved job into something else (safety review)."""
        return dict(scope=agent['scope'], profile_id=agent['profile_id'], model=agent['model'], channel_id=agent['channel_id'])

    # ---------------------------------------------------------------- operations
    def dispatch(self, operation, args, actor):
        required, optional = FIELDS[operation], OPTIONAL_FIELDS.get(operation, set())
        if not isinstance(args, dict) or not (required <= set(args) <= required | optional):
            raise Rejected('Unsupported operation or fields')
        project_id = self._agents()._project(args['projectId'])
        if operation == 'assistant_jobs':
            return [self.job_view(j) for j in self.jobs(project_id)]
        if operation == 'assistant_job_save':
            return self.job_view(self.save(project_id, args))
        if operation == 'assistant_job_pause':
            job = self.job(project_id, args['jobId'])
            if type(args['paused']) is not bool:
                raise Rejected('Pause or resume, nothing else.')
            if job['state'] == 'done':
                raise Rejected('This one-off job already ran.')
            if not args['paused']:
                due = next_due(job['schedule'], time.time(), job.get('last_due'))
                if due is None:
                    raise Rejected('Its time passed while it was paused. Edit it to pick a new time.')
                agent = self._agents().get(project_id, job['agent_id'])
                if agent is None:
                    raise Rejected('Its agent was deleted. Edit it to pick another agent.')
                # Resuming is approving it again as the agent is now.
                job.update(next_due=due, terms=self._terms(agent), failures=0, paused_why=None)
            job['state'] = 'paused' if args['paused'] else 'active'
            self._put_job(job)
            return self.job_view(job)
        if operation == 'assistant_job_delete':
            self.job(project_id, args['jobId'])
            with self.db:
                # Runs it already started keep their records in the agent's conversation; only the schedule goes.
                self.db.execute('DELETE FROM assistant_jobs WHERE id=?', (args['jobId'],))
            return [self.job_view(j) for j in self.jobs(project_id)]
        if operation == 'assistant_job_run_now':
            job = self.job(project_id, args['jobId'])
            # A click: the gate doesn't apply, the access stays Home's unclicked-job access (the click is on a schedule,
            # not on the work), and the request ID is this click's own.
            run = self._new_run(job, time.time(), trigger='now')
            return self._start(job, run, gated=False)
        if operation == 'assistant_runs':
            job = self.job(project_id, args['jobId'])
            return self.runs(job['id'])  # read only; the sweep settles them
        if operation == 'assistant_parse':
            return self.parse(project_id, args['text'])
        if operation == 'assistant_heartbeat':
            return self.heartbeat_view(project_id)
        return self.heartbeat_set(project_id, args)

    def save(self, project_id, args):
        import secret_scan
        from dervo_agents import _clean
        name = _clean(args['name'], NAME_CHARS, 'A name', multiline=False)
        prompt = _clean(args['prompt'], PROMPT_CHARS, 'What the job asks')
        if secret_scan.redact(prompt) != prompt or secret_scan.redact(name) != name:
            raise Rejected('Remove credentials from the job. It’s sent every time it runs.')
        if args['gate'] not in GATES:
            raise Rejected('Pick when it runs: always, only when another thread moved, or only when a thread waits on you.')
        agent = self._agents().get(project_id, args['agentId']) if type(args['agentId']) is str else None
        if agent is None:
            raise Rejected('A job runs as one of this project’s agents. Pick one, or make one first.')
        now = time.time()
        schedule = check_schedule(args['schedule'])
        existing = self.job(project_id, args['jobId']) if 'jobId' in args else None
        if existing is None and len(self.jobs(project_id)) >= JOBS_PER_PROJECT:
            raise Rejected(f'A project has at most {JOBS_PER_PROJECT} scheduled jobs.')
        due = next_due(schedule, now)
        if due is None:
            raise Rejected('That time has passed. Pick a time still to come.')
        # Editing keeps a paused job paused; it only starts again when you resume it.
        state = 'paused' if existing and existing['state'] == 'paused' else 'active'
        job = dict(existing or {}, id=(existing or {}).get('id') or uuid.uuid4().hex, project_id=project_id, name=name, prompt=prompt,
                   agent_id=agent['id'], terms=self._terms(agent), schedule=schedule, gate=args['gate'], state=state, next_due=due,
                   last_due=None, failures=0, paused_why=None if state == 'active' else (existing or {}).get('paused_why'),
                   created_at=(existing or {}).get('created_at') or now, updated_at=now,
                   last_run_at=(existing or {}).get('last_run_at'))
        self._put_job(job)
        return job

    def parse(self, project_id, text):
        """A proposed job from plain words. Saves nothing; the person confirms with assistant_job_save."""
        from dervo_agents import handles_in
        if type(text) is not str or not text.strip():
            raise Rejected('Say when and what, like "every weekday at 8, @reviewer look over yesterday’s work".')
        schedule, rest = parse_schedule(text.strip())
        agents = self._agents()
        agent = next((agents.by_handle(project_id, h) for h in handles_in(rest) if agents.by_handle(project_id, h)), None)
        prompt = re.sub(r'(?<![\w@.])@' + re.escape(agent['handle']) + r'\b', '', rest).strip(' ,.:;-') if agent else rest
        # Only an explicit "only when/if": "check if anything changed" is the job's own words, not a condition on it.
        gate = 'needs_you' if re.search(r'\bonly\s+(?:when|if)\b[^.]*\b(?:need|waiting|blocked)', text, re.I) else \
            'changed' if re.search(r'\bonly\s+(?:when|if)\b[^.]*\b(?:change[sd]?|moved|new)\b', text, re.I) else None
        if gate is None:
            # A repeating job wakes a model only when its threads moved, unless the person says to run it regardless.
            # A one-off always runs: it was asked for once, at a time the person chose.
            regardless = re.search(r'\b(?:even\s+if|regardless|no\s+matter|every\s+time)\b', text, re.I)
            gate = 'changed' if schedule and schedule['kind'] != 'once' and not regardless else 'always'
        missing = [what for what, ok in (('when', schedule), ('agent', agent), ('prompt', prompt)) if not ok]
        return dict(schedule=schedule, when=describe(schedule) if schedule else None, agentId=agent and agent['id'],
                    handle=agent and agent['handle'], prompt=prompt or None, name=_clip(prompt, 40) if prompt else None,
                    gate=gate, missing=missing)

    # ---------------------------------------------------------------- the sweep
    def tick(self, now=None, force=False):
        """From the host's tick. Once a minute, or on the next round when the last sweep left due work behind (it starts
        at most one run a round, so a round never holds the host's lane for more than one start)."""
        clock = time.monotonic()
        if not force and not self._more and self._swept_at is not None and clock - self._swept_at < SWEEP_SECONDS:
            return
        if not self._more:
            self._swept_at = clock
        self._more = self.sweep(time.time() if now is None else now)

    def sweep(self, now):
        """One round. True when due work is left for the next round."""
        started = gates = 0

        def budget():
            return started < STARTS_PER_SWEEP and gates < GATES_PER_SWEEP

        more = False
        # Runs saved but never sent (a host that closed between the two) go first, under their own request IDs.
        for (document,) in self.db.execute("SELECT document FROM assistant_runs WHERE ended=0 ORDER BY rowid").fetchall():
            run = json.loads(document)
            try:
                if run['state'] == 'due':
                    if not budget():
                        more = True
                        continue
                    job = self._job_or_none(run)
                    if job is None:
                        run.update(state='failed', detail='Its job was deleted before it started.', quiet=True)
                        self._put_run(run)
                        continue
                    gates += 1
                    started += self._start(job, run, gated=True, now=now)['state'] in ('working', 'queued')
                elif run['state'] in ('working', 'queued'):
                    self._refresh(run)
                elif run['state'] in ENDED:
                    job = self._job_or_none(run)
                    if job is None:
                        run['quiet'] = True
                        self._put_run(run)
                    else:
                        self._say_run(job, run)  # finished, but Home didn't take it last time
            except Exception as exc:  # noqa: BLE001 - one bad run mustn't stop the others
                print(f'Scheduled run {run.get("id")} skipped a round: {type(exc).__name__}: {exc}', file=sys.stderr)
        for (document,) in self.db.execute('SELECT document FROM assistant_jobs ORDER BY rowid').fetchall():
            job = json.loads(document)
            if job['state'] != 'active' or job['next_due'] is None or job['next_due'] > now:
                continue
            if not budget():
                more = True
                break  # the rest are still due on the next round
            try:
                slot = job['next_due']
                run = self._new_run(job, slot, trigger='schedule')
                # The job moves on before anything is sent, so a crash here can't run the same slot twice: the saved run
                # is finished by the next sweep (above) under its own request ID.
                following = next_due(job['schedule'], now, slot)
                job.update(next_due=following, last_due=slot, state='active' if following is not None else 'done')
                self._put_job(job)
                if run['state'] == 'due':  # a run a crashed round already started is left as it is
                    gates += 1
                    started += self._start(job, run, gated=True, now=now)['state'] in ('working', 'queued')
            except Exception as exc:  # noqa: BLE001
                print(f'Scheduled job {job.get("id")} skipped a round: {type(exc).__name__}: {exc}', file=sys.stderr)
        if not more:
            more = self.beat(now)
        return more

    def _job_or_none(self, run):
        row = self.db.execute('SELECT document FROM assistant_jobs WHERE id=?', (run['job_id'],)).fetchone()
        return json.loads(row[0]) if row else None

    def _new_run(self, job, slot, trigger):
        client = f"{PREFIX}{job['id'][:12]}-{int(slot)}" if trigger == 'schedule' else f"{PREFIX}{job['id'][:12]}-now-{uuid.uuid4().hex[:8]}"
        existing = self.db.execute('SELECT document FROM assistant_runs WHERE client_id=?', (client,)).fetchone()
        if existing:
            return json.loads(existing[0])
        run = dict(id=uuid.uuid4().hex, job_id=job['id'], project_id=job['project_id'], client_id=client, slot=slot, trigger=trigger,
                   state='due', created_at=time.time(), channel_id=None, task_id=None, detail=None, answer=None, ended_at=None,
                   posted_home=False)
        self._put_run(run)
        return run

    def gate(self, job, agent, now):
        """None when the job should wake its agent, else the reason it was skipped. Records only, no model call."""
        if job['gate'] == 'always':
            return None
        import conversation_activity
        conversation_activity.schema(self.db)
        skip = agent_threads(self.db)
        threads = [r[0] for r in self.db.execute(
            "SELECT id FROM threads WHERE project_id=? AND visibility='shared'", (job['project_id'],)) if r[0] not in skip]
        if job['gate'] == 'changed':
            since = job.get('last_run_at') or job['created_at']
            if threads and self.db.execute(
                    f"SELECT 1 FROM conversation_task_state WHERE thread_id IN ({','.join('?' * len(threads))}) AND changed>? LIMIT 1",
                    (*threads, since)).fetchone():
                return None
            return 'Nothing in the project moved since it last ran.'
        waiting = [tid for tid, item in conversation_activity.summaries(self.db, threads).items() if item['status'] == 'needs']
        return None if waiting else 'No thread was waiting on you.'

    def _pause(self, job, why):
        job.update(state='paused', paused_why=why)
        self._put_job(job)

    def _start(self, job, run, *, gated, now=None):
        now = time.time() if now is None else now
        agents = self._agents()
        agent = agents.get(job['project_id'], job['agent_id'])
        if agent is None:
            run.update(state='failed', detail='Its agent was deleted. Pick another agent to run it.', ended_at=now)
            self._put_run(run)
            self._pause(job, run['detail'])
            self._say_run(job, run)
            return run
        if job.get('terms') and job['terms'] != self._terms(agent):
            why = (f"@{agent['handle']} changed after you set this job up (what it may change, its account or its model), so it "
                   'didn’t run. Resume it to run it as the agent is now.')
            run.update(state='failed', detail=why, ended_at=now)
            self._put_run(run)
            self._pause(job, why)
            self._say_run(job, run)
            return run
        if gated:
            skipped = self.gate(job, agent, now)
            if skipped:
                run.update(state='skipped', detail=skipped, ended_at=now)
                self._put_run(run)
                return run
        problem = agents.state(agent)
        if problem == 'ready':
            text = (f"This is the scheduled job \"{job['name']}\" ({describe(job['schedule'])}), which the user set up for you. "
                    f"Nobody is watching it run, so what you find is said in Home.\n\n{job['prompt']}")
            request = {'projectId': job['project_id'], 'channelId': agent['channel_id'], 'clientId': run['client_id'],
                       'text': agents.request_text(agent, 'Home', text), 'queueIfBusy': True,
                       # Started with nobody clicking: never full access (conversation_coding.HOME_LIMITED).
                       'origin': 'home', **({'readOnly': True} if agent['scope'] == 'read' else {})}
            run['channel_id'] = agent['channel_id']
            try:
                result = self.host.dispatch('coding_request', request)
            except Exception as exc:  # noqa: BLE001 - the run says why; nothing reached the agent
                result = {'detail': str(exc)}
        else:
            result = {'detail': problem}
        if result.get('taskId'):
            run.update(state='working', task_id=result['taskId'], detail=None)
        elif result.get('state') == 'queued' or result.get('queueId'):
            run.update(state='queued', detail=None)
        else:
            run.update(state='failed', detail=_clip(result.get('detail') or 'The agent couldn’t start.', 300), ended_at=now)
        self._put_run(run)
        job = self._job_or_none(run) or job
        job['last_run_at'] = now
        if run['state'] == 'failed':
            # Said once, then paused after a few in a row, so a job that can't run doesn't post every slot forever.
            job['failures'] = job.get('failures', 0) + 1
            if job['failures'] >= FAILURES_BEFORE_PAUSE:
                job.update(state='paused', paused_why=f'Paused after {FAILURES_BEFORE_PAUSE} runs in a row didn’t start: {run["detail"]}')
            if job['failures'] > 1 and job['state'] != 'paused':
                run['quiet'] = True
                self._put_run(run)
        else:
            job['failures'] = 0
        self._put_job(job)
        if run['state'] == 'failed':
            self._say_run(job, run)
        return run

    def _refresh(self, run):
        if run['state'] not in ('working', 'queued'):
            return run
        before = json.dumps(run, sort_keys=True)
        from dervo_agents import settle_from_task
        coding = getattr(self.host, 'coding', None)
        if run['task_id'] is None and coding is not None:
            request = coding.conversation.find(run['channel_id'], run['client_id'])
            if request and request.get('task_id'):
                run.update(state='working', task_id=request['task_id'])
            else:
                item = coding.conversation.queue.find(run['channel_id'], run['client_id'])
                if item is None:
                    run.update(state='failed', detail='It was cancelled before it started.', ended_at=time.time())
                elif item['state'] != 'queued':
                    run.update(state='failed', detail=_clip(item.get('error') or 'The queued run didn’t start.', 300), ended_at=time.time())
        agent = self._agents().get(run['project_id'], (self._job_or_none(run) or {}).get('agent_id'))
        ending = settle_from_task(self.db, run['task_id'], handle=agent['handle'] if agent and agent['scope'] == 'change' else None)
        if ending is not None:
            run.update(state={'answered': 'done'}.get(ending['state'], ending['state']), task_state=ending['task_state'],
                       answer=ending['answer'], detail=ending['detail'], ended_at=time.time())
        self._put_run(run, before)
        if run['state'] in ENDED:
            job = self._job_or_none(run)
            if job is not None:
                self._say_run(job, run)
        return run

    def _say_run(self, job, run):
        if run.get('posted_home') or run.get('quiet') or run['state'] == 'skipped':
            return  # a gate that found nothing stays quiet: that's the point of it
        agent = self._agents().get(job['project_id'], job['agent_id'])
        who = f"@{agent['handle']}" if agent else 'Its agent'
        if run['state'] == 'done':
            said = _clip(run['answer'] or 'It finished without saying anything.', SAID_CHARS) + (f"\n\n{run['detail']}" if run.get('detail') else '')
            title = f"{who}: {job['name']}"
        elif run['state'] == 'unknown':
            said = run['detail']
            title = f"{who}: {job['name']}, outcome unknown"
        else:
            said = f"It didn’t finish: {run['detail']}" + (f"\n\n{job['paused_why']}" if job.get('paused_why') else '')
            title = f"{who}: {job['name']} didn’t finish"
        if home_post(self.host, job['project_id'], said, source='job', title=title,
                     ref=dict(kind='job', job_id=job['id'], run_id=run['id'], channel_id=run.get('channel_id'), task_id=run.get('task_id'))):
            run['posted_home'] = True
            self._put_run(run)

    # ---------------------------------------------------------------- the heartbeat
    def heartbeat(self, project_id):
        row = self.db.execute('SELECT document FROM assistant_heartbeat WHERE project_id=?', (project_id,)).fetchone()
        return json.loads(row[0]) if row else dict(enabled=True, minutes=HEARTBEAT_MINUTES, last_at=None, seen=[], said_at=None)

    def _put_heartbeat(self, project_id, beat):
        with self.db:
            self.db.execute('INSERT INTO assistant_heartbeat VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                            (project_id, json.dumps(beat)))

    def heartbeat_view(self, project_id):
        beat = self.heartbeat(project_id)
        return dict(enabled=beat['enabled'], minutes=beat['minutes'], last_at=beat['last_at'], said_at=beat['said_at'])

    def heartbeat_set(self, project_id, args):
        if type(args['enabled']) is not bool:
            raise Rejected('Turn the heartbeat on or off.')
        if type(args['minutes']) is not int or not HEARTBEAT_MIN <= args['minutes'] <= HEARTBEAT_MAX:
            raise Rejected(f'The heartbeat runs every {HEARTBEAT_MIN} minutes to once a day.')
        beat = self.heartbeat(project_id)
        beat.update(enabled=args['enabled'], minutes=args['minutes'])
        self._put_heartbeat(project_id, beat)
        return self.heartbeat_view(project_id)

    def attention(self, project_id):
        """Threads of this project that wait on you and that you haven't opened since: [(key, thread, line)]. Records only."""
        import conversation_activity
        conversation_activity.schema(self.db)
        archived = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_archives'").fetchone()
        rows = self.db.execute("SELECT t.id, c.name FROM threads t LEFT JOIN local_channels c ON c.id=t.id "
                               "WHERE t.project_id=? AND t.visibility='shared'" +
                               (' AND t.id NOT IN (SELECT thread_id FROM conversation_archives)' if archived else ''),
                               (project_id,)).fetchall()
        skip = agent_threads(self.db)
        names = {tid: name or 'A thread' for tid, name in rows if tid not in skip}
        found = []
        for tid, item in conversation_activity.summaries(self.db, list(names)).items():
            if item['status'] != 'needs' or not item['attention']:
                continue
            why = item.get('pending_question') or item.get('summary') or (
                'Dervo restarted mid-run, so it can’t tell how far this got' if item.get('lost') else 'it’s waiting on you')
            found.append((f"{tid}:{item.get('at')}", tid, f"#{names[tid]}: {_clip(why, 160)}"))
        return found

    def beat(self, now):
        """The heartbeat for projects that have an agent (making one is choosing the assistant), one due project a round.
        True when another project is still due."""
        projects = sorted({r[0] for r in self.db.execute('SELECT project_id FROM dervo_agents')})
        due = []
        for project_id in projects:
            beat = self.heartbeat(project_id)
            if beat['enabled'] and (beat['last_at'] is None or now - beat['last_at'] >= beat['minutes'] * 60):
                due.append((project_id, beat))
        if not due:
            return False
        project_id, beat = due[0]
        items = self.attention(project_id)
        # The first beat only learns what's already waiting: Home's Waiting on you shows those, so having the assistant
        # never dumps a backlog into the chat.
        new = [item for item in items if item[0] not in beat['seen']] if beat['last_at'] is not None else []
        beat.update(last_at=now, seen=[item[0] for item in items])  # what's no longer waiting drops out, so it can come back
        if new:
            head = 'One thread needs you' if len(new) == 1 else f'{len(new)} threads need you'
            said = '\n'.join('- ' + line for _, _, line in new[:6]) + (f'\n- and {len(new) - 6} more' if len(new) > 6 else '')
            if home_post(self.host, project_id, said, source='heartbeat', title=head,
                         ref=dict(kind='heartbeat', threads=[tid for _, tid, _ in new])):
                beat['said_at'] = now
        self._put_heartbeat(project_id, beat)
        return len(due) > 1
