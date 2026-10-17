"""release_signature.check: the host asks the running app to verify a release's signature. No network or real keys."""
import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import release_signature as module
from beta_download import DownloadError
from test_beta_updates import record

RESOURCES = Path('/Applications/Dervo Local.app/Contents/Resources')
REPO = Path(__file__).resolve().parent.parent


def runner(answer, code=0):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, json.loads(kwargs['input']), kwargs))
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(returncode=code, stdout=(answer if isinstance(answer, bytes) else json.dumps(answer).encode()))
    return run, calls


VERIFIED = {'verified': True, 'releaseId': record()['release_id'], 'sha256': record()['sha256']}


class Check(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'darwin':
            # The Mac app's layout everywhere, so these check the request and answers; platform_os.app_executable has
            # its own Windows and Linux tests (test_windows_update, test_platform_os).
            mac = RESOURCES.parent / 'MacOS' / 'dervo-local'
            self.enterContext(patch.object(module.platform_os, 'app_executable', lambda resources: mac))

    def test_a_verified_answer_about_this_release_passes(self):
        run, calls = runner(VERIFIED)
        self.assertIsNone(module.check(RESOURCES, record(), installing=True, run=run))
        argv, request, kwargs = calls[0]
        self.assertEqual(argv, ['/Applications/Dervo Local.app/Contents/MacOS/dervo-local', '--verify-release-signature'])
        self.assertEqual(request, {'releaseId': '0.1.0-prebeta.100', 'buildNumber': '1789452916913747', 'productVersion': '0.1.0',
                                   'sha256': 'a' * 64, 'signature': 'e' * 128})
        self.assertEqual(kwargs['env'], {})

    def test_an_unsigned_release_is_refused_without_asking(self):
        run, calls = runner(VERIFIED)
        for installing in (True, False):
            with self.assertRaises(DownloadError) as caught:
                module.check(RESOURCES, record(signature=None), installing=installing, run=run)
            self.assertEqual(str(caught.exception), module.REFUSED[installing])
        self.assertEqual(calls, [])

    def test_anything_but_a_matching_verified_answer_is_refused(self):
        for answer, code in [({'verified': False, 'reason': 'bad_signature'}, 1), ({'verified': False, 'reason': 'unsigned'}, 1),
                             ({**VERIFIED, 'sha256': 'b' * 64}, 0), ({**VERIFIED, 'releaseId': 'other'}, 0),
                             (VERIFIED, 1), (b'not json', 0), (b'', 0), (OSError('missing'), 0),
                             (subprocess.TimeoutExpired('dervo-local', 30), 0)]:
            with self.subTest(answer=answer, code=code):
                run, _ = runner(answer, code)
                with self.assertRaisesRegex(DownloadError, 'isn’t signed by Dervo'):
                    module.check(RESOURCES, record(), installing=True, run=run)

    def test_an_app_without_a_key_says_so(self):
        run, _ = runner({'verified': False, 'reason': 'no_key'}, 1)
        with self.assertRaisesRegex(DownloadError, 'can’t check update signatures'):
            module.check(RESOURCES, record(), installing=True, run=run)


@unittest.skipUnless((REPO / 'target/debug/dervo-local').is_file(), 'needs a debug build of the app: cargo build')
class RealExecutable(unittest.TestCase):
    """The flag runs the real verifier and exits without opening a window. The debug build's key file decides the answer."""
    def test_the_app_answers_the_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            contents = Path(folder) / 'Contents'
            (contents / 'MacOS').mkdir(parents=True)
            (contents / 'Resources').mkdir()
            (contents / 'MacOS/dervo-local').symlink_to(REPO / 'target/debug/dervo-local')
            with self.assertRaises(DownloadError) as caught:
                module.check(contents / 'Resources', record(), installing=True)
            key = (REPO / 'src/release-signing-key.hex').read_text().strip()
            # A fixture signature never verifies: with no key compiled in the app says so, with one it refuses.
            self.assertEqual(str(caught.exception), module.REFUSED[True] if key else module.NO_KEY)


if __name__ == '__main__':
    unittest.main()
