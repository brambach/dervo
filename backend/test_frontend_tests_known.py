"""scripts/frontend-tests-known.py: only a frontend test that isn't known to fail turns the build red."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'frontend-tests-known.py'
spec = importlib.util.spec_from_file_location('frontend_tests_known', SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

TAP = '''# Subtest: parent
    not ok 1 - a subtest is reported by its parent
not ok 1 - parent
ok 2 - passes
not ok 3 - known # TODO
# pass 1
# fail 2
'''


class FrontendGateTests(unittest.TestCase):
    def run_gate(self, known, output):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'known.json'
            path.write_text(json.dumps({sys.platform: known}))
            return gate.main(['gate', str(path), '--', sys.executable, '-c', f'print({output!r})'])

    def test_top_level_failures_only(self):
        self.assertEqual(gate.failures(TAP), {'parent', 'known'})

    def test_a_new_failure_is_red_and_known_ones_are_not(self):
        self.assertEqual(self.run_gate({'parent': 'why', 'known': 'why'}, TAP), 0)
        self.assertEqual(self.run_gate({'known': 'why'}, TAP), 1)

    def test_a_run_without_a_summary_is_red(self):
        self.assertEqual(self.run_gate({}, 'ok 1 - cut off'), 1)


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(sys.platform.startswith('linux'), 'the memory watch reads /proc')
class MemoryWatchTests(unittest.TestCase):
    def test_a_test_file_over_the_limit_is_stopped_and_named(self):
        import subprocess
        import threading
        from unittest.mock import patch
        hog = subprocess.Popen(['sh', '-c', f'exec {sys.executable} -c "x = bytearray(160 * 2**20); import time; '
                                'time.sleep(30)" src/hog.test.mjs'])
        self.addCleanup(hog.wait)
        self.addCleanup(lambda: hog.poll() is None and hog.kill())
        killed = []
        with patch.object(gate, 'LIMIT_MB', 100):
            # The watch looks below its root, so watch this test's own process, the hog's parent.
            import os
            watcher = threading.Thread(target=gate.watch, args=(os.getpid(), killed, 0.05), daemon=True)
            watcher.start()
            self.assertEqual(hog.wait(timeout=20), -9)
        self.assertEqual([file for file, _ in killed], ['src/hog.test.mjs'])
