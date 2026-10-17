"""Pinned coding-only controls. Ordinary chat policy stays unchanged."""
from copy import deepcopy
from codex_policy import executable, for_folder, launched, POLICY as CHAT_POLICY, verify_config as verify_chat_config
from dervo_connections import Rejected

POLICY = deepcopy(CHAT_POLICY)
POLICY['features']['code_mode_host'] = {'enabled': True, 'disable_in_process_fallback': True}
POLICY['agents'] = {'enabled': False}


def policy_for(full_access=False):
    policy = deepcopy(POLICY)
    if full_access:
        policy.update(sandbox_mode='danger-full-access', approval_policy='never')
        policy['features'].update(shell_tool=True, unified_exec=True)
        # Forward only the host's clean toolchain, git guard and empty gh folder (claude_coding.github_guard) to commands.
        # No shell profile or provider secrets, and no GitHub sign-in: gh reads as signed out.
        policy['shell_environment_policy'] = {'inherit': 'all', 'include_only': [
            'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', 'GH_CONFIG_DIR']}
    return policy


def argv(*, login_method='chatgpt', full_access=False, cwd=None):
    """``cwd`` is the task's folder; the launch marks it untrusted (codex_policy.untrusted_projects), which also keeps
    a full-access thread/start from writing trust into the profile."""
    import json
    if login_method not in ('chatgpt', 'api'):
        raise Rejected('Codex login method is chatgpt or api')
    def toml(value):
        if isinstance(value, dict):
            return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
        return json.dumps(value)
    args = [executable()]
    for key, value in {**for_folder(policy_for(full_access), cwd), 'forced_login_method': login_method}.items():
        args += ['-c', key + '=' + toml(value)]
    return args + ['app-server', '--listen', 'stdio://', '--strict-config']


def verify_config(result, login_method='chatgpt', *, full_access=False, cwd=None):
    """As codex_policy.verify_config, for the coding policy: Codex must echo the login method and folder this launch
    passed."""
    expected = launched(for_folder(policy_for(full_access), cwd), login_method)
    layers = result.get('layers')
    if not isinstance(layers, list) or not layers or any(not isinstance(x, dict) for x in layers):
        raise Rejected('Codex did not return coding configuration layers')
    flags = [x.get('config') for x in layers if x.get('name', {}).get('type') == 'sessionFlags']
    if flags != [expected]:
        raise Rejected('Codex coding launch policy was not applied exactly')
    config = result.get('config', {})
    host = config.get('features', {}).get('code_mode_host')
    agents = config.get('agents')
    if (not isinstance(host, dict) or set(host) != {'enabled', 'disable_in_process_fallback'}
            or any(value is not True for value in host.values())
            or not isinstance(agents, dict) or agents.get('enabled') is not False
            or any(value is not None for key, value in agents.items() if key != 'enabled')):
        raise Rejected('Codex effective coding controls differ from the launch policy')
    # Validate the two coding differences before projecting to unchanged chat
    # verification. Keep every other effective field and source layer intact.
    checked = deepcopy(result)
    checked['config'].pop('agents', None)
    checked['config']['features']['code_mode_host'] = False
    chat_policy = policy_for(full_access)
    chat_policy.pop('agents')
    chat_policy['features']['code_mode_host'] = False
    for layer in checked['layers']:
        if layer.get('name', {}).get('type') == 'sessionFlags':
            layer['config'] = launched(for_folder(chat_policy, cwd), login_method)
    verify_chat_config(checked, login_method, policy=chat_policy, cwd=cwd)
