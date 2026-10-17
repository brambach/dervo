"""Short names for threads and workstreams, from the request alone. Rules, no model.

Bryce, 18 Sep 2026 (beta.7): threads were named after their first message ("Ideally I want to see this thing working
and see the orchestrator working in real time so I…"). A thread's name is two to four words about the work, in
sentence case: "Sidebar scroll position", "Connector picker", "Fix login". These rules give every thread one at its
first message, good enough never to embarrass. The coding agent improves it for free through the ``title`` of its
Task guide (task_guide), and Home's model names the threads it starts itself.

How a name is made: the request's preamble goes ("I think it would be good to", "can you please"), the sentence that
carries the request is chosen, that sentence is cut at its clause breaks ("as a really critical design person" falls
away), and the words that carry meaning are kept in their original order, stop words dropped. File and module names
always count. A request that is all verbs ("Update and restart") keeps its verbs.

Everything here is pure: text in, text out. The privacy checks (secrets, code, paths, URLs) stay in
conversation_organisation.suggest, which calls ``short_name`` only after they pass.
"""
import re

MIN_WORDS, MAX_WORDS = 2, 4
CONTENT_WORDS = 3  # of the four, at most three carry meaning; the fourth is a connector ("Login and database migration")
NAME_LIMIT = 48  # characters, the width a sidebar row can carry

# Words that never name the work by themselves. Casefold. Contractions are listed whole.
STOP = frozenset('''
a an the this that these those some any each every all both either neither another other such same own
i me my mine we us our ours you your yours he him his she her hers it its they them their theirs who whom whose what
which someone something anything everything nothing anyone everyone one ones
am is are was were be been being have has had having do does did doing done will would shall should can could may might
must ought need needs needed
about above across after against along among around at before behind below beneath beside between beyond by despite
down during except for from in inside into like near of off on onto out outside over past per since through throughout
to toward towards under underneath until up upon via with within without
and or but nor so yet if then than because although though while whereas whether not no also too very really actually
just still already only even ever never always often sometimes now here there where when why how again once more most
much many few little less least quite rather pretty fairly maybe perhaps probably please ok okay yes well right
currently ideally basically honestly obviously clearly simply
thing things stuff bit bits way ways lot lots kind sort part parts
good great nice bad better best easier easy hard simple quick proper
first last next previous other
discussed mentioned earlier yesterday previously left
wrong broken issue issues problem problems error errors bug bugs
hi hello hey thanks thank cheers
it's don't doesn't didn't isn't aren't wasn't weren't can't couldn't won't wouldn't shouldn't haven't hasn't hadn't
i'm i've i'd i'll we're we've we'd we'll you're you've you'd you'll they're they've that's there's here's what's who's
let's it'll
'''.split())
# Verbs that start a request. They open REQUEST_START, and they split "..., and explain what the check did" off.
REQUEST_VERBS = '''fix repair improve add update implement verify recheck retry repeat inspect explain make remove check
test describe document investigate reduce refactor polish write design show build create move rename tighten clean tidy
wire ship plan review start stop finish change migrate delete drop restart install upgrade render support allow offer
give put set turn look read open see run try use find'''.split()
# Dropped while nouns are available, kept when the request is all verbs ("Update and restart"). Words that are as often
# nouns (design, plan, review, test, check, update, build, change, run, work, polish) are left out on purpose.
VERBS = frozenset('''
fix repair improve add implement verify recheck retry repeat inspect explain make remove describe document investigate
reduce refactor write show create tighten clean tidy wire ship rename delete drop migrate allow ensure handle
continue figure deal tell ask say says want wanted need know think feel feels like see look read open go get give put
take keep let try use do does doing done carry pick start stop finish turn sort help find hook offer render working works
takes took gets goes comes came seems seem looks appears appear means shows uses fails breaks crashes hangs happens
happen keeps tries stays stay
'''.split())
MODALS = frozenset('should could would will must can may might to'.split())
# Small words kept between two kept words: "CSV export for invoices", "Login and database migration".
CONNECTORS = frozenset('and or for of in on to with from between into under over across without about at by via per'.split())
DETERMINERS = frozenset('the a an this that these those its your our my their'.split())
MODIFIERS = frozenset('full new old whole entire initial final quick slow big small better proper real'.split())  # never a workstream's first word
# How something is going wrong, never what it is: "Scattered knowledge page" is about the knowledge page.
COMPLAINTS = frozenset('long scattered blank laggy janky messy ugly missing stuck flaky confusing misaligned cluttered noisy '
                       'empty weird odd strange buggy'.split())
