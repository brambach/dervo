"""Focused child-thread proposals. Provider tools propose; only an explicit UI action starts work.

A child gets the access its source task had, never more (security review H1, 23 Sep). The proposal's words are the
agent's, so the click on Start thread isn't the person asking for a shell: a source that ran without full access, like
a Home job nobody clicked, proposes a child that runs without it and shows Home's usual "Say go" notice.

Suggestions (9 Oct, Bryce and tester Harrison: Home shows reminders, suggestions and what needs you). The Claude desktop
app's "Suggested task" chips come from a tool that app gives Claude. Codex sends nothing like them. Claude Code can
print one guessed next prompt per turn (``--prompt-suggestions``), which Dervo doesn't ask for: a guess at what you'd
type isn't a task an agent proposes. Home lists the proposals this tool made that nobody has started or dismissed
(``home_suggestions``); each opens its task, where Start shows the whole request first. Agents aren't asked to propose
unasked follow-ups: how often they would, and how useful those would be, hasn't been measured on real runs."""
import hashlib
import json
import re
import time
import uuid

import secret_scan
from dervo_connections import Rejected

TOOL = 'dervo_propose_thread'
LIMIT = 3
BINDING_KEYS = ('profile_id', 'provider', 'identity', 'model', 'cwd', 'visibility')
PROMPT = ('When the user asks to split work into separate threads, use dervo_propose_thread once per focused request, '
          'up to three. Include the facts and source references that child needs, without unrelated ticket context. '
          'Each proposal shows its complete request for the user to start. Say proposed, never started. '
          'Do not propose extra threads for ordinary work or claim the children have already run. '
          # Bryce, 26 Sep: "should several ideas share one session, or keep a thread each?" (PLAN-beta17, section 1).
          'A separate thread is for work the user will want to find, review or approve on its own: it gets its own '
          'result, files and Catch up row, and this thread reviews it when it finishes. To split one piece of work inside '
          'this thread (parallel reading, checks), use your own sub-agents instead. Several unrelated ideas don\'t share '
          'one session.')
HOME_FIELDS = {'home_suggestions': {'projectId', 'profileId'}}
SUGGESTED_DAYS, SUGGESTED_SHOWN = 7, 3


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS thread_proposals ('
               'id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, task_id TEXT NOT NULL, '
               'proposal_key TEXT NOT NULL, child_thread_id TEXT, document TEXT NOT NULL, UNIQUE(task_id, proposal_key))')
    db.execute('CREATE UNIQUE INDEX IF NOT EXISTS thread_proposals_child ON thread_proposals(child_thread_id)')
    # Owned by the child. Deleting the source mustn't turn a child into a new fan-out root.
    db.execute('CREATE TABLE IF NOT EXISTS thread_fanout_children ('
               'thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, project_id TEXT NOT NULL, '
               'source_thread_id TEXT NOT NULL, source_task_id TEXT NOT NULL, purpose TEXT NOT NULL)')


def offered(coding, task):
    return task.get('origin') == 'conversation' and not coding.store.db.execute(
        'SELECT 1 FROM thread_fanout_children WHERE thread_id=?', (task['context']['thread_id'],)).fetchone()


def access_stamp(db, pid):
    # These are local grants, not credentials. A changed grant can't silently widen a saved proposal.
    records = []
    for table in ('coding_permissions', 'coding_full_access'):
        row = db.execute(f'SELECT document FROM {table} WHERE project_id=?', (pid,)).fetchone()
        records.append(json.loads(row[0]) if row else None)
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _text(value, name, limit):
    if type(value) is not str or not value.strip() or len(value.encode()) > limit or '\x00' in value:
        raise Rejected(f'{name} needs text up to {limit} UTF-8 bytes.')
    if secret_scan.redact(value) != value:
        raise Rejected(f'Remove credentials from the proposed {name.lower()}.')
    return value.strip()


