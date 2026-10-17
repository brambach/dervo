"""Saved human decisions and delivery observations, never new task authority."""

def outcome_facts(task):
    decision = task.get('review')
    if decision not in ('approved', 'rejected'):
        decision = None
    uncertain = task.get('state') in ('disconnected', 'unknown')
    for key in ('runs', 'tokens'):
        entries = task.get(key)
        if entries is None:
            continue
        if not isinstance(entries, list):
            uncertain = True
            continue
        uncertain = uncertain or any(not isinstance(entry, dict) or entry.get('state') == 'uncertain' for entry in entries)
    return {'human_decision': decision, 'outcome_uncertain': uncertain}


def review_line(task):
    """The one sentence a finished result waiting on you gets, everywhere it appears: "Ready for review." and one short
    clause about the checks. Never the task's error: that has its own home on the screen (Bryce, 18 Sep: the review
    line appeared twice, and once with an error folded in)."""
    completion = task.get('completion') if isinstance(task.get('completion'), dict) else {}
    profile = task.get('check_profile') if isinstance(task.get('check_profile'), str) else completion.get('check_profile')
    exit_status = completion.get('test_exit_status')
    if profile == 'manual-review':
        return 'Ready for review. No checks ran in Dervo.'
    if type(exit_status) is int:
        return 'Ready for review. The check passed.' if exit_status == 0 else 'Ready for review. The check failed.'
    return 'Ready for review.'
