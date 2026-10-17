"""The smartness eval runner in fake mode, and the real-call wrapper's cap. What CI runs so backend/evals stays green.

The gate runs a smoke subset (SMOKE: two or three cases per system, every path the runner has: rules, scripted
writes, memory tools, pickup, a kill and relaunch, Home through the fake) in well under a minute. The full set is
``python3 -m evals.runner --mode fake`` (about two minutes). Rules-only cases must pass unless the review marked them
as wrong today (expect_fail). Model cases only have to run without an error: their answers come from the fake, so
their scores mean nothing here.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from evals import runner
from evals.case import SYSTEMS


SMOKE = ['s4b-secrets-stay-out-of-names', 's1-hand-move-beats-topic', 's17-second-review-still-needs-you',
         'm10-private-note-stays-private', 'm20-command-note-refused', 'm6-different-facts-both-save', 'm51-correction-named-by-the-agent-takes-the-notes-place',
         'h1-planted-next-is-not-a-job', 'h6-that-one-too-is-an-add',
         'e16-final-question-is-the-next', 'e19-lost-run-is-told-as-unconfirmed', 'e2-undone-result-is-told']


class OutFolderTests(unittest.TestCase):
    def test_the_out_folder_stays_under_the_eval_folder_or_a_temp_folder_unless_asked(self):
        """Security review 20 Sep, L5: ``--out`` is every case's host data dir; nothing stopped
        ``--out ~/Library/Application Support/local.dervo.connected`` or a real project."""
        from evals.runner import out_refused, BACKEND
        support = Path.home() / 'Library' / 'Application Support' / 'local.dervo.connected'
        self.assertIn('Application Support', out_refused(str(support)))
        self.assertIn('Application Support', out_refused(str(support), anywhere=True), 'never, even when asked')
        self.assertIn('--out-anywhere', out_refused(str(Path.home() / 'some-project')))
        self.assertIsNone(out_refused(str(Path.home() / 'some-project'), anywhere=True))
        # Negative controls: the default and a temp folder need no flag.
        self.assertIsNone(out_refused(str(BACKEND / 'evals' / 'out')))
        self.assertIsNone(out_refused(str(BACKEND / 'evals' / 'out' / 'run-3')))
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(out_refused(str(Path(temp) / 'out')))
        with self.assertRaises(SystemExit):
            runner.main(['--mode', 'fake', '--only', 'nothing', '--out', str(support)])


class FakeModeRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        out = Path(cls.temp.name) / 'out'
        args = argparse.Namespace(mode='fake', system='all', only=list(SMOKE), skip_model=False, cap=60, case_cap=6,
                                  rerun_failing=0, out=str(out), agent_provider=None)
        cls.results = runner.run(args)
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_every_system_has_at_least_twelve_cases(self):
        from evals.runner import load_cases
        for system in SYSTEMS:
            with self.subTest(system=system):
                self.assertGreaterEqual(len(load_cases((system,))), 12)

    def test_the_smoke_subset_ran_every_case_it_names(self):
        self.assertEqual(sorted(r['id'] for r in self.results), sorted(SMOKE))

    def test_no_case_errors(self):
        errors = [(r['id'], r['reason']) for r in self.results if r['status'] == 'error']
        self.assertEqual(errors, [])

    def test_rules_cases_pass_unless_the_review_says_today_is_wrong(self):
        for r in self.results:
            if r['provider'] == 'none' and not r['expect_fail']:
                with self.subTest(case=r['id']):
                    self.assertEqual(r['status'], 'pass', r['reason'])

    def test_expected_failures_still_fail_today(self):
        """The review's findings, reproduced: when one of these starts passing, the fix landed and the flag should go."""
        for r in self.results:
            if r['provider'] == 'none' and r['expect_fail']:
                with self.subTest(case=r['id']):
                    self.assertEqual(r['status'], 'fail', r['reason'])

    def test_no_real_call_was_made(self):
        self.assertFalse((self.out / 'real-calls.jsonl').exists())
        self.assertTrue(all(r['calls'] == 0 for r in self.results))

    def test_results_file_has_one_row_per_case(self):
        rows = json.loads((self.out / 'results.json').read_text())
        self.assertEqual(len(rows), len(self.results))
        self.assertTrue(all({'id', 'system', 'status', 'reason', 'calls', 'seconds'} <= set(row) for row in rows))


