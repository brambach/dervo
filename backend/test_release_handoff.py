"""Version-backup and restore tests use private disposable workspaces only."""
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from dervo_connections import Store, Rejected
from release_handoff import WorkspaceLease, inventory, verified_backup, restore_snapshot, recover_interrupted, RECOVERY_MARKER


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.data = self.root / 'data'
        store = Store(self.data / 'conversations.sqlite3')
        store.add_profile('p', 'codex', self.root / 'provider-home', 'test@example.test', 'personal')
        store.close()
        (self.data / 'settings.json').write_text('{"name":"before"}')
        self.lease = WorkspaceLease(self.data)
        self.backup = self.root / 'backups/before'
        self.recovery = self.root / 'backups/newer-work'

    def make_backup(self):
        with self.lease.acquire():
            return self.lease.snapshot(self.backup)

    def test_live_host_excludes_backup_lease_and_backup_excludes_new_host(self):
        host = Store(self.data / 'conversations.sqlite3')
        try:
            with self.assertRaisesRegex(RuntimeError, 'still using'):
                with self.lease.acquire(): pass
        finally: host.close()
        with self.lease.acquire():
            with self.assertRaisesRegex(Rejected, 'Another Dervo host'):
                Store(self.data / 'conversations.sqlite3')

    def test_backup_preserves_data_and_links_without_copying_external_files(self):
        external = self.root / 'external'; external.mkdir()
        (external / 'private').write_text('outside the workspace')
        (self.data / 'connection-link').symlink_to(external, target_is_directory=True)
        expected = inventory(self.data)
        record = self.make_backup()
        self.assertEqual(record['files'], expected)
        self.assertEqual(verified_backup(self.backup, self.data)['state'], 'verified')
        self.assertTrue((self.backup / 'workspace/connection-link').is_symlink())
        self.assertEqual(os.readlink(self.backup / 'workspace/connection-link'), str(external))
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o700)
        self.assertNotIn('conversations.sqlite3.lock', record['files'])
        with sqlite3.connect(self.backup / 'workspace/conversations.sqlite3') as db:
            self.assertEqual(db.execute('SELECT identity FROM profiles').fetchone()[0], 'test@example.test')

    def test_nested_destinations_existing_backup_and_symlink_roots_are_rejected(self):
        with self.lease.acquire():
            with self.assertRaisesRegex(RuntimeError, 'outside'):
                self.lease.snapshot(self.data / 'backup')
            self.lease.snapshot(self.backup)
            with self.assertRaisesRegex(RuntimeError, 'new backup'):
                self.lease.snapshot(self.backup)
        (self.root / 'linked-data').symlink_to(self.data, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, 'linked parent'):
            WorkspaceLease(self.root / 'linked-data')

    def test_backup_change_and_wrong_workspace_refuse_restore(self):
        self.make_backup()
        with self.assertRaisesRegex(RuntimeError, 'doesn’t belong'):
            verified_backup(self.backup, self.root / 'other')
        (self.backup / 'workspace/settings.json').write_text('tampered')
        with self.assertRaisesRegex(RuntimeError, 'changed since'):
            verified_backup(self.backup, self.data)

    def test_dangling_recovery_marker_blocks_backup_before_copy(self):
        (self.data / RECOVERY_MARKER).symlink_to(self.root / 'missing')
        with self.lease.acquire():
            with self.assertRaisesRegex(RuntimeError, 'interrupted restore'):
                self.lease.snapshot(self.backup)
        self.assertFalse(self.backup.exists())
        with self.assertRaisesRegex(Rejected, 'restore was interrupted'):
            Store(self.data / 'conversations.sqlite3')

    def test_restore_requires_exact_choice_and_preserves_newer_work(self):
        original = inventory(self.data); self.make_backup()
        (self.data / 'settings.json').write_text('{"name":"newer"}')
        (self.data / 'new-conversation.txt').write_text('keep this newer work')
        newer = inventory(self.data)
        lock = self.data / 'conversations.sqlite3.lock'
        inode = lock.stat().st_ino
        with self.lease.acquire():
            with self.assertRaisesRegex(RuntimeError, 'Confirm the exact'):
                restore_snapshot(self.lease, self.backup, self.recovery, confirmation='yes')
            self.assertEqual(inventory(self.data), newer)
            result = restore_snapshot(self.lease, self.backup, self.recovery, confirmation='RESTORE before')
            self.assertEqual(result['state'], 'restored')
            self.assertEqual(inventory(self.data), original)
            self.assertEqual(inventory(self.recovery / 'workspace'), newer)
            self.assertEqual(lock.stat().st_ino, inode)
            with self.assertRaises(Rejected): Store(self.data / 'conversations.sqlite3')
        restored = Store(self.data / 'conversations.sqlite3'); restored.close()
        self.assertFalse((self.data / RECOVERY_MARKER).exists())

    def test_nested_recovery_refuses_before_mutating_backup(self):
        self.make_backup(); before = inventory(self.backup)
        with self.lease.acquire():
            with self.assertRaisesRegex(RuntimeError, 'separate'):
                restore_snapshot(self.lease, self.backup, self.backup / 'workspace/recovery', confirmation='RESTORE before')
        self.assertEqual(inventory(self.backup), before)

    def test_backup_cannot_replace_workspace_lock_or_recovery_marker(self):
        self.make_backup()
        original = inventory(self.data)
        lock_inode = (self.data / 'conversations.sqlite3.lock').stat().st_ino
        for name in ['conversations.sqlite3.lock', RECOVERY_MARKER]:
            injected = self.backup / 'workspace' / name
            injected.write_text('injected control file')
            with self.lease.acquire():
                with self.assertRaisesRegex(RuntimeError, 'reserved workspace control'):
                    restore_snapshot(self.lease, self.backup, self.recovery, confirmation='RESTORE before')
                self.assertEqual((self.data / 'conversations.sqlite3.lock').stat().st_ino, lock_inode)
                with self.assertRaises(Rejected): Store(self.data / 'conversations.sqlite3')
            self.assertEqual(inventory(self.data), original)
            self.assertFalse(self.recovery.exists())
            injected.unlink()

    def test_mid_restore_io_failure_puts_current_workspace_back(self):
        self.make_backup(); (self.data / 'settings.json').write_text('newer')
        newer = inventory(self.data)
        original_rename = Path.rename
        failed = False
        def fail_once(path, target):
            nonlocal failed
            if path.parent.name == 'restore-staging' and not failed:
                failed = True
                raise OSError('test write failure')
            return original_rename(path, target)
        with self.lease.acquire(), patch.object(Path, 'rename', fail_once):
            with self.assertRaisesRegex(OSError, 'test write failure'):
                restore_snapshot(self.lease, self.backup, self.recovery, confirmation='RESTORE before')
        self.assertTrue(failed)
        self.assertEqual(inventory(self.data), newer)
        self.assertEqual(inventory(self.recovery / 'workspace'), newer)
        self.assertFalse((self.data / RECOVERY_MARKER).exists())

    def test_process_crash_blocks_host_until_explicit_recovery_of_newer_work(self):
        self.make_backup(); (self.data / 'settings.json').write_text('newer')
        newer = inventory(self.data)
        script = '''
import os, sys
from pathlib import Path
from release_handoff import WorkspaceLease, restore_snapshot
original = Path.rename
def crash_after_first_move(path, target):
    result = original(path, target)
    if Path(target).parent.name == 'displaced': os._exit(19)
    return result
Path.rename = crash_after_first_move
with WorkspaceLease(sys.argv[1]).acquire() as lease:
    restore_snapshot(lease, Path(sys.argv[2]), Path(sys.argv[3]), confirmation='RESTORE before')
'''
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.data), str(self.backup), str(self.recovery)],
                                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertTrue((self.data / RECOVERY_MARKER).exists())
        with self.assertRaisesRegex(Rejected, 'restore was interrupted'):
            Store(self.data / 'conversations.sqlite3')
        with self.lease.acquire():
            with self.assertRaisesRegex(RuntimeError, 'Confirm the saved'):
                recover_interrupted(self.lease, confirmation='yes')
            result = recover_interrupted(self.lease, confirmation='RECOVER newer-work')
            self.assertEqual(result['state'], 'recovered')
            self.assertEqual(inventory(self.data), newer)
        host = Store(self.data / 'conversations.sqlite3'); host.close()
        self.assertEqual(verified_backup(self.backup, self.data)['state'], 'verified')

    def test_external_write_during_copy_leaves_backup_unverified(self):
        import shutil
        copy = shutil.copytree
        def changed(source, target, **kwargs):
            result = copy(source, target, **kwargs)
            (self.data / 'settings.json').write_text('concurrent writer')
            return result
        with self.lease.acquire(), patch('release_handoff.shutil.copytree', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'changed during backup'):
                self.lease.snapshot(self.backup)
        self.assertEqual(json.loads((self.backup / 'backup.json').read_text())['state'], 'incomplete')
        with self.assertRaisesRegex(RuntimeError, 'wasn’t verified'):
            verified_backup(self.backup, self.data)


if __name__ == '__main__':
    unittest.main()
