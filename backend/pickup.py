"""Where you left off: what you were doing in a project, and in each thread, from Dervo's records alone.

Bryce, 18 Sep 2026: "Catch-up and picking up where I left off is the most important part of Dervo, more than the
orchestrator." So this never calls a model and never waits on one. It reads what Dervo already keeps: your messages,
each task's state, the step and next step its agent last reported, when you last opened each thread, and when Dervo
was last closed. It works signed out and offline. Home's model may talk about the same facts in the chat; it's never
needed here.

It's about what you were doing, not only what the agents did: what you last asked, what the agent did and where it
stopped, the next step it reported, when you last looked, and whether anything changed after that. What's blocked on
you comes first, then what's running, then what waits for your review, then what you were most recently in.

Every line here is one plain sentence or two, in the voice of the design system (19 Sep): short, true, nothing a record
doesn't hold. Your own words and the agent's reported words lose their Markdown before they're shown.
"""
import json
import re
import sys
import time

from dervo_connections import ON_PROJECT_SIDE, Rejected
from project_record_scope import project_record_scope
from voice import plain as house_style

FIELDS = {'project_pickup': {'projectId', 'profileId'}, 'conversation_pickup': {'projectId', 'channelId'}}
SHOWN = 5  # threads Home shows in full; the rest are counted
QUIET_DAYS = 7  # a thread nobody touched for this long folds into "N more"
ASKED_LIMIT = 90  # what you asked, cut to one readable line
NAME_IN_LEAD = 48  # a thread named after a long first message reads as a name, not a paragraph
NAMED_IN_LEAD = 3  # threads the lead names before it counts the rest
LINE_LIMIT = 160
DAY = 86400

# One word for each place a thread's newest task can be, and how it reads.
STATUS = {'running': 'still running', 'review': 'finished, needs your review', 'permission': 'waiting for your permission',
          'stopped': 'stopped before it finished', 'unconfirmed': 'stopped, outcome not confirmed', 'answered': 'answered',
          'finished': 'finished', 'approved': 'approved', 'new': 'no work yet'}
RESTARTED = 'Dervo restarted. It can’t tell how far this got.'  # a run the host died with (handoff map H3)
CLOSED_ON = 'Stopped when Dervo closed.'  # the same, once the relaunch has read the folder (night 1, finding 2)
YOU_STOPPED = 'You paused this.'  # your own Pause (was Stop until 16.4): nothing needs you (orchestrator map F5)
# Beta.18 (run_ending): a lost run Dervo settled as paused from the provider's own record, a lost run you left stopped
# (Mark as stopped until beta.18), and a quit that paused a run before it could report its step.
RESTART_PAUSED = 'Paused when Dervo restarted.'
LEFT_STOPPED = 'You left this stopped.'
QUIT_PAUSED = 'Paused when Dervo quit. Its last step may not have finished.'
DECLINED = 'You chose not to run it. Nothing was sent.'
SUMMARY_OF = 'a summary of the last {n} messages'  # what a fresh session gets instead of the chat (handoff map H2)
KINDS = {'working': 'running', 'ready_for_review': 'review', 'needs_permission': 'permission', 'blocked': 'stopped',
         'disconnected': 'unconfirmed', 'answered': 'answered', 'completed': 'finished', 'idle': 'approved'}
NEXT = {'review': 'Your review, then commit.', 'permission': 'Your permission, then it starts.',
        'stopped': 'Resume it, or look at what it left.', 'unconfirmed': 'Look at what it left, then resume it.'}
DONE = ('answered', 'finished', 'approved')
STOPPED = ('stopped', 'unconfirmed')
# Blocked on you: an agent that asked something, or one that can't start without your say. Rust on Home marks the first.
ASKING = ('permission',)
PROVIDER = {'claude': 'Claude', 'codex': 'Codex'}
LIMITS = {'usage_limit': 'usage limit', 'rate_limit': 'rate limit'}
WORDS = {2: 'Two', 3: 'Three'}


