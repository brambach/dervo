"""Full access: Claude Code runs its own shell and file tools in the project folder, and Dervo records what changed from git.

Bryce, 17 September 2026: "I should be able to use Dervo in order to build Dervo." In the default mode Dervo's host does
every read, write and check, so the evidence is exact by construction. In full access it can't be: the agent builds,
runs previews and edits files itself. So the record comes from git instead, before and after each run:

* Before: the project folder must be in a git repository with at least one commit. Dervo notes HEAD and the hash of every
  file git already reports as changed (your own uncommitted work).
* After: if HEAD moved and the run's git guard saw the agent try to move it, the agent changed git history and nothing is
  recorded as approvable. Beta.18: if the guard saw no attempt, the commit came from outside the run (you, by hand, or
  another tool), so the run is recorded on top of the new HEAD and the result names that commit. Otherwise every path git
  now reports, that wasn't already changed before the run, is a change of this run. Its before-image is the HEAD blob;
  its after-image is the file now. Both go into the task's evidence as a normal reviewable attempt, so the diff, Approve
  and commit, and Undo work the way they do for any task.
* What Dervo can't stand behind blocks approval with the reason named: a file that already had your uncommitted
  changes, a hidden, secret-named, linked or oversized file, or more files than one attempt records.
* Beta.15 (Bryce, 24 Sep): a file left changed by Dervo's own earlier run that nobody committed isn't your edit. Each run
  keeps the fingerprint of every file it left (``runs[].left``), and a later run that starts from exactly that records
  the file like any other. A committed file the run deleted is a reviewable change too (``deleted`` on the attempt),
  and approving it commits the removal (task_git.commit).

Nothing here runs a check. Approval is always required for a full-access task.
"""
import base64
import difflib
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from dervo_connections import Rejected
import task_git

DIRTY_LIMIT = 2000   # files already changed before a run that Dervo will fingerprint
CHANGE_LIMIT = 64    # files one full-access attempt records (TaskEvidence.ADMIT_LIMIT)
NOT_A_REPO = 'Full access needs the project folder in a git repository with at least one commit, so Dervo can record what changed.'


def _git(cwd, *args, run=subprocess.run, text=True):
    try:
        return task_git._git(cwd, *args, run=run, text=text)
    except (OSError, subprocess.SubprocessError):
        raise Rejected('Git could not run in the project folder.') from None


def _head(cwd, run=subprocess.run):
    result = _git(cwd, 'rev-parse', '--verify', 'HEAD', run=run)
    head = result.stdout.strip() if result.returncode == 0 else ''
    if not head:
        raise Rejected(NOT_A_REPO)
    return head


