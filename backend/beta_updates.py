"""Private-beta release reads and short-lived download links, owned by the host.

No publication, installation, account mutation or token refresh. The account
worker supplies its current session. Never return SignedDownload to the renderer
or record its URL in logs; the native opening action must consume it directly.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import re
from urllib.parse import urlsplit, parse_qsl

import platform_os
from release_profiles import read as release_profile
from supabase_client import AccountError

FIELDS = ('release_id', 'build_number', 'product_version', 'channel', 'platform',
          'title', 'notes', 'format', 'object_name', 'download_origin', 'sha256', 'bytes', 'published_at', 'signature')
SELECT = ','.join(FIELDS)
BUCKET = 'dervo-beta-releases'
# Admin previews (Bryce, 24 Sep 2026): preview builds sit on their own channel, which the catalog's policy shows to
# admins only (supabase/migrations/202609240001_dervo_admin_previews.sql). The app asks for both and gets what it may
# read, so a tester's newest is always a numbered beta and an admin's can be a preview.
CHANNELS = ('private-beta', 'admin-preview')
LINK_SECONDS = 120
# What each computer may install (supabase/migrations/202609260001_dervo_windows_releases.sql): its platform's rows only,
# in its formats. A Windows row's release id ends in -windows and a Mac row's never does (Bryce, 26 Sep 2026).
FORMATS = {'macos-arm64': ('zip', 'dmg'), 'windows-x64': ('exe',), 'linux-x64': ('appimage', 'deb', 'rpm')}
WINDOWS_ID = '-windows'
# Linux (supabase/migrations/202610080001_dervo_linux_releases.sql): one row per package format, each with its own id
# ending in -linux-<format> and its own build (scripts/linux-release.py), and an installed Dervo reads only its own
# format's rows, so a .deb install is offered the next .deb.
LINUX_ID = '-linux-'


def this_platform():
    return 'windows-x64' if platform_os.WINDOWS else 'linux-x64' if platform_os.LINUX else 'macos-arm64'


def linux_format(environ=None, dpkg_list='/var/lib/dpkg/info/dervo-local.list'):
    """Which package this Linux Dervo was installed from: an AppImage says so in $APPIMAGE, dpkg lists a .deb's files,
    and anything else came from the .rpm."""
    environ = os.environ if environ is None else environ
    if environ.get('APPIMAGE'):
        return 'appimage'
    return 'deb' if os.path.isfile(dpkg_list) else 'rpm'


@dataclass(frozen=True, repr=False)
class SignedDownload:
    url: str = field(repr=False)
    release: dict
    expires_at: float


def _id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value):
        raise AccountError('invalid_response')
    return value


def _build(value):
    # PostgREST returns numeric fields as numbers. Compare exact integers only.
    if type(value) is not int or not 0 < value < 10 ** 20:
        raise AccountError('invalid_response')
    return value


def _record(data, now, platform):
    if type(data) is not dict or set(data) != set(FIELDS):
        raise AccountError('invalid_response')
    _id(data['release_id'])
    _build(data['build_number'])
    if (type(data['product_version']) is not str
            or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', data['product_version'])
            or data['channel'] not in CHANNELS or data['platform'] != platform
            or data['format'] not in FORMATS[platform]
            or data['release_id'].endswith(WINDOWS_ID) != (platform == 'windows-x64')
            or (LINUX_ID in data['release_id']) != (platform == 'linux-x64')
            or platform == 'linux-x64' and not data['release_id'].endswith(LINUX_ID + data['format'])
            or data['object_name'] != data['release_id'] + '/Dervo-' + platform + '.' + data['format']
            or type(data['download_origin']) is not str
            or not re.fullmatch(r'https://[a-f0-9]{32}\.r2\.cloudflarestorage\.com', data['download_origin'])
            or type(data['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', data['sha256'])
            or type(data['bytes']) is not int or not 0 < data['bytes'] <= 2147483648
            # The release key's Ed25519 signature (release_signature.py). Unsigned rows are readable and never installed.
            or data['signature'] is not None and (type(data['signature']) is not str
                                                  or not re.fullmatch('[0-9a-f]{128}', data['signature']))):
        raise AccountError('invalid_response')
    for key, limit in [('title', 120), ('notes', 4000)]:
        value = data[key]
        if (type(value) is not str or not 1 <= len(value) <= limit or not value.strip()
                or any(ord(c) < 32 and c not in '\n\t' for c in value)):
            raise AccountError('invalid_response')
    try:
        published = datetime.fromisoformat(data['published_at'].replace('Z', '+00:00'))
        if published.tzinfo is None or published.timestamp() > now:
            raise ValueError()
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise AccountError('invalid_response') from None
    return dict(data)


def _access(client, session, resources):
    if release_profile(resources)['profile'] != 'private-beta':
        raise AccountError('configuration')
    if session.expires_at <= client.clock():
        raise AccountError('expired')
    if not client.access(session)['allowed']:
        raise AccountError('rejected')


def _rows(client, session, suffix, platform=None):
    result = client._request('GET', '/rest/v1/dervo_beta_releases?select=' + SELECT + suffix,
                             access_token=session.access_token)
    if type(result) is not list or len(result) > 1:
        raise AccountError('invalid_response')
    return _record(result[0], client.clock(), platform or this_platform()) if result else None


def latest(client, session, resources, current_build, platform=None):
    """Read fresh membership and the newest published build for this computer. No link is created."""
    platform = platform or this_platform()
    _build(current_build)
    _access(client, session, resources)
    only = '&format=eq.' + linux_format() if platform == 'linux-x64' else ''
    release = _rows(client, session, '&channel=in.(' + ','.join(CHANNELS) + ')&platform=eq.' + platform + only
                    + '&order=build_number.desc&limit=1', platform)
    return {'state': 'unpublished' if release is None else
                    'available' if release['build_number'] > current_build else 'current',
            'release': release}


def download(client, session, resources, release_id, platform=None):
    """Recheck membership and the exact release before creating a private link.

    A signed link is a bearer capability until expiry. Revoking membership or
    withdrawing metadata prevents new links, but doesn't revoke an issued link.
    """
    _id(release_id)
    _access(client, session, resources)
    release = _rows(client, session, '&release_id=eq.' + release_id + '&limit=1', platform)
    if release is None or release['release_id'] != release_id:
        raise AccountError('rejected')
    started = client.clock()
    result = client._request('POST', '/functions/v1/dervo-beta-download',
                             {'releaseId': release_id, 'sha256': release['sha256']}, session.access_token)
    if (type(result) is not dict or set(result) != {'releaseId', 'sha256', 'bytes', 'url', 'expiresAt'}
            or result['releaseId'] != release_id or result['sha256'] != release['sha256']
            or type(result['bytes']) is not int or result['bytes'] != release['bytes']
            or type(result['expiresAt']) is not int
            or not client.clock() < result['expiresAt'] <= client.clock() + LINK_SECONDS + 5):
        raise AccountError('invalid_response')
    url = result['url']
    if type(url) is not str or len(url) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise AccountError('invalid_response')
    expected = release['download_origin'] + '/' + BUCKET + '/' + release['object_name']
    try:
        parsed = urlsplit(url)
        pairs = parse_qsl(parsed.query, strict_parsing=True)
        query = dict(pairs)
        required = {'X-Amz-Algorithm', 'X-Amz-Credential', 'X-Amz-Date', 'X-Amz-Expires',
                    'X-Amz-SignedHeaders', 'X-Amz-Signature', 'response-content-disposition'}
        if (url.split('?', 1)[0] != expected or parsed.fragment or len(pairs) != len(query)
                or set(query) != required or query['X-Amz-Algorithm'] != 'AWS4-HMAC-SHA256'
                or query['X-Amz-SignedHeaders'] != 'host' or query['X-Amz-Expires'] != str(LINK_SECONDS)
                or not re.fullmatch('[a-f0-9]{64}', query['X-Amz-Signature'])
                or not re.fullmatch(r'[a-f0-9]{32}/[0-9]{8}/auto/s3/aws4_request', query['X-Amz-Credential'])
                or query['response-content-disposition'] != 'attachment; filename="Dervo-' + release_id + '.' + release['format'] + '"'):
            raise ValueError()
        signed_at = datetime.strptime(query['X-Amz-Date'], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        if (signed_at > client.clock() + 5 or signed_at + LINK_SECONDS < result['expiresAt']
                or signed_at + LINK_SECONDS <= client.clock()
                or query['X-Amz-Credential'].split('/')[1] != query['X-Amz-Date'][:8]):
            raise ValueError()
    except (TypeError, ValueError, OverflowError):
        raise AccountError('invalid_response') from None
    return SignedDownload(url, release, min(result['expiresAt'], started + LINK_SECONDS))