# Words ending in -ing that name a thing or an area rather than an action ("Billing", "Onboarding"), so they can head a
# workstream. Any other -ing word of six letters or more is read as an action ("Exporting invoices" is about invoices).
ING_NOUNS = frozenset('billing onboarding pricing routing branding messaging logging testing networking caching hosting '
                      'reporting scheduling mapping packaging signing sharing heading setting settings string spring thing '
                      'nothing something anything everything building ceiling morning evening wording padding spacing '
                      'rendering tracking licensing booking listing posting meeting warning coding'.split())
# A particle that makes one word with the one before it ("catch up", "sign in", "roll back").
PHRASAL = {'catch': ('up',), 'sign': ('in', 'up', 'out', 'off'), 'log': ('in', 'out'), 'roll': ('back',), 'set': ('up',),
           'start': ('up',), 'back': ('up',), 'clean': ('up',), 'follow': ('up',), 'pop': ('up',), 'drop': ('down',),
           'check': ('in', 'out'), 'shut': ('down',), 'hand': ('off',), 'fall': ('back',), 'write': ('up',),
           'walk': ('through',), 'lock': ('out',), 'break': ('down',), 'lay': ('out',)}
# A request that leans on the one before it. "this", "that", "these" and "those" only as pronouns ("this is broken too",
# "that should also warn"): as a determiner they open an ordinary first message ("This button is misaligned in
# settings", 19 Sep auto-sorting review #1), and so do a bare "now", "next", "more" and "still". "it" is always a
# pronoun, so it always leans back ("It stopped mid-edit, pick it up").
FOLLOW_UP = re.compile(r"^(?:and|also|plus|then|same|again|it|put this|add on|add to|on top|one more|another)\b", re.I)
DEMONSTRATIVE = re.compile(r"^(?:this|that|these|those)\s+([\w'’]+)", re.I)
# "that chat", "this thread": a reference to a place in Dervo, never the work.
META = re.compile(r"\b(?:this|that|the|its|my|our|your)\s+(?:chat|thread|conversation|one)\b", re.I)

_PREAMBLE_PART = (r"(?:(?:ok(?:ay)?|so|also|hey|hi|hello|please|now|next|ideally|basically|honestly|actually|right)[,:]?\s+)"
                  r"|(?:i|we)\s+(?:think|guess|feel|believe|reckon)\s+(?:that\s+)?(?:it\s+would\s+be\s+(?:good|great|nice|useful|worth\s+it)\s+to\s+"
                  r"|(?:we|you)\s+(?:should|need\s+to|could|ought\s+to)\s+|it'?s\s+(?:time|worth)\s+(?:to\s+)?)"
                  r"|(?:i|we)\s+(?:want|need|would\s+like|'d\s+like|wanted|would\s+love|'d\s+love|expect)\s+(?:you\s+)?(?:to\s+)?(?:be\s+able\s+to\s+)?"
                  r"|(?:can|could|would|will)\s+you\s+(?:please\s+)?"
                  r"|(?:it\s+would\s+be\s+(?:good|great|nice|useful)\s+to\s+)"
                  r"|(?:let'?s\s+)|(?:help\s+me\s+(?:to\s+)?)|(?:we\s+should\s+)|(?:you\s+should\s+)|(?:go\s+ahead\s+and\s+)"
                  r"|(?:make\s+sure\s+(?:that\s+)?(?:you\s+)?)|(?:try\s+to\s+)|(?:be\s+sure\s+to\s+)")
