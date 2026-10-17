"""Mark runs Dervo lost track of as stopped, so a conversation isn't stuck.

Dervo has a Mark as stopped button for this now. Keep this for a workspace you can't open, for example when the app
refuses to start. Quit Dervo first: it holds the lock while it runs.
"""
import fcntl, json, os, sqlite3, sys, time

def workspaces():
    """Where a Dervo install keeps its data: the app's own folder first, then any preview data beside a build."""
    found = []
    override = os.environ.get('DERVO_DATA')
    if override:
        found.append(override)
    found.append(os.path.expanduser('~/Library/Application Support/local.dervo.connected'))
    releases = os.path.expanduser('~/Dervo Releases')
    if os.path.isdir(releases):
        for name in sorted(os.listdir(releases), reverse=True):
            found.append(os.path.join(releases, name, 'preview-data'))
    seen, ordered = set(), []
    for path in found:
        if path not in seen and os.path.isfile(os.path.join(path, 'conversations.sqlite3')):
            seen.add(path)
            ordered.append(path)
    return ordered


spaces = workspaces()
if not spaces:
    print('Could not find any Dervo data on this Mac. Nothing was changed.')
    sys.exit(1)
data = spaces[0]
print('Workspace: ' + data + ('' if len(spaces) == 1 else '  (set DERVO_DATA to choose another)'))
db_path = os.path.join(data, 'conversations.sqlite3')
lock_path = db_path + '.lock'
STUCK = ('working', 'disconnected')
prompt = not os.environ.get('DERVO_NO_PROMPT')

def done(message, code=0):
    print('\n' + message)
    if prompt:
        input('\nPress Return to close this window.')
    sys.exit(code)

if not os.path.isfile(db_path):
    done('Could not find your Dervo data at:\n  ' + db_path + '\nNothing was changed.', 1)

# Dervo holds this lock while it is open.
lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    done('Dervo is still open. Quit Dervo (Dervo menu > Quit), then double-click this file again.\nNothing was changed.', 1)

db = sqlite3.connect(db_path)
rows = [json.loads(document) for (document,) in db.execute('SELECT document FROM coding_tasks ORDER BY rowid')]
rows = [task for task in rows if task.get('state') in STUCK]
if not rows:
    done('No stuck tasks found. Nothing was changed.\nOpen Dervo and try Update and restart.')

print('Stuck tasks found:\n')
for task in rows:
    error = (task.get('error') or 'No message').split('. ')[0]
    print('  - ' + task['id'][:8] + ' (' + task['state'] + '): ' + error[:100])
print('\nThese runs have already ended. Their file changes stay in your project folder.')
if prompt and input('\nMark them as stopped? Type y and press Return: ').strip().lower() != 'y':
    done('Nothing was changed.')

backup = os.path.join(data, 'conversations.before-unstick-' + time.strftime('%Y%m%d-%H%M%S') + '.sqlite3')
target = sqlite3.connect(backup)
db.backup(target)
target.close()
print('\nBackup saved: ' + backup)

now = round(time.time(), 3)
with db:
    for task in rows:
        task['state'] = 'blocked'
        task['error'] = 'Stopped. This run was lost and marked as stopped by hand so Dervo could update. ' + (task.get('error') or '')
        if task.get('runs') and task['runs'][-1].get('state') in ('running', 'uncertain'):
            task['runs'][-1]['state'] = 'interrupted'
        for token in task.get('tokens', []):
            if token.get('state') not in ('delivered', 'cancelled'):
                token.update(state='cancelled', ended_at=now)
        db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(task), task['id']))
db.close()
done('Done. ' + str(len(rows)) + ' task(s) marked as stopped.\nOpen Dervo and press Update and restart.')
