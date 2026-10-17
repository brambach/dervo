#!/usr/bin/env python3
"""Read-only preflight for the prerequisites build.sh depends on.

What it does
  * Resolves the node, cargo and python3 executables build.sh needs, using the
    same override names build.sh honours: --node/--cargo/--python flags here,
    or the DERVO_NODE, DERVO_CARGO and DERVO_PYTHON environment variables.
    Otherwise absolute PATH directories are searched. An invalid override is a
    failure; the preflight never falls back to PATH when an override was given,
    and it never guesses user-specific paths.
  * Confirms the frontend dependency entry points build.sh invokes with node,
    and that the fixed macOS codesign tool build.sh calls is present.
  * Confirms runtime/python.json and scripts/python_runtime.py are present:
    build.sh stages the app's own Python 3.13 from them after bundling.
  * Reports platform and Python interpreter facts, and separately reports the
    Python 3.13 framework runtime that source checkouts, backend tests,
    src/bridge.rs and the frontend host harnesses still pin. Built apps carry
    their own Python since beta.17. DERVO_PYTHON does not change the pin.
  * Prints a human report or --json, and exits nonzero when a required
    prerequisite is absent.

What it never does
  * Run node, cargo, python3 or any other tool. Versions aren't discovered by
    execution, and no rust-toolchain file pins a Rust version.
  * Install anything, launch models, read credentials, .env or auth files, or
    modify configuration. It only stats files and reads its own arguments.
  * Claim that "ready" means a build succeeded, that runtime or security
    verification ran, or that packaging or portability is solved.

Symlink policy
  Executable candidates and overrides may be symlinks. They're accepted when
  the final target is a regular file the current user may execute. Dangling
  symlinks are reported as missing. Both the given path and the resolved
  target are reported. Standard library only.
"""
import argparse
import dataclasses
import json
import os
from pathlib import Path
import platform as platform_module
import sys

SCHEMA = 'dervo.build-prerequisites/2'
FRAMEWORK_PREFIX = '/Library/Frameworks/Python.framework/Versions/3.13'
SANDBOX_PYTHON = '/usr/local/bin/python3'
SANDBOX_EXEC = '/usr/bin/sandbox-exec'
CODESIGN = '/usr/bin/codesign'
MINIMUM_PYTHON = (3, 9)
READY, MISSING, UNSUPPORTED = 'ready', 'missing', 'unsupported'
TOOLS = ('node', 'cargo', 'python3')
ENV_OVERRIDES = {'node': 'DERVO_NODE', 'cargo': 'DERVO_CARGO', 'python3': 'DERVO_PYTHON'}
FLAG_OVERRIDES = {'node': '--node', 'cargo': '--cargo', 'python3': '--python'}
ROOT_ENTRIES = (('build.sh', 'file'), ('backend', 'dir'), ('frontend', 'dir'), ('Cargo.toml', 'file'))
FRONTEND_ENTRYPOINTS = (
    ('frontend_typescript', 'TypeScript compiler (build.sh: tsc --noEmit)',
     'frontend/node_modules/typescript/bin/tsc'),
    ('frontend_vite', 'Vite bundler (build.sh: vite build)',
     'frontend/node_modules/vite/bin/vite.js'),
    ('frontend_tauri_cli', 'Tauri CLI (build.sh: tauri build)',
     'frontend/node_modules/@tauri-apps/cli/tauri.js'),
)
MEANING = ('"ready" means only that every required check in this preflight passed. '
           'It is not a successful build, and no runtime, sandbox or security verification ran.')
NOT_CHECKED = (
    'Build success: nothing was compiled, bundled or signed.',
    'Tool versions: node, cargo and python3 were not executed, so their versions are not verified. '
    'No rust-toolchain file pins a Rust version; build.sh uses whichever cargo the caller supplies.',
    'Runtime or security verification: sandbox confinement, code signing and the content security '
    'policy were not exercised. Only the presence of the codesign tool is checked.',
    'Provider identity, sign-in state and model availability: not inferred and not checked.',
    'Packaging and portability: this reports facts about this machine only. The backend sandbox '
    'interpreter pin, the frontend host harness interpreter and other hardcoded paths outside build.sh '
    'remain as they are.',
    'The app\'s own Python: runtime/python.json is only checked for presence. Its archive is fetched and '
    'its SHA-256 checked by scripts/python_runtime.py during build.sh, which needs the network the first time.',
    'Frontend dependency completeness beyond the three entry points, and the Playwright browser. '
    'PLAYWRIGHT_CHROMIUM_EXECUTABLE is validated by build.sh at run time, not here.',
    'Credentials, .env files and auth files: deliberately never read.',
)


