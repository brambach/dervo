"""Scoped, bounded conversation updates. Full records remain the authority."""
from collections import OrderedDict
import hashlib
import json
import secrets
import time
from dervo_connections import Rejected

MAX_SNAPSHOT_BYTES = 6 * 1024 * 1024
MAX_CACHE_BYTES = 24 * 1024 * 1024
MAX_CURSORS = 8
MAX_PATCHES = 2048
BLOCKED_PATH_KEYS = {'__proto__', 'prototype', 'constructor'}
# 16.4 item 1: a thread's feed carries its newest page of messages and tasks; older pages come from conversation_older.
PAGE_ROWS = 80
PAGE_BYTES = 2 * 1024 * 1024
# A page that grew past this while its cursor was kept is cut again at the newest page.
KEPT_PAGE_BYTES = 4 * 1024 * 1024
SETTLED_TASK_STATES = {'completed', 'answered', 'idle', 'cancelled', 'failed', 'refused'}
# What the thread view never reads. The full record stays in the store and task_detail returns it. A run's baseline
# (git HEAD and the fingerprints of files changed before the agent started) was about 124 of a task's 190 KiB in the
# installed app's 200 largest tasks on 28 Sep; written is up to 2,000 paths a run.
TRIMMED_TOKEN_KEYS = ('request_id', 'call_id', 'session_id', 'turn_id', 'audit_ref')
TRIMMED_RUN_KEYS = ('baseline', 'written', 'process')


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode()


def stamp(value):
    """Where a row sorts in the thread (timelineRows.orderRows): a message by its time, a task by its first message's."""
    if 'messages' in value:
        messages = value.get('messages') or []
        value = messages[0] if messages and isinstance(messages[0], dict) else {}
    at = value.get('created_at')
    return at if type(at) in (int, float) and at == at and at >= 0 else 0


def trimmed(task):
    """A task as the thread view shows it: its steps and runs without the protocol records it never reads."""
    task = dict(task)
    task['tokens'] = [{key: value for key, value in token.items() if key not in TRIMMED_TOKEN_KEYS} if isinstance(token, dict) else token
                      for token in task.get('tokens') or []]
    task['runs'] = [{key: value for key, value in run.items() if key not in TRIMMED_RUN_KEYS} if isinstance(run, dict) else run
                    for run in task.get('runs') or []]
    import run_ending
    task['ending'] = run_ending.classify(task)  # beta.17.1: what ended the newest run, so only a real failure shows an X
    return task


def pinned(task):
    """Open work stays in the newest page whatever its age: stop, the guide and questions read it from there."""
    return task.get('state') not in SETTLED_TASK_STATES or any(
        isinstance(question, dict) and question.get('state') == 'pending' for question in task.get('questions') or [])


def page(history, tasks, before=None, cut=None):
    """The newest rows below ``before``, cut between two times so the next page starts exactly where this one ends.

    With ``cut``, every row at or after that time instead (a kept page). Rows that share a time are never split, so a
    page can pass PAGE_ROWS or PAGE_BYTES by one group. Returns the history and tasks in their original order, the cut
    and whether anything older is left."""
    rows = [(stamp(row), row) for row in [*history, *tasks] if before is None or stamp(row) < before]
    if cut is None:
        groups = {}
        for at, row in rows:
            groups.setdefault(at, []).append(row)
        count = size = 0
        for at in sorted(groups, reverse=True):
            group = groups[at]
            weight = sum(len(encoded(row)) for row in group)
            if cut is not None and (count+len(group) > PAGE_ROWS or size+weight > PAGE_BYTES):
                break
            count, size, cut = count+len(group), size+weight, at
    if cut is None:
        return [], [], None, False
    return ([row for row in history if cut <= stamp(row) and (before is None or stamp(row) < before)],
            [row for row in tasks if cut <= stamp(row) and (before is None or stamp(row) < before)],
            cut, any(at < cut for at, _ in rows))


