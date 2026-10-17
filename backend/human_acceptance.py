"""Optional human result approval, one project policy, separate from execution permission and from Independent review.

With approval on (the pre-existing behaviour) a finished result stays ``ready_for_review`` until the local owner
records an explicit decision through ``task_review``. With approval off a finished owner request becomes ``completed``
only from recorded host facts: the provider run completed, the current file attempt has completed host verification
with exit status 0 and no stop, no error is recorded, and the request's Independent review (if it was bound) ended
``passed``. A running, stopped, failed, stale or unresolved review, a failed check, an uncertain run or a text-only
answer never completes anything. Completion is never a human approval: ``task['review']`` and the evidence approval
record stay untouched, so the diff, check and review records remain inspectable exactly as recorded.

The policy is snapshotted onto each owner request when it starts (``task['acceptance']``). Changing the project policy
later changes nothing about pending tasks or existing records. A store whose coding grant predates this policy keeps
explicit approval on (``legacy``) until the owner changes it; a new project defaults to off.

Result approval does not authorise file access, destructive actions, deployment or spending: those are decided by the
execution permission alone, and nothing here widens it.
"""
import json
import time

from dervo_connections import Rejected

DETAIL = ('Result approval is separate from the coding permission. It does not authorise file access, destructive actions, '
          'deployment or spending. ')
DETAILS = {
    True: DETAIL + 'Each finished result waits for your explicit approval or correction request.',
    False: DETAIL + 'A finished result counts as Completed only after the provider completes, the host check process exits '
                    'successfully and the Independent review, when configured, ends clean. Nothing is marked as approved by you.',
}
COMPLETED_DETAIL = ('Completed. The provider finished, the host check process exited successfully and no review is open. '
                    'The provider can edit the checked tests, so this is not proof the work is correct. Not marked as approved by you.')


