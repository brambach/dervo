"""Update and restart on Linux (linux_update, linux_install), run for real against fake AppImages: a fake Dervo that
confirms (or doesn't) through DERVO_HANDOFF_FILE, and the last step on the switch folder's copy of "Python", a shell
script that runs this Python. A .deb or .rpm goes to a fake software installer. No real Dervo or package manager."""
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

from dervo_connections import Rejected
import beta_update_host
import linux_update
import platform_os
import release_history

HERE = Path(__file__).resolve().parent
RELEASE = '0.1.0-beta.19-linux-appimage'
BUILD = 1790500000000000


def appimage(version, confirm=True):
    """A fake Dervo AppImage. With DERVO_HANDOFF_FILE it confirms like src/version_switch.rs unless told not to; either
    way it notes which version opened and how, beside itself."""
    answer = '''printf '{"detail":"","ready":true}\\n' > "$DERVO_HANDOFF_FILE"''' if confirm else 'true'
    return f'''#!/bin/sh
log="$(dirname "$(readlink -f "$0")")/opened.log"
if [ -n "$DERVO_HANDOFF_FILE" ]; then echo "{version} handoff" >> "$log"; {answer}; else echo "{version} plain" >> "$log"; fi
'''


class Finished:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout):
        return self.code


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux updates: AppImages, packages and Linux file modes')
class AppImageUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.apps = self.root / 'Applications'
        self.apps.mkdir()
        self.current = self.apps / 'Dervo-linux-x64.AppImage'
        self.current.write_text(appimage('old')); self.current.chmod(0o755)
        # The old AppImage's resources: its Python and the last step, which the helper copies out of the mount.
        self.resources = self.root / 'mount' / 'usr' / 'lib' / 'Dervo Local'
        (self.resources / 'linux-python' / 'bin').mkdir(parents=True)
        python = self.resources / 'linux-python' / 'bin' / 'python3.13'
        python.write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n'); python.chmod(0o755)
        (self.resources / 'backend').mkdir()
        shutil.copyfile(HERE / 'linux_install.py', self.resources / 'backend' / 'linux_install.py')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        (self.workspace / 'conversations.sqlite3').write_bytes(b'saved work')
        self.updates = self.root / 'AppData' / 'Dervo' / 'Updates' / RELEASE
        self.updates.mkdir(parents=True)
        support = patch.object(platform_os, 'app_support_dir', return_value=self.root / 'AppData')
        support.start(); self.addCleanup(support.stop)

    def download(self, script):
        path = self.updates / 'Dervo-linux-x64.AppImage'
        path.write_text(script); path.chmod(0o755)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def closed_pid(self):
        process = subprocess.Popen(['/bin/true'])
        process.wait()
        return process.pid

    def plan(self, path, sha256, channel='appimage', current=None):
        return {'format': 1, 'channel': channel, 'file': str(path), 'sha256': sha256, 'releaseId': RELEASE,
                'build': BUILD, 'current': str(current or self.current), 'currentRelease': '0.1.0-beta.18-linux-appimage',
                'workspace': str(self.workspace), 'dataOverride': False, 'parentPid': self.closed_pid()}

    def stream(self, *lines):
        read, write = os.pipe()
        os.write(write, ''.join(json.dumps(line) + '\n' for line in lines).encode())
        os.close(write)
        stream = os.fdopen(read)
        self.addCleanup(stream.close)
        return stream

    def update(self, script):
        path, sha256 = self.download(script)
        reports, started = [], []
        record = linux_update.run(self.plan(path, sha256), self.stream({'action': 'backup'}, {'action': 'launch'}),
                                  report=reports.append, helper_pid=self.closed_pid(), resources=self.resources,
                                  start=lambda *args, **options: started.append(subprocess.Popen(*args, **options)))
        self.assertEqual([r['state'] for r in reports], ['prepared', 'backed_up'])
        self.assertEqual(record['state'], 'handing_over')
        self.assertEqual(started[0].wait(timeout=120), 0)
        folder = Path(record['directory'])
        return folder, json.loads((folder / 'switch.json').read_text())

    def opened(self, count):
        log = self.apps / 'opened.log'
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            lines = log.read_text().split('\n')[:-1] if log.exists() else []
            if len(lines) >= count:
                return lines
            time.sleep(.05)
        return lines

    def test_an_update_swaps_the_appimage_opens_it_and_keeps_the_old_one(self):
        folder, record = self.update(appimage('new'))
        self.assertEqual(record['state'], 'opened')
        self.assertEqual(self.opened(1), ['new handoff'])
        self.assertIn('new handoff', self.current.read_text())
        self.assertEqual(self.current.stat().st_mode & 0o777, 0o755)
        self.assertIn('old plain', (folder / 'previous.AppImage').read_text())
        self.assertEqual((folder / 'backup' / 'workspace' / 'conversations.sqlite3').read_bytes(), b'saved work')
        self.assertFalse(self.updates.exists())
        self.assertFalse((folder / 'python').exists())
        self.assertEqual(sorted(p.name for p in self.apps.iterdir()), ['Dervo-linux-x64.AppImage', 'opened.log'])

    def test_a_new_version_that_never_confirms_is_put_back(self):
        with patch.dict(os.environ, {}):
            _, record = self.update(appimage('new', confirm=False))
        self.assertEqual(record['state'], 'restored_previous')
        self.assertIn('old plain', self.current.read_text())
        self.assertEqual(self.opened(2), ['new handoff', 'old plain'])
        self.assertTrue(self.updates.exists())

    def test_a_download_changed_after_the_check_is_never_installed(self):
        path, sha256 = self.download(appimage('new'))
        path.write_text(appimage('evil'))
        with self.assertRaisesRegex(RuntimeError, 'changed'):
            linux_update.run(self.plan(path, sha256), self.stream(), report=lambda _: None, resources=self.resources)
        self.assertIn('old', self.current.read_text())

    def test_a_cancelled_update_changes_nothing(self):
        path, sha256 = self.download(appimage('new'))
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            linux_update.run(self.plan(path, sha256), self.stream({'action': 'cancel'}), report=lambda _: None,
                             resources=self.resources)
        self.assertIn('old plain', self.current.read_text())
        self.assertFalse((self.apps / 'opened.log').exists())

    def test_a_bad_plan_is_refused(self):
        path, sha256 = self.download(appimage('new'))
        for change in [{'channel': 'installer'}, {'build': str(BUILD)}, {'parentPid': 1}, {'extra': True}]:
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'invalid'):
                linux_update.run({**self.plan(path, sha256), **change}, self.stream(), report=lambda _: None)

    def test_an_appimage_that_cant_be_replaced_where_it_is_says_why(self):
        link = self.apps / 'Dervo.AppImage'
        link.symlink_to(self.current)
        self.assertIn('link', linux_update.appimage_problem(link))
        self.apps.chmod(0o555)
        self.addCleanup(self.apps.chmod, 0o755)
        if os.geteuid() != 0:  # root writes anywhere
            self.assertIn('can’t replace files', linux_update.appimage_problem(self.current))
        self.apps.chmod(0o777)
        self.assertIn('Other people on this computer', linux_update.appimage_problem(self.current))
        self.apps.chmod(0o1777)  # sticky, like /tmp: others can still leave a file where the new one goes
        self.assertIn('Other people on this computer', linux_update.appimage_problem(self.current))
        self.apps.chmod(0o775)  # the person's own group
        self.assertIsNone(linux_update.appimage_problem(self.current))
        with patch.object(os, 'getgid', return_value=self.apps.stat().st_gid + 1):
            self.assertIn('Other people on this computer', linux_update.appimage_problem(self.current))
        self.apps.chmod(0o755)
        self.assertIsNone(linux_update.appimage_problem(self.current))

    def test_a_package_opens_in_the_software_installer_once_dervo_closes(self):
        package = self.updates / 'Dervo-linux-x64.deb'
        package.write_bytes(b'deb')
        sha256 = hashlib.sha256(b'deb').hexdigest()
        opened = []
        def opener(argv, **options):
            opened.append(argv)
            self.assertEqual(options['start_new_session'], True)
            return Finished(0)
        record = linux_update.run(self.plan(package, sha256, 'package', current='/usr/bin/dervo-local'),
                                  self.stream({'action': 'backup'}, {'action': 'launch'}), report=lambda _: None,
                                  opener=opener, resources=self.resources)
        self.assertEqual(record['state'], 'opened_installer')
        self.assertEqual(opened, [platform_os.open_command(package)])
        self.assertFalse((Path(record['directory']) / 'python').exists())

    def test_a_package_no_installer_takes_is_shown_with_the_command(self):
        package = self.updates / 'Dervo-linux-x64.rpm'
        package.write_bytes(b'rpm')
        opened = []
        def opener(argv, **options):
            opened.append(argv)
            return Finished(4 if len(opened) == 1 else 0)
        record = linux_update.run(self.plan(package, hashlib.sha256(b'rpm').hexdigest(), 'package',
                                            current='/usr/bin/dervo-local'),
                                  self.stream({'action': 'backup'}, {'action': 'launch'}), report=lambda _: None,
                                  opener=opener, resources=self.resources)
        self.assertEqual(record['state'], 'shown_package')
        self.assertEqual(opened[1], platform_os.open_folder_command(self.updates))
        self.assertIn(f'sudo dnf install {package}', (self.updates / 'How to install.txt').read_text())

    def test_an_installer_still_open_counts_as_handed_over_without_waiting_on_it(self):
        class Open:
            def wait(self, timeout):
                raise subprocess.TimeoutExpired('xdg-open', timeout)
        began = time.monotonic()
        self.assertTrue(linux_update.hand_over(lambda argv, **options: Open(), ['xdg-open', 'x'], {}, None, seconds=.1))
        self.assertLess(time.monotonic() - began, 1)

    def test_the_install_hint_quotes_a_path_with_a_quote_in_it(self):
        hint = linux_update.install_hint(Path("/home/a/it's here/Dervo.deb"))
        self.assertIn("sudo apt install '/home/a/it'\"'\"'s here/Dervo.deb'", hint)

    def test_the_switch_helper_hands_linux_plans_here(self):
        import release_switch
        for channel in ('appimage', 'package'):
            with patch.object(linux_update, 'run', return_value={'state': 'x'}) as run:
                release_switch.run({'channel': channel}, 'stream')
            run.assert_called_once_with({'channel': channel}, 'stream')

    def test_a_linux_update_is_not_offered_as_a_mac_rollback(self):
        folder = self.root / 'AppData' / 'history'
        record = {'format': 1, 'state': 'opened', 'channel': 'appimage', 'target': {'id': RELEASE}, 'current': {'id': 'old'}}
        with patch.object(release_history, 'history_root', return_value=folder):
            (folder / ('a' * 32) / 'backup').mkdir(parents=True)
            (folder / ('a' * 32) / 'backup' / 'backup.json').write_text('{}')
            (folder / ('a' * 32) / 'switch.json').write_text(json.dumps(record))
            with patch.object(release_history, 'switch_record', return_value=(folder / ('a' * 32), record)):
                self.assertIsNone(release_history.rollback_choice(self.workspace, RELEASE))


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux updates: AppImages, packages and Linux file modes')
class SwitchStepTests(unittest.TestCase):
    """linux_install.install's failure paths, with the old Dervo always opened again when it's still in place."""
    def setUp(self):
        import linux_install
        self.install = linux_install
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.folder, self.target, self.new = root / 'switch', root / 'Dervo.AppImage', root / 'updates' / 'new.AppImage'
        self.folder.mkdir()
        self.new.parent.mkdir()
        self.target.write_bytes(b'old')
        self.new.write_bytes(b'new')
        (self.folder / 'switch.json').write_text('{}')
        (self.folder / 'install.json').write_text(json.dumps({
            'file': str(self.new), 'sha256': hashlib.sha256(b'new').hexdigest(), 'target': str(self.target),
            'previous': str(self.folder / 'previous.AppImage'), 'env': {}, 'waitPids': [], 'updates': str(self.new.parent)}))
        self.opened = []

    def popen(self, executable, env, log, started=None):
        self.opened.append((Path(executable).read_bytes(), started is not None))
        return None

    def test_a_swap_that_fails_before_its_rename_reopens_the_untouched_old_one(self):
        real = os.replace

        def replace(source, target):
            if str(source).endswith('.dervo-part'):
                raise OSError(28, 'No space left on device')
            return real(source, target)
        with patch.object(self.install.os, 'replace', side_effect=replace):
            record = self.install.install(self.folder, popen=self.popen)
        self.assertEqual(record['state'], 'failed')
        self.assertIn('still in place', record['error'])
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertEqual(self.opened, [(b'old', False)])
        self.assertEqual([p.name for p in self.target.parent.iterdir() if p.name.endswith('.dervo-part')], [])

    def test_a_download_changed_after_the_hand_over_reopens_the_old_one(self):
        self.new.write_bytes(b'tampered')
        record = self.install.install(self.folder, popen=self.popen)
        self.assertEqual(record['state'], 'failed')
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertEqual(self.opened, [(b'old', False)])

    def test_a_new_version_that_never_confirms_is_stopped_with_everything_it_started(self):
        pidfile = self.folder / 'host.pid'

        def popen(executable, env, log, started=None):
            self.opened.append((Path(executable).read_bytes(), started is not None))
            if started is None:
                return None
            # The new Dervo and the host it starts, which stays in its session; the host ignores SIGTERM.
            return subprocess.Popen(['sh', '-c', f'sh -c "trap \'\' TERM; sleep 60" & echo $! > {pidfile}; sleep 60'],
                                    start_new_session=True)

        def wait(process, started):
            while not pidfile.exists() or not pidfile.read_text().strip():
                time.sleep(.02)
            raise RuntimeError('The new Dervo didn’t confirm it started within a minute.')
        record = self.install.install(self.folder, popen=popen, wait=wait)
        self.assertEqual(record['state'], 'restored_previous')
        self.assertEqual(self.target.read_bytes(), b'old')
        self.assertEqual(self.opened, [(b'new', True), (b'old', False)])
        host = int(pidfile.read_text())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and Path(f'/proc/{host}').exists() and \
                Path(f'/proc/{host}/stat').read_text().split(') ')[1][0] != 'Z':
            time.sleep(.05)
        alive = Path(f'/proc/{host}').exists() and Path(f'/proc/{host}/stat').read_text().split(') ')[1][0] != 'Z'
        self.assertFalse(alive, 'the new version’s host was left running')

    def test_a_kept_copy_that_changed_is_not_put_back(self):
        def wait(process, started):
            (self.folder / 'previous.AppImage').write_bytes(b'damaged')
            raise RuntimeError('The new Dervo stopped while it was starting.')
        record = self.install.install(self.folder, popen=self.popen, wait=wait)
        self.assertEqual(record['state'], 'restore_failed')
        self.assertEqual(self.target.read_bytes(), b'new')
        self.assertEqual(self.opened, [(b'new', True), (b'new', False)])


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux updates: AppImages, packages and Linux file modes')
class StageTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.file = Path(temp.name).resolve() / 'Dervo-linux-x64.AppImage'
        self.file.write_bytes(b'appimage')
        self.file.chmod(0o600)
        self.row = {'platform': 'linux-x64', 'format': 'appimage', 'release_id': RELEASE, 'build_number': BUILD,
                    'sha256': hashlib.sha256(b'appimage').hexdigest()}

    def test_a_checked_newer_appimage_is_staged_executable(self):
        staged = linux_update.stage(self.file, self.row, BUILD - 1, 'appimage')
        self.assertEqual(staged, {'file': str(self.file), 'releaseId': RELEASE, 'build': BUILD,
                                  'sha256': self.row['sha256'], 'format': 'appimage'})
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o755)
        self.assertEqual(json.loads((self.file.parent / 'release.json').read_text())['state'], 'staged')

    def test_anything_else_is_refused(self):
        for change, fmt in [({'format': 'deb'}, 'appimage'), ({'platform': 'windows-x64'}, 'appimage'),
                            ({'release_id': '0.1.0-beta.19-linux-deb'}, 'appimage'), ({'build_number': BUILD}, 'deb'),
                            ({'sha256': '0' * 64}, 'appimage'), ({'build_number': str(BUILD)}, 'appimage')]:
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                linux_update.stage(self.file, {**self.row, **change}, BUILD - 1, fmt)
        with self.assertRaises(RuntimeError):
            linux_update.stage(self.file, self.row, BUILD, 'appimage')

    def test_a_package_is_staged_readable_and_marked_for_the_software_installer(self):
        row = {**self.row, 'format': 'deb', 'release_id': '0.1.0-beta.19-linux-deb'}
        self.assertEqual(linux_update.stage(self.file, row, BUILD - 1, 'deb')['format'], 'deb')
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o644)
        host = beta_update_host.Updates.__new__(beta_update_host.Updates)
        host._ready({'releaseId': row['release_id'], 'source': 'package'})
        self.assertIn('software installer', host.detail)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux updates: AppImages, packages and Linux file modes')
class PrepareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.exe = self.root / 'Dervo.AppImage'
        self.exe.write_text('x')
        self.host = type('Host', (), {'root': self.root / 'workspace'})()
        patches = [patch('release_switch.require_idle'), patch.dict(os.environ, {'DERVO_APP_EXECUTABLE': str(self.exe)}),
                   patch('beta_update_host.running_version', return_value={'releaseId': 'old'})]
        for p in patches:
            p.start(); self.addCleanup(p.stop)
        self.staged = {'file': str(self.root / 'new.AppImage'), 'releaseId': RELEASE, 'build': BUILD, 'sha256': 'a' * 64,
                       'format': 'appimage'}

    def args(self, exe=None):
        return {'currentExecutable': str(exe or self.exe), 'dataOverride': False, 'parentPid': 4242}

    def test_the_plan_names_the_appimage_the_person_opened(self):
        with patch.object(platform_os, 'LINUX', True), patch.object(platform_os, 'WINDOWS', False):
            plan = linux_update.prepare(self.host, self.args(), self.staged, self.root)
        self.assertEqual((plan['channel'], plan['current'], plan['file']), ('appimage', str(self.exe), self.staged['file']))
        with patch.object(platform_os, 'LINUX', True), patch.object(platform_os, 'WINDOWS', False):
            plan = linux_update.prepare(self.host, self.args(), {**self.staged, 'format': 'rpm'}, self.root)
        self.assertEqual(plan['channel'], 'package')

    def test_another_executable_or_no_download_is_refused(self):
        with patch.object(platform_os, 'LINUX', True), patch.object(platform_os, 'WINDOWS', False):
            with self.assertRaises(Rejected):
                linux_update.prepare(self.host, self.args(self.root / 'other'), self.staged, self.root)
            with self.assertRaises(Rejected):
                linux_update.prepare(self.host, self.args(), None, self.root)
            with self.assertRaises(Rejected):
                linux_update.prepare(self.host, {**self.args(), 'parentPid': 1}, self.staged, self.root)


if __name__ == '__main__':
    unittest.main()
