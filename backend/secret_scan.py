"""One check for keys, tokens and passwords in text Dervo keeps (beta.11, smartness review: memory item 1).

Two callers, one list of shapes:

* ``find(text)`` answers what an agent's note, skill proposal or lesson would leak, so Dervo can refuse the save.
  It's strict: a shape only counts when the value looks issued or random, so "the token bucket is in limits.py",
  a git SHA or an ``sk-`` CSS class still save.
* ``redact(text)`` replaces every span ``find`` would name, plus a few looser shapes (any ``ghp_`` word, any word after
  ``Bearer``), with ``[redacted]``. It's for text that's kept whatever it says: git's output in task_push, and the tool
  audit of a refused save. Redacting a harmless word there costs nothing.

Refuse rather than redact for notes: a redacted note would still be sent to every later chat, it can keep part of a
secret the pattern missed, and "KEY=[redacted]" teaches the next chat nothing. A refusal tells the agent to save where
the secret lives instead, and nothing of it reaches the store.
"""
import re

REFUSAL = ("That looks like a key, token or password, so it wasn't saved. Dervo never keeps secrets in project memory, "
           "skills or lessons, because they're sent to later chats with Claude and Codex. Say where it lives instead "
           "(for example 'the OpenAI key is in .env.local') and retry. Nothing was saved.")

PLACEHOLDERS = frozenset('password passwd pass secret token key apikey value changeme example redacted none null xxx '
                         'yourpassword your-password your_password yourkey your-key your_key mypassword'.split())


# A value that points at a secret store rather than holding one: a CI secret, an environment variable, a vault path.
REFERENCE = re.compile(r'^["\'`]?(?:\$|%|<|\{|\[|secrets\.|vars\.|env\.|process\.env|os\.environ|os\.getenv|getenv|ENV\[|'
                       r'op://|vault:|keychain:|ssm:|arn:aws:secretsmanager)', re.I)
# Hash formats that are random by design and never a credential: npm/SRI integrity values and digests.
HASH = re.compile(r'^(?:sha(?:1|224|256|384|512)|md5)[-:]', re.I)
# Words that say where a password is, not what it is ("password: see the team vault"), or what state it's in
# ("the password is wrong"), so "the db password is swordfish" counts and those don't (security review 20 Sep, M4).
POINTERS = frozenset('see in stored kept lives the a from at via ask use check same none unset empty prompt keychain '
                     '1password vault env environment set given sent shared elsewhere '
                     'required optional wrong right correct incorrect missing invalid valid unknown blank hidden secure '
                     'insecure weak strong longer shorter different always never still also only just probably likely '
                     'mine yours theirs whatever something anything nothing plaintext cleartext hashed salted encrypted '
                     'encoded either neither there here now not fine okay broken working expected'.split())
# A plain word after "password is" that describes the password rather than being it: stored, expiring, weekly, urgent.
DESCRIBES = re.compile(r'(?:ed|ing|ly|ent|ant|al|ect|id|own|ous|ive|ful|less|able|ible|ness|ment|ion)$', re.I)
# A word that says a hex run is a hash or an id, not a key: "commit 3f2a…", "the sha256 is …", "hash: …".
NOT_A_KEY = re.compile(r'\b(?:commit|commits|sha1?|sha-?(?:224|256|384|512)|hash|hashes|hashed|digest|checksum|rev|revision|'
                       r'blob|tree|etag|md5|id|uuid|guid|fingerprint)\b(?:\s+(?:is|was|of|for))?\W*$', re.I)


def _placeholder(value):
    bare = value.strip('\'"`<>[]{}()$*').casefold()
    return (not bare or bare in PLACEHOLDERS or set(bare) <= set('x*.-_') or bool(REFERENCE.match(value))
            or bool(HASH.match(value)) or bare.startswith('your'))


def _mixed(value, *, digits=1, upper=0, lower=0):
    return (sum(c.isdigit() for c in value) >= digits and sum(c.isupper() for c in value) >= upper
            and sum(c.islower() for c in value) >= lower)


def _prefixed_sk(match):
    tail = match.group(1)
    return len(tail) >= 32 or (len(tail) >= 12 and _mixed(tail) and any(c.isalpha() for c in tail))


def _bearer(match):
    value = match.group(1)
    return value.startswith('eyJ') or (len(value) >= 16 and _mixed(value) and any(c.isalpha() for c in value))


