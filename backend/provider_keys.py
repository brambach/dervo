"""API-key accounts for Claude Code and Codex, beside subscription sign-in.

Bryce's 17 September 2026 decision, after the provider-terms check: subscription sign-in stays, and an API key is
offered next to it. A key is checked once with the provider's own models endpoint (a free call), then stored:

* Claude: in the macOS Keychain under a Dervo-owned service, and passed to Claude Code as ANTHROPIC_API_KEY in the
  process environment of each run. Claude Code then reports ``authMethod: "api_key"`` from ``claude auth status``.
* Codex: through Codex's own ``codex login --with-api-key``, which reads the key from stdin and keeps it in
  ``auth.json`` (mode 0600) inside the Dervo-owned CODEX_HOME. Codex is then started with ``forced_login_method="api"``
  and ``account/read`` reports ``{"type": "apiKey"}``.

The key never appears in argv, in a saved record, in a task's binding, in a log line or in an error message. The
profile row stores only a label ending in the key's last four characters and the monthly spend limit.

What that doesn't cover (security review, 19 Sep 2026, finding 1): anything running as you can read ``auth.json``, and a
full-access run can read the Keychain items too (see account_keychain). Codex's ``cli_auth_credentials_store="keyring"``
isn't used yet. The "file" value is pinned in codex_policy.POLICY, which verify_config requires Codex to echo exactly,
and in the vendored harness worker, so a key saved to the keyring would be invisible to every launch. Switching needs
a per-account policy through login, every launch and verify_config, a move for existing keys, and a live check on a
throwaway key. evidence/security-followups-20260919.md has the plan.
"""
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from dervo_connections import Rejected

PROVIDER_KEY_REF = 'dervoproviderkeys001'  # a fixed Keychain namespace; provider keys don't belong to the account project
LABELS = {'claude': 'Anthropic API key', 'codex': 'OpenAI API key'}
PREFIXES = {'claude': 'sk-ant-', 'codex': 'sk-'}
ENDPOINTS = {'claude': 'https://api.anthropic.com/v1/models?limit=1', 'codex': 'https://api.openai.com/v1/models'}
PROVIDER_NAMES = {'claude': 'Anthropic', 'codex': 'OpenAI'}
KEYCHAIN_DETAIL = 'API keys need macOS Keychain, which this build can’t reach. Nothing was saved.'
FIELDS = {
    'profile_key_add': {'provider', 'key'},
    'profile_key_remove': {'profileId'},
    'profile_limit_set': {'profileId', 'monthlyLimitUsd'},
}
# ``category`` is Work or Personal, personal when left out (every key added before 26 Sep 2026 was personal).
OPTIONAL = {'profile_key_add': {'monthlyLimitUsd', 'category'}}


def valid_key(provider, key):
    """The key as typed, or a refusal that never repeats it."""
    if provider not in LABELS:
        raise Rejected('Choose Claude Code or Codex.')
    if (type(key) is not str or key != key.strip() or not 20 <= len(key) <= 512
            or not key.isascii() or not key.isprintable() or any(c.isspace() for c in key)
            or not key.startswith(PREFIXES[provider])):
        raise Rejected(f'That doesn’t look like an {LABELS[provider]}. Paste the whole key, starting with {PREFIXES[provider]}.')
    return key


def valid_limit(value):
    if value is None:
        return None
    if type(value) not in (int, float) or isinstance(value, bool) or not 0 < value <= 100000:
        raise Rejected('The monthly spend limit is a number of dollars, or none.')
    return float(value)


def default_fetch(url, headers, timeout=8):
    request = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def check_key(provider, key, fetch=default_fetch):
    """One free models-list call to the provider. 'ok', 'invalid' or 'unreachable'. The key goes only to that provider."""
    headers = ({'x-api-key': key, 'anthropic-version': '2023-06-01'} if provider == 'claude'
               else {'Authorization': 'Bearer ' + key})
    try:
        status = fetch(ENDPOINTS[provider], headers)
    except Exception:
        return 'unreachable'
    if status == 200:
        return 'ok'
    if status in (401, 403):
        return 'invalid'
    return 'unreachable'


