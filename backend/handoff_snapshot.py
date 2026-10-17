"""A snapshot of the project folder, taken before another agent carries on from uncommitted work, and the restore that
puts it back.

The 23 Sep review, bet 3: "Continue in Codex" refused when Claude stopped with changed files, and that's the usual state
at a usage limit. Now the next agent carries that work on, and this module is what keeps it safe: before the next agent
starts, Dervo records the folder exactly as the stopped run left it, and a restore puts those files back.

What it writes, and nothing else:

* objects in the repository's own object store: blobs for the changed files, two trees and two commits shaped like
  ``git stash create``'s result (the snapshot commit's parents are HEAD and a commit of the index);
* one ref, ``refs/dervo/handoff/<task id>`` (``refs/dervo/restore/...`` for the copy taken before a restore), so
  ``git gc`` keeps those objects;
* copies of untracked files, which a stash leaves out, in Dervo's own data folder, named by their sha256.

When (23 Sep, follow-up): only when you press Send, and only if the folder is still what the confirm read. Opening the
confirm runs ``capture`` without writing: ``hash-object`` without ``-w``, a status with optional locks off, nothing into
the object store, the refs or Dervo's folder, so a Cancel leaves the repository exactly as it was. How long:
until the task that carried on is approved, rejected or undone, plus a week; then ``forget`` deletes the ref, only
while it still points at the commit Dervo recorded, and Dervo's copies go too (ConversationCoding.sweep_handoffs).
The objects the ref kept are left for git's own gc.

What it never does: ``git stash`` (push, pop or apply), ``reset``, ``checkout``, ``clean``, ``add`` to the real index,
or anything else that changes the index, HEAD, the stash list or a file in the folder. ``git stash create`` is out too:
it refreshes and rewrites the real index (checked against git 2.39.5, Apple Git-154). Everything that needs an index
works on a copy in Dervo's data folder through GIT_INDEX_FILE. Every git call goes through task_git: hooks, fsmonitor,
external diff, pagers and the repository's own filter drivers are off.

Changed and untracked files are stored as their bytes on disk (``hash-object --no-filters``), so a restore writes back
exactly those bytes. A file that was unchanged at the snapshot is written back as a checkout would write it. Ignored
files, submodules, nested repositories and anything outside the project folder aren't in the snapshot.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

from dervo_connections import Rejected
import platform_os
import task_git

REF_ROOT = 'refs/dervo/handoff/'
RESTORE_REF_ROOT = 'refs/dervo/restore/'
KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}')
CHANGED_LIMIT = 2000               # tracked files the snapshot hashes from disk
UNTRACKED_COUNT_LIMIT = 2000       # untracked files it copies
UNTRACKED_FILE_LIMIT = 64 << 20    # bytes, one untracked file
UNTRACKED_TOTAL_LIMIT = 256 << 20  # bytes, all untracked files together
FILE_DIFF_LIMIT = 4000             # characters of diff one file gets in a brief
DIFF_LIMIT = 12000                 # characters of diff a brief holds in all
TEXT_LIMIT = 1 << 20               # bytes; a bigger file's diff is only counted
IDENTITY = {'GIT_AUTHOR_NAME': 'Dervo', 'GIT_AUTHOR_EMAIL': 'dervo@localhost',
            'GIT_COMMITTER_NAME': 'Dervo', 'GIT_COMMITTER_EMAIL': 'dervo@localhost'}
# Writes to a temporary index stay plain: no split index or untracked cache files next to the repository's own index.
TEMP_INDEX = ('-c', 'core.splitIndex=false', '-c', 'core.untrackedCache=false')

NOT_A_REPO = 'This folder isn’t in a git repository, so Dervo can’t save a snapshot of it.'
MERGING = 'A merge in this repository isn’t finished, so Dervo can’t save a snapshot. Finish or abort the merge first.'
GIT_FAILED = 'Git couldn’t read the project folder, so Dervo didn’t save a snapshot.'
MISSING = 'The snapshot isn’t in this repository any more, so Dervo can’t restore it.'


def _named(paths, shown=3):
    return ', '.join(paths[:shown]) + (f' and {len(paths) - shown} more' if len(paths) > shown else '')


class Git:
    """Git in one project folder, through task_git's hardened call. The repository's filter guards are read once."""

    def __init__(self, cwd, run=subprocess.run):
        self.cwd, self.run = str(cwd), run
        try:
            self.guards = task_git.filter_guards(self.cwd, run=run)
        except (OSError, subprocess.SubprocessError):
            raise Rejected(GIT_FAILED) from None

    def __call__(self, *args, index=None, stdin=None, text=False, identity=False, check=None):
        env = {'GIT_LITERAL_PATHSPECS': '1'}
        if index is not None:
            env['GIT_INDEX_FILE'] = str(index)
            args = TEMP_INDEX + args
        if identity:
            env.update(IDENTITY)
        try:
            result = task_git._raw(self.cwd, *self.guards, *args, run=self.run, text=text, env=env, stdin=stdin)
        except (OSError, subprocess.SubprocessError):
            raise Rejected(GIT_FAILED) from None
        if check is not None and result.returncode != 0:
            raise Rejected(check)
        return result

    def out(self, *args, **kwargs):
        return self(*args, check=kwargs.pop('check', GIT_FAILED), **kwargs).stdout


def locate(cwd, run=subprocess.run):
    """(Git, repository top, the project folder's prefix inside it, the index file) or None when ``cwd`` isn't in a
    work tree. The prefix is '' at the top, else ends with '/'."""
    git = Git(cwd, run=run)  # a filter Dervo can't switch off is refused here, in its own words
    found = git('rev-parse', '--show-toplevel', '--show-prefix', '--git-path', 'index', text=True)
    if found.returncode != 0:
        return None
    lines = found.stdout.split('\n')
    if len(lines) < 3 or not lines[0]:
        return None
    index = lines[2]
    return git, lines[0], lines[1], Path(index if os.path.isabs(index) else os.path.join(cwd, index))


def _entries(raw):
    """Parse NUL-separated ``<mode> <type or sha> ...\\t<path>`` records (ls-files -s, ls-tree) into {path: (mode, sha)}.
    Unmerged index entries (stage 1 to 3) are kept under their stage so a conflict is seen."""
    found = {}
    for record in raw.split(b'\0'):
        if not record:
            continue
        meta, _, path = record.partition(b'\t')
        fields = meta.split()
        name = path.decode('utf-8', 'surrogateescape')
        if len(fields) == 3 and fields[1] in (b'blob', b'tree', b'commit'):   # ls-tree: mode type sha
            found[name] = (fields[0].decode(), fields[2].decode())
        elif len(fields) == 3:                                               # ls-files -s: mode sha stage
            stage = fields[2].decode()
            found[name if stage == '0' else f'{name}\0{stage}'] = (fields[0].decode(), fields[1].decode())
    return found


def status(git, prefix):
    """{project-relative path: two-letter code} for every changed or untracked path under the project folder, and the
    untracked directories git didn't open (nested repositories). Paths git reports with a line break are refused."""
    raw = git.out('status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames', '--ignore-submodules=all', '--', '.')
    codes, nested = {}, []
    for entry in raw.split(b'\0'):
        if len(entry) < 4:
            continue
        code, path = entry[:2].decode(), entry[3:].decode('utf-8', 'surrogateescape')
        if prefix and not path.startswith(prefix):
            continue
        path = path[len(prefix):]
        if 'U' in code or code in ('AA', 'DD'):
            raise Rejected(MERGING)
        if '\n' in path or '\r' in path:
            raise Rejected(f'A file name here has a line break in it ({path.splitlines()[0]}…), so Dervo can’t save a snapshot.')
        if code == '??' and path.endswith('/'):
            nested.append(path)  # a folder git didn't list inside: its own repository
            continue
        codes[path] = code
    return codes, nested


def _lstat(root, path=''):
    """lstat of ``root/path`` (or ``root`` itself), None when it isn't there. Never follows a link, even a folder's."""
    try:
        return os.lstat(os.path.join(root, path) if path else root)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _sha256_file(path, limit=None):
    digest, size = hashlib.sha256(), 0
    with open(path, 'rb', buffering=0) as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b''):
            digest.update(chunk)
            size += len(chunk)
            if limit is not None and size > limit:
                return None, size
    return digest.hexdigest(), size