def _url_password(match):
    return not _placeholder(match.group(1))


SECRET_NAME = re.compile(r'(?:^|_)(?:key|token|secret|password|passwd|pwd|pass|credentials?|auth)(?:_|$)', re.I)


def _env(match):
    """NAME=value or name: value where the name says secret at a word boundary (API_KEY, aws_secret_access_key, not
    keyboard) and the value holds one rather than pointing at one."""
    if not SECRET_NAME.search(match.group(1)):
        return False
    value = match.group(2).rstrip('.,;)')
    return not _placeholder(value) and len(value) >= 8 and (len(value) >= 16 or _mixed(value)) and '/' not in value[:1]


def _env_random(match):
    value = match.group(1)
    return _mixed(value, digits=1, upper=1, lower=1) and not _placeholder(value)


def _password(match):
    value = match.group(1).rstrip('.,;:!?)')
    if len(value) < 6 or _placeholder(value) or value.casefold() in POINTERS:
        return False
    if value.startswith(('.', '~', '/')) or '/' in value:
        return False  # a path says where it lives, which is what we ask for
    if any(not c.isalpha() for c in value):
        return True
    # A plain word: "the db password is swordfish" counts; "the password is required" or "stored" describes it.
    return not DESCRIBES.search(value)


def _hex_key(match):
    """A lowercase hex run of 32 to 64 characters within a few words of "key", "token", "secret" or "password": the
    mailgun, twilio and deploy-token shapes. Not when the word right before it says hash or commit, and not without
    both digits and letters (a hash is random too; only the naming tells them apart)."""
    value = match.group(1)
    if not any(c.isdigit() for c in value) or not any(c.isalpha() for c in value):
        return False
    return not NOT_A_KEY.search(match.string[max(0, match.start(1) - 24):match.start(1)])


def _password_set(match):
    """'password: hunter' or 'pw=hunter': after a colon or equals sign even a plain word is the password itself."""
    value = match.group(1).rstrip('.,;:!?)')
    return (len(value) >= 4 and not _placeholder(value) and value.casefold() not in POINTERS
            and not value.startswith(('.', '~', '/')) and '/' not in value)


def _token_word(match):
    value = match.group(1).rstrip('.,;:!?)')
    return len(value) >= 12 and _mixed(value) and any(c.isalpha() for c in value) and not _placeholder(value) and '/' not in value


def _random_run(match):
    return _mixed(match.group(0), digits=2, upper=2, lower=2) and not HASH.match(match.group(0))


def _always(_match):
    return True