def capture(coding, task, args):
    if not offered(coding, task):
        raise Rejected('Only an original conversation can propose focused threads.')
    if set(args) != {'proposal_key', 'title', 'purpose', 'request'}:
        raise Rejected('Unsupported thread proposal fields.')
    key = args['proposal_key']
    if type(key) is not str or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', key):
        raise Rejected('Use a stable proposal key with up to 64 letters, digits, underscores or hyphens.')
    title = _text(args['title'], 'Title', 100)
    if '\n' in title or '\r' in title:
        raise Rejected('Keep the thread title on one line.')
    content = dict(title=title, purpose=_text(args['purpose'], 'Purpose', 500), request=_text(args['request'], 'Request', 16000))
    db, ctx = coding.store.db, task['context']
    existing = db.execute('SELECT document FROM thread_proposals WHERE task_id=? AND proposal_key=?', (task['id'], key)).fetchone()
    if existing:
        row = json.loads(existing[0])
        if any(row[k] != value for k, value in content.items()):
            raise Rejected('That proposal key already refers to a different request.')
        return {'id': row['id'], 'state': row['state'], 'detail': 'Already proposed. The user can review it in this conversation.'}
    if db.execute('SELECT count(*) FROM thread_proposals WHERE task_id=?', (task['id'],)).fetchone()[0] >= LIMIT:
        raise Rejected('This request already has three thread proposals. Keep the remaining work in this conversation.')
    binding = coding.store.binding(**ctx)
    if any(binding.get(k) != task['binding'].get(k) for k in BINDING_KEYS):
        raise Rejected('The source account or model changed. Ask for a fresh proposal.')
    source_request = next((m['text'] for m in reversed(task.get('messages', [])) if m.get('role') == 'user'), '')
    row = dict(id=str(uuid.uuid4()), project_id=ctx['project_id'], thread_id=ctx['thread_id'], task_id=task['id'],
               actor=ctx['actor'], proposal_key=key, binding={k: binding.get(k) for k in BINDING_KEYS},
               access_stamp=access_stamp(db, ctx['project_id']), read_only=task.get('read_only') is True,
               source_request=secret_scan.redact(source_request), created_at=int(time.time()), state='proposed',
               child_thread_id=None, error=None, **content)
    with db:
        db.execute('INSERT INTO thread_proposals VALUES (?,?,?,?,?,?,?)',
                   (row['id'], row['project_id'], row['thread_id'], task['id'], key, None, json.dumps(row)))
    return {'id': row['id'], 'state': 'proposed', 'detail': 'Proposed for the user to review and start. No child thread has run.'}


def _rows(coding, task):
    rows = coding.store.db.execute('SELECT document FROM thread_proposals WHERE task_id=? ORDER BY rowid', (task['id'],))
    return [json.loads(row[0]) for row in rows]


def _existing(coding, row):
    child = row.get('child_thread_id')
    if not child:
        return None
    client = 'thread-proposal-' + row['id']
    request = coding.conversation.find(child, client)
    if request:
        return {'state': 'started', 'taskId': request['task_id']}
    queue = coding.conversation.queue.find(child, client)
    if queue:
        return {'state': queue['state'], 'taskId': None, 'detail': queue.get('error')}
    return None


def _problem(coding, task, row):
    if row['actor'] != task['context']['actor'] or row['project_id'] != task['context']['project_id'] or row['thread_id'] != task['context']['thread_id']:
        raise Rejected('This proposal belongs to another conversation.')
    binding = coding.store.binding(**task['context'])
    if any(binding.get(k) != row['binding'].get(k) for k in BINDING_KEYS):
        return 'The source account or model changed. Ask for a fresh proposal.'
    if access_stamp(coding.store.db, row['project_id']) != row['access_stamp']:
        return 'Project access changed. Ask for a fresh proposal before starting this thread.'
    if row['child_thread_id']:
        try:
            child = coding.store.binding(row['child_thread_id'], row['project_id'], actor=row['actor'])
            if any(child.get(k) != row['binding'].get(k) for k in BINDING_KEYS):
                return 'The child thread’s account or model changed. Continue in that thread.'
        except Rejected:
            return 'The child thread was removed. Ask for a fresh proposal.'
    return None


