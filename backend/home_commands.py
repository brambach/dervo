"""What you type into Home that's a click, done as that click (9 Oct 2026, Bryce: "For example approving, all that."
and "Want it super fluid just like Claude projects.").

The router (thread_router, thread_routing) already reads "approve it", "stop the login one" and "archive that thread"
as a thread action for Home, never work for an agent. Until now Home's model could only answer that it was a button.
Here it's the button: the same host operation the button calls, with every check the button makes first, and nothing
more. Home's chat gets a ready reply at once, with no model call, saying what happened, with Undo where the button's
own action has one.

What acts from words, and through what:

- approve: a result waiting for you, Approve on Catch up (task_review approved, which commits as the button does),
  with Catch up's holds (catchup_action and CatchupActions.approve). Or a scope waiting for you (task_approve). Only
  the word approve or accept: "merge it" and "commit it" also read as approve to the router, and a merge isn't this.
- allow: a scope waiting for you ("Approve and start"), task_approve.
- option N, "use option 2", "the second one": the one open question's option, task_question_answer.
- stop, pause, cancel: every working run in the thread, task_stop (Pause).
- archive, restore, pin, unpin: conversation_archive, conversation_restore, conversation_pin.

Never from words: push (outward-facing; it stays its own click, Bryce, 19 Sep), delete (can't be undone) and rename
(Home's model does that with your title). Those go on to Home's model as before.

Which thread: the one you named, or the one your last message went to when you say "it" or "that" (the router's
answer). With neither, the one thread with something of that kind waiting, when there's exactly one. Two or more and
nothing acts: the reply asks which, naming them. While an earlier message is still being answered, a command only acts
on a thread it names, since "it" may mean what that answer is about to start.
"""
import re
import uuid

from dervo_connections import Rejected

# The verbs a typed command may act on (the router's command, then these words in what you wrote).
ACTS = ('approve', 'stop', 'archive', 'restore', 'pin', 'unpin')
APPROVE_WORD = re.compile(r"\b(?:approve|accept)\b", re.I)
REVIEW_OPEN = ('pending', 'initial', 'correction', 'final')  # collaboration.REVIEW_OPEN: the host still drives these
ORDINALS = {'first': 1, '1st': 1, 'second': 2, '2nd': 2, 'third': 3, '3rd': 3, 'one': 1, 'two': 2, 'three': 3}
LEAD = r"^(?:(?:ok(?:ay)?|so|then|yes|yeah|yep|sure|fine|great|alright|and|now|please|pls|just|go ahead and|let'?s)[,!.\s]+)*"
# "for the picker one", "in the login thread": which thread an answer is for. The router reads the name.
WHERE = r"(?:\s+(?:for|in|on|to)\s+(?:the\s+)?(?:[\w.'’-]+\s+){0,5}?(?:one|thread|chat))?"
TAIL = r"(?:\s+(?:please|pls|then|now|for it|for that))?[\s.!]*$"
NUMBER = r"([1-3]|one|two|three)"
# "use option 2", "option two", "go with the second one", "pick 3", "use option 2 for the picker one". A bare number is
# left to the model ("2" can be a count), and so is "the second one" with no verb: Home's own reply may list things too.
VERB = r"(use|pick|choose|take|select|go with|answer)"
OPTION = re.compile(LEAD + r"(?:" + VERB + r"\s+)?(?:option\s*#?\s*" + NUMBER + r"|(?:the\s+)?(first|second|third|1st|2nd|3rd)"
                    r"(?:\s+(option|choice|one))?|#?([1-3]))" + WHERE + TAIL, re.I)
ALLOW = re.compile(LEAD + r"(?:allow|permit|approve (?:the|its|that|this) (?:scope|plan))(?:\s+(?:it|that|this|them|both))?"
                   r"(?:\s+(?:to run|to start))?" + WHERE.replace('(?:for|in|on|to)', '(?:for|in|on)') + TAIL, re.I)
ALLOW_NAMED = re.compile(LEAD + r"(?:allow|permit)\s+(?:the\s+)?(?:[\w.'’-]+\s+){1,5}?(?:one|thread|chat)" + TAIL, re.I)
# "approve", "yes approve", "accept it", "approve the result": approve with no thread, when the router has no command.
APPROVE = re.compile(LEAD + r"(?:approve|accept)(?:\s+(?:it|that|this|the result|the change|the changes|the fix))?" + TAIL, re.I)
# "stop everything", "pause all of them", "stop all runs": every run going, not one thread.
STOP_ALL = re.compile(LEAD + r"(?:stop|pause|halt|cancel)\s+(?:everything|all(?: of them| runs| the runs| agents| threads)?|"
                      r"every (?:run|thread|agent)|both(?: of them)?)" + TAIL, re.I)
