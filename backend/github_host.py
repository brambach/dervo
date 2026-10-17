"""GitHub from a finished thread, with the user's own gh sign-in (beta.16, b16/git).

Design: evidence/b16-git/survey.md, sections 3A, 4 and 7, and the thread mock Bryce approved on 25 Sep
(evidence/design-tests/b16-git/thread-pr-*). The operation names and fields are b16/bridge's (shared_operations.py);
this module is their handler, ``shared_dispatch``.

* ``github_status``: is gh installed, new enough and signed in, and as whom. When it isn't, the answer carries the
  command that fixes it, for Run in the project terminal, where the click is the consent. Dervo never installs gh or
  signs in on its own, never runs ``gh auth token`` and never holds a GitHub token.
* ``pr_preview``: what the confirm shows, for one of two ways: ``thread`` puts only this thread's commits on a new
  branch made from the repository's default branch, without touching your folder or checked-out branch (the default
  choice); ``branch`` opens it from the current branch, with everything on it. It names both outward steps, the
  repository and the account, and hands out a one-use id that ties the click to exactly that.
* ``pr_create``: the one "Push and open pull request" click. It re-checks the account and what goes, then pushes and
  opens the pull request on a worker thread, since a push can take longer than the bridge lets a request run. It's
  never part of accepting a result.
* ``pr_status``: the thread's pull request as Dervo last saw it, and with ``refresh`` read again from GitHub as you:
  its state, checks and the reviewers' words, quoted. A read that fails keeps the last answer and its time. Nothing is
  guessed, and a create whose result Dervo lost is settled from GitHub before anything is tried again.
* ``pr_thread_branch``: what a branch of only this thread's commits would hold, or the commits it depends on that
  aren't on GitHub. It writes git objects only: never a ref, the index or a file in your folder.

gh runs from a fixed location with a clean environment (no GH_TOKEN, GITHUB_TOKEN or GH_HOST, so it's the Keychain
login), from a neutral folder, so the project's own git config can't steer it. Git runs through task_git and
task_push's hardening, as the push does, and the push signs in through gh as the confirmed account and nothing else
(``push_as``). Merging, closing, commenting and force-pushing aren't here.
"""
import calendar
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import secret_scan
import task_git
import task_push
from dervo_connections import Rejected

SHARED = frozenset({'github_status', 'pr_preview', 'pr_create', 'pr_status', 'pr_thread_branch'})
MODES = ('thread', 'branch')
GH_PATHS = ('/opt/homebrew/bin/gh', '/usr/local/bin/gh')  # fixed places only, the way claude is found; no PATH lookup
BREW_PATHS = ('/opt/homebrew/bin/brew', '/usr/local/bin/brew')
# Every gh call here is ``gh api`` (REST) or ``gh --version``. ``gh api`` with --hostname, -X and --input has been
# there since gh 1.0; 2.0 (Aug 2021) is a floor with room, not a feature line.
MIN_VERSION = (2, 0, 0)
INSTALL = 'brew install gh'
UPGRADE = 'brew upgrade gh'
INSTALL_PAGE = 'https://cli.github.com'
BUDGET = 7.0          # one request's share of the bridge's ten seconds, for every gh and git call in it
READ_TIMEOUT = 6.0
WRITE_TIMEOUT = 60
STATUS_SECONDS = 300  # a status answer is reused this long unless you ask to check again
REFRESH_SECONDS = 20  # a PR isn't read from GitHub more often than this, however often the screen asks
CONFIRM_SECONDS = 600
COMMENT_CHARS = 600
COMMENTS = 12
TITLE_CHARS = 120
BRANCH_PREFIX = 'dervo/'

NO_GH = 'The GitHub CLI (gh) isn’t installed on this Mac.'
OLD_GH = 'This gh is older than {min}. Update it, then check again.'
SIGNED_OUT = 'gh isn’t signed in to {host}. Dervo uses gh’s own sign-in and never sees the token.'
NOT_GITHUB = 'This project’s remote isn’t on GitHub, so Dervo can’t open a pull request for it.'
NO_COMMITS = 'This thread has nothing committed yet. Approve and commit its result first.'
ALL_THERE = 'Everything this thread committed is already on {base}.'
ON_BASE = '{branch} is the branch pull requests go into, so everything on it can’t be one. Pick This thread only.'
OPEN_ALREADY = 'Pull request #{number} is already open from {head}.'
RECONFIRM = 'Reload and confirm again.'
ACCOUNT_CHANGED = 'GitHub now says you’re signed in as {now}, not {then}. Nothing was pushed. ' + RECONFIRM
DRAFT_REFUSED = 'GitHub doesn’t allow draft pull requests in {repo}. {head} is on GitHub; open it as a ready pull request?'
RESTARTED = 'Dervo restarted while this was running, so it doesn’t know how it ended.'
LOST = 'Dervo lost track of this before GitHub answered.'
MERGE_DRIVER = 'This project’s git config names a merge driver, so Dervo won’t build a one-thread branch. Pick Everything on {branch}.'
SIGN_IN_HEADER = ('This project’s git config adds its own sign-in header for {host}, so the push might not go as {login}. '
                  'Push from Terminal, or remove the http extraheader setting.')
NOT_AS = 'Git would still push somewhere other than {repo} over https, so Dervo can’t make it go as {login}. Push from Terminal.'
PUSH_REFUSED_AS = 'GitHub didn’t take gh’s sign-in as {login} for the push. Nothing was pushed. Check gh auth status in Terminal.'
SLUG = re.compile(r'[^a-z0-9]+')
_HTTPS = re.compile(r'^https://(?:[^@/]+@)?([^/:]+)(?::\d+)?/([^/]+)/([^/]+?)(?:\.git)?/?$')
_SSH = re.compile(r'^(?:ssh://)?(?:[^@/]+@)?([^/:]+)(?::\d+)?[:/]([^/]+)/([^/]+?)(?:\.git)?/?$')


def scrub(text, limit=None):
    text = secret_scan.redact(text or '')
    return text[:limit] if limit else text


# ---------------------------------------------------------------- finding gh

