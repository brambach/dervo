"""Explicit project compiler selection without provider calls or global configuration."""
import json
import os
import platform
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from host import Host, ACTOR
from dervo_connections import Rejected
import rust_checks

class RustSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.project=self.base/'project';self.project.mkdir()
        (self.project/'check.rs').write_text('pub fn value() -> u32 { 42 }')
        (self.project/'test_ok.py').write_text('import unittest')
        self.compiler=self.base/'toolchain';(self.compiler/'bin').mkdir(parents=True)
        target={'arm64':'aarch64','x86_64':'x86_64'}[platform.machine()]+'-apple-darwin'
        libs=self.compiler/'lib/rustlib'/target/'lib';libs.mkdir(parents=True)
        (self.compiler/'bin/rustc').write_bytes(b'\xcf\xfa\xed\xfefixture');(self.compiler/'bin/rustc').chmod(0o755)
        (self.compiler/'lib/librustc_driver-fixture.dylib').write_bytes(b'driver')
        (libs/'libstd-fixture.rmeta').write_bytes(b'metadata')
        env=patch.dict(os.environ,{},clear=True);env.start();self.addCleanup(env.stop)
        config=patch.object(rust_checks,'CONFIG',self.base/'absent-config');config.start();self.addCleanup(config.stop)
        self.host=Host(self.base/'data');self.addCleanup(lambda:self.host.close())
        self.host.store.add_profile('p','codex',self.base/'data/profiles/p','synthetic@example.test','personal')
        self.host.store.add_project('p',self.project,'p','synthetic-model')
        self.host.store.add_thread('c','p','c','private',ACTOR)
        self.scope=dict(projectId='p',channelId='c')
        self.ctx=dict(project_id='p',thread_id='c',actor=ACTOR)
    def grant(self,**changes):
        return self.host.dispatch('coding_permission_grant',{**self.scope,'files':['check.rs'],'testFile':'check.rs','checkProfile':'rust-check','rustToolchain':str(self.compiler),**changes})
    def task(self):
        return self.host.dispatch('task_create',{**self.scope,'text':'Check the Rust source','files':['check.rs'],'testFile':'check.rs','checkProfile':'rust-check','rustToolchain':str(self.compiler)})
    def test_save_reopen_and_bind_without_environment(self):
        saved=self.grant();self.assertEqual(saved['state'],'granted');self.assertEqual(saved['rust_toolchain'],str(self.compiler))
        self.host.close();self.host=Host(self.base/'data')
        self.assertEqual(self.host.dispatch('coding_permission',self.scope)['rust_toolchain'],str(self.compiler))
        task=self.task();evidence=self.host.coding.evidence.read(task['id'],**self.ctx)
        self.assertEqual(evidence['compiler'],rust_checks.identity(str(self.compiler)))
        self.assertEqual(task['argv'][0],str(self.compiler/'bin/rustc'))
        self.assertEqual(task['state'],'needs_permission');self.assertEqual(task['runs'],[])
        self.assertEqual(self.host.coding.evidence._compiler_inputs(evidence)['identity'],evidence['compiler'])
    def test_invalid_selection_preserves_previous_grant(self):
        self.grant();before=self.host.coding.conversation._grant('p')
        alias=self.base/'alias';alias.symlink_to(self.compiler,target_is_directory=True)
        for path in ['',None,42,'relative',str(alias),str(self.base/'missing')]:
            with self.subTest(path=path),self.assertRaises(Rejected):self.grant(rustToolchain=path)
            self.assertEqual(self.host.coding.conversation._grant('p'),before)
        with self.assertRaises(Rejected):self.grant(checkProfile='python-unittest',files=['test_ok.py'],testFile='test_ok.py')
    def test_project_compiler_and_changed_bytes_are_refused(self):
        inside=self.project/'compiler';shutil.copytree(self.compiler,inside)
        with self.assertRaisesRegex(Rejected,'outside the project'):self.grant(rustToolchain=str(inside))
        self.grant();task=self.task();evidence=self.host.coding.evidence.read(task['id'],**self.ctx)
        (self.compiler/'lib/librustc_driver-fixture.dylib').write_bytes(b'change')
        with self.assertRaisesRegex(Rejected,'changed since scope'):self.host.coding.evidence._compiler_inputs(evidence)
    def test_changed_choice_and_revocation_block_existing_conversation_task(self):
        self.grant();task=self.task();task['origin']='conversation';self.host.coding.save(task)
        self.host.coding.require_grant(task)
        other=self.base/'another';shutil.copytree(self.compiler,other)
        self.grant(rustToolchain=str(other))
        with self.assertRaisesRegex(Rejected,'toolchain changed'):self.host.coding.require_grant(task)
        self.host.dispatch('coding_permission_revoke',self.scope)
        with self.assertRaisesRegex(Rejected,'revoked'):self.host.coding.require_grant(task)
    def test_other_project_never_inherits_selection(self):
        self.grant();other=self.base/'other';other.mkdir();(other/'check.rs').write_text('pub fn ok() {}')
        self.host.store.add_project('q',other,'p','synthetic-model');self.host.store.add_thread('d','q','d','private',ACTOR)
        value=self.host.dispatch('coding_permission',dict(projectId='q',channelId='d'))
        self.assertIsNone(value['rust_toolchain'])
        with self.assertRaisesRegex(Rejected,'No configured Rust'):
            self.host.dispatch('task_create',dict(projectId='q',channelId='d',text='Check source',files=['check.rs'],testFile='check.rs',checkProfile='rust-check'))

    def test_conversation_request_carries_the_saved_compiler(self):
        self.grant()
        with patch('coding_integration.CodingTasks.start') as start:
            result=self.host.dispatch('coding_request',{**self.scope,'clientId':'request-1','text':'Check this Rust source'})
        task=self.host.coding.read(result['task_id'],self.ctx)
        self.assertEqual(task['rust_toolchain'],str(self.compiler))
        self.assertEqual(task['argv'][0],str(self.compiler/'bin/rustc'))
        self.assertEqual(start.call_count,1)
        self.assertEqual(start.call_args.args[0]['id'],task['id'])

    def test_permission_observation_is_lightweight_but_execution_rechecks_bytes(self):
        self.grant();task=self.task();evidence=self.host.coding.evidence.read(task['id'],**self.ctx)
        read=os.read;count=[]
        def tracked(fd,size):
            value=read(fd,size);count.append(len(value));return value
        with patch.object(rust_checks.os,'read',side_effect=tracked),patch.object(rust_checks,'identity',side_effect=AssertionError('observation hashed compiler')):
            value=self.host.dispatch('coding_permission',self.scope)
        self.assertEqual(value['state'],'granted');self.assertEqual(sum(count),4)
        (self.compiler/'lib/librustc_driver-fixture.dylib').write_bytes(b'change')
        self.assertEqual(self.host.dispatch('coding_permission',self.scope)['state'],'granted')
        with self.assertRaisesRegex(Rejected,'changed since scope'):self.host.coding.evidence._compiler_inputs(evidence)
        (self.compiler/'bin/rustc').unlink()
        self.assertEqual(self.host.dispatch('coding_permission',self.scope)['state'],'stale')

    def test_switching_away_from_admin_rust_blocks_the_old_check(self):
        with patch.dict(os.environ,DERVO_RUST_TOOLCHAIN=str(self.compiler)):
            fields={**self.scope,'files':['check.rs'],'testFile':'check.rs','checkProfile':'rust-check'}
            self.host.dispatch('coding_permission_grant',fields)
            task=self.host.dispatch('task_create',{**fields,'text':'Check Rust'})
            task['origin']='conversation';self.host.coding.save(task)
            self.assertIsNone(task['rust_toolchain']);self.host.coding.require_grant(task)
            self.host.dispatch('coding_permission_grant',{**self.scope,'files':['test_ok.py'],'testFile':'test_ok.py'})
            with self.assertRaisesRegex(Rejected,'no longer permits the Rust check'):self.host.coding.require_grant(task)


class ReadIdentityTests(unittest.TestCase):
    def test_reading_a_file_that_updates_its_access_time_is_not_a_change(self):
        import os, tempfile, rust_checks
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lib.rlib'
            path.write_bytes(b'x' * 64)
            before = os.stat(path)
            path.read_bytes()
            fields = list(before)
            fields[7] += 5  # st_atime: a read moved only the access time
            read = os.stat_result(fields, {'st_atime_ns': before.st_atime_ns + 5_000_000_000, 'st_mtime_ns': before.st_mtime_ns, 'st_ctime_ns': before.st_ctime_ns})
            self.assertNotEqual(tuple(read), tuple(before))
            self.assertEqual(rust_checks.file_state(read), rust_checks.file_state(before))
            path.write_bytes(b'y' * 65)
            self.assertNotEqual(rust_checks.file_state(os.stat(path)), rust_checks.file_state(before), 'a real change still counts')
