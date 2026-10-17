"""The one release command (scripts/ship-release.py): every platform's package checked against its catalog row, and
nothing uploaded, inserted, released or posted without --go. Every command here is faked; no network is used."""
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'ship-release.py'
spec = importlib.util.spec_from_file_location('ship_release', SCRIPT)
ship = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ship)

BASE = '0.1.0-beta.19'
ACCOUNT = '0123456789abcdef0123456789abcdef'
WEBHOOK = 'https://discord.com/api/webhooks/1/token'
NAMES = {'macos': 'Dervo-0.1.0-beta.19.dmg', 'windows': 'Dervo-windows-x64.exe', 'linux-deb': 'Dervo-linux-x64.deb',
         'linux-rpm': 'Dervo-linux-x64.rpm', 'linux-appimage': 'Dervo-linux-x64.AppImage'}


def write_package(folder, release_id, name, build, version='0.1.0', data=None):
    folder.mkdir(parents=True)
    data = data or (release_id + ' installer').encode()
    (folder / name).write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    (folder / 'package.json').write_text(json.dumps({
        'releaseId': release_id, 'artifact': name, 'sha256': sha, 'bytes': len(data),
        'bundleAudit': {'nativeVersion': {'releaseId': release_id, 'buildNumber': str(build),
                                          'productVersion': version}}}))
    return sha


def sign_package(folder):
    package = json.loads((folder / 'package.json').read_text())
    (folder / 'release-signature.json').write_text(json.dumps({
        'releaseId': package['releaseId'], 'sha256': package['sha256'], 'signature': 'ab' * 64}))


class Recorder:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        failed = self.fail_on is not None and self.fail_on in ' '.join(argv)
        return subprocess.CompletedProcess(argv, 1 if failed else 0, '', 'refused' if failed else '')


class ShipReleaseTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.folder = self.temp / 'release'
        for number, (name, _, _, ending, _) in enumerate(ship.ROWS):
            write_package(self.folder / name, BASE + ending, NAMES[name], 1790400000 + number)
        self.notes = self.temp / 'notes.md'
        self.notes.write_text('# Faster Home\n\nHome shows every agent.\nLinux and Windows join the beta.\n')
        self.env = {'DERVO_R2_ACCOUNT_ID': ACCOUNT, 'DERVO_CATALOG_DATABASE_URL': 'postgresql://example/db',
                    'DERVO_DISCORD_RELEASES_WEBHOOK': WEBHOOK}

    def sign_all(self):
        for name, *_ in ship.ROWS:
            sign_package(self.folder / name)

    def test_base_is_the_mac_id_without_a_platform_ending(self):
        self.assertEqual(ship.base_id(BASE), BASE)
        for bad in (BASE + '-windows', BASE + '-linux', BASE + '-linux-deb', '', '../x', 'a' * 81, None):
            with self.assertRaises(ship.ShipError):
                ship.base_id(bad)

    def test_prepare_writes_both_switches(self):
        (self.temp / '.github').mkdir()
        result = ship.prepare(self.temp, BASE)
        self.assertEqual(json.loads((self.temp / '.github/windows-release.json').read_text()),
                         {'releaseId': BASE + '-windows'})
        self.assertEqual(json.loads((self.temp / '.github/linux-release.json').read_text()),
                         {'releaseId': BASE + '-linux'})
        self.assertEqual(set(result['written'].values()), {BASE + '-windows', BASE + '-linux'})

    def test_every_row_matches_the_catalog_shape(self):
        rows = ship.release_rows(self.folder, BASE)
        self.assertEqual([(r['release_id'], r['platform'], r['format'], r['object_name']) for r in rows], [
            (BASE, 'macos-arm64', 'dmg', BASE + '/Dervo-macos-arm64.dmg'),
            (BASE + '-windows', 'windows-x64', 'exe', BASE + '-windows/Dervo-windows-x64.exe'),
            (BASE + '-linux-deb', 'linux-x64', 'deb', BASE + '-linux-deb/Dervo-linux-x64.deb'),
            (BASE + '-linux-rpm', 'linux-x64', 'rpm', BASE + '-linux-rpm/Dervo-linux-x64.rpm'),
            (BASE + '-linux-appimage', 'linux-x64', 'appimage', BASE + '-linux-appimage/Dervo-linux-x64.appimage')])
        self.assertTrue(all(r['signature'] is None for r in rows))
        self.sign_all()
        self.assertTrue(all(r['signature'] == 'ab' * 64 for r in ship.release_rows(self.folder, BASE)))

    def test_a_package_that_doesnt_match_its_row_is_refused(self):
        cases = {
            'wrong id': lambda: write_package(self.temp / 'x' / 'windows', BASE + '-linux', NAMES['windows'], 9),
            'changed installer': lambda: (self.folder / 'linux-rpm' / NAMES['linux-rpm']).write_bytes(b'other'),
            'shared build': lambda: write_package(self.temp / 'x' / 'windows', BASE + '-windows', NAMES['windows'],
                                                  1790400000),
            'other version': lambda: write_package(self.temp / 'x' / 'windows', BASE + '-windows', NAMES['windows'],
                                                   9, version='0.2.0'),
            'signature of another release': lambda: (self.folder / 'linux-deb' / 'release-signature.json').write_text(
                json.dumps({'releaseId': BASE + '-linux-rpm', 'sha256': '0' * 64, 'signature': 'ab' * 64})),
        }
        original = {p: p.read_bytes() for p in self.folder.rglob('*') if p.is_file()}
        for label, change in cases.items():
            with self.subTest(label):
                for p in list(self.folder.rglob('release-signature.json')):
                    p.unlink()
                for p, data in original.items():
                    p.write_bytes(data)
                if (self.temp / 'x').exists():
                    shutil.rmtree(self.temp / 'x')
                change()
                if (self.temp / 'x' / 'windows').exists():
                    shutil.rmtree(self.folder / 'windows')
                    shutil.copytree(self.temp / 'x' / 'windows', self.folder / 'windows')
                with self.assertRaises(ship.ShipError):
                    ship.release_rows(self.folder, BASE)

    def test_collect_downloads_each_platforms_release_and_checks_it(self):
        def run(argv, **kwargs):
            target = Path(argv[argv.index('--dir') + 1])
            name = argv[argv.index('--name') + 1]
            if name.endswith('-windows'):
                (self.folder / 'windows').rename(target)
            else:
                target.mkdir(parents=True)
                for fmt in ('deb', 'rpm', 'appimage'):
                    (self.folder / ('linux-' + fmt)).rename(target / fmt)
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, '', '')
        calls = []
        out = self.temp / 'collected'
        rows = ship.collect(BASE, self.folder / 'macos', out, {'windows': '111', 'linux': None}, run=run)
        self.assertEqual([r['release_id'] for r in rows],
                         [BASE, BASE + '-windows', BASE + '-linux-deb', BASE + '-linux-rpm', BASE + '-linux-appimage'])
        self.assertEqual(calls[0][:5], ['gh', 'run', 'download', '111', '--repo'])
        self.assertIn('dervo-windows-release-' + BASE + '-windows', calls[0])
        self.assertIn('dervo-linux-release-' + BASE + '-linux', calls[1])
        self.assertNotIn(None, calls[1])

    def test_collect_stops_when_a_download_fails(self):
        with self.assertRaisesRegex(ship.ShipError, 'dervo-windows-release'):
            ship.collect(BASE, self.folder / 'macos', self.temp / 'out', {}, run=Recorder(fail_on='download'))

    def test_sign_skips_signed_packages_and_stops_at_the_first_refusal(self):
        sign_package(self.folder / 'macos')
        run = Recorder()
        self.assertEqual(ship.sign(self.folder, run=run)[0], 'macos (already signed)')
        self.assertEqual([argv[-1] for argv, _ in run.calls],
                         [str((self.folder / name).resolve()) for name, *_ in ship.ROWS[1:]])
        run = Recorder(fail_on='linux-deb')
        with self.assertRaises(ship.ShipError):
            ship.sign(self.folder, run=run)
        self.assertEqual(len(run.calls), 2)  # windows, then linux-deb refused; macos was already signed

    def test_publish_refuses_unsigned_packages(self):
        run = Recorder()
        with self.assertRaisesRegex(ship.ShipError, 'isn.t signed'):
            ship.publish(self.folder, BASE, self.notes, 'private-beta', True, env=self.env, run=run)
        self.assertEqual(run.calls, [])

    def test_publish_is_a_dry_run_without_go(self):
        self.sign_all()
        run = Recorder()
        result = ship.publish(self.folder, BASE, self.notes, 'private-beta', False, env=self.env, run=run)
        self.assertTrue(result['dryRun'])
        self.assertEqual(run.calls, [])
        self.assertEqual(len(result['uploads']), 5)
        self.assertTrue(all(u.startswith('dervo-beta-releases/' + BASE) for u in result['uploads']))
        sql = (self.folder / 'catalog.sql').read_text()
        self.assertTrue(sql.startswith('begin;') and sql.endswith('commit;\n'))
        self.assertEqual(sql.count("'linux-x64'"), 3)
        self.assertIn(f"'https://{ACCOUNT}.r2.cloudflarestorage.com'", sql)
        self.assertIn("'Faster Home'", sql)

    def test_catalog_sql_quotes_text_and_checks_its_inputs(self):
        self.sign_all()
        rows = ship.release_rows(self.folder, BASE)
        sql = ship.catalog_sql(rows, "Bryce's build", "It's here; drop table x; --", 'admin-preview',
                               f'https://{ACCOUNT}.r2.cloudflarestorage.com')
        self.assertIn("'Bryce''s build'", sql)
        self.assertIn("'It''s here; drop table x; --'", sql)
        self.assertIn("'admin-preview'", sql)
        with self.assertRaises(ship.ShipError):
            ship.catalog_sql(rows, 't', 'n', 'public', f'https://{ACCOUNT}.r2.cloudflarestorage.com')
        with self.assertRaises(ship.ShipError):
            ship.catalog_sql(rows, 't', 'n', 'private-beta', 'https://.r2.cloudflarestorage.com')

    def test_publish_uploads_every_installer_before_any_catalog_row(self):
        self.sign_all()
        run = Recorder()
        ship.publish(self.folder, BASE, self.notes, 'private-beta', True, env=self.env, run=run)
        commands = [argv[0] for argv, _ in run.calls]
        self.assertEqual(commands, ['npx'] * 5 + ['psql'])
        self.assertEqual(run.calls[-1][1]['env']['PGDATABASE'], 'postgresql://example/db')
        self.assertNotIn('postgresql://example/db', ' '.join(' '.join(argv) for argv, _ in run.calls))

    def test_publish_adds_no_row_when_an_upload_fails_or_the_database_is_unset(self):
        self.sign_all()
        run = Recorder(fail_on='linux-rpm')
        with self.assertRaisesRegex(ship.ShipError, 'No catalog row'):
            ship.publish(self.folder, BASE, self.notes, 'private-beta', True, env=self.env, run=run)
        self.assertNotIn('psql', [argv[0] for argv, _ in run.calls])
        run = Recorder()
        env = {k: v for k, v in self.env.items() if k != 'DERVO_CATALOG_DATABASE_URL'}
        with self.assertRaises(ship.ShipError):
            ship.publish(self.folder, BASE, self.notes, 'private-beta', True, env=env, run=run)
        self.assertEqual(run.calls, [])

    def test_announce_is_a_dry_run_without_go_and_never_pings_anyone(self):
        self.sign_all()
        run, posts = Recorder(), []
        result = ship.announce(self.folder, BASE, self.notes, False, root=self.temp, env=self.env, run=run,
                               post=lambda *a: posts.append(a))
        self.assertTrue(result['dryRun'])
        self.assertEqual((run.calls, posts), ([], []))
        self.assertFalse((self.temp / 'CHANGELOG.md').exists())
        message = result['discordMessage']
        self.assertEqual(message['allowed_mentions'], {'parse': []})
        self.assertIn('Linux (AppImage)', message['embeds'][0]['fields'][0]['value'])
        manifest = json.loads((self.folder / 'beta.json').read_text())
        self.assertEqual(manifest['releaseId'], BASE)
        self.assertEqual([d['file'] for d in manifest['downloads']], [BASE + '/' + NAMES[n] for n, *_ in ship.ROWS])

    def test_announce_needs_signed_packages_and_the_webhook(self):
        run = Recorder()
        with self.assertRaises(ship.ShipError):
            ship.announce(self.folder, BASE, self.notes, True, root=self.temp, env=self.env, run=run, post=None)
        self.sign_all()
        for webhook in ('', 'https://example.com/api/webhooks/1/x'):
            with self.assertRaisesRegex(ship.ShipError, 'Nothing was announced'):
                ship.announce(self.folder, BASE, self.notes, True, root=self.temp,
                              env={**self.env, 'DERVO_DISCORD_RELEASES_WEBHOOK': webhook}, run=run, post=None)
        self.assertEqual(run.calls, [])

    def test_announce_posts_to_discord_last(self):
        self.sign_all()
        (self.temp / 'CHANGELOG.md').write_text('# Dervo changelog\n\n## 0.1.0-beta.18 (2026-10-01)\n\nOlder.\n')
        order = []
        run = Recorder()
        def recording_run(argv, **kwargs):
            order.append(argv[0] if argv[0] != 'npx' else 'site')
            return run(argv, **kwargs)
        now = datetime.datetime(2026, 10, 9, tzinfo=datetime.timezone.utc)
        result = ship.announce(self.folder, BASE, self.notes, True, root=self.temp, env=self.env, run=recording_run,
                               post=lambda url, payload: order.append(('discord', url)), now=now)
        self.assertEqual(result['announced'], ['site', 'github', 'changelog', 'discord'])
        self.assertEqual(order, ['site'] * 6 + ['gh', ('discord', WEBHOOK)])
        self.assertIn('dervo-beta-installers/beta/latest.json', run.calls[5][0])
        changelog = (self.temp / 'CHANGELOG.md').read_text()
        self.assertLess(changelog.index(BASE + ' (2026-10-09)'), changelog.index('0.1.0-beta.18'))

    def test_announce_doesnt_post_when_the_github_release_fails(self):
        self.sign_all()
        posts = []
        with self.assertRaisesRegex(ship.ShipError, "Announced so far: \\['site'\\]"):
            ship.announce(self.folder, BASE, self.notes, True, root=self.temp, env=self.env,
                          run=Recorder(fail_on='release create'), post=lambda *a: posts.append(a))
        self.assertEqual(posts, [])
        self.assertFalse((self.temp / 'CHANGELOG.md').exists())


if __name__ == '__main__':
    unittest.main()