def _store(folder):
    path = Path(folder) / 'files'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _keep(folder, source):
    """Copy ``source`` into the content store, named by the sha256 of the bytes copied. Returns (sha256, size)."""
    store = _store(folder)
    temp = store / f'.incoming-{uuid.uuid4().hex}'
    digest, size = hashlib.sha256(), 0
    try:
        with open(source, 'rb', buffering=0) as reading, open(temp, 'xb') as writing:
            for chunk in iter(lambda: reading.read(1 << 16), b''):
                digest.update(chunk)
                size += len(chunk)
                if size > UNTRACKED_FILE_LIMIT:
                    raise Rejected(f'{source.name} is over {UNTRACKED_FILE_LIMIT >> 20} MB, so Dervo can’t copy it into a snapshot.')
                writing.write(chunk)
            writing.flush()
            os.fsync(writing.fileno())
        name = digest.hexdigest()
        target = store / name
        if target.exists():
            temp.unlink()
        else:
            os.replace(temp, target)
            os.chmod(target, 0o600)
        return name, size
    finally:
        if temp.exists():
            temp.unlink()


def kept(folder, sha):
    """The bytes the content store holds for ``sha``, checked, or None."""
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        return None
    path = Path(folder) / 'files' / sha
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if hashlib.sha256(data).hexdigest() == sha else None


