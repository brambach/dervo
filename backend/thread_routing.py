"""Where a message typed into Home goes, what a thread is called, what order threads sit in, and which ones get put away.

Bryce, 8 Oct 2026: Home should route a rapid stream of messages the way Claude Projects' coordinator does, and be "very
very smart at the thread creation, auto sorting, auto naming, and auto deletion", all of it smooth. This module is the
decision logic only. It's pure: records in, a decision out, no I/O, no model, stdlib, thread_names and
conversation_organisation.suggest only, so a send still feels instant: about 2 ms a message with 200 threads once their
words are indexed (the index is cached by the threads' text; building it for 200 long threads takes about 0.1 s). The host (project_orchestrator, Organisation, project_lifecycle)
calls it, applies what it says and logs each decision; a decision below AUTO_ROUTE is a suggestion, and ``unsure`` hands
the message to Home's model with the likeliest threads named.

How it's measured and improved: evals/routing replays realistic message streams through ``route``, ``name``, ``sort``
and ``tidy`` and scores them (python3 -m evals.routing). test_thread_routing fails when a change scores below the
committed baseline, and evals/routing/learn.py turns the host's decision log (a Move, a rename, an unarchive) into new
cases, so every real mistake becomes a test.

Inputs are the state spine's records (thread_states) with a few optional extras; missing keys are fine:
    {id, name, state, since, at, archived, pinned, unread, attention, running, pending_question, goal, summary,
     request, nameLocked, messages: [recent texts you sent there], files: [paths it changed], runs: count}
"""
import functools
import math
import re
import time

import thread_names

AUTO_ROUTE = 0.75  # at or above: applied silently, with a receipt ("Sent to Auth")
SUGGEST = 0.5  # at or above: applied, but the receipt offers Move; below: kind becomes 'unsure' and the model decides
# name() has its own bar: a rename is cheap to undo and never moves work, so a clean name from the first message (0.7)
# applies. From the agent's goal: 0.8. From the current name: 0.6. Made distinct from another thread's: 0.15 less.
NAME_APPLY = 0.65  # at or above: rename without asking; below: keep the name until the agent says what the work is

KINDS = ('existing', 'new', 'home', 'unsure')

# ---------------------------------------------------------------- words

TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.'’/-]*[A-Za-z0-9]|[A-Za-z0-9]")
CAMEL = re.compile(r'(?<=[a-z0-9])(?=[A-Z])')
# Words that say how to work or how you feel, never what the work is about. thread_names.STOP and VERBS hold the rest.
GENERIC = frozenset('''
thread threads chat chats convo conversation one ones dervo home agent agents claude codex task tasks job jobs work
please pls thx asap rn lol breh bro mate dude yeah yep yes nope ok okay hmm hm idk tbh imo btw like just kinda sorta
bit little lot super really actually also maybe still now soon later today tonight tomorrow again instead too either
fix fixed fixing make makes made making add added adding update updated change changed changing want wanted need
needed try trying tried let lets get got thing things stuff look looks looking see seems go going went done do does
did put set use used using check checked checking should could would can will must might may cant wont dont doesnt
isnt didnt its it's thats that's theres there's whats what's hows how's
'''.split()) | thread_names.STOP | thread_names.VERBS
# One spelling for words people use for the same thing.
SYNONYMS = {
    'login': 'auth', 'logins': 'auth', 'signin': 'auth', 'sign-in': 'auth', 'logon': 'auth', 'authentication': 'auth',
    'authenticate': 'auth', 'oauth': 'auth', 'log-in': 'auth', 'sso': 'auth', 'password': 'auth', 'passwords': 'auth',
    'colour': 'color', 'colours': 'color', 'colors': 'color', 'btn': 'button', 'msg': 'message', 'msgs': 'message',
    'img': 'image', 'pic': 'image', 'pics': 'image', 'photo': 'image', 'screenshot': 'image', 'docs': 'doc',
    'documentation': 'doc', 'readme': 'doc', 'repository': 'repo', 'db': 'database',
    'notif': 'notification', 'notifs': 'notification', 'notifications': 'notification',
    'spec': 'test', 'specs': 'test', 'tests': 'test', 'testing': 'test', 'unittest': 'test', 'pytest': 'test',
    'iphone': 'phone', 'ios': 'phone', 'mobile': 'phone', 'android': 'phone', 'app-store': 'phone',
    'payment': 'billing', 'payments': 'billing', 'checkout': 'billing',
    'subscription': 'billing', 'subscriptions': 'billing', 'invoice': 'invoice', 'pricing': 'billing',
    'email': 'email', 'emails': 'email', 'mail': 'email', 'graph': 'chart', 'graphs': 'chart', 'plot': 'chart',
    'charts': 'chart', 'dark-mode': 'dark', 'darkmode': 'dark', 'onboard': 'onboarding', 'signup': 'onboarding',
    'sign-up': 'onboarding', 'crashes': 'crash', 'crashing': 'crash', 'crashed': 'crash', 'segfault': 'crash',
    'deploy': 'release', 'deploys': 'release', 'deployment': 'release', 'ship': 'release', 'shipping': 'release',
    'releases': 'release', 'installer': 'release', 'dmg': 'release', 'perf': 'speed', 'performance': 'speed',
    'slow': 'speed', 'faster': 'speed', 'latency': 'speed', 'lag': 'speed', 'laggy': 'speed', 'webhook': 'webhook',
    'websocket': 'socket', 'ws': 'socket', 'pr': 'pull-request', 'prs': 'pull-request', 'ci': 'ci',
    'speech': 'voice', 'mic': 'voice', 'microphone': 'voice', 'dictation': 'voice', 'transcription': 'voice',
    'nav': 'navigation', 'sidebar': 'sidebar', 'side-bar': 'sidebar', 'modal': 'dialog', 'popup': 'dialog',
    'headline': 'copy', 'headlines': 'copy', 'tagline': 'copy', 'wording': 'copy', 'microcopy': 'copy', 'blurb': 'copy',
    'csv': 'csv', 'xlsx': 'excel', 'spreadsheet': 'excel', 'landing': 'landing', 'homepage': 'landing',
    'website': 'site', 'web-site': 'site', 'analytics': 'analytics', 'metrics': 'analytics', 'dashboard': 'dashboard',
}
# "sign in", "log in": one word before the synonyms see them.
PHRASES = ((re.compile(r"\b(?:sign|log)(?:ging|ged|s)?[\s-]+(?:in|on)\b", re.I), ' login '),
           (re.compile(r"\bsign[\s-]+up\b", re.I), ' signup '), (re.compile(r"\bdark[\s-]+mode\b", re.I), ' darkmode '),
           (re.compile(r"\bpull[\s-]+requests?\b", re.I), ' pr '), (re.compile(r"\bapp\s+store\b", re.I), ' app-store '),
           (re.compile(r"\blanding\s+page\b", re.I), ' landing '), (re.compile(r"\bweb\s+site\b", re.I), ' website '))


# A quoted value ("change the headline to 'Ship faster'") is what to write, not what the work is about.
QUOTED = re.compile(r"(?:(?<=\s)|^)['\"“‘]([^'\"”’\n]{1,80})['\"”’](?=[\s.,!?;:)]|$)")


def stem(word):
    word = word.casefold().replace('’', "'")
    if word.endswith("'s"):
        word = word[:-2]
    word = SYNONYMS.get(word, word)
    if len(word) > 4 and word.endswith('ies'):
        word = word[:-3] + 'y'
    elif len(word) > 4 and word.endswith(('ches', 'shes', 'sses', 'xes')):
        word = word[:-2]
    elif len(word) > 3 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        word = word[:-1]
    elif len(word) > 5 and word.endswith('ing'):
        word = word[:-3]
        if len(word) > 2 and word[-1] == word[-2]:
            word = word[:-1]  # "scrolling" and "scroll", "mapping" and "map"
    elif len(word) > 4 and word.endswith('ed') and not word.endswith('eed'):
        word = word[:-2] if not word.endswith('ied') else word[:-3] + 'y'
        if len(word) > 2 and word[-1] == word[-2]:
            word = word[:-1]
    return SYNONYMS.get(word, word)


