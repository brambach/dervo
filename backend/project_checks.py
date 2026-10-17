"""Checks after every full-access run (Bryce, 8 Oct 2026: "I want to get to a point where it's just doing all the
automated testing on its own").

In full access Dervo recorded what changed from git but never ran a check (native_evidence: "Nothing here runs a
check"), so every result reached Bryce untested. Now a project can name its own check commands once (``set``), and after
each full-access run that changed files Dervo runs them on a thread, never on the request pipe, and says what the run
changed: which checks pass, which were already failing before it, and which it broke.

"Already failing" is measured, not assumed. The same commands run once on a clean checkout of the commit the run started
from (a detached git worktree, with the project's installed dependency folders linked in, so a fresh checkout doesn't
fail for want of node_modules), and that result is kept for that commit and those commands. So a red suite with known
failures still reads clearly: "214 passed, 3 were already failing, 1 new: test_send_order".

What runs is exactly what you saved, as an argv list with no shell, in the project folder, with the same PATH a
full-access run gets and no account credentials. Nothing the agent says can add or change a command. Each command has a
time limit, and its output is kept (the last OUTPUT_TAIL characters) so the failure is one click away.
"""
import ast
import fnmatch
import functools
import hashlib
import itertools
import json
import os
import platform_os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from dervo_connections import Rejected

