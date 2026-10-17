"""Send to cloud: a run in the provider's own cloud, started and followed through the user's own signed-in CLI.

Design: /mnt/project-files/dervo/cloud-strategy.md (8 Oct 2026, section 4.2). Dervo hosts nothing. The work runs in
Claude's cloud (``claude --cloud``) or in Codex cloud (``codex cloud exec``), on the account the conversation already
uses, so it costs nothing beyond that account's plan. Dervo keeps one record per cloud run and shows only what it can
see:

* **Codex**: status and summary from ``codex cloud list --json``, with the time Dervo last checked. A check that fails,
  or one older than STALE_SECONDS, reads *unknown*, never the last status from memory.
* **Claude**: there's no documented way to read a cloud session's progress, so a started run reads *unknown* with its
  session link until a new branch appears on the repository's remote. Then it's *ready* with that branch.

The flow is a preview and a send, like github_host's pull request. ``cloud_preview`` says exactly what goes and where:
the account, the repository and branch the cloud copy is made from, and what it won't have (unpushed commits, edits
you haven't committed). It hands out a one-use id. ``cloud_send`` takes that id, checks the account is still the one
shown, records the run as *starting* and launches the CLI on a worker thread, because a launch can take longer than the
bridge lets a request run. A launch whose result Dervo lost (a timeout, a host that closed mid-launch) is *unknown*,
never failed, and it's never sent again on its own: the provider may have it.

Results come back as a branch. ``cloud_bring_back`` fetches the run's branch from the remote and makes a local branch of
it, without checking it out or touching your folder. Codex cloud tasks that don't push a branch come back by applying
the task's diff onto a new branch in a scratch worktree (``codex cloud diff``), again without touching your folder.

Nothing here merges, pushes, applies to your working tree or starts a second attempt. Account checks reuse
connections_ui.probe (the same identity rule every run uses), and personal and work stay apart (owned_profile).

Seams for tests: ``run`` (subprocess.run), ``probe`` (connections_ui.probe), ``claude_path`` and ``codex_path``. No test
calls a real CLI or the network.
"""
import json
import os
import platform_os
import re
import select
import signal
import secrets
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import secret_scan
from dervo_connections import Rejected

REQUIRED = {
    'cloud_preview': {'projectId', 'conversationId', 'text'},
    'cloud_send': {'previewId'},
    'cloud_runs': {'projectId'},
    'cloud_check': {'runId'},
    'cloud_follow_up': {'runId', 'text'},
    'cloud_bring_back': {'runId', 'branch'},
    'cloud_environment_set': {'projectId', 'environment'},
}
FIELDS = REQUIRED
PROVIDERS = ('claude', 'codex')
STATES = ('starting', 'working', 'ready', 'failed', 'unknown')
OPEN_STATES = ('starting', 'working', 'unknown')
TEXT_LIMIT = 20000
PREVIEW_SECONDS = 600       # a preview is good for this long, then it's shown again
CHECK_SECONDS = {'codex': 30, 'claude': 120}  # a run isn't read more often than this, however often the screen asks
FORCED_CHECK_SECONDS = 5    # Check now, pressed twice
TICK_SECONDS = 5
STALE_SECONDS = 300         # a Codex status older than this reads unknown
LAUNCH_TIMEOUT = 300        # a launch provisions the cloud machine and clones or uploads the repository
STARTING_GRACE = 120        # a run still "starting" this long after LAUNCH_TIMEOUT reads unknown (a wedged worker)
FOLLOW_DAYS = 3             # a run no one has seen move in this long isn't checked on its own any more
DIFF_LIMIT = 20 * 1024 * 1024
OUTPUT_LIMIT = 4 * 1024 * 1024
CODEX_PAGES = 5             # codex cloud list gives at most 20 tasks a page
READ_TIMEOUT = 20
FETCH_TIMEOUT = 120
BRANCH_PREFIXES = {'claude': 'claude/', 'codex': 'codex/'}
_ENVIRONMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')
_BRANCH = re.compile(r'^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]{1,200}(?<![./])$')
_CODEX_ID = re.compile(r'^[A-Za-z0-9_-]{4,128}$')
_URL = re.compile(r'https://[^\s"\'<>]+')

PROVIDER_WORDS = {'claude': 'Claude’s cloud', 'codex': 'Codex cloud'}
NO_REPO = 'This project’s folder isn’t a git repository with a remote, so there’s nothing for the cloud to start from.'
NO_BRANCH = 'Check out a branch first. The cloud copy is made from a branch on the remote, not a detached commit.'
NOT_ON_REMOTE = '{branch} isn’t on {remote} yet, so the cloud can’t start from it. Push it first.'
API_KEY = {'claude': 'Claude’s cloud needs a Claude subscription sign-in (Pro, Max, Team or Enterprise). This account uses an API key.',
           'codex': 'Codex cloud needs a ChatGPT sign-in. This account uses an API key.'}
NO_ENVIRONMENT = ('Codex cloud runs in an environment you set up on chatgpt.com/codex. Add its id to this project '
                  '(Send to cloud, Environment) first. Nothing was sent.')
BUSY = 'Dervo is checking this run right now. Try again in a moment.'
DASH = 'Start the task with a word, not a dash. The CLI would read it as an option.'
NOT_LISTED = ('Codex cloud’s recent tasks don’t include this one any more, so Dervo can’t see where it is. Open it on '
              'chatgpt.com/codex.')
STOPPED_FOLLOWING = 'Dervo stopped checking this run after {days} days with no change. Check now looks again.'
# Words the CLIs use for refusals that happen before anything reaches the provider, so failed is safe to say. Anything
# else that exits non-zero after a launch began is unknown: the provider may have the run.
REFUSED_BEFORE_SENDING = ('disabled by your organization', 'need a claude.ai sign-in', 'requires authentication',
                          'not available with', 'not uploading this working tree', 'unknown option', 'unexpected argument',
                          'unrecognized', 'not logged in', 'please log in', 'sign in', 'login required', 'no such environment',
                          'environment not found', 'unable to get organization uuid', "couldn't verify your organization")
ACCOUNT_CHANGED = 'The account signed in isn’t the one the preview showed any more. Nothing was sent. Check it again.'
EXPIRED = 'That preview is out of date. Look again before sending.'
CLAUDE_UNKNOWN = ('Running in Claude’s cloud. Dervo can’t see its progress there, so it shows unknown until a branch '
                  'appears on {remote}.')
LAUNCH_LOST = ('Dervo lost sight of this launch, so it may have started. Check {where} before sending it again. Dervo '
               'won’t send it again on its own.')


def now():
    return time.time()


SECRET_FILE = re.compile(r'(?:^|/)(?:\.env(?:\.(?!(?:example|sample|template|dist|defaults?)$)[^/]+)?|[^/]*\.(?:pem|key|p12|pfx|keystore|jks)|'
                         r'id_(?:rsa|dsa|ecdsa|ed25519)|\.netrc|\.npmrc|\.pypirc|credentials(?:\.json)?|'
                         r'[^/]*secrets?\.(?:json|ya?ml|toml|env|txt|ini))$', re.I)