def capture(cwd, folder, *, write, run=subprocess.run):
    """The folder's state now. ``identity`` is a hash of HEAD, the index and every changed and untracked file's bytes:
    two captures with the same identity saw the same folder. With ``write`` the changed files go into the object store,
    both trees are built, and untracked files are copied into ``folder``, ready for save(). Without it nothing is
    written anywhere."""
    found = locate(cwd, run=run)
    if found is None:
        raise Rejected(NOT_A_REPO)
    git, top, prefix, index = found
    head = git('rev-parse', '--verify', '-q', 'HEAD^{commit}', text=True)
    head = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else None
    listing = git.out('ls-files', '-s', '-z')
    if any('\0' in path for path in _entries(listing)):
        raise Rejected(MERGING)
    codes, nested = status(git, prefix)
    tracked = {path: code for path, code in codes.items() if code != '??'}
    untracked = sorted(path for path, code in codes.items() if code == '??')
    if len(tracked) > CHANGED_LIMIT:
        raise Rejected(f'More than {CHANGED_LIMIT} files here have uncommitted changes, so Dervo can’t save a snapshot. Commit some of them first.')
    if len(untracked) > UNTRACKED_COUNT_LIMIT:
        raise Rejected(f'More than {UNTRACKED_COUNT_LIMIT} untracked files are here, so Dervo can’t copy them into a snapshot. '
                       'Add the ones you don’t need to .gitignore first.')
    # Tracked changes, as the bytes on disk. Deleted, and replaced by a folder, both leave the tree.
    entries, files, perms = {}, [], {}
    for path in sorted(tracked):
        info = _lstat(cwd, path)
        if info is None or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
            entries[path] = None
        elif stat.S_ISLNK(info.st_mode):
            target = os.fsencode(os.readlink(os.path.join(cwd, path)))
            sha = git.out('hash-object', *(['-w'] if write else []), '--no-filters', '--stdin', stdin=target).decode().strip()
            entries[path] = ('120000', sha)
        else:
            files.append(path)
            perms[path] = stat.S_IMODE(info.st_mode)
            entries[path] = ('100755' if info.st_mode & 0o100 else '100644', None)
    if files:
        shas = git.out('hash-object', *(['-w'] if write else []), '--no-filters', '--stdin-paths',
                       stdin=''.join(os.path.join(os.path.abspath(cwd), path) + '\n' for path in files).encode('utf-8', 'surrogateescape'))
        shas = shas.decode().split()
        if len(shas) != len(files):
            raise Rejected(GIT_FAILED)
        for path, sha in zip(files, shas):
            entries[path] = (entries[path][0], sha)
    # Untracked files: hashed, and copied into Dervo's folder when writing. Links keep their target; anything else waits.
    manifest, total = {}, 0
    for path in untracked:
        info = _lstat(cwd, path)
        if info is None:
            continue
        if stat.S_ISLNK(info.st_mode):
            manifest[path] = dict(kind='link', target=os.readlink(os.path.join(cwd, path)))
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        total += info.st_size
        if info.st_size > UNTRACKED_FILE_LIMIT or total > UNTRACKED_TOTAL_LIMIT:
            raise Rejected(f'{path} is too big for Dervo to copy into a snapshot ({info.st_size >> 20} MB). '
                           'Add it to .gitignore or move it, then try again.')
        if write:
            sha, size = _keep(folder, Path(cwd) / path)
        else:
            sha, size = _sha256_file(os.path.join(cwd, path), limit=UNTRACKED_FILE_LIMIT)
        manifest[path] = dict(kind='file', sha256=sha, size=size, mode=stat.S_IMODE(info.st_mode))
    identity = hashlib.sha256(json.dumps([head, hashlib.sha256(listing).hexdigest(), sorted((p, e) for p, e in entries.items() if e),
                                          sorted(p for p, e in entries.items() if e is None),
                                          sorted((p, m.get('sha256') or m.get('target')) for p, m in manifest.items())]).encode()).hexdigest()
    state = dict(top=top, prefix=prefix, head=head, identity=identity, tracked=tracked, raw=sorted(p for p, e in entries.items() if e),
                 perms=perms, untracked=manifest, nested=nested)
    if not write:
        return state
    # Both trees from a copy of the index: the real one is only ever read.
    Path(folder).mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = Path(folder) / f'index-{uuid.uuid4().hex}'
    try:
        if index.is_file():
            shutil.copyfile(index, temp)
        state['index_tree'] = git.out('write-tree', index=temp, check=MERGING).decode().strip()
        if entries:
            info = b''.join(f"{'0' if e is None else e[0]} {'0' * 40 if e is None else e[1]}\t".encode()
                            + (prefix + path).encode('utf-8', 'surrogateescape') + b'\0' for path, e in entries.items())
            git.out('update-index', '-z', '--index-info', index=temp, stdin=info)
        state['tree'] = git.out('write-tree', index=temp).decode().strip()
    finally:
        for leftover in (temp, temp.with_name(temp.name + '.lock')):
            if leftover.exists():
                leftover.unlink()
    return state