def tokens(text):
    """The words in ``text`` that say what it's about, stemmed and folded, in order. File names count whole and in parts
    ("Sidebar.tsx" is sidebar.tsx, sidebar and tsx), and so do camelCase identifiers."""
    if not isinstance(text, str) or not text:
        return []
    return list(_tokens(text[:4000]))


@functools.lru_cache(maxsize=1024)
def _tokens(text):
    text = QUOTED.sub(' ', text)
    for pattern, replacement in PHRASES:
        text = pattern.sub(replacement, text)
    out = []
    for raw in TOKEN.findall(text[:4000]):
        folded = raw.casefold().replace('’', "'")
        if folded in GENERIC or stem(folded) in GENERIC or folded.isdigit():
            continue
        parts = [raw]
        if re.search(r'[./_-]', raw) or CAMEL.search(raw):
            parts += [p for p in re.split(r'[./_-]+', CAMEL.sub(' ', raw).replace(' ', '_')) if p]
        for index, part in enumerate(parts):
            key = part.casefold() if index == 0 and len(parts) > 1 else stem(part)
            if len(key) < 2 or key in GENERIC or key.isdigit():
                continue
            out.append(key)
    return tuple(out)


# ---------------------------------------------------------------- what kind of message it is

def _clean(text):
    return ' '.join(str(text or '').split())


_CHAT_ITEM = (r"(?:hi|hey|hello|yo|sup|gm|good (?:morning|afternoon|evening|night)|morning|night|gn|"
              r"thanks|thank you|thank u|thx|ty|tysm|cheers|ta|nice|cool|great|perfect|awesome|amazing|sweet|"
              r"lovely|love it|love that|beautiful|wow|lol|(?:ha)+|lmao|ok|okay|k+|sure|alright|all good|"
              r"got it|noted|understood|makes sense|fair|fair enough|nice one|good job|great job|well done|"
              r"brb|back|i'?m back|bye|cya|later|ttyl|dervo|h+m+|ugh+|meh|breh|bruh|whatever|nvm|never ?mind|oh|ah|"
              r"so much|a lot|very much|again|for that|for this|appreciate it|ill look later|i'?ll look later|"
              r"later then|will look later|let me look|let me check|one sec|sec)")
# Items always have a separator between them, so a failing match stays linear ("kkkk...x" used to take seconds).
CHIT_CHAT = re.compile(r"^" + _CHAT_ITEM + r"(?:[\s,.!:;)]+" + _CHAT_ITEM + r")*[\s,.!:;)]*"
                       r"(?:(?:dervo|mate|man|dude|breh|bro|team|guys|all)[\s.!:)]*)?"
                       r"(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\uFE0F?\s*)*$", re.I)
PRAISE = re.compile(r"^(?:(?:nice|good|great|amazing|awesome|solid)\s+(?:work|job|stuff)\b.*|(?:(?:the|this|that|it|its|it's|these|those)\s+)?(?:[\w.'’-]+\s+){0,3}(?:looks?|is|are|was|were|seems?|works?|"
                    r"feels?)\s+(?:so\s+|really\s+|super\s+)?(?:great|good|amazing|perfect|awesome|nice|fine|solid|clean|lovely|"
                    r"sick|fire|beautiful|better|much better|way better)(?:\s+(?:now|btw|thanks|tbh|already|imo))*[\s.!]*)$", re.I)
LEADING_ACK = re.compile(r"^(?:(?:thanks|thank you|thx|ty|cheers|nice|nice work|good work|great work|nice job|great job|good job|"
                         r"well done|cool|great|perfect|awesome|ok(?:ay)?|sweet|love it|"
                         r"hi|hey|hello|good (?:morning|afternoon|evening)|morning|"
                         r"(?:i )?hope (?:it'?s|you'?re|you are|all is|things are|everything'?s) (?:going |doing )?(?:well|good|great)"
                         r"(?: (?:today|this morning))?)[\s,.!]+)+(?=\S)", re.I)
THANKS_LEAD = re.compile(r"^(?:thanks|thank you|thx|ty|cheers|nice work|good work|great work|nice job|great job|good job|"
                         r"well done|love it|love that|amazing work|awesome work)\b", re.I)
FILLER = re.compile(r"^(?:(?:actually|ok(?:ay)?|so|wait|no wait|hmm+|hm+|right|um+|uh+|on second thought|nah)[,\s]+)+", re.I)
EMOJI_ONLY = re.compile(r"^(?:[\U0001F300-\U0001FAFF☀-➿]️?\s*)+$")
QUESTION = re.compile(r"^(?:what|whats|what's|how|hows|how's|is|are|did|does|do|has|have|any|anything|where|wheres|"
                      r"where's|who|whos|who's|when|which|can you tell me|tell me|give me|quick)\b", re.I)
STATUS_WORDS = re.compile(r"\b(?:going|status|progress|done|finished|finish|running|run|doing|working on|up to|waiting|"
                          r"wait(?:ing)? on|stuck|blocked|happening|happened|left|eta|landed|merged|ready|state|"
                          r"updates?|news|overview|summary|recap|catch me up|catch up|where are we|where we at|"
                          r"how far|need(?:s)? me|need(?:s)? my|on my plate|still going|still running|alive|"
                          r"ahead|behind|go(?:ing)? on|passed|pass|yet|asking|ask(?:ed)? me|how long|"
                          r"come back|came back|finish(?:ed)? yet)\b|\bwork(?:ed|ing)?\s*\?\s*$", re.I)
STATUS_PHRASES = re.compile(r"^(?:(?:wait |hmm |so )?(?:what was i|where was i|what were we|where were we)\b.*|status|status\?|updates?\??|any news\??|what'?s up\??|sup\??|whats new\??|"
                            r"catch me up|where are we(?: at)?\??|where we at\??|overview|recap|summary)[\s.!?]*$", re.I)
# Words that make a question a request for work, even when it asks about state: "can you check why the tests are stuck".
ASKS = re.compile(r"\b(?:can you|could you|would you|will you|pls|please|let'?s|go ahead|i want|i need|we need|make|fix|"
                  r"add|change|remove|build|write|implement|start|create|rename|move|refactor|redo|update|delete|"
                  r"try|run the|rerun|retry)\b", re.I)

NEW_THREAD = re.compile(r"(?:^|\b(?:(?:start|open|make|create|need|want|use|spin up|kick off|put (?:it|this|that) in|in|into|for|as|"
                        r"gets?|give (?:it|this) (?:its own|a))\s+(?:a|an)?\s*|\b(?:its|their|own)\s+))(?:brand\s+)?(?:new|separate|fresh|"
                        r"another|different|its own|their own|own)\s+(?:thread|chat|convo|conversation|task|job|workstream)s?\b(?!\s+(?:gets?|is|was|appears?|shows?|opens?|comes?)\b)|"
                        r"\bspin\s+(?:up|off)\b|"
                        r"\b(?:start|open|make|create|kick off)\s+(?:a|an)\s+(?:thread|chat|task|job)\b|"
                        r"\bin\s+(?:a|its)\s+(?:new|own|separate)\s+one\b|^new\s*(?:thread|chat|task)?\s*[:\-]", re.I)
NEW_PREFIX = re.compile(r"^(?:(?:ok(?:ay)?|so|also|and|oh|hey|btw|actually)[,\s]+)*(?:(?:can you|could you|please|pls)\s+)?"
                        r"(?:(?:start|open|make|create|spin up|kick off)\s+)?(?:a\s+|an\s+)?"
                        r"(?:new|separate|fresh|another|different)?\s*(?:thread|chat|task|job)?\s*"
                        r"(?:[:\-]|\bfor\b|\bto\b|\bthat\b|\bwhere\b|\babout\b)?\s*", re.I)
SEPARATELY = re.compile(r"^\s*separately[,:\s]+", re.I)
# "start the docs page for connectors": a start verb with its own object opens new work, even mid-burst and even
# with "too" on the end. "start it again", "start over" and "start on the warning" carry on with what's there.
START_WORK = re.compile(r"^(?:(?:ok(?:ay)?|so|now|then|and|also|next|oh)[,\s]+)*(?:(?:can you|could you|would you|please|pls|"
                        r"let'?s|go)\s+)?(?:start|kick off|begin|spin up)\s+(?:(?:a|an|the|some|work on (?:a|an|the))\s+)?"
                        r"(?!(?:it|its|that|this|these|those|them|they|same|over|again|back|up|on|with|from|doing|"
                        r"thread|chat|task|job)\b)[a-z]", re.I)
