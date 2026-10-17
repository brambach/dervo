"""Push the project's current branch to GitHub, once you've confirmed what goes.

Design: PLAN-github-push.md (19 September 2026). Dervo counts the commits on the current branch that its upstream doesn't
have, lists them for a confirm that shows the full remote URL, and pushes with plain ``git push <remote> <branch>``:
never ``--force``, never ``--tags``, never another branch. Dervo never pulls, merges or rewrites history, and never
pushes on its own.

The push runs on one worker thread and is polled (``push_status``), because the desktop bridge stops a host request
that takes over ten seconds. A push whose process times out or dies is *unknown*, not failed: ``push_recount`` asks the
remote what it has (``git ls-remote``) and settles it before another push is allowed. A host restart marks a running
push unknown the same way.

Credentials are git's own (osxkeychain over HTTPS on a Mac). Dervo passes only what task_git._env passes, so it never
sees a token, and nothing that looks like one is ever stored in a record or returned to the screen.

The URL the confirm shows is the one git pushes to: ``git remote get-url --push``, which applies ``pushurl`` and
``pushInsteadOf``. A remote with more than one push URL, or one that isn't HTTPS or SSH, is refused. The project's own
``.git/config`` can be written by anything that runs project code, so the push and ``ls-remote`` never run a program it
names: ``hardening`` empties the credential helper list (then puts back only your system and global helpers, such as
osxkeychain or gh), clears ``core.askpass``, pins ``core.sshCommand`` to ``ssh``, turns off ``push.gpgSign`` and
``core.fsmonitor``, and allows only the HTTPS and SSH transports (so no ``ext::``, local ``receivepack`` or
``remote.<name>.vcs`` helper). Hooks are already off (task_git._git).

Approval before the commit stays where it is (coding_integration.commit_record). ``thread_name_for`` is the helper that
step will use to give commits the conversation's name as their subject.
"""
import json
import re

import secret_scan
import secrets
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import task_git
from dervo_connections import Rejected

FIELDS = {
    'push_preview': {'projectId'},
    'push_start': {'projectId', 'confirmToken', 'setUpstream'},
    'push_status': {'projectId'},
    'push_recount': {'projectId'},
}

CONFIRM_SECONDS = 600
NO_UPSTREAM_CAP = 200
RESTARTED = 'Dervo restarted during this push.'
LOST = 'Dervo lost track of this push.'
NO_REMOTE = 'This project has no GitHub remote.'
DETACHED = "This project isn't on a branch. Check out a branch in Terminal first."
RECONFIRM = 'Reload and confirm again.'
REMOTE_CHANGED = 'The remote changed since you confirmed. ' + RECONFIRM
BRANCH_CHANGED = 'The branch changed since you confirmed. ' + RECONFIRM
HEAD_CHANGED = 'The branch has new commits since you confirmed. ' + RECONFIRM
BEHIND = "GitHub has commits you don't have. Pull in Terminal first."
SIGN_IN = "Git couldn't sign in to GitHub. Push once from Terminal so your Mac saves the login, then try again."
SSH = "This remote uses SSH. Dervo can't reach your SSH agent yet. Push from Terminal, or switch the remote to HTTPS."
NO_CONNECTION = 'No connection to GitHub.'
CONFIRMED = 'Confirmed on GitHub.'
NOT_PUSHED = "GitHub doesn't have it. You can push again."
SETTLED_STATES = ('pushed', 'failed', 'not_pushed')
MANY_URLS = 'This remote pushes to more than one URL. Push from Terminal.'
UNSAFE_URL = 'Dervo pushes over HTTPS or SSH only. Push this remote from Terminal.'
ODD_NAME = "Dervo can't push to a remote whose name starts with a dash."
PROTOCOLS = ('https', 'ssh')  # the transports a push may use; tests add 'file' for their local bare repository
_SCP_LIKE = re.compile(r'^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:')

def scrub(text):
    """Text safe to keep: URL passwords and token-shaped words become ``[redacted]``. The shapes live in secret_scan,
    shared with the check that keeps secrets out of project memory, skills and lessons."""
    return secret_scan.redact(text)


def _run(cwd, *args, run=subprocess.run, timeout=task_git.TIMEOUT):
    return task_git._git(cwd, *args, run=run, timeout=timeout)


def _line(result):
    return (result.stdout or '').strip()


