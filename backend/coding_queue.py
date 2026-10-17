"""Queued coding requests: a message sent while this conversation's own task runs waits, durably, and starts when that run ends.

Nothing is sent when a request is queued. The saved item holds the composer's exact arguments, so starting it later goes
through the same ``request`` path as a fresh send, with every permission, check and review rule applied then. An item that
can't start stays visible with the reason until it's dismissed. A request sent while every task slot is busy elsewhere
waits here too, as "Waiting for a slot" (why='slot'), and starts when one frees.
"""
import json
import time
import uuid

from dervo_connections import Rejected

DETAILS = {
    'queued': 'Queued. Starts when the current task ends.',
    'failed': 'Could not start.',
}
RESTART_ERROR = 'Dervo restarted before this request started. Nothing was sent. Send it again.'
RESET_RESTART_ERROR = 'Dervo restarted before the account’s usage window reset. Nothing was sent. Send it again.'
# How long a request stays waiting across a restart: a slot wait from when it was sent, a reset wait from its reset.
# Longer than a night, so work queued in the evening still starts after an overnight update or crash; older than this
# it fails as before rather than starting by surprise the moment Dervo opens.
KEPT_FOR = 24 * 3600
# A kept request doesn't start while runs Dervo lost track of in the same project are unsettled: they may have left
# changes in the folder nobody has checked (product review, 23 Sep, finding 1).
KEPT_DETAIL = 'Kept when Dervo restarted. Starts once the runs Dervo lost track of in this project are settled.'


def survives_restart(item, now):
    """Whether a queued item keeps waiting after Dervo restarts (systems report, 23 Sep, problem 5). Nothing is ever sent
    for a queued item, so keeping one is safe when nothing it waited on is in doubt: it waited only for a free slot
    ('slot'), or for the account's usage reset (``not_before`` with no other reason). An item waiting behind its own
    conversation's run still fails, because that run ended without confirmation when Dervo closed. So does one whose
    own conversation lost a run (CodingQueue.__init__), and a kept one waits while its project has any (_held)."""
    if item.get('why') == 'slot':
        return now - (item.get('created_at') or 0) < KEPT_FOR
    return bool(item.get('not_before')) and item.get('why') is None and now - item['not_before'] < KEPT_FOR
# Why a message sent mid-run waited instead of joining the run (#5, DC4). The key is ``why`` on the queue item and its
# view; the sentence is the view's ``detail``. A message joins a run only as plain text, so anything carrying more waits.
WHY_DETAILS = {
    'image': 'Waits for this run to end: a message with an image can’t join a run.',
    'review': 'Waits for this run to end: a message with its own review setting can’t join a run.',
    'check': 'Waits for this run to end: a message with its own check choice can’t join a run.',
    'read_only': 'Waits for this run to end: a read-only question can’t join a run.',
    'no_run': 'Waits for this run to end.',
    # Home's jobs never join a running task: they'd reach it as your words, with its access (security review H1).
    'home': 'Waits for this run to end: a job from Home starts as its own task.',
    # Sent into a run that ended before reading it (not your Stop): it goes as the next message (handoff map H5).
    'unread': 'The run ended before it read this, so it goes next.',
    # Every task slot was taken in other conversations when it was sent (MAX_PARALLEL_OWNERS): it starts when one frees.
    'slot': 'Waiting for a slot. Every task slot is busy, and this starts when one frees up.',
    # Beta.18: the thread's own branch and folder are being set up (thread_folders). It starts when they're ready.
    'setup': 'Waits for this thread’s branch and folder, then it starts.',
}


def waiting_detail(until, why=None):
    if why in WHY_DETAILS:
        return WHY_DETAILS[why]
    return 'Waiting for the account’s usage window to reset, then it starts.' if until else DETAILS['queued']


