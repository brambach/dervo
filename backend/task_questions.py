"""Durable questions from a conversation's owner.

An answer never gets more access than the task that asked (security review H1, 23 Sep). Its words are the agent's
question plus the option you picked, so a click on an option isn't you asking for a shell. Steered into a running task,
it joins that run's access. Sent after the task finished, it starts as a Home job that's clicked only when the asking
task had full access, so an answer to a Home job nobody clicked runs without it and shows Home's usual "Say go" notice.
An option can't carry a grant either (validate)."""
import json
import re
import time
import uuid

from dervo_connections import Rejected
import secret_scan
import voice

TOOL = 'dervo_ask_user'
PREFIX = 'question-answer-'
MAX_PENDING, MAX_QUESTIONS, ANSWER_BYTES = 3, 12, 8192
BINDING_KEYS = ('profile_id', 'provider', 'identity', 'method', 'model', 'cwd', 'visibility', 'owner')
PROMPT = ('Use dervo_ask_user for a missing requirement or preference only the user can answer. '
          'Give a stable question_key, one short question and zero to three concise choices. '
          'The host saves the question and returns immediately. Continue independent work while it waits. '
          'Never treat the receipt as an answer. This tool cannot request or grant permissions, launch commands, '
          'change accounts or approve work. Do not ask for secrets.\n')
DETAILS = {
    'saved': 'Answer saved. Delivery is being recorded.',
    'sent': 'Sent to the source task. Dervo can’t confirm that the agent read it.',
    'queued': 'Answer saved. It waits for the source task to finish.',
    'not_sent': 'Answer saved but not sent. Copy it into a new message if it’s still relevant.',
    'unknown': 'Answer saved. Delivery couldn’t be confirmed, so Dervo won’t send it again automatically.',
    'cancelled': 'The queued answer was cancelled. Nothing else was sent.',
}


class NotSent(Rejected):
    """The exact source couldn't take input before any provider write was attempted."""


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_questions ('
               'id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, task_id TEXT NOT NULL, '
               'question_key TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(task_id, question_key))')


def offered(task):
    return task.get('origin') == 'conversation' and not task.get('role') and not task.get('review_phase')


def _text(value, name, chars, *, empty=False):
    if type(value) is not str or '\x00' in value or len(value) > chars:
        raise Rejected(f'{name} needs plain text within {chars} characters.')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise Rejected(f'{name} contains invalid Unicode.') from None
    if size > chars * 4 or (not empty and not value.strip()):
        raise Rejected(f'{name} is empty or too large.')
    if secret_scan.redact(value) != value:
        raise Rejected(f'Remove credentials from the {name.lower()}.')
    clean = voice.plain(value).strip()
    if len(clean) > chars or len(clean.encode()) > chars * 4:
        raise Rejected(f'Shorten the {name.lower()} to {chars} characters.')
    return clean


def validate(args):
    if type(args) is not dict or set(args) != {'question_key', 'ask', 'options'}:
        raise Rejected('Unsupported question fields.')
    key = args['question_key']
    if type(key) is not str or not re.fullmatch('[a-zA-Z0-9_-]{1,64}', key):
        raise Rejected('Use a stable question key with up to 64 letters, digits, underscores or hyphens.')
    ask = _text(args['ask'], 'Question', 200)
    options = args['options']
    if type(options) is not list or len(options) > 3:
        raise Rejected('A question can have zero to three options.')
    clean = []
    for option in options:
        if type(option) is not dict or set(option) != {'label', 'detail'}:
            raise Rejected('A question option only has a label and detail. It cannot grant access or run a command.')
        clean.append({'label': _text(option['label'], 'Option label', 80),
                      'detail': _text(option['detail'], 'Option detail', 120, empty=True) or None})
    if len({option['label'] for option in clean}) != len(clean):
        raise Rejected('Give each question option a different label.')
    return {'question_key': key, 'ask': ask, 'options': clean}


def access_stamp(coding, pid):
    from thread_fanout import access_stamp as stamp
    return stamp(coding.store.db, pid)


def _rows(coding, task):
    return [json.loads(row[0]) for row in coding.store.db.execute(
        'SELECT document FROM task_questions WHERE task_id=? ORDER BY rowid', (task['id'],))]


def _save(coding, row):
    with coding.store.db:
        coding.store.db.execute('UPDATE task_questions SET document=? WHERE id=?', (json.dumps(row), row['id']))
        found = coding.store.db.execute('SELECT thread_id FROM task_questions WHERE id=?', (row['id'],)).fetchone()
        _moved(coding.store.db, found[0] if found else None)


def _moved(db, thread_id):
    """A question asked or answered changes its thread's row (state spine)."""
    if isinstance(thread_id, str):
        import conversation_activity
        conversation_activity.bump(db, thread_id)


