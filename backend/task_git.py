"""Dervo commits an approved task's changed files. The agent never runs git.

Bryce's 17 September 2026 decision. When you approve a result (or ask for it on a completed one), Dervo makes one commit
on the project's current branch holding exactly the files this task changed, after checking that each file on disk still
has the bytes of the approved attempt. "Undo this task" is one more commit that puts exactly those files back, refused
when later changes overlap them. A project folder that isn't in a git repository records that nothing was committed and
the approval stands. Your other staged changes are left as they are, and the project folder may sit inside a larger
repository: every path is given relative to the project folder.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from dervo_connections import Rejected
import platform_os

GIT = platform_os.git_executable()
NOT_A_REPO = 'Not a git repository'
TIMEOUT = 30
PUSH_TIMEOUT = 120  # a push crosses the network; a commit doesn't (task_push.py)
PLACEHOLDER_NAME = 'New conversation'  # host.py's automatic name, never a commit subject
SUBJECT_LIMIT = 60


def _env():
    # The user's own git identity and config apply; nothing else from the shell does. No prompts, no signing prompts.
    if platform_os.WINDOWS:
        env = platform_os.provider_environment()
        env.update(HOME=os.path.expanduser('~'), GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
        return env
    return {'PATH': '/usr/bin:/bin', 'HOME': os.path.expanduser('~'), 'LANG': 'en_US.UTF-8', 'LC_ALL': 'en_US.UTF-8',
            'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0'}


# Config keys a project's own .git/config could set to run a program during a Dervo git call, pinned off on every call:
# hooks, the filesystem monitor (``git status`` runs it), external diff (``git show`` and ``git diff`` run it), a
# pager. Clean, smudge and process filters (``filter.<name>.clean`` and friends) have no global off switch, but they can
# be switched off by name, and they do run on Dervo's calls for every path .gitattributes assigns the filter: on
# status when a file's stat changed, on diff, on add, on commit (twice) and on Undo's checkout (security review 20 Sep,
# M2). filter_guards below reads the repository's own filter drivers and adds ``-c filter.<name>.clean=`` and friends
# for each, on every call. Push-time keys (credential helpers, askpass, ssh, transports) are task_push.hardening's.
SAFE = ('-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
        '-c', 'core.useBuiltinFSMonitor=false', '-c', 'diff.external=', '-c', 'core.pager=cat',
        # A planted log.showSignature=true would make git log run the repo's gpg.program on any signed commit.
        '-c', 'log.showSignature=false')
# Config scopes a project's own files hold: .git/config and its includes, .git/config.worktree. A driver from the user's
# global or system config (git-lfs) is theirs and keeps working, unless the file defining it sits under the repository.
REPO_SCOPES = ('local', 'worktree')
FILTER_KEYS = ('clean', 'smudge', 'process')
UNGUARDABLE_FILTER = ('This project’s git config defines a filter named “{name}”, which Dervo can’t switch off for its own git '
                      'calls. Remove that filter from the repository’s git config, then try again.')


def _raw(cwd, *args, run=subprocess.run, timeout=TIMEOUT, text=True, env=None, stdin=None):
    return run([GIT, *SAFE, *args], cwd=cwd, env={**_env(), **(env or {})}, capture_output=True, text=text, timeout=timeout,
               **({'input': stdin} if stdin is not None else {}))


def repo_filters(cwd, run=subprocess.run, timeout=TIMEOUT):
    """Names of the filter drivers the repository itself defines: any ``filter.<name>.*`` key from a repo-scoped config
    file (.git/config, .git/config.worktree, whatever they include, wherever that file is) or from a global or system
    file kept under the repository (a global ``includeIf`` pointing into it). Reading config runs nothing. Empty when
    git can't answer, or ``cwd`` isn't in a repository."""
    try:
        listing = run([GIT, 'config', '-z', '--show-origin', '--show-scope', '--get-regexp', r'^filter\.'],
                      cwd=cwd, env=_env(), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    if getattr(listing, 'returncode', None) not in (0, 1) or not isinstance(getattr(listing, 'stdout', None), str):
        return []
    fields = listing.stdout.split('\0')
    names, root = [], None
    for scope, origin, entry in zip(fields[0::3], fields[1::3], fields[2::3]):
        parts = entry.split('\n', 1)[0].split('.')
        if len(parts) < 3 or parts[0] != 'filter':
            continue
        name = '.'.join(parts[1:-1])
        if scope not in REPO_SCOPES:
            if scope == 'command' or not origin.startswith('file:') or not os.path.isabs(origin[5:]):
                continue
            if root is None:
                try:
                    top = run([GIT, 'rev-parse', '--show-toplevel'], cwd=cwd, env=_env(), capture_output=True, text=True, timeout=timeout)
                    root = os.path.realpath(top.stdout.strip()) if top.returncode == 0 and top.stdout.strip() else ''
                except (OSError, subprocess.SubprocessError):
                    root = ''
            if not root or os.path.commonpath([root, os.path.realpath(origin[5:])]) != root:
                continue
        if name not in names:
            names.append(name)
    return names


def filter_guards(cwd, run=subprocess.run, timeout=TIMEOUT):
    """``-c`` flags that switch off every filter driver the repository defines (repo_filters), for one git call: the
    clean, smudge and process commands emptied and ``required`` off, so a path the filter is assigned to is read and
    written as plain bytes and nothing runs. Verified against git 2.39 with a planted marker filter: status, diff,
    add, commit and checkout ran it without these flags and didn't with them. A name holding ``=`` can't be named on
    the command line (``-c`` splits at the first one), so it's refused rather than left running."""
    flags = []
    for name in repo_filters(cwd, run=run, timeout=timeout):
        if '=' in name or '\n' in name:
            raise Rejected(UNGUARDABLE_FILTER.format(name=' '.join(name.split())[:40]))
        flags += [flag for key in FILTER_KEYS for flag in ('-c', f'filter.{name}.{key}=')]
        flags += ['-c', f'filter.{name}.required=false']
    return flags


