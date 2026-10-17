"""The Claude Code version floor (PLAN-native.md, A7): Connected accounts, first run, and the checks before a coding run
and a Home run. A scripted ``--version`` stands in for the CLI; nothing launches Claude Code or calls a model."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import claude_version
from dervo_connections import Rejected
from host import Host


def scripted(output, code=0, calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((list(argv), kwargs))
        return SimpleNamespace(returncode=code, stdout=output, stderr='')
    return run


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.exe = Path(self.temp.name) / 'claude'
        self.exe.write_text('#!/bin/sh\n'); self.exe.chmod(0o755)
        claude_version._seen.clear(); self.addCleanup(claude_version._seen.clear)
        claude_version._failed.clear(); self.addCleanup(claude_version._failed.clear)

    def test_parse_and_the_floor(self):
        self.assertEqual(claude_version.parse('2.1.274 (Claude Code)'), (2, 1, 274))
        self.assertEqual(claude_version.parse('Claude Code v2.1.263\n'), (2, 1, 263))
        for text in ('', 'Claude Code', None, '1.2', '2.1.274.5'):
            with self.subTest(text=text):
                self.assertIsNone(claude_version.parse(text))
        self.assertTrue(claude_version.too_old((2, 1, 270)))
        self.assertFalse(claude_version.too_old((2, 1, 271)))
        self.assertFalse(claude_version.too_old((3, 0, 0)))
        self.assertFalse(claude_version.too_old(None), 'an unreadable version is not called old')

    def test_status_says_ok_old_missing_or_unknown(self):
        calls = []
        ok = claude_version.status(run=scripted('2.1.274 (Claude Code)\n', calls=calls), find=lambda: str(self.exe))
        self.assertEqual((ok['state'], ok['version'], ok['command']), ('ok', '2.1.274', None))
        self.assertEqual(calls[0][0], [str(self.exe), '--version'], 'only --version, never a session or a model call')
        self.assertNotIn('ANTHROPIC_API_KEY', calls[0][1]['env'])
        old = claude_version.status(run=scripted('2.1.263 (Claude Code)'), find=lambda: str(self.exe), fresh=True)
        self.assertEqual((old['state'], old['version'], old['floor'], old['command']), ('old', '2.1.263', '2.1.271', 'claude update'))
        self.assertIn('no Claude account can run', old['detail'])
        unknown = claude_version.status(run=scripted('', code=1), find=lambda: str(self.exe), fresh=True)
        self.assertEqual(unknown['state'], 'unknown')
        def missing():
            raise Rejected('not installed')
        self.assertEqual(claude_version.status(find=missing)['state'], 'missing')

    def test_the_answer_is_kept_until_the_file_changes_or_a_recheck(self):
        calls = []
        run = scripted('2.1.263', calls=calls)
        claude_version.read(self.exe, run=run); claude_version.read(self.exe, run=run)
        self.assertEqual(len(calls), 1, 'one --version per executable file')
        claude_version.read(self.exe, run=run, fresh=True)
        self.assertEqual(len(calls), 2, 'Check again reads it again')
        self.exe.write_text('#!/bin/sh\n# updated\n')
        self.assertEqual(claude_version.read(self.exe, run=scripted('2.1.280')), (2, 1, 280), 'claude update is seen at once')

    def test_require_refuses_only_a_known_old_version(self):
        with self.assertRaisesRegex(Rejected, r'Update Claude Code to 2\.1\.271 or later\. Run claude update'):
            claude_version.require(self.exe, run=scripted('2.1.263 (Claude Code)'))
        claude_version._seen.clear()
        claude_version.require(self.exe, run=scripted('2.1.271 (Claude Code)'))
        claude_version._seen.clear()
        claude_version.require(self.exe, run=scripted('', code=2))  # unreadable: the run reports its own error
        claude_version.require(Path(self.temp.name) / 'absent')

    def test_a_coding_run_stops_before_auth_status_on_an_old_version(self):
        import claude_coding
        from codex_coding import TrustedTaskBinding
        started = []
        self.enterContext(patch.object(claude_coding, 'executable', lambda: str(self.exe)))
        self.enterContext(patch.object(claude_coding, 'auth_status', lambda *a, **k: started.append('auth') or {}))
        self.enterContext(patch.object(claude_coding, 'OwnedProcess', lambda *a, **k: started.append('launch')))
        self.enterContext(patch.object(claude_version, 'read', lambda path, **k: (2, 1, 263)))
        binding = dict(provider='claude', identity='me@example.test', category='personal', home=self.temp.name,
                       cwd=self.temp.name, model='claude-sonnet-5', session_id=None)
        with self.assertRaisesRegex(Rejected, 'Update Claude Code to 2.1.271'):
            claude_coding.ClaudeCodingTurn(binding, task=TrustedTaskBinding('t', binding, lambda *_: True),
                                           context=lambda: ('', 'r'), emit=lambda *_: None)
        self.assertEqual(started, [], 'nothing about the account is read and nothing launches')

    def test_opus_5_5_needs_claude_code_2_1_280(self):
        detail = claude_version.model_detail('claude-opus-5-5', (2, 1, 278))
        self.assertEqual(detail, 'Opus 5.5 needs Claude Code 2.1.280 or later, and this Mac has 2.1.278. '
                                 'Run claude update in Terminal, then try again, or pick another model.')
        for typed in ('claude-opus-5-5[1m]', 'claude-opus-5-5-20260922', 'claude-opus-5-5-20260922[1m]'):
            with self.subTest(typed=typed):
                self.assertIn('Opus 5.5 needs Claude Code 2.1.280', claude_version.model_detail(typed, (2, 1, 278)))
        for model, version in (('claude-opus-5-5', (2, 1, 280)), ('claude-opus-5-5', (2, 2, 0)), ('claude-opus-5-5', None),
                               ('claude-opus-5-50', (2, 1, 271)), ('claude-opus-5-5-beta', (2, 1, 271)), (123, (2, 1, 271)),
                               ('claude-opus-5', (2, 1, 271)), ('claude-sonnet-5', (2, 1, 271)), (None, (2, 1, 271))):
            with self.subTest(model=model, version=version):
                self.assertIsNone(claude_version.model_detail(model, version))
        self.assertEqual(claude_version.default_model((2, 1, 280)), 'claude-opus-5-5')
        self.assertEqual(claude_version.default_model((2, 1, 279)), 'claude-opus-5')
        self.assertEqual(claude_version.default_model(None), 'claude-opus-5', 'an unknown version gets the Opus every supported version runs')
        for output, model, refusal in (('2.1.278', 'claude-opus-5-5', 'Opus 5.5 needs Claude Code 2.1.280'),
                                       ('2.1.263', 'claude-opus-5-5', 'Update Claude Code to 2.1.271'),
                                       ('2.1.278', 'claude-opus-5', None), ('2.1.280', 'claude-opus-5-5', None), ('', 'claude-opus-5-5', None)):
            with self.subTest(output=output, model=model):
                claude_version._seen.clear(); claude_version._failed.clear()
                run = scripted(output + ' (Claude Code)' if output else '', code=0 if output else 2)
                if refusal:
                    with self.assertRaisesRegex(Rejected, refusal):
                        claude_version.require(self.exe, run=run, model=model)
                else:
                    claude_version.require(self.exe, run=run, model=model)

    def test_sonnet_5_5_needs_claude_code_2_1_284(self):
        self.assertEqual(claude_version.model_detail('claude-sonnet-5-5', (2, 1, 280)),
                         'Sonnet 5.5 needs Claude Code 2.1.284 or later, and this Mac has 2.1.280. '
                         'Run claude update in Terminal, then try again, or pick another model.')
        self.assertIn('Sonnet 5.5 needs Claude Code 2.1.284', claude_version.model_detail('claude-sonnet-5-5[1m]', (2, 1, 283)))
        for model, version in (('claude-sonnet-5-5', (2, 1, 284)), ('claude-sonnet-5-5', None), ('claude-sonnet-5', (2, 1, 271))):
            with self.subTest(model=model, version=version):
                self.assertIsNone(claude_version.model_detail(model, version))
        self.assertEqual(claude_version.default_model((2, 1, 284)), 'claude-opus-5-5', 'Sonnet 5.5 doesn’t change the default')

    def test_a_coding_run_on_opus_5_5_stops_before_auth_status_on_an_older_claude_code(self):
        import claude_coding
        from codex_coding import TrustedTaskBinding
        started = []
        self.enterContext(patch.object(claude_coding, 'executable', lambda: str(self.exe)))
        self.enterContext(patch.object(claude_coding, 'auth_status', lambda *a, **k: started.append('auth') or {}))
        self.enterContext(patch.object(claude_coding, 'OwnedProcess', lambda *a, **k: started.append('launch')))
        self.enterContext(patch.object(claude_version, 'read', lambda path, **k: (2, 1, 278)))
        binding = dict(provider='claude', identity='me@example.test', category='personal', home=self.temp.name,
                       cwd=self.temp.name, model='claude-opus-5-5', session_id=None)
        with self.assertRaisesRegex(Rejected, 'Opus 5.5 needs Claude Code 2.1.280 or later, and this Mac has 2.1.278'):
            claude_coding.ClaudeCodingTurn(binding, task=TrustedTaskBinding('t', binding, lambda *_: True),
                                           context=lambda: ('', 'r'), emit=lambda *_: None)
        self.assertEqual(started, [], 'nothing about the account is read and nothing launches')

    def test_a_home_run_uses_the_same_floor(self):
        import project_orchestrator
        host = Host(Path(self.temp.name) / 'data'); self.addCleanup(host.close)
        profile = host.dispatch('add_profile', {'provider': 'claude', 'identity': 'me@example.test', 'category': 'personal'})['id']
        host.store.add_project('p', self.temp.name, profile, 'claude-sonnet-5')
        self.enterContext(patch('claude_policy.executable', lambda: str(self.exe)))
        self.enterContext(patch.object(claude_version, 'read', lambda path, **k: (2, 1, 263)))
        orchestrator = project_orchestrator.Orchestrator(host.store, host=host)
        with self.assertRaisesRegex(Rejected, 'Update Claude Code'):
            orchestrator.account({'project_id': 'p'})

    def test_first_run_says_to_update_an_old_claude_code(self):
        import first_run
        host = Host(Path(self.temp.name) / 'data'); self.addCleanup(host.close)
        self.enterContext(patch('claude_policy.executable', lambda: str(self.exe)))
        auth = []
        signed_in = {'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty', 'subscriptionType': 'max', 'email': 'me@example.test'}
        self.enterContext(patch('claude_policy.auth_status', lambda *a, **k: auth.append(1) or dict(signed_in)))
        row = first_run._claude(host, run=scripted('2.1.263 (Claude Code)'))
        self.assertEqual((row['state'], row['update'], row['version']), ('outdated', 'claude update', '2.1.263'))
        self.assertNotIn('install', row, 'it is installed: the hint says update, not install')
        self.assertIn('Update it to 2.1.271 or later', row['detail'])
        self.assertEqual(auth, [], 'an old Claude Code is reported before its sign-in is read')
        current = first_run._claude(host, run=scripted('2.1.274 (Claude Code)'))
        self.assertEqual((current['state'], current['version']), ('ready', '2.1.274'))

    def test_a_hanging_version_read_is_bounded_and_not_repeated_for_a_minute(self):
        # Security review, 19 Sep, finding 8: --version (then 6 seconds) plus auth status (8) could pass the bridge's 10
        # seconds for one request, and a failed read was retried on every run.
        import subprocess
        calls, now = [], [1000.0]
        def hang(argv, **kwargs):
            calls.append(kwargs['timeout'])
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        clock = lambda: now[0]
        self.assertIsNone(claude_version.read(self.exe, run=hang, clock=clock))
        self.assertEqual(calls, [2], 'the read gets 2 seconds')
        now[0] += 59
        self.assertIsNone(claude_version.read(self.exe, run=hang, clock=clock))
        self.assertEqual(len(calls), 1, 'a failure is kept for a minute, so the next run pays nothing')
        self.assertEqual(claude_version.read(self.exe, run=scripted('2.1.274'), clock=clock, fresh=True), (2, 1, 274),
                         'Check again reads it at once')
        claude_version._seen.clear()
        now[0] += 61
        self.assertIsNone(claude_version.read(self.exe, run=hang, clock=clock))
        self.assertEqual(len(calls), 2, 'after a minute it tries again')

    def test_a_coding_run_bounds_auth_status_so_one_request_stays_under_ten_seconds(self):
        import subprocess
        import claude_coding
        from codex_coding import TrustedTaskBinding
        seen, launched = [], []
        def slow_auth(*args, **kwargs):
            seen.append(kwargs.get('timeout'))
            raise subprocess.TimeoutExpired('claude', kwargs.get('timeout'))
        self.enterContext(patch.object(claude_coding, 'executable', lambda: str(self.exe)))
        self.enterContext(patch.object(claude_coding, 'auth_status', slow_auth))
        self.enterContext(patch.object(claude_coding, 'OwnedProcess', lambda *a, **k: launched.append('launch')))
        self.enterContext(patch.object(claude_version, 'read', lambda path, **k: (2, 1, 274)))
        binding = dict(provider='claude', identity='me@example.test', category='personal', home=self.temp.name,
                       cwd=self.temp.name, model='claude-sonnet-5', session_id=None)
        with self.assertRaisesRegex(Rejected, 'took too long to say which account'):
            claude_coding.ClaudeCodingTurn(binding, task=TrustedTaskBinding('t', binding, lambda *_: True),
                                           context=lambda: ('', 'r'), emit=lambda *_: None)
        self.assertEqual(seen, [claude_coding.AUTH_SECONDS])
        self.assertEqual(launched, [])
        self.assertLess(claude_version.TIMEOUT + claude_coding.AUTH_SECONDS, 10, 'the bridge ends the backend at 10 seconds')

    def test_the_host_operation_checks_its_fields(self):
        host = Host(Path(self.temp.name) / 'data'); self.addCleanup(host.close)
        seen = []
        self.enterContext(patch.object(claude_version, 'status', lambda **kw: seen.append(kw) or {'state': 'ok'}))
        self.assertEqual(host.dispatch('claude_version', {'recheck': False}), {'state': 'ok'})
        host.dispatch('claude_version', {'recheck': True})
        self.assertEqual(seen, [{'fresh': False}, {'fresh': True}])
        for args in ({}, {'recheck': 'yes'}, {'recheck': True, 'path': '/bin/sh'}):
            with self.subTest(args=args), self.assertRaises(Rejected):
                host.dispatch('claude_version', args)


if __name__ == '__main__':
    unittest.main()