def _scope(task, row):
    ctx = task['context']
    if (row['task_id'], row['project_id'], row['thread_id'], row['actor']) != (
            task['id'], ctx['project_id'], ctx['thread_id'], ctx['actor']):
        raise Rejected('This question belongs to another task or conversation.')


def problem(coding, task, row):
    return changed(coding, task, row) or ended(coding, task, row)


def changed(coding, task, row):
    """Why an answer can't go anywhere: the thread's account, model or project access moved on since the question."""
    _scope(task, row)
    ctx = task['context']
    binding = coding.store.binding(**ctx)
    if any(binding.get(key) != row['binding'].get(key) for key in BINDING_KEYS):
        return 'The source account, model or conversation changed. Ask a new question in the current conversation.'
    if access_stamp(coding, ctx['project_id']) != row['access_stamp']:
        return 'Project access changed after this question. Start a new message with the current permissions.'
    return None


def ended(coding, task, row):
    """Why the asking run can't take the answer any more. Beta.16.1 (Bryce, 26 Sep): the answer then goes in as the
    thread's next message instead (``carried``), still with the asking task's access and no more. The usual cause is a
    run that ended ``blocked``, as "Nothing to approve" does when Dervo couldn't record a file, while a question it
    asked near its end was still waiting."""
    ctx = task['context']
    run = (task.get('runs') or [{}])[-1]
    if run.get('id') != row['run_id']:
        return 'The source run was replaced. This question is kept for reference.'
    # Creation order, not mutable activity timestamps, identifies the latest task.
    newest = coding.store.db.execute("SELECT id FROM coding_tasks WHERE json_extract(document,'$.origin')='conversation' "
        "AND json_extract(document,'$.context.thread_id')=? AND json_extract(document,'$.context.project_id')=? "
        "AND json_extract(document,'$.context.actor')=? ORDER BY rowid DESC LIMIT 1",
        (ctx['thread_id'], ctx['project_id'], ctx['actor'])).fetchone()
    if newest and newest[0] != task['id']:
        return 'A newer task is in this conversation. Copy your answer into that task if it’s still relevant.'
    if task.get('state') == 'working' and run.get('state') == 'running':
        turn = coding.turns.get(task['id'])
        if turn is None or turn.closed or getattr(turn, 'stopping', False):
            return 'The source task is no longer accepting replies. This question is kept for reference.'
    elif task.get('state') not in ('answered', 'ready_for_review', 'completed') or run.get('state') != 'completed':
        return 'The source task stopped or its outcome is uncertain. Start a new message to continue.'
    return None


def view(coding, task, row):
    _scope(task, row)
    stuck = changed(coding, task, row) if row['state'] == 'pending' else None
    issue = stuck or (ended(coding, task, row) if row['state'] == 'pending' else None)
    delivery, detail = row.get('delivery'), row.get('detail')
    if delivery == 'queued':
        request = coding.conversation.find(row['thread_id'], PREFIX + row['id'])
        item = coding.conversation.queue.find(row['thread_id'], PREFIX + row['id'])
        if not request and item and item['state'] == 'failed':
            delivery, detail = 'not_sent', item.get('error') or DETAILS['not_sent']
        else:
            if request:
                record = coding.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (request['task_id'],)).fetchone()
                following = json.loads(record[0]) if record else {}
                run = (following.get('runs') or [{}])[-1]
                delivery = ('sent' if run.get('state') in ('running', 'completed') else
                            'unknown' if run.get('state') in ('failed', 'uncertain', 'interrupted') else 'saved')
                detail = DETAILS[delivery]
    return {'id': row['id'], 'taskId': task['id'], 'runId': row['run_id'], 'asker': row['asker'],
            'ask': row['ask'], 'options': row['options'], 'createdAt': row['created_at'],
            'state': 'stale' if issue else row['state'], 'answer': row.get('answer'),
            'answerClientId': row.get('answer_client_id'), 'delivery': delivery,
            'canAnswer': row['state'] == 'pending' and not issue, 'canSkip': row['state'] == 'pending',
            'canCarry': row['state'] == 'pending' and bool(issue) and not stuck, 'carried': bool(row.get('carried')),
            'detail': issue or detail or DETAILS.get(delivery)}


def annotate(coding, task):
    task['questions'] = [view(coding, task, row) for row in _rows(coding, task)]


def recover(coding):
    for (document,) in coding.store.db.execute('SELECT document FROM task_questions').fetchall():
        row = json.loads(document)
        task_row = coding.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (row['task_id'],)).fetchone()
        task = json.loads(task_row[0]) if task_row else {}
        unconfirmed = any(message.get('question_id') == row['id'] and message.get('steer') in ('sent', 'question_sending', 'question_unknown')
                          for message in task.get('messages', []))
        if row.get('delivery') == 'saved' or (row.get('delivery') == 'sent' and unconfirmed):
            row.update(delivery='unknown', detail=DETAILS['unknown'])
            _save(coding, row)


