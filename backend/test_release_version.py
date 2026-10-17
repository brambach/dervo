"""Native build identity survives staging and rejects stale generated metadata."""
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch

import release_version
from release_storage import BuildStorage


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / 'source'
        self.work.mkdir()
        self.config = self.work / 'tauri.conf.json'
        self.config.write_text(json.dumps({'version': '0.1.0', 'bundle': {'macOS': {'minimumSystemVersion': '14.0'}}}))
        self.plist = self.work / 'Info.plist'
        self.plist.write_bytes(plistlib.dumps({'CFBundleVersion': 'old', 'NSMicrophoneUsageDescription': 'Keep this text'}))

    def test_stage_overrides_stale_version_preserves_unrelated_fields_and_verifies(self):
        expected = release_version.stage(self.work, '0.1.0-prebeta.15', '1789430000000000')
        config = json.loads(self.config.read_text())
        self.assertEqual(config['bundle']['macOS']['bundleVersion'], expected['buildNumber'])
        self.assertEqual(config['bundle']['macOS']['minimumSystemVersion'], '14.0')
        self.assertEqual(plistlib.loads(self.plist.read_bytes())['NSMicrophoneUsageDescription'], 'Keep this text')
        bundle = self.root / 'fixture.app'
        (bundle / 'Contents').mkdir(parents=True)
        target = bundle / 'Contents/Info.plist'
        target.write_bytes(self.plist.read_bytes())
        self.assertEqual(release_version.verify(bundle, expected), expected)
        for key in ('CFBundleVersion', 'CFBundleShortVersionString', 'DervoReleaseID'):
            info = plistlib.loads(self.plist.read_bytes())
            info[key] = 'other'
            target.write_bytes(plistlib.dumps(info))
            with self.assertRaises(ValueError):
                release_version.verify(bundle, expected)

    def test_invalid_identifiers_fail_before_mutating_snapshot(self):
        before = self.config.read_bytes(), self.plist.read_bytes()
        for release, number in [('../escape', '123'), ('fine', '1.2'), ('fine', '-1'), ('fine', '0')]:
            with self.assertRaises(ValueError):
                release_version.stage(self.work, release, number)
            self.assertEqual((self.config.read_bytes(), self.plist.read_bytes()), before)
        self.config.write_text(json.dumps({'version': '0.1.0-prebeta.15'}))
        with self.assertRaisesRegex(ValueError, 'major.minor.patch'):
            release_version.stage(self.work, 'fine', '123')

    def test_counter_survives_clock_rollback_and_failed_builds(self):
        storage = BuildStorage(self.work, self.root / 'cache')
        with self.assertRaises(RuntimeError):
            storage.next_build_number()
        with storage.acquire(), patch('release_storage.time.time_ns', return_value=5000):
            self.assertEqual(storage.next_build_number(), '5')
            self.assertEqual(storage.next_build_number(), '6')
        # A new storage object and a clock that moved backwards cannot reuse 5 or 6.
        restored = BuildStorage(self.work, self.root / 'cache')
        with restored.acquire(), patch('release_storage.time.time_ns', return_value=1000):
            self.assertEqual(restored.next_build_number(), '7')

    def test_malformed_or_linked_counter_isnt_silently_reset(self):
        storage = BuildStorage(self.work, self.root / 'cache')
        with storage.acquire():
            ledger = storage.directory / 'build-number.json'
            ledger.write_text(json.dumps({'schema': 1, 'last': True}))
            with self.assertRaises(RuntimeError):
                storage.next_build_number()
            ledger.unlink()
            ledger.symlink_to(self.config)
            with self.assertRaises(RuntimeError):
                storage.next_build_number()


if __name__ == '__main__':
    unittest.main()
