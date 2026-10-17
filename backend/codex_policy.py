"""Pinned Codex 0.156.1 text-turn policy. No permission grants."""
import os
from pathlib import Path
import tomllib
import platform_os
from codex_runtime import VERSION, BINARY, executable
from dervo_connections import Rejected

EXECUTABLE = str(BINARY)

# Disabling shell isn't enough: apps, hooks and host-side tools are independent.
DISABLED = ('shell_tool unified_exec shell_snapshot shell_zsh_fork unified_exec_zsh_fork '
    'apps connectors plugins remote_plugin plugin_hooks codex_hooks hooks '
    'browser_use browser_use_external computer_use in_app_browser image_generation imagegenext '
    'view_image js_repl code_mode code_mode_host code_mode_only multi_agent multi_agent_v2 collab '
    'memories memory_tool skill_search skill_mcp_dependency_install skill_env_var_dependency_prompt '
    'tool_search tool_suggest search_tool '
    'standalone_web_search remote_control workspace_dependencies external_migration '
    'external_agent_memory_import request_permissions_tool request_permissions '
    'code_mode_prewarm unified_exec_tty shell_snapshot_v2 in_app_local_automation '
    'worktrees sleep_tool step_model_switching goals').split()
POLICY = {'cli_auth_credentials_store':'file', 'mcp_oauth_credentials_store':'file',
    'forced_login_method':'chatgpt', 'model_provider':'openai', 'approval_policy':'on-request',
    'approvals_reviewer':'user', 'sandbox_mode':'read-only', 'web_search':'cached',
    'allow_login_shell':False, 'project_doc_max_bytes':0,
    'features':{**{key:False for key in DISABLED}, 'skip_host_skill_discovery':True},
    # skip_host_skill_discovery does nothing in app-server 0.154.0: its host skill provider overrides the opt-out, and
    # on 19 Sep skills/list showed a repo's .agents/skills entry and the bundled skills with this exact policy. These two
    # keep every skill out of the model's instructions (checked with `codex debug prompt-input`) until Dervo delivers
    # reviewed skills itself (PLAN-native.md, A2 and B8). Same on 0.156.1 (25 Sep): skills/list still shows a repo skill,
    # and prompt-input leaves it out under this policy (evidence/codex-local-upgrade/prompt-input-skills.json).
    'skills':{'include_instructions':False, 'bundled':{'enabled':False}},
    'shell_environment_policy':{'inherit':'none'}, 'mcp_servers':{}}
# Web search (PLAN-native.md, A5, approved 19 Sep): 'cached' serves results from OpenAI's index rather than fetching live
# pages, and it's a hosted tool, so the sandbox keeps no network. Queries go to OpenAI, which already sees the thread.
# The legacy feature toggles web_search, web_search_cached and web_search_request are off the disabled list so they
# can't fight this key; if one ever did, verify_config sees the effective value differ and refuses to send.
# dervo_harness/codex_worker.py (vendored, checksummed) keeps its own copy with search disabled, for text-only helpers.
SANDBOX = {'type':'readOnly', 'networkAccess':False}

# Project trust (23 Sep). Codex 0.154.0's thread/start, given a danger-full-access or workspace-write sandbox for a folder
# whose trust isn't set, writes [projects."<git root>"] trust_level = "trusted" into CODEX_HOME/config.toml. Measured on
# the bundled binary with a throwaway home: no sign-in, no turn. Every full-access Codex run left one in its profile, and
# check_profile then refused the profile for good. A file holding only that trust is accepted; anything more still isn't.
# The same thread/start also trusts the folder in memory, so its own .codex/config.toml loaded after config/read had
# checked the launch (measured: a planted model_reasoning_effort reached the thread). Codex skips both when the folder
# is explicitly untrusted, and a -c projects table counts, so every launch passes one for its cwd (untrusted_projects).
# Rechecked on 0.156.1 (25 Sep, evidence/codex-local-upgrade/probe-0156.json): without the table thread/start still writes
# the trust and loads the repo's setting; with it, nothing is written and the project layer stays disabled.
TRUST_LEVELS = ('trusted', 'untrusted')
CONFIG_LIMIT = 64 * 1024