def save(cwd, state, key, *, ref_root=REF_ROOT, run=subprocess.run):
    """Commit a written capture and point the task's ref at it. Returns the record to keep."""
    if not KEY.fullmatch(key or ''):
        raise Rejected('A snapshot needs a plain task id.')
    git = Git(cwd, run=run)
    parents = ['-p', state['head']] if state['head'] else []
    index_commit = git.out('commit-tree', state['index_tree'], *parents, '-m', f'index on Dervo snapshot {key}',
                           identity=True, text=True).strip()
    commit = git.out('commit-tree', state['tree'], *parents, '-p', index_commit, '-m',
                     f'Dervo saved this before another agent carried on ({key})', identity=True, text=True).strip()
    ref = ref_root + key
    git.out('update-ref', ref, commit, text=True, check='Git couldn’t record the snapshot, so nothing was saved.')
    return dict(ref=ref, commit=commit, index_commit=index_commit, created_at=int(time.time()),
                **{k: state[k] for k in ('top', 'prefix', 'head', 'identity', 'tracked', 'raw', 'perms', 'untracked', 'nested', 'index_tree', 'tree')})


def snapshot(cwd, folder, key, *, ref_root=REF_ROOT, run=subprocess.run):
    """Capture and save in one step."""
    return save(cwd, capture(cwd, folder, write=True, run=run), key, ref_root=ref_root, run=run)


def forget(cwd, ref, commit, run=subprocess.run):
    """Delete one of Dervo's own refs, and only while it still points at ``commit``: True once it's gone (or was never
    there), False when it points somewhere else now and was left alone. Only a name under ``refs/dervo/handoff/`` or
    ``refs/dervo/restore/`` is ever touched; the old value goes to ``update-ref -d`` so git checks it atomically.
    Nothing else changes: no other ref, the stash, the index, HEAD or a file in the folder. The objects the ref kept
    become unreachable and git's own gc removes them in its own time."""
    if not isinstance(ref, str) or not any(ref.startswith(root) and KEY.fullmatch(ref[len(root):]) for root in (REF_ROOT, RESTORE_REF_ROOT)):
        raise Rejected('Dervo only removes its own snapshot refs.')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise Rejected('A snapshot ref is removed only with the commit Dervo recorded for it.')
    git = Git(cwd, run=run)
    now = git('rev-parse', '--verify', '-q', ref, text=True)
    if now.returncode != 0 or not now.stdout.strip():
        return True
    if now.stdout.strip() != commit:
        return False
    git.out('update-ref', '-d', ref, commit, text=True, check='Git couldn’t remove Dervo’s snapshot ref.')
    return True