def capture(coding, task, token, args):
    content = validate(args)
    if not offered(task) or task['state'] != 'working':
        raise Rejected('Only the active conversation owner can ask the user a question.')
    fresh = coding.read(task['id'], task['context'])
    run = (fresh.get('runs') or [{}])[-1]
    if (fresh['state'] != 'working' or not offered(fresh) or run.get('state') != 'running'
            or token.get('run_id') != run.get('id') or token.get('session_id') != run.get('session_id')):
        raise Rejected('This question needs the source task’s current acknowledged run.')
    binding = coding.store.binding(**fresh['context'])
    if any(binding.get(key) != fresh['binding'].get(key) for key in BINDING_KEYS):
        raise Rejected('The source task’s account or model changed.')
    coding.require_grant(fresh)
    rows = _rows(coding, fresh)
    old = next((row for row in rows if row['question_key'] == content['question_key']), None)
    if old:
        if old['content'] != content or old['run_id'] != run['id']:
            raise Rejected('That question key already belongs to a different question or run.')
        return {'questionId': old['id'], 'state': old['state'], 'detail': 'Question already saved. Continue independent work.'}
    if len(rows) >= MAX_QUESTIONS or sum(row['state'] == 'pending' for row in rows) >= MAX_PENDING:
        raise Rejected('This task has reached its question limit. Continue independent work and keep remaining questions in your reply.')
    qid, ctx = uuid.uuid4().hex, fresh['context']
    row = {'id': qid, 'project_id': ctx['project_id'], 'thread_id': ctx['thread_id'], 'task_id': task['id'],
           'actor': ctx['actor'], 'run_id': run['id'], 'tool_token': token['token'], 'question_key': content['question_key'],
           'content': content, 'ask': content['ask'], 'asker': 'Claude' if binding['provider'] == 'claude' else 'Codex',
           'options': [{'id': uuid.uuid4().hex, **option, 'grant': None} for option in content['options']],
           'binding': {key: binding.get(key) for key in BINDING_KEYS}, 'access_stamp': access_stamp(coding, ctx['project_id']),
           'read_only': bool(fresh.get('read_only')), 'created_at': time.time(),
           'state': 'pending', 'answer': None, 'answer_client_id': None, 'delivery': None, 'detail': None}
    with coding.store.db:
        coding.store.db.execute('INSERT INTO task_questions VALUES (?,?,?,?,?,?)',
            (qid, ctx['project_id'], ctx['thread_id'], task['id'], row['question_key'], json.dumps(row)))
        _moved(coding.store.db, ctx['thread_id'])
    return {'questionId': qid, 'state': 'pending', 'detail': 'Question saved. Continue independent work while the user answers.'}


def normalize_answer(row, answer):
    if type(answer) is not dict:
        raise Rejected('Choose an option, write an answer or skip this question.')
    kind = answer.get('kind')
    if kind == 'skip' and set(answer) == {'kind'}:
        return {'kind': 'skip'}
    if kind == 'option' and set(answer) == {'kind', 'optionId'}:
        option = next((option for option in row['options'] if option['id'] == answer['optionId']), None)
        if option:
            return {'kind': 'option', 'optionId': option['id'], 'text': option['label']}
    if kind == 'text' and set(answer) == {'kind', 'text'} and type(answer['text']) is str:
        text = answer['text']
        try:
            valid = bool(text.strip()) and '\x00' not in text and len(text.encode()) <= ANSWER_BYTES
        except UnicodeError:
            valid = False
        if valid:
            return {'kind': 'text', 'text': text}
    raise Rejected('Choose a saved option or write an answer within 8192 UTF-8 bytes.')


def answer_text(row):
    where = 'asked before its run ended' if row.get('carried') else 'from this task'
    return f"Answer to question {row['id']} {where}:\n{row['ask']}\n\nMy answer:\n{row['answer']['text']}"


def delivery(coding, qid, state, detail=None):
    record = coding.store.db.execute('SELECT document FROM task_questions WHERE id=?', (qid,)).fetchone()
    if record:
        row = json.loads(record[0])
        if state == 'sent' and row.get('delivery') in ('unknown', 'cancelled', 'not_sent'):
            return  # a late or uncorrelated receipt cannot settle an uncertain send
        row.update(delivery=state, detail=detail or DETAILS[state])
        _save(coding, row)