@dataclasses.dataclass(frozen=True)
class Override:
    value: str
    source: str  # a flag such as "--node" or a variable such as "DERVO_NODE"


@dataclasses.dataclass
class Facts:
    """Everything the checks look at. Tests inject these; main() fills them from the system."""
    root: Path
    platform: str
    machine: str
    python_version: tuple
    python_executable: str
    python_prefix: str
    path_dirs: list
    node_override: 'Override | None' = None
    cargo_override: 'Override | None' = None
    python_override: 'Override | None' = None
    framework_prefix: Path = Path(FRAMEWORK_PREFIX)
    sandbox_python: Path = Path(SANDBOX_PYTHON)
    sandbox_exec: Path = Path(SANDBOX_EXEC)
    codesign: Path = Path(CODESIGN)

    @classmethod
    def from_system(cls, root, *, node, cargo, python, environ):
        """Reads only PATH and the three documented override variables from environ."""
        path_value = environ.get('PATH')
        return cls(
            root=Path(root),
            platform=sys.platform,
            machine=platform_module.machine(),
            python_version=tuple(sys.version_info[:3]),
            python_executable=sys.executable or '',
            python_prefix=sys.prefix,
            path_dirs=path_value.split(os.pathsep) if path_value is not None else [],
            node_override=_override('node', node, environ),
            cargo_override=_override('cargo', cargo, environ),
            python_override=_override('python3', python, environ),
        )

    def override_for(self, name):
        return {'node': self.node_override, 'cargo': self.cargo_override, 'python3': self.python_override}[name]


def _override(name, flag_value, environ):
    if flag_value is not None:
        return Override(flag_value, FLAG_OVERRIDES[name])
    variable = ENV_OVERRIDES[name]
    if variable in environ:
        return Override(environ[variable], variable)
    return None


@dataclasses.dataclass
class Check:
    id: str
    title: str
    required: bool
    status: str
    detail: str
    path: 'str | None' = None
    resolved: 'str | None' = None

    def as_dict(self):
        data = {'id': self.id, 'title': self.title, 'required': self.required,
                'status': self.status, 'detail': self.detail}
        if self.path is not None:
            data['path'] = self.path
        if self.resolved is not None:
            data['resolved'] = self.resolved
        return data


def _kind(path):
    """Classify a path without raising. Returns (kind, error_text)."""
    try:
        if path.is_symlink() and not path.exists():
            return 'dangling', ''
        if not path.exists():
            return 'absent', ''
        if path.is_dir():
            return 'dir', ''
        if path.is_file():
            return 'file', ''
        return 'other', ''
    except OSError as error:
        return 'error', error.strerror or str(error)


def _executable(path):
    """Returns (ok, reason, resolved_target). Symlinks to executable regular files are accepted."""
    kind, error = _kind(path)
    if kind == 'dangling':
        return False, 'is a dangling symlink', None
    if kind == 'absent':
        return False, 'does not exist', None
    if kind == 'error':
        return False, f'could not be inspected ({error})', None
    if kind == 'dir':
        return False, 'is a directory, not an executable file', None
    if kind != 'file':
        return False, 'is not a regular file', None
    try:
        if not os.access(path, os.X_OK):
            return False, 'is not executable by the current user', None
        return True, '', os.path.realpath(path)
    except OSError as error:
        return False, f'could not be inspected ({error.strerror or error})', None


