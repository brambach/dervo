"""What a saved task record says about itself, in one sentence, for Catch up.

Everything here is read from the task document. Nothing is inferred, nothing
is live, and a missing fact stays missing rather than becoming a guess. The
wording matches the task footer's own sentences so the two never disagree.
"""
from task_error import task_error

TOOL_VERBS = {
    'dervo_read_file': 'reading', 'dervo_write_file': 'editing', 'dervo_edit_file': 'editing', 'dervo_create_file': 'creating',
    'dervo_list_files': 'listing project files', 'dervo_search_files': 'searching project files',
    'dervo_run_tests': 'running the configured check', 'dervo_request_help': 'waiting for a helper',
    'dervo_update_task_guide': 'updating the task guide', 'dervo_remember': 'saving to project memory', 'dervo_propose_skill': 'proposing a skill',
    'dervo_search_history': 'looking through earlier chats', 'dervo_suggest_lesson': 'suggesting project guidance',
    'native:Bash': 'running a command', 'native:Read': 'reading', 'native:Edit': 'editing', 'native:Write': 'writing',
    'native:Glob': 'finding files', 'native:Grep': 'searching', 'native:WebFetch': 'fetching a web page', 'native:Connector': 'using a connector',
    'native:WebSearch': 'searching the web',
}
LIVE_TOKEN_STATES = ('executing', 'running', 'queued', 'delivering')
FILE_LIMIT = 8


def _time(value):
    return int(value) if type(value) in (int, float) and value > 0 else None


def _basename(path):
    if not isinstance(path, str):
        return None
    name = path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
    return name[:48] if name and name not in ('.', '..') else None


def task_facts(task, facts):
    """Timestamps, files, check and review facts a row can show truthfully. ``facts`` is task_outcome.outcome_facts."""
    messages = [m for m in task.get('messages') or [] if isinstance(m, dict)]
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    collaboration = task.get('collaboration') if isinstance(task.get('collaboration'), dict) else {}
    review = collaboration.get('review') if isinstance(collaboration.get('review'), dict) else {}
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    run = runs[-1] if runs else {}
    tokens = [t for t in task.get('tokens') or [] if isinstance(t, dict) and (not run.get('id') or t.get('run_id') == run.get('id'))]
    live = next((t for t in reversed(tokens) if t.get('state') in LIVE_TOKEN_STATES), None)
    files = [f for f in (completion.get('changed_files') or []) if isinstance(f, str)]
    return dict(
        askedAt=_time(next((m.get('created_at') for m in reversed(messages) if m.get('role') == 'user'), None)),
        answeredAt=_time(next((m.get('created_at') for m in reversed(messages) if m.get('role') == 'assistant' and str(m.get('text', '')).strip()), None)),
        completedAt=_time(completion.get('at')),
        changedFiles=files[:FILE_LIMIT], changedFileCount=len(files),
        testExitStatus=completion.get('test_exit_status') if type(completion.get('test_exit_status')) is int else None,
        checkProfile=task.get('check_profile') if isinstance(task.get('check_profile'), str) else (completion.get('check_profile') if isinstance(completion.get('check_profile'), str) else None),
        reviewPhase=review.get('phase') if isinstance(review.get('phase'), str) else None,
        runState=run.get('state') if isinstance(run.get('state'), str) else None,
        lastTool=live.get('tool') if live and isinstance(live.get('tool'), str) else None,
        lastPath=_basename(live.get('path')) if live else None,
        outcomeUncertain=facts['outcome_uncertain'], humanDecision=facts['human_decision'],
        # Beta.17.1 (run_ending): how a blocked or lost run ended, so Catch up and search draw an X only for failed work.
        ending=_ending(task),
    )


def _ending(task):
    import run_ending
    return (run_ending.classify(task) or {}).get('kind')


def task_summary(task, detail):
    """One sentence from the record. ``detail`` is task_facts(); the task's error is coerced to text here."""
    state = task.get('state')
    error = task_error(task.get('error'))
    if detail['outcomeUncertain']:
        return 'Outcome unconfirmed. Dervo lost confirmation of a run or tool result.'
    if state == 'working':
        verb = TOOL_VERBS.get(detail['lastTool'] or '')
        if verb and detail['lastPath'] and detail['lastTool'] in ('dervo_read_file', 'dervo_write_file', 'dervo_edit_file', 'dervo_create_file', 'native:Read', 'native:Edit', 'native:Write'):
            return f'Still working: {verb} {detail["lastPath"]}.'
        return f'Still working: {verb}.' if verb else 'Still working.'
    if state == 'needs_permission':
        return 'Waiting for file permission before it can start.'
    if state == 'completed' and detail['completedAt']:
        files = ('Changed ' + ', '.join(detail['changedFiles']) + (f' and {detail["changedFileCount"] - FILE_LIMIT} more' if detail['changedFileCount'] > FILE_LIMIT else '')) if detail['changedFiles'] else 'No file changed'
        check = 'Host type check' if detail['checkProfile'] in ('rust-check', 'typescript-check') else 'Host tests'
        exit_part = f' {check} exit {detail["testExitStatus"]}.' if detail['testExitStatus'] is not None else ''
        review = ' Independent review passed.' if detail['reviewPhase'] == 'passed' else ' No independent review was configured.' if not detail['reviewPhase'] else f' Independent review ended {detail["reviewPhase"]}.'
        return f'{files}.{exit_part}{review} Not marked as approved by you.'
    if state == 'answered':
        return 'Answered in text. No project file was changed.'
    if state == 'idle' and detail['humanDecision'] == 'approved':
        return 'Result approved by you.'
    if state == 'ready_for_review':
        if detail['reviewPhase'] in ('pending', 'initial', 'correction', 'final'):
            return 'Independent review is running.'
        if (task.get('completion') or {}).get('stale') is True if isinstance(task.get('completion'), dict) else False:
            return 'The files changed after this result. It needs a fresh check.'
        if detail['humanDecision'] == 'rejected':
            return 'Waiting for your correction feedback.'
        from task_outcome import review_line
        return review_line(task) + (' Independent review passed.' if detail['reviewPhase'] == 'passed' else '')
    if state in ('blocked', 'disconnected', 'unknown'):
        return f'Couldn’t finish: {error}' if error else 'Couldn’t finish.'
    return ''
