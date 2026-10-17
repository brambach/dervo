"""The Linux release build (scripts/linux-release.py): windows-release.py's stage and restore with the Linux config,
switch and id ending, and one signer-ready package folder per format. Pure file work, so it runs anywhere."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from beta_update_host import running_version

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'linux-release.py'
spec = importlib.util.spec_from_file_location('linux_release', SCRIPT)
linux_release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(linux_release)
release = linux_release.release

ACCOUNT = Path(__file__).resolve().parents[1] / 'runtime' / 'dervo-account-public.json'
CLOCK = lambda: 1790400000123456789  # noqa: E731


class LinuxReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / 'source'
        self.held = self.root / 'linux-held'
        for name, text in [('backend/host.py', 'print(1)\n'), ('backend/test_host.py', 'pass\n'),
                           ('linux-python/bin/python3.13', '\x7fELF'), ('linux-python/lib/python3.13/os.py', 'pass\n'),
                           ('LICENSE.md', 'licence\n')]:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)
        (self.root / 'runtime').mkdir()
        shutil.copyfile(ACCOUNT, self.root / 'runtime' / ACCOUNT.name)
        (self.root / 'tauri.conf.json').write_text(json.dumps({'version': '0.1.0'}))
        (self.root / 'tauri.linux.conf.json').write_text(json.dumps({'bundle': {'resources': [
            'backend/*.py', 'runtime/*', 'linux-python/**/*', 'LICENSE.md']}}))
        (self.root / '.github').mkdir()

    def switch(self, value):
        (self.root / '.github/linux-release.json').write_text(json.dumps({'releaseId': value}))

    def test_the_linux_switch_config_and_id_ending_are_used(self):
        self.assertEqual((str(release.SWITCH), release.CONFIG, release.PLATFORM),
                         ('.github/linux-release.json', 'tauri.linux.conf.json', 'linux-x64'))
        for bad in ('0.1.0-beta.17-windows', '0.1.0-beta.17'):
            self.switch(bad)
            with self.assertRaisesRegex(ValueError, 'ends in -linux'):
                linux_release.stage(self.root, self.held, 'deb', CLOCK)
            self.assertFalse(self.held.exists())
        self.switch('0.1.0-beta.17-linux')
        self.assertIsNone(linux_release.stage(self.root, self.held, None, CLOCK)['releaseId'], 'no format: the test build')
        release.restore(self.root, self.held)
        shutil.rmtree(self.held)
        with self.assertRaisesRegex(ValueError, 'format is one of'):
            linux_release.stage(self.root, self.held, 'snap', CLOCK)

    def test_each_format_is_its_own_stamped_release_packaged_for_the_signer(self):
        self.switch('0.1.0-beta.17-linux')
        bundle = self.root / 'bundle'
        for fmt, folder, name, artifact in (('deb', 'deb', 'Dervo Local_0.1.0_amd64.deb', 'Dervo-linux-x64.deb'),
                                            ('rpm', 'rpm', 'Dervo Local-0.1.0-1.x86_64.rpm', 'Dervo-linux-x64.rpm'),
                                            ('appimage', 'appimage', 'Dervo Local_0.1.0_amd64.AppImage', 'Dervo-linux-x64.AppImage')):
            held = self.root / ('held-' + fmt)
            record = linux_release.stage(self.root, held, fmt, CLOCK)
            native = {'productVersion': '0.1.0', 'buildNumber': '1790400000123456', 'releaseId': '0.1.0-beta.17-linux-' + fmt}
            self.assertEqual(running_version(self.root), native, 'the app knows which package it came from')
            self.assertEqual((record['format'], record['excludedTests']), (fmt, ['backend/test_host.py']))
            (bundle / folder).mkdir(parents=True)
            (bundle / folder / name).write_bytes(fmt.encode())
            result = linux_release.package(self.root, held, bundle, self.root / 'out')
            saved = json.loads((self.root / 'out' / fmt / 'package.json').read_text())
            self.assertEqual(saved, result)
            # The fields tools/release-sign reads, which must agree with the stamped id.
            self.assertEqual((saved['releaseId'], saved['artifact'], saved['format'], saved['platform']),
                             ('0.1.0-beta.17-linux-' + fmt, artifact, fmt, 'linux-x64'))
            self.assertEqual(saved['bundleAudit']['nativeVersion'], native)
            self.assertEqual(saved['sha256'], hashlib.sha256(fmt.encode()).hexdigest())
            release.restore(self.root, held)
            self.assertTrue((self.root / 'backend/test_host.py').exists())
            self.assertFalse((self.root / release.MARKER).exists())
        self.assertTrue((self.root / 'out/appimage/Dervo-linux-x64.AppImage').stat().st_mode & 0o100)

    def test_a_test_build_isnt_packaged_and_a_missing_file_stops_it(self):
        self.switch(None)
        self.assertIsNone(linux_release.stage(self.root, self.held, None, CLOCK)['releaseId'])
        with self.assertRaisesRegex(ValueError, 'test build'):
            linux_release.package(self.root, self.held, self.root, self.root / 'out')
        release.restore(self.root, self.held)
        shutil.rmtree(self.held)
        self.switch('0.1.0-beta.17-linux')
        linux_release.stage(self.root, self.held, 'rpm', CLOCK)
        (self.root / 'bundle/rpm').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'Expected one'):
            linux_release.package(self.root, self.held, self.root / 'bundle', self.root / 'out')
        self.assertFalse((self.root / 'out').exists())


if __name__ == '__main__':
    unittest.main()
