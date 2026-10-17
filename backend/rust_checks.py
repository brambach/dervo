"""Dependency-free Rust metadata only. No Cargo, linker, plugins or executed tests.

The project permission can select an installed toolchain. A host administrator
can instead select a toolchain with DERVO_RUST_TOOLCHAIN,
 or /usr/local/etc/dervo/rust-toolchain.json containing {"toolchain": "/absolute/root"}.
No PATH, rustup shim, project config or builder cache is discovered. Configuration
is a trust decision for the compiler, not a request to execute project code.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile

from dervo_connections import Rejected
import check_profiles
import platform_os

PROFILE = 'rust-check'
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\.rs')
CONFIG = Path('/usr/local/etc/dervo/rust-toolchain.json')
BYTE_CAP = 768 * 1024 * 1024
FILE_CAP = 256
OUTPUT_CAP = 16 * 1024 * 1024


def toolchain_root(value=None):
    if value is None:
        value = os.environ.get('DERVO_RUST_TOOLCHAIN')
    if value is None:
        try:
            fd = _open_regular(CONFIG)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 4096 or info.st_mode & 0o022:
                    raise ValueError('configuration must be a small regular file without group/world write access')
                config = json.loads(os.read(fd, 4097))
            finally:
                os.close(fd)
            if type(config) is not dict or set(config) != {'toolchain'}:
                raise ValueError('expected only toolchain')
            value = config['toolchain']
        except (OSError, ValueError) as exc:
            raise Rejected('No configured Rust toolchain. Set DERVO_RUST_TOOLCHAIN or /usr/local/etc/dervo/rust-toolchain.json to an existing trusted toolchain root. Nothing is installed. ' + str(exc)) from exc
    if type(value) is not str or not value or not Path(value).is_absolute() or '..' in Path(value).parts:
        raise Rejected('Rust toolchain must be an absolute directory without ..')
    root = Path(value)
    try:
        if root.resolve(strict=True) != root or not root.is_dir():
            raise ValueError('symlink or non-directory')
    except (OSError, ValueError) as exc:
        raise Rejected('Rust toolchain is missing or malformed: ' + str(exc)) from exc
    return root


def argv(root, name, output='check.rmeta'):
    if type(name) is not str or not NAME.fullmatch(name):
        raise Rejected('Rust type check needs one root .rs file')
    return [str(root / 'bin/rustc'), '--crate-name', 'dervo_check', '--crate-type=lib',
            '--edition=2021', '--emit=metadata', '--sysroot', str(root), '-o', output, name]


def _open_regular(path):
    # Hold every ancestor descriptor so a directory substitution can't redirect a read.
    fd = platform_os.open_root(path)
    try:
        for part in platform_os.below_root(path)[:-1]:
            new = platform_os.open_dir_at(part, fd)
            os.close(fd)
            fd = new
        return platform_os.open_file_at(path.name, fd)
    finally:
        os.close(fd)


def _inputs(directory, pattern):
    paths = []
    with os.scandir(directory) as entries:
        for count, entry in enumerate(entries):
            if count >= 1024:
                raise Rejected('Rust runtime directory exceeds enumeration cap')
            if pattern.fullmatch(entry.name):
                paths.append(directory / entry.name)
                if len(paths) > FILE_CAP:
                    raise Rejected('Rust runtime exceeds file cap')
    return sorted(paths)


def file_state(info):
    """What a change to a file's bytes or metadata moves. Not the access time: reading a file can update it, which made a
    file that never changed look "changed while copying" (17 Sep)."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def collect(destination=None, *, root=None, inspect_only=False):
    """Hash bounded regular inputs via no-follow descriptors; optionally copy those same bytes.

    Only host-target rmeta files and the compiler driver are exposed. rustlib dylibs
    (including proc_macro libraries), linkers and codegen plugins aren't copied.
    """
    if inspect_only and destination is not None:
        raise Rejected('Availability inspection cannot produce a compiler snapshot')
    root = toolchain_root(root)
    target = {'arm64': 'aarch64', 'x86_64': 'x86_64'}.get(platform.machine())
    if target is None:
        raise Rejected('Rust metadata supports macOS arm64 and x86_64 toolchains only')
    lib = root / 'lib'
    syslib = lib / 'rustlib' / (target + '-apple-darwin') / 'lib'
    try:
        drivers = _inputs(lib, re.compile(r'librustc_driver-[A-Za-z0-9_-]+\.dylib'))
        metadata = _inputs(syslib, re.compile(r'lib[A-Za-z0-9_-]+\.rmeta'))
        paths = [root / 'bin/rustc', *drivers, *metadata]
        if len(drivers) != 1 or not any(p.name.startswith('libstd-') for p in metadata):
            raise Rejected('Rust toolchain needs one compiler driver and host standard-library metadata')
        if len(paths) > FILE_CAP:
            raise Rejected('Rust runtime exceeds file cap')
        records, total = {}, 0
        for path in paths:
            if path.resolve(strict=True) != path:
                raise Rejected('Rust runtime symlinks are refused')
            fd = _open_regular(path)
            try:
                info = os.fstat(fd)
                total += info.st_size
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or total > BYTE_CAP:
                    raise Rejected('Rust inputs must be regular, not group/world writable, and within the byte cap')
                relative = str(path.relative_to(root))
                if relative == 'bin/rustc' and not info.st_mode & 0o111:
                    raise Rejected('Rust compiler is not executable')
                if inspect_only:
                    # Observation checks shape, modes and presence, never supplies execution identity.
                    if relative == 'bin/rustc' and os.read(fd, 4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                        raise Rejected('Rust compiler must be a native Mach-O executable, not a shim or script')
                    records[relative] = dict(size=info.st_size, mode=stat.S_IMODE(info.st_mode))
                    continue
                digest, count, magic = hashlib.sha256(), 0, b''
                sink = None
                if destination is not None:
                    dest = Path(destination) / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    sink = dest.open('xb')
                try:
                    while chunk := os.read(fd, 1024 * 1024):
                        if not magic:
                            magic = chunk[:4]
                        count += len(chunk)
                        if count > info.st_size:
                            raise Rejected('Rust input changed while copying')
                        digest.update(chunk)
                        if sink:
                            sink.write(chunk)
                finally:
                    if sink:
                        sink.close()
                if count != info.st_size or file_state(os.fstat(fd)) != file_state(info):
                    raise Rejected('Rust input changed while copying')
                if relative == 'bin/rustc' and magic not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                    raise Rejected('Rust compiler must be a native Mach-O executable, not a shim or script')
                records[relative] = dict(sha256=digest.hexdigest(), size=count, mode=stat.S_IMODE(info.st_mode))
                if destination is not None:
                    dest.chmod(0o500 if relative == 'bin/rustc' else 0o400)
            finally:
                os.close(fd)
        return dict(root=str(root), target=target + '-apple-darwin', files=records)
    except OSError as exc:
        raise Rejected('Rust runtime cannot be read: ' + str(exc)) from exc


def availability(root=None):
    return collect(root=root, inspect_only=True)


def identity(root=None):
    return collect(root=root)


def policy(source, output, runtime, identity):
    literal = lambda p: '(literal ' + json.dumps(str(p)) + ')'
    executable = runtime / 'bin/rustc'
    system = ['/usr/lib/dyld', '/usr/lib/libSystem.B.dylib', '/usr/lib/libc++.1.dylib',
              '/usr/lib/libc++abi.dylib', '/usr/lib/libiconv.2.dylib', '/usr/lib/libz.1.dylib',
              '/usr/lib/libobjc.A.dylib',
              '/System/Library/Frameworks/Foundation.framework/Versions/C/Foundation']
    runtime_files = [runtime / p for p in identity['files']]
    directories = {runtime} | {parent for path in runtime_files for parent in path.parents if parent == runtime or runtime in parent.parents}
    reads = runtime_files + sorted(directories) + system
    ancestors = {p for path in (source, output, *[runtime / p for p in identity['files']]) for p in path.parents}
    return ('(version 1)(deny default)(deny network*)(deny process-fork)'
            # The macOS loader needs to read the root directory itself before rustc starts.
            # A literal grants no reads of files or subdirectories beneath it.
            '(allow file-read-data (literal "/"))'
            '(allow sysctl-read)(allow process-exec ' + literal(executable) + ')'
            '(allow file-map-executable ' + ' '.join(map(literal, reads)) + ')'
            '(allow file-read-metadata ' + ' '.join(map(literal, sorted(ancestors))) + ')'
            '(allow file-read* ' + ' '.join(map(literal, reads)) + ' (subpath ' + json.dumps(str(source)) + '))'
            '(allow file-read* file-write* (subpath ' + json.dumps(str(output)) + '))')


def verify(task, snapshots, cancel=None, compiler=None, *, timeout=5, output_limit=65536):
    from task_evidence import ConfinementBlocked, _capture_confined
    import sys
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise ConfinementBlocked('Rust metadata requires macOS sandbox-exec')
    check_profiles.runner_timeout(timeout)
    if not 0 < output_limit <= 65536:
        raise Rejected('Verification limits exceed host bounds')
    expected = compiler['identity'] if compiler else task.get('compiler')
    if not expected or task['argv'] != argv(Path(expected['root']), task['argv'][-1]):
        raise Rejected('Rust verification requires exact scope-bound compiler and argv')
    if cancel is not None and cancel.is_set():
        return dict(exit_status=-1, stopped='cancelled', stdout='', stderr='', check_profile=PROFILE, argv=task['argv'])
    with tempfile.TemporaryDirectory(prefix='dervo-rust-') as temp:
        base = Path(temp).resolve()
        source, output, runtime = (base / name for name in ('source', 'output', 'runtime'))
        for path in (source, output, runtime):
            path.mkdir()
        actual = collect(runtime, root=expected['root'])
        if actual != expected:
            raise Rejected('Rust compiler/sysroot changed since scope approval')
        for name, snapshot in snapshots.items():
            from task_evidence import TaskEvidence
            TaskEvidence._relative(name)
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(snapshot['bytes_b64'], validate=True))
            target.chmod(0o400)
        command = argv(runtime, task['argv'][-1], str(output / 'check.rmeta'))
        sandbox_policy = policy(source, output, runtime, actual)
        result = _capture_confined(source, command, sandbox_policy, PROFILE,
                                   set(str(runtime / p) for p in actual['files']), timeout=timeout,
                                   output_limit=output_limit, cancel=cancel, file_limit=OUTPUT_CAP)
        result.update(compiler=actual, scope_argv=task['argv'], kind='metadata', tests_executed=False,
                      policy_sha256=hashlib.sha256(sandbox_policy.encode()).hexdigest(),
                      cwd=str(source), output_directory=str(output))
        if result['exit_status'] == 0 and not result['stopped'] and not (output / 'check.rmeta').is_file():
            result['stopped'] = 'metadata_missing'
        return result
