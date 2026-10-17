"""Project-folder boundary and bounded, static inputs for confined source checks.

This module never imports or executes project code. Check inputs aren't task edits.
"""
import ast
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import time

from dervo_connections import Rejected
import platform_os

MAX_FILE = 262144        # one automatic check input
MAX_TASK_FILE = 16777216  # one file a task reads or changes (big files in scoped mode, beta.15); task_evidence.FILE_LIMIT
MAX_FILES = 128
MAX_BYTES = 8388608

# The one file-name rule for reading, writing and full-access recording (TaskEvidence._relative uses it too).
# 16.2 (Bryce, 27 Sep): the old rule refused any name whose first word (split on . _ -) was data, auth, profile, token,
# secret, credential or tooling, so data.js, docs/data-model.md and src/tokens.css came back as "a hidden, secret or
# generated file". Now it matches real secret shapes, a generated folder counts only when git ignores it, and each
# refusal says which rule it was. The reasons go into unrecorded rows as they are, so none holds a parenthesis.
HIDDEN = 'A hidden file or folder, its name starts with a dot'
SECRET_FOLDER = 'Inside a secrets folder'
SECRET_FILE = 'Named like a credentials or secrets file'
KEY_FILE = 'A private key or certificate store'
DATABASE = 'A database file'
IGNORED = 'Inside a folder git ignores, like build output or dependencies'
GENERATED_FOLDER = 'Inside a build or dependency folder'
SECRET_DIRS = {'secret', 'secrets', 'credentials', 'private_keys'}
SECRET_STEMS = {'credentials', 'secret', 'secrets', 'client_secret', 'token'}  # with a config-style extension only
SECRET_WORDS = {'token', 'secret', 'secrets', 'credential', 'credentials', 'password', 'passwords', 'apikey'}
SECRET_EXTS = {'', 'json', 'yml', 'yaml', 'toml', 'env', 'ini', 'xml', 'txt', 'conf', 'cfg', 'properties'}
SECRET_NAMES = {'auth.json', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'known_hosts', 'authorized_keys', 'htpasswd'}
KEY_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.ppk', '.jks', '.keystore', '.kdbx')
DB_SUFFIXES = ('.sqlite', '.sqlite3', '.db', '.db-wal', '.db-shm', '.db-journal', '.sqlite-wal', '.sqlite-shm',
               '.sqlite-journal', '.sqlite3-wal', '.sqlite3-shm', '.sqlite3-journal')
GENERATED = {'node_modules', 'target', 'dist', 'build', '__pycache__', 'venv', 'coverage'}
IGNORE_SECONDS = 5   # how long one git check-ignore answer is reused: a search asks about the same folders many times
_ignored_cache = {}


def _secret_file(low):
    stem, _, ext = low.partition('.')
    if low in SECRET_NAMES or (stem in SECRET_STEMS and ext in SECRET_EXTS):
        return True
    # auth-token.json, db_password.txt, github_secret.env: a secret word anywhere in a config-style name. Source files
    # (token_count.py, tokens.css) have other extensions and pass.
    if ext in SECRET_EXTS and set(re.split(r'[-_]', stem)) & SECRET_WORDS:
        return True
    if low.startswith(('client_secret', 'service-account', 'service_account')) and low.endswith('.json'):
        return True
    return stem.startswith(('id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519')) and not low.endswith('.pub')


def git_ignores(root, path):
    """True when git ignores ``path`` in the repository holding ``root``, or when git can't say (no repository, git
    missing): an unknown generated folder stays refused. A tracked file inside an ignored folder isn't ignored."""
    key, now = (str(root), path), time.monotonic()
    cached = _ignored_cache.get(key)
    if cached and now - cached[0] < IGNORE_SECONDS:
        return cached[1]
    import task_git
    try:
        result = task_git._raw(root, 'check-ignore', '-q', '--', path)
        ignored = result.returncode != 1
    except (OSError, subprocess.SubprocessError):
        ignored = True
    if len(_ignored_cache) > 4096:
        _ignored_cache.clear()
    _ignored_cache[key] = (now, ignored)
    return ignored


