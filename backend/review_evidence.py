"""Bounded, immutable owner evidence for automatic review. No new permissions."""
import hashlib
import json
import re
import sqlite3

from dervo_connections import Rejected

# 17.1 (#42): the limits were 4 KiB for the request and 16 KiB for every answer of the run together, and anything clipped
# fails review (require_complete), so long Home requests and long runs were never reviewed. Now:
# - REQUEST_BYTES is the most a coding request or a message added to a running task can be (conversation_coding's
#   coding_request and CodingTasks' task_create both refuse more), so a request Dervo accepted is never clipped.
#   When the run's instruction is that same message it isn't copied a second time (run_instruction same_as 'request').
# - ANSWER_BYTES covers every answer of the run together. One answer is cut at 65,536 characters when it's recorded, so
#   the longest single English answer fits whole. More than that, together, still fails review with the reason.
# - RECORD_BYTES bounds the whole record as JSON: both of the above, four checks, 20 KB of guidance and 16 notices.
REQUEST_BYTES = 32768
ANSWER_BYTES = 65536
RECORD_BYTES = 196608
SAME_AS_REQUEST = {'same_as': 'request'}


def excerpt(value, limit):
    raw = (value if isinstance(value, str) else '').encode('utf-8')
    return dict(text=raw[:limit].decode('utf-8', errors='ignore'), truncated=len(raw) > limit,
                original_bytes=len(raw))


def require_complete(record):
    """A model verdict can't authorize review of an answer it wasn't given."""
    answers = record.get('answers') or []
    if (record.get('answers_unavailable') or record.get('answers_omitted')
            or not answers or record.get('answer_count') != len(answers)
            or not any(item.get('text', '').strip() for item in answers)
            or any(item.get('truncated') for item in answers)):
        raise Rejected('The complete owner answer is unavailable or too large for automatic review. The owner result is preserved.')
    for key in ('request', 'run_instruction'):
        value = record.get(key) or {}
        if key == 'run_instruction' and value == SAME_AS_REQUEST:
            continue  # the request itself, checked just above
        if value.get('truncated') or not value.get('text', '').strip():
            raise Rejected('The complete owner request is unavailable or too large for automatic review. The owner result is preserved.')
    if record.get('checks_status') != 'recorded' or record.get('checks_omitted'):
        raise Rejected('The current owner check history is unavailable or too large for automatic review. The owner result is preserved.')
    notices = record.get('host_notices')
    if not isinstance(notices, dict) or notices.get('status') != 'recorded' or not isinstance(notices.get('notices'), list):
        raise Rejected('The delivered host notice history is unavailable. Request a fresh review. The owner result is preserved.')


def host_notices(coding, task, run):
    """Project only delivered read verification metadata, never file contents."""
    error = 'The delivered host notice history is unavailable or too large for automatic review. The owner result is preserved.'
    tokens = task.get('tokens')
    if not isinstance(tokens, list) or any(not isinstance(token, dict) for token in tokens):
        raise Rejected(error)
    reads = []
    for token in tokens:
        if token.get('tool') != 'dervo_read_file':
            continue
        if not token.get('run_id'):
            raise Rejected(error)
        if token['run_id'] == run['id']:
            reads.append(token)
    if len(reads) > 128:
        raise Rejected(error)
    notices, seen = [], set()
    for token in reads:
        identity = token.get('token')
        if not isinstance(identity, str) or not identity or identity in seen or token.get('state') != 'delivered':
            raise Rejected(error)
        seen.add(identity)
        try:
            row = coding.store.db.execute('SELECT document FROM coding_tool_audit WHERE task_id=? AND token=?',
                                          (task['id'], identity)).fetchone()
            audit = json.loads(row[0]) if row else None
            response = audit['response']
            if (audit['token'] != identity or type(response['success']) is not bool
                    or token.get('success') is not response['success'] or not isinstance(response['text'], str)):
                raise ValueError('Invalid audit binding')
            if not response['success']:
                continue
            result = json.loads(response['text'])
            if not isinstance(result, dict):
                raise ValueError('Invalid read response')
            if 'verification_required' not in result:
                continue
            notice, path, digest = result['verification_required'], token.get('path'), result.get('sha256')
            if (not isinstance(notice, str) or not notice.strip() or len(notice.encode()) > 512
                    or not isinstance(path, str) or audit['arguments'].get('path') != path
                    or path not in coding.collaboration.evidence_scope(task)
                    or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                    or audit['snapshot'].get('sha256') != digest):
                raise ValueError('Invalid host notice provenance')
            notices.append(dict(source='host_tool_response', field='verification_required',
                                tool='dervo_read_file', run_id=run['id'], path=path, sha256=digest,
                                response_sha256=hashlib.sha256(response['text'].encode()).hexdigest(),
                                delivery='delivered', message=notice))
        except (KeyError, TypeError, ValueError, AttributeError, sqlite3.Error):
            raise Rejected(error) from None
        if len(notices) > 16:
            raise Rejected(error)
    return dict(status='recorded', reads_checked=len(reads), notices=notices)


def shared_binding(store, task):
    current = store.binding(**task['context'])
    if current.get('visibility') != 'shared' or current.get('category') not in ('personal', 'work') or any(
            current.get(k) != task['binding'].get(k)
            for k in ('profile_id', 'provider', 'identity', 'model', 'cwd', 'visibility')):
        raise Rejected('Review evidence is unavailable after the owner account or sharing scope changed.')
    return current