RESTART = re.compile(r"\b(?:again|over|afresh|from scratch|from the top|back up|re-?run)\b", re.I)
# "start the dev server" runs something for the agent at hand; it isn't a piece of work of its own.
RUNNABLE = re.compile(r"\b(?:servers?|apps?|builds?|tests?|test suite|suite|simulator|emulator|watcher|daemon|container|"
                      r"docker|deploy|deployment|process|bot|worker|ci|pipeline|recording|timer|stream|session|backend|"
                      r"frontend|db|database|debugger|postgres|redis|vite|jest|storybook|repl|shell|terminal)\b", re.I)
POINTS_BACK = re.compile(r"\b(?:it|its|it's|that|those|these|them|they|this|same)\b", re.I)  # "a fresh one" doesn't
# Next steps of the work at hand, not work of their own: "start the fix", "start a PR", "start the remaining ones".
NEXT_STEP = frozenset('''pull-request branch commit review step follow-up follow rest remain next part phase stage
plan draft first second third last test spec patch change migrat writ'''.split())
START_PREFIX = re.compile(r"^(?:(?:ok(?:ay)?|so|now|then|and|also|next|oh)[,\s]+)*(?:(?:can you|could you|would you|please|pls|"
                          r"let'?s|go)\s+)?(?:start|kick off|begin|spin up)\s+(?:(?:a|an|the|some|work on (?:a|an|the))\s+)?", re.I)
TRAILING_TOO = re.compile(r"[\s,]+(?:too|as well|also|next|now|please|pls|thanks)[.!?]*$", re.I)

# A message that leans on the one before it. "another thing" and "different question" open a new topic, so they don't.
CONTINUE = re.compile(r"^(?:and|also|plus|oh and|oh also|oh|actually|wait|hmm|hm|no|nah|nope|not|but|though|"
                      r"instead|then|same|again|too|ah|oops|ugh|ok and|ok also|ok now|now|still|yeah but|yes but|"
                      r"one more|on second thought|scratch that|never ?mind|undo|revert|keep|don'?t|dont|do not|"
                      r"make (?:it|them|that|those|this|these)|it|its|it's|that|that's|thats|those|these|they|"
                      r"them|this|this one|this is|they're|theyre|he|she|ditto|same here|again)\b", re.I)
PRONOUN = re.compile(r"\b(?:it|its|it's|that|those|these|them|they|this|there|same|ones?)\b", re.I)
OBJECT_PRONOUN = re.compile(r"\b(?:it|them|those|these|they|its|it's|theyre|they're)\b", re.I)
TRAILING_TOO = re.compile(r"\b(?:too|as well|also|instead|again)[\s.!?]*$", re.I)
NEW_TOPIC = re.compile(r"^(?:(?:ok(?:ay)?|so|also|and|oh|right)[,\s]+)*(?:separately|another thing|different (?:thing|topic|question)|"
                       r"unrelated|new topic|random(?: thought| q| question)?|side note|separate(?: thing| question)?|"
                       r"switching gears|changing topic|other thing|now (?:the|for|onto|on to)|next(?: up)?\s*[:,-])\b", re.I)

# Answers to a question a thread asked (pending_question).
ANSWER = re.compile(r"^(?:yes|yeah|yep|yup|ya|y|no|nope|nah|n|sure|ok(?:ay)?(?: go| do it| sure)?|go|go ahead|go for it|"
                    r"do it|do that|do both|ship it|sounds good|sgtm|lgtm|approved?|confirm(?:ed)?|"
                    r"(?:go with |use |pick |take |let'?s (?:do|go with) )?(?:option |choice |plan )?(?:[1-9]|[a-d]|one|two|three|"
                    r"the (?:first|second|third|last|former|latter)(?: one)?)(?=[\s.!,]*$|\s*[,.(-]\s)|"
                    r"(?:go with|use|pick|take|choose|prefer|let'?s do|let'?s go with|skip|leave it|keep it|both|neither|either)\b)"
                    r"(?:(?<=[a-z0-9])\b|(?<![a-z0-9]))", re.I)

REFERENCE_PATTERNS = (
    re.compile(r"\b(?:the|that|this|my|our|in|to|for|on)\s+((?:[\w.'’/-]+\s+){0,3}?[\w.'’/-]+)\s+(?:thread|one|chat|convo|"
               r"conversation|task|job|agent|thing|stuff|work|bit|side|part|end)\b", re.I),
    re.compile(r"\b(?:tell|ask|ping|nudge|remind|send (?:this|it|that) to|pass (?:this|it|that) to|forward (?:this|it|that) to)"
               r"\s+(?:the\s+)?((?:[\w.'’/-]+\s+){0,2}?[\w.'’/-]+?)(?=\s+(?:to|that|about|we|it|i|if|when|how|thread|one|chat)\b|\s*[:,]|$)",
               re.I),
    re.compile(r"(?:^|\s)[@#]([\w.-]+(?:\s+[\w.-]+){0,2})"),
    re.compile(r"^((?:[\w.'’/-]+\s+){0,2}[\w.'’/-]+)\s*:\s+\S", re.I),
)
NOT_A_REFERENCE = frozenset('first second third last next other same new another different right wrong previous old current '
                            'whole each every main only latest earlier older newer'.split())

SPLIT = re.compile(r"(?:\s+and\s+(?=(?:fix|add|make|bump|change|update|remove|rename|move|build|write|create|delete|"
                   r"swap|start|redo|refactor|implement)\s+(?!(?:it|them|that|this|those|these|sure)\b))|\n+\s*(?:[-*•]|\d+[.)])?\s*|(?<=[.!?;])\s+|\s+(?:and also|also|plus|and then|oh and|then also)\s+|"
                   r",\s+(?:and\s+)?(?:also|then|plus)\s+|\s*;\s*|\s+\+\s+|\s+&\s+)", re.I)
ASK_OPENER = re.compile(r"^(?:(?:ok(?:ay)?|so|also|and|then|oh|plus|pls|please)[,\s]+)*(?:can you|could you|would you|pls|please|"
                        r"let'?s|i want|i need|we need|we should|make sure|" + '|'.join(thread_names.REQUEST_VERBS) +
                        r"|fix|add|change|update|remove|build|redo|rename|move|refactor|clean|investigate|write|"
                        r"implement|create|delete|swap|bump|upgrade|wire|hook up|set up|setup|look into|figure out)\b", re.I)


# "update says available", "export keeps failing": a verb that's the subject of a report, not an ask.
NOUN_SUBJECT = re.compile(r"^[\w-]+\s+(?:says|is|are|was|were|does|doesn'?t|did|didn'?t|keeps|shows|has|have|won'?t|"
                          r"can'?t|isn'?t|fails|failed|broke|breaks|crashes|hangs|looks|seems)\b", re.I)


# A click, not a job (project_orchestrator.BUTTONS, Bryce's decisions of 19 Sep): push, approve and stop are buttons,
# and archive, delete, rename, pin and restore act on the thread itself. Sent to an agent as text, "delete the sidebar
# thread" could delete code, so these always go to Home with ``command`` set. Only when the object is a pronoun or a
# thread: "stop it crashing on launch" and "delete the old migration" are work.
COMMANDS = {'push': 'push', 'approve': 'approve', 'commit': 'approve', 'merge': 'approve', 'stop': 'stop', 'cancel': 'stop',
            'kill': 'stop', 'pause': 'stop', 'halt': 'stop', 'archive': 'archive', 'close': 'archive', 'resolve': 'archive',
            'hide': 'archive', 'delete': 'delete', 'remove': 'delete', 'trash': 'delete', 'rename': 'rename', 'retitle': 'rename',
            'pin': 'pin', 'unpin': 'unpin', 'unarchive': 'restore', 'restore': 'restore', 'reopen': 'restore'}