def resolve_executable(name, override, path_dirs):
    title = f'{name} executable (build.sh: {ENV_OVERRIDES[name]} or PATH)'
    if override is not None:
        source = override.source
        if override.value == '':
            return Check(name, title, True, MISSING,
                         f'{source} is set but empty. Fix or unset it. No PATH fallback is attempted '
                         'for an explicit override.')
        given = Path(override.value)
        ok, reason, resolved = _executable(given)
        if ok:
            return Check(name, title, True, READY, f'{given} from {source}', path=str(given), resolved=resolved)
        return Check(name, title, True, MISSING,
                     f'{source}={override.value} {reason}. No PATH fallback is attempted for an '
                     'explicit override.', path=str(given))
    searched = 0
    for entry in path_dirs:
        if not entry or not os.path.isabs(entry):
            continue  # empty and relative PATH entries are skipped deliberately
        searched += 1
        candidate = Path(entry) / name
        ok, reason, resolved = _executable(candidate)
        if ok:
            return Check(name, title, True, READY, f'{candidate} found on PATH (directory {entry})',
                         path=str(candidate), resolved=resolved)
    return Check(name, title, True, MISSING,
                 f'no executable named {name} in {searched} absolute PATH directories (empty and relative '
                 f'entries are skipped). Pass {FLAG_OVERRIDES[name]} or set {ENV_OVERRIDES[name]} to an '
                 'explicit path.')


def check_root(root):
    kind, error = _kind(root)
    if kind in ('absent', 'dangling'):
        return Check('root', 'source root', True, MISSING, f'{root} does not exist', path=str(root))
    if kind == 'error':
        return Check('root', 'source root', True, MISSING, f'{root} is not readable ({error})', path=str(root))
    if kind != 'dir':
        return Check('root', 'source root', True, MISSING, f'{root} is not a directory', path=str(root))
    try:
        if not os.access(root, os.R_OK | os.X_OK):
            raise PermissionError('permission denied')
        os.listdir(root)
    except OSError as error:
        return Check('root', 'source root', True, MISSING,
                     f'{root} is not readable by the current user ({error.strerror or error})', path=str(root))
    wrong = [f'{name} ({"directory" if expected == "dir" else "regular file"} expected)'
             for name, expected in ROOT_ENTRIES if _kind(root / name)[0] != expected]
    if wrong:
        return Check('root', 'source root', True, MISSING,
                     f'{root} lacks expected source entries: {", ".join(wrong)}. Expected build.sh and '
                     'Cargo.toml as regular files, backend/ and frontend/ as directories.', path=str(root))
    return Check('root', 'source root', True, READY,
                 f'{root} contains build.sh, backend/, frontend/ and Cargo.toml', path=str(root))


def check_platform(facts):
    if facts.platform != 'darwin':
        return Check('platform', 'macOS build target', True, UNSUPPORTED,
                     'build.sh targets macOS: it signs a Tauri .app bundle with /usr/bin/codesign. '
                     f'This platform reports {facts.platform}/{facts.machine}.')
    if facts.machine not in ('arm64', 'aarch64'):
        return Check('platform', 'macOS build target', True, UNSUPPORTED,
                     f'This Mac reports {facts.machine}. The staged build has only been exercised on Apple '
                     'silicon, so this preflight reports unsupported rather than claiming Intel support.')
    return Check('platform', 'macOS build target', True, READY,
                 f'macOS on {facts.machine}, the only configuration the staged build has been exercised on')


def check_codesign(facts):
    title = 'codesign tool (build.sh: /usr/bin/codesign)'
    if facts.platform != 'darwin':
        return Check('codesign', title, True, UNSUPPORTED,
                     f'build.sh signs the bundle with {facts.codesign}, which is a macOS tool. '
                     f'Not available on {facts.platform}.', path=str(facts.codesign))
    ok, reason, resolved = _executable(facts.codesign)
    if ok:
        return Check('codesign', title, True, READY,
                     f'{facts.codesign} is present. Signing itself is not exercised here.',
                     path=str(facts.codesign), resolved=resolved)
    return Check('codesign', title, True, MISSING,
                 f'{facts.codesign} {reason}. build.sh calls it at this fixed path for the signing steps.',
                 path=str(facts.codesign))


def check_entrypoints(root):
    checks = []
    for check_id, title, relative in FRONTEND_ENTRYPOINTS:
        target = root / relative
        kind, error = _kind(target)
        if kind == 'file':
            checks.append(Check(check_id, title, True, READY, f'{relative} is present', path=str(target)))
        elif kind == 'error':
            checks.append(Check(check_id, title, True, MISSING,
                                f'{relative} could not be inspected ({error})', path=str(target)))
        else:
            checks.append(Check(check_id, title, True, MISSING,
                                f'{relative} is missing. build.sh runs it with node. Frontend dependencies '
                                'need installing first; this preflight never installs.', path=str(target)))
    return checks


PYTHON_RUNTIME_FILES = ('runtime/python.json', 'scripts/python_runtime.py')


