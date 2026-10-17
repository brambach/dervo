"""Developer ID signing and notarisation with a faked step runner. No codesign, notarytool or Keychain is touched."""
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

import release_builder
import release_signing as module

MACH_O = b'\xcf\xfa\xed\xfe' + b'\0' * 28
APPLE_DEVELOPMENT = '  1) B12349804456C3C85B6B7BC390BE1423A103069D "Apple Development: someone@example.test (SJZTZ542CC)"\n'
DEVELOPER_ID = '  2) 0123456789ABCDEF0123456789ABCDEF01234567 "Developer ID Application: Bryce Rambach (ABCDE12345)"\n'
SECOND_ID = '  3) 89ABCDEF0123456789ABCDEF0123456789ABCDEF "Developer ID Application: Someone Else (ZZZZZ99999)"\n'
INSTALLER = '  4) FEDCBA9876543210FEDCBA9876543210FEDCBA98 "Developer ID Installer: Bryce Rambach (ABCDE12345)"\n'
CODEX_DETAILS = ('Identifier=codex\nCodeDirectory v=20500 size=1726225 flags=0x10000(runtime) hashes=53934+7 location=embedded\n'
                 'Timestamp=Sep 10, 2026 at 8:13:46 AM\nTeamIdentifier=2DC432GLL2\n')
ACCEPTED = 'Submission ID received\n  id: 11111111-2222-3333-4444-555555555555\nWaiting for processing to complete.\nProcessing complete\n  id: 11111111-2222-3333-4444-555555555555\n  status: Accepted\n'
INVALID = ACCEPTED.replace('status: Accepted', 'status: Invalid')
GATEKEEPER_OK = 'Dervo Local.app: accepted\nsource=Notarized Developer ID\norigin=Developer ID Application: Bryce Rambach (ABCDE12345)\n'
GATEKEEPER_NO = 'Dervo Local.app: rejected\nsource=Unnotarized Developer ID\n'


class Fake:
    """The builder's run (logged steps) and inspect (parsed probes), scripted per label and per path."""
    def __init__(self, output, *, listing, fail=(), logs=None, unsigned=(), app_team='ABCDE12345'):
        self.output, self.listing, self.fail = Path(output), listing, set(fail)
        self.logs = {'notarise': ACCEPTED, 'gatekeeper': GATEKEEPER_OK, **(logs or {})}
        self.unsigned, self.app_team = {str(p) for p in unsigned}, app_team
        self.steps, self.probes = [], []

    def run(self, label, command, cwd, timeout=1200):
        self.steps.append((label, list(command), timeout))
        (self.output / (label + '.log')).write_text(self.logs.get(label, label + ' output\n'))
        if label in self.fail:
            raise RuntimeError(label + ' failed. Open its log in this release folder.')

    def inspect(self, command, env=None):
        self.probes.append(list(command))
        if command[1] == 'find-identity':
            return 0, self.listing
        path = command[-1]
        if command[1] == '--verify':
            return (1 if path in self.unsigned else 0), ''
        if path.endswith('.app'):
            return 0, 'Identifier=local.dervo.connected\nCodeDirectory v=20500 flags=0x10000(runtime)\nTimestamp=now\nTeamIdentifier=' + self.app_team + '\n'
        if path in self.unsigned:
            return 1, 'code object is not signed at all\n'
        return 0, CODEX_DETAILS

    def labels(self):
        return [label for label, _, _ in self.steps]

    def command(self, label):
        return next(command for name, command, _ in self.steps if name == label)


class SigningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output, self.work = self.root / 'release', self.root / 'work'
        self.output.mkdir(); (self.work / 'signing').mkdir(parents=True)
        (self.work / module.ENTITLEMENTS).write_bytes(plistlib.dumps({}))
        self.bundle = self.output / 'Dervo Local.app'
        self.files = {}
        for relative in ('Contents/MacOS/dervo-local', 'Contents/MacOS/helper-tool', 'Contents/Resources/runtime/codex',
                         'Contents/Resources/runtime/codex-code-mode-host', 'Contents/Frameworks/Helper.framework/Versions/A/Helper'):
            path = self.bundle / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(MACH_O)
            self.files[relative] = path
        (self.bundle / 'Contents/Resources/backend').mkdir()
        (self.bundle / 'Contents/Resources/backend/host.py').write_text('print("not code to sign")')
        (self.bundle / 'Contents/Resources/runtime/codex.json').write_text(json.dumps({'file': 'codex', 'companion': {'file': 'codex-code-mode-host'}}))
        self.helpers = [self.files['Contents/Frameworks/Helper.framework/Versions/A/Helper'], self.files['Contents/MacOS/helper-tool']]

    def sign(self, fake, config=None):
        return module.sign(self.bundle, run=fake.run, output=self.output, work=self.work, config=config or {}, inspect=fake.inspect)

    def fake(self, listing=APPLE_DEVELOPMENT + DEVELOPER_ID, **kwargs):
        return Fake(self.output, listing=listing, unsigned=kwargs.pop('unsigned', self.helpers), **kwargs)

    # --- which path ------------------------------------------------------------------------------------------------
    def test_identity_lookup_failure_stops_before_signing(self):
        for listing in ('', DEVELOPER_ID):
            with self.subTest(listing=listing):
                fake = self.fake()
                fake.inspect = lambda command, env=None: (1, listing)
                with self.assertRaisesRegex(RuntimeError, 'signing identities'):
                    self.sign(fake)
                self.assertEqual(fake.steps, [])

    def test_without_a_developer_id_the_build_signs_ad_hoc_exactly_as_before(self):
        for listing in ('', APPLE_DEVELOPMENT, APPLE_DEVELOPMENT + INSTALLER, '     0 valid identities found\n'):
            with self.subTest(listing=listing.strip()[:30]):
                fake = self.fake(listing)
                record = self.sign(fake)
                self.assertEqual(fake.steps, [('sign', ['/usr/bin/codesign', '--force', '--sign', '-', str(self.bundle)], 1200),
                                              ('verify-sign', ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.bundle)], 1200)])
                self.assertEqual([p[1] for p in fake.probes], ['find-identity'], 'nothing else is probed on the ad hoc path')
                self.assertEqual((record['mode'], record['identity'], record['team'], record['notarised'], record['stapled']), ('ad-hoc', None, None, False, False))
                self.assertIn('Gatekeeper warning', record['detail'])
        fake = self.fake()
        self.assertEqual(self.sign(fake)['mode'], 'developer-id', 'control: a Developer ID certificate leaves the ad hoc path')
        self.assertNotIn(['/usr/bin/codesign', '--force', '--sign', '-', str(self.bundle)], [c for _, c, _ in fake.steps])

    def test_a_developer_id_signs_inside_out_then_notarises_staples_and_checks_gatekeeper(self):
        fake = self.fake()
        record = self.sign(fake)
        self.assertEqual(fake.labels(), ['sign-nested-1', 'sign-nested-2', 'sign', 'verify-sign', 'notary-zip', 'notarise', 'staple',
                                         'staple-validate', 'verify-stapled', 'gatekeeper'])
        self.assertEqual(fake.command('sign-nested-1')[-1], str(self.helpers[0]), 'the deepest binary first')
        self.assertEqual(fake.command('sign-nested-2')[-1], str(self.helpers[1]))
        for label in ('sign-nested-1', 'sign-nested-2', 'sign'):
            command = fake.command(label)
            self.assertEqual(command[:2], ['/usr/bin/codesign', '--force'])
            self.assertIn('runtime', command[command.index('--options') + 1])
            self.assertIn('--timestamp', command)
            self.assertEqual(command[command.index('--sign') + 1], '0123456789ABCDEF0123456789ABCDEF01234567')
        sign = fake.command('sign')
        self.assertEqual(sign[sign.index('--entitlements') + 1], str(self.work / module.ENTITLEMENTS))
        self.assertEqual(sign[-1], str(self.bundle))
        self.assertNotIn('--entitlements', fake.command('sign-nested-1'), 'helpers get no entitlements')
        self.assertEqual(fake.command('verify-sign')[:5], ['/usr/bin/codesign', '--verify', '--deep', '--strict', '--verbose=2'])
        notarise = fake.command('notarise')
        self.assertEqual(notarise[:3], ['/usr/bin/xcrun', 'notarytool', 'submit'])
        self.assertEqual(notarise[notarise.index('--keychain-profile') + 1], 'dervo-notary')
        self.assertIn('--wait', notarise)
        self.assertEqual(next(t for l, _, t in fake.steps if l == 'notarise'), 3600, 'Apple can take a while')
        self.assertEqual(fake.command('staple'), ['/usr/bin/xcrun', 'stapler', 'staple', str(self.bundle)])
        self.assertEqual(fake.command('gatekeeper')[:4], ['/usr/sbin/spctl', '--assess', '--type', 'execute'])
        self.assertFalse((self.output / module.NOTARY_UPLOAD).exists(), 'the upload zip is removed')
        self.assertEqual(record['mode'], 'developer-id')
        self.assertEqual((record['identity'], record['team'], record['profile']), ('Developer ID Application: Bryce Rambach (ABCDE12345)', 'ABCDE12345', 'dervo-notary'))
        self.assertEqual(record['notarisation'], {'id': '11111111-2222-3333-4444-555555555555', 'status': 'Accepted'})
        self.assertTrue(record['notarised'] and record['stapled'])
        self.assertIn('notarised by Apple', record['detail'])
        self.assertEqual(record['nested'], [
            {'path': 'Contents/Frameworks/Helper.framework/Versions/A/Helper', 'action': 'signed', 'team': 'ABCDE12345'},
            {'path': 'Contents/Resources/runtime/codex', 'action': 'kept', 'team': '2DC432GLL2'},
            {'path': 'Contents/Resources/runtime/codex-code-mode-host', 'action': 'kept', 'team': '2DC432GLL2'},
            {'path': 'Contents/MacOS/helper-tool', 'action': 'signed', 'team': 'ABCDE12345'}])
        self.assertNotIn(str(self.bundle / 'Contents/Resources/backend/host.py'), str(fake.steps), 'text files are sealed, not signed')

    def test_publisher_signed_binaries_are_kept_and_the_pinned_codex_runtime_is_never_re_signed(self):
        fake = self.fake()
        self.sign(fake)
        signed = [c[-1] for l, c, _ in fake.steps if l.startswith('sign-nested')]
        self.assertNotIn(str(self.files['Contents/Resources/runtime/codex']), signed)
        # Control: a Codex binary that arrived without its publisher's hardened signature can't be fixed here, because
        # codex_runtime.py would refuse the changed hash at launch.
        fake = self.fake(unsigned=self.helpers + [self.files['Contents/Resources/runtime/codex-code-mode-host']])
        with self.assertRaisesRegex(RuntimeError, 'codex-code-mode-host.*Vendor the official build'):
            self.sign(fake)
        self.assertEqual(fake.labels(), ['sign-nested-1'], 'the deeper helper was signed first; nothing after the refusal')
        # Control for the control: the same unsigned state on a binary Dervo doesn't pin is simply signed.
        (self.bundle / 'Contents/Resources/runtime/codex.json').unlink()
        fake = self.fake(unsigned=self.helpers + [self.files['Contents/Resources/runtime/codex-code-mode-host']])
        self.sign(fake)
        self.assertIn(str(self.files['Contents/Resources/runtime/codex-code-mode-host']), [c[-1] for l, c, _ in fake.steps if l.startswith('sign-nested')])

    def test_the_apps_own_python_is_signed_with_dervos_identity_and_the_hardened_runtime(self):
        # beta.17: scripts/python_runtime.py leaves one Mach-O in the runtime, linker-signed ad hoc by its builder. It
        # isn't pinned by hash at launch like Codex, so it's signed here like any other nested code, before the app.
        python = self.bundle / 'Contents/Resources/runtime/python/bin/python3.13'
        python.parent.mkdir(parents=True); python.write_bytes(MACH_O)
        (self.bundle / 'Contents/Resources/runtime/python/lib/python3.13').mkdir(parents=True)
        (self.bundle / 'Contents/Resources/runtime/python/lib/python3.13/os.py').write_text('# sealed, not signed\n')
        fake = self.fake(unsigned=self.helpers + [python])
        record = self.sign(fake)
        nested = [(label, command) for label, command, _ in fake.steps if label.startswith('sign-nested')]
        own = [command for _, command in nested if command[-1] == str(python)]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0][:6], ['/usr/bin/codesign', '--force', '--options', 'runtime', '--timestamp', '--sign'])
        self.assertNotIn('--entitlements', own[0], 'the runtime gets no entitlements of its own')
        self.assertIn({'path': 'Contents/Resources/runtime/python/bin/python3.13', 'action': 'signed', 'team': 'ABCDE12345'}, record['nested'])
        self.assertLess(fake.labels().index(next(l for l, c in nested if c[-1] == str(python))), fake.labels().index('sign'))
        self.assertNotIn('os.py', str(fake.steps))

    def test_the_voice_helper_alone_gets_the_microphone_entitlement(self):
        # Voice (scripts/voice_runtime.py): the audio helper reads the mic under the hardened runtime, so it's signed
        # with audio-input; voice's libraries are signed plain, and dervo-local keeps its empty entitlements.
        helper = self.bundle / 'Contents/Resources/voice/dervo-voice-audio'
        library = self.bundle / 'Contents/Resources/voice/site-packages/numpy/_core/_multiarray_umath.cpython-313-darwin.so'
        for path in (helper, library):
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(MACH_O)
        (self.work / 'voice/mac').mkdir(parents=True)
        (self.work / module.NESTED_ENTITLEMENTS['Contents/Resources/voice/dervo-voice-audio']).write_bytes(
            plistlib.dumps({'com.apple.security.device.audio-input': True}))
        fake = self.fake(unsigned=self.helpers + [helper, library])
        record = self.sign(fake)
        commands = {command[-1]: command for label, command, _ in fake.steps if label.startswith('sign-nested')}
        self.assertIn('--entitlements', commands[str(helper)])
        self.assertTrue(commands[str(helper)][commands[str(helper)].index('--entitlements') + 1].endswith('dervo-voice-audio.entitlements'))
        self.assertNotIn('--entitlements', commands[str(library)])
        self.assertIn({'path': 'Contents/Resources/voice/dervo-voice-audio', 'action': 'signed', 'team': 'ABCDE12345',
                       'entitlements': 'voice/mac/dervo-voice-audio.entitlements'}, record['nested'])

    # --- choosing the identity --------------------------------------------------------------------------------------
    def test_the_config_can_name_the_identity_or_force_ad_hoc(self):
        for wanted in ('Bryce Rambach', 'ABCDE12345', '0123456789ABCDEF0123456789ABCDEF01234567', 'Developer ID Application: Bryce Rambach (ABCDE12345)'):
            with self.subTest(wanted=wanted):
                fake = self.fake(APPLE_DEVELOPMENT + DEVELOPER_ID + SECOND_ID)
                self.assertEqual(self.sign(fake, {'signingIdentity': wanted})['team'], 'ABCDE12345')
        fake = self.fake(APPLE_DEVELOPMENT + DEVELOPER_ID + SECOND_ID)
        with self.assertRaisesRegex(RuntimeError, 'More than one Developer ID'):
            self.sign(fake)
        self.assertEqual(fake.steps, [], 'nothing signed until the choice is made')
        for wrong in ('Nobody', 'SJZTZ542CC', '', '   ', 7):
            with self.subTest(wrong=wrong), self.assertRaisesRegex(RuntimeError, 'signingIdentity'):
                self.sign(self.fake(), {'signingIdentity': wrong})
        fake = self.fake()
        self.assertEqual(self.sign(fake, {'signingIdentity': '-'})['mode'], 'ad-hoc', '"-" keeps ad hoc while the certificate exists')
        self.assertEqual(fake.labels(), ['sign', 'verify-sign'])

    def test_identity_listing_only_counts_developer_id_application_certificates(self):
        found = module.identities(APPLE_DEVELOPMENT + DEVELOPER_ID + INSTALLER + SECOND_ID + '     4 valid identities found\n')
        self.assertEqual([(i['holder'], i['team']) for i in found], [('Bryce Rambach', 'ABCDE12345'), ('Someone Else', 'ZZZZZ99999')])
        self.assertEqual(module.identities(APPLE_DEVELOPMENT + INSTALLER), [])
        self.assertEqual(module.choose_identity({}, []), None)

    # --- each later step can stop the build ---------------------------------------------------------------------------
    def test_a_signature_that_doesnt_verify_stops_before_anything_is_uploaded(self):
        fake = self.fake(fail=('verify-sign',))
        with self.assertRaisesRegex(RuntimeError, 'verify-sign failed'):
            self.sign(fake)
        self.assertEqual(fake.labels(), ['sign-nested-1', 'sign-nested-2', 'sign', 'verify-sign'])
        self.assertNotIn('notarise', fake.labels())

    def test_the_signed_app_must_carry_the_identitys_team_and_the_hardened_runtime(self):
        fake = self.fake(app_team='ZZZZZ99999')
        with self.assertRaisesRegex(RuntimeError, 'expected Team ID'):
            self.sign(fake)
        self.assertNotIn('notary-zip', fake.labels())
        self.assertEqual(self.sign(self.fake(app_team='ABCDE12345'))['team'], 'ABCDE12345', 'control')

    def test_a_notarisation_apple_refuses_fetches_the_reasons_and_never_staples(self):
        for scenario, kwargs in (('status Invalid', {'logs': {'notarise': INVALID}}), ('submit exits non-zero', {'fail': ('notarise',), 'logs': {'notarise': INVALID}}),
                                 ('no status at all', {'fail': ('notarise',), 'logs': {'notarise': 'Error: HTTP status code: 401\n'}})):
            with self.subTest(scenario=scenario):
                fake = self.fake(**kwargs)
                with self.assertRaisesRegex(RuntimeError, 'didn’t accept the build for notarisation'):
                    self.sign(fake)
                self.assertNotIn('staple', fake.labels())
                self.assertNotIn('gatekeeper', fake.labels())
                self.assertFalse((self.output / module.NOTARY_UPLOAD).exists(), 'the upload zip is removed on failure too')
                if 'Invalid' in kwargs['logs']['notarise']:
                    log = fake.command('notary-log')
                    self.assertEqual(log[:4], ['/usr/bin/xcrun', 'notarytool', 'log', '11111111-2222-3333-4444-555555555555'])
                    self.assertEqual(log[-1], str(self.output / module.NOTARY_LOG))
                else:
                    self.assertNotIn('notary-log', fake.labels(), 'no submission id, nothing to fetch')
        fake = self.fake(fail=('notary-log',), logs={'notarise': INVALID})
        with self.assertRaisesRegex(RuntimeError, 'didn’t accept'):
            self.sign(fake)
        self.assertTrue(self.sign(self.fake())['notarised'], 'control: Accepted goes on to staple')

    def test_a_failed_staple_stops_before_gatekeeper_and_a_failed_validate_too(self):
        for step in ('staple', 'staple-validate'):
            with self.subTest(step=step):
                fake = self.fake(fail=(step,))
                with self.assertRaisesRegex(RuntimeError, step + ' failed'):
                    self.sign(fake)
                self.assertNotIn('gatekeeper', fake.labels())
        self.assertTrue(self.sign(self.fake())['stapled'], 'control')

    def test_gatekeeper_must_accept_the_stapled_app_as_notarised(self):
        for text in (GATEKEEPER_NO, 'Dervo Local.app: accepted\nsource=Developer ID\n', ''):
            with self.subTest(text=text[:30]):
                fake = self.fake(logs={'gatekeeper': text})
                with self.assertRaisesRegex(RuntimeError, 'Gatekeeper didn’t accept'):
                    self.sign(fake)
        fake = self.fake(fail=('gatekeeper',), logs={'gatekeeper': GATEKEEPER_NO})
        with self.assertRaisesRegex(RuntimeError, 'gatekeeper failed'):
            self.sign(fake)
        self.assertIn('Gatekeeper accepts it', self.sign(self.fake())['detail'], 'control')

    # --- secrets and entitlements -----------------------------------------------------------------------------------
    def test_no_secret_reaches_a_command_or_a_log(self):
        for key in ('applePassword', 'notaryPassword', 'NOTARY_SECRET', 'appleApiKey'):
            with self.subTest(key=key):
                fake = self.fake()
                with self.assertRaisesRegex(RuntimeError, key + '.*store-credentials dervo-notary'):
                    self.sign(fake, {key: 'hunter2-never-logged'})
                self.assertEqual(fake.steps, [])
                self.assertEqual(fake.probes, [], 'refused before even listing identities')
        fake = self.fake()
        self.sign(fake, {'notaryProfile': 'dervo-notary', 'signingIdentity': 'ABCDE12345', 'node': '/usr/local/bin/node'})
        everything = json.dumps(fake.steps) + ''.join(p.read_text() for p in self.output.glob('*.log'))
        self.assertNotIn('hunter2', everything)
        for flag in ('--password', '--apple-id', '--team-id', '--key', '--issuer', '-p'):
            self.assertNotIn(flag, [part for _, command, _ in fake.steps for part in command], flag)
        with self.assertRaisesRegex(RuntimeError, 'notaryProfile'):
            self.sign(self.fake(), {'notaryProfile': 'bad profile; rm -rf'})
        self.assertEqual(self.sign(self.fake(), {'notaryProfile': 'other-profile'})['profile'], 'other-profile', 'control: a plain name is used')

    def test_entitlements_ship_in_the_snapshot_and_are_a_minimal_plist(self):
        self.assertIn('signing', release_builder.SOURCE_ITEMS)
        source = Path(__file__).resolve().parent.parent / module.ENTITLEMENTS
        self.assertEqual(plistlib.loads(source.read_bytes()), {}, 'every entitlement needs a checked reason; none has one yet')
        text = source.read_text()
        self.assertNotIn(chr(0x2014), text, 'house style: no em dashes')
        for considered in ('allow-jit', 'disable-library-validation', 'keychain-access-groups'):
            self.assertIn(considered, text, 'the file says why ' + considered + ' is not there')
        (self.work / module.ENTITLEMENTS).unlink()
        fake = self.fake()
        with self.assertRaisesRegex(RuntimeError, 'entitlements file is missing'):
            self.sign(fake)
        self.assertEqual(fake.steps, [])
        (self.work / module.ENTITLEMENTS).write_bytes(plistlib.dumps(['not', 'a', 'dict']))
        with self.assertRaisesRegex(RuntimeError, 'plist dictionary'):
            self.sign(self.fake())
        self.assertEqual(self.sign(self.fake('')).get('entitlements'), None, 'control: the ad hoc path never needs the file')

    # --- parsers ----------------------------------------------------------------------------------------------------
    def test_signature_details_and_notary_and_gatekeeper_parsers(self):
        self.assertEqual(module.signature_details(CODEX_DETAILS), {'team': '2DC432GLL2', 'hardened': True, 'timestamped': True})
        self.assertEqual(module.signature_details('CodeDirectory v=20400 flags=0x2(adhoc) hashes=1+2\nTeamIdentifier=not set\n'),
                         {'team': None, 'hardened': False, 'timestamped': False})
        self.assertEqual(module.signature_details('CodeDirectory v=20500 flags=0x10002(adhoc,runtime)\nTeamIdentifier=../x\n'),
                         {'team': None, 'hardened': True, 'timestamped': False})
        self.assertEqual(module.signature_details('code object is not signed at all\n'), {'team': None, 'hardened': False, 'timestamped': False})
        self.assertEqual(module.notary_result(ACCEPTED), ('11111111-2222-3333-4444-555555555555', 'Accepted'))
        self.assertEqual(module.notary_result(INVALID), ('11111111-2222-3333-4444-555555555555', 'Invalid'))
        self.assertEqual(module.notary_result('Error: HTTP status code: 401\n'), (None, None))
        self.assertTrue(module.gatekeeper_accepted(GATEKEEPER_OK))
        self.assertFalse(module.gatekeeper_accepted(GATEKEEPER_NO))
        self.assertFalse(module.gatekeeper_accepted('x: accepted\nsource=Developer ID\n'), 'accepted but not notarised is not enough')

    def test_mach_o_discovery_skips_text_symlinks_and_the_main_executable(self):
        link = self.bundle / 'Contents/MacOS/link'; link.symlink_to(self.files['Contents/MacOS/helper-tool'])
        (self.bundle / 'Contents/_CodeSignature').mkdir(); (self.bundle / 'Contents/_CodeSignature/CodeResources').write_bytes(MACH_O)
        found = [str(p.relative_to(self.bundle)) for p in module.mach_o_files(self.bundle)]
        self.assertEqual(found, ['Contents/Frameworks/Helper.framework/Versions/A/Helper', 'Contents/Resources/runtime/codex',
                                 'Contents/Resources/runtime/codex-code-mode-host', 'Contents/MacOS/helper-tool'])
        self.assertEqual(module.pinned_runtime_files(self.bundle), {'Contents/Resources/runtime/codex', 'Contents/Resources/runtime/codex-code-mode-host'})
        (self.bundle / 'Contents/Resources/runtime/codex.json').write_text('not json')
        self.assertEqual(module.pinned_runtime_files(self.bundle), set())