def _git(cwd, *args, run=subprocess.run, timeout=TIMEOUT, text=True, env=None, stdin=None):
    """Every git call Dervo makes in a project: SAFE pins, the repository's own filters switched off, a clean env.
    ``env`` adds to that env (the temporary index a commit builds in); ``stdin`` feeds bytes or text to git."""
    return _raw(cwd, *filter_guards(cwd, run=run, timeout=timeout), *args, run=run, timeout=timeout, text=text, env=env, stdin=stdin)


def repo_root(cwd, run=subprocess.run):
    """The repository containing the project folder, or None."""
    try:
        result = _git(cwd, 'rev-parse', '--show-toplevel', run=run)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


_REPO_SEEN = {}
REPO_KEPT = 10.0


def in_repo(cwd):
    """Whether the project folder is in a git repository, for what a screen says (the Approve button's words), as of the last
    few seconds. Asking starts three git processes (the filter guard's two, then git's own), and task_detail asks every 700 ms
    while a task works, with the host answering one request at a time. Nothing that records, commits or starts a run uses
    this: they call repo_root, which always asks git."""
    now = time.monotonic()
    seen = _REPO_SEEN.get(cwd)
    if seen is not None and now - seen[0] < REPO_KEPT:
        return seen[1]
    found = repo_root(cwd) is not None
    if len(_REPO_SEEN) >= 64:
        _REPO_SEEN.clear()
    _REPO_SEEN[cwd] = (now, found)
    return found


def changed_files(task, evidence):
    """The files this task's current run changed, from the completion record when there is one, otherwise from evidence."""
    completion = task.get('completion') or {}
    if completion.get('changed_files'):
        return sorted(f for f in completion['changed_files'] if isinstance(f, str))
    attempts = evidence.get('attempts') or []
    if not attempts or not task.get('runs'):
        return []
    from human_acceptance import HumanAcceptance
    first_number = task['runs'][0].get('prior_attempts', 0) + 1
    return HumanAcceptance.changed_scope(evidence, first_number, attempts[-1])


def approved_hashes(evidence):
    attempts = evidence.get('attempts') or []
    return dict((attempts[-1].get('after') or {})) if attempts else {}


def preimages(task, evidence):
    """Each file's first recorded preimage in this task's run ({path: snapshot dict or None for a file it created}),
    the same reading as HumanAcceptance.changed_scope: what the file held before this task touched it."""
    attempts = evidence.get('attempts') or []
    if not attempts or not task.get('runs'):
        return {}
    first_number = task['runs'][0].get('prior_attempts', 0) + 1
    last = attempts[-1]
    before = {}
    for attempt in sorted([a for a in attempts if first_number <= a['number'] <= last['number']] or [last], key=lambda a: a['number']):
        previous, following = attempt.get('before') or {}, attempt.get('after') or {}
        for path in set(previous) | set(following):
            before.setdefault(path, previous.get(path))
    return before


def target_bytes(cwd, path):
    return (Path(cwd) / path).read_bytes()


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _request(task):
    """The first user message of the task, or a stand-in when it has none."""
    return next((m.get('text', '') for m in task.get('messages') or [] if m.get('role') == 'user'), '') or 'Coding task'


def _cut_at_word(text, limit):
    if len(text) <= limit:
        return text.rstrip(' .,;:')
    cut = text[:limit]
    if ' ' in cut:
        cut = cut[:cut.rindex(' ')]
    return cut.rstrip(' .,;:')


def task_title(task):
    """The agent's own title for this task, from the newest Task guide that has one, cleaned the way a thread name is
    (thread_names.tidy_title drops Markdown, paths and keys), or None."""
    for run in reversed(task.get('runs') or []):
        guide = run.get('guide') if isinstance(run, dict) else None
        title = guide.get('title') if isinstance(guide, dict) else None
        if isinstance(title, str) and title.strip():
            from thread_names import tidy_title
            title = tidy_title(title)
            if title:
                return title
    return None