MAX_COMMANDS = 6
DEFAULT_TIMEOUT = 600
MAX_TIMEOUT = 1800
OUTPUT_TAIL = 6000
MAX_NAMES = 5000          # failure names compared per command; past this a check's result is unclear, never passed
SHOWN_NAMES = 200         # failure names the run's record keeps per list; the counts and the comparison use them all
DEPENDENCY_FOLDERS = ('node_modules', '.venv', 'venv')  # linked into the baseline checkout when the project has them
NAME = re.compile(r'[^\x00-\x1f]{1,60}')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS project_checks (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
    # What failed on a clean checkout of a commit, per set of commands: the "already failing" side of every comparison.
    db.execute('CREATE TABLE IF NOT EXISTS project_check_baselines (project_id TEXT NOT NULL, sha TEXT NOT NULL, '
               'commands TEXT NOT NULL, document TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (project_id, sha, commands))')
    db.execute('CREATE TABLE IF NOT EXISTS project_checks_offer (project_id TEXT PRIMARY KEY, dismissed_at REAL NOT NULL)')


def not_now(db, project_id, on=True):
    """You said Not now to the offer under a full-access result: it stays away until you turn checks on or off in
    Settings yourself (critic, 9 Oct: an offer on every result is a nag)."""
    schema(db)
    with db:
        if on:
            db.execute('INSERT OR REPLACE INTO project_checks_offer VALUES (?, ?)', (project_id, time.time()))
        else:
            db.execute('DELETE FROM project_checks_offer WHERE project_id=?', (project_id,))


def offered(db, project_id):
    """Whether the offer under a full-access result may show: you haven't said Not now."""
    schema(db)
    return db.execute('SELECT 1 FROM project_checks_offer WHERE project_id=?', (project_id,)).fetchone() is None


def _command(item):
    if not isinstance(item, dict) or set(item) - {'name', 'argv', 'cwd', 'timeout'}:
        raise Rejected('Each check needs a name and a command, and nothing else.')
    name, argv = item.get('name'), item.get('argv')
    if not isinstance(name, str) or not NAME.fullmatch(name.strip()):
        raise Rejected('Give each check a short name.')
    if not isinstance(argv, list) or not argv or len(argv) > 40 or not all(isinstance(a, str) and a and '\x00' not in a and len(a) <= 400 for a in argv):
        raise Rejected('A check’s command is a program and its arguments, as a list.')
    cwd = item.get('cwd') or '.'
    if not isinstance(cwd, str) or os.path.isabs(cwd) or '..' in Path(cwd).parts or '\x00' in cwd:
        raise Rejected('A check runs in the project folder or a folder inside it.')
    timeout = item.get('timeout', DEFAULT_TIMEOUT)
    if type(timeout) is not int or not 10 <= timeout <= MAX_TIMEOUT:
        raise Rejected(f'A check’s time limit is 10 to {MAX_TIMEOUT} seconds.')
    return {'name': name.strip(), 'argv': list(argv), 'cwd': cwd, 'timeout': timeout}


def read(db, project_id):
    """The project's saved checks, or [] when it has none."""
    schema(db)
    row = db.execute('SELECT document FROM project_checks WHERE project_id=?', (project_id,)).fetchone()
    return json.loads(row[0])['commands'] if row else []


def save(db, project_id, commands):
    """Save the project's checks: your explicit choice in settings, never the agent's."""
    if not isinstance(commands, list) or len(commands) > MAX_COMMANDS:
        raise Rejected(f'A project has up to {MAX_COMMANDS} checks.')
    clean = [_command(item) for item in commands]
    if len({c['name'] for c in clean}) != len(clean):
        raise Rejected('Each check needs its own name.')
    schema(db)
    with db:
        if clean:
            db.execute('INSERT INTO project_checks VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                       (project_id, json.dumps({'commands': clean, 'at': time.time()})))
        else:
            db.execute('DELETE FROM project_checks WHERE project_id=?', (project_id,))
    return clean


def choose(db, project_id, root, names):
    """The window's setting: which checks to run, by name, from what the project's own files suggest and what's already
    saved. The window never sends a command line: a host operation that ran a program on the window's say-so would let
    anything that can talk to the bridge run code (review, 8 Oct; bridge.rs keeps the same rule)."""
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise Rejected('Pick checks by name.')
    # What the files suggest now beats a saved check of the same name, so picking it again picks up a new venv (critic, 9 Oct).
    offered = {c['name']: c for c in (*read(db, project_id), *suggested(root))}
    unknown = [name for name in names if name not in offered]
    if unknown:
        raise Rejected(f'{unknown[0]} isn’t one of this project’s checks.')
    return save(db, project_id, [offered[name] for name in dict.fromkeys(names)])


def suggested(root):
    """Commands this project probably uses, from its files, for you to confirm. Nothing is saved or run.

    Each one has to run the project's tests for real, on whichever checkout it runs in, or it isn't offered: a check
    that runs no tests reads as passing on every run, one that can't start reads as unclear on every run, and one that
    tests the project folder's code from the clean checkout calls each new failure already failing (critic review and
    its three reviewers, 9 Oct, on 26 real layouts). So Python tests run with pytest only when the project's own virtual
    environment has it (unittest finds no tests in plain ``def test_`` files and, before Python 3.12, still exits 0, as
    Apple's python3 3.9 does), with that environment's Python, and with each checkout's own code first on the path when
    the environment has the project installed in editable mode. A Django project gets nothing: its runner needs its
    settings and finds tests by its own rules. npm's "no test specified" placeholder, watch modes and scripts that
    rewrite files (``--fix``, ``--write``) aren't offered.

    The thread's status asks for this on every read, so the paths it looked at are kept with how they looked, and
    they're only read again after one of them changes."""
    root = Path(root)
    kept = SUGGESTED.get(str(root))
    if kept and all(_stamp(path) == stamp for path, stamp in kept[0]):
        found = kept[1]
    else:
        started, look = time.time_ns(), _Look()
        try:
            found = _suggest(root, look)
        except Exception:  # noqa: BLE001 - the thread's status asks on every read, so a project Dervo can't read suggests nothing
            found, look.failed = [], True
        SUGGESTED.pop(str(root), None)
        # A path that changed within its clock's tick of this look (a second on HFS+, about 16 ms on NTFS) can change
        # again without its stamp moving, so the answer is kept only once everything it saw has been still for a while
        # (git's "racy" rule). An unreadable folder is looked at again on the next ask.
        if not look.failed and all(seen is None or abs(seen[2] - started) >= SETTLED_NS for seen in look.stamps.values()):
            SUGGESTED[str(root)] = (tuple(look.stamps.items()), found)
            while len(SUGGESTED) > SUGGESTED_KEPT:
                SUGGESTED.pop(next(iter(SUGGESTED)), None)
    return [dict(c, argv=list(c['argv'])) for c in found]


SUGGESTED = {}  # project folder -> (the paths suggested() looked at and how each looked, what it found)
SUGGESTED_KEPT = 64
SETTLED_NS = 2_000_000_000
VENVS = ('.venv', 'venv')
VENV_PYTHONS = ('bin/python', 'Scripts/python.exe')
# Where Python projects keep their settings and what they install: any of these naming pytest means the project uses it.
PYTHON_SETTINGS = ('pyproject.toml', 'setup.cfg', 'tox.ini', 'pytest.ini', 'setup.py', 'Pipfile', 'requirements.txt',
                   'requirements-dev.txt', 'dev-requirements.txt', 'requirements-test.txt', 'test-requirements.txt')
# Test folders, each with the folder its Python project is in (where the tests run, and where its venv is looked for).
PYTHON_TESTS = (('.', '.'), ('backend', 'backend'), ('backend/tests', 'backend'), ('tests', '.'), ('src', '.'))
SETTINGS_READ = 262144   # bytes of one settings file read
PACKAGE_READ = 8388608   # bytes of one package.json read: cut short, it wouldn't parse
TEST_READ = 32768        # bytes of one test file read
TESTS_READ = 8           # test files read to tell tests from scripts, and pytest from unittest
PYTEST_WORD = re.compile(r'\bpytest\b')
# pytest's own marks in a test file, or tests only pytest runs: plain functions and classes without a base.
PYTEST_STYLE = re.compile(r'^[ \t]*(?:import pytest\b|from pytest\b|@pytest\.)|^(?:async[ \t]+)?def test_\w*[ \t]*\(|'
                          r'^class Test\w*[ \t]*(?::|\([ \t]*(?:object[ \t]*)?\))', re.M)
UNITTEST_STYLE = re.compile(r'\b(?:IsolatedAsyncio)?TestCase\b')
DJANGO = re.compile(r'\bdjango\b')  # a manage.py that runs Django's commands
SRC_IMPORT = re.compile(r'^[ \t]*(?:from|import)[ \t]+src\b', re.M)  # tests that import src as a package
IMPORTED = re.compile(r'(?:^|;)\s*import\s+([\w.]+)')  # what a .pth file's start-up line imports
MAX_ARGUMENT = 400  # save() takes arguments up to this long
# Scripts that aren't a check: npm's placeholder ("echo \"Error: no test specified\" && exit 1") and others that only
# print, exit or say true (an "echo no tests || true" passes forever); watch modes and runners that wait for you, which
# never finish (Angular's and Karma's runners watch unless told not to, Cypress's open, Vitest's or Playwright's --ui and
# Playwright's --debug wait for clicks, --inspect-brk waits for a debugger, and -w is _watches'); anything that rewrites
# the project's files, which a check must never do: a fixer (Vue CLI's lint fixes unless told not to, and ruff's
# --fix-only), a formatter that isn't only checking, lint-staged (which runs fixers on what's staged), or a test runner
# told to update its snapshots in any spelling (which also passes every snapshot test); and a script that ends by
# ignoring how what it ran went ("eslint . || true", "jest; exit 0"), which passes whatever failed.
NOT_A_CHECK = (
    re.compile(r'no test specified|^\s*(?:(?:echo|exit|true|:)(?![\w-])[^&|;]*(?:&&|\|\||;)\s*)*(?:echo|exit|true|:)(?![\w-])[^&|;]*(?:;\s*)?$', re.I),
    re.compile(r'--watch(?:All)?(?:=(?:true|1))?(?![=\w-])(?!\s+(?:false|0)\b)|--watch-path\b|(?<!\S)--inspect-brk\b|'
               r'\bng\s+test\b(?!.*(?:--no-watch|--watch[= ]false))|\bkarma\s+start\b(?!.*--single-run)|'
               r'\bcypress\s+open\b|\b(?:vitest|playwright\s+test)\b[^&|;]*(?<!\S)--ui(?![\w-])|'
               r'\bplaywright\s+test\b[^&|;]*(?<!\S)--debug(?![\w-])'),
    re.compile(r'(?<!\S)--(?:fix(?:-only)?|write|apply(?:-unsafe)?)(?![\w-])|\blint-staged\b(?![.\w-])|'
               r'\b(?:jest|vitest|playwright\s+test)\b[^&|;]*(?<!\S)(?:-u|--update(?:Snapshot|-snapshots?)?)(?![\w-])'
               r'(?!=(?:false|none|0)\b)|'
               r'(?:^|[;&|]\s*|\brun\s+|(?<!\S)-m\s+)(?:black|isort)(?![\w.-])(?![^;&|]*--(?:check|diff))|'
               r'\b(?:ruff\s+format|(?:cargo|deno|dprint)\s+fmt)\b(?![^;&|]*--(?:check|diff))|'
               r'\bvue-cli-service\s+lint\b(?![^;&|]*--no-fix)'),
    re.compile(r'(?:\|\||;)\s*(?:true|:|exit\s+0|echo\b[^&|;]*)\s*;?\s*$'),
)
# -w is mocha's and tsc's watch and prettier's write, but jest's worker count when a number follows, and npm's and pnpm's
# workspace (npm test -w packages/core).
WATCH_FLAG = re.compile(r'(?<!\S)-w(?![\w=-])(?!\s+\d)')
SEGMENTS = re.compile(r'&&|\|\||[;&|]')  # where one command in a script ends and the next starts
# A script longer than this isn't offered: reading one takes time that grows faster than its length, and the status
# asks on every read. Real ones are a few hundred characters.
SCRIPT_LIMIT = 2048
SCRIPTS_FOLLOWED = 32  # scripts one check may reach through pre and post scripts and names; past this it isn't offered
# What a package.json script runs by name: npm run lint, pnpm lint, yarn run format, bun run build, concurrently's
# "npm:lint", node --run test, and run-s, run-p or npm-run-all with names or patterns (lint:*).
SCRIPT_RUN = re.compile(r'\b(?:npm|pnpm|yarn|bun)\s+(?:run(?:-script)?\s+)?([\w:.-]+)|\b(?:npm|pnpm|yarn|bun):([\w:.*-]+)|'
                        r'\bnode\s+--run(?:=|\s+)([\w:.-]+)')
RUN_ALL = re.compile(r'\b(?:run-s|run-p|npm-run-all2?)\b(.*)')
# The modules a line imports by name, never relative ones: from helpers import x, import os, helpers as h.
IMPORTS = re.compile(r'^[ \t]*from[ \t]+(\w+)|^[ \t]*import[ \t]+([\w. \t,]+)', re.M)
TESTS_BESIDE = 64  # test files read for a bare import of a module beside them
# An editable install's hook, naming each module and where it imports from: setuptools' finder has a MAPPING
# {'shop': '/x/src/shop'}, and editables (hatchling's exact mode) calls map_module('shop', '/x/src/shop/__init__.py').
EDITABLE_MAPPING = re.compile(r'^MAPPING\b[^=\n]*=[ \t]*(\{.*\})[ \t]*$', re.M)
EDITABLE_MODULE = re.compile(r'''map_module\(\s*('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")\s*,\s*('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")''')


def _stamp(path):
    """How a path looks now, cheaply: its kind, size and change time, or None when it isn't there."""
    try:
        found = os.stat(path)
    except (OSError, ValueError):
        return None
    return (stat.S_IFMT(found.st_mode), found.st_size, found.st_mtime_ns)


class _Look:
    """The paths one suggestion looked at, each with how it first looked, so the next one can tell nothing changed. A
    folder's change time moves when a file is added to it or taken out of it. ``failed`` when something couldn't be
    read, so the answer isn't kept: a permission change doesn't move a change time."""

    def __init__(self):
        self.stamps, self.failed = {}, False

    def _see(self, path):
        # The first look counts: a path that changes between two looks in one suggestion then reads as changed next time.
        return self.stamps.setdefault(str(path), _stamp(path))

    def is_file(self, path):
        seen = self._see(path)
        return bool(seen) and stat.S_ISREG(seen[0])

    def is_dir(self, path):
        seen = self._see(path)
        return bool(seen) and stat.S_ISDIR(seen[0])

    def files(self, folder, *patterns):
        """The files in ``folder`` matching any of ``patterns``, sorted. A pattern that looks below it (``*/*.py``) stamps
        each folder it looks in too, as a file added to one moves only that folder's change time (review, 9 Oct). glob
        passes over a folder it can't read without a word, so one Dervo can't read means the answer isn't kept."""
        if not self.is_dir(folder):
            return []
        try:
            depth = max(pattern.count('/') for pattern in patterns)
            for place in [Path(folder), *(inner for level in range(1, depth + 1) for inner in Path(folder).glob('/'.join(['*'] * level))
                                           if inner.name != '__pycache__' and inner.is_dir())]:
                self._see(place)
                if not os.access(place, os.R_OK | os.X_OK):
                    self.failed = True
            return sorted({path for pattern in patterns for path in Path(folder).glob(pattern) if path.is_file()})
        except OSError:
            self.failed = True
            return []

    def folders(self, folder):
        if not self.is_dir(folder):
            return []
        try:
            return sorted(path for path in Path(folder).iterdir() if path.is_dir() and not path.name.startswith(('.', '_')))
        except OSError:
            self.failed = True
            return []

    def read(self, path, limit):
        if not self.is_file(path):
            return ''
        try:
            with open(path, 'rb') as handle:
                return handle.read(limit).decode('utf-8', 'replace')
        except OSError:
            self.failed = True
            return ''


def _suggest(root, look):
    found = []
    cargo = look.read(root / 'Cargo.toml', SETTINGS_READ)
    if look.is_file(root / 'Cargo.toml'):
        # A package at the top tests only itself, not its workspace's members, unless asked (review, 9 Oct); a project
        # that picked its default members has chosen what its tests are.
        found.append({'name': 'Rust tests', 'argv': ['cargo', 'test', '--no-fail-fast', *([] if 'default-members' in cargo else ['--workspace'])],
                      'cwd': '.', 'timeout': DEFAULT_TIMEOUT})
    for folder in ('.', 'frontend', 'web', 'app'):
        package = root / folder / 'package.json'
        try:
            scripts = json.loads(look.read(package, PACKAGE_READ) or 'null').get('scripts') or {}
        except (ValueError, AttributeError, RecursionError):
            continue
        if not isinstance(scripts, dict):
            continue
        runner = 'pnpm' if look.is_file(root / folder / 'pnpm-lock.yaml') else 'yarn' if look.is_file(root / folder / 'yarn.lock') else 'npm'
        label = '' if folder == '.' else f' ({folder})'
        if _offered(scripts, 'test'):
            found.append({'name': f'Tests{label}', 'argv': [runner, 'test'] if runner != 'npm' else ['npm', 'test'], 'cwd': folder, 'timeout': DEFAULT_TIMEOUT})
        for script in ('typecheck', 'check:types', 'lint'):
            if _offered(scripts, script):
                found.append({'name': f'{script.capitalize()}{label}', 'argv': [runner, 'run', script], 'cwd': folder, 'timeout': DEFAULT_TIMEOUT})
    python = _python_tests(root, look)
    if python:
        found.append(python)
    return found[:MAX_COMMANDS]


def _a_check(script):
    return (isinstance(script, str) and bool(script.strip()) and len(script) <= SCRIPT_LIMIT
            and not any(pattern.search(script) for pattern in NOT_A_CHECK) and not _watches(script))


def _offered(scripts, name):
    """Whether a package.json script makes a check: it is one, and nothing npm runs for it watches, rewrites files or
    ignores a failure. That counts the pre and post scripts npm and Yarn 1 run around each script (review, 9 Oct: a
    pretest that formatted the code rewrote it on every check) and the scripts it runs by name, all the way down, up to
    SCRIPTS_FOLLOWED of them, each read once (second review, 9 Oct: 2,000 chained scripts took 53 s)."""
    if not _a_check(scripts.get(name)):
        return False
    seen, waiting = set(), [name]
    while waiting:
        each = waiting.pop()
        for script in (f'pre{each}', each, f'post{each}'):
            body = scripts.get(script)
            if script in seen or not isinstance(body, str):
                continue
            seen.add(script)
            if len(seen) > SCRIPTS_FOLLOWED or len(body) > SCRIPT_LIMIT:
                return False
            harmless, runs = _script(body, script != each)
            if not harmless:
                return False
            waiting += [found for run in runs for found in ([key for key in scripts if fnmatch.fnmatchcase(key, run)] if '*' in run else [run])]
    return True


@functools.lru_cache(maxsize=1024)
def _script(body, hook):
    """Whether one script a check reaches is harmless, and the scripts it runs by name. A pre or post script (``hook``)
    may ignore its own failure: npm still stops at the check's own."""
    if NOT_A_CHECK[1].search(body) or NOT_A_CHECK[2].search(body) or _watches(body) or not hook and NOT_A_CHECK[3].search(body):
        return False, ()
    runs = []
    for command in SEGMENTS.split(body):
        if command.split()[:1] == ['echo']:
            continue  # echo "run npm run format" names a script without running it
        runs += [name for found in SCRIPT_RUN.findall(command) for name in found if name]
        runs += [word.strip('\'"') for rest in RUN_ALL.findall(command) for word in rest.split() if not word.startswith('-')]
    return True, tuple(runs)


def _watches(script):
    """Whether a command in ``script`` gets -w, mocha's and tsc's watch and prettier's write. npm's and pnpm's own -w
    picks a workspace, so for them only what follows -- counts (second review, 9 Oct: npm test -w packages/core)."""
    for command in SEGMENTS.split(script):
        words = command.split()
        if words[:1] in (['npm'], ['pnpm']):
            command = ' '.join(words[words.index('--') + 1:]) if '--' in words else ''
        if WATCH_FLAG.search(command):
            return True
    return False


def _python_tests(root, look):
    """The project's Python tests as one check, from the first test folder whose files hold tests. pytest when the
    project uses it and its venv has it; else unittest when the files hold unittest tests; else nothing."""
    for tests, project in PYTHON_TESTS:
        direct = look.files(root / tests, 'test_*.py')
        found = direct or look.files(root / tests, '*_test.py')
        if not found and Path(tests).name == 'tests':
            # pytest finds tests in folders under tests/ (tests/unit/test_api.py). Only the first few are read.
            found = list(itertools.islice((path for folder in look.folders(root / tests)
                                           for path in look.files(folder, 'test_*.py', '*_test.py')), TESTS_READ))
        texts = {path: look.read(path, TEST_READ) for path in found[:TESTS_READ]}
        pytest_style = any(PYTEST_STYLE.search(text) for text in texts.values())
        unittest_style = any(UNITTEST_STYLE.search(text) for path, text in texts.items() if path in direct)
        if not (pytest_style or unittest_style):
            continue  # files named like tests that hold none (a test_connection.py script): the next folder may hold the real ones
        base = root / project
        python, sites = _venv(root, project, look)
        settings = ''.join(look.read(place / name, SETTINGS_READ) for place in dict.fromkeys((base, root)) for name in PYTHON_SETTINGS)
        # Each checkout tests its own code. An editable install in the venv imports the project from the project folder
        # whatever folder the tests run in, so on the clean checkout the checks compare with, each new failure would read
        # as already failing (measured, 9 Oct); the folders it names go first on the path instead, as paths from here, even
        # the tests' own folder, for the Python the tests start elsewhere (run_command makes them whole paths). A src
        # layout without one imports from src/ the way the project runs its own tests (a Makefile's PYTHONPATH=src,
        # pytest's pythonpath), unless src is a package the tests import as src. Windows has no env to say this with, so
        # there the tests' own folder, which Python puts on the path for the tests themselves, is left out.
        paths = _editable_paths(root, base, sites, look)
        if paths is None:
            return None
        if (not look.is_file(base / 'src' / '__init__.py')
                and (look.files(base / 'src', '*.py', '*/*.py') or look.files(base / 'src', '*/*/__init__.py'))
                and not any(SRC_IMPORT.search(text) for text in texts.values())):
            paths = list(dict.fromkeys(['src', *paths]))
        if platform_os.WINDOWS:
            paths = [path for path in paths if path != '.']
        if paths and (platform_os.WINDOWS or len('PYTHONPATH=' + os.pathsep.join(paths)) > MAX_ARGUMENT):
            return None
        first = ['env', 'PYTHONPATH=' + os.pathsep.join(paths)] if paths else []
        label = '' if project == '.' else f' ({project})'
        check = {'name': f'Python tests{label}', 'cwd': project, 'timeout': DEFAULT_TIMEOUT}
        if pytest_style or _uses_pytest(root, tests, base, settings, sites, look):
            if not any(look.is_dir(site / 'pytest') for site in sites):
                return None  # it would fail to start on every run: the project's venv doesn't have pytest, or there's no venv
            # pytest collects from the project folder, as the project's own pytest does, but not the files named like tests
            # there that hold none: importing a script runs it (a test_connection.py that exits read unclear forever).
            scripts = [] if tests == project else look.files(base, 'test_*.py', '*_test.py')
            if len(scripts) > 20:
                return None
            return dict(check, argv=[*first, python, '-m', 'pytest', *(f'--ignore={path.name}' for path in scripts)])
        if DJANGO.search(look.read(base / 'manage.py', TEST_READ)):
            return None
        start = [] if tests == project else ['-s', Path(os.path.relpath(root / tests, base)).as_posix()]
        own = [base, base / 'src', *(base / path for path in paths)]
        if start and look.is_file(root / tests / '__init__.py') and not _imports_beside(root / tests, own, texts, look):
            start += ['-t', '.']  # a tests package imports as one, so its relative imports work
        return dict(check, argv=[*first, python, '-m', 'unittest', 'discover', *start, '-p', 'test_*.py'])
    return None


def _imports_beside(tests, own, texts, look):
    """Whether the test files import a module beside them by its bare name (from helpers import x). That works only with
    the tests folder as where imports start, as in the project's own ``discover -s tests``; ``-t .`` would make every
    such file fail to import, before a run and after it (review, 9 Oct). A name the standard library or the project's
    own code (``own``: its folder, src/ and the folders its venv imports it from) also has isn't one: there a tests/shop
    folder beside the shop package, or tests/http beside from http import HTTPStatus, would hide the real module unless
    imports start at the project's folder (second review, 9 Oct)."""
    beside = {path.stem for path in look.files(tests, '*.py')} | {folder.name for folder in look.folders(tests)}
    beside = {name for name in beside - {'__init__', 'conftest'} - set(sys.stdlib_module_names)
              if not any(look.is_file(folder / f'{name}.py') or look.is_dir(folder / name) for folder in own)}
    if not beside:
        return False
    for path in look.files(tests, 'test_*.py', '*_test.py')[:TESTS_BESIDE]:
        for source, listed in IMPORTS.findall(texts[path] if path in texts else look.read(path, TEST_READ)):
            names = {source} | {item.split()[0].split('.')[0] for item in listed.split(',') if item.split()}
            if names & beside:
                return True
    return False


def _uses_pytest(root, tests, base, settings, sites, look):
    """Whether the project runs its tests with pytest: it has a conftest.py, its settings or requirements name pytest,
    or its venv has pytest installed."""
    if any(look.is_file(folder / 'conftest.py') for folder in {base, base / 'tests', root / tests}):
        return True
    return bool(PYTEST_WORD.search(settings)) or any(look.is_dir(site / 'pytest') for site in sites)


def _venv(root, project, look):
    """The project's own virtual environment: its Python, as a whole path, and its site-packages folders. The
    project's .venv or venv, else the repository's when the project is a folder in it; (the usual Python, []) when
    there's none. A whole path, because the same check runs in a thread's own folder, which gets a copy of .venv only
    for some lockfiles (thread_folders), and on the clean checkout, which links the same folder in."""
    for place in dict.fromkeys((project, '.')):
        for venv in VENVS:
            for python in VENV_PYTHONS:
                path = root / place / venv / python
                if look.is_file(path):
                    folder = root / place / venv
                    sites = [folder / 'Lib' / 'site-packages',
                             *(lib / 'site-packages' for lib in look.folders(folder / 'lib') if lib.name.startswith('python'))]
                    return str(path), [site for site in sites if look.is_dir(site)]
    return ('python' if platform_os.WINDOWS else 'python3'), []


def _editable_paths(root, base, sites, look):
    """The folders in the repository that the venv's editable installs put on Python's path, each as a path from
    ``base``, the folder the tests run in. None when an editable install imports from somewhere a path can't say: a hook
    Dervo can't read, or a package in the repository whose folder isn't named like it (package-dir = {shop = "src"})."""
    top, here = os.path.realpath(root), os.path.realpath(base)
    inside = lambda path: path.is_absolute() and (os.path.realpath(path) == top or os.path.realpath(path).startswith(top.rstrip(os.sep) + os.sep))
    found = []
    for site in sites:
        for pth in look.files(site, '*.pth'):
            for line in look.read(pth, SETTINGS_READ).splitlines():
                line = line.strip()
                if line.startswith(('import ', 'import\t')):
                    if 'editable' not in pth.name and 'editable' not in line:
                        continue  # another package's start-up hook, not an editable install
                    # An editable install that imports through a hook (setuptools' finder, hatchling's exact mode) names
                    # each module and where it imports from. The folder that holds its top package goes on the path: up
                    # one folder per dot in the name, so acme.billing at /x/acme/billing is /x, not /x/acme, where an
                    # acme/email folder would hide the standard library's email (review, 9 Oct).
                    pairs = [pair for name in IMPORTED.findall(line) for pair in _hooked(look.read(site / f'{name}.py', SETTINGS_READ))]
                    if not pairs:
                        return None
                    for name, path in pairs:
                        holder = _holder(name, Path(path))
                        if inside(Path(path)) and not (holder and inside(holder)):
                            return None
                        found.append(holder or Path(path))
                elif line and not line.startswith('#'):
                    found.append(Path(line))
    return list(dict.fromkeys(Path(os.path.relpath(os.path.realpath(path), here)).as_posix() for path in found if inside(path)))


def _hooked(text):
    """The (module, whole path) pairs an editable install's hook module maps."""
    pairs = [(_literal(name), _literal(path)) for name, path in EDITABLE_MODULE.findall(text)]
    for mapping in EDITABLE_MAPPING.findall(text):
        names = _literal(mapping)
        pairs += list(names.items()) if isinstance(names, dict) else []
    return [(name, path) for name, path in pairs if isinstance(name, str) and isinstance(path, str)]


def _literal(text):
    try:
        return ast.literal_eval(text)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def _holder(name, path):
    """The folder that, on the path, makes ``name`` import from ``path``, or None when the folders aren't named like
    the module's parts."""
    if path.name == '__init__.py':
        path = path.parent
    elif path.suffix == '.py':
        path = path.with_suffix('')
    parts = tuple(name.split('.'))
    return path.parents[len(parts) - 1] if path.is_absolute() and path.parts[-len(parts):] == parts else None


# Failure names from the usual runners' output. Each is a stable name for one failing test, so the before and after
# sets compare. Output that names none still fails by its exit status, under the command's own name.
PATTERNS = (
    re.compile(r'^(?:FAIL|ERROR): (\S+) \(([^)\s]+)\)', re.M),          # Python unittest: "FAIL: test_x (module.Class.test_x)"
    re.compile(r'^not ok \d+ - (.+?)\s*(?:#.*)?$', re.M),                # node:test and other TAP, top level only
    re.compile(r'^test (\S+) \.\.\. FAILED$', re.M),                     # cargo test
    # pytest's short summary, for failures and for setup, teardown and collection errors: "FAILED tests/x.py::test_a -
    # boom", "ERROR tests/x.py". The name can hold spaces (a parametrize id like test_p[a b]), so the line is read whole
    # and cut by _pytest_name (fifth review, 8 Oct). unittest's "FAILED (failures=2)" and "ERROR: test_a (...)" aren't names.
    re.compile(r'^(?:FAILED|ERROR) (\S.*?)\s*$', re.M),
    re.compile(r'^(\S+?)\(\d+,\d+\): error (TS\d+): (.*)$', re.M),       # tsc: file, code and message, not the line number
    re.compile(r'^\s*FAIL\s+(\S+\.\S+)(?:\s+\(.*\))?\s*$', re.M),             # jest: "FAIL src/a.test.js (5 s)", one per file
    re.compile(r'^\s*--- FAIL: (\S+)', re.M),                              # go test: "--- FAIL: TestX (0.00s)", subtests indented
)
PYTEST, TSC = PATTERNS[3], PATTERNS[4]
# node:test's spec reporter, which Node 24 prints on a pipe instead of TAP: "✖ sends the draft (1.2ms)", top level only
# (a subtest is indented). Read only above its "✖ failing tests:" recap, which repeats every failure, subtests too, at
# the left edge. A "# TODO" test isn't a failure, as in TAP.
NODE_SPEC = re.compile(r'^✖ (.+?) \([\d.]+m?s\)(?: # (TODO|SKIP)\b.*)?$', re.M)
NODE_SPEC_RECAP = re.compile(r'^✖ failing tests:$', re.M)
ANSI = re.compile(r'\x1b\[[0-9;]*m')

# A runner that stops at its first failure (pytest -x or --maxfail, unittest -f, jest --bail, cargo test without
# --no-fail-fast) never reaches the tests after it, so a known failure can hide a new one (fifth review, 8 Oct).
STOPPED_EARLY = re.compile(r'stopping after \d+ failures?|Interrupted: \d+ errors? during collection|'
                           r'Test run was interrupted|error: test failed, to rerun pass|^panic: ', re.I | re.M)
FAIL_FAST_FLAGS = ('-x', '-f', '--exitfirst', '--failfast', '--bail')
# A runner that found no tests to run. A clean exit after none isn't a pass: macOS's Python 3.9 exits 0 for unittest
# discover on pytest-style tests, and a check that never tests anything would let every result commit (critic, 9 Oct).
# The test counts the usual runners print. A run reports no tests only when every count it printed is zero, so a cargo
# doc-test binary's "running 0 tests" or one empty workspace beside real tests never reads as none (review, 9 Oct).
TEST_COUNTS = re.compile(r'^Ran (\d+) tests?\b|^collected (\d+) items?\b|^# tests (\d+)$|^ℹ tests (\d+)$|^running (\d+) tests?$', re.M)
# Words a runner prints when it found nothing, for runners that print no count: pytest, jest and go.
NO_TESTS = re.compile(r'^(?:=+ )?no tests ran\b|^No tests found\b', re.I | re.M)
# unittest's stand-ins for a module it couldn't import or load: every test in it reads as this one error.
IMPORT_FAILED = re.compile(r'unittest\.loader\.(?:_FailedTest|ModuleImportFailure)\b')
GO_EMPTY, GO_RAN = re.compile(r'\[no test files\]'), re.compile(r'^(?:ok|FAIL|---)\s', re.M)


def ran_no_tests(output):
    """Whether a runner's output says it ran no tests at all."""
    plain = ANSI.sub('', output or '')
    counts = [int(next(group for group in match.groups() if group is not None)) for match in TEST_COUNTS.finditer(plain)]
    if counts:
        return not any(counts)
    if GO_EMPTY.search(plain):
        return not GO_RAN.search(plain)
    return bool(NO_TESTS.search(plain))


def _pytest_name(rest):
    """The test id in a pytest summary line, without its " - message". Cut at the first " - " outside the id's
    brackets. None for anything that isn't a test id or a file (unittest's "FAILED (failures=2)", a bare log word)."""
    cut = len(rest)
    for match in re.finditer(' - ', rest):
        head = rest[:match.start()]
        if head.count('[') <= head.count(']'):
            cut = match.start()
            break
    name = rest[:cut].rstrip()
    path = name.split('::', 1)[0]
    if not name or name.startswith('(') or ' ' in path or not ('::' in name or '/' in path or '\\' in path or path.endswith('.py')):
        return None
    return name


def failures(output):
    """The failing test names in a runner's output, sorted and without duplicates."""
    names = set()
    for pattern in PATTERNS:
        for match in pattern.finditer(output or ''):
            groups = match.groups()
            if pattern is PATTERNS[0]:
                # Python 3.11 names the test in the brackets ("mod.Class.test_x"); 3.10 and older, macOS's own python3
                # among them, name only the class ("mod.Class"), so the test's name is added back (review, 8 Oct).
                where = groups[1]
                names.add(where if where.rsplit('.', 1)[-1] == groups[0] else f'{where}.{groups[0]}' if '.' in where else f'{groups[0]} ({where})')
            elif pattern is TSC:
                names.add(f'{groups[0]}: {groups[1]} {groups[2].strip()[:120]}')
            elif pattern is PYTEST:
                name = _pytest_name(groups[0])
                if name:
                    names.add(name)
            elif pattern is PATTERNS[1] and re.search(r'#\s*(TODO|SKIP)\b', match.group(0), re.I):
                continue  # TAP: a TODO or skipped test isn't a failure
            else:
                names.add(groups[0].strip())
    names.update(_jest_tests(output or ''))
    spec = ANSI.sub('', output or '')
    recap = NODE_SPEC_RECAP.search(spec)
    names.update(match.group(1).strip() for match in NODE_SPEC.finditer(spec[:recap.start()] if recap else spec) if not match.group(2))
    return sorted(names)


JEST_FILE = re.compile(r'^\s*(PASS|FAIL)\s+(\S+\.\S+)(?:\s+\(.*\))?\s*$')
JEST_TEST = re.compile(r'^\s+● (.+?)\s*$')


def _jest_tests(output):
    """Each failing jest test by file and title ("src/a.test.js › suite › test"), from the "●" headings under its file's
    FAIL line. The file alone isn't enough: a new failing test in a file that already fails would read as old (sixth
    review, 8 Oct)."""
    names, current = set(), None
    for line in output.splitlines():
        header = JEST_FILE.match(line)
        if header:
            current = header.group(2) if header.group(1) == 'FAIL' else None
            continue
        test = JEST_TEST.match(line) if current else None
        if test:
            names.add(f'{current} › {test.group(1)}')
    return names


def stopped_early(argv, output):
    """Whether the run may have stopped before all its tests: a fail-fast flag, or a runner saying it stopped."""
    flags = [arg for arg in argv[1:] if arg in FAIL_FAST_FLAGS or arg.startswith(('--maxfail', '--bail='))]
    return bool(flags) or bool(STOPPED_EARLY.search(output or ''))


def environment():
    """What a check runs with: the PATH a full-access run gets and the account's home, and nothing about any account."""
    import platform_os
    from claude_coding import full_access_path
    env = platform_os.provider_environment()
    env['HOME'] = os.path.expanduser('~')
    env['PATH'] = full_access_path(env.get('PATH') or os.defpath)
    env['CI'] = '1'  # runners skip watch modes and prompts
    return env


RUNNING = set()  # process groups of checks running now, ended when Dervo closes (stop_all)
STOPS = [0]  # how many times stop_all ran: a job that started before the newest one runs no more steps


def _run_group(argv, cwd, env, timeout):
    """subprocess.run in its own process group, so a time limit or Dervo closing ends what the check started too (npm's
    node, test workers), not only the command itself."""
    import signal
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, errors='replace', **platform_os.new_group())
    RUNNING.add(process.pid)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _end_group(process.pid, signal.SIGKILL)
        stdout, stderr = _leftover(process)
        raise subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr) from None
    finally:
        RUNNING.discard(process.pid)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _leftover(process):
    """What a killed check wrote, without waiting on a child that left its group and still holds the pipes open
    (review, 8 Oct: an unbounded read here turned a 2 s limit into 30 s)."""
    try:
        return process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return '', ''