PREAMBLE = re.compile(r"^(?:" + _PREAMBLE_PART + r")+", re.I)
SUBORDINATE = ('so that', 'so', 'because', 'since', 'as', 'which', 'who', 'where', 'when', 'whenever', 'while', 'after', 'before',
               'until', 'unless', 'if', 'once', 'then', 'but', 'instead of', 'rather than', 'in order to', 'though', 'although',
               'whereas', 'how', 'why', 'whether', 'what')
# "..., and explain what the check did" is a second request; "Update and restart" is one.
CLAUSE = re.compile(r"\s+(?:" + '|'.join(re.escape(w) for w in SUBORDINATE) + r")\s+"
                    r"|\s+and\s+(?=(?:then\s+)?(?:" + '|'.join(REQUEST_VERBS) + r")\s+(?:the|a|an|this|that|these|those|its|your|our|my|what|why|how|me|us|it)\b)"
                    r"|\s*[;:(,]\s*|\s+-\s+|\s*[\u2014\u2013]\s*", re.I)
COORDINATED = re.compile(r"^(?:" + '|'.join(REQUEST_VERBS) + r")\s+and\s+\w", re.I)
LEADS_SUBORDINATE = re.compile(r"^(?:" + '|'.join(re.escape(w) for w in SUBORDINATE) + r")\b", re.I)
REQUEST_START = re.compile(r"^(?:" + '|'.join(REQUEST_VERBS) + r")\b", re.I)
READ_FIRST = re.compile(r"^(?:read|look\s+at|open|see|check|review|skim|start\s+(?:by|with)\s+reading)\b.*\bfirst\W*$", re.I)
STATUS_PREAMBLE = re.compile(r'\b(?:is|are|was|were|has been|have been)\s+(?:now\s+)?(?:available|ready|repaired|fixed|connected|installed|enabled|done|working)\b', re.I)
TOKEN = re.compile(r"[^\W_][\w.'’/-]*[^\W_]|[^\W_]")
# A key or a token is never a name or a workstream, whatever path the text arrives by: a known prefix (sk-ant-, sk-proj-,
# ghp_, xoxb-, AKIA, AIza, eyJ), or 16 or more characters mixing letters and digits. release_profiles.verify_bundle
# scans files for the same shapes; a shorter secret can't be told from a word, so suggest() abstains on the words
# around one (SENSITIVE) and _words drops the value that follows them ("password Tr0ub4dor").
SECRET_PREFIXES = ('sk-', 'ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_', 'xox', 'AKIA', 'AIza', 'eyJ')
SECRET = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{16,}$')
SECRET_WORDS = frozenset('password passwords passwd pw key keys apikey token tokens secret secrets credential credentials'.split())
FILE_NAME = re.compile(r'/|\.[A-Za-z0-9]{1,5}$')
# A word straight after key, token, password, secret or pw, bare or after is, to, as, : or = ("token swordfish", "set
# the key to mango", "pw: tiger") may be
# the value itself, so it's never part of a name unless it's one of the ordinary things people say about keys
# ("token usage", "key handling"). Everything secret_scan flags is left out too. Both become SCRUBBED, which
# looks_secret matches, so _pick never takes it.
VALUE_WORDS = r'(?:api[ _-]?)?(?:keys?|tokens?|apikey|passwords?|passwd|secrets?|pw)'
AFTER_VALUE_WORD = re.compile(r'(?i)\b(' + VALUE_WORDS + r')\b(\s*(?:(?:is|was|are|were|to|as)\b\s*)?[:=]?\s*)([^\W\d_][\w\'’-]*)')
KEY_TALK = frozenset('''
usage use used handling binding bindings count counts limit limits rotation rotate refresh expiry expiration expires storage
store stored field fields input name names format bucket budget budgets meter meters cost costs estimate estimates press
presses shortcut shortcuts map mapping events event validation check checks management manager picker flow page screen
setting settings button error errors file files leak leaks scope scopes type types reset resets length strength policy
prompt prompts entry sheet dialog modal field label warning warnings rule rules cache caching list lists handler
handlers helper helpers support login auth request requests header headers value values scan scanner detection
'''.split())
SCRUBBED = 'Scrubbed0000000000'


