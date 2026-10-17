"""Rust metadata checks. Host tests are opt-in and never turn confinement failures into skips."""
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_profiles
from dervo_connections import Rejected

# Rust checks stage a Mach-O rustc and an apple-darwin sysroot and run under sandbox-exec, so they only exist on a Mac.
# Windows lists both Rust profiles as unavailable (RustOnWindowsTests); these classes would only fail in their fixtures.
MAC_ONLY = unittest.skipUnless(sys.platform == 'darwin', 'Rust checks run only on a Mac')


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
class RustOnWindowsTests(unittest.TestCase):
    def test_rust_profiles_are_listed_unavailable_and_refused(self):
        for profile_id in check_profiles.RUST_PROFILES:
            entry = next(e for e in check_profiles.catalogue() if e['id'] == profile_id)
            self.assertFalse(entry['available'])
            with self.assertRaises(Rejected):
                check_profiles.profile(profile_id)


class RustContractTests(unittest.TestCase):
    @MAC_ONLY
    def test_catalogue_and_root_name(self):
        entry = next(e for e in check_profiles.catalogue() if e['id'] == 'rust-check')
        self.assertEqual(entry['label'], 'Rust type check')
        for name in ('check.rs', 'launch.rs', 'test_valid.rs'):
            self.assertTrue(check_profiles.test_pattern('rust-check').fullmatch(name))
        for name in ('../check.rs', 'src/check.rs', '-check.rs', 'check.rs --test'):
            self.assertFalse(check_profiles.test_pattern('rust-check').fullmatch(name))

    def test_missing_and_malformed_runtime(self):
        import rust_checks
        for value in ('', 'relative', '/does-not-exist'):
            with self.subTest(value=value), patch.dict(os.environ, DERVO_RUST_TOOLCHAIN=value):
                with self.assertRaises(Rejected):
                    rust_checks.identity()

    @MAC_ONLY
    def test_fixed_argv(self):
        import rust_checks
        with patch.object(rust_checks, 'toolchain_root', return_value=Path('/trusted/rust')):
            argv = check_profiles.argv_for('rust-check', 'check.rs')
            self.assertIn('--emit=metadata', argv)
            self.assertNotIn('--test', argv)
            self.assertEqual(check_profiles.profile_of(argv), 'rust-check')
            for extra in ('--test', '--extern', '-C', '--emit=link'):
                with self.assertRaises(Rejected):
                    check_profiles.profile_of(argv + [extra])


def snapshot(data):
    import base64
    import hashlib
    if isinstance(data, str):
        data = data.encode()
    return dict(bytes_b64=base64.b64encode(data).decode(), sha256=hashlib.sha256(data).hexdigest())


