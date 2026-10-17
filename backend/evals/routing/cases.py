"""Naming, sorting and cleanup cases for thread_routing: small, hand-checked, and grown from real mistakes.

Each case is (input, what's right). ``run()`` returns {group: (passed, total, [failure lines])}.
"""
import json
import pathlib

import thread_routing

LEARNED = pathlib.Path(__file__).resolve().parent / 'learned_cases.json'

NOW = 1_800_000_000.0
DAY = 86400

# name(thread): words, any of which the new name must contain, or None when the name must be left alone.
NAMING = [
    (dict(name='can you make the sidebar scroll position stick when switching threads please', request=
          'can you make the sidebar scroll position stick when switching threads please'), ['sidebar', 'scroll']),
    (dict(name='New conversation', request='the export button crashes when the table is empty'), ['export']),
    (dict(name='New conversation', goal='Fix CSV export crash on empty tables', request='export is broken'), ['csv', 'export']),
    (dict(name='Sidebar scroll position', request='make the sidebar keep its scroll'), None),
    (dict(name='i want the onboarding to wow people, the first screen should show the smiley…', nameLocked=True), None),
    (dict(name='Hey can we redo onboarding so it feels like magic and the smiley waves?',
          request='Hey can we redo onboarding so it feels like magic and the smiley waves?'), ['onboarding']),
    (dict(name='New conversation', request='Add Stripe webhooks for subscription renewals'), ['stripe', 'webhook']),
    (dict(name='New conversation', request='fix the flaky test in test_home_routing.py'), ['test', 'flaky']),
    (dict(name='New conversation', request='Investigate why the iPhone app drops the relay connection after sleep'),
     ['relay', 'iphone', 'connection']),
    (dict(name='New conversation', request='the landing page hero is too tall on mobile'), ['landing', 'hero']),
]
NAMING_OTHERS = ['CSV export', 'Fix login crash']

# sort(threads): the expected order of thread ids.
SORTING = [
    ([dict(id='a', state='idle', since=NOW - 50), dict(id='b', state='working', since=NOW - 300),
      dict(id='c', state='needs_you', pending_question='Which DB?', since=NOW - 100),
      dict(id='d', state='finished', unread=True, since=NOW - 20), dict(id='e', state='finished', since=NOW - 10),
      dict(id='f', state='working', archived=True, since=NOW - 5)],
     ['c', 'b', 'd', 'e', 'a', 'f']),
    # Rows stay put while they stream: last activity doesn't move a working row, when it started does.
    ([dict(id='a', state='working', since=NOW - 600, at=NOW - 1), dict(id='b', state='working', since=NOW - 60, at=NOW - 50)],
     ['b', 'a']),
    # The longest wait first in needs_you, and a lost run counts as needing you.
    ([dict(id='a', state='needs_you', pending_question='?', since=NOW - 60), dict(id='b', state='needs_you', pending_question='?', since=NOW - 900),
      dict(id='c', state='unknown', since=NOW - 30)],
     ['b', 'a', 'c']),
    # Pinned leads its bucket.
    ([dict(id='a', state='idle', since=NOW - 10), dict(id='b', state='idle', pinned=True, since=NOW - 9000)], ['b', 'a']),
]

# tidy(threads): {thread_id: action} expected; anything not listed must be left alone.
TIDY = [
    ([dict(id='done-read', state='finished', at=NOW - 3 * DAY), dict(id='done-unread', state='finished', unread=True, at=NOW - 9 * DAY),
      dict(id='working', state='working', at=NOW - 9 * DAY), dict(id='asks', state='needs_you', pending_question='?', at=NOW - 30 * DAY),
      dict(id='lost', state='unknown', at=NOW - 30 * DAY), dict(id='pinned', state='idle', pinned=True, at=NOW - 30 * DAY),
      dict(id='fresh', state='finished', at=NOW - 3600), dict(id='old-idle', state='idle', at=NOW - 8 * DAY),
      dict(id='stopped-recent', state='stopped', at=NOW - 3 * DAY)],
     {'done-read': 'archive', 'old-idle': 'archive'}),
    ([dict(id='empty', name='CSV export', state='idle', runs=0, at=NOW - 25 * 3600),
      dict(id='real', name='CSV export', state='finished', unread=True, runs=2, at=NOW - 25 * 3600),
      dict(id='empty-new', state='idle', runs=0, at=NOW - 600)],
     {'empty': 'archive'}),
    ([dict(id='back', state='working', archived=True, at=NOW - 5), dict(id='still-away', state='idle', archived=True, at=NOW - 99 * DAY),
      dict(id='waiting-review', state='finished', attention=True, at=NOW - 20 * DAY)],
     {'back': 'unarchive'}),
]


def learned():
    """Corrections from your own use (learn.py): a name you changed back, an archive you undid."""
    try:
        data = json.loads(LEARNED.read_text())
    except (OSError, ValueError):
        return [], []
    names = [(dict(item['thread'], _others=item.get('others') or []), item) for item in data.get('naming') or [] if isinstance(item, dict)]
    tidies = [item for item in data.get('tidy') or [] if isinstance(item, dict)]
    return names, tidies


def run(learned_too=False):
    """``learned_too`` adds your own corrections (learned_cases.json, not in git). The committed gate never reads them."""
    out = {}
    passed, failures = 0, []
    for thread, wanted in NAMING:
        got = thread_routing.name(thread, NAMING_OTHERS)
        title = got['title'] if got else None
        ok = title is None if wanted is None else bool(title) and any(w in title.casefold() for w in wanted)
        passed += ok
        if not ok:
            failures.append(f"name({thread.get('name')!r}) gave {title!r}, wanted {'no change' if wanted is None else wanted}")
    out['naming'] = (passed, len(NAMING), failures)
    passed, failures = 0, []
    for threads, order in SORTING:
        got = [row['thread_id'] for row in thread_routing.sort(threads, NOW)]
        passed += got == order
        if got != order:
            failures.append(f'sort gave {got}, wanted {order}')
    out['sorting'] = (passed, len(SORTING), failures)
    passed, failures = 0, []
    for threads, wanted in TIDY:
        got = {row['thread_id']: row['action'] for row in thread_routing.tidy(threads, NOW)}
        passed += got == wanted
        if got != wanted:
            failures.append(f'tidy gave {got}, wanted {wanted}')
    out['cleanup'] = (passed, len(TIDY), failures)
    names, tidies = learned() if learned_too else ([], [])
    if names or tidies:
        passed, failures = 0, []
        for thread, item in names:
            got = thread_routing.name({k: v for k, v in thread.items() if k != '_others'}, thread['_others'])
            ok = not got or got['title'] != item.get('rejected')  # never the name you changed
            passed += ok
            if not ok:
                failures.append(f"name gave {got['title']!r} again; you renamed it {item.get('wanted')!r}")
        for item in tidies:
            from evals.routing.score import thread_record
            threads = [thread_record(record, NOW) for record in item['threads']]
            got = {row['thread_id'] for row in thread_routing.tidy(threads, NOW) if row['action'] == 'archive'}
            ok = item.get('keep') not in got
            passed += ok
            if not ok:
                failures.append(f"tidy archived {item.get('keep')} again; you brought it back")
        out['learned'] = (passed, len(names) + len(tidies), failures)
    return out