COMMAND = re.compile(r"^(?:(?:ok(?:ay)?|so|and|also|now|then|pls|please|can you|could you|go ahead and|just)[,\s]+)*"
                     r"(" + '|'.join(COMMANDS) + r")\s+(?:(?:it|that|this|them|those|these|everything|all(?: of them)?|both)\b"
                     r"(?:\s+(?:thread|one|chat|run|runs|agent|work|job))?|(?:the|that|this|my)\s+(?:[\w.'’-]+\s+){0,3}?"
                     r"(?:thread|threads|one|ones|chat|chats|convo|run|runs|agent|job)\b)"
                     r"(?:\s+(?:to github|to origin|to main|to remote|now|please|pls|for now|for me|too|as well))?[\s.!]*$", re.I)
# Only a rename takes a free object: "rename the billing thread to Usage meter". "push it to the left" is work.
RENAME = re.compile(r"^(?:(?:ok(?:ay)?|so|and|also|now|then|pls|please|can you|could you|just)[,\s]+)*(?:rename|retitle|call)\s+"
                    r"(?:it|this|that|(?:the|that|this)\s+(?:[\w.'’-]+\s+){0,3}?(?:thread|one|chat|convo))\s+(?:to\s+)?['\"“‘]?[^\n]{1,48}$", re.I)
GO_AHEAD = re.compile(r"^(?:(?:ok(?:ay)?|yes|yeah|yep|sure|great|cool|alright|perfect|sounds good)[,!.\s]+)*"
                      r"(?:go|go ahead|go for it|do it|do that|do both|do them|start it|start that|start them|start both|please do|"
                      r"let'?s do (?:it|that|both|them)|let'?s go|ship it|send it)[\s.!]*(?:please|thanks|thank you)?[\s.!]*$", re.I)


def command(text):
    """The thread action a message asks for ('push', 'approve', 'stop', 'archive', 'delete', 'rename', 'pin', 'unpin',
    'restore'), or None when it asks for work."""
    said = _clean(text)
    if RENAME.match(said):
        return 'rename'
    match = COMMAND.match(said)
    return COMMANDS[match.group(1).casefold()] if match else None


def chit_chat(text):
    said = _clean(text)
    if not said:
        return False
    if len(said) > 200:
        return False
    if CHIT_CHAT.fullmatch(said) or EMOJI_ONLY.fullmatch(said):
        return True
    return bool(PRAISE.fullmatch(said)) and not re.search(r"\b(?:but|except|though|not|n't|isn't|doesn't|should|make|fix)\b", said, re.I)


# A question about what an agent did or is doing, not about the code: Home answers it from the thread's record.
AGENT_QUESTION = re.compile(r"\bthe\s+[\w.'’-]+(?:\s+[\w.'’-]+){0,2}\s+(?:one|thread|agent|chat)\s+"
                            r"(?:taking|take|change|changed|do|doing|did|touch|touched|need|needs|want|wants|asking|ask|stuck|"
                            r"waiting|blocked|up to|working on|get|got|finish|finished|land|landed)\b|"
                            r"\b(?:it|they)\s+(?:taking|stuck|waiting|blocked|up to|working on|doing|asking)\b|"
                            r"\bwhat (?:does|did|do) (?:it|they|the [\w\s.'’-]{1,30}?(?:one|thread|agent)) (?:need|want|ask)"
                            r"(?:\s+(?:from|of) (?:me|us)|\s+for)?[\s?!.]*$|"
                            r"\b(?:get|got|been) (?:notari[sz]ed|signed|merged|deployed|released|shipped|approved|reviewed|built)\b", re.I)


REQUESTING = re.compile(r"\b(?:can you|could you|would you|will you|please|pls|let'?s|go ahead|i want|i need|we need|"
                        r"make sure|also|too|instead)\b", re.I)


def status_question(text):
    """A question about how things stand that Home answers from the records: "what's running?", "anything waiting on
    me", "how's the login fix going". Not "can you check why the tests are stuck", which asks for work."""
    said = _clean(text)
    if STATUS_PHRASES.fullmatch(said):
        return True
    asked = said.endswith('?') or bool(QUESTION.match(said))
    if not asked:
        return False
    if len(said.split()) <= 18 and not REQUESTING.search(said) and (AGENT_QUESTION.search(said) or re.match(
            r"^(?:(?:ok(?:ay)?|so|hey|and|btw)[,\s]+)*(?:how'?s|hows|how is|how are|is|are|did|has|have)\b.*\b(?:going|coming along|"
            r"done|finished|land|landed|merged|ready|shipped|passing|green|work(?:ed)?)\b[\s?!.]*$", said, re.I)):
        return True  # "did the login fix land?", "why is the login one taking so long?", "what did the sidebar one change?"
    if not STATUS_WORDS.search(said):
        return False
    if len(said.split()) > 18 or re.search(r"\b(?:also|too|instead|should|make it|can it)\b", said, re.I):
        return False  # a long message, or one that adds to the work, is about the work
    lead = re.sub(r"^(?:(?:ok(?:ay)?|so|hey|and|btw|quick q|quick question)[,:\s]+)*", '', said, flags=re.I)
    if re.match(r"^(?:can|could|would|will)\s+you\b", lead, re.I) and not re.match(
            r"^(?:can|could|would|will)\s+you\s+(?:tell me|give me|show me|catch me up|summari[sz]e|recap|update me)\b", lead, re.I):
        return False
    if re.match(r"^(?:how|what|why)\s+(?:do|does|should|would|can|could)\b", lead, re.I) and not AGENT_QUESTION.search(lead):
        return False  # "how do I...", "what should the button say": a real question about the work, for a thread
    if re.match(r"^why\b", lead, re.I) and not AGENT_QUESTION.search(lead):
        return False
    if re.match(r"^(?:how'?s|hows|how is|how are|is|are|did|has|have)\b", lead, re.I) and re.search(
            r"\b(?:going|coming along|done|finished|land|landed|merged|ready|shipped|passing|green)\b|\bwork(?:ed|ing)?\s*\??\s*$", lead, re.I):
        return True  # "how's the login fix going", "is the export done?", "did the login fix land?"
    if AGENT_QUESTION.search(lead) and not ASKS.search(lead):
        return True  # "why is the login one taking so long?", "what did the sidebar one change?"
    return not ASKS.search(re.sub(r"\b(?:catch me up|update me|tell me|give me|show me)\b", '', lead, flags=re.I))


def continues(text):
    """Whether a message leans on the one before it: "also make it blue", "actually use red", "it's still broken"."""
    said = _clean(text)
    if not said or NEW_TOPIC.match(said):
        return False
    if CONTINUE.match(said) or thread_names.is_follow_up(said) or TRAILING_TOO.search(said):
        return True
    if re.search(r"\balso\b", ' '.join(said.split()[:5]), re.I):
        return True  # "ok let's also return a Retry-After header", "could you also validate..."
    if len(said.split()) <= 14 and OBJECT_PRONOUN.search(said):
        return True  # "send it mondays at 9", "use sf symbols for those"
    content = tokens(said)
    return len(content) <= 2 and bool(PRONOUN.search(said)) and len(said.split()) <= 12


STRONG_LEAN = re.compile(r"^(?:(?:ok(?:ay)?|oh|and|also|plus|but|hmm)[,\s]+)*(?:actually|wait|no\b|nah|nope|instead|undo|revert|"
                         r"scratch that|never ?mind|on second thought|same\b|ditto|again|keep it|don'?t|dont|do not)|"
                         r"^(?:(?:ok(?:ay)?|oh|and|also|plus|but|now|then)[,\s]+)*(?:it|its|it's|they|they're|theyre|those|these|"
                         r"that one|this one|same)\b|"
                         r"^(?:(?:ok(?:ay)?|oh|and|also|plus|but|now|then|pls|please)[,\s]+)*(?:make|set|have|let|move|put|change|turn|"
                         r"keep|get|send|show|give|run|ship|test|try|rename|cap|limit|move)\s+(?:it|them|those|these|that)\b", re.I)


def strong_lean(text):
    """A lean that only makes sense with the message before it: a correction ("actually use red"), or the work named by a
    pronoun ("make it stick"). "also add dark mode" leans weakly: it has its own subject."""
    return bool(STRONG_LEAN.search(_clean(text)))


def explicit_new(text):
    said = _clean(text)
    if not NEW_THREAD.search(said):
        return False
    # "tell the new thread to..." and "in the other thread" point at one that exists.
    return not re.search(r"\b(?:the|that|this)\s+(?:new|other|separate|fresh)\s+(?:thread|chat|one|task)\b", said, re.I)


