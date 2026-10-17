"""Update and restart on Windows (windows_update, windows_install), run for real on a Mac against a fake install
folder: a fake installer, a fake Dervo that confirms (or doesn't) through DERVO_HANDOFF_FILE, and the install step on
the kept copy's own "Python", which is a shell script that runs this Mac's Python 3.13. No real Dervo or installer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import platform_os
import release_history
import windows_update

HERE = Path(__file__).resolve().parent
RELEASE = '0.1.0-beta.17-windows'
BUILD = 1790500000000000
# The fake Dervo: with DERVO_HANDOFF_FILE it confirms like src/version_switch.rs (unless the version it runs says not
# to), and either way it notes which version opened and how.
APP = r'''#!/bin/sh
here=$(cd "$(dirname "$0")" && pwd)
version=$(cat "$here/runtime/version.txt")
if [ -n "$DERVO_HANDOFF_FILE" ]; then
  echo "$version handoff" >> "$here/../opened.log"
  if [ ! -f "$here/runtime/no-confirm" ]; then printf '{"detail":"","ready":true}\n' > "$DERVO_HANDOFF_FILE"; fi
else
  echo "$version plain" >> "$here/../opened.log"
fi
'''


def installer(marker_release=RELEASE, marker_build=BUILD, code=0, confirm=True):
    """A fake NSIS installer: reads /D=, writes the new version into that folder, exits with ``code``."""
    lines = ['#!/bin/sh', 'for arg in "$@"; do case "$arg" in /D=*) dir="${arg#/D=}";; esac; done',
             'echo "installed with $*" >> "$dir/../installer.log"']
    if code == 0:
        marker = json.dumps({'releaseId': marker_release, 'nativeVersion': {'buildNumber': str(marker_build)}})
        lines += [f"printf '%s' '{marker}' > \"$dir/runtime/dervo-preview.json\"",
                  'echo new > "$dir/runtime/version.txt"']
        if not confirm:
            lines.append('touch "$dir/runtime/no-confirm"')
    lines.append(f'exit {code}')
    return '\n'.join(lines) + '\n'


@unittest.skipIf(os.name == 'nt', 'the fake installer, Dervo and Python are shell scripts')
class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.install = self.root / 'Programs' / 'Dervo Local'
        self.exe = self.install / 'Dervo Local.exe'
        (self.install / 'runtime').mkdir(parents=True)
        (self.install / 'backend').mkdir()
        (self.install / 'windows-python').mkdir()
        self.exe.write_text(APP); self.exe.chmod(0o755)
        (self.install / 'runtime' / 'version.txt').write_text('old\n')
        (self.install / 'runtime' / 'dervo-preview.json').write_text(json.dumps(
            {'releaseId': '0.1.0-beta.16-windows', 'nativeVersion': {'buildNumber': '1790400000000000'}}))
        shutil.copyfile(HERE / 'windows_install.py', self.install / 'backend' / 'windows_install.py')
        python = self.install / 'windows-python' / 'python.exe'
        python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n'); python.chmod(0o755)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'conversations.sqlite3').write_bytes(b'saved work')
        self.updates = self.root / 'Updates' / RELEASE
        self.updates.mkdir(parents=True)
        support = patch.object(platform_os, 'app_support_dir', return_value=self.root / 'AppData')
        support.start(); self.addCleanup(support.stop)

    def fake_installer(self, script):
        path = self.updates / f'Dervo-{RELEASE}.exe'
        path.write_text(script); path.chmod(0o755)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def closed_pid(self):
        process = subprocess.Popen(['/usr/bin/true'])
        process.wait()
        return process.pid

    def plan(self, path, sha256):
        return {'format': 1, 'channel': 'installer', 'installer': str(path), 'sha256': sha256, 'releaseId': RELEASE,
                'build': BUILD, 'current': str(self.exe), 'currentRelease': '0.1.0-beta.16-windows',
                'workspace': str(self.workspace), 'dataOverride': False, 'parentPid': self.closed_pid()}

    def stream(self, *lines):
        read, write = os.pipe()
        os.write(write, ''.join(json.dumps(line) + '\n' for line in lines).encode())
        os.close(write)
        stream = os.fdopen(read)
        self.addCleanup(stream.close)
        return stream

    def update(self, script):
        path, sha256 = self.fake_installer(script)
        reports, started = [], []
        # The install step waits for the helper to exit. Here the helper is this test, so it names one that has.
        record = windows_update.run(self.plan(path, sha256), self.stream({'action': 'backup'}, {'action': 'launch'}),
                                    report=reports.append, helper_pid=self.closed_pid(),
                                    start=lambda *args, **options: started.append(subprocess.Popen(*args, **options)))
        self.assertEqual(reports[0], {'state': 'prepared'})
        self.assertEqual(reports[1]['state'], 'backed_up')
        self.assertEqual(record['state'], 'handing_over')
        self.assertEqual(started[0].wait(timeout=90), 0)
        folder = Path(record['directory'])
        return folder, json.loads((folder / 'switch.json').read_text())

    def opened(self, count):
        """The fake Dervo's log once it has ``count`` lines. It's opened without waiting, so it may still be writing."""
        log = self.install.parent / 'opened.log'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            lines = log.read_text().split('\n')[:-1] if log.exists() else []
            if len(lines) >= count:
                return lines
            time.sleep(.05)
        return lines

    def test_an_update_installs_opens_and_is_confirmed(self):
        folder, record = self.update(installer())
        self.assertEqual(record['state'], 'opened')
        self.assertEqual(self.opened(1), ['new handoff'])
        self.assertIn(f'/S /D={self.install}', (self.install.parent / 'installer.log').read_text())
        # Kept to go back to, with the workspace as it was; the downloaded installer is tidied away.
        self.assertEqual((folder / 'previous' / 'runtime' / 'version.txt').read_text(), 'old\n')
        self.assertEqual((folder / 'backup' / 'workspace' / 'conversations.sqlite3').read_bytes(), b'saved work')
        self.assertFalse(self.updates.exists())
        self.assertTrue((folder / 'Open previous Dervo.cmd').is_file())

    def test_a_failed_installer_puts_the_previous_version_back_and_opens_it(self):
        _, record = self.update(installer(code=3))
        self.assertEqual(record['state'], 'restored_previous')
        self.assertIn('code 3', record['error'])
        self.assertEqual((self.install / 'runtime' / 'version.txt').read_text(), 'old\n')
        self.assertEqual(self.opened(1), ['old plain'])
        self.assertTrue(self.updates.exists())

    def test_an_install_of_the_wrong_release_is_put_back(self):
        _, record = self.update(installer(marker_release='0.1.0-beta.99-windows'))
        self.assertEqual(record['state'], 'restored_previous')
        self.assertEqual((self.install / 'runtime' / 'version.txt').read_text(), 'old\n')
        self.assertEqual(self.opened(1), ['old plain'])

    def test_a_new_version_that_never_confirms_is_put_back(self):
        _, record = self.update(installer(confirm=False))
        self.assertEqual(record['state'], 'restored_previous')
        self.assertEqual(self.opened(2), ['new handoff', 'old plain'])
        self.assertFalse((self.install / 'runtime' / 'no-confirm').exists())

    def test_an_installer_changed_after_the_check_is_never_run(self):
        path, sha256 = self.fake_installer(installer())
        path.write_text(installer() + '# changed\n')
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            windows_update.run(self.plan(path, sha256), self.stream(), report=lambda _: None)
        self.assertFalse((self.install.parent / 'installer.log').exists())

    def test_a_cancelled_update_changes_nothing(self):
        path, sha256 = self.fake_installer(installer())
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            windows_update.run(self.plan(path, sha256), self.stream({'action': 'cancel'}), report=lambda _: None)
        self.assertFalse((self.install.parent / 'installer.log').exists())
        self.assertEqual((self.install / 'runtime' / 'version.txt').read_text(), 'old\n')

    def test_a_bad_plan_is_refused(self):
        path, sha256 = self.fake_installer(installer())
        for change in [{'channel': 'download'}, {'build': str(BUILD)}, {'parentPid': 1}, {'extra': True}]:
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'invalid'):
                windows_update.run({**self.plan(path, sha256), **change}, self.stream(), report=lambda _: None)

    def test_the_switch_helper_hands_an_installer_plan_here(self):
        import release_switch
        with patch.object(windows_update, 'run', return_value={'state': 'handing_over'}) as run:
            self.assertEqual(release_switch.run({'channel': 'installer'}, 'stream'), {'state': 'handing_over'})
        run.assert_called_once_with({'channel': 'installer'}, 'stream')

    def test_a_windows_update_is_not_offered_as_a_mac_rollback(self):
        folder = self.root / 'AppData' / 'history'
        record = {'format': 1, 'state': 'opened', 'channel': 'installer', 'target': {'id': RELEASE},
                  'current': {'id': 'old'}}
        with patch.object(release_history, 'history_root', return_value=folder):
            (folder / ('a' * 32) / 'backup').mkdir(parents=True)
            (folder / ('a' * 32) / 'backup' / 'backup.json').write_text('{}')
            (folder / ('a' * 32) / 'switch.json').write_text(json.dumps(record))
            with patch.object(release_history, 'switch_record', return_value=(folder / ('a' * 32), record)):
                self.assertIsNone(release_history.rollback_choice(self.workspace, RELEASE))


class StageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()
        self.installer = self.folder / 'Dervo.exe'
        self.installer.write_bytes(b'installer')
        self.row = {'release_id': RELEASE, 'platform': 'windows-x64', 'format': 'exe', 'build_number': BUILD,
                    'sha256': hashlib.sha256(b'installer').hexdigest()}

    def test_a_checked_windows_installer_is_ready(self):
        staged = windows_update.stage(self.installer, self.row, BUILD - 1)
        self.assertEqual(staged, {'installer': str(self.installer), 'releaseId': RELEASE, 'build': BUILD,
                                  'sha256': self.row['sha256']})
        self.assertEqual(json.loads((self.folder / 'release.json').read_text())['state'], 'staged')

    def test_anything_else_is_refused(self):
        for change, running in [({'platform': 'macos-arm64'}, 1), ({'format': 'zip'}, 1), ({'release_id': '0.1.0-beta.17'}, 1),
                                ({}, BUILD), ({'sha256': 'b' * 64}, 1)]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                windows_update.stage(self.installer, {**self.row, **change}, running)


class InstallerCommandTests(unittest.TestCase):
    def test_windows_writes_d_last_and_unquoted(self):
        import windows_install
        with patch.object(windows_install, 'WINDOWS', True):
            self.assertEqual(windows_install.installer_command(r'C:\Users\A B\Dervo.exe', r'C:\Users\A B\Dervo Local'),
                             r'"C:\Users\A B\Dervo.exe" /S /D=C:\Users\A B\Dervo Local')


class AppExecutableTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'a Mac path')
    def test_a_mac_finds_its_bundle_executable(self):
        self.assertEqual(platform_os.app_executable('/Applications/Dervo.app/Contents/Resources'),
                         Path('/Applications/Dervo.app/Contents/MacOS/dervo-local'))

    def test_windows_takes_the_named_exe_only_from_its_own_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            resources = Path(temp).resolve()
            exe = resources / 'Dervo Local.exe'
            exe.write_bytes(b'MZ')
            elsewhere = resources / 'other' / 'Dervo Local.exe'
            elsewhere.parent.mkdir()
            elsewhere.write_bytes(b'MZ')
            with patch.object(platform_os, 'WINDOWS', True):
                with patch.dict(os.environ, {platform_os.APP_EXECUTABLE: str(exe)}):
                    self.assertEqual(platform_os.app_executable(resources), exe)
                for named in [str(elsewhere), 'Dervo Local.exe', str(resources / 'missing.exe'), '']:
                    with self.subTest(named=named), patch.dict(os.environ, {platform_os.APP_EXECUTABLE: named}):
                        with self.assertRaises(RuntimeError):
                            platform_os.app_executable(resources)


if __name__ == '__main__':
    unittest.main()