def identity(cwd, folder, run=subprocess.run):
    """What the folder is now, as capture() hashes it, writing nothing."""
    return capture(cwd, folder, write=False, run=run)['identity']


# ------------------------------------------------------------------ reading a snapshot back
def _blobs(git, shas):
    """{sha: bytes} for blobs, in one ``cat-file --batch``. A missing one is left out."""
    shas = sorted(set(shas))
    if not shas:
        return {}
    raw = git.out('cat-file', '--batch', stdin=''.join(f'{sha}\n' for sha in shas).encode())
    found, at = {}, 0
    for sha in shas:
        end = raw.index(b'\n', at)
        header = raw[at:end].split()
        at = end + 1
        if len(header) < 3:
            continue
        size = int(header[2])
        found[sha] = raw[at:at + size]
        at += size + 1
    return found


def _filemode(git):
    value = git('config', '--type=bool', '--get', 'core.fileMode', text=True)
    return not (value.returncode == 0 and value.stdout.strip() == 'false')


def current(cwd, path):
    """What a project-relative path is on disk now: None, ('link', target), ('file', sha256, executable, bytes) or
    ('other',). A path under a linked folder reads as other: a restore never writes through a link."""
    parts = path.split('/')
    here = str(cwd)
    for part in parts[:-1]:
        here = os.path.join(here, part)
        info = _lstat(here)
        if info is None:
            return None
        if not stat.S_ISDIR(info.st_mode):
            return ('other',)
    info = _lstat(cwd, path)
    if info is None:
        return None
    full = os.path.join(cwd, path)
    if stat.S_ISLNK(info.st_mode):
        return ('link', os.readlink(full))
    if stat.S_ISREG(info.st_mode):
        data = Path(full).read_bytes()
        return ('file', hashlib.sha256(data).hexdigest(), bool(info.st_mode & 0o100), data)
    return ('other',)


class Snapshot:
    """A saved snapshot read back: what each project-relative path held, and the bytes to write it again."""

    def __init__(self, cwd, record, folder, run=subprocess.run):
        found = locate(cwd, run=run)
        if found is None:
            raise Rejected(NOT_A_REPO)
        self.git, _top, self.prefix, _index = found
        self.cwd, self.record, self.folder = str(cwd), record, folder
        if self.prefix != record.get('prefix') or self.git('cat-file', '-e', f"{record['commit']}^{{commit}}").returncode != 0:
            raise Rejected(MISSING)
        self.tree = {p: e for p, e in _entries(self.git.out('ls-tree', '-r', '-z', record['tree'])).items() if e[0] != '160000'}
        self.filemode = _filemode(self.git)
        self._bytes = {}

    def expected(self, path):
        """None (absent), ('link', target) or ('file', sha256, executable, bytes, permissions or None)."""
        untracked = self.record['untracked'].get(path)
        if untracked is not None:
            if untracked['kind'] == 'link':
                return ('link', untracked['target'])
            data = kept(self.folder, untracked['sha256'])
            if data is None:
                raise Rejected(f'Dervo’s copy of {path} is missing or damaged, so it can’t restore that file.')
            return ('file', untracked['sha256'], bool(untracked['mode'] & 0o100), data, untracked['mode'])
        entry = self.tree.get(path)
        if entry is None:
            return None
        mode, sha = entry
        if mode == '120000' or path in self.record['raw']:
            data = self._bytes.get(sha)
            if data is None:
                data = _blobs(self.git, [sha]).get(sha)
                if data is None:
                    raise Rejected(MISSING)
        else:
            # Unchanged at the snapshot: what a checkout of it writes, line endings and all.
            data = self.git.out('cat-file', '--filters', f"{self.record['commit']}:{self.prefix}{path}", check=MISSING)
        if mode == '120000':
            return ('link', os.fsdecode(data))
        return ('file', hashlib.sha256(data).hexdigest(), mode == '100755', data, self.record['perms'].get(path))

    def same(self, now, then):
        if now is None or then is None:
            return now is None and then is None
        if now[0] != then[0]:
            return False
        if now[0] == 'link':
            return now[1] == then[1]
        return now[1] == then[1] and (not self.filemode or now[2] == then[2])

    def committed(self, pairs):
        """The paths, of (path, what current() read) pairs, whose bytes on disk are exactly what HEAD holds now: a
        restore that overwrites or removes one loses nothing git doesn't already keep."""
        head = self.git('rev-parse', '--verify', '-q', 'HEAD^{commit}', text=True)
        if head.returncode != 0 or not pairs:
            return set()
        entries = _entries(self.git.out('ls-tree', '-r', '-z', head.stdout.strip()))
        found = set()
        for path, now in pairs:
            entry = entries.get(path)
            if entry is None or now[0] not in ('file', 'link') or (now[0] == 'link') != (entry[0] == '120000'):
                continue
            data = now[3] if now[0] == 'file' else os.fsencode(now[1])
            digest = hashlib.sha256 if len(entry[1]) == 64 else hashlib.sha1
            if digest(b'blob %d\0' % len(data) + data).hexdigest() == entry[1]:
                found.add(path)
        return found

    def differences(self):
        """[(path, now, then)] for every project-relative path whose state on disk isn't the snapshot's, sorted."""
        index_now = _entries(self.git.out('ls-files', '-s', '-z'))
        codes, nested = status(self.git, self.prefix)
        skipped = [n for n in list(self.record.get('nested') or []) + nested]
        candidates = set(self.record['tracked']) | set(self.record['untracked']) | set(codes)
        candidates |= {path.split('\0')[0] for path in set(self.tree) | set(index_now)
                       if self.tree.get(path.split('\0')[0]) != index_now.get(path)}
        # The raw blobs in one read.
        raw = [self.tree[p][1] for p in candidates if p in self.tree and (p in self.record['raw'] or self.tree[p][0] == '120000')]
        self._bytes.update(_blobs(self.git, raw))
        out = []
        for path in sorted(candidates):
            if any(path == n.rstrip('/') or path.startswith(n) for n in skipped) or index_now.get(path, ('',))[0] == '160000':
                continue
            now, then = current(self.cwd, path), self.expected(path)
            if not self.same(now, then):
                out.append((path, now, then))
        return out


