"""Check a downloaded release's signature before it's unpacked or saved.

The release key's public half is compiled into the running app (src/release_signature.rs), so the check runs there:
this app's own executable, as ``dervo-local --verify-release-signature``, with the release fields on stdin. A release
record with no signature, a signature over different values, or one from another key is refused, and the current
version keeps running. The SHA-256 and bundle checks stay as they are; this is one more check, not a replacement.
"""
import json
import subprocess

from beta_download import DownloadError
import platform_os

FLAG = '--verify-release-signature'
REFUSED = {True: 'This update isn’t signed by Dervo. It wasn’t installed.',
           False: 'This installer isn’t signed by Dervo. It wasn’t saved.'}
NO_KEY = ('This Dervo can’t check update signatures, so it won’t install one. '
          'Download the new version from the beta page and install it by hand.')


def executable(resources):
    """This app's own executable: Contents/Resources/../MacOS/dervo-local on a Mac, the exe beside the resources on
    Windows (platform_os.app_executable)."""
    return platform_os.app_executable(resources)


def check(resources, release, *, installing, run=subprocess.run):
    """Raise DownloadError unless the running app verifies ``release``'s signature. Never returns anything else."""
    refused = REFUSED[installing]
    if not release.get('signature'):
        raise DownloadError(refused)
    request = {'releaseId': release['release_id'], 'buildNumber': str(release['build_number']),
               'productVersion': release['product_version'], 'sha256': release['sha256'], 'signature': release['signature']}
    try:
        app = executable(resources)
    except RuntimeError:
        raise DownloadError(refused) from None
    try:
        result = run([str(app), FLAG], input=json.dumps(request).encode(), capture_output=True,
                     timeout=30, env={}, check=False)
        answer = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise DownloadError(refused) from None
    if type(answer) is dict and answer.get('reason') == 'no_key':
        raise DownloadError(NO_KEY)
    if result.returncode != 0 or answer != {'verified': True, 'releaseId': request['releaseId'], 'sha256': request['sha256']}:
        raise DownloadError(refused)
