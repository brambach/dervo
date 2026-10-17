"""Your MCP servers and claude.ai connectors, by name, for a full-access launch.

Claude Code allows an MCP server's tools only by naming the server (``mcp__<server>__*``); a catch-all such as ``mcp__*``
is skipped (permissions docs, read 18 Sep 2026). So Dervo reads the names from `claude mcp list` in the account's own
profile. Checked live on CLI 2.1.274 (18 Sep): it prints one "<name>: <target> - <status>" line per server, includes
claude.ai connectors ("claude.ai Wispr Flow"), health-checks each one and takes about 8 seconds, and --safe-mode hides
every server. Only the names are kept: a line's target can hold a local server's command and arguments.

The list is cached per account and always refreshed in the background: nothing here ever waits for it. The desktop app
ends the backend when one request takes longer than 10 seconds (src/bridge.rs), and on 18 Sep a first full-access run
that waited for this list did exactly that. So a launch uses whatever list is ready, and `warm` starts the listing early,
when you open or turn on full access. A first run that finds no list at all (beta.15) holds its launch in the run's own
tick until `listing` says it's done (claude_coding.ClaudeCodingTurn._listed), never inside a request."""
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import platform_os

FRESH_SECONDS = 600
LIST_TIMEOUT = 30
LIMIT = 64
_LINE = re.compile(r'^(?P<name>[^:\r\n]{1,100}): .* - (?P<status>.+)$')
_cache = {}  # profile home -> (checked monotonic, names)
_running = {}  # profile home -> thread
_lock = threading.Lock()


def server_key(name):
    """How Claude Code turns a server name into the middle of its tool names: "claude.ai Wispr Flow" -> claude_ai_Wispr_Flow."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', name)


def parse(output, *, exclude=('dervo',)):
    """Server names from `claude mcp list`, in order, without the servers it didn't connect to because nobody approved them."""
    names = []
    for line in output.splitlines():
        match = _LINE.match(line.strip())
        if not match or 'Pending approval' in match['status']:
            continue
        name = match['name'].strip()
        key = server_key(name)
        if name and key not in exclude and key.strip('_') and name not in names:
            names.append(name)
    return names[:LIMIT]