def protocol_of(url):
    """The transport git would use for ``url``: https, ssh, file, or other."""
    if url.startswith('https://'):
        return 'https'
    if url.startswith('ssh://') or _SCP_LIKE.match(url):
        return 'ssh'
    if url.startswith('file://') or url.startswith('/') or url.startswith('.'):
        return 'file'
    return 'other'


def hardening(cwd, *, run=subprocess.run):
    """``-c`` pairs that keep the repository's own config from running a program during a push or ``ls-remote``.

    Command-line config is read last, so an empty ``credential.helper`` drops every helper the system, global and
    repository files named, including URL-scoped ones. Then the system and global helpers go back in their own order,
    read with ``--system``/``--global`` so the repository file is never consulted."""
    pairs = ['core.askpass=', 'core.sshCommand=ssh', 'core.fsmonitor=false', 'push.gpgSign=false', 'protocol.allow=never',
             *[f'protocol.{name}.allow=always' for name in PROTOCOLS], 'credential.helper=']
    for scope in ('--system', '--global'):
        try:
            found = _run(cwd, 'config', scope, '--includes', '--get-regexp', r'^credential\..*helper$', run=run)
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (found.stdout or '').splitlines() if found.returncode == 0 else []:
            key, _, value = line.partition(' ')
            if key:
                pairs.append(f'{key}={value}')
    return [arg for pair in pairs for arg in ('-c', pair)]


def remote_state(cwd, *, run=subprocess.run):
    """What a push would do from here: the branch, its upstream, the remote and its full URL, the counts and HEAD.

    ``git=False`` when the folder isn't in a repository. ``branch=None`` on a detached HEAD. ``remote=None`` when there
    is no remote at all. ``upstream=None`` when the branch has none; then ``remote`` is ``origin`` if it exists and
    ``ahead`` counts the branch's commits (capped) since nothing on GitHub is known.
    """
    state = dict(git=True, branch=None, upstream=None, remote=None, url=None, ahead=0, behind=0, head=None, detail=None)
    try:
        if task_git.repo_root(cwd, run=run) is None:
            return dict(state, git=False, detail=task_git.NOT_A_REPO)
        head = _run(cwd, 'rev-parse', 'HEAD', run=run)
        state['head'] = _line(head) if head.returncode == 0 else None
        branch = _run(cwd, 'rev-parse', '--abbrev-ref', 'HEAD', run=run)
        name = _line(branch) if branch.returncode == 0 else ''
        if not name or name == 'HEAD':
            return dict(state, detail=DETACHED)
        state['branch'] = name
        remotes = _run(cwd, 'remote', run=run)
        names = _line(remotes).split() if remotes.returncode == 0 else []
        if not names:
            return dict(state, detail=NO_REMOTE)
        upstream = _run(cwd, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}', run=run)
        if upstream.returncode == 0 and _line(upstream):
            state['upstream'] = _line(upstream)
            configured = _run(cwd, 'config', f'branch.{name}.remote', run=run)
            state['remote'] = _line(configured) if configured.returncode == 0 and _line(configured) else state['upstream'].split('/', 1)[0]
            ahead = _run(cwd, 'rev-list', '--count', '@{upstream}..HEAD', run=run)
            behind = _run(cwd, 'rev-list', '--count', 'HEAD..@{upstream}', run=run)
            state['ahead'] = int(_line(ahead) or 0) if ahead.returncode == 0 else 0
            state['behind'] = int(_line(behind) or 0) if behind.returncode == 0 else 0
        else:
            state['remote'] = 'origin' if 'origin' in names else names[0]
            count = _run(cwd, 'rev-list', '--count', f'--max-count={NO_UPSTREAM_CAP}', 'HEAD', run=run)
            state['ahead'] = int(_line(count) or 0) if count.returncode == 0 else 0
            state['detail'] = f"{name} isn't on GitHub yet. Pushing it sets {state['remote']}/{name} as its upstream."
        if state['remote'].startswith('-'):
            return dict(state, detail=ODD_NAME)
        # The URL git will really push to: pushurl and pushInsteadOf applied. The fetch URL can differ.
        url = _run(cwd, 'remote', 'get-url', '--push', '--all', '--', state['remote'], run=run)
        urls = _line(url).splitlines() if url.returncode == 0 else []
        if not urls:
            return dict(state, detail=NO_REMOTE)
        if len(urls) > 1:
            return dict(state, detail=MANY_URLS)
        if protocol_of(urls[0]) not in PROTOCOLS:
            return dict(state, detail=UNSAFE_URL)
        state['url'] = scrub(urls[0])
    except (OSError, subprocess.SubprocessError):
        return dict(state, detail='Git could not run.')
    return state


