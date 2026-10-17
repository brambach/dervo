"""Offline avatar privacy, image bounds and account-switch regressions."""
import base64
import io
import json
import os
import struct
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zlib

import account_photo as photo
from supabase_client import AccountError, Session, SupabaseClient
from test_supabase_client import HTTP, PROJECT, USER, OTHER, ACCESS, REFRESH
from test_supabase_account import AccountTests


def png(metadata=False, pixels=None):
    header = struct.pack('>IIBBBBB', 128, 128, 8, 6, 0, 0, 0)
    pixels = pixels if pixels is not None else (b'\x00' + b'\x88\x99\xaa\xff' * 128) * 128
    return photo.SIGNATURE + photo.chunk(b'IHDR', header) + (photo.chunk(b'tEXt', b'private metadata') if metadata else b'') + photo.chunk(b'IDAT', zlib.compress(pixels)) + photo.chunk(b'IEND', b'')


class PhotoTests(unittest.TestCase):
    def test_normalization_removes_metadata_and_fits_native_bridge_limit(self):
        source = png(metadata=True, pixels=b''.join(b'\x00' + os.urandom(512) for _ in range(128)))
        clean = photo.normalize(source)
        self.assertNotIn(b'private metadata', clean)
        self.assertEqual(photo.normalize(clean), clean)
        request = {'operation': 'dervo_account_save_photo', 'args': {'subject': USER, 'png': base64.b64encode(clean).decode()}}
        self.assertLess(len(json.dumps(request)), 131072)

    def test_invalid_crc_trailing_content_and_decompression_bomb_are_rejected(self):
        bad_filter = (b'\x05' + b'\0' * 512) * 128
        for value in (b'not a photo', png() + b'private extra', png()[:-1] + b'x',
                      png(pixels=b'\0' * 1000000), png(pixels=bad_filter)):
            with self.assertRaises(AccountError):
                photo.normalize(value)
        with self.assertRaises(AccountError):
            photo.decode('x' * 110001)

    def test_http_uses_only_private_owner_path_and_scoped_delete(self):
        http = HTTP({}, png(), [])
        client = SupabaseClient(PROJECT, opener=http)
        session = Session(ACCESS, REFRESH, USER, 4600)
        photo.request(client, session, 'POST', png())
        self.assertEqual(photo.request(client, session, 'GET'), photo.normalize(png()))
        photo.request(client, session, 'DELETE')
        post, get, delete = http.requests
        self.assertTrue(post.full_url.endswith('/object/dervo-avatars/' + USER + '/profile.png'))
        self.assertEqual(post.headers['X-upsert'], 'true')
        self.assertIn('/object/authenticated/dervo-avatars/', get.full_url)
        self.assertEqual(json.loads(delete.data), {'prefixes': [USER + '/profile.png']})
        self.assertTrue(delete.full_url.endswith('/object/dervo-avatars'))
        self.assertTrue(all(r.headers['Authorization'] == 'Bearer ' + ACCESS for r in http.requests))

    def test_missing_is_distinct_from_denied_or_server_failure(self):
        for code, payload, missing in [(404, {}, True), (400, {'statusCode': '404'}, True),
                                        (403, {}, False), (500, {}, False), (400, {'error': 'bad'}, False)]:
            client = SupabaseClient(PROJECT, opener=HTTP(HTTPError('https://fixture.invalid/' + ACCESS,
                code, REFRESH, {}, io.BytesIO(json.dumps(payload).encode()))))
            session = Session(ACCESS, REFRESH, USER, 4600)
            if missing:
                self.assertIsNone(photo.request(client, session, 'GET'))
            else:
                with self.assertRaises(AccountError) as caught:
                    photo.request(client, session, 'GET')
                self.assertNotIn(ACCESS, str(caught.exception))
                self.assertNotIn(REFRESH, str(caught.exception))


