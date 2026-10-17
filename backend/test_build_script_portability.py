"""Behavioural regression tests for build.sh tool discovery and ordering.

A copy of build.sh runs inside a temporary root whose name contains spaces.
node, cargo and python3 are stub shell scripts that append what they were asked
to do to a log and exit 0. No real build, provider, browser or network is used.

The final step of build.sh signs the staged bundle with /usr/bin/codesign at a
fixed system path. The stubs don't produce a bundle, so a complete run is
expected to stop there with a nonzero exit. These tests assert the ordered
invocations before that point and that nothing runs after it.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

APP_ROOT = Path(__file__).resolve().parents[1]
BUILD_SH = APP_ROOT / 'build.sh'
STUBS = ('node', 'cargo', 'python3')

STUB_SOURCE = """#!/bin/sh
{
printf 'name=%s\\n' '{name}'
printf 'self=%s\\n' "$0"
printf 'cwd=%s\\n' "$PWD"
for a in "$@"; do printf 'arg=%s\\n' "$a"; done
printf 'RUSTUP_HOME=%s\\n' "${RUSTUP_HOME-<unset>}"
printf 'CARGO_HOME=%s\\n' "${CARGO_HOME-<unset>}"
printf 'PATH=%s\\n' "$PATH"
printf 'PLAYWRIGHT=%s\\n' "${PLAYWRIGHT_CHROMIUM_EXECUTABLE-<unset>}"
printf 'END\\n'
} >> "$STUB_LOG"
exit 0
"""


def write_stub(directory, name, mode=0o755):
    path = Path(directory) / name
    path.write_text(STUB_SOURCE.replace('{name}', name))
    path.chmod(mode)
    return path


def parse_log(log_path):
    """Returns a list of dicts, one per stub invocation, in order."""
    if not log_path.exists():
        return []
    records, current = [], None
    for line in log_path.read_text().splitlines():
        if line == 'END':
            records.append(current)
            current = None
            continue
        key, _, value = line.partition('=')
        if current is None:
            current = {'args': []}
        if key == 'arg':
            current['args'].append(value)
        else:
            current[key] = value
    return records


class BuildScriptFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert BUILD_SH.is_file(), f'missing {BUILD_SH}'

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dervo-build-sh-')
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.root = self.tmp / 'dervo build root'
        for relative in ('backend', 'frontend', 'frontend/node_modules/typescript/bin',
                         'frontend/node_modules/vite/bin', 'frontend/node_modules/@tauri-apps/cli'):
            (self.root / relative).mkdir(parents=True)
        (self.root / 'titlebar.test.mjs').write_text('// fixture\n')
        (self.root / 'Cargo.toml').write_text('[package]\nname = "fixture"\n')
        shutil.copyfile(BUILD_SH, self.root / 'build.sh')
        self.stub_bin = self.tmp / 'stub bin'
        self.stub_bin.mkdir()
        self.stubs = {name: write_stub(self.stub_bin, name) for name in STUBS}
        self.log = self.tmp / 'invocations.log'

    def run_build(self, env=None, cwd=None, script=None):
        """Runs build.sh with a minimal PATH. `script` is the argv form used to name the script."""
        base = {'PATH': str(self.stub_bin), 'STUB_LOG': str(self.log)}
        base.update(env or {})
        return subprocess.run(['/bin/sh', script or str(self.root / 'build.sh')], cwd=cwd or self.tmp,
                              env=base, capture_output=True, text=True, timeout=120)

    def records(self):
        return parse_log(self.log)


EXPECTED_STEPS = (
    ('node', '', ['--test', 'titlebar.test.mjs']),
    ('python3', 'backend', ['-m', 'unittest', '-v']),
    ('node', 'frontend', ['node_modules/typescript/bin/tsc', '--noEmit']),
    ('node', '', ['frontend/node_modules/vite/bin/vite.js', 'build', 'frontend', '--outDir', '../dist']),
    ('node', 'frontend', ['--import', './test-loader.mjs', '--experimental-strip-types', '--test',
                          'src/features/messages/ui/useDraftPersistSnapshot.test.mjs',
                          'src/features/messages/lib/useDrafts.test.mjs',
                          'src/features/messages/lib/sendChannelBinding.test.mjs']),
    ('node', 'frontend', ['local-ui.test.mjs']),
    ('cargo', '', ['test', '--offline']),
    ('node', '', ['frontend/node_modules/@tauri-apps/cli/tauri.js', 'build', '--bundles', 'app', '--', '--offline']),
    # The app's own Python, from runtime/python.json, before the bundle is signed.
    ('python3', '', ['scripts/python_runtime.py', 'stage', '--app', 'target/release/bundle/macos/Dervo Local.app']),
    # Voice's pinned libraries and its audio helper (runtime/voice.json), after the Python they run on.
    ('python3', '', ['scripts/voice_runtime.py', 'stage', '--app', 'target/release/bundle/macos/Dervo Local.app']),
)


class OrderedInvocations(BuildScriptFixture):
    def test_steps_run_in_order_from_path_discovery_and_stop_at_codesign(self):
        result = self.run_build()
        records = self.records()
        self.assertEqual([(r['name'], r['args']) for r in records],
                         [(name, args) for name, _, args in EXPECTED_STEPS], result.stderr)
        for record, (_, subdir, _) in zip(records, EXPECTED_STEPS):
            self.assertEqual(Path(record['cwd']).resolve(), (self.root / subdir).resolve(), record)
        self.assertNotEqual(result.returncode, 0, 'codesign has no real bundle to sign, so the run must not succeed')
        self.assertNotIn('Dervo Local.app', ''.join(r['self'] for r in records), 'no stub was asked to sign')

    def test_resolved_tools_are_reported_and_sandbox_pin_is_named_as_separate_debt(self):
        result = self.run_build()
        for name in STUBS:
            self.assertIn(f'{name}={self.stubs[name]}', result.stderr)
        self.assertIn('task_evidence.py', result.stderr)
        self.assertIn('separate', result.stderr.lower())

    def test_caller_rust_environment_and_path_are_preserved(self):
        result = self.run_build(env={'RUSTUP_HOME': '/caller rustup home', 'CARGO_HOME': '/caller cargo home'})
        cargo = [r for r in self.records() if r['name'] == 'cargo']
        self.assertEqual(len(cargo), 1, result.stderr)
        self.assertEqual(cargo[0]['RUSTUP_HOME'], '/caller rustup home')
        self.assertEqual(cargo[0]['CARGO_HOME'], '/caller cargo home')
        self.assertEqual(cargo[0]['PATH'], str(self.stub_bin), 'PATH must not be replaced or extended')

    def test_unset_rust_environment_stays_unset(self):
        self.run_build()
        cargo = [r for r in self.records() if r['name'] == 'cargo'][0]
        self.assertEqual(cargo['RUSTUP_HOME'], '<unset>')
        self.assertEqual(cargo['CARGO_HOME'], '<unset>')


class RootDiscovery(BuildScriptFixture):
    """The script must find its own directory with shell builtins only, under any invocation form."""

    def assert_all_steps_ran_from_root(self, result):
        records = self.records()
        self.assertEqual(len(records), len(EXPECTED_STEPS), result.stderr)
        for record, (_, subdir, _) in zip(records, EXPECTED_STEPS):
            self.assertEqual(Path(record['cwd']).resolve(), (self.root / subdir).resolve(), record)

    def test_bare_name_from_inside_root(self):
        self.assert_all_steps_ran_from_root(self.run_build(cwd=self.root, script='build.sh'))

    def test_dot_slash_from_inside_root(self):
        self.assert_all_steps_ran_from_root(self.run_build(cwd=self.root, script='./build.sh'))

    def test_relative_path_from_parent_directory(self):
        self.assert_all_steps_ran_from_root(self.run_build(cwd=self.tmp, script='dervo build root/build.sh'))

    def test_empty_path_still_finds_root_and_reports_missing_node(self):
        result = self.run_build(env={'PATH': ''}, cwd=self.root, script='build.sh')
        self.assertNotIn('dirname', result.stderr)
        self.assertNotIn('not found', result.stderr)
        self.assertIn('DERVO_NODE', result.stderr)


class RelativePathAnchoring(BuildScriptFixture):
    """Relative PATH entries and a relative browser override are anchored to the caller's cwd before cd."""

    def test_relative_path_entry_resolves_to_absolute_tool_before_cd(self):
        result = self.run_build(env={'PATH': 'stub bin'}, cwd=self.tmp)
        records = self.records()
        self.assertEqual(len(records), len(EXPECTED_STEPS), result.stderr)
        for record in records:
            self.assertTrue(Path(record['self']).is_absolute(), record)
            self.assertEqual(Path(record['self']).resolve(), self.stubs[record['name']].resolve(), record)
        reported = [line.partition('node=')[2] for line in result.stderr.splitlines() if line.startswith('build.sh: node=')]
        self.assertEqual(len(reported), 1, result.stderr)
        self.assertTrue(Path(reported[0]).is_absolute(), reported)
        self.assertEqual(Path(reported[0]).resolve(), (self.stub_bin / 'node').resolve())

    def test_relative_browser_override_is_exported_as_absolute(self):
        browsers = self.tmp / 'browser dir'
        browsers.mkdir()
        browser = write_stub(browsers, 'chrome headless shell')
        result = self.run_build(env={'PLAYWRIGHT_CHROMIUM_EXECUTABLE': 'browser dir/chrome headless shell'}, cwd=self.tmp)
        steps = [r for r in self.records() if r['args'] == ['local-ui.test.mjs']]
        self.assertEqual(len(steps), 1, result.stderr)
        self.assertEqual(Path(steps[0]['PLAYWRIGHT']).resolve(), browser.resolve())
        self.assertTrue(Path(steps[0]['PLAYWRIGHT']).is_absolute())

    def test_relative_browser_override_that_does_not_exist_is_rejected(self):
        result = self.run_build(env={'PLAYWRIGHT_CHROMIUM_EXECUTABLE': 'browser dir/missing'}, cwd=self.tmp)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records(), [])
        self.assertIn('PLAYWRIGHT_CHROMIUM_EXECUTABLE', result.stderr)


