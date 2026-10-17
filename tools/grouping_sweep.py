"""What the workstreams look like under different filing rules. Reads history, writes nothing.

Two knobs, because the two walls found tonight are a one-word match inside a very long brief, and the cap's
fallback once seven workstreams exist:

  window  how many words of the request may match a workstream. 0 means the thread's name only.
  cap     WORKSTREAM_CAP.

usage: grouping_sweep.py <folder> [<folder> ...]
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'tools'))

import project_ideas
import history_import


def sweep(folder, window, cap):
    """One run of the reader with the two knobs applied, returning {workstream: thread count}."""
    original_words, original_cap = project_ideas.FilingView.words, project_ideas.WORKSTREAM_CAP
    project_ideas.WORKSTREAM_CAP = cap

    def windowed(self, text):
        # The opening of the request only. The rest of a 3,000 character brief is what lets one common verb
        # attach a thread to whatever workstream was made first.
        cut = text if window is None else ' '.join(str(text or '').split()[:window])
        return original_words(self, cut)

    project_ideas.FilingView.words = windowed
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as store:
            plan = history_import.plan(folder, store)
    finally:
        project_ideas.FilingView.words, project_ideas.WORKSTREAM_CAP = original_words, original_cap
    return Counter(w['name'] for w in plan['workstreams'] for _ in w['threads'])


for folder in sys.argv[1:]:
    print(f'\n=== {folder}')
    for window in (None, 100, 40, 20, 0):
        for cap in (7, 15, 30):
            try:
                counts = sweep(folder, window, cap)
            except Exception as exc:  # noqa: BLE001 - a sweep cell that fails should not stop the table
                print(f'  window={window if window is not None else "all":>4} cap={cap:<3} failed: {type(exc).__name__}: {exc}')
                continue
            biggest = max(counts.values()) if counts else 0
            print(f'  window={str(window) if window is not None else "all":>4} cap={cap:<3} '
                  f'{len(counts):>2} workstreams, biggest holds {biggest:>2}  {dict(counts.most_common(6))}')
