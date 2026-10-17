"""The name you give a connected account ("Personal", "Acme").

Settings design sweep, 19 Sep 2026: accounts are shown by the name you give them, set with Rename on Connected accounts,
and the same name appears in the model picker, the composer chip, Connected accounts and the sidebar. The host keeps it
on the profile row (``profiles.name``) and sends it as ``name`` on every account in ``profiles`` and ``usage_summary``.
An account nobody has named has ``name`` null, and every screen falls back to the same default.

A name is a label only. It never selects an account, a home or a credential, and nothing is looked up by it.
"""
import re

from dervo_connections import Rejected

FIELDS = {'profile_rename': {'profileId', 'name'}}
LIMIT = 40
PROVIDERS = {'claude': 'Claude', 'codex': 'Codex'}
# A pasted key or token never becomes a name, because a name is on every screen and in screenshots. Security review,
# 19 Sep: the old rule (sk- or any 32-character run) let Slack, AWS, JWT and uppercase SK- tokens through and refused
# plain names like digital-directions-personal-acct. Now a name is refused when a word in it starts with a published
# key prefix, or when it has 20 or more base64 characters in a row that mix letters and digits.
# Prefixes you'd type in any case: an uppercase SK- is still a key.
PREFIXED = re.compile(r'(?:sk[-_]|rk_|gh[pousr]_|github_pat_|glpat-|xox[a-z][-.]|npm_|hf_|sb_)', re.I)
# Prefixes providers issue in one exact case: an AWS key ID, a Google API key, a JWT.
ISSUED = re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{12,}|AIza[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{8,}')
WORD = re.compile(r'[A-Za-z0-9_.-]+')  # '/', '+' and '=' split words here, so "keys/sk-..." still starts one with sk-
RUN = re.compile(r'[A-Za-z0-9+/=]{20,}')  # base64 keeps '/', '+' and '='; hyphens, dots and spaces end a run


def looks_like_key(name):
    for word in WORD.findall(name):
        prefix = PREFIXED.match(word)
        if prefix:
            rest = word[prefix.end():]
            # "SK Telecom" and "sb_personal" stay names: after a prefix, a key has a digit or at least 20 characters.
            if len(rest) >= 20 or (len(rest) >= 8 and any(c.isdigit() for c in rest)):
                return True
        if ISSUED.match(word):
            return True
    return any(any(c.isdigit() for c in run) and any(c.isalpha() for c in run) for run in RUN.findall(name))


def valid_name(value):
    """The name as it will be saved, or a refusal that says what to change."""
    if type(value) is not str:
        raise Rejected('Give the account a name.')
    name = ' '.join(value.split())
    if not name:
        raise Rejected('Give the account a name.')
    if len(name) > LIMIT:
        raise Rejected(f'Keep the name to {LIMIT} characters.')
    if any(not c.isprintable() for c in name):
        raise Rejected('Use letters, numbers and spaces in the name.')
    if looks_like_key(name):
        raise Rejected('That looks like a key. Use a short name, like Personal.')
    return name


def rename(store, args):
    if type(args) is not dict or set(args) != FIELDS['profile_rename']:
        raise Rejected('Unsupported account fields')
    profile_id = args['profileId']
    if type(profile_id) is not str or not profile_id:
        raise Rejected('Choose an account.')
    row = store.db.execute('SELECT id, provider FROM profiles WHERE id=? AND removed_at IS NULL', (profile_id,)).fetchone()
    if row is None:
        raise Rejected('That account isn’t connected any more.')
    name = valid_name(args['name'])
    taken = store.db.execute('SELECT name FROM profiles WHERE provider=? AND id<>? AND lower(name)=lower(?) AND removed_at IS NULL',
                             (row['provider'], profile_id, name)).fetchone()
    if taken:
        raise Rejected(f'Another {PROVIDERS[row["provider"]]} account is already called {taken["name"]}.')
    with store.db:
        store.db.execute('UPDATE profiles SET name=? WHERE id=?', (name, profile_id))
    return {'id': profile_id, 'name': name}
