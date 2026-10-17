"""scripts/voice_runtime.py: voice's pinned libraries and audio helper, staged into a built app.

Fixture wheels and a fake opener stand in for the network, and a stub stands in for the Swift build. Nothing is
installed.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
_writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no scripts/__pycache__ (as test_python_runtime)
SPEC = importlib.util.spec_from_file_location('voice_runtime', ROOT / 'scripts/voice_runtime.py')
voice_runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(voice_runtime)
sys.dont_write_bytecode = _writes
MACH_O = b'\xcf\xfa\xed\xfe' + b'\0' * 28


def wheel(files, links=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 << 16)
            archive.writestr(info, 'elsewhere')
    return buffer.getvalue()


class Staging(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.app = self.folder / 'Dervo Local.app'
        (self.app / 'Contents/Resources').mkdir(parents=True)
        (self.app / 'Contents/Info.plist').write_text('<plist/>')
        python = self.app / voice_runtime.PYTHON  # stands in for the app's Python; run_stub answers for it
        python.parent.mkdir(parents=True)
        python.write_bytes(MACH_O)
        self.cache = self.folder / 'cache'
        self.served = {
            'https://files.pythonhosted.org/a/lib-1.0.whl': wheel({
                'lib/__init__.py': b'', 'lib/core.so': MACH_O, 'lib/tests/test_x.py': b'', 'lib-1.0.dist-info/LICENSE': b'MIT'}),
        }
        self.pin = self.pin_for(self.served)
        self.built = []

    def pin_for(self, served, remove=('lib/tests',), mach_o=('lib/core.so',)):
        return {'wheels': [{'name': 'lib', 'version': '1.0', 'licence': 'MIT', 'url': url, 'size': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()} for url, data in served.items()],
                'remove': list(remove), 'mach_o': list(mach_o)}

    def opener(self, url):
        return io.BytesIO(self.served[url])

    def run_stub(self, command, **kwargs):
        # The Swift build: writes a Mach-O where build.sh would.
        self.built.append(command)
        if command[-2].endswith('build.sh'):
            Path(command[-1], 'dervo-voice-audio').write_bytes(MACH_O)
        if '-c' in command:  # the import check
            return subprocess.CompletedProcess(command, 0, json.dumps({'numpy': '1', 'onnxruntime': '1', 'outside': []}), '')
        return subprocess.CompletedProcess(command, 0, '', '')

    def stage(self, **kwargs):
        return voice_runtime.stage(self.app, self.pin, self.cache, opener=self.opener, run=self.run_stub, **kwargs)

    def test_libraries_and_helper_land_in_the_app_trimmed_and_listed(self):
        record = self.stage()
        site = self.app / voice_runtime.SITE
        self.assertTrue((site / 'lib/core.so').is_file())
        self.assertFalse((site / 'lib/tests').exists(), 'tests are trimmed')
        self.assertEqual((site / 'lib-1.0.dist-info/LICENSE').read_bytes(), b'MIT', 'licences stay')
        self.assertTrue((self.app / voice_runtime.HELPER).is_file())
        self.assertEqual(record['mach_o'], [voice_runtime.SITE + '/lib/core.so'])
        self.assertEqual(record['helper'], voice_runtime.HELPER)
        self.assertFalse(list((self.app / voice_runtime.VOICE).glob('.voice-*')), 'no work folder left behind')
        self.assertEqual(record['imports']['outside'], [])

    def test_libraries_imported_from_outside_the_app_stop_the_build(self):
        def outside(command, **kwargs):
            if '-c' in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({'numpy': '1', 'onnxruntime': '1', 'outside': ['/usr/lib/numpy']}), '')
            return self.run_stub(command, **kwargs)
        with self.assertRaises(voice_runtime.python_runtime.StageError) as caught:
            voice_runtime.stage(self.app, self.pin, self.cache, opener=self.opener, run=outside)
        self.assertIn('outside the app', str(caught.exception))

    def test_a_tampered_wheel_is_never_used(self):
        self.served['https://files.pythonhosted.org/a/lib-1.0.whl'] += b'x'
        with self.assertRaises(voice_runtime.python_runtime.StageError):
            self.stage()
        self.assertFalse((self.app / voice_runtime.SITE).exists())

    def test_an_unexpected_binary_stops_the_build(self):
        self.pin = self.pin_for(self.served, mach_o=())
        with self.assertRaises(voice_runtime.python_runtime.StageError) as caught:
            self.stage()
        self.assertIn('differ from runtime/voice.json', str(caught.exception))

    def test_a_link_inside_a_wheel_is_refused(self):
        self.served = {'https://files.pythonhosted.org/a/lib-1.0.whl': wheel({'lib/core.so': MACH_O}, links=('lib/link',))}
        self.pin = self.pin_for(self.served, remove=())
        with self.assertRaises(voice_runtime.python_runtime.StageError):
            self.stage()

    def test_staging_twice_is_refused(self):
        self.stage()
        with self.assertRaises(voice_runtime.python_runtime.StageError):
            self.stage()

    def test_the_real_pin_is_well_formed_and_from_pypi(self):
        pin = voice_runtime.read_pin()
        self.assertEqual(sorted(wheel['name'] for wheel in pin['wheels']), ['numpy', 'onnxruntime', 'sherpa-onnx', 'sherpa-onnx-core'])
        self.assertTrue(all(wheel['licence'] for wheel in pin['wheels']))
        self.assertTrue(all('macosx' in wheel['url'] and 'arm64' in wheel['url'] for wheel in pin['wheels']))
        self.assertLess(sum(wheel['size'] for wheel in pin['wheels']), 45 * 1024 * 1024, 'the download cost stays as documented')


if __name__ == '__main__':
    unittest.main()
