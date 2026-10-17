"""Provider-owned sign-in instructions. This module never reads credential files."""
from pathlib import Path
import shlex
from dervo_connections import Rejected
import platform_os
from transport import clean_environment
from codex_policy import executable as codex_executable


from claude_policy import PLANS

# The subscription plans each side accepts. A work account is usually a Team or Enterprise seat, and sometimes a
# plan the employer pays for; a personal one is your own. What keeps the two apart is the exact email each was added
# with and the project's Work or Personal mark (dervo_connections.require_context), not the plan. Codex's "business",
# "enterprise" and "edu" plan names are OpenAI's documented seat types; only "plus", "pro" and "team" were observed here.
# "prolite" is ChatGPT's $100 a month Pro plan (Josh, feedback #9, 27 Sep 2026), in Codex 0.156.1's PlanType. It goes
# wherever "pro" does.
CLAUDE_PLANS = {'personal': PLANS, 'work': PLANS + ('enterprise',)}
CODEX_PLANS = {'personal': ('plus', 'prolite', 'pro'),
               'work': ('plus', 'prolite', 'pro', 'team', 'business', 'enterprise', 'edu')}


def owned_profile(store, root, profile_id, *, context=None):
    """The account, checked: its own folder under Dervo's profiles (or your existing Claude Code, personal only), and,
    when ``context`` is given, the same Work or Personal as the project it would run. Never a fallback to another."""
    from dervo_connections import CONTEXTS, require_context
    row = store.db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
    if row is None:
        raise Rejected('Unknown account profile')
    profile = dict(row)
    if profile.get('removed_at'):
        # Removed in Settings (account_signin.remove): its folder is gone and it never runs again. Whatever used it waits
        # for you to pick another account; Dervo doesn't pick one for you.
        raise Rejected('That account was removed from Dervo. Pick another account. Nothing was sent.')
    home = Path(profile['home'])
    expected = Path(root).resolve() / 'profiles' / profile_id
    from claude_policy import is_system_home
    system = profile['provider'] == 'claude' and profile.get('method') == 'subscription' and is_system_home(home)
    if not system and (home != expected or home.resolve() != expected or home.is_symlink()):
        raise Rejected('Account directory no longer matches its owned profile')
    if profile['category'] not in CONTEXTS:
        raise Rejected('This account isn’t marked Work or Personal. Remove it and add it again.')
    if system and profile['category'] != 'personal':
        # Your existing Claude Code folder is the personal sign-in on this Mac. A work account always gets its own.
        raise Rejected('A work account needs its own sign-in folder, not your existing Claude Code. Add it again as a work account.')
    if context is not None:
        require_context(store.db, context, profile_id)
    if not system:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return profile


def same_email(seen, identity):
    """The email the provider reports is this account's own. 16.2 (Shane, 27 Sep): capitals and stray spaces don't
    count, so ``Shane@x`` typed into Dervo matches ``shane@x`` from ChatGPT. Nothing else is loosened."""
    return (type(seen) is str and type(identity) is str and bool(identity.strip())
            and seen.strip().casefold() == identity.strip().casefold())


def same_org(profile, metadata):
    """The Claude organisation the sign-in is in is this account's own. One email can be in several (Bryce, 8 Oct 2026:
    a personal Max and a Team seat on the same address), and Claude Code's ``auth status`` names it as ``orgId``. An
    account with no org recorded yet (added before beta.18's fix) takes any until its first good check records one
    (Host._checked); after that a sign-in in another organisation is someone else's account."""
    org = profile.get('org')
    return not org or metadata.get('orgId') == org


def org_named(name):
    """An organisation name worth showing. Claude names a personal plan's organisation after its email
    ("you@x.com's Organization"), which says nothing the email doesn't."""
    return (type(name) is str and bool(name.strip()) and not name.strip().casefold().endswith(("'s organization", "'s organisation", "’s organization", "’s organisation"))
            and name.strip())


def claude_subscription(profile, metadata):
    """Claude Code signed in to claude.ai as this account's email and organisation, on a plan its side accepts."""
    return (isinstance(metadata, dict) and metadata.get('loggedIn') is True and metadata.get('authMethod') == 'claude.ai'
            and metadata.get('apiProvider') == 'firstParty' and same_email(metadata.get('email'), profile['identity'])
            and same_org(profile, metadata)
            and metadata.get('subscriptionType') in CLAUDE_PLANS.get(profile.get('category'), ()))


def codex_subscription(profile, account):
    """Codex signed in to ChatGPT as this account's email, on a plan its side accepts."""
    return (isinstance(account, dict) and account.get('type') == 'chatgpt' and same_email(account.get('email'), profile['identity'])
            and account.get('planType') in CODEX_PLANS.get(profile.get('category'), ()))