# Templates people commit on purpose (.env.example and the like) hold placeholders, not keys, so their names don't block.
CONFIG_FILE = re.compile(r'\.(?:json|ya?ml|toml|ini|cfg|conf|properties|env|txt|xml|plist)$', re.I)
SECRET_SCAN_BYTES = 1024 * 1024     # what the content scan reads in all, per preview
SECRET_SCAN_SECONDS = 0.5           # and how long it may take; names are always checked
SECRET_FILE_BYTES = 64 * 1024


def secret_files(cwd, paths, *, contents=True, clock=time.monotonic):
    """(files that look like they hold keys, whether every file was checked). Every path is checked by name; with
    ``contents``, small config-like files are also read and run through secret_scan, within SECRET_SCAN_BYTES and
    SECRET_SCAN_SECONDS so a big folder never holds up the preview. Running out of budget means not every file was
    checked, which the caller treats like a find. Dervo reads the files locally only."""
    import secret_scan
    found = [path for path in paths if SECRET_FILE.search(path)]
    if not contents:
        return found, True
    named, budget, deadline = set(found), SECRET_SCAN_BYTES, clock() + SECRET_SCAN_SECONDS
    for path in paths:
        if path in named or not CONFIG_FILE.search(path):
            continue
        if budget <= 0 or clock() > deadline:
            return found, False
        full = os.path.join(cwd, path)
        try:
            if not os.path.isfile(full) or os.path.islink(full) or os.path.getsize(full) > SECRET_FILE_BYTES:
                continue
            with open(full, 'rb') as handle:
                data = handle.read(SECRET_FILE_BYTES)
        except OSError:
            continue
        budget -= len(data)
        if b'\0' not in data and secret_scan.find(data.decode('utf-8', 'replace')):
            found.append(path)
    return found, True


def might_upload_keys(where):
    """Why a Claude cloud run can't be sent from this folder as it is, or None. Without the Claude GitHub App, Claude Code
    uploads the folder, and Dervo can't tell beforehand which way it'll go, so a key-looking file (or one it couldn't
    check in time) among the uncommitted or new files stops the send: fail closed."""
    if where['secrets']:
        count = len(where['secrets'])
        names = ', '.join(where['secrets'][:3]) + (f' and {count - 3} more' if count > 3 else '')
        return (f'{names} look{"s" if count == 1 else ""} like {"it holds" if count == 1 else "they hold"} keys or passwords. '
                'If Claude Code uploads this folder, it could go too, so Dervo won’t send from it. Move '
                f'{"it" if count == 1 else "them"} out of the folder, then look again.')
    if not where.get('scanned', True):
        return ('This folder has too many new or changed settings files for Dervo to check for keys quickly, and Claude Code '
                'may upload them, so Dervo won’t send from it. Commit and push or remove them, then look again.')
    return None


def porcelain_paths(out):
    """[(status, path)] from ``git status --porcelain -z``. A rename's old name follows it and is skipped."""
    parts, entries, at = out.split('\0'), [], 0
    while at < len(parts):
        part = parts[at]
        at += 1
        if len(part) < 4:
            continue
        entries.append((part[:2], part[3:]))
        if part[0] in 'RC':
            at += 1
    return entries


def run_group(argv, *, cwd=None, env=None, timeout=None, input=None, stdin=None, capture_output=True, text=False,
              errors=None, **_):
    """subprocess.run for a CLI that may start children of its own: it runs in its own process group, and a timeout
    ends the whole group, not just the first process. Output is capped at OUTPUT_LIMIT per stream."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE if input is not None else stdin,
                               stdout=subprocess.PIPE if capture_output else None, stderr=subprocess.PIPE if capture_output else None,
                               **platform_os.new_group())
    try:
        out, err = process.communicate(input.encode() if isinstance(input, str) else input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(process)
        out, err = process.communicate()
        raise subprocess.TimeoutExpired(argv, timeout, output=_text(out, text, errors), stderr=_text(err, text, errors)) from None
    return subprocess.CompletedProcess(argv, process.returncode, _text(out, text, errors), _text(err, text, errors))


def _end_group(process):
    try:
        platform_os.signal_group(process.pid, signal.SIGTERM)
        process.wait(3)
    except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
        try:
            platform_os.signal_group(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _text(data, text, errors):
    data = (data or b'')[:OUTPUT_LIMIT]
    return data.decode('utf-8', errors or 'replace') if text else data


def run_terminal(argv, *, cwd, env, timeout):
    """Run a CLI that only works in a terminal (``claude --cloud "task"`` draws a live checklist) on a pseudo-terminal,
    in its own process group, and return (exit code or None when Dervo ended it, everything it printed). It's left to
    finish on its own: the session link appears early, but the CLI may still be sending the task, so it's only ended at
    ``timeout``. Nothing is ever typed into it."""
    main, side = platform_os.open_pty()
    try:
        process = subprocess.Popen(argv, cwd=cwd, env={**env, 'TERM': 'dumb', 'COLUMNS': '200', 'LINES': '50'},
                                   stdin=side, stdout=side, stderr=side, close_fds=True, **platform_os.new_group())
    finally:
        os.close(side)
    chunks, size, deadline = [], 0, time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                _end_group(process)
                return None, b''.join(chunks).decode('utf-8', 'replace')
            ready, _, _ = select.select([main], [], [], min(left, 1.0))
            if ready:
                try:
                    data = os.read(main, 65536)
                except OSError:  # the other side closed: the CLI is done
                    data = b''
                if not data:
                    break
                if size < OUTPUT_LIMIT:
                    chunks.append(data)
                    size += len(data)
            elif process.poll() is not None:
                break
        try:
            code = process.wait(5)
        except subprocess.TimeoutExpired:
            _end_group(process)
            code = None
        return code, b''.join(chunks).decode('utf-8', 'replace')
    finally:
        os.close(main)


def clip(text, limit=300):
    """One line of text from a CLI, safe to keep and show: secrets redacted, whitespace collapsed, cut at ``limit``."""
    return secret_scan.redact(' '.join(str(text or '').split()))[:limit]


# ------------------------------------------------------------------------------------------------------- commands
# Every argv in one place, so a change in a CLI is one edit and one test. Checked against the docs on 8 Oct 2026; see
# the PR for the sources and what wasn't confirmed.

def claude_start_argv(claude, text):
    """Start a Claude cloud session from the current repository and branch. Documented: ``claude --cloud "task"``
    (``--remote`` is the deprecated alias). There's no documented JSON for a new session, so the output is read for the
    session link (parse_claude_start), and a launch that prints none is unknown."""
    # Only your own settings load: a repository's .claude/settings.json could point Claude Code at another API host or
    # an API-key helper, or run hooks, the way --setting-sources '' keeps it from doing on Dervo's coding runs.
    return [claude, '--setting-sources', 'user', '--cloud', text]


def claude_follow_up_argv(claude, session_id, text):
    """Queue a message on a running Claude cloud session and exit. Answers {ok, session_id, url}."""
    return [claude, '--setting-sources', 'user', '-p', text, '--cloud', session_id, '--output-format', 'json']


def codex_start_argv(codex, environment, text, branch):
    return [codex, 'cloud', 'exec', '--env', environment, '--branch', branch, '--', text]


def codex_list_argv(codex, environment=None, cursor=None):
    """One page (at most 20 tasks) of ``codex cloud list --json``, narrowed to the run's environment."""
    return [codex, 'cloud', 'list', '--json', '--limit', '20', *(['--env', environment] if environment else []),
            *(['--cursor', cursor] if cursor else [])]