def view(coding, task, row):
    existing = _existing(coding, row)
    problem = _problem(coding, task, row)
    state = existing['state'] if existing else row['state']
    if state in ('cancelled', 'dismissed'):
        problem = None
    if not existing and state in ('started', 'queued'):
        state = 'unknown'
        problem = problem or 'The saved request is no longer available. Open the child thread before starting new work.'
    return {k: row[k] for k in ('id', 'title', 'purpose', 'request', 'source_request', 'created_at', 'read_only')} | {
        'state': state, 'channelId': row['child_thread_id'], 'taskId': existing.get('taskId') if existing else None,
        'canStart': not existing and state in ('proposed', 'starting', 'failed') and not problem,
        'canDismiss': not existing and state in ('proposed', 'starting', 'failed'),
        'detail': problem or (existing.get('detail') if existing else row.get('error')), 'model': row['binding']['model'], 'identity': row['binding']['identity'],
        # What Start asks for, so the button can say so. A changed grant already stops the start (access_stamp).
        'fullAccess': source_full_access(task, row)}


def source_full_access(task, row):
    """Whether starting this proposal asks for full access: only when its source task had it and the child isn't read-only."""
    return task.get('full_access') is True and not row['read_only']


def _save(db, row):
    with db:
        db.execute('UPDATE thread_proposals SET child_thread_id=?,document=? WHERE id=?',
                   (row['child_thread_id'], json.dumps(row), row['id']))


def dispatch(coding, op, task, args):
    rows = _rows(coding, task)
    if op == 'task_thread_proposals':
        return [view(coding, task, row) for row in rows]
    row = next((row for row in rows if row['id'] == args['proposalId']), None)
    if not row:
        raise Rejected('Unknown thread proposal for this task.')
    existing = _existing(coding, row)
    if op == 'task_thread_dismiss':
        if existing:
            raise Rejected('This thread has started. Open it to stop or review its work.')
        row.update(state='dismissed', error=None)
        _save(coding.store.db, row)
        return view(coding, task, row)
    if existing:
        return view(coding, task, row)
    if row['state'] not in ('proposed', 'starting', 'failed'):
        raise Rejected('This proposal can’t start again. Open the child thread or ask for a fresh proposal.')
    problem = _problem(coding, task, row)
    if problem:
        raise Rejected(problem)
    coding.host.require_beta_access()
    db = coding.store.db
    # The new conversation starts on the source binding, even if the project's defaults differ.
    if not row['child_thread_id']:
        coding.host.organisation()
        with db:
            db.execute('BEGIN IMMEDIATE')
            project = dict(db.execute('SELECT * FROM projects WHERE id=?', (row['project_id'],)).fetchone())
            project.update(profile_id=row['binding']['profile_id'], model=row['binding']['model'])
            row['child_thread_id'] = coding.host.new_conversation(project, row['title'], private=row['binding']['visibility'] == 'private')
            db.execute('INSERT INTO thread_fanout_children VALUES (?,?,?,?,?)',
                (row['child_thread_id'], row['project_id'], row['thread_id'], row['task_id'], row['purpose']))
            row.update(state='starting', error=None)
            db.execute('UPDATE thread_proposals SET child_thread_id=?,document=? WHERE id=?',
                       (row['child_thread_id'], json.dumps(row), row['id']))
    text = f"Purpose: {row['purpose']}\n\n{row['request']}"
    # Sent as a Home job that's clicked only when the source had full access: the child never gets more than its source.
    try:
        result = coding.host.dispatch('coding_request', {'projectId': row['project_id'], 'channelId': row['child_thread_id'],
            'clientId': 'thread-proposal-' + row['id'], 'text': text, 'queueIfBusy': True,
            'origin': 'home', 'clicked': source_full_access(task, row),
            **({'readOnly': True} if row['read_only'] else {})})
        row.update(state=('queued' if result.get('state') == 'queued' else 'started') if result.get('id') else 'failed',
                   error=result.get('detail') if not result.get('id') else None)
    except Exception as exc:
        row.update(state='failed', error=secret_scan.redact(str(exc)))
    _save(db, row)
    return view(coding, task, row)


def validate_launch(coding, binding, client_id):
    """Recheck a queued proposal at the moment it starts, after any account or grant change."""
    if not client_id.startswith('thread-proposal-'):
        return
    record = coding.store.db.execute('SELECT document FROM thread_proposals WHERE id=? AND child_thread_id=?',
                                    (client_id[len('thread-proposal-'):], binding['id'])).fetchone()
    if not record:
        raise Rejected('The source thread proposal is no longer available. Send a new request in this thread.')
    row = json.loads(record[0])
    context = dict(project_id=row['project_id'], thread_id=row['thread_id'], actor=row['actor'])
    task = coding.read(row['task_id'], context, rebound=True)
    problem = _problem(coding, task, row)
    if problem:
        raise Rejected(problem)
    if row['state'] in ('dismissed', 'cancelled'):
        raise Rejected('This thread proposal was cancelled. Send a new request to start work.')