def subject(task, thread_name):
    """``Dervo: <title>``, cut at a word boundary. The title is this task's own Task guide title, else the conversation's
    name when it has a real one (the automatic placeholder never counts), else the first line of the request. Until
    23 Sep a follow-up's subject was its message, so real commits read "Dervo: go". The prefix stays:
    native_evidence.dervo_commits_only knows Dervo's commits by it."""
    title = task_title(task)
    name = ' '.join(thread_name.split()) if isinstance(thread_name, str) else ''
    if not title and name and name != PLACEHOLDER_NAME:
        title = name
    return f'Dervo: {_cut_at_word(title or request_line(task), SUBJECT_LIMIT)}'


def request_line(task):
    """The first line of the first user message, whitespace collapsed, cut to 120 characters. The subject may be the
    agent's words for the thread, so the body keeps yours."""
    lines = [line for line in _request(task).splitlines() if line.strip()]
    return ' '.join((lines[0] if lines else 'Coding task').split())[:120]


def message(task, files, how, thread_name=None, left=None, carried=None):
    who = ('Dervo made this commit. The agent had full access; Dervo checked it left git history unchanged.' if task.get('full_access')
           else 'Dervo made this commit; the agent ran no git.')
    # 16.2: "automatic" is a project's Commit results automatically setting; the line keeps " in Dervo. Dervo made this
    # commit", which native_evidence.dervo_commits_only reads.
    done = 'approved and committed automatically' if how == 'automatic' else how
    lines = [subject(task, thread_name), '', f'Task {task["id"]} {done} in Dervo. {who}', f'Request: {request_line(task)}', 'Changed files:']
    left = left or {}
    lines += [f'- {path}' + (f" (this task's change only; {left[path]}'s uncommitted change stays in the folder)" if path in left else '')
              for path in files]
    if carried:
        # Never a line that starts "Task <id>": run_history reads that line for the task a commit belongs to.
        start = f"Carried on from {carried['here']}'s stopped run, task {carried['from_task_id']}."
        lines.append(f"{start} Its changes before the handoff are in this commit too: {', '.join(carried['files'])}." if carried['files']
                     else f'{start} None of its files are in this commit.')
        lines += [f"Left out: {item['path']} ({item['why']})" for item in carried.get('flagged') or []]
    return '\n'.join(lines) + '\n'


def other_thread(others, path):
    """Who else's uncommitted change a file holds, for a message: the thread's name when it's known. ``others`` maps
    a path to a name, or to dict(name, added) where ``added`` are the lines that thread's own change put in."""
    entry = (others or {}).get(path)
    name = entry.get('name') if isinstance(entry, dict) else entry
    return name if isinstance(name, str) and name.strip() else 'another thread'


def _lines(data):
    return {line.strip() for line in data.decode('utf-8', 'replace').splitlines() if line.strip()}


def theirs_inside(others, path, before, after):
    """Whether lines another thread's own change added are among the lines this task's change added (reviewer,
    20 Sep: a thread that wrote the file while this task ran leaves its lines inside this task's approved bytes, where
    a preimage that still matches HEAD wouldn't show them). Only judged when the caller recorded that thread's lines."""
    entry = (others or {}).get(path)
    added = entry.get('added') if isinstance(entry, dict) else None
    if not added or not isinstance(after, dict) or not after.get('bytes_b64'):
        return False
    mine = _lines(base64.b64decode(after['bytes_b64'])) - (_lines(base64.b64decode(before['bytes_b64'])) if isinstance(before, dict) and before.get('bytes_b64') else set())
    return bool(mine & {str(line).strip() for line in added if str(line).strip()})


PENDING = 'dervo-pending-index.json'  # in the git dir while a commit has moved HEAD but the index isn't refreshed yet


def _git_dir(cwd, run=subprocess.run):
    result = _git(cwd, 'rev-parse', '--git-dir', run=run)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else Path(cwd) / path


def _pending_path(cwd, run=subprocess.run):
    git_dir = _git_dir(cwd, run=run)
    return git_dir / PENDING if git_dir else None


def _read_pending(marker):
    """The pending records in the marker: a list (one per commit awaiting its index refresh or its task's claim)."""
    try:
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if isinstance(data, dict) and isinstance(data.get('pending'), list):
        return [item for item in data['pending'] if isinstance(item, dict)]
    return [data] if isinstance(data, dict) and data.get('sha') else []


def _write_pending(marker, items):
    if items:
        marker.write_text(json.dumps(dict(pending=items)), encoding='utf-8')
    else:
        marker.unlink(missing_ok=True)