def codex_diff_argv(codex, task_id):
    return [codex, 'cloud', 'diff', task_id]


# ------------------------------------------------------------------------------------------------------- parsing

_SESSION = re.compile(r'\b((?:session|cse)_[A-Za-z0-9]{8,64})\b')
_SESSION_ID = re.compile(r'(?:session|cse)_[A-Za-z0-9]{8,64}')
CLAUDE_LINK = 'https://claude.ai/code/'
CODEX_LINK = 'https://chatgpt.com/'


_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def parse_claude_start(stdout):
    """(session id, url) from a Claude cloud launch, either from JSON ({session_id, url}) or from the lines it prints.
    (None, None) when neither is there: the launch is then unknown, never assumed."""
    text = _ANSI.sub('', stdout or '')
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        value = None
    if isinstance(value, dict):
        session = value.get('session_id') if type(value.get('session_id')) is str and _SESSION_ID.fullmatch(value['session_id']) else None
        url = value.get('url') if type(value.get('url')) is str and value['url'].startswith(CLAUDE_LINK) else None
        if session or url:
            return session, url
    url = next((u for u in _URL.findall(text) if u.startswith(CLAUDE_LINK)), None)
    found = _SESSION.search(url or '') or _SESSION.search(text)
    return (found.group(1) if found else None), url


def parse_codex_start(stdout):
    """(task id, url) from ``codex cloud exec``: the task URL it prints, and the id from that URL or a ``task_`` word."""
    text = _ANSI.sub('', stdout or '')
    url = next((u for u in _URL.findall(text) if u.startswith(CODEX_LINK) and '/tasks/' in u), None)
    task = None
    if url and '/tasks/' in url:
        task = url.split('?', 1)[0].split('#', 1)[0].rstrip('/').rsplit('/', 1)[-1]
    if not task:
        found = re.search(r'\b(task_[A-Za-z0-9_-]{4,})\b', text)
        task = found.group(1) if found else None
    if task and not _CODEX_ID.match(task):
        task = None
    return task, url


CODEX_STATUS = {
    # Codex cloud's task states as ``codex cloud list --json`` reports them, to Dervo's.
    'pending': 'working', 'queued': 'working', 'running': 'working', 'in_progress': 'working',
    'ready': 'ready', 'completed': 'ready', 'applied': 'ready',
    'error': 'failed', 'failed': 'failed', 'cancelled': 'failed',
}


def codex_cursor(stdout):
    try:
        value = json.loads(stdout or '')
    except ValueError:
        return None
    cursor = value.get('cursor') if isinstance(value, dict) else None
    return cursor if type(cursor) is str and cursor else None


def parse_codex_list(stdout):
    """{task id: {status, title, summary, url, updated_at}} from ``codex cloud list --json``. Accepts a list of tasks or
    an object holding one under ``tasks``. Anything else raises ValueError, and the check reads unknown."""
    value = json.loads(stdout or '')
    tasks = value.get('tasks') if isinstance(value, dict) else value
    if not isinstance(tasks, list):
        raise ValueError('codex cloud list gave no task list')
    found = {}
    for task in tasks:
        if not isinstance(task, dict) or type(task.get('id')) is not str:
            continue
        summary = task.get('summary')
        if isinstance(summary, dict):  # some versions give {files_changed, lines_added, lines_removed}
            parts = [f'{summary[k]} {w}' for k, w in (('files_changed', 'files'), ('lines_added', 'added'),
                                                       ('lines_removed', 'removed')) if isinstance(summary.get(k), int)]
            summary = ', '.join(parts) or None
        found[task['id']] = {
            'status': task.get('status') if type(task.get('status')) is str else None,
            'title': task.get('title') if type(task.get('title')) is str else None,
            'summary': summary if type(summary) is str else None,
            'url': task.get('url') if type(task.get('url')) is str and task['url'].startswith(CODEX_LINK) else None,
        }
    return found


def parse_heads(stdout, prefix):
    """{branch: sha} for the remote heads under ``prefix`` in ``git ls-remote --heads`` output."""
    heads = {}
    for line in (stdout or '').splitlines():
        sha, _, ref = line.partition('\t')
        if ref.startswith('refs/heads/' + prefix) and re.fullmatch(r'[0-9a-f]{40,64}', sha.strip()):
            heads[ref[len('refs/heads/'):]] = sha.strip()
    return heads


# ------------------------------------------------------------------------------------------------------- store

def observed(row, at):
    """(state, line) as shown: a Codex status Dervo hasn't confirmed within STALE_SECONDS reads unknown. Dervo never shows
    working from memory."""
    if row['provider'] == 'codex' and row['state'] == 'working' and (row['seen_at'] is None or at - row['seen_at'] > STALE_SECONDS):
        return 'unknown', 'Dervo can’t reach Codex cloud right now, so it doesn’t know where this run is.'
    if row['state'] == 'starting' and at - row['created_at'] > LAUNCH_TIMEOUT + STARTING_GRACE:
        return 'unknown', LAUNCH_LOST.format(where=PROVIDER_WORDS[row['provider']])
    if row['state'] in ('working', 'unknown') and at - (row['seen_at'] or row['created_at']) > FOLLOW_DAYS * 86400:
        return 'unknown', STOPPED_FOLLOWING.format(days=FOLLOW_DAYS)
    return row['state'], row['detail']


SCHEMA = '''
create table if not exists cloud_runs (
  id text primary key,
  project_id text not null,
  conversation_id text,
  provider text not null check (provider in ('claude', 'codex')),
  profile_id text not null,
  identity text,
  task text not null,
  remote text not null,
  repo_url text,
  base_branch text not null,
  base_sha text,
  environment text,
  remote_id text,
  url text,
  branch text,
  candidates text,
  known_heads text,
  state text not null check (state in ('starting', 'working', 'ready', 'failed', 'unknown')),
  detail text,
  provider_status text,
  summary text,
  brought_back text,
  created_at real not null,
  updated_at real not null,
  checked_at real,
  seen_at real
);
create index if not exists cloud_runs_project on cloud_runs(project_id, created_at);
create table if not exists cloud_environments (project_id text primary key, environment text not null, set_at real not null);
'''


def schema(db):
    db.executescript(SCHEMA)