def guidance(coding, task, agent, run):
    binding = shared_binding(coding.store, task)
    if agent['project_id'] != binding['project_id'] or agent['profile_id'] != binding['profile_id']:
        return dict(status='unavailable', reason='The reviewer uses a different project or account. No project guidance is shared.')
    snapshot = run.get('knowledge_snapshot')
    if not isinstance(snapshot, dict):
        return dict(status='unavailable', reason='No saved guidance record exists for this owner run.')
    if snapshot.get('project_id') != binding['project_id'] or snapshot.get('profile_id') != binding['profile_id']:
        raise Rejected('The owner guidance snapshot belongs to another project or account.')
    rules = snapshot.get('rules')
    if not isinstance(rules, list) or len(rules) > 12:
        raise Rejected('The saved owner guidance record is invalid.')
    for rule in rules:
        if not isinstance(rule, dict) or 'source' not in rule:
            raise Rejected('The saved owner guidance source is invalid.')
        coding.knowledge.source(rule['source'], binding, task['context']['actor'])
    record = dict(status='recorded', project_id=binding['project_id'], profile_id=binding['profile_id'],
                  captured_at=snapshot.get('captured_at'), omitted=snapshot.get('omitted'), rules=rules)
    if len(json.dumps(record, ensure_ascii=False).encode()) > 20000:
        raise Rejected('The saved owner guidance exceeds the review evidence limit.')
    return record


def capture(coding, task, agent):
    runs = task.get('runs') or []
    run = runs[-1] if runs else {}
    if run.get('state') != 'completed' or not run.get('id'):
        raise Rejected('Automatic review needs the latest completed owner run.')
    messages = [m for m in task.get('messages', []) if m.get('role') == 'assistant' and m.get('run_id') == run['id']]
    remaining, answers = ANSWER_BYTES, []
    for message in messages:
        item = excerpt(message.get('text'), remaining)
        remaining -= len(item['text'].encode())
        answers.append(dict(item_id=message.get('item_id'), **item))
        if not remaining:
            break
    attempts = coding.evidence.read(task['id'], **task['context'])['attempts']
    prior = run.get('prior_attempts')
    valid_prior = type(prior) is int and 0 <= prior <= len(attempts)
    current = attempts[prior:] if valid_prior else []
    checks = []
    for attempt in current[-4:]:
        test = attempt.get('test') or {}
        checks.append(dict(number=attempt['number'], kind=attempt.get('kind'), state=attempt['state'],
                           blocker=excerpt(attempt.get('blocker'), 512),
                           test={k: test.get(k) for k in ('exit_status', 'stopped', 'argv')},
                           stdout=excerpt(test.get('stdout'), 1024), stderr=excerpt(test.get('stderr'), 1024),
                           diff=excerpt('\n'.join(f'--- {p}\n{d}' for p, d in (attempt.get('diff') or {}).items() if d), 2048),
                           # A full-access run's changes come from git (native_evidence.finish). Files it changed that
                           # Dervo couldn't record aren't in the snapshot, so the reviewer is told which and why.
                           **({'unrecorded': excerpt('\n'.join(attempt['unrecorded']), 1024)} if attempt.get('unrecorded') else {})))
    user_messages = [m for m in task.get('messages', []) if m.get('role') == 'user']
    asked = next((m for m in reversed(user_messages) if m.get('source') != 'independent_review'), None)
    request = (asked or {}).get('text', '')
    latest = user_messages[-1] if user_messages else None
    instruction = (dict(SAME_AS_REQUEST) if latest is not None and latest is asked
                   else excerpt(latest.get('text') if latest else '', REQUEST_BYTES))
    record = dict(schema='dervo-review-evidence-v1', task_id=task['id'], run_id=run['id'], run_state=run['state'],
                  request=excerpt(request, REQUEST_BYTES), run_instruction=instruction,
                  answers=answers, answer_count=len(messages),
                  answers_unavailable=not messages, answers_omitted=len(messages)-len(answers),
                  checks_status='recorded' if valid_prior else 'unavailable', prior_attempts=prior,
                  checks=checks, checks_omitted=max(0, len(current)-len(checks)),
                  guidance=guidance(coding, task, agent, run), host_notices=host_notices(coding, task, run))
    # Escape-heavy text must still stay below the provider transport limit.
    if len(json.dumps(record, ensure_ascii=False).encode()) > RECORD_BYTES:
        raise Rejected('The owner review evidence exceeds the delivery limit.')
    require_complete(record)
    return record


def validate_delivery(coding, packet, actor):
    record = packet.get('review_evidence')
    if record is None:
        if packet.get('kind') == 'review' and packet.get('phase'):
            raise Rejected('This saved automatic review has no owner evidence record. Request a fresh review. No review context was sent.')
        return
    require_complete(record)
    task = coding.read(packet['owner_task_id'], dict(project_id=packet['source']['project_id'],
                       thread_id=packet['source']['thread_id'], actor=actor))
    run = (task.get('runs') or [{}])[-1]
    if run.get('id') != record['run_id'] or run.get('state') != 'completed':
        raise Rejected('The owner run changed before review evidence could be delivered.')
    shared_binding(coding.store, task)
    if host_notices(coding, task, run) != record['host_notices']:
        raise Rejected('The delivered host notice record changed before review delivery. Request a fresh review.')
    if record['guidance']['status'] == 'recorded':
        destination = packet['destination']
        binding = coding.store.binding(destination['thread_id'], destination['project_id'], actor=task['context']['actor'])
        if binding['visibility'] != 'shared' or any(binding.get(k) != destination.get(k) for k in ('profile_id', 'provider', 'model')):
            raise Rejected('The reviewer account or sharing scope changed before guidance delivery.')
        current = guidance(coding, task, destination, run)
        if current != record['guidance']:
            raise Rejected('The saved owner guidance changed before review delivery.')
