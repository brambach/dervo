"""Independent snapshot contents, no overwrite, and native APFS clone semantics."""
import errno
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from release_copy import clone_file,copy_snapshot_file,file_hash

class CopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.write_bytes(b'original runtime\n'*1024)
        self.source.chmod(0o755);self.destination=self.root/'snapshot'
    def test_copy_keeps_metadata_and_edits_never_change_source(self):
        copy_snapshot_file(self.source,self.destination)
        self.assertEqual(file_hash(self.source),file_hash(self.destination))
        self.assertNotEqual(self.source.stat().st_ino,self.destination.stat().st_ino)
        self.assertEqual(self.source.stat().st_mode,self.destination.stat().st_mode)
        self.assertEqual(self.source.stat().st_mtime_ns,self.destination.stat().st_mtime_ns)
        self.destination.write_text('snapshot edit')
        self.assertTrue(self.source.read_bytes().startswith(b'original runtime'))
    def test_existing_files_and_links_are_never_overwritten(self):
        self.destination.write_text('keep')
        with self.assertRaises(FileExistsError):copy_snapshot_file(self.source,self.destination)
        self.assertEqual(self.destination.read_text(),'keep')
        self.destination.unlink();self.destination.symlink_to(self.root/'missing')
        with self.assertRaises(FileExistsError):copy_snapshot_file(self.source,self.destination)
        link=self.root/'source-link';link.symlink_to(self.source)
        with self.assertRaises(ValueError):copy_snapshot_file(link,self.root/'other')
    def test_unsupported_clone_falls_back_to_independent_copy(self):
        with patch('release_copy.clone_file',side_effect=OSError(errno.ENOTSUP,'unsupported')):
            copy_snapshot_file(self.source,self.destination)
        self.source.write_text('source edit')
        self.assertTrue(self.destination.read_bytes().startswith(b'original runtime'))
    def test_other_clone_failure_is_not_hidden(self):
        with patch('release_copy.clone_file',side_effect=OSError(errno.EACCES,'denied')):
            with self.assertRaises(PermissionError):copy_snapshot_file(self.source,self.destination)
        self.assertFalse(self.destination.exists())
    @unittest.skipUnless(sys.platform=='darwin','Requires Apple clonefile')
    def test_actual_clone_has_distinct_inode_and_independent_writes(self):
        clone_file(self.source,self.destination)
        self.assertEqual(file_hash(self.source),file_hash(self.destination))
        self.assertNotEqual(self.source.stat().st_ino,self.destination.stat().st_ino)
        self.destination.write_bytes(b'changed')
        self.assertTrue(self.source.read_bytes().startswith(b'original runtime'))
if __name__=='__main__':unittest.main()
