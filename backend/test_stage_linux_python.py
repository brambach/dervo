"""scripts/stage-linux-python.py: the folder a debug build leaves (build.rs) doesn't count as a staged runtime."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'stage-linux-python.py'
spec = importlib.util.spec_from_file_location('stage_linux_python', SCRIPT)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


class UnstagedFolderTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dest = Path(folder.name) / 'linux-python'

    def test_a_folder_holding_only_the_marker_is_cleared(self):
        self.dest.mkdir()
        (self.dest / 'NOT-STAGED').write_text('x')
        stage.clear_unstaged(self.dest)
        self.assertFalse(self.dest.exists())

    def test_a_staged_runtime_is_left_alone(self):
        (self.dest / 'bin').mkdir(parents=True)
        (self.dest / 'bin' / 'python3.13').write_text('x')
        (self.dest / 'NOT-STAGED').write_text('x')
        stage.clear_unstaged(self.dest)
        self.assertTrue((self.dest / 'bin' / 'python3.13').exists())

    def test_a_missing_folder_is_fine(self):
        stage.clear_unstaged(self.dest)
        self.assertFalse(self.dest.exists())


if __name__ == '__main__':
    unittest.main()