@MAC_ONLY
class RustBindingTests(unittest.TestCase):
    def setUp(self):
        import rust_checks
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'bin').mkdir()
        (self.root / 'lib').mkdir()
        target = {'arm64': 'aarch64', 'x86_64': 'x86_64'}[__import__('platform').machine()]
        self.syslib = self.root / 'lib/rustlib' / (target + '-apple-darwin') / 'lib'
        self.syslib.mkdir(parents=True)
        (self.root / 'bin/rustc').write_bytes(b'\xcf\xfa\xed\xfe' + b'fixture')
        (self.root / 'bin/rustc').chmod(0o755)
        (self.root / 'lib/librustc_driver-fixture.dylib').write_bytes(b'driver')
        (self.syslib / 'libstd-fixture.rmeta').write_bytes(b'metadata')
        self.env = patch.dict(os.environ, DERVO_RUST_TOOLCHAIN=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.identity = rust_checks.identity()

    def test_compiler_sysroot_substitutions_and_read_only_staging(self):
        import rust_checks
        import task_evidence
        task = dict(check_profile='rust-check', compiler=self.identity,
                    argv=rust_checks.argv(self.root, 'check.rs'))
        for path in (self.root / 'bin/rustc', self.syslib / 'libstd-fixture.rmeta'):
            original = path.read_bytes()
            path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaisesRegex(Rejected, 'changed'):
                task_evidence.TaskEvidence._compiler_inputs(task)
            with patch.object(task_evidence, '_capture_confined', side_effect=AssertionError('must not launch')):
                with self.assertRaisesRegex(Rejected, 'changed'):
                    rust_checks.verify(task, {'check.rs': snapshot('pub fn ok() {}')})
            path.write_bytes(original)
        with tempfile.TemporaryDirectory() as dest:
            self.assertEqual(rust_checks.collect(dest), self.identity)
            self.assertEqual((Path(dest) / 'bin/rustc').stat().st_mode & 0o777, 0o500)
            self.assertFalse(list(Path(dest).rglob('*.rlib')))

    def test_malformed_symlink_world_writable_and_caps(self):
        import rust_checks
        compiler = self.root / 'bin/rustc'
        compiler.write_bytes(b'#!/bin/sh\nexit 0')
        with self.assertRaisesRegex(Rejected, 'Mach-O'):
            rust_checks.identity()
        compiler.write_bytes(b'\xcf\xfa\xed\xfe' + b'fixture')
        compiler.chmod(0o777)
        with self.assertRaises(Rejected):
            rust_checks.identity()
        compiler.chmod(0o755)
        with patch.object(rust_checks, 'BYTE_CAP', 1), self.assertRaises(Rejected):
            rust_checks.identity()
        with patch.object(rust_checks, 'FILE_CAP', 1), self.assertRaises(Rejected):
            rust_checks.identity()
        meta = self.syslib / 'libstd-fixture.rmeta'
        meta.unlink()
        meta.symlink_to(self.root / 'bin/rustc')
        with self.assertRaisesRegex(Rejected, 'symlink'):
            rust_checks.identity()

    def test_policy_denies_children_network_and_source_writes(self):
        import rust_checks
        policy = rust_checks.policy(Path('/source'), Path('/output'), Path('/runtime'), self.identity)
        self.assertIn('(deny network*)', policy)
        self.assertIn('(deny process-fork)', policy)
        self.assertIn('(allow process-exec (literal "/runtime/bin/rustc"))', policy)
        self.assertIn('(allow file-read* file-write* (subpath "/output"))', policy)
        self.assertNotIn('file-write* (subpath "/source")', policy)
        self.assertNotIn('allow network', policy)
        self.assertNotIn('proc_macro', policy)

    def test_cancel_before_launch_and_cleanup_after_error(self):
        import threading
        import rust_checks
        import task_evidence
        task = dict(compiler=self.identity, argv=rust_checks.argv(self.root, 'check.rs'))
        event = threading.Event()
        event.set()
        with patch.object(task_evidence, '_capture_confined', side_effect=AssertionError('launch')):
            result = rust_checks.verify(task, {}, event)
        self.assertEqual(result['stopped'], 'cancelled')
        roots = []
        def fail(root, *args, **kwargs):
            roots.append(root.parent)
            raise RuntimeError('capture failed')
        with patch.object(task_evidence, '_capture_confined', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'capture failed'):
                rust_checks.verify(task, {'check.rs': snapshot('pub fn ok() {}')})
        self.assertTrue(roots)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_snapshot_result_requires_metadata_and_preserves_stopped_reason(self):
        import rust_checks
        import task_evidence
        task = dict(compiler=self.identity, argv=rust_checks.argv(self.root, 'check.rs'))
        for reason in (None, 'cancelled', 'timeout', 'output_limit'):
            result = dict(exit_status=0, stopped=reason, stdout='', stderr='')
            with patch.object(task_evidence, '_capture_confined', return_value=result):
                actual = rust_checks.verify(task, {'check.rs': snapshot('pub fn ok() {}')})
            self.assertEqual(actual['stopped'], reason or 'metadata_missing')
            self.assertFalse(actual['tests_executed'])


@MAC_ONLY
class RustHostTests(unittest.TestCase):
    """Run on the host with a configured compiler. Missing runtime or sandbox is an ERROR, never a skip.

    The worker runs the two deterministic classes explicitly. The coordinator runs
    the whole module, which includes these real confined rustc invocations.
    """
    @classmethod
    def setUpClass(cls):
        import rust_checks
        cls.identity = rust_checks.identity()

    def run_check(self, data, extras=None, **kwargs):
        import rust_checks
        task = dict(check_profile='rust-check', compiler=self.identity,
                    argv=rust_checks.argv(Path(self.identity['root']), 'check.rs'))
        sources = {'check.rs': snapshot(data)}
        sources.update({name: snapshot(value) for name, value in (extras or {}).items()})
        result = rust_checks.verify(task, sources, **kwargs)
        print('RUST REAL RESULT', __import__('json').dumps(result, sort_keys=True), flush=True)
        self.assertFalse(Path(result['cwd']).exists(), 'source snapshot must be cleaned')
        self.assertFalse(Path(result['output_directory']).exists(), 'metadata must be cleaned')
        return result

    def test_real_valid_and_type_error(self):
        result = self.run_check('pub fn value() -> u32 { 42 }')
        self.assertEqual((result['exit_status'], result['stopped']), (0, None))
        self.assertFalse(result['tests_executed'])
        result = self.run_check('pub fn value() -> u32 { "wrong" }')
        self.assertNotEqual(result['exit_status'], 0)
        self.assertIn('mismatched types', result['stderr'])

    def test_real_unchanged_dervo_launch_module(self):
        # Copy all imports with the unchanged module. No native crate, linker or #[test] execution.
        path = Path(__file__).resolve().parents[1] / 'src/launch.rs'
        data = path.read_bytes()
        before = __import__('hashlib').sha256(data).hexdigest()
        self.assertEqual(before, '7390eab9f4986db3dbc85760ad79b0a6707c8f278d4c7a7db4da2c89751c9c84', 'representative source changed from the assigned frozen input')
        result = self.run_check('pub mod launch;', {'launch.rs': data})
        self.assertEqual((result['exit_status'], result['stopped']), (0, None))
        self.assertEqual(__import__('hashlib').sha256(path.read_bytes()).hexdigest(), before)
        print('UNCHANGED DERVO MODULE SHA256', before, flush=True)

    def test_real_compile_time_include_inside_and_protected_outside(self):
        result = self.run_check('pub const X: &str = include_str!("inside.txt");', {'inside.txt': 'snapshot input'})
        self.assertEqual((result['exit_status'], result['stopped']), (0, None))
        with tempfile.TemporaryDirectory() as temp:
            protected = Path(temp).resolve() / 'protected'
            protected.write_text('synthetic protected input')
            for expression in (f'include_str!({__import__("json").dumps(str(protected))})',):
                # Confirm that this exact synthetic include succeeds without confinement.
                # Otherwise a missing file or compiler failure could masquerade as access denial.
                source = 'pub const X: &str = ' + expression + ';'
                control = Path(temp).resolve() / 'check.rs'
                control.write_text(source)
                import rust_checks
                command = rust_checks.argv(Path(self.identity['root']), 'check.rs', str(Path(temp).resolve() / 'control.rmeta'))
                unconfined = subprocess.run(command, cwd=control.parent, env={'HOME': ''},
                                           capture_output=True, timeout=5)
                self.assertEqual(unconfined.returncode, 0, unconfined.stderr.decode(errors='replace'))
                self.assertTrue((control.parent / 'control.rmeta').is_file())
                result = self.run_check(source)
                self.assertNotEqual(result['exit_status'], 0)
                self.assertRegex(result['stderr'], 'Operation not permitted|Permission denied')
            self.assertEqual(protected.read_text(), 'synthetic protected input')

    def test_real_no_proc_macro_or_dependency_loading(self):
        for data in ('extern crate arbitrary_dependency;',
                     'extern crate proc_macro; #[proc_macro] pub fn forbidden(x: proc_macro::TokenStream) -> proc_macro::TokenStream { x }'):
            result = self.run_check(data)
            self.assertNotEqual(result['exit_status'], 0)

    def test_real_timeout_and_output_bound(self):
        result = self.run_check('pub fn ok() {}', timeout=0.000001)
        self.assertEqual(result['stopped'], 'timeout')
        result = self.run_check('\n'.join(f'compile_error!("diagnostic {i}");' for i in range(1000)), output_limit=128)
        self.assertEqual(result['stopped'], 'output_limit')
        self.assertLessEqual(len(result['stdout'].encode()) + len(result['stderr'].encode()), 128)

    def test_real_compile_time_write_denial(self):
        # Host-only adversarial -o overrides test actual compiler writes with the identical policy.
        # Providers cannot select these argv; the production runner always selects output/check.rmeta.
        import rust_checks
        from task_evidence import _capture_confined
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            source, output, runtime = (base / n for n in ('source', 'output', 'runtime'))
            for path in (source, output, runtime):
                path.mkdir()
            identity = rust_checks.collect(runtime)
            file = source / 'check.rs'
            file.write_text('pub fn ok() {}')
            original = file.read_bytes()
            other = source / 'other.rs'
            other.write_text('preserved source')
            for destination in (base / 'outside.rmeta', other):
                command = rust_checks.argv(runtime, 'check.rs', str(destination))
                result = _capture_confined(source, command, rust_checks.policy(source, output, runtime, identity),
                                           'rust-check', set(), file_limit=rust_checks.OUTPUT_CAP)
                print('RUST WRITE DENIAL', __import__('json').dumps(result), flush=True)
                self.assertNotEqual(result['exit_status'], 0)
                self.assertRegex(result['stderr'], 'Operation not permitted|Permission denied|input file would be overwritten')
            self.assertFalse((base / 'outside.rmeta').exists())
            self.assertEqual(file.read_bytes(), original)
            self.assertEqual(other.read_text(), 'preserved source')
            bounded = output / 'bounded.rmeta'
            result = _capture_confined(source, rust_checks.argv(runtime, 'check.rs', str(bounded)),
                                       rust_checks.policy(source, output, runtime, identity), 'rust-check', set(), file_limit=32)
            print('RUST METADATA FILE CAP', __import__('json').dumps(result), flush=True)
            self.assertNotEqual(result['exit_status'], 0)
            self.assertLessEqual(bounded.stat().st_size if bounded.exists() else 0, 32)


    def test_real_cancellation_during_metadata_check(self):
        import threading
        import task_evidence
        actual_capture = task_evidence._capture_confined
        event = threading.Event()
        def capture(*args, **kwargs):
            timer = threading.Timer(0.1, event.set)
            timer.start()
            try:
                return actual_capture(*args, **kwargs)
            finally:
                timer.cancel()
                timer.join()
        with patch.object(task_evidence, '_capture_confined', side_effect=capture):
            result = self.run_check('#![allow(long_running_const_eval)]\npub const X:u64 = {let mut i=0; while i<1000000000 {i+=1;} i};', cancel=event)
        self.assertEqual(result['stopped'], 'cancelled')
