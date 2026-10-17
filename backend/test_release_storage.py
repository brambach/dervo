"""Disposable filesystem and process checks for preview cache ownership and reuse."""
import json
import os
import plistlib
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from release_builder import build
import account_config
from test_supabase_client import PROJECT
from release_storage import BuildStorage, MIN_FREE_BYTES, require_space


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'project'
        (self.source / 'frontend/node_modules').mkdir(parents=True)
        (self.source / 'frontend/main.ts').write_text('export const version = 1;')
        (self.source / 'tauri.conf.json').write_text(json.dumps({'identifier': 'local.dervo.connected', 'version': '0.1.0'}))
        (self.source / 'Info.plist').write_bytes(plistlib.dumps({}))
        self.storage = BuildStorage(self.source, self.root / 'cache')
        account_config.stage(self.source, PROJECT)

    def test_lock_blocks_another_process_and_releases_after_owner_dies(self):
        script = '''
import sys, time
from release_storage import BuildStorage
with BuildStorage(sys.argv[1], sys.argv[2]).acquire():
    print('locked', flush=True)
    time.sleep(30)
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(self.source), str(self.root / 'cache')],
                                   stdout=subprocess.PIPE, text=True, cwd=Path(__file__).parent)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertEqual(process.stdout.readline().strip(), 'locked')
        with self.assertRaisesRegex(RuntimeError, 'Another preview'):
            with self.storage.acquire():
                self.fail('Two processes acquired the same cache')
        process.kill(); process.wait(timeout=5); process.stdout.close()
        with self.storage.acquire():
            self.assertTrue(self.storage.locked)

    def test_stale_work_cleanup_preserves_source_dependencies_and_cache(self):
        with self.storage.acquire():
            self.storage.work.mkdir()
            (self.storage.work / 'dependencies').symlink_to(self.source / 'frontend/node_modules')
            (self.source / 'frontend/node_modules/keep').write_text('dependency')
            self.storage.target.mkdir()
            (self.storage.target / 'keep').write_text('compiler artifact')
            self.storage.clean_work()
        self.assertFalse(self.storage.work.exists())
        self.assertTrue((self.storage.target / 'keep').exists())
        self.assertTrue((self.source / 'frontend/node_modules/keep').exists())
        with self.assertRaisesRegex(RuntimeError, 'Lock'):
            self.storage.clean_work()

    def test_build_child_keeps_lock_after_builder_leader_crashes(self):
        script = '''
