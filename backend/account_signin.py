"""Adding a Claude or Codex subscription account by signing in first, using a different sign-in for an account, and
removing any account (beta.18; Bryce, 7 Oct 2026: "I want to make the user experience of adding accounts super easy...
when you type in your email and it doesn't match... I've had people kind of fall off because of that").

Before beta.18 adding asked for the account's email, then the provider's sign-in had to come back as exactly that email,
and anything else was identity_mismatch: a typo, an alias, or a Google sign-in whose email isn't the one typed. Now:

1. ``account_signin_start`` runs the provider's own sign-in (claude_login / codex_login, unchanged) in a Dervo-owned
   folder that isn't an account yet: no row, nothing can run on it, only the sign-in's own status calls reach it.
2. When the sign-in finishes, the ordinary identity probe (connections_ui.probe) reads who signed in, from the provider:
   Claude Code's ``auth status`` email and plan, Codex's ``account/read`` email and plan. ``identity`` says what it found:
   "Signed in as you@x.com, Max plan", already added, your account on the other side, or a plan this side can't use.
3. ``account_signin_add`` makes the account from that sign-in, with the provider's email as its identity, only on your
   click. From then on every check and run is the same exact-email rule as before (connections_ui.signed_in_as).

The rules that matter stay where they were: nothing from a project reaches an account until its identity is verified
(every run still checks it); a sign-in that's your account on the other side, personal or work, is refused by name;
no silent fallback. Adding an account that's already here says so and hands back that account, not a duplicate.

Existing accounts keep the email they were added with. When one's sign-in is a different valid account on its side,
Host._checked names it and offers ``account_use_signed_in`` ("Use this account instead"), again only on your click.

``account_remove`` works for every account: Dervo forgets it, deletes its Dervo-owned folder (which holds its sign-in)
and an API key's saved key. It never signs you out of claude.ai or ChatGPT anywhere else, and never touches ~/.claude:
your existing Claude Code, added as an account, is only forgotten. The row stays, marked removed, so the projects and
threads that used it say "pick an account" instead of moving anywhere on their own. Refused while a run is live on it.
"""
import os
import shutil
import time
from pathlib import Path

from dervo_connections import CONTEXTS, SAME_ORG, Rejected

FIELDS = {
    'account_signin_start': {'provider', 'category'},
    'account_signin_add': {'pendingId'},
    'account_signin_cancel': {'pendingId'},
    'account_use_signed_in': {'profileId'},
    'account_remove': {'profileId'},
}
# A folder made for a sign-in that hasn't become an account. Only folders carrying it are ever swept.
MARKER = '.dervo-pending-sign-in'
TOOL = {'claude': ('Claude Code', 'Claude', 'claude.ai'), 'codex': ('Codex', 'ChatGPT', 'chatgpt.com')}


def _plan_words(provider, plan):
    from connections_ui import PLAN_NAMES
    if type(plan) is not str or not plan:
        return ''
    return f'{TOOL[provider][1]} {PLAN_NAMES.get(plan.lower(), plan)}'


def _same_email(db, provider, email, *, org=None, category=None, other_side_of=None, besides=None):
    """The live subscription account here with this email: on ``category``, or on the side that isn't ``other_side_of``.
    With ``org`` (a Claude sign-in's organisation), an account in another known organisation isn't the same one: Bryce,
    8 Oct 2026, a personal Max and a Team seat on one email. An account whose organisation isn't known yet still counts."""
    sql = ("SELECT id, name, category, org, org_name FROM profiles WHERE provider=? AND method='subscription' AND lower(identity)=lower(?) "
           "AND removed_at IS NULL AND " + SAME_ORG)
    params = [provider, email, org, org]
    if category is not None:
        sql += ' AND category=?'
        params.append(category)
    if other_side_of is not None:
        sql += ' AND category<>?'
        params.append(other_side_of)
    if besides is not None:
        sql += ' AND id<>?'
        params.append(besides)
    row = db.execute(sql + ' ORDER BY rowid LIMIT 1', params).fetchone()
    return dict(row) if row else None


def _name(row, provider):
    return row.get('name') or f'your {TOOL[provider][1]} account'


