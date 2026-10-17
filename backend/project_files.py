"""A user-clicked file within the selected project: reveal it, open it with its default app, or read a Markdown or text
file for Preview. Nothing here runs a file. Also the files git reports as changed since a thread's first run, so Preview
can list what an agent made with shell commands (beta.15, group 1)."""
import os
from pathlib import Path
import subprocess
import time

from dervo_connections import Rejected
import platform_os

FIELDS = {
    'project_file_open': {'projectId', 'path'},
    'project_file_text': {'projectId', 'path'},
    'preview_changes': {'projectId', 'since'},
}
# The thread a link or Preview came from, when there is one. A thread that changes code works in its own folder
# (thread_folders), so its new and changed files are there and not in the project folder (Bryce, 8 Oct).
OPTIONAL = frozenset({'threadId'})
ACTOR = 'local-user'
# Documents macOS opens in a viewer or editor. Never a script, an app, an installer, a shortcut or a folder: `open` on
# one of those runs it (a .command, a .app bundle, a .webloc pointing anywhere).
OPENABLE = frozenset({'html', 'htm', 'md', 'markdown', 'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg',
                      'heic', 'csv', 'json', 'log', 'mov', 'mp4', 'm4v', 'mp3', 'wav', 'rtf', 'docx', 'xlsx', 'pptx'})