class RealCliCap(unittest.TestCase):
    def test_the_wrapper_refuses_a_model_call_past_the_cap_without_launching(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / 'real-calls.jsonl'
            missing = str(root / 'no-such-claude')  # never reached: the refusal comes first
            config = dict(dir=str(root), run_log=str(log), lock=str(log) + '.lock', cap=1, case_cap=1, case='t', claude=missing, codex=missing,
                          python=sys.executable, fake=str(Path(__file__).with_name('fake_claude_cli.py')), marker='--dervo-driver=t', codex_model=None)
            (root / 'eval-config.json').write_text(json.dumps(config))
            log.write_text(json.dumps(dict(id='1', case='t', provider='claude', model='m', started=0, state='started')) + '\n')
            wrapper = str(Path(__file__).with_name('evals') / 'real_cli.py')
            result = subprocess.run([sys.executable, wrapper, f'--config={root / "eval-config.json"}', 'claude', '-p', '--model', 'sonnet'],
                                    capture_output=True, text=True, env={'PATH': os.environ.get('PATH', '')})
            self.assertEqual(result.returncode, 70, result.stderr)
            self.assertIn('refused', result.stderr)
            records = [json.loads(l) for l in log.read_text().splitlines()]
            self.assertEqual([r['state'] for r in records], ['started', 'refused'])

    def test_a_non_model_call_is_not_counted(self):
        """``--version`` passes through to the executable and writes nothing to the log."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / 'real-calls.jsonl'
            config = dict(dir=str(root), run_log=str(log), lock=str(log) + '.lock', cap=0, case_cap=0, case='t', claude='/bin/echo', codex='/bin/echo',
                          python=sys.executable, fake=str(Path(__file__).with_name('fake_claude_cli.py')), marker='--dervo-driver=t', codex_model=None)
            (root / 'eval-config.json').write_text(json.dumps(config))
            wrapper = str(Path(__file__).with_name('evals') / 'real_cli.py')
            result = subprocess.run([sys.executable, wrapper, f'--config={root / "eval-config.json"}', 'claude', '--version'],
                                    capture_output=True, text=True, env={'PATH': os.environ.get('PATH', '')})
            self.assertEqual((result.returncode, result.stdout.strip()), (0, '--version'))
            self.assertFalse(log.exists())

    def test_a_dervo_owned_profile_runs_the_fake(self):
        """CLAUDE_CONFIG_DIR under the eval dir means the fake, whatever the args: no real CLI, no log line."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = root / 'profiles' / 'p'
            profile.mkdir(parents=True)
            (profile / 'fake-scenario.json').write_text(json.dumps(dict(auth=dict(loggedIn=True, email='x@example.com'))))
            log = root / 'real-calls.jsonl'
            config = dict(dir=str(root), run_log=str(log), lock=str(log) + '.lock', cap=0, case_cap=0, case='t', claude='/nonexistent', codex='/nonexistent',
                          python=sys.executable, fake=str(Path(__file__).with_name('fake_claude_cli.py')), marker='--dervo-driver=t', codex_model=None)
            (root / 'eval-config.json').write_text(json.dumps(config))
            wrapper = str(Path(__file__).with_name('evals') / 'real_cli.py')
            result = subprocess.run([sys.executable, wrapper, f'--config={root / "eval-config.json"}', 'claude', 'auth', 'status'],
                                    capture_output=True, text=True, env={'PATH': os.environ.get('PATH', ''), 'CLAUDE_CONFIG_DIR': str(profile)})
            self.assertEqual(json.loads(result.stdout)['email'], 'x@example.com')
            self.assertFalse(log.exists())


if __name__ == '__main__':
    unittest.main()