def dispatch(coding, task, args):
    row = next((row for row in _rows(coding, task) if row['id'] == args['questionId']), None)
    if row is None:
        raise Rejected('This question is not part of the selected task.')
    _scope(task, row)
    client = args['clientId']
    if type(client) is not str or not 1 <= len(client) <= 128:
        raise Rejected('An answer needs a stable request ID.')
    answer = normalize_answer(row, args['answer'])
    if row['answer'] is not None:
        if row['answer_client_id'] != client or row['answer'] != answer:
            raise Rejected('This question already has a saved answer. It won’t be replaced or sent again.')
        return view(coding, task, row)
    carried = False
    if answer['kind'] != 'skip':
        coding.host.require_beta_access()
        issue = changed(coding, task, row)
        if issue:
            raise Rejected(issue)
        carried = ended(coding, task, row) is not None
        coding.require_grant(task)
    row.update(carried=carried)
    row.update(answer=answer, answer_client_id=client, state='skipped' if answer['kind'] == 'skip' else 'answered',
               delivery='not_sent' if answer['kind'] == 'skip' else 'saved',
               detail='Skipped. No reply was sent and no work started.' if answer['kind'] == 'skip' else DETAILS['saved'])
    _save(coding, row)  # immutable intent is durable before any transport or request starts
    if answer['kind'] == 'skip':
        return view(coding, task, row)
    request = {'clientId': PREFIX + row['id'], 'text': answer_text(row), 'queueIfBusy': True, **source_access(task),
               **({'readOnly': True} if row['read_only'] else {})}
    ctx = task['context']
    binding = coding.store.binding(**ctx)
    if task['state'] == 'working' and not carried:
        try:
            coding.steer(ctx['thread_id'], request['text'], request['clientId'], expected_task_id=task['id'],
                         expected_run_id=row['run_id'], question_id=row['id'])
            delivery(coding, row['id'], 'sent')
        except NotSent:
            try:
                coding.conversation.queue.add(request, ctx, binding, why='no_run')
                delivery(coding, row['id'], 'queued')
            except Exception:
                delivery(coding, row['id'], 'not_sent')
        except Exception:
            delivery(coding, row['id'], 'unknown')
    else:
        try:
            result = coding.conversation.request(request, ctx, binding)
            delivery(coding, row['id'], 'queued' if result.get('queued') or result.get('taskId') else 'not_sent')
        except Exception:
            # The normal request may already have committed a task before its reply
            # was lost. Never issue a second request to settle that uncertainty.
            delivery(coding, row['id'], 'unknown')
    current = next(item for item in _rows(coding, task) if item['id'] == row['id'])
    return view(coding, task, current)


def source_access(task):
    """The request fields that give an answer's own run the asking task's access and no more (see the module docstring)."""
    return {'origin': 'home', 'clicked': task.get('full_access') is True}


def request_question(coding, binding, client_id):
    if not client_id.startswith(PREFIX):
        return None
    record = coding.store.db.execute('SELECT document FROM task_questions WHERE id=?', (client_id[len(PREFIX):],)).fetchone()
    if record is None:
        raise Rejected('The saved question answer is unavailable. Start a new message.')
    row = json.loads(record[0])
    if row['thread_id'] != binding['id'] or row['project_id'] != binding['project_id']:
        raise Rejected('The question answer belongs to another conversation.')
    return row


def validate_launch(coding, binding, args):
    row = request_question(coding, binding, args['clientId'])
    if row is None:
        return None
    ctx = dict(project_id=row['project_id'], thread_id=row['thread_id'], actor=row['actor'])
    task = coding.read(row['task_id'], ctx, rebound=True)
    # A carried answer was taken after its run ended, so only a changed account or access stops it now.
    issue = changed(coding, task, row) or (None if row.get('carried') else ended(coding, task, row))
    if issue:
        raise Rejected(issue)
    if (row['state'] != 'answered' or row['delivery'] not in ('saved', 'queued')
            or args['text'] != answer_text(row) or bool(args.get('readOnly')) != row['read_only']):
        raise Rejected('This question answer is no longer waiting or its saved content changed.')
    # An answer queued before 23 Sep went out with no origin, as a message you typed. It can't start that way now.
    if {key: args.get(key) for key in ('origin', 'clicked')} != source_access(task):
        raise Rejected('This answer would start with different access than the task that asked. Send it again as a new message.')
    if task['state'] == 'working':
        raise Rejected('The answer is waiting for its source task to finish.')
    coding.host.require_beta_access()
    coding.require_grant(task)
    return {'why': 'Answer to the source task’s question. Its account stays selected.', 'switch_to': None}


def cancelled(coding, item):
    if not item['client_id'].startswith(PREFIX):
        return
    if coding.conversation.find(item['thread_id'], item['client_id']):
        return  # stale queue cleanup cannot cancel a request that already started
    qid = item['client_id'][len(PREFIX):]
    record = coding.store.db.execute('SELECT document FROM task_questions WHERE id=? AND thread_id=?', (qid, item['thread_id'])).fetchone()
    if record:
        delivery(coding, qid, 'cancelled')
