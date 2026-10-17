"""scripts/python_runtime.py: the pin, the download cache and staging into a built app (PLAN-beta17 section 4).

Fixture archives and a fake opener stand in for the network. The one test that stages the real pinned runtime runs only
when its archive is already in the build download cache, and is skipped otherwise. Nothing is installed.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('python_runtime', ROOT / 'scripts/python_runtime.py')
runtime = importlib.util.module_from_spec(SPEC)
_writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no scripts/__pycache__ (as test_build_prerequisites)
SPEC.loader.exec_module(runtime)
sys.dont_write_bytecode = _writes
MACH_O = b'\xcf\xfa\xed\xfe' + b'\0' * 28


def archive(files, links=()):
    """A .tar.gz of {path: bytes} plus (path, target) symlinks, as bytes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return buffer.getvalue()


class Opener:
    """Serves one body for any URL and counts the requests."""
    def __init__(self, body):
        self.body, self.calls = body, 0

    def __call__(self, url):
        self.calls += 1
        return io.BytesIO(self.body)


class Fixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.cache = self.root / 'cache'
        self.app = self.root / 'Dervo Local.app'
        (self.app / 'Contents/Resources/runtime').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_bytes(b'')
        self.files = {'python/bin/python3.13': MACH_O, 'python/bin/pip3': b'#!/bin/sh\n',
                      'python/lib/python3.13/os.py': b'', 'python/lib/libtcl9.0.dylib': MACH_O,
                      'python/include/Python.h': b''}
        self.links = [('python/bin/python3', 'python3.13')]

    def pin(self, body, **changes):
        return {'version': '3.13.16', 'build': '20261001', 'platform': 'aarch64-apple-darwin',
                'url': 'https://example.test/cpython-3.13.16%2B20261001-fixture.tar.gz', 'size': len(body),
                'sha256': hashlib.sha256(body).hexdigest(), 'archive_root': 'python', 'executable': 'bin/python3.13',
                'mach_o': ['bin/python3.13'],
                'remove': ['bin/pip3', 'bin/python3', 'lib/libtcl9.0.dylib', 'include'], **changes}

    def fake_python(self, version='3.13.16', prefix=None, smoke_code=0):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            if '-c' in argv:
                facts = {'version': version, 'prefix': str(prefix or self.app / runtime.BUNDLED),
                         'base_prefix': '', 'sqlite': '3', 'openssl': 'OpenSSL'}
                return subprocess.CompletedProcess(argv, smoke_code, json.dumps(facts), '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        return run, calls


class PinTests(Fixture):
    def test_the_committed_pin_is_valid_and_names_its_licence(self):
        pin = runtime.read_pin(ROOT / 'runtime/python.json')
        self.assertRegex(pin['version'], r'^3\.13\.\d+$')
        self.assertTrue(pin['url'].startswith('https://github.com/astral-sh/python-build-standalone/releases/download/'))
        self.assertEqual(pin['mach_o'], [pin['executable']])
        self.assertTrue((ROOT / 'runtime' / pin['licence']).is_file())
        self.assertIn('Python Software Foundation', (ROOT / 'runtime' / pin['licence']).read_text())

    def test_bad_pins_are_refused(self):
        body = b'x'
        for change in ({'version': '3.12.9'}, {'url': 'http://example.test/a.tar.gz'}, {'sha256': 'abc'},
                       {'size': 0}, {'remove': ['../outside']}, {'remove': ['/abs']}, {'mach_o': ['bin/other']},
                       {'executable': 'bin/other'}):
            path = self.root / 'pin.json'
            path.write_text(json.dumps(self.pin(body, **change)))
            with self.subTest(change=change), self.assertRaises(runtime.StageError):
                runtime.read_pin(path)
        with self.assertRaisesRegex(runtime.StageError, 'missing'):
            runtime.read_pin(self.root / 'absent.json')


class FetchTests(Fixture):
    def test_downloads_once_then_uses_the_checked_cache(self):
        body = archive(self.files)
        opener = Opener(body)
        first = runtime.fetch(self.pin(body), self.cache, opener)
        second = runtime.fetch(self.pin(body), self.cache, opener)
        self.assertEqual((first, opener.calls), (second, 1))
        self.assertEqual(first.read_bytes(), body)
        self.assertTrue(first.name.startswith(hashlib.sha256(body).hexdigest()))

    def test_a_mismatched_download_is_never_kept(self):
        body = archive(self.files)
        for served in (body + b'extra', b'y' * len(body)):
            with self.subTest(size=len(served)), self.assertRaises(runtime.StageError):
                runtime.fetch(self.pin(body), self.cache, Opener(served))
        self.assertEqual(sorted(p.name for p in self.cache.iterdir()), [])

    def test_a_damaged_cache_file_is_replaced_by_a_checked_download(self):
        body = archive(self.files)
        target = runtime.fetch(self.pin(body), self.cache, Opener(body))
        target.write_bytes(b'damaged')
        opener = Opener(body)
        self.assertEqual(runtime.fetch(self.pin(body), self.cache, opener).read_bytes(), body)
        self.assertEqual(opener.calls, 1)

    def test_a_linked_cache_is_refused(self):
        real = self.root / 'real cache'
        real.mkdir()
        (self.root / 'linked cache').symlink_to(real)
        with self.assertRaisesRegex(runtime.StageError, 'linked'):
            runtime.fetch(self.pin(b'x'), self.root / 'linked cache', Opener(b'x'))


class StageTests(Fixture):
    def stage(self, body, pin=None, **fake):
        run, calls = self.fake_python(**fake)
        return runtime.stage(self.app, pin or self.pin(body), self.cache, Opener(body), run=run), calls

    def test_trims_checks_precompiles_and_records_the_runtime(self):
        body = archive(self.files, self.links)
        record, calls = self.stage(body)
        python = self.app / runtime.BUNDLED / 'bin/python3.13'
        self.assertEqual(sorted(str(p.relative_to(self.app / runtime.BUNDLED)) for p in (self.app / runtime.BUNDLED).rglob('*')),
                         ['bin', 'bin/python3.13', 'lib', 'lib/python3.13', 'lib/python3.13/os.py'])
        self.assertEqual(record['executable'], 'Contents/Resources/runtime/python/bin/python3.13')
        self.assertEqual(record['mach_o'], ['Contents/Resources/runtime/python/bin/python3.13'])
        self.assertEqual(record['version'], '3.13.16')
        compile_call, smoke_call = calls
        self.assertEqual(compile_call[:6], [str(python), '-E', '-s', '-I', '-m', 'compileall'])
        self.assertIn('unchecked-hash', compile_call)
        self.assertEqual(smoke_call[:4], [str(python), '-E', '-s', '-B'])
        self.assertEqual([p.name for p in (self.app / 'Contents/Resources/runtime').iterdir()], ['python'],
                         'no unpacking folder is left behind')

    def test_leftover_links_or_unlisted_mach_o_files_are_refused(self):
        body = archive(self.files, self.links)
        for pin, message in ((self.pin(body, remove=['bin/pip3', 'lib/libtcl9.0.dylib', 'include']), 'links'),
                             (self.pin(body, remove=['bin/pip3', 'bin/python3', 'include']), 'Mach-O')):
            with self.subTest(message=message), self.assertRaisesRegex(runtime.StageError, message):
                self.stage(body, pin)
            self.assertFalse((self.app / runtime.BUNDLED).exists())

    def test_a_remove_entry_missing_from_the_archive_is_refused(self):
        body = archive(self.files, self.links)
        with self.assertRaisesRegex(runtime.StageError, 'isn.t in the archive'):
            self.stage(body, self.pin(body, remove=self.pin(body)['remove'] + ['lib/tk9.0']))

    def test_an_archive_that_escapes_its_folder_is_refused(self):
        body = archive({**self.files, '../escape.txt': b'x'})
        with self.assertRaises(tarfile.TarError):
            self.stage(body)
        self.assertFalse((self.app / 'Contents/Resources/escape.txt').exists())
        body = archive(self.files, [('python/lib/outside', '/etc/hosts')])
        with self.assertRaises(tarfile.TarError):
            self.stage(body)

    def test_a_runtime_that_reports_another_version_or_place_is_refused(self):
        body = archive(self.files, self.links)
        with self.assertRaisesRegex(runtime.StageError, '3.13.15'):
            self.stage(body, version='3.13.15')

    def test_staging_twice_or_into_something_else_is_refused(self):
        body = archive(self.files, self.links)
        self.stage(body)
        with self.assertRaisesRegex(runtime.StageError, 'already exists'):
            self.stage(body)
        with self.assertRaisesRegex(runtime.StageError, 'built app'):
            runtime.stage(self.root / 'cache', self.pin(body), self.cache, Opener(body), run=self.fake_python()[0])

    def test_the_command_line_reports_problems_without_a_traceback(self):
        missing = runtime.main(['stage', '--app', str(self.app), '--pin', str(self.root / 'absent.json')])
        self.assertEqual(missing, 1)


@unittest.skipUnless(sys.platform == 'darwin', 'the pinned runtime is for macOS on Apple silicon')
class PinnedRuntimeTests(unittest.TestCase):
    """Stages the real pinned archive when it's already in the build download cache (a build puts it there)."""
    def test_the_pinned_runtime_starts_from_inside_an_app(self):
        pin = runtime.read_pin(ROOT / 'runtime/python.json')
        cached = [p for p in runtime.CACHE.glob(pin['sha256'] + '-*')] if runtime.CACHE.is_dir() else []
        if not cached:
            self.skipTest('the pinned archive isn\'t in ' + str(runtime.CACHE) + ' yet; build.sh or the managed build fetches it')
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp).resolve() / 'Dervo Local.app'
            (app / 'Contents/Resources/runtime').mkdir(parents=True)
            (app / 'Contents/Info.plist').write_bytes(b'')
            record = runtime.stage(app, pin, runtime.CACHE, opener=lambda url: self.fail('no download expected'))
            self.assertEqual(record['version'], pin['version'])
            python = app / record['executable']
            done = subprocess.run([str(python), '-E', '-s', '-B', '-c', 'import sys; print(sys.prefix)'],
                                  capture_output=True, text=True, timeout=60, env={})
            self.assertEqual(Path(done.stdout.strip()), app / runtime.BUNDLED)


if __name__ == '__main__':
    unittest.main()
