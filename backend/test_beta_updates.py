"""Offline release transport checks. No hosted calls, real keys or downloads."""
import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import beta_updates as updates
from release_profiles import stage
from supabase_client import AccountError, Session, SupabaseClient
from test_supabase_client import ACCESS, REFRESH, USER, PROJECT, HTTP


def record(**changes):
    return {**dict(release_id='0.1.0-prebeta.100', build_number=1789452916913747,
                   product_version='0.1.0', channel='private-beta', platform='macos-arm64',
                   title='A clearer workspace', notes='Save images.\nPick up your work later.',
                   download_origin='https://' + 'a' * 32 + '.r2.cloudflarestorage.com',
                   format='zip', object_name='0.1.0-prebeta.100/Dervo-macos-arm64.zip',
                   sha256='a' * 64, bytes=300000000, published_at='2026-01-01T00:00:00Z', signature='e' * 128), **changes}


ALLOWED = dict(status='approved', allowed=True, expires_at=None,
               release_channel='private-beta', is_admin=False)
NOW = 1789460000
SESSION = Session(ACCESS, REFRESH, USER, NOW + 3600)


class UpdateTests(unittest.TestCase):
    # record() is a Mac row, so these tests ask as a Mac does, on the Windows runner too.
    MAC_ROWS = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        stage(self.root, 'private-beta')
        if self.MAC_ROWS:
            mac = patch.object(updates, 'this_platform', return_value='macos-arm64')
            mac.start()
            self.addCleanup(mac.stop)

    def client(self, *responses):
        http = HTTP(*responses)
        return SupabaseClient(PROJECT, opener=http, clock=lambda: NOW), http

    def test_latest_uses_numeric_build_and_only_authenticated_fixed_endpoint(self):
        client, http = self.client(ALLOWED, [record()])
        result = updates.latest(client, SESSION, self.root, 1789452916913746)
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['release'], record())
        self.assertEqual([r.method for r in http.requests], ['POST', 'GET'])
        self.assertIn('order=build_number.desc&limit=1', http.requests[1].full_url)
        for r in http.requests:
            self.assertEqual(r.headers['Authorization'], 'Bearer ' + ACCESS)
            self.assertNotIn(ACCESS, r.full_url)
        self.assertNotIn(REFRESH, str([r.data for r in http.requests]))

    def test_an_admin_preview_is_asked_for_and_offered_like_a_beta(self):
        """Admin previews (24 Sep): the app asks for both channels; the catalog's policy decides who gets a preview row
        (supabase/tests/releases.sql), so a tester's answer is only ever a beta."""
        preview = record(release_id='0.1.0-preview.20260924120000000', channel='admin-preview',
                         object_name='0.1.0-preview.20260924120000000/Dervo-macos-arm64.zip')
        client, http = self.client(ALLOWED, [preview])
        result = updates.latest(client, SESSION, self.root, 1789452916913746)
        self.assertEqual((result['state'], result['release']['channel']), ('available', 'admin-preview'))
        self.assertIn('channel=in.(private-beta,admin-preview)', http.requests[1].full_url)

    def test_no_release_equal_or_older_never_offers_downgrade(self):
        for rows, current in [([], 1), ([record()], 1789452916913747), ([record()], 1789452916913748)]:
            client, _ = self.client(ALLOWED, rows)
            self.assertEqual(updates.latest(client, SESSION, self.root, current)['state'],
                             'current' if rows else 'unpublished')

    def test_personal_profile_and_bad_build_refuse_before_network(self):
        client, http = self.client()
        for build in [True, '1789452916913747', 1.5, 0, -1, 10 ** 20]:
            with self.assertRaises(AccountError):
                updates.latest(client, SESSION, self.root, build)
        stage(self.root, 'personal-experimental')
        with self.assertRaises(AccountError):
            updates.latest(client, SESSION, self.root, 1)
        with self.assertRaises(AccountError):
            updates.download(client, SESSION, self.root, record()['release_id'])
        self.assertEqual(http.requests, [])

    def test_membership_checked_before_every_lookup_and_download(self):
        for status in ['pending', 'revoked']:
            for action in ['latest', 'download']:
                client, http = self.client(dict(ALLOWED, status=status, allowed=False))
                with self.assertRaises(AccountError):
                    getattr(updates, action)(client, SESSION, self.root, 1 if action == 'latest' else 'v1')
                self.assertEqual(len(http.requests), 1)
        client, http = self.client()
        with self.assertRaises(AccountError):
            updates.latest(client, Session(ACCESS, REFRESH, USER, NOW), self.root, 1)
        self.assertEqual(http.requests, [])

    def test_bad_metadata_cannot_supply_urls_paths_or_non_beta_packages(self):
        for change in [{'object_name': '../../evil.zip'}, {'download_origin': 'https://evil.example'}, {'platform': 'macos-x64'},
                       {'channel': 'personal-experimental'}, {'format': 'exe'},
                       {'build_number': 1.5}, {'build_number': True}, {'sha256': 'BAD'},
                       {'bytes': 0}, {'bytes': True}, {'bytes': 2147483649},
                       {'title': ' '}, {'notes': '\x00private'}, {'release_id': 'v1&limit=20'},
                       {'published_at': None}, {'published_at': '2099-01-01T00:00:00Z'},
                       {'published_at': '2026-01-01'}, {'product_version': 'latest'},
                       {'download_url': 'https://evil.example'}, {'signature': 'E' * 128}, {'signature': 'e' * 127},
                       {'signature': ''}, {'signature': 7}]:
            with self.subTest(change=change):
                client, _ = self.client(ALLOWED, [dict(record(), **change)])
                with self.assertRaises(AccountError):
                    updates.latest(client, SESSION, self.root, 1)

    def test_an_unsigned_release_is_still_readable(self):
        # Its download is refused later, by release_signature.check. Reading it here says an update exists.
        client, _ = self.client(ALLOWED, [record(signature=None)])
        self.assertEqual(updates.latest(client, SESSION, self.root, 1)['release']['signature'], None)
        client, http = self.client(ALLOWED, [record()])
        updates.latest(client, SESSION, self.root, 1)
        self.assertIn(',signature', http.requests[1].full_url)

    def test_invalid_cardinality_and_unknown_failure_do_not_mean_up_to_date(self):
        for rows in [None, {}, [record(), record()], [{'release_id': 'x'}]]:
            client, _ = self.client(ALLOWED, rows)
            with self.assertRaises(AccountError):
                updates.latest(client, SESSION, self.root, 1)
        client, _ = self.client(ALLOWED, HTTPError('https://fixture.invalid/?private', 500, '', {}, None))
        with self.assertRaises(AccountError) as error:
            updates.latest(client, SESSION, self.root, 1)
        self.assertNotIn('private', str(error.exception))

    def signed(self, **changes):
        stamp = datetime.fromtimestamp(NOW, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        query = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256', 'X-Amz-Credential': 'b' * 32 + '/' + stamp[:8] + '/auto/s3/aws4_request',
                 'X-Amz-Date': stamp, 'X-Amz-Expires': '120', 'X-Amz-SignedHeaders': 'host',
                 'X-Amz-Signature': 'c' * 64,
                 'response-content-disposition': 'attachment; filename="Dervo-' + record()['release_id'] + '.zip"'}
        return dict(releaseId=record()['release_id'], sha256=record()['sha256'], bytes=record()['bytes'],
                    expiresAt=NOW + 120, url=record()['download_origin'] + '/dervo-beta-releases/' + record()['object_name'] + '?' + urlencode(query), **changes)

    def test_download_rechecks_exact_record_and_calls_only_project_function(self):
        client, http = self.client(ALLOWED, [record()], self.signed())
        result = updates.download(client, SESSION, self.root, record()['release_id'])
        self.assertEqual(result.url, self.signed()['url'])
        self.assertEqual(result.expires_at, NOW + 120)
        self.assertNotIn('X-Amz-Signature', repr(result))
        self.assertEqual(json.loads(http.requests[-1].data), {'releaseId': record()['release_id'], 'sha256': record()['sha256']})
        self.assertEqual(http.requests[-1].full_url, PROJECT.url + '/functions/v1/dervo-beta-download')
        self.assertEqual(http.requests[-1].method, 'POST')
        self.assertIn('release_id=eq.0.1.0-prebeta.100', http.requests[1].full_url)
        self.assertTrue(all(r.full_url.startswith(PROJECT.url) for r in http.requests))

    def test_withdrawn_or_mismatched_release_cannot_create_link(self):
        for rows in [[], [record()]]:
            client, http = self.client(ALLOWED, rows)
            with self.assertRaises(AccountError):
                updates.download(client, SESSION, self.root, 'different')
            self.assertEqual(len(http.requests), 2)

    def test_link_pins_origin_object_artifact_expiry_and_aws_query(self):
        good = self.signed()
        for change in [{'url': 'https://evil.example/'}, {'url': '//evil.example/'},
                       {'url': good['url'] + '&redirect=https://evil.example'},
                       {'url': good['url'].replace('prebeta.100/', 'prebeta.99/')},
                       {'url': good['url'] + chr(10)}, {'url': 123},
                       {'url': good['url'].replace('X-Amz-Expires=120', 'X-Amz-Expires=3600')},
                       {'url': good['url'] + '&X-Amz-Expires=120'},
                       {'url': good['url'] + '#fragment'},
                       {'url': good['url'].replace('SignedHeaders=host', 'SignedHeaders=host%3Bauthorization')},
                       {'url': good['url'].replace('a' * 32, 'd' * 32)},
                       {'expiresAt': NOW}, {'expiresAt': NOW + 3600}, {'expiresAt': True},
                       {'sha256': 'd' * 64}, {'bytes': 1}, {'releaseId': 'different'}, {'extra': 'value'}]:
            with self.subTest(change=change):
                client, _ = self.client(ALLOWED, [record()], dict(good, **change))
                with self.assertRaises(AccountError):
                    updates.download(client, SESSION, self.root, record()['release_id'])


