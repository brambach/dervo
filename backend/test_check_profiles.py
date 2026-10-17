"""Check profiles: named, host-selected, bound to task scope. Real sandbox-exec runs for Node where the host has it."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import check_profiles
import platform_os
import task_evidence
from dervo_connections import Rejected, Store

NODE_OK = "import {test} from 'node:test';\nimport assert from 'node:assert/strict';\ntest('ok', () => assert.equal(1, 1));\n"
NODE_FAIL = "import {test} from 'node:test';\nimport assert from 'node:assert/strict';\ntest('fails', () => assert.equal(1, 2));\n"


def node_available():
    try:
        return check_profiles.node_runtime()
    except Rejected:
        return None


class ProfileCatalogueTests(unittest.TestCase):
    def test_catalogue_names_python_node_and_typescript(self):
        entries = {e['id']: e for e in check_profiles.catalogue()}
        # A superset, not an exact list. manual-review joined the catalogue later and the pinned set went red
        # without anything about these profiles changing. What the catalogue owes is that the five named here
        # are present, and that every profile it lists is described, selectable and wired to a check pattern.
        self.assertLessEqual({'python-unittest', 'node-test', 'typescript-check', 'rust-check', 'rust-test'}, set(entries))
        for entry in entries.values():
            with self.subTest(profile=entry['id']):
                for key in ('id', 'label', 'test_pattern', 'detail'):
                    self.assertIsInstance(entry[key], str)
                    self.assertTrue(entry[key], key + ' is described')
                self.assertIsInstance(entry['available'], bool)
                if entry['available']:
                    self.assertEqual(check_profiles.profile(entry['id']), entry['id'], 'a listed profile is selectable')
                    check_profiles.test_pattern(entry['id'])   # every profile is wired to a check-file pattern
        self.assertTrue(entries['python-unittest']['available'])
        if not platform_os.WINDOWS:  # Node and TypeScript checks need sandbox-exec
            self.assertTrue(entries['node-test']['available'])
            self.assertTrue(entries['typescript-check']['available'])
            self.assertIn('node_modules/typescript', entries['typescript-check']['detail'])
            self.assertIn('TS2307', entries['typescript-check']['detail'], 'the detail says external packages fail visibly')
        self.assertEqual(check_profiles.profile(None), 'python-unittest', 'legacy default')
        for bad in ('rust-cargo', 'shell', '', 7):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected):
                    check_profiles.profile(bad)

    def test_windows_offers_python_and_review_and_lists_the_rest_as_unavailable(self):
        # Python checks run in a Windows app container. Node, TypeScript and Rust need sandbox-exec, so they're listed
        # but can't be chosen, and nothing runs unconfined.
        import importlib
        import platform_os
        try:
            with patch.object(platform_os, 'WINDOWS', True):
                importlib.reload(check_profiles)
                entries = {e['id']: e for e in check_profiles.catalogue()}
                self.assertEqual([k for k, e in entries.items() if e['available']], [check_profiles.MANUAL, check_profiles.PYTHON])
                self.assertEqual(check_profiles.profile(None), check_profiles.PYTHON, 'the grant dialog leaves Python out of its payload')
                self.assertEqual(entries['python-unittest']['detail'], check_profiles.WINDOWS_PYTHON_DETAIL)
                self.assertIn('app container', entries['python-unittest']['detail'])
                self.assertEqual(Path(check_profiles.python_interpreter()), Path(sys.base_prefix) / 'python.exe')
                for profile_id in ('node-test', 'typescript-check', 'rust-check', 'rust-test'):
                    with self.subTest(profile=profile_id):
                        self.assertEqual(entries[profile_id]['detail'], check_profiles.WINDOWS_DETAIL)
                        with self.assertRaisesRegex(Rejected, 'Only Python checks run on Windows'):
                            check_profiles.profile(profile_id)
        finally:
            importlib.reload(check_profiles)
        if sys.platform == 'darwin':
            self.assertTrue(all(e['available'] for e in check_profiles.catalogue()), 'the Mac catalogue is back unchanged')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'the Linux catalogue')
    def test_linux_offers_python_node_typescript_and_review_and_lists_rust_as_unavailable(self):
        entries = {e['id']: e for e in check_profiles.catalogue()}
        self.assertEqual([k for k, e in entries.items() if not e['available']], [check_profiles.RUST_EXEC, check_profiles.RUST])
        self.assertIn('bubblewrap', entries['python-unittest']['detail'])
        self.assertIn('/usr/bin/node', entries['node-test']['detail'])
        for profile_id in check_profiles.RUST_PROFILES:
            with self.assertRaisesRegex(Rejected, 'Rust checks run only on a Mac'):
                check_profiles.profile(profile_id)

    def test_python_argv_is_unchanged_and_test_names_are_per_profile(self):
        argv = check_profiles.argv_for('python-unittest', 'test_sample.py')
        self.assertEqual(argv[1:], ['-I', '-B', '-m', 'unittest', 'discover', '-s', '.', '-p', 'test_sample.py'])
        self.assertEqual(check_profiles.profile_of(argv), 'python-unittest')
        for profile, name in (('python-unittest', 'test_sample.mjs'), ('node-test', 'test_sample.py'), ('node-test', 'sub/test_a.mjs'),
                              ('node-test', '../test_a.mjs'), ('node-test', 'test*.mjs'), ('python-unittest', 'test*.py')):
            with self.subTest(profile=profile, name=name):
                with self.assertRaises(Rejected):
                    check_profiles.argv_for(profile, name)

    @unittest.skipIf(platform_os.WINDOWS, 'Node checks run only on a Mac')
    def test_missing_or_invalid_node_runtime_is_refused_not_substituted(self):
        with patch.object(check_profiles, 'NODE_CANDIDATES', ('/nonexistent/dervo/node',)):
            with self.assertRaisesRegex(Rejected, 'No trusted Node runtime'):
                check_profiles.argv_for('node-test', 'test_a.mjs')
        with tempfile.TemporaryDirectory() as temp:
            fake = Path(temp) / 'node'
            fake.write_text('#!/bin/sh\necho pwned\n')
            fake.chmod(0o755)
            with patch.object(check_profiles, 'NODE_CANDIDATES', (str(fake),)):
                with self.assertRaisesRegex(Rejected, 'outside the trusted install roots'):
                    check_profiles.node_runtime()
            link = Path(temp) / 'link'
            os.symlink(fake, link)
            with patch.object(check_profiles, 'NODE_CANDIDATES', (str(link),)):
                with self.assertRaises(Rejected):
                    check_profiles.node_runtime()
            with patch.object(check_profiles, 'NODE_CANDIDATES', (str(Path(temp) / 'dir'),)):
                (Path(temp) / 'dir').mkdir()
                with self.assertRaises(Rejected):
                    check_profiles.node_runtime()

    @unittest.skipIf(platform_os.WINDOWS, 'a shell-script stand-in for Node')
    def test_node_flags_follow_what_the_install_accepts(self):
        # Node 24.21 refuses --no-experimental-fetch with exit 9; an older Node takes it. Each is asked once per binary.
        with tempfile.TemporaryDirectory() as temp:
            calls = Path(temp) / 'calls'
            node = Path(temp) / 'node'
            def install(code):
                node.write_text(f'#!/bin/sh\necho "$@" >> {calls}\nexit {code}\n')
                node.chmod(0o755)
            install(9)
            with patch.dict(check_profiles._node_flags_seen, clear=True):
                self.assertEqual(check_profiles.node_flags(str(node)), ('--no-addons',))
                self.assertEqual(check_profiles.node_flags(str(node)), ('--no-addons',))
                self.assertEqual(calls.read_text().splitlines(), ['--no-experimental-fetch -e 0'], 'asked once, with nothing else run')
                install(0)
                os.utime(node, ns=(1, 1))  # an upgrade in place is asked again
                self.assertEqual(check_profiles.node_flags(str(node)), check_profiles.NODE_FLAGS)
                self.assertEqual(check_profiles.node_flags(str(Path(temp) / 'missing')), check_profiles.NODE_FLAGS,
                                 'a Node that can\'t be asked keeps the full set and fails visibly')

    def test_a_check_reads_as_itself_with_either_flag_set(self):
        node = '/usr/local/bin/node'
        with patch.object(check_profiles, 'node_runtime', return_value=node):
            for flags in check_profiles.NODE_FLAG_SETS:
                with self.subTest(flags=flags):
                    self.assertEqual(check_profiles.profile_of([node, *flags, 'test_a.mjs']), 'node-test')
                    self.assertEqual(check_profiles.profile_of([node, *flags, check_profiles.TS_ENTRY, *check_profiles.TS_ARGS]), 'typescript-check')
            with patch.object(check_profiles, 'node_flags', return_value=('--no-addons',)):
                self.assertEqual(check_profiles.argv_for('node-test', 'test_a.mjs'), [node, '--no-addons', 'test_a.mjs'])
            for argv in ([node, '--no-experimental-fetch', 'test_a.mjs'], [node, '--no-addons', '--no-addons', 'test_a.mjs'],
                         [node, '--no-addons', '-e', '1']):
                with self.subTest(argv=argv), self.assertRaises(Rejected):
                    check_profiles.profile_of(argv)

    def test_either_reporter_summary_counts_as_tests_reported(self):
        # Node 22 prints TAP when piped; Node 24 prints the spec reporter's summary.
        for out, reported in (('# tests 1\n# pass 1', True), ('ℹ tests 3\nℹ pass 3', True), ('ℹ tests 0', False),
                              ('# tests 0', False), ('', False), ('  ℹ tests 1', False)):
            with self.subTest(out=out):
                self.assertEqual(bool(task_evidence.NODE_TESTS_REPORTED.search(out)), reported)

    def test_arbitrary_argv_is_rejected_before_any_launch(self):
        node = node_available() or '/usr/local/bin/node'
        for argv in (['/bin/sh', '-c', 'echo hi'], [node, '-e', 'process.exit(0)'], [node, 'test_a.mjs'], [node, *check_profiles.NODE_FLAGS, 'app.mjs'],
                     [node, '--test', 'test_a.mjs'], [node, *check_profiles.NODE_FLAGS, 'test_a.mjs', '--extra'], [node, *check_profiles.NODE_FLAGS, '-e', '1'],
                     [node, *check_profiles.NODE_FLAGS, 'test*.mjs'], ['/usr/local/bin/npm', 'test'],
                     [node, *check_profiles.NODE_FLAGS, 'test_a.mjs', '&&', 'curl', 'x'], 'node --test test_a.mjs', []):
            with self.subTest(argv=argv):
                with self.assertRaises(Rejected):
                    check_profiles.profile_of(argv) if isinstance(argv, list) else task_evidence._run_confined('/', argv)
                with patch.object(task_evidence.subprocess, 'Popen', side_effect=AssertionError('launched')):
                    with self.assertRaises(Rejected):
                        task_evidence._run_confined('/', argv)


class ConfinedNodeTests(unittest.TestCase):
    """Actual sandbox runs. Skipped with the exact blocker when the host has no trusted Node or no confinement."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        self.node = node_available()
        if not self.node:
            self.skipTest('No trusted Node runtime at ' + ' or '.join(check_profiles.NODE_CANDIDATES))

    def run_node(self, name, source, **kw):
        (self.root / name).write_text(source)
        try:
            return task_evidence._run_confined(self.root, check_profiles.argv_for('node-test', name), **kw)
        except task_evidence.ConfinementBlocked as exc:
            print('NODE SANDBOX BLOCKER:', str(exc))
            self.skipTest(str(exc))

    def test_successful_and_failed_node_checks_are_captured(self):
        result = self.run_node('test_ok.mjs', NODE_OK)
        print('NODE OK:', {k: v for k, v in result.items() if k != 'runtime_files'})
        self.assertEqual((result['exit_status'], result['stopped'], result['check_profile']), (0, None, 'node-test'), result)
        self.assertEqual(result['argv'], [self.node, *check_profiles.node_flags(self.node), 'test_ok.mjs'])
        # TAP (`# pass 1`) up to Node 22; Node 24's spec reporter says `ℹ pass 1` (task_evidence.NODE_TESTS_REPORTED reads both).
        self.assertRegex(result['stdout'], r'(?m)^(#|ℹ) pass 1$')
        failed = self.run_node('test_fail.mjs', NODE_FAIL)
        self.assertEqual(failed['exit_status'], 1, failed)
        self.assertRegex(failed['stdout'], r'(?m)^(#|ℹ) fail 1$')

    def test_node_canaries_are_denied(self):
        secret = self.base / 'outside-secret'
        secret.write_text('do not read')
        write = self.base / 'outside-write'
        name, source = task_evidence.TaskEvidence.canary_source('node-test', secret, write)
        result = self.run_node(name, source)
        print('NODE CANARIES:', {k: v for k, v in result.items() if k != 'runtime_files'})
        self.assertEqual(result['exit_status'], 0, result)
        self.assertIn('host-canaries-denied', result['stdout'])
        self.assertFalse(write.exists())
        self.assertFalse((self.root / 'local-write').exists())
        for path in result['runtime_files']:
            self.assertFalse(path.startswith('/Users/'), path)
            self.assertNotIn(path, ('/usr', '/usr/local', '/opt/homebrew', '/System', '/Library'))

    def test_node_network_canary_needs_a_permission_denial_not_a_closed_port(self):
        """Negative control, run without the sandbox: a closed port answers ECONNREFUSED to an unconfined connect, and the
        canary must reject that. The file probes are satisfied by ordinary permissions so the network probe is the first
        one that can differ between a confined and an unconfined run."""
        import subprocess
        secret = self.base / 'outside-secret'
        secret.write_text('do not read')
        secret.chmod(0)
        locked = self.base / 'locked'
        locked.mkdir()
        write = locked / 'outside-write'
        for path in (locked, self.root):
            self.addCleanup(path.chmod, 0o700)
        name, source = task_evidence.TaskEvidence.canary_source('node-test', secret, write)
        (self.root / name).write_text(source)
        locked.chmod(0o500)
        self.root.chmod(0o500)
        if os.geteuid() == 0:
            self.skipTest('root ignores file permissions; this control needs an ordinary user')
        probe = subprocess.run([self.node, *check_profiles.node_flags(self.node), name], cwd=self.root, env={'HOME': ''}, capture_output=True,
                               text=True, timeout=30, stdin=subprocess.DEVNULL)
        print('NODE UNCONFINED CANARY:', dict(exit_status=probe.returncode, stdout=probe.stdout[:1500], stderr=probe.stderr[:1500]))
        output = probe.stdout + probe.stderr
        self.assertNotEqual(probe.returncode, 0, 'an unconfined run must never pass the host canary')
        self.assertNotIn('host-canaries-denied', probe.stdout)
        self.assertTrue('ECONNREFUSED' in output or 'network allowed' in output,
                        'the network probe itself must reject a refused or successful connect; a later probe failing is not proof')
        self.assertNotIn('child process allowed', output, 'the run must stop at the network probe, before the fork probe')

    def test_a_node_file_that_reports_no_tests_is_not_a_passing_check(self):
        """Honestly detectable zero-test runs: a file that never registers a node:test test exits 0 with no ``# tests N`` summary
        (N >= 1). They are recorded with their real argv, exit and output but never count as a passing run. A registered no-op
        test is still a test, and any project file may print its own summary: this is a courtesy check, not a trust boundary."""
        for name, source in (('test_empty.mjs', ''), ('test_import_only.mjs', "import {test} from 'node:test';\n"),
                             ('test_plain.mjs', "console.log('no test framework here');\n")):
            with self.subTest(name=name):
                result = self.run_node(name, source)
                print('NODE ZERO TESTS:', name, {k: v for k, v in result.items() if k != 'runtime_files'})
                self.assertEqual(result['exit_status'], 0, result)
                self.assertEqual(result['stopped'], 'no_tests_reported', result)
        noop = self.run_node('test_noop.mjs', "import {test} from 'node:test';\ntest('noop', () => {});\n")
        self.assertEqual((noop['exit_status'], noop['stopped']), (0, None), 'a registered no-op test is a test; the host cannot judge its worth')
        failed = self.run_node('test_fail.mjs', NODE_FAIL)
        self.assertEqual((failed['exit_status'], failed['stopped']), (1, None), 'a failing run keeps its own exit status as the reason')

    def test_node_timeout_output_bound_and_cancel_kill_descendants(self):
        result = self.run_node('test_hang.mjs', "console.log(process.pid); setTimeout(() => {}, 30000);\n", timeout=0.6)
        self.assertEqual((result['stopped'], result['exit_status']), ('timeout', -9), result)
        if sys.platform.startswith('linux'):
            # The pid Node printed is its own in the sandbox's PID namespace. Nothing may still be working in the snapshot.
            for proc in Path('/proc').iterdir():
                try:
                    self.assertNotEqual(os.readlink(proc / 'cwd'), str(self.root), 'the timed-out check is still running')
                except OSError:
                    pass
        else:
            with self.assertRaises(ProcessLookupError):
                os.kill(int(result['stdout'].strip().splitlines()[0]), 0)
        result = self.run_node('test_spam.mjs', "for(;;) process.stdout.write('x'.repeat(4096));\n", output_limit=4096)
        self.assertEqual(result['stopped'], 'output_limit')
        self.assertLessEqual(len(result['stdout']) + len(result['stderr']), 4096)
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.monotonic()
        result = self.run_node('test_cancel.mjs', "setTimeout(() => {}, 30000);\n", cancel=cancel)
        self.assertEqual(result['stopped'], 'cancelled')
        self.assertLess(time.monotonic() - started, 4)