def cancelled(coding, item):
    """Keep cancellation alongside the proposal when the normal queue removes its item. Caller owns the transaction."""
    if not item['client_id'].startswith('thread-proposal-'):
        return
    record = coding.store.db.execute('SELECT document FROM thread_proposals WHERE id=? AND child_thread_id=?',
        (item['client_id'][len('thread-proposal-'):], item['thread_id'])).fetchone()
    if record:
        row = json.loads(record[0])
        row.update(state='cancelled', error='Cancelled before this thread started.')
        coding.store.db.execute('UPDATE thread_proposals SET document=? WHERE id=?', (json.dumps(row), row['id']))


def annotate(coding, task):
    task.pop('has_thread_proposals', None)
    task.pop('thread_source', None)
    db = coding.store.db
    if db.execute('SELECT 1 FROM thread_proposals WHERE task_id=?', (task['id'],)).fetchone():
        task['has_thread_proposals'] = True
    row = db.execute('SELECT * FROM thread_fanout_children WHERE thread_id=? AND project_id=?',
                     (task['context']['thread_id'], task['context']['project_id'])).fetchone()
    if not row:
        return
    source = dict(row)
    try:
        parent = coding.store.binding(source['source_thread_id'], source['project_id'], actor=task['context']['actor'])
    except Rejected:
        task['thread_source'] = {'channelId': None, 'taskId': None, 'name': 'Source thread removed', 'purpose': source['purpose']}
        return
    if parent['visibility'] == 'private' and task['binding']['visibility'] != 'private':
        return
    name = db.execute('SELECT name FROM local_channels WHERE id=?', (source['source_thread_id'],)).fetchone()
    task['thread_source'] = {'channelId': source['source_thread_id'], 'taskId': source['source_task_id'], 'name': name[0] if name else 'Source thread', 'purpose': source['purpose']}


def suggestions(host, actor, project_id, profile_id, now=None):
    """Home's Suggestions: proposals agents made in this project's threads in the last week that nobody has started or
    dismissed, newest first. One whose account, model or project access changed since isn't listed: its thread says
    why. Reading them starts nothing; each row opens its task."""
    from project_record_scope import project_record_scope
    import pickup
    scope = project_record_scope(host.store, project_id, profile_id)
    db = host.store.db
    schema(db)
    # The threads Home lists: shared ones and your own private ones on this project's side, not archived.
    threads = {row['id']: row for row in pickup.project_threads(db, scope, actor)}
    since = (time.time() if now is None else now) - SUGGESTED_DAYS * 86400
    coding = host.coding_tasks()
    shown = []
    for (document,) in db.execute("SELECT document FROM thread_proposals WHERE project_id=? AND json_extract(document,'$.state')='proposed' "
                                  "AND json_extract(document,'$.created_at')>=? ORDER BY rowid DESC", (project_id, since)).fetchall():
        row = json.loads(document)
        if row['thread_id'] not in threads or row['actor'] != actor or row.get('child_thread_id'):
            continue
        context = {'project_id': row['project_id'], 'thread_id': row['thread_id'], 'actor': row['actor']}
        try:
            if _problem(coding, {'context': context}, row):
                continue
        except Rejected:
            continue
        shown.append(row)
    return {'suggestions': [{'id': row['id'], 'title': row['title'], 'purpose': row['purpose'], 'channelId': row['thread_id'],
                             'thread': threads[row['thread_id']]['name'], 'taskId': row['task_id'],
                             'provider': row['binding'].get('provider'), 'createdAt': row['created_at']}
                            for row in shown[:SUGGESTED_SHOWN]],
            'more': max(0, len(shown) - SUGGESTED_SHOWN)}


def home_dispatch(host, operation, args, actor, now=None):
    if not isinstance(args, dict) or set(args) != HOME_FIELDS[operation]:
        raise Rejected('Unsupported suggestion fields')
    return suggestions(host, actor, args['projectId'], args['profileId'], now)