def _end_group(pid, sig):
    try:
        platform_os.signal_group(pid, sig)
    except (OSError, AttributeError):
        pass


def stop_all():
    """Dervo is closing: end every running check's process group. Their runs read as not run next time."""
    import signal
    STOPS[0] += 1
    for pid in list(RUNNING):
        _end_group(pid, signal.SIGKILL)


def _in_checkout(argv, folder, env):
    """A Python check as its tests and what they start see it (review, 9 Oct). The PYTHONPATH suggested() gives it, each
    checkout's own code first, as whole paths into this checkout, so Python that a test starts in another folder imports
    this checkout's code too. And its venv's scripts first on PATH, as in a terminal with the venv active, so a test
    that runs the project's own command finds it, rather than failing alike before and after a run. The paths start
    from where the folder really is, as suggested() measured them, so a project folder that links elsewhere still finds
    them. A folder named with the path separator (a Finder name with a slash has a colon in its path) would split them
    in two, so there the check keeps its paths from its own folder, as saved, and PATH as it was (second review, 9 Oct)."""
    if argv[:1] == ['env'] and argv[1:2] and argv[1].startswith('PYTHONPATH='):
        here = os.path.realpath(folder)
        paths = [os.path.normpath(os.path.join(here, path)) for path in argv[1][len('PYTHONPATH='):].split(os.pathsep) if path]
        if not any(os.pathsep in path for path in paths):
            argv = ['env', 'PYTHONPATH=' + os.pathsep.join(paths), *argv[2:]]
    python = _python_of(argv)
    if python.is_absolute() and (python.parent.parent / 'pyvenv.cfg').is_file() and os.pathsep not in str(python.parent):
        env = dict(env, PATH=str(python.parent) + os.pathsep + env.get('PATH', ''))
    return argv, env