PLAN_NAMES = {'plus': 'Plus', 'prolite': 'Pro Lite', 'pro': 'Pro', 'max': 'Max', 'team': 'Team', 'business': 'Business', 'enterprise': 'Enterprise',
              'edu': 'Edu', 'free': 'Free', 'go': 'Go'}


def _plans_said(plans):
    names = [PLAN_NAMES.get(plan, plan) for plan in plans]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' or ' + names[-1]


def mismatch_detail(profile, metadata):
    """Which part of a subscription sign-in didn't match, said plainly (16.2, Shane, 27 Sep: "Check it's the account
    you meant" failed with no reason). The email, then the plan. None when neither can be read, so the caller keeps its
    general line."""
    codex = profile['provider'] == 'codex'
    account = metadata.get('account') if codex else metadata
    if not isinstance(account, dict):
        return None
    seen, plan = account.get('email'), account.get('planType' if codex else 'subscriptionType')
    tool, service, site = ('Codex', 'ChatGPT', 'chatgpt.com') if codex else ('Claude Code', 'Claude', 'claude.ai')
    category = profile.get('category')
    identity = str(profile['identity']).strip()
    # An email you never added here is never repeated (Host._checked names your other side's account itself).
    if type(seen) is str and seen.strip() and not same_email(seen, identity):
        return (f'{tool} is signed in with a different email, not {identity}. Sign out of {site} in your browser, then sign in '
                + ('with your work email, not your personal one.' if category == 'work' else f'as {identity}.') + ' No messages were sent.')
    if type(seen) is not str or not seen.strip():
        return None
    if not codex and not same_org(profile, metadata):
        theirs = org_named(metadata.get('orgName'))
        ours = org_named(profile.get('org_name'))
        return (f'{tool} is signed in as {identity}' + (f' in {theirs}' if theirs else ' in another organisation')
                + (f', not {ours}.' if ours else ', not the one this account was added with.')
                + f' Sign out of {site} in your browser, then sign in and pick ' + (ours or 'that organisation') + '. No messages were sent.')
    allowed = (CODEX_PLANS if codex else CLAUDE_PLANS).get(category, ())
    if type(plan) is not str or not plan:
        return f'{tool} didn’t say which {service} plan {identity} is on, so Dervo can’t confirm it. Check again. No messages were sent.'
    line = (f'{identity} is on the {service} {PLAN_NAMES.get(plan.lower(), plan)} plan. A {category} account needs '
            f'{_plans_said(allowed)}.')
    return line + ' No messages were sent.'


def signed_in_as(profile, metadata):
    """The one identity rule the checks, the sign-in and every run share: this account's own sign-in, never another.
    ``metadata`` is Claude Code's ``auth status`` or Codex's ``account/read`` result."""
    from dervo_connections import CONTEXTS
    if profile.get('category') not in CONTEXTS or not isinstance(metadata, dict):
        return False
    if profile.get('method') == 'api_key':
        return api_key_account(profile, metadata)
    if profile['provider'] == 'codex':
        return codex_subscription(profile, metadata.get('account'))
    return claude_subscription(profile, metadata) or own_key_sign_in(profile, metadata)


def api_key_account(profile, metadata):
    """An API-key account is verified by the provider reporting API-key auth, never by an email. Observed 17 September
    2026: Claude Code reports authMethod "api_key" with apiKeySource "ANTHROPIC_API_KEY"; Codex account/read reports
    {"type": "apiKey"} once started with forced_login_method "api"."""
    if profile['provider'] == 'codex':
        account = metadata.get('account')
        return isinstance(account, dict) and account.get('type') == 'apiKey'
    return (metadata.get('loggedIn') is True and metadata.get('authMethod') == 'api_key'
            and metadata.get('apiProvider') == 'firstParty' and metadata.get('apiKeySource') == 'ANTHROPIC_API_KEY')


def own_key_metadata(metadata):
    """Claude Code signed in with its own Anthropic API key: logged in with Anthropic directly, not through a Claude
    subscription. Read from ``auth status`` only; Dervo never sees the key."""
    return (isinstance(metadata, dict) and metadata.get('loggedIn') is True and metadata.get('apiProvider') == 'firstParty'
            and type(metadata.get('authMethod')) is str and metadata.get('authMethod') not in ('', 'claude.ai'))


def own_key_sign_in(profile, metadata):
    """Your existing Claude Code (its own default folder), signed in with its own API key. Anthropic's terms for apps that
    run Claude Code say no built-in sign-in method may be restricted, including the user's own API key, so this is a
    supported way to use your existing sign-in. Only the system folder: an account Dervo added for a subscription still
    has to be that subscription, and a Dervo API-key account is checked by ``api_key_account``."""
    import claude_policy
    return (profile.get('provider') == 'claude' and profile.get('method', 'subscription') == 'subscription'
            and profile.get('category') == 'personal'
            and bool(profile.get('home')) and claude_policy.is_system_home(profile['home']) and own_key_metadata(metadata))


