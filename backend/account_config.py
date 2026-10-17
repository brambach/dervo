"""Load only build-owned public account configuration. Never select it from UI/env."""
import json
from pathlib import Path

from account_keychain import MacKeychain
import platform_os
from windows_credentials import WindowsCredentials
from linux_credentials import LinuxSecretService
from dervo_connections import Rejected
from supabase_account import FIELDS, SessionVault, SupabaseAccount
from supabase_client import PublicProject, SupabaseClient

NAME = 'dervo-account-public.json'


def require(resources):
    project = read(resources)
    if project is None:
        raise Rejected('This build is missing its public Dervo account configuration.')
    return project


def for_build(source, config):
    """Accept only the three public project fields, before creating build output."""
    location = config.get('accountPublicConfig')
    if location is None:
        return require(source)
    try:
        if type(location) is not str or not location:
            raise ValueError()
        path = Path(location).expanduser()
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError()
        if not path.is_file() or path.stat().st_size > 8192:
            raise ValueError()
        return PublicProject.from_public_config(json.loads(path.read_bytes()))
    except Exception:
        raise Rejected('Choose a valid local public Dervo account configuration before building. No release was created.') from None


def stage(resources, project):
    if not isinstance(project, PublicProject):
        raise Rejected('A validated public project is required.')
    root = Path(resources)
    runtime = root / 'runtime'
    path = runtime / NAME
    if any(part.is_symlink() for part in (root, runtime, path)):
        raise Rejected('Account configuration cannot be staged through a link.')
    runtime.mkdir(exist_ok=True)
    path.write_text(json.dumps({'schema': 1, 'required': True, 'project': {
        'project_ref': project.project_ref, 'url': project.url, 'publishable_key': project.publishable_key}}, indent=2) + '\n')


def read(resources):
    root = Path(resources)
    path = root / 'runtime' / NAME
    if root.is_symlink() or path.parent.is_symlink() or path.is_symlink():
        raise Rejected('Dervo account configuration needs a regular bundled file.')
    if not path.exists():
        return None
    try:
        if not path.is_file() or path.stat().st_size > 8192:
            raise ValueError()
        record = json.loads(path.read_bytes())
        if type(record) is not dict or set(record) != {'schema', 'required', 'project'} or record['schema'] != 1 or record['required'] is not True:
            raise ValueError()
        return PublicProject.from_public_config(record['project'])
    except Exception:
        raise Rejected('Dervo account configuration needs attention. No account connection was started.') from None


class UnavailableAccount:
    """A broken configured account never falls back to unrestricted local execution."""
    def status(self):
        return dict(state='unavailable', method='email_code',
                    detail='Dervo account setup needs attention in this build. Your saved work is still available.',
                    identity=None, operation_id=None, expires_at=None, can_start=False,
                    can_cancel=False, can_poll=False, can_restore=False, can_sign_out=False,
                    can_verify=False, can_save_profile=False, membership=None, access_valid_until=None,
                    ownership='local_unclaimed', sync_available=False)

    def dispatch(self, operation, args):
        if operation == 'dervo_account_status' and args == {}:
            return self.status()
        raise Rejected(self.status()['detail'])

    def require_access(self):
        raise Rejected(self.status()['detail'])

    def tick(self):
        pass

    def close(self):
        pass


def create(store, data_root, resources=None):
    root = Path(resources) if resources is not None else Path(__file__).resolve().parent.parent
    try:
        project = read(root)
        if project is None:
            # Source checkouts remain usable offline. Packaged builds can't lose
            # their account gate by omitting a required resource.
            if (root / 'runtime' / 'dervo-release-profile.json').exists() or (root.name == 'Resources' and root.parent.name == 'Contents'):
                return UnavailableAccount()
            return None
        from release_profiles import read as release_profile
        saved = WindowsCredentials if platform_os.WINDOWS else LinuxSecretService if platform_os.LINUX else MacKeychain
        keychain = saved(project_ref=project.project_ref, profile=release_profile(root)['profile'],
                               workspace_id=str(Path(data_root).resolve()))
        return SupabaseAccount(store, SupabaseClient(project), SessionVault(keychain), data_root=data_root)
    except Exception as exc:
        # Only the error's type: it names what broke (an AttributeError on Windows hid for a whole tester check) and
        # carries no account data.
        import sys
        print(f'Dervo account setup failed: {type(exc).__name__}', file=sys.stderr)
        return UnavailableAccount()