def _python_of(argv):
    """The program a check runs, after env and its settings."""
    return Path(next((item for item in argv[1:] if '=' not in item), '') if argv[:1] == ['env'] else argv[0] if argv else '')


def run_command(command, root, run=None, clock=time.monotonic):
    """Run one check in ``root`` and answer {name, exit, seconds, failing, output, timed_out}. Never raises."""
    folder = Path(root) / command['cwd']
    started = clock()
    try:
        if not Path(folder).resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('its folder leads outside the project')  # a folder the run replaced with a link (review, 8 Oct)
        argv, env = _in_checkout(command['argv'], folder, environment())
        if run is None:
            done = _run_group(argv, folder, env, command['timeout'])
        else:
            done = run(argv, cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       errors='replace', timeout=command['timeout'])
        output, code, timed_out = (done.stdout or '') + (done.stderr or ''), done.returncode, False
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b'').decode('utf-8', 'replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')) + \
                 ((exc.stderr or b'').decode('utf-8', 'replace') if isinstance(exc.stderr, bytes) else (exc.stderr or ''))
        code, timed_out = None, True
    except (OSError, ValueError) as exc:
        output, code, timed_out = f'{type(exc).__name__}: {exc}', None, False
    failing = failures(output)
    if code != 0 or timed_out:
        # Always, even when tests are named: a runner that also stopped for another reason (a pytest collection error,
        # a crash part-way) exits differently, and that change is a failure of its own (review, 8 Oct, High 1).
        failing.append(f'{command["name"]}: ' + ('ran out of time' if timed_out else 'couldn’t start' if code is None else f'exited {code}'))
    return {'name': command['name'], 'exit': code, 'timed_out': timed_out, 'seconds': round(clock() - started, 1),
            'failing': failing, 'output': output[-OUTPUT_TAIL:],
            'stopped_early': bool(failing) and stopped_early(command['argv'], output),
            'no_tests': code == 0 and not timed_out and ran_no_tests(output)}


