"""Clear tool calls Dervo still lists as running in a run it already lost, so Update and restart can go ahead.

When Dervo quits during a run, the next start marks the task disconnected but leaves its native tool calls (Bash,
Read, web search and so on) at "running". The version switch counts any running tool call as live work, so it says
"Wait for the checks in <thread> to finish" forever, and Mark as stopped can't reach them.

This only changes those tool calls, to "cancelled", which is what Dervo does itself when a run ends normally
(coding_integration: native tokens still running at the terminal event). The tasks stay disconnected, so each thread
still says the run was lost and you can check the folder and mark it stopped as usual. Nothing else changes.

    python3 clear_stuck_checks.py            # dry run: lists what it would change
    python3 clear_stuck_checks.py --apply    # Dervo must be quit; saves the old records first
    python3 clear_stuck_checks.py --restore <saved .json>

--data <folder> picks another workspace (a copy, for testing).
"""
import argparse
import fcntl
import json
import os
import sqlite3
import sys
import time

WORKSPACE = os.path.expanduser('~/Library/Application Support/local.dervo.connected')
# Outside the workspace, so the version switch's own backup doesn't copy these around.
SAVED = os.path.expanduser('~/Library/Application Support/Dervo/Stuck check fixes')
LIVE = ('executing', 'running', 'queued', 'delivering')  # project_lifecycle.LIVE_TOKENS


def live(token):
    return token.get('state') in LIVE or bool(token.get('job_id') and not token.get('job_done'))


def names(db):
    return {row[0]: row[1] for row in db.execute('SELECT id, name FROM local_channels')}


def scan(db):
    """[(task_id, old_document, task, [stuck tokens], [other live tokens])] for every task with a live token."""
    found = []
    for task_id, document in db.execute('SELECT id, document FROM coding_tasks ORDER BY rowid'):
        task = json.loads(document)
        tokens = [token for token in task.get('tokens', []) if live(token)]
        if not tokens:
            continue
        # Only a lost run's native tool calls. A working task, or a check job the host polls, is left alone.
        stuck = [token for token in tokens if task.get('state') == 'disconnected'
                 and token.get('native') and token.get('state') == 'running']
        found.append((task_id, document, task, stuck, [token for token in tokens if token not in stuck]))
    return found


def describe(db, found):
    threads = names(db)
    for task_id, _, task, stuck, other in found:
        thread = threads.get((task.get('context') or {}).get('thread_id')) or 'an unnamed thread'
        print(f'  {thread}  (task {task_id[:8]}, {task.get("state")})')
        for token in stuck:
            started = time.strftime('%d %b %H:%M', time.localtime(token.get('started_at') or 0))
            print(f'    will clear: {token.get("tool", "").replace("native:", "")} "{token.get("command") or token.get("path") or ""}", started {started}')
        for token in other:
            print(f'    left alone: {token.get("tool")} ({token.get("state")}). This may really be running.')


def lock(data):
    """Dervo holds this lock while it's open (release_handoff.WorkspaceLease)."""
    fd = os.open(os.path.join(data, 'conversations.sqlite3.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit('Dervo is still open. Quit it with Command-Q, then run this again. Nothing was changed.')
    return fd


def apply(db, data):
    lock(data)
    db.execute('BEGIN IMMEDIATE')
    found = [item for item in scan(db) if item[3]]
    if not found:
        db.rollback()
        print('Nothing to clear. Nothing was changed.')
        return
    describe(db, found)
    now = round(time.time(), 3)
    record = {'workspace': data, 'saved_at': now, 'tasks': []}
    for task_id, document, task, stuck, _ in found:
        for token in stuck:
            token.update(state='cancelled', ended_at=now)
        after = json.dumps(task)
        record['tasks'].append({'id': task_id, 'before': document, 'after': after})
    os.makedirs(SAVED, mode=0o700, exist_ok=True)
    path = os.path.join(SAVED, time.strftime('%Y%m%d-%H%M%S') + '.json')
    with open(path, 'x') as stream:
        json.dump(record, stream)
    os.chmod(path, 0o600)
    for task in record['tasks']:
        db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (task['after'], task['id']))
    db.commit()
    print(f'\nCleared {sum(len(item[3]) for item in found)} tool call(s) in {len(found)} task(s).')
    print('Old records saved to: ' + path)
    print('To undo: python3 ' + os.path.basename(__file__) + ' --restore "' + path + '"')


def restore(db, data, path):
    lock(data)
    with open(path) as stream:
        record = json.load(stream)
    if os.path.realpath(record['workspace']) != os.path.realpath(data):
        sys.exit('That file was saved from another workspace: ' + record['workspace'] + '. Nothing was changed.')
    db.execute('BEGIN IMMEDIATE')
    for task in record['tasks']:
        row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()
        if not row or row[0] != task['after']:
            db.rollback()
            sys.exit(f'Task {task["id"][:8]} has changed since the fix, so restoring it would lose newer work. Nothing was changed.')
    for task in record['tasks']:
        db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (task['before'], task['id']))
    db.commit()
    print(f'Restored {len(record["tasks"])} task(s).')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--data', default=WORKSPACE)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--apply', action='store_true')
    group.add_argument('--restore', metavar='SAVED_JSON')
    args = parser.parse_args()
    data = os.path.expanduser(args.data)
    path = os.path.join(data, 'conversations.sqlite3')
    if not os.path.isfile(path):
        sys.exit('No Dervo data at ' + path + '. Nothing was changed.')
    print('Workspace: ' + data + '\n')
    if args.apply or args.restore:
        db = sqlite3.connect(path, isolation_level=None, timeout=5)
        apply(db, data) if args.apply else restore(db, data, args.restore)
        return
    db = sqlite3.connect('file:' + path + '?mode=ro', uri=True)
    found = scan(db)
    if not found:
        print('No tool calls or checks are listed as running. Nothing to clear.')
        return
    describe(db, found)
    if any(item[3] for item in found):
        print('\nDry run. Nothing was changed. Quit Dervo, then run this again with --apply.')


if __name__ == '__main__':
    main()