def _task_index(db):
    """Commit sha to the Dervo task that made it (its commit or its undo), with the conversation's name."""
    index = {}
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        return index
    for task_id, document in db.execute('SELECT id, document FROM coding_tasks').fetchall():
        try:
            task = json.loads(document)
        except ValueError:
            continue
        record = task.get('commit') or {}
        thread_id = (task.get('context') or {}).get('thread_id')
        for sha in (record.get('sha'), (record.get('reverted') or {}).get('sha')):
            if isinstance(sha, str) and sha:
                index[sha] = dict(task_id=task_id, thread_id=thread_id, thread_name=thread_name_for(db, task))
    return index


def pending_commits(db, cwd, state, *, run=subprocess.run):
    """The commits a push would send, newest first: sha, short id, subject, and the Dervo conversation that made each
    one or ``made_outside_dervo``. Never a commit body."""
    if not state.get('git') or not state.get('branch') or not state.get('ahead'):
        return []
    span = ['@{upstream}..HEAD'] if state.get('upstream') else [f'--max-count={NO_UPSTREAM_CAP}', 'HEAD']
    try:
        log = _run(cwd, 'log', '--format=%H%x00%s', *span, run=run)
    except (OSError, subprocess.SubprocessError):
        return []
    if log.returncode != 0:
        return []
    index = _task_index(db)
    commits = []
    for line in (log.stdout or '').splitlines():
        sha, _, subject = line.partition('\x00')
        if not sha:
            continue
        made = index.get(sha)
        commits.append(dict(sha=sha, short=sha[:7], subject=scrub(subject)[:200],
                            thread_id=made['thread_id'] if made else None, thread_name=made['thread_name'] if made else None,
                            task_id=made['task_id'] if made else None, made_outside_dervo=made is None))
    return commits


def thread_name_for(db, task):
    """The conversation's name for ``task``, from local_channels, or None. The commit subject helper
    (task_git.subject) treats the automatic placeholder as no name."""
    thread_id = (task.get('context') or {}).get('thread_id') if isinstance(task, dict) else None
    if not isinstance(thread_id, str):
        return None
    row = db.execute('SELECT name FROM local_channels WHERE id=?', (thread_id,)).fetchone()
    return row[0] if row and isinstance(row[0], str) and row[0].strip() else None


def failure_reason(stderr):
    """A plain sentence for git's stderr, never a credential."""
    text = stderr or ''
    if 'Permission denied (publickey)' in text or 'publickey' in text.lower():
        return SSH
    if 'could not read Username' in text or 'Authentication failed' in text or 'Permission denied' in text or 'terminal prompts disabled' in text:
        return SIGN_IN
    if 'rejected' in text and ('fetch first' in text or 'non-fast-forward' in text or 'stale info' in text):
        return BEHIND
    if 'Could not resolve host' in text or 'Could not connect' in text or 'Connection refused' in text:
        return NO_CONNECTION
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return scrub(lines[-1])[:200] if lines else 'git push failed'


