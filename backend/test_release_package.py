"""Release packaging rejects mismatched builds and leaves failed output unpublished."""
import json
from pathlib import Path
import plistlib
import tempfile
import sys
import unittest
from unittest.mock import patch

import account_config
import release_package as packaging
import release_profiles
from test_supabase_client import PROJECT


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.release = self.root / '0.1.0-prebeta.1'
        self.bundle = self.release / 'Dervo Local.app'
        self.resources = self.bundle / 'Contents/Resources'
        self.resources.mkdir(parents=True)
        release_profiles.stage(self.resources, 'private-beta')
        account_config.stage(self.resources, PROJECT)
        self.profile = release_profiles.manifest()
        self.native = {'productVersion': '0.1.0', 'buildNumber': '123', 'releaseId': self.release.name}
        (self.resources / 'runtime/dervo-preview.json').write_text(json.dumps(
            {'releaseId': self.release.name, 'releaseProfile': self.profile, 'nativeVersion': self.native}))
        executable = self.bundle / 'Contents/MacOS/dervo-local'
        executable.parent.mkdir()
        executable.write_bytes(b'fixture executable')
        executable.chmod(0o755)
        (self.bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.dervo.connected', 'CFBundleShortVersionString': '0.1.0',
            'CFBundleVersion': '123', 'DervoReleaseID': self.release.name}))
        self.record = {'id': self.release.name, 'state': 'built', 'release_profile': self.profile,
                       'signing': {'mode': 'developer-id', 'team': packaging.DISTRIBUTION_TEAM, 'notarised': True, 'stapled': True},
                       'native_version': self.native,
                       'executable_sha256': packaging.digest(executable)}
        self.save()
        self.instructions = self.root / 'instructions.txt'
        self.instructions.write_text('Sign into your Dervo account. Beta access must be approved.')
        self.signature = (0, 'CodeDirectory v=20500 flags=0x10000(runtime)\nTimestamp=now\nTeamIdentifier='
                          + packaging.DISTRIBUTION_TEAM + '\n')
        self.probe = self.enterContext(patch.object(packaging.release_signing, 'inspect', return_value=self.signature))

    def save(self):
        (self.release / 'release.json').write_text(json.dumps(self.record))

    def test_valid_inspection_checks_signature_without_secret_environment(self):
        with patch.object(packaging, 'run') as run:
            release_id, bundle, files, audit = packaging.inspect_release(self.release)
        self.assertEqual(release_id, self.release.name)
        self.assertEqual(bundle, self.bundle)
        self.assertEqual(files['Contents/MacOS/dervo-local']['mode'], 0o755)
        self.assertEqual(audit['profile'], 'private-beta')
        self.assertIn('--strict', run.call_args_list[0].args[0])
        self.assertEqual(audit['distributionSignature']['gatekeeper'], 'accepted')

    def test_distribution_refuses_ad_hoc_wrong_team_and_unnotarised_records(self):
        original = self.record['signing'].copy()
        for change in ({'mode': 'ad-hoc'}, {'team': 'ZZZZZ99999'}, {'notarised': False}, {'stapled': False}):
            self.record['signing'] = {**original, **change}
            self.save()
            with self.subTest(change=change), patch.object(packaging, 'run'), self.assertRaisesRegex(ValueError, 'notarised Developer ID'):
                packaging.inspect_release(self.release)

    def test_distribution_rechecks_the_bundle_instead_of_trusting_the_record(self):
        for probe in ((1, self.signature[1]), (0, self.signature[1].replace(packaging.DISTRIBUTION_TEAM, 'ZZZZZ99999')),
                      (0, 'Signature=adhoc\nTeamIdentifier=not set\n')):
            self.probe.return_value = probe
            with self.subTest(probe=probe), patch.object(packaging, 'run'), self.assertRaisesRegex(ValueError, 'expected Developer ID'):
                packaging.inspect_release(self.release)

    def test_changed_binary_and_marker_are_rejected_before_signing(self):
        with patch.object(packaging, 'run') as run:
            self.record['executable_sha256'] = '0' * 64
            self.save()
            with self.assertRaisesRegex(ValueError, 'executable differs'):
                packaging.inspect_release(self.release)
            marker = self.resources / 'runtime/dervo-preview.json'
            marker.write_text(json.dumps({'releaseId': 'other', 'releaseProfile': self.profile}))
            with self.assertRaisesRegex(ValueError, 'different builds'):
                packaging.inspect_release(self.release)
        run.assert_not_called()

    def test_personal_or_incomplete_release_cannot_be_packaged(self):
        for change in ({'state': 'failed'}, {'release_profile': release_profiles.manifest('personal-experimental')},
                       {'id': '../escape'}):
            original = self.record.copy()
            self.record.update(change)
            self.save()
            with self.assertRaises(ValueError):
                packaging.inspect_release(self.release)
            self.record = original

    def test_legacy_and_mismatched_native_versions_are_rejected(self):
        self.record.pop('native_version')
        self.save()
        with self.assertRaisesRegex(ValueError, 'needs native version'):
            packaging.inspect_release(self.release)
        self.record['native_version'] = self.native
        self.save()
        path = self.bundle / 'Contents/Info.plist'
        info = plistlib.loads(path.read_bytes())
        info['CFBundleVersion'] = '122'
        path.write_bytes(plistlib.dumps(info))
        with self.assertRaisesRegex(ValueError, 'Native bundle version differs'):
            packaging.inspect_release(self.release)

    def test_null_version_cannot_match_missing_native_fields(self):
        invalid = {**self.native, 'productVersion': None, 'buildNumber': None}
        self.record['native_version'] = invalid
        self.save()
        (self.resources / 'runtime/dervo-preview.json').write_text(json.dumps({
            'releaseId': self.release.name, 'releaseProfile': self.profile, 'nativeVersion': invalid}))
        (self.bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleIdentifier': 'local.dervo.connected', 'DervoReleaseID': self.release.name}))
        with patch.object(packaging, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'major.minor.patch'):
                packaging.inspect_release(self.release)
        run.assert_not_called()

    def test_accounts_outside_resources_and_symlinks_are_rejected(self):
        extra = self.bundle / 'Contents/auth.json'
        extra.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'account or workspace'):
            packaging.inspect_release(self.release)
        extra.unlink()
        extra.symlink_to(self.instructions)
        with self.assertRaisesRegex(ValueError, 'Linked bundle entry'):
            packaging.inspect_release(self.release)

    def test_inventory_detects_content_and_permission_changes(self):
        before = packaging.inventory(self.bundle)
        executable = self.bundle / 'Contents/MacOS/dervo-local'
        executable.chmod(0o644)
        self.assertNotEqual(before, packaging.inventory(self.bundle))
        executable.chmod(0o755)
        executable.write_bytes(b'changed executable')
        self.assertNotEqual(before, packaging.inventory(self.bundle))

    def test_existing_output_is_never_overwritten(self):
        output = self.root / 'already-there'
        output.mkdir()
        marker = output / 'keep.txt'
        marker.write_text('keep')
        with self.assertRaises(ValueError):
            packaging.package(self.release, output, self.instructions)
        self.assertEqual(marker.read_text(), 'keep')

    def test_failed_copy_leaves_no_package_and_cleans_temporary_directory(self):
        output = self.root / 'packages' / 'candidate'
        calls = []
        def command(args):
            calls.append(args)
            if args[0] == '/usr/bin/ditto':
                raise RuntimeError('copy failed')
            return b''
        with patch.object(packaging, 'run', side_effect=command), patch.object(packaging, 'require_space'):
            with self.assertRaisesRegex(RuntimeError, 'copy failed'):
                packaging.package(self.release, output, self.instructions)
        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.iterdir()), [])
        self.assertTrue(self.bundle.exists())

    def test_tool_errors_do_not_relay_output(self):
        import subprocess
        with patch.object(packaging.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 1, stdout=b'private output', stderr=b'private error')) as run:
            with self.assertRaises(RuntimeError) as caught:
                packaging.run(['/usr/bin/ditto', 'fixture'])
        self.assertNotIn('private', str(caught.exception))
        self.assertEqual(set(run.call_args.kwargs['env']), {'PATH'})

    @unittest.skipUnless(sys.platform == 'darwin', "renamex_np in libSystem: the Mac release builder's publish step")
    def test_publication_refuses_even_an_empty_intervening_destination(self):
        source = self.root / 'ready'
        source.mkdir()
        (source / 'artifact').write_text('archive')
        destination = self.root / 'published'
        destination.mkdir()
        inode = destination.stat().st_ino
        with self.assertRaises(OSError):
            packaging.publish(source, destination)
        self.assertEqual(destination.stat().st_ino, inode)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual((source / 'artifact').read_text(), 'archive')
        destination.rmdir()
        packaging.publish(source, destination)
        self.assertEqual((destination / 'artifact').read_text(), 'archive')

    def test_a_disk_image_carries_the_applications_shortcut_and_a_zip_never_does(self):
        # The drag-to-Applications window (Harrison, 25 Sep): the app sits beside a link to /Applications.
        payload = self.root / 'payload'
        app = payload / 'Dervo Local.app/Contents'
        app.mkdir(parents=True)
        (app / 'Info.plist').write_text('x')
        (payload / 'Start here.txt').write_text('steps')
        files = packaging.inventory(payload / 'Dervo Local.app')
        with patch.object(packaging, 'run', return_value=b''):
            with self.assertRaisesRegex(ValueError, 'unexpected top-level'):
                packaging.check_payload(payload, files, 'steps', disk_image=True)
            (payload / 'Applications').symlink_to('/Applications')
            packaging.check_payload(payload, files, 'steps', disk_image=True)
            with self.assertRaisesRegex(ValueError, 'unexpected top-level'):
                packaging.check_payload(payload, files, 'steps')
            (payload / 'Applications').unlink()
            (payload / 'Applications').symlink_to('/tmp')
            with self.assertRaisesRegex(ValueError, 'Applications shortcut'):
                packaging.check_payload(payload, files, 'steps', disk_image=True)

    def test_failed_attach_still_attempts_detach(self):
        calls = []
        def command(args):
            calls.append(args)
            if 'attach' in args:
                raise RuntimeError('attach failed')
            return b''
        with patch.object(packaging, 'run', side_effect=command):
            with self.assertRaisesRegex(RuntimeError, 'attach failed'):
                packaging.check_disk_image(self.root / 'fixture.dmg', self.root, {}, '')
        self.assertEqual([args[1] for args in calls], ['attach', 'detach'])
        self.assertFalse(list(self.root.glob('.dervo-mount-*')))

    def test_failed_detach_keeps_mount_directory_outside_staging(self):
        def command(args):
            if 'detach' in args:
                raise RuntimeError('detach failed')
            return b''
        with patch.object(packaging, 'run', side_effect=command), patch.object(packaging, 'check_payload'):
            with self.assertRaisesRegex(RuntimeError, 'mount folder was kept'):
                packaging.check_disk_image(self.root / 'fixture.dmg', self.root, {}, '')
        self.assertEqual(len(list(self.root.glob('.dervo-mount-*'))), 1)


if __name__ == '__main__':
    unittest.main()