def run_all(commands, root, run=None):
    """Each check in turn. Once Dervo starts closing (stop_all), the steps left aren't started."""
    stops, results = STOPS[0], []
    for command in commands:
        if STOPS[0] != stops:
            raise Stopped()
        results.append(run_command(command, root, run=run))
    return results


class Stopped(Exception):
    """Dervo closed while the checks ran."""


BASELINE_FORMAT = 5  # 2: a non-zero exit is always one of a check’s failures. 3: pytest ids with spaces, jest and go names. 4: jest per test. 5: Python checks see each checkout’s code in what their tests start. Older kept baselines are measured again.


def commands_key(commands):
    return hashlib.sha256(json.dumps(dict(commands=commands, format=BASELINE_FORMAT), sort_keys=True).encode()).hexdigest()


LOCKFILES = ('package-lock.json', 'pnpm-lock.yaml', 'yarn.lock', 'Cargo.lock', 'poetry.lock', 'uv.lock', 'Pipfile.lock',
             'requirements.txt', 'go.sum', 'Gemfile.lock')
BASELINE_DAYS = 7  # a clean checkout's result is measured again after this long, in case one of its failures was flaky


def dependencies_key(root):
    """A digest of the project's lockfiles, here and one folder down: installed dependencies changing (which the clean
    checkout borrows) means its old result no longer stands."""
    digest = hashlib.sha256()
    for depth in ('', '*/'):
        for name in LOCKFILES:
            for found in sorted(Path(root).glob(depth + name)):
                try:
                    digest.update(str(found.relative_to(root)).encode() + b'\0' + found.read_bytes())
                except OSError:
                    continue
    return digest.hexdigest()[:16]