def untrusted_projects(cwd):
    """The ``projects`` table a launch for ``cwd`` passes: the folder and every folder above it, as given and as its real
    path, each untrusted. Codex looks a folder up by its own path, then by its git root (the main checkout for a
    worktree), and a trusted entry an earlier build left in the profile loses to these. A repo's own .codex settings
    stay switched off, as config/read reports them for an untrusted folder, so a repo that has them still runs."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or '\0' in cwd:
        raise Rejected('Codex needs an absolute project folder.')
    folders = {}
    for path in (Path(cwd), Path(os.path.realpath(cwd)), *main_checkouts(cwd)):
        for folder in (path, *path.parents):
            folders[str(folder)] = {'trust_level': 'untrusted'}
    return folders


def main_checkouts(cwd):
    """For a folder inside a linked git worktree (a thread's own folder, beta.18), the main checkout Codex falls back
    to, and the same place inside it: [main checkout, main checkout/<cwd's path in the worktree>]. Empty for a folder
    in a main checkout or outside git. Read from the worktree's .git file and the commondir git keeps for it, so no git
    runs. Codex's lookup by git root would otherwise find a trusted entry an older build left for the project."""
    try:
        here = Path(os.path.realpath(cwd))
        for top in (here, *here.parents):
            marker = top / '.git'
            if marker.is_dir():
                return []
            if not marker.is_file():
                continue
            text = marker.read_text(encoding='utf-8', errors='replace').strip()
            if not text.startswith('gitdir:'):
                return []
            gitdir = Path(text[len('gitdir:'):].strip())
            gitdir = gitdir if gitdir.is_absolute() else top / gitdir
            common = (gitdir / 'commondir').read_text(encoding='utf-8').strip()
            common = Path(os.path.realpath(common if os.path.isabs(common) else gitdir / common))
            if common.name != '.git':
                return []  # a bare repository's worktree has no main checkout to trust
            main = common.parent
            inside = here.relative_to(top)
            return [main] + ([main / inside] if str(inside) != '.' else [])
    except (OSError, ValueError):
        return []
    return []


def for_folder(policy, cwd):
    """``policy`` as launched for ``cwd``: with its untrusted ``projects`` table, or unchanged when there's no folder."""
    return dict(policy) if cwd is None else {**policy, 'projects': untrusted_projects(cwd)}


def trust_only(config):
    """True when a parsed Codex config holds nothing but project trust: a ``projects`` table whose every key is an
    absolute path and whose every entry is exactly ``trust_level`` set to trusted or untrusted."""
    if not isinstance(config, dict) or set(config) != {'projects'} or not isinstance(config['projects'], dict):
        return False
    return all(isinstance(path, str) and os.path.isabs(path) and '\0' not in path
               and isinstance(entry, dict) and set(entry) == {'trust_level'} and entry['trust_level'] in TRUST_LEVELS
               for path, entry in config['projects'].items())


def trust_only_file(path):
    """True when ``path`` is a regular file, not a link, under CONFIG_LIMIT, whose TOML is project trust and nothing else.
    Earlier builds let Codex write one into the profile (see TRUST_LEVELS), so testers already have it."""
    import stat
    try:
        if platform_os.WINDOWS:
            # Windows has no O_NOFOLLOW (every Codex run stopped here with AttributeError), so a link or junction is
            # refused by lstat, and the file opened has to be the one lstat saw.
            seen = os.lstat(path)
            if stat.S_ISLNK(seen.st_mode) or getattr(seen, 'st_reparse_tag', 0):
                return False
        fd = os.open(path, os.O_RDONLY | platform_os.NOFOLLOW | getattr(os, 'O_BINARY', 0))
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > CONFIG_LIMIT:
            return False
        if platform_os.WINDOWS and (info.st_dev, info.st_ino) != (seen.st_dev, seen.st_ino):
            return False
        with os.fdopen(fd, 'rb') as handle:
            fd = None
            raw = handle.read(CONFIG_LIMIT + 1)
        if len(raw) > CONFIG_LIMIT:
            return False
        return trust_only(tomllib.loads(raw.decode('utf-8')))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    finally:
        if fd is not None:
            os.close(fd)


def login_argv(executable_path):
    if not Path(executable_path).is_absolute():
        raise Rejected('Use an absolute Codex executable.')
    return [executable_path, '-c', 'cli_auth_credentials_store="file"',
            '-c', 'forced_login_method="chatgpt"', 'login']


LOGIN_METHODS = ('chatgpt', 'api')


def login_method(binding):
    """The ``forced_login_method`` Dervo launches Codex with for this account: 'api' for an API-key account, 'chatgpt'
    for a subscription. Every launch path uses this rule, and the config check expects exactly what it gives."""
    return 'api' if binding.get('method') == 'api_key' else 'chatgpt'


def launched(policy, method):
    """``policy`` as Dervo passed it for a launch with this login method. Anything but 'chatgpt' or 'api' is refused."""
    if method not in LOGIN_METHODS:
        raise Rejected('Codex login method is chatgpt or api')
    return {**policy, 'forced_login_method': method}


def argv(executable_path=None, *, login_method='chatgpt', cwd=None):
    """``login_method`` is 'chatgpt' for a subscription account and 'api' for an API-key account, whose key Codex
    keeps in its own auth.json; under the other value Codex reports the account as signed out. ``cwd`` is the folder the
    thread and config/read will name; the launch marks it untrusted (untrusted_projects)."""
    import json
    if login_method not in ('chatgpt', 'api'):
        raise Rejected('Codex login method is chatgpt or api')
    args=[executable_path or executable()]
    def toml(value):
        if isinstance(value,dict):
            return '{'+','.join(json.dumps(k)+'='+toml(v) for k,v in value.items())+'}'
        return json.dumps(value)
    for key,value in {**for_folder(POLICY, cwd), 'forced_login_method': login_method}.items(): args += ['-c',key+'='+toml(value)]
    return args+['app-server','--listen','stdio://','--strict-config']


def check_profile(home):
    home=Path(home)
    # Refuse custom owned config before startup, including executable hooks.
    skills=home/'skills'
    if skills.exists() or skills.is_symlink():
        if skills.is_symlink() or not skills.is_dir() or any(p.name!='.system' or p.is_symlink() for p in skills.iterdir()):
            raise Rejected('Custom Codex skills need review before sending')
    # Codex itself installs skills/.system during metadata initialization. Those
    # instructions don't grant execution, which stays disabled independently.
    for relative in ('config.toml','.codex/config.toml','hooks.json','hooks','plugins','.agents'):
        p=home/relative
        if relative == 'config.toml' and trust_only_file(p):
            continue  # project trust Codex wrote itself (TRUST_LEVELS); config/read still has to show nothing else
        if p.exists() or p.is_symlink():
            raise Rejected('Custom Codex profile configuration needs review: '+relative)


def verify_config(result, login_method='chatgpt', *, policy=None, cwd=None):
    """Codex's config/read against the policy Dervo launched. ``login_method`` is the one that launch passed ('chatgpt'
    or 'api'), and Codex must echo exactly that: an API-key launch echoing chatgpt, a subscription launch echoing api, or
    any other value is refused. ``cwd`` is the folder that launch passed to argv, whose untrusted entries must be in
    force. Every other key is compared exactly, as before."""
    expected=launched(for_folder(POLICY if policy is None else policy, cwd), login_method)
    layers=result.get('layers')
    if not isinstance(layers,list) or not layers:
        raise Rejected('Codex did not return configuration layers')
    flags=[]
    for layer in layers:
        kind=layer.get('name',{}).get('type')
        if kind=='sessionFlags':
            flags.append(layer.get('config'))
        elif (kind == 'project' and isinstance(layer.get('disabledReason'), str)
              and layer['disabledReason'].strip()):
            # Official app-server reports untrusted project settings as disabled.
            # They are provenance only; effective policy is still checked below.
            continue
        elif kind == 'user' and trust_only(layer.get('config')):
            # The profile's config.toml with project trust only (check_profile). Trust changes no launch setting. A repo
            # .codex/config.toml it switches on comes back as a project layer without a disabledReason, which the next
            # branch refuses like any other custom layer.
            continue
        elif layer.get('config') != {}:
            raise Rejected('Custom or managed Codex configuration needs review before sending')
    if flags != [expected]:
        raise Rejected('Codex launch policy was not applied exactly')
    config=result.get('config',{})
    if any(config.get(k)!=v for k,v in expected.items() if k not in ('features','tools','shell_environment_policy','skills','projects')):
        raise Rejected('Codex effective configuration differs from the launch policy')
    if 'projects' in expected:
        # The profile's own trust entries for other folders may sit beside Dervo's; every one of Dervo's must win.
        projects=config.get('projects')
        if not trust_only({'projects':projects}) or any(projects.get(k)!=v for k,v in expected['projects'].items()):
            raise Rejected('Codex would trust this project folder, so its own .codex settings could load')
    for key in ('features','shell_environment_policy'):
        if any(config.get(key,{}).get(k)!=v for k,v in expected[key].items()):
            raise Rejected('Codex effective tool controls differ from the launch policy')
    skills=config.get('skills') or {}
    if skills.get('include_instructions') is not False or (skills.get('bundled') or {}).get('enabled') is not False:
        raise Rejected('Codex would show skills Dervo hasn\'t reviewed')
    for key in ('hooks','plugins','model_providers','notify','model_catalog_json','model_instructions_file'):
        if config.get(key) not in (None,{},[]):
            raise Rejected('Unexpected executable or provider configuration: '+key)