def classify(profile, metadata):
    state = 'unknown'
    key_account = profile.get('method') == 'api_key'
    if profile['provider'] == 'codex':
        account = metadata.get('account')
        if 'account' in metadata and account is None:
            state = 'signed_out'
        elif isinstance(account, dict):
            state = 'identity_verified' if signed_in_as(profile, metadata) else 'identity_mismatch'
    elif metadata.get('loggedIn') is False:
        state = 'signed_out'
    elif metadata.get('loggedIn') is True:
        state = 'identity_verified' if signed_in_as(profile, metadata) else 'identity_mismatch'
    work = profile.get('category') == 'work'
    details = {
        'unknown': 'The provider identity is unknown. Check again. No messages were sent.',
        'signed_out': 'This account is signed out. Sign in to connect it to Dervo.',
        'identity_mismatch': (f'The signed-in account isn’t {profile["identity"]}. No messages were sent. Sign in with your work email, not your personal one.'
                              if work else 'The signed-in identity or plan does not match this personal profile. No messages were sent. Don\'t use a work account here.'),
        'identity_verified': ('Work account verified. It only runs Work projects, and Dervo checks the model and project permissions before each request.'
                              if work else 'Personal account verified. Dervo checks the model and project permissions before each request.')}
    if key_account:
        tool = 'Claude Code' if profile['provider'] == 'claude' else 'Codex'
        details.update({
            'signed_out': f'The saved API key isn’t reaching {tool}. Remove this account and add the key again.',
            'identity_mismatch': f'{tool} is using a different sign-in than this account’s API key. Remove this account and add the key again.',
            'identity_verified': f'API key ready. Dervo passes it only to {tool} on this Mac, and checks the model and project permissions before each request.'})
    if state == 'identity_mismatch' and not key_account:
        details['identity_mismatch'] = mismatch_detail(profile, metadata) or details['identity_mismatch']
    result = {'state': state, 'canSend': False, 'detail': details[state]}
    # Who did sign in, for the host only: it says "that's your personal account" when a work sign-in comes back as one
    # (Host._checked), and drops this before anything reaches the screens.
    codex = profile['provider'] == 'codex' and isinstance(metadata.get('account'), dict)
    seen = (metadata.get('account') or {}).get('email') if codex else metadata.get('email')
    if state == 'identity_mismatch' and not key_account and isinstance(seen, str) and seen.strip():
        result['seen'] = seen.strip()
        # Beta.18: the same sign-in checked as if its email were the account's. Whether it could be added (a sign-in
        # started with no email, account_signin.py) or used instead of the email this account was added with, its plan,
        # and why not. Host only, like ``seen``: Host._checked drops all three before anything reaches a screen.
        candidate = {**profile, 'identity': seen.strip()}
        result['seenOk'] = signed_in_as(candidate, metadata)
        plan = (metadata.get('account') or {}).get('planType') if codex else metadata.get('subscriptionType')
        if type(plan) is str and plan:
            result['seenPlan'] = plan
        if not result['seenOk']:
            result['seenDetail'] = mismatch_detail(candidate, metadata) or details['identity_mismatch']
    return result