NAMES_BY_PRONOUN = re.compile(r"\b(?:it|that|this|them|those|these|both|all)\b", re.I)


def bare(text):
    """What you wrote without "yes," "sure," and the like up front, which the router's command reading doesn't take."""
    said = ' '.join(str(text or '').split())
    return re.sub(LEAD, '', said, flags=re.I) or said


def typed(text):
    """('option', n), ('allow', None), ('approve', None), ('stop_all', None) or None: what the router doesn't read as a
    command (an answer, an allow, a bare approve, a stop of everything)."""
    said = ' '.join(str(text or '').split())
    match = OPTION.match(said)
    if match:
        verb, number, ordinal, noun, bare_number = match.groups()
        word = lambda value: int(value) if value.isdigit() else ORDINALS[value.casefold()]
        if number:
            return ('option', word(number))
        if ordinal and (verb or (noun or '').casefold() in ('option', 'choice')):
            return ('option', ORDINALS[ordinal.casefold()])
        if bare_number and verb:
            return ('option', word(bare_number))
    if ALLOW.match(said) or ALLOW_NAMED.match(said):
        return ('allow', None)
    if APPROVE.match(said):
        return ('approve', None)
    if STOP_ALL.match(said):
        return ('stop_all', None)
    return None


def one_line(text, limit=60):
    said = ' '.join(str(text or '').split())
    return said if len(said) <= limit else said[:limit - 1].rstrip() + '…'


def task_title(task):
    asked = next((message.get('text') for message in task.get('messages') or [] if isinstance(message, dict)
                  and message.get('role') == 'user' and isinstance(message.get('text'), str) and message['text'].strip()), '')
    return one_line(asked.split('\n\n')[0], 60) if asked else None


def result_waiting(task):
    """A result waiting on your Approve, as Catch up's card reads a row (catchupAction): never a helper's, a stale one,
    one whose outcome Dervo lost sight of, one an Independent review is still on, or one you rejected."""
    from task_outcome import outcome_facts
    acceptance = task.get('acceptance') if isinstance(task.get('acceptance'), dict) else {}
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    review = ((task.get('collaboration') or {}).get('review') or {}) if isinstance(task.get('collaboration'), dict) else {}
    facts = outcome_facts(task)
    return (task.get('state') == 'ready_for_review' and task.get('origin') != 'collaboration'
            and acceptance.get('required') is not False and completion.get('stale') is not True
            and not facts['outcome_uncertain'] and facts['human_decision'] != 'rejected'
            and (review.get('phase') if isinstance(review, dict) else None) not in REVIEW_OPEN)


def running(task):
    review = ((task.get('collaboration') or {}).get('review') or {}) if isinstance(task.get('collaboration'), dict) else {}
    return task.get('state') == 'working' or (isinstance(review, dict) and review.get('phase') in REVIEW_OPEN)


