"""Personal-profile OpenRouter key storage and read-only connection checks.

No completion endpoint, account management operation or plaintext credential
fallback is exposed here. The host must supply its bundled resource directory.
"""
import json
from decimal import Decimal
import math
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from account_keychain import MacKeychain
import platform_os
from windows_credentials import WindowsCredentials
from linux_credentials import LinuxSecretService
from release_profiles import require_provider
from supabase_client import account_https_context


class ConnectionError(Exception):
    MESSAGES = {
        'key': 'Enter an OpenRouter inference key in Dervo’s secure setup.',
        'permissions': 'Use an inference key, not an account management key.',
        'unauthorized': 'OpenRouter didn’t accept this key. Check it in your OpenRouter account.',
        'rate_limited': 'OpenRouter asked us to wait. Try the connection check again shortly.',
        'unavailable': 'Dervo couldn’t check OpenRouter. Your saved key hasn’t been changed.',
        'response': 'OpenRouter returned connection details Dervo couldn’t verify.',
        'usage_pending': 'The OpenRouter charge isn’t available yet. Its budget stays reserved.',
        'usage_external': 'This request may have charges outside OpenRouter. Its budget stays reserved until those charges are checked.',
    }

    def __init__(self, code):
        self.code = code if code in self.MESSAGES else 'unavailable'
        super().__init__(self.MESSAGES[self.code])


def valid_key(value):
    if type(value) is not str or not re.fullmatch(r'sk-or-v1-[A-Za-z0-9_-]{32,256}', value):
        raise ConnectionError('key')
    return value


class KeyVault:
    def __init__(self, *, resources, project_ref, workspace, user_id, keychain_factory=None):
        self.resources = Path(resources)
        self._allowed()
        try:
            if type(user_id) is not str or str(UUID(user_id)) != user_id:
                raise ValueError()
            workspace = str(Path(workspace).resolve())
        except Exception:
            raise ConnectionError('key') from None
        # Separate service, account, release profile, project and local workspace.
        keychain_factory = keychain_factory or (WindowsCredentials if platform_os.WINDOWS else LinuxSecretService if platform_os.LINUX else MacKeychain)
        self._store = keychain_factory(project_ref=project_ref, profile='personal-experimental',
                                       workspace_id=json.dumps([workspace, user_id]), purpose='openrouter')

    def _allowed(self):
        require_provider('openrouter', self.resources)

    def load(self):
        self._allowed()
        raw = self._store.read()
        if raw is None:
            return None
        try:
            return valid_key(raw.decode('ascii'))
        except Exception:
            raise ConnectionError('key') from None

    def save(self, key):
        self._allowed()
        self._store.write(valid_key(key).encode('ascii'))

    def delete(self):
        self._allowed()
        self._store.delete()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def money(data, field, nullable=False):
    value = data.get(field)
    if value is None and nullable and field in data:
        return None
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except (OverflowError, ValueError, TypeError):
        valid = False
    if not valid:
        raise ConnectionError('response')
    return value


class ReadOnlyClient:
    MAX_RESPONSE = 8 * 1024 * 1024

    def __init__(self, resources, *, opener=None):
        self.resources = Path(resources)
        require_provider('openrouter', self.resources)
        self._opener = opener if opener is not None else build_opener(
            ProxyHandler({}), NoRedirect(), HTTPSHandler(context=account_https_context()))

    def _get(self, path, key):
        require_provider('openrouter', self.resources)
        generation = bool(re.fullmatch(r'/generation\?id=[A-Za-z0-9_-]{1,150}', path))
        if path not in ('/key', '/models/user') and not generation and not re.fullmatch(r'/models/[A-Za-z0-9._-]+/[A-Za-z0-9._%:-]+/endpoints', path):
            raise ConnectionError('response')
        key = valid_key(key)
        request = Request('https://openrouter.ai/api/v1' + path,
                          headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}, method='GET')
        try:
            with self._opener.open(request, timeout=15) as response:
                if response.status != 200:
                    raise ConnectionError('response')
                raw = response.read(self.MAX_RESPONSE + 1)
                if len(raw) > self.MAX_RESPONSE:
                    raise ConnectionError('response')
                result = json.loads(raw, parse_float=Decimal) if generation else json.loads(raw)
            if type(result) is not dict or 'data' not in result:
                raise ConnectionError('response')
            if path == '/models/user' and 'total_count' in result:
                total = result['total_count']
                if type(total) is not int or type(result['data']) is not list or total != len(result['data']):
                    raise ConnectionError('response')
            return result['data']
        except HTTPError as error:
            status = error.code
            error.close()
            raise ConnectionError('usage_pending' if generation and status == 404 else
                                  'unauthorized' if status in (401, 403) else
                                  'rate_limited' if status == 429 else 'unavailable') from None
        except ConnectionError:
            raise
        except Exception:
            # Never forward exception URLs, headers, response text or key labels.
            raise ConnectionError('unavailable') from None

    def key_status(self, key):
        data = self._get('/key', key)
        if type(data) is not dict:
            raise ConnectionError('response')
        if data.get('is_management_key') is not False or data.get('is_provisioning_key') is not False:
            raise ConnectionError('permissions')
        return {'limitUsd': money(data, 'limit', nullable=True),
                'remainingUsd': money(data, 'limit_remaining', nullable=True),
                'usedUsd': money(data, 'usage'), 'usedTodayUsd': money(data, 'usage_daily'),
                'usedThisMonthUsd': money(data, 'usage_monthly')}

    def generation(self, key, generation):
        from openrouter_receipts import generation_id, parse
        generation_id(generation)
        return parse(self._get('/generation?id=' + generation, key), generation)

    def endpoints(self, key, model):
        from openrouter_routes import model_id, endpoints
        author, slug = model_id(model).split('/', 1)
        path = '/models/' + quote(author, safe='') + '/' + quote(slug, safe='') + '/endpoints'
        return endpoints(self._get(path, key), model, key)

    def models(self, key):
        data = self._get('/models/user', key)
        if type(data) is not list or len(data) > 10000:
            raise ConnectionError('response')
        models, seen = [], set()
        for row in data:
            if type(row) is not dict:
                raise ConnectionError('response')
            model = row.get('id')
            if (type(model) is not str or len(model) > 200 or key in model
                    or not re.fullmatch(r'[A-Za-z0-9._-]+/[A-Za-z0-9._:/-]+', model) or model in seen):
                raise ConnectionError('response')
            context = row.get('context_length')
            if type(context) is not int or context <= 0:
                raise ConnectionError('response')
            seen.add(model)
            models.append({'id': model, 'contextTokens': context})
        return models
