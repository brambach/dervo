"""Runtime relocation, tampering and consistent provider launch regressions."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import codex_runtime as runtime
from dervo_connections import Rejected


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='dervo runtime ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'codex'
        self.binary.write_bytes(b'fixture executable')
        self.binary.chmod(0o755)
        self.digest = hashlib.sha256(self.binary.read_bytes()).hexdigest()

    def test_modified_binary_invalidates_successful_check(self):
        self.assertEqual(runtime.verified_binary(self.binary, self.digest), str(self.binary))
        self.binary.write_bytes(b'changed executable')
        with self.assertRaisesRegex(Rejected, 'runtime has changed'):
            runtime.verified_binary(self.binary, self.digest)

    def test_the_hash_streams_instead_of_reading_the_whole_binary(self):
        # Efficiency review, 23 Sep: both Codex binaries were read whole to hash them, a 300 MB spike on first use.
        import tracemalloc
        large = self.root / 'large-codex'
        chunk = bytes(range(256)) * 4096  # 1 MiB
        with large.open('wb') as out:
            for _ in range(24):
                out.write(chunk)
        large.chmod(0o755)
        digest = hashlib.sha256(chunk * 24).hexdigest()
        runtime._verified.cache_clear()
        tracemalloc.start()
        try:
            self.assertEqual(runtime.verified_binary(large, digest), str(large))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 4 * 1024 * 1024, f'hashing a 24 MiB binary peaked at {peak // 1024} KiB')

    def test_missing_nonexecutable_and_symlink_refuse(self):
        for kind in ('missing', 'nonexecutable', 'symlink'):
            path = self.root / kind
            if kind == 'nonexecutable':
                path.write_bytes(b'fixture')
                path.chmod(0o644)
            elif kind == 'symlink':
                path.symlink_to(self.binary)
            with self.subTest(kind=kind), self.assertRaises(Rejected):
                runtime.verified_binary(path, self.digest)

    def test_missing_bundle_never_uses_system_cli(self):
        with patch.object(runtime, 'BINARY', self.root / 'missing'), patch.dict('os.environ', {'PATH': '/usr/local/bin'}):
            with self.assertRaisesRegex(Rejected, 'missing its bundled'):
                runtime.executable()

    def test_missing_or_changed_tool_host_blocks_before_provider_launch(self):
        for path in (self.root / 'missing-host', self.binary):
            with self.subTest(path=path), patch.object(runtime, 'HOST', path):
                with self.assertRaises(Rejected):
                    runtime.executable()

    def test_packaged_location_survives_relocation(self):
        backend = self.root / 'App With Spaces' / 'backend'
        backend.mkdir(parents=True)
        shutil.copy2(runtime.__file__, backend / 'codex_runtime.py')
        shutil.copy2(Path(runtime.__file__).with_name('platform_os.py'), backend / 'platform_os.py')  # picks the Mac or Windows binary
        (backend / 'dervo_connections.py').write_text('class Rejected(Exception): pass\n')
        expected = backend.parent / 'runtime' / 'codex'
        if sys.platform.startswith('linux'):  # Linux reads its pin from runtime/ and keeps its own layout
            (backend.parent / 'runtime').mkdir()
            shutil.copy2(Path(runtime.__file__).parents[1] / 'runtime' / 'codex-linux-x64.json', backend.parent / 'runtime')
            expected = backend.parent / 'runtime' / 'codex-linux' / 'bin' / 'codex'
        result = subprocess.run([sys.executable, '-I', '-B', '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); import codex_runtime; print(codex_runtime.BINARY)', str(backend)],
            text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), str(expected.resolve()))

    def test_login_chat_and_coding_use_same_verified_binary(self):
        import codex_policy
        import codex_coding_policy
        from connections_ui import instructions
        import shlex
        selected = codex_policy.executable()
        self.assertEqual(codex_policy.argv()[0], selected)
        self.assertEqual(codex_policy.login_argv(selected)[0], selected)
        self.assertEqual(codex_coding_policy.argv()[0], selected)
        profile = dict(provider='codex', home=str(self.root), identity='fixture@example.test')
        self.assertIn(selected, shlex.split(instructions(profile)['command']))
        config = json.loads((Path(__file__).resolve().parents[1] / 'tauri.conf.json').read_text())
        self.assertIn('runtime/*', config['bundle']['resources'])


class WindowsRuntimeTests(unittest.TestCase):
    """Codex on Windows (b16/windows): the official win32-x64 package, pinned in runtime/codex-win32-x64.json, staged into
    runtime/codex-win by scripts/stage-windows-codex.py and packaged by tauri.windows.conf.json."""
    ROOT = Path(__file__).resolve().parent.parent

    def load_as_windows(self):
        """The module's paths and pins as a Windows host computes them, then the module back as this computer's."""
        import importlib
        import platform_os
        from types import SimpleNamespace
        try:
            with patch.object(platform_os, 'WINDOWS', True):
                windows = importlib.reload(runtime)
                return SimpleNamespace(**{name: getattr(windows, name) for name in ('BINARY', 'BINARY_SHA256', 'HOST', 'HOST_SHA256')})
        finally:
            importlib.reload(runtime)

    def test_windows_uses_the_pinned_exe_and_the_same_version_as_the_mac(self):
        pin = json.loads((self.ROOT / 'runtime' / 'codex-win32-x64.json').read_text())
        windows = self.load_as_windows()
        self.assertEqual(windows.BINARY, self.ROOT / 'runtime' / 'codex-win' / 'bin' / 'codex.exe')
        self.assertEqual(windows.HOST, self.ROOT / 'runtime' / 'codex-win' / 'bin' / 'codex-code-mode-host.exe')
        self.assertEqual(windows.BINARY_SHA256, pin['files']['bin/codex.exe'])
        self.assertEqual(windows.HOST_SHA256, pin['files']['bin/codex-code-mode-host.exe'])
        self.assertEqual(pin['version'], runtime.VERSION)
        self.assertTrue(pin['tarball'].startswith('https://registry.npmjs.org/@openai/codex/-/'))
        # Negative control: this computer keeps its own binary and pins.
        own = 'codex-linux/bin/codex' if sys.platform.startswith('linux') else 'codex'
        self.assertEqual(runtime.BINARY, self.ROOT / 'runtime' / own)

    def test_the_installer_takes_the_staged_folder_and_git_never_does(self):
        config = json.loads((self.ROOT / 'tauri.windows.conf.json').read_text())
        self.assertIn('runtime/codex-win/**/*', config['bundle']['resources'])
        self.assertIn('runtime/codex-win/', (self.ROOT / '.gitignore').read_text().splitlines())

    def test_staging_refuses_a_file_that_differs_from_its_pin(self):
        import importlib.util
        import io
        import tarfile
        spec = importlib.util.spec_from_file_location('stage_windows_codex', self.ROOT / 'scripts' / 'stage-windows-codex.py')
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            info = tarfile.TarInfo('package/vendor/x/bin/codex.exe')
            info.size = 4
            tar.addfile(info, io.BytesIO(b'good'))
        pin = {'folder': 'staged', 'archive_root': 'package/vendor/x/', 'files': {'bin/codex.exe': hashlib.sha256(b'good').hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            staged = stage.stage(pin, buffer.getvalue(), root=Path(folder))
            self.assertEqual((staged / 'bin' / 'codex.exe').read_bytes(), b'good')
            with self.assertRaises(SystemExit):
                stage.stage(dict(pin, files={'bin/codex.exe': '0' * 64}), buffer.getvalue(), root=Path(folder))


if __name__ == '__main__':
    unittest.main()
