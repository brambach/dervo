"""Identity-verified Claude helper binding: from the app's owned profiles into the harness adapter.

One helper per (owned personal Claude profile, model that profile's conversations are bound to). The helper's port is
the library's own ``ClaudeCliWorker`` in text-only mode, built per harness project with:

- ``config_dir`` = the profile's owned home (``<root>/profiles/<id>``, the CLAUDE_CONFIG_DIR the app signs in to);
- ``identity`` = the profile's account email, which the worker requires ``claude auth status`` to report before it
  launches anything (a different account, a signed-out dir, or a non-subscription login fails the run with no context sent);
- ``model`` = the conversation's model, which the worker requires on the CLI's init message, every assistant message
  and the usage report;
- ``executable`` = the published CLI found by ``claude_policy.executable`` (tests inject a fake);
- ``work_dir`` = a directory the adapter owns under the harness data dir.

Labels are not authority: the ``profile:<id>`` account label only names which profile's verification applies, and the
worker does that verification itself. Nothing here reads credential files, chooses a fallback account, crosses
personal and work profiles (``owned_profile`` refuses work), or grants anything beyond the one conversation room and
the helper's own home room. A profile whose identity is not an email, or a conversation whose model is not a Claude
model id, is excluded with the reason and never launched.

API-key accounts (19 Sep 2026). Anthropic's terms for apps that run Claude Code don't let an app restrict a built-in
sign-in method, the user's own API key included. The library's worker requires a claude.ai subscription and passes no
key, so an API-key account's helper is the same worker with two things changed here, in app code, leaving the vendored
library as it was: its environment carries that account's key as ANTHROPIC_API_KEY, read from Keychain by the host
at the moment of each launch and never stored, and ``claude auth status`` must report API-key auth from exactly that
variable. A subscription account's helper is the library's worker unchanged, so it never gets a key.
"""
from claude_policy import PLANS
import hashlib
import json
import re
from pathlib import Path

from dervo_connections import Rejected
from harness_port import HelperSpec

MODEL_RE = re.compile(r'^claude-[a-z0-9][a-z0-9.-]{0,62}$')
DRAIN_WAIT = 0.05  # seconds one drain waits for CLI output; the host tick loop must stay responsive


def helper_name(profile_id, model):
    return 'claude-' + hashlib.sha256(f'{profile_id}\0{model}'.encode('utf-8')).hexdigest()[:12]


def identity_problem(identity):
    if type(identity) is not str or '@' not in identity or len(identity) > 254 or any(c in identity for c in '\r\n\x00 \t'):
        return 'profile identity is not an account email; the worker cannot verify the signed-in account'
    return None


def model_problem(model):
    if type(model) is not str or not MODEL_RE.match(model):
        return f'model {model!r} is not a Claude model id (claude-...); the worker verifies the effective model against it'
    return None


def derive(store, root, *, executable=None, label=None, actions=frozenset(), key_for=None):
    """Helpers for every (owned personal Claude profile, model) pair in use, and the excluded pairs with reasons.

    ``label(profile_id)`` gives the account label the host records on the helper: ``profile:<id>`` by default, or the
    owner-created boundary label when the host holds such a record (harness_host). ``actions`` lists the profile ids
    whose owner opted the Claude port into the library's ``request_help`` action bridge; the bridge is configured only
    for a profile that is both opted in and inside a boundary record, because only then is there any linked helper the
    library would let it ask. ``key_for(binding)`` is the host's Keychain read for an API-key account; without it an
    API-key account is excluded with the reason."""
    from connections_ui import owned_profile
    label = label or (lambda profile_id: 'profile:' + profile_id)
    helpers, excluded = {}, []
    rows = store.db.execute("SELECT DISTINCT t.profile_id, t.model FROM threads t JOIN profiles p ON p.id=t.profile_id "
                            "WHERE p.provider='claude' ORDER BY t.profile_id, t.model").fetchall()
    for row in rows:
        profile_id, model = row['profile_id'], row['model']
        try:
            profile = owned_profile(store, root, profile_id)
        except Rejected as exc:
            excluded.append({'profile': profile_id, 'model': model, 'reason': str(exc)})
            continue
        if profile.get('category') != 'personal':
            # Helpers stay personal for now (b16-work, 26 Sep 2026): work accounts run Work projects, but helping across
            # accounts isn't built for them yet, so a work account is never offered as a helper.
            excluded.append({'profile': profile_id, 'model': model, 'reason': 'Work accounts can’t be helpers yet. They run Work projects only.'})
            continue
        keyed = profile.get('method') == 'api_key'
        reason = ((None if key_for else 'this API-key account needs the host’s key store, which isn’t available here') if keyed
                  else identity_problem(profile['identity'])) or model_problem(model)
        if reason:
            excluded.append({'profile': profile_id, 'model': model, 'reason': reason})
            continue
        account = label(profile_id)
        can_ask = profile_id in actions and account.startswith('boundary:')
        helpers[helper_name(profile_id, model)] = HelperSpec(port_factory(profile, model, executable, actions=can_ask,
                                                                          key_for=key_for if keyed else None), 'claude', account,
                                                              note='Claude Code, text only' + (', may ask linked helpers' if can_ask else ''),
                                                              model=model, can_ask=can_ask)
    return helpers, excluded