def repair_pending(cwd, run=subprocess.run):
    """Finish commits that moved HEAD but never refreshed the index (a crash or a held index.lock between the two,
    reviewer, 20 Sep): the index still holds the parent's version of the committed files, so the user's next commit
    would undo Dervo's. Returns the record it repaired now (the newest, with any others under ``also``), None when
    there was nothing to do (no marker, a folder outside a repository, or everything refreshed already: running it
    twice is a no-op and never reverses anything), and raises Rejected when a refresh can't be done now. Paths in
    the marker are the repository's own, reset with ``:/`` pathspecs, so a sibling project in the same repository
    repairs the same entries. A record stays in the marker until ``commit()`` for that task claims it: a retry after
    a host restart then gets its landed commit instead of making an empty second one."""
    try:
        marker = _pending_path(cwd, run=run)
        if marker is None or not marker.is_file():
            return None
        kept, repaired = [], []
        for pending in _read_pending(marker):
            files = [f for f in pending.get('repo_files') or [] if isinstance(f, str)]
            sha = pending.get('sha')
            landed = isinstance(sha, str) and _git(cwd, 'merge-base', '--is-ancestor', sha, 'HEAD', run=run).returncode == 0
            if not landed:
                continue  # the commit never landed (or was removed by hand): the index has nothing to catch up on
            if not pending.get('refreshed'):
                keep = [f for f in files if f not in (pending.get('staged') or [])]
                if keep and _git(cwd, 'reset', '-q', '--', *[':/' + f for f in keep], run=run).returncode != 0:
                    # The marker is left as it is; the next call tries again.
                    raise Rejected(f'A Dervo commit ({sha[:7]}) landed but git couldn\u2019t refresh the index afterwards, so your next commit '
                                   f'would undo it. Run "git reset -- {" ".join(":/" + f for f in keep)}" in the project folder (git may be holding index.lock).')
                pending['refreshed'] = True
                repaired.append(pending)
            kept.append(pending)
        _write_pending(marker, kept)
        if not repaired:
            return None
        return dict(repaired[-1], **({'also': repaired[:-1]} if len(repaired) > 1 else {}))
    except Rejected:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _claim_pending(cwd, task, run=subprocess.run):
    """The marker record for this task's own landed commit (refreshed), removed from the marker; None when there is none."""
    try:
        repair_pending(cwd, run=run)
        marker = _pending_path(cwd, run=run)
        if marker is None or not marker.is_file():
            return None
        records = _read_pending(marker)
    except (Rejected, OSError, ValueError, subprocess.SubprocessError):
        return None
    mine = next((p for p in records if p.get('task_id') == task.get('id') and p.get('refreshed')), None)
    if mine is None:
        return None
    _write_pending(marker, [p for p in records if p is not mine])
    return mine


def _normalised(cwd, repo_path, data, run=subprocess.run):
    """(blob id, bytes) as git stores ``data`` at ``repo_path``: line endings normalised the way text=auto, eol and
    autocrlf ask (the repository's own filter drivers stay switched off). Comparing and merging in this form means a
    CRLF working tree never reads as a different file from the LF blob HEAD holds (reviewer, 20 Sep)."""
    blob = _git(cwd, 'hash-object', '-w', f'--path={repo_path}', '--stdin', run=run, text=False, stdin=data)
    if blob.returncode != 0:
        return None, None
    blob_id = blob.stdout.decode().strip()
    stored = _git(cwd, 'cat-file', '-p', blob_id, run=run, text=False)
    return (blob_id, stored.stdout) if stored.returncode == 0 else (None, None)


def _merged(cwd, temp, path, head_bytes, before, after, run=subprocess.run):
    """This task's own change carried onto HEAD: a three-way merge (git merge-file) of HEAD's bytes with the change from
    the task's preimage to its approved bytes, all three as bytes in git's stored form. None when the two changes touch
    the same lines."""
    paths = []
    for name, data in (('head', head_bytes), ('base', before), ('mine', after)):
        target = os.path.join(temp, name)
        with open(target, 'wb') as fh:
            fh.write(data)
        paths.append(target)
    result = _git(cwd, 'merge-file', '-p', '-L', 'HEAD', '-L', 'before this task', '-L', 'this task', *paths, run=run, text=False)
    return result.stdout if result.returncode == 0 else None


