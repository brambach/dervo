"""Checks after every full-access run (project_checks.py): what a project may save, what its files suggest, how each
runner's failures are named and how a result reads. The end-to-end runs are in test_full_access."""
import json
import os
import time
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

import project_checks as pc
from dervo_connections import Rejected


class FailureNameTests(unittest.TestCase):
    def test_each_usual_runner_names_its_failing_tests(self):
        unittest_output = ('FAIL: test_value (test_greeting.T.test_value)\nERROR: test_boot (pkg.test_app.AppTests.test_boot)\n'
                           'Ran 4 tests in 0.1s\n\nFAILED (failures=1, errors=1)\n')
        self.assertEqual(pc.failures(unittest_output), ['pkg.test_app.AppTests.test_boot', 'test_greeting.T.test_value'])
        tap = 'ok 1 - renders\nnot ok 2 - sends the draft\n  not ok 1 - a subtest\nnot ok 3 - later # TODO\nok 4 - b\n'
        self.assertEqual(pc.failures(tap), ['sends the draft'], 'top-level TAP only, and a TODO is not a failure')
        # Node 24's spec reporter, as a piped node:test file prints it: subtests indented, then a recap that repeats
        # every failure at the left edge, subtests included.
        spec = ('✔ renders (0.68ms)\n✖ sends the draft (0.59ms)\n▶ parent\n  ✖ a subtest (0.11ms)\n✖ parent (0.37ms)\n'
                '✖ later (0.09ms) # TODO\n✖ name with (parens) (1.2s)\n﹣ skipped (0.13ms) # SKIP\nℹ tests 6\nℹ fail 3\n'
                '✖ failing tests:\n\ntest at a.test.mjs:3:1\n✖ sends the draft (0.59ms)\n  AssertionError [ERR_ASSERTION]: 1 == 2\n'
                'test at a.test.mjs:4:33\n✖ a subtest (0.11ms)\n  Error: x\n')
        self.assertEqual(pc.failures(spec), ['name with (parens)', 'parent', 'sends the draft'],
                         'top level only, a TODO is not a failure, and the recap adds nothing')
        self.assertEqual(pc.failures('\x1b[31m✖ sends the draft (0.59ms)\x1b[39m\n'), ['sends the draft'], 'colour codes, if any')
        self.assertEqual(pc.failures('✔ renders (0.68ms)\nℹ tests 1\nℹ fail 0\n'), [])
        cargo = 'test bridge::tests::a ... ok\ntest bridge::tests::b ... FAILED\n'
        self.assertEqual(pc.failures(cargo), ['bridge::tests::b'])
        pytest = 'FAILED tests/test_x.py::test_y - AssertionError: 1 != 2\n'
        self.assertEqual(pc.failures(pytest), ['tests/test_x.py::test_y'])
        tsc = "src/a.tsx(69,62): error TS2554: Expected 2 arguments, but got 1.\n"
        self.assertEqual(pc.failures(tsc), ['src/a.tsx: TS2554 Expected 2 arguments, but got 1.'], 'the line number moves; the error is the name')

    def test_a_failing_command_with_no_names_still_fails_under_its_own_name(self):
        def run(argv, **kw):
            return type('Done', (), {'stdout': 'Segmentation fault\n', 'stderr': '', 'returncode': 139})()
        result = pc.run_command({'name': 'Build', 'argv': ['make'], 'cwd': '.', 'timeout': 60}, '/tmp', run=run)
        self.assertEqual(result['failing'], ['Build: exited 139'])


class SettingTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    def test_a_project_saves_named_argv_commands_and_nothing_shell_shaped(self):
        saved = pc.save(self.db, 'p', [{'name': 'Tests', 'argv': ['pnpm', 'test'], 'cwd': 'frontend'}])
        self.assertEqual(saved, [{'name': 'Tests', 'argv': ['pnpm', 'test'], 'cwd': 'frontend', 'timeout': pc.DEFAULT_TIMEOUT}])
        self.assertEqual(pc.read(self.db, 'p'), saved)
        for bad in ([{'name': 'x', 'argv': 'pnpm test'}], [{'name': 'x', 'argv': ['a'], 'cwd': '../elsewhere'}],
                    [{'name': 'x', 'argv': ['a'], 'cwd': '/abs'}], [{'name': 'x', 'argv': ['a'], 'shell': True}],
                    [{'name': 'x', 'argv': ['a'], 'timeout': 99999}], [{'name': 'x', 'argv': ['a']}, {'name': 'x', 'argv': ['b']}]):
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                pc.save(self.db, 'p', bad)
        self.assertEqual(pc.save(self.db, 'p', []), [])
        self.assertEqual(pc.read(self.db, 'p'), [], 'an empty list turns checks off')

    def test_suggestions_come_from_the_projects_files_and_are_never_saved(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'Cargo.toml').write_text('[package]\n')
            (root / 'frontend').mkdir()
            (root / 'frontend/package.json').write_text(json.dumps({'scripts': {'test': 'node --test', 'typecheck': 'tsc'}}))
            (root / 'frontend/pnpm-lock.yaml').write_text('')
            (root / 'backend').mkdir()
            (root / 'backend/test_app.py').write_text('import unittest\n\nclass AppTests(unittest.TestCase):\n    pass\n')
            found = pc.suggested(root)
        self.assertEqual([(c['name'], c['argv'], c['cwd']) for c in found], [
            ('Rust tests', ['cargo', 'test', '--no-fail-fast', '--workspace'], '.'), ('Tests (frontend)', ['pnpm', 'test'], 'frontend'),
            ('Typecheck (frontend)', ['pnpm', 'run', 'typecheck'], 'frontend'),
            ('Python tests (backend)', ['python3', '-m', 'unittest', 'discover', '-p', 'test_*.py'], 'backend')])
        self.assertEqual(pc.read(self.db, 'p'), [])


PLAIN = 'def test_one():\n    assert 1 + 1 == 2\n'
CASE = 'import unittest\n\n\nclass OneTests(unittest.TestCase):\n    def test_one(self):\n        self.assertEqual(1 + 1, 2)\n'
SITE = '.venv/lib/python3.12/site-packages'
VENV = {'.venv/bin/python': '', f'{SITE}/pytest/__init__.py': ''}  # a project venv with pytest installed
PY = '<root>/.venv/bin/python'


def python_check(files, windows=False):
    """The Python check suggested() offers for a project made of ``files``, as [argv, cwd], or None. ``<root>`` stands for
    the project folder, in the files and in what comes back."""
    with tempfile.TemporaryDirectory() as temp, patch.object(pc.platform_os, 'WINDOWS', windows):
        root = Path(temp)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            # A hook module holds its paths as Python source, escaped, as setuptools and editables write them.
            (root / name).write_text(text.replace('<root>', temp.replace('\\', '\\\\') if name.endswith('.py') else temp))
        found = [(c['argv'], c['cwd']) for c in pc.suggested(root) if c['name'].startswith('Python tests')]
        return [[a.replace(temp, '<root>').replace(os.sep, '/') if temp in a else a for a in found[0][0]], found[0][1]] if found else None


