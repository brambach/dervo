"""Host-only task boundary. Never expose these methods as renderer passthrough.

The host supplies authenticated context, serializes calls with Store, and alone
calls approve/review. Provider adapters may request allowlisted reads/changes,
not roots, interpreters, command strings, approval identities or test evidence.
"""
import base64
from contextlib import contextmanager, ExitStack
import difflib
import hashlib
import functools
import json
import os
from pathlib import Path
import platform_os
import re
import selectors
import signal
import stat
import subprocess
import sys
import sysconfig
import tempfile
import threading
import queue
import time
import uuid

from dervo_connections import Rejected
import check_profiles
import typescript_inputs
import project_access


class ConfinementBlocked(Rejected):
    """No verification ran outside confinement. The host must show this blocker."""


CONFINEMENT_PLAIN = ('Dervo couldn’t run the check safely on this Mac, so it didn’t run it. Review the change yourself, or pick '
                     'another check in Settings, Checks and review.')


def check_blocker(exc):
    """What a check that couldn't run says on the result's card: plain words, never a class name (polish review, 9 Oct).
    A refusal says its own sentence; anything unexpected keeps its type so a bug report can name it."""
    if isinstance(exc, ConfinementBlocked):
        return CONFINEMENT_PLAIN
    if isinstance(exc, Rejected):
        return str(exc)
    return f'The check couldn’t run ({type(exc).__name__}: {exc})'


# Beta.17.1: a task's attempts are limited per project (run_limits, 64 by default, was a fixed 16). The line starts the
# same way whatever the number, so run_ending can tell a limit from a failure in records it didn't see made.
ATTEMPT_LIMIT_LEAD = 'This task has used all '


class AttemptLimit(Rejected):
    """The task reached its project's attempt limit. Raising the limit in Settings lets the same task carry on."""
    def __init__(self, limit):
        super().__init__(f'{ATTEMPT_LIMIT_LEAD}{limit} of its attempts, this project’s limit. Raise it in Settings, '
                         'Access, or ask for the rest in a new message.')
        self.limit = limit


# Big files in scoped mode (beta.15, harrison's report of 24 Sep). A file up to INLINE_LIMIT keeps its bytes inline in
# the task record, exactly as before. A bigger one, up to FILE_LIMIT, keeps them in a file in Dervo's app data
# (``file-versions`` beside the database, named by sha256), and the record keeps {sha256, bytes, stored: True}. Nothing
# over FILE_LIMIT is read or changed, so a stray huge file can't fill the disk or the database.
INLINE_LIMIT = 262144
FILE_LIMIT = 16777216
READ_BUDGET = 40000  # bytes of file text, as the reply's JSON spells it, in one read; a whole tool result may be 60000