_START = r'(?<![A-Za-z0-9_-])'
# (what it is, pattern, which group is the secret, whether the match counts). Group 0 means the whole match.
STRICT = (
    ('private key', re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)'), 0, _always),
    ('API key', re.compile(_START + r'sk-((?:ant-|proj-|svcacct-|or-v1-|admin-)?[A-Za-z0-9_-]{12,})'), 0, _prefixed_sk),
    ('API key', re.compile(_START + r'(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}'), 0, _always),
    ('GitHub token', re.compile(_START + r'gh[pousr]_[A-Za-z0-9]{20,}'), 0, _always),
    ('GitHub token', re.compile(_START + r'github_pat_[A-Za-z0-9_]{20,}'), 0, _always),
    ('Slack token', re.compile(_START + r'(?:xox[abposr]|xapp)-[A-Za-z0-9-]{10,}'), 0, lambda m: _mixed(m.group(0))),
    ('Slack webhook', re.compile(r'hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{8,}'), 0, _always),
    ('AWS secret key', re.compile(r'(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])'), 0,
     lambda m: _mixed(m.group(0), digits=1, upper=2, lower=2) and (_mixed(m.group(0), digits=2) or any(c in '/+' for c in m.group(0)))),
    ('AWS key', re.compile(r'(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])'), 0, _always),
    ('Google key', re.compile(_START + r'AIza[A-Za-z0-9_-]{30,}'), 0, _always),
    ('access token', re.compile(_START + r'(?:glpat-[A-Za-z0-9_-]{20,}|npm_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{30,}|sb_secret_[A-Za-z0-9_-]{10,})'), 0, _always),
    # Security review 20 Sep, M4: DigitalOcean, SendGrid and Discord tokens whole (the run shape caught only a part),
    # an Azure connection string's key, and lowercase hex keys named as keys.
    ('access token', re.compile(_START + r'do[por]_v1_[0-9a-f]{64}'), 0, _always),
    ('API key', re.compile(_START + r'SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}'), 0, _always),
    ('access token', re.compile(_START + r'[MN][A-Za-z0-9]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}'), 0, _always),
    ('secret setting', re.compile(r'(?i)\b(?:AccountKey|SharedAccessKey)\s*=\s*([A-Za-z0-9+/]{40,}={0,2})'), 1, lambda m: not _placeholder(m.group(1))),
    ('API key', re.compile(r'(?i)(?<![A-Za-z0-9])(?:api[_ -]?key|access[_ -]?key|secret[_ -]?key|client[_ -]?secret|auth[_ -]?token|access[_ -]?token|'
                          r'key|token|secret|password|passwd|pwd|credentials?)(?![A-Za-z0-9])(?:\W+\w+){0,3}?\W+(?-i:([0-9a-f]{32,64}))(?![0-9A-Za-z])'), 1, _hex_key),
    ('JWT', re.compile(_START + r'eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}|' + _START + r'eyJ[A-Za-z0-9_-]{20,}'), 0, _always),
    ('bearer token', re.compile(r'(?i:\b(?:bearer|basic))\s+([A-Za-z0-9._~+/-]{8,}=*)'), 1, _bearer),
    ('password in a URL', re.compile(r'[A-Za-z][A-Za-z0-9+.-]*://[^/@:\s]+:([^@/\s]+)@'), 1, _url_password),
    # The value is a lookahead, so "Noted: MAILGUN_KEY=…" doesn't swallow the setting behind the first colon (M4).
    ('secret setting', re.compile(r'\b([A-Za-z][A-Za-z0-9_]*)\s*[=:]\s*["\']?(?=([^\s"\'`]+))'), 2, _env),
    ('secret setting', re.compile(r'\b[A-Z][A-Z0-9_]{2,}\s*=\s*["\']?([A-Za-z0-9+/_.-]{20,}=*)'), 1, _env_random),
    ('password', re.compile(r'(?i)\b(?:password|passwd|passphrase|pwd)\b(?:\s+for\s+\S+)?\s*(?:is|was|=|:)\s*["\'`]?([^\s"\'`]+)'), 1, _password),
    ('password', re.compile(r'(?i)\b(?:password|passwd|passphrase|pwd|pw)\b(?:\s+for\s+\S+)?\s*[=:]\s*["\'`]?([^\s"\'`]+)'), 1, _password_set),
    ('token', re.compile(r'(?i)\b(?:token|secret|api[_ -]?key|access[_ -]?key)\b\s*(?:is|was|=|:)\s*["\'`]?([^\s"\'`]+)'), 1, _token_word),
    ('random key', re.compile(r'(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+_-]{32,}={0,2}(?![A-Za-z0-9+/_=-])'), 0, _random_run),
)
# Only for redact(): words that are worth hiding from a log even when they might be harmless.
LOOSE = (
    ('GitHub token', re.compile(_START + r'(?:gh[pousr]_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{6,})'), 0, _always),
    ('bearer token', re.compile(r'(?i:\b(?:bearer|basic))\s+[A-Za-z0-9._\-+/=]{8,}'), 0, _always),
)


def _spans(text, shapes):
    for kind, pattern, group, counts in shapes:
        for match in pattern.finditer(text):
            if counts(match):
                yield kind, match.start(group), match.end(group)


def find(text):
    """What kind of secret ``text`` holds ('API key', 'password', ...), or None. Strings only; anything else is None."""
    if not isinstance(text, str) or not text:
        return None
    return next((kind for kind, _, _ in _spans(text, STRICT)), None)


def find_any(*texts):
    """The first secret in any of ``texts``, or None."""
    return next((kind for kind in map(find, texts) if kind), None)


def redact(text):
    """``text`` with every secret-shaped span replaced by ``[redacted]``. Non-strings come back unchanged."""
    if not isinstance(text, str) or not text:
        return text
    spans = sorted((start, end) for _, start, end in _spans(text, STRICT + LOOSE) if end > start)
    out, at = [], 0
    for start, end in spans:
        if end <= at:
            continue
        start = max(start, at)
        out.append(text[at:start])
        out.append('[redacted]')
        at = end
    out.append(text[at:])
    return ''.join(out)


def redact_value(value):
    """``redact`` through a JSON-shaped value: dict values and list items, keys untouched."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    return value