def commit(task, evidence, cwd, *, how='approved', run=subprocess.run, thread_name=None, others=None, carried=None):
    """Commit exactly this task's changes to ``files`` on the current branch. Returns the commit record; raises Rejected
    with a plain reason.

    Night 2, finding 2: three threads shared one folder, and committing "Fix missing-file crash" took all of tally.py,
    including the --version line another thread had left uncommitted. A file whose HEAD bytes are what this task first
    saw (its preimage) is committed whole. A file that already held someone else's uncommitted change when this task
    started is committed as this task's own hunks only (preimage to approved bytes, applied to HEAD in a temporary
    index, a three-way merge with this task's preimage as the base), and that other change stays in the folder, uncommitted. When the two changes touch the same lines the
    commit is refused and the line names the other thread (``others`` is {path: thread name}, from the caller's
    records). The real index is never used to build the commit and only these paths are refreshed in it afterwards.

    ``thread_name`` is the conversation's name for the subject (task_push.thread_name_for reads it). None keeps the
    cut-down first message.

    ``carried`` is the plan for a task that carried on from a handoff snapshot (ConversationCoding.carried_plan, 23 Sep):
    the stopped run's files join this task's as one piece of work. ``before`` gives each such file's version from before
    the stopped run touched it, in place of this task's own preimage, and ``after`` the bytes the stopped run left, for a
    file this task didn't change. Every rule above then applies to the whole set: a file is committed whole when HEAD
    still holds its ``before``, as its own change merged onto HEAD when it doesn't, and refused when that can't be done.
    Files the plan flagged aren't in it and aren't committed."""
    if task.get('commit') and task['commit'].get('sha') and not task['commit'].get('reverted'):
        raise Rejected('This task is already committed.')
    root = repo_root(cwd, run=run)
    if root is None:
        raise Rejected(NOT_A_REPO)
    pending = _claim_pending(cwd, task, run=run)
    if pending:
        # This task's own commit landed before Dervo could record it (a crash between the two): that's the commit.
        branch = _git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD', run=run)
        return {'sha': pending['sha'], 'branch': branch.stdout.strip() if branch.returncode == 0 else None,
                'files': list(pending.get('files') or []), 'at': int(pending.get('at') or time.time()), 'how': how, 'reverted': None,
                **({'left': pending['left']} if pending.get('left') else {})}
    files = changed_files(task, evidence)
    hashes = approved_hashes(evidence)
    before = preimages(task, evidence)
    # Beta.15 (Bryce, 24 Sep): a committed file the approved result deleted ({path: its committed version}, from
    # native_evidence.finish). Its removal is committed when HEAD still holds the version the task deleted.
    removals = dict(((evidence.get('attempts') or [{}])[-1].get('deleted') or {}) if evidence.get('attempts') else {})
    if carried:
        before.update(carried.get('before') or {})
        for path, after in (carried.get('after') or {}).items():
            hashes.setdefault(path, after)
        files = sorted(set(files) | set(carried.get('before') or {}))
    if not files:
        raise Rejected('This task changed no files, so there is nothing to commit.')
    moved_on = []  # files whose bytes aren't the approved ones: fine only when the difference is commits since, below
    for path in files:
        target = Path(cwd) / path
        if path in removals:
            if target.exists() or target.is_symlink():
                raise Rejected(f'{path} is back in the project folder since this result deleted it. Nothing was committed. '
                               'Request a correction or recheck first.')
            continue
        if not target.exists() and path not in hashes and before.get(path) is not None:
            # The task removed or renamed it: Dervo commits changed files only, as it always has (reviewer, 20 Sep).
            raise Rejected(f'{path} was removed or renamed by this task. Dervo doesn\u2019t commit removals or renames yet, so this '
                           'result stays uncommitted. Commit that change by hand in the project folder.')
        if not target.is_file() or target.is_symlink():
            raise Rejected(f'{path} is missing from the project folder. Nothing was committed.')
        if path not in hashes:
            raise Rejected(f'{path} changed after the checked result. Nothing was committed. Request a correction or recheck first.')
        if _sha256(target) != hashes[path].get('sha256'):
            moved_on.append(path)
    import tempfile
    left = {}
    landed = None  # the commit's sha once HEAD has moved: from then on nothing may say "nothing was committed"
    warning = None
    try:
        with tempfile.TemporaryDirectory() as temp:
            env = {'GIT_INDEX_FILE': os.path.join(temp, 'index')}
            parent = _git(cwd, 'rev-parse', '-q', '--verify', 'HEAD^{commit}', run=run)
            parent = parent.stdout.strip() if parent.returncode == 0 else None
            seeded = _git(cwd, 'read-tree', parent, run=run, env=env) if parent else _git(cwd, 'read-tree', '--empty', run=run, env=env)
            if seeded.returncode != 0:
                raise Rejected('Git could not read the current commit. Nothing was committed.')
            # The project folder may sit inside a larger repository: index paths are the repository's, not the folder's.
            prefix = _git(cwd, 'rev-parse', '--show-prefix', run=run)
            prefix = prefix.stdout.strip() if prefix.returncode == 0 else ''
            staged_by_you = []  # repo paths whose index entry is your own staged change, never reset (reviewer, 20 Sep)
            for path in files:
                repo_path = prefix + path
                head_bytes, head_blob = None, None
                if parent:
                    # ls-tree answers empty for a path HEAD doesn't have and fails only when git itself can't read HEAD.
                    listed = _git(cwd, 'ls-tree', 'HEAD', '--', path, run=run)
                    if listed.returncode != 0:
                        raise Rejected(f'Git could not read the committed {path}. Nothing was committed.')
                    if listed.stdout.split():
                        head_blob = listed.stdout.split()[2]
                        head = _git(cwd, 'show', f'HEAD:./{path}', run=run, text=False)
                        if head.returncode != 0:
                            raise Rejected(f'Git could not read the committed {path}. Nothing was committed.')
                        head_bytes = head.stdout
                yours = _git(cwd, 'ls-files', '--stage', '--', path, run=run)
                if yours.returncode == 0 and yours.stdout.split() and yours.stdout.split()[1] != (head_blob or ''):
                    staged_by_you.append(repo_path)
                mine = before.get(path)
                if path in removals:
                    if head_bytes is None:
                        continue  # HEAD doesn't hold it any more: nothing left to remove
                    if not isinstance(mine, dict) or hashlib.sha256(head_bytes).hexdigest() != mine.get('sha256'):
                        raise Rejected(f'{path} changed in git after this task deleted it. Nothing was committed. '
                                       'Request a correction or recheck first.')
                    if _git(cwd, 'update-index', '--force-remove', '--', path, run=run, env=env).returncode != 0:
                        raise Rejected(f'Git could not stage removing {path}. Nothing was committed.')
                    continue
                who = other_thread(others, path)
                if theirs_inside(others, path, mine, hashes[path]):
                    raise Rejected(f'{path} holds lines {who} wrote while this task ran, and Dervo can\u2019t separate them from this '
                                   f'task\u2019s. Commit {who} first, or ask this thread to redo its change. Nothing was committed.')
                # Compared and merged as git stores them (line endings normalised), never as raw working-tree bytes.
                mine_blob, mine_bytes = _normalised(cwd, repo_path, base64.b64decode(mine['bytes_b64']), run=run) if isinstance(mine, dict) and mine.get('bytes_b64') else (None, None)
                same = (head_bytes is None and mine is None) or (head_blob is not None and mine is not None
                                                                 and (mine_blob == head_blob or hashlib.sha256(head_bytes).hexdigest() == mine.get('sha256')))
                if same and path in moved_on:
                    raise Rejected(f'{path} changed after the checked result. Nothing was committed. Request a correction or recheck first.')
                if same:
                    # The file was as HEAD has it when this task started: everything since is this task's. --path lets
                    # text=auto and autocrlf apply as a normal commit would; the repository's own filter drivers are
                    # already switched off for this call (filter_guards).
                    blob = _git(cwd, 'hash-object', '-w', f'--path={repo_path}', '--stdin', run=run, text=False, stdin=(Path(cwd) / path).read_bytes())
                    if blob.returncode != 0:
                        raise Rejected(f'Git could not store {path}. Nothing was committed.')
                    mode = '100755' if os.access(Path(cwd) / path, os.X_OK) else '100644'
                    if head_bytes is not None:
                        entry = _git(cwd, 'ls-files', '--stage', '--', path, run=run, env=env)
                        if entry.returncode != 0 or not entry.stdout.split():
                            raise Rejected(f'Git could not read the index entry for {path}. Nothing was committed.')
                        mode = entry.stdout.split()[0]
                    staged = _git(cwd, 'update-index', '--add', '--cacheinfo', f'{mode},{blob.stdout.decode().strip()},{repo_path}', run=run, env=env)
                    if staged.returncode != 0:
                        raise Rejected(f'Git could not stage {path}. Nothing was committed.')
                    continue
                if path in moved_on and (head_bytes is None or mine is None):
                    raise Rejected(f'{path} changed after the checked result. Nothing was committed. Request a correction or recheck first.')
                if head_bytes is None or mine_bytes is None or not hashes[path].get('bytes_b64'):
                    raise Rejected(f'{path} already held {who}\u2019s uncommitted change when this task started, and Dervo can\u2019t '
                                   f'separate the two. Commit {who} first, then this one. Nothing was committed.')
                _, after_bytes = _normalised(cwd, repo_path, base64.b64decode(hashes[path]['bytes_b64']), run=run)
                if after_bytes is None:
                    raise Rejected(f'Git could not store {path}. Nothing was committed.')
                merged = _merged(cwd, temp, path, head_bytes, mine_bytes, after_bytes, run=run)
                if merged is None and path in moved_on:
                    raise Rejected(f'{path} changed after the checked result. Nothing was committed. Request a correction or recheck first.')
                if merged is None:
                    raise Rejected(f'{path} also holds {who}\u2019s uncommitted change on the same lines, so Dervo can\u2019t commit this '
                                   f'task\u2019s part on its own. Commit {who} first, or ask this thread to redo its change. Nothing was committed.')
                if path in moved_on:
                    # The approved bytes are gone from the folder because commits landed since (the other thread's,
                    # or yours). That's fine only when the file now reads as HEAD plus exactly this task's change: then
                    # this task's own work is intact and nothing else is uncommitted in it.
                    _, disk = _normalised(cwd, repo_path, target_bytes(cwd, path), run=run)
                    if merged != disk:
                        raise Rejected(f'{path} changed after the checked result. Nothing was committed. Request a correction or recheck first.')
                else:
                    left[path] = who
                blob = _git(cwd, 'hash-object', '-w', f'--path={repo_path}', '--stdin', run=run, text=False, stdin=merged)
                entry = _git(cwd, 'ls-files', '--stage', '--', path, run=run, env=env)
                if entry.returncode != 0 or not entry.stdout.split():
                    raise Rejected(f'Git could not read the index entry for {path}. Nothing was committed.')
                mode = entry.stdout.split()[0]
                if blob.returncode != 0 or _git(cwd, 'update-index', '--add', '--cacheinfo', f'{mode},{blob.stdout.decode().strip()},{repo_path}',
                                                run=run, env=env).returncode != 0:
                    raise Rejected(f'Git could not stage {path}. Nothing was committed.')
            tree = _git(cwd, 'write-tree', run=run, env=env)
            if tree.returncode != 0 or not tree.stdout.strip():
                raise Rejected('Git could not build the commit. Nothing was committed.')
            if parent:
                # A tree equal to the parent's would be an empty commit: HEAD already holds these changes (a retry
                # after a crash whose marker was lost, or a change committed by hand). Reviewer, 20 Sep.
                parent_tree = _git(cwd, 'rev-parse', 'HEAD^{tree}', run=run)
                if parent_tree.returncode == 0 and parent_tree.stdout.strip() == tree.stdout.strip():
                    raise Rejected('HEAD already holds exactly these changes, so there is nothing new to commit. Nothing was committed.')
            made = _git(cwd, 'commit-tree', tree.stdout.strip(), *(['-p', parent] if parent else []), '-m',
                        message(task, files, how, thread_name, left, carried), run=run)
            if made.returncode != 0:
                detail = (made.stderr or made.stdout).strip().splitlines()
                reason = detail[-1][:200] if detail else 'git commit failed'
                if 'Please tell me who you are' in (made.stderr or '') or 'user.name' in (made.stderr or '') or 'user.email' in (made.stderr or ''):
                    reason = 'Git needs your name and email. Set user.name and user.email with git config, then commit again.'
                raise Rejected(f'Nothing was committed: {reason}')
            sha = made.stdout.strip()
            # The marker outlives a crash between moving HEAD and refreshing the index; repair_pending reads it. Paths
            # are the repository's own, so a sibling project in the same repository repairs the same index entries.
            marker = _pending_path(cwd, run=run)
            if marker is None:
                raise Rejected('Git could not find its own folder. Nothing was committed.')
            record = dict(task_id=task['id'], sha=sha, parent=parent, files=list(files), repo_files=[prefix + f for f in files],
                          staged=staged_by_you, left=left, at=int(time.time()))
            others_pending = [p for p in _read_pending(marker) if p.get('sha') != sha]
            _write_pending(marker, others_pending + [record])
            moved = _git(cwd, 'update-ref', '-m', f'commit: {subject(task, thread_name)}', 'HEAD', sha, *([parent] if parent else []), run=run)
            if moved.returncode != 0:
                _write_pending(marker, others_pending)
                raise Rejected('Nothing was committed: the branch moved while Dervo was committing. Try again.')
            landed = sha
        # Your index now matches HEAD for these paths (their stat is refreshed, nothing else you staged is touched), so
        # git status shows only what stays uncommitted: another thread's change in a shared file. A path you had staged
        # yourself keeps your staged version. A held index.lock gets one more try; after that the commit stands, the
        # marker stays for repair_pending, and the record says so.
        keep = [prefix + f for f in files if prefix + f not in staged_by_you]
        refreshed = _git(cwd, 'reset', '-q', '--', *[':/' + f for f in keep], run=run) if keep else None
        if refreshed is not None and refreshed.returncode != 0:
            time.sleep(0.5)
            refreshed = _git(cwd, 'reset', '-q', '--', *[':/' + f for f in keep], run=run)
        if refreshed is None or refreshed.returncode == 0:
            _write_pending(marker, [p for p in _read_pending(marker) if p.get('sha') != sha])
        else:
            warning = (f'Committed as {sha[:7]}, but git couldn\u2019t refresh your index afterwards (it may be holding index.lock). '
                       f'Until Dervo\u2019s next git call repairs it, run "git reset -- {" ".join(":/" + f for f in keep)}" in the project folder, or your '
                       'next commit would undo this one.')
    except (OSError, subprocess.SubprocessError) as exc:
        if landed is None:
            raise Rejected('Git could not run. Nothing was committed.') from None
        # HEAD moved before git stopped answering (a timeout on the index refresh): say what happened, not "nothing".
        why = 'timed out' if isinstance(exc, subprocess.TimeoutExpired) else 'could not run'
        warning = (f'Committed as {landed[:7]}, but git {why} while refreshing your index afterwards. Until Dervo\u2019s next git call '
                   f'repairs it, run "git reset -- {" ".join(":/" + prefix + f for f in files)}" in the project folder, or your next commit would undo this one.')
        return {'sha': landed, 'branch': None, 'files': files, 'at': int(time.time()), 'how': how, 'reverted': None,
                **({'left': left} if left else {}), 'index_warning': warning, **_carried_record(carried)}
    head = _git(cwd, 'rev-parse', 'HEAD', run=run)
    if head.returncode != 0 or head.stdout.strip() != sha:
        raise Rejected('The commit was made but its ID could not be read. Check git log before undoing anything.')
    branch = _git(cwd, 'rev-parse', '--abbrev-ref', 'HEAD', run=run)
    return {'sha': sha, 'branch': branch.stdout.strip() if branch.returncode == 0 else None,
            'files': files, 'at': int(time.time()), 'how': how, 'reverted': None, **({'left': left} if left else {}),
            **({'index_warning': warning} if warning else {}), **_carried_record(carried)}