def _list(claude, home, env, run):
    try:
        result = run([claude, 'mcp', 'list'], cwd=home, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                     timeout=LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > 262144:
        return None
    return parse(result.stdout)


def _refresh(claude, home, env, run):
    names = _list(claude, home, env, run)
    with _lock:
        if names is not None:
            _cache[home] = (time.monotonic(), names)
        _running.pop(home, None)


def names(claude, home, env, *, run=subprocess.run, now=time.monotonic):
    """The account's server names for this launch, never waiting: the cached list, or none yet. A missing or stale list
    starts a background refresh. An empty list means the run gets no connectors, never a failed or slow launch."""
    with _lock:
        cached = _cache.get(home)
        if (cached is None or now() - cached[0] > FRESH_SECONDS) and home not in _running:
            thread = threading.Thread(target=_refresh, args=(claude, home, dict(env), run), daemon=True,
                                      name='dervo-mcp-list')
            _running[home] = thread
            thread.start()
    return list(cached[1]) if cached else []


def cached(home):
    """The account's last listed server names, or None while it has never been listed. Never lists and never waits."""
    with _lock:
        found = _cache.get(home)
    return list(found[1]) if found else None


def listing(home):
    """True while the account's servers are being listed in the background. Never waits."""
    with _lock:
        return home in _running


def warm(home):
    """Start listing an account's servers ahead of its first full-access run. Never raises and never waits."""
    try:
        from claude_policy import executable
        from transport import clean_environment
        names(executable(), home, clean_environment('claude', home, home))
    except Exception:
        pass


# Parity with a terminal (Harrison, 9 Oct 2026: "less access to various mcp tools"). Measured on Claude Code 2.1.295
# (9 Oct): --restricted, and --setting-sources '' on its own, each leave out every server from your user, local and
# project scopes, so a full-access run had none of them while `claude` in the same folder had all three. 2.1.274 still
# loaded them (18 Sep). Servers passed with --mcp-config load under both. So Dervo reads the same configuration Claude
# Code reads and hands the servers over itself, in a private file rather than argv, since a server's env can hold a key.
# Only what the terminal would start: user and local servers, and a project's .mcp.json servers only once you approved
# them in Claude Code (and trusted the folder), and only for a run that has its own shell anyway (``configured``).
# Approval never counts from the repository's own committed .claude/settings.json.
CONFIG_LIMIT = 32 * 1024 * 1024  # ~/.claude.json grows with history; past this it isn't read
FILE_LIMIT = 1024 * 1024  # .mcp.json and settings files
SERVERS_FILE = 'mcp-servers.json'


def _json(path, limit=FILE_LIMIT):
    try:
        with open(path, 'rb') as file:
            data = file.read(limit + 1)
        if len(data) > limit:
            return {}
        value = json.loads(data.decode('utf-8'))
    except (OSError, ValueError, UnicodeError, RecursionError):  # deeply nested JSON raises RecursionError (review 9 Oct, 4)
        return {}
    return value if isinstance(value, dict) else {}


def _text(path, limit=4096):
    try:
        with open(path, 'rb') as file:
            return file.read(limit).decode('utf-8')
    except (OSError, UnicodeError):
        return ''


def _global_config(home):
    """Claude Code's own global config for this profile: ~/.claude.json for your existing sign-in, .claude.json inside
    a Dervo-owned CLAUDE_CONFIG_DIR. The same file `claude mcp add` writes to."""
    from claude_policy import is_system_home
    if is_system_home(home):
        from transport import account_home
        return Path(account_home()) / '.claude.json'
    return Path(home) / '.claude.json'


def _main_folder(project):
    """The main checkout of a git worktree (where you'd run `claude` yourself), from its .git file. None otherwise.
    A .git file alone could name any folder and borrow its trust and approvals (security review 9 Oct, 1), so the
    worktree's record in the main checkout must point back at this folder, and the main checkout's .git be a folder."""
    dot_git = Path(project) / '.git'
    text = _text(dot_git) if dot_git.is_file() else ''
    if not text.startswith('gitdir:'):
        return None
    gitdir = Path(text[len('gitdir:'):].strip())
    if not gitdir.is_absolute():
        gitdir = Path(project) / gitdir
    gitdir = Path(os.path.realpath(gitdir))
    parts = gitdir.parts
    # <main>/.git/worktrees/<name>
    if len(parts) < 4 or parts[-2] != 'worktrees' or parts[-3] != '.git':
        return None
    back = _text(gitdir / 'gitdir').strip()
    main = Path(*parts[:-3])
    if not back or os.path.realpath(back) != os.path.realpath(dot_git) or not (main / '.git').is_dir() or (main / '.git').is_symlink():
        return None
    return str(main)


def _root(project):
    """The folder Claude Code keys this project by in ~/.claude.json: the git root, the main checkout for a worktree
    (checked against 2.1.295 by review, 9 Oct), else the folder itself. Real path, with / on Windows like the CLI."""
    folder = Path(os.path.realpath(project))
    root = folder
    for candidate in (folder, *folder.parents):
        if (candidate / '.git').exists():
            root = Path(_main_folder(candidate) or candidate) if (candidate / '.git').is_file() else candidate
            break
    key = os.path.realpath(root)
    return key.replace('\\', '/') if platform_os.WINDOWS else key


def _project_files(project, root):
    """The folders whose .mcp.json Claude Code reads: the run's folder up to the git root, nearest first."""
    folder, top = Path(os.path.realpath(project)), Path(root)
    found = [folder]
    for parent in folder.parents:
        if parent != top and top not in parent.parents:
            break
        found.append(parent)
    return found


def _servers(value):
    """Valid named server entries from an ``mcpServers`` object. Dervo's own server name is never taken."""
    servers = {}
    if not isinstance(value, dict):
        return servers
    for name, config in value.items():
        if (type(name) is str and 0 < len(name) <= 100 and isinstance(config, dict) and config.get('type') != 'sdk'
                and server_key(name).strip('_')
                and server_key(name).lower() != 'dervo' and not any(c in name for c in '\r\n\x00')):
            servers[name] = config
    return servers


def configured(home, project, *, project_scope=True):
    """{name: config} for the MCP servers `claude` would start in ``project`` for this profile, local over project over
    user like Claude Code, without the ones you switched off there (/mcp disable). Never raises; anything unreadable is
    left out.

    ``project_scope`` False leaves out the project's .mcp.json servers. Both files that start one (.mcp.json and the
    approval in .claude/settings.local.json, where Claude Code keeps it now) sit in the folder a run can write, so a run
    with no shell (agent commands off, or a Home job) could otherwise plant a command a later run starts (security
    review 9 Oct, 2). A run with its own shell already runs commands as you, so it gets them, like the terminal."""
    if not project or not Path(project).is_absolute():
        return {}
    config = _global_config_cached(home)
    projects = config.get('projects') if isinstance(config.get('projects'), dict) else {}
    root = _root(project)
    # Only the project's own entry counts, for trust too: Claude Code doesn't inherit trust from a parent for MCP
    # approval (review 9 Oct, 1).
    entry = projects.get(root) if isinstance(projects.get(root), dict) else {}
    user = _servers(config.get('mcpServers'))
    local = _servers(entry.get('mcpServers'))
    approved = {}
    if project_scope and entry.get('hasTrustDialogAccepted') is True:
        shared = {}
        for folder in reversed(_project_files(project, root)):  # nearer folders win on a name clash
            shared.update(_servers(_json(folder / '.mcp.json').get('mcpServers')))
        # settings.local.json from the run's folder, and from the main checkout when the run is in a Dervo worktree,
        # where you never ran `claude` yourself. Never the committed .claude/settings.json.
        locals_ = {os.path.realpath(project), root}
        sources = [_json(Path(home) / 'settings.json'), entry] + [_json(Path(folder) / '.claude' / 'settings.local.json') for folder in locals_]
        enabled, disabled, every = set(), set(), False
        for source in sources:
            for key, into in (('enabledMcpjsonServers', enabled), ('disabledMcpjsonServers', disabled)):
                if isinstance(source.get(key), list):
                    into.update(name for name in source[key] if type(name) is str)
            every = every or source.get('enableAllProjectMcpServers') is True
        approved = {name: value for name, value in shared.items() if (every or name in enabled) and name not in disabled}
    servers = {**user, **approved, **local}
    off = {name for name in entry.get('disabledMcpServers') or [] if type(name) is str} if isinstance(entry.get('disabledMcpServers'), list) else set()
    return dict(list((name, value) for name, value in servers.items() if name not in off)[:LIMIT])


_config_cache = {}  # config path -> ((mtime, size), parsed)


def _global_config_cached(home):
    """The global config, parsed again only when it changed: Access polls read it, and it can be tens of MB."""
    path = _global_config(home)
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    with _lock:
        cached = _config_cache.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1]
    value = _json(path, CONFIG_LIMIT)
    with _lock:
        _config_cache[str(path)] = (stamp, value)
    return value


def write_servers(servers, folder):
    """The servers as an --mcp-config file only this account can read, in a run's private folder. Returns its path."""
    path = Path(folder) / SERVERS_FILE
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump({'mcpServers': servers}, out, separators=(',', ':'))
    return str(path)


def forget():
    """Tests only."""
    with _lock:
        _cache.clear()
        _running.clear()
        _config_cache.clear()