def claim_config_dir(home):
    """Mark the owned profile home as the worker's CLAUDE_CONFIG_DIR. The library claims empty dirs itself; a signed-in
    owned dir (created by this app under <root>/profiles/<id>, verified by owned_profile) is marked by the app."""
    import dervo_harness as lib
    from dervo_harness.claude_worker import CONFIG_MARKER
    home = Path(home)
    marker = home / CONFIG_MARKER
    if marker.is_file():
        return
    if not home.exists() or not any(home.iterdir()):
        lib.ClaudeCliWorker.prepare_config_dir(str(home))
        return
    marker.write_text(json.dumps({'owner': 'dervo-harness', 'purpose': 'CLAUDE_CONFIG_DIR for the text-only worker',
                                  'claimed_by': 'dervo-app owned profile'}) + '\n', encoding='utf-8')


def port_factory(profile, model, executable=None, actions=False, key_for=None):
    home, identity = str(profile['home']), profile['identity']
    keyed = profile.get('method') == 'api_key'
    if keyed and key_for is None:
        raise Rejected('An API-key account’s helper needs its key from Keychain.')
    if keyed:
        # The library's config wants an email-shaped identity, and an API-key account has none. This label is one no
        # sign-in can report (a profile id has no domain); the keyed worker checks API-key auth instead of an email.
        identity = f'api-key@{profile["id"]}'

    def port_for(project_id, work_dir):
        import dervo_harness as lib
        if executable is None:
            from claude_policy import executable as find_executable
            exe = find_executable()  # Rejected when the published CLI is not installed
        else:
            exe = executable
        claim_config_dir(home)
        config = lib.ClaudeCliConfig(executable=exe, model=model, project_id=project_id, config_dir=home, identity=identity,
                                     work_dir=str(work_dir), enabled=True, actions=bool(actions), drain_wait=DRAIN_WAIT,
                                     subscription_types=PLANS)
        if keyed:
            binding = {**profile, 'profile_id': profile['id']}
            return keyed_worker_class(lib)(config, lambda: key_for(binding))
        return lib.ClaudeCliWorker(config)

    return port_for


def keyed_worker_class(lib):
    """The library's Claude worker for an API-key account: the same launch and the same checks on every message, with
    the account's key in the child's environment and API-key auth required from ``claude auth status``."""
    from connections_ui import api_key_account
    base = lib.ClaudeCliWorker

    class ApiKeyClaudeWorker(base):
        def __init__(self, config, read_key):
            super().__init__(config)
            self._read_key = read_key  # the host's Keychain read for this one account; the key itself isn't kept here

        def environment(self):
            env = super().environment()
            key = self._read_key()
            if type(key) is not str or not key or not key.isascii() or any(c.isspace() for c in key):
                raise lib.StateError('The API key for this account is unreadable; not launching')
            env['ANTHROPIC_API_KEY'] = key
            return env

        def observe_auth(self):
            """``claude auth status`` in the owned config dir with this account's key: it must report API-key auth from
            ANTHROPIC_API_KEY, first-party. Never a model call. The observed fields hold no secret."""
            import subprocess
            env = self.environment()
            cwd = self.work_dir / 'auth-cwd'
            cwd.mkdir(parents=True, exist_ok=True)
            try:
                proc = subprocess.run([self.config.executable, '--safe-mode', 'auth', 'status'], cwd=str(cwd), env=env,
                                      stdin=subprocess.DEVNULL, capture_output=True, timeout=self.config.auth_timeout)
            except subprocess.TimeoutExpired:
                raise lib.StateError('auth status did not answer in time; not launching') from None
            try:
                meta = json.loads(proc.stdout) if proc.returncode == 0 and len(proc.stdout) <= 65536 else None
            except (ValueError, UnicodeError):
                meta = None
            if not isinstance(meta, dict) or not api_key_account({'provider': 'claude'}, meta):
                raise lib.StateError('Claude Code isn’t using this account’s API key; not launching')
            return {k: meta.get(k) for k in ('loggedIn', 'authMethod', 'apiProvider', 'apiKeySource')}

    return ApiKeyClaudeWorker
