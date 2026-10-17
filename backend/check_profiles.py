"""Named host check profiles: what one confined verification may execute.

A profile is chosen once in the project permission and bound to a task's scope. Providers never pick an executable,
argv, shell or environment: ``dervo_run_tests`` runs the profile's fixed argv against the task's own snapshot. Every
runtime is discovered from an explicit trusted list, never PATH or the user's home, and a missing or invalid runtime is
refused rather than substituted. ``typescript-check`` runs the project's own ``node_modules/typescript`` compiler: its
finite inputs (package.json, lib/tsc.js, lib/_tsc.js, lib/lib*.d.ts) are validated and copied into the disposable
snapshot under a byte cap, then tsc.js --noEmit runs under the same Node sandbox with only the snapshot readable. Optional explicit declaration/config inputs are copied with their own limits and exact hashes.
Unlisted dependencies fail resolution rather than reading the live project.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

from dervo_connections import Rejected
import platform_os

PYTHON = 'python-unittest'
NODE = 'node-test'
TYPESCRIPT = 'typescript-check'
RUST = 'rust-check'
RUST_EXEC = 'rust-test'
MANUAL = 'manual-review'
RUST_PROFILES = (RUST, RUST_EXEC)
RUST_TEST = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\.rs')
DEFAULT = PYTHON
DEFAULT_CHECK_TIMEOUT = 5
MAX_CHECK_TIMEOUT = 120


def check_timeout(value=DEFAULT_CHECK_TIMEOUT):
    """Public scope duration. Missing legacy fields use five seconds; supplied values are strict."""
    if type(value) is not int or not DEFAULT_CHECK_TIMEOUT <= value <= MAX_CHECK_TIMEOUT:
        raise Rejected('Maximum check time must be a whole number from 5 to 120 seconds')
    return value


def runner_timeout(value):
    """Private runners accept short fractional deadlines for cancellation tests."""
    if type(value) not in (int, float) or not 0 < value <= MAX_CHECK_TIMEOUT:
        raise Rejected('Verification limits exceed host bounds')
    return value

PYTHON_TEST = re.compile(r'test_[A-Za-z0-9_]+\.py')
NODE_TEST = re.compile(r'(test_[A-Za-z0-9_]+|[A-Za-z0-9_-]+\.test)\.(mjs|cjs|js)')
TS_TEST = re.compile(r'tsconfig\.json')  # the root project config is the one "test file" of a TypeScript check
NODE_CANDIDATES = ('/usr/local/bin/node', '/opt/homebrew/bin/node')  # explicit supported install locations, never PATH or $HOME
NODE_ROOTS = ('/usr/local/', '/opt/homebrew/')
if platform_os.LINUX:
    # nodejs.org's tarball in /usr/local, or the distribution's or NodeSource's package in /usr/bin. Both under /usr, which
    # the Linux sandbox reads anyway (linux_confinement.SYSTEM_FOLDERS).
    NODE_CANDIDATES = ('/usr/local/bin/node', '/usr/bin/node')
    NODE_ROOTS = ('/usr/local/', '/usr/bin/', '/usr/lib/')
# The node:test file is run directly, in-process: `node --test` spawns one child per file and the sandbox denies fork, and the
# in-process `--test-isolation=none` flag is not accepted by the Node installs verified here. A directly run node:test file
# reports a summary on stdout (TAP up to Node 22, the spec reporter from 24) and exits 1 when any test fails.
# Node 24.21 made fetch permanent and refuses --no-experimental-fetch (exit 9), so a Node that doesn't take it runs with
# --no-addons alone; the sandbox still denies the network either way. node_flags() picks the set for one install.
NODE_FLAGS = ('--no-addons', '--no-experimental-fetch')
NODE_FLAG_SETS = (NODE_FLAGS, ('--no-addons',))
_node_flags_seen = {}
TS_PACKAGE = 'node_modules/typescript'          # resolved under the project root only; a symlink may not leave the root
TS_ENTRY = TS_PACKAGE + '/lib/tsc.js'
TS_ARGS = ('--noEmit', '-p', 'tsconfig.json')
TS_INPUT = re.compile(r'(tsc|_tsc)\.js|lib(\.[A-Za-z0-9_.-]+)?\.d\.ts')  # the compiler bundle and its bundled lib declarations, nothing else
TS_BYTE_CAP = 64 * 1024 * 1024
TS_FILE_CAP = 256
TS_DETAIL = ('Runs the supported local Node install on a hash-bound copy of node_modules/typescript with --noEmit -p '
             'tsconfig.json. Optional dervoInputs explicitly lists in-root declaration files, package metadata, original '
             'licences, root-level tsconfig imports and preview-features.json with a bounded public-feature schema '
             '(2048 files, 32 MiB plus compiler limits; preview JSON up to 64 KiB and 128 features). This mode requires an '
             'explicit nonempty source files list. No dependency discovery or installs. Unlisted packages fail with TS2307 '
             'or another compiler resolution diagnostic. No shell, npm scripts, network, writes, child processes or '
             'symlinks in dependency inputs. Project references, typeRoots, plugins and build outputs remain unsupported.')


CATALOGUE = (
    dict(id=MANUAL, label='Review changes myself', test_pattern='No test file needed', available=True,
         detail='No automated checks run. Dervo records the changed files and waits for your review. '
                'Your approval records your decision, not a passing test.'),
    dict(id=RUST_EXEC, label='Rust tests', test_pattern='root .rs file', available=True,
         detail='Compiles and runs dependency-free Rust tests from an isolated source copy. Uses your selected '
                'installed Rust toolchain and Apple Command Line Tools SDK. No Cargo, downloads, external crates '
                'or build scripts. Tests cannot access files outside the snapshot, write files, use the network '
                'or start child processes. Compilation and execution share the saved check time.'),
    dict(id=RUST, label='Rust type check', test_pattern='root .rs file', available=True,
         detail='Checks dependency-free Rust with rustc --emit=metadata. No Rust tests execute, no linker, Cargo, '
                'downloads, proc macros, plugins, network or children. Source is read-only; metadata uses disposable output. '
                'Choose an installed trusted toolchain in Files and checks. No compiler is downloaded.'),
    dict(id=PYTHON, label='Python unittest', test_pattern='test_*.py', available=True,
         detail='Runs Dervo’s own Python 3.13 with -I -B -m unittest discover -p <test file> on the task snapshot. '
                'No network, no writes, no child processes, no HOME or PATH.'),
    dict(id=NODE, label='Node test', test_pattern='test_*.mjs|cjs|js or *.test.mjs|cjs|js', available=True,
         detail='Runs the supported local Node install (/usr/local/bin/node or /opt/homebrew/bin/node, no PATH lookup) with '
                '--no-addons (and --no-experimental-fetch where that Node accepts it) <node:test file> on the task snapshot, in-process. No shell, no npm scripts, '
                'no network, no writes, no child processes. node_modules is not copied: a bare package import fails visibly in the '
                'evidence rather than reading the live project. A run that reports no tests does not count as passing. '
                'Only the nodejs.org installer layout is supported: a Node that links its own libraries (Homebrew) fails to start '
                'in confinement and the check is blocked, never run unconfined.'),
    dict(id=TYPESCRIPT, label='TypeScript check', test_pattern='tsconfig.json', available=True, detail=TS_DETAIL),
)
WINDOWS_DETAIL = ('Only Python checks run on Windows for now. This one needs a Mac. Choose Python unittest, or Review '
                  'changes myself and Dervo records the changes for you to review.')
WINDOWS_PYTHON_DETAIL = ("Runs Dervo's own Python 3.13 with -I -B -m unittest discover -p <test file> on the task snapshot, in a "
                         'Windows app container on a restricted token. No network, no writes, no child processes, no HOME or PATH.')
if platform_os.WINDOWS:
    # Python checks run in a Windows app container (windows_confinement.py). The others need macOS sandbox-exec, so
    # they're listed as unavailable and never run unconfined. Python stays the default, as on a Mac: the grant dialog
    # leaves checkProfile out for Python (grantPayload in frontend/src/local/api.ts).
    CATALOGUE = tuple(entry if entry['id'] == MANUAL else dict(entry, detail=WINDOWS_PYTHON_DETAIL) if entry['id'] == PYTHON
                      else dict(entry, available=False, detail=WINDOWS_DETAIL) for entry in CATALOGUE)


LINUX_DETAIL = ('Rust checks run only on a Mac for now. Choose Python unittest, Node test or TypeScript check, or Review '
                'changes myself and Dervo records the changes for you to review.')
LINUX_PYTHON_DETAIL = ("Runs Dervo's own Python 3.13 with -I -B -m unittest discover -p <test file> on the task snapshot, under "
                       'bubblewrap with a seccomp filter. No network, no writes, no child processes, no HOME or PATH.')
if platform_os.LINUX and not platform_os.WINDOWS:  # a test that pretends to be Windows on Linux gets Windows
    # Python, Node and TypeScript checks run under bubblewrap (linux_confinement.py). Rust checks need the Mac's SDK and
    # sandbox-exec, so they're listed as unavailable and never run unconfined, as on Windows.
    CATALOGUE = tuple(dict(entry, detail=LINUX_PYTHON_DETAIL) if entry['id'] == PYTHON
                      else dict(entry, available=False, detail=LINUX_DETAIL) if entry['id'] in RUST_PROFILES
                      else dict(entry, detail=entry['detail'].replace('/usr/local/bin/node or /opt/homebrew/bin/node',
                                                                      '/usr/local/bin/node or /usr/bin/node'))
                      if entry['id'] == NODE else entry for entry in CATALOGUE)


def catalogue():
    return [dict(entry) for entry in CATALOGUE]


def profile(profile_id):
    if profile_id is None:
        return DEFAULT
    if type(profile_id) is not str or profile_id not in {e['id'] for e in CATALOGUE}:
        raise Rejected('Unknown check profile. Choose one of ' + ', '.join(e['id'] for e in CATALOGUE))
    if not next(e for e in CATALOGUE if e['id'] == profile_id)['available']:
        raise Rejected(next(e for e in CATALOGUE if e['id'] == profile_id)['detail'])
    return profile_id


def test_pattern(profile_id):
    return {MANUAL: re.compile(r'(?!)'), PYTHON: PYTHON_TEST, NODE: NODE_TEST, TYPESCRIPT: TS_TEST, RUST: RUST_TEST, RUST_EXEC: RUST_TEST}[profile(profile_id)]


FRAMEWORK_PYTHON = Path('/Library/Frameworks/Python.framework/Versions/3.13')


def audited_python_prefix(backend=None):
    """The one Python 3.13 a Python check may run on, confined (task_evidence): the copy a built app carries beside this
    backend (``<name>.app/Contents/Resources/runtime/python``, runtime/python.json), or the python.org framework in a
    source checkout. Nothing else, whatever else the Mac has installed."""
    resources = Path(backend if backend is not None else Path(__file__).resolve().parent).parent
    if resources.name == 'Resources' and resources.parent.name == 'Contents' and resources.parent.parent.suffix == '.app':
        return resources / 'runtime/python'
    if platform_os.LINUX:
        # The Python the host runs on: the package's linux-python (src/launch.rs), the system's in a source checkout.
        return Path(sys.base_prefix)
    return FRAMEWORK_PYTHON


def python_interpreter():
    if platform_os.WINDOWS:
        return str(Path(sys.base_prefix) / 'python.exe')  # the windows-python the app carries and the host runs on
    """The running host's own interpreter, as a check runs it. The framework's real executable is its Python.app stub;
    the app's own runtime (python-build-standalone) runs bin/python3.13 directly."""
    base = Path(sys.base_prefix)
    if base.resolve() == FRAMEWORK_PYTHON:
        return str(base / 'Resources/Python.app/Contents/MacOS/Python')
    return str(base / 'bin' / ('python%d.%d' % sys.version_info[:2]))