class SuggestionTests(unittest.TestCase):
    """What suggested() offers has to run the project's tests for real, on whichever checkout it runs in (critic review
    and its three reviewers, 9 Oct, on 26 real layouts): unittest on plain test functions finds none and, before Python
    3.12, exits 0, which would read as passing on every run."""

    def test_pytest_style_tests_run_with_pytest_from_the_projects_venv(self):
        for test in (PLAIN, 'async def test_one():\n    pass\n', 'class TestApi:\n    def test_one(self):\n        pass\n',
                     'import pytest\n\n\n@pytest.mark.slow\ndef check():\n    pass\n'):
            with self.subTest(test=test):
                self.assertEqual(python_check({**VENV, 'tests/test_api.py': test}), [[PY, '-m', 'pytest'], '.'])
        self.assertEqual(python_check({**VENV, 'tests/unit/test_api.py': PLAIN}), [[PY, '-m', 'pytest'], '.'], 'pytest finds tests below tests/')
        self.assertEqual(python_check({**VENV, 'backend/test_app.py': PLAIN}), [[PY, '-m', 'pytest'], 'backend'], 'the repository’s venv')

    def test_unittest_classes_run_with_pytest_when_the_venv_has_it(self):
        self.assertEqual(python_check({**VENV, 'tests/test_api.py': CASE}), [[PY, '-m', 'pytest'], '.'])

    def test_a_project_without_pytest_where_its_tests_would_run_gets_nothing(self):
        # It would fail to start on every run ("No module named pytest") and read unclear forever.
        without = {'.venv/bin/python': '', f'{SITE}/requests/__init__.py': ''}
        for files in ({'tests/test_api.py': PLAIN}, {**without, 'tests/test_api.py': PLAIN},
                      {'pyproject.toml': '[tool.pytest.ini_options]\n', 'tests/test_api.py': CASE},
                      {'requirements-dev.txt': 'pytest==8.3\n', 'tests/test_api.py': CASE}, {'tests/conftest.py': '', 'tests/test_api.py': CASE},
                      {'pyproject.toml': '[tool.pytest.ini_options]\n', 'backend/test_app.py': CASE}):
            with self.subTest(files=list(files)):
                self.assertIsNone(python_check(files))

    def test_unittest_projects_keep_unittest_from_their_tests_folder(self):
        self.assertEqual(python_check({'tests/test_api.py': CASE}), [['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], '.'])
        self.assertEqual(python_check({'tests/__init__.py': '', 'tests/helpers.py': '', 'tests/test_api.py': CASE})[0],
                         ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-p', 'test_*.py'],
                         'a tests package imports as one, so its relative imports work')
        self.assertEqual(python_check({'backend/tests/test_api.py': CASE}),
                         [['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], 'backend'])
        self.assertEqual(python_check({'test_api.py': 'import unittest\n\n\nclass ApiTests(unittest.IsolatedAsyncioTestCase):\n'
                                                     '    async def test_one(self):\n        pass\n'}),
                         [['python3', '-m', 'unittest', 'discover', '-p', 'test_*.py'], '.'])

    def test_a_tests_package_importing_its_helpers_by_name_starts_imports_in_its_folder(self):
        # The project's own "discover -s tests" runs these; with -t . every file would fail to import, before a run and
        # after it, and a broken test would read as already failing (review, 9 Oct).
        helpers = {'tests/__init__.py': '', 'tests/helpers.py': 'THREE = 3\n'}
        for imported in ('from helpers import THREE\n', 'import helpers\n', 'from fixtures.orders import make\n'):
            with self.subTest(imported=imported):
                self.assertEqual(python_check({**helpers, 'tests/fixtures/orders.py': '', 'tests/test_api.py': imported + CASE})[0],
                                 ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'])
        for imported in ('from .helpers import THREE\n', 'from tests.helpers import THREE\n', 'import os\n'):
            with self.subTest(imported=imported):
                self.assertEqual(python_check({**helpers, 'tests/test_api.py': imported + CASE})[0],
                                 ['python3', '-m', 'unittest', 'discover', '-s', 'tests', '-t', '.', '-p', 'test_*.py'])
        self.assertEqual(python_check({**helpers, 'tests/test_api.py': 'import os, helpers\n' + CASE})[0][-4:], ['-s', 'tests', '-p', 'test_*.py'])
        late = {**helpers, **{f'tests/test_{number}.py': CASE for number in range(9)}, 'tests/test_9.py': 'from helpers import THREE\n' + CASE}
        self.assertEqual(python_check(late)[0][-4:], ['-s', 'tests', '-p', 'test_*.py'], 'only a file past the first eight imports it')
        # A name the standard library or the project's own code also has isn't beside them: without -t . the tests folder
        # would hide the real module, and every file would fail to import (second review, 9 Oct).
        for files in ({'shop/__init__.py': '', 'tests/shop/__init__.py': '', 'tests/test_api.py': 'from shop import api\n' + CASE},
                      {'src/shop/__init__.py': '', 'tests/shop/__init__.py': '', 'tests/test_api.py': 'import shop\n' + CASE},
                      {'tests/http/__init__.py': '', 'tests/test_api.py': 'from http import HTTPStatus\n' + CASE}):
            with self.subTest(files=list(files)):
                self.assertEqual(python_check({'tests/__init__.py': '', **files})[0][-6:], ['-s', 'tests', '-t', '.', '-p', 'test_*.py'])

    def test_the_projects_own_venv_runs_the_tests_from_any_folder(self):
        # A whole path: a thread's own folder gets a copy of .venv only for some lockfiles, and the clean checkout links
        # the same venv in.
        self.assertEqual(python_check({'.venv/bin/python': '', 'backend/test_app.py': CASE}),
                         [['<root>/.venv/bin/python', '-m', 'unittest', 'discover', '-p', 'test_*.py'], 'backend'])
        self.assertEqual(python_check({'.venv/bin/python': '', 'backend/venv/bin/python': '', 'backend/test_app.py': CASE})[0][0],
                         '<root>/backend/venv/bin/python', 'the project’s own venv first')
        self.assertEqual(python_check({'.venv/Scripts/python.exe': '', '.venv/Lib/site-packages/pytest/__init__.py': '',
                                       'tests/test_api.py': PLAIN}, windows=True)[0][0], '<root>/.venv/Scripts/python.exe')
        self.assertEqual(python_check({'tests/test_api.py': CASE}, windows=True)[0][0], 'python')

    def test_each_checkout_tests_its_own_code_when_the_venv_installs_the_project_editable(self):
        # The editable install imports the project from the project folder whatever folder the tests run in, so on the
        # clean checkout a new failure would read as already failing (measured, 9 Oct).
        src = {**VENV, 'src/shop/__init__.py': '', 'tests/test_api.py': PLAIN}
        for hook in ({f'{SITE}/_shop.pth': '<root>/src\n'},
                     {f'{SITE}/__editable__.shop-0.1.pth': 'import __editable___shop_0_1_finder; __editable___shop_0_1_finder.install()\n',
                      f'{SITE}/__editable___shop_0_1_finder.py': "MAPPING: dict[str, str] = {'shop': '<root>/src/shop'}\n"},
                     {f'{SITE}/_editable_impl_shop.pth': 'import _editable_impl_shop\n',
                      f'{SITE}/_editable_impl_shop.py': "F.map_module('shop', '<root>/src/shop/__init__.py')\n"}):
            with self.subTest(hook=list(hook)):
                self.assertEqual(python_check({**src, **hook})[0], ['env', 'PYTHONPATH=src', PY, '-m', 'pytest'])
        member = {**VENV, f'{SITE}/core.pth': '<root>/packages/core/src\n', 'backend/test_app.py': PLAIN}
        self.assertEqual(python_check(member)[0], ['env', 'PYTHONPATH=../packages/core/src', PY, '-m', 'pytest'], 'a workspace member')
        unit = {'.venv/bin/python': '', f'{SITE}/_shop.pth': '<root>/src\n', 'tests/test_api.py': CASE}
        self.assertEqual(python_check(unit)[0], ['env', 'PYTHONPATH=src', PY, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'])
        flat = {**VENV, 'shop/__init__.py': '', 'tests/test_api.py': PLAIN}
        for elsewhere in ('/elsewhere/lib\n', '# a comment\n'):
            with self.subTest(elsewhere=elsewhere):
                self.assertEqual(python_check({**flat, f'{SITE}/other.pth': elsewhere})[0], [PY, '-m', 'pytest'])
        # The tests' own folder too: Python a test starts in another folder (python -m shop in a temporary folder, or the
        # venv's shop command) would import the project folder's code through the install (review, 9 Oct).
        self.assertEqual(python_check({**flat, f'{SITE}/_shop.pth': '<root>\n'})[0], ['env', 'PYTHONPATH=.', PY, '-m', 'pytest'])
        self.assertEqual(python_check({'.venv/Scripts/python.exe': '', '.venv/Lib/site-packages/pytest/__init__.py': '', 'shop/__init__.py': '',
                                       '.venv/Lib/site-packages/_shop.pth': '<root>\n', 'tests/test_api.py': PLAIN}, windows=True)[0],
                         ['<root>/.venv/Scripts/python.exe', '-m', 'pytest'], 'Windows: Python puts it on the path for the tests themselves')
        finder = {f'{SITE}/__editable__.shop-0.1.pth': 'import __editable___shop_0_1_finder; __editable___shop_0_1_finder.install()\n'}
        mapping = lambda names: {**finder, f'{SITE}/__editable___shop_0_1_finder.py': f'MAPPING = {names!r}\nNAMESPACES = {{}}\n'}
        self.assertEqual(python_check({**VENV, **mapping({'acme.billing': '<root>/acme/billing', 'acme.email': '<root>/acme/email'}),
                                       'tests/test_api.py': PLAIN})[0], ['env', 'PYTHONPATH=.', PY, '-m', 'pytest'],
                         'acme.email imports from the folder that holds acme, never from acme/, which would hide the standard email')
        self.assertEqual(python_check({**VENV, **mapping({'shop': '<root>/lib/shop.py'}), 'tests/test_api.py': PLAIN})[0],
                         ['env', 'PYTHONPATH=lib', PY, '-m', 'pytest'], 'a module')
        self.assertEqual(python_check({**VENV, **mapping({'tool': '/elsewhere/lib'}), 'tests/test_api.py': PLAIN})[0], [PY, '-m', 'pytest'],
                         'another project installed from elsewhere imports the same on every checkout')
        for names in ({'shop': '<root>/src'}, {'shop': '<root>/lib'}, {'shop': '<root>'}):
            with self.subTest(names=names):
                self.assertIsNone(python_check({**VENV, **mapping(names), 'tests/test_api.py': PLAIN}),
                                  'package-dir = {shop = "src"}: no folder on the path makes shop import from this checkout')
        self.assertIsNone(python_check({**src, f'{SITE}/__editable__.shop.pth': 'import __editable___shop_finder\n'}),
                          'an editable install whose hook Dervo can’t read')
        self.assertIsNone(python_check({**src, '.venv/Scripts/python.exe': '', '.venv/Lib/site-packages/pytest/__init__.py': '',
                                        '.venv/Lib/site-packages/_shop.pth': '<root>/src\n'}, windows=True), 'no env on Windows')

    def test_a_src_layout_without_an_install_imports_from_src_as_its_own_tests_do(self):
        # A Makefile's PYTHONPATH=src, or pytest's pythonpath: without it the imports fail the same way before and after,
        # and a broken test would read as already failing (review, 9 Oct).
        self.assertEqual(python_check({'src/shop/__init__.py': '', 'tests/test_api.py': CASE})[0],
                         ['env', 'PYTHONPATH=src', 'python3', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'])
        self.assertEqual(python_check({**VENV, 'src/acme/billing/__init__.py': '', 'tests/test_api.py': PLAIN})[0],
                         ['env', 'PYTHONPATH=src', PY, '-m', 'pytest'], 'a namespace package')
        self.assertEqual(python_check({**VENV, 'src/shop.py': '', 'tests/test_api.py': PLAIN})[0], ['env', 'PYTHONPATH=src', PY, '-m', 'pytest'])
        for files in ({'src/__init__.py': '', 'src/app/__init__.py': ''}, {'src/app/__init__.py': '', 'tests/test_app.py': 'from src.app import run\n' + PLAIN},
                      {'src/app.js': ''}):
            with self.subTest(files=list(files)):
                self.assertEqual(python_check({**VENV, 'tests/test_api.py': PLAIN, **files})[0], [PY, '-m', 'pytest'])
        self.assertIsNone(python_check({'src/shop/__init__.py': '', 'tests/test_api.py': CASE}, windows=True), 'no env on Windows')

    def test_a_django_project_gets_nothing_unless_it_uses_pytest(self):
        manage = 'import os\nfrom django.core.management import execute_from_command_line\n'
        test = 'from django.test import TestCase\n\n\nclass PollTests(TestCase):\n    pass\n'
        self.assertIsNone(python_check({'manage.py': manage, 'tests/test_polls.py': test}))
        self.assertEqual(python_check({**VENV, 'manage.py': manage, 'requirements.txt': 'pytest-django\n', 'tests/test_polls.py': test})[0],
                         [PY, '-m', 'pytest'])

    def test_files_named_like_tests_that_hold_no_tests_arent_a_check_or_collected(self):
        script = 'import socket\nprint(socket.gethostname())\n'
        self.assertIsNone(python_check({'test_connection.py': script}))
        self.assertIsNone(python_check({**VENV, 'test_connection.py': script}))
        self.assertEqual(python_check({'test_connection.py': script, 'tests/test_api.py': CASE})[0][-4:], ['-s', 'tests', '-p', 'test_*.py'])
        self.assertEqual(python_check({**VENV, 'test_connection.py': script, 'tests/test_api.py': PLAIN})[0],
                         [PY, '-m', 'pytest', '--ignore=test_connection.py'], 'importing a script runs it')

    def test_scripts_that_test_nothing_never_finish_or_rewrite_files_arent_offered(self):
        for script in ('echo "Error: no test specified" && exit 1', 'exit 0', 'true', 'echo skip', '', '  ', 'vitest --watch',
                       'jest --watchAll', 'tsc --noEmit --watch', 'vitest --watch=true', 'mocha -w', 'tsc -w', 'prettier -w .',
                       'eslint . --fix', 'prettier --write .', 'ng test', 'karma start', None, ['jest'],
                       'echo "no tests yet" || true', 'echo skip; exit 0', 'cypress open', 'vitest --ui', 'playwright test --ui',
                       'jest -u', 'jest --ci --updateSnapshot', 'vitest run -u', 'playwright test --update-snapshots',
                       'biome check --apply-unsafe .', 'black .', 'python -m black src', 'poetry run isort .', 'ruff format .',
                       'ruff check . && ruff format .', 'cargo fmt', 'deno fmt', ':', 'true;', 'lint-staged', 'vitest ' * 300,
                       'vue-cli-service lint', 'node lint.js || true', 'eslint . || true', 'mocha; exit 0', 'vitest run || echo failed',
                       'jest; :', 'npm test -- -w', 'jest --update-snapshot', 'playwright test --update-snapshots=changed',
                       'vitest run --update=true', 'ruff check --fix-only .', 'node --test --watch-path=src', 'playwright test --debug',
                       'mocha --inspect-brk'):
            with self.subTest(script=script):
                self.assertFalse(pc._a_check(script))
        for script in ('jest', 'jest --watchAll=false', 'vitest run', 'vitest --watch=false', 'echo start && jest', 'node --test',
                       'tsc --noEmit', 'eslint .', 'jest -w 4', 'ng test --watch=false', 'ng test --no-watch',
                       'karma start --single-run', 'eslint . --fix-dry-run', 'prettier --check .', 'pytest -W error',
                       'mocha -u tdd', 'mocha --ui bdd', 'cypress run', 'black --check .', 'ruff format --check . && ruff check .',
                       'isort --check-only .', 'cargo fmt --check', 'deno fmt --check', 'blacken-docs --check', 'node test/black.js',
                       'vitest --watch false', 'echo-cli test', 'true-myth-test', 'vue-cli-service lint --no-fix', 'jest || exit 1',
                       'jest; exit', 'npm test -w packages/core', 'pnpm -w test', 'ruff format --diff', 'eslint lint-staged.config.js',
                       'npm run black-check', 'eslint --fix-type problem .', 'jest --update-snapshot=false',
                       'playwright test --update-snapshots=none'):
            with self.subTest(script=script):
                self.assertTrue(pc._a_check(script))
        # npm and Yarn 1 run a script's pre and post scripts, and a script can run others by name (review, 9 Oct: a
        # pretest that formatted the code rewrote it on every check).
        for scripts in ({'pretest': 'prettier --write .', 'test': 'jest'}, {'test': 'jest', 'posttest': 'eslint --fix .'},
                        {'test': 'npm run format && jest', 'format': 'prettier --write .'},
                        {'test': 'pnpm lint && vitest run', 'lint': 'yarn run fix', 'fix': 'eslint --fix .'},
                        {'test': 'npm run build && jest', 'prebuild': 'tsc --watch'},
                        {'test': 'run-s lint unit', 'lint': 'eslint --fix .', 'unit': 'jest'},
                        {'test': 'npm-run-all "lint:*" unit', 'lint:js': 'eslint --fix .', 'unit': 'jest'},
                        {'test': 'concurrently "npm:lint" "npm:unit"', 'lint': 'prettier --write .', 'unit': 'jest'},
                        {'test': 'node --run lint && jest', 'lint': 'eslint --fix .'}, {'test': 'npm run unit', 'unit': 'jest || true'},
                        {'test': 'npm run s0', **{f's{number}': f'npm run s{number + 1}' for number in range(40)}, 's40': 'jest'}):
            with self.subTest(scripts=scripts):
                self.assertFalse(pc._offered(scripts, 'test'))
        for scripts in ({'pretest': 'npm run build', 'build': 'tsc', 'test': 'jest', 'posttest': 'echo done'},
                        {'test': 'npm test --workspaces', 'format': 'prettier --write .'}, {'test': 'npm run test:unit', 'test:unit': 'jest'},
                        {'pretest': 'node setup.js || true', 'test': 'jest'}, {'test': 'run-p "lint:*"', 'lint:a': 'eslint .', 'lint:b': 'tsc'},
                        {'test': 'echo "run npm run format" && jest', 'format': 'prettier --write .'},
                        {'test': 'npm run black-check', 'black-check': 'black --check .'},
                        {'test': 'npm run s0', **{f's{number}': f'npm run s{number + 1}' for number in range(9)}, 's9': 'jest'}):
            with self.subTest(scripts=scripts):
                self.assertTrue(pc._offered(scripts, 'test'))
        self.assertFalse(pc._offered({'prelint': 'eslint --fix .', 'lint': 'eslint .'}, 'lint'))
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'package.json').write_text(json.dumps({'scripts': {'test': 'echo "Error: no test specified" && exit 1',
                                                                             'lint': 'eslint .'}, 'notes': 'x' * 300000}))
            self.assertEqual([(c['name'], c['argv']) for c in pc.suggested(temp)], [('Lint', ['npm', 'run', 'lint'])],
                             'a long package.json is read whole')
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'package.json').write_text('[' * 100000 + ']' * 100000)
            self.assertEqual(pc.suggested(temp), [])

    def test_rust_tests_cover_the_whole_workspace_unless_the_project_chose_its_members(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'Cargo.toml').write_text('[package]\nname = "app"\n\n[workspace]\nmembers = ["crates/*"]\n')
            self.assertEqual(pc.suggested(temp)[0]['argv'], ['cargo', 'test', '--no-fail-fast', '--workspace'])
            (Path(temp) / 'Cargo.toml').write_text('[workspace]\nmembers = ["a", "b"]\ndefault-members = ["a"]\n')
            self.assertEqual(pc.suggested(temp)[0]['argv'], ['cargo', 'test', '--no-fail-fast'])

    def test_the_project_is_read_again_only_when_a_path_it_looked_at_changes(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(pc, 'SETTLED_NS', 0), patch.object(pc.platform_os, 'WINDOWS', False):
            root = Path(temp)
            (root / 'tests').mkdir()
            (root / 'tests/test_api.py').write_text(CASE)
            first = pc.suggested(root)
            first[0]['argv'].append('--changed-by-the-caller')
            with patch.object(pc, '_suggest', wraps=pc._suggest) as looked:
                self.assertEqual(pc.suggested(root), [dict(first[0], argv=first[0]['argv'][:-1])])
                self.assertEqual(looked.call_count, 0, 'nothing changed, so nothing was read')
                (root / '.venv/bin').mkdir(parents=True)
                (root / '.venv/bin/python').write_text('')
                self.assertEqual(pc.suggested(root)[0]['argv'][0], str(root / '.venv/bin/python'), 'a path that wasn’t there')
                (root / SITE / 'pytest').mkdir(parents=True)
                self.assertEqual(pc.suggested(root)[0]['argv'], [str(root / '.venv/bin/python'), '-m', 'pytest'])
                self.assertEqual(looked.call_count, 2)

    def test_what_changed_just_now_or_couldnt_be_read_is_read_again(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'tests').mkdir()
            (Path(temp) / 'tests/test_api.py').write_text(CASE)
            with patch.object(pc, '_suggest', wraps=pc._suggest) as looked:
                pc.suggested(temp)
                pc.suggested(temp)
                self.assertEqual(looked.call_count, 2, 'changed within the last 2 s: its stamp may not have moved yet')
                for path in (Path(temp), Path(temp) / 'tests', Path(temp) / 'tests/test_api.py'):
                    os.utime(path, ns=(time.time_ns() - 10**10,) * 2)
                pc.suggested(temp)
                pc.suggested(temp)
                self.assertEqual(looked.call_count, 3)
        with tempfile.TemporaryDirectory() as temp, patch.object(pc, 'SETTLED_NS', 0):
            (Path(temp) / 'tests/unit').mkdir(parents=True)
            with patch('pathlib.Path.iterdir', side_effect=PermissionError(13, 'Permission denied')):
                self.assertEqual(pc.suggested(temp), [])
            self.assertNotIn(str(Path(temp)), pc.SUGGESTED, 'a permission change doesn’t move a change time')
            with patch.object(pc, '_python_tests', side_effect=RecursionError):
                self.assertEqual(pc.suggested(temp), [], 'the thread’s status asks on every read, so it never raises')

    def test_a_file_added_in_a_folder_below_one_it_looked_in_reads_as_a_change(self):
        # Adding src/shop/__init__.py moves only src/shop's change time (review, 9 Oct).
        with tempfile.TemporaryDirectory() as temp, patch.object(pc, 'SETTLED_NS', 0), patch.object(pc.platform_os, 'WINDOWS', False):
            root = Path(temp)
            (root / 'tests').mkdir()
            (root / 'tests/test_api.py').write_text(CASE)
            (root / 'src/shop').mkdir(parents=True)
            (root / 'src/shop/data.txt').write_text('')
            self.assertEqual(pc.suggested(root)[0]['argv'][:2], ['python3', '-m'])
            (root / 'src/shop/__init__.py').write_text('')
            self.assertEqual(pc.suggested(root)[0]['argv'][:2], ['env', 'PYTHONPATH=src'])
        with tempfile.TemporaryDirectory() as temp, patch.object(pc, 'SETTLED_NS', 0):
            (Path(temp) / 'src/shop').mkdir(parents=True)
            (Path(temp) / 'tests').mkdir()
            (Path(temp) / 'tests/test_api.py').write_text(CASE)
            real = os.access
            with patch.object(pc.os, 'access', lambda path, mode: not str(path).endswith('shop') and real(path, mode)):
                pc.suggested(temp)
            self.assertNotIn(str(Path(temp)), pc.SUGGESTED, 'glob passes over a folder it can’t read without a word')

    def test_python_that_the_tests_start_elsewhere_imports_this_checkouts_code(self):
        # The PYTHONPATH a check carries becomes whole paths into the checkout it runs in, and the venv's commands come
        # first on PATH (review, 9 Oct: a test running python -m shop in a temporary folder imported the project folder's
        # code through the editable install, on the clean checkout too).
        with tempfile.TemporaryDirectory() as temp:
            venv = Path(temp) / '.venv'
            (venv / 'bin').mkdir(parents=True)
            (venv / 'bin/python').write_text('')
            (venv / 'pyvenv.cfg').write_text('home = /usr/bin\n')
            argv, env = pc._in_checkout(['env', f'PYTHONPATH=src{os.pathsep}.{os.pathsep}../core', str(venv / 'bin/python'), '-m', 'pytest'],
                                        Path(temp) / 'backend', {'PATH': '/usr/bin'})
            real = Path(os.path.realpath(temp))
            self.assertEqual(argv, ['env', 'PYTHONPATH=' + os.pathsep.join(str(real / path) for path in ('backend/src', 'backend', 'core')),
                                    str(venv / 'bin/python'), '-m', 'pytest'])
            self.assertEqual(env['PATH'], str(venv / 'bin') + os.pathsep + '/usr/bin')
            self.assertEqual(pc._in_checkout(['python3', '-m', 'unittest'], Path(temp), {'PATH': '/usr/bin'}),
                             (['python3', '-m', 'unittest'], {'PATH': '/usr/bin'}), 'anything else runs as it was saved')
            # A folder named with the path separator (a Finder name with a slash) would split a whole path in two, so its
            # check keeps its paths from its own folder, as saved (second review, 9 Oct).
            odd = Path(temp) / f'Client{os.pathsep}Acme'
            (odd / '.venv/bin').mkdir(parents=True)
            (odd / '.venv/bin/python').write_text('')
            (odd / '.venv/pyvenv.cfg').write_text('home = /usr/bin\n')
            saved = ['env', f'PYTHONPATH=src{os.pathsep}.', str(odd / '.venv/bin/python'), '-m', 'pytest']
            self.assertEqual(pc._in_checkout(saved, odd, {'PATH': '/usr/bin'}), (saved, {'PATH': '/usr/bin'}))

    @unittest.skipIf(os.name == 'nt', 'a link needs extra rights on Windows')
    def test_a_check_folder_that_links_elsewhere_finds_the_paths_from_where_it_really_is(self):
        # suggested() measured ../../packages/core/src from services/backend, where backend really is (second review, 9 Oct).
        with tempfile.TemporaryDirectory() as temp:
            root = Path(os.path.realpath(temp))
            (root / 'services/backend').mkdir(parents=True)
            (root / 'backend').symlink_to(root / 'services/backend', target_is_directory=True)
            argv, _ = pc._in_checkout(['env', 'PYTHONPATH=../../packages/core/src', 'python3', '-m', 'pytest'], root / 'backend', {})
        self.assertEqual(argv[1], 'PYTHONPATH=' + str(root / 'packages/core/src'))

    @unittest.skipIf(pc.platform_os.WINDOWS, 'Windows gets no PYTHONPATH check: it has no env')
    def test_a_test_that_starts_python_in_another_folder_imports_this_checkouts_code(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'src/shop').mkdir(parents=True)
            (Path(temp) / 'src/shop/__init__.py').write_text('')
            (Path(temp) / 'src/shop/__main__.py').write_text('print(2 + 2)\n')
            (Path(temp) / 'tests').mkdir()
            (Path(temp) / 'tests/test_cli.py').write_text(
                'import subprocess, sys, tempfile, unittest\n\n\nclass CliTests(unittest.TestCase):\n    def test_cli(self):\n'
                '        done = subprocess.run([sys.executable, "-m", "shop"], cwd=tempfile.gettempdir(), capture_output=True, text=True)\n'
                '        self.assertEqual(done.stdout.strip(), "4", done.stderr)\n')
            [check] = pc.suggested(temp)
            self.assertEqual(check['argv'][:2], ['env', 'PYTHONPATH=src'])
            result = pc.run_command(check, temp)
        self.assertEqual((result['exit'], result['failing']), (0, []), result['output'])

    def test_the_suggested_command_really_runs_the_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'tests').mkdir()
            (Path(temp) / 'tests/__init__.py').write_text('')
            (Path(temp) / 'tests/helpers.py').write_text('THREE = 3\n')
            (Path(temp) / 'tests/test_api.py').write_text('from .helpers import THREE\n' + CASE +
                                                          '\n    def test_two(self):\n        self.assertEqual(1 + 1, THREE)\n')
            [check] = pc.suggested(temp)
            result = pc.run_command(check, temp)
        self.assertEqual(result['failing'], ['tests.test_api.OneTests.test_two', 'Python tests: exited 1'])
        self.assertIn('Ran 2 tests', result['output'])


class SummaryTests(unittest.TestCase):
    def test_a_result_reads_as_what_the_run_changed(self):
        after = [{'name': 'Tests', 'failing': ['a', 'b', 'c']}]
        results = pc.compare(after, {'Tests': ['a', 'z']})
        self.assertEqual((results[0]['new'], results[0]['already'], results[0]['fixed']), (['b', 'c'], ['a'], ['z']))
        self.assertEqual(pc.summary(results), '2 new failures: b, c. 1 was already failing')
        self.assertEqual(pc.summary(pc.compare([{'name': 'T', 'failing': ['a']}], {'T': ['a']})), 'No new failures. 1 was already failing before this run')
        self.assertEqual(pc.summary(pc.compare([{'name': 'T', 'failing': []}], None)), 'Checks pass')
        self.assertEqual(pc.summary(pc.compare([{'name': 'T', 'failing': ['a']}], None), compared=False),
                         '1 failing (Dervo couldn’t check which were failing before)')


    def test_a_clean_checkout_that_couldnt_run_a_check_makes_it_unclear_not_new(self):
        """Dogfooding on Dervo (8 Oct): the clean checkout's typecheck couldn't start without node_modules, so every
        existing type error looked new. A check that only failed to run before can't say which failures are new."""
        before = {'Typecheck': ['Typecheck: exited 2'], 'Tests': ['a']}
        after = [{'name': 'Typecheck', 'failing': ['x.ts: TS2322 nope']}, {'name': 'Tests', 'failing': ['a']}]
        results = pc.compare(after, before)
        self.assertEqual((results[0]['new'], results[0]['unclear']), ([], ['x.ts: TS2322 nope']))
        self.assertEqual(pc.summary(results), '1 failing in Typecheck, and Dervo couldn’t run it on the code before this run to tell which are new')
        ran = [{'name': 'Typecheck', 'failing': ['x.ts: TS2322 nope']}]
        out = pc.check_run('/nowhere', 'abc', [{'name': 'Typecheck', 'argv': ['tsc'], 'cwd': '.', 'timeout': 60}], '/tmp',
                           cached={'Typecheck': ['Typecheck: exited 2']},
                           run=lambda *a, **k: type('Done', (), {'stdout': 'x.ts(1,2): error TS2322: nope', 'stderr': '', 'returncode': 2})())
        self.assertEqual(out['state'], 'unclear', 'never passed, so never auto-committed')

    def test_dependency_folders_are_linked_into_the_clean_checkout_even_through_a_link(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / 'cache/node_modules'; real.mkdir(parents=True)
            project = root / 'project'; (project / 'frontend').mkdir(parents=True)
            (project / 'frontend/node_modules').symlink_to(real, target_is_directory=True)
            (project / '.venv').mkdir()
            checkout = root / 'checkout'; (checkout / 'frontend').mkdir(parents=True)
            pc._link_dependencies(project, checkout)
            self.assertEqual((checkout / 'frontend/node_modules').resolve(), real.resolve())
            self.assertEqual((checkout / '.venv').resolve(), (project / '.venv').resolve())


    @unittest.skipIf(pc.platform_os.WINDOWS, 'the check runs through env')
    def test_a_module_a_thread_deleted_that_the_project_folder_still_has_is_unclear(self):
        """Second review (9 Oct): in a thread's own folder, with the project installed editable from the project folder, a
        module the run deleted still imports from the project folder, so a test that still needs it passed."""
        import subprocess, sys
        test = ('import unittest\n\n\nclass ShopTests(unittest.TestCase):\n    def test_value(self):\n'
                '        import helpers\n        self.assertEqual(helpers.VALUE, 1)\n')
        with tempfile.TemporaryDirectory() as temp:
            top = Path(os.path.realpath(temp))
            project, thread = top / 'project', top / 'thread'
            for name, text in {'src/helpers.py': 'VALUE = 1\n', 'src/shop/__init__.py': '', 'src/shop/legacy.py': '', 'tools/make.py': '',
                               'tests/test_shop.py': test, '.gitignore': '.venv/\n__pycache__/\n'}.items():
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text(text)
            git = lambda *args: subprocess.run(['git', *args], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            git('init', '-q')
            git('add', '-A')
            git('-c', 'user.name=Dervo', '-c', 'user.email=dervo@example.com', 'commit', '-qm', 'start')
            sha = git('rev-parse', 'HEAD')
            subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(project / '.venv')], check=True)
            [site] = (project / '.venv').glob('lib/python*/site-packages')
            (site / '__editable__.shop-0.1.pth').write_text(f'{project / "src"}\n')  # setuptools' src layout install
            git('worktree', 'add', '-q', '--detach', str(thread), sha)
            check = {'name': 'Python tests', 'argv': ['env', 'PYTHONPATH=src', str(project / '.venv/bin/python'), '-m', 'unittest',
                                                      'discover', '-s', 'tests', '-p', 'test_*.py'], 'cwd': '.', 'timeout': 60}
            for gone in ('tools/make.py', 'src/shop/legacy.py'):
                (thread / gone).unlink()
            self.assertEqual(pc.check_run(thread, sha, [check], top / 'scratch')['state'], 'passed',
                             'nothing imports them through the project folder: a package’s modules import from this checkout’s package')
            (thread / 'src/helpers.py').unlink()
            out = pc.check_run(thread, sha, [check], top / 'scratch')
            self.assertEqual((out['state'], out['results'][0]['unclear']), ('unclear', ['src/helpers.py']), out['results'][0]['output'])
            self.assertEqual(out['summary'], 'This run deleted src/helpers.py, which Python tests can still import from the project folder, '
                                             'so Dervo can’t tell whether anything still needs it')
            (project / 'src/helpers.py').unlink()
            self.assertEqual(pc.check_run(thread, sha, [check], top / 'scratch')['state'], 'new_failures', 'the project folder hasn’t got it either')

    def test_what_an_editable_install_imports_from_elsewhere_counts_as_python_finds_it(self):
        # setuptools' finder maps a module without its .py and finds a mapped package's own modules there; a folder on the
        # path finds the modules and packages right in it. Deeper modules import through this checkout's own package.
        with tempfile.TemporaryDirectory() as temp:
            top = Path(os.path.realpath(temp))
            project, thread = top / 'project', top / 'thread'
            for name in ('.git/HEAD', 'helpers.py', 'shop/__init__.py', 'shop/legacy.py', 'shop/deep/thing.py', 'src/tool.py',
                         '.venv/pyvenv.cfg', '.venv/bin/python'):
                (project / name).parent.mkdir(parents=True, exist_ok=True)
                (project / name).write_text('')
            thread.mkdir()
            site = project / '.venv/lib/python3.12/site-packages'
            site.mkdir(parents=True)
            (site / '__editable__.shop-0.1.pth').write_text('import __editable___shop_0_1_finder; __editable___shop_0_1_finder.install()\n')
            (site / '__editable___shop_0_1_finder.py').write_text(f'MAPPING = {{"helpers": {str(project / "helpers")!r}, "shop": {str(project / "shop")!r}}}\n')
            (site / 'tools.pth').write_text(f'{project / "src"}\n# a comment\n')
            found = pc._imports_elsewhere([str(project / '.venv/bin/python'), '-m', 'pytest'], thread)
            self.assertEqual(found, [(project / 'helpers', 'module'), (project / 'shop', 'module'), (project / 'src', 'path')])
            self.assertEqual(pc._imports_elsewhere([str(project / '.venv/bin/python'), '-m', 'pytest'], project), [], 'its own folder')
            self.assertEqual(pc._imports_elsewhere(['python3', '-m', 'pytest'], thread), [])
            still = {name for name in ('helpers.py', 'shop/legacy.py', 'shop/__init__.py', 'shop/deep/thing.py', 'src/tool.py', 'gone.py')
                     if any(pc._still_has(place, kind, name) for place, kind in found)}
            self.assertEqual(still, {'helpers.py', 'shop/legacy.py', 'shop/__init__.py', 'src/tool.py'})

    def test_a_new_failure_is_never_rerun_to_be_called_flaky(self):
        """Fourth review (8 Oct): a failure that fails only sometimes is still new, and a rerun cost a whole second pass."""
        calls = []
        def done(*a, **k):
            calls.append(a)
            return type('Done', (), {'stdout': 'FAIL: test_a (m.T.test_a)\nFAIL: test_b (m.T.test_b)', 'stderr': '', 'returncode': 1})()
        out = pc.check_run('/nowhere', 'abc', [{'name': 'Tests', 'argv': ['t'], 'cwd': '.', 'timeout': 60}], '/tmp',
                           cached={'Tests': ['Tests: exited 1', 'm.T.test_b']}, run=done)
        self.assertEqual((out['state'], out['results'][0]['new'], len(calls)), ('new_failures', ['m.T.test_a'], 1))
        self.assertEqual(out['summary'], '1 new failure: m.T.test_a. 1 was already failing')


class ReviewFixTests(unittest.TestCase):
    """Second review of self-checks (8 Oct)."""

    def check(self, outputs, cached):
        outputs = iter(outputs)
        def done(*a, **k):
            stdout, code = next(outputs)
            return type('Done', (), {'stdout': stdout, 'stderr': '', 'returncode': code})()
        return pc.check_run('/nowhere', 'abc', [{'name': 'Tests', 'argv': ['t'], 'cwd': '.', 'timeout': 60}], '/tmp',
                            cached=cached, run=done)

    def test_a_runner_that_also_stopped_for_another_reason_is_a_new_failure(self):
        """Third review, High 1: a pytest collection error exits 2 while the known failure is still named."""
        out = self.check([('FAILED tests/test_a.py::test_old - boom\nERROR collecting tests/test_new.py', 2)] * 2,
                         {'Tests': ['Tests: exited 1', 'tests/test_a.py::test_old']})
        self.assertEqual((out['state'], out['results'][0]['new']), ('new_failures', ['Tests: exited 2']))
        self.assertEqual(out['summary'], '1 new failure: Tests: exited 2. 1 was already failing')

    def test_a_new_failure_past_any_name_limit_is_still_new(self):
        """Third review, High 2: names were cut at 200, so a new failure sorting after them was lost."""
        old = [f'm.T.test_{n:04d}' for n in range(300)]
        output = '\n'.join(f'FAIL: {name.rsplit(".", 1)[1]} ({name})' for name in old + ['m.T.test_zzz'])
        out = self.check([(output, 1)] * 2, {'Tests': ['Tests: exited 1'] + old})
        self.assertEqual((out['state'], out['results'][0]['new'], out['results'][0]['new_count']), ('new_failures', ['m.T.test_zzz'], 1))
        self.assertEqual(len(out['results'][0]['already']), pc.SHOWN_NAMES, 'the record keeps a readable number of names')
        self.assertEqual(out['results'][0]['already_count'], 301)

    def test_pytest_setup_and_collection_errors_are_failures(self):
        """Fourth review, High 1."""
        output = ('FAILED tests/test_a.py::test_old - boom\nERROR tests/test_a.py::test_fixture - setup broke\n'
                  'ERROR tests/test_new.py\nERROR: test_x (m.T.test_x)\n')
        self.assertEqual(pc.failures(output), ['m.T.test_x', 'tests/test_a.py::test_fixture', 'tests/test_a.py::test_old', 'tests/test_new.py'])
        out = self.check([(output, 1)], {'Tests': ['Tests: exited 1', 'tests/test_a.py::test_old']})
        self.assertEqual(out['state'], 'new_failures')
        self.assertIn('tests/test_new.py', out['results'][0]['new'])

    def test_a_pytest_id_with_spaces_is_still_a_name(self):
        """Fifth review, High: test_p[a b] dropped the whole line, so a new failure auto-committed."""
        output = 'FAILED tests/a.py::test_old - boom\nFAILED tests/a.py::test_p[a b] - x\nFAILED tests/a.py::test_q[x - y] - z - w\n'
        self.assertEqual(pc.failures(output), ['tests/a.py::test_old', 'tests/a.py::test_p[a b]', 'tests/a.py::test_q[x - y]'])
        out = self.check([(output, 1)], {'Tests': ['Tests: exited 1', 'tests/a.py::test_old']})
        self.assertEqual(out['state'], 'new_failures')
        self.assertEqual(out['results'][0]['new'], ['tests/a.py::test_p[a b]', 'tests/a.py::test_q[x - y]'])

    def test_a_log_word_or_a_unittest_total_isnt_a_test_name(self):
        """Fifth review, Low: "ERROR timeout" named a test called timeout."""
        self.assertEqual(pc.failures('ERROR timeout\nERROR could not connect\nFAILED (failures=2)\nERROR tests/x.py\n'), ['tests/x.py'])

    def test_jest_and_go_name_their_failures(self):
        """Fifth review, Low: they read as unclear even when the failure was already there."""
        output = 'FAIL src/a.test.js (5.2 s)\n PASS src/b.test.js\n--- FAIL: TestX (0.00s)\n    --- FAIL: TestX/sub (0.00s)\n'
        self.assertEqual(pc.failures(output), ['TestX', 'TestX/sub', 'src/a.test.js'])

    def test_a_new_failing_jest_test_in_a_file_that_already_fails_is_new(self):
        """Sixth review, High: jest was named per file, so a second failing test in that file read as already failing."""
        before = 'FAIL src/a.test.js\n  ● login › old\n\n    expect(x)\n'
        after = 'FAIL src/a.test.js (2 s)\n  ● login › old\n\n  ● login › new one\n PASS src/b.test.js\n  ● not a failure\n'
        self.assertEqual(pc.failures(after), ['src/a.test.js', 'src/a.test.js › login › new one', 'src/a.test.js › login › old'])
        out = self.check([(after, 1)], {'Tests': ['Tests: exited 1'] + pc.failures(before)})
        self.assertEqual((out['state'], out['results'][0]['new']), ('new_failures', ['src/a.test.js › login › new one']))

    def test_a_go_panic_stops_the_tests_after_it(self):
        """Sixth review, Medium."""
        self.assertTrue(pc.stopped_early(['go', 'test', './...'], '--- FAIL: TestA (0.00s)\npanic: boom [recovered]\n'))

    def test_a_run_that_stopped_at_a_known_failure_is_unclear(self):
        """Fifth review, Medium: a fail-fast run stops at the old failure and never reaches a new one."""
        known = {'Tests': ['Tests: exited 1', 'tests/a.py::test_old']}
        out = self.check([('FAILED tests/a.py::test_old - boom\n!!!!! stopping after 1 failures !!!!!', 1)], known)
        self.assertEqual(out['state'], 'unclear')
        self.assertIn('stopped at a failure that was already there', out['summary'])
        # A new failure in a fail-fast run is still new, and a run with no fail-fast sign compares as usual.
        self.assertEqual(self.check([('FAILED tests/a.py::test_new - boom\nstopping after 1 failures', 1)], known)['state'], 'new_failures')
        self.assertEqual(self.check([('FAILED tests/a.py::test_old - boom', 1)], known)['state'], 'passed')
        self.assertTrue(pc.stopped_early(['pytest', '--maxfail=2'], '') and pc.stopped_early(['python3', '-m', 'unittest', '-f'], ''))
        self.assertFalse(pc.stopped_early(['cargo', 'test', '--no-fail-fast'], 'test a ... FAILED'))

    def test_a_failing_run_that_names_no_test_is_unclear_even_with_the_same_exit_code(self):
        """Fourth review, High 2: it may have stopped before the tests that would fail now."""
        out = self.check([('Traceback: something broke before any test ran', 1)], {'Tests': ['Tests: exited 1', 'm.T.test_old']})
        self.assertEqual(out['state'], 'unclear')
        self.assertEqual(out['summary'], 'Tests exited 1 without naming a test, so Dervo can’t tell what’s new')
        self.assertEqual(self.check([('all good', 0)], {'Tests': []})['state'], 'passed', 'a clean exit still passes')

    def test_a_check_that_ran_no_tests_never_passes(self):
        """Critic, 9 Oct: unittest discover on pytest-style tests runs nothing, and macOS's Python 3.9 exits 0 for it."""
        for output in ('\n----------------------------------------------------------------------\nRan 0 tests in 0.000s\n\nOK',
                       'collected 0 items\n\n============ no tests ran in 0.01s ============', '# tests 0\n# pass 0',
                       'ℹ tests 0\nℹ pass 0', 'No tests found, exiting with code 0', '?   \texample.com/m\t[no test files]',
                       'running 0 tests\n\ntest result: ok. 0 passed\n\nrunning 0 tests\n'):
            with self.subTest(output=output[:30]):
                out = self.check([(output, 0)], {'Tests': []})
                self.assertEqual(out['state'], 'unclear')
                self.assertEqual(out['summary'], 'Tests ran no tests, so it doesn’t count as passing')
        self.assertEqual(self.check([('running 0 tests\n\nrunning 3 tests\ntest a ... ok', 0)], {'Tests': []})['state'], 'passed',
                         'one cargo binary with tests is enough')
        self.assertEqual(self.check([('Ran 12 tests in 0.4s\n\nOK', 0)], {'Tests': []})['state'], 'passed')
        for output in ('ok  \texample.com/a\t0.1s\n?   \texample.com/b\t[no test files]', '# tests 0\n# tests 12',
                       'Ran 4 tests in 0.1s\n\nOK\nrunning 0 tests\n'):
            with self.subTest(output=output[:30]):
                self.assertEqual(self.check([(output, 0)], {'Tests': []})['state'], 'passed', 'one empty part beside real tests')

    def test_tests_that_couldnt_import_before_and_after_are_unclear_not_already_failing(self):
        """Critic, 9 Oct: an ImportError in every module reads the same before and after, which said "No new failures"."""
        broken = 'ERROR: test_greeting (unittest.loader._FailedTest.test_greeting)\nImportError: No module named x\n\nFAILED (errors=1)'
        known = {'Tests': ['Tests: exited 1', 'unittest.loader._FailedTest.test_greeting']}
        out = self.check([(broken, 1)], known)
        self.assertEqual(out['state'], 'unclear')
        self.assertEqual(out['summary'], 'Tests couldn’t import some of its tests, so Dervo can’t tell what’s new')
        # An import this run broke is new, and so is a new failure beside an old import error.
        self.assertEqual(self.check([(broken, 1)], {'Tests': ['Tests: exited 1', 'm.T.test_old']})['state'], 'new_failures')
        self.assertEqual(self.check([(broken + '\nFAIL: test_b (m.T.test_b)', 1)], known)['state'], 'new_failures')

    def test_more_failures_than_dervo_compares_is_unclear_never_passed(self):
        with patch.object(pc, 'MAX_NAMES', 3):
            out = self.check([('\n'.join(f'FAIL: test_{n} (m.T.test_{n})' for n in range(5)), 1)],
                             {'Tests': ['Tests: exited 1'] + [f'm.T.test_{n}' for n in range(5)]})
        self.assertEqual(out['state'], 'unclear')

    def test_closing_dervo_stops_the_checks_left_to_run(self):
        calls = []
        def done(argv, **k):
            calls.append(argv)
            pc.stop_all()  # Dervo closes while the first check runs
            return type('Done', (), {'stdout': '', 'stderr': '', 'returncode': 0})()
        commands = [{'name': name, 'argv': [name], 'cwd': '.', 'timeout': 60} for name in ('A', 'B')]
        out = pc.check_run('/nowhere', 'abc', commands, '/tmp', run=done)
        self.assertEqual((out['state'], calls), ('not_run', [['A']]))

    def test_a_killed_check_whose_child_keeps_the_pipes_open_still_ends_on_time(self):
        """Third review, M4: the read after the kill had no limit (2 s became 30 s)."""
        import sys
        child = 'import os,subprocess,sys,time; os.setsid() if hasattr(os,"setsid") else None; time.sleep(30)'
        script = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(30)'
        with tempfile.TemporaryDirectory() as temp:
            began = time.monotonic()
            result = pc.run_command({'name': 'Slow', 'argv': [sys.executable, '-c', script], 'cwd': '.', 'timeout': 1}, temp)
        self.assertTrue(result['timed_out'])
        self.assertLess(time.monotonic() - began, 8)

    def test_python_3_9_unittest_lines_still_name_each_test(self):
        self.assertEqual(pc.failures('FAIL: test_a (mod.Cls)\nERROR: test_b (mod.Cls)'), ['mod.Cls.test_a', 'mod.Cls.test_b'])
        self.assertEqual(pc.failures('FAIL: test_a (mod.Cls.test_a)'), ['mod.Cls.test_a'])

    def test_a_check_that_named_no_test_before_is_unclear_even_when_it_names_none_now(self):
        results = pc.compare([{'name': 'Tests', 'failing': ['Tests: exited 1']}], {'Tests': ['Tests: exited 1']})
        self.assertEqual((results[0]['already'], results[0]['unclear']), ([], ['Tests: exited 1']))

    def test_the_window_picks_checks_by_name_and_can_never_send_a_command_line(self):
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'Cargo.toml').write_text('[package]\n')
            self.assertEqual([c['argv'] for c in pc.choose(db, 'p', temp, ['Rust tests'])], [['cargo', 'test', '--no-fail-fast', '--workspace']])
            with self.assertRaises(Rejected):
                pc.choose(db, 'p', temp, ['rm -rf /'])
            self.assertEqual(pc.choose(db, 'p', temp, []), [])

    def test_picking_a_check_again_takes_what_the_files_suggest_now(self):
        """Critic, 9 Oct: a saved check beat today's suggestion of the same name, so re-picking kept the old command."""
        import sqlite3
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'Cargo.toml').write_text('[package]\n')
            pc.save(db, 'p', [{'name': 'Rust tests', 'argv': ['cargo', 'test'], 'cwd': '.', 'timeout': pc.DEFAULT_TIMEOUT}])
            self.assertEqual([c['argv'] for c in pc.choose(db, 'p', temp, ['Rust tests'])], [['cargo', 'test', '--no-fail-fast', '--workspace']])
            # A saved check the files no longer suggest can still be kept.
            (Path(temp) / 'Cargo.toml').unlink()
            self.assertEqual([c['argv'] for c in pc.choose(db, 'p', temp, ['Rust tests'])], [['cargo', 'test', '--no-fail-fast', '--workspace']])

    def test_a_time_limit_ends_what_the_check_started_too(self):
        import subprocess, sys, time
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'child-alive'
            child = f"import time,pathlib;time.sleep(3);pathlib.Path({str(marker)!r}).write_text('x')"
            parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(30)"
            started = time.monotonic()
            result = pc.run_command({'name': 'Slow', 'argv': [sys.executable, '-c', parent], 'cwd': '.', 'timeout': 1}, temp)
            self.assertTrue(result['timed_out'])
            self.assertLess(time.monotonic() - started, 10)
            time.sleep(3.5)
            self.assertFalse(marker.exists(), 'the grandchild was ended with its group')

    def test_a_check_folder_that_now_leads_outside_the_project_doesnt_run(self):
        with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
            (Path(temp) / 'tests').symlink_to(outside, target_is_directory=True)
            result = pc.run_command({'name': 'T', 'argv': ['true'], 'cwd': 'tests', 'timeout': 10}, temp)
            self.assertEqual(result['failing'], ['T: couldn’t start'])


if __name__ == '__main__':
    unittest.main()