def scrub(text):
    """``text`` with every span secret_scan flags, and any possible value after key/token/password/secret/pw, replaced
    by SCRUBBED. A name is only ever made from what's left."""
    import secret_scan
    text = secret_scan.redact(text).replace('[redacted]', f' {SCRUBBED} ')

    def value(match):
        word = match.group(3)
        key = word.replace('’', "'").casefold()
        if key in KEY_TALK or key in STOP or key in VERBS or key in MODALS or key in CONNECTORS or key == SCRUBBED.casefold():
            return match.group(0)
        return f'{match.group(1)}{match.group(2)}{SCRUBBED}'
    return AFTER_VALUE_WORD.sub(value, text)


def looks_secret(token):
    """Whether a token is shaped like a key or a token: a known prefix, or 16 or more characters mixing letters and digits."""
    return isinstance(token, str) and (token.startswith(SECRET_PREFIXES) or bool(SECRET.match(token)))


def _identifier(word):
    """A file, module or code name: keeps its case and always counts."""
    return bool(re.search(r"[._/]|\d|[a-z][A-Z]", word)) and not word.isdigit()


def _words(clause):
    """[(token, casefold key, kind)] with kind 'id', 'stop', 'verb', 'word' or 'secret'. A word right after a modal
    ("should warn", "to find") is a verb whatever it is. A key-shaped token is a secret, and so is an identifier within
    two tokens of a word like password or key ("the key sk-...", "password is Tr0ub4dor"): _pick never takes one."""
    out, previous, guarded = [], None, 0
    for token in TOKEN.findall(clause):
        token = token.strip("'’.")
        if not token or (len(token) < 2 and not token.isdigit()):
            continue  # a stray letter ("30 s") never names anything
        key = token.replace('’', "'").casefold()
        if looks_secret(token) or (guarded and _identifier(token)):
            kind = 'secret'
        elif _identifier(token):
            kind = 'id'
        elif key in STOP or key.isdigit():
            kind = 'stop'
        elif key in VERBS or previous in MODALS:
            kind = 'verb'
        else:
            kind = 'word'
        out.append((token, key, kind))
        previous = key
        guarded = 2 if key in SECRET_WORDS else max(guarded - 1, 0)
    # A request verb that opens the clause is an imperative ("Update catch_up"), and so is one joined to it with "and".
    if out and out[0][1] in REQUEST_VERBS and out[0][2] == 'word':
        out[0] = (out[0][0], out[0][1], 'verb')
        if len(out) > 2 and out[1][1] == 'and' and out[2][2] == 'word':
            out[2] = (out[2][0], out[2][1], 'verb')
    # A particle that makes one thing with the word before it stays with it: "the catch up screen" was "Catch screen"
    # because "up" is a stop word (19 Sep auto-sorting review #3).
    joined = []
    for token, key, kind in out:
        if joined and kind == 'stop' and key in PHRASAL.get(joined[-1][1], ()) and joined[-1][2] in ('word', 'verb'):
            before = joined.pop()
            joined.append((f'{before[0]} {token}', f'{before[1]} {key}', before[2]))
        else:
            joined.append((token, key, kind))
    return joined


def _sentences(prose):
    prose = ' '.join(prose.split())
    return [s for s in re.split(r'(?<=[.!?])\s+', prose) if s]


def _request_sentence(sentences):
    """The sentence that carries the request: not a setup line ("Read PLAN.md first"), not a status line ("The
    connection is now ready"), and a sentence that starts with a request verb when the first doesn't."""
    kept = [s for s in sentences if not READ_FIRST.match(s.rstrip('.?!'))] or sentences
    starts_request = lambda s: bool(REQUEST_START.match(PREAMBLE.sub('', s)))
    # "The connection is now ready." reports on the setup; only a request that follows it can name the thread.
    kept = [s for s in kept if starts_request(s) or not STATUS_PREAMBLE.search(s)]
    if not kept:
        return None, []
    first = kept[0]
    if not starts_request(first):
        for later in kept[1:3]:
            if starts_request(later):
                return later, kept
    return first, kept