import subprocess, sys
from release_storage import BuildStorage
with BuildStorage(sys.argv[1], sys.argv[2]).acquire() as storage:
    storage.work.mkdir()
    (storage.work / 'keep').write_text('child is using this')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], pass_fds=storage.child_fds())
    print(child.pid, flush=True)
    child.wait()
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(self.source), str(self.root / 'cache')],
                                   stdout=subprocess.PIPE, text=True, cwd=Path(__file__).parent, start_new_session=True)
        try:
            child_pid = int(process.stdout.readline().strip())
            process.kill(); process.wait(timeout=5)
            os.kill(child_pid, 0)
            with self.assertRaisesRegex(RuntimeError, 'Another preview'):
                with self.storage.acquire(): self.storage.clean_work()
            self.assertTrue((self.storage.work / 'keep').exists())
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=5); process.stdout.close()

    def test_unowned_or_redirected_storage_is_never_cleaned(self):
        self.storage.directory.mkdir(parents=True)
        self.storage.work.mkdir(); (self.storage.work / 'keep').write_text('not ours')
        with self.assertRaisesRegex(RuntimeError, 'ownership'):
            with self.storage.acquire(): pass
        self.assertTrue((self.storage.work / 'keep').exists())
        shutil.rmtree(self.storage.directory)
        with self.storage.acquire():
            self.storage.work.symlink_to(self.source, target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, 'Linked build storage'):
                self.storage.clean_work()
        self.assertTrue((self.source / 'frontend/main.ts').exists())

    def test_foreign_owner_and_lock_symlink_are_rejected(self):
        with self.storage.acquire(): pass
        owner = self.storage.directory / 'owner.json'
        owner.write_text(json.dumps({'format': 1, 'source': '/another/project'}))
        with self.assertRaisesRegex(RuntimeError, 'different project'):
            with self.storage.acquire(): pass
        lock = self.storage.directory / 'build.lock'; lock.unlink()
        lock.symlink_to(self.source / 'frontend/main.ts')
        with self.assertRaises(OSError):
            with self.storage.acquire(): pass
        self.assertEqual((self.source / 'frontend/main.ts').read_text(), 'export const version = 1;')

    def test_low_disk_space_refuses_before_creating_a_release(self):
        usage = shutil._ntuple_diskusage(MIN_FREE_BYTES * 2, MIN_FREE_BYTES + 1, MIN_FREE_BYTES - 1)
        with patch('release_storage.shutil.disk_usage', return_value=usage):
            with self.assertRaisesRegex(RuntimeError, 'GB free'):
                build(self.source, self.root / 'releases/one', {})
        self.assertFalse((self.root / 'releases').exists())
        self.assertFalse(self.storage.directory.exists())

    def fake_command(self, command, **kwargs):
        if command[0] == '/bin/cp':
            # Preserve the copy's behaviour without requiring APFS in the test.
            shutil.copytree(command[-2], command[-1])
        elif 'build' in command and '--bundles' in command:
            target = Path(kwargs['env']['CARGO_TARGET_DIR'])
            bundle = target / 'release/bundle/macos/Dervo Local.app'
            (bundle / 'Contents/Resources/runtime').mkdir(parents=True)
            shutil.copy2(Path(kwargs['cwd'])/'runtime/dervo-release-profile.json',bundle/'Contents/Resources/runtime/dervo-release-profile.json')
            shutil.copy2(Path(kwargs['cwd'])/'runtime'/account_config.NAME,bundle/'Contents/Resources/runtime'/account_config.NAME)
            (bundle / 'Contents/MacOS').mkdir()
            (bundle / 'Contents/MacOS/dervo-local').write_bytes(b'fixture executable')
            shutil.copy2(Path(kwargs['cwd'])/'Info.plist', bundle/'Contents/Info.plist')
        return subprocess.CompletedProcess(command, 0)

    def test_two_releases_reuse_one_cache_and_archive_only_source(self):
        (self.source / 'target').mkdir()
        (self.source / 'target/artifact').write_bytes(b'compiler artifact')
        with patch('release_builder.BuildStorage', return_value=self.storage), \
             patch('release_builder.shutil.which', return_value=sys.executable), \
             patch('release_builder.subprocess.run', side_effect=self.fake_command) as commands:
            first = build(self.source, self.root / 'one', {})
            self.assertFalse((self.storage.target / 'artifact').exists())
            (self.storage.target / 'artifact').write_bytes(b'updated compiler artifact')
            second = build(self.source, self.root / 'two', {}, profile='personal-experimental')
        self.assertEqual(first['release_profile']['profile'],'private-beta')
        self.assertEqual(second['release_profile']['profile'],'personal-experimental')
        self.assertFalse(first['release_profile']['openRouter'])
        self.assertTrue(second['release_profile']['openRouter'])
        self.assertLess(int(first['native_version']['buildNumber']), int(second['native_version']['buildNumber']))
        self.assertEqual(first['native_version']['releaseId'], 'one')
        self.assertEqual(second['native_version']['releaseId'], 'two')
        self.assertEqual((self.storage.target / 'artifact').read_bytes(), b'updated compiler artifact')
        copies = [c.args[0] for c in commands.call_args_list if c.args[0][0] == '/bin/cp']
        self.assertTrue(all(c.kwargs.get('pass_fds') for c in commands.call_args_list))
        self.assertEqual(sum(str(self.source / 'target') in c for c in copies), 0)
        self.assertEqual((self.source / 'target/artifact').read_bytes(), b'compiler artifact')
        self.assertFalse(self.storage.work.exists())
        for record in (first, second):
            folder = Path(record['directory'])
            self.assertEqual(record['state'], 'built')
            self.assertFalse((folder / 'source').exists())
            self.assertFalse((folder / 'target').exists())
            self.assertTrue(Path(record['bundle']).is_dir())
            with tarfile.open(record['source_archive']) as archive:
                self.assertIn('source/frontend/main.ts', archive.getnames())
                self.assertFalse(any('node_modules' in n for n in archive.getnames()))
                self.assertEqual(archive.extractfile('source/frontend/main.ts').read(), b'export const version = 1;')
        self.assertEqual(json.loads((Path(first['bundle']) / 'Contents/Resources/runtime/dervo-preview.json').read_text())['releaseId'], 'one')

    def test_mismatched_bundled_profile_fails_before_signing(self):
        def wrong_profile(command, **kwargs):
            result=self.fake_command(command, **kwargs)
            if 'build' in command and '--bundles' in command:
                from release_profiles import stage
                stage(Path(kwargs['env']['CARGO_TARGET_DIR'])/'release/bundle/macos/Dervo Local.app/Contents/Resources','personal-experimental')
            return result
        with patch('release_builder.BuildStorage', return_value=self.storage), \
             patch('release_builder.shutil.which', return_value=sys.executable), \
             patch('release_builder.subprocess.run', side_effect=wrong_profile) as commands:
            with self.assertRaisesRegex(ValueError,'differs from the requested profile'):
                build(self.source,self.root/'wrong',{})
        self.assertFalse(any(call.args[0][0]=='/usr/bin/codesign' for call in commands.call_args_list))
        self.assertFalse(self.storage.work.exists())
        self.assertEqual(json.loads((self.root/'wrong/release.json').read_text())['state'],'failed')

    def test_failed_check_retains_evidence_but_removes_temporary_source(self):
        with patch('release_builder.BuildStorage', return_value=self.storage), \
             patch('release_builder.shutil.which', return_value=sys.executable), \
             patch('release_builder.subprocess.run', return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(RuntimeError, 'feature-checks failed'):
                build(self.source, self.root / 'failed', {})
        self.assertFalse(self.storage.work.exists())
        record = json.loads((self.root / 'failed/release.json').read_text())
        self.assertEqual(record['state'], 'failed')
        self.assertTrue((self.root / 'failed/feature-checks.log').exists())
        self.assertTrue((self.root / 'failed/source.tar.gz').exists())
        self.assertTrue((self.source / 'frontend/main.ts').exists())


if __name__ == '__main__':
    unittest.main()
