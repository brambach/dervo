"""The parallel-run load test (tools/load_parallel_runs.py), small: three runs at once instead of ten, four-second runs.
It starts the real host and fake Claude Code subprocesses on a throwaway store; no provider, account or network."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'load_parallel_runs.py'


class ParallelRunTests(unittest.TestCase):
    def test_the_limit_holds_waiting_requests_start_and_a_restart_neither_loses_nor_doubles_a_run(self):
        spec = importlib.util.spec_from_file_location('load_parallel_runs', TOOL)
        load = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(load)
        with tempfile.TemporaryDirectory() as out:
            result = load.run(3, 4, Path(out))
        self.assertEqual(result['findings'], [])
        self.assertTrue(result['passed'])
        self.assertEqual(result['peak']['fake_processes'], 3, 'never more runs than the limit')
        self.assertEqual((result['starts'], result['tasks']), (8, 8), '5 in the first round, 3 after the restart, each once')
        self.assertEqual(result['second_connection']['locked'], 0)
        # 16.4: the quit pauses the 3 runs going at the restart, so they end blocked (interrupted) like the 2 stopped in step 2.
        self.assertEqual(result['final_states'].get('blocked'), 5, 'runs going at the quit come back paused, never replayed')
        self.assertIsNone(result['final_states'].get('disconnected'))
        self.assertLess(result['restart_seconds'], 4, 'the quit pause fits in the 4 s the app gives the host (bridge.rs)')


if __name__ == '__main__':
    unittest.main()