def changes(before, after, path=()):
    """Append text without resending its prefix. Replace structural changes atomically."""
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, str) and isinstance(after, str) and after.startswith(before):
        return [{'op':'append', 'path':list(path), 'length':len(before.encode('utf-16-le'))//2, 'text':after[len(before):]}]
    if isinstance(before, dict) and isinstance(after, dict) and not ((before.keys() | after.keys()) & BLOCKED_PATH_KEYS):
        result = [{'op':'remove','path':[*path,key]} for key in before.keys()-after.keys()]
        if len(result) > MAX_PATCHES: return [{'op':'set','path':list(path),'value':after}]
        for key, value in after.items():
            result += changes(before[key], value, path+(key,)) if key in before else [{'op':'set','path':[*path,key],'value':value}]
            if len(result) > MAX_PATCHES: return [{'op':'set','path':list(path),'value':after}]
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        result = []
        for index, value in enumerate(after):
            result += changes(before[index], value, path+(index,))
            if len(result) > MAX_PATCHES: return [{'op':'set','path':list(path),'value':after}]
        return result
    return [{'op':'set','path':list(path),'value':after}]


class ConversationFeed:
    def __init__(self, host, clock=time.monotonic):
        self.host, self.clock = host, clock
        self.cache = OrderedDict()
        self.bytes = 0

    def _prune(self):
        now = self.clock()
        for key, record in list(self.cache.items()):
            if now-record['used_at'] > 60:
                self.bytes -= record['bytes']
                del self.cache[key]
        while len(self.cache) > MAX_CURSORS or self.bytes > MAX_CACHE_BYTES:
            _, record = self.cache.popitem(last=False)
            self.bytes -= record['bytes']

    def read(self, args, actor):
        cursor, force = args['cursor'], args['force']
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) != 32 or any(c not in '0123456789abcdef' for c in cursor)):
            raise Rejected('Conversation cursor is invalid. Reload this conversation.')
        if type(force) is not bool:
            raise Rejected('Conversation refresh must be an explicit choice.')
        scope = {'projectId':args['projectId'], 'channelId':args['channelId']}
        # Never reuse cached content before current account, project and private ownership checks.
        binding = self.host.store.binding(scope['channelId'], scope['projectId'], actor=actor)
        # The session and the working folder aren't ownership: a thread's first run on its own branch moves it into its
        # worktree (cwd changes), and refusing that cursor left the open thread stuck on "Reload before continuing".
        fingerprint = hashlib.sha256(encoded({'binding':{key:value for key,value in binding.items() if key not in ('session_id','cwd')},'actor':actor})).hexdigest()
        self._prune()
        previous = self.cache.get(cursor)
        if previous and previous['binding'] != fingerprint:
            raise Rejected('This conversation connection changed. Reload before continuing.')
        now = self.clock()
        if previous:
            previous['used_at'] = now
            self.cache.move_to_end(cursor)
            # Provider text and task changes are committed to this same SQLite connection.
            # Periodic fresh records still detect changed files and non-database lifecycle transitions.
            if not force and previous['stamp'] == self.host.store.db.total_changes and now-previous['checked_at'] < .5:
                return {'cursor':cursor,'base':cursor,'reset':False,'patches':[], 'active':previous['active']}
        history = self.host.dispatch('history',scope)
        tasks = [trimmed(task) for task in self.host.dispatch('tasks',scope)]
        # Streamed text belongs to the turn running now, so it's always in the newest page.
        streaming = [row for row in history if row.get('state') == 'streaming']
        settled = [row for row in history if row.get('state') != 'streaming']
        # A kept cursor keeps its page's start, so rows don't slide out under the reader between polls.
        kept = previous.get('cut') if previous else None
        shown, listed, cut, older = page(settled, tasks, cut=kept)
        if kept is not None and len(encoded([shown, listed])) > KEPT_PAGE_BYTES:
            shown, listed, cut, older = page(settled, tasks)
        ids = {task['id'] for task in listed}
        # A task this cursor already carried stays, so a reader never keeps an open task's last copy after it settles.
        carried = {task['id'] for task in previous['snapshot']['tasks']} if previous else set()
        ids |= {task['id'] for task in tasks if task['id'] in carried}
        snapshot = {
            'activity':self.host.dispatch('conversation_status',scope),
            'history':shown+streaming,
            'tasks':[task for task in tasks if task['id'] in ids or pinned(task)],
            'requests':self.host.dispatch('harness_requests',scope),
            'queue':self.host.coding.conversation.queue.views(scope['channelId']),
            # Rows before this time come from conversation_older.
            'page':{'before':cut,'has_older':older},
        }
        snapshot['activity']['active_turn_ids'] = [
            mid for mid, job in self.host.turns.items()
            if not job.closed and job.binding['id'] == scope['channelId']
        ]
        size = len(encoded(snapshot))
        if size > MAX_SNAPSHOT_BYTES:
            # Open tasks and one time's rows can't be split. Keep the reader alive with what's left, and say so.
            snapshot['tasks'] = [task for task in snapshot['tasks'] if task['id'] in ids]
            snapshot['history'] = shown
            size = len(encoded(snapshot))
            if size > MAX_SNAPSHOT_BYTES:
                raise Rejected('This conversation is too large to update at once. Its saved work is retained.')
        active = bool(snapshot['activity'].get('active') or snapshot['activity'].get('review_active') or any(
            request.get('state') in ('queued','running','stopping') or any(item.get('status') in ('dispatched','cancelling') for item in request.get('delegations',[]))
            for request in snapshot['requests']) or any(item['state'] == 'queued' for item in snapshot['queue']))
        if previous and previous['snapshot'] == snapshot and not force:
            previous.update(stamp=self.host.store.db.total_changes, checked_at=now, active=active)
            return {'cursor':cursor,'base':cursor,'reset':False,'patches':[], 'active':active}
        token = secrets.token_hex(16)
        self.cache[token] = {'snapshot':snapshot, 'binding':fingerprint, 'bytes':size, 'cut':cut,
            'stamp':self.host.store.db.total_changes,'checked_at':now,'used_at':now,'active':active}
        self.bytes += size
        self._prune()
        patches = changes(previous['snapshot'],snapshot) if previous and not force else None
        if patches is not None and len(encoded(patches)) < size:
            return {'cursor':token,'base':cursor,'reset':False,'patches':patches,'active':active}
        return {'cursor':token,'base':cursor,'reset':True,'snapshot':snapshot,'active':active}

    def older(self, args, actor):
        """The page of history and tasks just before ``before`` (a snapshot's or an earlier page's cut)."""
        before = args['before']
        if type(before) not in (int, float) or before != before or not 0 < before < 2**53:
            raise Rejected('Older messages need the time the loaded ones start at. Reload this conversation.')
        scope = {'projectId':args['projectId'], 'channelId':args['channelId']}
        # The same account, project and private ownership checks as a snapshot.
        self.host.store.binding(scope['channelId'], scope['projectId'], actor=actor)
        history = [row for row in self.host.dispatch('history',scope) if row.get('state') != 'streaming']
        tasks = [trimmed(task) for task in self.host.dispatch('tasks',scope)]
        shown, listed, cut, older = page(history, tasks, before=before)
        return {'history':shown, 'tasks':listed, 'before':cut, 'has_older':older}