def check_python_pin(root):
    absent = [name for name in PYTHON_RUNTIME_FILES if _kind(root / name)[0] != 'file']
    title = "the app's own Python 3.13 (runtime/python.json)"
    if absent:
        return Check('python_runtime_pin', title, True, MISSING,
                     f'{", ".join(absent)} missing. build.sh stages the app\'s own Python from the pin after '
                     'bundling, and a Mac app without it stops at startup.', path=str(root / PYTHON_RUNTIME_FILES[0]))
    return Check('python_runtime_pin', title, True, READY,
                 'runtime/python.json and scripts/python_runtime.py are present. build.sh fetches the pinned archive '
                 'into ~/Library/Caches/Dervo/build-downloads (about 25 MB, once), checks its SHA-256 and stages it '
                 'into the app. Not fetched or verified here.', path=str(root / PYTHON_RUNTIME_FILES[0]))


def _version_text(version):
    return '.'.join(str(part) for part in version[:3])


def check_python_runtime(facts):
    version = _version_text(facts.python_version)
    status = READY if tuple(facts.python_version[:2]) >= MINIMUM_PYTHON else UNSUPPORTED
    detail = (f'Python {version} at {facts.python_executable} (prefix {facts.python_prefix}) on '
              f'{facts.platform}/{facts.machine}. These are facts about the interpreter running this '
              'preflight. build.sh runs the backend unit tests with the python3 resolved in the python3 '
              'check (DERVO_PYTHON or PATH), which may be a different interpreter. Nothing here claims the '
              'macOS Python sandbox is portable.')
    if status == UNSUPPORTED:
        detail = f'Python {version} is older than {_version_text(MINIMUM_PYTHON)}. ' + detail
    return Check('python_runtime', 'Python interpreter facts', True, status, detail,
                 path=facts.python_executable or None)


def check_python_framework(facts):
    title = 'pinned Python 3.13 sandbox runtime (macOS only, informational)'
    portability = ('Built apps carry their own Python 3.13 (runtime/python.json). In a source checkout, '
                   'backend/task_evidence.py, src/bridge.rs and the frontend host harnesses pin the Python 3.13 '
                   f'framework runtime and {facts.sandbox_python} on macOS. DERVO_PYTHON does not change that '
                   'pin. It is builder-specific and not portable; this preflight only reports it.')
    if facts.platform != 'darwin':
        return Check('python_313_framework', title, False, UNSUPPORTED,
                     f'Requires macOS sandbox-exec and the Python 3.13 framework at {facts.framework_prefix}; '
                     f'unavailable on {facts.platform}. {portability}')
    pieces = (
        (facts.framework_prefix, 'Python 3.13 framework directory', lambda p: _kind(p)[0] == 'dir'),
        (facts.sandbox_exec, 'sandbox-exec', lambda p: _kind(p)[0] == 'file'),
        (facts.sandbox_python, 'pinned sandbox interpreter', lambda p: _executable(p)[0]),
    )
    absent = [f'{label} at {path}' for path, label, present in pieces if not present(path)]
    try:
        same = os.path.realpath(facts.python_prefix) == os.path.realpath(facts.framework_prefix)
    except OSError:
        same = False
    running = (f'The interpreter running this preflight has prefix {facts.python_prefix}, which '
               f'{"matches" if same else "does not match"} the pinned framework.')
    if absent:
        return Check('python_313_framework', title, False, MISSING,
                     f'Missing: {"; ".join(absent)}. {portability} {running}', path=str(facts.framework_prefix))
    return Check('python_313_framework', title, False, READY,
                 f'Python 3.13 framework directory {facts.framework_prefix}, {facts.sandbox_exec} and '
                 f'{facts.sandbox_python} are present. {portability} {running}', path=str(facts.framework_prefix))


def run_checks(facts):
    root = Path(os.path.abspath(facts.root))
    checks = [check_root(root), check_platform(facts), check_codesign(facts)]
    checks.extend(resolve_executable(name, facts.override_for(name), facts.path_dirs) for name in TOOLS)
    checks.extend(check_entrypoints(root))
    checks.append(check_python_pin(root))
    checks.append(check_python_runtime(facts))
    checks.append(check_python_framework(facts))
    return checks