class Pushes:
    """One per host: the confirm tokens, the worker and the push records."""

    def __init__(self, host, *, run=subprocess.run, clock=time.time):
        self.host, self.store, self.run, self.clock = host, host.store, run, clock
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-push')
        self.futures = {}  # project_id -> (record id, future)
        self.tokens = {}  # confirm token -> what the confirm showed
        self.lock = threading.Lock()
        self.closed = False
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS push_records (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document TEXT NOT NULL)')
        # A push that was running when the last host ended has an unknown result: nobody saw git finish.
        for row in self.store.db.execute('SELECT document FROM push_records').fetchall():
            record = json.loads(row[0])
            if record.get('state') == 'running':
                self._save(dict(record, state='unknown', detail=RESTARTED, finished_at=int(self.clock())))

    # ---------------------------------------------------------------- records
    def _save(self, record):
        with self.store.db:
            self.store.db.execute('INSERT OR REPLACE INTO push_records VALUES (?,?,?)', (record['id'], record['project_id'], json.dumps(record)))
        return record

    def latest(self, project_id):
        row = self.store.db.execute('SELECT document FROM push_records WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (project_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cwd(self, project_id):
        if type(project_id) is not str:
            raise Rejected('Unknown project')
        row = self.store.db.execute('SELECT cwd FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise Rejected('Unknown project')
        if self.store.db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (project_id,)).fetchone():
            raise Rejected('This project is archived. Restore it before pushing.')
        return row[0]

    # ---------------------------------------------------------------- worker
    def tick(self):
        """Settle any finished push. Runs on the request thread, which owns the store."""
        for project_id, (record_id, future) in list(self.futures.items()):
            if future.done():
                self._finish(project_id, record_id, future)

    def _finish(self, project_id, record_id, future):
        self.futures.pop(project_id, None)
        row = self.store.db.execute('SELECT document FROM push_records WHERE id=?', (record_id,)).fetchone()
        if not row:
            return
        record = json.loads(row[0])
        if record.get('state') != 'running':
            return
        try:
            outcome = future.result()
        except Exception:  # noqa: BLE001 - the worker never raises on purpose; anything else is an unknown result
            outcome = dict(unknown=True)
        if outcome.get('unknown'):
            state, detail = 'unknown', LOST
        elif outcome.get('returncode') == 0:
            count = record.get('count') or 0
            state, detail = 'pushed', f"Pushed {count} commit{'' if count == 1 else 's'} to {record['remote']}/{record['branch']}."
        else:
            state, detail = 'failed', failure_reason(outcome.get('stderr'))
        self._save(dict(record, state=state, detail=detail, finished_at=int(self.clock())))

    def _work(self, cwd, argv):
        """The push itself, on the worker. Returns what happened; a timeout or a dead process is unknown, not failed."""
        try:
            result = task_git._git(cwd, *argv, run=self.run, timeout=task_git.PUSH_TIMEOUT)  # hooks off, signing off, no prompts
        except (OSError, subprocess.SubprocessError):
            return dict(unknown=True)
        return dict(returncode=result.returncode, stderr=scrub(result.stderr or ''))

    # ---------------------------------------------------------------- operations
    def dispatch(self, operation, args):
        if not isinstance(args, dict) or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported push fields')
        self.tick()
        cwd = self._cwd(args['projectId'])
        if operation == 'push_preview':
            return self.preview(args['projectId'], cwd)
        if operation == 'push_start':
            return self.start(args['projectId'], cwd, args['confirmToken'], args['setUpstream'])
        if operation == 'push_status':
            return self.latest(args['projectId'])
        if operation == 'push_recount':
            return self.recount(args['projectId'], cwd)
        raise Rejected('Unsupported push operation')

    def preview(self, project_id, cwd):
        """What the confirm shows: the remote state, each commit, and a token that ties the push to exactly this."""
        state = remote_state(cwd, run=self.run)
        commits = pending_commits(self.store.db, cwd, state, run=self.run)
        latest = self.latest(project_id)
        can_push = bool(state['git'] and state['branch'] and state['remote'] and state['url'] and state['head'] and state['ahead'] > 0
                        and state['behind'] == 0 and (latest is None or latest['state'] in SETTLED_STATES))
        token = None
        now = self.clock()
        with self.lock:
            for key in [k for k, v in self.tokens.items() if now - v['drawn_at'] > CONFIRM_SECONDS]:
                del self.tokens[key]  # stale confirms go; a newer preview doesn't cancel an open one, push_start re-checks the live state anyway
            if can_push:
                token = secrets.token_hex(8)
                self.tokens[token] = dict(project_id=project_id, url=state['url'], remote=state['remote'], branch=state['branch'],
                                          head=state['head'], upstream=state['upstream'], drawn_at=now)
        detail = state['detail']
        if detail is None and state['behind'] > 0:
            detail = BEHIND
        elif detail is None and state['ahead'] == 0:
            detail = f"Nothing to push. {state['upstream']} has everything on {state['branch']}."
        elif latest is not None and latest['state'] == 'running':
            detail = 'A push is running.'
        elif latest is not None and latest['state'] == 'unknown':
            detail = latest['detail'] + ' Check GitHub before pushing again.'
        return dict(state, commits=commits, confirm_token=token, drawn_at=int(now) if token else None, can_push=can_push,
                    detail=detail, latest=latest)

    def start(self, project_id, cwd, token, set_upstream):
        if type(token) is not str or type(set_upstream) is not bool:
            raise Rejected('Unsupported push fields')
        if self.closed:
            raise Rejected('Dervo is closing. Reopen it before pushing.')
        with self.lock:
            shown = self.tokens.get(token)
            if shown is None or shown['project_id'] != project_id:
                raise Rejected('This confirmation is no longer valid. ' + RECONFIRM)
            if self.clock() - shown['drawn_at'] > CONFIRM_SECONDS:
                del self.tokens[token]
                raise Rejected('This confirmation is older than 10 minutes. ' + RECONFIRM)
        latest = self.latest(project_id)
        if latest is not None and latest['state'] == 'running':
            raise Rejected('A push is already running for this project.')
        if latest is not None and latest['state'] == 'unknown':
            raise Rejected('The last push has an unknown result. Check GitHub (push_recount) before pushing again.')
        state = remote_state(cwd, run=self.run)
        if not state['git']:
            raise Rejected(task_git.NOT_A_REPO)
        if state['branch'] is None:
            raise Rejected(DETACHED)
        if state['remote'] is None or state['url'] is None:
            raise Rejected(state['detail'] if state['detail'] in (MANY_URLS, UNSAFE_URL, ODD_NAME) else NO_REMOTE)
        # SEC2: the push goes to exactly the remote, branch and commit the confirm showed.
        if state['url'] != shown['url'] or state['remote'] != shown['remote']:
            raise Rejected(REMOTE_CHANGED)
        if state['branch'] != shown['branch']:
            raise Rejected(BRANCH_CHANGED)
        if state['head'] != shown['head']:
            raise Rejected(HEAD_CHANGED)
        if state['upstream'] is None and not set_upstream:
            raise Rejected(f"{state['branch']} isn't on GitHub yet. Confirm setting {state['remote']}/{state['branch']} as its upstream to push it.")
        if state['behind'] > 0:
            raise Rejected(BEHIND)
        if state['ahead'] == 0:
            raise Rejected('Nothing to push.')
        with self.lock:
            self.tokens.pop(token, None)  # a confirm is good for one push
        argv = [*hardening(cwd, run=self.run), 'push', *(['-u'] if state['upstream'] is None else []), '--', state['remote'], state['branch']]
        record = dict(id=uuid.uuid4().hex, project_id=project_id, cwd=cwd, branch=state['branch'], remote=state['remote'], url=state['url'],
                      upstream=state['upstream'] or f"{state['remote']}/{state['branch']}", sets_upstream=state['upstream'] is None,
                      head=state['head'], count=state['ahead'], state='running', detail=f"Pushing {state['ahead']} commit{'' if state['ahead'] == 1 else 's'} to {state['remote']}/{state['branch']}.",
                      started_at=int(self.clock()), finished_at=None)
        self._save(record)
        self.futures[project_id] = (record['id'], self.worker.submit(self._work, cwd, argv))
        return record

    def recount(self, project_id, cwd):
        """Ask the remote what it has for the branch and settle the record from that. Never pushes."""
        record = self.latest(project_id)
        if record is None:
            raise Rejected('No push to check.')
        if record['state'] == 'running':
            raise Rejected('This push is still running.')
        try:
            result = _run(cwd, *hardening(cwd, run=self.run), 'ls-remote', '--heads', '--', record['remote'], record['branch'], run=self.run)
        except (OSError, subprocess.SubprocessError):
            return self._save(dict(record, state='unknown', detail="Dervo couldn't reach the remote to check. " + LOST, checked_at=int(self.clock())))
        if result.returncode != 0:
            return self._save(dict(record, state='unknown', detail="Dervo couldn't check GitHub: " + failure_reason(result.stderr), checked_at=int(self.clock())))
        heads = [line.split()[0] for line in (result.stdout or '').splitlines() if line.split()]
        if record['head'] in heads:
            return self._save(dict(record, state='pushed', detail=CONFIRMED, checked_at=int(self.clock())))
        return self._save(dict(record, state='not_pushed', detail=NOT_PUSHED, checked_at=int(self.clock())))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.tick()  # a push that already finished gets its true result; one still running is unknown at the next start
        self.worker.shutdown(wait=False, cancel_futures=False)