def node_runtime():
    """The one supported Node binary: an explicit candidate that resolves, without leaving /usr/local or /opt/homebrew, to a
    regular executable file that is not world-writable. This is a location and mode check, not an ownership check: both
    roots can be user-writable (Homebrew's prefix usually is), so a user-level process could replace the binary. Missing
    or odd installs are refused, never worked around. Whether the install actually starts in confinement is settled by
    the sandbox self-test at verification, not here."""
    for candidate in NODE_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            real = path.resolve(strict=True)
        except OSError as exc:
            raise Rejected(f'Node runtime {candidate} cannot be resolved: {exc}')
        info = os.stat(real)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & stat.S_IXUSR:
            raise Rejected(f'Node runtime {candidate} is not a regular executable file')
        if not str(real).startswith(NODE_ROOTS):
            raise Rejected(f'Node runtime {candidate} resolves outside the trusted install roots: {real}')
        if info.st_mode & stat.S_IWOTH:
            raise Rejected(f'Node runtime {real} is world-writable')
        return str(real)
    raise Rejected('No trusted Node runtime found at ' + ' or '.join(NODE_CANDIDATES) + '. Install Node there or choose another check profile.')  # message kept: tests and the grant dialog match on it


def node_flags(node):
    """The flags this Node install takes: NODE_FLAGS, or --no-addons alone where --no-experimental-fetch is refused. Asked once
    per binary path, mtime and size, so an upgrade in place is asked again. A probe that can't run keeps NODE_FLAGS, and
    the check then fails visibly rather than running with fewer flags than it could."""
    try:
        info = os.stat(node)
    except OSError:
        return NODE_FLAGS
    key = (node, info.st_mtime_ns, info.st_size)
    if key not in _node_flags_seen:
        try:
            probe = subprocess.run([node, '--no-experimental-fetch', '-e', '0'], env={}, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return NODE_FLAGS
        _node_flags_seen[key] = NODE_FLAGS if probe.returncode == 0 else NODE_FLAG_SETS[1]
    return _node_flags_seen[key]


def typescript_compiler(root, *, read=False):
    """The project's own compiler as finite snapshot inputs.

    ``read=False`` is the cheap preflight (grant, permission status): ``{package, version, bytes, files: {path: size}}``
    without reading the inputs. ``read=True`` is what scope and launch use: the same plus ``sha256: {path: hex}`` over the
    bytes actually read and ``content: {path: bytes}``, the exact bytes that are copied into the snapshot. Identity is the
    record without ``content``; sizes alone never bind a compiler (a same-size replacement is a different sha256).

    ``<root>/node_modules/typescript`` may be a symlink (pnpm layouts are) but must resolve inside the project root; every
    step below the resolved package is opened O_NOFOLLOW, so a symlinked input is refused. Only package.json, lib/tsc.js,
    lib/_tsc.js and lib/lib*.d.ts are inputs; everything else in the package is ignored. Sizes are capped before any read.
    A missing compiler is refused with a message the grant dialog shows; nothing is installed or downloaded."""
    project = Path(root).resolve()
    candidate = project / TS_PACKAGE
    if not candidate.exists():
        raise Rejected(f'No TypeScript compiler at {TS_PACKAGE} in the project. Install typescript there yourself or choose another check profile.')
    try:
        real = candidate.resolve(strict=True)
    except OSError as exc:
        raise Rejected(f'TypeScript compiler {TS_PACKAGE} cannot be resolved: {exc}')
    if real != project and project not in real.parents:
        raise Rejected(f'TypeScript compiler {TS_PACKAGE} resolves outside the project: {real}. Only a compiler inside the project is used.')
    files, total = {}, 0
    descriptors = []

    def _open(fd, name, directory):
        try:
            new = platform_os.open_dir_at(name, fd) if directory else platform_os.open_file_at(name, fd)
        except OSError as exc:
            kind = 'is a symlink; refused' if exc.errno == errno.ELOOP else 'cannot be opened'
            raise Rejected(f'TypeScript compiler input {name} {kind}: {exc}') from exc
        descriptors.append(new)
        return new

    try:
        fd = platform_os.open_root(real)
        descriptors.append(fd)
        for part in platform_os.below_root(real):
            fd = _open(fd, part, True)
        package_fd = fd
        for relative, dir_fd in (('package.json', package_fd),):
            files[relative] = _regular(dir_fd, relative, files, total)
            total += files[relative]
        lib_fd = _open(package_fd, 'lib', True)
        names = sorted(n for n in platform_os.listdir_fd(lib_fd) if TS_INPUT.fullmatch(n))
        if 'tsc.js' not in names:
            raise Rejected(f'TypeScript compiler {TS_PACKAGE} has no lib/tsc.js; refused')
        if len(names) + 1 > TS_FILE_CAP:
            raise Rejected(f'TypeScript compiler {TS_PACKAGE} exceeds the {TS_FILE_CAP} input file cap; refused')
        for name in names:
            files['lib/' + name] = _regular(lib_fd, name, files, total)
            total += files['lib/' + name]
        package_json = _read(package_fd, 'package.json', files['package.json'])
        try:
            meta = json.loads(package_json)
        except ValueError as exc:
            raise Rejected(f'TypeScript compiler package.json is not JSON: {exc}')
        if type(meta) is not dict or meta.get('name') != 'typescript' or type(meta.get('version')) is not str \
                or not re.fullmatch(r'\d+\.\d+\.\d+([-+][A-Za-z0-9.-]+)?', meta['version']):
            raise Rejected(f'{TS_PACKAGE} is not the typescript package (name/version missing or different); refused')
        result = dict(package=TS_PACKAGE, version=meta['version'], bytes=total,
                      files={TS_PACKAGE + '/' + rel: size for rel, size in files.items()})
        if read:
            content, digests = {}, {}
            for rel, size in files.items():
                dir_fd, name = (package_fd, rel) if '/' not in rel else (lib_fd, rel.split('/', 1)[1])
                data = package_json if rel == 'package.json' else _read(dir_fd, name, size)
                content[TS_PACKAGE + '/' + rel] = data
                digests[TS_PACKAGE + '/' + rel] = hashlib.sha256(data).hexdigest()
            result['sha256'] = digests
            result['content'] = content
        return result
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def _regular(dir_fd, name, files, total):
    try:
        info = platform_os.stat_at(name, dir_fd)
    except OSError as exc:
        raise Rejected(f'TypeScript compiler input {name} cannot be read: {exc}') from exc
    if stat.S_ISLNK(info.st_mode):
        raise Rejected(f'TypeScript compiler input {name} is a symlink; refused')
    if not stat.S_ISREG(info.st_mode):
        raise Rejected(f'TypeScript compiler input {name} is not a regular file; refused')
    if total + info.st_size > TS_BYTE_CAP:
        raise Rejected(f'TypeScript compiler exceeds the {TS_BYTE_CAP} byte cap at {name}; refused')
    return info.st_size


def _read(dir_fd, name, size):
    """Read one input by name under an already-open directory. The open is O_NOFOLLOW and the opened descriptor is checked
    to be a regular file of the size just measured, so an input swapped for a symlink, FIFO or other bytes between the size
    check and the read is refused (never followed, never blocked on, never silently hashed as something else)."""
    try:
        fd = platform_os.open_file_at(name, dir_fd)
    except OSError as exc:
        kind = 'is a symlink; refused' if exc.errno == errno.ELOOP else 'cannot be read'
        raise Rejected(f'TypeScript compiler input {name} {kind}: {exc}') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Rejected(f'TypeScript compiler input {name} is not a regular file; refused')
        if info.st_size != size:
            raise Rejected(f'TypeScript compiler input {name} changed while being read; refused')
        chunks, remaining = [], size + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise Rejected(f'TypeScript compiler input {name} cannot be read: {exc}') from exc
    finally:
        os.close(fd)
    data = b''.join(chunks)
    if len(data) != size:
        raise Rejected(f'TypeScript compiler input {name} changed while being read; refused')
    return data


def typescript_snapshot(root):
    """Compiler and optional explicit declaration/config inputs share one hash binding."""
    import typescript_inputs
    record = typescript_compiler(root, read=True)
    extra = typescript_inputs.collect(root) if (Path(root) / 'tsconfig.json').exists() else {}
    for path, data in extra.items():
        record['content'][path] = data
        record['files'][path] = len(data)
        record['sha256'][path] = hashlib.sha256(data).hexdigest()
        record['bytes'] += len(data)
    return record


def typescript_identity(root):
    """The compiler identity a scope binds: package, version, sizes and the sha256 of every input's bytes as read now.
    ``content`` is left out so the record is small enough to persist; launch re-reads and compares the whole identity."""
    record = typescript_snapshot(root)
    record.pop('content')
    return record


def root_tsconfig(root):
    """The bytes of ``<root>/tsconfig.json`` for grant-time validation: opened O_NOFOLLOW, a regular file of at most 256 KiB.
    The verification itself validates the task snapshot's own bytes again; this only makes a refusal visible at grant."""
    try:
        # Windows has no O_NOFOLLOW or O_NONBLOCK (platform_os.NOFOLLOW is 0 there).
        fd = os.open(str(Path(root) / 'tsconfig.json'), os.O_RDONLY | platform_os.NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
                     | getattr(os, 'O_BINARY', 0))
    except OSError as exc:
        raise Rejected(f'tsconfig.json cannot be read: {exc}') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 262144:
            raise Rejected('tsconfig.json must be a regular file up to 256 KiB')
        return os.read(fd, 262144)
    except OSError as exc:
        raise Rejected(f'tsconfig.json cannot be read: {exc}') from exc
    finally:
        os.close(fd)


def preflight(profile_id, root, test_file, *, rust_toolchain=None, observation=False):
    """Everything a grant must prove before it is recorded and every permission read repeats: the fixed argv (runtime
    discovered), and for the TypeScript check the project's own in-root compiler and a validated root tsconfig.json.
    Observation checks Rust presence, shape and modes without hashing the runtime. Saving permission
    performs full validation; scope and launch bind and re-check the actual bytes."""
    argv = argv_for(profile_id, test_file, rust_toolchain=rust_toolchain)
    if profile(profile_id) in RUST_PROFILES:
        import rust_checks
        selected = rust_checks.toolchain_root(rust_toolchain)
        if selected == Path(root).resolve() or Path(root).resolve() in selected.parents:
            raise Rejected('Choose a trusted Rust toolchain outside the project folder')
        import rust_tests
        module = rust_tests if profile(profile_id) == RUST_EXEC else rust_checks
        (module.availability if observation else module.identity)(str(selected))
    if profile(profile_id) == TYPESCRIPT:
        typescript_compiler(root)
        validate_tsconfig(root_tsconfig(root))
        import typescript_inputs
        typescript_inputs.collect(root)
    return argv


TS_TOP_REFUSED = ('extends', 'references')
TS_OPTION_REFUSED = ('plugins', 'incremental', 'composite', 'tsBuildInfoFile', 'typeRoots', 'types')
TS_OPTION_PATHS = ('baseUrl', 'rootDir', 'rootDirs', 'outDir', 'outFile', 'declarationDir')


def _strip_json_comments(text):
    out, i, n = [], 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == '\\' and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif text.startswith('//', i):
            while i < n and text[i] != '\n':
                i += 1
            continue
        elif text.startswith('/*', i):
            end = text.find('*/', i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r',(\s*[}\]])', r'\1', ''.join(out))


def _inside_project(value, where):
    if type(value) is not str or not value or value.startswith('/') or '\\' in value or '${' in value or '\x00' in value \
            or any(part == '..' for part in value.split('/')):
        raise Rejected(f'tsconfig.json {where} may only name paths inside the project (no absolute, .., ${{configDir}} or backslash paths)')


def validate_tsconfig(data, *, bounded=False):
    """Refuse a tsconfig.json whose options could reach outside the snapshot, need inputs that are not copied or write
    outputs. The sandbox denies all of that anyway; this makes the refusal a clear message instead of an opaque failure."""
    try:
        config = json.loads(_strip_json_comments(data.decode('utf-8')))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Rejected(f'tsconfig.json is not JSON (comments and trailing commas are allowed): {exc}')
    if type(config) is not dict:
        raise Rejected('tsconfig.json must be a JSON object')
    bounded = bounded or 'dervoInputs' in config
    for key in TS_TOP_REFUSED:
        if key == 'extends' and bounded:
            continue
        if key in config:
            raise Rejected(f'tsconfig.json "{key}" is not supported by the TypeScript check: other config files and project references are not copied')
    for key in ('files', 'include', 'exclude'):
        if key in config:
            if type(config[key]) is not list:
                raise Rejected(f'tsconfig.json "{key}" must be a list')
            for entry in config[key]:
                _inside_project(entry, key)
    options = config.get('compilerOptions', {})
    if type(options) is not dict:
        raise Rejected('tsconfig.json compilerOptions must be an object')
    if options.get('noCheck') or options.get('noResolve'):
        raise Rejected('TypeScript noCheck/noResolve cannot pass the requested project check')
    for key in TS_OPTION_REFUSED:
        if bounded and key == 'types':
            if type(options.get(key, [])) is not list:
                raise Rejected('TypeScript types must be an explicit list')
            for name in options.get(key, []):
                _inside_project(name, 'compilerOptions.types')
            continue
        if key in options and options[key] not in (False, None, []):
            raise Rejected(f'tsconfig.json compilerOptions.{key} is not supported by the TypeScript check: plugins, build outputs and '
                           'external type packages are not copied into the snapshot')
    for key in TS_OPTION_PATHS:
        if key in options:
            for entry in (options[key] if type(options[key]) is list else [options[key]]):
                _inside_project(entry, 'compilerOptions.' + key)
    paths = options.get('paths', {})
    if type(paths) is not dict:
        raise Rejected('tsconfig.json compilerOptions.paths must be an object')
    for alias, targets in paths.items():
        for entry in (targets if type(targets) is list else [targets]):
            _inside_project(entry, f'compilerOptions.paths[{alias}]')
    return config


def argv_for(profile_id, test_file, *, rust_toolchain=None):
    """The fixed argv of a profile for one root-level test file. The runtime is discovered now, so a missing runtime is
    refused at grant or scope time, not silently at verification."""
    profile_id = profile(profile_id)
    if profile_id == MANUAL:
        if test_file is not None or rust_toolchain is not None:
            raise Rejected('Manual review has no check file or compiler')
        return []
    if type(test_file) is not str or not test_pattern(profile_id).fullmatch(test_file):
        raise Rejected('Select one explicit root test file matching ' + next(e for e in CATALOGUE if e['id'] == profile_id)['test_pattern'])
    if profile_id in RUST_PROFILES:
        import rust_checks, rust_tests
        module = rust_tests if profile_id == RUST_EXEC else rust_checks
        return module.argv(rust_checks.toolchain_root(rust_toolchain), test_file)
    if profile_id == PYTHON:
        return [python_interpreter(), '-I', '-B', '-m', 'unittest', 'discover', '-s', '.', '-p', test_file]
    if profile_id == TYPESCRIPT:
        node = node_runtime()
        return [node, *node_flags(node), TS_ENTRY, *TS_ARGS]
    node = node_runtime()
    return [node, *node_flags(node), test_file]


def profile_of(argv):
    """Which profile an exact argv belongs to, or a Rejected for anything else (arbitrary commands, shells, extra flags)."""
    if len(argv) == 10 and RUST_TEST.fullmatch(argv[-1]):
        import rust_checks, rust_tests
        if argv == rust_tests.argv(rust_checks.toolchain_root(), argv[-1]):
            return RUST_EXEC
    if len(argv) == 11 and RUST_TEST.fullmatch(argv[-1]):
        import rust_checks
        if argv == rust_checks.argv(rust_checks.toolchain_root(), argv[-1]):
            return RUST
    if (len(argv) == 10 and argv[:9] == [python_interpreter(), '-I', '-B', '-m', 'unittest', 'discover', '-s', '.', '-p']
            and PYTHON_TEST.fullmatch(argv[9])):
        return PYTHON
    # Either flag set: a check saved before a Node upgrade still reads as the check it was.
    if len(argv) >= 2 and tuple(argv[1:-1]) in NODE_FLAG_SETS and NODE_TEST.fullmatch(argv[-1]):
        try:
            if argv[0] == node_runtime():
                return NODE
        except Rejected:
            pass
    if any(tuple(argv[1:]) == (*flags, TS_ENTRY, *TS_ARGS) for flags in NODE_FLAG_SETS):
        try:
            if argv[0] == node_runtime():
                return TYPESCRIPT
        except Rejected:
            pass
    raise Rejected('Only host-selected check profile argv is supported (Python unittest, Node test or TypeScript check on one root file)')
