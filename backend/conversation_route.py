"""Who works on a conversation's next coding request.

A conversation is either ``fixed`` on its own account (the default, and what every conversation was before this) or
``auto``. Auto never rotates between accounts of one provider: the only other choice it knows is your account on the
other provider, taken from the most recent conversation in this workspace that used that provider, with the model you
chose there. Bryce's decisions of 17 September 2026 set the rest:

* By default Auto suggests. When the conversation's account is low (the host's usage summary says so) or a run fails on
  the provider's side, the reply says so and offers the other provider. Nothing moves until you choose.
* With the project's "switch automatically" choice on (coding_route_policy), an Auto conversation moves to the other
  provider before starting when its own account is low, and any conversation retries once there after a provider failure.

Every request records ``route`` on its task: the mode, the account and model that ran it, and a plain reason.
"""
import json
import time

MODES = ('auto', 'fixed')
PROVIDER_NAMES = {'claude': 'Claude Code', 'codex': 'Codex'}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS conversation_route (thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, '
               'document TEXT NOT NULL)')


def mode(db, thread_id):
    row = db.execute('SELECT document FROM conversation_route WHERE thread_id=?', (thread_id,)).fetchone()
    value = json.loads(row[0]).get('mode') if row else None
    return value if value in MODES else 'fixed'


def set_mode(db, thread_id, value, actor):
    with db:
        db.execute('INSERT INTO conversation_route VALUES (?,?) ON CONFLICT(thread_id) DO UPDATE SET document=excluded.document',
                   (thread_id, json.dumps(dict(mode=value, actor=actor, created_at=int(time.time())))))


def label(provider, method=None):
    name = PROVIDER_NAMES.get(provider, provider)
    return f'{name} API key' if method == 'api_key' else name


def alternative(db, binding):
    """Your account and model on the other provider, from the most recent conversation that used it, or None. Only an
    account on this project's side: a Work project never switches to a personal account, or the other way round."""
    # A binding always carries its project's side (Store.binding); one without is from before sides and was personal.
    context = binding.get('context') or binding.get('category') or 'personal'
    if context not in ('personal', 'work'):
        return None
    row = db.execute('''SELECT t.profile_id, t.model, p.provider, p.identity, p.method FROM threads t JOIN profiles p ON p.id=t.profile_id
        WHERE p.provider IN ('claude','codex') AND p.provider<>? AND p.category=? ORDER BY t.rowid DESC LIMIT 1''',
                     (binding['provider'], context)).fetchone()
    if not row:
        return None
    return dict(profile_id=row[0], model=row[1], provider=row[2], identity=row[3], method=row[4] or 'subscription',
                label=label(row[2], row[4]))


def low_note(account):
    """Why an account counts as low, in the summary's own terms, or None when it isn't low."""
    if not account or not account.get('low') or account.get('percent') is None:
        return None
    if account.get('basis') == 'spend_limit':
        return f"at {account['percent']}% of its monthly spend limit"
    return f"at {account['percent']}% of its usage window"


FAILURE_WORDS = {'api_error': 'hit an API error', 'usage_limit': 'reached its usage limit', 'rate_limit': 'was rate limited',
                 'overloaded': 'was overloaded', 'server_error': 'had a server error', 'connection': 'lost its connection'}


def plan(db, binding, *, auto_switch, usage):
    """Decide before a request starts. Returns the mode, the reason for the account that will run, the other provider's
    pair to switch to (only when switching is allowed) and one to suggest (when it isn't)."""
    current_mode = mode(db, binding['id'])
    here = label(binding['provider'], binding.get('method'))
    if current_mode == 'fixed':
        return dict(mode='fixed', why='Chosen for this conversation', switch_to=None, suggest=None)
    accounts = {a['profile_id']: a for a in usage.get('accounts', [])}
    note = low_note(accounts.get(binding['profile_id']))
    other = alternative(db, binding)
    if note and other and not low_note(accounts.get(other['profile_id'])):
        if auto_switch:
            return dict(mode='auto', why=f"Auto: {here} is {note}, so {other['label']}", switch_to=other, suggest=None)
        return dict(mode='auto', why=f'Auto: {here}, which is {note}', switch_to=None,
                    suggest=dict(other, reason=f'{here} is {note}.'))
    if note:
        return dict(mode='auto', why=f'Auto: {here}, which is {note}' + ('' if other else '. No account on the other provider is set up.'),
                    switch_to=None, suggest=None)
    return dict(mode='auto', why=f'Auto: {here}', switch_to=None, suggest=None)


def switch_back(db, binding, pair):
    """Undo ``switch`` when the request it was for couldn't be created: the conversation returns to the account, model and
    provider session it had, only if nothing else moved it in between."""
    with db:
        return db.execute('UPDATE threads SET profile_id=?, model=?, session_id=? WHERE id=? AND project_id=? AND profile_id=? AND model=? AND session_id IS NULL',
                          (binding['profile_id'], binding['model'], binding.get('session_id'), binding['id'], binding['project_id'],
                           pair['profile_id'], pair['model'])).rowcount == 1


def switch(db, binding, pair):
    """Move one conversation to the other provider's pair, only if its account and model are still what the caller read.
    The old provider session belongs to the old account, so it's dropped, exactly as conversation_profile_set does."""
    with db:
        return db.execute('UPDATE threads SET profile_id=?, model=?, session_id=NULL WHERE id=? AND project_id=? AND profile_id=? AND model=?',
                          (pair['profile_id'], pair['model'], binding['id'], binding['project_id'], binding['profile_id'],
                           binding['model'])).rowcount == 1