def _clauses(sentence):
    """The clauses of a request sentence, the ones that lead with a subordinator ("After a beta downloads, ...") last."""
    body = META.sub(' ', PREAMBLE.sub('', sentence)).strip()
    parts = [c.strip() for c in CLAUSE.split(body) if c and c.strip()]
    if len(parts) > 1 and COORDINATED.match(body):
        parts = [parts[0] + ' and ' + parts[1]] + parts[2:]  # "Update and restart the app" is one request
    return body, [c for c in parts if not LEADS_SUBORDINATE.match(c)] + [c for c in parts if LEADS_SUBORDINATE.match(c)]


def _pick(words, allow_verbs=False, need_noun=False):
    """Up to MAX_WORDS tokens from one clause, in order: the words that carry meaning, a connector kept only between two
    of them. None when fewer than MIN_WORDS words carry meaning (the caller then widens its view). ``need_noun`` refuses
    a pick made of verbs alone ("see working"), for the pass that wants a request verb with its object ("Fix login")."""
    kinds = {'id', 'word'} | ({'verb'} if allow_verbs else set())
    chosen, seen, content = [], set(), 0
    for index, (token, key, kind) in enumerate(words):
        if kind not in kinds:
            continue
        stem = key.rstrip('s') if len(key) > 3 else key
        if stem in seen:
            continue
        addition = [(index, token)]
        if chosen:
            gap = words[chosen[-1][0] + 1:index]
            if gap and gap[0][1] in CONNECTORS and all(g[1] in DETERMINERS for g in gap[1:]):
                addition = [(chosen[-1][0] + 1, gap[0][0]), (index, token)]
        if len(chosen) + len(addition) > MAX_WORDS or content + 1 > CONTENT_WORDS:
            # A phrase cut after "and" ("composer spacing and reduced") reads worse than one without it.
            if len(chosen) >= 3 and chosen[-2][1].casefold() in ('and', 'or') and words[chosen[-1][0] + 1:index] == []:
                chosen = chosen[:-2]
            break
        chosen += addition
        content += 1
        seen.add(stem)
    if content < MIN_WORDS or (need_noun and not any(words[i][2] in ('id', 'word') for i, _ in chosen)):
        return None
    return chosen


def _sentence_case(tokens):
    """Sentence case: the first plain word capitalised, the rest as written, except a word that was only capitalised
    because it began the request."""
    out = []
    for position, (index, token) in enumerate(tokens):
        if _identifier(token) or token.isupper():
            out.append(token)
        elif position == 0:
            out.append(token[0].upper() + token[1:])
        elif index == 0 and token[:1].isupper() and token[1:].islower():
            out.append(token.casefold())
        else:
            out.append(token)
    return ' '.join(out)


def short_name(text):
    """Two to four words about the work, or None when the text has no words that could name it."""
    if not isinstance(text, str) or not text.strip():
        return None
    sentences = _sentences(scrub(text))
    if not sentences:
        return None
    request, kept = _request_sentence(sentences)
    if request is None:
        return None
    ordered = [request] + [s for s in kept if s is not request]
    parsed = [_clauses(s) for s in ordered]
    attempts = []
    # Nouns from the request sentence's first clause, then its request verb with its one object ("Fix login"), then
    # the nouns of its other clauses and of the other sentences. Verbs everywhere come last ("Update and restart").
    body, clauses = parsed[0]
    attempts += [(c, False) for c in clauses[:1]]
    if clauses and REQUEST_START.match(body):
        attempts.append((clauses[0], True, True))
    attempts += [(c, False) for c in clauses[1:]] + [(body, False)]
    for other_body, other_clauses in parsed[1:]:
        attempts += [(c, False) for c in other_clauses] + [(other_body, False)]
    # Verbs alone name the work only when they are the request ("Update and restart"), never a remark ("take a look").
    for other_body, other_clauses in parsed:
        if REQUEST_START.match(other_body):
            attempts += [(c, True) for c in other_clauses] + [(other_body, True)]
    for clause, allow_verbs, *need_noun in attempts:
        words = _words(clause)
        chosen = _pick(words, allow_verbs, bool(need_noun))
        if chosen:
            return _finish(_sentence_case(chosen))
    # One word is better than the person's sentence.
    for body, _ in parsed:
        single = next((token for token, _, kind in _words(body) if kind in ('id', 'word')), None)
        if single:
            return _finish(_sentence_case([(0, single)]))
    return None


