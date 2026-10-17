"""Voice model downloads (dervo_voice/setup.py): checked against pinned hashes, unpacked safely, never half-installed.
No network: a fake opener serves local bytes."""
import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dervo_voice import setup


def tarball(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:bz2') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def opener_for(served):
    def opener(url, timeout=None):
        assert timeout, 'every download has a timeout'
        return io.BytesIO(served[url])
    return opener


class Setup(unittest.TestCase):
    def setUp(self):
        self.target = tempfile.mkdtemp()
        self.events = []
        self.single = b'onnx bytes'
        self.archive = tarball({'model-dir/model.onnx': b'weights', 'model-dir/test_wavs/a.wav': b'x'})
        self.models = [
            {'name': 'vad', 'what': 'VAD', 'licence': 'MIT', 'url': 'https://x/vad.onnx', 'file': 'vad.onnx',
             'sha256': hashlib.sha256(self.single).hexdigest(), 'bytes': len(self.single)},
            {'name': 'asr', 'what': 'ASR', 'licence': 'CC-BY-4.0', 'url': 'https://x/asr.tar.bz2', 'folder': 'model-dir',
             'sha256': hashlib.sha256(self.archive).hexdigest(), 'bytes': len(self.archive)},
        ]
        self.served = {'https://x/vad.onnx': self.single, 'https://x/asr.tar.bz2': self.archive}

    def install(self, **kwargs):
        with mock.patch.object(setup, 'MODELS', self.models):
            setup.install(self.target, self.events.append, opener=opener_for(self.served), **kwargs)

    def test_installs_checked_files_with_their_licences(self):
        self.install()
        self.assertEqual((Path(self.target) / 'vad.onnx').read_bytes(), self.single)
        self.assertEqual((Path(self.target) / 'model-dir' / 'model.onnx').read_bytes(), b'weights')
        self.assertIn('CC-BY-4.0', (Path(self.target) / 'model-dir' / 'LICENCE.txt').read_text())
        self.assertFalse((Path(self.target) / 'model-dir' / 'test_wavs').exists())
        self.assertTrue((Path(self.target) / 'LICENCES.json').exists())
        self.assertEqual([e['state'] for e in self.events if e['name'] == 'asr'][-1], 'ready')

    def test_a_download_bigger_than_pinned_is_stopped(self):
        self.served['https://x/vad.onnx'] = self.single * 3
        with self.assertRaises(ValueError) as caught:
            self.install()
        self.assertIn('bigger than expected', str(caught.exception))
        self.assertFalse((Path(self.target) / 'vad.onnx').exists())

    def test_a_cut_off_download_left_behind_is_cleared(self):
        stale = Path(self.target) / '.setup-abc123'
        stale.mkdir()
        (stale / 'download').write_bytes(b'half')
        self.install()
        self.assertFalse(stale.exists())

    def test_a_bad_checksum_installs_nothing(self):
        self.served['https://x/asr.tar.bz2'] = self.archive + b'tampered'
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((Path(self.target) / 'model-dir').exists())
        self.assertEqual([p for p in os.listdir(self.target) if p.startswith('.setup-')], [], 'no half-done folders left')
        self.assertEqual(self.events[-1]['state'], 'failed')

    def test_an_archive_reaching_outside_is_refused(self):
        self.archive = tarball({'../escape.txt': b'x', 'model-dir/model.onnx': b'w'})
        self.models[1]['sha256'] = hashlib.sha256(self.archive).hexdigest()
        self.served['https://x/asr.tar.bz2'] = self.archive
        with self.assertRaises(Exception):
            self.install()
        self.assertFalse((Path(self.target).parent / 'escape.txt').exists())

    def test_installed_models_are_not_fetched_again(self):
        self.install()
        self.served.clear()  # any fetch would now fail
        self.events.clear()
        self.install()
        self.assertTrue(all(e.get('already') for e in self.events))

    def test_pinned_manifest_is_complete(self):
        names = {model['name'] for model in setup.MODELS}
        self.assertEqual(names, {'vad', 'turn', 'wake', 'asr', 'tts'})
        for model in setup.MODELS:
            self.assertRegex(model['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(model['url'].startswith('https://'))
            self.assertTrue(model['licence'])
        self.assertLess(setup.TOTAL_BYTES, 900 * 1024 * 1024)


if __name__ == '__main__':
    unittest.main()