def cached_baseline(db, project_id, sha, commands, key=''):
    """What failed on a clean checkout of ``sha`` with these commands and dependencies, if it was measured in the last
    BASELINE_DAYS; else None."""
    schema(db)
    row = db.execute('SELECT document FROM project_check_baselines WHERE project_id=? AND sha=? AND commands=? AND at>?',
                     (project_id, sha, commands_key(commands) + (key or ''), time.time() - BASELINE_DAYS * 86400)).fetchone()
    return json.loads(row[0]) if row else None


def keep_baseline(db, project_id, sha, commands, found, key=''):
    schema(db)
    with db:
        db.execute('INSERT OR REPLACE INTO project_check_baselines VALUES (?,?,?,?,?)',
                   (project_id, sha, commands_key(commands) + (key or ''), json.dumps(found), time.time()))


def clear_scratch(scratch):
    """Clean checkouts a Dervo that closed mid-check left behind. Git forgets them at the next measure's prune."""
    for folder in Path(scratch).glob('baseline-*'):
        shutil.rmtree(folder, ignore_errors=True)


def measure_baseline(project_root, sha, commands, scratch, run=None):
    """Run the commands on a detached checkout of ``sha`` in ``scratch``, then remove it. The project folder itself is
    never touched: no stash, no checkout, no reset."""
    import task_git
    git = run or subprocess.run
    top = task_git.repo_root(str(project_root), run=git)
    if top is None:
        return None
    Path(scratch).mkdir(parents=True, exist_ok=True)
    folder = Path(scratch) / f'baseline-{uuid.uuid4().hex[:12]}'
    # Through task_git, so the repository's own hooks, filters and fsmonitor don't run as the checkout is made (review).
    added = task_git._git(top, 'worktree', 'add', '--detach', '--quiet', str(folder), sha, run=git, timeout=120)
    if added.returncode != 0:
        return None
    try:
        relative = Path(project_root).resolve().relative_to(Path(top).resolve())
        root = folder / relative
        _link_dependencies(Path(project_root), root)
        return {result['name']: result['failing'] for result in run_all(commands, root, run=run)}
    finally:
        task_git._git(top, 'worktree', 'remove', '--force', str(folder), run=git, timeout=120)
        shutil.rmtree(folder, ignore_errors=True)
        task_git._git(top, 'worktree', 'prune', run=git, timeout=60)