class Commands:
    """One typed command, answered in code. ``home`` is the Orchestrator; every action goes through its host."""

    # Threads read for "the one waiting", at most: needs_you and working first. More than this and the reply asks which.
    SCAN = 12

    def __init__(self, home, scope, actor, client_id=None):
        self.home, self.scope, self.actor = home, scope, actor
        # An answer's request id comes from your send's, so a send that runs twice can't answer twice.
        self.client_id = client_id or uuid.uuid4().hex
        self.host = home.host
        self._tasks = {}

    def where(self, thread_id):
        return dict(projectId=self.scope['project_id'], channelId=thread_id)

    def tasks(self, thread_id):
        if thread_id not in self._tasks:
            try:
                found = self.host.dispatch('tasks', self.where(thread_id))
            except Rejected:
                found = []
            self._tasks[thread_id] = [task for task in found or [] if isinstance(task, dict)]
        return self._tasks[thread_id]

    def items(self, thread_id, kind):
        """What's waiting in one thread for one kind of command: [(kind, task, question)]."""
        found = []
        for task in self.tasks(thread_id):
            if kind in ('approve',) and result_waiting(task):
                found.append(('result', task, None))
            if kind in ('approve', 'allow') and task.get('state') == 'needs_permission' and task.get('scope_version') is not None:
                found.append(('scope', task, None))
            if kind == 'option':
                found += [('question', task, question) for question in task.get('questions') or []
                          if isinstance(question, dict) and question.get('canAnswer') and question.get('options')]
            if kind == 'stop' and running(task):
                found.append(('run', task, None))
        return found

    # ---------------------------------------------------------------- answering
    def answer(self, text, rows, decision, ahead, asking=False):
        """dict(text, changes, notes) for a command this answers in code, or None for Home's model to answer as before.
        ``asking``: Home's own last reply asked something or offered jobs, so "option 2" or "allow it" may answer that."""
        own = typed(text)
        command = decision.get('command') if decision else None
        if command in ACTS and own and own[0] in ('approve', 'stop_all'):
            own = None  # the router's reading, with the thread it names
        if own is None and command not in ACTS:
            return None
        if own is None and command == 'approve' and not APPROVE_WORD.search(text):
            return None  # "merge it", "commit that": not Approve
        kind = own[0] if own else command
        # The thread the router read it as about: named, or where your last message went for "it". An answer the router
        # reads as continuing a thread ("use option 2 for the picker one") names that thread too; it acts only if that
        # thread has something of this kind waiting.
        named = decision.get('thread_id') if decision and decision.get('kind') in (('home', 'existing') if own else ('home',)) else None
        self.rows = rows
        live = {row['id']: row for row in rows}
        if (ahead or asking) and (named is None or NAMES_BY_PRONOUN.search(text)):
            # "it" may be what the answer still thinking is about to start, or what Home's last reply offered or asked:
            # only a thread named by name acts now. The rest waits its turn or is Home's model's to read.
            return None
        if kind not in ('archive', 'restore') and named is not None and (live.get(named) or {}).get('archived', True):
            return None  # an archived thread comes back first; Home's model asks
        if kind in ('archive', 'restore', 'pin', 'unpin'):
            return self.thread_action(kind, live.get(named))
        order = {'needs_you': 0, 'working': 1, 'quiet': 2}
        scan = lambda: sorted((row for row in rows if not row.get('archived') and row.get('state') in order),
                              key=lambda row: order[row['state']])[:self.SCAN]
        threads = [live[named]] if named in live and kind != 'stop_all' else scan()
        looking = 'stop' if kind == 'stop_all' else kind
        waiting = [(row, item) for row in threads for item in self.items(row['id'], looking)]
        if not waiting and named in live and decision.get('kind') == 'existing':
            return None  # the router reads it as for that thread, and nothing there is a click: it goes there as before
        if kind in ('stop', 'stop_all'):
            return self.stop(threads[0] if named and threads and kind == 'stop' else None, waiting, every=kind == 'stop_all')
        if not waiting:
            return self.nothing(kind, threads[0] if named and threads else None)
        if len(waiting) > 1:
            return self.which(kind, waiting)
        row, item = waiting[0]
        if named is None and item[0] == 'scope' and (item[1].get('project_access') or item[1].get('folders')):
            # A plan that reaches past its files: approved only with its thread named, never found by a scan.
            example = self.handle(row, 'approve the {} one')
            return self.reply(text=f'{self.name(row)} wants to work beyond its own files. '
                                   + (f'Say "{example}" to start it, or open it to see the plan.' if example else 'Open it to see the plan.'))
        if kind == 'option':
            return self.choose(row, item, own[1])
        return self.approve(row, item)

    def reply(self, changes=(), notes=(), text=None):
        return dict(text=text, changes=list(changes), notes=[note for note in notes if note][:5])

    def change(self, line, thread, *, undo=None):
        return dict(kind='acted', thread=dict(id=thread['id'], name=thread.get('name') or '') if thread else None, idea=None,
                    before=None, revision=None, can_undo=undo is not None, undone=False, line=line,
                    **({'undo': undo} if undo else {}))

    @staticmethod
    def name(row):
        """A thread's name inside a sentence: a name ending "?" or "." would end the sentence twice."""
        return one_line((row or {}).get('name') or 'that thread', 60).rstrip(' .?!…') or 'that thread'

    def nothing(self, kind, row):
        where = f' in {self.name(row)}' if row else ''
        line = {'approve': f'Nothing{where} is waiting for your approval right now.',
                'allow': f'Nothing{where} needs your go-ahead right now.',
                'option': f'No question{where} is waiting for an answer right now.'}[kind]
        return self.reply(text=line)

    def handle(self, row, phrase):
        """The words that name this thread to the router, shortest first, in ``phrase`` ("approve the {} one"), or None
        when no short form of its name does. Checked with the router itself, so the example in a reply works."""
        import thread_router
        words = [word for word in re.split(r"[^\w'’-]+", (row or {}).get('name') or '') if word]
        for size in range(1, min(len(words), 4) + 1):
            for start in range(0, len(words) - size + 1):
                said = phrase.format(' '.join(words[start:start + size]))
                decision = thread_router.route(said, [], self.rows) if thread_router.module() is not None else None
                if decision and decision.get('thread_id') == row['id'] and decision.get('kind') in ('home', 'existing'):
                    return said
        return None

    def which(self, kind, waiting):
        rows = []
        for row, _ in waiting:
            if row['id'] not in {seen['id'] for seen in rows}:
                rows.append(row)
        what = {'approve': 'approve', 'allow': 'allow', 'option': 'answer'}[kind]
        if len(rows) == 1:
            return self.reply(text=f'{self.name(rows[0])} has {len(waiting)} things waiting. Open it to say which one to {what}.')
        names = [self.name(row) for row in rows[:4]]
        listed = ', '.join(names[:-1]) + ' or ' + names[-1]
        phrase = {'approve': 'approve the {} one', 'allow': 'approve the {} one', 'option': 'use option 1 for the {} one'}[kind]
        example = self.handle(rows[0], phrase)
        return self.reply(text=f'Which one should I {what}: {listed}?' + (f' Say it like "{example}".' if example else ' Open the one you mean.'))

    def failed(self, row, exc):
        return self.reply(text=f"I couldn’t do that in {self.name(row)}: {str(exc).rstrip('.')}.")

    # ---------------------------------------------------------------- each action, as its button does it
    def approve(self, row, item):
        kind, task, _ = item
        title = task_title(task)
        about = f' ({title})' if title else ''
        args = dict(self.where(row['id']), taskId=task['id'])
        try:
            if kind == 'scope':
                self.host.dispatch('task_approve', dict(args, scopeVersion=task['scope_version']))
                return self.reply([self.change(f'Approved the plan in {self.name(row)}{about} and started it.', row)])
            # Catch up's Approve (CatchupActions.approve): the latest attempt, re-read, with its holds.
            detail = self.host.dispatch('task_detail', args)
            attempts = ((detail or {}).get('evidence') or {}).get('attempts') or []
            latest = attempts[-1] if attempts else None
            if (detail.get('rebound') or not latest or latest.get('kind') == 'restoration' or (detail.get('retry') or {}).get('available')
                    or (detail.get('task') or {}).get('state') != 'ready_for_review'):
                return self.reply(text=f'The result in {self.name(row)} can’t be approved from Home. Open it to see why.')
            if detail.get('carried'):
                here = (detail['carried'] or {}).get('here') or 'the run before it'
                return self.reply(text=f'The result in {self.name(row)} includes files {here} changed before the handoff. '
                                       'Open it to see them before approving.')
            saved = self.host.dispatch('task_review', dict(args, attemptNumber=latest['number'], decision='approved'))
        except Rejected as exc:
            return self.failed(row, exc)
        commit = (saved or {}).get('commit') or {}
        if commit.get('sha'):
            line = f'Approved and committed the result in {self.name(row)}{about}.'
            return self.reply([self.change(line, row, undo=dict(op='task_undo', args=dict(channelId=row['id'], taskId=task['id']),
                                                                 line=f'Reverted that commit in {self.name(row)}.'))])
        note = commit.get('skipped') or (f"Nothing was committed: {commit['error'].rstrip('.')}." if commit.get('error') else None)
        return self.reply([self.change(f'Approved the result in {self.name(row)}{about}.', row)], [note])

    def choose(self, row, item, number):
        _, task, question = item
        options = question['options']
        if number > len(options):
            listed = '; '.join(f'{n}. {one_line(option.get("label"), 40)}' for n, option in enumerate(options, 1))
            return self.reply(text=f'That question in {self.name(row)} has {len(options)} options: {listed}.')
        option = options[number - 1]
        try:
            self.host.dispatch('task_question_answer', dict(self.where(row['id']), taskId=task['id'], questionId=question['id'],
                                                            clientId='home-' + self.client_id,
                                                            answer=dict(kind='option', optionId=option['id'])))
        except Rejected as exc:
            return self.failed(row, exc)
        return self.reply([self.change(f'Answered {self.name(row)} with option {number}: {one_line(option.get("label"), 60)}.', row)])

    def stop(self, row, waiting, every=False):
        """Pause every run in one thread, or with ``every`` ("stop everything") in every thread that has one."""
        runs = [(thread, task) for thread, (_, task, _) in waiting]
        threads = list({thread['id']: thread for thread, _ in runs}.values())
        # A chat turn or a harness request runs with no task to pause (ConversationStop stops those too): never say
        # nothing is running when the thread's row says something is. The thread's own Stop handles those.
        other = [thread for thread in ([row] if row else self.rows) if thread and thread.get('running') and not thread.get('archived')
                 and thread['id'] not in {seen['id'] for seen in threads}]
        if not runs:
            if other:
                return self.reply(text=f'{self.name(other[0])} is running something Home can’t pause. Use Stop in the thread.'
                                  if len(other) == 1 else 'What’s running can’t be paused from Home. Use Stop in each thread.')
            return self.reply(text=f'Nothing is running in {self.name(row)}.' if row else 'Nothing is running right now.')
        if row is None and len(threads) > 1 and not every:
            names = [self.name(thread) for thread in threads[:4]]
            example = self.handle(threads[0], 'pause the {} one')
            return self.reply(text=f'Which one should I pause: {", ".join(names[:-1])} or {names[-1]}? '
                                   + (f'Say it like "{example}", or "pause everything".' if example else 'Say "pause everything" for all of them.'))
        stopped, notes = [], []
        for thread, task in runs:
            try:
                self.host.dispatch('task_stop', dict(self.where(thread['id']), taskId=task['id']))
                stopped.append(thread)
            except Rejected as exc:
                notes.append(f"Couldn’t pause a run in {self.name(thread)}: {str(exc).rstrip('.')}.")
        if not stopped:
            return self.reply([], notes)
        where = list({thread['id']: thread for thread in stopped}.values())
        places = [self.name(thread) for thread in where]
        named = places[0] if len(places) == 1 else ', '.join(places[:-1]) + ' and ' + places[-1]
        line = f'Paused {named}.' if len(stopped) == 1 else f'Paused {len(stopped)} runs in {named}.'
        if every and other:
            notes.append(f'{len(other)} more can’t be paused from Home. Use Stop in each thread.' if len(other) > 1
                         else f'{self.name(other[0])} can’t be paused from Home. Use Stop in the thread.')
        return self.reply([self.change(line, where[0] if len(where) == 1 else None)], notes)

    def thread_action(self, kind, row):
        if row is None:
            return self.reply(text=f'Say which thread to {kind}, like "{kind} the login thread".')
        where = self.where(row['id'])
        try:
            if kind == 'archive':
                if row.get('archived'):
                    return self.reply(text=f'{self.name(row)} is archived already.')
                self.host.dispatch('conversation_archive', where)
                return self.reply([self.change(f'Archived {self.name(row)}.', row, undo=dict(
                    op='conversation_restore', args=dict(channelId=row['id']), line=f'Brought {self.name(row)} back.'))])
            if kind == 'restore':
                if not row.get('archived'):
                    return self.reply(text=f'{self.name(row)} isn’t archived.')
                self.host.dispatch('conversation_restore', where)
                return self.reply([self.change(f'Brought {self.name(row)} back.', row, undo=dict(
                    op='conversation_archive', args=dict(channelId=row['id']), line=f'Archived {self.name(row)} again.'))])
            pin = kind == 'pin'
            if bool(row.get('pinned')) == pin:
                return self.reply(text=f'{self.name(row)} is {"pinned" if pin else "not pinned"} already.')
            self.host.dispatch('conversation_pin', dict(where, pinned=pin))
            return self.reply([self.change(f'{"Pinned" if pin else "Unpinned"} {self.name(row)}.', row, undo=dict(
                op='conversation_pin', args=dict(channelId=row['id'], pinned=not pin),
                line=f'{"Unpinned" if pin else "Pinned"} {self.name(row)} again.'))])
        except Rejected as exc:
            return self.failed(row, exc)


# What an 'acted' change's Undo may call: the inverse of something answer() did, nothing else.
UNDO_OPS = {'task_undo': {'channelId', 'taskId'}, 'conversation_restore': {'channelId'}, 'conversation_archive': {'channelId'},
            'conversation_pin': {'channelId', 'pinned'}}


def undo(host, scope, change):
    """Undo an 'acted' change through the inverse operation it recorded. Returns the line it now reads."""
    recorded = change.get('undo') if isinstance(change.get('undo'), dict) else {}
    op, args = recorded.get('op'), recorded.get('args')
    if op not in UNDO_OPS or not isinstance(args, dict) or set(args) != UNDO_OPS[op]:
        raise Rejected('This one can’t be undone from here.')
    host.dispatch(op, dict(args, projectId=scope['project_id']))
    return recorded.get('line') or change.get('line')
