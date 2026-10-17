"""How a task's newest run ended, by what ended it (beta.17.1).

Bryce, 5 Oct 2026: "things shouldn't fail unless they are absolutely something that failed". Up to beta.17 every
``blocked`` and ``disconnected`` task showed a failure X, whatever stopped it. This sorts them from the record alone:

- ``failed``: the work itself failed. The agent or its provider reported an error.
- ``finished``: the agent finished, and a later Dervo step (recording its changes, its checks) didn't complete.
- ``unknown``: Dervo lost track of the run (a restart, a lost process, its own error), so nobody can say how it ended.
- ``stopped``: you stopped it, Dervo paused it when it quit, Dervo settled it as paused after a restart (beta.18,
  ``by='restart'``), or you left a lost run stopped (``left``, the button that was Mark as stopped).
- ``limit``: it reached one of this project's limits (run_limits), or the provider's usage limit.
- ``not_started``: the task never reached the agent, so nothing ran.

Only ``failed`` is a failure. The others never claim success either: nothing here changes a task's state, retry or
approval, it only says which of these the record shows. The thread view (conversation_feed) and pickup read it.
"""

KINDS = ('failed', 'finished', 'unknown', 'stopped', 'limit', 'not_started')
# The lines older records carry, matched so a run ended before 17.1 reads the same way (coding_integration, claude_coding).
CLOSE_LOST_LEAD = 'Dervo quit mid-run'
STOPPED_UNACKNOWLEDGED = 'Stopped before provider acknowledgement'
RESTARTED_UNSENT = ('Dervo closed before this started.', 'Host restarted before this')
PROVIDER_LIMITS = ('usage_limit', 'rate_limit')
# Beta.18 (direction B): what ended a run that isn't a failure, set by coding_integration. ``settled_by_user`` is Leave it
# stopped (Mark as stopped until beta.18), which up to 17.1 fell through to ``failed`` and drew an X. ``restart_paused`` is a
# lost run whose provider record showed it between steps (lost_runs.settle), so Dervo settled it as paused.
SETTLED_BY_USER = 'settled_by_user'
RESTART_PAUSED = 'restart_paused'
# Don't run on a permission request (task_deny): nothing was sent, so it never started. It drew an X up to 17.1.
DENIED = 'Execution scope denied.'


def last_seen(task, run):
    """When the run last did something Dervo saw: its last output, else its last recorded step. None when unknown."""
    seen = run.get('last_seen') if isinstance(run.get('last_seen'), (int, float)) else None
    for token in task.get('tokens') or []:
        if isinstance(token, dict) and token.get('run_id') == run.get('id'):
            for key in ('ended_at', 'started_at'):
                at = token.get(key)
                if isinstance(at, (int, float)) and not isinstance(at, bool) and (seen is None or at > seen):
                    seen = at
    return round(seen, 3) if seen is not None else None


def step_of(run):
    """What the run said it was doing, from its task guide."""
    guide = run.get('guide') if isinstance(run.get('guide'), dict) else {}
    step = guide.get('step') if guide.get('run_id') == run.get('id') else None
    return step if isinstance(step, str) and step.strip() else None


def limit_of(run, error):
    """Which Dervo limit a host stop was: the run's record, else the line older records carry."""
    if run.get('limit') in ('idle', 'hard', 'stop', 'output'):
        return run['limit']
    if ' sent nothing for ' in error:
        return 'idle'
    if 'limit, so Dervo stopped it' in error:
        return 'hard'
    if 'seconds of Stop' in error:
        return 'stop'
    return 'output'


def finished_step(task, error):
    """Which Dervo step didn't complete after the agent finished (``snag``): record, checks, changed or nothing."""
    if task.get('unrecorded'):
        return 'record'
    if error.startswith(('Tests were not run', 'Host verification did not finish')):
        return 'checks'
    if 'changed after its last check' in error:
        return 'changed'
    if error.startswith('No file attempt'):
        return 'nothing'
    return 'record'