def clip(text, limit):
    if not isinstance(text, str):
        return None
    # Asked, did and next lines quote people and agents; they reach the screen in house style (voice.plain).
    text = house_style(text)
    text = ' '.join(''.join(c if c.isprintable() else ' ' for c in text).split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if text[limit - 1] != ' ' and ' ' in cut[limit // 2:]:  # end on a whole word unless the last word is most of the line
        cut = cut.rsplit(' ', 1)[0]
    return cut.rstrip(' ,.;:') + '…'


def plain(text):
    """Text with its Markdown taken out: fences and backticks, emphasis, headings, links, quote and list markers. What
    you typed in a composer, or what an agent reported, read as prose. Identifiers like project_ideas.py keep their
    underscores."""
    if not isinstance(text, str):
        return None
    text = re.sub(r'```[^\n]*', '', text)
    text = re.sub(r'`([^`\n]*)`', r'\1', text)
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'^[ \t]{0,3}(?:#{1,6}[ \t]+|>[ \t]?|[-*+][ \t]+|\d+[.)][ \t]+)', '', text, flags=re.M)
    text = re.sub(r'(\*\*|__)(?=\S)(.+?)(?<=\S)\1', r'\2', text)
    text = re.sub(r'(?<![\w*])[*_](?=\S)([^*_\n]+?)(?<=\S)[*_](?![\w*])', r'\1', text)
    return text


def short(name, limit):
    """A thread's name cut at a word, for the middle of a sentence."""
    name = clip(name, 400) or ''
    if len(name) <= limit:
        return name
    cut = name[:limit].rsplit(' ', 1)[0].rstrip(' ,.;:…')
    return (cut or name[:limit]) + '…'


def sentence(text):
    """Text that ends like a sentence, so three lines read as three sentences."""
    return text if not text or text[-1] in '.?!…' else text + '.'


def first_sentence(text, limit=LINE_LIMIT):
    text = clip(text, 2000)
    if not text:
        return None
    found = re.match(r'(.+?[.?!])(\s|$)', text)
    return clip(found.group(1) if found else text, limit)


# Before #24 agents ended a reply with four headings. Answers saved then still carry them, and the Answered line read
# "Changed Saved a project memory note." Read those replies by their sections instead.
REPORT_HEADINGS = ('changed', 'checked', 'not checked', 'next')
HEADING_LINE = re.compile(r'^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*|__)?(changed|checked|not checked|next)(?:\*\*|__)?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*(.*)$',
                          re.I)
EMPTY_SECTION = re.compile(r'^(?:[-*+][ \t]+)?(?:nothing|none|n/?a|no changes?|no files? changed|nothing changed)\b[\s.,;:!-]*$', re.I)


def answer_prose(text):
    """An answer as prose to quote. A heading-style reply gives the first line of its first section that says
    something, Changed before Checked before Next ("Nothing." doesn't count, and Not checked is never quoted). Prose
    comes back unchanged."""
    if not isinstance(text, str):
        return text
    sections, current, headed = {}, None, False
    for line in text.splitlines():
        found = HEADING_LINE.match(line)
        # A heading line is the heading alone, or "Changed: text". "Next week we ship" is prose, not a heading.
        if found and (not found.group(2) or re.match(r'^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*|__)?(?:changed|checked|not checked|next)(?:\*\*|__)?[ \t]*:', line, re.I)):
            current, headed = found.group(1).lower(), True
            sections.setdefault(current, [])
            if found.group(2).strip():
                sections[current].append(found.group(2).strip())
            continue
        if current and line.strip():
            sections[current].append(line.strip())
        elif current is None and line.strip():
            return text  # prose before any heading: the reply isn't in the old form
    if not headed:
        return text
    for name in ('changed', 'checked', 'next'):
        for line in sections.get(name, []):
            said = plain(line).strip()
            if said and not EMPTY_SECTION.match(said):
                return sentence(said)
    return ''


def when(at):
    from project_orchestrator import when as spoken
    return spoken(at)


def clock(at, now=None):
    """"11:20 PM" for a moment within the last day, so read in the morning it means last night; otherwise the spoken
    day and time."""
    now = now or time.time()
    if 0 <= now - at < DAY:
        return time.strftime('%I:%M %p', time.localtime(at)).lstrip('0')
    return when(at)


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


def counted_files(files):
    return f"{plural(len(files), 'file')} changed." if files else None


def checks(exit_status):
    """What the host's check said, or that none ran. A record without a check result is a record of no check."""
    if exit_status == 0:
        return 'Checks passed.'
    if isinstance(exit_status, int) and not isinstance(exit_status, bool):
        return 'A check failed.'
    return 'No checks ran.'


def files_left(error):
    """How many files an interrupted run had changed, from the sentence native_evidence writes into its error."""
    found = re.search(r'It had changed (.*?)\.(?=\s+[A-Z]|$)', error)  # file names have dots; the sentence ends before the next one
    if not found:
        return None
    names = found.group(1)
    more = re.search(r' and (\d+) more$', names)
    count = len([n for n in re.sub(r' and \d+ more$', '', names).split(', ') if n]) + (int(more.group(1)) if more else 0)
    return f"{plural(count, 'file')} changed, not committed."


def left_work(run, error, attempts):
    """Whether a stopped run left work in the folder: files a full-access run left changed (its error says so,
    native_evidence.unfinished_note, or it recorded its own edits) or file attempts this run recorded."""
    return bool(files_left(error) or run.get('written') or (attempts is not None and attempts != run.get('prior_attempts', 0)))


def hand_off_ready(task, run, error, attempts, repo=False):
    """Whether "Continue in Codex" would go through, read from the records the way conversation_coding.retry_on reads
    them: it refuses a run that failed anywhere but the provider's side, or that's still settling
    (coding_integration.retry_status). ``attempts`` is how many file attempts the task's evidence holds, or None when it
    has no evidence record, which retry_on refuses as an unknown task. A run that left work in the folder goes through
    too since 23 Sep (the handoff that carries the work), but only where Dervo can snapshot the folder first: ``repo``
    says the folder is in a git repository. Anywhere else it's refused, as it was, and the row asks for a review."""
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    if task.get('state') not in ('blocked', 'ready_for_review') or not runs:
        return False
    suggest = task.get('suggest') if isinstance(task.get('suggest'), dict) else {}
    # Since 26 Sep (behaviour review N1) your own Stop offers it too, as conversation_coding.handoff_target reads it.
    from conversation_activity import stopped_by_user
    stopped = suggest.get('kind') == 'stopped' and stopped_by_user(task)
    if not stopped and (run.get('state') != 'failed' or not run.get('provider_failure')):
        return False
    if attempts is None:
        return False
    if any(r.get('state') == 'uncertain' for r in runs):
        return False
    tokens = [t for t in task.get('tokens') or [] if isinstance(t, dict)]
    if not all(t.get('state') in ('delivered', 'cancelled') and (not t.get('job_id') or t.get('job_done')) for t in tokens):
        return False
    return bool(repo) or not left_work(run, error, attempts)


def stop_reason(task, run, error, user_stop):
    """Why a run stopped, from the run's recorded provider failure or the task's error, as one short sentence."""
    if user_stop:
        return YOU_STOPPED
    suggest = task.get('suggest') if isinstance(task.get('suggest'), dict) else {}
    kind = suggest.get('kind') or run.get('provider_failure')
    who = PROVIDER.get((task.get('binding') or {}).get('provider') if isinstance(task.get('binding'), dict) else None)
    if kind in LIMITS and who:
        # Keep the provider's own reset when it gave one ("You've hit your session limit · resets 3pm (Australia/
        # Brisbane)"), so the one fact the raw text used to carry isn't lost (product review, 23 Sep, 3).
        found = re.search(r'\bresets\s+([^·\n]{1,60})', error or '') if isinstance(error, str) else None
        when = found.group(1).strip().rstrip('.') if found else ''
        return f"Stopped at {who}’s {LIMITS[kind]}. Resets {when}." if when else f"Stopped at {who}’s {LIMITS[kind]}."
    reason = first_sentence(plain(suggest.get('reason'))) or first_sentence(plain(error))
    return f'Stopped. {reason}' if reason else 'Stopped. No reason was recorded.'


RESULT_LIMIT = 200  # the row's one sentence: the guide's goal, which the agent writes as what you'll have
FILES_SHOWN = 8  # changed files the peek and the panel name; the rest are counted
WRITES = ('dervo_write_file', 'dervo_edit_file', 'dervo_create_file', 'native:Edit', 'native:Write')


def result_facts(task, kind, run, guide, completion, exit_status):
    """What a row says beyond its status (16.2, Home #30), from records only: ``result`` is the task guide's goal from
    the current run, which the agent writes as the outcome you'll have, so there's no extra model call; ``files`` and
    ``files_count`` are the changed files Dervo recorded (a running run's count is what it has written so far, with no
    names); ``checks_line`` is what Dervo's own check said; ``unchecked`` is the guide's own "not checked" once the run
    has ended. Each is None or empty when nothing records it."""
    result = clip(plain(guide.get('goal')), RESULT_LIMIT)
    files = []
    if kind == 'running':
        tokens = [t for t in task.get('tokens') or [] if isinstance(t, dict) and t.get('run_id') == run.get('id')]
        editing = list(dict.fromkeys([p for p in run.get('written') or [] if isinstance(p, str)]
                                     + [t['path'] for t in tokens if isinstance(t.get('path'), str) and t.get('tool') in WRITES]))
        return dict(result=result, files=[], files_count=len(editing), checks_line=None, unchecked=None)
    if kind in ('review', 'finished', 'answered'):
        files = [f for f in completion.get('changed_files') or [] if isinstance(f, str)]
    elif kind == 'approved':
        commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
        files = [f for f in commit.get('files') or completion.get('changed_files') or [] if isinstance(f, str)]
    elif kind == 'unconfirmed':
        after = run.get('after_close') if isinstance(run.get('after_close'), dict) else {}
        files = [f for f in after.get('changed') or [] if isinstance(f, str) and f]
    elif kind == 'stopped':
        # What a stopped run wrote is kept as the paths it touched, which can be absolute: counted, not named.
        written = list(dict.fromkeys(p for p in run.get('written') or [] if isinstance(p, str)))
        return dict(result=result, files=[], files_count=len(written), checks_line=None,
                    unchecked=clip(plain(guide.get('limits')), LINE_LIMIT))
    files = list(dict.fromkeys(files))
    return dict(result=result, files=files[:FILES_SHOWN], files_count=len(files),
                checks_line=checks(exit_status) if kind in ('review', 'finished') else None,
                unchecked=clip(plain(guide.get('limits')), LINE_LIMIT) if kind != 'permission' else None)


def thread_facts(thread, task, activity, seen, attempts=None, repo=False):
    """One thread's pickup from its newest task (None when it has none), its activity index entry and when you last
    looked. Every field is read from a record or is Dervo's own fixed wording for a state. ``line`` is the one plain
    status line a compact row shows; ``did`` is the fuller Did fact and the thread pickup's middle line. ``attempts`` is
    the task's recorded file attempts (hand_off_ready); without it a stopped run offers no handoff. ``repo`` says the
    task's folder is in a git repository, so a run that left work can be carried on after a snapshot. ``left_changes``
    is true when a stopped run left uncommitted file changes: with no handoff on offer, the row asks you to review them."""
    activity = activity or {}
    moved = activity.get('at')
    item = dict(id=thread['id'], name=thread['name'], private=thread.get('visibility') == 'private', seen=seen, moved=moved,
                kind='new', status=STATUS['new'], needs=False, asked=None, line=None, did=None, now=None, next=None, next_by=None,
                running_since=None, changed=bool(moved and (seen is None or moved > seen)), question=False, failed=False, ending=None,
                checks=None, handoff=None, left_changes=False, account=None, continuity=None, note=None,
                task_id=None, result=None, files=[], files_count=0, checks_line=None, unchecked=None)
    if task is None:
        # A run with no coding task behind it yet (a warm start, a turn): the index says it's running, and so does
        # thread_states, so the pickup says so too rather than "new" (design review, 8 Oct).
        if activity.get('lost'):
            item.update(kind='unconfirmed', status=STATUS['unconfirmed'])
        elif activity.get('running'):
            now = clip(plain(activity.get('step')), LINE_LIMIT) or 'Working'
            item.update(kind='running', status=STATUS['running'], now=now, running_since=activity.get('since'), line=sentence(now),
                        did=f'Still working: {sentence(now)}')
        return item
    state = task.get('state')
    # A run Dervo died with still says working in its record until the coding runtime reconciles it; the index knows.
    lost = bool(activity.get('lost'))
    kind = 'unconfirmed' if lost else 'running' if activity.get('running') or state == 'working' else KINDS.get(state, 'finished')
    runs = [run for run in task.get('runs') or [] if isinstance(run, dict)]
    run = runs[-1] if runs else {}
    guide = run.get('guide') if isinstance(run.get('guide'), dict) and run['guide'].get('run_id') == run.get('id') else {}
    messages = [m for m in task.get('messages') or [] if isinstance(m, dict)]
    from chat_links import your_words  # a job Home routed: your words, never Dervo's instructions to the agent
    asked = your_words(next((m.get('text') for m in reversed(messages) if m.get('role') == 'user'), None))
    answer = next((m.get('text') for m in reversed(messages) if m.get('role') == 'assistant'), None)
    error = task.get('error') if isinstance(task.get('error'), str) else ''
    step = clip(plain(guide.get('step')), LINE_LIMIT)
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    files = [f for f in completion.get('changed_files') or [] if isinstance(f, str)]
    exit_status = completion.get('test_exit_status')
    commit = (task.get('commit') or {}).get('sha') if isinstance(task.get('commit'), dict) else None
    item.update(kind=kind, status=STATUS[kind], needs=activity.get('status') == 'needs', asked=clip(plain(asked), ASKED_LIMIT),
                continuity=continuity_line(task), note=note_line(task, kind),
                task_id=task.get('id') if isinstance(task.get('id'), str) else None,
                **result_facts(task, kind, run, guide, completion, exit_status))
    if kind == 'running':
        now = clip(plain(activity.get('step')), LINE_LIMIT) or step or 'Working'
        item.update(now=now, running_since=activity.get('since'), line=sentence(now), did=f'Still working: {sentence(now)}')
    elif kind in ('review', 'finished'):
        item['checks'] = 'passed' if exit_status == 0 else 'failed' if isinstance(exit_status, int) and not isinstance(exit_status, bool) else None
        item['line'] = item['did'] = ' '.join(filter(None, ['Finished.', counted_files(files), checks(exit_status)]))
    elif kind == 'stopped':
        from conversation_activity import stopped_by_user
        user_stop = stopped_by_user(task)
        suggest = task.get('suggest') if isinstance(task.get('suggest'), dict) else {}
        other = suggest.get('other') if isinstance(suggest.get('other'), dict) else None
        # Offered only where the handoff will go through: retry_on refuses a run still settling, and one that left changes
        # in a folder Dervo can't snapshot.
        if other and suggest.get('taken') is None and hand_off_ready(task, run, error, attempts, repo):
            item['handoff'] = PROVIDER.get(other.get('provider')) or clip(other.get('label'), 20)
        item['left_changes'] = bool(files_left(error))
        # Beta.17.1 (run_ending): only work that failed is a failure. A limit, a run that never started and a finished
        # run whose later Dervo step didn't complete say so, with no X.
        import run_ending
        ending = run_ending.classify(task) or {}
        item['ending'] = 'stopped' if user_stop else ending.get('kind')
        item['failed'] = not user_stop and ending.get('kind') in (None, 'failed')
        if ending.get('kind') == 'stopped' and (ending.get('by') == 'restart' or ending.get('left')):
            item['paused_by'] = 'restart' if ending.get('by') == 'restart' else 'left'
            item['line'] = ' '.join(filter(None, [RESTART_PAUSED if ending.get('by') == 'restart' else LEFT_STOPPED, files_left(error)]))
        elif ending.get('kind') == 'not_started' and not user_stop:
            item['line'] = DECLINED if ending.get('declined') else 'Didn’t start. Nothing was sent.'
        elif ending.get('kind') == 'finished' and not user_stop:
            item['status'] = 'finished, but a Dervo step didn’t complete'
            reason = first_sentence(plain(error))
            item['line'] = ' '.join(filter(None, ['Finished.', reason]))
        else:
            item['line'] = ' '.join(filter(None, [stop_reason(task, run, error, user_stop), files_left(error)]))
        item['did'] = ' '.join(filter(None, [item['line'], f'It was on: {sentence(step)}' if step else None]))
    elif kind == 'unconfirmed':
        restarted = lost or error.startswith('Host restarted')
        reason = None if restarted else first_sentence(plain(error))
        # Lost track isn't failed (beta.17.1): the row shows unknown, with what was last seen.
        item.update(failed=False, ending='unknown', line=RESTARTED if restarted else 'Stopped. Dervo couldn’t confirm how far it got.')
        import run_ending
        if (run_ending.classify(task) or {}).get('by') == 'quit':
            # Beta.18: Dervo paused it when it quit. The thread's card says Paused, so the strip does too (it said Not
            # confirmed up to 17.1). Its last step is still unconfirmed, and the line says so.
            item.update(ending='stopped', line=QUIT_PAUSED)
            reason = None
        item['did'] = ' '.join(filter(None, [item['line'], f'The reason: {reason}' if reason else None,
                                             f'It was on: {sentence(step)}' if step else None]))
        after = run.get('after_close') if isinstance(run.get('after_close'), dict) else {}
        changed = after.get('changed')
        if isinstance(changed, list) and all(isinstance(path, str) for path in changed):
            # The relaunch read the folder (coding_integration.changes_after_close): the outcome isn't unconfirmed, it's
            # in these files. Night 1, finding 2: the real CLI finished its change after the host died.
            names = [path for path in changed if path]
            item['line'] = (f"{CLOSED_ON} It kept working: {plural(len(names), 'file')} changed, not committed." if names
                            else f'{CLOSED_ON} Nothing in the folder changed.')
            from native_evidence import named
            item['did'] = ' '.join(filter(None, [item['line'], f'Review {named(names)} before you carry on.' if names else None,
                                                 f'It was on: {sentence(step)}' if step else None]))
        record = after.get('record') if isinstance(after.get('record'), dict) else {}
        if record.get('state') == 'unsure':
            # Beta.18 (lost_runs): the provider's record shows one outside step with no end. The card names it and offers
            # Continue, checking first, so the strip says the same instead of "It kept working".
            unsure = record.get('step') if isinstance(record.get('step'), dict) else {}
            item['line'] = (f"{CLOSED_ON} One step may not have finished: {unsure['text']}." if unsure.get('text')
                            else f'{CLOSED_ON} One step may not have finished.')
            item['did'] = ' '.join(filter(None, [item['line'], 'Check it before you run it again.']))
    elif kind == 'permission':
        item['line'] = item['did'] = 'Waiting for your permission to start.'
    elif kind == 'answered':
        said = first_sentence(plain(answer_prose(answer)), 120)
        item['line'] = item['did'] = f'Answered: {said}' if said else 'Answered in text.'
    elif kind == 'approved':
        item['line'] = item['did'] = 'Approved and committed.' if commit else 'Approved.'
    # While it runs, the agent's next step is its plan. Once the run has ended it counts only if nothing ran and you
    # said nothing after it was written (conversation_activity.fresh_next); otherwise Dervo's own next step for the
    # state, or none for a done one (handoff review, 19 Sep, #5).
    from conversation_activity import fresh_next
    reported = clip(plain(guide.get('next') if kind == 'running' else fresh_next(task)), LINE_LIMIT)
    if reported and kind != 'approved':
        item.update(next=sentence(reported), next_by='agent')
    elif kind in NEXT:
        item.update(next=NEXT[kind], next_by='dervo')
    # An answer that ends on a real question for you (conversation_activity.asks_you): that question is what's next.
    # A run that changed files and then asked ("Should the greeting be Hi or Hello?") is still a review, but the
    # question is what it's blocked on, so it's the Next and the review line follows it (hard case s45, 21 Sep).
    from conversation_activity import asks_you, closing_question
    ended_asking = kind in ('answered', 'finished') and item['needs'] and asks_you(task)
    if ended_asking:
        item.update(next=sentence(clip(plain(closing_question(answer)), LINE_LIMIT)), next_by='agent')
    elif kind == 'review' and item['needs'] and item['next_by'] == 'dervo' and closing_question(answer):
        ended_asking = True
        item.update(next=sentence(clip(plain(closing_question(answer)), LINE_LIMIT)), next_by='agent')
    # The agent's reported next step is a question for you: it stopped to ask, and Home's rust marks that.
    item['question'] = bool(ended_asking or (item['needs'] and item['next_by'] == 'agent' and item['next'].endswith('?')))
    if activity.get('pending_question') and item['needs']:
        item.update(next=sentence(clip(plain(activity['pending_question']), LINE_LIMIT)), next_by='agent', question=True)
    return item


def summary_size():
    """How many exchanges a fresh session is given, from the coding module that builds it."""
    try:
        from conversation_coding import TRANSCRIPT_TASKS
        return TRANSCRIPT_TASKS
    except Exception:
        return 6


def continuity_line(task):
    """What the newest message's session was given, when it wasn't the whole chat: one calm line from the decision
    recorded on the task (session_continuity) or the fallback Claude Code made at launch. None while a message carries
    on its session, and for the first message of a chat. Handoff map, 19 Sep, section 5."""
    continuity = task.get('continuity') if isinstance(task.get('continuity'), dict) else {}
    reason = continuity.get('reason') if isinstance(continuity.get('reason'), str) else ''
    if continuity.get('state') == 'continues' and continuity.get('parent'):
        return 'Claude couldn’t reopen the last session, so it carried on from the one before and was told the last message.'
    if continuity.get('state') != 'fresh' or not reason:
        return None
    size = continuity.get('summary_size') if isinstance(continuity.get('summary_size'), int) else summary_size()
    summary = 'a summary of the last message' if size == 1 else SUMMARY_OF.format(n=size)
    if reason == 'You chose to start fresh.':
        return f'You started fresh, so it got {summary}.'
    if reason.startswith('The earlier Claude Code session'):
        return f'Claude couldn’t reopen the earlier session, so it got {summary} instead.'
    if reason.startswith('The last run') or reason.startswith('The last message'):
        return f'The last run didn’t end cleanly, so this one started with {summary}.'
    if reason.startswith('Codex gets'):
        return f'Codex started fresh with {summary}.'
    if reason.startswith('Moved from'):
        return f"{reason.split('.')[0]}, so Claude started fresh with {summary}."
    if reason.startswith('Switched to another account'):
        return f'Switched account, so it started fresh with {summary}.'
    return f'{sentence(clip(reason, 120))} It started fresh with {summary}.'


def note_line(task, kind):
    """A message you sent into the run that the run ended before reading: it hasn't been lost, it goes with the next
    message (session_continuity.since_note carries it into a continued session; the fresh-session summary lists the
    run's mid-run messages). Handoff map, 19 Sep, H5."""
    if kind in ('running', 'permission'):
        return None
    unread = [m for m in task.get('messages') or [] if isinstance(m, dict) and m.get('role') == 'user' and m.get('steer') == 'sent']
    runs = [r for r in task.get('runs') or [] if isinstance(r, dict)]
    requeued = runs[-1].get('requeued') if runs else None
    who = PROVIDER.get((task.get('binding') or {}).get('provider') if isinstance(task.get('binding'), dict) else None) or 'It'
    if unread:
        return f'{who} stopped before reading your note. It goes with your next message.'
    if isinstance(requeued, int) and requeued > 0:
        # coding_integration moved the unread note to the queue when the run ended on its own (phase 2, item 7).
        return f'{who} stopped before reading your note, so Dervo sent it as the next message.'
    return None


def asking(item):
    """Blocked on you: it asked a question, or it can't start without your permission. Home's one accent."""
    return item['needs'] and (item['kind'] in ASKING or item['question'])


def attention(item):
    """What's first on Home: blocked on you, or stopped short."""
    return asking(item) or (item['needs'] and item['kind'] in STOPPED)


def quoted(text):
    """Your own words, quoted, so they read as yours in the middle of a sentence."""
    end = '' if text[-1] in '?!…' else '.'
    return f'“{text.rstrip(".")}”{end}'


def lines(item):
    """The three-line pickup at the top of a thread."""
    if item['kind'] == 'new':
        return []
    return [line for line in (f"Last time: you asked {quoted(item['asked'])}" if item['asked'] else None,
                              item['did'], f"Next: {item['next']}" if item['next'] else None, item['continuity'], item['note']) if line]


def project_threads(db, scope, actor):
    """This project's threads you can see: shared ones and your own private ones, archived ones left out. Whatever
    account each is on: a thread you moved to Codex, or one left on the old account after the project's default
    changed, is still your work here (handoff map, 19 Sep, H1). Only accounts on the scope's own side, Work or
    Personal: a work account's scope reads no personal thread, nor the other way round."""
    archived = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_archives'").fetchone()
    return [dict(row) for row in db.execute(
        'SELECT t.id AS id, c.name AS name, t.visibility AS visibility, t.profile_id AS profile_id, p.provider AS provider '
        'FROM threads t JOIN local_channels c ON c.id=t.id JOIN profiles p ON p.id=t.profile_id '
        "WHERE t.project_id=? AND p.category IN ('personal','work') AND (t.visibility='shared' OR t.owner=?) "
        # A work account's scope gets no personal threads, nor the other way round (security review M3), and a thread on
        # an account from the other side of its project is never read.
        "AND (SELECT category FROM profiles WHERE id=?)=p.category AND " + ON_PROJECT_SIDE + " "
        + ('AND t.id NOT IN (SELECT thread_id FROM conversation_archives) ' if archived else '') + 'ORDER BY t.rowid',
        (scope['project_id'], actor, scope.get('profile_id')))]


def account_word(thread, scope):
    """The account a thread is on when it isn't the project's: the other provider by name, or another account of the
    same one. None on the project's own account."""
    if thread.get('profile_id') in (None, scope['profile_id']):
        return None
    provider = PROVIDER.get(thread.get('provider'), 'another account')
    return provider if thread.get('provider') != scope.get('provider') else f'another {provider} account'


def gather(db, threads):
    """Facts for each thread, from one pass over the tasks and one read of the activity index."""
    import chat_links
    import conversation_activity
    conversation_activity.schema(db)
    ids = [thread['id'] for thread in threads]
    tasks = chat_links.latest_tasks(db, ids)
    activity = conversation_activity.summaries(db, ids)
    marks = ','.join('?' * len(ids))
    seen = dict(db.execute(f'SELECT thread_id, seen FROM conversation_seen WHERE thread_id IN ({marks})', ids).fetchall()) if ids else {}
    offering = [task for task in tasks.values() if offers_other(task)]
    attempts = evidence_attempts(db, offering)
    repos = in_repo(task for task in offering if left_work(last_run(task), task.get('error') if isinstance(task.get('error'), str) else '',
                                                            attempts.get(task.get('id'))))
    return [thread_facts(thread, tasks.get(thread['id']), activity.get(thread['id']), seen.get(thread['id']),
                         attempts.get((tasks.get(thread['id']) or {}).get('id')),
                         repos.get(((tasks.get(thread['id']) or {}).get('binding') or {}).get('cwd'), False)) for thread in threads]


def last_run(task):
    runs = [run for run in task.get('runs') or [] if isinstance(run, dict)]
    return runs[-1] if runs else {}


def in_repo(tasks):
    """{folder: whether it's in a git repository} for these tasks' folders. Git runs once per folder, and only for a
    stopped run that left work and could be carried on, so a plain Home read runs none."""
    import task_git
    found = {}
    for task in tasks:
        cwd = (task.get('binding') or {}).get('cwd') if isinstance(task.get('binding'), dict) else None
        if isinstance(cwd, str) and cwd and cwd not in found:
            found[cwd] = task_git.repo_root(cwd) is not None
    return found


def offers_other(task):
    """A task whose provider failure named another account nobody has taken yet: the only kind whose evidence is read."""
    suggest = task.get('suggest') if isinstance(task, dict) and isinstance(task.get('suggest'), dict) else {}
    return isinstance(suggest.get('other'), dict) and suggest.get('taken') is None


def evidence_attempts(db, tasks):
    """{task id: how many file attempts its evidence record holds}, for the few tasks that might offer a handoff. A task
    with no evidence record is left out, which reads as no handoff."""
    ids = [task['id'] for task in tasks if isinstance(task.get('id'), str)]
    if not ids or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_evidence'").fetchone():
        return {}
    marks = ','.join('?' * len(ids))
    found = {}
    for task_id, document in db.execute(f'SELECT id, document FROM task_evidence WHERE id IN ({marks})', ids):
        try:
            attempts = json.loads(document).get('attempts')
        except (ValueError, AttributeError):
            continue
        if isinstance(attempts, list):
            found[task_id] = len(attempts)
    return found


def project(db, scope, actor, now=None):
    """Home's pickup: a lead sentence, the threads that matter in order, the counts its caption reads, and how many more
    are quiet. Order: blocked on you or stopped, then running, then finished and waiting for your review, then what you
    were most recently in."""
    import conversation_activity
    now = now or time.time()
    since = conversation_activity.last_session_end(db)
    threads = project_threads(db, scope, actor)
    facts = [item for item in gather(db, threads) if item['kind'] != 'new']
    words = {thread['id']: account_word(thread, scope) for thread in threads}
    for item in facts:
        if words.get(item['id']):
            # "On Codex." on the row, so a thread you moved is still here and says where it went.
            item['account'] = words[item['id']]
            item['line'] = ' '.join(filter(None, [f"On {item['account']}.", item['line']]))
            item['did'] = ' '.join(filter(None, [f"On {item['account']}.", item['did']]))
    recent = lambda item: max(item['seen'] or 0, item['moved'] or 0)
    # Blocked on you before stopped short: an agent waiting on your answer is where you're needed most.
    first = sorted((item for item in facts if attention(item)), key=lambda item: (0 if asking(item) else 1, -(item['moved'] or 0)))
    running = sorted((item for item in facts if item['kind'] == 'running' and not attention(item)),
                     key=lambda item: -(item['running_since'] or item['moved'] or 0))
    review = sorted((item for item in facts if item['needs'] and item not in first and item not in running), key=lambda item: -(item['moved'] or 0))
    rest = sorted((item for item in facts if item not in first and item not in running and item not in review), key=lambda item: -recent(item))
    active = [item for item in rest if now - recent(item) < QUIET_DAYS * DAY]
    ordered = first + running + review + active
    # Everything blocked on you or still running is shown, however many; the cap only folds the rest (orchestrator map F12).
    shown = max(SHOWN, len(first) + len(running))
    middle = max((item for item in facts if item['seen'] and item['kind'] != 'approved'), key=lambda item: item['seen'], default=None)
    moved = [item for item in facts if since and item['kind'] != 'running' and item['moved'] and item['moved'] > since]
    counts = dict(threads=len(facts), running=len(running), moved=len(moved), finished=sum(1 for item in moved if item['kind'] in DONE + ('review',)),
                  stopped=sum(1 for item in moved if item['kind'] in STOPPED), asking=sum(1 for item in facts if asking(item)))
    return dict(since=since, lead=lead(facts, ordered, since, middle, now), counts=counts,
                middle=middle and dict(id=middle['id'], name=middle['name'], next=middle['next']),
                items=ordered[:shown], more=len(facts) - len(ordered[:shown]), at=now)


def told(item):
    """What one thread did since you left, as the rest of a sentence that starts with its name."""
    kind = item['kind']
    if kind == 'permission':
        return 'is waiting for your permission.'
    if item['question']:
        return 'has a question for you.'
    if kind in ('review', 'finished'):
        end = {'passed': ' and passed its checks.', 'failed': ', but a check failed.'}.get(item['checks'], ' and needs your review.' if kind == 'review' else '.')
        return f'finished{end}'
    if kind == 'unconfirmed':
        if item.get('ending') == 'stopped':
            return 'was paused when Dervo quit.'
        if item['line'] and item['line'].startswith(CLOSED_ON):
            return 'stopped when Dervo closed.' + item['line'][len(CLOSED_ON):]
        return 'stopped, and Dervo couldn’t confirm how far it got.'
    if kind == 'stopped':
        if item.get('paused_by') == 'restart':
            return 'was paused when Dervo restarted.'
        if item.get('paused_by') == 'left':
            return 'was left stopped.'
        if item.get('ending') == 'stopped':
            return 'was stopped by you.'
        if item.get('ending') == 'not_started':
            return 'didn’t start. Nothing was sent.'
        if item.get('ending') == 'finished':
            rest = item['line'][len('Finished. '):] if item['line'].startswith('Finished. ') else ''
            return f'finished, but {rest}' if rest else 'finished, but a Dervo step didn’t complete.'
        head = re.sub(r'\s*\d+ files? changed, not committed\.$', '', item['line'])
        return 'stopped: ' + head[len('Stopped. '):] if head.startswith('Stopped. ') else 's' + head[1:]
    if kind == 'approved':
        return 'was approved and committed.' if item['line'].endswith('committed.') else 'was approved.'
    return 'answered.'


def lead(facts, ordered, since, middle, now=None):
    """What moved since you were last here, each thread by name in bold, then what's running and what you were in the
    middle of. "Nothing moved" is said too. From records only."""
    moved = [item for item in ordered if item['kind'] != 'running' and item['moved'] and (since is None or item['moved'] > since)]
    if since is None:  # the first session: nothing to compare with, so only what's blocked on you is named
        moved = [item for item in ordered if attention(item)]
    named = moved[:NAMED_IN_LEAD]
    parts = [f"**{short(item['name'], NAME_IN_LEAD)}** {told(item)}" for item in named]
    rest = moved[NAMED_IN_LEAD:]
    if rest:
        groups = [(n, verb) for n, verb in ((sum(1 for item in rest if item['kind'] in DONE + ('review',)), 'finished'),
                                            (sum(1 for item in rest if item['kind'] in STOPPED), 'stopped'),
                                            (sum(1 for item in rest if item['kind'] == 'permission'), 'waiting for your permission')) if n]
        parts.append(f'{len(rest)} more {groups[0][1]}.' if len(groups) == 1 else
                     f"{plural(len(rest), 'more thread')} moved: {', '.join(f'{n} {verb}' for n, verb in groups)}.")
    running = [item for item in facts if item['kind'] == 'running']
    if running:
        count = 'One run is' if len(running) == 1 else f"{WORDS.get(len(running), str(len(running)))} runs are"
        # A finished run waiting for your review is Home's "Waiting on you" too (design review, 8 Oct).
        parts.append(f'{count} working and nothing needs you yet.' if not moved and not any(attention(item) or item['kind'] == 'review' for item in facts)
                     else f'{count} still working.')
    if not parts:
        parts.append(f'Nothing moved since {clock(since, now)}.' if since else
                     'Here’s where your threads stand.' if facts else 'Nothing here yet. Start a thread, or ask below.')
    # Only work that isn't finished is something you're in the middle of (first-run review, 9 Oct: on a fresh install the
    # lead said it of the thread whose one answer you'd just read). The arrival summary still gets it as where you were.
    if middle and middle not in named and middle['kind'] not in DONE:
        parts.append(f"You were in the middle of **{short(middle['name'], NAME_IN_LEAD)}**.")
    return ' '.join(parts)


def conversation(db, store, project_id, thread_id, actor):
    """The pickup at the top of one thread. ``seen`` is your visit before this one, so opening the thread doesn't erase
    when you were last in it."""
    import conversation_activity
    thread = store._access(thread_id, project_id, actor)
    name = db.execute('SELECT name FROM local_channels WHERE id=?', (thread_id,)).fetchone()
    conversation_activity.schema(db)
    previous = conversation_activity.previous_visit(db, thread_id)
    item = gather(db, [dict(id=thread_id, name=name[0] if name else '', visibility=thread['visibility'])])[0]
    item.update(seen=previous, changed=bool(item['moved'] and (previous is None or item['moved'] > previous)))
    return dict(item, lines=lines(item), work=work_lines(store, project_id, thread_id))


def work_lines(store, project_id, thread_id):
    """The bugs and ideas this thread is working on or marked done, from the list's records (project_ideas
    .thread_lines): one line each, with what its Undo needs. Nothing when the list can't be read."""
    try:
        from project_ideas import ProjectIdeas
        return ProjectIdeas(store).thread_lines(project_id, thread_id)
    except Exception as exc:  # noqa: BLE001 - the pickup comes first
        print(f'Bugs and ideas could not be read for the thread pickup: {exc}', file=sys.stderr)
        return []


def dispatch(host, operation, args, actor):
    if not isinstance(args, dict) or set(args) != FIELDS[operation]:
        raise Rejected('Unsupported pickup fields')
    db = host.store.db
    if operation == 'project_pickup':
        scope = project_record_scope(host.store, args['projectId'], args['profileId'])
        found = project(db, scope, actor)
        # While the host holds the Mac awake for open runs (keep_awake.py), the one line Home shows under Running, from
        # here so the wording lives in one place; None when it isn't holding (caffeinate never started, or nothing runs).
        keep = getattr(host, 'keep_awake', None)
        from keep_awake import LINE
        found['awake'] = LINE if keep is not None and keep.holding else None
        return found
    if type(args['channelId']) is not str or not args['channelId']:
        raise Rejected('Choose the exact conversation')
    return conversation(db, host.store, args['projectId'], args['channelId'], actor)