def find_gh():
    """gh's path, or None. Only the fixed places a Mac installs it."""
    return next((path for path in GH_PATHS if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def find_brew():
    return next((path for path in BREW_PATHS if os.path.isfile(path) and os.access(path, os.X_OK)), None)


def gh_env():
    """What gh gets: your HOME (for its config and the Keychain) and nothing that could name another account."""
    return {'PATH': '/usr/bin:/bin', 'HOME': os.path.expanduser('~'), 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
            'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'GH_PAGER': 'cat', 'NO_COLOR': '1', 'GH_SPINNER_DISABLED': '1'}


def parse_version(text):
    match = re.search(r'gh version (\d+)\.(\d+)\.(\d+)', text or '')
    return tuple(int(part) for part in match.groups()) if match else None


def repo_from_url(url):
    """(host, 'owner/repo') for a GitHub-shaped remote URL, else None. Any host: GitHub Enterprise answers gh the same way."""
    if not isinstance(url, str):
        return None
    for pattern in (_HTTPS, _SSH):
        match = pattern.match(url.strip())
        if match:
            host, owner, name = match.groups()
            if host and owner and name and not owner.startswith('-'):
                return host.lower(), f'{owner}/{name}'
    return None


def slug(name, thread_id):
    words = SLUG.sub('-', (name or '').lower()).strip('-')[:40].strip('-')
    return words or f'thread-{thread_id[:8]}'


class Clock:
    """One request's time budget, shared by every call in it."""

    def __init__(self, seconds=BUDGET):
        self.until = time.monotonic() + seconds

    def left(self, cap):
        left = self.until - time.monotonic()
        if left < 0.5:
            raise subprocess.TimeoutExpired('budget', 0)
        return min(cap, left)


class GitHubError(Exception):
    """gh ran and GitHub said no, or gh couldn't reach it. ``kind``: signed_out, not_found, draft, network, other."""

    def __init__(self, kind, detail, status=None):
        super().__init__(detail)
        self.kind, self.detail, self.status = kind, detail, status


def classify(returncode, stdout, stderr):
    text = f'{stderr or ""}\n{stdout or ""}'
    status = re.search(r'\(HTTP (\d{3})\)', text)
    status = int(status.group(1)) if status else None
    lower = text.lower()
    if status == 401 or 'gh auth login' in lower or 'not logged' in lower or 'authentication required' in lower:
        return GitHubError('signed_out', 'gh isn’t signed in.', status)
    if 'draft pull requests are not supported' in lower or ('draft' in lower and status == 422):
        return GitHubError('draft', 'Draft pull requests aren’t allowed here.', status)
    if status == 404:
        return GitHubError('not_found', 'GitHub says it isn’t there, or this account can’t see it.', status)
    if 'could not resolve' in lower or 'dial tcp' in lower or 'timeout' in lower or 'connection' in lower or 'no such host' in lower:
        return GitHubError('network', 'No connection to GitHub.', status)
    message = None
    try:
        answer = json.loads(stdout or 'null')
    except ValueError:
        answer = None
    if isinstance(answer, dict):
        message = answer.get('message') if isinstance(answer.get('message'), str) else None
        errors = answer.get('errors')
        first = errors[0] if isinstance(errors, list) and errors and isinstance(errors[0], dict) else {}
        if isinstance(first.get('message'), str):
            message = f"{message}: {first['message']}" if message else first['message']
    lines = [line.strip() for line in (stderr or '').splitlines() if line.strip()]
    return GitHubError('other', scrub(message or (lines[-1] if lines else f'gh ended with {returncode}.'), 200), status)


class Gh:
    """gh, found at a fixed place, run with gh_env from a neutral folder."""

    def __init__(self, path, run=subprocess.run):
        self.path, self.run = path, run

    def version(self, timeout):
        result = self.run([self.path, '--version'], cwd=tempfile.gettempdir(), env=gh_env(), capture_output=True, text=True, timeout=timeout)
        return parse_version(result.stdout) if result.returncode == 0 else None

    def api(self, host, path, *, method='GET', body=None, timeout=READ_TIMEOUT):
        argv = [self.path, 'api', '--hostname', host, '-X', method, '-H', 'Accept: application/vnd.github+json', path]
        if body is not None:
            argv += ['--input', '-']
        try:
            result = self.run(argv, cwd=tempfile.gettempdir(), env=gh_env(), capture_output=True, text=True, timeout=timeout,
                              **({'input': json.dumps(body)} if body is not None else {}))
        except subprocess.TimeoutExpired:
            raise
        except OSError as exc:
            raise GitHubError('other', f'gh could not run: {exc.strerror or exc}') from None
        if result.returncode != 0:
            raise classify(result.returncode, result.stdout, result.stderr)
        try:
            return json.loads(result.stdout or 'null')
        except ValueError:
            raise GitHubError('other', 'gh answered with something that isn’t JSON.') from None


# ---------------------------------------------------------------- git, locally

def _git(cwd, *args, run=subprocess.run, timeout=task_git.TIMEOUT, env=None, stdin=None, text=True):
    return task_git._git(cwd, *args, run=run, timeout=timeout, env=env, stdin=stdin, text=text)


def _out(result):
    return (result.stdout or '').strip() if result.returncode == 0 else ''


def push_url(host, repo):
    """Where a push goes: the confirmed repository over https, whatever transport the remote names."""
    return f'https://{host}/{repo}.git'


def push_as(cwd, gh_path, live, run=subprocess.run):
    """``-c`` pairs, after task_push.hardening, that make a push sign in through gh as the confirmed account and nothing
    else (security review S3, 26 Sep): every credential helper hardening kept is dropped, gh's ``auth git-credential`` is
    the only one, and it's asked for that user, which gh refuses when its active account on the host is another. The
    remote's push URL is pointed at the repository over https, so an SSH remote goes the same way instead of with
    whichever key ssh offers, and a URL with a token in it goes without the token. Dervo never reads or stores the token:
    git asks gh and hands it to the transport.

    Returns (pairs, None), or (None, why) when git would still reach GitHub some other way: a sign-in header in git
    config, or a push URL that doesn't come out as the repository."""
    target = push_url(live['host'], live['repo'])
    scheme, _, rest = target.partition('//')
    pairs = ['credential.helper=', f"credential.helper=!'{gh_path}' auth git-credential",
             f'credential.{scheme}//{rest.split("/", 1)[0]}.username={live["login"]}']
    try:
        # The remote's own URLs as written, before any rewrite: a push uses pushInsteadOf on ``url`` when there's no
        # ``pushurl``, and insteadOf on ``pushurl`` when there is. Each, matched in full, is the longest match there is.
        raw = [line for key in ('url', 'pushurl')
               for line in _out(_git(cwd, 'config', '--get-all', f'remote.{live["remote"]}.{key}', run=run)).splitlines() if line]
        for url in dict.fromkeys(raw + [live['url']]):
            pairs += [f'url.{target}.insteadOf={url}', f'url.{target}.pushInsteadOf={url}']
        args = [arg for pair in pairs for arg in ('-c', pair)]
        headers = _git(cwd, *args, 'config', '--get-regexp', r'^http\..*extraheader$', run=run)
        urls = _git(cwd, *task_push.hardening(cwd, run=run), *args, 'remote', 'get-url', '--push', '--all', '--', live['remote'], run=run)
    except (OSError, subprocess.SubprocessError):
        return None, NOT_AS.format(repo=live['repo'], login=live['login'])
    for line in _out(headers).splitlines():
        key, _, value = line.partition(' ')
        # An empty value is git's reset; a header scoped to another host can't reach this push.
        if value.strip() and (key.lower() == 'http.extraheader' or live['host'] in key.lower()):
            return None, SIGN_IN_HEADER.format(host=live['host'], login=live['login'])
    if _out(urls).splitlines() != [target]:
        return None, NOT_AS.format(repo=live['repo'], login=live['login'])
    return args, None


def push_env():
    """What gh gets when git runs it as the credential helper, on top of task_git's clean env."""
    return {key: value for key, value in gh_env().items() if key.startswith('GH_') or key == 'NO_COLOR'}


def default_branch(cwd, remote, run=subprocess.run):
    """The remote's default branch as this Mac knows it (refs/remotes/<remote>/HEAD), else main or master when this Mac
    has that remote branch. None when it can't tell locally."""
    head = _out(_git(cwd, 'symbolic-ref', '--quiet', f'refs/remotes/{remote}/HEAD', run=run))
    if head.startswith(f'refs/remotes/{remote}/'):
        return head[len(f'refs/remotes/{remote}/'):]
    for name in ('main', 'master'):
        if _git(cwd, 'rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{name}', run=run).returncode == 0:
            return name
    return None


def remote_ref(cwd, remote, branch, run=subprocess.run):
    return _out(_git(cwd, 'rev-parse', '--verify', '--quiet', f'refs/remotes/{remote}/{branch}^{{commit}}', run=run)) or None


def merge_drivers(cwd, run=subprocess.run):
    """Merge drivers the repository's own config names. A replay never runs one, but a branch built where one is set
    wouldn't be what git would build, so Dervo says so instead."""
    try:
        listing = run([task_git.GIT, 'config', '--show-scope', '--get-regexp', r'^merge\..*\.driver$'], cwd=cwd, env=task_git._env(),
                      capture_output=True, text=True, timeout=task_git.TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = (listing.stdout or '').splitlines() if listing.returncode == 0 else []
    return [line.split()[1] for line in lines if line.split() and line.split()[0] in task_git.REPO_SCOPES and len(line.split()) > 1]


def thread_commits(db, project_id, thread_id):
    """This thread's live commits from Dervo's records, oldest first: [(sha, task_id)]. An undone task's commit and its
    undo cancel out, so neither goes."""
    found = []
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
        return found
    for task_id, document in db.execute('SELECT id, document FROM coding_tasks WHERE instr(document, ?)>0', (thread_id,)).fetchall():
        try:
            task = json.loads(document)
        except ValueError:
            continue
        context = task.get('context') or {}
        record = task.get('commit') or {}
        if context.get('thread_id') != thread_id or context.get('project_id') not in (project_id, None):
            continue
        sha = record.get('sha')
        if isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha) and not record.get('reverted'):
            found.append((int(record.get('at') or 0), sha, task_id))
    return [(sha, task_id) for _, sha, task_id in sorted(found)]


def describe(db, cwd, shas, run=subprocess.run):
    """[{sha, short, subject, channelId, thread, outside}] for these commits, in the order given."""
    index = task_push._task_index(db)
    rows = []
    for sha in shas:
        subject = _out(_git(cwd, 'log', '-1', '--format=%s', sha, run=run))
        made = index.get(sha)
        rows.append(dict(sha=sha, short=sha[:7], subject=scrub(subject, 200), channelId=made['thread_id'] if made else None,
                         thread=made['thread_name'] if made else None, outside=made is None))
    return rows


def replay(cwd, shas, onto, run=subprocess.run):
    """This thread's commits carried onto ``onto`` one at a time, the way a cherry-pick would, without touching your
    folder, index or any ref: each commit's change is merged three ways in a temporary index (its parent as the base,
    the tip so far as ours), file conflicts go through ``git merge-file`` on the blobs, and the result is a new commit
    object with the original's message and author. Returns dict(tip, made=[new shas]) or dict(conflict=sha,
    paths=[...]). Works with git 2.39 (no ``merge-tree --merge-base``)."""
    tip, made = onto, []
    with tempfile.TemporaryDirectory(prefix='dervo-pr-') as temp:
        for sha in shas:
            parents = _out(_git(cwd, 'rev-list', '--parents', '-n', '1', sha, run=run)).split()[1:]
            if len(parents) != 1:
                return dict(conflict=sha, paths=[], why='merge')
            env = {'GIT_INDEX_FILE': os.path.join(temp, f'index-{sha[:12]}')}
            read = _git(cwd, 'read-tree', '-m', '-i', '--aggressive', parents[0], tip, sha, run=run, env=env)
            if read.returncode != 0:
                return dict(conflict=sha, paths=[], why='read')
            unmerged = {}
            for line in _out(_git(cwd, 'ls-files', '-u', '-z', run=run, env=env)).split('\0'):
                meta, _, path = line.partition('\t')
                if not path:
                    continue
                mode, blob, stage = meta.split()
                unmerged.setdefault(path, {})[int(stage)] = (mode, blob)
            conflicts = []
            for path, stages in unmerged.items():
                if not {1, 2, 3} <= set(stages):
                    conflicts.append(path)  # added on one side, removed on the other: a person decides that
                    continue
                modes = {stage: stages[stage][0] for stage in stages}
                mode = modes[3] if modes[2] == modes[1] else modes[2] if modes[3] == modes[1] else None
                if mode is None or modes[2] != modes[3] and modes[2] != modes[1] and modes[3] != modes[1]:
                    conflicts.append(path)
                    continue
                files = []
                for stage in (2, 1, 3):
                    target = os.path.join(temp, f'stage{stage}')
                    data = _git(cwd, 'cat-file', 'blob', stages[stage][1], run=run, text=False)
                    if data.returncode != 0:
                        return dict(conflict=sha, paths=[path], why='read')
                    with open(target, 'wb') as handle:
                        handle.write(data.stdout)
                    files.append(target)
                merged = _git(cwd, 'merge-file', '-p', *files, run=run, text=False)
                if merged.returncode != 0:
                    conflicts.append(path)
                    continue
                blob = _git(cwd, 'hash-object', '-w', '--stdin', run=run, text=False, stdin=merged.stdout)
                if blob.returncode != 0 or _git(cwd, 'update-index', '--cacheinfo', f'{mode},{blob.stdout.decode().strip()},{path}', run=run, env=env).returncode != 0:
                    return dict(conflict=sha, paths=[path], why='write')
            if conflicts:
                return dict(conflict=sha, paths=sorted(conflicts), why='lines')
            tree = _out(_git(cwd, 'write-tree', run=run, env=env))
            if not tree:
                return dict(conflict=sha, paths=[], why='write')
            author = _out(_git(cwd, 'log', '-1', '--format=%an%x00%ae%x00%aI', sha, run=run)).split('\0')
            body = _git(cwd, 'log', '-1', '--format=%B', sha, run=run)
            who = dict(GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1], GIT_AUTHOR_DATE=author[2]) if len(author) == 3 else {}
            commit = _out(_git(cwd, 'commit-tree', tree, '-p', tip, run=run, env=who, stdin=body.stdout if body.returncode == 0 else sha))
            if not commit:
                return dict(conflict=sha, paths=[], why='write')
            tip = commit
            made.append(commit)
    return dict(tip=tip, made=made)


def depends_on(db, cwd, base_ref, sha, paths, run=subprocess.run):
    """Commits before ``sha`` that aren't on the base and touch the same files: what the thread's change depends on."""
    if not paths:
        return []
    listed = _out(_git(cwd, 'log', '--format=%H', '--max-count=20', f'{base_ref}..{sha}^', '--', *paths, run=run)).split()
    return describe(db, cwd, listed, run=run)


def on_remotes(cwd, shas, run=subprocess.run, timeout=task_git.TIMEOUT):
    """{sha: True when a remote-tracking branch on this Mac holds it, False when none does}. Empty when git can't say,
    so the screen never guesses. As of this Mac's last push or fetch, not a call to GitHub. Three git calls however
    many commits: History asks for all of a page at once."""
    shas = [sha for sha in dict.fromkeys(shas) if isinstance(sha, str) and re.fullmatch(r'[0-9a-f]{40}', sha)]
    if not shas:
        return {}
    try:
        if not _out(_git(cwd, 'for-each-ref', '--count=1', '--format=%(refname)', 'refs/remotes', run=run, timeout=timeout)):
            return {}
        checked = _git(cwd, 'cat-file', '--batch-check=%(objectname) %(objecttype)', run=run, timeout=timeout, stdin=''.join(f'{sha}\n' for sha in shas))
        present = [line.split()[0] for line in (checked.stdout or '').splitlines() if line.endswith(' commit')] if checked.returncode == 0 else []
        if not present:
            return {}
        local = _git(cwd, 'rev-list', '--max-count=5000', *present, '--not', '--remotes', run=run, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return {}
    if local.returncode != 0:
        return {}
    only_here = set((local.stdout or '').split())
    return {sha: sha not in only_here for sha in present}


# ---------------------------------------------------------------- records

def ensure_tables(db):
    with db:
        db.execute('CREATE TABLE IF NOT EXISTS pr_records (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, '
                   'document TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS github_accounts (project_id TEXT NOT NULL, host TEXT NOT NULL, login TEXT NOT NULL, '
                   'draft_ok INTEGER, at INTEGER NOT NULL, PRIMARY KEY (project_id, host))')


def latest_prs(db, project_id):
    """{thread id: its newest pull request record} for History. Read from Dervo's own records; never calls GitHub."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pr_records'").fetchone():
        return {}
    found = {}
    for thread_id, document in db.execute('SELECT thread_id, document FROM pr_records WHERE project_id=? ORDER BY rowid', (project_id,)):
        try:
            record = json.loads(document)
        except ValueError:
            continue
        found[thread_id] = record
    return found


def pull_request(record):
    """The PullRequest view of a record (api.ts), or None before GitHub gave it a number."""
    if not record or not record.get('number'):
        return None
    seen = record.get('seen') or {}
    return dict(number=record['number'], url=record.get('url'), repo=record.get('repo'), base=record.get('base'), head=record.get('head'),
                draft=bool(seen.get('draft', record.get('draft'))), state=seen.get('state') or 'open', review=seen.get('review'),
                checks=seen.get('checks'), comments=seen.get('comments') or [], reviewers=seen.get('reviewers') or {},
                checkedAt=record.get('checked_at'), checkedAs=record.get('checked_as'), mode=record.get('mode'),
                commits=record.get('commits') or [], checkError=record.get('check_error'))


def attempt(record):
    if not record:
        return None
    return dict(id=record['id'], state=record['state'], step=record.get('step'), detail=record.get('detail'), head=record.get('head'),
                repo=record.get('repo'), base=record.get('base'), draft=record.get('draft'), mode=record.get('mode'),
                startedAt=record.get('started_at'), finishedAt=record.get('finished_at'), dependsOn=record.get('depends_on') or [])


# ---------------------------------------------------------------- the host's one GitHub worker

class GitHub:
    """One per host (``for_host``): the gh status cache, the confirm ids, the worker and the pull request records."""

    def __init__(self, host, *, run=subprocess.run, clock=time.time):
        self.host, self.store, self.db, self.run, self.clock = host, host.store, host.store.db, run, clock
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='dervo-github')
        self.futures = {}      # record id -> future
        self.previews = {}     # preview id -> what the confirm showed
        self.statuses = {}     # host -> last status answer
        self.steps = {}        # record id -> (step, words) while its worker runs
        self.lock = threading.Lock()
        self.closed = False
        ensure_tables(self.db)
        for record_id, document in self.db.execute('SELECT id, document FROM pr_records').fetchall():
            record = json.loads(document)
            if record.get('state') == 'running':
                self._save(dict(record, state='unknown', detail=RESTARTED, finished_at=int(self.clock())))

    # ------------------------------------------------------------ plumbing
    def _save(self, record):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO pr_records VALUES (?,?,?,?)',
                            (record['id'], record['project_id'], record['thread_id'], json.dumps(record)))
        return record

    def _record(self, record_id):
        row = self.db.execute('SELECT document FROM pr_records WHERE id=?', (record_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest(self, project_id, thread_id):
        row = self.db.execute('SELECT document FROM pr_records WHERE project_id=? AND thread_id=? ORDER BY rowid DESC LIMIT 1',
                              (project_id, thread_id)).fetchone()
        return json.loads(row[0]) if row else None

    def _project(self, project_id):
        row = self.db.execute('SELECT cwd FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise Rejected('Unknown project')
        if self.db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (project_id,)).fetchone():
            raise Rejected('This project is archived. Restore it before opening its records.')
        return row[0]

    def _thread(self, project_id, thread_id, actor):
        row = self.db.execute("SELECT c.name FROM threads t LEFT JOIN local_channels c ON c.id=t.id WHERE t.id=? AND t.project_id=? "
                              "AND (t.visibility='shared' OR t.owner=?)", (thread_id, project_id, actor)).fetchone()
        if row is None:
            raise Rejected('Unknown conversation')
        name = row[0] if isinstance(row[0], str) and row[0].strip() and row[0] != task_git.PLACEHOLDER_NAME else None
        return name

    def gh(self):
        path = find_gh()
        return Gh(path, run=self.run) if path else None

    def tick(self):
        for record_id, future in list(self.futures.items()):
            if future.done():
                self.futures.pop(record_id, None)
                self.steps.pop(record_id, None)
                record = self._record(record_id)
                if record is None or record.get('state') != 'running':
                    continue
                try:
                    outcome = future.result()
                except Exception:  # noqa: BLE001 - the worker catches its own; anything else is an unknown result
                    outcome = dict(state='unknown', detail=LOST)
                self._save(dict(record, **outcome, finished_at=int(self.clock())))
                draft_ok = 0 if outcome.get('state') == 'draft_refused' else 1 if outcome.get('state') == 'opened' and record.get('draft') else None
                if draft_ok is not None:
                    # Remembered per project: a repository that refused a draft isn't offered one again.
                    with self.db:
                        self.db.execute('UPDATE github_accounts SET draft_ok=? WHERE project_id=? AND host=?', (draft_ok, record['project_id'], record['host']))

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.tick()
        self.worker.shutdown(wait=False, cancel_futures=False)

    # ------------------------------------------------------------ status
    def status(self, project_id, recheck=False, clock=None):
        """GitHubStatus (api.ts). Reads: gh --version and ``gh api user``. Nothing else."""
        clock = clock or Clock()
        cwd = self._project(project_id)
        state = task_push.remote_state(cwd, run=self.run)
        repo = repo_from_url(state.get('url')) if state.get('url') else None
        host = repo[0] if repo else 'github.com'
        brew = find_brew()
        sign_in = f'gh auth login --hostname {host} --web'
        base = dict(host=host, login=None, version=None, installCommand=None, signInCommand=None, checkedAt=None,
                    repo=repo[1] if repo else None, remote=state.get('remote'), branch=state.get('branch'), installPage=INSTALL_PAGE)
        gh = self.gh()
        if gh is None:
            return dict(base, state='missing', detail=NO_GH + (' Install it with Homebrew, then sign in.' if brew else f' Install it from {INSTALL_PAGE}, then sign in.'),
                        installCommand=INSTALL if brew else None, signInCommand=sign_in)
        cached = self.statuses.get((gh.path, host))
        if cached and not recheck and self.clock() - (cached.get('checkedAt') or 0) < STATUS_SECONDS and cached['state'] == 'ready':
            return dict(cached, repo=base['repo'], remote=base['remote'], branch=base['branch'])
        try:
            version = gh.version(clock.left(READ_TIMEOUT))
        except (OSError, subprocess.SubprocessError):
            version = None
        shown = '.'.join(map(str, version)) if version else None
        if version is None:
            return dict(base, state='unknown', detail='gh didn’t say which version it is. Check that it runs in Terminal.', version=None)
        if version < MIN_VERSION:
            return dict(base, state='too_old', version=shown, detail=OLD_GH.format(min='.'.join(map(str, MIN_VERSION))),
                        installCommand=UPGRADE if brew else None)
        try:
            user = gh.api(host, 'user', timeout=clock.left(READ_TIMEOUT))
        except subprocess.TimeoutExpired:
            return self._unknown(base, shown, cached, 'GitHub didn’t answer in time.')
        except GitHubError as error:
            if error.kind == 'signed_out':
                answer = dict(base, state='signed_out', version=shown, detail=SIGNED_OUT.format(host=host), signInCommand=sign_in)
                self.statuses[(gh.path, host)] = dict(answer, checkedAt=int(self.clock()))
                return answer
            return self._unknown(base, shown, cached, error.detail)
        login = user.get('login') if isinstance(user, dict) and isinstance(user.get('login'), str) else None
        if not login:
            return self._unknown(base, shown, cached, 'GitHub didn’t say which account this is.')
        answer = dict(base, state='ready', version=shown, login=login, detail=f'Signed in to {host} as {login}.', checkedAt=int(self.clock()))
        self.statuses[(gh.path, host)] = answer
        return answer

    def _unknown(self, base, version, cached, why):
        """A check that couldn't finish: says so, with when it last worked, never the last answer as if it were new."""
        last = cached.get('checkedAt') if cached and cached.get('state') == 'ready' else None
        return dict(base, state='unknown', version=version, login=cached.get('login') if last else None, checkedAt=last,
                    detail=scrub(why, 200) + (' Showing what Dervo last saw.' if last else ''))

    # ------------------------------------------------------------ planning
    def plan(self, project_id, thread_id, mode, actor, clock):
        """Everything a confirm names, from local git and two or three reads from GitHub. No refs change."""
        cwd = self._project(project_id)
        name = self._thread(project_id, thread_id, actor)
        title = scrub(name or 'Pull request from Dervo', TITLE_CHARS)
        state = task_push.remote_state(cwd, run=self.run)
        out = dict(mode=mode, repo=None, host=None, account=None, base=None, head=None, title=title, draft=True, commits=[],
                   pushNeeded=False, fetched=False, available=False, reason=None, branch=state.get('branch'), dependsOn=[],
                   others=0, outside=0, previousAccount=None, remote=state.get('remote'), url=state.get('url'))
        if not state['git']:
            return dict(out, reason=task_git.NOT_A_REPO + '. A pull request needs one.'), None
        if not state['remote'] or not state['url']:
            return dict(out, reason=state['detail'] or task_push.NO_REMOTE), None
        repo = repo_from_url(state['url'])
        fetch_url = _out(_git(cwd, 'remote', 'get-url', '--', state['remote'], run=self.run))
        if repo is None or repo != repo_from_url(fetch_url):
            return dict(out, reason=NOT_GITHUB if repo is None else 'This remote pushes somewhere other than the repository it reads from. '
                        'Open the pull request from Terminal.'), None
        host, full = repo
        out.update(repo=full, host=host)
        status = self.status(project_id, clock=clock)
        if status['state'] != 'ready':
            return dict(out, reason=status['detail'], signIn=status.get('signInCommand'), install=status.get('installCommand'),
                        ghState=status['state']), None
        out['account'] = status['login']
        remembered = self.db.execute('SELECT login, draft_ok FROM github_accounts WHERE project_id=? AND host=?', (project_id, host)).fetchone()
        if remembered and remembered[0] != status['login']:
            out['previousAccount'] = remembered[0]
        if remembered and remembered[1] == 0:
            out['draft'] = False
        gh = self.gh()
        try:
            about = gh.api(host, f'repos/{full}', timeout=clock.left(READ_TIMEOUT))
        except subprocess.TimeoutExpired:
            return dict(out, reason='GitHub didn’t answer in time. Try again.'), None
        except GitHubError as error:
            return dict(out, reason=f'{full}: {error.detail}' if error.kind != 'signed_out' else SIGNED_OUT.format(host=host)), None
        base = about.get('default_branch') if isinstance(about, dict) and isinstance(about.get('default_branch'), str) else None
        base = base or default_branch(cwd, state['remote'], run=self.run)
        if not base:
            return dict(out, reason='Dervo couldn’t tell which branch pull requests go into.'), None
        permissions = about.get('permissions') if isinstance(about, dict) else None
        if isinstance(permissions, dict) and not (permissions.get('push') or permissions.get('admin') or permissions.get('maintain')):
            return dict(out, base=base, reason=f'{status["login"]} can’t push to {full}. Open the pull request from a fork in Terminal.'), None
        out['base'] = base
        owner = full.split('/', 1)[0]
        live = dict(project_id=project_id, thread_id=thread_id, mode=mode, cwd=cwd, host=host, repo=full, owner=owner, login=status['login'],
                    base=base, remote=state['remote'], url=state['url'], title=title, name=name)
        if mode == 'branch':
            return self._plan_branch(out, live, state, clock)
        return self._plan_thread(out, live, state, clock)

    def _plan_branch(self, out, live, state, clock):
        branch, base, cwd = state['branch'], live['base'], live['cwd']
        out['head'] = branch
        if branch is None:
            return dict(out, reason=task_push.DETACHED), None
        if branch == base:
            return dict(out, reason=ON_BASE.format(branch=branch)), None
        if state['behind'] > 0:
            return dict(out, reason=task_push.BEHIND), None
        known = remote_ref(cwd, state['remote'], base, run=self.run)
        span = [f'{known}..HEAD'] if known else (['@{upstream}..HEAD'] if state['upstream'] else [f'--max-count={task_push.NO_UPSTREAM_CAP}', 'HEAD'])
        shas = _out(_git(cwd, 'log', '--format=%H', f'--max-count={task_push.NO_UPSTREAM_CAP}', *span, run=self.run)).split()
        if not shas:
            return dict(out, reason=f'{branch} has nothing that isn’t on {base}.'), None
        commits = describe(self.db, cwd, shas, run=self.run)
        out['commits'] = commits
        out['others'] = sum(1 for c in commits if not c['outside'] and c['channelId'] != live['thread_id'])
        out['outside'] = sum(1 for c in commits if c['outside'])
        out['pushNeeded'] = state['upstream'] is None or state['ahead'] > 0
        try:
            already = live and self.gh().api(live['host'], f'repos/{live["repo"]}/pulls?state=open&head={live["owner"]}:{branch}',
                                             timeout=clock.left(READ_TIMEOUT))
        except (subprocess.TimeoutExpired, GitHubError):
            already = None  # the create checks again; GitHub refuses a second PR for the same head anyway
        if isinstance(already, list) and already and isinstance(already[0], dict) and already[0].get('number'):
            return dict(out, reason=OPEN_ALREADY.format(number=already[0]['number'], head=branch) + ' Push from Home adds these commits to it.'), None
        live.update(head=branch, head_sha=state['head'], upstream=state['upstream'], shas=shas)
        return dict(out, available=True), live

    def _plan_thread(self, out, live, state, clock):
        cwd, base, thread_id = live['cwd'], live['base'], live['thread_id']
        mine = [(sha, task) for sha, task in thread_commits(self.db, live['project_id'], thread_id)
                if _git(cwd, 'cat-file', '-e', f'{sha}^{{commit}}', run=self.run).returncode == 0]
        if not mine:
            return dict(out, reason=NO_COMMITS), None
        drivers = merge_drivers(cwd, run=self.run)
        if drivers:
            return dict(out, reason=MERGE_DRIVER.format(branch=state.get('branch') or 'this branch')), None
        onto = remote_ref(cwd, live['remote'], base, run=self.run)
        if onto is None:
            return dict(out, reason=f'This Mac hasn’t seen {live["remote"]}/{base} yet. Fetch it in Terminal once, then try again.'), None
        shas = [sha for sha, _ in mine if _git(cwd, 'merge-base', '--is-ancestor', sha, onto, run=self.run).returncode != 0]
        out['commits'] = describe(self.db, cwd, [sha for sha, _ in mine], run=self.run)
        if not shas:
            return dict(out, reason=ALL_THERE.format(base=base)), None
        built = replay(cwd, shas, onto, run=self.run)
        if 'conflict' in built:
            depends = depends_on(self.db, cwd, onto, built['conflict'], built['paths'], run=self.run)
            names = sorted({c['thread'] or 'made outside Dervo' for c in depends})
            why = (f'This thread’s {built["conflict"][:7]} builds on {len(depends)} commit{"" if len(depends) == 1 else "s"} that '
                   f'{"isn’t" if len(depends) == 1 else "aren’t"} on {base} ({", ".join(names)}), so it can’t go on its own branch. '
                   f'Pick Everything on {state.get("branch") or "this branch"}, or get those onto {base} first.') if depends else (
                   f'This thread’s {built["conflict"][:7]} doesn’t apply cleanly to {base}. Pick Everything on {state.get("branch") or "this branch"}.')
            return dict(out, reason=why, dependsOn=depends), None
        latest = self.latest(live['project_id'], thread_id)
        if latest and latest.get('number') and (latest.get('seen') or {}).get('state', 'open') == 'open' and latest.get('state') == 'opened':
            return dict(out, reason=OPEN_ALREADY.format(number=latest['number'], head=latest.get('head'))), None
        pushed = self._pushed_before(latest, shas, live, clock)
        if pushed:
            # The last attempt pushed this thread's commits and stopped before the pull request opened: open from that
            # branch, rather than pushing the same commits again under a new name (behaviour review S5, 25 Sep).
            out.update(head=pushed['head'], pushNeeded=False)
            live.update(head=pushed['head'], shas=shas, onto=onto, tip=pushed['head_sha'], reuse=True)
            return dict(out, available=True), live
        name = BRANCH_PREFIX + slug(live['name'], thread_id)
        try:
            taken = self.gh().api(live['host'], f'repos/{live["repo"]}/git/matching-refs/heads/{name}', timeout=clock.left(READ_TIMEOUT))
        except subprocess.TimeoutExpired:
            return dict(out, reason='GitHub didn’t answer in time. Try again.'), None
        except GitHubError as error:
            return dict(out, reason=error.detail), None
        used = {ref.get('ref', '')[len('refs/heads/'):] for ref in taken if isinstance(ref, dict)} if isinstance(taken, list) else set()
        head = next((candidate for candidate in [name] + [f'{name}-{n}' for n in range(2, 10)] if candidate not in used), None)
        if head is None:
            return dict(out, reason=f'{name} and nine more like it are taken on GitHub. Open this one from Terminal.'), None
        out.update(head=head, pushNeeded=True)
        live.update(head=head, shas=shas, onto=onto, tip=built['tip'])
        return dict(out, available=True), live

    def _pushed_before(self, latest, shas, live, clock):
        """The head this thread's last attempt pushed, when that attempt stopped before a pull request opened (GitHub
        refused the draft, or the create failed at the opening step), it held the same commits, and GitHub still has
        that branch at the commit Dervo pushed. None otherwise, or when GitHub can't say."""
        if not latest or latest.get('mode') != 'thread' or not latest.get('head') or not latest.get('head_sha'):
            return None
        if not (latest.get('state') == 'draft_refused' or latest.get('state') == 'failed' and latest.get('step') == 'opening'):
            return None
        if sorted(latest.get('commits') or []) != sorted(shas):
            return None
        try:
            refs = self.gh().api(live['host'], f'repos/{live["repo"]}/git/matching-refs/heads/{latest["head"]}', timeout=clock.left(READ_TIMEOUT))
        except (subprocess.TimeoutExpired, GitHubError):
            return None
        exact = [ref for ref in refs if isinstance(ref, dict) and ref.get('ref') == f'refs/heads/{latest["head"]}'] if isinstance(refs, list) else []
        if len(exact) != 1 or (exact[0].get('object') or {}).get('sha') != latest['head_sha']:
            return None
        return dict(head=latest['head'], head_sha=latest['head_sha'])

    # ------------------------------------------------------------ operations
    def preview(self, project_id, thread_id, mode, actor):
        clock = Clock()
        out, live = self.plan(project_id, thread_id, mode, actor, clock)
        if live is not None and out['pushNeeded']:
            # The confirm names one account, so it's only offered when the push can be made to go as that account.
            _, why = push_as(live['cwd'], self.gh().path, live, run=self.run)
            if why:
                out, live = dict(out, available=False, reason=why), None
        preview_id = None
        now = self.clock()
        out['body'] = body_for(out, live)
        with self.lock:
            for key in [k for k, v in self.previews.items() if now - v['drawn_at'] > CONFIRM_SECONDS]:
                del self.previews[key]
            if live is not None:
                preview_id = secrets.token_hex(12)
                self.previews[preview_id] = dict(live, drawn_at=now, pushNeeded=out['pushNeeded'], commits=[c['sha'] for c in out['commits']])
        return dict(out, id=preview_id)

    def create(self, project_id, thread_id, preview_id, draft, actor):
        if type(draft) is not bool:
            raise Rejected('Unsupported pull request fields')
        if self.closed:
            raise Rejected('Dervo is closing. Reopen it before opening a pull request.')
        with self.lock:
            shown = self.previews.get(preview_id)
            if shown is None or shown['project_id'] != project_id or shown['thread_id'] != thread_id:
                raise Rejected('This confirmation is no longer valid. ' + RECONFIRM)
            if self.clock() - shown['drawn_at'] > CONFIRM_SECONDS:
                del self.previews[preview_id]
                raise Rejected('This confirmation is older than 10 minutes. ' + RECONFIRM)
        latest = self.latest(project_id, thread_id)
        if latest and latest['state'] == 'running':
            raise Rejected('A pull request is already being opened from this thread.')
        if latest and latest['state'] == 'unknown':
            raise Rejected('Dervo doesn’t know how the last one ended. Check GitHub first.')
        # The live state has to be what the confirm showed: same remote, same branch and commit, same thread commits.
        cwd = self._project(project_id)
        state = task_push.remote_state(cwd, run=self.run)
        if state['url'] != shown['url'] or state['remote'] != shown['remote']:
            raise Rejected(task_push.REMOTE_CHANGED)
        if shown['mode'] == 'branch':
            if state['branch'] != shown['head']:
                raise Rejected(task_push.BRANCH_CHANGED)
            if state['head'] != shown['head_sha']:
                raise Rejected(task_push.HEAD_CHANGED)
            if state['behind'] > 0:
                raise Rejected(task_push.BEHIND)
        else:
            now = {sha for sha, _ in thread_commits(self.db, project_id, thread_id)}
            if now != set(shown['commits']):
                raise Rejected('This thread’s commits changed since you confirmed. ' + RECONFIRM)
        with self.lock:
            self.previews.pop(preview_id, None)  # one click per confirm
        record = dict(id=uuid.uuid4().hex, project_id=project_id, thread_id=thread_id, mode=shown['mode'], repo=shown['repo'], host=shown['host'],
                      base=shown['base'], head=shown['head'], title=shown['title'], draft=draft, login=shown['login'], remote=shown['remote'],
                      url=None, number=None, head_sha=None, commits=list(shown['shas']), state='running', step='checking',
                      detail='Checking your GitHub account.', started_at=int(self.clock()), finished_at=None, seen=None,
                      checked_at=None, checked_as=None, body=shown.get('body_text'))
        self._save(record)
        with self.db:
            self.db.execute('INSERT INTO github_accounts VALUES (?,?,?,?,?) ON CONFLICT(project_id, host) DO UPDATE SET login=excluded.login, at=excluded.at',
                            (project_id, shown['host'], shown['login'], None, int(self.clock())))
        self.futures[record['id']] = self.worker.submit(self._open, record['id'], dict(shown), cwd, draft)
        return attempt(record)

    def _step(self, record_id, step, detail):
        """The worker's progress, for the screen: kept in memory, since only the request thread writes the store. The
        request thread settles the result."""
        self.steps[record_id] = (step, detail)

    def _open(self, record_id, shown, cwd, draft):
        """On the worker: the account, then the branch, the push and the pull request. Returns the record's outcome.
        A timeout or a dead process after the push started is unknown, never failed: GitHub may have it."""
        gh = self.gh()
        if gh is None:
            return dict(state='failed', step='checking', detail=NO_GH)
        try:
            user = gh.api(shown['host'], 'user', timeout=READ_TIMEOUT * 2)
        except (subprocess.SubprocessError, GitHubError) as error:
            return dict(state='failed', step='checking', detail=getattr(error, 'detail', None) or 'GitHub didn’t answer. Nothing was pushed.')
        login = user.get('login') if isinstance(user, dict) else None
        if login != shown['login']:
            return dict(state='failed', step='checking', detail=ACCOUNT_CHANGED.format(now=login or 'nobody', then=shown['login']))
        hardening = task_push.hardening(cwd, run=self.run)
        if shown['mode'] == 'thread' and shown.get('reuse'):
            # Already on GitHub from the attempt before (_pushed_before): nothing to build or push.
            argv, head_sha = None, shown['tip']
        elif shown['mode'] == 'thread':
            self._step(record_id, 'fetching', f'Fetching {shown["base"]} from GitHub.')
            # Dervo's one fetch: the default branch only, so the branch is made from what GitHub has now.
            try:
                fetched = _git(cwd, *hardening, 'fetch', '--no-tags', '--no-recurse-submodules', '--no-write-fetch-head', '--', shown['remote'],
                               f'+refs/heads/{shown["base"]}:refs/remotes/{shown["remote"]}/{shown["base"]}', run=self.run, timeout=task_git.PUSH_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                return dict(state='failed', step='fetching', detail=f'Dervo couldn’t fetch {shown["base"]}. Nothing was pushed.')
            if fetched.returncode != 0:
                return dict(state='failed', step='fetching', detail=task_push.failure_reason(fetched.stderr) + ' Nothing was pushed.')
            onto = remote_ref(cwd, shown['remote'], shown['base'], run=self.run)
            built = replay(cwd, shown['shas'], onto, run=self.run) if onto else dict(conflict=None, paths=[])
            if 'conflict' in built:
                depends = depends_on(self.db, cwd, onto, built['conflict'], built['paths'], run=self.run) if onto and built['conflict'] else []
                return dict(state='failed', step='building', depends_on=depends,
                            detail=f'{shown["base"]} changed on GitHub and this thread’s commits don’t apply to it on their own now. Nothing was pushed.')
            try:
                taken = gh.api(shown['host'], f'repos/{shown["repo"]}/git/matching-refs/heads/{shown["head"]}', timeout=READ_TIMEOUT * 2)
            except (subprocess.SubprocessError, GitHubError):
                taken = None
            if taken is None or any(isinstance(ref, dict) and ref.get('ref') == f'refs/heads/{shown["head"]}' for ref in taken):
                return dict(state='failed', step='building', detail=f'{shown["head"]} is on GitHub already, or Dervo couldn’t check. Nothing was pushed. ' + RECONFIRM)
            argv = [*hardening, 'push', '--', shown['remote'], f'{built["tip"]}:refs/heads/{shown["head"]}']
            head_sha = built['tip']
        else:
            argv = [*hardening, 'push', *(['-u'] if shown['upstream'] is None else []), '--', shown['remote'], shown['head']] if shown['pushNeeded'] else None
            head_sha = shown['head_sha']
        if argv:
            # Checked again here, with the git config as it is now: the push goes as the account the confirm named or not at all.
            as_login, why = push_as(cwd, gh.path, shown, run=self.run)
            if why:
                return dict(state='failed', step='checking', detail=f'{why} Nothing was pushed.')
            argv = [*hardening, *as_login, *argv[len(hardening):]]
            self._step(record_id, 'pushing', f'Pushing {shown["head"]} to GitHub as {shown["login"]}.')
            try:
                pushed = _git(cwd, *argv, run=self.run, timeout=task_git.PUSH_TIMEOUT, env=push_env())
            except (OSError, subprocess.SubprocessError):
                return dict(state='unknown', step='pushing', head_sha=head_sha, detail=LOST + ' The push may have reached GitHub.')
            if pushed.returncode != 0:
                reason = task_push.failure_reason(pushed.stderr)
                return dict(state='failed', step='pushing', head_sha=head_sha,
                            detail=PUSH_REFUSED_AS.format(login=shown['login']) if reason == task_push.SIGN_IN else reason)
        self._step(record_id, 'opening', 'Opening the pull request.')
        body = dict(title=shown['title'], head=shown['head'], base=shown['base'], body=shown.get('body_text') or 'Opened from Dervo.', draft=draft)
        try:
            made = gh.api(shown['host'], f'repos/{shown["repo"]}/pulls', method='POST', body=body, timeout=WRITE_TIMEOUT)
        except subprocess.TimeoutExpired:
            return dict(state='unknown', step='opening', head_sha=head_sha, detail=LOST + f' {shown["head"]} is on GitHub; the pull request may be too.')
        except GitHubError as error:
            if error.kind == 'draft':
                return dict(state='draft_refused', step='opening', head_sha=head_sha, detail=DRAFT_REFUSED.format(repo=shown['repo'], head=shown['head']))
            return dict(state='failed', step='opening', head_sha=head_sha, detail=f'{shown["head"]} is on GitHub, but the pull request didn’t open: {error.detail}')
        if not isinstance(made, dict) or not isinstance(made.get('number'), int):
            return dict(state='unknown', step='opening', head_sha=head_sha, detail='GitHub answered without a pull request number. Check GitHub.')
        return dict(state='opened', step='opened', head_sha=head_sha, number=made['number'], url=made.get('html_url'),
                    detail=f'Opened {"draft " if made.get("draft") else ""}pull request #{made["number"]} on {shown["repo"]}.',
                    seen=read_pull(made, [], [], None), checked_at=int(self.clock()), checked_as=login)

    def status_of(self, project_id, thread_id, refresh):
        """{pr, attempt}: the thread's pull request as last seen, read again from GitHub with ``refresh``."""
        self._project(project_id)
        record = self.latest(project_id, thread_id)
        if record is None:
            return dict(pr=None, attempt=None, pullOffer=None)
        if refresh and record['state'] in ('opened', 'unknown') and self.clock() - (record.get('checked_at') or 0) >= REFRESH_SECONDS:
            record = self._refresh(record)
        return dict(pr=pull_request(record), attempt=self._attempt(record), pullOffer=self._pull_offer(record))

    def _attempt(self, record):
        view = attempt(record)
        if view and record['state'] == 'running' and record['id'] in self.steps:
            view['step'], view['detail'] = self.steps[record['id']]
        return view

    def _refresh(self, record):
        gh = self.gh()
        if gh is None:
            return self._save(dict(record, check_error=NO_GH))
        clock = Clock()
        try:
            if record['state'] == 'unknown':
                # Settle a lost create from GitHub before anything else: is there a PR from this head?
                found = gh.api(record['host'], f'repos/{record["repo"]}/pulls?state=all&head={record["repo"].split("/")[0]}:{record["head"]}',
                               timeout=clock.left(READ_TIMEOUT))
                # Only a pull request this attempt could have made: the commit it pushed, or opened after it started. A
                # branch's older closed one isn't this one (security review, 25 Sep, S4).
                pull = next((p for p in found if isinstance(p, dict) and _made_by(p, record)), None) if isinstance(found, list) else None
                if pull is None:
                    return self._save(dict(record, state='failed', detail=f'GitHub has no pull request from {record["head"]}. You can open it again.',
                                           checked_at=int(self.clock()), finished_at=int(self.clock())))
                record = dict(record, state='opened', number=pull.get('number'), url=pull.get('html_url'),
                              detail=f'Opened pull request #{pull.get("number")} on {record["repo"]}.')
            number = record['number']
            pull = gh.api(record['host'], f'repos/{record["repo"]}/pulls/{number}', timeout=clock.left(READ_TIMEOUT))
            reviews = gh.api(record['host'], f'repos/{record["repo"]}/pulls/{number}/reviews?per_page=50', timeout=clock.left(READ_TIMEOUT))
            comments = gh.api(record['host'], f'repos/{record["repo"]}/pulls/{number}/comments?per_page=30', timeout=clock.left(READ_TIMEOUT))
            sha = (pull.get('head') or {}).get('sha') if isinstance(pull, dict) else None
            try:
                runs = gh.api(record['host'], f'repos/{record["repo"]}/commits/{sha}/check-runs?per_page=100', timeout=clock.left(READ_TIMEOUT)) if sha else None
            except GitHubError:
                runs = None
            login = (self.statuses.get((gh.path, record['host'])) or {}).get('login') or record.get('login')
        except subprocess.TimeoutExpired:
            return self._save(dict(record, check_error='GitHub didn’t answer in time.'))
        except GitHubError as error:
            return self._save(dict(record, check_error=SIGNED_OUT.format(host=record['host']) if error.kind == 'signed_out' else error.detail))
        seen = read_pull(pull, reviews if isinstance(reviews, list) else [], comments if isinstance(comments, list) else [], runs)
        return self._save(dict(record, seen=seen, checked_at=int(self.clock()), checked_as=login, check_error=None))

    def _pull_offer(self, record):
        """After a one-thread PR merges: your local branch still holds those commits under their old ids. Offered only on
        the base branch itself and only while nothing runs in the project. Dervo never runs it."""
        if record.get('mode') != 'thread' or (record.get('seen') or {}).get('state') != 'merged':
            return None
        try:
            cwd = self._project(record['project_id'])
            state = task_push.remote_state(cwd, run=self.run)
        except Rejected:
            return None
        if state.get('branch') != record.get('base'):
            return None
        still = [sha for sha in record.get('commits') or [] if _git(cwd, 'merge-base', '--is-ancestor', sha, 'HEAD', run=self.run).returncode == 0
                 and _git(cwd, 'merge-base', '--is-ancestor', sha, f'refs/remotes/{record["remote"]}/{record["base"]}', run=self.run).returncode != 0]
        if not still or self._running(record['project_id']):
            return None
        return dict(command='git pull --rebase', count=len(still), base=record['base'])

    def _running(self, project_id):
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone():
            return False
        for (document,) in self.db.execute('SELECT document FROM coding_tasks WHERE instr(document, ?)>0', (project_id,)):
            try:
                task = json.loads(document)
            except ValueError:
                continue
            if (task.get('context') or {}).get('project_id') == project_id and task.get('state') == 'working':
                return True
        return False

    def thread_branch(self, project_id, thread_id, actor):
        out, live = self.plan(project_id, thread_id, 'thread', actor, Clock())
        return dict(branch=out.get('head'), base=out.get('base'), commits=out['commits'], dependsOn=out.get('dependsOn') or [],
                    available=out['available'], reason=out['reason'])


def read_pull(pull, reviews, comments, runs):
    """What History and the thread show of a pull request: its state, the review decision, checks, and reviewers' words
    quoted and scrubbed. Other people's text is data, never an instruction, and it's cut short."""
    pull = pull if isinstance(pull, dict) else {}
    state = 'merged' if pull.get('merged_at') or pull.get('merged') else 'closed' if pull.get('state') == 'closed' else 'open'
    latest = {}
    for review in reviews:
        if not isinstance(review, dict):
            continue
        who = (review.get('user') or {}).get('login')
        if who and review.get('state') in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'):
            latest[who] = review['state']
    changes = sorted(who for who, value in latest.items() if value == 'CHANGES_REQUESTED')
    approved = sorted(who for who, value in latest.items() if value == 'APPROVED')
    requested = [r.get('login') for r in pull.get('requested_reviewers') or [] if isinstance(r, dict) and r.get('login')]
    review = 'changes_requested' if changes else 'approved' if approved else 'review_required' if requested else None
    said = []
    for review_item in reviews:
        if isinstance(review_item, dict) and isinstance(review_item.get('body'), str) and review_item['body'].strip():
            said.append(dict(author=(review_item.get('user') or {}).get('login') or 'someone', text=_quote(review_item['body']), path=None, line=None,
                             at=_when(review_item.get('submitted_at'))))
    for comment in comments:
        if isinstance(comment, dict) and isinstance(comment.get('body'), str) and comment['body'].strip():
            said.append(dict(author=(comment.get('user') or {}).get('login') or 'someone', text=_quote(comment['body']),
                             path=scrub(comment.get('path'), 200) if isinstance(comment.get('path'), str) else None,
                             line=comment.get('line') if type(comment.get('line')) is int else comment.get('original_line') if type(comment.get('original_line')) is int else None,
                             at=_when(comment.get('created_at'))))
    said.sort(key=lambda item: item['at'] or 0)
    checks = None
    if isinstance(runs, dict) and isinstance(runs.get('check_runs'), list):
        passing = failing = pending = 0
        for run in runs['check_runs']:
            if not isinstance(run, dict):
                continue
            if run.get('status') != 'completed':
                pending += 1
            elif run.get('conclusion') in ('success', 'neutral', 'skipped'):
                passing += 1
            else:
                failing += 1
        checks = dict(passing=passing, failing=failing, pending=pending)
    return dict(state=state, draft=bool(pull.get('draft')), review=review, reviewers=dict(changes=changes, approved=approved, requested=requested),
                checks=checks, comments=said[-COMMENTS:], mergedAt=_when(pull.get('merged_at')))


def _made_by(pull, record):
    """Whether ``pull`` can be the one ``record``'s create opened: it's from the commit the attempt pushed, or GitHub
    made it no earlier than the attempt started (a minute's slack for clocks)."""
    sha = (pull.get('head') or {}).get('sha') if isinstance(pull.get('head'), dict) else None
    if record.get('head_sha') and sha == record['head_sha']:
        return True
    made = _when(pull.get('created_at'))
    return made is not None and made >= int(record.get('started_at') or 0) - 60


def _quote(text):
    return scrub(' '.join(text.split()), COMMENT_CHARS) + ('…' if len(' '.join(text.split())) > COMMENT_CHARS else '')


def _when(value):
    if not isinstance(value, str):
        return None
    try:
        return calendar.timegm(time.strptime(value[:19], '%Y-%m-%dT%H:%M:%S'))
    except ValueError:
        return None


def body_for(out, live):
    """The pull request's description: which commits, each with the thread that made it, and that Dervo opened it."""
    if not live:
        return None
    lines = ['Opened from Dervo.', '']
    if out['mode'] == 'thread':
        lines.append(f'Only this thread’s commits, on their own branch made from {out["base"]}:')
    else:
        lines.append(f'Everything on {out["head"]} that isn’t on {out["base"]}:')
    for commit in out['commits'][:50]:
        who = 'made outside Dervo' if commit['outside'] else commit['thread'] or ''
        lines.append(f'- {commit["short"]} {commit["subject"]}' + (f' ({who})' if who and who not in commit['subject'] else ''))
    text = '\n'.join(lines) + '\n'
    live['body_text'] = text
    return text


def for_host(host):
    """The host's one GitHub worker, made on first use and kept on the host (the push worker is kept the same way)."""
    github = getattr(host, '_github', None)
    if github is None:
        github = GitHub(host)
        host._github = github
    return github


def shared_dispatch(host, operation, args, actor):
    """shared_operations.dispatch has checked the fields and their types, and the archived-thread rule."""
    github = for_host(host)
    github.tick()
    if operation == 'github_status':
        return github.status(args['projectId'], bool(args.get('recheck')))
    if operation == 'pr_preview':
        if args['mode'] not in MODES:
            raise Rejected('Pick This thread only or Everything on the branch.')
        return github.preview(args['projectId'], args['channelId'], args['mode'], actor)
    if operation == 'pr_create':
        return github.create(args['projectId'], args['channelId'], args['previewId'], args['draft'], actor)
    if operation == 'pr_status':
        return github.status_of(args['projectId'], args['channelId'], bool(args.get('refresh')))
    if operation == 'pr_thread_branch':
        return github.thread_branch(args['projectId'], args['channelId'], actor)
    raise Rejected('Unsupported GitHub operation')