class TaskBindingTests(unittest.TestCase):
    """The evidence task carries the profile; a persisted pre-profile task stays Python."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        self.root.mkdir()
        (self.root / 'test_sample.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n')
        (self.root / 'test_sample.mjs').write_text(NODE_OK)
        self.store = Store(self.base / 'store.sqlite')
        self.addCleanup(self.store.close)
        self.store.add_profile('p', 'codex', self.base / 'identity', 'synthetic@example.test', 'personal')
        self.store.add_project('p', self.root, 'p', 'synthetic-model')
        self.store.add_thread('c', 'p', 'c', 'private', 'local-user')
        self.ctx = dict(thread_id='c', project_id='p', actor='local-user')

    def test_profile_is_bound_at_creation_and_legacy_tasks_default_to_python(self):
        service = task_evidence.TaskEvidence(self.store)
        legacy = service.create(files=['test_sample.py'], test_file='test_sample.py', **self.ctx)
        self.assertEqual(legacy['check_profile'], 'python-unittest')
        self.assertEqual(legacy['argv'][-4:], ['-s', '.', '-p', 'test_sample.py'])
        with self.assertRaises(Rejected):
            service.create(files=['test_sample.mjs'], test_file='test_sample.mjs', **self.ctx)  # Python profile: not a unittest file
        with self.assertRaises(Rejected):  # TypeScript profile: the test file must be the root tsconfig.json
            service.create(files=['test_sample.py'], test_file='test_sample.py', check_profile='typescript-check', **self.ctx)
        with patch.object(check_profiles, 'NODE_CANDIDATES', ('/nonexistent/node',)):
            with self.assertRaisesRegex(Rejected, 'Only Python checks run on Windows' if platform_os.WINDOWS else 'No trusted Node runtime'):
                service.create(files=['test_sample.mjs'], test_file='test_sample.mjs', check_profile='node-test', **self.ctx)
        if node_available():
            task = service.create(files=['test_sample.mjs'], test_file='test_sample.mjs', check_profile='node-test', **self.ctx)
            self.assertEqual(task['check_profile'], 'node-test')
            self.assertEqual(task['argv'], [node_available(), *check_profiles.node_flags(node_available()), 'test_sample.mjs'])
            with self.assertRaises(Rejected):  # no tsconfig.json among the files and no project compiler
                service.scope(task['id'], files=['test_sample.mjs'], test_file='test_sample.mjs', check_profile='typescript-check', **self.ctx)
        # A task persisted before profiles existed has no check_profile field: it reads back as Python with its argv intact.
        import json
        row = json.loads(self.store.db.execute('SELECT document FROM task_evidence WHERE id=?', (legacy['id'],)).fetchone()[0])
        del row['check_profile']
        with self.store.db:
            self.store.db.execute('UPDATE task_evidence SET document=? WHERE id=?', (json.dumps(row), legacy['id']))
        read = task_evidence.TaskEvidence(self.store).read(legacy['id'], **self.ctx)
        self.assertEqual((read['check_profile'], read['argv']), ('python-unittest', legacy['argv']))

    def test_node_task_end_to_end_verification(self):
        if not node_available():
            self.skipTest('No trusted Node runtime')
        service = task_evidence.TaskEvidence(self.store)
        task = service.create(files=['test_sample.mjs'], test_file='test_sample.mjs', check_profile='node-test', **self.ctx)
        service.approve(task['id'], scope_version=1, **self.ctx)
        attempt = service.verify_current(task['id'], **self.ctx)
        job = service.launch(task['id'], attempt_number=attempt['number'], **self.ctx)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            attempt = service.poll(task['id'], job_id=job, **self.ctx)
            if attempt['state'] != 'running':
                break
            time.sleep(0.05)
        if attempt['state'] == 'blocked' and 'Confinement' in (attempt['blocker'] or ''):
            print('NODE SANDBOX BLOCKER:', attempt['blocker'])
            self.skipTest(attempt['blocker'])
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.assertEqual((attempt['test']['exit_status'], attempt['test']['check_profile']), (0, 'node-test'))
        self.assertEqual(service.review(task['id'], attempt_number=attempt['number'], decision='approved', **self.ctx)['decision'], 'approved')


TS_OK = "export function greet(name: string): string { return 'hi ' + name; }\n"
TS_BAD = "export const n: number = 'not a number';\n"
TS_REACT = "import React from 'react';\nexport const x = React;\n"
TSCONFIG = '{\n  // comments are allowed in tsconfig\n  "compilerOptions": {"target": "ES2020", "module": "ESNext", "moduleResolution": "bundler", "strict": true, "noEmit": true, "lib": ["ES2020"],},\n  "include": ["src"]\n}\n'
CACHED_COMPILER = Path(__file__).resolve().parent.parent / 'frontend/node_modules/typescript'  # coordinator test input only, never a production path


def cached_compiler():
    """The coordinator's cached compiler, wherever its symlink resolves. Tests copy its finite inputs into a temp project so
    production never hardcodes this checkout."""
    try:
        real = CACHED_COMPILER.resolve(strict=True)
    except OSError:
        return None
    return real if (real / 'lib/tsc.js').is_file() and (real / 'package.json').is_file() else None


def install_compiler(root, source=None):
    source = source or cached_compiler()
    target = root / 'node_modules/typescript'
    (target / 'lib').mkdir(parents=True, exist_ok=True)
    (target / 'package.json').write_bytes((source / 'package.json').read_bytes())
    for entry in (source / 'lib').iterdir():
        if entry.name in ('tsc.js', '_tsc.js') or (entry.name.startswith('lib') and entry.name.endswith('.d.ts')):
            (target / 'lib' / entry.name).write_bytes(entry.read_bytes())
    return target


class TypeScriptProfileTests(unittest.TestCase):
    """The TypeScript check: the project's own node_modules/typescript, validated and copied as finite inputs, runs tsc.js
    --noEmit on the snapshot under the Node sandbox. Real runs are skipped with the exact blocker when the host lacks them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'
        (self.root / 'src').mkdir(parents=True)
        (self.root / 'tsconfig.json').write_text(TSCONFIG)
        self.node = node_available()

    def test_fixed_argv_and_names(self):
        if not self.node:
            self.skipTest('No trusted Node runtime')
        argv = check_profiles.argv_for('typescript-check', 'tsconfig.json')
        self.assertEqual(argv, [self.node, *check_profiles.node_flags(self.node), 'node_modules/typescript/lib/tsc.js', '--noEmit', '-p', 'tsconfig.json'])
        self.assertEqual(check_profiles.profile_of(argv), 'typescript-check')
        for name in ('tsconfig.node.json', 'src/tsconfig.json', '../tsconfig.json', 'test_a.mjs', 'tsconfig.json --watch'):
            with self.subTest(name=name):
                with self.assertRaises(Rejected):
                    check_profiles.argv_for('typescript-check', name)
        for argv in ([self.node, *check_profiles.NODE_FLAGS, 'node_modules/typescript/lib/tsc.js', '-p', 'tsconfig.json'],
                     [self.node, *check_profiles.NODE_FLAGS, 'node_modules/typescript/lib/tsc.js', '--noEmit', '-p', 'other.json'],
                     [self.node, *check_profiles.NODE_FLAGS, 'node_modules/typescript/lib/tsc.js', '--noEmit', '-p', 'tsconfig.json', '--watch'],
                     [self.node, *check_profiles.NODE_FLAGS, 'node_modules/typescript/lib/typescript.js', '--noEmit', '-p', 'tsconfig.json']):
            with self.subTest(argv=argv):
                with self.assertRaises(Rejected):
                    check_profiles.profile_of(argv)

    def test_missing_escaping_and_unbounded_compilers_are_refused(self):
        with self.assertRaisesRegex(Rejected, 'No TypeScript compiler'):
            check_profiles.typescript_compiler(self.root)
        outside = self.base / 'outside-typescript'
        (outside / 'lib').mkdir(parents=True)
        (outside / 'package.json').write_text('{"name": "typescript", "version": "5.0.0"}')
        (outside / 'lib/tsc.js').write_text('console.log("x")')
        (self.root / 'node_modules').mkdir()
        os.symlink(outside, self.root / 'node_modules/typescript')
        with self.assertRaisesRegex(Rejected, 'outside the project'):
            check_profiles.typescript_compiler(self.root)
        os.unlink(self.root / 'node_modules/typescript')
        package = install_compiler(self.root, outside)
        self.assertEqual(check_profiles.typescript_compiler(self.root)['version'], '5.0.0')
        (package / 'lib/lib.es5.d.ts').symlink_to(self.base / 'outside-secret')
        with self.assertRaisesRegex(Rejected, 'symlink'):
            check_profiles.typescript_compiler(self.root)
        (package / 'lib/lib.es5.d.ts').unlink()
        (package / 'package.json').write_text('{"name": "not-typescript", "version": "5.0.0"}')
        with self.assertRaisesRegex(Rejected, 'not the typescript package'):
            check_profiles.typescript_compiler(self.root)
        (package / 'package.json').write_text('{"name": "typescript", "version": "5.0.0"}')
        with patch.object(check_profiles, 'TS_BYTE_CAP', 10):
            with self.assertRaisesRegex(Rejected, 'byte cap'):
                check_profiles.typescript_compiler(self.root)
        (package / 'lib/extra.js').write_text('ignored: not a compiler input')
        (package / 'lib/lib.es5.d.ts').write_text('declare var x: number;')
        inputs = check_profiles.typescript_compiler(self.root, read=True)
        self.assertEqual(sorted(inputs['files']), ['node_modules/typescript/lib/lib.es5.d.ts', 'node_modules/typescript/lib/tsc.js',
                                                    'node_modules/typescript/package.json'])
        self.assertEqual(inputs['content']['node_modules/typescript/lib/tsc.js'], b'console.log("x")')
        self.assertEqual(inputs['files']['node_modules/typescript/lib/tsc.js'], len(b'console.log("x")'))
        self.assertEqual(sorted(inputs['sha256']), sorted(inputs['files']))
        self.assertNotIn('content', check_profiles.typescript_identity(self.root), 'the persisted identity carries hashes, not bytes')

    def test_tsconfig_escapes_plugins_and_build_outputs_are_refused(self):
        check_profiles.validate_tsconfig(TSCONFIG.encode())
        for bad in ('{"extends": "./base.json"}', '{"references": [{"path": "./tsconfig.node.json"}]}',
                    '{"compilerOptions": {"plugins": [{"name": "x"}]}}', '{"compilerOptions": {"incremental": true}}',
                    '{"compilerOptions": {"composite": true}}', '{"compilerOptions": {"tsBuildInfoFile": "x"}}',
                    '{"compilerOptions": {"paths": {"@/*": ["../src/*"]}}}', '{"compilerOptions": {"baseUrl": "/Users"}}',
                    '{"compilerOptions": {"typeRoots": ["../types"]}}', '{"compilerOptions": {"types": ["node"]}}',
                    '{"include": ["../src"]}', '{"files": ["/etc/passwd"]}', '{"include": ["${configDir}/../x"]}', '[]', 'not json'):
            with self.subTest(bad=bad):
                with self.assertRaises(Rejected):
                    check_profiles.validate_tsconfig(bad.encode())

    def run_ts(self, sources, tsconfig=TSCONFIG):
        if not self.node:
            self.skipTest('No trusted Node runtime')
        if not cached_compiler():
            self.skipTest('No cached TypeScript compiler at ' + str(CACHED_COMPILER))
        (self.root / 'tsconfig.json').write_text(tsconfig)
        for name, source in sources.items():
            (self.root / name).write_text(source)
        install_compiler(self.root)
        try:
            return task_evidence._run_confined(self.root, check_profiles.argv_for('typescript-check', 'tsconfig.json'))
        except task_evidence.ConfinementBlocked as exc:
            print('TS SANDBOX BLOCKER:', str(exc))
            self.skipTest(str(exc))

    def test_good_bad_and_dependency_free_only(self):
        ok = self.run_ts({'src/a.ts': TS_OK})
        print('TS OK:', {k: v for k, v in ok.items() if k != 'runtime_files'})
        self.assertEqual((ok['exit_status'], ok['stopped'], ok['check_profile']), (0, None, 'typescript-check'), ok)
        bad = self.run_ts({'src/a.ts': TS_BAD})
        self.assertNotEqual(bad['exit_status'], 0)
        self.assertIn('error TS2322', bad['stdout'] + bad['stderr'])
        react = self.run_ts({'src/a.ts': TS_REACT})
        self.assertNotEqual(react['exit_status'], 0, 'a package that was never copied must fail visibly, never be read from the live project')
        self.assertIn('TS2307', react['stdout'] + react['stderr'])

    def test_typescript_canaries_are_denied_and_compiler_cannot_leave_the_snapshot(self):
        secret = self.base / 'outside-secret'
        secret.write_text('declare const leaked: number;')
        write = self.base / 'outside-write'
        name, source = task_evidence.TaskEvidence.canary_source('typescript-check', secret, write)
        self.assertEqual(name, 'test_confinement.mjs', 'the TypeScript check proves policy with the Node canary')
        result = self.run_ts({'src/a.ts': TS_OK})
        for path in result['runtime_files']:
            self.assertFalse(path.startswith('/Users/'), path)
        escape = self.run_ts({'src/a.ts': f'/// <reference path="{secret}" />\nexport const y = leaked;\n'})
        self.assertNotEqual(escape['exit_status'], 0, escape)
        self.assertNotIn('outside-secret', str(escape.get('runtime_files')))
        self.assertFalse(write.exists())

    def test_task_end_to_end_verification(self):
        if not self.node or not cached_compiler():
            self.skipTest('No trusted Node runtime or cached compiler')
        (self.root / 'src/a.ts').write_text(TS_OK)
        store = Store(self.base / 'store.sqlite')
        self.addCleanup(store.close)
        store.add_profile('p', 'codex', self.base / 'identity', 'synthetic@example.test', 'personal')
        store.add_project('p', self.root, 'p', 'synthetic-model')
        store.add_thread('c', 'p', 'c', 'private', 'local-user')
        ctx = dict(thread_id='c', project_id='p', actor='local-user')
        service = task_evidence.TaskEvidence(store)
        with self.assertRaisesRegex(Rejected, 'No TypeScript compiler'):
            service.create(files=['tsconfig.json', 'src/a.ts'], test_file='tsconfig.json', check_profile='typescript-check', **ctx)
        install_compiler(self.root)
        task = service.create(files=['tsconfig.json', 'src/a.ts'], test_file='tsconfig.json', check_profile='typescript-check', **ctx)
        self.assertEqual(task['check_profile'], 'typescript-check')
        self.assertEqual(task['compiler']['package'], 'node_modules/typescript')
        service.approve(task['id'], scope_version=1, **ctx)
        attempt = service.verify_current(task['id'], **ctx)
        job = service.launch(task['id'], attempt_number=attempt['number'], **ctx)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            attempt = service.poll(task['id'], job_id=job, **ctx)
            if attempt['state'] != 'running':
                break
            time.sleep(0.05)
        if attempt['state'] == 'blocked' and 'Confinement' in (attempt['blocker'] or ''):
            self.skipTest(attempt['blocker'])
        self.assertEqual(attempt['state'], 'verified', attempt.get('blocker'))
        self.assertEqual((attempt['test']['exit_status'], attempt['test']['check_profile']), (0, 'typescript-check'))
        self.assertEqual(attempt['test']['compiler'], task['compiler'])
        self.assertEqual(set(task['compiler']['sha256']), set(task['compiler']['files']), 'every input is bound by content hash')

    def test_a_same_size_compiler_replacement_is_refused_at_launch(self):
        """The scope binds the compiler by content, not by version and byte counts: swapping lib/tsc.js for other bytes of
        the same length after the scope was approved refuses the run on the host thread, before any confined launch."""
        if not self.node or not cached_compiler():
            self.skipTest('No trusted Node runtime or cached compiler')
        (self.root / 'src/a.ts').write_text(TS_OK)
        store = Store(self.base / 'store.sqlite')
        self.addCleanup(store.close)
        store.add_profile('p', 'codex', self.base / 'identity', 'synthetic@example.test', 'personal')
        store.add_project('p', self.root, 'p', 'synthetic-model')
        store.add_thread('c', 'p', 'c', 'private', 'local-user')
        ctx = dict(thread_id='c', project_id='p', actor='local-user')
        package = install_compiler(self.root)
        service = task_evidence.TaskEvidence(store)
        task = service.create(files=['tsconfig.json', 'src/a.ts'], test_file='tsconfig.json', check_profile='typescript-check', **ctx)
        service.approve(task['id'], scope_version=1, **ctx)
        attempt = service.verify_current(task['id'], **ctx)
        entry = package / 'lib/tsc.js'
        original = entry.read_bytes()
        self.addCleanup(entry.write_bytes, original)
        replaced = bytearray(original)
        replaced[:len(b'process.exit(0);//')] = b'process.exit(0);//'
        self.assertEqual(len(replaced), len(original))
        entry.write_bytes(bytes(replaced))
        self.assertEqual(check_profiles.typescript_compiler(self.root)['files'], task['compiler']['files'], 'sizes alone cannot tell the swap')
        with patch.object(task_evidence.subprocess, 'Popen', side_effect=AssertionError('launched with a replaced compiler')):
            with self.assertRaisesRegex(Rejected, 'compiler changed'):
                service.launch(task['id'], attempt_number=attempt['number'], **ctx)
        self.assertEqual(service.read(task['id'], **ctx)['attempts'][-1]['state'], 'prepared', 'nothing ran; the attempt is untouched')
        # Restoring the exact bytes makes the bound compiler current again; a re-scope binds whatever is there now.
        entry.write_bytes(original)
        self.assertEqual(service._compiler_inputs(service.read(task['id'], **ctx))['identity'], task['compiler'])

    def test_compiler_inputs_are_read_as_regular_files_and_a_swap_during_read_is_a_refusal(self):
        """An input replaced by a symlink or a non-regular file between the size check and the read is a Rejected, never an
        uncaught OSError or a blocking read; the hash covers exactly the bytes that will be copied into the snapshot."""
        outside = self.base / 'outside-typescript'
        (outside / 'lib').mkdir(parents=True)
        (outside / 'package.json').write_text('{"name": "typescript", "version": "5.0.0"}')
        (outside / 'lib/tsc.js').write_text('console.log("x")')
        package = install_compiler(self.root, outside)
        inputs = check_profiles.typescript_compiler(self.root, read=True)
        import hashlib
        self.assertEqual(inputs['sha256']['node_modules/typescript/lib/tsc.js'], hashlib.sha256(b'console.log("x")').hexdigest())
        self.assertEqual(inputs['content']['node_modules/typescript/lib/tsc.js'], b'console.log("x")')
        original_regular = check_profiles._regular
        def swap_then_stat(dir_fd, name, files, total):
            size = original_regular(dir_fd, name, files, total)
            if name == 'tsc.js':
                (package / 'lib/tsc.js').unlink()
                os.symlink(self.base / 'outside-secret', package / 'lib/tsc.js')
            return size
        (self.base / 'outside-secret').write_text('console.log("y")')
        with patch.object(check_profiles, '_regular', swap_then_stat):
            with self.assertRaisesRegex(Rejected, 'symlink'):
                check_profiles.typescript_compiler(self.root, read=True)
        (package / 'lib/tsc.js').unlink()
        os.mkfifo(package / 'lib/tsc.js')
        with self.assertRaisesRegex(Rejected, 'not a regular file'):
            check_profiles.typescript_compiler(self.root, read=True)


