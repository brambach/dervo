"""Thread folders: a branch and folder of its own for each thread that changes code (beta.18, direction B).

notes/WORKTREES-explore-20261005.md, with Bryce's decisions of 5 Oct 2026:

- Dervo picks per thread. A thread that will change code gets its own branch and folder; explores, plans, notes and
  read-only questions stay in the project folder. When Dervo can't tell, it picks its own branch. The pick shows as a
  chip on the composer and can be flipped until the thread's first run. After that it's fixed: Claude Code and Codex
  keep a session with the folder it ran in.
- Folders live in Application Support (platform_os.worktrees_dir), never inside the project folder.
- Dependencies are cloned per thread when the thread's lockfile matches the project folder's. A different lockfile asks
  "Install dependencies?" with the exact command and never installs by itself.
- Every cleanly recorded result in a thread folder is committed to the thread's branch (coding_integration.auto_commit,
  ``how`` 'automatic', Undo as today). Merge makes a merge commit on the base, never a squash, and is always the
  person's click. Pushing stays task_push's own confirm and is never part of Merge.

The run never chooses its folder and never commits: Dervo makes the worktree with its own git calls (task_git's safe
flags, a clean environment), and the agent's git guard (claude_coding.git_guard) still refuses commit, merge, rebase and
push in every worktree sharing the project's .git. Before every run Dervo checks the folder is still the one it made
(``verify``): listed in ``git worktree list --porcelain``, on the thread's branch, sharing the project's .git and not a
link. Anything else is unknown, and the run doesn't start.

One table, ``thread_folders``. A thread's row is keyed by its thread id. The project's own row (``kind`` 'project',
keyed ``project:<id>``) holds the ignored files you approved for copying into its thread folders (Project settings,
Access).

Setup runs on a background thread, because a clone of a big repo can pass the bridge's 10 seconds per request
(src/bridge.rs). It never touches the database: the host's thread settles it (``Folders.settle``). The message that
started it waits in the conversation's queue (why 'setup') and starts when the folder is ready.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import platform_os
import release_storage
import task_git
from dervo_connections import Rejected

# The same floor the preview builder keeps (release_storage.MIN_FREE_BYTES, 6 GB): a thread folder is never made when
# it would leave less than this free.
FLOOR = release_storage.MIN_FREE_BYTES
PREFIX = 'dervo/'
CHOICES = ('own', 'project')
# chosen: picked, nothing made yet. setting_up: the background setup is running. needs_install: a lockfile differs
# and Dervo is asking. ready: the folder is there and runs start in it. failed: setup stopped, nothing ran.
# merged / archived: the folder is gone and the branch stays. The next message sets it up again from the branch.
STATES = ('chosen', 'setting_up', 'needs_install', 'ready', 'failed', 'merged', 'archived')
# States in which the thread's runs have used (or will use) the thread folder, so binding['cwd'] is that folder. A
# merged or archived thread keeps its folder path: its earlier tasks were recorded there (task_evidence.read compares
# the root), and its next run sets the same folder up again.
BOUND = ('ready', 'merged', 'archived')
# Most of a clone is shared blocks, so the estimate is the files that can't be cloned plus this much for git's index and
# the folder's own metadata.
CLONE_OVERHEAD = 64 * 1024 ** 2
LIMIT_IGNORED = 40

LOCKFILES = {
    'pnpm-lock.yaml': ('node_modules', ['pnpm', 'install', '--frozen-lockfile']),
    'package-lock.json': ('node_modules', ['npm', 'ci']),
    'yarn.lock': ('node_modules', ['yarn', 'install', '--frozen-lockfile']),
    'bun.lockb': ('node_modules', ['bun', 'install', '--frozen-lockfile']),
    'bun.lock': ('node_modules', ['bun', 'install', '--frozen-lockfile']),
    'uv.lock': ('.venv', ['uv', 'sync', '--frozen']),
    'poetry.lock': ('.venv', ['poetry', 'install']),
    'Gemfile.lock': ('vendor/bundle', ['bundle', 'install']),
}
# Ignored files never proposed for copying: caches and build output (a thread builds its own), dependency folders (the
# dependency step handles them) and anything secret-named, which is only copied if you add it yourself.
NOT_PROPOSED = {'dist', 'build', 'target', 'out', '__pycache__', '.DS_Store', '.cache', '.pytest_cache', '.mypy_cache',
                '.ruff_cache', '.next', '.turbo', 'coverage', '.coverage', 'node_modules', '.venv', 'venv', '.tox', '.idea',
                '.vscode', '.claude', '.codex'}
SECRET_NAME = re.compile(r'(^\.env)|(\.pem$)|(\.p12$)|(\.key$)|(key)|(secret)|(credential)|(token)|(^id_[rd]sa)|(\.keychain)', re.I)

# The pick (Q1, direction B). The signals a thread has before its first send: whether the project is a git repository,
# whether this thread may edit at all, a read-only request or proposal, and the words of the request (Home's start, a
# fan-out proposal's purpose, or the composer's draft).
EXPLORE = re.compile(r'\b(explore|exploration|investigate|research|plan|planning|propose|proposal|note|notes|notebook|'
                     r'review|reviewing|summari[sz]e|summary|explain|read[- ]only|look into|what do you think|brainstorm|compare)\b', re.I)
BUILD = re.compile(r'\b(build|fix|implement|add|change|refactor|rename|remove|delete|update|write|make|ship|port|migrate|test|bug)\b', re.I)
REASONS = {
    'code': 'Dervo picked this because the request asks to build or change something.',
    'unsure': 'Dervo couldn’t tell from the request, so it picked its own branch. That’s the safe side.',
    'explore': 'Explores, plans and notes stay in the project folder, so notes land where they do now.',
    'read_only': 'A read-only request stays in the project folder.',
    'no_edit': 'This thread can only read files, so it stays in the project folder.',
    'not_repo': 'The project folder isn’t a git repository, so threads work there as today.',
    'no_commits': 'The repository has no commits yet, so threads work in the project folder as today.',
    'started': 'This thread already ran in the project folder, so it stays there.',
    'windows': 'On Windows, threads work in the project folder for now.',
    'you': 'You picked this.',
}
# Bryce, 6 Oct 2026: in beta.18 Windows threads always work in the project folder, because Windows can't clone
# node_modules into a thread folder. Thread folders on Windows wait for a later beta.
NOT_ON_WINDOWS = 'Threads work in the project folder on Windows for now. Their own branch and folder come in a later beta.'


# ---------------------------------------------------------------------------------------------------------- schema
def schema(db):
    """The one table (and its migration: it's created on first open, and nothing older has it)."""
    db.execute('CREATE TABLE IF NOT EXISTS thread_folders ('
               'id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN (\'thread\',\'project\')), project_id TEXT NOT NULL, '
               'thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE, '
               'choice TEXT CHECK(choice IN (\'own\',\'project\')), reason TEXT, '
               'folder TEXT, cwd TEXT, branch TEXT, base TEXT, base_commit TEXT, '
               'state TEXT CHECK(state IN (\'chosen\',\'setting_up\',\'needs_install\',\'ready\',\'failed\',\'merged\',\'archived\')), '
               'detail TEXT NOT NULL DEFAULT \'{}\', created REAL NOT NULL, updated REAL NOT NULL, '
               'CHECK((kind=\'thread\' AND thread_id IS NOT NULL AND id=thread_id) OR (kind=\'project\' AND thread_id IS NULL)))')
    db.execute('CREATE INDEX IF NOT EXISTS thread_folders_project ON thread_folders(project_id)')


def _has_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_folders'").fetchone() is not None


def read(db, thread_id):
    """The thread's row as a dict (``detail`` parsed), or None."""
    if not _has_table(db):
        return None
    row = db.execute("SELECT * FROM thread_folders WHERE id=? AND kind='thread'", (thread_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    try:
        out['detail'] = json.loads(out.get('detail') or '{}')
    except ValueError:
        out['detail'] = {}
    return out


def write(db, row):
    """Insert or replace a thread's row. The caller holds the transaction."""
    now = time.time()
    db.execute('INSERT INTO thread_folders (id, kind, project_id, thread_id, choice, reason, folder, cwd, branch, base, base_commit, '
               'state, detail, created, updated) VALUES (?,\'thread\',?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET '
               'choice=excluded.choice, reason=excluded.reason, folder=excluded.folder, cwd=excluded.cwd, branch=excluded.branch, '
               'base=excluded.base, base_commit=excluded.base_commit, state=excluded.state, detail=excluded.detail, updated=excluded.updated',
               (row['thread_id'], row['project_id'], row['thread_id'], row.get('choice'), row.get('reason'), row.get('folder'),
                row.get('cwd'), row.get('branch'), row.get('base'), row.get('base_commit'), row.get('state') or 'chosen',
                json.dumps(row.get('detail') or {}), row.get('created') or now, now))


def cwd_for(db, thread_id, project_cwd):
    """The folder a thread's runs use: its own when it has one (BOUND), otherwise the project folder. Store.binding's
    swap. Never a fallback for a thread whose folder went missing: that's ``verify``'s unknown, and the run doesn't start."""
    row = read(db, thread_id)
    if row and row['choice'] == 'own' and row['state'] in BOUND and row.get('cwd'):
        return row['cwd'], row
    return project_cwd, row


def project_settings(db, project_id):
    if not _has_table(db):
        return {'ignored': [], 'actor': None, 'at': None}
    row = db.execute("SELECT detail FROM thread_folders WHERE id=? AND kind='project'", ('project:' + project_id,)).fetchone()
    detail = json.loads(row[0]) if row else {}
    return {'ignored': list(detail.get('ignored') or []), 'actor': detail.get('actor'), 'at': detail.get('at')}


def set_ignored(db, project_id, paths, actor):
    """Your approved list of ignored files copied into this project's thread folders. Exactly the paths given."""
    if type(paths) is not list or len(paths) > LIMIT_IGNORED or any(type(p) is not str for p in paths) or len(set(paths)) != len(paths):
        raise Rejected(f'List up to {LIMIT_IGNORED} distinct relative paths.')
    for path in paths:
        if not _relative_ok(path):
            raise Rejected(f'{path} isn’t a relative path inside the project folder.')
    now = time.time()
    document = json.dumps({'ignored': sorted(paths), 'actor': actor, 'at': int(now)})
    db.execute('INSERT INTO thread_folders (id, kind, project_id, thread_id, detail, created, updated) VALUES (?,\'project\',?,NULL,?,?,?) '
               'ON CONFLICT(id) DO UPDATE SET detail=excluded.detail, updated=excluded.updated',
               ('project:' + project_id, project_id, document, now, now))


def _relative_ok(path):
    if not path or path.startswith(('/', '\\')) or '\0' in path or '\n' in path:
        return False
    parts = Path(path).parts
    return bool(parts) and '..' not in parts and parts[0] != '.git' and not re.match(r'^[A-Za-z]:', path)


def locked(db, thread_id):
    """Whether the thread has run before (a coding task or a chat turn): its folder is fixed from then on."""
    if db.execute('SELECT 1 FROM turns WHERE thread_id=? LIMIT 1', (thread_id,)).fetchone():
        return True
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_tasks'").fetchone() is None:
        return False
    return db.execute("SELECT 1 FROM coding_tasks WHERE json_extract(document, '$.context.thread_id')=? LIMIT 1",
                      (thread_id,)).fetchone() is not None


def pick(text='', *, repo=True, commits=True, can_edit=True, read_only=False):
    """(choice, reason key): Dervo's pick for a thread that hasn't run yet."""
    if platform_os.WINDOWS:
        return 'project', 'windows'
    if not repo:
        return 'project', 'not_repo'
    if not commits:
        return 'project', 'no_commits'
    if read_only:
        return 'project', 'read_only'
    if not can_edit:
        return 'project', 'no_edit'
    text = text if isinstance(text, str) else ''
    explore, build = EXPLORE.search(text), BUILD.search(text)
    if explore and not (build and build.start() < explore.start()):
        return 'project', 'explore'
    return 'own', 'code' if build else 'unsure'


# ---------------------------------------------------------------------------------------------------------- git
def _git(cwd, *args, timeout=task_git.TIMEOUT, text=True, env=None, stdin=None):
    """Dervo's own git call: task_git's safe flags (hooks, fsmonitor, external diff, pager and signing off, the
    repository's filters switched off) and a clean environment, so the run's git guard never applies to it."""
    try:
        return task_git._git(cwd, *args, timeout=timeout, text=text, env=env, stdin=stdin)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Rejected(f'Git couldn’t run: {exc}') from None


def _ok(result, what):
    if result.returncode != 0:
        detail = ' '.join((result.stderr or '').split())[:300] if isinstance(result.stderr, str) else ''
        raise Rejected(f'{what}{": " + detail if detail else "."}')
    return result.stdout


def repo_info(cwd):
    """What setup needs to know about the project folder: its repository's top folder, the project folder's place in
    it, the shared .git, the branch and commit it's on. None when it isn't in a repository; 'commit' None when the
    repository has no commits yet."""
    top = _git(cwd, 'rev-parse', '--show-toplevel')
    if top.returncode != 0 or not top.stdout.strip():
        return None
    prefix = _git(cwd, 'rev-parse', '--show-prefix').stdout.strip()
    common = _git(cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout.strip()
    head = _git(cwd, 'rev-parse', '--verify', '-q', 'HEAD^{commit}')
    branch = _git(cwd, 'symbolic-ref', '--short', '-q', 'HEAD')
    return {'top': os.path.realpath(top.stdout.strip()), 'prefix': prefix.rstrip('/'), 'common': os.path.realpath(common) if common else None,
            'commit': head.stdout.strip() if head.returncode == 0 else None,
            'branch': branch.stdout.strip() if branch.returncode == 0 and branch.stdout.strip() else None}


def slug(text, limit=40):
    words = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    if len(words) > limit:
        words = words[:limit].rsplit('-', 1)[0] or words[:limit]
    return words.strip('-.') or 'thread'


def branch_exists(top, branch):
    return _git(top, 'show-ref', '--verify', '-q', f'refs/heads/{branch}').returncode == 0


def branch_name(top, name, prefix=PREFIX):
    """``dervo/<thread name, short>``, with -2, -3 on a clash. Named once, at the first run, and never renamed."""
    base = prefix + slug(name)
    if _git(top, 'check-ref-format', '--branch', base).returncode != 0:
        base = prefix + 'thread'
    candidate, n = base, 2
    while branch_exists(top, candidate):
        candidate, n = f'{base}-{n}', n + 1
    return candidate


def worktrees(top):
    """[{worktree, head, branch, flags}] from ``git worktree list --porcelain``."""
    out = _ok(_git(top, 'worktree', 'list', '--porcelain'), 'Git couldn’t list worktrees')
    entries, entry = [], None
    for line in out.splitlines() + ['']:
        if not line:
            if entry:
                entries.append(entry)
            entry = None
            continue
        key, _, value = line.partition(' ')
        if key == 'worktree':
            entry = {'worktree': value, 'head': None, 'branch': None, 'flags': []}
        elif entry is not None:
            if key == 'HEAD':
                entry['head'] = value
            elif key == 'branch':
                entry['branch'] = value
            else:
                entry['flags'].append(key)
    return entries


def checked_out_at(top, branch):
    """The folder that has ``branch`` checked out, or None."""
    for entry in worktrees(top):
        if entry['branch'] == f'refs/heads/{branch}' and 'prunable' not in entry['flags']:
            return entry['worktree']
    return None


def verify(row, project_cwd, info=None):
    """None when the thread's folder is the one Dervo made, otherwise why it's unknown: it's listed in the project
    repository's ``git worktree list --porcelain``, its HEAD is the thread's branch, it shares the project's .git and
    neither it nor a folder above it is a link. ``info``: the project's repo_info when the caller has just read it
    (Folders.view), so it isn't read twice. The check before a run never passes one."""
    folder = row.get('folder')
    if not folder or not os.path.isabs(folder):
        return 'Dervo has no folder recorded for this thread.'
    if os.path.realpath(folder) != folder:
        return 'The thread’s folder is a link now, so Dervo can’t be sure what a run would change.'
    if not os.path.isdir(folder):
        return 'The thread’s folder is missing.'
    info = info or repo_info(project_cwd)
    if info is None:
        return 'The project folder isn’t a git repository any more.'
    try:
        listed = next((e for e in worktrees(info['top']) if os.path.realpath(e['worktree']) == folder), None)
    except Rejected as exc:
        return str(exc)
    if listed is None:
        return 'Git doesn’t list the thread’s folder as a worktree of this project.'
    if 'prunable' in listed['flags']:
        return 'Git marks the thread’s folder as missing.'
    if listed['branch'] != f'refs/heads/{row["branch"]}':
        return f'The thread’s folder isn’t on {row["branch"]} any more.'
    common = _git(folder, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    if common.returncode != 0 or os.path.realpath(common.stdout.strip()) != info['common']:
        return 'The thread’s folder doesn’t share this project’s git repository.'
    head = _git(folder, 'symbolic-ref', '-q', 'HEAD')
    if head.returncode != 0 or head.stdout.strip() != f'refs/heads/{row["branch"]}':
        return f'The thread’s folder isn’t on {row["branch"]} any more.'
    return None


# ---------------------------------------------------------------------------------------------------------- files
_clone = platform_os.clone_path
CANT_CLONE = platform_os.CANT_CLONE


def free_bytes(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def _same_volume(a, b):
    a, b = Path(a), Path(b)
    while not b.exists():
        b = b.parent
    return os.stat(a).st_dev == os.stat(b).st_dev


def _tree(top, commit):
    """[(mode, size, path)] for every file in ``commit``."""
    out = _ok(_git(top, 'ls-tree', '-r', '-l', '-z', '--full-tree', commit), 'Git couldn’t list the base’s files')
    entries = []
    for record in out.split('\0'):
        if not record:
            continue
        meta, _, path = record.partition('\t')
        mode, _kind, _sha, size = meta.split()
        entries.append((mode, int(size) if size.isdigit() else 0, path))
    return entries


def _dirty(top):
    """Paths in the project's checkout with uncommitted changes (staged or not), repository-relative."""
    out = _ok(_git(top, 'status', '--porcelain=v1', '-z', '--untracked-files=no', '--no-renames', '--ignore-submodules=all'),
              'Git couldn’t read the project folder’s status')
    return {entry[3:] for entry in out.split('\0') if len(entry) > 3}


def _size_text(n):
    gb = n / 1024 ** 3
    return f'{gb:.1f} GB' if gb >= 0.1 else f'{max(1, round(n / 1024 ** 2))} MB'


class Plan(dict):
    """Everything setup decides on the host's thread before the background work starts (reads only, no folder made)."""


def plan(project_cwd, *, thread_id, project_id, name, base=None, branch=None, root, prefix=PREFIX, ignored=()):
    """Read the project and decide where and how to set up. Raises Rejected with a setup failure key in ``.key``."""
    info = repo_info(project_cwd)
    if info is None:
        raise SetupStop('not_repo', 'The project folder isn’t a git repository.')
    if info['commit'] is None:
        raise SetupStop('no_commits', 'The repository has no commits yet.')
    base = base or info['branch']
    if not base:
        raise SetupStop('no_base', 'The project folder isn’t on a branch, so there’s nothing to branch from. Check out a branch there, or run in the project folder.')
    resolved = _git(info['top'], 'rev-parse', '--verify', '-q', f'refs/heads/{base}^{{commit}}')
    if resolved.returncode != 0:
        raise SetupStop('base_gone', f'The branch {base} isn’t there any more.')
    base_commit = resolved.stdout.strip()
    reuse = bool(branch) and branch_exists(info['top'], branch)
    if not branch:
        branch = branch_name(info['top'], name, prefix)
    start = _git(info['top'], 'rev-parse', '--verify', '-q', f'refs/heads/{branch}^{{commit}}').stdout.strip() if reuse else base_commit
    group = f'{slug(Path(info["top"]).name, 30)}-{project_id[:8]}'
    folder = Path(root) / group / slug(branch[len(prefix):] if branch.startswith(prefix) else branch)
    if Path(folder).exists():
        n = 2
        while (Path(f'{folder}-{n}')).exists():
            n += 1
        folder = Path(f'{folder}-{n}')
    cwd = folder / info['prefix'] if info['prefix'] else folder
    tree = _tree(info['top'], start)
    dirty = _dirty(info['top'])
    clone = (platform_os.can_clone() and start == info['commit'] and _same_volume(info['top'], root))
    total = sum(size for _mode, size, _path in tree)
    estimate = (sum(size for _mode, size, path in tree if path in dirty) + CLONE_OVERHEAD) if clone else total
    free = free_bytes(root)
    return Plan(thread_id=thread_id, project_id=project_id, top=info['top'], prefix=info['prefix'], common=info['common'],
                project_cwd=str(project_cwd), base=base, base_commit=base_commit, start=start, branch=branch, reuse=reuse,
                folder=str(folder), cwd=str(cwd), root=str(root), clone=clone, files=len(tree), estimate=estimate, free=free,
                dirty=sorted(dirty), ignored=list(ignored), project_branch=info['branch'])


class SetupStop(Rejected):
    """Setup stopped before anything ran. ``key`` names the failure the card shows."""
    def __init__(self, key, text, **extra):
        super().__init__(text)
        self.key, self.extra = key, extra


def _step(steps, key, state, line, progress):
    for step in steps:
        if step['key'] == key:
            step.update(state=state, line=line)
            break
    else:
        steps.append({'key': key, 'state': state, 'line': line})
    if progress:
        progress([dict(s) for s in steps])


def initial_steps(p=None):
    later = 'later'
    return [{'key': key, 'state': later, 'line': ''} for key in ('space', 'branch', 'files', 'dependencies', 'ignored')]


def setup(p, *, progress=None, clock=time.monotonic, exclude_backup=True):
    """Make the thread's folder from plan ``p``: steps 1 to 5 of the plan's Q3. Returns the result dict (state 'ready'
    or 'needs_install'); raises SetupStop. Runs on a background thread: files and git only, never the database.

    1. Space: free space must stay above FLOOR after the estimate. Below it, nothing is made.
    2. Branch and empty folder: ``git worktree add --no-checkout -b <branch> <folder> <base commit>``.
    3. Files: when the base is the commit the project folder has checked out (and the volume clones), each tracked file
       without uncommitted changes is cloned from the project folder, then the index is read from the commit and
       refreshed. Files with your uncommitted changes, links and anything that can't be cloned come from git, never the
       folder. Otherwise a plain checkout.
    4. Dependencies: each lockfile that matches the project folder's gets its installed folder cloned (linked where it
       can't be). One that differs is reported for "Install dependencies?" and nothing is installed.
    5. Ignored files: the project's approved list, cloned from the project folder.
    """
    steps = initial_steps()
    started = clock()
    # 1. Space
    needed = p['estimate'] + FLOOR
    if p['free'] < needed:
        _step(steps, 'space', 'needs', f'{_size_text(p["free"])} free. Dervo keeps {_size_text(FLOOR)} free, and this folder needs about {_size_text(p["estimate"])} more.', progress)
        raise SetupStop('space', f'Not enough free space: {_size_text(p["free"])} free, {_size_text(needed)} needed.', free=p['free'], needed=needed,
                        estimate=p['estimate'], steps=steps)
    _step(steps, 'space', 'done', f'{_size_text(p["free"])} free. This folder needs about {_size_text(p["estimate"])}.', progress)
    # 2. Branch and empty folder
    root = Path(p['root'])
    fresh_root = not root.exists()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if fresh_root and exclude_backup:
        exclude_from_backup(root)
    Path(p['folder']).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    _step(steps, 'branch', 'running', f'{p["branch"]} from {p["base"]}', progress)
    args = ['worktree', 'add', '--no-checkout', p['folder'], p['branch']] if p['reuse'] else \
           ['worktree', 'add', '--no-checkout', '-b', p['branch'], p['folder'], p['start']]
    made = _git(p['top'], *args)
    if made.returncode != 0:
        _step(steps, 'branch', 'needs', 'Git couldn’t make the branch and folder.', progress)
        raise SetupStop('branch', 'Git couldn’t make the thread’s branch and folder: ' + ' '.join((made.stderr or '').split())[:300], steps=steps)
    _step(steps, 'branch', 'done', (f'{p["branch"]}, set up again' if p['reuse'] else f'{p["branch"]} from {p["base"]} at {p["start"][:7]}'), progress)
    try:
        # 3. Files
        _step(steps, 'files', 'running', f'{p["files"]:,} files', progress)
        cloned = _files(p)
        how = (f'{cloned:,} of {p["files"]:,} files cloned from the project folder' if cloned
               else f'{p["files"]:,} files checked out from {p["base"]}')
        if not p['clone'] and p['project_branch'] != p['base']:
            how += f'. The project folder is on {p["project_branch"] or "another commit"}, so they can’t be clones'
        _step(steps, 'files', 'done', how, progress)
        # 4. Dependencies
        _step(steps, 'dependencies', 'running', 'Looking for lockfiles', progress)
        deps = dependencies(p)
        waiting = [d for d in deps if d['state'] == 'differs']
        if waiting:
            _step(steps, 'dependencies', 'needs', dependency_line(deps), progress)
        else:
            _step(steps, 'dependencies', 'done', dependency_line(deps), progress)
        # 5. Ignored files
        copied = copy_ignored(p)
        _step(steps, 'ignored', 'done', ignored_line(copied, p['ignored']), progress)
    except SetupStop as stop:
        _undo(p)
        stop.extra.setdefault('steps', steps)
        raise
    except Exception as exc:  # noqa: BLE001 - whatever stopped it, nothing is left half made
        _undo(p)
        raise SetupStop('files', f'Setup stopped: {exc}', steps=steps) from None
    return {'state': 'needs_install' if waiting else 'ready', 'steps': steps, 'dependencies': deps, 'copied': copied,
            'seconds': round(clock() - started, 2), 'cloned': cloned}


def exclude_from_backup(folder):
    """Keep Time Machine out of the thread folders: every result is committed into the project's own .git, which is
    backed up. Never fails setup."""
    return platform_os.exclude_from_backup(folder)


def _undo(p):
    """Remove a half-made folder and a branch this setup made. Nothing else."""
    _git(p['top'], 'worktree', 'remove', '--force', p['folder'])
    if os.path.isdir(p['folder']):
        shutil.rmtree(p['folder'], ignore_errors=True)
    _git(p['top'], 'worktree', 'prune')
    if not p['reuse']:
        _git(p['top'], 'branch', '-D', p['branch'])


def _files(p):
    """Fill the empty worktree with the start commit's files. Returns how many were cloned (0 for a plain checkout)."""
    folder = p['folder']
    if p['clone']:
        dirty = set(p['dirty'])
        from_git, cloned = [], 0
        for mode, _size, path in _tree(p['top'], p['start']):
            source, target = os.path.join(p['top'], path), os.path.join(folder, path)
            if mode != '100644' and mode != '100755' or path in dirty:
                from_git.append(path)  # links, submodules and your uncommitted changes: from git, never your folder
                continue
            try:
                info = os.lstat(source)
            except OSError:
                from_git.append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                from_git.append(path)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                _clone(source, target)
                cloned += 1
            except OSError as exc:
                if exc.errno in CANT_CLONE and cloned == 0:
                    return _checkout(p)  # this volume can't clone after all: a plain checkout instead
                from_git.append(path)
        _ok(_git(folder, 'read-tree', 'HEAD'), 'Git couldn’t read the base into the thread’s index')
        if from_git:
            for chunk in range(0, len(from_git), 500):
                _ok(_git(folder, 'checkout-index', '-f', '--', *from_git[chunk:chunk + 500]), 'Git couldn’t check out files')
        _git(folder, 'update-index', '-q', '--refresh', timeout=600)
        # A clone whose bytes don't match the commit (a file changed between the status read and the clone) is put back
        # from git. Then the folder must be exactly the commit.
        changed = _changed(folder)
        if changed:
            for chunk in range(0, len(changed), 500):
                _ok(_git(folder, 'checkout-index', '-f', '--', *changed[chunk:chunk + 500]), 'Git couldn’t check out files')
            _git(folder, 'update-index', '-q', '--refresh', timeout=600)
            if _changed(folder):
                raise SetupStop('files', 'The thread’s folder didn’t match its branch after setup, so Dervo removed it.')
        return cloned
    return _checkout(p)


def _checkout(p):
    folder = p['folder']
    _ok(_git(folder, 'read-tree', 'HEAD'), 'Git couldn’t read the base into the thread’s index')
    _ok(_git(folder, 'checkout-index', '-a', '-f', timeout=600), 'Git couldn’t check out the base’s files')
    _git(folder, 'update-index', '-q', '--refresh', timeout=600)
    if _changed(folder):
        raise SetupStop('files', 'The thread’s folder didn’t match its branch after setup, so Dervo removed it.')
    return 0


def _changed(folder):
    out = _git(folder, 'status', '--porcelain=v1', '-z', '--untracked-files=no', '--no-renames', '--ignore-submodules=all').stdout
    return [entry[3:] for entry in out.split('\0') if len(entry) > 3]


def lockfiles(p):
    """[(lockfile path, installed folder, command)] for the tracked lockfiles of the thread's start commit."""
    found = []
    for _mode, _size, path in _tree(p['top'], p['start']):
        name = os.path.basename(path)
        if name in LOCKFILES and 'node_modules' not in Path(path).parts and path.count('/') <= 3:
            installed, command = LOCKFILES[name]
            here = os.path.dirname(path)
            found.append((path, os.path.join(here, installed) if here else installed, command))
    return found


def dependencies(p):
    """Each lockfile's installed folder: 'cloned' (or 'linked' where it can't clone) when the thread's lockfile matches
    the project folder's, 'differs' when it doesn't (nothing installed: the card asks), 'absent' when the project
    folder has nothing installed there either."""
    out = []
    for lockfile, installed, command in lockfiles(p):
        source, target = os.path.join(p['top'], installed), os.path.join(p['folder'], installed)
        entry = {'lockfile': lockfile, 'folder': installed, 'command': command, 'cwd': os.path.dirname(lockfile)}
        if not os.path.lexists(source):
            out.append(dict(entry, state='absent'))
            continue
        if os.path.lexists(target):
            out.append(dict(entry, state='present'))
            continue
        try:
            same = Path(p['top'], lockfile).read_bytes() == Path(p['folder'], lockfile).read_bytes()
        except OSError:
            same = False
        if not same:
            out.append(dict(entry, state='differs'))
            continue
        os.makedirs(os.path.dirname(target) or p['folder'], exist_ok=True)
        if os.path.islink(source):
            # The project folder links it already (the app-wt worktrees do): the thread gets the same link.
            os.symlink(os.readlink(source), target)
            out.append(dict(entry, state='linked'))
            continue
        try:
            _clone(source, target)
            out.append(dict(entry, state='cloned'))
        except OSError:
            os.symlink(os.path.realpath(source), target)
            out.append(dict(entry, state='linked'))
    return out


def dependency_line(deps):
    if not deps:
        return 'No lockfiles, so nothing to install.'
    parts = []
    for d in deps:
        if d['state'] == 'cloned':
            parts.append(f'{d["lockfile"]} matches the project folder’s, so {d["folder"]} is cloned, not installed')
        elif d['state'] == 'linked':
            parts.append(f'{d["lockfile"]} matches the project folder’s, so {d["folder"]} is linked to the project folder’s')
        elif d['state'] == 'differs':
            parts.append(f'{d["lockfile"]} differs from the project folder’s. Install dependencies?')
        elif d['state'] == 'absent':
            parts.append(f'Nothing installed for {d["lockfile"]} in the project folder, so nothing to clone')
    return '. '.join(parts) + '.' if parts else 'Dependencies were already there.'


def copy_ignored(p):
    """Clone the approved ignored files (paths relative to the project folder) into the thread's folder. Returns the
    paths copied. A path that's gone from the project folder, or already in the thread's, is skipped."""
    copied = []
    for path in p['ignored']:
        if not _relative_ok(path):
            continue
        source, target = os.path.join(p['project_cwd'], path), os.path.join(p['cwd'], path)
        if not os.path.lexists(source) or os.path.lexists(target):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        try:
            _clone(source, target)
        except OSError:
            if os.path.isdir(source) and not os.path.islink(source):
                shutil.copytree(source, target, symlinks=True)
            elif os.path.islink(source):
                os.symlink(os.readlink(source), target)
            else:
                shutil.copy2(source, target)
        copied.append(path)
    return copied


def ignored_line(copied, approved):
    if not approved:
        return 'None approved in Project settings, Access.'
    if not copied:
        return 'None of the approved ones are in the project folder.'
    return ', '.join(copied) + ', from Project settings'


def ignored_candidates(project_cwd, limit=LIMIT_IGNORED):
    """Ignored files and folders in the project folder a thread folder may need, for you to approve (Project settings,
    Access): git's ignored list minus caches, build output, dependency folders and anything secret-named."""
    out = _git(project_cwd, 'ls-files', '--others', '--ignored', '--exclude-standard', '--directory', '-z')
    if out.returncode != 0:
        return []
    found = []
    for path in out.stdout.split('\0'):
        path = path.rstrip('/')
        if not path:
            continue
        parts = Path(path).parts
        if any(part in NOT_PROPOSED for part in parts) or any(SECRET_NAME.search(part) for part in parts):
            continue
        found.append(path)
    return sorted(found)[:limit]


# ---------------------------------------------------------------------------------------------------------- install
def install_argv(command):
    """The install command with its program found in the usual places (never your shell's PATH: it may run other code),
    or None when it isn't installed."""
    program = shutil.which(command[0], path=os.pathsep.join(platform_os.tool_dirs()))
    return [program, *command[1:]] if program else None


def install(folder, dep, *, timeout=900):
    """Run one approved install in the thread's folder. Only ever called after your click on Install."""
    argv = install_argv(dep['command'])
    if argv is None:
        raise Rejected(f'Dervo couldn’t find {dep["command"][0]}. Install it, or run “{" ".join(dep["command"])}” in the thread’s folder yourself.')
    cwd = os.path.join(folder, dep['cwd']) if dep['cwd'] else folder
    env = {'PATH': os.pathsep.join([os.path.dirname(argv[0]), *platform_os.system_path()]), 'HOME': str(Path.home()),
           'LANG': 'en_US.UTF-8', 'CI': '1'}
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode != 0:
        raise Rejected(f'{" ".join(dep["command"])} stopped: ' + ' '.join((result.stderr or result.stdout or '').split())[-300:])
    return {'command': dep['command'], 'seconds': None}


# ---------------------------------------------------------------------------------------------------------- merge
def merge_message(branch, base, thread_name, actor, commits):
    """A merge commit Dervo makes when you click Merge. It starts "Dervo: " and says " in Dervo. Dervo made this
    commit", so native_evidence.dervo_commits_only treats it as Dervo's own when it lands under a running thread."""
    name = ' '.join((thread_name or '').split())[:80] or branch
    return (f'Dervo: Merge {branch} into {base}\n\n'
            f'Thread {name} merged in Dervo. Dervo made this commit after {actor} clicked Merge.\n'
            f'Commits: {commits}\n')


def branch_state(top, branch, base):
    """How the thread's branch stands against its base: commits ahead and behind, and the files that would clash.
    ``git merge-tree --write-tree`` touches no folder (git 2.38 or later; Apple's is 2.50.1)."""
    # One call counts both sides: the base's own commits (behind), then the branch's (ahead).
    counts = _git(top, 'rev-list', '--left-right', '--count', f'refs/heads/{base}...refs/heads/{branch}')
    sides = counts.stdout.split()
    if counts.returncode != 0 or len(sides) != 2:
        raise Rejected(f'Git couldn’t compare {branch} with {base}.')
    behind, ahead = int(sides[0]), int(sides[1])
    result = _git(top, 'merge-tree', '--write-tree', '--name-only', '--no-messages', f'refs/heads/{base}', f'refs/heads/{branch}')
    if result.returncode not in (0, 1):
        raise Rejected('Git couldn’t check for clashes: ' + ' '.join((result.stderr or '').split())[:200])
    lines = result.stdout.splitlines()
    tree = lines[0].strip() if lines else ''
    clashes = sorted({line.strip() for line in lines[1:] if line.strip()}) if result.returncode == 1 else []
    tips = _git(top, 'rev-parse', f'refs/heads/{branch}', f'refs/heads/{base}', f'refs/heads/{base}^{{tree}}').stdout.split()
    tip, base_tip, base_tree = (tips + ['', '', ''])[:3]
    # A result and its Undo leave commits that change nothing: a merge would leave the base exactly as it is (polish, 9 Oct).
    empty = result.returncode == 0 and bool(tree) and tree == base_tree
    return {'ahead': ahead, 'behind': behind, 'clashes': clashes, 'empty': empty,
            'tree': tree if result.returncode == 0 else None, 'tip': tip, 'base_tip': base_tip, 'checked': int(time.time())}


def merge(row, project_cwd, *, actor, thread_name=None, busy=lambda folder: False):
    """Merge the thread's branch into its base as a merge commit, never a squash. Only after your click. Returns
    {sha, base, branch, where, commits}. Refuses when there's nothing to merge, when the branch clashes with the base,
    when the thread's folder has changes not committed yet, or when the base is checked out in a folder that has
    uncommitted changes in the merged files or a run in progress. Never pushes."""
    info = repo_info(project_cwd)
    if info is None:
        raise Rejected('The project folder isn’t a git repository any more.')
    top, branch, base = info['top'], row['branch'], row['base']
    if not branch_exists(top, branch):
        raise Rejected(f'The branch {branch} isn’t there any more.')
    if not branch_exists(top, base):
        raise Rejected(f'The branch {base} isn’t there any more, so there’s nothing to merge into.')
    folder = row.get('folder')
    if folder and os.path.isdir(folder):
        left = _git(folder, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames').stdout
        waiting = [entry[3:] for entry in left.split('\0') if len(entry) > 3]
        if waiting:
            raise Rejected('This thread has changes that aren’t committed to its branch yet: ' + ', '.join(waiting[:3])
                           + (f' and {len(waiting) - 3} more' if len(waiting) > 3 else '') + '. Approve or undo them first.')
    state = branch_state(top, branch, base)
    if state['ahead'] == 0:
        raise Rejected(f'Nothing to merge: {branch} has no commits that {base} doesn’t.')
    if state['clashes']:
        raise Rejected(f'Can’t merge yet: {base} also changed ' + ', '.join(state['clashes'][:3])
                       + (f' and {len(state["clashes"]) - 3} more' if len(state['clashes']) > 3 else '') + '.')
    message = merge_message(branch, base, thread_name, actor, state['ahead'])
    where = checked_out_at(top, base)
    if where:
        if busy(os.path.realpath(where)):
            raise Rejected(f'A run is working in the folder that has {base} checked out. Merge when it ends.')
        merged = _ok(_git(top, 'diff', '--name-only', '-z', f'refs/heads/{base}...refs/heads/{branch}'), 'Git couldn’t list the merged files')
        files = {p for p in merged.split('\0') if p}
        dirty = _git(where, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames').stdout
        staged = [entry for entry in dirty.split('\0') if len(entry) > 3 and entry[0] not in (' ', '?')]
        overlap = sorted({entry[3:] for entry in dirty.split('\0') if len(entry) > 3} & files)
        if overlap:
            raise Rejected(f'{base} is checked out in {where}, which has uncommitted changes in ' + ', '.join(overlap[:3])
                           + '. Commit or put those aside, then merge.')
        if staged:
            raise Rejected(f'{base} is checked out in {where}, which has staged changes. Commit or unstage them, then merge.')
        result = _git(where, '-c', 'merge.autoStash=false', '-c', 'rerere.enabled=false', 'merge', '--no-ff', '--no-edit',
                      '--no-verify', '-m', message, f'refs/heads/{branch}')
        if result.returncode != 0:
            _git(where, 'merge', '--abort')
            raise Rejected('Git couldn’t merge: ' + ' '.join(((result.stderr or '') + ' ' + (result.stdout or '')).split())[:300])
        sha = _git(where, 'rev-parse', 'HEAD').stdout.strip()
        return {'sha': sha, 'base': base, 'branch': branch, 'where': where, 'commits': state['ahead'], 'at': int(time.time())}
    # The base isn't checked out anywhere: write the merge without a folder.
    tree = state['tree']
    commit = _ok(_git(top, 'commit-tree', tree, '-p', state['base_tip'], '-p', state['tip'], stdin=message), 'Git couldn’t write the merge commit').strip()
    _ok(_git(top, 'update-ref', '-m', f'Dervo: merge {branch}', f'refs/heads/{base}', commit, state['base_tip']),
        f'{base} moved while Dervo was merging. Nothing changed; try Merge again')
    return {'sha': commit, 'base': base, 'branch': branch, 'where': None, 'commits': state['ahead'], 'at': int(time.time())}


def remove(row, project_cwd):
    """Remove the thread's folder and keep its branch. Refuses while the folder has changes that aren't committed:
    nothing of yours or the agent's is ever deleted. Returns True when it was removed (or was already gone)."""
    folder = row.get('folder')
    if not folder:
        return True
    info = repo_info(project_cwd)
    if not os.path.isdir(folder):
        if info:
            _git(info['top'], 'worktree', 'prune')
        return True
    left = _git(folder, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames')
    if left.returncode != 0:
        raise Rejected('Git couldn’t read the thread’s folder, so Dervo left it where it is.')
    waiting = [entry[3:] for entry in left.stdout.split('\0') if len(entry) > 3]
    if waiting:
        raise Rejected('The thread’s folder has changes that aren’t committed: ' + ', '.join(waiting[:3])
                       + (f' and {len(waiting) - 3} more' if len(waiting) > 3 else '') + '. Dervo left it where it is.')
    if info is None:
        raise Rejected('The project folder isn’t a git repository any more, so Dervo left the thread’s folder where it is.')
    # Ignored files (the cloned dependencies, runtime files) are Dervo's copies: --force removes them with the folder.
    result = _git(info['top'], 'worktree', 'remove', '--force', folder)
    if result.returncode != 0:
        raise Rejected('Git couldn’t remove the thread’s folder: ' + ' '.join((result.stderr or '').split())[:200])
    return True


# ---------------------------------------------------------------------------------------------------------- operations
# The renderer's operations (src/bridge.rs lists each). ``text`` on thread_folder is the composer's draft, so the chip
# shows the pick the first send would make.
FIELDS = {
    'thread_folder': {'projectId', 'channelId'},
    'thread_folder_choose': {'projectId', 'channelId', 'choice'},
    'thread_folder_retry': {'projectId', 'channelId'},
    'thread_folder_install': {'projectId', 'channelId'},
    'thread_folder_skip_install': {'projectId', 'channelId'},
    'thread_folder_merge': {'projectId', 'channelId'},
    'thread_folder_reveal': {'projectId', 'channelId'},
    'thread_folder_settings': {'projectId'},
    'thread_folder_ignored_set': {'projectId', 'paths'},
}
OPTIONAL = {'thread_folder': {'text'}}


def dispatch(host, op, args, actor):
    if not isinstance(args, dict) or not (FIELDS[op] <= set(args) <= FIELDS[op] | OPTIONAL.get(op, set())):
        raise Rejected('Unsupported thread folder fields')
    coding = host.coding_tasks()
    folders = coding.folders
    if op in ('thread_folder_settings', 'thread_folder_ignored_set'):
        from conversation_coding import project_binding
        binding = project_binding(coding.store, args['projectId'])
        if op == 'thread_folder_ignored_set':
            with coding.store.db:
                set_ignored(coding.store.db, args['projectId'], args['paths'], actor)
        return {'approved': project_settings(coding.store.db, args['projectId'])['ignored'],
                'candidates': ignored_candidates(binding['cwd']), 'root': str(folders.worktrees_root())}
    if type(args['channelId']) is not str or type(args['projectId']) is not str:
        raise Rejected('Choose a thread')
    binding = coding.store.binding(args['channelId'], args['projectId'], actor=actor)
    if op == 'thread_folder':
        text = args.get('text')
        if text is not None and (type(text) is not str or len(text.encode()) > 32768):
            raise Rejected('The draft is text up to 32 KiB')
        return folders.view(binding, text)
    if op == 'thread_folder_choose':
        return folders.choose(binding, args['choice'], actor)
    if op == 'thread_folder_retry':
        return folders.retry(binding)
    if op == 'thread_folder_install':
        return folders.install(binding, actor)
    if op == 'thread_folder_skip_install':
        return folders.skip_install(binding, actor)
    if op == 'thread_folder_merge':
        return folders.merge(binding, actor)
    if op == 'thread_folder_reveal':
        row = folders._row(binding)
        if row['choice'] != 'own' or row['state'] != 'ready' or not row.get('folder') or not os.path.isdir(row['folder']):
            raise Rejected('This thread has no folder of its own right now.')
        subprocess.run(platform_os.reveal_command(row['folder']), capture_output=True, timeout=10)
        return folders.view(binding)
    raise Rejected('Unsupported thread folder operation')


# ---------------------------------------------------------------------------------------------------------- host side
class Job:
    """One background setup or install. Its fields are read by the host's thread; only ``run`` writes them."""
    def __init__(self, kind, thread_id, work):
        self.kind, self.thread_id, self.work = kind, thread_id, work
        self.lock = threading.Lock()
        self.steps = initial_steps()
        self.result = self.error = None
        self.done = False
        self.started = time.time()
        self.thread = threading.Thread(target=self._run, name=f'dervo-thread-folder-{kind}', daemon=True)

    def progress(self, steps):
        with self.lock:
            self.steps = steps

    def _run(self):
        try:
            result = self.work(self.progress)
            with self.lock:
                self.result = result
        except Exception as exc:  # noqa: BLE001 - reported on the card, never raised into the host
            with self.lock:
                self.error = exc
        finally:
            with self.lock:
                self.done = True

    def start(self):
        self.thread.start()
        return self


class Folders:
    """The host's side: the pick, setup jobs, the check before every run, merge and removal. One per CodingTasks."""
    def __init__(self, coding, root=None):
        self.coding, self.store = coding, coding.store
        self.root = Path(root) if root is not None else None
        self.jobs = {}
        self.facts = {}
        with self.store.db:
            schema(self.store.db)
        self.restarted()

    # The chip and the card ask ``thread_folder`` for every thread you open and again every few seconds, and each answer
    # starts git processes (18 git calls for a thread on its own branch, about 0.4 s on a small repo). The host answers one request
    # at a time, so a slow read holds up every click behind it. What view() reads from git is kept this long. A change
    # Dervo makes itself (choose, setup, merge, archive, a result's commit or Undo) forgets it at once; the check before a
    # run never reads from here.
    FACTS_FOR = 10.0

    def remember(self, key, make):
        now = time.monotonic()
        hit = self.facts.get(key)
        if hit is not None and now - hit[0] < self.FACTS_FOR:
            return hit[1]
        value = make()  # a refusal (Git couldn't run) isn't kept
        if len(self.facts) >= 64:
            self.facts.pop(min(self.facts, key=lambda k: self.facts[k][0]))
        self.facts[key] = (now, value)
        return value

    def forget(self):
        """Drop the kept git facts: Dervo itself just changed a branch (a commit, an undo, a merge)."""
        self.facts.clear()

    def project_info(self, cwd):
        """repo_info for a project folder, as kept by remember()."""
        return self.remember(('info', cwd), lambda: repo_info(cwd))

    def restarted(self):
        """A setup or install the last host didn't finish: nothing ran in that folder, so it's stopped with the reason
        and Try again starts it clean. A half-made folder and a branch that setup made are removed (never one reused)."""
        rows = self.store.db.execute("SELECT id FROM thread_folders WHERE kind='thread' AND state='setting_up'").fetchall()
        for (thread_id,) in rows:
            row = read(self.store.db, thread_id)
            text = 'Dervo closed before this thread’s folder was ready. Nothing ran.'
            if row['detail'].get('install_actor') and row['detail'].get('dependencies') is not None:
                # It closed during an install you asked for: the folder stays, and the card asks again.
                with self.store.db:
                    write(self.store.db, {**row, 'state': 'needs_install', 'detail': {**row['detail'], 'install_error': text}})
                continue
            fresh = not row['detail'].get('reused')
            if fresh and row.get('folder'):
                try:
                    project = self.store.db.execute('SELECT cwd FROM projects WHERE id=?', (row['project_id'],)).fetchone()
                    info = repo_info(project[0]) if project else None
                    if info:
                        _undo({'top': info['top'], 'folder': row['folder'], 'reuse': False, 'branch': row['branch']})
                except (Rejected, OSError):
                    pass
            elif row.get('folder'):
                try:
                    project = self.store.db.execute('SELECT cwd FROM projects WHERE id=?', (row['project_id'],)).fetchone()
                    info = repo_info(project[0]) if project else None
                    if info:
                        _undo({'top': info['top'], 'folder': row['folder'], 'reuse': True, 'branch': row['branch']})
                except (Rejected, OSError):
                    pass
            with self.store.db:
                write(self.store.db, {**row, 'state': 'failed', 'detail': {**row['detail'], 'error': {'key': 'restart', 'text': text}}})

    def worktrees_root(self):
        return self.root if self.root is not None else platform_os.worktrees_dir()

    # ---------------------------------------------------------------- the pick
    def project_cwd(self, binding):
        return binding.get('project_cwd') or binding['cwd']

    def can_edit(self, binding):
        try:
            permission = self.coding.conversation.permission(binding)
        except Rejected:
            return False
        return permission.get('state') == 'granted'

    def proposed(self, binding, text, read_only=False, kept=False):
        """Dervo's pick for this thread right now, without saving it. ``kept``: the project's git facts may be a few
        seconds old (the chip's read); a request that is about to start reads them fresh."""
        cwd = self.project_cwd(binding)
        try:
            info = self.project_info(cwd) if kept else repo_info(cwd)
        except Rejected:
            info = None
        return pick(text, repo=info is not None, commits=bool(info and info['commit']), can_edit=self.can_edit(binding), read_only=read_only)

    def choose(self, binding, choice, actor):
        """Your flip on the composer chip. Only before the thread's first run."""
        if choice not in CHOICES:
            raise Rejected('A thread works on its own branch (own) or in the project folder (project).')
        if choice == 'own' and platform_os.WINDOWS:
            raise Rejected(NOT_ON_WINDOWS)
        thread_id = binding['id']
        if locked(self.store.db, thread_id):
            raise Rejected('This thread has already run, so its folder is fixed. Start a new thread to work somewhere else.')
        row = read(self.store.db, thread_id)
        if row and row['state'] not in ('chosen', 'failed', 'needs_install'):
            raise Rejected('This thread’s folder is already being set up.')
        if choice == 'own':
            info = repo_info(self.project_cwd(binding))
            if info is None or info['commit'] is None:
                raise Rejected('The project folder isn’t a git repository with commits, so threads work there as today.')
        if row and row['state'] == 'needs_install' and choice == 'project':
            self._discard(row, binding)
        with self.store.db:
            write(self.store.db, {**(row or {}), 'thread_id': thread_id, 'project_id': binding['project_id'], 'choice': choice,
                                  'reason': 'you', 'state': 'chosen', 'detail': {'actor': actor}})
        self.facts.clear()
        return self.view(binding)

    def _discard(self, row, binding):
        """A folder set up before the first run that you've decided against: removed with its new branch."""
        try:
            remove(row, self.project_cwd(binding))
            info = repo_info(self.project_cwd(binding))
            if info and row.get('branch') and not row['detail'].get('reused'):
                _git(info['top'], 'branch', '-D', row['branch'])
        except Rejected:
            pass

    # ---------------------------------------------------------------- the gate before a request
    def gate(self, binding, args):
        """Before a coding request makes its task. None: go ahead (binding['cwd'] is where it runs). Otherwise a reason
        the message waits in the queue ('setup') while the folder is set up, or a failure the card shows."""
        thread_id = binding['id']
        self.settle(thread_id)
        row = read(self.store.db, thread_id)
        if row is None:
            if locked(self.store.db, thread_id):
                # A thread that ran in beta.17 or earlier stays in the project folder, exactly as it was.
                self._save_pick(binding, 'project', 'started')
                return None
            choice, reason = self.proposed(binding, args.get('text') or '', read_only=args.get('readOnly') is True)
            row = self._save_pick(binding, choice, reason)
        elif platform_os.WINDOWS and row['choice'] == 'own' and row['state'] not in BOUND:
            # Windows (beta.18): a pick of its own branch that never got a folder (a store from a Mac) runs in the project
            # folder instead, straight away, rather than waiting for a setup Windows doesn't do yet.
            row = self._save_pick(binding, 'project', 'windows')
        if row['choice'] == 'project':
            return None
        if row['state'] == 'ready':
            return None
        if row['state'] in ('chosen', 'merged', 'archived'):
            self.start_setup(binding, row, name=args.get('text') or '')
            return 'setup'
        return 'setup'  # setting up, waiting for your install answer, or stopped: the message waits with the card

    def _save_pick(self, binding, choice, reason):
        row = {'thread_id': binding['id'], 'project_id': binding['project_id'], 'choice': choice, 'reason': reason, 'state': 'chosen', 'detail': {}}
        with self.store.db:
            write(self.store.db, row)
        return read(self.store.db, binding['id'])

    def thread_name(self, thread_id):
        import sqlite3
        import task_push
        try:
            return task_push.thread_name_for(self.store.db, {'context': {'thread_id': thread_id}})
        except sqlite3.Error:
            return None

    def start_setup(self, binding, row, name=''):
        if platform_os.WINDOWS:
            raise Rejected(NOT_ON_WINDOWS)
        thread_id = binding['id']
        job = self.jobs.get(thread_id)
        if job and not job.done:
            return job
        self.facts.clear()
        project_cwd = self.project_cwd(binding)
        title = self.thread_name(thread_id)
        if not title or title == task_git.PLACEHOLDER_NAME:
            title = ' '.join((name or '').split()[:6]) or f'thread-{thread_id[:6]}'
        try:
            p = plan(project_cwd, thread_id=thread_id, project_id=binding['project_id'], name=title, base=row.get('base'),
                     branch=row.get('branch'), root=self.worktrees_root(), ignored=project_settings(self.store.db, binding['project_id'])['ignored'])
        except SetupStop as stop:
            self._stopped(binding, row, stop)
            return None
        detail = {**(row.get('detail') or {}), 'plan': {k: p[k] for k in ('estimate', 'free', 'files', 'clone', 'start')}, 'error': None,
                  'reused': p['reuse'], 'steps': initial_steps()}
        with self.store.db:
            write(self.store.db, {**row, 'folder': p['folder'], 'cwd': p['cwd'], 'branch': p['branch'], 'base': p['base'],
                                  'base_commit': p['base_commit'], 'state': 'setting_up', 'detail': detail})
        job = Job('setup', thread_id, lambda progress: setup(p, progress=progress)).start()
        job.plan, job.binding = p, binding
        self.jobs[thread_id] = job
        return job

    def _stopped(self, binding, row, stop):
        key = getattr(stop, 'key', 'files')
        if key in ('not_repo', 'no_commits'):
            # No repository to branch from: the thread runs in the project folder as today, and the chip says so.
            with self.store.db:
                write(self.store.db, {**row, 'choice': 'project', 'reason': key, 'state': 'chosen'})
            return
        extra = dict(getattr(stop, 'extra', {}) or {})
        with self.store.db:
            write(self.store.db, {**row, 'state': 'failed', 'detail': {**(row.get('detail') or {}), 'error': {'key': key, 'text': str(stop), **{
                k: v for k, v in extra.items() if k != 'steps'}}, 'steps': extra.get('steps') or (row.get('detail') or {}).get('steps') or initial_steps(),
                'stopped_at': int(time.time())}})

    def settle(self, thread_id=None):
        """Save what finished background jobs found. On the host's thread; called before every read of a row."""
        for tid, job in list(self.jobs.items()):
            if thread_id is not None and tid != thread_id or not job.done:
                continue
            self.jobs.pop(tid)
            row = read(self.store.db, tid)
            if row is None:
                continue
            detail = dict(row.get('detail') or {})
            if job.error is not None:
                stop = job.error if isinstance(job.error, SetupStop) else SetupStop('files', f'Setup stopped: {job.error}')
                if job.kind == 'install':
                    detail['install_error'] = str(job.error)
                    with self.store.db:
                        write(self.store.db, {**row, 'state': 'needs_install', 'detail': detail})
                    continue
                self._stopped(job.binding, row, stop)
                continue
            if job.kind == 'install':
                deps = [dict(d, state='installed') if d['state'] == 'differs' else d for d in detail.get('dependencies') or []]
                steps = [dict(s, state='done', line=dependency_line(deps).replace('Install dependencies?', 'Installed.')) if s['key'] == 'dependencies' else s
                         for s in detail.get('steps') or []]
                detail.update(dependencies=deps, steps=steps, install_error=None)
                with self.store.db:
                    write(self.store.db, {**row, 'state': 'ready', 'detail': detail})
                continue
            result = job.result
            detail.update(steps=result['steps'], dependencies=result['dependencies'], copied=result['copied'], seconds=result['seconds'],
                          cloned=result['cloned'], error=None, ready_at=int(time.time()))
            with self.store.db:
                write(self.store.db, {**row, 'state': result['state'], 'detail': detail})

    def busy(self):
        return any(not job.done for job in self.jobs.values())

    def waiting(self, thread_id):
        """Whether a message for this thread has to keep waiting: its folder is being set up, asks about an install, or
        stopped. Cheap: the queue asks on every pump. Never on Windows, where threads work in the project folder."""
        if platform_os.WINDOWS:
            return False
        self.settle(thread_id)
        row = read(self.store.db, thread_id)
        return bool(row and row['choice'] == 'own' and row['state'] in ('setting_up', 'needs_install', 'failed'))

    # ---------------------------------------------------------------- your answers on the card
    def retry(self, binding):
        row = self._row(binding)
        if row['state'] not in ('failed',):
            raise Rejected('Setup isn’t stopped, so there’s nothing to try again.')
        self.start_setup(binding, {**row, 'state': 'chosen'})
        return self.view(binding)

    def project_folder_instead(self, binding, actor):
        """"Run in the project folder instead": before the first run, the thread works in the project folder from now on,
        as in beta.17. (A session stays with its folder, so this isn't per run.)"""
        return self.choose(binding, 'project', actor)

    def install(self, binding, actor):
        row = self._row(binding)
        if row['state'] != 'needs_install':
            raise Rejected('Nothing is waiting to be installed.')
        waiting = [d for d in row['detail'].get('dependencies') or [] if d['state'] == 'differs']
        folder = row['folder']
        def work(progress):
            for dep in waiting:
                install(folder, dep)
            return {'installed': [d['lockfile'] for d in waiting]}
        job = Job('install', binding['id'], work).start()
        job.binding = binding
        self.jobs[binding['id']] = job
        with self.store.db:
            write(self.store.db, {**row, 'state': 'setting_up', 'detail': {**row['detail'], 'install_actor': actor, 'install_error': None}})
        return self.view(binding)

    def skip_install(self, binding, actor):
        """Go on without installing. The card said what that means: builds and tests needing them may fail."""
        row = self._row(binding)
        if row['state'] != 'needs_install':
            raise Rejected('Nothing is waiting to be installed.')
        deps = [dict(d, state='skipped') if d['state'] == 'differs' else d for d in row['detail'].get('dependencies') or []]
        steps = [dict(s, state='done', line='Not installed: you chose to go on without them.') if s['key'] == 'dependencies' else s
                 for s in row['detail'].get('steps') or []]
        with self.store.db:
            write(self.store.db, {**row, 'state': 'ready', 'detail': {**row['detail'], 'dependencies': deps, 'steps': steps, 'skip_actor': actor}})
        return self.view(binding)

    def _row(self, binding):
        self.settle(binding['id'])
        row = read(self.store.db, binding['id'])
        if row is None:
            raise Rejected('This thread has no folder of its own.')
        return row

    # ---------------------------------------------------------------- before every run
    def check(self, binding, task=None):
        """Raise Rejected unless a run may start in binding['cwd']. Project-folder threads pass as in beta.17. ``task``'s
        snapshot must name the same folder: native_evidence.finish records from it."""
        row = read(self.store.db, binding['id']) if binding.get('id') else None
        if row is None or row['choice'] != 'own':
            return None
        if row['state'] != 'ready':
            raise Rejected('This thread’s folder isn’t ready, so nothing was started. Its setup card says why.')
        if binding['cwd'] != row['cwd'] or (task is not None and (task.get('binding') or {}).get('cwd') not in (None, row['cwd'])):
            raise Rejected('This run’s folder isn’t the thread’s own folder. Nothing was started.')
        problem = verify(row, self.project_cwd(binding))
        if problem:
            raise Rejected(f'Unknown folder: {problem} Nothing was started.')
        return None

    def committing(self, task):
        """Whether ``task`` ran in its thread's own folder, so a clean result commits to the thread's branch."""
        thread_id = (task.get('context') or {}).get('thread_id')
        row = read(self.store.db, thread_id) if thread_id else None
        return bool(row and row['choice'] == 'own' and row['state'] == 'ready' and row.get('cwd')
                    and (task.get('binding') or {}).get('cwd') == row['cwd'])

    # ---------------------------------------------------------------- merge, archive
    def merge(self, binding, actor):
        row = self._row(binding)
        if row['choice'] != 'own' or row['state'] != 'ready':
            raise Rejected('This thread has no branch ready to merge.')
        if self.coding.busy_thread(binding['id']):
            raise Rejected('A run is still working in this thread. Merge when it ends.')
        def busy(folder):
            for task_id, turn in self.coding.turns.items():
                if not turn.closed and os.path.realpath(self.coding._cwd(task_id) or '') == folder:
                    return True
            return False
        self.facts.clear()
        result = merge(row, self.project_cwd(binding), actor=actor, thread_name=self.thread_name(binding['id']), busy=busy)
        self.facts.clear()
        detail = {**row['detail'], 'merge': result, 'left': None}
        try:
            remove(row, self.project_cwd(binding))
        except Rejected as exc:
            detail['left'] = str(exc)
        with self.store.db:
            write(self.store.db, {**row, 'state': 'merged' if not detail['left'] else 'ready', 'detail': detail})
        return self.view(binding)

    def archived(self, thread_id, project_cwd):
        """The thread was archived: remove its folder, keep its branch. A folder with changes not committed stays, and
        the row says why. Never raises."""
        try:
            row = read(self.store.db, thread_id)
            if row is None or row['choice'] != 'own' or row['state'] != 'ready':
                return None
            self.facts.clear()
            try:
                remove(row, project_cwd)
                state, left = 'archived', None
            except Rejected as exc:
                state, left = 'ready', str(exc)
            with self.store.db:
                write(self.store.db, {**row, 'state': state, 'detail': {**row['detail'], 'left': left, 'archived_at': int(time.time())}})
            return state
        except Exception as exc:  # noqa: BLE001 - archiving stands; the folder stays
            print(f'Thread folder for {thread_id} was left in place: {type(exc).__name__}: {exc}', file=sys.stderr)
            return None

    # ---------------------------------------------------------------- what the renderer sees
    def view(self, binding, text=None):
        """The chip, the setup card and the branch card, from the row (and Dervo's pick when there's no row yet)."""
        thread_id = binding['id']
        self.settle(thread_id)
        row = read(self.store.db, thread_id)
        project_cwd = self.project_cwd(binding)
        fixed = locked(self.store.db, thread_id)
        if row is None:
            if fixed:
                choice, reason = 'project', 'started'
            else:
                choice, reason = self.proposed(binding, text or '', kept=True)
            row = {'choice': choice, 'reason': reason, 'state': 'chosen', 'detail': {}}
        try:
            info = self.project_info(project_cwd)
        except Rejected:
            info = None
        detail = row.get('detail') or {}
        job = self.jobs.get(thread_id)
        steps = job.steps if job and not job.done and job.kind == 'setup' else detail.get('steps')
        out = {'choice': row['choice'], 'reason': row.get('reason'), 'why': REASONS.get(row.get('reason') or '', ''),
               'state': row['state'], 'flippable': not fixed and not platform_os.WINDOWS and row['state'] in ('chosen', 'failed', 'needs_install'),
               'branch': row.get('branch'), 'base': row.get('base') or (info or {}).get('branch'),
               'base_commit': row.get('base_commit'), 'folder': row.get('folder'),
               'project_folder': project_cwd, 'project_branch': (info or {}).get('branch'),
               'repo': info is not None, 'steps': steps, 'error': detail.get('error'),
               'dependencies': detail.get('dependencies'), 'install_error': detail.get('install_error'),
               'merge': detail.get('merge'), 'left': detail.get('left'), 'seconds': detail.get('seconds'),
               'estimate': (detail.get('plan') or {}).get('estimate'), 'free': (detail.get('plan') or {}).get('free'),
               'floor': FLOOR, 'suggested_branch': None, 'branch_state': None, 'unknown': None}
        if row['choice'] == 'own' and not row.get('branch') and info and info['commit']:
            name = self.thread_name(thread_id)
            if not name or name == task_git.PLACEHOLDER_NAME:
                name = ' '.join((text or '').split()[:6]) or f'thread-{thread_id[:6]}'
            out['suggested_branch'] = branch_name(info['top'], name)
        if row['choice'] == 'own' and row['state'] == 'ready' and info:
            def standing():
                try:
                    return branch_state(info['top'], row['branch'], row['base'])
                except Rejected as exc:
                    return {'error': str(exc)}
            out['unknown'], out['branch_state'] = self.remember(
                ('standing', thread_id, project_cwd, row.get('folder'), row.get('branch'), row.get('base')),
                lambda: (verify(row, project_cwd, info), standing()))
        return out
