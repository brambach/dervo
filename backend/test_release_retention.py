"""Disposable release slots, independent build processes and preserved adjacent data."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from release_builder import build
from release_retention import APP_LIMIT, ReleaseSlot, inventory, status


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.releases = self.root/'releases'

    def app(self, number):
        folder = self.releases/str(number)
        (folder/'Dervo Local.app/Contents').mkdir(parents=True)
        (folder/'source.tar.gz').write_bytes(b'keep source')
        (folder/'build.log').write_text('keep log')
        (folder/'preview-data').mkdir()
        (folder/'preview-data/conversations.sqlite3').write_bytes(b'keep workspace')
        return folder

    def test_read_only_count_includes_failed_apps_and_preserves_every_entry(self):
        self.assertEqual(inventory(self.releases)['retainedApps'], 0)
        self.assertFalse(self.releases.exists())
        for n in range(APP_LIMIT):self.app(n)
        before={str(p):p.read_bytes() for p in self.releases.rglob('*') if p.is_file()}
        report=inventory(self.releases)
        self.assertEqual(report['retainedApps'], APP_LIMIT)
        self.assertFalse(report['canBuild'])
        with self.assertRaisesRegex(RuntimeError,'limit is 8'):
            with ReleaseSlot(self.releases).acquire():self.fail('Limit bypassed')
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.releases.rglob('*') if p.is_file() and p.name!='.build-slots.lock'})

    def test_limit_stops_builder_before_snapshot_cache_or_output(self):
        for n in range(APP_LIMIT):self.app(n)
        with patch('release_builder.BuildStorage') as storage,patch('release_builder.require_space'):
            with self.assertRaisesRegex(RuntimeError,'saved Dervo apps'):
                build(self.root/'source',self.releases/'new',{})
            storage.assert_not_called()
        self.assertFalse((self.releases/'new').exists())

    def test_unreadable_or_linked_release_storage_never_looks_empty(self):
        self.releases.mkdir()
        (self.releases/'linked').symlink_to(self.root/'absent')
        self.assertFalse(status(self.releases)['canBuild'])
        self.assertIsNone(status(self.releases)['retainedApps'])
        (self.releases/'linked').unlink()
        self.app(0)
        target=self.releases/'0/Dervo Local.app'
        target.rename(self.root/'original-app')
        target.symlink_to(self.root/'original-app',target_is_directory=True)
        self.assertFalse(status(self.releases)['canBuild'])
        self.assertTrue((self.root/'original-app/Contents').is_dir())

    def test_two_builders_cannot_reserve_the_last_slot(self):
        for n in range(APP_LIMIT-1):self.app(n)
        code='from release_retention import ReleaseSlot; import sys; s=ReleaseSlot(sys.argv[1]);\nwith s.acquire(): print("reserved",flush=True)'
        with ReleaseSlot(self.releases).acquire():
            result=subprocess.run([sys.executable,'-B','-c',code,str(self.releases)],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Another preview',result.stderr)
            self.app(APP_LIMIT-1)
        with self.assertRaisesRegex(RuntimeError,'limit is 8'):
            with ReleaseSlot(self.releases).acquire():self.fail('Last slot reused')

    def test_non_regular_lock_is_rejected_without_blocking(self):
        self.releases.mkdir()
        os.mkfifo(self.releases/'.build-slots.lock')
        with self.assertRaisesRegex(RuntimeError,'lock needs review'):
            with ReleaseSlot(self.releases).acquire():self.fail('FIFO accepted as lock')

    def test_child_retains_reservation_after_builder_dies(self):
        code='''
import subprocess,sys,time
from release_retention import ReleaseSlot
with ReleaseSlot(sys.argv[1]).acquire() as slot:
 child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],pass_fds=slot.child_fds())
 print(child.pid,flush=True)
 child.wait()
'''
        process=subprocess.Popen([sys.executable,'-B','-c',code,str(self.releases)],stdout=subprocess.PIPE,text=True,start_new_session=True)
        try:
            child=int(process.stdout.readline().strip())
            process.kill();process.wait(timeout=5)
            os.kill(child,0)
            with self.assertRaisesRegex(RuntimeError,'Another preview'):
                with ReleaseSlot(self.releases).acquire():self.fail('Child lost the slot')
        finally:
            import signal
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait(timeout=5);process.stdout.close()


if __name__=='__main__':unittest.main()