class AuditedPythonTests(unittest.TestCase):
    """Which Python a Python check may run on (beta.17): the app's own copy in a built app, the framework in a source
    checkout. A real run of both is in evidence/b17-python/CHECKS.md; these pin the path rules."""
    def test_a_built_apps_backend_uses_the_runtime_beside_it(self):
        backend = Path('/Applications/Dervo Local.app/Contents/Resources/backend')
        self.assertEqual(check_profiles.audited_python_prefix(backend),
                         Path('/Applications/Dervo Local.app/Contents/Resources/runtime/python'))

    @unittest.skipUnless(sys.platform == 'darwin', 'the Mac framework; Linux uses the Python the host runs on, below')
    def test_a_source_checkout_uses_the_framework_only(self):
        for backend in ('/Users/x/dervo/app/backend', '/x/Dervo Local/Contents/Resources/backend',
                        '/x/Dervo.app/Contents/Other/backend', '/x/Dervo.app/Resources/backend'):
            with self.subTest(backend=backend):
                self.assertEqual(check_profiles.audited_python_prefix(Path(backend)), check_profiles.FRAMEWORK_PYTHON)
        self.assertEqual(check_profiles.audited_python_prefix(), check_profiles.FRAMEWORK_PYTHON)

    def test_the_interpreter_follows_the_runtime_layout(self):
        framework = str(check_profiles.FRAMEWORK_PYTHON)
        with patch('check_profiles.sys.base_prefix', framework):
            self.assertEqual(check_profiles.python_interpreter(), framework + '/Resources/Python.app/Contents/MacOS/Python')
        own = '/Applications/Dervo Local.app/Contents/Resources/runtime/python'
        with patch('check_profiles.sys.base_prefix', own):
            self.assertEqual(check_profiles.python_interpreter(), own + '/bin/python3.13')

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux')
    def test_linux_checks_run_on_the_python_the_host_runs_on(self):
        self.assertEqual(check_profiles.audited_python_prefix(Path('/usr/lib/Dervo Local/backend')), Path(sys.base_prefix))
        argv = check_profiles.argv_for('python-unittest', 'test_sample.py')
        self.assertEqual(argv[0], str(Path(sys.base_prefix) / 'bin' / 'python3.13'))
        with self.assertRaises(Rejected):
            task_evidence._run_confined(tempfile.gettempdir(), ['/usr/bin/python3'] + argv[1:])

    @unittest.skipUnless(sys.platform == 'darwin', 'the Mac sandbox-exec path')
    def test_confinement_refuses_a_python_that_isnt_the_audited_one(self):
        own = Path('/Applications/Dervo Local.app/Contents/Resources/runtime/python')
        with patch('task_evidence.sys.prefix', str(own)), \
                patch('check_profiles.audited_python_prefix', return_value=check_profiles.FRAMEWORK_PYTHON):
            with self.assertRaisesRegex(task_evidence.ConfinementBlocked, 'audited Python 3.13 runtime'):
                task_evidence._run_confined(tempfile.gettempdir(), ['python3'])
        with patch('task_evidence.sys.prefix', '/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13'), \
                patch('check_profiles.audited_python_prefix', return_value=own):
            with self.assertRaisesRegex(task_evidence.ConfinementBlocked, 'audited Python 3.13 runtime'):
                task_evidence._run_confined(tempfile.gettempdir(), ['python3'])


if __name__ == '__main__':
    unittest.main()