def refusal(path, root=None, *, generated=True):
    """Why ``path`` is kept out, or None. ``root`` is the project folder: with it, a build, dist, node_modules or other
    generated folder is refused only when git ignores it. Without it, such a folder is refused as before, unless
    ``generated`` is False (the scoped rule, which never refused them)."""
    if not isinstance(path, str) or not path or '\\' in path or '\x00' in path:
        return 'Invalid relative project file'
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return 'Invalid relative project file'
    for index, part in enumerate(parts):
        low = part.lower()
        folder = index < len(parts) - 1
        if part.startswith('.'):
            return HIDDEN
        if folder and low in SECRET_DIRS:
            return SECRET_FOLDER
        if not folder and _secret_file(low):
            return SECRET_FILE
        if low.endswith(KEY_SUFFIXES):
            return KEY_FILE
        if low.endswith(DB_SUFFIXES):
            return DATABASE
    if generated:
        for index, part in enumerate(parts):
            if part.lower() in GENERATED:
                if root is None:
                    return GENERATED_FOLDER
                # A trailing slash lets a "__pycache__/" pattern match a folder that isn't on disk (a deletion).
                if git_ignores(root, '/'.join(parts[:index + 1]) + ('/' if index < len(parts) - 1 else '')):
                    return IGNORED
    return None


def relative(path, root=None, *, generated=True):
    """The path's parts, or Rejected naming the rule that refused it (``refusal``)."""
    why = refusal(path, root, generated=generated)
    if why:
        raise Rejected(why)
    return path.split('/')


@contextmanager
def directory(root, parts=(), identity=None):
    """Keep the checked root descriptor open while walking descendants without links."""
    descriptors = []
    if parts:
        relative('/'.join(parts), root)
    try:
        fd = platform_os.open_root(root)
        descriptors.append(fd)
        for part in platform_os.below_root(root):
            fd = platform_os.open_dir_at(part, fd)
            descriptors.append(fd)
        info = os.fstat(fd)
        if identity is not None and not same_root(identity, [info.st_dev, info.st_ino]):
            raise Rejected('The project folder was replaced. Allow coding again for the selected folder.')
        for part in parts:
            relative(part)
            fd = platform_os.open_dir_at(part, fd)
            descriptors.append(fd)
        yield fd
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def same_root(recorded, current):
    """A folder keeps its inode across a restart but not its device number (macOS renumbers volumes at boot), so the
    inode alone tells a replaced folder apart."""
    return (isinstance(recorded, (list, tuple)) and isinstance(current, (list, tuple))
            and len(recorded) == 2 and len(current) == 2 and recorded[1] == current[1])


def root_identity(root):
    try:
        with directory(root) as fd:
            info = os.fstat(fd)
            return [info.st_dev, info.st_ino]
    except OSError as exc:
        raise Rejected('Safe project directory access failed: ' + str(exc)) from exc


@contextmanager
def file_descriptor(task, path, writable=False):
    parts = relative(path, task['root'])
    try:
        with directory(task['root'], parts[:-1], task['root_identity']) as directory_fd:
            fd = platform_os.open_file_at(parts[-1], directory_fd, 'rw' if writable else 'r')
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_TASK_FILE:
                    raise Rejected('File must be a single-link regular file up to 16 MiB')
                yield fd
            finally:
                os.close(fd)
    except OSError as exc:
        raise Rejected('Safe project file access failed: ' + str(exc)) from exc


