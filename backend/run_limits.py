"""How long a run may go quiet, how long it may last, and how many attempts a task gets: per project (beta.17.1).

Up to beta.17 these were fixed at 30 minutes of silence, 6 hours a run (claude_coding.IDLE_SECONDS, RUN_SECONDS; Codex
had its own 30-minute silence and no cap) and 16 attempts a task (task_evidence). Bryce, 5 Oct 2026: they ended work
that was fine, so they're looser and each project picks its own. Silence always ends a run, so a stuck run still stops,
and a run takes the limits as they were when it started. Attempts are read when each one is made, so raising the limit
lets the same task carry on.
"""
import json
import time

from dervo_connections import Rejected

IDLE_CHOICES = (1800, 3600, 7200, 14400)  # 30 min, 1 h, 2 h, 4 h. Never off: silence is what ends a stuck run.
RUN_CHOICES = (21600, 43200, 86400, None)  # 6 h, 12 h, 24 h or no cap
ATTEMPT_CHOICES = (16, 64, None)  # None is no limit, with a warning on the setting
DEFAULTS = {'idle_seconds': 7200, 'run_seconds': 86400, 'attempts': 64}
# Each attempt keeps a copy of the files it changed, so a task with no limit grows its record without bound.
NO_ATTEMPT_LIMIT_WARNING = ('With no attempt limit, a task can keep trying for as long as its runs last, and each attempt '
                            'keeps a copy of the files it changed.')
KEYS = {'idleSeconds': ('idle_seconds', IDLE_CHOICES), 'runSeconds': ('run_seconds', RUN_CHOICES),
        'attempts': ('attempts', ATTEMPT_CHOICES)}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS coding_run_limits (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')


def read(db, project_id):
    """This project's limits: the defaults, with whatever the project chose over them."""
    schema(db)
    row = db.execute('SELECT document FROM coding_run_limits WHERE project_id=?', (project_id,)).fetchone() if project_id else None
    chosen = json.loads(row[0]) if row else {}
    limits = dict(DEFAULTS)
    for key, choices in (('idle_seconds', IDLE_CHOICES), ('run_seconds', RUN_CHOICES), ('attempts', ATTEMPT_CHOICES)):
        if key in chosen and chosen[key] in choices:
            limits[key] = chosen[key]
    return limits


def view(db, project_id):
    """What Settings shows: the limits, the choices and the warning for no attempt limit."""
    limits = read(db, project_id)
    return dict(limits, defaults=dict(DEFAULTS), choices={'idle_seconds': list(IDLE_CHOICES), 'run_seconds': list(RUN_CHOICES),
                'attempts': list(ATTEMPT_CHOICES)}, warning=NO_ATTEMPT_LIMIT_WARNING if limits['attempts'] is None else None)


def choose(db, project_id, args, actor):
    """Change one or more of a project's limits. Each must be one of its choices; nothing else is stored."""
    if not project_id:
        raise Rejected('Run limits belong to a project.')
    given = {name: args[name] for name in KEYS if name in args}
    if not given:
        raise Rejected('Choose a run limit to change.')
    with db:
        schema(db)
        row = db.execute('SELECT document FROM coding_run_limits WHERE project_id=?', (project_id,)).fetchone()
        record = json.loads(row[0]) if row else {}
        for name, value in given.items():
            key, choices = KEYS[name]
            if type(value) is bool or value not in choices:
                raise Rejected(f'{name} must be one of: ' + ', '.join('none' if c is None else str(c) for c in choices) + '.')
            record[key] = value
        record.update(actor=actor, at=int(time.time()))
        db.execute('INSERT INTO coding_run_limits (project_id, document) VALUES (?, ?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                   (project_id, json.dumps(record)))
    return view(db, project_id)


def duration(seconds):
    """'30 minutes', '1 hour', '24 hours': how the limit reads in a stop line."""
    if seconds is None:
        return 'no limit'
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f'{minutes} minute' + ('' if minutes == 1 else 's')
    hours = seconds / 3600
    hours = int(hours) if hours == int(hours) else round(hours, 1)
    return f'{hours} hour' + ('' if hours == 1 else 's')