class HumanAcceptance:
    def __init__(self, coding):
        self.coding, self.store = coding, coding.store
        existed = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_acceptance_policy'").fetchone()
        with self.store.db:
            self.store.db.execute('CREATE TABLE IF NOT EXISTS coding_acceptance_policy (project_id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            if not existed:
                # Grants saved before this policy existed keep the explicit human approval they were made under.
                exists_grants = self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coding_permissions'").fetchone()
                rows = self.store.db.execute('SELECT project_id FROM coding_permissions').fetchall() if exists_grants else []
                for (project_id,) in rows:
                    self.store.db.execute('INSERT OR IGNORE INTO coding_acceptance_policy VALUES (?,?)',
                                          (project_id, json.dumps(dict(required=True, source='legacy', actor=None, created_at=int(time.time())))))

    # ------------------------------------------------------------------ policy
    def policy(self, project_id):
        row = self.store.db.execute('SELECT document FROM coding_acceptance_policy WHERE project_id=?', (project_id,)).fetchone()
        record = json.loads(row[0]) if row else dict(required=False, source='default', actor=None, created_at=None)
        return {**record, 'detail': DETAILS[record['required']]}

    def snapshot(self, project_id):
        policy = self.policy(project_id)
        return {'required': policy['required'], 'source': policy['source']}

    def set_policy(self, project_id, required, actor):
        if type(required) is not bool:
            raise Rejected('Result approval is exactly on (true) or off (false)')
        record = dict(required=required, source='explicit', actor=actor, created_at=int(time.time()))
        with self.store.db:
            self.store.db.execute('INSERT INTO coding_acceptance_policy VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET document=excluded.document',
                                  (project_id, json.dumps(record)))
        return self.policy(project_id)

    # ------------------------------------------------------------------ completion from recorded facts
    def task_blocker(self, task):
        """Reject blocked task records before loading their potentially large evidence documents."""
        if task.get('check_profile') == 'manual-review':
            return 'manual review requires your approval; no automated check ran'
        acceptance = task.get('acceptance')
        if not acceptance or acceptance.get('required') is not False:
            return 'human approval required'
        if task['state'] != 'ready_for_review' or task.get('review') is not None or task.get('error'):
            return 'not a clean finished result'
        if not task['runs'] or task['runs'][-1]['state'] != 'completed' or any(r['state'] == 'uncertain' for r in task['runs']):
            return 'provider run not confirmed completed'
        turn = self.coding.turns.get(task['id'])
        if turn is not None and not turn.closed:
            return 'provider turn still open'
        if any(t['state'] not in ('delivered', 'cancelled') or (t.get('job_id') and not t.get('job_done')) for t in task['tokens']):
            return 'tool delivery or check still pending'
        review = (task.get('collaboration') or {}).get('review')
        if review is not None and review.get('phase') != 'passed':
            return f"independent review {review.get('phase')}"
        return None

    def blocker(self, task, evidence):
        """Why this task can't be Completed right now, or None, using recorded facts and current files."""
        reason = self.task_blocker(task)
        if reason is not None:
            return reason
        if not evidence['attempts']:
            return 'no checked attempt'
        attempt = evidence['attempts'][-1]
        if attempt['number'] <= task['runs'][-1].get('prior_attempts', 0):
            return 'no attempt from the current run'
        # A stopped or unresolved review cannot complete. Check its recorded outcome before rereading compiler inputs.
        review = (task.get('collaboration') or {}).get('review')
        if review is not None:
            if not self.review_current(review, attempt):
                return 'independent review snapshot is not current'
        if attempt['state'] != 'verified' or not attempt.get('test') or not self.coding.evidence._current(evidence, attempt):
            return 'host verification is not current'
        if attempt['test']['exit_status'] != 0 or attempt['test']['stopped']:
            return 'host checks failed or stopped'
        if evidence.get('approval') != {'scope_version': task['scope_version'], 'actor': task['context']['actor']}:
            return 'execution approval is not current'
        if task.get('origin') == 'conversation' and self.coding.conversation.grant_problem(task) is not None:
            return 'coding permission revoked or changed'
        return None

    @staticmethod
    def review_current(review, attempt):
        """Whether the passed verdict describes exactly this attempt's files: the reviewed paths are the attempt's complete
        after set (a created or admitted file the reviewer never saw is not covered) and every hash matches."""
        snapshot, after = review.get('snapshot') or {}, attempt.get('after') or {}
        return bool(snapshot) and set(snapshot) == set(after) and all(after[path].get('sha256') == digest for path, digest in snapshot.items())

    @staticmethod
    def changed_scope(evidence, first_number, last):
        """Compare each file's first observed preimage with the final checked bytes.

        Files can enter scope after the first check through a read. That doesn't
        create them. A creation has no preimage in its first recorded attempt.
        """
        before = {}
        attempts = [a for a in evidence['attempts'] if first_number <= a['number'] <= last['number']]
        for attempt in sorted(attempts or [last], key=lambda a: a['number']):
            previous, following = attempt.get('before') or {}, attempt.get('after') or {}
            for path in set(previous) | set(following):
                before.setdefault(path, previous.get(path))
        after = last.get('after') or {}
        return sorted(p for p in set(before) | set(after) if (before.get(p) or {}).get('sha256') != (after.get(p) or {}).get('sha256'))

    def rearm_review(self, task):
        """A correction of a task whose Independent review already ended: the old verdict describes files that will change, so
        one fresh bounded cycle is armed (pending) and the earlier outcome is kept in ``history``. The request budget is not
        reset: ``review_open`` refuses a phase the budget cannot afford and ends the review ``failed`` with that reason."""
        review = (task.get('collaboration') or {}).get('review')
        if not review or review.get('phase') in ('pending',):
            return
        if review.get('phase') in ('initial', 'correction', 'final'):
            raise Rejected(f"Independent review is still running ({review['phase']}). Stop the task before requesting a correction.")
        history = review.setdefault('history', [])
        history.append({k: review.get(k) for k in ('phase', 'packets', 'findings', 'unresolved', 'snapshot', 'error')})
        review.update(phase='pending', packets=[], findings=None, unresolved=None, snapshot=None, error=None)

    def settle(self):
        """Complete every owner task whose snapshot says approval is off and whose recorded facts are all clean."""
        for task in self.coding.pending_tasks('acceptance'):
            if self.task_blocker(task) is not None:
                continue
            try:
                evidence = self.coding.evidence.read(task['id'], **task['context'])
            except Rejected:
                continue
            if self.blocker(task, evidence) is not None:
                continue
            attempt = evidence['attempts'][-1]
            review = (task.get('collaboration') or {}).get('review')
            first_number = task['runs'][0].get('prior_attempts', 0) + 1
            if task.get('completion'):
                task.setdefault('completions', []).append(task['completion'])  # the earlier outcome record is kept, never overwritten
            task['completion'] = dict(at=int(time.time()), attempt=attempt['number'], run_id=task['runs'][-1]['id'],
                                      review_phase=review['phase'] if review else None,
                                      review_snapshot=dict(review['snapshot']) if review else None,
                                      changed_files=self.changed_scope(evidence, first_number, attempt),
                                      attempt_changed_files=sorted(p for p, d in (attempt.get('diff') or {}).items() if d),
                                      test_exit_status=attempt['test']['exit_status'],
                                      check_profile=attempt['test'].get('check_profile'),  # absent on records made before profiles existed
                                      human_approved=False)
            task['state'] = 'completed'
            self.coding.save(task)

    STALE_ERROR = ('Files changed after this result was completed. The completed record is kept as recorded; approval is unavailable '
                   'for it. Request a correction or recheck to continue this task from the current files.')

    def check_current(self, task, evidence):
        """A completed task whose checked files changed afterwards is no longer Completed; the record stays inspectable."""
        if task['state'] != 'completed' or not task.get('completion'):
            return task
        attempt = next((a for a in evidence['attempts'] if a['number'] == task['completion']['attempt']), None)
        if attempt is None or not self.coding.evidence._current(evidence, attempt):
            task['completion']['stale'] = True
            task.update(state='ready_for_review', error=self.STALE_ERROR)
            self.coding.save(task)
        return task

    @staticmethod
    def stale_completed(task):
        """A result that was Completed and whose files changed afterwards: approval is unavailable; a correction is the route."""
        return task['state'] == 'ready_for_review' and bool((task.get('completion') or {}).get('stale'))