def new_subject(text):
    """The words of a "new thread for X" message that name the work: X."""
    said = SEPARATELY.sub('', _clean(text))
    stripped = NEW_PREFIX.sub('', said, count=1).strip()
    stripped = re.sub(r"\s*,?\s*(?:in|as)\s+(?:a|its|their)\s+(?:own|new|separate)\s+(?:thread|chat|one|task)\b", '',
                      stripped, flags=re.I)
    stripped = re.sub(r"\s*\b(?:separately|in a new (?:thread|chat))\b[.!]?", '', stripped, flags=re.I)
    return stripped or said


def starts_work(text):
    """True for "start the docs page for connectors (too)": a start verb with an object of its own."""
    said = _clean(text)
    if not START_WORK.match(said) or RESTART.search(said) or POINTS_BACK.search(said):
        return False  # "start a PR for this" is the work at hand
    subject = START_PREFIX.sub('', said, count=1)
    # Two words of its own: "the docs page", "the release notes". "start the fix" or "start postgres" carries on.
    return not RUNNABLE.search(subject) and len([w for w in tokens(subject) if w not in NEXT_STEP]) >= 2


# ---------------------------------------------------------------- matching threads

FIELD_WEIGHTS = (('name', 1.0), ('goal', 0.8), ('request', 0.8), ('files', 0.8), ('pending_question', 0.7),
                 ('summary', 0.6), ('next', 0.5), ('messages', 0.6))
TYPO_MIN = 5  # words this long or longer may be a typo of a thread's word ("onbaording", "notarizaton")


def near(a, b):
    """One edit apart: a letter added, dropped, changed, or two swapped. Same first letter, which typos rarely touch."""
    if a == b or a[:1] != b[:1] or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


@functools.lru_cache(maxsize=2048)
def _strength(fields):
    """{word: weight} for one thread's fields (FIELD_WEIGHTS order). Cached: a thread's text rarely changes between sends."""
    strength = {}
    for (_, weight), value in zip(FIELD_WEIGHTS, fields):
        for word in tokens(value):
            if strength.get(word, 0.0) < weight:
                strength[word] = weight
    return strength


@functools.lru_cache(maxsize=16)
def _corpus(signature):
    """Everything about a thread list that doesn't depend on the message: each thread's word weights, its name's words,
    how many threads use each word, each word's rarity, and the words by first letter for typo matching. Cached by the
    threads' text, so a burst of sends against the same threads builds it once."""
    strength, name_words, df = {}, {}, {}
    for tid, name, fields in signature:
        strength[tid] = _strength(fields)
        name_words[tid] = set(tokens(name))
        for word in strength[tid]:
            df[word] = df.get(word, 0) + 1
    count = len(signature)
    idf = {word: math.log(1.0 + (count + 1.0) / (n + 0.5)) for word, n in df.items()}
    by_letter = {}
    for known in df:
        if len(known) >= TYPO_MIN - 1:
            by_letter.setdefault(known[0], []).append(known)
    return strength, name_words, df, idf, by_letter


TYPO_WORDS = 30  # at most this many words of one message are checked for typos


class Index:
    """Each thread's words, by how strongly each field says what the thread is about, and how rare each word is across
    the project's threads. Built once per call; each text's words are cached across calls (``_tokens``)."""

    def __init__(self, threads):
        self.threads = _records(threads)
        self.by_id = {t['id']: t for t in self.threads}
        signature = tuple((t['id'], t.get('name') if isinstance(t.get('name'), str) else '',
                           tuple(' '.join(t[field][-6:]) if field in ('messages', 'files') else
                                 (t.get(field) if isinstance(t.get(field), str) else '') for field, _ in FIELD_WEIGHTS))
                          for t in self.threads)
        self.strength, self.name_words, self.df, self._idf, self._by_letter = _corpus(signature)
        self.count = len(self.threads)
        self._resolved = {}

    def resolve(self, words):
        """The message's words with a typo of a thread's word read as that word."""
        out, checked = [], 0
        for word in words:
            if word in self.df or len(word) < TYPO_MIN or not word.isalpha() or (word not in self._resolved and checked >= TYPO_WORDS):
                out.append(word)
                continue
            if word not in self._resolved:
                checked += 1
                matches = {known for known in self._by_letter.get(word[0], ()) if near(word, known)}
                self._resolved[word] = matches.pop() if len(matches) == 1 else word
            out.append(self._resolved[word])
        return out

    def idf(self, word):
        # A word no thread uses weighs most: it's what makes a message new work.
        known = self._idf.get(word)
        return known if known is not None else math.log(1.0 + (self.count + 1.0) / 0.5)

    def coverage(self, words, thread_id):
        """How strongly this thread is what the message is about, 0 to 1. Words no thread has (details: "footer",
        "year", "polars") count a little against it; words another thread has count more."""
        if not words:
            return 0.0
        strength = self.strength.get(thread_id, {})
        matched = other = novel = 0.0
        for word in set(words):
            weight = self.idf(word)
            if strength.get(word):
                matched += weight * strength[word]
            elif word in self.df:
                other += weight
            else:
                novel += weight
        if not matched:
            return 0.0
        # Saying most of a thread's name is about that thread however much else the message says.
        name_words = self.name_words.get(thread_id) or set()
        name_weight = sum(self.idf(w) for w in name_words)
        said = name_words & set(words)
        named = sum(self.idf(w) for w in said) / name_weight if name_weight and (len(said) >= 2 or said == name_words) else 0.0
        return max(matched / (matched + 0.8 * other + 0.35 * novel), 0.75 * named)

    def novelty(self, words):
        """The share of the message's meaning no thread holds at all."""
        if not words:
            return 0.0
        unique = set(words)
        total = sum(self.idf(w) for w in unique)
        return sum(self.idf(w) for w in unique if w not in self.df) / total if total else 0.0

    def referenced(self, text):
        """[(thread_id, strength)] for threads the message names: "in the voice thread", "tell onboarding to", "@Export",
        "Stripe webhooks: ...", or a thread's whole name said outright. Best first."""
        found = {}
        said = _clean(text)
        for pattern in REFERENCE_PATTERNS:
            for match in pattern.finditer(said):
                phrase = match.group(1)
                if phrase.split()[0].casefold() in NOT_A_REFERENCE:
                    continue
                words = set(self.resolve(tokens(phrase)))
                if not words:
                    continue
                for tid, name_words in self.name_words.items():
                    shared = words & name_words
                    if not shared:
                        continue
                    name_weight = sum(self.idf(w) for w in name_words)
                    of_name = sum(self.idf(w) for w in shared) / name_weight if name_weight else 0.0
                    score = 0.45 + 0.35 * of_name + 0.2 * len(shared) / len(words)
                    found[tid] = max(found.get(tid, 0.0), score)
        message_words = set(self.resolve(tokens(said)))
        for tid, name_words in self.name_words.items():
            if len(name_words) >= 2 and name_words <= message_words:
                found[tid] = max(found.get(tid, 0.0), 0.75)
        return sorted(found.items(), key=lambda item: -item[1])


def _num(value):
    """A usable time or count: a real, finite number (never a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float('inf'), float('-inf')):
        return None
    return value


def _records(threads):
    """The records that can be acted on, with odd fields made harmless: an id that's a string, no deleted ones, a state
    that's a string, messages and files that are lists of strings, times and counts that are numbers."""
    out, seen = [], set()
    for thread in threads if isinstance(threads, (list, tuple)) else []:
        if not isinstance(thread, dict) or not isinstance(thread.get('id'), str) or not thread['id'] or thread.get('gone'):
            continue
        if thread['id'] in seen:
            continue
        seen.add(thread['id'])
        record = dict(thread)
        record['state'] = thread.get('state') if isinstance(thread.get('state'), str) else None
        for key in ('messages', 'files'):
            value = thread.get(key)
            record[key] = [v for v in value if isinstance(v, str)] if isinstance(value, list) else []
        for key in ('since', 'at', 'touched'):
            record[key] = _num(thread.get(key))
        record['runs'] = thread.get('runs') if type(thread.get('runs')) is int else None
        record['cloud'] = thread.get('cloud') if isinstance(thread.get('cloud'), dict) else None
        out.append(record)
    return out