class BuilderIntegration(unittest.TestCase):
    """release_builder.build drives the signing step with its own logged runner: every child carries the slot fds,
    the logs land in the release folder and release.json records the signing outcome."""
    def setUp(self):
        import account_config
        from release_storage import BuildStorage
        from test_supabase_client import PROJECT
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'project'
        (self.source / 'frontend/node_modules').mkdir(parents=True)
        (self.source / 'frontend/main.ts').write_text('export const version = 1;')
        (self.source / 'tauri.conf.json').write_text(json.dumps({'identifier': 'local.dervo.connected', 'version': '0.1.0'}))
        (self.source / 'Info.plist').write_bytes(plistlib.dumps({}))
        (self.source / 'signing').mkdir(); (self.source / module.ENTITLEMENTS).write_bytes(plistlib.dumps({}))
        self.storage = BuildStorage(self.source, self.root / 'cache')
        account_config.stage(self.source, PROJECT)
        self.listing = APPLE_DEVELOPMENT + DEVELOPER_ID
        self.calls = []
        self.canary_exit = 0

    def fake_command(self, command, **kwargs):
        import shutil
        import subprocess
        import account_config
        self.calls.append((list(command), kwargs))
        out = ''
        if command[1:] == ['--keychain-self-check']:
            return subprocess.CompletedProcess(command, self.canary_exit, stdout='', stderr='')
        if command[0] == '/bin/cp':
            shutil.copytree(command[-2], command[-1])
        elif 'build' in command and '--bundles' in command:
            target = Path(kwargs['env']['CARGO_TARGET_DIR'])
            bundle = target / 'release/bundle/macos/Dervo Local.app'
            (bundle / 'Contents/Resources/runtime').mkdir(parents=True)
            for name in ('dervo-release-profile.json', account_config.NAME):
                shutil.copy2(Path(kwargs['cwd']) / 'runtime' / name, bundle / 'Contents/Resources/runtime' / name)
            (bundle / 'Contents/MacOS').mkdir()
            (bundle / 'Contents/MacOS/dervo-local').write_bytes(MACH_O)
            shutil.copy2(Path(kwargs['cwd']) / 'Info.plist', bundle / 'Contents/Info.plist')
        elif command[1] == 'find-identity':
            out = self.listing
        elif command[0] == '/usr/bin/codesign' and command[1] == '-d':
            out = 'CodeDirectory v=20500 flags=0x10000(runtime)\nTimestamp=now\nTeamIdentifier=ABCDE12345\n'
        elif command[1:3] == ['notarytool', 'submit']:
            kwargs['stdout'].write(ACCEPTED)
        elif command[0] == '/usr/sbin/spctl':
            kwargs['stdout'].write(GATEKEEPER_OK)
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr='')

    def build(self, name):
        import sys
        from unittest.mock import patch
        with patch('release_builder.BuildStorage', return_value=self.storage), patch('release_builder.shutil.which', return_value=sys.executable), \
                patch('release_builder.subprocess.run', side_effect=self.fake_command):
            return release_builder.build(self.source, self.root / name, {})

    def test_a_developer_id_build_is_signed_notarised_and_recorded_with_its_logs(self):
        record = self.build('signed')
        self.assertEqual(record['state'], 'built')
        signing = record['signing']
        self.assertEqual((signing['mode'], signing['team'], signing['notarised'], signing['stapled']), ('developer-id', 'ABCDE12345', True, True))
        self.assertEqual(signing['notarisation'], {'id': '11111111-2222-3333-4444-555555555555', 'status': 'Accepted'})
        self.assertIn('notarised by Apple', record['verification'])
        self.assertEqual(json.loads((self.root / 'signed/release.json').read_text())['signing'], signing)
        steps = [step['name'] for step in record['steps']]
        self.assertEqual(steps[steps.index('sign'):], ['sign', 'verify-sign', 'notary-zip', 'notarise', 'staple', 'staple-validate', 'verify-stapled', 'gatekeeper', 'native-keychain-check'])
        self.assertEqual(record['native_keychain'], {'state': 'passed', 'scope': 'disposable fixture only'})
        for label in ('sign', 'notarise', 'gatekeeper'):
            self.assertTrue((self.root / 'signed' / (label + '.log')).is_file())
        self.assertTrue(all(kwargs.get('pass_fds') for _, kwargs in self.calls), 'probes carry the slot fds like every other child')
        notarise = next((c, k) for c, k in self.calls if c[1:3] == ['notarytool', 'submit'])
        self.assertEqual(notarise[1]['timeout'], 3600)
        self.assertIn('HOME', notarise[1]['env'], 'notarytool and codesign read the login Keychain through HOME')
        sign = next(c for c, _ in self.calls if c[:2] == ['/usr/bin/codesign', '--force'] and c[-1].endswith('.app'))
        self.assertIn('--entitlements', sign)
        self.assertEqual(sign[sign.index('--entitlements') + 1], str(self.storage.work / module.ENTITLEMENTS), 'the snapshot copy, not the source checkout')
        self.assertFalse((self.root / 'signed' / module.NOTARY_UPLOAD).exists())

    def test_failed_native_keychain_check_prevents_a_ready_build(self):
        self.canary_exit = 1
        with self.assertRaisesRegex(RuntimeError, 'native-keychain-check failed'):
            self.build('failed-canary')
        record = json.loads((self.root / 'failed-canary/release.json').read_text())
        self.assertEqual(record['state'], 'failed')
        self.assertNotIn('native_keychain', record)
        self.assertIsNone(record['bundle'])

    def test_without_a_certificate_the_build_is_ad_hoc_as_before(self):
        self.listing = APPLE_DEVELOPMENT
        record = self.build('adhoc')
        self.assertEqual(record['state'], 'built')
        self.assertEqual((record['signing']['mode'], record['signing']['team'], record['signing']['notarised']), ('ad-hoc', None, False))
        codesign = [c for c, _ in self.calls if c[0] == '/usr/bin/codesign']
        bundle = str(self.root / 'adhoc/Dervo Local.app')
        self.assertEqual(codesign, [['/usr/bin/codesign', '--force', '--sign', '-', bundle], ['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle]])
        self.assertFalse(any(c[0] == '/usr/bin/xcrun' or c[0] == '/usr/sbin/spctl' for c, _ in self.calls))
        self.assertIn('Gatekeeper warning', record['verification'])


if __name__ == '__main__':
    unittest.main()