def windows(**changes):
    return record(release_id='0.1.0-beta.17-windows', platform='windows-x64', format='exe',
                  object_name='0.1.0-beta.17-windows/Dervo-windows-x64.exe', **changes)


class WindowsUpdateTests(unittest.TestCase):
    """Windows rows (supabase/migrations/202609260001_dervo_windows_releases.sql): each computer asks for its own
    platform and takes only its own rows, and a Windows row's release id ends in -windows."""
    MAC_ROWS = False
    setUp = UpdateTests.setUp
    client = UpdateTests.client

    def test_this_computer_decides_the_platform(self):
        with patch.object(updates.platform_os, 'WINDOWS', True):
            self.assertEqual(updates.this_platform(), 'windows-x64')
        with patch.object(updates.platform_os, 'WINDOWS', False), patch.object(updates.platform_os, 'LINUX', False):
            self.assertEqual(updates.this_platform(), 'macos-arm64')
        with patch.object(updates.platform_os, 'WINDOWS', False), patch.object(updates.platform_os, 'LINUX', True):
            self.assertEqual(updates.this_platform(), 'linux-x64')

    def test_windows_asks_for_and_offers_its_own_rows(self):
        client, http = self.client(ALLOWED, [windows()])
        result = updates.latest(client, SESSION, self.root, 1, 'windows-x64')
        self.assertEqual((result['state'], result['release']), ('available', windows()))
        self.assertIn('&platform=eq.windows-x64&', http.requests[1].full_url)

    def test_a_mac_asks_only_for_mac_rows(self):
        client, http = self.client(ALLOWED, [record()])
        updates.latest(client, SESSION, self.root, 1, 'macos-arm64')
        self.assertIn('&platform=eq.macos-arm64&', http.requests[1].full_url)

    def test_neither_computer_takes_the_others_rows(self):
        for platform, row in [('windows-x64', record()), ('macos-arm64', windows())]:
            with self.subTest(platform=platform):
                client, _ = self.client(ALLOWED, [row])
                with self.assertRaises(AccountError):
                    updates.latest(client, SESSION, self.root, 1, platform)

    def test_bad_windows_rows_are_refused(self):
        for change in [{'format': 'zip', 'object_name': '0.1.0-beta.17-windows/Dervo-windows-x64.zip'},
                       {'object_name': '0.1.0-beta.17-windows/Dervo-macos-arm64.exe'},
                       {'release_id': '0.1.0-beta.17', 'object_name': '0.1.0-beta.17/Dervo-windows-x64.exe'},
                       {'object_name': '../Dervo-windows-x64.exe'}]:
            with self.subTest(change=change):
                client, _ = self.client(ALLOWED, [dict(windows(), **change)])
                with self.assertRaises(AccountError):
                    updates.latest(client, SESSION, self.root, 1, 'windows-x64')

    def test_a_mac_row_can_not_carry_a_windows_id(self):
        row = record(release_id='0.1.0-beta.17-windows', object_name='0.1.0-beta.17-windows/Dervo-macos-arm64.zip')
        client, _ = self.client(ALLOWED, [row])
        with self.assertRaises(AccountError):
            updates.latest(client, SESSION, self.root, 1, 'macos-arm64')

    def test_windows_download_link_names_the_installer(self):
        stamp = datetime.fromtimestamp(NOW, timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        row = windows()
        query = {'X-Amz-Algorithm': 'AWS4-HMAC-SHA256', 'X-Amz-Credential': 'b' * 32 + '/' + stamp[:8] + '/auto/s3/aws4_request',
                 'X-Amz-Date': stamp, 'X-Amz-Expires': '120', 'X-Amz-SignedHeaders': 'host', 'X-Amz-Signature': 'c' * 64,
                 'response-content-disposition': 'attachment; filename="Dervo-0.1.0-beta.17-windows.exe"'}
        url = row['download_origin'] + '/dervo-beta-releases/' + row['object_name'] + '?' + urlencode(query)
        signed = dict(releaseId=row['release_id'], sha256=row['sha256'], bytes=row['bytes'], expiresAt=NOW + 120, url=url)
        client, _ = self.client(ALLOWED, [row], signed)
        self.assertEqual(updates.download(client, SESSION, self.root, row['release_id'], 'windows-x64').url, url)


if __name__ == '__main__':
    unittest.main()

def linux(fmt='deb', **changes):
    release = '0.1.0-beta.19-linux-' + fmt
    return record(**{'release_id': release, 'platform': 'linux-x64', 'format': fmt,
                     'object_name': release + '/Dervo-linux-x64.' + fmt, **changes})


class LinuxUpdateTests(WindowsUpdateTests):
    """Linux rows (supabase/migrations/202610080001_dervo_linux_releases.sql): one per package format, each id ending in
    -linux-<format>, and an installed Dervo asks only for its own format."""

    def test_linux_asks_for_its_own_format_and_takes_only_that(self):
        for fmt in ('deb', 'rpm', 'appimage'):
            with self.subTest(fmt=fmt), patch.object(updates, 'linux_format', return_value=fmt):
                client, http = self.client(ALLOWED, [linux(fmt)])
                result = updates.latest(client, SESSION, self.root, 1, 'linux-x64')
                self.assertEqual((result['state'], result['release']), ('available', linux(fmt)))
                self.assertIn('&platform=eq.linux-x64&format=eq.' + fmt + '&', http.requests[1].full_url)

    def test_bad_linux_rows_and_other_platforms_rows_are_refused(self):
        bad = [linux('deb', release_id='0.1.0-beta.19-linux-rpm', object_name='0.1.0-beta.19-linux-rpm/Dervo-linux-x64.deb'),
               linux('deb', format='exe', object_name='0.1.0-beta.19-linux-deb/Dervo-linux-x64.exe'),
               linux('deb', release_id='0.1.0-beta.19', object_name='0.1.0-beta.19/Dervo-linux-x64.deb'),
               record(), windows()]
        for row in bad:
            with self.subTest(row=row['release_id']), patch.object(updates, 'linux_format', return_value='deb'):
                client, _ = self.client(ALLOWED, [row])
                with self.assertRaises(AccountError):
                    updates.latest(client, SESSION, self.root, 1, 'linux-x64')
        for platform in ('windows-x64', 'macos-arm64'):
            client, _ = self.client(ALLOWED, [linux()])
            with self.assertRaises(AccountError):
                updates.latest(client, SESSION, self.root, 1, platform)

    def test_the_install_format_comes_from_the_package_it_was_installed_from(self):
        with tempfile.TemporaryDirectory() as folder:
            listed = Path(folder) / 'dervo-local.list'
            self.assertEqual(updates.linux_format({'APPIMAGE': '/home/a/Dervo.AppImage'}, str(listed)), 'appimage')
            self.assertEqual(updates.linux_format({}, str(listed)), 'rpm')
            listed.write_text('/usr/bin/dervo-local\n')
            self.assertEqual(updates.linux_format({}, str(listed)), 'deb')