class Overrides(BuildScriptFixture):
    def override_dir(self):
        other = self.tmp / 'override tools dir'
        other.mkdir()
        return other

    def test_explicit_overrides_with_spaces_are_used_instead_of_path(self):
        other = self.override_dir()
        overrides = {name: write_stub(other, name) for name in STUBS}
        result = self.run_build(env={'DERVO_NODE': str(overrides['node']), 'DERVO_CARGO': str(overrides['cargo']),
                                     'DERVO_PYTHON': str(overrides['python3'])})
        records = self.records()
        self.assertEqual(len(records), len(EXPECTED_STEPS), result.stderr)
        for record in records:
            self.assertEqual(Path(record['self']), overrides[record['name']], record)

    def test_relative_override_is_resolved_against_caller_directory(self):
        other = self.override_dir()
        node = write_stub(other, 'node')
        result = self.run_build(env={'DERVO_NODE': 'override tools dir/node'}, cwd=self.tmp)
        first = self.records()[0]
        self.assertEqual(first['name'], 'node', result.stderr)
        self.assertEqual(Path(first['self']).resolve(), node.resolve())

    def assert_rejected(self, env, variable):
        result = self.run_build(env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records(), [], 'nothing may run after an invalid override')
        self.assertIn(variable, result.stderr)
        return result

    def test_missing_node_override_fails_without_falling_back_to_path(self):
        result = self.assert_rejected({'DERVO_NODE': str(self.tmp / 'absent tools' / 'node')}, 'DERVO_NODE')
        self.assertIn('fallback', result.stderr.lower())

    def test_missing_cargo_override_fails_before_any_step(self):
        self.assert_rejected({'DERVO_CARGO': str(self.tmp / 'absent tools' / 'cargo')}, 'DERVO_CARGO')

    def test_missing_python_override_fails_before_any_step(self):
        self.assert_rejected({'DERVO_PYTHON': str(self.tmp / 'absent tools' / 'python3')}, 'DERVO_PYTHON')

    def test_directory_override_is_rejected(self):
        self.assert_rejected({'DERVO_NODE': str(self.stub_bin)}, 'DERVO_NODE')

    def test_non_executable_override_is_rejected(self):
        plain = write_stub(self.override_dir(), 'node', mode=0o644)
        self.assert_rejected({'DERVO_NODE': str(plain)}, 'DERVO_NODE')

    def test_empty_override_is_rejected_not_ignored(self):
        result = self.assert_rejected({'DERVO_CARGO': ''}, 'DERVO_CARGO')
        self.assertIn('empty', result.stderr.lower())

    def test_dangling_symlink_override_is_rejected(self):
        link = self.tmp / 'dangling node'
        link.symlink_to(self.tmp / 'gone')
        self.assert_rejected({'DERVO_NODE': str(link)}, 'DERVO_NODE')

    def test_symlink_override_to_executable_is_accepted(self):
        link = self.tmp / 'link to node'
        link.symlink_to(self.stubs['node'])
        result = self.run_build(env={'DERVO_NODE': str(link)})
        self.assertEqual(self.records()[0]['self'], str(link), result.stderr)