def read_optional(task, path):
    """Only absence permits dependency resolution to try another filename."""
    try:
        with file_descriptor(task, path) as fd:
            data = os.read(fd, MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise Rejected('Check input exceeded 256 KiB')
            return data
    except Rejected as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise


def _local(source, spec):
    if '\\' in spec or spec.startswith('/') or '\x00' in spec:
        raise Rejected('Check dependency must stay inside the project: ' + spec)
    pieces = list(PurePosixPath(source).parent.parts)
    for piece in spec.split('/'):
        if piece in ('', '.'):
            continue
        if piece == '..':
            if not pieces:
                raise Rejected('Check dependency leaves the project: ' + spec)
            pieces.pop()
        else:
            pieces.append(piece)
    result = '/'.join(pieces)
    relative(result)
    return result


# Supported source syntax is deliberately narrow. Unknown package/computed inputs
# stop setup instead of granting a runtime access to the original project.
_JS_IMPORT = re.compile(r'''\b(?:import\s+(?:[^;\n]*?\s+from\s*)?|export\s+[^;\n]*?\s+from\s*)["']([^"']+)["']''')
_JS_CALL = re.compile(r'''\b(require|import)\s*\(\s*([^)]*)\)''')
_JS_URL = re.compile(r'''\bnew\s+URL\s*\(\s*(["'])([^"']+)\1\s*,\s*import\.meta\.url\s*\)''')
_LITERAL = re.compile(r'''\s*(["'])([^"']+)\1\s*''')
_NODE_BUILTINS = {'assert', 'assert/strict', 'async_hooks', 'buffer', 'child_process', 'cluster', 'console',
                  'constants', 'crypto', 'dgram', 'diagnostics_channel', 'dns', 'dns/promises', 'domain',
                  'events', 'fs', 'fs/promises', 'http', 'http2', 'https', 'module', 'net', 'os', 'path',
                  'path/posix', 'path/win32', 'perf_hooks', 'process', 'punycode', 'querystring', 'readline',
                  'readline/promises', 'repl', 'stream', 'stream/promises', 'stream/web', 'string_decoder',
                  'sys', 'test', 'timers', 'timers/promises', 'tls', 'trace_events', 'tty', 'url', 'util',
                  'util/types', 'v8', 'vm', 'wasi', 'worker_threads', 'zlib'}


def collect(task, test_file):
    """Return exact safe input bytes, following modules but never parsing data files.

    Supports root Node and Python tests with literal local dependencies. Package
    installs, computed loads and arbitrary project scripts aren't inferred.
    """
    profile = task['check_profile']
    if profile not in ('node-test', 'python-unittest'):
        return {}
    inputs, parsed, queue = {}, set(), [(test_file, True)]
    total = 0

    def load(path):
        nonlocal total
        if path in inputs:
            return inputs[path]
        data = read_optional(task, path)
        if data is None:
            return None
        if len(inputs) >= MAX_FILES or total + len(data) > MAX_BYTES:
            raise Rejected('Automatic check inputs exceed 128 files or 8 MiB. Choose a smaller check.')
        inputs[path] = data
        total += len(data)
        return data

    def resolve(path, candidates, executable=True):
        for candidate in candidates:
            relative(candidate)
            if load(candidate) is not None:
                queue.append((candidate, executable))
                return candidate
        raise Rejected('Check dependency is missing: ' + path)

    while queue:
        source, executable = queue.pop(0)
        raw = load(source)
        if raw is None:
            raise Rejected('Check input is missing: ' + source)
        if not executable or source in parsed:
            continue
        parsed.add(source)
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise Rejected('Check module must be UTF-8: ' + source) from exc
        if profile == 'node-test':
            specs = list(_JS_IMPORT.findall(text))
            for kind, argument in _JS_CALL.findall(text):
                match = _LITERAL.fullmatch(argument)
                if not match:
                    raise Rejected('Computed ' + kind + ' dependency is unsupported in ' + source)
                specs.append(match[2])
            for spec in specs:
                if spec.removeprefix('node:') in _NODE_BUILTINS:
                    continue
                if not spec.startswith('./') and not spec.startswith('../'):
                    raise Rejected('Package check dependencies are unsupported: ' + spec)
                path = _local(source, spec)
                candidates = [path] if PurePosixPath(path).suffix else [path + ext for ext in ('.js', '.mjs', '.cjs', '.json')] + [path + '/index.js']
                resolve(path, candidates, not path.endswith('.json'))
            urls = list(_JS_URL.finditer(text))
            if len(urls) != len(re.findall(r'\bnew\s+URL\s*\(', text)):
                raise Rejected('Computed URL check inputs are unsupported in ' + source)
            for match in urls:
                path = _local(source, match[2])
                resolve(path, [path], False)
        else:
            try:
                tree = ast.parse(text, filename=source)
            except SyntaxError as exc:
                raise Rejected('Cannot discover Python check inputs: ' + str(exc)) from exc
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (isinstance(node.func, ast.Name) and node.func.id == '__import__'
                        or isinstance(node.func, ast.Attribute) and node.func.attr == 'import_module'):
                    raise Rejected('Dynamic Python imports are unsupported in ' + source)
                if not isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or '']
                for name in names:
                    level = getattr(node, 'level', 0)
                    if not level and name.split('.')[0] in sys.stdlib_module_names:
                        continue
                    base = list(PurePosixPath(source).parent.parts) if level else []
                    if level > len(base) + 1:
                        raise Rejected('Python import leaves the project: ' + source)
                    if level > 1:
                        base = base[:len(base) - level + 1]
                    pieces = base + (name.split('.') if name else [])
                    if not pieces:
                        raise Rejected('Unsupported Python relative import in ' + source)
                    path = '/'.join(pieces)
                    found = resolve(path, [path + '.py', path + '/__init__.py'])
                    # Python executes each package initializer before its child module.
                    for n in range(1, len(pieces)):
                        init = '/'.join(pieces[:n]) + '/__init__.py'
                        if load(init) is not None:
                            queue.append((init, True))
                    if isinstance(node, ast.ImportFrom) and found.endswith('/__init__.py'):
                        for alias in node.names:
                            if alias.name != '*':
                                child = path + '/' + alias.name
                                for candidate in (child + '.py', child + '/__init__.py'):
                                    if load(candidate) is not None:
                                        queue.append((candidate, True))
                                        break
    return inputs