def _finish(name):
    words = name.split()
    while len(' '.join(words)) > NAME_LIMIT and len(words) > 1:
        words.pop()
    name = ' '.join(words)
    return name[:NAME_LIMIT] if name else None


ESCAPES = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]')  # terminal escape sequences, whole
BLANKS = frozenset('⠀ㅤᅟᅠﾠ')  # printable characters that draw as nothing (braille blank, Hangul fillers)
MARKDOWN = ((re.compile(r'!?\[([^\]]*)\]\([^)]*\)'), r'\1'),  # a link or an image is its text
            (re.compile(r'<[^<>]*>'), ' '),  # a tag
            (re.compile(r'(\*{1,3}|_{2,3}|`+)(?=\S)(.+?)(?<=\S)\1'), r'\2'),  # emphasis and code marks
            (re.compile(r'^\s*(?:#{1,6}|>|[-*+]|\d+[.)])\s+'), ''))  # a heading, quote or list mark
ABSOLUTE_PATH = re.compile(r'(?<!\S)(?:~|\.{1,2})?/(?:[^\s/]+/)*([^\s/]+)')


def tidy_title(title):
    """A model's title as a thread name: cleaned the way a typed name is checked, and shortened by the same rules when
    it runs past four words. Cleaned means: escape sequences, control, bidi, zero-width and blank characters and
    Markdown go, an absolute path is its file name, and a key goes whatever else the title says."""
    from voice import plain
    if not isinstance(title, str):
        return None
    title = ESCAPES.sub(' ', title)
    title = ''.join(' ' if c in BLANKS or not c.isprintable() else c for c in title)
    for pattern, replacement in MARKDOWN:
        title = pattern.sub(replacement, title)
    title = ABSOLUTE_PATH.sub(r'\1', title)
    title = ' '.join(scrub(plain(title)).split()).strip(' .,:;')
    secrets = {token for token, _, kind in _words(title) if kind == 'secret'}
    if secrets:  # a key in a model's title goes, whatever else the title says
        title = ' '.join(word for word in title.split() if word.strip("'’.,:;()") not in secrets).strip(' .,:;')
        words = title.split()
        while words and words[-1].casefold() in CONNECTORS | DETERMINERS | {'is', 'was', 'are', 'were', 'as'}:
            words.pop()  # "Set the key to" once the value has gone
        title = ' '.join(words)
        if title and title[0].islower() and not _identifier(title.split()[0]):
            title = title[0].upper() + title[1:]
    if not title or title.casefold() == 'new conversation':
        return None
    words = title.split()
    if len(words) > MAX_WORDS:
        return short_name(title) or _finish(' '.join(words[:MAX_WORDS]))
    return _finish(title)


