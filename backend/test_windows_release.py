"""The Windows release build (scripts/windows-release.py): a test build only keeps the tests out of the installer, a
release build is stamped the way Updates reads it, audited, and packaged the way the release signer reads it. Pure file
work, so it runs the same on a Mac."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from beta_update_host import running_version

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'windows-release.py'
spec = importlib.util.spec_from_file_location('windows_release', SCRIPT)
windows_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows_release)

ACCOUNT = Path(__file__).resolve().parents[1] / 'runtime' / 'dervo-account-public.json'
CLOCK = lambda: 1790400000123456789  # noqa: E731


class WindowsReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'source'
        self.held = self.root / 'windows-held'
        for name, text in [('backend/host.py', 'print(1)\n'), ('backend/test_host.py', 'pass\n'),
                           ('backend/test_other.py', 'pass\n'), ('backend/dervo_harness/worker.py', 'pass\n'),
                           ('windows-python/python.exe', 'MZ'), ('windows-python/Lib/os.py', 'pass\n'),
                           ('LICENSE.md', 'licence\n'), ('frontend/src/secret.env', 'not packaged\n')]:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        (self.root / 'runtime').mkdir()
        shutil.copyfile(ACCOUNT, self.root / 'runtime' / ACCOUNT.name)
        (self.root / 'tauri.conf.json').write_text(json.dumps({'version': '0.1.0'}))
        (self.root / 'tauri.windows.conf.json').write_text(json.dumps({'bundle': {'resources': [
            'backend/*.py', 'backend/dervo_harness/*', 'runtime/*', 'windows-python/**/*', 'LICENSE.md']}}))
        (self.root / '.github').mkdir()

    def switch(self, value):
        (self.root / windows_release.SWITCH).write_text(json.dumps({'releaseId': value}))

    def test_test_build_keeps_tests_out_and_puts_them_back(self):
        self.switch(None)
        record = windows_release.stage(self.root, self.held, CLOCK)
        self.assertEqual(record, {'releaseId': None, 'excludedTests': ['backend/test_host.py', 'backend/test_other.py']})
        self.assertEqual(sorted(p.name for p in (self.root / 'backend').glob('*.py')), ['host.py'])
        self.assertFalse((self.root / windows_release.MARKER).exists())
        with self.assertRaisesRegex(ValueError, 'test build'):
            windows_release.package(self.root, self.held, self.root, self.root / 'out')
        windows_release.restore(self.root, self.held)
        self.assertEqual(sorted(p.name for p in (self.root / 'backend').glob('*.py')),
                         ['host.py', 'test_host.py', 'test_other.py'])

    def test_no_switch_file_is_a_test_build(self):
        self.assertIsNone(windows_release.stage(self.root, self.held, CLOCK)['releaseId'])

    def test_release_is_stamped_the_way_updates_read_it_and_packaged_for_the_signer(self):
        self.switch('0.1.0-beta.17-windows')
        record = windows_release.stage(self.root, self.held, CLOCK)
        native = {'productVersion': '0.1.0', 'buildNumber': '1790400000123456', 'releaseId': '0.1.0-beta.17-windows'}
        self.assertEqual(record['nativeVersion'], native)
        self.assertEqual(running_version(self.root), native)
        self.assertEqual(record['audit']['credentialPatternCheck'], 'passed')
        # frontend/ isn't a resource, so its file isn't audited or packaged. The account file, marker and profile are.
        self.assertEqual(record['audit']['filesChecked'], 8)

        nsis = self.root / 'nsis'
        nsis.mkdir()
        (nsis / 'Dervo Local_0.1.0_x64-setup.exe').write_bytes(b'installer bytes')
        package = windows_release.package(self.root, self.held, nsis, self.root / 'out')
        installer = self.root / 'out' / 'Dervo-windows-x64.exe'
        self.assertEqual(package['sha256'], hashlib.sha256(b'installer bytes').hexdigest())
        self.assertEqual(package['bytes'], 15)
        self.assertEqual(installer.read_bytes(), b'installer bytes')
        saved = json.loads((self.root / 'out' / 'package.json').read_text())
        # The fields tools/release-sign reads (package_release in its main.rs).
        self.assertEqual((saved['releaseId'], saved['artifact'], saved['bundleAudit']['nativeVersion']),
                         ('0.1.0-beta.17-windows', 'Dervo-windows-x64.exe', native))
        self.assertEqual(saved['signing']['mode'], 'unsigned')

        windows_release.restore(self.root, self.held)
        self.assertFalse((self.root / windows_release.MARKER).exists())
        self.assertFalse((self.root / 'runtime/dervo-release-profile.json').exists())
        self.assertTrue((self.root / 'backend/test_host.py').exists())

    def test_release_refuses_an_installer_carrying_secrets_or_local_data(self):
        self.switch('0.1.0-beta.17-windows')
        (self.root / 'windows-python/Lib/.env').write_text('KEY=1\n')
        with self.assertRaisesRegex(ValueError, 'Local account or workspace data'):
            windows_release.stage(self.root, self.held, CLOCK)

    def test_bad_release_id_stops_before_anything_moves(self):
        for bad in ('../beta', '', 'beta 17', 17, '0.1.0-beta.17'):
            self.switch(bad)
            with self.assertRaises((ValueError, TypeError)):
                windows_release.stage(self.root, self.held, CLOCK)
            self.assertFalse(self.held.exists())
            self.assertTrue((self.root / 'backend/test_host.py').exists())

    def test_switch_file_holds_only_the_release_id(self):
        (self.root / windows_release.SWITCH).write_text(json.dumps({'releaseId': None, 'sign': True}))
        with self.assertRaisesRegex(ValueError, 'only'):
            windows_release.stage(self.root, self.held, CLOCK)

    def test_package_wants_exactly_one_installer(self):
        self.switch('0.1.0-beta.17-windows')
        windows_release.stage(self.root, self.held, CLOCK)
        nsis = self.root / 'nsis'
        nsis.mkdir()
        with self.assertRaisesRegex(ValueError, 'found 0'):
            windows_release.package(self.root, self.held, nsis, self.root / 'out')


if __name__ == '__main__':
    unittest.main()
