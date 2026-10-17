"""First run: one short setup that finds what's already on this Mac and ends with an agent reading your project.

Bryce, 18 September 2026, after the onboarding research: no tour and no questionnaire. Ask only what changes what happens
next, detect everything else, and reach real work fast. The steps, in the renderer's FirstRun screen:

1. Getting your Mac ready. ``first_run_status`` checks, without asking: your Dervo beta access, Claude Code (installed,
   version, and whether your existing sign-in is usable), Codex (bundled, and whether an account is connected) and git.
2. Choose a project. The native folder picker stages the folder (``_stage_project_folder``); ``first_run_inspect``
   says what it found: git repository and branch, uncommitted files, and the stack from files at the top of the folder.
3. Which agent. ``first_run_claude`` turns your existing Claude Code sign-in into a Dervo account (no second sign-in).
   Codex connects through the existing account flow.
4. How much it can do, and start. ``first_run_finish`` creates the project, allows coding for the whole folder with your
   approval before anything is kept (or reading only), and starts a read-only "Project overview" conversation. Full access
   (recommended, Bryce 9 Oct) also lets the agent run commands, with its warning on screen, and saves the checks the folder
   suggests, so Dervo runs the project's tests after each change.

Nothing here runs a model except the overview the person starts, and nothing reads file contents to detect the stack:
only which marker files exist. The checks setup offers come from project_checks.suggested, which reads package.json's
scripts.
"""
import json
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, wait as wait_for
from pathlib import Path

from dervo_connections import Rejected
import platform_os

FIELDS = {
    'first_run_status': set(),
    'first_run_claude': set(),
    'first_run_inspect': {'selectionId'},
    'first_run_finish': {'selectionId', 'profileId', 'model', 'name', 'access'},
}
CLAUDE_DEFAULT_MODEL = 'claude-opus-5'  # before Claude Code's version is known; see claude_version.default_model
KEY_IDENTITY = 'Claude Code’s API key'
WAITING_DETAIL = 'Your beta access is waiting for approval. The overview starts as soon as it’s approved.'
REMOVED_DETAIL = 'This account no longer has access to Dervo. Your saved work is still on this Mac.'
PLAN_DETAIL = 'Choose a plan or start your free trial in Settings › Account to start agent work. Your saved work stays on this Mac.'
ACCESS = ('full', 'edit', 'read')  # full: edit plus the agent's own commands, with the project's checks after each change
# Marker files at the top of a folder, and the stack they name. Existence only; contents are never read.
STACKS = [
    ('src-tauri', 'Tauri'), ('Cargo.toml', 'Rust'), ('package.json', 'JavaScript'), ('tsconfig.json', 'TypeScript'),
    ('next.config.js', 'Next.js'), ('next.config.mjs', 'Next.js'), ('next.config.ts', 'Next.js'), ('vite.config.ts', 'Vite'),
    ('pyproject.toml', 'Python'), ('requirements.txt', 'Python'), ('go.mod', 'Go'), ('Gemfile', 'Ruby'),
    ('Package.swift', 'Swift'), ('pubspec.yaml', 'Flutter'), ('composer.json', 'PHP'), ('pom.xml', 'Java'),
    ('build.gradle', 'Java'), ('build.gradle.kts', 'Kotlin'), ('mix.exs', 'Elixir'), ('deno.json', 'Deno'),
]


CHECKING_CLAUDE = 'Still checking Claude Code. This can take a moment the first time it runs.'
CHECKING_GIT = 'Still checking git.'
UNKNOWN_CLAUDE = 'Claude Code didn’t answer when Dervo checked its sign-in, so Dervo can’t tell yet. Choose Check again.'
# The bridge ends a host request after 10 s (src/bridge.rs). The Mac check's probes (claude --version, its auth status,
# xcode-select -p and git --version) could take 26 s one after another on a slow first launch, and the tester's first
# screen said "Local store didn't answer" (systems report, 23 Sep, problem 7). So they run side by side off the request
# thread and anything still going comes back as 'checking'; the screen asks again each second. Only the request that
# starts a probe waits for it, up to PROBE_WAIT. A request that finds it still running waits JOIN_WAIT at most, so the
# polling never parks the host's one request loop, which also serves Stop and the feeds (backend review, 23 Sep, 4).
PROBE_WAIT = 6.0
JOIN_WAIT = 0.3
PROBE_FRESH = 5.0  # an unread answer serves the next request for this long after it finished, then it's checked again
_probes, _probes_lock = {}, threading.Lock()