def _ago(now, at):
    at = _num(at)
    return now - at if at is not None and at > 0 else None


def _last_routed(history, threads):
    """The newest Home message that went to a thread still on the list: (thread_id, at)."""
    for item in reversed(list(history) if isinstance(history, (list, tuple)) else []):
        if isinstance(item, dict) and isinstance(item.get('thread_id'), str) and item['thread_id'] in threads:
            return item['thread_id'], item.get('at')
    return None, None


def _decision(kind, *, thread=None, name=None, confidence=0.0, why='', candidates=(), command=None):
    return dict(kind=kind, command=command, thread_id=thread['id'] if thread else None,
                name=name if name is not None else (thread.get('name') if thread else None),
                confidence=round(max(0.0, min(1.0, confidence)), 3), why=why,
                candidates=[dict(thread_id=tid, score=round(score, 3)) for tid, score in candidates if tid][:3],
                archived=bool(thread and thread.get('archived')))


def safe_name(text):
    """Two to four words for a thread, through conversation_organisation.suggest's privacy rules: it abstains on keys,
    sensitive words, URLs, absolute paths and code. None then, and the thread waits for its agent's title."""
    from conversation_organisation import suggest
    return suggest(text)[0] if isinstance(text, str) else None


def _new(text, index, confidence, why, subject=None):
    name = safe_name(subject or text) or (safe_name(text) if subject else None)
    if name and name.casefold() in PLACEHOLDERS:
        name = None  # "new thread" alone: the host's placeholder stands until the first real message names it
    if name:
        taken = {(t.get('name') or '').casefold() for t in index.threads}
        if name.casefold() in taken:
            name = _distinct(name, subject or text, taken)
    return _decision('new', name=name, confidence=confidence, why=why)


def _distinct(name, text, taken):
    """A name that isn't one another thread already has: the next word of the request that isn't in it, else a number."""
    have = {w.casefold() for w in name.split()}
    for word in TOKEN.findall(text or ''):
        # Only a plain word: never a key, a path or a number that happened to follow ("Rotate token sk-ant-...").
        if not re.fullmatch(r"[A-Za-z][a-z]{2,15}", word) or word.casefold() in thread_names.SECRET_WORDS:
            continue
        if word.casefold() not in have and word.casefold() not in GENERIC:
            candidate = f'{name} {word.casefold()}'
            if candidate.casefold() not in taken and len(candidate) <= thread_names.NAME_LIMIT:
                return candidate
    number = 2
    while f'{name} {number}'.casefold() in taken:
        number += 1
    return f'{name} {number}'


LIMIT = 4000  # characters of a message the rules read; the rest is detail
RAPID = 45  # seconds: messages this close together are one burst


def route(text, history=(), threads=(), now=None):
    """Where one message typed into Home goes.

    ``history`` is Home's recent messages, oldest first: [{text, at, thread_id}], thread_id being where each one went
    (None when Home answered it). ``threads`` are spine records (see the module docstring). Returns
    {kind, thread_id, name, confidence, why, candidates, archived}:

    - existing: send to thread_id. archived=True means it's archived and comes back for this.
    - new: start a thread called ``name``.
    - home: Home answers from the records (greetings, thanks, status). thread_id is set when the question is about one.
    - unsure: Home's model decides; ``candidates`` are the likeliest threads, best first.
    """
    now = time.time() if now is None else now
    said = _clean(text)[:LIMIT]
    index = Index(threads)
    if not said:
        return _decision('home', confidence=1.0, why='Nothing to route')
    last, last_at = _last_routed(history, index.by_id)
    gap = _ago(now, last_at)
    pending = _pending_threads(index)

    action = command(said)
    if action:
        refs = index.referenced(said)
        about = index.by_id[refs[0][0]] if refs and refs[0][1] >= 0.6 else index.by_id.get(last) if PRONOUN.search(said) else None
        return _decision('home', thread=about, confidence=0.9, why=f'A thread action ({action}), not work for its agent',
                         command=action)
    if GO_AHEAD.match(said) and not (len(pending) == 1 and (last is None or last == pending[0]['id'])):
        return _decision('home', confidence=0.85, why="A go-ahead to Home's own offer")
    if chit_chat(said):
        bare = len(said) <= 200 and (CHIT_CHAT.fullmatch(said) or EMOJI_ONLY.fullmatch(said))
        if len(pending) == 1 and (last is None or last == pending[0]['id']) and (
                ANSWER.fullmatch(said.rstrip('.!')) or not bare):
            # "monthly is fine" answers the one open question; swallowing it leaves the thread waiting forever.
            return _decision('existing', thread=pending[0], confidence=0.6, why='An answer to the one open question')
        return _decision('home', confidence=0.95, why='Greeting or thanks')
    acked = LEADING_ACK.sub('', said)
    if THANKS_LEAD.match(said) and not ASK_OPENER.match(re.sub(r"^(?:now|then|and|but)\s+", '', acked, flags=re.I)) \
            and not REQUESTING.search(acked) and not strong_lean(acked) and len(pending) != 1:
        return _decision('home', confidence=0.85, why='Thanks or praise')  # "thanks, that sidebar fix is perfect"
    said = acked or said  # "thanks, now make it blue" is "now make it blue"
    words = index.resolve(tokens(said))
    if status_question(said):
        refs = index.referenced(said)
        about = (index.by_id[refs[0][0]] if refs and refs[0][1] >= 0.55 else _best_named_by_words(index, words)
                 or (index.by_id.get(last) if OBJECT_PRONOUN.search(said) else None))
        return _decision('home', thread=about, name=about.get('name') if about else None, confidence=0.9,
                         why='A question about how things stand')
    several = _several_asks(LEADING_ACK.sub('', str(text).strip()[:LIMIT]), index)
    if several:
        return _decision('unsure', confidence=0.3, why='Several separate asks in one message', candidates=several)
    if explicit_new(said):
        return _new(said, index, 0.95, 'Asked for a new thread', new_subject(said))
    refs = index.referenced(said)
    if refs and refs[0][1] >= 0.6 and (len(refs) == 1 or refs[0][1] - refs[1][1] >= 0.12):
        named = index.by_id[refs[0][0]]
        return _decision('existing', thread=named, confidence=0.75 + 0.25 * min(1.0, refs[0][1]),
                         why=f"Names {named.get('name') or 'the thread'}")
    if starts_work(said):
        subject = TRAILING_TOO.sub('', START_PREFIX.sub('', said, count=1)).strip()
        own = max((index.coverage(index.resolve(tokens(subject)), t['id']) for t in index.threads), default=0.0)
        if own < 0.55:  # "start the connector picker warning" is that thread's work, said another way
            fresh = index.novelty(index.resolve(tokens(subject))) >= 0.5  # "docs page" is new even if "connectors" isn't
            return _new(said, index, 0.85 if own < 0.35 else 0.8 if fresh else 0.7, 'Starts new work', subject)

    # Each thread's score: how much of the message it covers, plus what the stream and its state say.
    leaning = continues(said)
    novelty = index.novelty(words)
    asks = bool(ASK_OPENER.match(said)) and not NOUN_SUBJECT.match(said)
    answering = bool(ANSWER.match(FILLER.sub('', said))) and len(words) <= 8
    scores = {}
    for thread in index.threads:
        tid = thread['id']
        score = index.coverage(words, tid)
        if tid == last and gap is not None and not NEW_TOPIC.match(said):
            score += _stream_bonus(gap, 'strong' if leaning and strong_lean(said) else leaning, novelty, words)
        if thread in pending and answering:
            score += 0.55 if len(pending) == 1 else 0.1 + 0.4 * index.coverage(words, tid)
        if thread.get('state') in ('working', 'needs_you', 'quiet'):
            score += 0.03
        if thread.get('archived'):
            score -= 0.08
        scores[tid] = score
    ranked = sorted(scores.items(), key=lambda item: -item[1])
    best_id, best = ranked[0] if ranked else (None, 0.0)
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best - second
    candidates = [(tid, score) for tid, score in ranked if score >= 0.15][:3]
    fresh_ask = asks and not leaning and len(words) >= 2 and novelty >= 0.5

    if best_id and best >= 0.55 and margin >= 0.18 and not (fresh_ask and best < 0.7 and best_id != last):
        thread = index.by_id[best_id]
        why = ('Continues the message before it' if best_id == last and (leaning or (gap or 1e9) < RAPID) else
               'Answers its open question' if thread in pending and answering else
               f"About {thread.get('name') or 'the same work'}")
        confidence = min(0.97, 0.45 + 0.5 * best + 0.4 * margin)
        own = index.coverage(words, best_id)
        rival = max((index.coverage(words, t['id']) for t in index.threads if t['id'] != best_id), default=0.0)
        if best_id == last and rival >= 0.4:
            confidence = min(confidence, 0.7)  # the words point at another thread, only the stream points here
        if best_id == last and own < 0.4 and not (leaning and strong_lean(said)):
            confidence = min(confidence, 0.7)  # only the stream says so: a fresh ask in a burst may be new work
        if best_id == last and fresh_ask and not strong_lean(said):
            confidence = min(confidence, 0.7)  # "add a search box to the sidebar" right after a sidebar fix
        if NEW_TOPIC.match(said):
            confidence = min(confidence, 0.7)
        if leaning and last is None and own < 0.6:
            confidence = min(confidence, 0.7)  # "it looks off" with nothing before it
        return _decision('existing', thread=thread, confidence=confidence, why=why, candidates=candidates)
    if leaning and (not words or not index.threads):
        # "make it bigger" with nothing before it to lean on: only the model can tell.
        return _decision('unsure', confidence=0.3, why='Leans on something, but nothing before it says what',
                         candidates=candidates)
    burst = last is not None and gap is not None and gap < RAPID
    # A new thread takes an ask with words no thread has. A report ("the badge count doesn't reset") with no thread
    # that matches it is often about one in other words, so the model decides, unless there's nothing to match.
    if words and best < 0.35 and not leaning and not burst and (
            not index.threads or (asks and novelty >= 0.5 and len(words) >= 2)):
        strength = min(1.0, len(words) / 3.0)
        confidence = 0.55 + 0.4 * strength * (1.0 - best / 0.35) * (0.6 + 0.4 * novelty)
        if index.threads and len(words) < 3:
            confidence = min(confidence, 0.7)  # "make the button bigger" may well be about a thread's button
        return _new(said, index, confidence, 'New work no thread covers')
    if best_id and best >= 0.4 and margin >= 0.15:
        thread = index.by_id[best_id]
        return _decision('existing', thread=thread, confidence=max(SUGGEST, 0.45 + 0.3 * margin),
                         why=f"Probably {thread.get('name') or 'that thread'}", candidates=candidates)
    return _decision('unsure', confidence=0.25, why='Could go more than one way', candidates=candidates)