def _link_dependencies(source, target):
    """Installed dependency folders (ignored by git, so missing from a fresh checkout) linked from the project, one and
    two levels down, following a project's own link to where they really are. Read by the checks, never written by
    Dervo. Without them the clean checkout's checks can't start, and nothing could be called already failing."""
    for depth in ('', '*/'):
        for name in DEPENDENCY_FOLDERS:
            for found in source.glob(depth + name):
                if found.is_dir():
                    place = target / found.relative_to(source)
                    if place.parent.is_dir() and not place.exists() and not place.is_symlink():
                        place.symlink_to(found.resolve(), target_is_directory=True)


def _only_ran_badly(name, failing):
    """Whether a check's failures are just its own "exited 2" or "couldn't start", with no test named."""
    return bool(failing) and all(item.startswith(f'{name}: ') for item in failing)


def compare(after, before):
    """The run's result per check: what's failing now, split into already failing (before) and new. When the clean
    checkout's check only failed to run (named no test) and this run's names tests, the two can't be compared: that
    check's failures are ``unclear``, never called new or already failing (dogfooding on Dervo, 8 Oct: a clean checkout
    without its dependencies made every existing failure look new)."""
    results = []
    for item in after:
        known = set((before or {}).get(item['name']) or [])
        failing = item['failing']
        if item.get('no_tests'):
            # It exited cleanly having run nothing: never called passed, so nothing commits on its say-so.
            results.append(dict(item, new=[], already=[], fixed=[], unclear=[f'{item["name"]}: ran no tests'], why='no_tests'))
            continue
        if any(IMPORT_FAILED.search(name) and name in known for name in failing) and all(name in known for name in failing):
            # A test module that couldn't import before this run and still can't names its tests as the same loader
            # error, so "already failing" would hide that they never ran (critic, 9 Oct). Never called passed. An import
            # this run broke, or any other new failure, still reads as new.
            results.append(dict(item, new=[], already=[], fixed=[], unclear=list(failing), why='import_failed'))
            continue
        if failing and (len(failing) > MAX_NAMES or len(known) > MAX_NAMES):
            # Too many to compare name by name: it can't be called passed (review, 8 Oct, High 2).
            results.append(dict(item, new=[], already=[], fixed=[], unclear=list(failing), why='too_many'))
            continue
        if _only_ran_badly(item['name'], failing):
            # It failed and named no test, so nothing says whether what failed before is all that fails now, even with
            # the same exit code (fourth review, 8 Oct, High 2). Only a clean exit passes.
            results.append(dict(item, new=[], already=[], fixed=[], unclear=list(failing), why='no_names'))
            continue
        if before is not None and _only_ran_badly(item['name'], known) and failing:
            # The clean checkout's check named no test: either it couldn't run, or its runner's output isn't one Dervo
            # reads. Either way a failure now can't be told apart from one before, so it's never "already failing"
            # (review, 8 Oct: a jest project's one known failure hid every new one).
            results.append(dict(item, new=[], already=[], fixed=[], unclear=list(failing), why='no_baseline'))
            continue
        if item.get('stopped_early') and not [name for name in failing if name not in known]:
            # It stopped at a failure that was already there, so the tests after it never ran (fifth review, 8 Oct).
            results.append(dict(item, new=[], already=[], fixed=[], unclear=list(failing), why='stopped_early'))
            continue
        results.append(dict(item, new=[name for name in failing if name not in known],
                            already=[name for name in failing if name in known],
                            fixed=sorted(known - set(failing)), unclear=[]))
    return results


def _shadowed(results, commands, root, base_sha, run=None):
    """A Python check that ran in another checkout than the one its venv imports the project from (a thread's own folder,
    with the project installed editable from the project folder) can't see a module the run deleted: Python imports the
    project folder's copy instead, and the tests pass (second review, 9 Oct: a thread that deleted shop/legacy.py, still
    imported, read "Checks pass"). So that check's pass is unclear when the run deleted a Python file that a folder it
    imports from still has. Never called for the clean checkout, which has every file."""
    import task_git
    git, argvs = run or subprocess.run, {command['name']: command['argv'] for command in commands}
    top = gone = None
    for index, result in enumerate(results):
        argv = argvs.get(result['name'])
        elsewhere = _imports_elsewhere(argv, root) if argv and not result.get('new') and not result.get('unclear') else []
        if not elsewhere:
            continue
        if top is None:
            top = task_git.repo_root(str(root), run=git) or ''
        elsewhere = [(place, kind) for place, kind in elsewhere if top and not _within(place, top)]
        if not elsewhere:
            continue
        if gone is None:
            listed = task_git._git(top, 'diff', '--no-renames', '--diff-filter=D', '--name-only', '-z', base_sha, '--', '*.py',
                                   run=git, timeout=120)
            gone = [name for name in listed.stdout.split('\0') if name] if listed.returncode == 0 else False
        still = [name for name in gone if any(_still_has(place, kind, name) for place, kind in elsewhere)] if gone is not False else \
            [f'{result["name"]}: couldn’t list what this run deleted']
        if still:
            results[index] = dict(result, unclear=still, why='deleted')
    return results


