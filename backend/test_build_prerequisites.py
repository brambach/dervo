"""Tests for scripts/check-build-prerequisites.py.

Everything here runs against temporary fixture roots and fake executable files.
Nothing touches the installed Node, Rust, Python framework or any account.
The fake executables are never run; the preflight only inspects them.
"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check-build-prerequisites.py'
ENTRYPOINTS = (
    'frontend/node_modules/typescript/bin/tsc',
    'frontend/node_modules/vite/bin/vite.js',
    'frontend/node_modules/@tauri-apps/cli/tauri.js',
)
TOOLS = ('node', 'cargo', 'python3')
REQUIRED_IDS = ('root', 'platform', 'codesign', 'node', 'cargo', 'python3',
                'frontend_typescript', 'frontend_vite', 'frontend_tauri_cli', 'python_runtime_pin', 'python_runtime')
INFO_IDS = ('python_313_framework',)
IS_ROOT_USER = hasattr(os, 'geteuid') and os.geteuid() == 0
MODULE_NAME = 'check_build_prerequisites'
MISSING = object()


def load_module():
    """Exec the hyphenated script as a module without writing a scripts/__pycache__.

    The module is registered in sys.modules while its body runs because Python 3.13
    dataclasses resolve string annotations through sys.modules[cls.__module__].
    Any prior entry under that name is preserved and restored afterwards.
    """
    source = SCRIPT.read_text(encoding='utf-8')
    module = types.ModuleType(MODULE_NAME)
    module.__file__ = str(SCRIPT)
    previous = sys.modules.get(MODULE_NAME, MISSING)
    sys.modules[MODULE_NAME] = module
    try:
        exec(compile(source, str(SCRIPT), 'exec'), module.__dict__)
    finally:
        if previous is MISSING:
            sys.modules.pop(MODULE_NAME, None)
        else:
            sys.modules[MODULE_NAME] = previous
    return module


def make_root(base, name='dervo root with spaces', entrypoints=ENTRYPOINTS):
    root = Path(base) / name
    (root / 'backend').mkdir(parents=True)
    (root / 'frontend').mkdir()
    (root / 'build.sh').write_text('#!/bin/sh\nexit 0\n')
    (root / 'Cargo.toml').write_text('[package]\nname = "fixture"\n')
    for rel in entrypoints:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('// fixture entry point\n')
    for rel in ('runtime/python.json', 'scripts/python_runtime.py'):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text('{}\n')
    return root


def make_executable(directory, name, mode=0o755):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(mode)
    return path


def by_id(report, check_id):
    matches = [c for c in report['checks'] if c['id'] == check_id]
    assert len(matches) == 1, (check_id, [c['id'] for c in report['checks']])
    return matches[0]


class PreflightFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert SCRIPT.is_file(), f'missing script: {SCRIPT}'
        cls.mod = load_module()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dervo-preflight-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.bin = self.tmp / 'tool bin'
        self.bin.mkdir()
        self.tools = {name: make_executable(self.bin, name) for name in TOOLS}
        self.codesign = make_executable(self.bin, 'codesign')
        self.sandbox_exec = make_executable(self.bin, 'sandbox-exec')
        self.framework = self.tmp / 'Python.framework' / 'Versions' / '3.13'
        self.framework.mkdir(parents=True)
        # The sandbox interpreter pin is a different file from the python3 build.sh would use.
        self.sandbox_python = make_executable(self.framework / 'bin', 'python3')
        self.root = make_root(self.tmp)

    def facts(self, **overrides):
        base = dict(
            root=self.root,
            platform='darwin',
            machine='arm64',
            python_version=(3, 13, 0),
            python_executable=str(self.tools['python3']),
            python_prefix=str(self.framework),
            path_dirs=[str(self.bin)],
            framework_prefix=self.framework,
            sandbox_python=self.sandbox_python,
            sandbox_exec=self.sandbox_exec,
            codesign=self.codesign,
        )
        base.update(overrides)
        return self.mod.Facts(**base)

    def report(self, **overrides):
        return self.mod.build_report(self.facts(**overrides))

    def restore_mode(self, path, mode):
        self.addCleanup(lambda: os.chmod(path, mode))


class ReadyPath(PreflightFixture):
    def test_everything_present_is_ready(self):
        report = self.report()
        self.assertTrue(report['ready'], report)
        for check_id in REQUIRED_IDS + INFO_IDS:
            self.assertEqual(by_id(report, check_id)['status'], 'ready', by_id(report, check_id))
        for name in TOOLS:
            self.assertEqual(by_id(report, name)['path'], str(self.tools[name]))
        self.assertEqual(report['root'], str(self.root))
        self.assertIn(' ', report['root'], 'fixture root must exercise a path with spaces')

    def test_check_set_matches_current_build_sh_contract(self):
        report = self.report()
        self.assertEqual({c['id'] for c in report['checks']}, set(REQUIRED_IDS + INFO_IDS))
        self.assertEqual({c['id'] for c in report['checks'] if c['required']}, set(REQUIRED_IDS))
        # build.sh no longer replaces PATH or uses a sibling toolchain; the preflight must not describe either.
        text = json.dumps(report).lower()
        self.assertNotIn('sibling', text)
        self.assertNotIn('replaces path', text)
        self.assertNotIn('native/.rustup', text)

    def test_ready_meaning_is_limited_to_preflight(self):
        report = self.report()
        meaning = report['meaning'].lower()
        self.assertIn('preflight', meaning)
        self.assertIn('not', meaning)
        joined = ' '.join(report['not_checked']).lower()
        for topic in ('build', 'provider', 'model', 'portab', 'codesign', 'version', 'rust'):
            self.assertIn(topic, joined, topic)

    def test_json_is_serialisable_and_statuses_are_explicit(self):
        report = self.report()
        json.dumps(report)
        for check in report['checks']:
            self.assertIn(check['status'], ('ready', 'missing', 'unsupported'), check)
            self.assertIsInstance(check['required'], bool, check)
            self.assertTrue(check['detail'], check)
        self.assertEqual(report['facts']['override_names']['environment'], ['DERVO_NODE', 'DERVO_CARGO', 'DERVO_PYTHON'])

    def test_exit_status_tracks_required_checks(self):
        self.assertEqual(self.mod.exit_status(self.report()), 0)
        self.assertEqual(self.mod.exit_status(self.report(platform='linux')), 1)

    def test_preflight_never_imports_subprocess_or_runs_tools(self):
        source = SCRIPT.read_text(encoding='utf-8')
        for forbidden in ('import subprocess', 'os.system', 'os.popen', 'os.exec', 'os.spawn'):
            self.assertNotIn(forbidden, source, forbidden)


class Overrides(PreflightFixture):
    def other_dir(self):
        other = self.tmp / 'other tools'
        other.mkdir(exist_ok=True)
        return other

    def test_flag_override_is_used_and_reported(self):
        node = make_executable(self.other_dir(), 'node')
        check = by_id(self.report(node_override=self.mod.Override(str(node), '--node')), 'node')
        self.assertEqual(check['status'], 'ready')
        self.assertEqual(check['path'], str(node))
        self.assertIn('--node', check['detail'])

    def test_python_override_flag_and_variable(self):
        python = make_executable(self.other_dir(), 'python3')
        for source in ('--python', 'DERVO_PYTHON'):
            check = by_id(self.report(python_override=self.mod.Override(str(python), source)), 'python3')
            self.assertEqual(check['status'], 'ready', check)
            self.assertEqual(check['path'], str(python))
            self.assertIn(source, check['detail'])

    def test_missing_override_fails_without_path_fallback(self):
        for name, variable in (('node', 'DERVO_NODE'), ('cargo', 'DERVO_CARGO'), ('python3', 'DERVO_PYTHON')):
            missing = self.tmp / 'nowhere' / name
            check = by_id(self.report(**{f"{name.rstrip('3')}_override": self.mod.Override(str(missing), variable)}), name)
            self.assertEqual(check['status'], 'missing', check)
            self.assertNotEqual(check.get('path'), str(self.tools[name]), 'must not fall back to PATH candidate')
            self.assertIn(variable, check['detail'])
            self.assertIn('fallback', check['detail'].lower())

    def test_non_executable_override_fails(self):
        plain = make_executable(self.tmp, 'node', mode=0o644)
        check = by_id(self.report(cargo_override=self.mod.Override(str(plain), '--cargo')), 'cargo')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('executable', check['detail'].lower())

    def test_directory_override_fails(self):
        check = by_id(self.report(node_override=self.mod.Override(str(self.bin), '--node')), 'node')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('directory', check['detail'].lower())

    def test_empty_override_is_invalid_not_unset(self):
        check = by_id(self.report(python_override=self.mod.Override('', 'DERVO_PYTHON')), 'python3')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('empty', check['detail'].lower())

    def test_symlink_override_is_accepted_and_resolved_target_reported(self):
        link = self.tmp / 'link to node'
        link.symlink_to(self.tools['node'])
        check = by_id(self.report(node_override=self.mod.Override(str(link), '--node')), 'node')
        self.assertEqual(check['status'], 'ready')
        self.assertEqual(check['path'], str(link))
        self.assertEqual(Path(check['resolved']), self.tools['node'].resolve())

    def test_dangling_symlink_override_fails(self):
        link = self.tmp / 'dangling'
        link.symlink_to(self.tmp / 'gone')
        check = by_id(self.report(node_override=self.mod.Override(str(link), '--node')), 'node')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('symlink', check['detail'].lower())

    def test_from_system_reads_only_named_environment_keys(self):
        environ = {
            'PATH': os.pathsep.join([str(self.bin), '', 'relative/bin']),
            'DERVO_CARGO': str(self.tools['cargo']),
            'DERVO_PYTHON': str(self.tools['python3']),
            'ANTHROPIC_API_KEY': 'synthetic-secret-value',
            'OPENAI_API_KEY': 'synthetic-secret-value',
        }
        facts = self.mod.Facts.from_system(self.root, node=str(self.tools['node']), cargo=None, python=None, environ=environ)
        self.assertEqual(facts.node_override, self.mod.Override(str(self.tools['node']), '--node'))
        self.assertEqual(facts.cargo_override, self.mod.Override(str(self.tools['cargo']), 'DERVO_CARGO'))
        self.assertEqual(facts.python_override, self.mod.Override(str(self.tools['python3']), 'DERVO_PYTHON'))
        self.assertEqual(facts.path_dirs, [str(self.bin), '', 'relative/bin'])
        report = self.mod.build_report(facts)
        rendered = json.dumps(report) + self.mod.render_text(report)
        self.assertNotIn('synthetic-secret-value', rendered)

    def test_flag_beats_environment_override(self):
        facts = self.mod.Facts.from_system(
            self.root, node=None, cargo=None, python=str(self.tools['python3']),
            environ={'PATH': str(self.bin), 'DERVO_PYTHON': str(self.tmp / 'ignored')})
        self.assertEqual(facts.python_override.source, '--python')
        self.assertEqual(facts.python_override.value, str(self.tools['python3']))


class PathSearch(PreflightFixture):
    def test_first_executable_candidate_wins(self):
        second = self.tmp / 'second bin'
        second.mkdir()
        make_executable(second, 'node')
        check = by_id(self.report(path_dirs=[str(second), str(self.bin)]), 'node')
        self.assertEqual(check['path'], str(second / 'node'))
        self.assertIn('PATH', check['detail'])

    def test_non_executable_relative_and_empty_entries_are_skipped(self):
        plain_dir = self.tmp / 'plain'
        plain_dir.mkdir()
        make_executable(plain_dir, 'node', mode=0o644)
        check = by_id(self.report(path_dirs=['', 'relative/bin', str(plain_dir), str(self.bin)]), 'node')
        self.assertEqual(check['status'], 'ready')
        self.assertEqual(check['path'], str(self.tools['node']))

    def test_nothing_on_path_is_missing_for_every_tool(self):
        empty = self.tmp / 'empty'
        empty.mkdir()
        report = self.report(path_dirs=[str(empty)])
        for name in TOOLS:
            check = by_id(report, name)
            self.assertEqual(check['status'], 'missing', check)
            self.assertIn(self.mod.ENV_OVERRIDES[name], check['detail'])
        self.assertFalse(report['ready'])

    def test_symlinked_path_candidate_is_accepted(self):
        linked = self.tmp / 'linked bin'
        linked.mkdir()
        (linked / 'node').symlink_to(self.tools['node'])
        check = by_id(self.report(path_dirs=[str(linked)]), 'node')
        self.assertEqual(check['status'], 'ready')
        self.assertEqual(Path(check['resolved']), self.tools['node'].resolve())


class PythonRuntimePin(PreflightFixture):
    def test_present_pin_and_stager_are_ready_without_fetching(self):
        check = by_id(self.report(), 'python_runtime_pin')
        self.assertEqual(check['status'], 'ready')
        self.assertTrue(check['required'])
        self.assertIn('Not fetched or verified here', check['detail'])

    def test_missing_pin_or_stager_blocks_ready(self):
        for rel in ('runtime/python.json', 'scripts/python_runtime.py'):
            with self.subTest(rel=rel):
                root = make_root(self.tmp, name='pin ' + rel.replace('/', ' '))
                (root / rel).unlink()
                report = self.report(root=root)
                self.assertFalse(report['ready'])
                check = by_id(report, 'python_runtime_pin')
                self.assertEqual(check['status'], 'missing')
                self.assertIn(rel, check['detail'])


class FrontendEntrypoints(PreflightFixture):
    def test_missing_entrypoints_are_reported_individually(self):
        (self.root / 'frontend/node_modules/vite/bin/vite.js').unlink()
        report = self.report()
        self.assertFalse(report['ready'])
        self.assertEqual(by_id(report, 'frontend_vite')['status'], 'missing')
        self.assertEqual(by_id(report, 'frontend_typescript')['status'], 'ready')
        self.assertEqual(by_id(report, 'frontend_tauri_cli')['status'], 'ready')
        self.assertIn('node_modules/vite/bin/vite.js', by_id(report, 'frontend_vite')['path'])

    def test_no_node_modules_at_all(self):
        root = make_root(self.tmp, name='bare root', entrypoints=())
        report = self.report(root=root)
        for check_id in ('frontend_typescript', 'frontend_vite', 'frontend_tauri_cli'):
            self.assertEqual(by_id(report, check_id)['status'], 'missing', check_id)
            self.assertTrue(by_id(report, check_id)['required'])


class RootHandling(PreflightFixture):
    def test_missing_root_is_missing_and_not_ready(self):
        report = self.report(root=self.tmp / 'does not exist')
        self.assertEqual(by_id(report, 'root')['status'], 'missing')
        self.assertFalse(report['ready'])

    def test_root_without_build_sh_is_missing(self):
        (self.root / 'build.sh').unlink()
        check = by_id(self.report(), 'root')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('build.sh', check['detail'])

    @unittest.skipIf(IS_ROOT_USER, 'permission bits do not apply to root')
    def test_read_only_root_is_checked_and_nothing_is_written(self):
        before = sorted(str(p) for p in self.root.rglob('*'))
        os.chmod(self.root, 0o500)
        self.restore_mode(self.root, 0o700)
        report = self.report()
        self.assertTrue(report['ready'], report)
        self.assertEqual(sorted(str(p) for p in self.root.rglob('*')), before)

    @unittest.skipIf(IS_ROOT_USER, 'permission bits do not apply to root')
    def test_unreadable_root_is_reported_not_raised(self):
        os.chmod(self.root, 0o000)
        self.restore_mode(self.root, 0o700)
        report = self.report()
        self.assertEqual(by_id(report, 'root')['status'], 'missing')
        self.assertIn('readable', by_id(report, 'root')['detail'].lower())
        self.assertFalse(report['ready'])

    def test_root_is_reported_absolute(self):
        self.assertTrue(Path(self.report()['root']).is_absolute())

    def test_directory_named_build_sh_is_not_accepted(self):
        (self.root / 'build.sh').unlink()
        (self.root / 'build.sh').mkdir()
        check = by_id(self.report(), 'root')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('build.sh', check['detail'])
        self.assertIn('file', check['detail'].lower())

    def test_directory_named_cargo_toml_is_not_accepted(self):
        (self.root / 'Cargo.toml').unlink()
        (self.root / 'Cargo.toml').mkdir()
        check = by_id(self.report(), 'root')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('Cargo.toml', check['detail'])

    def test_regular_file_named_frontend_is_not_accepted(self):
        root = make_root(self.tmp, name='file frontend root', entrypoints=())
        (root / 'frontend').rmdir()
        (root / 'frontend').write_text('not a directory\n')
        check = by_id(self.report(root=root), 'root')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('frontend', check['detail'])
        self.assertIn('directory', check['detail'].lower())

    def test_regular_file_named_backend_is_not_accepted(self):
        root = make_root(self.tmp, name='file backend root', entrypoints=())
        (root / 'backend').rmdir()
        (root / 'backend').write_text('not a directory\n')
        check = by_id(self.report(root=root), 'root')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('backend', check['detail'])

    def test_correct_kinds_remain_ready(self):
        self.assertEqual(by_id(self.report(), 'root')['status'], 'ready')


class LoaderLifecycle(unittest.TestCase):
    def test_module_not_left_registered_when_absent_before(self):
        self.assertNotIn(MODULE_NAME, sys.modules)
        module = load_module()
        self.assertNotIn(MODULE_NAME, sys.modules)
        self.assertTrue(hasattr(module, 'Facts'))
        module.Check('x', 'x', True, 'ready', 'dataclass usable after load')

    def test_prior_entry_is_preserved(self):
        sentinel = types.ModuleType(MODULE_NAME)
        sys.modules[MODULE_NAME] = sentinel
        self.addCleanup(sys.modules.pop, MODULE_NAME, None)
        load_module()
        self.assertIs(sys.modules[MODULE_NAME], sentinel)


class PlatformCodesignAndPython(PreflightFixture):
    def test_non_darwin_platform_is_unsupported(self):
        report = self.report(platform='linux', machine='x86_64')
        self.assertEqual(by_id(report, 'platform')['status'], 'unsupported')
        self.assertTrue(by_id(report, 'platform')['required'])
        self.assertEqual(by_id(report, 'codesign')['status'], 'unsupported')
        self.assertFalse(report['ready'])

    def test_intel_mac_is_reported_unsupported_not_claimed(self):
        check = by_id(self.report(machine='x86_64'), 'platform')
        self.assertEqual(check['status'], 'unsupported')
        self.assertIn('x86_64', check['detail'])
        self.assertIn('exercised', check['detail'].lower())

    def test_codesign_presence_is_required(self):
        check = by_id(self.report(), 'codesign')
        self.assertEqual(check['status'], 'ready')
        self.assertTrue(check['required'])
        self.assertEqual(check['path'], str(self.codesign))
        report = self.report(codesign=self.tmp / 'no codesign')
        self.assertEqual(by_id(report, 'codesign')['status'], 'missing')
        self.assertFalse(report['ready'])

    def test_python_runtime_facts_are_reported(self):
        check = by_id(self.report(), 'python_runtime')
        self.assertEqual(check['status'], 'ready')
        self.assertIn('3.13.0', check['detail'])
        self.assertIn(str(self.tools['python3']), check['detail'])
        self.assertIn('DERVO_PYTHON', check['detail'])
        facts = self.report()['facts']
        self.assertEqual(facts['python_version'], '3.13.0')
        self.assertEqual(facts['platform'], 'darwin')
        self.assertEqual(facts['machine'], 'arm64')

    def test_old_python_is_unsupported(self):
        check = by_id(self.report(python_version=(3, 7, 9)), 'python_runtime')
        self.assertEqual(check['status'], 'unsupported')

    def test_framework_check_is_separate_and_not_required(self):
        check = by_id(self.report(), 'python_313_framework')
        self.assertEqual(check['status'], 'ready')
        self.assertFalse(check['required'])
        self.assertIn('3.13', check['detail'])
        self.assertIn('portab', check['detail'].lower())
        self.assertIn('DERVO_PYTHON', check['detail'])
        self.assertIn(str(self.sandbox_python), check['detail'])

    def test_framework_pin_is_independent_of_python3_tool(self):
        """A missing sandbox interpreter must not be hidden by a valid DERVO_PYTHON, and vice versa."""
        self.sandbox_python.unlink()
        report = self.report(python_override=self.mod.Override(str(self.tools['python3']), 'DERVO_PYTHON'))
        self.assertEqual(by_id(report, 'python3')['status'], 'ready')
        self.assertEqual(by_id(report, 'python_313_framework')['status'], 'missing')
        self.assertIn('pinned sandbox interpreter', by_id(report, 'python_313_framework')['detail'])
        self.assertTrue(report['ready'], 'framework check is informational, must not gate ready')

    def test_framework_missing_pieces_are_listed(self):
        self.sandbox_exec.unlink()
        report = self.report(framework_prefix=self.tmp / 'no framework')
        check = by_id(report, 'python_313_framework')
        self.assertEqual(check['status'], 'missing')
        self.assertIn('sandbox-exec', check['detail'])
        self.assertIn('no framework', check['detail'])
        self.assertTrue(report['ready'])

    def test_framework_check_unsupported_off_macos(self):
        check = by_id(self.report(platform='linux'), 'python_313_framework')
        self.assertEqual(check['status'], 'unsupported')

    def test_framework_check_notes_when_running_interpreter_differs(self):
        check = by_id(self.report(python_prefix='/elsewhere/python'), 'python_313_framework')
        self.assertEqual(check['status'], 'ready')
        self.assertIn('/elsewhere/python', check['detail'])


class Rendering(PreflightFixture):
    def test_text_output_lists_every_check_with_status(self):
        report = self.report(path_dirs=[])
        text = self.mod.render_text(report)
        for check in report['checks']:
            self.assertIn(check['id'], text)
            self.assertIn(f"[{check['status']}]", text)
        self.assertIn('NOT READY', text)
        self.assertIn('preflight', text.lower())
        self.assertIn('Not checked', text)

    def test_text_output_ready_wording(self):
        text = self.mod.render_text(self.report())
        self.assertIn('READY', text)
        self.assertNotIn('NOT READY', text)

    def test_main_json_mode_writes_only_json(self):
        out, err = io.StringIO(), io.StringIO()
        code = self.mod.main(
            ['--json', '--node', str(self.tools['node']), '--cargo', str(self.tools['cargo']),
             '--python', str(self.tools['python3']), str(self.root)],
            environ={'PATH': ''}, stdout=out, stderr=err)
        report = json.loads(out.getvalue())
        self.assertEqual(report['schema'], self.mod.SCHEMA)
        for name in TOOLS:
            self.assertEqual(by_id(report, name)['path'], str(self.tools[name]))
        # Platform facts come from the real interpreter in main(); only the injected parts are asserted.
        self.assertEqual(code, self.mod.exit_status(report))

    def test_main_rejects_missing_override_even_with_path_candidate(self):
        out = io.StringIO()
        code = self.mod.main(['--json', '--python', str(self.tmp / 'absent python'), str(self.root)],
                             environ={'PATH': str(self.bin)}, stdout=out, stderr=io.StringIO())
        report = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(by_id(report, 'python3')['status'], 'missing')


class SubprocessInvocation(unittest.TestCase):
    """Runs the script the way the coordinator will, with a synthetic root and empty PATH."""

    def run_script(self, *args, env=None):
        return subprocess.run(
            [sys.executable, '-I', '-B', str(SCRIPT), *args],
            capture_output=True, text=True, timeout=30, env=env or {'PATH': ''})

    def test_help_documents_override_names_and_symlink_policy(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for token in ('--node', '--cargo', '--python', 'DERVO_NODE', 'DERVO_CARGO', 'DERVO_PYTHON', '--json', 'symlink'):
            self.assertIn(token, result.stdout, token)

    def test_missing_prerequisites_root_exits_nonzero_with_json(self):
        with tempfile.TemporaryDirectory(prefix='dervo-preflight-sub-') as tmp:
            root = Path(tmp) / 'synthetic root'
            root.mkdir()
            empty = Path(tmp) / 'empty path'
            empty.mkdir()
            result = self.run_script('--json', str(root), env={'PATH': str(empty)})
            self.assertEqual(result.returncode, 1, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report['ready'])
            for check_id in ('root', 'node', 'cargo', 'python3', 'frontend_typescript', 'frontend_vite', 'frontend_tauri_cli'):
                self.assertEqual(by_id(report, check_id)['status'], 'missing', check_id)
            self.assertEqual(sorted(os.listdir(root)), [], 'preflight must not write into the root')

    def test_missing_prerequisites_root_text_mode(self):
        with tempfile.TemporaryDirectory(prefix='dervo-preflight-sub-') as tmp:
            root = Path(tmp) / 'synthetic root'
            root.mkdir()
            result = self.run_script(str(root), env={'PATH': ''})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('NOT READY', result.stdout)
            self.assertIn('[missing]', result.stdout)

    def test_nonexistent_root_argument_exits_nonzero(self):
        with tempfile.TemporaryDirectory(prefix='dervo-preflight-sub-') as tmp:
            result = self.run_script('--json', str(Path(tmp) / 'no such root'))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(by_id(json.loads(result.stdout), 'root')['status'], 'missing')


if __name__ == '__main__':
    unittest.main()