def status(cwd, run=subprocess.run):
    """Paths git reports as changed or untracked under the project folder, relative to it. Ignored files aren't listed."""
    prefix = _git(cwd, 'rev-parse', '--show-prefix', run=run)
    if prefix.returncode != 0:
        raise Rejected(NOT_A_REPO)
    prefix = prefix.stdout.strip()
    result = _git(cwd, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames', '--', '.', run=run)
    if result.returncode != 0:
        raise Rejected('Git couldn’t read the project folder’s status.')
    paths = {}
    for entry in result.stdout.split('\0'):
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if prefix and not path.startswith(prefix):
            continue
        paths[path[len(prefix):]] = code
    return paths


def fingerprint(cwd, path):
    """sha256 of a regular file's bytes, 'link' for a symlink, 'other' for anything else, None when it doesn't exist."""
    target = Path(cwd) / path
    if target.is_symlink():
        return 'link'
    if not target.exists():
        return None
    if not target.is_file():
        return 'other'
    digest = hashlib.sha256()
    with open(target, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def baseline(cwd, run=subprocess.run):
    """Recorded on the run before the agent starts."""
    if task_git.repo_root(cwd, run=run) is None:
        raise Rejected(NOT_A_REPO)
    head = _head(cwd, run=run)
    dirty = status(cwd, run=run)
    if len(dirty) > DIRTY_LIMIT:
        raise Rejected(f'The project folder has more than {DIRTY_LIMIT} uncommitted files. Commit or clean up first, so Dervo can tell your changes from the agent’s.')
    return {'head': head, 'dirty': {path: fingerprint(cwd, path) for path in dirty}}


def dervo_commits_only(cwd, base_head, head, run=subprocess.run):
    """True when HEAD moved only by commits Dervo made for approved tasks (another chat's approval during this run)."""
    ancestor = _git(cwd, 'merge-base', '--is-ancestor', base_head, head, run=run)
    if ancestor.returncode != 0:
        return False
    bodies = _git(cwd, 'log', '--format=%B%x00', f'{base_head}..{head}', run=run)
    if bodies.returncode != 0:
        return False
    messages = [b.strip() for b in bodies.stdout.split('\0') if b.strip()]
    return bool(messages) and all(m.startswith('Dervo: ') and ' in Dervo. Dervo made this commit' in m for m in messages)


HELD = 'waiting in another thread’s result'


HISTORY_CHANGED = ('The agent changed git history during the run, so Dervo recorded nothing for approval. '
                   'Check git log in the project folder before continuing.')
MOVED_SHOWN = 5  # outside commits a result names


def outside_commits(cwd, base_head, head, run=subprocess.run):
    """{'from', 'to', 'branch', 'commits': [{sha, subject, at}]} for HEAD moving from ``base_head`` to ``head`` by commits
    made outside the run, newest first. ``commits`` is empty when the old HEAD isn't an ancestor (a reset, a checkout)."""
    moved = {'from': base_head, 'to': head, 'branch': None, 'commits': []}
    branch = _git(cwd, 'symbolic-ref', '-q', '--short', 'HEAD', run=run)
    if branch.returncode == 0 and branch.stdout.strip():
        moved['branch'] = branch.stdout.strip()[:200]
    if _git(cwd, 'merge-base', '--is-ancestor', base_head, head, run=run).returncode == 0:
        log = _git(cwd, 'log', f'--max-count={MOVED_SHOWN}', '--format=%H%x1f%s%x1f%ct%x00', f'{base_head}..{head}', run=run)
        for entry in (log.stdout if log.returncode == 0 else '').split('\0'):
            parts = entry.strip().split('\x1f')
            if len(parts) == 3 and parts[0]:
                moved['commits'].append({'sha': parts[0], 'subject': parts[1][:200], 'at': int(parts[2]) if parts[2].isdigit() else None})
    return moved


def outside_files(task):
    """{'count', 'files'} for files this run's own Edit and Write calls wrote outside the project folder (runs[].written
    keeps an absolute path for those), which recording never sees. None when there are none."""
    written = (task.get('runs') or [{}])[-1].get('written') or []
    home = os.path.expanduser('~')
    found = [path for path in written if isinstance(path, str) and (path.startswith('/') or path[1:3] in (':\\', ':/'))]
    if not found:
        return None
    shown = ['~' + path[len(home):] if path.startswith(home + os.sep) else path for path in found]
    return {'count': len(shown), 'files': shown[:ALONGSIDE_SHOWN]}


def changes(cwd, base, run=subprocess.run, *, own=None, earlier=None, held=None, detail=None, tried=None):
    """What this run changed: ([path], [problem sentences], [other paths]), or raises when git history moved.

    ``own`` is None when the run had the folder to itself: every new change is its own. When another chat shared the
    folder it's the set of paths this run's own edits touched, and only those are credited; other changes are listed,
    not blocked, because they belong to someone else or came from a command Dervo can't attribute.

    Beta.15: ``earlier`` is {path: {fingerprint: task id}} for what Dervo's own earlier runs left (earlier_left). A file
    that started this run exactly as one of them left it is this run's to record, not your uncommitted change. With a
    ``detail`` dict, a committed file the run deleted goes in detail['deleted'] instead of the problems, and the files
    recorded from an earlier run's leftovers in detail['inherited'] ({path: task id}).

    16.2: ``earlier`` holds only this thread's own waiting results, and ``held`` ({path: {fingerprint: holder}}, from
    waiting_left) another thread's. A file that started this run as another thread left it stays protected, and its
    problem says so; with ``detail`` the holder goes in detail['held'] ({path: holder}) so the block can name it.

    Beta.18: ``tried`` is what the run's git guard refused to move (claude_coding.git_tried): [] when it saw the agent
    try nothing, None when there was no guard to ask. HEAD moved with ``tried == []`` was a commit from outside the run
    (the 5 Oct "17.1: merge and test" stop), so the run is read on top of the new HEAD and, with ``detail``, the move goes
    in detail['moved_head'] (outside_commits). Anything else keeps the refusal."""
    head = _head(cwd, run=run)
    if head != base['head'] and not dervo_commits_only(cwd, base['head'], head, run=run):
        if tried != []:
            raise Rejected(HISTORY_CHANGED)
        if detail is not None:
            detail['moved_head'] = outside_commits(cwd, base['head'], head, run=run)
    now = status(cwd, run=run)
    changed, problems, others = [], [], []
    carry = {}  # 16.2: this thread's leftovers the run didn't touch. They join the result if it changed anything itself.
    for path in sorted(set(now) | set(base['dirty'])):
        current = fingerprint(cwd, path)
        if detail is not None and path in base['dirty'] and current == base['dirty'][path]:
            source = ((earlier or {}).get(path) or {}).get(current)
            if source is not None:
                carry[path] = source
            continue
        if own is not None and path not in own:
            if path not in base['dirty'] or current != base['dirty'][path]:
                others.append(path)
            continue
        inherited = None
        if path in base['dirty']:
            if current == base['dirty'][path]:
                continue
            inherited = ((earlier or {}).get(path) or {}).get(base['dirty'][path])
            if inherited is None:
                holder = ((held or {}).get(path) or {}).get(base['dirty'][path])
                if holder is not None:
                    problems.append(f'{path} ({HELD})')
                    if detail is not None:
                        detail.setdefault('held', {})[path] = holder
                else:
                    problems.append(f'{path} (it already had uncommitted changes when this run started)')
                continue
        if current is None and detail is not None and (size := _head_size(cwd, path, run=run)) is not None:
            why = deletion_problem(path, size, cwd)
            if why:
                problems.append(f'{path} ({why})')
                continue
            detail.setdefault('deleted', []).append(path)
        elif current is None and inherited is not None and detail is not None:
            continue  # a file an earlier run created and this one removed again: the folder matches HEAD there
        elif current is None:
            problems.append(f'{path} (deleted)')
            continue
        elif current in ('link', 'other'):
            problems.append(f'{path} (not a regular file)')
            continue
        else:
            changed.append(path)
        if inherited is not None and detail is not None:
            detail.setdefault('inherited', {})[path] = inherited
    if carry and (changed or detail.get('deleted')):
        # A follow-up that builds on this thread's unapproved result covers all of it, so approving the newest result
        # commits both (settle_carried then marks the earlier one). Diffs are against HEAD, as for every file here.
        for path, source in carry.items():
            printed = base['dirty'][path]
            if printed is None:
                size = _head_size(cwd, path, run=run)
                if size is None:
                    continue
                why = deletion_problem(path, size, cwd)
                if why:
                    problems.append(f'{path} ({why})')
                    continue
                detail.setdefault('deleted', []).append(path)
            elif printed in ('link', 'other'):
                continue
            else:
                changed.append(path)
            detail.setdefault('inherited', {})[path] = source
        changed.sort()
        if detail.get('deleted'):
            detail['deleted'].sort()
    return changed, problems, others


LEFT_LIMIT = 256  # files one run's leftovers are fingerprinted for


def left_prints(cwd, paths):
    """{path: fingerprint} of what a run left in the folder, None for a file it deleted (runs[].left)."""
    return {path: fingerprint(cwd, path) for path in sorted(paths)[:LEFT_LIMIT]}


def earlier_left(coding, task):
    """{path: {fingerprint: task id}} for files this thread's own earlier runs in this project folder left changed and
    nobody approved. A file that still holds exactly that wasn't touched since by you or another thread (waiting_left)."""
    return waiting_left(coding, task)[0]


def _thread_name(coding, other):
    try:
        import task_push
        return task_push.thread_name_for(coding.store.db, other)
    except Exception:  # noqa: BLE001 - the block still says another thread holds it
        return None


def waiting_left(coding, task):
    """(mine, theirs) for files Dervo's own earlier runs in this project folder left changed and nobody approved. ``mine``
    is {path: {fingerprint: task id}} for this thread's tasks (this task's earlier runs included): a follow-up builds on
    them and its result covers both. ``theirs`` is {path: {fingerprint: {task_id, thread_id, thread}}} for another
    thread's: 16.2 (Bryce, 27 Sep) keeps those protected, and the block names the thread holding the file.

    Security review S2 (24 Sep): only what's still waiting counts. A task whose result was committed (Undo included) left
    nothing unapproved, and for each file only the newest run's print counts, so restoring an old version by hand, or
    deleting a file some long-finished run once deleted, stays your change."""
    found, theirs, newest, cwd = {}, {}, {}, task['binding']['cwd']
    project = (task.get('context') or {}).get('project_id')
    thread = (task.get('context') or {}).get('thread_id')
    current = task['runs'][-1].get('id') if task.get('runs') else None
    try:
        rows = coding.store.db.execute('SELECT document FROM coding_tasks ORDER BY rowid DESC').fetchall()
    except Exception:  # noqa: BLE001 - no record to match is the old behaviour, never a failed finish
        return found, theirs
    for (document,) in rows:
        try:
            other = json.loads(document)
        except ValueError:
            continue
        if (other.get('context') or {}).get('project_id') != project or (other.get('binding') or {}).get('cwd') != cwd:
            continue
        commit = other.get('commit') if isinstance(other.get('commit'), dict) else {}
        if commit.get('sha'):
            continue
        # This task's newest run that recorded what it left (not the run finishing now).
        past = next((run for run in reversed(other.get('runs') or []) if isinstance(run, dict) and run.get('id') != current
                     and isinstance(run.get('left'), dict)), None)
        if past is None:
            continue
        at = past.get('left_at') if type(past.get('left_at')) in (int, float) else 0
        other_thread = (other.get('context') or {}).get('thread_id')
        same = other.get('id') == task.get('id') or (thread is not None and other_thread == thread)
        name = None
        for path, printed in past['left'].items():
            # Rows come newest task first, so on equal (or missing) times the newer task keeps the file.
            if isinstance(path, str) and (printed is None or isinstance(printed, str)) and (path not in newest or at > newest[path]):
                newest[path] = at
                found.pop(path, None)
                theirs.pop(path, None)
                if same:
                    found[path] = {printed: other.get('id')}
                else:
                    name = name or _thread_name(coding, other) or 'another thread'
                    theirs[path] = {printed: {'task_id': other.get('id'), 'thread_id': other_thread, 'thread': name}}
    return found, theirs


def _head_size(cwd, path, run=subprocess.run):
    """The committed file's size in bytes, or None when HEAD doesn't hold it. Reads no content."""
    result = _git(cwd, 'cat-file', '-s', f'HEAD:./{path}', run=run)
    size = result.stdout.strip() if result.returncode == 0 and isinstance(result.stdout, str) else ''
    return int(size) if size.isdigit() else None


def deletion_problem(path, size, cwd=None):
    """Why a committed file this run deleted can't be a reviewable deletion, or None (security review M1, 24 Sep). The same
    name rule and size cap as any file Dervo records: a deleted .env, secrets file or database stays unrecordable, so its
    committed contents never reach the task record or the diff, and nothing over 16 MiB is read back from git."""
    import project_access
    import task_evidence
    try:
        project_access.relative(path, cwd)
    except Rejected as exc:
        return str(exc).rstrip('.')
    if size > task_evidence.FILE_LIMIT:
        return 'deleted, and over 16 MiB'
    return None


def _head_bytes(cwd, path, run=subprocess.run):
    result = _git(cwd, 'show', f'HEAD:./{path}', run=run, text=False)  # task_git._git: SAFE pins and the repo's filters off
    return result.stdout if result.returncode == 0 else None


def _diff(path, left, right):
    import task_evidence
    if max(len(left), len(right)) > task_evidence.INLINE_LIMIT:
        # Big files: only the changed part is compared, and a binary one keeps no hex diff (it would be as big as the file).
        try:
            return task_evidence._line_diff(path, left.decode('utf-8'), right.decode('utf-8'))
        except UnicodeDecodeError:
            return (f'Binary diff not kept for a file over 256 KiB: sha256 {hashlib.sha256(left).hexdigest()} ({len(left)} bytes) '
                    f'became {hashlib.sha256(right).hexdigest()} ({len(right)} bytes).\n')
    try:
        left.decode('utf-8'); right.decode('utf-8')
    except UnicodeDecodeError:
        delta = ''.join(difflib.unified_diff([left[i:i + 16].hex(' ') + '\n' for i in range(0, len(left), 16)],
                                             [right[i:i + 16].hex(' ') + '\n' for i in range(0, len(right), 16)],
                                             fromfile='before/' + path, tofile='after/' + path))
        return 'Binary diff (hex bytes):\n' + delta if delta else ''
    lines = difflib.diff_bytes(difflib.unified_diff, left.splitlines(keepends=True), right.splitlines(keepends=True),
                               fromfile=('before/' + path).encode(), tofile=('after/' + path).encode())
    return b''.join(line if line.endswith(b'\n') else line + b'\n\\ No newline at end of file\n' for line in lines).decode('utf-8')


def named(paths, shown=3):
    """The first few paths and how many more: ".env.local, dist/app.js and 2 more"."""
    return ', '.join(paths[:shown]) + (f' and {len(paths) - shown} more' if len(paths) > shown else '')


# Beta.15 (Bryce, 23 Sep: "Dervo is turning premium and pristine"): these used to be one long sentence with the file list
# and every reason inside it, shown as the result's error, so a note that another chat shared the folder turned a
# normal result rust. Now the task carries them as data (``unrecorded``, ``alongside``), the result card lays them out,
# and the error is one short line for the places that only show text (Catch up, History, Home).
REASONS = {
    'it already had uncommitted changes when this run started': 'You had uncommitted changes in it before the run',
    'deleted': 'Deleted',
    'not a regular file': 'A link or special file, not a regular file',
    # Before 16.2 every name refusal read like this; project_access now names the rule itself.
    'Hidden, credential, database or generated project files are excluded': 'A hidden, secret or generated file',
    HELD: 'Waiting in another thread’s result. Approve that one first',
}
ALONGSIDE_SHOWN = 20


def unrecorded(problems, held=None):
    """The files Dervo couldn't record, as rows for the result card: [{path, why, count}], sorted by path. ``count`` is
    more than 1 only for the over-limit row, whose path names the first few files. 16.2: a file another thread's waiting
    result holds (``held``, {path: holder}) also carries ``task_id``, ``thread_id`` and ``thread``, so the decision bar
    can name that thread and open it."""
    rows = []
    for problem in sorted(problems):
        # The reason is the last parenthesis: a path can hold one itself ("logo (1).png", a browser's second download),
        # and splitting at the first named the wrong file and read the rest of its name as the reason.
        path, _, reason = problem.rpartition(' (')
        path, reason = (path, reason.removesuffix(')')) if _ else (problem, '')
        count = 1
        if reason.endswith(f'beyond the {CHANGE_LIMIT} one attempt records'):
            count = int(reason.split(' ', 1)[0]) if reason.split(' ', 1)[0].isdigit() else 1
            reason = f'More files than one result holds ({CHANGE_LIMIT})'
        why = REASONS.get(reason) or (reason[:1].upper() + reason[1:] if reason else 'Dervo couldn’t read it')
        row = {'path': path, 'why': why, 'count': count}
        holder = (held or {}).get(path) if reason == HELD else None
        if isinstance(holder, dict):
            row.update(task_id=holder.get('task_id'), thread_id=holder.get('thread_id'), thread=holder.get('thread'))
            if holder.get('thread') and holder['thread'] != 'another thread':
                row['why'] = f'Waiting in {holder["thread"]}’s result. Approve that one first'
        rows.append(row)
    return rows


def _files(count):
    return f'{count} file' if count == 1 else f'{count} files'


def problem_text(problems):
    """Some changes were recorded and these weren't: the result is reviewable, approval isn't. One line; the rows are
    ``unrecorded`` on the task."""
    count = sum(row['count'] for row in unrecorded(problems))
    return f'Dervo couldn’t record {_files(count)} from this run, so it can’t be approved yet.'


def blocked_text(problems):
    """Nothing this run left could be recorded (#9, failure B: a build folder, an .env.local, a link). One line that
    still names the first files, for surfaces that show only text; the result card lists every row with its reason."""
    rows = unrecorded(problems)
    count = sum(row['count'] for row in rows)
    return f'Dervo couldn’t record {_files(count)} this run left ({named([row["path"] for row in rows], 2)}), so there’s nothing to approve.'


NO_BASELINE = 'This run has no git starting point recorded, so Dervo can’t say what it changed.'


def recording_inputs(coding, task):
    """What ``prepare`` needs from the records, read on the request thread: (cwd, base, own, mine, theirs, tried)."""
    last = task['runs'][-1]
    own = set(last.get('written') or []) if last.get('shared_folder') is True else None
    mine, theirs = waiting_left(coding, task)
    tried = last.get('git_tried') if isinstance(last.get('git_tried'), list) else None
    return task['binding']['cwd'], last.get('baseline'), own, mine, theirs, tried


def prepare(cwd, base, own, mine, theirs, tried, run=subprocess.run):
    """The git part of recording a finished run: what changed (``changes``), what it leaves and each file's HEAD version.
    Touches no record, so it can run on a thread (beta.18, step 5: a slow git on a busy Mac kept the host from answering,
    and the bridge killed it). {'error'} when it can't, the way ``finish`` would have raised."""
    if not isinstance(base, dict):
        return {'error': NO_BASELINE}
    detail = {}
    try:
        changed, problems, others = changes(cwd, base, run=run, own=own, earlier=mine, held=theirs, detail=detail, tried=tried)
        files = (changed + detail.get('deleted', []))[:CHANGE_LIMIT]  # what finish keeps (the rest is named as a problem)
        heads = {path: _head_bytes(cwd, path, run=run) for path in files}
        left = left_prints(cwd, files)
    except Rejected as exc:
        return {'error': str(exc)}
    return {'changed': changed, 'problems': problems, 'others': others, 'detail': detail, 'heads': heads, 'left': left}


def finish(coding, task, run=subprocess.run, prepared=None):
    """After a completed full-access run: record the changes as one reviewable attempt, or answer when nothing changed.
    Updates ``task`` in place (state and error); the caller saves it. ``prepared`` is ``prepare``'s answer when the git
    part already ran on a thread (coding_integration.settle_recordings); without it, it runs here."""
    ctx, cwd = task['context'], task['binding']['cwd']
    if prepared is None:
        prepared = prepare(*recording_inputs(coding, task), run=run)
    if prepared.get('error'):
        raise Rejected(prepared['error'])
    shared = task['runs'][-1].get('shared_folder') is True
    changed, problems, others = list(prepared['changed']), list(prepared['problems']), list(prepared['others'])
    detail = prepared['detail']
    heads = prepared.get('heads') or {}
    # Beta.18 (record-b): a commit from outside the run, and files the run wrote outside the project, said on the card.
    if detail.get('moved_head'):
        task['moved_head'] = detail['moved_head']
    else:
        task.pop('moved_head', None)
    outside = outside_files(task)
    if outside:
        task['outside'] = outside
    else:
        task.pop('outside', None)
    deleted, inherited, held = detail.get('deleted', []), detail.get('inherited', {}), detail.get('held', {})
    evidence = coding.evidence.read(task['id'], **ctx)
    if len(changed) + len(deleted) > CHANGE_LIMIT:
        beyond = (changed + deleted)[CHANGE_LIMIT:]
        problems.append(f'{named(beyond)} ({len(beyond)} files beyond the {CHANGE_LIMIT} one attempt records)')
        changed, deleted = changed[:CHANGE_LIMIT], deleted[:max(0, CHANGE_LIMIT - len(changed))]
    # What this run leaves, so a later run starting from exactly this records it instead of calling it your edit.
    left = prepared.get('left') or {}
    task['runs'][-1]['left'] = {path: left[path] for path in sorted(changed + deleted)[:LEFT_LIMIT] if path in left}
    task['runs'][-1]['left_at'] = time.time()
    recorded = []
    for path in changed:
        try:
            coding.evidence._admit(evidence, path)
            recorded.append(path)
        except Rejected as exc:
            if path in coding.evidence._scoped(evidence):
                recorded.append(path)
            else:
                problems.append(f'{path} ({str(exc).rstrip(".")})')
    # Another chat's edits aren't a problem with this result: data for a quiet line on the card, never the error.
    if shared and others:
        task['alongside'] = {'count': len(others), 'files': others[:ALONGSIDE_SHOWN]}
    else:
        task.pop('alongside', None)
    if problems:
        task['unrecorded'] = unrecorded(problems, held)
    else:
        task.pop('unrecorded', None)
    # Beta.15: files this result took over from an earlier unapproved run, for a quiet line on the card.
    carried_over = {path: source for path, source in inherited.items() if path in recorded or path in deleted}
    if carried_over:
        task['inherited'] = [{'path': path, 'task_id': source} for path, source in sorted(carried_over.items())]
    else:
        task.pop('inherited', None)
    if not recorded and not deleted and not problems:
        task.update(state='answered', error=None)
        return None
    if not recorded and not deleted:
        task.update(state='blocked', error=blocked_text(problems))
        return None
    attempt = coding.evidence.verify_current(task['id'], **ctx)
    evidence = coding.evidence.read(task['id'], **ctx)
    final = evidence['attempts'][-1]
    before, diffs, removed = dict(final['before']), {}, {}
    for path in recorded:
        head = heads[path] if path in heads else _head_bytes(cwd, path, run=run)
        # A file over 256 KiB keeps its HEAD version beside the database, like any other big version (task_evidence).
        before[path] = None if head is None else coding.evidence._record(head)
        diffs[path] = _diff(path, head or b'', coding.evidence.snapshot_bytes(final['after'][path]))
    for path in deleted:
        # A deletion: its committed version is the "before", and it has no "after" (kept out of ``after``, which every
        # reader takes as a file's bytes). Approving commits the removal; Undo after that restores it from git.
        head = (heads[path] if path in heads else _head_bytes(cwd, path, run=run)) or b''
        before[path] = removed[path] = coding.evidence._record(head)
        diffs[path] = _diff(path, head, b'') or f'Deleted {path} ({len(head)} bytes).\n'
    final.update(kind='manual-review', state='unverified', test=None, checks='not_run', before=before, diff=diffs,
                 native=True, unrecorded=problems, **({'deleted': removed} if removed else {}))
    coding.evidence._save(evidence)
    task['acceptance'] = {'required': True, 'source': 'full-access'}
    task.update(state='ready_for_review', error=problem_text(problems) if problems else None)
    return attempt['number']


SETTLED_WITH = 'with_later'


def settle_carried(coding, task):
    """16.2 (Bryce, 27 Sep): approving a follow-up that built on this thread's earlier unapproved result approves both.
    Once ``task`` is committed, each earlier task it took files over from (``inherited``) whose leftovers all went into
    that commit is marked approved and committed by it (``commit.how`` is 'with_later', ``with_task_id`` names the
    newer task), so it stops waiting for a decision. One that still has files of its own outside the commit keeps
    waiting. Answers with the task ids settled; never raises, since the approval already stands."""
    commit = task.get('commit') if isinstance(task.get('commit'), dict) else {}
    if not commit.get('sha') or commit.get('reverted'):
        return []
    files = set(commit.get('files') or [])
    thread = (task.get('context') or {}).get('thread_id')
    sources = sorted({row.get('task_id') for row in task.get('inherited') or [] if isinstance(row, dict)} - {task.get('id'), None})
    settled = []
    for source in sources:
        try:
            row = coding.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (source,)).fetchone()
            other = json.loads(row[0]) if row else None
            if not isinstance(other, dict) or (other.get('context') or {}).get('thread_id') != thread:
                continue
            if isinstance(other.get('commit'), dict) and other['commit'].get('sha') and not other['commit'].get('reverted'):
                continue
            past = next((run for run in reversed(other.get('runs') or []) if isinstance(run, dict) and isinstance(run.get('left'), dict)), None)
            left = set((past or {}).get('left') or {})
            if not left or not left <= files:
                continue
            other['commit'] = {'sha': commit['sha'], 'branch': commit.get('branch'), 'files': sorted(left), 'at': commit.get('at'),
                               'how': SETTLED_WITH, 'with_task_id': task['id'], 'reverted': None}
            other.update(state='idle', review='approved', error=None)
            other.pop('unrecorded', None)
            coding.save(other)
            settled.append(source)
        except Exception as exc:  # noqa: BLE001 - the newer result is committed whatever happens here
            print(f'An earlier result could not be marked committed with task {task.get("id")}: {type(exc).__name__}', file=sys.stderr)
    return settled


def shared_note(others):
    """One line for text-only surfaces: another chat changed files in this folder during the run. Informational, never
    blocking, and never the task's error; the card reads ``alongside``."""
    if not others:
        return None
    return f'Another chat changed {_files(len(others))} in this folder during the run. This result has only its own.'


def unfinished_note(task, run=subprocess.run, *, earlier=None):
    """When a full-access run stops or fails, say which files it left changed. Never raises. ``earlier`` as in changes."""
    base = task['runs'][-1].get('baseline') if task.get('runs') else None
    if not isinstance(base, dict):
        return None
    try:
        own = set(task['runs'][-1].get('written') or []) if task['runs'][-1].get('shared_folder') is True else None
        detail = {}
        tried = task['runs'][-1].get('git_tried') if isinstance(task['runs'][-1].get('git_tried'), list) else None
        changed, problems, _others = changes(task['binding']['cwd'], base, run=run, own=own, earlier=earlier, detail=detail, tried=tried)
    except Rejected as exc:
        return str(exc)
    # Beta.15: a stopped run's leftovers are Dervo's too, so the next run records them rather than calling them yours.
    task['runs'][-1]['left'] = left_prints(task['binding']['cwd'], changed + detail.get('deleted', []))
    task['runs'][-1]['left_at'] = time.time()
    touched = changed + detail.get('deleted', []) + [p.split(' (')[0] for p in problems]
    if not touched:
        return None
    base = [path.rsplit('/', 1)[-1] for path in touched]
    names = ', '.join(base[:3]) + (f' and {len(base) - 3} more' if len(base) > 3 else '')
    return f'It had changed {names}. Those edits are in your project folder, not committed.'