class CodingQueue:
    def __init__(self, conversation):
        self.conversation, self.coding, self.host, self.store = conversation, conversation.coding, conversation.host, conversation.store
        from image_attachments import schema as image_schema
        with self.store.db:
            image_schema(self.store.db)  # queued items pin their images in the same use table a task does
            self.store.db.execute('CREATE TABLE IF NOT EXISTS coding_queue (id TEXT PRIMARY KEY, '
                                  'thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, project_id TEXT NOT NULL, '
                                  'client_id TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(thread_id, client_id))')
            # A queued follow-up never starts on its own after a restart: the task it waited for ended without confirmation.
            # One that only waited for a slot or a usage reset keeps waiting (survives_restart), unless its own
            # conversation lost a run at this restart: a slot item can sit behind its own thread's run once an earlier
            # item from the same thread started (backend review, 23 Sep, finding 2).
            now = time.time()
            lost_threads = {run.get('thread_id') for run in getattr(self.coding, 'lost_runs', None) or []}
            # Every run still unsettled, not only this restart's: after a second restart the first one's lost runs are
            # already disconnected, so they're no longer in lost_runs, and a kept item beside them would start (_held).
            self.unsettled = [{'task_id': row[0], 'project_id': row[1]} for row in self.store.db.execute(
                "SELECT id, json_extract(document, '$.context.project_id') FROM coding_tasks "
                "WHERE json_extract(document, '$.state')='disconnected'").fetchall()] if self.store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone() else []
            for item in self._all():
                if item['state'] != 'queued':
                    continue
                if survives_restart(item, now) and item['thread_id'] not in lost_threads:
                    item['kept'] = int(now)
                else:
                    item.update(state='failed', error=RESET_RESTART_ERROR if item.get('not_before') else RESTART_ERROR)
                self._put(item)

    # ------------------------------------------------------------------ records
    def _all(self):
        return [json.loads(row[0]) for row in self.store.db.execute('SELECT document FROM coding_queue ORDER BY rowid').fetchall()]

    def _put(self, item):
        self.store.db.execute('INSERT INTO coding_queue VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                              (item['id'], item['thread_id'], item['project_id'], item['client_id'], json.dumps(item)))

    def items(self, thread_id):
        return [json.loads(row[0]) for row in self.store.db.execute(
            'SELECT document FROM coding_queue WHERE thread_id=? ORDER BY rowid', (thread_id,)).fetchall()]

    def find(self, thread_id, client_id):
        row = self.store.db.execute('SELECT document FROM coding_queue WHERE thread_id=? AND client_id=?', (thread_id, client_id)).fetchone()
        return json.loads(row[0]) if row else None

    def get(self, thread_id, queue_id):
        row = self.store.db.execute('SELECT document FROM coding_queue WHERE thread_id=? AND id=?', (thread_id, queue_id)).fetchone()
        return json.loads(row[0]) if row else None

    def view(self, item):
        waiting = [other['id'] for other in self.items(item['thread_id']) if other['state'] == 'queued']
        # A slot wait whose thread started an earlier item now waits for that run, not for a slot, whatever it was when
        # it was sent (backend review, 23 Sep): it reads as queued behind the run, which is where it stands.
        why = item.get('why')
        if why == 'slot' and item['state'] == 'queued' and self.coding.busy_thread(item['thread_id']):
            why = None
        # Kept across a restart and waiting on runs Dervo lost here: the composer says so and names what to do (the
        # frontend reads this, not the sentence).
        held = item['state'] == 'queued' and bool(item.get('kept')) and self._held(item['project_id'])
        return {'id': item['id'], 'client_id': item['client_id'], 'taskId': None, 'task_id': None, 'state': item['state'],
                'queued': True, 'text': item['args']['text'], 'images': list(item['args'].get('images') or []),
                'checkProfile': item['args'].get('checkProfile'), 'review': item['args'].get('review'),
                'position': waiting.index(item['id']) + 1 if item['id'] in waiting else None,
                'error': item.get('error'), 'created_at': item['created_at'], 'until': item.get('not_before'),
                'why': why, 'kept': bool(item.get('kept')), 'held': held,
                'detail': item.get('error') or (KEPT_DETAIL if held
                                                else waiting_detail(item.get('not_before'), why) if item['state'] == 'queued' else DETAILS[item['state']])}

    def _held(self, project_id):
        """Whether a run Dervo lost track of, at this restart or an earlier one, in this project, is still unsettled
        (disconnected). Primary-key reads of the few lost runs only, so the pump can ask every round."""
        for lost in (getattr(self.coding, 'lost_runs', None) or []) + getattr(self, 'unsettled', []):
            if lost.get('project_id') != project_id:
                continue
            row = self.store.db.execute("SELECT json_extract(document, '$.state') FROM coding_tasks WHERE id=?", (lost['task_id'],)).fetchone()
            if row and row[0] == 'disconnected':
                return True
        return False

    def views(self, thread_id):
        return [self.view(item) for item in self.items(thread_id)]

    # ------------------------------------------------------------------ add, cancel
    def add(self, args, ctx, binding, not_before=None, why=None):
        """Save the composer's arguments as they were sent. Images are pinned so they can't be removed while waiting.
        ``why`` (a WHY_DETAILS key, or None) is why a mid-run message waited instead of joining the run."""
        if why is not None and why not in WHY_DETAILS:
            raise Rejected('A queued message can only wait for a known reason')
        item = dict(id=uuid.uuid4().hex, thread_id=binding['id'], project_id=binding['project_id'], client_id=args['clientId'],
                    args={key: args[key] for key in ('text', 'readOnly', 'images', 'checkProfile', 'review', 'origin', 'clicked') if key in args},
                    state='queued', error=None, created_at=int(time.time()), why=why,
                    computer_access=self.conversation.full_access(binding)['computer_access'])
        if not_before is not None:
            item['not_before'] = int(not_before)  # D5: starts no earlier than the account's reported reset
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            for image_id in item['args'].get('images') or []:
                row = self.store.db.execute("SELECT id FROM conversation_images WHERE id=? AND thread_id=? AND state='ready'",
                                            (image_id, binding['id'])).fetchone()
                if row is None:
                    raise Rejected('An attached image was removed before sending. Choose it again.')
                self.store.db.execute('INSERT OR IGNORE INTO conversation_image_uses VALUES (?,?,?)', (image_id, 'queue', item['id']))
            self._put(item)
        return self.view(item)

    def remove(self, item):
        with self.store.db:
            self.store.db.execute("DELETE FROM conversation_image_uses WHERE source_kind='queue' AND source_id=?", (item['id'],))
            self.store.db.execute('DELETE FROM coding_queue WHERE id=?', (item['id'],))

    def cancel(self, args, binding):
        queue_id = args['queueId']
        if type(queue_id) is not str:
            raise Rejected('Cancelling a queued request needs its ID')
        item = self.get(binding['id'], queue_id)
        if item is None:
            raise Rejected('That queued request is no longer waiting')
        with self.store.db:
            import thread_fanout
            thread_fanout.cancelled(self.coding, item)
            import task_questions
            task_questions.cancelled(self.coding, item)
            import thread_notes
            thread_notes.cancelled(self.coding, item)
            self.remove(item)
        return {'id': queue_id, 'state': 'cancelled'}

    # ------------------------------------------------------------------ start
    def drain(self):
        """Start the first waiting item of every conversation whose own run has ended. Called from the coding pump."""
        from host import ACTOR
        threads = [row[0] for row in self.store.db.execute(
            'SELECT DISTINCT thread_id FROM coding_queue ORDER BY rowid').fetchall()]
        for thread_id in threads:
            head = next((item for item in self.items(thread_id) if item['state'] == 'queued'), None)
            if head is None or self.coding.busy_thread(thread_id) or self.coding.warm_start_going(thread_id):
                continue  # its run, or the Claude Code started ahead of time for it, isn't done yet
            if head.get('not_before') and head['not_before'] > time.time():
                continue  # waiting for the account's usage window to reset
            if head.get('kept') and self._held(head['project_id']):
                continue  # kept across a restart: waits until the runs Dervo lost in this project are settled
            if self.coding.owner_slot_problem(thread_id):
                continue  # the host's limit runs elsewhere: this one keeps waiting for a slot
            ctx = dict(thread_id=thread_id, project_id=head['project_id'], actor=ACTOR)
            try:
                binding = self.store.binding(**ctx)
            except Rejected as exc:
                self._fail(head, str(exc))
                continue
            try:
                self.conversation.check_writer(binding)
            except Rejected:
                continue  # a chat or coordination turn is open here right now; the item keeps waiting
            try:
                started = self.conversation.request({'clientId': head['client_id'], **head['args']}, ctx, binding, starting=head)
            except Rejected as exc:
                self._fail(head, str(exc))
                continue
            if started.get('taskId'):
                self.remove(head)  # the task pinned the same images under its own id inside its save
            elif started.get('state') == 'busy_elsewhere':
                continue  # a slot was taken between the check and the start; wait for the next pump
            elif started.get('state') != 'queued':
                self._fail(head, started.get('detail') or 'Could not start.')

    def _fail(self, item, reason):
        item.update(state='failed', error=str(reason)[:1024])
        with self.store.db:
            self._put(item)