def probe(profile, executable_path=None, *, api_key=None):
    """Read provider metadata in the owned home. Never start or resume a thread. ``api_key`` is a Claude API-key
    account's key for this one check; a Codex API-key account is probed with its own login method instead."""
    import json
    import subprocess
    import tempfile
    import time
    from transport import OwnedProcess
    env = clean_environment(profile['provider'], profile['home'], profile['home'])
    if profile['provider'] == 'claude':
        from claude_policy import auth_status, executable
        from provider_plans import plan_of
        metadata = auth_status(executable(), profile['home'], api_key=api_key)
        result = classify(profile, metadata)
        plan = plan_of(metadata)
        # The plan travels with the check so the host can keep it (provider_plans); it isn't part of the identity state.
        # So does the organisation: the host records it on a verified account (Host._checked) and the add card names it.
        if isinstance(metadata, dict) and type(metadata.get('orgId')) is str and metadata['orgId']:
            result = {**result, 'org': metadata['orgId'], 'orgName': metadata.get('orgName') if type(metadata.get('orgName')) is str else None}
        return {**result, 'plan': plan} if plan else result
    # A preflight never loads a selected project. Refuse custom config rather than
    # assuming an empty override removes inherited settings or executable hooks.
    from codex_policy import argv as codex_argv, check_profile, login_method, trust_only_file
    home = Path(profile['home'])
    # The one exception is project trust Codex wrote into config.toml itself (codex_policy.TRUST_LEVELS).
    configs = [home/'.codex'/'config.toml'] + ([] if trust_only_file(home/'config.toml') else [home/'config.toml'])
    if any(p.exists() or p.is_symlink() for p in configs):
        return {'state':'config_blocked', 'canSend':False,
                'detail':'Custom Codex configuration needs review before this profile can be checked. Nothing was launched.'}
    check_profile(home)
    argv = codex_argv(executable_path, login_method=login_method(profile))
    deadline = time.monotonic() + 8
    result = None
    with tempfile.TemporaryDirectory(prefix='dervo-identity-', dir=platform_os.scratch_root()) as neutral, OwnedProcess(argv, cwd=neutral, env=env, byte_limit=1024*1024) as process:
        process.send({'id':1,'method':'initialize','params':{
            'clientInfo':{'name':'dervo','title':'Dervo','version':'0.1.0'},
            'capabilities':{'experimentalApi':False}}})
        initialized = False
        while time.monotonic() < deadline:
            try:
                batch = process.read(max(.001, deadline-time.monotonic()))
            except TimeoutError:
                break
            if batch is None: break
            for message in batch:
                if 'method' in message and 'id' in message:
                    process.send({'id':message['id'],'error':{'code':-32601,'message':'Identity preflight does not run tools'}})
                elif message.get('id') == 1 and not initialized:
                    if 'error' in message: return classify(profile, {})
                    initialized = True
                    process.send({'method':'initialized','params':{}})
                    process.send({'id':2,'method':'account/read','params':{'refreshToken':False}})
                elif message.get('id') == 2 and initialized:
                    # A Codex plan isn't kept here: it arrives with the rate-limit windows (provider_usage), which the
                    # usage summary carries as rate_limits.planType. provider_plans keeps Claude's plan only.
                    result = classify(profile, message.get('result', {}))
                    if result['state'] != 'identity_verified':
                        return result
                    # A verified account also reports its rate-limit windows; the summary shows them until the next update.
                    process.send({'id':3,'method':'account/rateLimits/read','params':{}})
                elif message.get('id') == 3 and initialized:
                    limits = (message.get('result') or {}).get('rateLimits') if isinstance(message.get('result'), dict) else None
                    return {**result, **({'rateLimits': limits} if isinstance(limits, dict) else {})}
    return result if result is not None else classify(profile, {})


def probe_catalog(profile, factory=None):
    """Catalog metadata only, with identity checked in the same process."""
    import tempfile
    import time
    from adapters import CodexAdapter
    from codex_policy import argv, check_profile, login_method
    from transport import OwnedProcess
    if profile['provider']!='codex':
        raise Rejected('Claude model selection remains gated with its custom interface')
    check_profile(profile['home'])
    with tempfile.TemporaryDirectory(prefix='dervo-models-',dir=platform_os.scratch_root()) as neutral:
        process=(factory or OwnedProcess)(argv(login_method=login_method(profile),cwd=neutral),cwd=neutral,env=clean_environment('codex',profile['home'],profile['home']))
        try:
            adapter=CodexAdapter({**profile,'cwd':neutral},process.send,lambda *_:None,catalog_only=True)
            adapter.connect()
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                try: batch=process.read(.1)
                except TimeoutError: continue
                if batch is None: raise Rejected('Codex disconnected before returning its model catalog')
                for event in batch: adapter.receive(event)
                if adapter.state=='catalog':
                    models={m['model']:m for m in adapter.models if m.get('hidden') is False and isinstance(m.get('model'),str)}
                    return {'state':'catalog','canSend':False,'models':list(models.values()),
                        'detail':'Current Codex catalog. Every send checks the exact model and permissions again.'}
                if adapter.state in ('failed','blocked'): break
            raise Rejected('Codex did not return a verified model catalog')
        finally: process.close()


def instructions(profile):
    if profile.get('method') == 'api_key':
        raise Rejected('This account uses an API key. There is no sign-in to run; remove the account and add the key again if it stopped working.')
    env = clean_environment(profile['provider'], profile['home'], profile['home'])
    argv = ['/usr/bin/env', '-i', *[f'{k}={v}' for k, v in env.items()]]
    if profile['provider'] == 'codex':
        argv += [codex_executable(), '-c', 'cli_auth_credentials_store="file"', 'login']
    else:
        from claude_policy import executable, login_argv
        argv += login_argv(executable(), profile['identity'])
    return {'state': 'unchecked', 'canSend': False, 'expectedIdentity': profile['identity'],
            'provider': profile['provider'],
            'command': 'cd ' + shlex.quote(profile['home']) + ' && ' + shlex.join(argv),
            'detail': f'Run this command in Terminal, then choose your {"work" if profile.get("category") == "work" else "personal"} account, {profile["identity"]}, in the provider browser flow. Dervo never asks for a password or token. Return here to check the identity. Queued messages stay local.'}
