"""Compile and run dependency-free Rust tests with separate compiler/test permissions.

The selected compiler, standard libraries, linker and installed Apple SDK stubs
are copied and hash-bound. Cargo, downloads, build scripts and external crates
aren't available. The test process cannot use the compiler's writable output.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from dervo_connections import Rejected
import check_profiles
import rust_checks

PROFILE = 'rust-test'
SDK_LINK = Path('/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk')
OUTPUT_CAP = 64 * 1024 * 1024
PASSED = re.compile(r'^test result: ok\. ([1-9][0-9]*) passed; 0 failed;', re.MULTILINE)


def argv(root, name):
    if type(name) is not str or not rust_checks.NAME.fullmatch(name):
        raise Rejected('Rust tests need one root .rs file')
    # This scope argv describes the request. The runner substitutes only its own
    # disposable paths and fixed linker flags after verifying all compiler bytes.
    return [str(root / 'bin/rustc'), '--crate-name', 'dervo_tests', '--edition=2021',
            '--test', '--sysroot', str(root), '-o', 'dervo-tests', name]


def _extra(path, relative, records, destination, inspect_only):
    fd = rust_checks._open_regular(path)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022
                or info.st_size + sum(r['size'] for r in records.values()) > rust_checks.BYTE_CAP
                or len(records) >= rust_checks.FILE_CAP):
            raise Rejected('Rust test runtime exceeds its file, mode or byte limits')
        executable = relative.endswith('/rust-lld')
        if executable and (not info.st_mode & 0o111 or os.read(fd, 4) not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')):
            raise Rejected('Rust tests need a native executable rust-lld linker')
        os.lseek(fd, 0, os.SEEK_SET)
        record = dict(size=info.st_size, mode=stat.S_IMODE(info.st_mode))
        if inspect_only:
            records[relative] = record
            return
        digest, count = hashlib.sha256(), 0
        sink = None
        if destination is not None:
            dest = Path(destination) / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            sink = dest.open('xb')
        try:
            while chunk := os.read(fd, 1024 * 1024):
                count += len(chunk)
                if count > info.st_size:
                    raise Rejected('Rust test runtime changed while reading')
                digest.update(chunk)
                if sink: sink.write(chunk)
        finally:
            if sink: sink.close()
        if count != info.st_size or os.fstat(fd) != info:
            raise Rejected('Rust test runtime changed while reading')
        records[relative] = dict(record, sha256=digest.hexdigest())
        if destination is not None: dest.chmod(0o500 if executable else 0o400)
    finally:
        os.close(fd)


def collect(destination=None, *, root=None, inspect_only=False):
    result = rust_checks.collect(destination, root=root, inspect_only=inspect_only)
    root = Path(result['root'])
    try:
        sdk = SDK_LINK.resolve(strict=True)
        if not sdk.is_dir() or sdk.parent != SDK_LINK.parent:
            raise Rejected('Rust tests need an installed Apple Command Line Tools SDK')
        relative = Path('lib/rustlib') / result['target']
        libraries = rust_checks._inputs(root / relative / 'lib', re.compile(r'lib[A-Za-z0-9_-]+\.rlib'))
        if not any(p.name.startswith('libtest-') for p in libraries):
            raise Rejected('The selected Rust toolchain has no test standard library')
        for path in [*libraries, root / relative / 'bin/rust-lld']:
            _extra(path, str(path.relative_to(root)), result['files'], destination, inspect_only)
        for name in ('libSystem.tbd', 'libc.tbd', 'libm.tbd'):
            path = (sdk / 'usr/lib' / name).resolve(strict=True)
            if sdk not in path.parents:
                raise Rejected('Apple SDK stubs must stay inside the installed SDK')
            _extra(path, 'sdk/usr/lib/' + name, result['files'], destination, inspect_only)
        result['sdk_root'] = str(sdk)
        return result
    except OSError as exc:
        raise Rejected('Rust tests need readable installed standard libraries, rust-lld and Apple SDK stubs. Nothing is installed or downloaded. ' + str(exc)) from exc


def identity(root=None):
    return collect(root=root)


def availability(root=None):
    return collect(root=root, inspect_only=True)


def compile_policy(source, output, runtime, compiler):
    linker = runtime / 'lib/rustlib' / compiler['target'] / 'bin/rust-lld'
    # The compiler may start its exact copied linker. Neither phase can execute
    # generated code until the host launches it under the separate test policy.
    return (rust_checks.policy(source, output, runtime, compiler)
            .replace('(deny process-fork)', '(allow process-fork)')
            + '(allow process-exec (literal ' + json.dumps(str(linker)) + '))')


def test_policy(source, executable):
    literals = [str(executable), '/usr/lib/dyld', '/usr/lib/libSystem.B.dylib']
    reads = ' '.join('(literal ' + json.dumps(p) + ')' for p in literals)
    return ('(version 1)(deny default)(deny network*)(deny file-write*)(deny process-fork)'
            '(allow sysctl-read)(allow file-read-data (literal "/"))'
            '(allow process-exec (literal ' + json.dumps(str(executable)) + '))'
            '(allow file-read* ' + reads + ' (subpath ' + json.dumps(str(source)) + '))'
            '(allow file-map-executable ' + reads + ')')


def phase_record(result):
    """Diagnostics live once at the top level, where provider delivery bounds them."""
    return {**{key:result[key] for key in ('phase', 'argv', 'exit_status', 'stopped')},
            'stdout_bytes':len(result['stdout'].encode()), 'stderr_bytes':len(result['stderr'].encode())}


def verify(task, snapshots, cancel=None, compiler=None, *, timeout=5, output_limit=65536):
    from task_evidence import ConfinementBlocked, TaskEvidence, _capture_confined
    import sys
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
        raise ConfinementBlocked('Rust tests require macOS sandbox-exec')
    check_profiles.runner_timeout(timeout)
    if type(output_limit) is not int or not 0 < output_limit <= 65536:
        raise Rejected('Verification limits exceed host bounds')
    expected = compiler['identity'] if compiler else task.get('compiler')
    if not expected or task['argv'] != argv(Path(expected['root']), task['argv'][-1]):
        raise Rejected('Rust tests require the exact scope-bound compiler and argv')
    empty = dict(exit_status=-1, stopped='cancelled', stdout='', stderr='', check_profile=PROFILE,
                 scope_argv=task['argv'], kind='tests', tests_executed=False, phases=[])
    if cancel is not None and cancel.is_set(): return empty
    with tempfile.TemporaryDirectory(prefix='dervo-rust-tests-') as temporary:
        base = Path(temporary).resolve()
        source, output, runtime = [base / name for name in ('source', 'output', 'runtime')]
        for path in (source, output, runtime): path.mkdir()
        actual = collect(runtime, root=expected['root'])
        if actual != expected:
            raise Rejected('Rust compiler, linker, standard libraries or SDK changed since scope approval')
        if cancel is not None and cancel.is_set(): return empty
        for name, snapshot in snapshots.items():
            TaskEvidence._relative(name)
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(snapshot['bytes_b64'], validate=True)
            if hashlib.sha256(data).hexdigest() != snapshot['sha256']:
                raise Rejected('Rust source snapshot hash does not match')
            target.write_bytes(data); target.chmod(0o400)
        executable = output / 'tests'
        linker = runtime / 'lib/rustlib' / actual['target'] / 'bin/rust-lld'
        command = [*argv(runtime, task['argv'][-1])[:-3],
                   '-C', 'linker=' + str(linker), '-C', 'linker-flavor=ld64.lld',
                   '-C', 'codegen-units=1', '-C', 'debuginfo=0',
                   '-C', 'link-arg=-syslibroot', '-C', 'link-arg=' + str(runtime / 'sdk'),
                   '-o', str(executable), task['argv'][-1]]
        policy = compile_policy(source, output, runtime, actual)
        if cancel is not None and cancel.is_set(): return empty
        start = time.monotonic()
        built = _capture_confined(source, command, policy, PROFILE, set(), timeout=timeout,
                                  output_limit=output_limit, cancel=cancel, file_limit=OUTPUT_CAP,
                                  sdkroot=runtime / 'sdk')
        built['phase'] = 'compile'
        result = dict(built, compiler=actual, scope_argv=task['argv'], kind='tests', tests_executed=False,
                      phases=[phase_record(built)], policy_sha256=hashlib.sha256(policy.encode()).hexdigest(),
                      cwd=str(source), output_directory=str(output))
        if built['exit_status'] or built['stopped']: return result
        if not executable.is_file() or executable.is_symlink() or executable.stat().st_size > OUTPUT_CAP:
            result['stopped'] = 'test_executable_missing'
            return result
        executable.chmod(0o500)
        result['test_executable_sha256'] = hashlib.sha256(executable.read_bytes()).hexdigest()
        remaining = timeout - (time.monotonic() - start)
        remaining_output = output_limit - len(built['stdout'].encode()) - len(built['stderr'].encode())
        if remaining <= 0 or remaining_output <= 0 or (cancel is not None and cancel.is_set()):
            result['stopped'] = ('cancelled' if cancel is not None and cancel.is_set() else
                                 'timeout' if remaining <= 0 else 'output_limit')
            return result
        policy = test_policy(source, executable)
        tested = _capture_confined(source, [str(executable), '--test-threads=1'], policy, PROFILE,
                                   {str(executable)}, timeout=remaining, output_limit=remaining_output, cancel=cancel,
                                   signal_is_result=True)
        tested['phase'] = 'test'
        result['phases'].append(phase_record(tested))
        result.update(argv=tested['argv'], exit_status=tested['exit_status'], stopped=tested['stopped'],
                      stdout=built['stdout'] + tested['stdout'], stderr=built['stderr'] + tested['stderr'],
                      phase='test', tests_executed=True, test_policy_sha256=hashlib.sha256(policy.encode()).hexdigest())
        if tested['exit_status'] == 0 and not tested['stopped'] and not PASSED.search(tested['stdout']):
            # A courtesy check on libtest output, not proof against a program that
            # intentionally spoofs its output or calls process::exit itself.
            result['stopped'] = 'no_tests_reported'
        return result