def _stream_bonus(gap, leaning, novelty, words):
    """How much a message leans on the thread the one before it went to. Strong for "also make it blue" five seconds
    later, weak after a long gap, and weaker when the message brings words no thread has (likely a new topic)."""
    if leaning == 'strong':
        bonus = 0.72 if gap < 120 else 0.55 if gap < 900 else 0.35 if gap < 4 * 3600 else 0.2
        return bonus * (1.0 - 0.2 * novelty)
    if leaning:
        bonus = 0.62 if gap < 120 else 0.5 if gap < 900 else 0.35 if gap < 4 * 3600 else 0.2
        return bonus * (1.0 - 0.25 * novelty)
    short = len(words) <= 4
    bonus = (0.45 if short else 0.2) if gap < RAPID else (0.25 if short else 0.15) if gap < 120 else 0.05 if gap < 900 else 0.0
    return bonus * (1.0 - 0.3 * novelty)


def _pending_threads(index):
    return [t for t in index.threads if t.get('pending_question') and t.get('state') == 'needs_you' and not t.get('archived')]


def _best_named_by_words(index, words):
    if not words:
        return None
    best = max(index.threads, key=lambda t: index.coverage(words, t['id']), default=None)
    return best if best and index.coverage(words, best['id']) >= 0.5 else None


def _parts(text):
    """The message cut into its pieces: [(text, strong)]. A strong cut is one that says "another item" (a new list line,
    "and also", "also", "plus", "oh and", ";"); a sentence break is weak, so a second sentence that only describes the
    first ask ("it should include dates") never splits it."""
    # Runs of spaces become one, and the text is cut at LIMIT, so SPLIT's leading \s+ never backtracks across a long run
    # (8000 spaces took 1.8 s).
    text = re.sub(r"[ \t\r\f\v]+", ' ', str(text or '')[:LIMIT])
    out, strong = [], False
    position = 0
    for match in SPLIT.finditer(text):
        piece = text[position:match.start()].strip()
        if piece:
            out.append((piece, strong))
        cut = match.group(0)
        strong = bool(re.search(r"\n|also|plus|oh and|;|\+|&", cut, re.I))
        position = match.end()
    piece = text[position:].strip()
    if piece:
        out.append((piece, strong))
    return out


LIST_HEAD = re.compile(r"^(?:(?:a\s+)?(?:few|couple(?: of)?|some|several|\d+|two|three|four|five)\s+(?:things|items|bugs|fixes|"
                       r"issues|changes|requests|asks|todos)|to ?do|todo|bugs|fixes|list)\b[^:\n]{0,30}:?\s*$", re.I)


def _several_asks(text, index):
    """[(target, score)] when the message holds two or more asks that go to different places ("fix the login crash, and
    also add csv export", a numbered list of bugs), else []. A piece counts when it opens as an ask or follows a strong
    cut; one that only describes the ask before it doesn't."""
    said = str(text or '').strip()
    parts = _parts(said)
    if len(parts) < 2:
        return []
    targets = []
    for number, (part, strong) in enumerate(parts):
        words = index.resolve(tokens(part))
        if not words or LIST_HEAD.match(part) or (number > 0 and not strong and not ASK_OPENER.match(part)) or \
                (number == 0 and not (ASK_OPENER.match(part) or len(words) >= 2)):
            continue
        if number > 0 and continues(part) and not strong:
            continue
        refs = index.referenced(part)
        if refs and refs[0][1] >= 0.6:
            targets.append((refs[0][0], 1.0, set(words)))
            continue
        scored = sorted(((t['id'], index.coverage(words, t['id'])) for t in index.threads), key=lambda item: -item[1])
        if scored and scored[0][1] >= 0.25 and (len(scored) == 1 or scored[0][1] - scored[1][1] >= 0.1):
            targets.append((scored[0][0], scored[0][1], set(words)))
        elif not scored or scored[0][1] < 0.2:
            targets.append(('new:' + str(number), 1.0 - (scored[0][1] if scored else 0.0), set(words)))
    distinct = []
    for target, score, words in targets:
        same = next((d for d in distinct if d[0] == target or (target.startswith('new:') and d[0].startswith('new:') and words & d[2])), None)
        if not same:
            distinct.append((target, score, words))
    if len(distinct) < 2:
        return []
    return [(target, score) for target, score, _ in distinct if not target.startswith('new:')] or [('', 0.0)]


# ---------------------------------------------------------------- naming

PLACEHOLDERS = ('new conversation', 'new thread', 'untitled', 'new chat')


def _vague(name):
    """A name that says nothing about the work ("fix it", "make the button bigger" typed lowercase as a first message):
    replaceable once the agent has said what the work is."""
    first = (name.split() or [''])[0].casefold()
    common = first in thread_names.STOP or first in thread_names.VERBS or first in thread_names.REQUEST_VERBS
    return (name[:1].islower() and common) or len(tokens(name)) == 0


