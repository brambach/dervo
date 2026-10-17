"""The bounded live acceptance runner, exercised without any live provider: it fails closed, caps provider starts, and in
simulated mode drives the real Host, CodingTasks, collaboration broker, evidence and confined tests end to end with the
synthetic peers, evaluating every criterion from records. No profile, credential or model is touched here."""
import json
import tempfile
import unittest
from pathlib import Path

import collaboration_acceptance as m
from dervo_connections import Rejected


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_without_flags_only_the_plan_is_printed_and_nothing_is_created(self):
        evidence = self.base / 'evidence'
        self.assertEqual(m.main(['--evidence-dir', str(evidence), '--work-dir', str(self.base / 'work')]), m.EXIT_PLAN)
        self.assertFalse(evidence.exists())
        self.assertFalse((self.base / 'work').exists())

    def test_live_without_every_exact_input_is_refused_before_anything_exists(self):
        evidence = self.base / 'evidence'
        for argv in (['--allow-live'],
                     ['--allow-live', '--simulate'],
                     ['--allow-live', '--claude-owner-dir', str(self.base), '--claude-helper-dir', str(self.base), '--codex-reviewer-dir', str(self.base),
                      '--claude-identity', 'a@example.test', '--codex-identity', 'b@example.test', '--claude-model', 'claude-x', '--codex-model', 'gpt-x'],
                     ['--allow-live', '--claude-owner-dir', str(self.base / 'missing'), '--claude-helper-dir', str(self.base), '--codex-reviewer-dir', str(self.base / 'cx'),
                      '--claude-identity', 'a@example.test', '--codex-identity', 'b@example.test', '--claude-model', 'claude-x', '--codex-model', 'gpt-x'],
                     ['--allow-live', '--claude-owner-dir', str(self.base), '--claude-helper-dir', str(self.base), '--codex-reviewer-dir', str(self.base / 'cx'),
                      '--claude-identity', 'a@example.test', '--codex-identity', 'b@example.test', '--claude-model', 'claude-x']):
            (self.base / 'cx').mkdir(exist_ok=True)
            with self.subTest(argv=argv):
                self.assertEqual(m.main([*argv, '--evidence-dir', str(evidence), '--work-dir', str(self.base / 'work')]), m.EXIT_SETUP)
                self.assertFalse(evidence.exists(), 'refused before any record was written')
                self.assertFalse((self.base / 'work').exists())

    def test_start_guard_counts_three_starts_and_refuses_a_fourth_without_retry(self):
        calls = []

        class Coding:
            def start(self, task):
                calls.append(task['id'])
                return task
        coding = Coding()
        guard = m.StartGuard(coding, m.MAX_STARTS)
        task = lambda i, role=None: {'id': f't{i}', 'role': role, 'agent': None if role is None else 'x', 'binding': {'provider': 'claude', 'model': 'm', 'profile_id': 'p'},
                                     'context': {'thread_id': 'c'}}
        for i, role in enumerate((None, 'worker', 'reviewer')):
            coding.start(task(i, role))
        with self.assertRaises(Rejected) as refused:
            coding.start(task(3, 'worker'))
        self.assertIn('Acceptance cap', str(refused.exception))
        self.assertEqual((calls, len(guard.starts), guard.refused), (['t0', 't1', 't2'], 3, 1))
        self.assertEqual([s['role'] for s in guard.starts], ['owner', 'worker', 'reviewer'])

    def test_redaction_masks_identities_and_drops_runtime_listings(self):
        value = {'identity': 'someone@example.test', 'text': 'sent by someone@example.test', 'test': {'runtime_files': ['/a', '/b'], 'exit_status': 0}}
        out = m.redact(value, ['someone@example.test'])
        self.assertEqual(out, {'identity': 's***@example.test', 'text': 'sent by s***@example.test', 'test': {'runtime_files': '[omitted]', 'exit_status': 0}})


class SimulatedRunTests(unittest.TestCase):
    def test_simulated_run_proves_the_mechanics_and_labels_itself_simulated(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        evidence, work = base / 'evidence', base / 'work'
        self.assertEqual(m.main(['--simulate', '--evidence-dir', str(evidence), '--work-dir', str(work)]), m.EXIT_PASS)
        for name in ('plan.json', 'setup.json', 'request.json', 'progress.jsonl', 'owner.json', 'packets.json', 'helpers.json', 'report.json', 'final-tests.txt', 'fixture-before.json'):
            self.assertTrue((evidence / name).is_file(), name)
        report = json.loads((evidence / 'report.json').read_text())
        self.assertEqual((report['mode'], report['model_judgement_exercised'], report['passed'], report.get('timed_out')), ('simulated', False, True, False))
        self.assertEqual(sorted(report['criteria']), sorted(m.CRITERIA))
        self.assertTrue(all(c['pass'] for c in report['criteria'].values()), json.dumps(report['criteria'], indent=1))
        self.assertEqual(sorted(s['agent'] or 'owner' for s in report['provider_starts']), ['fixer', 'owner', 'reviewer'])
        self.assertEqual((len(report['provider_starts']), report['start_cap'], report['provider_starts_refused_by_cap'], report['peak_live_turns']), (3, 3, 0, 3))
        self.assertEqual({s['provider'] for s in report['provider_starts'] if s['agent'] == 'reviewer'}, {'codex'})
        self.assertEqual({s['provider'] for s in report['provider_starts'] if s['agent'] != 'reviewer'}, {'claude'})
        self.assertEqual(report['final_tests']['exit_status'], 0)
        # The fixture: the failing test was made to pass by a change to the scoped file only; the out-of-scope file is untouched.
        self.assertEqual((work / 'project' / 'greeting.py').read_text(), 'def greet():\n    return "hi"\n')
        self.assertEqual((work / 'project' / 'notes.py').read_text(), m.FIXTURE['notes.py'])
        self.assertEqual(report['files_after']['notes.py'], report['fixture_before']['notes.py'])
        packets = json.loads((evidence / 'packets.json').read_text())
        self.assertEqual(sorted((p['agent'], p['kind'], p['status']) for p in packets), [('fixer', 'work', 'returned'), ('reviewer', 'review', 'returned')])
        self.assertTrue(all(p['payload_text'] and p['snapshots'] for p in packets), 'exact payload and snapshot bytes are recorded')
        iterations = report['tool_iterations']
        self.assertEqual(sorted(v['role'] for v in iterations.values()), ['fixer', 'owner', 'reviewer'])
        self.assertTrue(all(v['runs'] == 1 for v in iterations.values()), 'no provider turn was retried')
        plan = json.loads((evidence / 'plan.json').read_text())
        self.assertEqual((plan['mode'], plan['max_provider_starts'], plan['policy']['maxCalls']), ('simulated', 3, 2))
        lines = [json.loads(line) for line in (evidence / 'progress.jsonl').read_text().splitlines()]
        self.assertEqual([e['role'] for e in lines if e['event'] == 'provider_start'][0], 'owner')
        self.assertIn('peak_concurrency', {e['event'] for e in lines})


if __name__ == '__main__':
    unittest.main()