class _Probe:
    """One probe on its own daemon thread, so a slow one never holds up quitting (a pool's workers are joined at exit)."""
    def __init__(self, work):
        self.future, self.done_at = Future(), None
        threading.Thread(target=self._run, args=(work,), name='dervo-first-run', daemon=True).start()

    def _run(self, work):
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001 - handed to the reader, which answers "couldn't tell"
            self.done_at = time.monotonic()
            self.future.set_exception(exc)
            return
        self.done_at = time.monotonic()
        self.future.set_result(result)


def _start(key, work):
    """The probe for ``key`` and whether this call started it: one still running, or finished within PROBE_FRESH and not
    yet read, is joined instead of started again. Probes only run subprocesses; anything that reads the store stays on
    the request thread (SQLite connections are per thread)."""
    with _probes_lock:
        found = _probes.get(key)
        if found is not None and found.future.done() and time.monotonic() - (found.done_at or 0) >= PROBE_FRESH:
            found = None
        if found is not None:
            return found, False
        found = _probes[key] = _Probe(work)
        return found, True


def _answer(key, probe, wait):
    """The probe's answer after waiting at most ``wait`` seconds, or None while it's still running. An answer is read
    once, so "Check again" after signing in in Terminal never gets the old one. A probe that raised answers {}."""
    wait_for([probe.future], timeout=max(0.0, wait))
    if not probe.future.done():
        return None
    with _probes_lock:
        if _probes.get(key) is probe:
            del _probes[key]
    try:
        return probe.future.result()
    except Exception:  # noqa: BLE001 - a probe that broke reads as "couldn't tell", never as a failed request
        return {}


def _run(argv, *, env=None, timeout=6, run=subprocess.run):
    try:
        return run(argv, capture_output=True, text=True, timeout=timeout, env=env or {'PATH': '/usr/bin:/bin', 'LANG': 'en_US.UTF-8'})
    except (OSError, subprocess.SubprocessError):
        return None


def _account(host):
    session = getattr(host, 'account_session', None)
    if session is None:
        return dict(state='ready', detail='This copy of Dervo doesn’t need a beta sign-in.', email=None)
    try:
        status = session.status()
    except Exception:
        return dict(state='unknown', detail='Dervo couldn’t check your beta access. Check again in a moment.', email=None)
    identity = status.get('identity') or {}
    email = identity.get('email')
    if status.get('state') != 'signed_in':
        return dict(state='sign_in', detail='Sign in with the email your beta invite went to.', email=email)
    membership = status.get('membership') or {}
    if membership.get('allowed'):
        return dict(state='ready', detail='You’re in the beta.', email=email)
    # An account whose access was taken away isn't waiting for anything: saying "pending" sent it to wait for an approval
    # that wasn't coming (merge review of #23).
    if membership.get('status') == 'revoked' or ((status.get('plan') or {}).get('plan')) == 'removed':
        return dict(state='removed', detail=REMOVED_DETAIL, email=email)
    # Waiting for approval comes first: a trial can't open access that isn't approved yet (code review, 8 Oct).
    if membership.get('status') == 'pending':
        return dict(state='pending', detail='Your beta access is waiting for approval. Set up now, and you can start once it’s approved.',
                    email=email)
    if ((status.get('plan') or {}).get('plan')) == 'none':
        # No trial or plan yet, or it ended (paid plans, 8 Oct): set up now, and agent work starts once there's a plan.
        return dict(state='plan', detail=status.get('detail') or PLAN_DETAIL, email=email)
    return dict(state='pending', detail='Your beta access is waiting for approval. Set up now, and you can start once it’s approved.',
                email=email)