def _imports_elsewhere(argv, top):
    """What a Python check's venv imports code from outside ``top``, through a path file or an editable install's hook,
    each as (whole real path, kind): 'path' for a folder it puts on Python's path, 'module' for a module or package a
    hook maps by name. The project folder's, when the check runs in a thread's own folder. [] for any other check."""
    python = _python_of(argv)
    venv = python.parent.parent
    if not (python.is_absolute() and (venv / 'pyvenv.cfg').is_file()):
        return []
    found = []
    for site in [venv / 'Lib' / 'site-packages', *sorted(venv.glob('lib/python*/site-packages'))]:
        for pth in sorted(site.glob('*.pth')):
            for line in _text(pth).splitlines():
                line = line.strip()
                if line.startswith(('import ', 'import\t')):
                    if 'editable' in pth.name or 'editable' in line:
                        found += [(path, 'module') for name in IMPORTED.findall(line) for _, path in _hooked(_text(site / f'{name}.py'))]
                elif line and not line.startswith('#'):
                    found.append((site / line, 'path'))  # a path file's line is from its own folder unless it's whole
    real = dict.fromkeys((Path(os.path.realpath(path)), kind) for path, kind in found)
    return [(path, kind) for path, kind in real if not _within(path, top) and not _within(path, venv)]


def _within(path, folder):
    folder = Path(os.path.realpath(folder))
    return path == folder or folder in path.parents


def _still_has(place, kind, name):
    """Whether the checkout ``place`` is in still has ``name`` (a path from a checkout's top, as git lists it) where Python
    finds it through ``place`` once this checkout's copy is gone: a module or package right in a folder on the path, or
    a module a hook maps (setuptools names one without its .py) and the modules right in a package it maps (deeper ones
    import through this checkout's own package). A place outside any checkout can't be matched up, so it doesn't count."""
    top = next((folder for folder in (place, *place.parents) if (folder / '.git').exists()), None)
    copy = top / name if top else None
    if copy is None or not copy.is_file():
        return False
    if kind == 'path':
        return copy.parent == place or copy.name == '__init__.py' and copy.parent.parent == place
    return copy == place or copy.with_suffix('') == place or copy.parent == place


def _text(path):
    try:
        with open(path, 'rb') as handle:
            return handle.read(SETTINGS_READ).decode('utf-8', 'replace')
    except OSError:
        return ''


def _shown(result):
    """A check's result as the run's record keeps it: each list of names cut to SHOWN_NAMES, with the full counts."""
    lists = ('failing', 'new', 'already', 'fixed', 'unclear')
    counts = {f'{key}_count': len(result.get(key) or []) for key in lists}
    return dict(result, **{key: (result.get(key) or [])[:SHOWN_NAMES] for key in lists if key in result}, **counts)


def _unclear_line(result):
    """Why one check's failures can't be sorted into new and already failing."""
    name, why = result['name'], result.get('why')
    if why == 'no_names':
        exit_words = next((item[len(name) + 2:] for item in result['unclear'] if item.startswith(f'{name}: ')), 'failed')
        return f'{name} {exit_words} without naming a test, so Dervo can’t tell what’s new'
    if why == 'import_failed':
        return f'{name} couldn’t import some of its tests, so Dervo can’t tell what’s new'
    if why == 'no_tests':
        return f'{name} ran no tests, so it doesn’t count as passing'
    if why == 'stopped_early':
        return f'{name} stopped at a failure that was already there, so the tests after it didn’t run and Dervo can’t tell what’s new'
    if why == 'deleted':
        gone = result['unclear']
        if gone[0].startswith(f'{name}: '):
            return f'{name} imports this project from another folder, and Dervo couldn’t list what this run deleted, so it can’t tell whether anything still needs it'
        what = gone[0] + (f' and {len(gone) - 1} more' if len(gone) > 1 else '')
        return f'This run deleted {what}, which {name} can still import from the project folder, so Dervo can’t tell whether anything still needs it'
    if why == 'too_many':
        return f'{name} has {len(result["unclear"])} failing, more than Dervo compares one by one, so it can’t tell which are new'
    count = sum(1 for item in result['unclear'] if not item.startswith(f'{name}: ')) or len(result['unclear'])
    return f'{count} failing in {name}, and Dervo couldn’t run it on the code before this run to tell which are new'


def summary(results, compared=True):
    """One line for the run's record, Home and the thread's live line."""
    if not results:
        return None

    def named(key):
        # A check's own "exited 1" is compared like any failure, but beside test names in the same list it isn't counted.
        return [name for r in results for name in r.get(key) or []
                if not (name.startswith(f'{r["name"]}: ') and any(not other.startswith(f'{r["name"]}: ') for other in r.get(key) or []))]
    new, already = len(named('new')), len(named('already'))
    unclear = [r['name'] for r in results if r.get('unclear')]
    if unclear and not new:
        return '. '.join(_unclear_line(r) for r in results if r.get('unclear'))
    if not compared:
        failing = len(named('failing'))
        return 'Checks pass' if not failing else f'{failing} failing (Dervo couldn’t check which were failing before)'
    if new:
        names = named('new')
        first = names[0] if len(names) == 1 else ', '.join(names[:2]) + (f' and {len(names) - 2} more' if len(names) > 2 else '')
        return f'{new} new failure{"s" if new != 1 else ""}: {first}' + (f'. {already} {"was" if already == 1 else "were"} already failing' if already else '')
    was = f'. {already} {"was" if already == 1 else "were"} already failing before this run' if already else ''
    if already:
        return 'No new failures' + was
    return 'Checks pass'


def check_run(project_root, base_sha, commands, scratch, cached=None, run=None, key=None):
    """The whole job, on a thread, with no database: the run's checks, then (only when something fails, and only once
    per commit) the clean checkout's, then the comparison. ``measured`` is a new baseline for the caller to keep.
    Never raises."""
    try:
        after = run_all(commands, project_root, run=run)
        before, measured = cached, None
        if before is None and base_sha and any(r['failing'] for r in after):
            before = measured = measure_baseline(project_root, base_sha, commands, scratch, run=run)
        results = compare(after, before)
        if base_sha:
            results = _shadowed(results, commands, project_root, base_sha, run=run)
        compared = before is not None or not any(r['failing'] for r in after)
        new = sum(len(r['new']) for r in results) if compared else sum(len(r['failing']) for r in results)
        # A new failure is never rerun to call it flaky: one that fails only sometimes is still new (third review), and a
        # rerun cost a whole second pass (fourth review). It waits for you either way.
        unclear = any(r.get('unclear') for r in results)
        return {'state': 'new_failures' if new else 'unclear' if unclear else 'passed', 'compared': compared, 'base': base_sha,
                'summary': summary(results, compared), 'results': [_shown(r) for r in results], 'ended': time.time(),
                'measured': measured, 'key': key}
    except Stopped:
        return {'state': 'not_run', 'summary': 'Dervo closed before the checks finished', 'results': [], 'ended': time.time(),
                'measured': None}
    except Exception as exc:  # noqa: BLE001 - a check that can't run says so; the result still waits for you
        return {'state': 'not_run', 'summary': f'Dervo couldn’t run the checks: {type(exc).__name__}', 'results': [],
                'ended': time.time(), 'measured': None}