def adoptable(db, profile, seen, plan, org=None):
    """For Host._checked: what the screens get when an existing account's sign-in is a different valid account on its
    side. ``canUseSignedIn`` is False when another account here already is that email (pick that one instead)."""
    taken = _same_email(db, profile['provider'], seen, org=org, category=profile['category'], besides=profile['id'])
    out = {'signedInAs': seen, 'canUseSignedIn': not taken}
    if plan:
        out['signedInPlan'] = plan
    if taken:
        out['takenBy'] = _name(taken, profile['provider'])
    return out


class AccountSignIns:
    """The host's sign-ins that aren't accounts yet, by pending id. Host thread only, like Host.logins."""

    def __init__(self, host):
        self.host, self.store = host, host.store
        self.pending = {}
        # Accounts checked once to learn their Claude organisation for a sign-in on the same email (_orgs_known).
        self.org_checks = set()

    # ------------------------------------------------------------------ pending sign-ins
    def profile(self, pending_id):
        """The pending sign-in as a profile for the login session and probe: no identity, never in the profiles table."""
        return self.pending.get(pending_id) if type(pending_id) is str else None

    def _folder(self, pending_id):
        root = (Path(self.host.root) / 'profiles').resolve()
        home = root / pending_id
        if not pending_id.isalnum() or home.parent != root or home.is_symlink():
            raise Rejected('That sign-in folder isn’t Dervo’s. Nothing was changed.')
        return home

    def _discard(self, pending_id):
        """Forget a pending sign-in and delete its folder, once its sign-in process has stopped. A folder that has since
        become an account, or doesn't carry the marker, is never touched."""
        profile = self.pending.get(pending_id)
        session = self.host.logins.get(pending_id)
        if session is not None:
            if not session.closed:
                session.cancel('Sign-in cancelled.')
            if not session.closed:
                return False  # its process is still stopping: the next sweep removes the folder
            self.host.logins.pop(pending_id, None)
        self.host.checks.pop(pending_id, None)
        self.pending.pop(pending_id, None)
        if profile is not None:
            self._remove_folder(pending_id)
        return True

    def _remove_folder(self, pending_id):
        home = self._folder(pending_id)
        if self.store.db.execute('SELECT 1 FROM profiles WHERE id=?', (pending_id,)).fetchone():
            return
        if home.is_dir() and (home / MARKER).is_file():
            shutil.rmtree(home, ignore_errors=True)

    def sweep(self):
        """Folders left by a sign-in that never became an account (Dervo closed in the middle). Marked ones only."""
        root = Path(self.host.root) / 'profiles'
        if not root.is_dir():
            return
        for home in root.iterdir():
            if home.name in self.pending or home.is_symlink() or not home.is_dir() or not (home / MARKER).is_file():
                continue
            try:
                self._remove_folder(home.name)
            except Rejected:
                continue

    def start(self, args):
        provider, category = args['provider'], args['category']
        if provider not in TOOL:
            raise Rejected('Choose Claude Code or Codex.')
        if category not in CONTEXTS:
            raise Rejected('Choose Work or Personal for this account. Nothing was started.')
        self.sweep()
        from host import identifier
        pending_id = identifier()
        home = self._folder(pending_id)
        home.mkdir(parents=True, mode=0o700)
        (home / MARKER).write_text('A sign-in Dervo started that isn’t an account yet. Dervo removes this folder.\n')
        self.pending[pending_id] = {'id': pending_id, 'provider': provider, 'home': str(home), 'identity': '', 'category': category,
                                    'method': 'subscription', 'name': None, 'pending': True}
        try:
            status = self.host._connection_login('connection_login', {'profileId': pending_id})
        except Exception:
            self._discard(pending_id)
            raise
        return {**status, 'pendingId': pending_id}

    def identity(self, profile, result):
        """What a finished sign-in found, for the add card. Never a send state: ``canSend`` is always False."""
        provider, category = profile['provider'], profile['category']
        tool, service, site = TOOL[provider]
        out = {'canSend': False}
        state = result.get('state') if isinstance(result, dict) else None
        seen = result.get('seen') if isinstance(result, dict) else None
        if state == 'signed_out':
            return {**out, 'state': 'signed_out', 'detail': f'{tool} says nobody signed in. Try again. Nothing was added.'}
        if state != 'identity_mismatch' or type(seen) is not str or not seen:
            return {**out, 'state': 'unknown', 'detail': f'Dervo couldn’t tell which {service} account signed in. Try again. Nothing was added.'}
        plan = result.get('seenPlan')
        org = result.get('org') if provider == 'claude' else None
        from connections_ui import org_named
        org_name = org_named(result.get('orgName')) if org else None
        out.update(signedInAs=seen, **({'plan': plan} if plan else {}), **({'orgName': org_name} if org_name else {}))
        if org and not self._orgs_known(provider, seen):
            return {**out, 'state': 'checking', 'detail': f'Checking which Claude organisation your other {seen} account is in. Nothing is added yet.'}
        other = _same_email(self.store.db, provider, seen, org=org, other_side_of=category)
        if other:
            side = other['category']
            return {**out, 'state': 'other_side', 'otherSide': side, 'detail': (
                f'That’s your {side} account. {seen} already runs your {side.capitalize()} projects, so it can’t be a {category} '
                f'one too. Sign out of {site} in your browser, then sign in with your {category} email. Nothing was added.')}
        if not result.get('seenOk'):
            from connections_ui import CLAUDE_PLANS, CODEX_PLANS
            allowed = (CODEX_PLANS if provider == 'codex' else CLAUDE_PLANS).get(category, ())
            # A plan the check named (allowed or not) has its own reason; only a sign-in with no plan at all gets the
            # API-key line, which used to show for an allowed plan that failed for another reason (onboarding audit, 8 Oct).
            detail = (result.get('seenDetail') or
                      f'{tool} signed in without a {service} plan Dervo can use, for example with an API key. Sign in with your {service} plan, or add an API key instead.')
            return {**out, 'state': 'refused', 'detail': (detail or '').replace('No messages were sent.', 'Nothing was added.')}
        existing = _same_email(self.store.db, provider, seen, org=org, category=category)
        if existing:
            return {**out, 'state': 'existing', 'profileId': existing['id'], 'name': _name(existing, provider),
                    'detail': f'{seen} is already added here as {_name(existing, provider)}.'}
        words = _plan_words(provider, plan)
        return {**out, 'state': 'signed_in', 'detail': f'Signed in as {seen}' + (f', {words} plan' if words else '')
                + (f' in {org_name}.' if org_name else '.')}

    def _orgs_known(self, provider, email):
        """Every live Claude account here with this email has its organisation recorded, so a sign-in on the same email
        can be told apart from it. One that hasn't (added before Dervo kept it) is checked once now, which records it
        (Host._checked). True when known, or when its check has finished without recording one: it then counts as the
        same sign-in, the safe side. False while a check is still going."""
        rows = self.store.db.execute("SELECT id FROM profiles WHERE provider=? AND method='subscription' AND lower(identity)=lower(?) "
                                     "AND removed_at IS NULL AND org IS NULL", (provider, email)).fetchall()
        known = True
        for (profile_id,) in rows:
            job = self.host.checks.get(profile_id)
            if profile_id not in self.org_checks or job is None:
                try:
                    self._recheck(profile_id)
                except Exception:
                    continue  # an account that can't be checked counts as the same sign-in
                self.org_checks.add(profile_id)
                known = False
            elif not job.done():
                known = False
            elif job.exception() is None:
                from connections_ui import owned_profile
                try:
                    self.host._checked(owned_profile(self.store, self.host.root, profile_id), job.result())
                except Exception:
                    pass
        return known

    def _found(self, pending_id):
        """The finished sign-in and what it found, or a refusal saying what's still missing."""
        profile = self.profile(pending_id)
        if profile is None:
            raise Rejected('That sign-in isn’t running any more. Start adding the account again.')
        status = self.host._connection_login('connection_login_status', {'profileId': pending_id})
        if status.get('state') != 'succeeded':
            raise Rejected('Finish signing in first. Nothing was added.')
        job = self.host.checks.get(pending_id)
        if job is None or not job.done():
            raise Rejected('Dervo is still checking which account signed in. Try again in a moment.')
        try:
            result = job.result()
        except Exception:
            raise Rejected('Dervo couldn’t tell which account signed in. Try again. Nothing was added.') from None
        return profile, result, self.identity(profile, result)

    def add(self, args):
        pending_id = args['pendingId']
        profile, result, found = self._found(pending_id)
        if found['state'] == 'existing':
            # Already here: that account, not a second one. This sign-in's folder goes; the existing account keeps its own.
            self._discard(pending_id)
            return {'id': found['profileId'], 'state': 'existing', 'name': found['name'], 'identity': found['signedInAs']}
        if found['state'] != 'signed_in':
            raise Rejected(found['detail'])
        seen, provider, category = found['signedInAs'], profile['provider'], profile['category']
        org = result.get('org') if provider == 'claude' else None
        org_name = result.get('orgName') if org else None
        home = self._folder(pending_id)
        session = self.host.logins.pop(pending_id, None)
        self.host.checks.pop(pending_id, None)
        self.pending.pop(pending_id, None)
        try:
            self.store.add_profile(pending_id, provider, home, seen, category, org=org, org_name=org_name)
        except Exception:
            self.pending[pending_id] = profile
            if session is not None:
                self.host.logins[pending_id] = session
            raise
        (home / MARKER).unlink(missing_ok=True)
        from connections_ui import org_named
        if self.store.db.execute("SELECT 1 FROM profiles WHERE provider=? AND lower(identity)=lower(?) AND id<>? AND removed_at IS NULL",
                                 (provider, seen, pending_id)).fetchone():
            # Two accounts on one email would both read as that email everywhere. Name this one after its organisation,
            # or its plan for a personal one; you can rename it on Connected accounts.
            name = org_named(org_name) or _plan_words(provider, result.get('seenPlan')) or None
            if name:
                with self.store.db:
                    self.store.db.execute('UPDATE profiles SET name=? WHERE id=? AND name IS NULL', (name, pending_id))
        if result.get('seenPlan') and provider == 'claude':
            import provider_plans
            provider_plans.record(self.store.db, pending_id, {'subscriptionType': result['seenPlan']})
        self._recheck(pending_id)
        return {'id': pending_id, 'state': 'added', 'identity': seen, 'provider': provider, 'category': category}

    def cancel(self, args):
        pending_id = args['pendingId']
        if self.profile(pending_id) is None:
            return {'state': 'cancelled'}
        return {'state': 'cancelled' if self._discard(pending_id) else 'stopping'}

    def _recheck(self, profile_id):
        """A fresh identity check of an account just added or changed, so its row says verified without a click."""
        from connections_ui import owned_profile, probe
        profile = owned_profile(self.store, self.host.root, profile_id)
        if profile['provider'] == 'codex':
            from codex_policy import executable
            self.host.checks[profile_id] = self.host.checker.submit(probe, profile, executable())
        else:
            self.host.checks[profile_id] = self.host.checker.submit(probe, profile)

    # ------------------------------------------------------------------ existing accounts
    def _live(self, profile_id):
        """Why the account can't change now: a run on it, a sign-in on it, or messages queued for it. None when idle."""
        db = self.store.db
        if any(not job.closed and job.binding.get('profile_id') == profile_id for job in self.host.turns.values()):
            return 'A message is running on it right now.'
        coding = getattr(self.host, 'coding', None)
        if coding is not None:
            import json
            for task_id, turn in list(coding.turns.items()):
                if turn.closed:
                    continue
                row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
                if row and (json.loads(row[0]).get('binding') or {}).get('profile_id') == profile_id:
                    return 'A task is running on it right now.'
        session = self.host.logins.get(profile_id)
        if session is not None and not session.closed:
            return 'Its sign-in is still open. Finish or cancel it first.'
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_queue'").fetchone():
            queued = db.execute("SELECT count(*) FROM coding_queue q JOIN threads t ON t.id=q.thread_id WHERE t.profile_id=? "
                                "AND json_extract(q.document, '$.state')='queued'", (profile_id,)).fetchone()[0]
            if queued:
                return f'{queued} queued message{"s are" if queued != 1 else " is"} waiting to run on it. Cancel {"them" if queued != 1 else "it"} or let {"them" if queued != 1 else "it"} run first.'
        return None

    def use_signed_in(self, args):
        """An existing account takes the email its sign-in reports, when that's a valid account on its side, on your click."""
        from connections_ui import owned_profile
        profile = owned_profile(self.store, self.host.root, args['profileId'])
        if profile.get('method') != 'subscription':
            raise Rejected('An API-key account has no sign-in to use. Nothing was changed.')
        job = self.host.checks.get(profile['id'])
        result = job.result() if job is not None and job.done() and job.exception() is None else None
        seen = result.get('seen') if isinstance(result, dict) and result.get('state') == 'identity_mismatch' else None
        if type(seen) is not str or not seen or not result.get('seenOk'):
            raise Rejected('Check sign-in again first, so Dervo knows which account signed in. Nothing was changed.')
        tool, _, site = TOOL[profile['provider']]
        org = result.get('org') if profile['provider'] == 'claude' else None
        other = _same_email(self.store.db, profile['provider'], seen, org=org, other_side_of=profile['category'])
        if other:
            raise Rejected(f'That’s your {other["category"]} account, so it can’t run {profile["category"].capitalize()} projects. '
                           f'Sign out of {site} in your browser, then sign in with your {profile["category"]} email. Nothing was changed.')
        taken = _same_email(self.store.db, profile['provider'], seen, org=org, category=profile['category'], besides=profile['id'])
        if taken:
            raise Rejected(f'{seen} is already {_name(taken, profile["provider"])} here. Pick that account instead. Nothing was changed.')
        busy = self._live(profile['id'])
        if busy:
            raise Rejected(f'{busy} Nothing was changed.')
        with self.store.db:
            self.store.db.execute('UPDATE profiles SET identity=? WHERE id=?', (seen, profile['id']))
            if org:
                self.store.db.execute('UPDATE profiles SET org=?, org_name=? WHERE id=?', (org, result.get('orgName'), profile['id']))
            # The provider sessions these threads kept belong to the old sign-in: their next message starts a new one with
            # the thread's recent messages, the same as moving a thread to another account (conversation_profile_set).
            self.store.db.execute('UPDATE threads SET session_id=NULL WHERE profile_id=?', (profile['id'],))
        if result.get('seenPlan') and profile['provider'] == 'claude':
            import provider_plans
            provider_plans.record(self.store.db, profile['id'], {'subscriptionType': result['seenPlan']})
        self.host.catalogs.pop(profile['id'], None)
        self._recheck(profile['id'])
        return {'id': profile['id'], 'identity': seen}

    def remove(self, args):
        profile_id = args['profileId']
        if type(profile_id) is not str or not profile_id:
            raise Rejected('Choose an account.')
        row = self.store.db.execute('SELECT * FROM profiles WHERE id=?', (profile_id,)).fetchone()
        if row is None or row['removed_at']:
            raise Rejected('That account was already removed.')
        profile = dict(row)
        busy = self._live(profile_id)
        if busy:
            raise Rejected(f'{busy} Nothing was removed.')
        import claude_policy
        system = (profile['provider'] == 'claude' and profile['method'] == 'subscription'
                  and claude_policy.is_system_home(profile['home']))
        home = Path(profile['home'])
        expected = (Path(self.host.root) / 'profiles' / profile_id).resolve()
        owned = not system and home == expected and not home.is_symlink()
        projects = self.store.db.execute('SELECT count(*) FROM projects WHERE profile_id=?', (profile_id,)).fetchone()[0]
        threads = self.store.db.execute('SELECT count(*) FROM threads WHERE profile_id=?', (profile_id,)).fetchone()[0]
        # Marked first, so nothing can start on it while its sign-in is deleted.
        with self.store.db:
            self.store.db.execute('UPDATE profiles SET removed_at=? WHERE id=?', (int(time.time()), profile_id))
        for jobs in (self.host.checks, self.host.catalogs):
            jobs.pop(profile_id, None)
        self.host.logins.pop(profile_id, None)
        left = []
        if profile['method'] == 'api_key' and profile['provider'] == 'claude':
            from account_keychain import KeychainError
            try:
                self.host.keys._keychain(profile_id).delete()
            except (KeychainError, Rejected):
                left.append('its API key in your Mac’s Keychain')
        if owned and home.exists():
            shutil.rmtree(home, ignore_errors=True)
            if home.exists():
                left.append(f'its folder {home}')
        result = {'id': profile_id, 'state': 'removed', 'projects': projects, 'threads': threads, 'kept': bool(system)}
        if left:
            result['detail'] = f'Dervo forgot the account but couldn’t delete {" or ".join(left)}. Delete it yourself if you want it gone.'
        return result

    def dispatch(self, operation, args):
        if type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported account fields')
        if operation == 'account_signin_start':
            return self.start(args)
        if operation == 'account_signin_add':
            return self.add(args)
        if operation == 'account_signin_cancel':
            return self.cancel(args)
        if operation == 'account_use_signed_in':
            return self.use_signed_in(args)
        return self.remove(args)