def _claude_probe(path, run):
    """Claude Code's version and, when it's new enough, its auth status. Subprocesses only, so it can run off the request
    thread (_start). ``meta`` is None when the auth status didn't answer (a timeout or a failed launch): that's unknown,
    not signed out (AGENTS.md: show unknown when observation is lost; product review, 23 Sep, 6)."""
    import claude_policy
    import claude_version
    found = _run([path, '--version'], env=_claude_env(), run=run)
    parsed = claude_version.parse(found.stdout) if found and found.returncode == 0 and found.stdout else None
    if claude_version.too_old(parsed):
        return dict(parsed=parsed, meta={})
    try:
        meta = claude_policy.auth_status(path, str(claude_policy.system_config_dir()), run=run)
    except Rejected:
        meta = {}
    except (OSError, subprocess.SubprocessError):
        meta = None
    return dict(parsed=parsed, meta=meta)


def _claude(host, run=subprocess.run, wait=PROBE_WAIT, join_wait=JOIN_WAIT):
    """Claude Code on this Mac, from a probe (_start). ``join_wait`` is how long to wait on a probe another request
    started: a poll only glances, while a click (use_claude) waits the full budget for its answer."""
    import claude_policy
    import claude_version
    try:
        path = claude_policy.executable()
    except Rejected:
        if platform_os.WINDOWS:
            return dict(state='missing', detail='Claude Code isn’t installed on this computer.',
                        install='irm https://claude.ai/install.ps1 | iex', path=None, version=None, profile_id=None)
        return dict(state='missing', detail='Claude Code isn’t installed on this Mac.',
                    install='curl -fsSL https://claude.ai/install.sh | bash', path=None, version=None, profile_id=None)
    existing = _system_profile(host)
    key = ('claude', path, run)
    probe, started = _start(key, lambda: _claude_probe(path, run))
    facts = _answer(key, probe, wait if started else min(wait, join_wait))
    if facts is None:
        return dict(state='checking', path=path, version=None, profile_id=existing['id'] if existing else None, detail=CHECKING_CLAUDE)
    parsed = facts.get('parsed')
    version = '.'.join(map(str, parsed)) if parsed else None
    if claude_version.too_old(parsed):
        # Dervo's runs need a newer Claude Code (PLAN-native A7). It's installed, so the fix is an update, not an install.
        return dict(state='outdated', path=path, version=version, profile_id=existing['id'] if existing else None,
                    update=claude_version.UPDATE_COMMAND,
                    detail=f'Claude Code {version} is too old for Dervo. Update it to {claude_version.FLOOR_TEXT} or later.')
    meta = facts.get('meta')
    base = dict(path=path, version=version, profile_id=existing['id'] if existing else None)
    if meta is None:
        return dict(base, state='unknown', detail=UNKNOWN_CLAUDE)
    if meta.get('loggedIn') is not True:
        # Dervo's own check came back signed out. That's what Claude Code answered, so it's what we can say, and it's
        # sometimes wrong (21 Sep: the check ran without USER and every signed-in tester was told to sign in). The
        # sentence says what wasn't found and what to do, not what's true of the Mac.
        return dict(base, state='signed_out', login='claude',
                    detail='Claude Code is installed, but Dervo couldn’t find its sign-in. If you’re already signed in, '
                           'choose Check again. If not, run claude in ' + ('PowerShell' if platform_os.WINDOWS else 'Terminal')
                           + ' and sign in.')
    if meta.get('apiProvider') != 'firstParty':
        return dict(base, state='unsupported', detail='Claude Code is set up for a cloud provider. Dervo runs it with your Claude '
                    'account or an Anthropic API key.')
    from connections_ui import own_key_metadata
    if own_key_metadata(meta):
        # Anthropic's terms for apps that run Claude Code: no built-in sign-in method may be restricted, including the
        # user's own API key. So Claude Code's own key sign-in is your existing sign-in, billed per use by Anthropic.
        return dict(base, state='ready', method='api_key', email=meta.get('email'), plan=None,
                    detail='Signed in with an Anthropic API key. Anthropic bills it per use, not your plan.')
    if meta.get('subscriptionType') not in claude_policy.PLANS:
        plan = meta.get('subscriptionType') or 'this'
        return dict(base, state='unsupported', detail=f'Claude Code is signed in on the {plan} plan, which the beta doesn’t support yet.')
    return dict(base, state='ready', method='subscription', email=meta.get('email'), plan=meta.get('subscriptionType'),
                detail='Signed in' + (f" as {meta['email']}" if meta.get('email') else '') + '.')


