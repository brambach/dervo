"""What a finished utterance means before it goes anywhere: a cancel, a yes or no, or an outward action that needs a
spoken yes first.

Stdlib only and word based on purpose. These checks run on every utterance in under a millisecond, and a wrong guess
is cheap in the safe direction: an ordinary request read as outward only costs one extra "yes". The other direction
(an outward request read as ordinary) is the one the word lists are tuned against, see test_voice_loop.py.
"""
import re

_WORDS = re.compile(r"[a-z0-9']+")

# Said on their own (or nearly), these take back what was just heard. "Cancel the export job" is a request, not this.
CANCEL = {'cancel', 'never mind', 'nevermind', 'scratch that', 'forget it', 'forget that', 'undo', 'undo that',
          'no wait', 'wait no', 'stop', 'not that', 'ignore that', 'cancel that'}
YES = {'yes', 'yeah', 'yep', 'yup', 'sure', 'ok', 'okay', 'go', 'go ahead', 'do it', 'confirm', 'confirmed', 'please do',
       'yes please', 'correct', 'right', 'affirmative', 'ship it', 'go for it'}
NO = {'no', 'nope', 'nah', "don't", 'do not', 'no thanks', 'not now', 'hold on', 'wait', 'negative', 'cancel', 'stop',
      'never mind', 'nevermind'}

# Outward or hard to undo: it leaves the Mac, changes shared history, or removes something. Each pattern is matched on
# whole words of the lowercased transcript. Reads like "what did the push do" also match; that's the safe direction.
OUTWARD = (
    (re.compile(r"\b(push|pushes|pushed|pushing|force[- ]push)\b"), 'pushes code'),
    (re.compile(r"\b(merge|merges|merged|merging)\b"), 'merges a branch'),
    # Every form counts, "deploy it" included. "Ship it" is how people say yes, so only a bare "ship it" is exempt.
    (re.compile(r"\b(deploy|deploys|deployed|deploying|release|releases|released|publish|publishes|published)\b"
                r"|\bship\b(?! it$)"), 'publishes or deploys'),
    (re.compile(r"\b(send|sends|post|reply|tweet|dm)\b.*\b(to|on|in)\b|\b(email|emails|message|messages|text|slack|ping)\s+\w+"),
     'sends a message outside Dervo'),
    (re.compile(r"\b(delete|deletes|deleting|remove|wipe|drop|erase|destroy|discard|discards|trash|rm|purge|"
                r"overwrite|overwrites|unpublish|archive|archives)\b"), 'deletes or overwrites something'),
    (re.compile(r"\b(reset|resets|rebase|revert|reverts|cherry[- ]pick|squash|amend)\b"), 'rewrites or reverts history'),
    (re.compile(r"\b(public|private|visibility)\b|\b(approve|approves|reject|rejects)\b"), 'changes who can see or approves something'),
    (re.compile(r"\b(pay|purchase|buy|charge|subscribe)\b"), 'spends money'),
    (re.compile(r"\bopen (a |the )?(pr|pull request)\b|\bcreate (a |the )?(pr|pull request)\b"), 'opens a pull request'),
    (re.compile(r"\bclose (a |the |that |this )?(pr|pull request|issue)\b"), 'closes a pull request or issue'),
    (re.compile(r"\b(upload|uploads|uploaded|uploading)\b"), 'uploads something'),
    (re.compile(r"\bforce\b.*\b(onto|to|into|over)\b"), 'rewrites or reverts history'),
)


def words(text):
    return ' '.join(_WORDS.findall((text or '').lower().replace('’', "'")))


FILLERS = {'uh', 'um', 'er', 'oh', 'hmm', 'well', 'please', 'thanks', 'thank', 'you', 'dervo', 'hey', 'actually',
           'just', 'so', 'ah'}


def _phrase_in(text, phrases):
    """True when ``text`` is one of ``phrases`` once filler is dropped, with at most one extra word before or after it
    ("uh yes", "okay go", "never mind then")."""
    tokens = [token for token in words(text).split() if token not in FILLERS]
    if not tokens:
        return False
    said = ' '.join(tokens)
    if said in phrases:
        return True
    return len(tokens) >= 2 and (' '.join(tokens[:-1]) in phrases or ' '.join(tokens[1:]) in phrases)


def is_cancel(text):
    """A take-back: the whole utterance is a cancel phrase, give or take filler."""
    return _phrase_in(text, CANCEL)


# Any of these anywhere in an answer means it isn't a clean yes ("yeah, no", "okay wait", "sure, not that").
NOT_YES = {'no', 'nope', 'nah', 'not', "don't", 'dont', 'wait', 'stop', 'cancel', 'never', 'hold', 'later', 'back',
           'negative', 'undo', 'scratch', 'forget'}


def is_yes(text):
    tokens = words(text).split()
    return _phrase_in(text, YES) and not any(token in NOT_YES for token in tokens)


# Enough for an ordinary yes, not for an outward action: "right" and "sure" are as often thinking aloud as agreeing.
WEAK_YES = {'sure', 'ok', 'okay', 'right', 'correct', 'go', 'alright', 'fine'}


def is_clear_yes(text):
    """A yes that can send something outward: a yes word, or a weak one with more ("okay, go ahead")."""
    tokens = [token for token in words(text).split() if token not in FILLERS]
    return is_yes(text) and not all(token in WEAK_YES for token in tokens)


def is_no(text):
    return not is_yes(text) and _phrase_in(text, NO)


# Words a request doesn't end on. Said before a pause, they mean you're still going.
DANGLING = {'that', 'and', 'the', 'a', 'an', 'to', 'of', 'for', 'with', 'but', 'because', 'or', 'if', 'which', 'when',
            'then', 'like', 'my', 'our', 'your', 'its', 'in', 'on', 'at', 'from', 'into', 'about', 'um', 'uh', 'so',
            'also', 'plus', 'where', 'who', 'whose', 'than', 'as', 'by', 'is', 'are', 'was', 'be', 'can', 'could',
            'should', 'would', 'will', 'please', 'maybe'}


def dangling(text):
    """True when the transcript stops on a word nothing ends on ("Add a button that")."""
    tokens = words(text).split()
    return bool(tokens) and tokens[-1] in DANGLING and not (text or '').rstrip().endswith('?')


def outward(text):
    """What the request would do outside the Mac, in a few words, or None when it's an ordinary request."""
    said = words(text)
    for pattern, what in OUTWARD:
        if pattern.search(said):
            return what
    return None


def readback(text, what):
    """What's said back before an outward request goes anywhere. One of a few fixed lines, so it's ready to play at
    once (speaker.py caches them); the transcript itself is on screen next to it."""
    return f'That {what}. Say yes to go ahead.'


READBACKS = [readback('', what) for _, what in OUTWARD]