class PathDiscoveryFailures(BuildScriptFixture):
    def test_missing_tool_on_path_names_the_override_variable(self):
        self.stubs['cargo'].unlink()
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records(), [], 'resolution happens before any step runs')
        self.assertIn('DERVO_CARGO', result.stderr)

    def test_empty_path_without_overrides_fails_cleanly(self):
        result = self.run_build(env={'PATH': ''})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DERVO_NODE', result.stderr)
        self.assertEqual(self.records(), [])


class PlaywrightBrowser(BuildScriptFixture):
    def browser_step(self):
        steps = [r for r in self.records() if r['args'] == ['local-ui.test.mjs']]
        self.assertEqual(len(steps), 1)
        return steps[0]

    def test_no_override_leaves_playwright_default_browser(self):
        self.run_build()
        self.assertEqual(self.browser_step()['PLAYWRIGHT'], '<unset>')

    def test_explicit_browser_override_passes_through_unchanged(self):
        browsers = self.tmp / 'browser dir'
        browsers.mkdir()
        browser = write_stub(browsers, 'chrome headless shell')
        self.run_build(env={'PLAYWRIGHT_CHROMIUM_EXECUTABLE': str(browser)})
        self.assertEqual(self.browser_step()['PLAYWRIGHT'], str(browser))

    def test_invalid_browser_override_is_rejected_before_any_step(self):
        result = self.run_build(env={'PLAYWRIGHT_CHROMIUM_EXECUTABLE': str(self.tmp / 'no browser here')})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.records(), [])
        self.assertIn('PLAYWRIGHT_CHROMIUM_EXECUTABLE', result.stderr)