def _claude_env():
    from transport import account_home
    if platform_os.WINDOWS:
        return dict(platform_os.provider_environment(), HOME=account_home())
    return {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': account_home(), 'LANG': 'en_US.UTF-8'}


def _system_profile(host):
    import claude_policy
    for row in host.store.db.execute("SELECT * FROM profiles WHERE provider='claude' AND method='subscription' AND category='personal' AND removed_at IS NULL"):
        if claude_policy.is_system_home(row['home']):
            return dict(row)
    return None


def _codex(host):
    try:
        import codex_runtime
        codex_runtime.executable()
        bundled = True
    except Exception:
        bundled = False
    connected = []
    for row in host.store.db.execute("SELECT id, identity, home, method FROM profiles WHERE provider='codex' AND category='personal' AND removed_at IS NULL"):
        if (Path(row['home']) / 'auth.json').is_file():
            connected.append(dict(id=row['id'], identity=row['identity'], method=row['method']))
    if not bundled:
        return dict(state='missing', detail='Codex isn’t available in this copy of Dervo.', profiles=[])
    if connected:
        return dict(state='ready', detail='Connected as ' + connected[0]['identity'] + '.', profiles=connected)
    return dict(state='available', detail='Ready to connect your ChatGPT account.', profiles=[])


def _git(run=subprocess.run):
    if platform_os.WINDOWS:
        result = _run([platform_os.git_executable(), '--version'], env=platform_os.provider_environment(), run=run)
        if result is None or result.returncode != 0:
            return dict(state='missing', detail='Git for Windows isn’t installed.', install='winget install --id Git.Git -e',
                        version=None)
        version = result.stdout.replace('git version', '').strip().split(' ')[0]
        return dict(state='ready', detail=f'Version {version}. Every agent change can be undone.', version=version)
    tools = _run(['/usr/bin/xcode-select', '-p'], run=run)
    if tools is None or tools.returncode != 0:
        # /usr/bin/git without the command line tools opens Apple's installer dialog, so never call it here.
        return dict(state='missing', detail='Git needs Apple’s command line tools.', install='xcode-select --install', version=None)
    result = _run(['/usr/bin/git', '--version'], run=run)
    if result is None or result.returncode != 0:
        return dict(state='missing', detail='Git isn’t working on this Mac.', install='xcode-select --install', version=None)
    version = result.stdout.replace('git version', '').strip().split(' ')[0]
    return dict(state='ready', detail=f'Version {version}. Every agent change can be undone.', version=version)


def status(host, run=subprocess.run, wait=PROBE_WAIT):
    projects = host.store.db.execute('SELECT count(*) FROM projects').fetchone()[0]
    deadline = time.monotonic() + wait
    # git's probe starts first, so it runs while Claude Code's is waited for; the whole request waits at most ``wait``,
    # and a poll that finds both still running only glances at each (JOIN_WAIT).
    key = ('git', run)
    probe, started = _start(key, lambda: _git(run=run))
    claude = _claude(host, run=run, wait=wait)
    left = max(0.0, deadline - time.monotonic())
    git = _answer(key, probe, left if started else min(left, JOIN_WAIT))
    if git is None:
        git = dict(state='checking', detail=CHECKING_GIT, version=None)
    elif not git:
        git = dict(state='missing', detail='Git isn’t working on this Mac.', install='xcode-select --install', version=None)
    return dict(account=_account(host), claude=claude, codex=_codex(host), git=git, projects=projects,
                checked_at=int(time.time()))


def use_claude(host, run=subprocess.run):
    """Your existing Claude Code sign-in as a Dervo account. Reuses the account if it's already there."""
    import claude_policy
    # A click, not a poll: it waits the full budget even on a probe the screen's poll started (backend review, 23 Sep, 5).
    found = _claude(host, run=run, join_wait=PROBE_WAIT)
    if found['state'] != 'ready':
        raise Rejected(found['detail'])
    # A subscription sign-in is known by its email. Claude Code's own API key may report none, so it's named for what it is.
    identity = found.get('email') or (KEY_IDENTITY if found.get('method') == 'api_key' else None)
    if not identity:
        raise Rejected('Claude Code didn’t report which account is signed in. Check again.')
    import provider_plans
    existing = _system_profile(host)
    if existing:
        if existing['identity'] != identity:
            with host.store.db:
                host.store.db.execute('UPDATE profiles SET identity=? WHERE id=?', (identity, existing['id']))
        provider_plans.record(host.store.db, existing['id'], {'subscriptionType': found.get('plan')})
        return dict(id=existing['id'], identity=identity, model=_default_model(found))
    key = uuid.uuid4().hex
    host.store.add_profile(key, 'claude', claude_policy.system_config_dir(), identity, 'personal')
    provider_plans.record(host.store.db, key, {'subscriptionType': found.get('plan')})
    return dict(id=key, identity=identity, model=_default_model(found))


def _default_model(found):
    """Opus 5.5 when the Claude Code first run found can run it, otherwise Opus 5 (claude_version.default_model)."""
    import claude_version
    return claude_version.default_model(claude_version.parse(found.get('version') or ''))


def inspect(host, args, run=subprocess.run):
    selection = getattr(host, 'picked_folder', None)
    if not selection or args['selectionId'] != selection['id']:
        raise Rejected('Choose the project folder again.')
    folder = Path(selection['path'])
    import task_git
    root = task_git.repo_root(str(folder), run=run)
    facts = dict(path=str(folder), name=folder.name, git=bool(root), repo=None, branch=None, uncommitted=0, stack=[])
    if root:
        facts['repo'] = Path(root).name
        branch = task_git._git(str(folder), 'rev-parse', '--abbrev-ref', 'HEAD', run=run)
        facts['branch'] = branch.stdout.strip() if branch.returncode == 0 else None
        changed = task_git._git(str(folder), 'status', '--porcelain', '--untracked-files=normal', run=run)
        facts['uncommitted'] = len([line for line in changed.stdout.splitlines() if line.strip()]) if changed.returncode == 0 else 0
        if facts['branch'] == 'HEAD':
            facts['branch'] = None
    try:
        names = {entry.name for entry in os.scandir(folder)}
    except OSError:
        names = set()
    stack = []
    for marker, label in STACKS:
        if marker in names and label not in stack:
            stack.append(label)
    if 'TypeScript' in stack and 'JavaScript' in stack:
        stack.remove('JavaScript')
    facts['stack'] = stack[:4]
    # Setup's access choice (Bryce, 9 Oct): full access is offered with its warning on screen, and turning it on in
    # finish needs the warning's version token, as the confirm in Settings sends it. checks: what Dervo would run after each
    # change, kept with the selection so finish saves exactly the ones shown.
    import project_checks
    from conversation_coding import ConversationCoding
    selection['checks'] = [check['name'] for check in project_checks.suggested(folder)] if root else []
    facts['full_access'] = dict(warning=ConversationCoding.FULL_ACCESS_WARNING,
                                detail={provider: ConversationCoding.full_access_text(provider) for provider in ('claude', 'codex')},
                                checks=selection['checks'])
    return facts


def _overview_from_starter(host, project_id, channel_id):
    """The new project's own empty thread becomes "Project overview", so setup leaves one thread, not an empty "New
    conversation" beside the overview (onboarding review, 9 Oct). Nothing has been sent in it yet."""
    db = host.store.db
    import project_lifecycle  # noqa: F401  (its schema makes project_explorations and conversation_origins)
    if db.execute('SELECT 1 FROM project_explorations WHERE project_id=?', (project_id,)).fetchone():
        return
    organisation = host.organisation()
    state = dict(organisation.read(channel_id), name='Project overview', nameLocked=True, undo=None)
    with db:
        organisation.write(channel_id, state)
        db.execute('INSERT INTO project_explorations VALUES (?,?)', (project_id, channel_id))
        db.execute("UPDATE conversation_origins SET origin='overview' WHERE thread_id=?", (channel_id,))


def finish(host, args):
    """Create the project with the chosen account, allow the chosen access, and start the read-only overview.

    Nothing that goes wrong after the project exists stops setup. Editing that can't be granted leaves the project reading
    only, with ``access_detail`` saying why, and the overview still starts. Full access that can't be turned on leaves it
    editing with your approval, the same way. While beta access waits for approval, the
    overview's thread is made but nothing is sent: its state is ``waiting`` and the renderer starts it with
    ``project_explore`` once access is approved. An overview that can't start for another reason comes back with its
    thread, when there is one, and ``overview_detail``."""
    if args.get('access') not in ACCESS:
        raise Rejected('Choose how much the agent can do.')
    from project_setup import save
    selection = getattr(host, 'picked_folder', None) or {}
    shown, again = selection.get('checks') or [], bool(selection.get('saved'))
    created = save(host, {k: args[k] for k in ('selectionId', 'profileId', 'model', 'name')})
    project_id, channel_id = created['id'], created['channelId']
    access, extra = args['access'], {}
    scope = {'projectId': project_id, 'channelId': channel_id}
    if again:
        # Start again after a failure partway (the project was made): what the first try granted gives way to this choice.
        for operation, wanted in (('coding_full_access_set', access != 'full'), ('coding_permission_revoke', access == 'read')):
            if wanted:
                try:
                    host.dispatch(operation, {**scope, 'enabled': False} if operation == 'coding_full_access_set' else scope)
                except Rejected:
                    pass
    if access in ('full', 'edit'):
        try:
            host.dispatch('coding_permission_grant', {**scope, 'files': [], 'testFile': None, 'projectAccess': True, 'checkProfile': 'manual-review'})
        except Rejected as exc:
            access, extra['access_detail'] = 'read', str(exc)
    if access == 'full':
        # Full access builds on the grant above, so anything it can't do leaves the project editing with your approval.
        try:
            host.dispatch('coding_full_access_set', {**scope, 'enabled': True, 'warning': args.get('warning')})
        except Rejected as exc:
            access, extra['access_detail'] = 'edit', str(exc)
    if access == 'full':
        # The checks the folder suggests, saved as if ticked in Settings › Checks: the run's tests after each change.
        names = list(shown)
        try:
            if names:
                host.dispatch('coding_checks_set', {**scope, 'names': names})
            extra['checks'] = names
        except Rejected as exc:
            extra['checks'], extra['checks_detail'] = [], str(exc)
    _overview_from_starter(host, project_id, channel_id)
    account = _account(host)
    if account['state'] in ('pending', 'plan'):
        # Starting now would record a request that fails on access and can't be started again under the same ID. So the
        # overview's thread is made now, and the renderer starts the overview with project_explore on approval, or once
        # a trial or plan gives access (``waiting_for`` says which).
        from project_lifecycle import exploration_thread
        detail = WAITING_DETAIL if account['state'] == 'pending' else account['detail']
        return dict(created, **extra, access=access, overview_detail=detail,
                    overview=dict(channelId=exploration_thread(host, project_id), state='waiting', detail=detail,
                                  waiting_for='beta' if account['state'] == 'pending' else 'plan'))
    try:
        overview = host.dispatch('project_explore', {'projectId': project_id, 'clientId': 'first-run-' + project_id})
    except Rejected as exc:
        try:
            row = host.store.db.execute('SELECT thread_id FROM project_explorations WHERE project_id=?', (project_id,)).fetchone()
        except sqlite3.Error:
            row = None
        return dict(created, **extra, access=access, overview_detail=str(exc),
                    overview=dict(channelId=row[0], state='not_started', detail=str(exc)) if row else None)
    result = dict(created, **extra, access=access, overview=dict(channelId=overview.get('channelId'), state=overview.get('state'),
                                                                detail=overview.get('detail')))
    if overview.get('state') in ('blocked', 'failed'):
        # The request was recorded but its run was refused as it started: say why, next to the project it's about.
        result['overview_detail'] = overview.get('error') or overview.get('detail')
    return result


def dispatch(host, operation, args):
    # Turning on full access sends the warning's version token too, which first_run_inspect gives out with the warning.
    optional = {'warning'} if operation == 'first_run_finish' else set()
    if type(args) is not dict or not FIELDS[operation] <= set(args) <= FIELDS[operation] | optional:
        raise Rejected('Unsupported first-run fields')
    if operation == 'first_run_status':
        return status(host)
    if operation == 'first_run_claude':
        return use_claude(host)
    if operation == 'first_run_inspect':
        return inspect(host, args)
    return finish(host, args)
