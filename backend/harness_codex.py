"""Identity-verified Codex helper binding: from the app's owned profiles into the same harness adapter as Claude.

One helper per (owned personal Codex profile, model that profile's conversations are bound to). The helper's port is
the library's own ``CodexAppServerWorker`` in text-only mode, built per harness project with:

- ``codex_home`` = the profile's owned home (``<root>/profiles/<id>``, the CODEX_HOME the app signs in to; also the
  child's HOME, as the app's own transport does);
- ``identity`` = the profile's account email, which the worker requires ``account/read`` to report on a ChatGPT
  subscription login before any text is sent (a different account, a signed-out home or an API-key account fails the run);
- ``model`` = the conversation's model, which the worker requires to be a visible ``model/list`` entry and the effective
  ``thread/start`` model before the turn is sent;
- ``executable`` = the app's pinned Codex binary (``codex_policy.EXECUTABLE``; tests inject a fake);
- ``work_dir`` = a directory the adapter owns under the harness data dir.

The worker refuses a home carrying custom configuration (``config.toml``, hooks, plugins, skills) before launching, the
same files the app's own ``check_profile`` refuses. Labels are not authority: the account label names which profile's
verification applies (or which owner-created boundary record, see harness_host), and the worker does that verification
itself. Nothing here reads credential files, chooses a fallback account, crosses personal and work profiles
(``owned_profile`` refuses work), or grants anything beyond the one conversation room and the helper's own home room.

A packaged library without the Codex worker (older than 0.7.1) excludes every Codex profile with that reason; nothing
else is selected instead.
"""
import hashlib
import json
import re
from pathlib import Path

from dervo_connections import Rejected
from harness_claude import identity_problem
from harness_port import HelperSpec

MODEL_RE = re.compile(r'^[a-z0-9][a-z0-9.-]{0,62}$')
DRAIN_WAIT = 0.05
FINISH_TIMEOUT = 3.0  # after turn/completed: how long to wait for the server to exit on EOF before terminating the group


def helper_name(profile_id, model):
    return 'codex-' + hashlib.sha256(f'{profile_id}\0{model}'.encode('utf-8')).hexdigest()[:12]


def model_problem(model):
    if type(model) is not str or not MODEL_RE.match(model):
        return f'model {model!r} is not a catalog model id; the worker verifies it against model/list before sending'
    return None


def library_problem():
    import dervo_harness as lib
    if getattr(lib, 'CodexAppServerWorker', None) is None or getattr(lib, 'CodexAppServerConfig', None) is None:
        return f'packaged harness {lib.__version__} has no Codex worker; vendor harness-core 0.7.1 or later'
    return None


def derive(store, root, *, executable=None, label=None):
    """Helpers for every (owned personal Codex profile, model) pair in use, and the excluded pairs with reasons."""
    from connections_ui import owned_profile
    label = label or (lambda profile_id: 'profile:' + profile_id)
    helpers, excluded = {}, []
    unsupported = library_problem()
    rows = store.db.execute("SELECT DISTINCT t.profile_id, t.model FROM threads t JOIN profiles p ON p.id=t.profile_id "
                            "WHERE p.provider='codex' ORDER BY t.profile_id, t.model").fetchall()
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
        reason = identity_problem(profile['identity']) or model_problem(model) or unsupported
        if reason:
            excluded.append({'profile': profile_id, 'model': model, 'reason': reason})
            continue
        helpers[helper_name(profile_id, model)] = HelperSpec(port_factory(profile, model, executable), 'codex', label(profile_id),
                                                              note='Codex app-server, text only', model=model)
    return helpers, excluded


def claim_codex_home(home):
    """Mark the owned profile home as the worker's CODEX_HOME. The library claims empty dirs itself; a signed-in owned dir
    (created by this app under <root>/profiles/<id>, verified by owned_profile) is marked by the app."""
    import dervo_harness as lib
    from dervo_harness.codex_worker import CODEX_HOME_MARKER
    home = Path(home)
    marker = home / CODEX_HOME_MARKER
    if marker.is_file():
        return
    if not home.exists() or not any(home.iterdir()):
        lib.CodexAppServerWorker.prepare_codex_home(str(home))
        return
    marker.write_text(json.dumps({'owner': 'dervo-harness', 'purpose': 'CODEX_HOME for the text-only Codex worker',
                                  'claimed_by': 'dervo-app owned profile'}) + '\n', encoding='utf-8')


def port_factory(profile, model, executable=None):
    home, identity = str(profile['home']), profile['identity']

    def port_for(project_id, work_dir):
        import dervo_harness as lib
        exe = executable
        if exe is None:
            from codex_policy import executable as bundled_executable
            exe = bundled_executable()
        claim_codex_home(home)
        # The library's own PINNED_VERSION is the version harness-core was written against. The app bundles the binary,
        # so initialize has to report the app's pin (codex_runtime.VERSION), and a harness pin that lags it can't block.
        from codex_runtime import VERSION
        # The same plans setup accepts (connections_ui.CODEX_PLANS). The library's own default is only Plus and Pro, so
        # a $100 Pro ("prolite") account passed setup and then every helper run refused it.
        from connections_ui import CODEX_PLANS
        config = lib.CodexAppServerConfig(executable=exe, model=model, project_id=project_id, codex_home=home, identity=identity,
                                          work_dir=str(work_dir), enabled=True, drain_wait=DRAIN_WAIT, finish_timeout=FINISH_TIMEOUT,
                                          version=VERSION, plan_types=CODEX_PLANS['personal'])
        return lib.CodexAppServerWorker(config)

    return port_for
