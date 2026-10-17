"""The seam between the Dervo app backend and the packaged Dervo harness library (``dervo_harness``).

The app hosts the harness Supervisor through this port only. The library is vendored unmodified into
backend/dervo_harness next to host.py (scripts/vendor-harness.py), verified against PROVENANCE.json before it is
imported, and driven by backend/harness_adapter.py through its public API. Nothing here mutates sys.path, reads a
builder checkout, reads credentials or starts a provider.

Port contract (dervo-harness-port-v2)
-------------------------------------
backend/harness_adapter.py defines:

    open_supervisor(data_dir: pathlib.Path, *, owner: str, helpers: Mapping[str, HelperSpec]) -> supervisor

``data_dir`` is the explicit directory the app owns for the harness; the adapter writes nowhere else. ``owner`` is the
local human actor every mapping is created by. ``helpers`` are the worker ports the app binds by name; the adapter
never discovers, installs or launches one. The returned object provides:

    submit(request_id, scope, text, *, helper) -> {'request_id', 'run_id'}
        scope: project_id, domain, room_id, visibility, account, provider (see HarnessCoordinator.scope).
        Raises HarnessRefused when the library refused before anything durable was submitted.
    status(harness_request_id) -> {'state', 'helper', 'linked_return', 'delegations', 'error', 'run_id', ...}
        state in STATES. Raising means observation is lost; the app reports unknown.
    cancel(harness_request_id) -> None
    pull_events(harness_request_id, after=0) -> list of {'seq', 'kind', 'data'}; seq is the library's own run event seq.
    tick() -> None      (one pump of the supervisor: drain worker ports, apply events, settle delegations)
    describe() -> dict  (library version, lease, pool members)
    close() -> None

Helper authority
----------------
A ``HelperSpec`` names the port and two host-chosen labels the library records on every run: ``provider`` and
``account``. A fake port (``port.is_fake``) is labelled exactly ``fake``/``none`` and is stamped fake on every run and
result by the library. A real port names its provider (``claude`` or ``codex``) and the profile account it acts under
(``profile:<id>``); it may only serve conversations of that provider and profile (``helper_allowed``). There is no
default helper and no fallback: a request names one, or it is refused.

Provenance
----------
backend/dervo_harness/PROVENANCE.json lists the library version, the licence and notice files and a sha256 for every
vendored file. A package with a missing or mismatching file, an unlisted code file, a missing entry point or a
version that disagrees with the imported module is refused. Nothing partial is loaded.
"""
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

PORT_VERSION = 'dervo-harness-port-v2'
PACKAGE = 'dervo_harness'
ADAPTER = 'harness_adapter.py'
ENTRY_POINT = 'open_supervisor'
PROVENANCE = 'PROVENANCE.json'
STATES = ('queued', 'running', 'stopping', 'completed', 'cancelled', 'failed', 'lost', 'unknown')
TERMINAL = ('completed', 'cancelled', 'failed', 'lost', 'unknown')
MODES = ('coordinate', 'execute')
FAKE_PROVIDER, FAKE_ACCOUNT = 'fake', 'none'
REAL_PROVIDERS = ('claude', 'codex')
ACCOUNT_PREFIXES = ('profile:', 'boundary:')  # a profile's own verification, or an owner-created record naming several profiles
HELPER_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,19}$')
PORT_METHODS = ('start', 'drain', 'steer', 'cancel', 'attach')
CODE_SUFFIXES = ('.py', '.pyc', '.pyo', '.pth', '.so', '.dylib', '.zip')
_DIGEST = re.compile(r'^[0-9a-f]{64}$')


class HarnessRefused(Exception):
    """The library refused before anything durable was submitted. Reported as failed, never as unknown."""


@dataclass(frozen=True)
class HelperSpec:
    """One worker port the app binds into the harness pool, with the labels the library records on its runs.

    ``port`` is a ``WorkerPort`` object, or for a helper whose port is per harness project (the Claude worker serves
    exactly one project) a factory ``port_for(project_id, work_dir) -> WorkerPort`` that the adapter calls when it
    binds the helper into that project. ``model`` is empty for fakes; a real helper serves only conversations bound to
    exactly that model, because its worker verifies the CLI's effective model against it.
    """
    port: object
    provider: str
    account: str
    note: str = ''
    model: str = ''
    can_ask: bool = False  # the port is configured with the library's request_help action bridge (opt-in, Claude only)


def is_port_factory(port):
    return callable(port) and not hasattr(port, 'start')