def build_report(facts):
    checks = run_checks(facts)
    overrides = {name: dataclasses.asdict(facts.override_for(name)) if facts.override_for(name) else None
                 for name in TOOLS}
    return {
        'schema': SCHEMA,
        'ready': all(c.status == READY for c in checks if c.required),
        'meaning': MEANING,
        'root': os.path.abspath(facts.root),
        'facts': {
            'platform': facts.platform,
            'machine': facts.machine,
            'python_version': _version_text(facts.python_version),
            'python_executable': facts.python_executable,
            'python_prefix': facts.python_prefix,
            'path_dirs': list(facts.path_dirs),
            'overrides': overrides,
            'override_names': {'flags': [FLAG_OVERRIDES[name] for name in TOOLS],
                               'environment': [ENV_OVERRIDES[name] for name in TOOLS]},
        },
        'checks': [c.as_dict() for c in checks],
        'not_checked': list(NOT_CHECKED),
    }


def exit_status(report):
    return 0 if report['ready'] else 1


def render_text(report):
    facts = report['facts']
    lines = ['Dervo build prerequisite preflight (read-only)',
             f'root: {report["root"]}',
             f'platform: {facts["platform"]}/{facts["machine"]}; python {facts["python_version"]} '
             f'({facts["python_executable"]})', '']
    width = max(len(c['id']) for c in report['checks'])
    for check in report['checks']:
        kind = 'required' if check['required'] else 'info'
        lines.append(f'[{check["status"]}]'.ljust(14) + check['id'].ljust(width + 2) + f'({kind}) {check["title"]}')
        lines.append(' ' * 14 + check['detail'])
    failed = [c['id'] for c in report['checks'] if c['required'] and c['status'] != READY]
    lines.append('')
    if report['ready']:
        lines.append('Result: READY. All required preflight checks passed.')
    else:
        lines.append(f'Result: NOT READY. Required checks missing or unsupported: {", ".join(failed)}.')
    lines.append(report['meaning'])
    lines.append('')
    lines.append('Not checked:')
    lines.extend(f'  - {item}' for item in report['not_checked'])
    return '\n'.join(lines) + '\n'


def build_parser():
    parser = argparse.ArgumentParser(
        prog='check-build-prerequisites.py',
        description='Read-only preflight for the node, cargo, python3, codesign, frontend dependency and '
                    'platform prerequisites that build.sh relies on. Exits 0 when every required check is '
                    'ready, 1 otherwise. Nothing is executed, installed or modified.',
        epilog=(
            'Overrides: --node PATH, --cargo PATH and --python PATH, or the environment variables DERVO_NODE, '
            'DERVO_CARGO and DERVO_PYTHON that build.sh honours (flags win). An override that is missing, '
            'empty, a directory or not executable fails the check; PATH is never used as a fallback for an '
            'explicit override. Without an override, absolute PATH directories are searched in order and '
            'empty or relative entries are skipped. Symlinks are accepted when the resolved target is an '
            'executable regular file; dangling symlinks are reported as missing. Only PATH and the three '
            'DERVO_* variables are read from the environment. .env, auth files and credentials are never '
            'read. DERVO_PYTHON selects the interpreter for the backend unit tests only; the backend sandbox '
            'runtime pin is reported separately and is not changed. "ready" covers these checks only, not a '
            'build, runtime or security verification.'),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('root', nargs='?', default=str(Path(__file__).resolve().parents[1]),
                        help='source root containing build.sh (default: the parent of this scripts/ directory)')
    parser.add_argument('--node', metavar='PATH', help='explicit node executable; overrides PATH and DERVO_NODE')
    parser.add_argument('--cargo', metavar='PATH', help='explicit cargo executable; overrides PATH and DERVO_CARGO')
    parser.add_argument('--python', metavar='PATH',
                        help='explicit python3 executable for backend tests; overrides PATH and DERVO_PYTHON')
    parser.add_argument('--json', action='store_true', help='emit the report as JSON instead of text')
    return parser


def main(argv=None, environ=None, stdout=None, stderr=None):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    args = build_parser().parse_args(argv)
    facts = Facts.from_system(args.root, node=args.node, cargo=args.cargo, python=args.python, environ=environ)
    report = build_report(facts)
    if args.json:
        stdout.write(json.dumps(report, indent=2) + '\n')
    else:
        stdout.write(render_text(report))
    return exit_status(report)


if __name__ == '__main__':
    sys.exit(main())
