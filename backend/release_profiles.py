"""Build-owned release profiles. User preferences cannot enable an experimental provider."""
import json
import re
from pathlib import Path

PROFILES = {
    'private-beta': {'label': 'Private beta', 'providers': ['claude', 'codex'], 'openRouter': False},
    'personal-experimental': {'label': 'Personal / Experimental', 'providers': ['claude', 'codex', 'openrouter'], 'openRouter': True},
}
MARKER = 'dervo-release-profile.json'


def manifest(profile='private-beta'):
    if profile not in PROFILES:
        raise ValueError('Choose a known Dervo release profile')
    return {'schema': 1, 'profile': profile, **json.loads(json.dumps(PROFILES[profile]))}


def read(resources=None):
    resources = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
    path = resources / 'runtime' / MARKER
    if resources.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise ValueError('Release profile must be a bundled file')
    if not path.exists():
        return manifest()  # Older builds and source runs cannot enable OpenRouter.
    record = json.loads(path.read_text())
    if type(record) is not dict or record != manifest(record.get('profile')):
        raise ValueError('Release profile does not match a supported build configuration')
    return record


def stage(work, profile):
    record = manifest(profile)
    runtime = Path(work) / 'runtime'
    runtime.mkdir(exist_ok=True)
    (runtime / MARKER).write_text(json.dumps(record, indent=2) + '\n')
    return record


def require_provider(provider, resources=None):
    if provider not in read(resources)['providers']:
        raise ValueError('This provider is unavailable in this Dervo release profile')


def exclude_test_modules(resources):
    """Remove development tests from the builder's fresh bundle before signing.

    Source archives and test gates retain these files. Only the independently
    copied runtime payload is trimmed.
    """
    resources = Path(resources)
    backend = resources / 'backend'
    if any(path.is_symlink() for path in (backend, resources, *resources.parents)):
        raise ValueError('Bundle test exclusion requires regular directories')
    candidates = sorted(backend.glob('test_*.py'))
    if any(not path.is_file() or path.is_symlink() or path.stat().st_nlink != 1 for path in candidates):
        raise ValueError('Bundle test exclusion found a linked or non-file test entry')
    for path in candidates:
        path.unlink()
    return [str(path.relative_to(resources)) for path in candidates]


def verify_bundle(resources, expected):
    resources = Path(resources)
    marker = resources / 'runtime' / MARKER
    if resources.is_symlink() or marker.parent.is_symlink() or marker.is_symlink() or not marker.is_file():
        raise ValueError('Packaging requires a regular bundled release profile')
    if read(resources) != manifest(expected):
        raise ValueError('The bundled release profile differs from the requested profile')
    from account_config import require as require_account
    require_account(resources)
    # Refuse recognizable credentials and workspace stores before signing. Report paths only.
    forbidden = {'auth.json', 'credentials.json', 'openrouter-settings.json', 'preview-jobs.json',
                 'preview-tools.json', 'profiles', 'preview-data', 'conversations.sqlite3'}
    key = re.compile(rb'(?:(?:sk-or-v1-|sk-ant-api[0-9]+-)[A-Za-z0-9_-]{32,}|sb_secret_[A-Za-z0-9_-]{10,}|eyJ[A-Za-z0-9_-]{6,1024}\.eyJ[A-Za-z0-9_-]{6,8192}\.[A-Za-z0-9_-]{16,2048})')
    checked = 0
    for path in Path(resources).rglob('*'):
        relative = path.relative_to(resources)
        if len(relative.parts) == 2 and relative.parts[0] == 'backend' and path.name.startswith('test_') and path.suffix == '.py':
            raise ValueError('Development test module found in bundle: ' + str(relative))
        if path.is_symlink():
            raise ValueError('Linked resource needs packaging review: ' + str(relative))
        if any(part in forbidden or part == '.env' or part.startswith('.env.') for part in relative.parts):
            raise ValueError('Local account or workspace data found in bundle: ' + str(relative))
        if path.is_file() and path.suffix.lower() in ('.sqlite', '.sqlite3', '.db', '.key', '.pem'):
            raise ValueError('Database or private-key file found in bundle: ' + str(relative))
        if not path.is_file():
            continue
        checked += 1
        with path.open('rb') as source:
            overlap = b''
            while chunk := source.read(1024 * 1024):
                data = overlap + chunk
                if key.search(data):
                    raise ValueError('Secret key material found in bundle: ' + str(relative))
                overlap = data[-16384:]
    return {'profile': expected, 'filesChecked': checked, 'credentialPatternCheck': 'passed', 'publicAccountConfiguration': 'validated'}