def _carried_record(carried):
    """What a commit record keeps of a carried plan: whose files joined, and what was left out and why. No bytes."""
    if not carried:
        return {}
    return {'carried': dict(from_task_id=carried.get('from_task_id'), here=carried.get('here'), files=list(carried.get('files') or []),
                            flagged=[dict(item) for item in carried.get('flagged') or []])}


def revert(task, cwd, *, run=subprocess.run, thread_name=None):
    """Undo this task: one commit that puts exactly its files back to their state before Dervo's commit.

    ``git revert`` refuses to run while anything else is staged, and it would touch every file in the commit even if
    later work built on one. So the undo is file-limited: it refuses when any of the task's files changed after Dervo's
    commit (in a later commit or in the working tree), restores each file from the commit's parent, and commits only
    those files. Your other staged or unsaved changes stay as they are.
    """
    record = task.get('commit') or {}
    if not record.get('sha'):
        raise Rejected('This task has no commit to undo.')
    if record.get('reverted'):
        raise Rejected('This task was already undone.')
    if repo_root(cwd, run=run) is None:
        raise Rejected(NOT_A_REPO)
    repair_pending(cwd, run=run)
    files = list(record.get('files') or [])
    if not files:
        raise Rejected('This task’s commit lists no files.')
    sha = record['sha']
    try:
        if _git(cwd, 'cat-file', '-e', f'{sha}^{{commit}}', run=run).returncode != 0:
            raise Rejected('This task’s commit is no longer in the repository, so Dervo can’t undo it.')
        later = _git(cwd, 'diff', '--quiet', sha, 'HEAD', '--', *files, run=run).returncode
        unsaved = _git(cwd, 'diff', '--quiet', 'HEAD', '--', *files, run=run).returncode
        staged = _git(cwd, 'diff', '--quiet', '--cached', 'HEAD', '--', *files, run=run).returncode
        if later or unsaved or staged:
            raise Rejected('Later changes overlap this task’s files, so Dervo didn’t undo it. Nothing was changed.')
        # Each file goes back to the parent's version; a file the task created is removed.
        for path in files:
            existed = _git(cwd, 'cat-file', '-e', f'{sha}^:./{path}', run=run).returncode == 0  # ./ keeps the path relative to the project folder
            step = _git(cwd, 'checkout', f'{sha}^', '--', path, run=run) if existed else _git(cwd, 'rm', '--quiet', '--', path, run=run)
            if step.returncode != 0:
                _git(cwd, 'checkout', 'HEAD', '--', *files, run=run)
                raise Rejected('Dervo couldn’t restore the earlier version of ' + path + '. Nothing was committed.')
        # Beta.18: "Dervo: Undo ..." with the " in Dervo. Dervo made this commit" line native_evidence.dervo_commits_only
        # reads, so clicking Undo while another full-access run works in this folder doesn't end that run with "The agent
        # changed git history". Up to 17.1 this was "Revert Dervo: ...", which didn't pass. No line starts "Task <id>".
        title = subject(task, thread_name).removeprefix('Dervo: ')
        text = (f'Dervo: Undo {title}\n\nUndoes commit {sha} for task {task["id"]} in Dervo. Dervo made this commit after you '
                'clicked Undo; the agent ran no git.\n')
        result = _git(cwd, 'commit', '--quiet', '--only', '-m', text, '--', *files, run=run)
        if result.returncode != 0:
            _git(cwd, 'checkout', 'HEAD', '--', *files, run=run)
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise Rejected('Nothing was undone: ' + (detail[-1][:200] if detail else 'git commit failed'))
        head = _git(cwd, 'rev-parse', 'HEAD', run=run)
    except (OSError, subprocess.SubprocessError):
        raise Rejected('Git could not run. Nothing was changed.') from None
    if head.returncode != 0 or not head.stdout.strip():
        raise Rejected('The undo commit was made but its ID could not be read. Check git log.')
    return {'sha': head.stdout.strip(), 'at': int(time.time())}
