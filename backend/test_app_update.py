"""One-click updates against disposable release folders, apps and workspaces. No real app is opened or removed."""
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import app_update
from dervo_connections import Rejected
from host import Host
from release_handoff import file_hash
from release_switch import run, verify_bundle


def fake_app(bundle, release, build):
    runtime = bundle / 'Contents/Resources/runtime'
    runtime.mkdir(parents=True)
    exe = bundle / 'Contents/MacOS/dervo-local'
    exe.parent.mkdir()
    exe.write_text(f'#!/bin/sh\n# {release}\nexit 0\n')
    exe.chmod(0o700)
    (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'local.dervo.connected'}))
    version = {'productVersion': '0.1.0', 'buildNumber': str(build), 'releaseId': release}
    (runtime / 'dervo-preview.json').write_text(json.dumps({'releaseId': release, 'workspaceHandoff': 1, 'nativeVersion': version}))
    return bundle


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.releases = self.root / 'Dervo Releases'
        self.host = None

    def close_host(self):
        if self.host:
            self.host.close()
            self.host = None

    def build(self, release, build, state='built'):
        folder = self.releases / release
        bundle = fake_app(folder / 'Dervo Local.app', release, build)
        (folder / 'source.tar.gz').write_bytes(b'keep source')
        (folder / 'feature-checks.log').write_text('keep log')
        (folder / 'release.json').write_text(json.dumps({'id': release, 'state': state, 'bundle': str(bundle),
                                                        'native_version': {'buildNumber': str(build), 'releaseId': release},
                                                        'executable_sha256': file_hash(bundle / 'Contents/MacOS/dervo-local')}))
        return bundle

    def sign(self):
        return self.teams({})

    # --- finding a newer build ---------------------------------------------------------------------------------------
    def test_a_newer_local_build_beside_this_app_is_found_and_nothing_else_counts(self):
        current = self.build('0.1.0-prebeta.42', 420)
        self.build('0.1.0-prebeta.41', 410)
        self.assertIsNone(app_update.local_candidate(current), 'nothing newer yet')
        newest = self.build('0.1.0-prebeta.44', 440)
        self.build('0.1.0-prebeta.43', 430)
        self.build('0.1.0-prebeta.45', 450, state='failed')
        (self.releases / '0.1.0-prebeta.46').mkdir()
        (self.releases / 'linked').symlink_to(self.releases / '0.1.0-prebeta.44', target_is_directory=True)
        self.assertEqual(app_update.local_candidate(current), {'bundle': str(newest), 'releaseId': '0.1.0-prebeta.44', 'build': 440})
        installed = fake_app(self.root / 'Applications/Dervo Local.app', '0.1.0-prebeta.42', 420)
        self.assertIsNone(app_update.local_candidate(installed), 'an installed app has no release folder to look beside')

    def test_older_local_apps_go_but_never_the_newest_two_a_protected_or_busy_app_or_anything_else(self):
        apps = {n: self.build(f'0.1.0-prebeta.{n}', n) for n in (35, 37, 38, 39, 40)}
        (self.releases / '0.1.0-prebeta.12/preview-data').mkdir(parents=True)
        (self.releases / '0.1.0-prebeta.12/preview-data/conversations.sqlite3').write_bytes(b'workspace')
        busy = str(apps[37])
        with patch('app_update.in_use', side_effect=lambda bundle: str(bundle) == busy):
            result = app_update.prune(self.releases, protect=(apps[35],))
        self.assertEqual([found['releaseId'] for found in result['removed']], ['0.1.0-prebeta.38'])
        self.assertEqual({found['releaseId']: found['reason'] for found in result['skipped']}, {'0.1.0-prebeta.37': 'in use', '0.1.0-prebeta.35': 'protected'})
        self.assertFalse(apps[38].exists())
        for n in (35, 37, 39, 40):
            self.assertTrue(apps[n].exists(), n)
        self.assertTrue((self.releases / '0.1.0-prebeta.38/source.tar.gz').exists(), 'only the app bundle is removed')
        self.assertTrue((self.releases / '0.1.0-prebeta.38/feature-checks.log').exists())
        self.assertTrue((self.releases / '0.1.0-prebeta.12/preview-data/conversations.sqlite3').exists())

    def test_in_use_sees_a_running_process_inside_the_app(self):
        bundle = self.build('0.1.0-prebeta.40', 400)
        ps = SimpleNamespace(returncode=0, stdout=f'/bin/zsh\n{bundle}/Contents/MacOS/dervo-local\n')
        with patch('app_update.run_process', return_value=ps):
            self.assertTrue(app_update.in_use(bundle))
        quiet = [SimpleNamespace(returncode=0, stdout='/bin/zsh\n'), SimpleNamespace(returncode=1, stdout='')]
        with patch('app_update.run_process', side_effect=quiet):
            self.assertFalse(app_update.in_use(bundle))

    # --- a downloaded update -----------------------------------------------------------------------------------------
    def zipped(self, release, build, *, extra=False):
        source = self.root / 'package'
        fake_app(source / 'Dervo Local.app', release, build)
        if extra:
            fake_app(source / 'nested/Dervo Local.app', release, build)
        (source / 'Start here.txt').write_text('Read me')
        folder = self.root / 'Updates' / release
        folder.mkdir(parents=True)
        archive = folder / f'Dervo-{release}.zip'
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--sequesterRsrc', str(source), str(archive)], check=True)
        return archive

    def test_a_checked_download_is_unpacked_recorded_like_a_build_and_its_zip_removed(self):
        archive = self.zipped('0.1.0-prebeta.44', 440)
        with self.sign():
            staged = app_update.stage(archive, '0.1.0-prebeta.44', 430, verify=verify_bundle)
        bundle = Path(staged['bundle'])
        self.assertEqual((bundle, staged['build']), (archive.parent / 'Dervo Local.app', 440))
        self.assertFalse(archive.exists())
        self.assertFalse((archive.parent / 'unpacked').exists())
        record = json.loads((archive.parent / 'release.json').read_text())
        self.assertEqual((record['state'], record['bundle'], record['source']), ('built', str(bundle), 'download'))

    def test_a_download_that_isnt_the_newer_published_release_is_refused(self):
        for release, running, extra, message in (('0.1.0-prebeta.44', 440, False, 'isn’t the newer release'),
                                                 ('0.1.0-prebeta.45', 400, True, 'exactly one Dervo app')):
            with self.subTest(message=message):
                archive = self.zipped(release, 440, extra=extra)
                with self.sign(), self.assertRaisesRegex(RuntimeError, message):
                    app_update.stage(archive, release, running, verify=verify_bundle)
                self.assertFalse((archive.parent / 'Dervo Local.app').exists())
                import shutil
                shutil.rmtree(self.root / 'package')

    # --- the plan and the switch -------------------------------------------------------------------------------------
    def start_host(self):
        self.host = Host(self.root / 'data')
        self.addCleanup(self.close_host)

    def test_the_update_plan_comes_only_from_the_native_app_and_needs_a_newer_version(self):
        self.start_host()
        current = self.build('0.1.0-prebeta.42', 420)
        args = {'currentExecutable': str(current / 'Contents/MacOS/dervo-local'), 'dataOverride': True, 'parentPid': 1000000}
        with self.assertRaisesRegex(Rejected, 'no newer Dervo ready'):
            app_update.prepare(self.host, args)
        newer = self.build('0.1.0-prebeta.43', 430)
        plan = app_update.prepare(self.host, args)
        self.assertEqual((plan['target'], plan['current'], plan['channel'], plan['releaseId']), (str(newer), str(current), 'local', '0.1.0-prebeta.43'))
        with self.assertRaises(Rejected):
            app_update.prepare(self.host, {**args, 'target': '/tmp/anything'})
        with self.assertRaises(Rejected):
            app_update.prepare(self.host, {**args, 'currentExecutable': '/bin/zsh'})
        staged = {'bundle': str(self.root / 'Updates/x/Dervo Local.app'), 'releaseId': '0.1.0-prebeta.44', 'build': 440}
        self.assertEqual(app_update.prepare(self.host, args, staged=staged)['channel'], 'download')

    def switch(self, plan, **patches):
        with self.sign(), patch('release_switch.Path.home', return_value=self.root), \
                patch('release_switch.receive', side_effect=[{'action': 'backup'}, {'action': 'launch'}]), patch('release_switch.report'), \
                patch('release_switch.os.kill', side_effect=ProcessLookupError), patch('app_update.in_use', return_value=False), \
                patch('release_switch.open_target', **patches) as spawn:
            return run(plan), spawn

    def test_a_local_update_opens_the_new_build_and_keeps_only_it_and_the_one_before(self):
        self.start_host()
        older = [self.build(f'0.1.0-prebeta.{n}', n) for n in (38, 39)]
        current = self.build('0.1.0-prebeta.42', 420)
        newer = self.build('0.1.0-prebeta.43', 430)
        plan = app_update.prepare(self.host, {'currentExecutable': str(current / 'Contents/MacOS/dervo-local'), 'dataOverride': True, 'parentPid': 1000000})
        self.close_host()
        record, spawn = self.switch(plan, return_value=4321)
        self.assertEqual(record['state'], 'opened')
        self.assertEqual(spawn.call_args.args[0]['executable'], str(newer / 'Contents/MacOS/dervo-local'))
        self.assertTrue(newer.exists() and current.exists())
        self.assertFalse(any(app.exists() for app in older))
        self.assertEqual(sorted(found['releaseId'] for found in record['pruned']['removed']), ['0.1.0-prebeta.38', '0.1.0-prebeta.39'])

    def installed(self):
        current = fake_app(self.root / 'Applications/Dervo Local.app', '0.1.0-prebeta.42', 420)
        archive = self.zipped('0.1.0-prebeta.43', 430)
        with self.sign():
            staged = app_update.stage(archive, '0.1.0-prebeta.43', 420, verify=verify_bundle)
        self.start_host()
        plan = app_update.prepare(self.host, {'currentExecutable': str(current / 'Contents/MacOS/dervo-local'), 'dataOverride': False, 'parentPid': 1000000}, staged=staged)
        self.close_host()
        return current, Path(staged['bundle']), plan

    def test_a_downloaded_update_takes_this_apps_place_and_keeps_this_app_to_go_back_to(self):
        current, staged, plan = self.installed()
        record, spawn = self.switch(plan, return_value=4321)
        self.assertEqual(record['state'], 'opened')
        self.assertEqual(spawn.call_args.args[0]['executable'], str(current / 'Contents/MacOS/dervo-local'), 'the new app opens from the old one’s place')
        self.assertIn('prebeta.43', (current / 'Contents/MacOS/dervo-local').read_text())
        previous = Path(record['previous'])
        self.assertIn('prebeta.42', (previous / 'Contents/MacOS/dervo-local').read_text())
        self.assertFalse(staged.exists())
        self.assertIn(str(previous), (Path(record['directory']) / 'Open previous Dervo.command').read_text())
        # The next update clears this kept copy and keeps its own.
        earlier = Path(record['directory'])
        self.assertEqual(app_update.clear_history_apps(earlier.parent, self.root / 'elsewhere'), [str(previous)])

    def test_an_app_that_cant_be_replaced_where_it_runs_is_named_before_anything_is_backed_up(self):
        # Harrison, 25 Sep: beta.14 ran from a macOS App Translocation copy, the swap hit a read-only file system and
        # Finder opened a Version history folder. Now the plan is refused with a plain step and no folder is made.
        current, staged, plan = self.installed()
        translocated = '/private/var/folders/x/T/AppTranslocation/B8C8/d/Dervo Local.app'
        self.assertEqual(app_update.install_problem(translocated, staged), app_update.MOVE_FIRST)
        self.assertIsNone(app_update.install_problem(current, staged))
        self.start_host()
        with patch('app_update.bundle_from_executable', return_value=Path(translocated)), \
                self.assertRaisesRegex(Rejected, 'move Dervo Local.app into your Applications folder'):
            app_update.prepare(self.host, {'currentExecutable': translocated + '/Contents/MacOS/dervo-local', 'dataOverride': False, 'parentPid': 1000000},
                               staged={'bundle': str(staged), 'releaseId': '0.1.0-prebeta.43', 'build': 430})
        self.close_host()
        with patch('app_update.install_problem', return_value=app_update.MOVE_FIRST), \
                self.assertRaisesRegex(RuntimeError, 'can’t update itself'):
            self.switch(plan, return_value=4321)
        self.assertEqual(list(self.root.glob('Library/Application Support/Dervo/Version history/*/*')), [])
        self.assertIn('prebeta.42', (current / 'Contents/MacOS/dervo-local').read_text())

    def test_a_read_only_folder_or_another_disk_is_named_and_applications_gets_the_permission_line(self):
        folder = self.root / 'locked'
        app = fake_app(folder / 'Dervo Local.app', '0.1.0-prebeta.42', 420)
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)
        self.assertEqual(app_update.install_problem(app), app_update.MOVE_FIRST)
        with patch('app_update.Path.home', return_value=self.root), patch('app_update.os.access', return_value=False):
            self.assertEqual(app_update.install_problem(Path('/Applications/Dervo Local.app')), app_update.NO_PERMISSION)
        folder.chmod(0o755)
        (self.root / 'Updates').mkdir()
        real = app_update.os.stat
        other = lambda path, *a, **k: (SimpleNamespace(st_dev=1, st_mode=real(path).st_mode) if Path(path) == self.root / 'Updates'
                                       else real(path, *a, **k))
        with patch('app_update.os.stat', side_effect=other):
            self.assertEqual(app_update.install_problem(app, self.root / 'Updates/Dervo Local.app'), app_update.MOVE_FIRST)
        self.assertIsNone(app_update.install_problem(app, self.root / 'Updates/Dervo Local.app'))

    def test_the_running_bundle_is_found_only_inside_an_app(self):
        self.assertEqual(app_update.running_bundle(self.root / 'Dervo Local.app/Contents/Resources'), self.root / 'Dervo Local.app')
        self.assertIsNone(app_update.running_bundle(self.root / 'backend'))
        self.assertIsNone(app_update.running_bundle(self.root / 'notanapp/Contents/Resources'))

    def test_a_downloaded_update_that_doesnt_start_puts_the_previous_app_back(self):
        current, staged, plan = self.installed()
        with self.assertRaisesRegex(RuntimeError, 'stopped during startup'):
            self.switch(plan, side_effect=RuntimeError('The new Dervo app stopped during startup.'))
        self.assertIn('prebeta.42', (current / 'Contents/MacOS/dervo-local').read_text())
        self.assertIn('prebeta.43', (staged / 'Contents/MacOS/dervo-local').read_text())
        records = list(self.root.glob('Library/Application Support/Dervo/Version history/*/*/switch.json'))
        self.assertEqual(json.loads(records[0].read_text())['state'], 'failed')

    def test_the_updates_screen_finds_a_local_build_without_an_account_and_offers_update_and_restart(self):
        from beta_update_host import Updates
        current = self.build('0.1.0-prebeta.42', 420)
        resources = current / 'Contents/Resources'
        (resources / 'runtime/dervo-release-profile.json').write_text(json.dumps({'schema': 1, 'profile': 'private-beta', 'label': 'Private beta', 'providers': ['claude', 'codex'], 'openRouter': False}))
        host = SimpleNamespace(account_session=SimpleNamespace(require_access=lambda: (_ for _ in ()).throw(Rejected('signed out'))))
        updates = Updates(host, resources=resources)
        with patch('beta_update_host.running_version', return_value={'releaseId': '0.1.0-prebeta.42', 'buildNumber': '420', 'productVersion': '0.1.0'}):
            context = updates.status()['context']
            with self.assertRaises(Rejected):
                updates.dispatch('beta_update_check', {'context': context})
            self.build('0.1.0-prebeta.43', 430)
            status = updates.dispatch('beta_update_check', {'context': context})
        self.assertEqual((status['state'], status['update'], status['canUpdate']), ('ready', {'source': 'local', 'releaseId': '0.1.0-prebeta.43'}, True))
        self.assertIn('Update and restart backs up your workspace', status['detail'])

    # --- Developer ID: a download is staged only when this app's team signed it -------------------------------------
    def teams(self, mapping):
        def fake(command, **kwargs):
            if command[1] == '-d':
                team = mapping.get(command[-1])
                return SimpleNamespace(returncode=0, stderr=('TeamIdentifier=' + (team or 'not set') + '\n'
                    + ('Signature=adhoc\n' if team is None else '')).encode())
            return SimpleNamespace(returncode=0, stderr=b'')
        return patch('release_switch.subprocess.run', side_effect=fake)

    def test_a_download_from_another_team_is_refused_and_one_from_this_team_or_by_an_ad_hoc_app_is_staged(self):
        from functools import partial
        for running_team, download_team, staged_ok in (('ABCDE12345', 'ZZZZZ99999', False), ('ABCDE12345', None, False),
                                                       ('ABCDE12345', 'ABCDE12345', True), (None, 'ABCDE12345', True), (None, None, True)):
            with self.subTest(running=running_team, download=download_team):
                archive = self.zipped('0.1.0-prebeta.44', 440)
                app = archive.parent / 'Dervo Local.app'
                verify = partial(verify_bundle, team=running_team)
                with self.teams({str(app): download_team}):
                    if staged_ok:
                        self.assertEqual(app_update.stage(archive, '0.1.0-prebeta.44', 430, verify=verify)['bundle'], str(app))
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'isn’t signed by the developer'):
                            app_update.stage(archive, '0.1.0-prebeta.44', 430, verify=verify)
                        self.assertTrue(archive.exists(), 'the archive is left for the caller to remove')
                import shutil
                shutil.rmtree(self.root / 'package'); shutil.rmtree(self.root / 'Updates')


if __name__ == '__main__':
    unittest.main()