def _read_all(fd, limit):
    """Up to ``limit`` + 1 bytes from an open file, so the caller can tell a file over the limit."""
    chunks, total = [], 0
    while total <= limit:
        chunk = os.read(fd, min(1048576, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b''.join(chunks)


def _line_diff(path, left, right):
    """A unified diff of two big text files. Only the part between their common first and last lines is compared, with
    three lines of context, so one edit in a 16 MiB file doesn't compare every line. Hunk line numbers are the file's."""
    a, b = left.splitlines(keepends=True), right.splitlines(keepends=True)
    shortest = min(len(a), len(b))
    start = 0
    while start < shortest and a[start] == b[start]:
        start += 1
    end = 0
    while end < shortest - start and a[len(a) - 1 - end] == b[len(b) - 1 - end]:
        end += 1
    low, keep = max(0, start - 3), max(0, end - 3)
    lines = difflib.unified_diff(a[low:len(a) - keep], b[low:len(b) - keep], fromfile='before/' + path, tofile='after/' + path)

    def moved(match):
        return f'@@ -{int(match[1]) + low}{match[2] or ""} +{int(match[3]) + low}{match[4] or ""} @@'
    out = []
    for line in lines:
        if line.startswith('@@'):
            line = re.sub(r'^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@', moved, line)
        out.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    return ''.join(out)


def versions_folder(db):
    """``file-versions`` beside the main database file, or None for a database in memory."""
    row = db.execute('PRAGMA database_list').fetchone()
    name = row[2] if row else ''
    return Path(name).parent / 'file-versions' if name else None


def stored_digests(value):
    """The sha256 of every version kept beside the database that a record names ({sha256, bytes, stored: True})."""
    found, stack = set(), [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            digest = item.get('sha256')
            if item.get('stored') is True and isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest):
                found.add(digest)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def forget_versions(db, digests):
    """After a project or conversation is deleted (security review S3, 24 Sep): remove the kept versions its records
    named that no remaining record names. Versions are shared by content, so one another task still names stays. Answers
    how many files went."""
    folder = versions_folder(db)
    if not digests or folder is None:
        return 0
    still = set()
    for (document,) in db.execute('SELECT document FROM task_evidence'):
        try:
            still |= stored_digests(json.loads(document))
        except ValueError:
            continue
    removed = 0
    for digest in sorted(set(digests) - still):
        try:
            (folder / digest[:2] / digest).unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _python_runtime_files():
    prefix = check_profiles.audited_python_prefix()
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    allowed = {check_profiles.python_interpreter(), '/usr/lib/libSystem.B.dylib',
               '/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation'}
    if prefix == check_profiles.FRAMEWORK_PYTHON:
        allowed.add(str(prefix / 'Python'))  # the framework's interpreter library; the app's own runtime is one executable
    # Exact runtime files, never the entire framework, /Library, /usr or /System.
    for directory, dirs, files in os.walk(stdlib):
        dirs[:] = [d for d in dirs if d not in ('site-packages', '__pycache__', 'ensurepip', 'test', 'idlelib', 'tkinter')
                   and not (Path(directory) / d).is_symlink()]
        allowed.add(directory)
        for name in files:
            path = Path(directory) / name
            if path.suffix in ('.py', '.so') and not path.is_symlink():
                allowed.add(str(path))
    for path in (prefix / 'lib').glob('*.dylib'):
        allowed.add(str(path.resolve()))
    return allowed


# TAP's summary, or the spec reporter's that Node 24 prints on a pipe too.
NODE_TESTS_REPORTED = re.compile(r'^(?:#|ℹ) tests [1-9]\d*$', re.MULTILINE)


def _node_runtime_files(executable):
    # Supported layout: the nodejs.org installer binary, which links only system libraries from the shared cache. A build
    # that links other libraries (Homebrew's node links icu4c, openssl, libuv and more from its own prefix) is not listed
    # here and fails at dyld inside the sandbox; that surfaces as ConfinementBlocked and is never retried unconfined.
    # /usr, /opt and the user's home are never allowed as a whole.
    # /System/Library/OpenSSL/openssl.cnf is the one system config Node's bundled OpenSSL opens at startup (a denied open aborts
    # startup; an absent file would be ignored). It holds no user data.
    return {executable, '/System/Library/OpenSSL/openssl.cnf', '/usr/lib/libSystem.B.dylib', '/usr/lib/libc++.1.dylib', '/usr/lib/libc++abi.dylib',
            '/usr/lib/libobjc.A.dylib', '/usr/lib/libresolv.9.dylib',
            '/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation',
            '/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation',
            '/System/Library/Frameworks/Security.framework/Versions/A/Security'}


def _run_confined(root, argv, *, timeout=5, output_limit=65536, cancel=None):
    """Private host runner. Only the exact argv of an available check profile (Python unittest, Node test or TypeScript
    check on one root file) is accepted; the runtime named in argv must be the one the profile discovers. The TypeScript
    check reads its compiler from the snapshot root itself, so its policy is exactly the Node policy."""
    if platform_os.WINDOWS:
        return _run_confined_windows(root, argv, timeout=timeout, output_limit=output_limit, cancel=cancel)
    if platform_os.LINUX:
        return _run_confined_linux(root, argv, timeout=timeout, output_limit=output_limit, cancel=cancel)
    prefix = check_profiles.audited_python_prefix()
    if (sys.platform != 'darwin' or sys.version_info[:2] != (3, 13) or Path(sys.prefix).resolve() != prefix.resolve()
            or not Path('/usr/bin/sandbox-exec').is_file()):
        raise ConfinementBlocked('Confinement unavailable: requires macOS sandbox-exec and the audited Python 3.13 runtime '
                                 '(Dervo’s own in the app, the python.org framework in a source checkout)')
    if type(argv) is not list or any(type(a) is not str for a in argv):
        raise Rejected('Only host-selected check profile argv is supported')
    profile_id = check_profiles.profile_of(argv)
    if profile_id in check_profiles.RUST_PROFILES:
        raise Rejected('Rust metadata requires the bound snapshot runner')
    check_profiles.runner_timeout(timeout)
    if not 0 < output_limit <= 65536:
        raise Rejected('Verification limits exceed host bounds')
    executable = argv[0]
    allowed = {'/'} | (_python_runtime_files() if profile_id == check_profiles.PYTHON else _node_runtime_files(executable))
    resolved_root = Path(root).resolve()
    ancestors = [str(p) for p in resolved_root.parents]  # metadata only: Node realpaths the script through each component
    literals = ' '.join('(literal ' + json.dumps(p) + ')' for p in sorted(allowed))
    profile = ('(version 1)(deny default)(deny network*)(deny file-write*)(deny process-fork)'
               '(allow sysctl-read)(allow process-exec (literal ' + json.dumps(executable) + '))'
               '(allow file-map-executable ' + literals + ')'
               '(allow file-read-metadata ' + ' '.join('(literal ' + json.dumps(p) + ')' for p in ancestors) + ')'
               '(allow file-read* ' + literals + ' (subpath ' + json.dumps(str(resolved_root)) + '))')
    return _capture_confined(root, argv, profile, profile_id, allowed, timeout=timeout,
                             output_limit=output_limit, cancel=cancel)


_WINDOWS_GRANTED = set()  # runtime folders already granted to the check container in this process


def _run_confined_windows(root, argv, *, timeout, output_limit, cancel):
    """Windows: Python checks only, in an app container on a restricted token (windows_confinement.py). The runtime
    is the Python 3.13 the host itself runs on, which the app carries. Anything else is ConfinementBlocked."""
    import windows_confinement
    if type(argv) is not list or any(type(a) is not str for a in argv):
        raise Rejected('Only host-selected check profile argv is supported')
    profile_id = check_profiles.profile_of(argv)
    if profile_id != check_profiles.PYTHON:
        raise ConfinementBlocked('Confinement unavailable: only Python checks run on Windows')
    check_profiles.runner_timeout(timeout)
    if not 0 < output_limit <= 65536:
        raise Rejected('Verification limits exceed host bounds')
    runtime = Path(sys.base_prefix).resolve()
    if sys.version_info[:2] != (3, 13) or Path(argv[0]).resolve() != runtime / 'python.exe':
        raise ConfinementBlocked("Confinement unavailable: requires Dervo's own Python 3.13 runtime")
    try:
        if runtime not in _WINDOWS_GRANTED:  # granting walks the whole runtime, so once per host
            windows_confinement.grant_read(str(runtime))
            _WINDOWS_GRANTED.add(runtime)
        windows_confinement.grant_read(str(Path(root).resolve()))
        status, output, reason = windows_confinement.run(
            argv, root, {'HOME': '', 'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows')},
            timeout=timeout, output_limit=output_limit, cancel=cancel)
    except windows_confinement.Unavailable as exc:
        raise ConfinementBlocked('Confinement launch failed: ' + str(exc)) from exc
    result = {k: v.decode('utf-8', 'replace') for k, v in output.items()}
    result.update(exit_status=status, stopped=reason, argv=argv, confinement=windows_confinement.CONFINEMENT,
                  check_profile=profile_id, runtime_files=[str(runtime)])
    return result


def _run_confined_linux(root, argv, *, timeout, output_limit, cancel):
    """Linux: Python, Node and TypeScript checks under bubblewrap with a seccomp filter (linux_confinement.py). Python
    is the 3.13 the host itself runs on: the app's own in a package, the system's in a source checkout. Node is the one
    check_profiles.node_runtime found (profile_of has already matched argv[0] to it). Rust is ConfinementBlocked."""
    import linux_confinement
    if type(argv) is not list or any(type(a) is not str for a in argv):
        raise Rejected('Only host-selected check profile argv is supported')
    profile_id = check_profiles.profile_of(argv)
    if profile_id in check_profiles.RUST_PROFILES:
        raise ConfinementBlocked('Confinement unavailable: Rust checks run only on a Mac')
    check_profiles.runner_timeout(timeout)
    if not 0 < output_limit <= 65536:
        raise Rejected('Verification limits exceed host bounds')
    if profile_id == check_profiles.PYTHON:
        runtime = Path(sys.base_prefix).resolve()
        if sys.version_info[:2] != (3, 13) or argv[0] != check_profiles.python_interpreter():
            raise ConfinementBlocked("Confinement unavailable: requires Dervo's own Python 3.13 runtime")
    else:
        runtime = Path(argv[0]).resolve().parent
    read, write = os.pipe()
    try:
        try:
            os.write(write, linux_confinement.seccomp_filter())
            launch = linux_confinement.command(argv, root, runtime, read)
        except linux_confinement.Unavailable as exc:
            raise ConfinementBlocked('Confinement unavailable: ' + str(exc)) from exc
        finally:
            os.close(write)
        return _capture_confined(root, argv, None, profile_id, {str(runtime)}, timeout=timeout, output_limit=output_limit,
                                 cancel=cancel, launch=launch, pass_fds=(read,),
                                 confinement=linux_confinement.CONFINEMENT)
    finally:
        os.close(read)


def _capture_confined(root, argv, profile, profile_id, allowed, *, timeout=5, output_limit=65536, cancel=None, file_limit=None, sdkroot=None, signal_is_result=False,
                      launch=None, pass_fds=(), confinement='macos-sandbox-exec'):
    # Shared capture mechanics; policy and exact argv remain selected by each host profile.
    # The Rust-only pre-exec action is the C setrlimit call, with imports and arguments prepared on the host.
    # ``launch`` is the whole confined command when it isn't sandbox-exec with ``profile`` (Linux: bwrap, which reads its
    # seccomp filter from one of ``pass_fds``).
    import resource  # Unix only: Windows checks capture through windows_confinement.run
    limits = functools.partial(resource.setrlimit, resource.RLIMIT_FSIZE, (file_limit, file_limit)) if file_limit is not None else None
    try:
        process = subprocess.Popen(launch or ['/usr/bin/sandbox-exec', '-p', profile, *argv], cwd=root,
                                   env={'HOME': '', **({'SDKROOT': str(sdkroot)} if sdkroot is not None else {})}, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, **platform_os.new_group(), close_fds=True, pass_fds=pass_fds,
                                   **({"preexec_fn": limits} if file_limit is not None else {}))
    except OSError as exc:
        raise ConfinementBlocked('Confinement launch failed: ' + str(exc)) from exc
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    reason = None
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for name in buffers:
                pipe = getattr(process, name)
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                if cancel is not None and cancel.is_set():
                    reason = 'cancelled'
                    break
                if time.monotonic() >= deadline:
                    reason = 'timeout'
                    break
                for key, _ in selector.select(min(0.05, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = output_limit - sum(map(len, buffers.values()))
                    buffers[key.data].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        reason = 'output_limit'
                        break
                if reason:
                    break
            if not reason:
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    reason = 'timeout'
    finally:
        try:
            platform_os.signal_group(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()
        if confinement == 'linux-bubblewrap':
            import linux_confinement
            linux_confinement.settle(root)
    if file_limit is not None and process.returncode == -signal.SIGXFSZ and not reason:
        reason = 'output_file_limit'
    result = {k: bytes(v).decode('utf-8', 'replace') for k, v in buffers.items()}
    result.update(exit_status=process.returncode, stopped=reason, argv=argv, confinement=confinement,
                  check_profile=profile_id, runtime_files=sorted(allowed))
    if result['stderr'].startswith('bwrap:'):
        import linux_confinement
        if linux_confinement.namespace_refused(result['stderr']):
            raise ConfinementBlocked(linux_confinement.NAMESPACE_REFUSED + ' ' + result['stderr'][:500])
    if 'sandbox-exec:' in result['stderr'] or 'sandbox_init:' in result['stderr'] or result['stderr'].startswith('bwrap:'):
        raise ConfinementBlocked('Confinement initialization failed: ' + result['stderr'][:2000])
    if process.returncode < 0 and not reason and not signal_is_result:
        raise ConfinementBlocked('Confinement runtime aborted with signal ' + str(-process.returncode)
                                 + '; exact-runtime profile could not verify ' + ('Rust compiler' if profile_id in check_profiles.RUST_PROFILES else 'Python startup') + '. No unrestricted retry. '
                                 + 'stdout=' + repr(result['stdout'][:1000]) + ' stderr=' + repr(result['stderr'][:1000]))
    if profile_id == check_profiles.NODE and process.returncode == 0 and not reason and not NODE_TESTS_REPORTED.search(result['stdout']):
        # A directly run file that never registers a node:test test exits 0 with no summary at all (unittest discover exits 5
        # for the same case). The run is kept exactly as recorded but doesn't count as a passing check. This is a courtesy
        # check on untrusted output, not a trust boundary: a registered no-op test is a test, and the file can print anything.
        result['stopped'] = 'no_tests_reported'
    return result


class TaskEvidence:
    def __init__(self, store):
        self.store = store
        self._jobs = {}
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS task_evidence (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            for row in store.db.execute('SELECT document FROM task_evidence').fetchall():
                task = json.loads(row[0])
                if task['attempts'] and task['attempts'][-1]['state'] == 'running':
                    task['attempts'][-1].update(state='uncertain', blocker='Host restarted before verification confirmation')
                    task['review'] = None
                    store.db.execute('UPDATE task_evidence SET document=? WHERE id=?', (json.dumps(task), task['id']))

    # ------------------------------------------------------------------ big files: bytes kept beside the database
    def versions_folder(self):
        """Where a file over INLINE_LIMIT keeps each recorded version: ``file-versions`` beside Dervo's database, in its
        app data. None for a database that has no folder (in memory), which then refuses big files."""
        return versions_folder(self.store.db)

    def _keep(self, data):
        """Save one big version by its sha256 (written once, then only read) and answer its record entry."""
        digest = hashlib.sha256(data).hexdigest()
        folder = self.versions_folder()
        if folder is None:
            raise Rejected('Files over 256 KiB need Dervo’s app data folder, and this workspace has none')
        target = folder / digest[:2] / digest
        if not target.is_file():
            # Both levels 0700: mkdir's mode applies only to the folder it makes last (security review S3).
            folder.mkdir(exist_ok=True, mode=0o700)
            target.parent.mkdir(exist_ok=True, mode=0o700)
            fd, temp = tempfile.mkstemp(dir=target.parent, prefix='.' + digest[:8])
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp, 0o600)
                os.replace(temp, target)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        return dict(sha256=digest, bytes=len(data), stored=True)

    def snapshot_bytes(self, snapshot):
        """The bytes a recorded snapshot holds, inline or kept beside the database. Raises when a kept version is missing
        or no longer matches its sha256, so nothing is ever committed, checked or compared from the wrong bytes."""
        if not isinstance(snapshot, dict):
            raise Rejected('No recorded version')
        if 'bytes_b64' in snapshot:
            return base64.b64decode(snapshot['bytes_b64'])
        folder = self.versions_folder()
        digest = snapshot.get('sha256')
        if not snapshot.get('stored') or folder is None or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise Rejected('No recorded version')
        try:
            data = (folder / digest[:2] / digest).read_bytes()
        except OSError as exc:
            raise Rejected('Dervo’s saved copy of this file version is missing') from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise Rejected('Dervo’s saved copy of this file version changed on disk')
        return data

    def hydrated(self, snapshot):
        """A kept snapshot in the inline form older readers expect ({sha256, bytes_b64}), for this call only. The stored
        record never changes. Inline snapshots and None come back as they are."""
        if not isinstance(snapshot, dict) or 'bytes_b64' in snapshot or not snapshot.get('stored'):
            return snapshot
        return dict(sha256=snapshot['sha256'], bytes_b64=base64.b64encode(self.snapshot_bytes(snapshot)).decode())

    def for_commit(self, evidence, task):
        """A copy of ``evidence`` with the snapshots a commit of ``task`` reads (task_git.preimages: each file's first
        recorded preimage in the task's runs, and task_git.approved_hashes: the last attempt's after) in inline form.
        Only those are read from disk, so sixteen attempts on a 16 MiB file don't all load at once."""
        attempts = evidence.get('attempts') or []
        if not any(isinstance(s, dict) and s.get('stored') for a in attempts for side in ('before', 'after')
                   for s in (a.get(side) or {}).values()):
            return evidence
        copied = dict(evidence, attempts=[dict(a, before=dict(a.get('before') or {}), after=dict(a['after']) if isinstance(a.get('after'), dict) else a.get('after'))
                                          for a in attempts])
        first = (task.get('runs') or [{}])[0].get('prior_attempts', 0) + 1
        last = copied['attempts'][-1]['number']
        seen = set()
        for attempt in sorted([a for a in copied['attempts'] if first <= a['number'] <= last] or copied['attempts'][-1:], key=lambda a: a['number']):
            for path in set(attempt['before']) | set(attempt.get('after') or {}):
                if path not in seen:
                    seen.add(path)
                    attempt['before'][path] = self.hydrated(attempt['before'].get(path))
        last = copied['attempts'][-1]
        if isinstance(last.get('after'), dict):
            last['after'] = {path: self.hydrated(snap) for path, snap in last['after'].items()}
        return copied

    def _save(self, task):
        with self.store.db:
            self.store.db.execute('INSERT INTO task_evidence VALUES (?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document',
                                  (task['id'], json.dumps(task)))
        return task

    def create(self, *, thread_id, project_id, actor, files, test_file, folders=(), check_profile=None, rust_toolchain=None, check_timeout_seconds=5, project_access=False):
        if type(project_access) is not bool:
            raise Rejected('Project access must be a boolean')
        binding = self.store.binding(thread_id, project_id, actor=actor)
        task = dict(id=str(uuid.uuid4()), thread_id=thread_id, project_id=project_id,
                    actor=actor, profile_id=binding['profile_id'], model=binding['model'],
                    root=binding['cwd'], visibility=binding['visibility'], files=files,
                    folders=self._folders(binding['cwd'], folders), created=[],
                    scope_version=1, approval=None, review=None, attempts=[], argv=None,
                    check_profile=check_profiles.profile(check_profile), rust_toolchain=rust_toolchain,
                    check_timeout_seconds=check_profiles.check_timeout(check_timeout_seconds))
        if project_access:
            task['project_access'] = True
            task['root_identity'] = globals()['project_access'].root_identity(task['root'])
        self._scope(task, files, test_file, check_profile)
        return self._save(task)

    # ------------------------------------------------------------------ granted folders (explicit, never the root)
    FOLDER_LIMIT = 8
    LIST_LIMIT = 200          # entries returned by one listing
    ENTRY_LIMIT = 2000        # directory entries examined by one enumeration before it stops (never a full listdir first)
    SEARCH_FILES = 200        # files scanned by one search
    # Security review N6 (24 Sep): a search runs on the host's request loop, which the app gives 10 s. 16 MiB still takes
    # the biggest file a task can change, and the clock stops a slow disk from holding the loop past a few seconds.
    SEARCH_BYTES = 16777216   # bytes scanned by one search (16 MiB: one of the biggest files a task can change)
    SEARCH_SECONDS = 3.0      # time one search may spend reading before it stops and reports a partial result
    SEARCH_HITS = 64          # matching lines returned by one search
    SEARCH_DIRECTORIES = 64   # directories visited (opened) by one search, including empty ones
    LINE_CLIP = 200
    CREATE_LIMIT = 32
    ADMIT_LIMIT = 64          # discovered files a task may admit into its evidence scope

    @staticmethod
    def _under_granted(parts, granted):
        return any(parts[:len(g.split('/'))] == g.split('/') for g in granted)

    @classmethod
    def _admits(cls, task, path):
        """One content-access policy for list, search, read and write: a path is accessible when it is a granted file, a
        file this task created or admitted, or a regular file discovered directly under a granted folder. The same name
        rules apply everywhere (``_relative``), so search never exposes a file that read refuses, and the reverse."""
        if task.get('project_access') is True:
            return bool(project_access.relative(path, task['root']))
        parts = cls._relative(path)
        if path in task['files'] or path in task.get('created', ()) or path in task.get('admitted', ()):
            return True
        return len(parts) >= 2 and cls._under_granted(parts[:-1], task.get('folders', ()))

    @staticmethod
    def _scoped(task):
        """Every file whose bytes this task's evidence covers: granted, created and admitted, in one stable order."""
        return list(task['files']) + list(task.get('created', ())) + list(task.get('admitted', ()))

    def _admit(self, task, path):
        """A discovered file becomes a durable part of the task's evidence scope the first time it is read or written, so
        every later snapshot, review packet and completion record covers it. Refused beyond ADMIT_LIMIT."""
        if path in self._scoped(task):
            return False
        if not self._admits(task, path):
            raise Rejected('File is outside task scope')
        if len(task.get('admitted', ())) >= self.ADMIT_LIMIT:
            raise Rejected(f'A task may admit up to {self.ADMIT_LIMIT} discovered files')
        with self._file(task, path):
            pass
        task.setdefault('admitted', []).append(path)
        task['review'] = None
        self._save(task)
        return True

    @classmethod
    def _folders(cls, root, folders):
        """Explicit user-granted relative directories. Each is named by the same relative and sensitive-name rules as a
        file, must exist as a real directory (no symlink) inside the project, and never names the project root."""
        if folders is None:
            folders = ()
        if (not isinstance(folders, (list, tuple)) or len(folders) > cls.FOLDER_LIMIT or len(set(folders)) != len(folders)
                or any(not isinstance(f, str) for f in folders)):
            raise Rejected(f'Granted folders are up to {cls.FOLDER_LIMIT} distinct relative directories')
        result = []
        for folder in folders:
            parts = cls._relative(folder)
            with cls._directory(root, parts, granted=None):
                pass
            result.append(folder)
        return sorted(result)

    @staticmethod
    @contextmanager
    def _directory(root, parts, *, granted):
        """Descriptor walk to a directory. Every step is O_NOFOLLOW, so a symlink anywhere is refused. ``granted`` is the
        task's folder list; when given, ``parts`` must be one of them or nested under one (never the root, never a sibling)."""
        parts = list(parts)
        if granted is not None and not TaskEvidence._under_granted(parts, granted):
            raise Rejected('Directory is outside the granted folders')
        descriptors = []
        try:
            fd = platform_os.open_root(root)
            descriptors.append(fd)
            for part in platform_os.below_root(root) + tuple(parts):
                fd = platform_os.open_dir_at(part, fd)
                descriptors.append(fd)
            yield fd
        except OSError as exc:
            raise Rejected('Safe directory access failed: ' + str(exc)) from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    @contextmanager
    def _task_directory(self, task, parts):
        if not task.get('project_access'):
            with self._directory(task['root'], parts, granted=task['folders']) as fd:
                yield fd
            return
        try:
            with project_access.directory(task['root'], parts, task['root_identity']) as fd:
                yield fd
        except OSError as exc:
            raise Rejected('Safe project directory access failed: ' + str(exc)) from exc

    @classmethod
    def _entries(cls, dir_fd, *, project=False, root=None, prefix=()):
        """Regular single-link files and plain directories inside one open directory. Dot names, sensitive names and
        anything that is a symlink, hardlinked or special is left out. Enumeration stops after ENTRY_LIMIT entries are
        examined (a streamed scandir, never a full listdir and sort first); the second value says whether it stopped
        early. Sorted over what was examined, never recursive by itself."""
        out, examined, truncated = [], 0, False
        try:
            fd = os.dup(dir_fd)  # scandir(fd) closes nothing of ours; the walk's descriptor stays valid
        except OSError as exc:
            raise Rejected('Safe directory access failed: ' + str(exc)) from exc
        try:
            with platform_os.scandir_fd(fd) as it:
                for entry in it:
                    if examined >= cls.ENTRY_LIMIT:
                        truncated = True
                        break
                    examined += 1
                    try:
                        if project:
                            project_access.relative('/'.join([*prefix, entry.name]), root)
                        else:
                            cls._relative(entry.name)
                    except Rejected:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(info.st_mode):
                        out.append(dict(name=entry.name, kind='directory', size=None))
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        out.append(dict(name=entry.name, kind='file', size=info.st_size))
        except OSError as exc:
            raise Rejected('Safe directory access failed: ' + str(exc)) from exc
        finally:
            os.close(fd)
        out.sort(key=lambda e: e['name'])
        return out, truncated

    def list_files(self, task_id, directory, **context):
        task = self.read(task_id, **context)
        parts = ([] if directory == '.' else project_access.relative(directory, task['root'])) if task.get('project_access') else self._relative(directory)
        with self._task_directory(task, parts) as fd:
            entries, partial = self._entries(fd, project=task.get('project_access', False), root=task['root'], prefix=parts)
        return dict(directory=directory, entries=entries[:self.LIST_LIMIT], truncated=partial or len(entries) > self.LIST_LIMIT,
                    limit=self.LIST_LIMIT, examined_limit=self.ENTRY_LIMIT)

    def search_files(self, task_id, directory, query, **context):
        """Literal substring search over regular files nested under one granted directory, with finite caps on directories
        visited, entries examined per directory, files, bytes and hits. Reports what was scanned and what was skipped so
        a partial result is never mistaken for a full one. Only files the read policy admits are opened."""
        task = self.read(task_id, **context)
        parts = ([] if directory == '.' else project_access.relative(directory, task['root'])) if task.get('project_access') else self._relative(directory)
        if not isinstance(query, str) or not query or len(query.encode()) > 256:
            raise Rejected('Search query is a literal string of 1 to 256 bytes')
        needle = query.encode()
        hits, scanned = [], dict(files=0, bytes=0, directories=0, skipped_oversize=0, unvisited_directories=0, stopped_for_time=False)
        truncated = False
        queue = [parts]
        deadline = time.monotonic() + self.SEARCH_SECONDS
        while queue and not truncated:
            if scanned['directories'] >= self.SEARCH_DIRECTORIES:
                truncated = True
                break
            current = queue.pop(0)
            scanned['directories'] += 1
            with self._task_directory(task, current) as fd:
                entries, partial = self._entries(fd, project=task.get('project_access', False), root=task['root'], prefix=current)
                if partial:
                    truncated = True
                for entry in entries:
                    if entry['kind'] == 'directory':
                        if len(queue) + scanned['directories'] < self.SEARCH_DIRECTORIES:
                            queue.append(current + [entry['name']])
                        else:
                            scanned['unvisited_directories'] += 1
                            truncated = True
                        continue
                    if scanned['files'] and time.monotonic() > deadline:
                        scanned['stopped_for_time'] = True  # at least one file is always read, so a search moves forward
                        truncated = True
                        break
                    if scanned['files'] >= self.SEARCH_FILES or scanned['bytes'] >= self.SEARCH_BYTES:
                        truncated = True
                        break
                    if entry['size'] > FILE_LIMIT or scanned['bytes'] + entry['size'] > self.SEARCH_BYTES:
                        scanned['skipped_oversize'] += 1
                        truncated = True
                        continue
                    if not self._admits(task, '/'.join(current + [entry['name']])):
                        continue
                    try:
                        ffd = platform_os.open_file_at(entry['name'], fd)
                    except OSError:
                        continue
                    try:
                        info = os.fstat(ffd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            continue
                        data = _read_all(ffd, FILE_LIMIT)[:FILE_LIMIT]
                    finally:
                        os.close(ffd)
                    scanned['files'] += 1
                    scanned['bytes'] += len(data)
                    if needle not in data:
                        continue
                    relative = '/'.join(current + [entry['name']])
                    for number, line in enumerate(data.splitlines(), 1):
                        if needle in line:
                            if len(hits) >= self.SEARCH_HITS:
                                truncated = True
                                break
                            hits.append(dict(path=relative, line=number,
                                             text=line[:self.LINE_CLIP].decode('utf-8', 'replace')))
                    if truncated:
                        break
        scanned['unvisited_directories'] += len(queue)
        if queue:
            truncated = True
        return dict(directory=directory, query=query, hits=hits, scanned=scanned, truncated=truncated,
                    coverage='partial' if truncated else 'complete',
                    limits=dict(files=self.SEARCH_FILES, bytes=self.SEARCH_BYTES, hits=self.SEARCH_HITS,
                                directories=self.SEARCH_DIRECTORIES, entries=self.ENTRY_LIMIT, seconds=self.SEARCH_SECONDS))

    def _room_for_attempt(self, task, context):
        """Raises AttemptLimit when the task has made as many attempts as its project allows. Read each time, so a limit
        raised in Settings applies to the same task's next attempt. No limit (None) never refuses."""
        import run_limits
        limit = run_limits.read(self.store.db, context.get('project_id'))['attempts']
        if limit is not None and len(task['attempts']) >= limit:
            raise AttemptLimit(limit)

    def create_file(self, task_id, path, content, **context):
        """Create one new regular file inside a granted folder. The intent is absent-before: an existing target is a
        conflict, a missing parent directory is refused (never created), and the attempt records absent-before,
        current-after and the diff. An interrupted creation stays uncertain and is never replayed."""
        task = self.read(task_id, **context)
        if task.get('read_only'):
            raise Rejected('Project reading cannot edit files or run checks')
        if not task['approval'] or task['approval']['scope_version'] != task['scope_version']:
            raise Rejected('Task scope needs explicit human approval')
        if task['attempts'] and task['attempts'][-1]['state'] in ('uncertain', 'running'):
            raise Rejected('Interrupted attempt requires host inspection, not automatic retry')
        self._room_for_attempt(task, context)
        if not isinstance(content, bytes) or len(content) > INLINE_LIMIT:
            raise Rejected('New file content must be bytes up to 256 KiB')
        parts = self._relative(path)
        if task.get('project_access'):
            project_access.relative(path, task['root'])
        if len(parts) < 2 and not task.get('project_access'):
            raise Rejected('New files are created inside a granted folder, never at the project root')
        if path in self._scoped(task):
            raise Rejected('Target already exists in task scope')
        if len(task['created']) >= self.CREATE_LIMIT:
            raise Rejected(f'A task may create up to {self.CREATE_LIMIT} files')
        scoped = self._scoped(task)
        before = {p: self._snapshot(task, p) for p in scoped}
        with self._task_directory(task, parts[:-1]) as dir_fd:
            try:
                platform_os.stat_at(parts[-1], dir_fd)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise Rejected('Safe file access failed: ' + str(exc)) from exc
            else:
                raise Rejected('Target already exists. Creation needs an absent target; a granted existing file is changed with dervo_write_file')
            before[path] = None
            attempt = dict(number=len(task['attempts']) + 1, scope_version=task['scope_version'], kind='create',
                           files=list(scoped) + [path], argv=list(task['argv']), check_timeout_seconds=task['check_timeout_seconds'], before=before,
                           after=None, diff={}, state='uncertain', test=None, blocker=None, created=path)
            task['attempts'].append(attempt)
            task['created'].append(path)
            task['review'] = None
            self._save(task)
            try:
                fd = platform_os.open_file_at(parts[-1], dir_fd, 'new', 0o644)
            except FileExistsError as exc:
                attempt.update(state='blocked', blocker='Target appeared before creation: ' + str(exc))
                task['created'].remove(path)
                self._save(task)
                raise Rejected('Target already exists. Creation needs an absent target') from exc
            except OSError as exc:
                attempt.update(state='blocked', blocker='Creation failed: ' + str(exc))
                task['created'].remove(path)
                self._save(task)
                raise Rejected('Safe file creation failed: ' + str(exc)) from exc
            try:
                data = memoryview(content)
                while data:
                    size = os.write(fd, data)
                    if not size:
                        raise Rejected('File write made no progress')
                    data = data[size:]
                os.fsync(fd)
            finally:
                os.close(fd)
        after = {p: self._snapshot(task, p) for p in attempt['files']}
        attempt['after'] = after
        lines = content.decode('utf-8', 'replace').splitlines(keepends=True)
        diff = ''.join(difflib.unified_diff([], lines, fromfile='absent/' + path, tofile='after/' + path))
        if diff and not diff.endswith('\n'):
            diff += '\n\\ No newline at end of file\n'
        attempt['diff'][path] = diff
        for p in scoped:
            attempt['diff'][p] = ''
        attempt['state'] = 'prepared'
        self._freeze_check_inputs(task, attempt)
        self._save(task)
        return attempt

    @staticmethod
    def _relative(path, root=None):
        """The same name rule as project access (project_access.refusal), so reading, writing and full-access recording
        can't disagree (16.2). With the project ``root``, a generated folder is refused only when git ignores it; a scoped
        name checked without one keeps its old leniency there."""
        return project_access.relative(path, root, generated=root is not None)

    @contextmanager
    def _file(self, task, path, writable=False):
        parts = self._relative(path, task['root'] if task.get('project_access') else None)
        if not self._admits(task, path):
            raise Rejected('File is outside task scope')
        if writable and task.get('read_only'):
            raise Rejected('Project reading cannot edit files')
        if writable and task.get('check_profile') == check_profiles.TYPESCRIPT \
                and path in task.get('compiler', {}).get('sha256', {}):
            raise Rejected('Read-only TypeScript input cannot be changed by the task: ' + path)
        if task.get('project_access'):
            with project_access.file_descriptor(task, path, writable) as fd:
                yield fd
            return
        descriptors = []
        try:
            fd = platform_os.open_root(task['root'])
            descriptors.append(fd)
            for part in platform_os.below_root(task['root']) + tuple(parts[:-1]):
                fd = platform_os.open_dir_at(part, fd)
                descriptors.append(fd)
            fd = platform_os.open_file_at(parts[-1], fd, 'rw' if writable else 'r')
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > FILE_LIMIT:
                raise Rejected('File must be a single-link regular file up to 16 MiB')
            yield fd
        except OSError as exc:
            raise Rejected('Safe file access failed: ' + str(exc)) from exc
        finally:
            for fd in reversed(descriptors):
                os.close(fd)

    def _bytes(self, task, path):
        with self._file(task, path) as fd:
            data = _read_all(fd, FILE_LIMIT)
        if len(data) > FILE_LIMIT:
            raise Rejected('File exceeded 16 MiB')
        return data

    def _record(self, data):
        """A snapshot entry for ``data``: inline up to INLINE_LIMIT (unchanged since before big files), kept beside the
        database above it."""
        if len(data) <= INLINE_LIMIT:
            return dict(sha256=hashlib.sha256(data).hexdigest(), bytes_b64=base64.b64encode(data).decode())
        return self._keep(data)

    def _snapshot(self, task, path):
        return self._record(self._bytes(task, path))

    def read_file(self, task_id, path, **context):
        task = self.read(task_id, **context)
        self._admit(task, path)  # a discovered file read once is in evidence from then on
        return self._snapshot(task, path)

    def read_lines(self, snapshot, start_line=None, end_line=None):
        """What one dervo_read_file reply carries: the file's sha256 (always of the whole file, so an edit can name it),
        its total lines, and the text of lines ``start_line`` to ``end_line`` (1-based, inclusive). Without a range a
        file that fits READ_BUDGET comes back whole, as before; a bigger one comes back from line 1 as far as the budget
        goes, marked partial with where to read on. A range is clipped to the budget the same way. Never raises for a
        range past the end: it says the file's length instead."""
        data = self.snapshot_bytes(snapshot)
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            raise Rejected('This file isn’t UTF-8 text, so it can’t be read as lines.') from None
        lines = text.splitlines(keepends=True)
        total = len(lines)
        ranged = start_line is not None or end_line is not None
        if not ranged and self._spelled(text) <= READ_BUDGET:
            return dict(content=text, sha256=snapshot['sha256'], total_lines=total)
        first = 1 if start_line is None else start_line
        last = total if end_line is None else min(end_line, total)
        if first > max(total, 1) or (end_line is not None and end_line < first):
            raise Rejected(f'The file has {total} lines. Ask for a range inside 1 to {total}.')
        chosen, size = [], 0
        for number in range(first, last + 1):
            line = lines[number - 1]
            size += self._spelled(line)
            if size > READ_BUDGET:
                break
            chosen.append(line)
        if not chosen and first <= last:
            raise Rejected(f'Line {first} is longer than one read can return ({READ_BUDGET} bytes), so Dervo can’t show it.')
        shown = first + len(chosen) - 1
        result = dict(content=''.join(chosen), sha256=snapshot['sha256'], total_lines=total, start_line=first, end_line=shown)
        if shown < total and (end_line is None or shown < end_line):
            result['partial'] = True
            result['next'] = f'Lines {first} to {shown} of {total}. Read on with start_line {shown + 1}.'
        return result

    @staticmethod
    def _spelled(text):
        """Bytes ``text`` takes inside the JSON reply (quotes, backslashes and control characters grow when escaped)."""
        return len(json.dumps(text, ensure_ascii=False).encode('utf-8')) - 2

    def edit_file(self, task_id, path, *, expected_sha256, old, new, **context):
        """Swap one exact, unique piece of text for another (dervo_edit_file). The file must still have
        ``expected_sha256``, and ``old`` must occur in it exactly once. The change is then an ordinary attempt (prepare),
        which checks the hash again on the open file, so a file changed in between is refused, never merged."""
        if not isinstance(old, bytes) or not old or not isinstance(new, bytes):
            raise Rejected('An edit needs the exact text to replace and its replacement')
        task = self.read(task_id, **context)
        if path not in self._scoped(task) and not self._admits(task, path):
            raise Rejected('File is outside task scope')
        data = self._bytes(task, path)
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise Rejected('Stale file preimage: the file changed since you read it. Read it again, then edit.')
        count = data.count(old)
        if count == 0:
            raise Rejected('The text to replace isn’t in the file. Read the lines again and copy them exactly, including spaces.')
        if count > 1:
            raise Rejected(f'The text to replace occurs {count} times. Include more of the surrounding lines so it occurs once.')
        content = data.replace(old, new, 1)
        if len(content) > FILE_LIMIT:
            raise Rejected('The edited file would be over 16 MiB')
        return self.prepare(task_id, changes={path: dict(expected_sha256=expected_sha256, content=content)}, **context)

    def reconcile(self, task_id, **context):
        """Inspect an interrupted attempt and report the exact safe next action, without replaying anything.

        Only one case is settled automatically, because only that case is provable from the host's own records: an
        interrupted creation whose target is absent. The intent was persisted but nothing reached disk, so the phantom
        path leaves ``created`` and the attempt ends ``blocked`` with that reason. A target that is present is left
        exactly as found: the host cannot tell its own interrupted write from content a person put there later, so the
        attempt stays uncertain, the file is never removed or overwritten, and the report names the manual step."""
        task = self.read(task_id, **context)
        if not task['attempts'] or task['attempts'][-1]['state'] not in ('uncertain', 'running'):
            return dict(action='none', detail='No interrupted attempt. Nothing to reconcile.', attempt=None)
        attempt = task['attempts'][-1]
        if attempt['state'] == 'running':
            return dict(action='wait', detail='The attempt is still running. Reconcile after it ends.', attempt=attempt['number'])
        if attempt['kind'] == 'restoration':
            path = attempt['restores']['path']
            return dict(action='inspect', attempt=attempt['number'], path=path,
                        detail=f'Restoration of {path} has an uncertain outcome. Compare its recorded before, intended '
                               'after and current bytes by hand. This inspection cannot prove which process wrote the '
                               'current bytes, so the event stays uncertain and further writes in this task stay blocked. '
                               'No bytes are replayed and no external effects are reversed.')
        if attempt['kind'] != 'create':
            return dict(action='inspect', attempt=attempt['number'],
                        detail='An interrupted change attempt may have written some files. Compare the recorded before '
                               'snapshots with the current files and correct them by hand; nothing is replayed.')
        path = attempt['created']
        parts = self._relative(path)
        with self._task_directory(task, parts[:-1]) as dir_fd:
            try:
                platform_os.stat_at(parts[-1], dir_fd)
            except FileNotFoundError:
                present = False
            except OSError as exc:
                raise Rejected('Safe file access failed: ' + str(exc)) from exc
            else:
                present = True
        if not present:
            attempt.update(state='blocked', blocker='Creation was interrupted before anything reached disk: the target is absent. '
                                                    'The recorded intent was dropped; nothing was replayed.')
            if path in task['created']:
                task['created'].remove(path)
            self._save(task)
            return dict(action='dropped_absent_creation', attempt=attempt['number'], path=path,
                        detail='Target absent after the interruption. The phantom created path was removed from scope; '
                               'the task may continue. Nothing was written.')
        return dict(action='inspect', attempt=attempt['number'], path=path,
                    detail=f'{path} exists after the interruption. The host cannot tell its own interrupted write from content '
                           'placed there later, so the attempt stays uncertain and the file is kept untouched. Inspect it; '
                           'to continue this task, move the file aside yourself and reconcile again.')

    def _scope(self, task, files, test_file, check_profile=None):
        """Bind the granted files and one root test file to a check profile. The profile's runtime is discovered here, so a
        missing runtime refuses the scope instead of failing later or falling back to another executable."""
        profile_id = check_profiles.profile(check_profile if check_profile is not None else task.get('check_profile'))
        manual = profile_id == check_profiles.MANUAL
        minimum = 0 if manual and (task.get('project_access') or task.get('folders')) else 1
        if not isinstance(files, list) or not minimum <= len(files) <= 32 or len(set(files)) != len(files):
            raise Rejected('Scope needs 1 to 32 distinct existing relative files')
        if not manual and (not isinstance(test_file, str) or not check_profiles.test_pattern(profile_id).fullmatch(test_file) or test_file not in files):
            raise Rejected('Select one explicit root test file for the ' + profile_id + ' check profile, among the granted files')
        if task.get('rust_toolchain') is not None and profile_id not in check_profiles.RUST_PROFILES:
            raise Rejected('A Rust toolchain can only be selected for the Rust check')
        argv = check_profiles.argv_for(profile_id, test_file, rust_toolchain=task.get('rust_toolchain'))
        task['files'] = sorted(files)
        for path in files:
            snapshot = self._snapshot(task, path)
            if profile_id == check_profiles.TYPESCRIPT and path == test_file:
                check_profiles.validate_tsconfig(self.snapshot_bytes(snapshot))
        task['check_profile'] = profile_id
        task['argv'] = argv
        if profile_id in check_profiles.RUST_PROFILES:
            import rust_checks
            selected = rust_checks.toolchain_root(task.get('rust_toolchain'))
            if selected == Path(task['root']).resolve() or Path(task['root']).resolve() in selected.parents:
                raise Rejected('Choose a trusted Rust toolchain outside the project folder')
            import rust_tests
            module = rust_tests if profile_id == check_profiles.RUST_EXEC else rust_checks
            task['compiler'] = module.identity(str(selected))
        elif profile_id == check_profiles.TYPESCRIPT:
            # The project's own compiler is resolved, bounded and read now, so a missing or escaping compiler refuses the scope.
            # Its identity (package, version, input sizes and the sha256 of every input's bytes) is recorded; launch re-reads
            # the inputs and refuses any whose content differs, so a same-size replacement is never checked with.
            task['compiler'] = check_profiles.typescript_identity(task['root'])
        else:
            task.pop('compiler', None)

    def scope(self, task_id, *, files, test_file, check_profile=None, **context):
        task = self.read(task_id, **context)
        if task.get('read_only'):
            raise Rejected('Project reading cannot edit files or run checks')
        self._scope(task, files, test_file, check_profile)
        task['scope_version'] += 1
        task['approval'] = task['review'] = None
        return self._save(task)

    def approve(self, task_id, *, scope_version, **context):
        task = self.read(task_id, **context)
        if scope_version != task['scope_version']:
            raise Rejected('Approval refers to stale scope')
        task['approval'] = dict(scope_version=scope_version, actor=context['actor'])
        return self._save(task)

    def read(self, task_id, *, thread_id, project_id, actor, rebound=False):
        """``rebound`` reads the record of a task whose conversation has since moved to another account or model (the
        conversation's own choice, or Auto). Listing, inspecting and recording a lost run as stopped pass it. Execution,
        approval, restoration and commit still read strictly under the original account and model."""
        binding = self.store.binding(thread_id, project_id, actor=actor)
        row = self.store.db.execute('SELECT document FROM task_evidence WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise Rejected('Unknown task')
        task = json.loads(row[0])
        expected = dict(thread_id=thread_id, project_id=project_id, actor=actor,
                        profile_id=binding['profile_id'], model=binding['model'], root=binding['cwd'],
                        visibility=binding['visibility'])
        if rebound:
            expected.pop('profile_id')
            expected.pop('model')
        if not actor or any(task[k] != v for k, v in expected.items()):
            raise Rejected('Task binding changed or access is outside task scope')
        task.setdefault('folders', [])   # tasks recorded before folder grants existed: file-only, nothing widened
        task.setdefault('created', [])
        task.setdefault('admitted', [])
        task.setdefault('check_profile', check_profiles.PYTHON)   # tasks recorded before profiles existed: Python unittest, unchanged argv
        task['check_timeout_seconds'] = check_profiles.check_timeout(task.get('check_timeout_seconds', 5))
        if task.get('project_access'):
            with self._task_directory(task, []):
                pass
        if task['review'] and not self._current(task, task['attempts'][-1]):
            task['review'] = None
            self._save(task)
        return task

    @staticmethod
    def canary_source(profile_id, secret, write, port=None):
        """Host-authored probe for one profile: it must prove the read, write, network, fork and environment denials."""
        if profile_id == check_profiles.PYTHON and platform_os.WINDOWS:
            # Windows has no fork, and a blocked connect times out rather than being refused. So the host listens on
            # ``port`` and checks afterwards that nothing reached it; a timeout here only counts with that. The user's
            # registry and the container's own TEMP are the Windows places a check could otherwise read or write.
            return 'test_confinement.py', f'''import os, socket, subprocess, sys, unittest, winreg
class Confinement(unittest.TestCase):
 def test_denials(self):
  for path, mode in [({str(secret)!r}, 'r'), ({str(write)!r}, 'w'), ('local-write', 'w'), (os.path.join(os.environ['TEMP'], 'write-canary'), 'w')]:
   with self.assertRaises(PermissionError):
    with open(path, mode): pass
  with self.assertRaises(PermissionError): winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
  with socket.socket() as sock:
   sock.settimeout(2)
   with self.assertRaises(OSError): sock.connect(('127.0.0.1', {int(port)}))
  with self.assertRaises(OSError): subprocess.run([sys.executable, '-c', '0'])
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  print('host-canaries-denied')
'''
        if profile_id != check_profiles.PYTHON and platform_os.LINUX:
            # Node under bubblewrap: the same proofs as the Linux Python canary below, in Node's words.
            return 'test_confinement.mjs', f'''import assert from 'node:assert/strict';
import {{test}} from 'node:test';
import fs from 'node:fs';
import net from 'node:net';
import {{spawnSync}} from 'node:child_process';
test('host denials', async () => {{
  // Inside, the canary paths don't exist (ENOENT) and the snapshot is read-only (EROFS). Ordinary permissions answer
  // EACCES, which test_check_profiles' unconfined control relies on: the network probe is the first that tells the two apart.
  assert.throws(() => fs.readFileSync({json.dumps(str(secret))}), /ENOENT|EACCES/);
  assert.throws(() => fs.writeFileSync({json.dumps(str(write))}, 'x'), /ENOENT|EROFS|EACCES/);
  assert.throws(() => fs.writeFileSync('local-write', 'x'), /EROFS|EACCES/);
  // The seccomp filter refuses the socket itself, so the error is EPERM, never ECONNREFUSED from a real attempt.
  await new Promise((resolve, reject) => {{ const s = net.connect(9, '127.0.0.1'); s.on('error', e => {{ try {{ assert.match(String(e.code), /^(EPERM|EACCES)$/, 'network connect was not denied by policy'); resolve(); }} catch (err) {{ reject(err); }} }}); s.on('connect', () => {{ reject(new Error('network allowed')); }}); }});
  assert.throws(() => fs.writeFileSync('/tmp/write-canary', 'x'), /ENOENT|EROFS/);
  assert.equal(fs.existsSync('/home') || fs.existsSync('/etc/passwd'), false);
  const child = spawnSync(process.execPath, ['-e', '0']);
  assert.ok(child.error || child.status !== 0, 'child process allowed');
  assert.equal(process.env.HOME, '');
  assert.equal(process.env.PATH, undefined);
  console.log('host-canaries-denied');
}});
'''
        if profile_id == check_profiles.PYTHON and platform_os.LINUX:
            # Under bubblewrap the canary paths and the host's /tmp don't exist inside (FileNotFoundError), the snapshot is
            # a read-only mount (EROFS), and the seccomp filter refuses an internet socket and a fork (EPERM).
            return 'test_confinement.py', f'''import os, socket, subprocess, sys, unittest
class Confinement(unittest.TestCase):
 def test_denials(self):
  for path, mode in [({str(secret)!r}, 'r'), ({str(write)!r}, 'w'), ('local-write', 'w'), ('/tmp/write-canary', 'w'), ('/write-canary', 'w')]:
   with self.assertRaises(OSError):
    with open(path, mode): pass
  self.assertFalse(os.path.exists('/home') or os.path.exists('/etc/passwd'))
  for family in (socket.AF_INET, socket.AF_INET6):
   with self.assertRaises(PermissionError): socket.socket(family, socket.SOCK_STREAM)
  with self.assertRaises(PermissionError): os.fork()
  with self.assertRaises(OSError): subprocess.run([sys.executable, '-c', '0'])
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  print('host-canaries-denied')
'''
        if profile_id == check_profiles.PYTHON:
            return 'test_confinement.py', f'''import os, socket, unittest
class Confinement(unittest.TestCase):
 def test_denials(self):
  for path, mode in [({str(secret)!r}, 'r'), ({str(write)!r}, 'w'), ('local-write', 'w')]:
   with self.assertRaises(PermissionError):
    with open(path, mode): pass
  with socket.socket() as sock:
   with self.assertRaises(PermissionError): sock.connect(('127.0.0.1', 9))
  with self.assertRaises(PermissionError): os.fork()
  self.assertEqual(os.environ.get('HOME'), '')
  self.assertNotIn('PATH', os.environ)
  print('host-canaries-denied')
'''
        return 'test_confinement.mjs', f'''import assert from 'node:assert/strict';
import {{test}} from 'node:test';
import fs from 'node:fs';
import net from 'node:net';
import {{spawnSync}} from 'node:child_process';
test('host denials', async () => {{
  assert.throws(() => fs.readFileSync({json.dumps(str(secret))}), /EPERM|EACCES/);
  assert.throws(() => fs.writeFileSync({json.dumps(str(write))}, 'x'), /EPERM|EACCES/);
  assert.throws(() => fs.writeFileSync('local-write', 'x'), /EPERM|EACCES/);
  // Only a permission denial proves the network policy. ECONNREFUSED is what an unconfined connect to a closed port
  // returns, so accepting it would let the self-test pass with no sandbox at all.
  await new Promise((resolve, reject) => {{ const s = net.connect(9, '127.0.0.1'); s.on('error', e => {{ try {{ assert.match(String(e.code), /^(EPERM|EACCES)$/, 'network connect was not denied by policy'); resolve(); }} catch (err) {{ reject(err); }} }}); s.on('connect', () => {{ reject(new Error('network allowed')); }}); }});
  const child = spawnSync(process.execPath, ['-e', '0']);
  assert.ok(child.error || child.status !== 0, 'child process allowed');
  assert.equal(process.env.HOME, '');
  assert.equal(process.env.PATH, undefined);
  console.log('host-canaries-denied');
}});
'''

    @staticmethod
    def _verify(task, snapshots, cancel=None, compiler=None):
        timeout = check_profiles.check_timeout(task.get('check_timeout_seconds', 5))
        if task.get('check_profile') in check_profiles.RUST_PROFILES:
            import rust_checks, rust_tests
            module = rust_tests if task['check_profile'] == check_profiles.RUST_EXEC else rust_checks
            return dict(module.verify(task, snapshots, cancel, compiler, timeout=timeout), check_timeout_seconds=timeout)
        # Prove policy enforcement with host-authored code before any project code.
        profile_id = check_profiles.profile_of(task['argv'])
        with tempfile.TemporaryDirectory(prefix='dervo-canary-') as temp:
            base = Path(temp).resolve()
            root = base / 'target'
            root.mkdir()
            secret = base / 'read-canary'
            secret.write_bytes(b'host canary')
            write = base / 'write-canary'
            with ExitStack() as stack:
                listener = None
                if platform_os.WINDOWS:
                    import socket
                    listener = stack.enter_context(socket.socket())
                    listener.bind(('127.0.0.1', 0))  # loopback only, which Windows Firewall never asks about
                    listener.listen()
                    listener.setblocking(False)
                name, source = TaskEvidence.canary_source(profile_id, secret, write, listener.getsockname()[1] if listener else None)
                (root / name).write_text(source, encoding='utf-8')  # a non-ASCII user name can be in the canary paths
                # The TypeScript check runs under the Node sandbox policy, so the Node canary proves it with the same runtime.
                canary_argv = [*task['argv'][:task['argv'].index(check_profiles.TS_ENTRY)], name] if profile_id == check_profiles.TYPESCRIPT else task['argv'][:-1] + [name]
                probe = _run_confined(root, canary_argv, cancel=cancel, timeout=10 if listener else 5)
                reached = False
                if listener:
                    try:
                        listener.accept()[0].close()
                        reached = True
                    except BlockingIOError:
                        pass
            if probe['exit_status'] != 0 or probe['stopped'] or 'host-canaries-denied' not in probe['stdout'] or write.exists() or reached:
                raise ConfinementBlocked('Confinement self-test failed: ' + json.dumps(dict({k: probe[k] for k in ('exit_status', 'stopped', 'stdout', 'stderr')}, **({'network_reached': True} if reached else {}))))
        # Project data, profiles and unlisted files never enter the sandbox target.
        with tempfile.TemporaryDirectory(prefix='dervo-verification-') as temp:
            root = Path(temp).resolve()
            if profile_id == check_profiles.TYPESCRIPT:
                if 'tsconfig.json' not in snapshots or compiler is None:
                    raise Rejected('TypeScript check needs the root tsconfig.json snapshot and the validated project compiler inputs')
                check_profiles.validate_tsconfig(base64.b64decode(snapshots['tsconfig.json']['bytes_b64']))
                for path, data in compiler['files'].items():
                    target = root / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
            for path, snapshot in snapshots.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(base64.b64decode(snapshot['bytes_b64']))
            if profile_id == check_profiles.TYPESCRIPT:
                content = dict(compiler['files'])
                for path, snapshot in snapshots.items():
                    data = base64.b64decode(snapshot['bytes_b64'])
                    if path in content and content[path] != data:
                        raise Rejected('Read-only TypeScript input differs from its approved hash: ' + path)
                    content[path] = data
                typescript_inputs.validate_snapshot(content)
            result = _run_confined(root, task['argv'], cancel=cancel, timeout=timeout)
            result['check_timeout_seconds'] = timeout
            if compiler is not None:
                result['compiler'] = compiler['identity']
            return result

    @staticmethod
    def _compiler_inputs(task):
        """Host-thread read of the recorded compiler's finite inputs for one verification. The bytes read here are the bytes
        copied into the snapshot, and their sha256 must equal what the scope bound: a compiler whose version, input set or
        any input's content differs refuses the run instead of silently checking with something else. A scope recorded
        without content hashes is refused too, so it gets bound again rather than trusted on sizes."""
        if task['check_profile'] in check_profiles.RUST_PROFILES:
            import rust_checks, rust_tests
            module = rust_tests if task['check_profile'] == check_profiles.RUST_EXEC else rust_checks
            current = module.identity(task.get('rust_toolchain'))
            if current != task.get('compiler'):
                raise Rejected('Rust compiler/sysroot changed since scope approval')
            return dict(identity=current)
        if task['check_profile'] != check_profiles.TYPESCRIPT:
            return None
        inputs = check_profiles.typescript_snapshot(task['root'])
        content = inputs.pop('content')
        if not task.get('compiler') or 'sha256' not in task['compiler'] or inputs != task['compiler']:
            raise Rejected('The project TypeScript compiler changed since the scope was approved (or the scope predates content '
                           'hashing). Set the scope again to bind the current compiler.')
        return dict(identity=inputs, files=content)

    def prepare(self, task_id, *, changes, **context):
        """Apply existing-file corrections and obtain evidence. No supplied results.

        changes is {relative_file: {expected_sha256: hex, content: bytes}}.
        File writes aren't a multi-file filesystem transaction. An interrupted
        attempt stays uncertain and must be inspected by the host, never replayed.
        """
        task = self.read(task_id, **context)
        if task.get('read_only'):
            raise Rejected('Project reading cannot edit files or run checks')
        if not task['approval'] or task['approval']['scope_version'] != task['scope_version']:
            raise Rejected('Task scope needs explicit human approval')
        if task['attempts'] and task['attempts'][-1]['state'] in ('uncertain', 'running'):
            raise Rejected('Interrupted attempt requires host inspection, not automatic retry')
        if not isinstance(changes, dict) or not changes:
            raise Rejected('An attempt needs changes')
        self._room_for_attempt(task, context)
        for path, change in changes.items():
            if path in self._scoped(task) or not self._admits(task, path):
                continue
            # A discovered file written for the first time enters evidence only with a matching preimage: a stale hash admits nothing.
            if not isinstance(change, dict) or self._snapshot(task, path)['sha256'] != change.get('expected_sha256'):
                raise Rejected('Stale file preimage')
            self._admit(task, path)
        scoped = self._scoped(task)
        before = {p: self._snapshot(task, p) for p in scoped}
        with ExitStack() as stack:
            opened = {}
            for path, change in changes.items():
                self._relative(path)
                if path not in before or not isinstance(change, dict) or set(change) != {'expected_sha256', 'content'}:
                    raise Rejected('Change is outside scope or has unsupported fields')
                if not isinstance(change['content'], bytes) or len(change['content']) > FILE_LIMIT:
                    raise Rejected('Change content must be bytes up to 16 MiB')
                fd = stack.enter_context(self._file(task, path, writable=True))
                actual = _read_all(fd, FILE_LIMIT)
                if hashlib.sha256(actual).hexdigest() != change['expected_sha256'] or before[path]['sha256'] != change['expected_sha256']:
                    raise Rejected('Stale file preimage')
                opened[path] = fd
            attempt = dict(number=len(task['attempts']) + 1, scope_version=task['scope_version'], kind='change',
                           files=list(scoped), argv=list(task['argv']), check_timeout_seconds=task['check_timeout_seconds'], before=before,
                           after=None, diff={}, state='uncertain', test=None, blocker=None)
            task['attempts'].append(attempt)
            task['review'] = None
            self._save(task)
            for path, fd in opened.items():
                os.lseek(fd, 0, os.SEEK_SET)
                data = memoryview(changes[path]['content'])
                while data:
                    size = os.write(fd, data)
                    if not size:
                        raise Rejected('File write made no progress')
                    data = data[size:]
                os.ftruncate(fd, len(changes[path]['content']))
                os.fsync(fd)
        after = {p: self._snapshot(task, p) for p in scoped}
        attempt['after'] = after
        for path in scoped:
            if before[path] == after[path]:
                attempt['diff'][path] = ''
                continue
            left = self.snapshot_bytes(before[path])
            right = self.snapshot_bytes(after[path])
            if max(len(left), len(right)) > INLINE_LIMIT:
                try:
                    attempt['diff'][path] = _line_diff(path, left.decode('utf-8'), right.decode('utf-8'))
                except UnicodeDecodeError:
                    attempt['diff'][path] = (f'Binary diff not kept for a file over 256 KiB: sha256 {before[path]["sha256"]} '
                                             f'({len(left)} bytes) became {after[path]["sha256"]} ({len(right)} bytes).\n')
                continue
            try:
                left.decode('utf-8')
                right.decode('utf-8')
            except UnicodeDecodeError:
                # Hex preserves every byte when either snapshot isn't UTF-8.
                hex_diff = ''.join(difflib.unified_diff(
                    [left[i:i + 16].hex(' ') + '\n' for i in range(0, len(left), 16)],
                    [right[i:i + 16].hex(' ') + '\n' for i in range(0, len(right), 16)],
                    fromfile='before/' + path, tofile='after/' + path))
                attempt['diff'][path] = 'Binary diff (hex bytes):\n' + hex_diff if hex_diff else ''
            else:
                lines = difflib.diff_bytes(difflib.unified_diff, left.splitlines(keepends=True),
                        right.splitlines(keepends=True), fromfile=('before/' + path).encode(), tofile=('after/' + path).encode())
                attempt['diff'][path] = b''.join(
                    line if line.endswith(b'\n') else line + b'\n\\ No newline at end of file\n'
                    for line in lines).decode('utf-8')
        attempt['state'] = 'prepared'
        self._freeze_check_inputs(task, attempt)
        self._save(task)
        return attempt

    def restoration_target(self, task_id, *, attempt_number, scope_version, path,
                           expected_hash, expected_attempts, **context):
        """Validate a human-selected recorded edit. The renderer supplies no replacement bytes."""
        task = self.read(task_id, **context)
        if (type(attempt_number) is not int or type(scope_version) is not int
                or type(expected_attempts) is not int or type(expected_hash) is not str):
            raise Rejected('Restoration needs exact attempt, scope version and after hash')
        if scope_version != task['scope_version'] or task['approval'] != {
                'scope_version': scope_version, 'actor': context['actor']}:
            raise Rejected('Restoration needs current explicit scope approval')
        if expected_attempts != len(task['attempts']):
            raise Rejected('The recorded file version changed. Inspect the result again')
        if any(a['state'] in ('uncertain', 'running') for a in task['attempts']) or self.busy():
            raise Rejected('An uncertain write or active check needs reconciliation before restoration')
        if not 1 <= attempt_number <= len(task['attempts']):
            raise Rejected('Select a recorded edit')
        self._room_for_attempt(task, context)
        source = task['attempts'][attempt_number - 1]
        self._relative(path)
        if source['scope_version'] != scope_version or path not in self._scoped(task):
            raise Rejected('The recorded edit is outside the current scope version')
        before, after = (source.get('before') or {}).get(path), (source.get('after') or {}).get(path)
        if before is None:
            raise Rejected('Created-file restoration is unsupported. No file will be deleted')
        if source.get('kind', 'change') != 'change' or not after or before == after:
            raise Rejected('Select a recorded existing-file edit with confirmed after bytes')
        if expected_hash != after['sha256'] or self._snapshot(task, path) != after:
            raise Rejected('The file changed after this edit. Later edits are kept')
        if any(a.get('restores') == {'attempt': attempt_number, 'path': path} for a in task['attempts']):
            raise Rejected('This edit already has a restoration event. Inspect its recorded outcome')
        raise Rejected("File restoration is unavailable because Dervo can't guarantee preservation of concurrent external edits. Inspect the recorded diff and snapshots.")

    def restore_file(self, task_id, *, attempt_number, scope_version, path,
                     expected_hash, expected_attempts, **context):
        """Fail closed: the existing write path can overwrite a noncooperating writer.

        Keep this rejection at the evidence boundary so direct renderer requests can't
        reach a write, even if availability validation is bypassed. Historical events
        remain readable through the existing inspection and reconciliation paths.
        """
        raise Rejected("File restoration is unavailable because Dervo can't guarantee preservation of concurrent external edits. Inspect the recorded diff and snapshots.")

    def _freeze_check_inputs(self, task, attempt):
        if not task.get('project_access') or task.get('check_profile') == check_profiles.MANUAL:
            return
        try:
            inputs = project_access.collect(task, task['argv'][-1])
            attempt['check_inputs'] = {
                path: dict(sha256=hashlib.sha256(data).hexdigest(), bytes_b64=base64.b64encode(data).decode())
                for path, data in inputs.items() if path not in attempt['after']}
        except Rejected as exc:
            attempt['check_inputs'] = {}
            attempt['check_input_blocker'] = str(exc)

    def verify_current(self, task_id, **context):
        """A verification-only attempt: the current scoped files, unchanged, become this
        attempt's snapshots and the host test command runs against them in confinement.

        No source bytes are written and no edit is claimed (kind 'verification', empty
        diff, before equals after). The result belongs to this attempt alone, so an
        earlier passing run is never inherited. Approval, scope, interrupted-attempt and
        attempt-count rules are the same as for a file change.
        """
        task = self.read(task_id, **context)
        if task.get('read_only'):
            raise Rejected('Project reading cannot edit files or run checks')
        if not task['approval'] or task['approval']['scope_version'] != task['scope_version']:
            raise Rejected('Task scope needs explicit human approval')
        if task['attempts'] and task['attempts'][-1]['state'] in ('uncertain', 'running'):
            raise Rejected('Interrupted attempt requires host inspection, not automatic retry')
        self._room_for_attempt(task, context)
        scoped = self._scoped(task)
        snapshots = {p: self._snapshot(task, p) for p in scoped}
        attempt = dict(number=len(task['attempts']) + 1, scope_version=task['scope_version'], kind='verification',
                       files=list(scoped), argv=list(task['argv']), check_timeout_seconds=task['check_timeout_seconds'], before=snapshots,
                       after={p: dict(s) for p, s in snapshots.items()}, diff={}, state='prepared', test=None, blocker=None)
        task['attempts'].append(attempt)
        task['review'] = None
        self._freeze_check_inputs(task, attempt)
        self._save(task)
        return attempt

    def busy(self):
        """Whether a verification job is running. The coordinator queues a second test request behind it."""
        return any(not job['done'] for job in self._jobs.values())

    def launch(self, task_id, *, attempt_number, **context):
        """Nonblocking host call. The worker gets immutable JSON, never Store."""
        task = self.read(task_id, **context)
        if task.get('check_profile') == check_profiles.MANUAL:
            raise Rejected('No automated check is configured. Review the recorded changes yourself.')
        if task.get('read_only'):
            raise Rejected('Project reading cannot edit files or run checks')
        if self.busy():
            raise Rejected('One verification job per host at a time')
        if not task['attempts']:
            raise Rejected('Prepare a file attempt before verification')
        attempt = task['attempts'][-1]
        if (attempt['number'] != attempt_number or attempt['state'] != 'prepared'
                or not task['approval'] or task['approval']['scope_version'] != task['scope_version']
                or not self._current(task, attempt, runtime=False)):
            raise Rejected('Verification needs a current prepared attempt and approved scope')
        if attempt.get('check_input_blocker'):
            attempt.update(state='blocked', blocker=attempt['check_input_blocker'])
            self._save(task)
            raise Rejected(attempt['blocker'])
        compiler = self._compiler_inputs(task)  # read on the host thread, bounded and validated; None for other profiles
        job_id = str(uuid.uuid4())
        cancel, results = threading.Event(), queue.Queue(maxsize=1)
        # Bytes are immutable and include only allowlisted host snapshots, argv and the validated compiler inputs.
        # A file kept beside the database is read back here, on the host thread, and checked against its sha256.
        after = {path: self.hydrated(snap) for path, snap in attempt['after'].items()}
        payload = json.dumps({'argv': task['argv'], 'check_profile': task['check_profile'], 'check_timeout_seconds': task['check_timeout_seconds'], 'after': dict(after, **attempt.get('check_inputs', {}))}).encode()
        def run():
            inputs = json.loads(payload)
            try:
                result = dict(test=TaskEvidence._verify(inputs, inputs['after'], cancel, compiler), blocker=None)
            except Exception as exc:
                result = dict(test=None, blocker=check_blocker(exc))
            results.put(json.dumps(result).encode())
        thread = threading.Thread(target=run, name='dervo-confined-test', daemon=False)
        self._jobs[job_id] = dict(task_id=task_id, number=attempt_number, cancel=cancel,
                                 results=results, thread=thread, done=False)
        attempt.update(state='running', job_id=job_id)
        self._save(task)
        thread.start()
        return job_id

    def poll(self, task_id, *, job_id, **context):
        """Read and persist internally produced results on the Store owner thread."""
        task = self.read(task_id, **context)
        job = self._jobs.get(job_id)
        if not job or job['task_id'] != task_id:
            raise Rejected('Unknown task job, or host restarted before confirmation')
        attempt = task['attempts'][job['number'] - 1]
        if job['done']:
            return attempt
        try:
            result = json.loads(job['results'].get_nowait())
        except queue.Empty:
            return attempt
        job['thread'].join()
        job['done'] = True
        attempt.update(result)
        attempt['state'] = ('cancelled' if job['cancel'].is_set() else
                            'blocked' if result['blocker'] else 'verified')
        self._save(task)
        return attempt

    def cancel(self, task_id, *, job_id, **context):
        self.read(task_id, **context)
        job = self._jobs.get(job_id)
        if not job or job['task_id'] != task_id:
            raise Rejected('Unknown task job')
        job['cancel'].set()

    def attempt(self, task_id, *, changes, **context):
        """Synchronous convenience for tests. UI hosts use prepare/launch/poll."""
        attempt = self.prepare(task_id, changes=changes, **context)
        job_id = self.launch(task_id, attempt_number=attempt['number'], **context)
        while True:
            attempt = self.poll(task_id, job_id=job_id, **context)
            if attempt['state'] != 'running':
                return attempt
            time.sleep(0.01)

    def evidence(self, task_id, **context):
        return self.read(task_id, **context)['attempts']

    def _current(self, task, attempt, *, runtime=True, complete_scope=True):
        if (attempt['scope_version'] != task['scope_version'] or attempt['after'] is None
                or not set(attempt['after']).issubset(self._scoped(task))
                or (complete_scope and set(attempt['after']) != set(self._scoped(task)))
                or attempt.get('check_timeout_seconds', 5) != task.get('check_timeout_seconds', 5)):
            return False
        try:
            if runtime:
                self._compiler_inputs(task)
            return (all(self._snapshot(task, p) == snap for p, snap in attempt['after'].items())
                    and all(self._snapshot(task, p) == snap for p, snap in attempt.get('check_inputs', {}).items()))
        except Rejected:
            return False

    def review(self, task_id, *, attempt_number, decision, **context):
        task = self.read(task_id, **context)
        if not task['attempts'] or decision not in ('approved', 'rejected'):
            raise Rejected('Review needs an explicit decision and an attempt')
        attempt = task['attempts'][-1]
        manual = (task.get('check_profile') == check_profiles.MANUAL and attempt['state'] == 'unverified'
                  and attempt.get('kind') == 'manual-review' and attempt.get('test') is None and attempt.get('argv') == [])
        if attempt['number'] != attempt_number or not self._current(task, attempt) or (not manual and attempt['state'] not in ('verified', 'blocked', 'cancelled')):
            raise Rejected('Review requires current completed evidence and unchanged files')
        if decision == 'approved' and attempt.get('unrecorded'):
            # The files and reasons are listed on the result itself; the refusal stays one line (beta.15).
            count = len(attempt['unrecorded'])
            raise Rejected(f'Dervo couldn’t record every change in this result, so it can’t be approved. '
                           f'{count} {"file is" if count == 1 else "files are"} listed on the result.')
        if decision == 'approved' and not manual and (attempt['state'] != 'verified' or attempt['test']['exit_status'] != 0 or attempt['test']['stopped']):
            raise Rejected('Approval requires successful actual confined verification')
        task['review'] = dict(attempt_number=attempt_number, scope_version=task['scope_version'], decision=decision, actor=context['actor'])
        if manual:
            task['review']['checks'] = 'not_run'
        self._save(task)
        return task['review']

    def export(self, task_id, **context):
        self.read(task_id, **context)
        raise Rejected('Task evidence export is disabled, including private tasks')
