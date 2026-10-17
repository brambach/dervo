"""A large compiler manifest mustn't replace an executed check with a tool error."""
from copy import deepcopy
import json
import unittest

from coding_integration import provider_check_result


class CheckResultTests(unittest.TestCase):
    def test_large_provenance_preserves_pass_failure_and_stopped_outcomes(self):
        compiler = {'package': 'node_modules/typescript', 'version': '6.0.3', 'bytes': 12365704,
                    'files': ['declarations/' + str(i) + '.d.ts' for i in range(700)],
                    'sha256': {str(i): 'a' * 64 for i in range(700)}}
        for exit_status, stopped in ((0, None), (1, None), (-15, 'cancelled'), (0, 'no_tests_reported')):
            attempt = dict(number=3, state='verified', blocker=None, kind='verification', test=dict(
                compiler=compiler, runtime_files=['/host/runtime'], exit_status=exit_status,
                stopped=stopped, stdout='', stderr='error TS2307: missing import' if exit_status == 1 else '',
                check_profile='typescript-check', argv=['node', 'tsc.js', '--noEmit', '-p', 'tsconfig.json']))
            before = deepcopy(attempt)
            self.assertGreater(len(json.dumps(attempt)), 60000)
            with self.subTest(exit_status=exit_status, stopped=stopped):
                result = provider_check_result('task-123', attempt)
                self.assertLess(len(json.dumps(result).encode()), 60000)
                for key in ('exit_status', 'stopped', 'stdout', 'stderr', 'check_profile', 'argv'):
                    self.assertEqual(result[key], attempt['test'][key])
                self.assertEqual(result['compiler']['file_count'], 700)
                self.assertEqual(result['evidence']['attempt'], 3)
                self.assertEqual(attempt, before, 'Full stored evidence must remain unchanged')

    def test_long_diagnostics_are_marked_and_keep_start_and_end(self):
        diagnostic = 'start ' + '\x00' * 100000 + ' final failure'
        attempt = dict(number=1, state='verified', blocker=None,
                       test=dict(exit_status=1, stopped=None, stdout=diagnostic, stderr=diagnostic))
        result = provider_check_result('task', attempt)
        self.assertLess(len(json.dumps(result).encode()), 60000)
        self.assertEqual(result['output_truncated'], ['stdout', 'stderr'])
        self.assertTrue(result['stderr'].startswith('start '))
        self.assertTrue(result['stderr'].endswith('final failure'))
        self.assertEqual(result['exit_status'], 1)


if __name__ == '__main__':
    unittest.main()
