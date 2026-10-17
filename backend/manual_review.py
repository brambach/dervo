"""Record untested changes for human review without running or inventing a check."""
import base64
import difflib
import check_profiles
from dervo_connections import Rejected


def enabled(task):
    return task.get('check_profile') == check_profiles.MANUAL


def finish(coding, task):
    if not enabled(task):
        raise Rejected('This task has an automated check')
    if not task['runs'] or task['runs'][-1]['state'] != 'completed':
        raise Rejected('Wait for the provider to finish before reviewing changes')
    if any(run['state'] == 'uncertain' for run in task['runs']):
        raise Rejected('The provider outcome is unknown. Inspect it before reviewing changes.')
    if any(token['state'] not in ('delivered', 'cancelled') for token in task['tokens']):
        raise Rejected('A tool result is still pending. Inspect it before reviewing changes.')
    coding.require_grant(task)
    evidence = coding.evidence.read(task['id'], **task['context'])
    attempts = evidence['attempts']
    if not attempts or len(attempts) <= task['runs'][-1].get('prior_attempts', 0):
        raise Rejected('No changes were recorded for this run')
    latest = attempts[-1]
    # Validate the previous write's bytes before capturing the full manual-review scope below.
    # Reading another file doesn't require a test command in this mode.
    if latest['state'] != 'prepared' or not coding.evidence._current(evidence, latest, complete_scope=False):
        raise Rejected('The recorded changes are incomplete or the files changed afterwards. Inspect them before continuing.')
    # A fresh snapshot includes files admitted after the final write. This doesn't execute a command.
    attempt = coding.evidence.verify_current(task['id'], **task['context'])
    evidence = coding.evidence.read(task['id'], **task['context'])
    final = evidence['attempts'][-1]
    # Summarise this run's recorded edits. Earlier write attempts keep their individual deltas.
    changes = attempts[task['runs'][-1].get('prior_attempts', 0):]
    before = dict(final['before'])
    seen = set()
    for change in changes:
        for path in change.get('after', {}):
            if path not in seen:
                before[path] = change.get('before', {}).get(path)
                seen.add(path)
    diffs = {}
    for path, after in final['after'].items():
        left = coding.evidence.snapshot_bytes(before[path]) if before.get(path) else b''
        right = coding.evidence.snapshot_bytes(after)
        if max(len(left), len(right)) > 262144:
            # A file over 256 KiB (big files in scoped mode, beta.15): the same diff full access records for one.
            from native_evidence import _diff
            diffs[path] = _diff(path, left, right)
            continue
        try:
            left.decode('utf-8'); right.decode('utf-8')
        except UnicodeDecodeError:
            lines = difflib.unified_diff([left[i:i+16].hex(' ')+'\n' for i in range(0,len(left),16)],
                                         [right[i:i+16].hex(' ')+'\n' for i in range(0,len(right),16)],
                                         fromfile='before/'+path,tofile='after/'+path)
            delta = ''.join(lines)
            diffs[path] = 'Binary diff (hex bytes):\n'+delta if delta else ''
        else:
            lines = difflib.diff_bytes(difflib.unified_diff,left.splitlines(keepends=True),right.splitlines(keepends=True),
                                       fromfile=('before/'+path).encode(),tofile=('after/'+path).encode())
            diffs[path] = b''.join(line if line.endswith(b'\n') else line+b'\n\\ No newline at end of file\n' for line in lines).decode('utf-8')
    final.update(kind='manual-review', state='unverified', test=None, checks='not_run', before=before, diff=diffs)
    coding.evidence._save(evidence)
    task['acceptance'] = {'required': True, 'source': 'manual-review'}
    task.update(state='ready_for_review', error=None)
    return attempt['number']
