"""Real confined Rust execution and temporary-store integration. No provider calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import check_profiles
from dervo_connections import Rejected
import rust_checks
import rust_tests
import task_evidence
from test_rust_checks import snapshot
from test_rust_setup import RustSetupTests


class RustExecutionBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RustSetupTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.compiler
        target = rust_checks.identity(str(self.root))['target']
        self.libs = self.root / 'lib/rustlib' / target / 'lib'
        for name in ('libtest-fixture.rlib', 'libstd-fixture.rlib'):
            (self.libs / name).write_bytes(b'archive-fixture')
        self.linker = self.root / 'lib/rustlib' / target / 'bin/rust-lld'
        self.linker.parent.mkdir()
        self.linker.write_bytes(b'\xcf\xfa\xed\xfefixture'); self.linker.chmod(0o755)
        self.sdk = self.fixture.base / 'SDKs/MacOSX15.sdk'
        (self.sdk / 'usr/lib').mkdir(parents=True)
        for name in ('libSystem.tbd', 'libc.tbd', 'libm.tbd'):
            (self.sdk / 'usr/lib' / name).write_bytes(b'stub-fixture')
        sdk_link = self.sdk.parent / 'MacOSX.sdk'; sdk_link.symlink_to(self.sdk, target_is_directory=True)
        self.override = patch.object(rust_tests, 'SDK_LINK', sdk_link)
        self.override.start(); self.addCleanup(self.override.stop)

    def grant(self, **fields):
        return self.fixture.grant(checkProfile='rust-test', **fields)

    def task(self):
        return self.fixture.host.dispatch('task_create', dict(self.fixture.scope, text='Run Rust tests',
            files=['check.rs'], testFile='check.rs', checkProfile='rust-test', rustToolchain=str(self.root), checkTimeoutSeconds=30))

    def test_profile_grant_request_and_immutable_runtime_inputs(self):
        saved = self.grant(checkTimeoutSeconds=30)
        self.assertEqual(saved['check_profile'], 'rust-test')
        with patch('coding_integration.CodingTasks.start'):
            result = self.fixture.host.dispatch('coding_request', dict(self.fixture.scope, clientId='rust-test-1', text='Run the Rust tests'))
        evidence = self.fixture.host.coding.evidence.read(result['task_id'], **self.fixture.ctx)
        self.assertEqual(evidence['argv'], rust_tests.argv(self.root, 'check.rs'))
        self.assertEqual(evidence['check_timeout_seconds'], 30)
        self.assertEqual(evidence['compiler'], rust_tests.identity(str(self.root)))
        for path in (self.linker, self.libs / 'libtest-fixture.rlib', self.sdk / 'usr/lib/libc.tbd'):
            old = path.read_bytes(); path.write_bytes(old + b'changed')
            with self.assertRaisesRegex(Rejected, 'changed since scope'):
                task_evidence.TaskEvidence._compiler_inputs(evidence)
            path.write_bytes(old)

    def test_changed_profile_preserves_the_old_tasks_exact_choice(self):
        self.grant(checkTimeoutSeconds=30); task = self.task(); task['origin'] = 'conversation'
        self.fixture.host.coding.save(task)
        self.fixture.host.coding.require_grant(task)
        self.fixture.grant(checkProfile='rust-check')
        with self.assertRaisesRegex(Rejected, 'no longer permits the Rust check'):
            self.fixture.host.coding.require_grant(task)
        self.assertEqual(self.fixture.host.coding.evidence.read(task['id'], **self.fixture.ctx)['check_profile'], 'rust-test')

    def test_symlinks_modes_caps_and_lightweight_observation(self):
        expected = rust_tests.identity(str(self.root))
        for path in (self.linker, self.libs / 'libtest-fixture.rlib'):
            original = path.read_bytes(); mode = path.stat().st_mode & 0o777
            path.unlink(); path.symlink_to(self.root / 'bin/rustc')
            with self.assertRaises(Rejected): rust_tests.identity(str(self.root))
            path.unlink(); path.write_bytes(original); path.chmod(mode)
        with patch.object(rust_checks, 'BYTE_CAP', 1), self.assertRaises(Rejected):
            rust_tests.identity(str(self.root))
        with tempfile.TemporaryDirectory() as destination:
            self.assertEqual(rust_tests.collect(destination, root=str(self.root)), expected)
            self.assertEqual(len(list(Path(destination).rglob('*.dylib'))), 1)  # Only the selected compiler driver.
            self.assertFalse(list((Path(destination) / 'lib/rustlib').rglob('*.dylib')))
        with patch.object(rust_tests.hashlib, 'sha256', side_effect=AssertionError('observation hashed bytes')):
            observed = rust_tests.availability(str(self.root))
        self.assertTrue(all('sha256' not in r for r in observed['files'].values()))

    def test_cancel_during_runtime_copy_never_starts_compilation(self):
        compiler=rust_tests.identity(str(self.root));event=threading.Event()
        def copied(*args,**kwargs):event.set();return compiler
        task=dict(compiler=compiler,argv=rust_tests.argv(self.root,'check.rs'))
        with patch.object(rust_tests,'collect',side_effect=copied), patch.object(task_evidence,'_capture_confined',side_effect=AssertionError('cancelled compile launched')):
            result=rust_tests.verify(task,{'check.rs':snapshot('#[test] fn t(){}')},cancel=event)
        self.assertEqual(result['stopped'],'cancelled');self.assertFalse(result['tests_executed'])


class RustExecutionHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = rust_tests.identity()

    def verify(self, source, *, extras=None, **kwargs):
        task = dict(check_profile='rust-test', compiler=self.compiler,
                    argv=rust_tests.argv(Path(self.compiler['root']), 'check.rs'))
        result = rust_tests.verify(task, {'check.rs': snapshot(source), **{k:snapshot(v) for k,v in (extras or {}).items()}}, **kwargs)
        print('RUST_EXECUTION', json.dumps({k:v for k,v in result.items() if k not in ('compiler','runtime_files')}, sort_keys=True), flush=True)
        if 'cwd' in result:
            self.assertFalse(Path(result['cwd']).exists())
            self.assertFalse(Path(result['output_directory']).exists())
        return result

    def test_actual_pass_fail_zero_and_compile_error(self):
        for source, expected, ran in [
            ('#[test] fn valid(){assert_eq!(2+2,4);}', None, True),
            ('#[test] fn fails(){assert_eq!(2+2,5);}', None, True),
            ('pub fn no_tests(){}', 'no_tests_reported', True),
            ('#[test] fn wrong(){let _:u32="wrong";}', None, False),
        ]:
            result = self.verify(source, timeout=15)
            self.assertEqual(result['stopped'], expected)
            self.assertEqual(result['tests_executed'], ran)
            self.assertEqual(result['exit_status'] == 0, 'fails' not in source and 'wrong' not in source)

    def test_real_test_denials_have_an_unconfined_negative_control(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve(); protected = base / 'protected'; protected.write_text('synthetic protected bytes')
            written = base / 'written'
            source = '''
fn denied<T>(value:std::io::Result<T>){assert!(matches!(value.err().and_then(|e|e.raw_os_error()),Some(1)|Some(13)));}
#[test] fn protected_read(){denied(std::fs::read(PROTECTED));}
#[test] fn outside_write(){denied(std::fs::write(WRITTEN,b"synthetic"));}
#[test] fn snapshot_write(){denied(std::fs::write("local-write",b"synthetic"));}
#[test] fn network(){denied(std::net::TcpStream::connect("127.0.0.1:9"));}
#[test] fn child(){denied(std::process::Command::new("/usr/bin/true").status());}
#[test] fn runtime_environment(){assert_eq!(std::env::var("HOME").unwrap(),"");assert!(std::env::var("PATH").is_err());}
'''.replace('PROTECTED', json.dumps(str(protected))).replace('WRITTEN', json.dumps(str(written)))
            capture = task_evidence._capture_confined; controls=[]
            def checked(root, command, *args, **kwargs):
                if command[-1] == '--test-threads=1':
                    # These exact six host-authored tests touch only temporary
                    # files, loopback port 9 and /usr/bin/true. No project code.
                    result = subprocess.run(command, cwd=root, env={'HOME':''}, capture_output=True, timeout=10, text=True)
                    controls.append(result.stdout)
                    self.assertEqual(result.returncode, 101)
                    for name in ('protected_read','outside_write','snapshot_write','network','child'):
                        self.assertIn('test '+name+' ... FAILED', result.stdout)
                    self.assertTrue(written.is_file()); written.unlink()
                    (Path(root) / 'local-write').unlink()
                return capture(root, command, *args, **kwargs)
            with patch.object(task_evidence, '_capture_confined', side_effect=checked):
                result = self.verify(source, timeout=20)
            self.assertTrue(controls); print('UNCONFINED_NEGATIVE_CONTROL', controls[0], flush=True)
            self.assertEqual((result['exit_status'], result['stopped']), (0,None))
            self.assertIn('6 passed', result['stdout']); self.assertFalse(written.exists())
            self.assertEqual(protected.read_text(), 'synthetic protected bytes')

    def test_compile_cannot_read_outside_or_load_external_crates(self):
        with tempfile.TemporaryDirectory() as directory:
            protected = Path(directory).resolve() / 'outside'; protected.write_text('synthetic')
            source = 'const VALUE:&str=include_str!('+json.dumps(str(protected))+'); #[test] fn check(){assert!(!VALUE.is_empty());}'
            result = self.verify(source, timeout=15)
            self.assertFalse(result['tests_executed']); self.assertNotEqual(result['exit_status'],0)
            self.assertRegex(result['stderr'],'Operation not permitted|Permission denied')
        result = self.verify('extern crate unavailable_dependency; #[test] fn t(){}',timeout=15)
        self.assertFalse(result['tests_executed']); self.assertNotEqual(result['exit_status'],0)

    def test_shared_deadline_and_cancel_during_real_test(self):
        capture = task_evidence._capture_confined; seen=[]; event=threading.Event()
        def checked(root, command, *args, **kwargs):
            seen.append(kwargs['timeout'])
            if command[-1] == '--test-threads=1':
                timer=threading.Timer(.15,event.set);timer.start()
                try:return capture(root,command,*args,**kwargs)
                finally:timer.cancel();timer.join()
            return capture(root,command,*args,**kwargs)
        with patch.object(task_evidence, '_capture_confined', side_effect=checked):
            result=self.verify('#[test] fn waits(){std::thread::sleep(std::time::Duration::from_secs(60));}',timeout=15,cancel=event)
        self.assertEqual(result['stopped'],'cancelled');self.assertEqual(result['exit_status'],-9)
        self.assertTrue(result['tests_executed']);self.assertEqual(seen[0],15);self.assertLess(seen[1],seen[0])
        result=self.verify('#[test] fn waits(){std::thread::sleep(std::time::Duration::from_secs(60));}',timeout=5)
        self.assertEqual(result['stopped'],'timeout')

    def test_prelaunch_cancel_and_real_output_bound(self):
        event=threading.Event();event.set()
        with patch.object(rust_tests,'collect',side_effect=AssertionError('cancelled launch')):
            result=self.verify('#[test] fn t(){}',cancel=event)
        self.assertEqual(result['stopped'],'cancelled');self.assertFalse(result['tests_executed'])
        result=self.verify('#[test] fn output(){panic!("{}","x".repeat(100000));}',timeout=15,output_limit=512)
        self.assertEqual(result['stopped'],'output_limit')
        self.assertLessEqual(len(result['stdout'].encode())+len(result['stderr'].encode()),512)

    def test_aborting_test_keeps_the_actual_compile_and_signal_outcome(self):
        import signal
        result=self.verify('#[test] fn aborts(){std::process::abort();}',timeout=15)
        self.assertEqual(result['exit_status'],-signal.SIGABRT)
        self.assertTrue(result['tests_executed']);self.assertEqual(result['phase'],'test')
        self.assertEqual([p['exit_status'] for p in result['phases']],[0,-signal.SIGABRT])
        self.assertEqual(result['argv'],result['phases'][1]['argv'])
        self.assertNotEqual(result['argv'],result['phases'][0]['argv'])
        self.assertNotIn('Python startup',result['stderr'])

    def test_real_host_worker_records_tests_and_keeps_the_result_after_restart(self):
        from host import Host, ACTOR
        from coding_integration import provider_check_result
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory).resolve(); project=base/'project';project.mkdir()
            file=project/'check.rs';file.write_text('#[test] fn later_task_uses_sorted_copy(){let original=vec![3,1,2];let mut ordered=original.clone();ordered.sort();assert_eq!(ordered,vec![1,2,3]);assert_eq!(original,vec![3,1,2]);}')
            original=file.read_bytes();host=Host(base/'store')
            try:
                host.store.add_profile('p','codex',base/'profile','synthetic@example.test','personal')
                host.store.add_project('p',project,'p','synthetic-model')
                host.store.add_thread('c','p','c','private',ACTOR)
                scope=dict(projectId='p',channelId='c');ctx=dict(project_id='p',thread_id='c',actor=ACTOR)
                task=host.dispatch('task_create',dict(scope,text='Run Rust tests',files=['check.rs'],testFile='check.rs',checkProfile='rust-test',rustToolchain=self.compiler['root'],checkTimeoutSeconds=30))
                evidence=host.coding.evidence
                evidence.approve(task['id'],scope_version=task['scope_version'],**ctx)
                attempt=evidence.verify_current(task['id'],**ctx)
                job=evidence.launch(task['id'],attempt_number=attempt['number'],**ctx)
                deadline=time.monotonic()+40
                while time.monotonic()<deadline:
                    result=evidence.poll(task['id'],job_id=job,**ctx)
                    if result['state']!='running':break
                    time.sleep(.02)
                self.assertEqual(result['state'],'verified',result.get('blocker'))
                self.assertTrue(result['test']['tests_executed']);self.assertEqual(result['test']['check_profile'],'rust-test')
                self.assertEqual(result['test']['check_timeout_seconds'],30)
                # A long real diagnostic still reaches the model through bounded
                # top-level projection, without a second copy nested in phases.
                result['test']['stdout']='x'*64000
                payload=provider_check_result(task['id'],result)
                self.assertLess(len(json.dumps(payload).encode()),15000)
                self.assertEqual(payload['output_truncated'],['stdout'])
                saved=host.store.db.execute('SELECT document FROM task_evidence WHERE id=?',(task['id'],)).fetchone()[0]
                self.assertEqual(file.read_bytes(),original)
            finally:host.close()
            host=Host(base/'store')
            try:
                self.assertEqual(host.store.db.execute('SELECT document FROM task_evidence WHERE id=?',(task['id'],)).fetchone()[0],saved)
                self.assertFalse(host.turns)
            finally:host.close()