class PhotoCacheFileTests(unittest.TestCase):
    def test_the_cached_copy_is_a_normalised_png_under_a_private_path(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = photo.cache_path(root, USER)
            self.assertEqual(path, root / 'profile-photo' / (USER + '.png'))
            # Only normalize() output is kept: the metadata chunk from the source never reaches the disk.
            self.assertTrue(photo.write_cached(root, USER, png(metadata=True)))
            self.assertEqual(photo.read_cached(root, USER), photo.normalize(png()))
            self.assertNotIn(b'private metadata', path.read_bytes())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            # A subject that isn't a UUID can't name a file.
            self.assertFalse(photo.write_cached(root, '../escape', png()))
            self.assertIsNone(photo.read_cached(root, '../escape'))
            # Negative control: clearing it leaves nothing to read.
            self.assertTrue(photo.clear_cached(root, USER))
            self.assertFalse(path.exists())
            self.assertIsNone(photo.read_cached(root, USER))
            # A symlink at the path is never followed, even to a valid photo.
            target = root / 'elsewhere.png'
            target.write_bytes(photo.normalize(png()))
            path.symlink_to(target)
            self.assertIsNone(photo.read_cached(root, USER))
            path.unlink()
            # Oversized and garbage files read as no cache.
            path.write_bytes(photo.normalize(png()) + b'\0' * photo.MAX_PNG)
            self.assertIsNone(photo.read_cached(root, USER))
            path.write_bytes(b'not a png at all')
            self.assertIsNone(photo.read_cached(root, USER))
            # A garbage photo is refused on write too, and the old file isn't replaced by it.
            self.assertTrue(photo.write_cached(root, USER, png()))
            self.assertFalse(photo.write_cached(root, USER, b'not a png at all'))
            self.assertEqual(photo.read_cached(root, USER), photo.normalize(png()))

    def test_the_cache_works_where_os_has_no_o_nofollow(self):
        # Windows' os has no O_NOFOLLOW. Build #7 read it here, so every start after signing in raised AttributeError,
        # account_config.create fell back to UnavailableAccount and each task said "Dervo account setup needs attention".
        import tempfile
        from pathlib import Path
        saved = os.__dict__.pop('O_NOFOLLOW', None)
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                self.assertIsNone(photo.read_cached(root, USER), 'nothing cached yet, and no error')
                self.assertTrue(photo.write_cached(root, USER, png()))
                self.assertEqual(photo.read_cached(root, USER), photo.normalize(png()))
        finally:
            if saved is not None:
                os.O_NOFOLLOW = saved

    def test_a_linked_photo_folder_is_never_written_through(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root, outside = Path(folder) / 'data', Path(folder) / 'outside'
            root.mkdir()
            outside.mkdir()
            (root / 'profile-photo').symlink_to(outside)
            self.assertFalse(photo.write_cached(root, USER, png()))
            self.assertEqual(list(outside.iterdir()), [])
            # Negative control: with a real folder the same write lands.
            (root / 'profile-photo').unlink()
            self.assertTrue(photo.write_cached(root, USER, png()))


class PhotoLifecycleTests(AccountTests):
    def test_photo_is_read_back_and_cleared_on_signout_without_sqlite_storage(self):
        self.signin()
        raw = png()
        with patch('account_photo.request', side_effect=[None, raw]) as request:
            self.call('save_photo', subject=USER, png=base64.b64encode(raw).decode())
            state = self.settle()
        self.assertEqual([c.args[2] for c in request.call_args_list], ['POST', 'GET'])
        self.assertEqual(state['photo'], photo.status(raw))
        self.assertNotIn(base64.b64encode(raw).decode(), '\n'.join(self.host.store.db.iterdump()))
        self.call('sign_out');self.settle()
        self.assertIsNone(self.account.status()['photo'])

    def test_photo_failure_doesnt_sign_out_verified_account(self):
        self.signin()
        with patch('account_photo.request', side_effect=AccountError('photo_service')):
            self.call('photo', subject=USER)
            state = self.settle()
        self.assertEqual(state['state'], 'signed_in')
        self.assertEqual(state['photo']['state'], 'error')

    def test_stale_photo_readback_doesnt_confirm_a_save_or_removal(self):
        self.signin()
        chosen = png()
        stale = png(pixels=(b'\0' + b'\0' * 512) * 128)
        for operation, args in [('save_photo', {'subject': USER, 'png': base64.b64encode(chosen).decode()}),
                                ('remove_photo', {'subject': USER})]:
            with patch('account_photo.request', side_effect=[None, stale]):
                self.call(operation, **args)
                state = self.settle()
            self.assertEqual(state['state'], 'signed_in')
            self.assertEqual(state['photo']['state'], 'error')

    def test_changed_vault_subject_cannot_update_photo_or_name(self):
        for operation, args in [('save_photo', {'subject': USER, 'png': base64.b64encode(png()).decode()}),
                                ('save_profile', {'displayName': 'Must not save'})]:
            self.signin()
            self.account.vault.save(Session(ACCESS, REFRESH, OTHER, self.now + 1000))
            self.client.calls.clear()
            with patch('account_photo.request') as request:
                self.call(operation, **args)
                self.settle()
            request.assert_not_called()
            self.assertNotIn('save_name', self.client.calls)
            self.assertEqual(self.account.status()['state'], 'unknown')
            self.call('sign_out');self.settle()


if __name__ == '__main__':
    unittest.main()