TEXT = frozenset({'md', 'markdown', 'txt'})
TEXT_LIMIT = 512 * 1024
# What Preview shows: pages, images, Markdown and PDF. Mirrors PreviewPane.tsx previewKind.
PREVIEWABLE = frozenset({'html', 'htm', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg', 'md', 'markdown', 'pdf'})
CHANGES_LIMIT = 200
# The whole git read behind Preview's list, all its calls together. It runs on the host's one request loop, and git's own
# budget is 30 s a call (task_git.TIMEOUT): a slow status in a big folder with untracked files would reach the bridge's
# 10 s kill and take every open run down with the host. Past this the list is only what the write tools recorded.
CHANGES_SECONDS = 4


# dd #15 (beta.15): an agent often writes a path from the folder it worked in ("src/local/Home.tsx" from frontend/), so a
# relative path that isn't there from the project root is looked for as the end of one path in the project. Bounded, as
# it runs on the host's one request loop.
MATCH_SECONDS = 1.5
MATCH_ENTRIES = 200000
SKIP_FOLDERS = frozenset({'node_modules', 'target', 'dist', 'build', '__pycache__', 'venv', 'Pods', 'DerivedData'})
MISSING = 'That file couldn’t be found. It may have moved or been deleted.'


def _one_match(root, relative, deadline=None):
    """The one file or folder in the project whose path ends with ``relative``, or None. Hidden, dependency and build
    folders are skipped. Several matches are refused rather than guessed. ``deadline`` is shared across the folders a
    link is looked for in, so two of them still take MATCH_SECONDS at most."""
    wanted = tuple(part for part in relative.parts if part != '.')
    if not wanted or '..' in wanted:
        return None
    found, seen = [], 0
    deadline = deadline if deadline is not None else time.monotonic() + MATCH_SECONDS
    for folder, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if not name.startswith('.') and name not in SKIP_FOLDERS]
        base = Path(folder).relative_to(root).parts
        for name in dirs + files:
            if name == wanted[-1] and (base + (name,))[-len(wanted):] == wanted and len(base) + 1 > len(wanted):
                found.append(root.joinpath(*base, name))
        seen += len(dirs) + len(files)
        if len(found) > 1 or seen > MATCH_ENTRIES or time.monotonic() > deadline:
            break
    if len(found) > 1:
        raise Rejected(f'More than one file in this project ends with {relative.as_posix()}. Use its full path.')
    return found[0].resolve(strict=True) if found else None


def _roots(cwd):
    """The folders a link is looked for in, first one first: a thread's own folder and then the project folder, or just
    the project folder. A thread folder that's gone (merged, archived) is skipped; the project folder never is."""
    folders = [cwd] if isinstance(cwd, (str, os.PathLike)) else list(cwd)
    roots = []
    for index, folder in enumerate(folders):
        try:
            roots.append(Path(folder).resolve(strict=True))
        except (OSError, ValueError, RuntimeError):
            if index == len(folders) - 1:
                raise Rejected('The project folder isn’t there any more. It may have been moved or renamed.') from None
    return list(dict.fromkeys(roots))


def _target(cwd, requested, *, anywhere=False):
    """The real path a link names, and the folder it's in. ``cwd`` is the project folder, or a thread's folder then the
    project folder (``folders``): an exact path in either wins over a guessed one, and the thread's own copy wins over the
    project's. ``anywhere``: an absolute or ~ path may be outside them (Show in Finder only)."""
    if type(requested) is not str or not requested or len(requested) > 4096 or any(ord(c) < 32 for c in requested):
        raise Rejected('This file link isn’t a valid path.')
    roots = _roots(cwd)
    candidate = Path(os.path.expanduser(requested) if requested.startswith('~/') else requested)
    if candidate.is_absolute():
        try:
            target = candidate.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            raise Rejected(MISSING) from None
        root = next((root for root in roots if target.is_relative_to(root)), None)
        if root is None and not anywhere:
            raise Rejected('That file is outside this project, so Dervo only shows it in Finder. Use Show in Finder.')
        return root or roots[0], target
    escaped = False
    for root in roots:
        try:
            target = (root / candidate).resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            continue
        if target.is_relative_to(root):
            return root, target
        escaped = True
    deadline = time.monotonic() + MATCH_SECONDS
    for root in roots:
        target = _one_match(root, candidate, deadline)
        if target is not None:
            return root, target
    if escaped:
        raise Rejected('That file is outside this project. Open its project before following the link.')
    raise Rejected(MISSING)


def _document(cwd, requested, kinds):
    """A regular file inside the project, with no hidden part in its path and one of ``kinds`` as its extension."""
    root, target = _target(cwd, requested)
    if any(part.startswith('.') for part in target.relative_to(root).parts):
        raise Rejected('Dervo doesn’t open hidden files or anything inside a hidden folder.')
    if not target.is_file():
        raise Rejected('This link doesn’t point to a file. Use Show in Finder for a folder.')
    if target.suffix[1:].lower() not in kinds:
        raise Rejected('Dervo only opens documents, pages and images from a link. Use Show in Finder for this one.')
    return root, target


def reveal(cwd, requested):
    # dd #15 (beta.15): a path an agent printed outside this project (a worktree, a screenshot in /private/tmp, Downloads)
    # is shown in Finder too. `open -R` only selects it and never opens or runs it; opening and Preview stay in the project.
    _, target = _target(cwd, requested, anywhere=True)
    if not target.is_file() and not target.is_dir():
        raise Rejected('This link doesn’t point to a regular file or folder.')
    result = subprocess.run(platform_os.reveal_command(target), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode:
        raise Rejected('Finder couldn’t reveal that file. Try opening the project folder.')
    return {'revealed': True}


def open_default(cwd, requested):
    """Open a document with the app macOS picks for it. Only the extensions in OPENABLE."""
    _, target = _document(cwd, requested, OPENABLE)
    result = subprocess.run(platform_os.open_command(target), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    if result.returncode:
        raise Rejected('macOS couldn’t open that file. Try Show in Finder.')
    return {'opened': True}


def read_text(cwd, requested):
    """A Markdown or text file's contents for Preview, up to 512 KB. Preview renders it without HTML or images."""
    root, target = _document(cwd, requested, TEXT)
    fd = os.open(target, os.O_RDONLY | platform_os.NOFOLLOW | getattr(os, 'O_BINARY', 0))  # no O_NOFOLLOW on Windows
    with os.fdopen(fd, 'rb') as handle:
        raw = handle.read(TEXT_LIMIT + 1)
    return {'path': target.relative_to(root).as_posix(), 'text': raw[:TEXT_LIMIT].decode('utf-8', errors='replace'),
            'truncated': len(raw) > TEXT_LIMIT}


def changes(cwd, since, run=subprocess.run):
    """Previewable files git reports as changed or untracked and modified at or after ``since`` (seconds), newest first.
    Ignored files aren't listed. Not a git folder, or git failing, is an empty list: Preview still has the files the
    agent's own write tools recorded."""
    if type(since) not in (int, float) or since < 0:
        raise Rejected('Choose when the thread’s first run started.')
    import native_evidence
    import time
    deadline = time.monotonic() + CHANGES_SECONDS

    def bounded(argv, **kwargs):
        # Once the budget is spent nothing more runs, so a filter check that timed out never lets status run unguarded.
        left = deadline - time.monotonic()
        if left <= 0:
            raise subprocess.TimeoutExpired(argv, 0)
        return run(argv, **{**kwargs, 'timeout': min(kwargs.get('timeout') or left, left)})
    try:
        paths = native_evidence.status(cwd, run=bounded)
    except Rejected:
        return {'files': []}
    root, found = Path(cwd), []
    for path, code in paths.items():
        parts = path.split('/')
        if 'D' in code or any(not part or part.startswith('.') for part in parts) or path.rsplit('.', 1)[-1].lower() not in PREVIEWABLE:
            continue
        try:
            info = (root / path).lstat()
        except OSError:
            continue
        if not os.path.isfile(root / path) or os.path.islink(root / path) or info.st_mtime < since:
            continue
        found.append({'path': path, 'at': info.st_mtime})
    found.sort(key=lambda item: (-item['at'], item['path']))
    return {'files': found[:CHANGES_LIMIT]}


def folders(host, project_id, project_cwd, thread_id=None):
    """Where a link from this thread is looked for: the thread's own folder first when it has one, then the project
    folder. No thread (Home, Next up) is the project folder alone."""
    if thread_id is None:
        return [project_cwd]
    if type(thread_id) is not str or not thread_id or len(thread_id) > 256:
        raise Rejected('Choose a thread.')
    # The thread's own checks (this project's, and yours if private), not its account: a link never needs one.
    import thread_folders
    host.store._access(thread_id, project_id, ACTOR)
    cwd, _ = thread_folders.cwd_for(host.store.db, thread_id, project_cwd)
    return [cwd, project_cwd] if cwd != project_cwd else [project_cwd]


def dispatch(host, operation, args):
    if type(args) is not dict or not FIELDS[operation] <= set(args) <= FIELDS[operation] | OPTIONAL:
        raise Rejected('Unsupported file fields.')
    pid = args['projectId']
    if type(pid) is not str or not pid or len(pid) > 256:
        raise Rejected('Choose a project.')
    row = host.store.db.execute('SELECT cwd FROM projects WHERE id=?', (pid,)).fetchone()
    if row is None:
        raise Rejected('This project is unavailable.')
    where = folders(host, pid, row[0], args.get('threadId'))
    if operation == 'project_file_open':
        return open_default(where, args['path'])
    if operation == 'project_file_text':
        return read_text(where, args['path'])
    # What the thread changed is in the folder it ran in.
    return changes(where[0], args['since'])