# ------------------------------------------------------------------ restore
def _open_dir(root_fd, parts, create):
    """A descriptor for the folder ``parts`` under ``root_fd``, walked without following a link. None when a part is
    missing and ``create`` is false."""
    fd = os.dup(root_fd)
    try:
        for part in parts:
            try:
                next_fd = platform_os.open_dir_at(part, fd)
            except FileNotFoundError:
                if not create:
                    os.close(fd)
                    return None
                platform_os.mkdir_at(part, fd, 0o755)
                next_fd = platform_os.open_dir_at(part, fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError:
        os.close(fd)
        raise


def unsafe(cwd, paths):
    """Paths a restore can't write safely: one whose folder is a link or a file, or that is a folder itself now."""
    out = []
    for path in paths:
        parts = path.split('/')
        if any(p in ('', '.', '..', '.git') for p in parts):
            out.append(path)
            continue
        here = str(cwd)
        for part in parts[:-1]:
            here = os.path.join(here, part)
            info = _lstat(here)
            if info is None:
                break
            if not stat.S_ISDIR(info.st_mode):
                out.append(path)
                break
        else:
            info = _lstat(cwd, path)
            if info is not None and stat.S_ISDIR(info.st_mode):
                out.append(path)
    return out


def restore(cwd, snap, paths):
    """Put exactly these project-relative paths back as ``snap`` holds them: write the snapshot's bytes and mode, make
    its links, remove what it didn't have. Nothing is written until every path is checked. Returns (written, removed)."""
    blocked = unsafe(cwd, paths)
    if blocked:
        raise Rejected(f'Dervo can’t restore {_named(blocked)} safely: a folder on the way is a link or a file, or the path '
                       'is a folder now. Nothing was changed.')
    plan = [(path, snap.expected(path)) for path in sorted(paths)]
    root = platform_os.open_dir(cwd, follow=True)
    written, removed = [], []
    try:
        for path, then in plan:
            parts = path.split('/')
            if then is None:
                fd = _open_dir(root, parts[:-1], create=False)
                if fd is None:
                    continue
                try:
                    try:
                        platform_os.unlink_at(parts[-1], fd)
                        removed.append(path)
                    except FileNotFoundError:
                        pass
                finally:
                    os.close(fd)
                _prune(root, parts[:-1], snap)
                continue
            fd = _open_dir(root, parts[:-1], create=True)
            temp = f'.dervo-restore-{uuid.uuid4().hex}'
            try:
                if then[0] == 'link':
                    platform_os.symlink_at(then[1], temp, fd)
                else:
                    try:
                        was = platform_os.stat_at(parts[-1], fd)
                        keep = stat.S_IMODE(was.st_mode) if stat.S_ISREG(was.st_mode) else None
                    except FileNotFoundError:
                        keep = None
                    mode = then[4] if then[4] is not None else ((keep or 0o644) | 0o111 if then[2] else (keep or 0o644) & ~0o111)
                    out = platform_os.open_file_at(temp, fd, 'new', 0o600)
                    try:
                        data = memoryview(then[3])
                        while data:
                            data = data[os.write(out, data):]
                        os.fchmod(out, mode)
                        os.fsync(out)
                    finally:
                        os.close(out)
                platform_os.replace_at(temp, parts[-1], fd)
                written.append(path)
            finally:
                try:
                    platform_os.unlink_at(temp, fd)
                except FileNotFoundError:
                    pass
                os.close(fd)
    finally:
        os.close(root)
    return written, removed


def _prune(root, parts, snap):
    """Remove folders a removed file leaves empty, when the snapshot holds nothing under them."""
    while parts:
        folder = '/'.join(parts) + '/'
        if any(p.startswith(folder) for p in list(snap.tree) + list(snap.record['untracked'])):
            return
        fd = _open_dir(root, parts[:-1], create=False)
        if fd is None:
            return
        try:
            platform_os.rmdir_at(parts[-1], fd)
        except OSError:
            return
        finally:
            os.close(fd)
        parts = parts[:-1]


# ------------------------------------------------------------------ what a brief shows
def _lines(data):
    try:
        return data.decode('utf-8').splitlines(keepends=True)
    except UnicodeDecodeError:
        return None


def changes(cwd, record, folder, paths, run=subprocess.run):
    """How each project-relative path differs between HEAD and the saved snapshot: [{path, change, added, removed,
    binary, diff}]. ``diff`` is a unified diff of at most FILE_DIFF_LIMIT characters, with the cut said. Nothing here
    runs a diff driver: both sides are read as bytes and compared in Python."""
    snap = Snapshot(cwd, record, folder, run=run)
    return _changes(snap.git, record.get('head'), paths, snap.expected)


def changes_on_disk(cwd, state, paths, run=subprocess.run):
    """changes() for a capture that wasn't written (the confirm's): each path as it is on disk now against HEAD. The
    confirm keeps the capture's identity, and Send takes the snapshot only while the folder still has it, so these are
    the bytes Send saves. Reads only."""
    found = locate(cwd, run=run)
    if found is None:
        raise Rejected(NOT_A_REPO)

    def now(path):
        seen = current(cwd, path)
        if seen is None or seen[0] == 'other':
            return None
        return ('link', seen[1]) if seen[0] == 'link' else ('file', seen[1], seen[2], seen[3], None)
    return _changes(found[0], state.get('head'), paths, now)


def _changes(git, head_commit, paths, after_of):
    import difflib
    head = {p: e for p, e in _entries(git.out('ls-tree', '-r', '-z', head_commit)).items()} if head_commit else {}
    before_bytes = _blobs(git, [head[p][1] for p in paths if p in head and head[p][0] != '160000'])
    out = []
    for path in paths:
        then = after_of(path)
        entry = head.get(path)
        before = before_bytes.get(entry[1]) if entry else None
        after = then[3] if then and then[0] == 'file' else (then[1].encode() if then else None)
        change = 'new' if before is None else 'deleted' if after is None else 'changed'
        left, right = (before or b''), (after or b'')
        a, b = (_lines(left) if len(left) <= TEXT_LIMIT else None), (_lines(right) if len(right) <= TEXT_LIMIT else None)
        item = dict(path=path, change=change, added=0, removed=0, binary=a is None or b is None, diff='')
        if not item['binary']:
            lines = list(difflib.unified_diff(a, b, fromfile='a/' + path, tofile='b/' + path))
            item['added'] = sum(1 for line in lines if line.startswith('+') and not line.startswith('+++'))
            item['removed'] = sum(1 for line in lines if line.startswith('-') and not line.startswith('---'))
            text = ''.join(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n' for line in lines)
            if len(text) > FILE_DIFF_LIMIT:
                text = text[:FILE_DIFF_LIMIT].rsplit('\n', 1)[0] + f'\n[Dervo cut this file’s diff here: {len(text) - FILE_DIFF_LIMIT} more characters]\n'
            item['diff'] = text
        out.append(item)
    return out