def check_helpers(helpers):
    """Validate the app's helper table. Raises ValueError naming the first problem; returns a plain dict."""
    out = {}
    for name, spec in dict(helpers or {}).items():
        if not isinstance(name, str) or not HELPER_NAME.match(name):
            raise ValueError(f'helper name {name!r} must be 1 to 20 lowercase letters, digits or hyphens')
        if not isinstance(spec, HelperSpec):
            raise ValueError(f'helper {name!r} must be a HelperSpec')
        port = spec.port
        factory = is_port_factory(port)
        if not factory and (any(not callable(getattr(port, method, None)) for method in PORT_METHODS)
                            or not isinstance(getattr(port, 'name', None), str) or not isinstance(getattr(port, 'is_fake', None), bool)):
            raise ValueError(f'helper {name!r}: port does not implement the dervo_harness WorkerPort protocol')
        if (not isinstance(spec.provider, str) or not spec.provider or not isinstance(spec.account, str) or not spec.account
                or not isinstance(spec.model, str) or not isinstance(spec.note, str) or not isinstance(spec.can_ask, bool)):
            raise ValueError(f'helper {name!r}: provider, account, model and note must be strings, provider and account non-empty, can_ask a bool')
        if spec.can_ask and spec.provider != 'claude':
            raise ValueError(f'helper {name!r}: only a Claude port carries the request_help action bridge')
        fake = (not factory) and port.is_fake
        if fake or spec.provider == FAKE_PROVIDER or spec.account == FAKE_ACCOUNT:
            if not (fake and spec.provider == FAKE_PROVIDER and spec.account == FAKE_ACCOUNT and not spec.model):
                raise ValueError(f'helper {name!r}: a fake port is labelled provider {FAKE_PROVIDER!r} and account {FAKE_ACCOUNT!r} with no '
                                 'model, and only a fake port may carry those labels')
        elif spec.provider not in REAL_PROVIDERS or not spec.account.startswith(ACCOUNT_PREFIXES) or not spec.model:
            raise ValueError(f'helper {name!r}: a real port names its provider ({" or ".join(REAL_PROVIDERS)}), the account label it acts '
                             "under ('profile:<id>', or 'boundary:<id>' for an owner-created record) and the exact model it verifies")
        out[name] = spec
    return out


def helper_bindable(spec, scope):
    """Whether the helper belongs in this conversation's project pool at all: a fake, or a real helper under the same
    account label (its own profile, or the same owner-created boundary record). Being bound lets same-account helpers
    see each other; running a request additionally needs ``helper_allowed``."""
    return spec.provider == FAKE_PROVIDER or spec.account == scope['account']


def helper_allowed(spec, scope):
    """None when the helper may serve this conversation, otherwise the reason it may not.

    A real helper serves only conversations of its own provider, profile account and model. A fake helper is not an
    authority at all: it is allowed anywhere because the library stamps every run and result it produces as fake.
    """
    if spec.provider == FAKE_PROVIDER:
        return None
    if spec.provider != scope['provider']:
        return f"helper is bound to provider {spec.provider}, not this conversation's provider {scope['provider']}"
    if spec.account != scope['account']:
        return f"helper is bound to account {spec.account}, not this conversation's account"
    if spec.model != scope.get('model'):
        return f"helper is bound to model {spec.model}, not this conversation's model {scope.get('model')!r}"
    return None


@dataclass
class LoadedSupervisor:
    state: str
    detail: str
    factory: object = None
    provenance: dict = field(default_factory=dict)


def verify_package(package):
    """Check backend/dervo_harness against its PROVENANCE.json. Returns (provenance, None) or (None, detail)."""
    package = Path(package)
    if not package.is_dir():
        return None, 'The Dervo harness is not packaged with this build. Coordination requests are unavailable.'
    path = package / PROVENANCE
    if not path.is_file():
        return None, f'The packaged harness has no {PROVENANCE}. It is not loaded.'
    try:
        provenance = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, f'The packaged harness {PROVENANCE} is unreadable. It is not loaded.'
    files = provenance.get('files') if isinstance(provenance, dict) else None
    shape_ok = (isinstance(files, dict) and files
                and all(isinstance(k, str) and k and '/' not in k and '\\' not in k and k not in ('.', '..') and isinstance(v, str) and _DIGEST.match(v)
                        for k, v in files.items())
                and all(isinstance(provenance.get(k), str) and provenance.get(k) for k in ('source', 'version', 'license'))
                and isinstance(provenance.get('notices'), list) and all(isinstance(n, str) for n in provenance['notices'])
                and '__init__.py' in files and provenance['license'] in files and all(n in files for n in provenance['notices']))
    if not shape_ok:
        return None, f'The packaged harness {PROVENANCE} must name source, version, license, notices and a sha256 for every file. It is not loaded.'
    problems = []
    for name, digest in sorted(files.items()):
        member = package / name
        if not member.is_file():
            problems.append(f'missing {name}')
        elif hashlib.sha256(member.read_bytes()).hexdigest() != digest:
            problems.append(f'digest mismatch {name}')
    for member in sorted(package.iterdir()):
        if member.name in files or member.name in (PROVENANCE, '__pycache__'):
            continue
        if member.is_dir() or member.suffix in CODE_SUFFIXES:
            problems.append(f'unlisted {member.name}')
    if problems:
        return None, 'The packaged harness does not match its provenance: ' + ', '.join(problems) + '. It is not loaded.'
    return provenance, None