class CloudRuns:
    """The host side of Send to cloud. Only the request thread writes the store: workers hand their outcome back through
    ``self.outcomes`` and ``settle`` writes it on the next request (or the host's tick, once it calls ``tick``)."""

    def __init__(self, host, *, run=run_group, terminal=run_terminal, probe=None, claude_path=None, codex_path=None, clock=now,
                 workers=None, launchers=None):
        self.host, self.run, self.terminal, self.clock = host, run, terminal, clock
        self._probe, self._claude_path, self._codex_path = probe, claude_path, codex_path
        self.db = host.store.db
        schema(self.db)
        self.path = self.db.execute('pragma database_list').fetchone()[2]
        self.lock = threading.Lock()
        self.previews = {}
        self.outcomes = {}       # run id -> dict of fields a worker settled
        self.busy = set()        # run ids a worker is on now
        self.ticked = float('-inf')
        self.pool = workers or ThreadPoolExecutor(max_workers=2, thread_name_prefix='dervo-cloud')
        # Launches get their own workers, so a send never waits behind checks and fetches.
        self.launchers = launchers or workers or ThreadPoolExecutor(max_workers=2, thread_name_prefix='dervo-cloud-launch')
        self._reconcile()

    # ------------------------------------------------------------------ seams
    def claude(self):
        if self._claude_path:
            return self._claude_path
        from claude_policy import executable
        return executable()

    def codex(self):
        if self._codex_path:
            return self._codex_path
        from codex_runtime import executable
        return executable()

    def probe(self, profile):
        if self._probe:
            return self._probe(profile)
        from connections_ui import probe
        return probe(profile, None if profile['provider'] == 'claude' else self.codex())

    def neutral(self):
        """An empty folder for CLI calls that don't need the repository, so a project's own CLI config can't apply."""
        import platform_os
        path = Path(platform_os.scratch_root()) / 'dervo-cloud-neutral'
        path.mkdir(mode=0o700, exist_ok=True)
        return str(path)

    def env(self, binding):
        from transport import clean_environment
        return clean_environment(binding['provider'], binding['home'], binding['home'])

    def git(self, cwd, *args, timeout=None, stdin=None):
        """Raises OSError or SubprocessError when git can't run or takes too long; every worker step catches both."""
        import task_git
        return task_git._git(cwd, *args, run=self.run, timeout=timeout or task_git.TIMEOUT, stdin=stdin)

    def hardening(self, cwd):
        import task_push
        return task_push.hardening(cwd, run=self.run)

    # ------------------------------------------------------------------ dispatch
    def dispatch(self, operation, args):
        if operation not in REQUIRED or type(args) is not dict or set(args) != REQUIRED[operation]:
            raise Rejected('Unsupported Send to cloud fields.')
        self.settle()
        if operation == 'cloud_preview':
            return self.preview(args['projectId'], args['conversationId'], args['text'])
        if operation == 'cloud_send':
            return self.send(args['previewId'])
        if operation == 'cloud_runs':
            return self.runs(args['projectId'])
        if operation == 'cloud_check':
            return self.check(args['runId'], force=True)
        if operation == 'cloud_follow_up':
            return self.follow_up(args['runId'], args['text'])
        if operation == 'cloud_bring_back':
            return self.bring_back(args['runId'], args['branch'])
        return self.set_environment(args['projectId'], args['environment'])

    def tick(self):
        """Background round for the host: settle finished work and start checks that are due, at most every
        TICK_SECONDS (the host's loop runs far more often while anything is in flight). Never raises."""
        at = self.clock()
        if at - self.ticked < TICK_SECONDS and not self.outcomes:
            return
        self.ticked = at
        try:
            self.settle()
            rows = self.db.execute(f"select id from cloud_runs where state in ({','.join('?' * len(OPEN_STATES))})",
                                   OPEN_STATES).fetchall()
        except Exception:  # noqa: BLE001 - background work never takes the host down
            return
        for row in rows:
            try:
                self.check(row[0], force=False)
            except Exception:  # noqa: BLE001 - one run that can't be followed (its account removed) never stops the rest
                pass

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.launchers.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ bindings
    def binding(self, project_id, conversation_id):
        if type(project_id) is not str or not project_id:
            raise Rejected('Choose a project')
        if conversation_id is None:
            from conversation_coding import project_binding
            from dervo_connections import project_context
            binding = {**project_binding(self.host.store, project_id), 'context': project_context(self.db, project_id)}
        elif type(conversation_id) is str:
            from host import ACTOR
            binding = self.host.store.binding(conversation_id, project_id, actor=ACTOR)
        else:
            raise Rejected('Choose a conversation')
        from connections_ui import owned_profile
        owned_profile(self.host.store, self.host.root, binding['profile_id'], context=binding['context'])
        if binding['provider'] not in PROVIDERS:
            raise Rejected('Send to cloud works with Claude Code and Codex accounts.')
        if binding.get('method') == 'api_key':
            raise Rejected(API_KEY[binding['provider']])
        return binding

    def run_binding(self, row):
        """The binding to follow a run with: it has to be the account the run was sent from. A conversation moved to
        another account since then is refused, never followed as the new one."""
        binding = self.binding(row['project_id'], row['conversation_id'])
        if binding['profile_id'] != row['profile_id']:
            raise Rejected('This conversation uses another account now, and the cloud run belongs to the first one. '
                           'Open it from the provider instead.')
        return binding

    def profile(self, binding):
        return {'provider': binding['provider'], 'home': binding['home'], 'identity': binding['identity'],
                'category': binding['category'], 'method': binding.get('method'), 'org': binding.get('org')}

    def environment(self, project_id):
        row = self.db.execute('select environment from cloud_environments where project_id=?', (project_id,)).fetchone()
        return row[0] if row else None

    def set_environment(self, project_id, environment):
        if type(project_id) is not str or not self.db.execute('select 1 from projects where id=?', (project_id,)).fetchone():
            raise Rejected('Unknown project')
        if environment is None:
            with self.db:
                self.db.execute('delete from cloud_environments where project_id=?', (project_id,))
            return {'environment': None}
        if type(environment) is not str or not _ENVIRONMENT.match(environment.strip()):
            raise Rejected('Paste the Codex environment id from chatgpt.com/codex (letters, numbers, dashes).')
        with self.db:
            self.db.execute('insert into cloud_environments values (?,?,?) on conflict(project_id) do update set '
                            'environment=excluded.environment, set_at=excluded.set_at',
                            (project_id, environment.strip(), self.clock()))
        return {'environment': environment.strip()}

    # ------------------------------------------------------------------ preview and send
    def where(self, cwd, *, scan=False):
        """The repository facts a cloud copy depends on, from git only (task_push.remote_state). Never the network."""
        import task_push
        state = task_push.remote_state(cwd, run=self.run)
        if not state.get('git') or not state.get('remote') or not state.get('url'):
            raise Rejected(state.get('detail') if state.get('git') and state.get('detail') else NO_REPO)
        if not state.get('branch'):
            raise Rejected(NO_BRANCH)
        if not state.get('upstream'):
            raise Rejected(NOT_ON_REMOTE.format(branch=state['branch'], remote=state['remote']))
        dirty = self.git(cwd, 'status', '--porcelain', '-z', '--untracked-files=all')
        entries = porcelain_paths(dirty.stdout or '') if dirty.returncode == 0 else []
        # The commit the cloud starts from is the remote's, not this folder's HEAD: unpushed commits aren't in it.
        base = self.git(cwd, 'rev-parse', '--verify', '--quiet', '@{upstream}')
        return {**state, 'changed': len([1 for status, _ in entries if status != '??']),
                'untracked': len([1 for status, _ in entries if status == '??']),
                **dict(zip(('secrets', 'scanned'), secret_files(cwd, [path for _, path in entries], contents=scan == 'full')
                                                       if scan else ([], True))),
                'base': (base.stdout or '').strip() if base.returncode == 0 else None,
                'remote_branch': state['upstream'].split('/', 1)[1] if '/' in state['upstream'] else state['branch']}

    def preview(self, project_id, conversation_id, text):
        if type(text) is not str or not text.strip():
            raise Rejected('Say what the cloud run should do.')
        if len(text) > TEXT_LIMIT:
            raise Rejected(f'Keep a cloud task under {TEXT_LIMIT:,} characters.')
        if text.lstrip().startswith('-'):
            raise Rejected(DASH)
        binding = self.binding(project_id, conversation_id)
        provider = binding['provider']
        where = self.where(binding['cwd'], scan='full' if provider == 'claude' else None)
        environment = self.environment(project_id) if provider == 'codex' else None
        notes = []
        if where['ahead']:
            notes.append(f'{where["ahead"]} local commit{"s" if where["ahead"] != 1 else ""} on {where["branch"]} '
                         f'{"aren’t" if where["ahead"] != 1 else "isn’t"} on {where["remote"]}, so the cloud copy won’t have '
                         f'{"them" if where["ahead"] != 1 else "it"}.')
        local = where['changed'] + where['untracked']
        if local and provider == 'codex':
            notes.append(f'{local} uncommitted or new file{"s" if local != 1 else ""} in your folder won’t go. Commit and push '
                         'first if the cloud run needs them.')
        elif local:
            notes.append(f'{local} uncommitted or new file{"s" if local != 1 else ""} in your folder. Claude normally starts from '
                         'GitHub, so they won’t go. If the Claude GitHub App isn’t on this repository, Claude Code uploads your '
                         'folder instead, and some of them may go. Dervo can’t tell which way Claude will take.')
        notes.append('Claude’s cloud picks the model. Dervo can’t choose it from here.' if provider == 'claude'
                     else 'Your Codex environment picks the model. Dervo can’t choose it from here.')
        blocked = (NO_ENVIRONMENT if provider == 'codex' and not environment else
                   might_upload_keys(where) if provider == 'claude' else None)
        preview_id = secrets.token_hex(8)
        shown = {'project_id': project_id, 'conversation_id': conversation_id, 'text': text, 'provider': provider,
                 'profile_id': binding['profile_id'], 'identity': binding['identity'], 'remote': where['remote'],
                 'url': where['url'], 'branch': where['branch'], 'upstream': where['upstream'], 'head': where['base'],
                 'remote_branch': where['remote_branch'],
                 'environment': environment, 'expires': self.clock() + PREVIEW_SECONDS}
        with self.lock:
            self.previews = {k: v for k, v in self.previews.items() if v['expires'] > self.clock()}
            if not blocked:
                self.previews[preview_id] = shown
        return {'previewId': None if blocked else preview_id, 'provider': provider, 'where': PROVIDER_WORDS[provider],
                'account': binding['identity'], 'repository': where['url'], 'remote': where['remote'],
                'branch': where['branch'], 'environment': environment, 'notes': notes, 'canSend': not blocked,
                'detail': blocked}

    def send(self, preview_id):
        with self.lock:
            shown = self.previews.pop(preview_id, None) if type(preview_id) is str else None
        if shown is None or shown['expires'] <= self.clock():
            raise Rejected(EXPIRED)
        binding = self.binding(shown['project_id'], shown['conversation_id'])
        if binding['profile_id'] != shown['profile_id'] or binding['provider'] != shown['provider']:
            raise Rejected(ACCOUNT_CHANGED)
        where = self.where(binding['cwd'], scan='names' if shown['provider'] == 'claude' else None)
        if (where['remote'], where['branch']) != (shown['remote'], shown['branch']):
            raise Rejected(EXPIRED)
        if shown['provider'] == 'claude' and might_upload_keys(where):
            raise Rejected(might_upload_keys(where))  # a key-looking file appeared after the preview
        run_id = str(uuid.uuid4())
        at = self.clock()
        with self.db:
            self.db.execute('insert into cloud_runs (id, project_id, conversation_id, provider, profile_id, identity, task, '
                            'remote, repo_url, base_branch, base_sha, environment, state, detail, created_at, updated_at) '
                            'values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (run_id, shown['project_id'], shown['conversation_id'], shown['provider'], shown['profile_id'],
                             shown['identity'], shown['text'], shown['remote'], shown['url'], shown['branch'], where['base'],
                             shown['environment'], 'starting', f'Sending to {PROVIDER_WORDS[shown["provider"]]}…', at, at))
            self._moved(run_id)
        self._submit(run_id, self._launch, run_id, binding, shown, launch=True)
        return self.view(self.row(run_id))

    def _submit(self, run_id, work, *args, launch=False):
        with self.lock:
            if run_id in self.busy:
                return False
            self.busy.add(run_id)

        def body():
            try:
                outcome = work(*args)
            except Exception as error:  # noqa: BLE001 - a worker's failure is the run's state, never the host's
                # A launch that broke part way may have reached the provider: unknown. Anything else (a check, a fetch)
                # leaves the run's state as it was and only says what went wrong.
                outcome = ({'state': 'unknown', 'detail': f'Dervo hit an error starting this run ({type(error).__name__}), so it may '
                            'have started. Dervo won’t send it again on its own.'} if launch else
                           {'detail': f'Dervo hit an error ({type(error).__name__}). The run itself is as it was. Try again.'})
            with self.lock:
                self.busy.discard(run_id)
                if outcome:
                    self.outcomes.setdefault(run_id, {}).update(outcome)

        (self.launchers if launch else self.pool).submit(body)
        return True

    def _verified(self, binding):
        """None when the account signed in is this binding's own, else the words for why not."""
        try:
            checked = self.probe(self.profile(binding))
        except Rejected as error:
            return str(error)
        except (OSError, subprocess.SubprocessError):
            return 'Dervo couldn’t check which account is signed in. Nothing was sent.'
        if not isinstance(checked, dict) or checked.get('state') != 'identity_verified':
            return (checked or {}).get('detail') or ACCOUNT_CHANGED
        return None

    def _launch(self, run_id, binding, shown):
        """On a worker: check the account, list the remote's branches, then launch. Returns the run's new fields."""
        try:
            refused = self._verified(binding)
            if refused:
                return {'state': 'failed', 'detail': f'Didn’t start: {refused}'}
            heads = self._heads(binding['cwd'], shown['remote'], BRANCH_PREFIXES[shown['provider']])
        except Exception as error:  # noqa: BLE001 - nothing has been sent yet, so this is a plain failure
            return {'state': 'failed', 'detail': f'Didn’t start: Dervo hit an error before sending ({type(error).__name__}). Nothing was sent.'}
        settled = {'known_heads': json.dumps(heads) if heads is not None else None}
        where = PROVIDER_WORDS[shown['provider']]
        claude = shown['provider'] == 'claude'
        try:
            if claude:
                # ``claude --cloud "task"`` is the terminal form (a live checklist, no JSON), so it gets a terminal.
                code, output = self.terminal(claude_start_argv(self.claude(), shown['text']), cwd=binding['cwd'],
                                             env=self.env(binding), timeout=LAUNCH_TIMEOUT)
            else:
                result = self.run(codex_start_argv(self.codex(), shown['environment'], shown['text'], shown['remote_branch']),
                                  cwd=binding['cwd'], env=self.env(binding), capture_output=True, text=True, errors='replace',
                                  timeout=LAUNCH_TIMEOUT, stdin=subprocess.DEVNULL)
                code, output = result.returncode, f'{result.stdout or ""}\n{result.stderr or ""}'
        except subprocess.TimeoutExpired as error:
            code, output = None, f'{error.output or ""}\n{error.stderr or ""}'
        except (OSError, Rejected) as error:
            # The CLI never ran (not installed, too old for Dervo): nothing reached the provider.
            return {'state': 'failed', 'detail': 'Didn’t start: ' + (clip(error) if isinstance(error, Rejected) else
                    f'Dervo couldn’t start the {"Claude Code" if claude else "Codex"} CLI. Nothing was sent.')}
        # The run's id comes first, whatever the exit: a CLI that crashed after creating the run still created it.
        remote, url = parse_claude_start(output) if claude else parse_codex_start(output)
        if remote or url:
            if claude:
                return {**settled, 'remote_id': remote, 'url': url, 'state': 'unknown', 'seen_at': self.clock(),
                        'detail': CLAUDE_UNKNOWN.format(remote=shown['remote'])}
            if remote:
                return {**settled, 'remote_id': remote, 'url': url, 'state': 'working', 'detail': 'Started in Codex cloud.',
                        'seen_at': self.clock(), 'checked_at': self.clock()}
        said = clip(output)
        if code not in (None, 0) and any(words in said.lower() for words in REFUSED_BEFORE_SENDING):
            return {'state': 'failed', 'detail': f'Didn’t start: {where} refused it. {said}'}
        lost = LAUNCH_LOST.format(where='claude.ai/code' if claude else 'chatgpt.com/codex')
        return {**settled, 'url': url, 'state': 'unknown', 'detail': f'{lost} The CLI said: {said}' if said and code else lost}

    def _heads(self, cwd, remote, prefix):
        """The remote's branches under ``prefix`` ({name: sha}), or None when the remote couldn't be read."""
        try:
            result = self.git(cwd, *self.hardening(cwd), 'ls-remote', '--heads', '--', remote, f'refs/heads/{prefix}*',
                              timeout=READ_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        return parse_heads(result.stdout, prefix) if result.returncode == 0 else None

    # ------------------------------------------------------------------ following
    def check(self, run_id, *, force):
        row = self.row(run_id)
        if row['state'] not in OPEN_STATES or row['state'] == 'starting' or row['brought_back']:
            if force and row['state'] != 'starting':
                raise Rejected('There’s nothing more to check on this run.')
            return self.view(row)
        every = FORCED_CHECK_SECONDS if force else CHECK_SECONDS[row['provider']]
        due = row['checked_at'] is None or self.clock() - row['checked_at'] >= every
        moved = row['seen_at'] or row['created_at']
        if not force and self.clock() - moved > FOLLOW_DAYS * 86400:
            return self.view(row)  # the screen says so (view); Check now still looks
        if not force and not self.learnable(row):
            return self.view(row)
        if due or force:
            binding = self.run_binding(row)  # Check now on a run whose account moved says so, even between checks
            if due:
                self._submit(run_id, self._check, dict(row), binding)
        return self.view(row)

    @staticmethod
    def learnable(row):
        """Whether a check on its own could tell Dervo anything: a Codex task it has the id of, or a run with the list of
        branches from its launch (without that, a new branch can't be tied to it, so reading the remote is wasted)."""
        if row['provider'] == 'codex' and row['remote_id']:
            return True
        return bool(row['known_heads']) and not row['branch']

    def _check(self, row, binding):
        at = self.clock()
        if row['provider'] == 'codex' and row['remote_id']:
            task, cursor, answered = None, None, False
            for _ in range(CODEX_PAGES):
                try:
                    result = self.run(codex_list_argv(self.codex(), row['environment'], cursor), cwd=self.neutral(),
                                      env=self.env(binding), capture_output=True, text=True, errors='replace',
                                      timeout=READ_TIMEOUT, stdin=subprocess.DEVNULL)
                    tasks = parse_codex_list(result.stdout) if result.returncode == 0 else None
                except (OSError, subprocess.SubprocessError, ValueError, Rejected):
                    tasks = None
                if tasks is None:
                    break
                answered = True
                task = tasks.get(row['remote_id'])
                cursor = codex_cursor(result.stdout)
                if task is not None or not cursor:
                    break
            if not answered:
                return {'checked_at': at, 'check_failed': True}
            if task is None:
                # Codex answered and this task isn't among its recent ones: say that, not "can't reach".
                return {'checked_at': at, 'state': 'unknown', 'detail': NOT_LISTED}
            state = CODEX_STATUS.get((task['status'] or '').lower(), 'unknown')
            words = {'working': 'Working in Codex cloud.', 'ready': 'Finished in Codex cloud.', 'failed': 'Codex cloud stopped this task with an error.',
                     'unknown': f'Codex cloud says “{clip(task["status"], 40)}”, which Dervo doesn’t know.'}[state]
            fields = {'checked_at': at, 'seen_at': at, 'state': state, 'provider_status': task['status'],
                      'summary': clip(task['summary'], 500) if task['summary'] else row['summary'], 'detail': words}
            if task['url']:
                fields['url'] = task['url']
            if state == 'ready':
                fields.update(self._new_branches(row, binding))
            return fields
        # Claude (or a Codex launch whose id was lost): the remote's branches are the only thing Dervo can see.
        fields = {'checked_at': at, **self._new_branches(row, binding)}
        if fields.get('branch') or fields.get('candidates'):
            fields.setdefault('state', 'ready')
        return fields

    def _new_branches(self, row, binding):
        """The branches under the provider's prefix that appeared on the remote since the launch. One: that's the run's.
        More than one (other cloud runs on the same repository): kept as candidates for you to pick, never guessed."""
        if row['branch']:
            return {}
        try:
            known = json.loads(row['known_heads']) if row['known_heads'] else None
        except ValueError:
            known = None
        if known is None:
            return {}  # without the list from the launch, a branch can't be tied to this run
        heads = self._heads(binding['cwd'], row['remote'], BRANCH_PREFIXES[row['provider']])
        if heads is None:
            return {'check_failed': True}
        claimed = self.claimed(row['id'])
        # Only branches that weren't there at launch: another session pushing to its own older branch isn't this run.
        fresh = sorted(name for name in heads if name not in known and name not in claimed)
        if len(fresh) == 1:
            # Another session on the same repository can push a branch too, so even one isn't claimed: you confirm it.
            return {'candidates': json.dumps(fresh), 'state': 'ready', 'seen_at': self.clock(),
                    'detail': f'A new cloud branch appeared on {row["remote"]}: {fresh[0]}. If it’s this run’s, bring it back.'}
        if fresh:
            return {'candidates': json.dumps(fresh), 'state': 'ready', 'seen_at': self.clock(),
                    'detail': f'{len(fresh)} new cloud branches appeared on {row["remote"]}. Pick the one from this run.'}
        return {}

    def claimed(self, run_id):
        """Branches other cloud runs already have, read on a worker's own read-only connection (only the request thread
        writes, and the store's connection belongs to it)."""
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)) as reader:
            return {r[0] for r in reader.execute('select branch from cloud_runs where branch is not null and id<>?', (run_id,))}

    def follow_up(self, run_id, text):
        row = self.row(run_id)
        if row['provider'] != 'claude':
            raise Rejected('Codex cloud doesn’t take follow-ups from Dervo. Open the task on chatgpt.com/codex to reply.')
        if not row['remote_id']:
            raise Rejected('Dervo doesn’t have this run’s session id, so it can’t send to it. Open it on claude.ai/code.')
        if type(text) is not str or not text.strip() or len(text) > TEXT_LIMIT:
            raise Rejected('Say what to send.')
        if text.lstrip().startswith('-'):
            raise Rejected(DASH)
        binding = self.run_binding(row)
        if not self._submit(run_id, self._follow_up, dict(row), binding, text):
            raise Rejected(BUSY)
        return self.view(row)

    def _follow_up(self, row, binding, text):
        refused = self._verified(binding)
        if refused:
            return {'detail': refused}
        try:
            result = self.run(claude_follow_up_argv(self.claude(), row['remote_id'], text), cwd=binding['cwd'],
                              env=self.env(binding), capture_output=True, text=True, errors='replace', timeout=READ_TIMEOUT * 3,
                              stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {'detail': 'Dervo lost sight of that follow-up, so it may have been queued. Check the session before sending it again.'}
        except (OSError, Rejected):
            return {'detail': 'Dervo couldn’t start Claude Code. The follow-up wasn’t sent.'}
        try:
            answer = json.loads(result.stdout or '')
        except ValueError:
            answer = None
        if result.returncode == 0 and isinstance(answer, dict) and answer.get('ok') is True:
            return {'detail': 'Follow-up queued in Claude’s cloud. ' + CLAUDE_UNKNOWN.format(remote=row['remote']),
                    'state': 'unknown' if not row['branch'] else row['state'], 'seen_at': self.clock()}
        return {'detail': 'Claude’s cloud didn’t take the follow-up: ' + (clip(result.stderr or result.stdout) or 'no reason given.')}

    # ------------------------------------------------------------------ bring back
    def bring_back(self, run_id, branch):
        row = self.row(run_id)
        if row['brought_back']:
            raise Rejected(f'This run is already back as {row["brought_back"]}.')
        candidates = json.loads(row['candidates']) if row['candidates'] else []
        chosen = branch if branch is not None else row['branch']
        if chosen is None:
            if row['provider'] == 'codex' and row['state'] == 'ready' and row['remote_id']:
                chosen = None  # Codex without a pushed branch: built from the task's diff below
            else:
                raise Rejected('There’s no result branch yet.')
        elif type(chosen) is not str or not _BRANCH.match(chosen) or (chosen != row['branch'] and chosen not in candidates):
            raise Rejected('Pick one of the branches Dervo found for this run.')
        binding = self.run_binding(row)
        work = (self._fetch, dict(row), binding, chosen) if chosen is not None else (self._from_diff, dict(row), binding)
        if not self._submit(run_id, *work):
            raise Rejected(BUSY)
        return self.view(row)

    def _local_name(self, cwd, wanted):
        """``wanted``, or ``wanted-2`` and so on when a local branch has that name already. Never overwrites one."""
        name, n = wanted, 1
        while self.git(cwd, 'rev-parse', '--verify', '--quiet', f'refs/heads/{name}').returncode == 0:
            n += 1
            name = f'{wanted}-{n}'
        return name

    def _fetch(self, row, binding, branch):
        cwd, remote = binding['cwd'], row['remote']
        try:
            fetched = self.git(cwd, *self.hardening(cwd), 'fetch', '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head',
                               '--', remote, f'+refs/heads/{branch}:refs/remotes/{remote}/{branch}', timeout=FETCH_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return {'detail': f'Dervo couldn’t fetch {branch}. Nothing in your folder changed.'}
        if fetched.returncode != 0:
            import task_push
            return {'detail': task_push.failure_reason(fetched.stderr) + ' Nothing in your folder changed.'}
        local = self._local_name(cwd, branch)
        made = self.git(cwd, 'branch', '--no-track', '--', local, f'refs/remotes/{remote}/{branch}')
        if made.returncode != 0:
            return {'detail': f'Fetched {branch}, but Dervo couldn’t make a local branch of it: {clip(made.stderr)}'}
        return {'branch': branch, 'candidates': None, 'brought_back': local, 'state': 'ready',
                'detail': f'Brought back as {local}. Check it out or merge it when you’re ready. Your folder wasn’t touched.'}

    def _from_diff(self, row, binding):
        """Codex without a pushed branch: the task's diff applied to the run's base commit in a scratch worktree, committed
        as ``codex/cloud-<id>``. Your folder and checked-out branch aren't touched."""
        import tempfile
        import platform_os
        cwd = binding['cwd']
        refused = self._verified(binding)
        if refused:
            return {'detail': refused}
        try:
            diff = self.run(codex_diff_argv(self.codex(), row['remote_id']), cwd=self.neutral(), env=self.env(binding), capture_output=True,
                            text=True, errors='replace', timeout=READ_TIMEOUT * 3, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError, Rejected):
            return {'detail': 'Dervo couldn’t read the task’s diff from Codex cloud. Nothing in your folder changed.'}
        if len(diff.stdout or '') >= DIFF_LIMIT:
            return {'detail': 'The task’s diff is too large for Dervo to bring back. Apply it from chatgpt.com/codex.'}
        if diff.returncode != 0 or not (diff.stdout or '').strip():
            return {'detail': 'Codex cloud gave no diff for this task. ' + (clip(diff.stderr) or 'Nothing in your folder changed.')}
        if not row['base_sha']:
            return {'detail': 'Dervo doesn’t know which commit this run started from, so it can’t build the branch.'}
        local = self._local_name(cwd, f'codex/cloud-{row["remote_id"][-12:]}')
        with tempfile.TemporaryDirectory(prefix='dervo-cloud-', dir=platform_os.scratch_root()) as scratch:
            tree = str(Path(scratch) / 'tree')
            self.git(cwd, 'worktree', 'prune')  # a scratch tree left by a host that closed mid-build
            added = self.git(cwd, 'worktree', 'add', '--detach', '--', tree, row['base_sha'])
            if added.returncode != 0:
                return {'detail': 'Dervo couldn’t set up a scratch copy to build the branch. ' + clip(added.stderr)}
            try:
                applied = self._apply(tree, diff.stdout)
                if applied.returncode != 0:
                    return {'detail': f'The task’s diff doesn’t apply to {row["base_branch"]} as it was when the run started. '
                                      + clip(applied.stderr)}
                committed = self.git(tree, '-c', 'user.name=Dervo', '-c', 'user.email=dervo@localhost', 'commit', '--no-verify',
                                     '-m', f'Dervo: Codex cloud task {row["remote_id"]}\n\n{clip(row["task"], 600)}')
                if committed.returncode != 0:
                    return {'detail': 'Dervo couldn’t commit the task’s diff. ' + clip(committed.stderr)}
                made = self.git(tree, 'branch', '--no-track', '--', local, 'HEAD')
                if made.returncode != 0:
                    return {'detail': 'Dervo couldn’t name the branch. ' + clip(made.stderr)}
            finally:
                self.git(cwd, 'worktree', 'remove', '--force', '--', tree)
        return {'brought_back': local, 'detail': f'Brought back as {local}, built from the task’s diff on {row["base_branch"]}. '
                                                 'Your folder wasn’t touched.'}

    def _apply(self, tree, patch):
        return self.git(tree, 'apply', '--index', '--whitespace=nowarn', '-', stdin=patch)

    # ------------------------------------------------------------------ records
    def row(self, run_id):
        row = self.db.execute('select * from cloud_runs where id=?', (run_id,)).fetchone() if type(run_id) is str else None
        if row is None:
            raise Rejected('That cloud run isn’t in Dervo.')
        return row

    def settle(self):
        with self.lock:
            outcomes, self.outcomes = self.outcomes, {}
        if not outcomes:
            return
        columns = {'state', 'detail', 'remote_id', 'url', 'branch', 'candidates', 'known_heads', 'provider_status', 'summary',
                   'brought_back', 'checked_at', 'seen_at'}
        with self.db:
            for run_id, fields in outcomes.items():
                fields.pop('check_failed', None)  # a failed check only moves checked_at; the view says when Dervo last saw it
                fields = {k: v for k, v in fields.items() if k in columns}
                if fields.get('state') not in (None, *STATES):
                    fields.pop('state')
                if fields.get('branch') and not fields.get('brought_back') and self.db.execute(
                        'select 1 from cloud_runs where branch=? and id<>?', (fields['branch'], run_id)).fetchone():
                    # Two checks finished together and both saw the same new branch: neither gets it on a guess.
                    fields.update(branch=None, candidates=json.dumps([fields['branch']]),
                                  detail='A new cloud branch appeared that another run may own. Pick it if it’s this run’s.')
                fields['updated_at'] = self.clock()
                self.db.execute(f'update cloud_runs set {", ".join(f"{k}=?" for k in fields)} where id=?', (*fields.values(), run_id))
                self._moved(run_id)

    def _moved(self, run_id):
        """Inside the caller's write: a run sent from a conversation moves that conversation's row on Home. The state
        spine's ``conversation_activity.bump`` (b19/state-spine) does that; before it lands there's nothing to move."""
        row = self.db.execute('select conversation_id from cloud_runs where id=?', (run_id,)).fetchone()
        if row and row[0]:
            import conversation_activity
            bump = getattr(conversation_activity, 'bump', None)
            if bump is not None:
                bump(self.db, row[0])

    def _reconcile(self):
        """At start: a launch the last host didn't finish is unknown, never sent again (it may have started)."""
        with self.db:
            for row in self.db.execute("select id, provider from cloud_runs where state='starting'").fetchall():
                where = 'claude.ai/code' if row[1] == 'claude' else 'chatgpt.com/codex'
                self.db.execute("update cloud_runs set state='unknown', detail=?, updated_at=? where id=?",
                                (LAUNCH_LOST.format(where=where), self.clock(), row[0]))
                self._moved(row[0])

    def runs(self, project_id):
        if type(project_id) is not str:
            raise Rejected('Choose a project')
        rows = self.db.execute('select * from cloud_runs where project_id=? order by created_at desc limit 50', (project_id,)).fetchall()
        for row in rows:
            if row['state'] in OPEN_STATES and row['state'] != 'starting':
                try:
                    self.check(row['id'], force=False)
                except Rejected:
                    pass  # the row says what it can; Check now gives the reason
        provider = self.db.execute('select p.provider from projects j join profiles p on p.id=j.profile_id where j.id=?',
                                   (project_id,)).fetchone()
        return {'runs': [self.view(row) for row in rows], 'environment': self.environment(project_id),
                'provider': provider[0] if provider else None}

    def view(self, row):
        """The record as the screen shows it, in the shape the state spine's run records use (kind, state, line,
        lastSeenAt). A Codex status older than STALE_SECONDS reads unknown: Dervo never shows working from memory."""
        state, line = observed(row, self.clock())
        with self.lock:
            busy = row['id'] in self.busy
        return {'id': row['id'], 'kind': 'cloud', 'provider': row['provider'], 'where': PROVIDER_WORDS[row['provider']],
                'projectId': row['project_id'], 'conversationId': row['conversation_id'], 'task': row['task'][:500],
                'state': state, 'line': line, 'summary': row['summary'], 'providerStatus': row['provider_status'],
                'link': row['url'], 'remoteId': row['remote_id'], 'baseBranch': row['base_branch'], 'branch': row['branch'],
                'candidates': json.loads(row['candidates']) if row['candidates'] else [], 'broughtBack': row['brought_back'],
                'account': row['identity'], 'createdAt': row['created_at'], 'lastCheckedAt': row['checked_at'],
                'lastSeenAt': row['seen_at'], 'checking': busy,
                'following': row['state'] in OPEN_STATES and not row['brought_back'] and line != STOPPED_FOLLOWING.format(days=FOLLOW_DAYS)
                             and (row['state'] == 'starting' or self.learnable(row)),
                'canFollowUp': row['provider'] == 'claude' and bool(row['remote_id']),
                'canCheck': state in ('working', 'unknown') and not row['brought_back'],
                'canBringBack': bool(row['branch'] or (row['provider'] == 'codex' and state == 'ready' and row['remote_id']))
                                and not row['brought_back']}


def latest_for_thread(db, thread_id, *, clock=now):
    """The newest cloud run sent from this conversation, in the record shape ``CloudRuns.view`` gives, or None. For the
    state spine's thread record (thread_states), so Home can say "In Claude's cloud, unknown" on the thread's row."""
    try:
        row = db.execute('select * from cloud_runs where conversation_id=? order by created_at desc limit 1', (thread_id,)).fetchone()
    except Exception:  # noqa: BLE001 - no table yet means no cloud run
        return None
    if row is None:
        return None
    state, line = observed(row, clock())
    return {'id': row['id'], 'kind': 'cloud', 'provider': row['provider'], 'where': PROVIDER_WORDS[row['provider']],
            'state': state, 'line': line, 'link': row['url'], 'branch': row['branch'], 'broughtBack': row['brought_back'],
            'lastSeenAt': row['seen_at'], 'lastCheckedAt': row['checked_at']}