def limit_words(ending, seconds=None):
    """'Stopped after 2 hours with no output', from a limit ending and the limit it hit (when the caller knows it)."""
    import run_limits
    limit = ending.get('limit')
    if limit == 'idle':
        return f'Stopped after {run_limits.duration(seconds)} with no output' if seconds else 'Stopped after a long silence'
    if limit == 'hard':
        return f'Stopped at the {run_limits.duration(seconds)} run limit' if seconds else 'Stopped at the run length limit'
    if limit == 'attempts':
        return 'Stopped at the attempt limit'
    if limit == 'provider':
        return 'Stopped at the usage limit'
    return 'Stopped: it sent more output than Dervo keeps'


def glyph_words(ending):
    """(glyph, words) for a non-failure ending, the shapes the window draws: None for ``failed``, whose words are the
    caller's own (the provider's failure, or what blocked it)."""
    kind = ending.get('kind') if isinstance(ending, dict) else None
    if kind == 'finished':
        return 'needs', 'Finished, but a Dervo step didn’t complete'
    if kind == 'unknown':
        return 'unknown', 'Outcome not confirmed'
    if kind == 'stopped':
        if ending.get('left'):
            return 'stopped', 'Left stopped'
        return 'stopped', {'quit': 'Paused when Dervo quit', 'restart': 'Paused when Dervo restarted'}.get(ending.get('by'), 'You paused it')
    if kind == 'limit':
        return 'stopped', limit_words(ending)
    if kind == 'not_started':
        return 'stopped', 'Didn’t start. Nothing was sent'
    return None


def classify(task):
    """The newest run's ending as {kind, ...}, or None for a task that's working, waiting or ended the ordinary way
    (a result to review, completed, answered, approved)."""
    state = task.get('state')
    if state not in ('blocked', 'disconnected'):
        return None
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    run = runs[-1] if runs else {}
    error = task.get('error') if isinstance(task.get('error'), str) else ''
    facts = dict(last_seen=last_seen(task, run), step=step_of(run))
    if state == 'disconnected':
        if error.startswith(CLOSE_LOST_LEAD):
            return dict(facts, kind='stopped', by='quit')
        if error == STOPPED_UNACKNOWLEDGED:
            return dict(facts, kind='stopped', by='you', confirmed=False)
        after = run.get('after_close') if isinstance(run.get('after_close'), dict) else {}
        return dict(facts, kind='unknown', cause='closed' if after else 'lost')
    cause = run.get('end_cause')
    if cause == SETTLED_BY_USER:
        return dict(facts, kind='stopped', by='you', confirmed=False, left=True)
    if cause == RESTART_PAUSED:
        return dict(facts, kind='stopped', by='restart', confirmed=True)
    if run.get('state') == 'interrupted' or error.startswith('Stopped before completion'):
        return dict(facts, kind='stopped', by='you', confirmed=True)
    if cause == 'host_limit':
        limit = limit_of(run, error)
        if limit == 'stop':
            return dict(facts, kind='stopped', by='you', confirmed=True)
        return dict(facts, kind='limit', limit=limit)
    if error.startswith(DENIED):
        return dict(facts, kind='not_started', declined=True)
    if cause in ('host_restart', 'not_started') or error.startswith(RESTARTED_UNSENT):
        return dict(facts, kind='not_started')
    from task_evidence import ATTEMPT_LIMIT_LEAD
    if run.get('limit_hit') == 'attempts' or error.startswith(ATTEMPT_LIMIT_LEAD):
        return dict(facts, kind='limit', limit='attempts')
    if run.get('provider_failure') in PROVIDER_LIMITS:
        return dict(facts, kind='limit', limit='provider')
    if run.get('state') == 'completed':
        return dict(facts, kind='finished', snag=finished_step(task, error))
    return dict(facts, kind='failed')