def _import_package(package):
    package = Path(package).resolve()
    existing = sys.modules.get(PACKAGE)
    if existing is not None:
        loaded_from = Path(getattr(existing, '__file__', '') or '/nonexistent').resolve().parent
        if loaded_from != package:
            raise ImportError(f'{PACKAGE} is already imported from {loaded_from}, not from the packaged copy')
        return existing
    spec = importlib.util.spec_from_file_location(PACKAGE, package / '__init__.py', submodule_search_locations=[str(package)])
    if spec is None or spec.loader is None:
        raise ImportError(f'{PACKAGE}/__init__.py could not be located')
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for key in [k for k in sys.modules if k == PACKAGE or k.startswith(PACKAGE + '.')]:
            sys.modules.pop(key, None)
        raise
    return module


def _import_adapter(backend_dir):
    path = (Path(backend_dir) / ADAPTER).resolve()
    if not path.is_file():
        raise ImportError(f'{ADAPTER} is missing next to the host')
    existing = sys.modules.get('harness_adapter')
    if existing is not None and Path(getattr(existing, '__file__', '') or '/nonexistent').resolve() == path:
        return existing
    name = 'harness_adapter' if existing is None else 'harness_adapter_' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f'{ADAPTER} could not be located')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@dataclass
class LoadedLibrary:
    """The verified ``dervo_harness`` module itself, for app code that uses the library's shared rules without the adapter
    (the coding collaboration's delegation admission). ``module`` is None when the package is not loaded, with ``detail`` saying why."""
    state: str
    detail: str
    module: object = None
    provenance: dict = field(default_factory=dict)


def load_library(backend_dir):
    """Verify the packaged library next to the host against its provenance and import it. Nothing partial is loaded."""
    package = Path(backend_dir) / PACKAGE
    provenance, detail = verify_package(package)
    if detail:
        return LoadedLibrary('unavailable', detail)
    try:
        module = _import_package(package)
    except Exception as exc:
        return LoadedLibrary('unavailable', f'The packaged harness failed to import: {exc}')
    version = getattr(module, '__version__', None)
    if version != provenance['version']:
        return LoadedLibrary('unavailable', f'The packaged harness reports version {version!r} but its provenance says {provenance["version"]!r}. It is not loaded.')
    return LoadedLibrary('available', f'Packaged harness {provenance["source"]} {provenance["version"]} verified ({PORT_VERSION}).', module, provenance)


def load_supervisor_factory(backend_dir):
    """Verify the packaged library next to the host, import it and the adapter, and hand back the entry point.

    Order matters: digests are checked before any library code is imported, and the adapter's entry point is checked
    before the library is imported, so a refused package never runs.
    """
    backend_dir = Path(backend_dir)
    package = backend_dir / PACKAGE
    provenance, detail = verify_package(package)
    if detail:
        return LoadedSupervisor('unavailable', detail)
    try:
        adapter = _import_adapter(backend_dir)
    except Exception as exc:
        return LoadedSupervisor('unavailable', f'The harness adapter {ADAPTER} failed to import: {exc}')
    factory = getattr(adapter, ENTRY_POINT, None)
    if not callable(factory):
        return LoadedSupervisor('unavailable', f'The harness adapter {ADAPTER} has no callable {ENTRY_POINT}. It is not loaded.')
    loaded = load_library(backend_dir)
    if loaded.module is None:
        return LoadedSupervisor('unavailable', loaded.detail)
    return LoadedSupervisor('available', loaded.detail, factory, loaded.provenance)
