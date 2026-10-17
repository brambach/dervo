"""Bounded profile PNGs and authenticated access to the owner's private object."""
import base64
import binascii
import json
import os
from pathlib import Path
import struct
import zlib
from urllib.error import HTTPError
from urllib.request import Request

import platform_os
from supabase_client import AccountError, _uuid

SIZE = 128
MAX_PNG = 80 * 1024
SIGNATURE = b'\x89PNG\r\n\x1a\n'


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)


def normalize(raw):
    """Accept only a small decoded raster; discard metadata and trailing content."""
    if type(raw) is not bytes or not 57 <= len(raw) <= MAX_PNG or not raw.startswith(SIGNATURE):
        raise AccountError('photo')
    position, header, compressed, ended = 8, None, bytearray(), False
    while position < len(raw):
        if position + 12 > len(raw):
            raise AccountError('photo')
        length = struct.unpack('>I', raw[position:position+4])[0]
        kind = raw[position+4:position+8]
        end = position + 12 + length
        if end > len(raw):
            raise AccountError('photo')
        data = raw[position+8:position+8+length]
        crc = struct.unpack('>I', raw[end-4:end])[0]
        if crc != binascii.crc32(kind + data) & 0xffffffff:
            raise AccountError('photo')
        if header is None and kind != b'IHDR':
            raise AccountError('photo')
        if kind == b'IHDR':
            if header is not None or length != 13:
                raise AccountError('photo')
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
            if (width, height, depth, compression, filtering, interlace) != (SIZE, SIZE, 8, 0, 0, 0) or color not in (2, 6):
                raise AccountError('photo')
            header = data
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            if length or end != len(raw):
                raise AccountError('photo')
            ended = True
            break
        elif not kind[0] & 32:  # Unknown critical chunks aren't an accepted raster.
            raise AccountError('photo')
        position = end
    if not ended or not compressed:
        raise AccountError('photo')
    stride = SIZE * (4 if header[9] == 6 else 3) + 1
    expected = SIZE * stride
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected + 1)
        if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError()
        if any(pixels[row * stride] > 4 for row in range(SIZE)):
            raise ValueError()
    except (ValueError, zlib.error):
        raise AccountError('photo') from None
    return SIGNATURE + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def decode(value):
    if type(value) is not str or len(value) > 110000:
        raise AccountError('photo')
    try:
        return normalize(base64.b64decode(value, validate=True))
    except (ValueError, binascii.Error):
        raise AccountError('photo') from None


def status(raw=None, state=None):
    # 'cached' is the copy kept on this Mac, shown at once but not yet confirmed by the account server.
    state = state or ('ready' if raw else 'missing')
    return {'state': state, 'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode() if raw else None,
            'checked': state != 'cached'}


# The last confirmed photo, kept in the app's data folder so an update or a slow fetch never shows initials instead.
# Only normalize() output is written: no data URL, no token, nothing from the server but the checked raster.
CACHE_FOLDER = 'profile-photo'


def cache_path(root, subject):
    return Path(root) / CACHE_FOLDER / (_uuid(subject) + '.png')


def read_cached(root, subject):
    """The cached PNG, or None for anything missing, linked, oversized or not an accepted raster."""
    try:
        path = cache_path(root, subject)
        if path.parent.is_symlink() or path.is_symlink():
            return None
        # Windows has no O_NOFOLLOW (platform_os.NOFOLLOW is 0 there); the is_symlink checks above still refuse a link.
        descriptor = os.open(path, os.O_RDONLY | platform_os.NOFOLLOW | getattr(os, 'O_BINARY', 0))
        with os.fdopen(descriptor, 'rb') as handle:
            if os.fstat(handle.fileno()).st_size > MAX_PNG:
                return None
            raw = handle.read(MAX_PNG + 1)
        return normalize(raw) if len(raw) <= MAX_PNG else None
    except (OSError, AccountError):
        return None


def write_cached(root, subject, raw):
    """Keep a confirmed photo. A failed write only means the next launch fetches it again."""
    try:
        clean = normalize(raw)
        path = cache_path(root, subject)
        if Path(root).is_symlink() or path.parent.is_symlink():
            return False
        path.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(path.parent, 0o700)
        temporary = path.with_name(path.name + '.tmp')
        if temporary.is_symlink() or temporary.exists():
            temporary.unlink()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | platform_os.NOFOLLOW | getattr(os, 'O_BINARY', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(clean)
        os.replace(temporary, path)
        return True
    except (OSError, AccountError):
        return False


def clear_cached(root, subject):
    try:
        path = cache_path(root, subject)
        if path.parent.is_symlink():
            return False
        path.unlink(missing_ok=True)
        return True
    except (OSError, AccountError):
        return False


def request(client, session, method, raw=None):
    user = _uuid(session.user_id)
    path = '/storage/v1/object/' + ('authenticated/' if method == 'GET' else '') + 'dervo-avatars/' + user + '/profile.png'
    headers = {'apikey': client.project.publishable_key,
               'Authorization': 'Bearer ' + client._token(session.access_token), 'Cache-Control': 'no-cache'}
    if method == 'DELETE':
        path = '/storage/v1/object/dervo-avatars'
        raw = json.dumps({'prefixes': [user + '/profile.png']}).encode()
        headers['Content-Type'] = 'application/json'
    elif raw is not None:
        raw = normalize(raw)
        headers.update({'Content-Type': 'image/png', 'x-upsert': 'true', 'Cache-Control': 'no-cache'})
    query = Request(client.project.url + path, data=raw, headers=headers, method=method)
    try:
        with client.opener.open(query, timeout=15) as response:
            if not 200 <= response.status < 300:
                raise AccountError('photo_service')
            body = response.read(MAX_PNG + 1)
            if len(body) > MAX_PNG:
                raise AccountError('photo_service')
            return normalize(body) if method == 'GET' else None
    except HTTPError as error:
        # Storage can express a missing object as HTTP 400 with statusCode 404.
        missing = error.code == 404
        if error.code == 400 and method in ('GET', 'DELETE'):
            try:
                body = error.read(4096)
                data = json.loads(body)
                missing = data.get('statusCode') in ('404', 404) or data.get('code') == 'NoSuchKey'
            except Exception:
                pass
        error.close()
        if missing and method in ('GET', 'DELETE'):
            return None
        raise AccountError('photo_service') from None
    except AccountError:
        raise
    except Exception:
        raise AccountError('photo_service') from None