def name(thread, others=()):
    """A better name for a thread, or None to keep the one it has. {title, confidence}; apply it at NAME_APPLY or above.

    A name set by hand (nameLocked) is never replaced. A thread named after its first message (long, a sentence, cut
    with an ellipsis, or "New conversation") gets two to four words from the agent's goal when there is one, else from
    the request. A good name is left alone: names that keep changing are worse than names that are a bit off. ``others``
    are the other threads' names, so two threads never share one."""
    if not isinstance(thread, dict) or thread.get('nameLocked'):
        return None
    current = _clean(thread.get('name'))
    if thread.get('titledBy') or (current and current.casefold() not in PLACEHOLDERS and not thread_names.first_message_name(current)
                                  and not (thread.get('goal') and _vague(current))):
        return None
    goal, request = _clean(thread.get('goal')), _clean(thread.get('request')) or _clean(next(iter(m for m in thread.get('messages') or [] if isinstance(m, str)), None)
                                                                     if isinstance(thread.get('messages'), list) else None)
    title, confidence = None, 0.0
    # The agent's goal is model-written: it gets the same privacy checks as your words, and a title, not a sentence.
    if goal and safe_name(goal):
        title = thread_names.tidy_title(goal) if len(goal.split()) <= thread_names.MAX_WORDS else safe_name(goal)
        confidence = 0.8
    if not title and request:
        title, confidence = safe_name(request), 0.7
    if not title and current and current.casefold() not in PLACEHOLDERS:
        title, confidence = safe_name(current), 0.6
    if not title or title == current:
        return None
    taken = {_clean(n).casefold() for n in others if isinstance(n, str)} - {current.casefold()}
    if title.casefold() in taken:
        title, confidence = _distinct(title, goal or request, taken), confidence - 0.15
    return dict(title=title, confidence=round(confidence, 3))


# ---------------------------------------------------------------- sorting

BUCKETS = ('needs_you', 'working', 'finished', 'idle', 'archived')
BUCKET_OF = {'needs_you': 'needs_you', 'unknown': 'needs_you', 'working': 'working', 'quiet': 'working',
             'finished': 'finished', 'stopped': 'finished', 'idle': 'idle'}


def bucket(thread):
    """Which of Home's buckets a thread sits in: needs_you (a question, a review, or a run Dervo lost sight of), working,
    finished (a result you haven't opened yet), idle, or archived."""
    if thread.get('archived'):
        return 'archived'
    state = thread.get('state')
    if thread.get('pending_question') or thread.get('attention') and state not in ('working', 'quiet'):
        return 'needs_you'
    found = BUCKET_OF.get(state, 'idle')
    if found == 'finished' and not thread.get('unread'):
        return 'idle'
    return found


def sort(threads, now=None):
    """Threads in display order: [{thread_id, bucket, why}]. Buckets go needs_you, working, finished, idle, archived.

    Rows stay still while they work: a thread is placed by when it entered its state (``since``), not by its last
    activity, so a streaming run doesn't jump to the top on every line. Pinned threads lead their bucket. needs_you puts
    the longest wait first, so nothing waits forever under newer asks; every other bucket puts the newest first."""
    now = time.time() if now is None else now
    rows = []
    for thread in _records(threads):
        where = bucket(thread)
        since = thread['since'] if thread['since'] is not None else thread['at'] or 0
        key = since if where == 'needs_you' else -since
        rows.append(((BUCKETS.index(where), 0 if thread.get('pinned') else 1, key, str(thread.get('name') or ''), thread['id']),
                     dict(thread_id=thread['id'], bucket=where, why=_bucket_why(thread, where, now))))
    rows.sort(key=lambda row: row[0])
    return [row for _, row in rows]


def _bucket_why(thread, where, now):
    if where == 'needs_you':
        if thread.get('state') == 'unknown':
            return 'Dervo lost sight of its run'
        return 'It asked you something' if thread.get('pending_question') else 'Its result waits on you'
    if where == 'finished' and thread.get('state') == 'stopped':
        return "Stopped before it finished, you haven't looked yet"
    return {'working': 'Running', 'finished': "Finished, you haven't looked yet", 'idle': 'Nothing running',
            'archived': 'Put away'}[where]


# ---------------------------------------------------------------- cleanup

DAY = 86400
# The project's own setting (project_lifecycle.tidy_days): 7, 14 or 30 days, 'smart', or None for Never. Smart is the
# default here: a settled thread after SETTLED_DAYS, anything else after QUIET_DAYS (project_lifecycle's SMART_*).
SETTLED_DAYS = 3  # a finished thread you've read, with nothing waiting: Friday's work is still there on Monday
QUIET_DAYS = 7
EMPTY_DAYS = 1  # a thread that never ran anything: a to-do made at 6pm is still there in the morning
BATCH = 12  # at most this many put away at once, oldest first, so a tidy never surprises
KEEP_STATES = frozenset({'needs_you', 'working', 'quiet', 'unknown'})


def tidy(threads, now=None, days='smart', keep=()):
    """What to put away or bring back: [{thread_id, action, why, confidence}], action 'archive' or 'unarchive'.

    Never deletes anything: archiving is reversible and the host keeps its own refusals (project_lifecycle.quiet_threads:
    queued work, changes waiting on review, agents). Never archives a pinned thread, one that's running, asks you
    something, has an unread result, stopped part way, or that Dervo lost sight of. Brings an archived thread back when
    work starts in it again or it asks you something (not one archived with its project).

    These are candidates: the host applies them through project_lifecycle (quiet_threads' refusals, last_touched, the
    undo batch). ``touched`` on a record (last_touched: you opening or writing in it) wins over its last activity, so a
    thread you read daily never ages out. ``days`` is the project's setting; None (Never) archives nothing.

    quiet_threads' refusals, when the record carries them: ``keep`` (the threads on screen), ``agent`` (an agent's
    conversation) and ``changesWaiting`` (files not reviewed or not committed, project_lifecycle.changes_waiting) are
    never archived. A record without those fields can't say, so the call site must still run quiet_threads."""
    keep = set(k for k in keep if isinstance(k, str)) if isinstance(keep, (list, tuple, set, frozenset)) else set()
    now = time.time() if now is None else now
    if days is None:
        return [dict(thread_id=t['id'], action='unarchive', why='Work started in it again', confidence=0.95)
                for t in _records(threads) if t.get('archived') and not t.get('projectArchived')
                and (t.get('state') in ('working', 'needs_you', 'quiet') or t.get('pending_question'))]
    if days != 'smart':
        try:
            days = float(days)
        except (TypeError, ValueError):
            days = 'smart'  # a setting this doesn't know reads as the default
    settled_days, quiet_days = (SETTLED_DAYS, QUIET_DAYS) if days == 'smart' else (days, days)
    out, candidates = [], []
    live = _records(threads)
    names = {}
    for thread in live:
        if not thread.get('archived'):
            names.setdefault(_clean(thread.get('name') if isinstance(thread.get('name'), str) else '').casefold(), []).append(thread)
    for thread in live:
        state = thread.get('state')
        if thread.get('archived'):
            if not thread.get('projectArchived') and (state in ('working', 'needs_you', 'quiet') or thread.get('pending_question')):
                out.append(dict(thread_id=thread['id'], action='unarchive', why='Work started in it again', confidence=0.95))
            continue
        if (thread.get('pinned') or state in KEEP_STATES or thread.get('pending_question') or thread.get('unread')
                or thread.get('attention') or thread.get('running') or state == 'stopped'
                or thread['id'] in keep or thread.get('agent') or thread.get('changesWaiting')
                or (thread.get('cloud') or {}).get('state') in ('starting', 'working', 'unknown')):
            continue
        touched = [v for v in (thread.get('touched'), thread.get('at') or thread.get('since')) if isinstance(v, (int, float))]
        quiet = _ago(now, max(touched)) if touched else None
        if quiet is None:
            continue
        ran = thread.get('runs') if isinstance(thread.get('runs'), int) else None
        if ran == 0 and not thread.get('messages') and quiet >= EMPTY_DAYS * DAY:
            twin = [t for t in names.get(_clean(thread.get('name')).casefold(), []) if t is not thread]
            why = 'Never started, and another thread has its name' if twin else 'Never started anything'
            candidates.append((quiet, dict(thread_id=thread['id'], action='archive', why=why, confidence=0.9)))
        elif state == 'finished' and quiet >= settled_days * DAY:
            candidates.append((quiet, dict(thread_id=thread['id'], action='archive',
                                           why=f'Finished and read, quiet for {int(quiet // DAY)} days', confidence=0.85)))
        elif quiet >= quiet_days * DAY:
            candidates.append((quiet, dict(thread_id=thread['id'], action='archive',
                                           why=f'Quiet for {int(quiet // DAY)} days', confidence=0.8)))
    candidates.sort(key=lambda item: -item[0])
    return out + [item for _, item in candidates[:BATCH]]