def first_message_name(name):
    """Whether a thread's name still reads as its first message: long, a sentence, or cut with an ellipsis."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    return (name.endswith(('…', '...', '.', '?', '!')) or len(name.split()) > MAX_WORDS or len(name) > NAME_LIMIT)


PARTICLES = frozenset('up out off down back away through along'.split())  # "Catch up", "Sign off": the verb needs it


def workstream_name(thread_name):
    """A one- to three-word workstream name from a thread's short name: its first word when that word is long enough to
    stand alone ("Knowledge", "Connector"), else its first two ("Live board"). A name that still reads as a first
    message (long, a sentence, or cut with an ellipsis) names no workstream: its opening words aren't about the work.

    A first word that can't stand alone keeps its next word whatever its length (19 Sep, map F6: "Catch up screen"
    made "Catch" and "Sweep of settings" made "Sweep"): a verb ("Fix crash"), a word joined to a particle ("Catch up"),
    or a word the thread name joins to the next with a connector ("Sweep of settings", "CSV export for invoices")."""
    if first_message_name(thread_name):
        return None
    given = (thread_name or '').split()
    words = [w for w in given if w.casefold() not in CONNECTORS | DETERMINERS]
    # A workstream is named with a noun (19 Sep auto-sorting review #3): a modifier, a complaint ("slow"), a verb
    # ("Fix crash" is Crash) or an action word in -ing ("Exporting invoices" is Invoices) never heads it while
    # another word can. A verb's particle goes with it ("Clean up sidebar" is Sidebar).
    while len(words) > 1 and (words[0].casefold() in MODIFIERS | COMPLAINTS or _action(words[0])):
        drop = 2 if _action(words[0]) and words[1].casefold() in PARTICLES and len(words) > 2 else 1
        words = words[drop:]
    if not words:
        return None
    head = words[0]
    if _identifier(head):
        # A file or module name stands alone ("WorkspaceSearch" for WorkspaceSearch.tsx). Any other identifier (a
        # version, a key that slipped past, catch_up) names no shared heading.
        return re.sub(r'\.[A-Za-z0-9]+$', '', head.rsplit('/', 1)[-1]) if FILE_NAME.search(head) and not looks_secret(head) else None
    key = head.casefold()
    at = given.index(head)
    # "and" and "or" join two things ("Login and database migration" is about login); a preposition binds the head to
    # its object. VERBS leaves out words as often nouns (design, plan, review), which stand alone as they always did.
    joined = at + 1 < len(given) and given[at + 1].casefold() in CONNECTORS - {'and', 'or'}
    fragment = key in VERBS or (len(words) > 1 and words[1].casefold() in PARTICLES)
    if joined and len(given) > at + 2:
        chosen = given[at:at + 3]  # the connector and its object: "Sweep of settings"
    elif len(words) > 1 and not fragment and _action(words[1]):
        chosen = [head]  # "Home filing threads" is about Home, however short the word
    elif len(head) >= 5 and not fragment or len(words) == 1:
        chosen = [head]
    else:
        chosen = words[:2]
    name = ' '.join(chosen)
    return name[0].upper() + name[1:]


def _action(word):
    """Whether a word in a thread's short name is an action rather than a thing: a verb ("Fix", "keeps") or an -ing
    word that isn't a noun ("exporting", "filing")."""
    key = word.casefold()
    if _identifier(word):
        return False
    if key in VERBS or (key.endswith('s') and key[:-1] in VERBS and key not in ING_NOUNS):
        return True
    return len(key) >= 6 and key.endswith('ing') and key not in ING_NOUNS


def _key_words(name):
    words = re.findall(r'[^\W_]+', (name or '').casefold())
    words = [w for w in words if w not in CONNECTORS and w not in ('work', 'stuff', 'things', 'tasks', 'items', 'misc', 'the', 'a')]
    return {w[:-1] if len(w) > 3 and w.endswith('s') else w for w in words}


def similar(a, b):
    """Near-duplicate workstream names: "Orchestrator" and "Orchestrator work", "Connector" and "Connectors", or one
    name's words inside the other's ("Sidebar" and "Sidebar polish")."""
    first, second = _key_words(a), _key_words(b)
    if not first or not second:
        return False
    return first <= second or second <= first


def is_follow_up(text):
    """A request that leans on the one before it: "also make the row frosted", "put this in that chat", "this is
    broken too". Not "This button is misaligned": there "this" picks out a new thing."""
    if not isinstance(text, str):
        return False
    text = text.strip()
    if FOLLOW_UP.match(text):
        return True
    pronoun = DEMONSTRATIVE.match(text)
    if not pronoun:
        return False
    following = pronoun.group(1).replace('’', "'").casefold()
    return following in STOP or following in VERBS or following.endswith("n't") or following.endswith('s') and following[:-1] in VERBS