class SourcePolicy(unittest.TestCase):
    def setUp(self):
        self.source = BUILD_SH.read_text()

    def test_no_builder_specific_paths_remain(self):
        for forbidden in ('/Users/bryce', 'native/.rustup', 'native/.cargo', 'ms-playwright',
                          '/usr/local/bin/python3', '1.95.0-aarch64-apple-darwin', 'export PATH='):
            self.assertNotIn(forbidden, self.source, forbidden)

    def test_override_names_and_preserved_steps_are_present(self):
        for token in ('DERVO_NODE', 'DERVO_CARGO', 'DERVO_PYTHON', 'PLAYWRIGHT_CHROMIUM_EXECUTABLE',
                      'set -eu', '--test titlebar.test.mjs', '-m unittest -v', 'tsc --noEmit',
                      'vite.js build frontend --outDir ../dist', 'local-ui.test.mjs', 'test --offline',
                      'tauri.js build --bundles app -- --offline',
                      'scripts/python_runtime.py stage --app "target/release/bundle/macos/Dervo Local.app"',
                      'scripts/voice_runtime.py stage --app "target/release/bundle/macos/Dervo Local.app"',
                      '/usr/bin/codesign --force --sign -', '/usr/bin/codesign --verify --deep --strict',
                      'target/release/bundle/macos/Dervo Local.app'):
            self.assertIn(token, self.source, token)

    def test_no_em_dashes(self):
        self.assertNotIn('—', self.source)


if __name__ == '__main__':
    unittest.main()
