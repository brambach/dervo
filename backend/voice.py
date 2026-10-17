"""One voice for everything Dervo and its agents say on screen.

Bryce, 18 Sep 2026: concise, accurate, useful. No em dashes, no filler, no exclamation marks, contractions, plain
words, the same everywhere. The build enforces it in three places:

* the prompts Dervo gives its models carry RULES (Home's system prompt and the coding agents' guide prompt both use it),
* every model-written line the backend passes to the screen goes through ``plain`` first (Home's replies and arrival
  summaries, its workstream statuses, the coding agents' guide lines and thread titles),
* ``frontend/scripts/check-copy.mjs`` scans the UI strings and the backend's own sentences for em dashes and a short
  banned list.

``plain`` cleans what is about to be stored or shown. It never rewrites a record that is already saved.
"""
import re

RULES = ('Write like a sharp colleague: short, plain, specific. Contractions ("it\'s", "don\'t"). Never the em dash '
         'character; use a full stop, a comma or a spaced hyphen. No exclamation marks. No filler and no praise: never '
         '"Great question", "Let me know", "Feel free", "Rest assured". Plain words, never leverage, robust, seamless, '
         'comprehensive or streamlined. Say what you didn\'t check.')

_EM_DASH = re.compile(r'\s*\u2014\s*')
_SPACED_EN_DASH = re.compile(r'(?<=\S)\s+–\s+(?=\S)')


def plain(text):
    """Model words in house style. An em dash becomes a spaced hyphen, and so does an en dash used as one (with spaces
    around it; "10–20" is a range and stays). Anything that isn't text comes back unchanged."""
    if not isinstance(text, str):
        return text
    return _SPACED_EN_DASH.sub(' - ', _EM_DASH.sub(' - ', text))