class ProviderKeys:
    def __init__(self, host, *, keychain_factory=None, run=subprocess.run, fetch=default_fetch):
        self.host, self.store, self.run, self.fetch = host, host.store, run, fetch
        self.keychain_factory = keychain_factory

    # ------------------------------------------------------------------ storage
    def _keychain(self, profile_id):
        from account_keychain import KeychainError, MacKeychain
        from release_profiles import read as release_profile
        import platform_os
        from windows_credentials import WindowsCredentials
        from linux_credentials import LinuxSecretService
        factory = self.keychain_factory or (WindowsCredentials if platform_os.WINDOWS else LinuxSecretService if platform_os.LINUX else MacKeychain)
        try:
            return factory(project_ref=PROVIDER_KEY_REF, profile=release_profile()['profile'],
                           workspace_id=json.dumps([str(Path(self.host.root).resolve()), profile_id]), purpose='provider-key')
        except (KeychainError, ValueError, OSError):
            raise Rejected(KEYCHAIN_DETAIL) from None

    def profile(self, profile_id):
        if type(profile_id) is not str or not profile_id:
            raise Rejected('Choose an account.')
        row = self.store.db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
        if row is None:
            raise Rejected('Unknown account profile')
        return dict(row)

    def key_for(self, binding):
        """The Claude key to place in a run's environment, or None for a subscription account. Never saved anywhere."""
        if binding.get('method') != 'api_key' or binding.get('provider') != 'claude':
            return None
        from account_keychain import KeychainError
        try:
            raw = self._keychain(binding['profile_id']).read()
        except KeychainError:
            raise Rejected(KEYCHAIN_DETAIL) from None
        if raw is None:
            raise Rejected('The API key for this account is missing from macOS Keychain. Remove the account and add it again.')
        try:
            return valid_key('claude', raw.decode('ascii'))
        except (UnicodeDecodeError, Rejected):
            raise Rejected('The saved API key for this account is unreadable. Remove the account and add it again.') from None

    def login_method(self, binding):
        return 'api' if binding.get('method') == 'api_key' else 'chatgpt'

    # ------------------------------------------------------------------ operations
    def dispatch(self, operation, args):
        optional = OPTIONAL.get(operation, set())
        if type(args) is not dict or set(args) - optional != FIELDS[operation]:
            raise Rejected('Unsupported account fields')
        if operation == 'profile_key_add':
            return self.add(args)
        if operation == 'profile_key_remove':
            return self.remove(args)
        return self.limit_set(args)

    def add(self, args):
        provider, key = args['provider'], valid_key(args.get('provider'), args.get('key'))
        limit = valid_limit(args.get('monthlyLimitUsd'))
        category = args.get('category', 'personal')
        if category not in ('personal', 'work'):
            raise Rejected('Choose Work or Personal for this key. Nothing was saved.')
        outcome = check_key(provider, key, self.fetch)
        if outcome == 'invalid':
            raise Rejected(f'{PROVIDER_NAMES[provider]} rejected that key. Check it in your {PROVIDER_NAMES[provider]} account and try again. Nothing was saved.')
        if outcome != 'ok':
            raise Rejected(f'Couldn’t reach {PROVIDER_NAMES[provider]} to check the key. Try again in a moment. Nothing was saved.')
        from host import identifier
        profile_id = identifier()
        home = Path(self.host.root) / 'profiles' / profile_id
        identity = f'{LABELS[provider]} ending {key[-4:]}'
        if provider == 'claude':
            store = self._keychain(profile_id)  # fail before any row exists when Keychain is unavailable
        self.store.add_profile(profile_id, provider, home, identity, category, method='api_key', monthly_limit_usd=limit)
        try:
            home.mkdir(parents=True, exist_ok=True, mode=0o700)
            if provider == 'claude':
                from account_keychain import KeychainError
                try:
                    store.write(key.encode('ascii'))
                except KeychainError:
                    raise Rejected(KEYCHAIN_DETAIL) from None
            else:
                self._codex_login(home, key)
        except Exception:
            with self.store.db:
                self.store.db.execute('DELETE FROM profiles WHERE id=?', (profile_id,))
            raise
        return {'id': profile_id, 'provider': provider, 'identity': identity, 'category': category, 'method': 'api_key',
                'monthly_limit_usd': limit, 'detail': f'Key checked with {PROVIDER_NAMES[provider]} and saved.'}

    def _codex_login(self, home, key):
        """Codex stores the key itself, from stdin, inside the owned CODEX_HOME. The key is never an argument."""
        from codex_policy import executable
        from transport import clean_environment
        try:
            result = self.run([executable(), '-c', 'cli_auth_credentials_store="file"', 'login', '--with-api-key'],
                              input=key.encode('ascii'), cwd=str(home), env=clean_environment('codex', str(home), str(home)),
                              capture_output=True, timeout=30)
            ok = result.returncode == 0 and (home / 'auth.json').is_file()
        except Exception:
            ok = False
        if not ok:
            raise Rejected('Codex could not save the key. Nothing was stored.')
        os.chmod(home / 'auth.json', 0o600)

    def remove(self, args):
        profile = self.profile(args['profileId'])
        if profile.get('method') != 'api_key':
            raise Rejected('Only an API-key account can be removed here. Subscription accounts are signed out from their provider.')
        used = (self.store.db.execute('SELECT count(*) FROM projects WHERE profile_id=?', (profile['id'],)).fetchone()[0]
                + self.store.db.execute('SELECT count(*) FROM threads WHERE profile_id=?', (profile['id'],)).fetchone()[0])
        if used:
            raise Rejected(f'{used} project{"s" if used != 1 else ""} or conversation{"s" if used != 1 else ""} still use this account. Change their account first.')
        if profile['provider'] == 'claude':
            from account_keychain import KeychainError
            try:
                self._keychain(profile['id']).delete()
            except KeychainError:
                raise Rejected('Dervo couldn’t remove the key from macOS Keychain. The account was kept.') from None
        else:
            auth = Path(profile['home']) / 'auth.json'
            if auth.exists() or auth.is_symlink():
                auth.unlink()
        with self.store.db:
            self.store.db.execute('DELETE FROM profiles WHERE id=?', (profile['id'],))
        return {'id': profile['id'], 'state': 'removed'}

    def limit_set(self, args):
        profile = self.profile(args['profileId'])
        if profile.get('method') != 'api_key':
            raise Rejected('Spend limits apply to API-key accounts. A subscription has its own allowance.')
        limit = valid_limit(args['monthlyLimitUsd'])
        with self.store.db:
            self.store.db.execute('UPDATE profiles SET monthly_limit_usd=? WHERE id=?', (limit, profile['id']))
        return {'id': profile['id'], 'monthly_limit_usd': limit}
