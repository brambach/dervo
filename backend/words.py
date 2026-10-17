"""Local word matching that knows word forms: "releases" and "release", "cutting" and "cut". No model, no files read.

One place for it, so skills, rule order, history search and the near-duplicate check agree on what a word is. ``stem``
is the same fold b11/home-t1 added to project_ideas.py for Home's bug matching (19 Sep), so that code can switch to
this module without changing a single match.
"""

import re

# Words that carry no subject. A superset of project_knowledge._COMMON_TERMS, so nothing that used to count as filler
# starts counting now. Checked before folding, so "note" isn't lost because it folds to "not".
STOP = frozenset('''
a about after again all also am an and any are as at be been before being both but by can could did do does done
doing each either every few first for from get gets got had has have having he her here his how i if in into is it
its it's just last let like may me might more most much must my need needs no nor not now of off on once only onto or
other our out over own please same she should so some still such than that the their them then there these they this
those through to too under until up us very was we were what when where which while who why will with would yes yet
you your ok okay go want wants keep make use
'''.split())

# Work words that say what kind of change, not what it's about. Two of them in common ("make a new build") isn't a
# subject, so skill matching leaves them out. Stored folded.
GENERIC_WORDS = 'new make fix add update change build file app code page work thing things stuff something'.split()

_TOKEN = re.compile(r'[^\W_]+')


def stem(word):
    """A light suffix fold: releases, released and releasing all read as the same word. Words under four letters are
    left alone, and a fold never leaves fewer than three letters."""
    word = str(word or '').casefold().strip("'")
    if word.endswith("'s"):
        word = word[:-2]
    for suffix, keep in (('ies', 'y'), ('sses', 'ss'), ('shes', 'sh'), ('ches', 'ch'), ('xes', 'x'), ('zes', 'z')):
        if word.endswith(suffix) and len(word) - len(suffix) + len(keep) >= 3:
            return word[:-len(suffix)] + keep
    if word.endswith('s') and not word.endswith(('ss', 'us', 'is')) and len(word) > 3:
        word = word[:-1]
    elif word.endswith('ing') and len(word) > 5:
        word = word[:-3]
    elif word.endswith('ed') and len(word) > 4:
        word = word[:-2]
    else:
        return word[:-1] if word.endswith('e') and len(word) > 3 else word
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in 'lsz':
        word = word[:-1]  # running, stopped
    return word[:-1] if word.endswith('e') and len(word) > 3 else word


GENERIC = frozenset(stem(word) for word in GENERIC_WORDS)


# Common dev verbs that mean the same action, for skill matching only (beta.11, eval m13: "publish beta 12" is cutting
# a release). Each row is one action; every word in it is stored folded. Nothing else in Dervo reads this table.
SYNONYM_ROWS = (
    ('release', 'publish ship release cut'),
    ('deploy', 'deploy rollout'),
    ('test', 'test check verify'),
    ('fix', 'fix repair'),
    ('remove', 'remove delete drop'),
)
SYNONYMS = {stem(word): action for action, row in SYNONYM_ROWS for word in row.split()}

# A dev verb only counts when it's used as a verb: at the start of a clause or after one of these. "the release notes"
# is a noun; "we need to publish", "please ship it" and "let's cut" are verbs.
VERB_LEADS = frozenset('''
to please pls let lets let's can could will would should must shall and then now just also so we i you ll re ve m
need needs gotta wanna
'''.split())
_ROLL_OUT = re.compile(r"\broll(?:s|ed|ing)?[ -]out\b")
_CLAUSE = re.compile(r"[.!?;:,\n()]")


def verb_actions(text):
    """The synonym-table actions ``text`` asks for with a verb, as {action: the folded words that asked}. "we need to
    publish beta 12" is {'release': {'publish'}}; "build the release notes page" is empty, because release there
    follows "the"."""
    found = {}
    for clause in _CLAUSE.split(_ROLL_OUT.sub('rollout', str(text or '')[:32768].casefold())):
        tokens = re.findall(r"[a-z0-9']+", clause)
        for index, token in enumerate(tokens):
            action = SYNONYMS.get(stem(token))
            if action and (index == 0 or tokens[index - 1] in VERB_LEADS):
                found.setdefault(action, set()).add(stem(token))
    return found


def word_forms(text, common=frozenset()):
    """The same helper project_ideas.word_forms is on b11/home-t1: words of three letters or more, ``common`` ones
    left out (checked before folding), each folded by ``stem``."""
    return {stem(word) for word in re.findall(r"[a-z0-9']{3,}", str(text or '').casefold()) if word not in common}


def terms(text, stop=STOP):
    """The words of ``text`` that can tie it to something else, folded. Code identifiers split (``WorkspaceSearch`` is
    workspace and search), two-letter words kept (``ui``, ``db``), bare numbers and filler dropped."""
    split = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', str(text or '')[:32768])
    return {stem(word) for word in _TOKEN.findall(split.casefold())
            if len(word) > 1 and not word.isdigit() and word not in stop}


def subject_terms(text):
    """``terms`` without the generic work words: what a request is about."""
    return terms(text) - GENERIC


def content_words(text):
    """Words of three letters or more that aren't filler, unfolded. The near-duplicate check's words: two notes about
    the same subject share "the", "need" and "with", which says nothing about whether they say the same thing."""
    return {w for w in re.findall(r'[a-z0-9]+', str(text or '').casefold()) if len(w) > 2 and w not in STOP}


NUMBER = re.compile(r'^v?[0-9]+(?:\.[0-9]+)*$')  # ASCII digits only: \d would take full-width and Arabic-Indic digits too
_EDGE_PUNCTUATION = '.,;:!?()[]{}"\'“”‘’'


def number_variant(old, new):
    """Where ``new`` is ``old`` with only numbers changed: the (old, new) token pairs that differ, or [] when the two
    differ anywhere else or nowhere. "The web tests need Node 20." against "The web tests need Node 22." gives
    [('20', '22')]. This is a hint, never a judgement (reviewer, 20 Sep): "Agent 2 runs the tests." is a variant of
    "Agent 1 runs the tests." and names a second agent, "Node 18" after "Node 22" is a variant and may be a pin-down,
    and no list of tool names can say which. project_memory saves a variant beside the note it resembles and hands the
    agent that note's id; a note is retired only when the agent names it. Case and the punctuation at a word's edges
    are ignored; every other word must match in place. ``content_words`` can't see any of this: it drops every token
    of two characters or fewer, so 20 and 22 never reach it (critical review, 20 Sep, #1)."""
    a, b = _plain_tokens(old), _plain_tokens(new)
    if not a or len(a) != len(b):
        return []
    changed = []
    for left, right in zip(a, b):
        if left == right:
            continue
        if not (NUMBER.match(left) and NUMBER.match(right)):
            return []
        changed.append((left, right))
    return changed


def _plain_tokens(text):
    tokens = (token.strip(_EDGE_PUNCTUATION) for token in str(text or '')[:32768].casefold().split())
    return [token for token in tokens if token]


def weighted_overlap(query, candidates):
    """For each candidate word set, the sum of 1 / (how many candidates share that word) over the words it shares with
    ``query``. A word every candidate has counts little; a word only one has counts fully."""
    frequency = {term: sum(term in words for words in candidates) for term in query}
    return [sum(1 / frequency[term] for term in query & words) for words in candidates]
