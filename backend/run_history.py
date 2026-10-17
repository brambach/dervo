"""Agent history: what each run did and why, read from the records Dervo already keeps.

Bryce, 23 Sep 2026: "Knowing exactly what the models are doing and why. A track record, kind of like a git history but
for agents, and making it really easy to go back and look at what it did and trace it back." The concept and this first
slice are in reports/review-20260923/agent-history.md.

The unit is the run: one agent, on one account and model, working once. Every sentence here is built from a record field
Dervo wrote itself (the task document, its evidence, the tool audit, Home's entries, git), or it's the agent's own words,
returned under ``said`` or ``step`` for the screen to quote, never as fact. Nothing is summarised by a model, and when a
record can't back a claim the answer says so instead of filling the gap.

Four reads. None of them writes anything, starts anything or calls a model:

* ``history_list``: a project's runs, newest first, each with how it ended, one line of what it did, who started it and
  the runs it's linked to. Home's replies that started threads are rows too.
* ``run_record``: one run in full: your words, Home's part, what it was told, the steps it reported with the calls Dervo
  saw under each, the checks with exit codes, the files with line counts and what it said about itself.
* ``trace_file``: a window of a file's lines, each block labelled with the run or git author that wrote it.
* ``trace_line``: why one line is there, from ``git blame`` and the commit's "Task <id> approved in Dervo" back to the run
  and your request. A line nobody wrote in Dervo says so and shows git's author.

Scope: the project's own personal account opens it (project_record_scope), and runs from every personal account in the
project show, not only that one, so a thread moved to Codex keeps its Claude runs (handoff map H1). A private thread shows
only to its owner. Work accounts and helper runs aren't rows. Archived threads are (b16/git and b16/lifecycle agreed,
25 Sep): they keep their place, marked Archived, and say when Dervo put them away itself (conversation_tidied).

Beta.16 (b16/git, evidence/b16-git/survey.md section 5): ``history_list`` also groups the runs into pieces of work, one
per thread, under ``work``: what it added up to, from the same rows, with its commits, whether this Mac knows they're on
GitHub, and its pull request as Dervo last saw it (github_host.latest_prs). Grouping happens here, not on the screen, so
the row cut never splits a thread. It never calls GitHub.
"""
import base64
import difflib
import json
import os
import re
import stat
import subprocess
import time

from dervo_connections import Rejected
from project_record_scope import project_record_scope
from task_questions import PREFIX as ANSWER

SCOPE = {'projectId', 'profileId'}
FIELDS = {
    'history_list': SCOPE | {'filter', 'provider', 'file'},
    'run_record': SCOPE | {'taskId', 'runId'},
    'trace_file': SCOPE | {'path', 'line'},
    'trace_line': SCOPE | {'path', 'line'},
}
# The grouped view's filters: everything, waiting on you, commits this Mac hasn't pushed, and threads with a pull
# request. 'changed' and 'home' are the run list's, kept for an older screen.
FILTERS = ('all', 'waiting', 'local', 'prs', 'changed', 'home')
PROVIDERS = {'claude': 'Claude Code', 'codex': 'Codex'}
SHORT = {'claude': 'Claude', 'codex': 'Codex'}
LIST_LIMIT = 150            # rows one history_list returns
WORK_LIMIT = 60             # pieces of work one history_list returns, each with all of its runs
SCAN_LIMIT = 800            # task documents one read looks at, newest first
DOC_BYTES = 4 * 1024 * 1024  # a larger task document is skipped, and the answer says some were
QUOTE = 600                 # characters of the agent's own words kept in one quote
ASKED = 2000                # characters of your request kept
FILE_BYTES = 2 * 1024 * 1024
WINDOW = 80                 # lines of a file one trace shows
LINE_CHARS = 400            # characters of one line returned
# One budget per request. The bridge doesn't just fail a request that takes ten seconds, it kills the host with every
# live run in it (src/bridge.rs), so every git call and the record scan share this one deadline and a slow repository
# gets a partial answer instead (merge security pass, 23 Sep, F1).
BUDGET = 5.0
SCAN_SHARE = 0.4            # the most of it reading task records may take in a trace, so git keeps the rest
MIN_GIT = 0.3               # a git call isn't started with less than this left
SLOW = 'Git took too long on this file. Try a smaller range.'
CUT = 'Dervo stopped reading older records so this could answer in time. Some older runs aren’t included.'
SHAS = 100                  # commits one trace reads
NOBODY = 'Nobody wrote this in Dervo.'
ZERO = '0' * 40
# Anchored at a line's start: a subject a thread's name wrote can't pass for the body's own line (security pass, L2).
TASK_LINE = re.compile(r'^Task ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ([a-z]+) in Dervo\.', re.M)
TRAILER = re.compile(r'^Dervo-Task:[ \t]*([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[ \t]*$', re.M)
SHA = re.compile(r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')
WRITES = ('dervo_write_file', 'dervo_edit_file', 'dervo_create_file', 'native:Edit', 'native:Write')
READS = ('dervo_read_file', 'native:Read')
GUIDE = 'dervo_update_task_guide'
CHECK = 'dervo_run_tests'
TOOLS = {'dervo_read_file': 'Read', 'dervo_write_file': 'Write', 'dervo_edit_file': 'Edit', 'dervo_create_file': 'Create', 'dervo_list_files': 'List',
         'dervo_search_files': 'Search', CHECK: 'Check', 'dervo_request_help': 'Ask', 'dervo_remember': 'Note',
         'dervo_propose_skill': 'Skill', 'dervo_search_history': 'History', 'dervo_suggest_lesson': 'Lesson',
         'dervo_check_chat': 'Chat'}
CHECK_WORDS = {'python-unittest': 'the Python tests', 'node-test': 'the Node tests', 'typescript-check': 'the TypeScript check',
               'rust-check': 'the Rust check', 'rust-test': 'the Rust tests'}
# A command the agent ran itself that reads like a check. Only which of its commands "Checked" lists; the words are its.
CHECKISH = re.compile(r'\b(tests?|pytest|unittest|jest|vitest|mocha|tsc|typecheck|type-check|lint|eslint|clippy|cargo (?:test|check)|go test)\b', re.I)
# A sentence of the agent's reply that claims something about checks or results. Quoted, never believed.
CLAIMS = re.compile(r'\b(tests?|checks?|pass(?:es|ed|ing)?|fail(?:s|ed|ing)?|verified|works|exit(?:ed)?|errors?)\b', re.I)
REVIEW_OPEN = ('pending', 'initial', 'correction', 'final')
FAILURES = {'usage_limit': 'Stopped at {who}’s usage limit', 'rate_limit': 'Stopped at {who}’s rate limit',
            'overloaded': 'Stopped: {who} was overloaded', 'server_error': 'Stopped: {who} had a server error',
            'connection': 'Stopped: {who} lost its connection', 'api_error': 'Stopped: {who} reported an API error'}
ORDINALS = ('First', 'Second', 'Third', 'Fourth', 'Fifth')
# Client ids of requests Dervo sends for you that carry an agent's words, not yours (thread_fanout.py starts a proposal
# as 'thread-proposal-<id>'; task_questions.PREFIX answers a question). Since the H1 fix both arrive as Home jobs.
PROPOSAL = 'thread-proposal-'


# ---------------------------------------------------------------- small readers

def _num(value):
    return float(value) if type(value) in (int, float) and value > 0 else None


def _dicts(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value):
    return value if isinstance(value, dict) else {}


def _text(value, limit=None):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if limit and len(value) > limit:
        cut = value[:limit]
        cut = cut[:cut.rindex(' ')] if ' ' in cut[limit // 2:] else cut
        return cut.rstrip(' .,;:') + '…'
    return value


def _base(path):
    if not isinstance(path, str) or not path:
        return None
    return path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] or None


def _join(items):
    items = [item for item in items if item]
    if len(items) <= 1:
        return ''.join(items)
    return ', '.join(items[:-1]) + ' and ' + items[-1]


def _table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _relative(path, root):
    """A recorded path as the project sees it: relative to its folder. Claude Code's own tools record absolute paths."""
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip().replace('\\', '/')
    if path.startswith('/'):
        for base in {str(root).rstrip('/'), os.path.realpath(str(root)).rstrip('/')}:
            if path.startswith(base + '/'):
                return path[len(base) + 1:]
        return path
    while path.startswith('./'):
        path = path[2:]
    return path


def _decode(entry):
    """A file image from evidence, as text, or None when there isn't one or it's too big to compare."""
    if not isinstance(entry, dict) or not isinstance(entry.get('bytes_b64'), str) or len(entry['bytes_b64']) > 4 * FILE_BYTES:
        return None
    try:
        return base64.b64decode(entry['bytes_b64']).decode('utf-8', errors='replace')
    except (ValueError, TypeError):
        return None


def _counts(diff):
    added = removed = 0
    for line in diff.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1
    return added, removed


def _delta(added, removed):
    return ' '.join(part for part in (f'+{added}' if added else '', f'−{removed}' if removed else '') if part)


def _added(diff):
    return {line[1:].rstrip('\r') for line in diff.split('\n') if line.startswith('+') and not line.startswith('+++')}


def _wrote(diff):
    """The lines a diff put in, numbered on the new side: (first, last), or None. Context lines don't count."""
    first = last = number = None
    for line in diff.split('\n'):
        match = HUNK.match(line)
        if match:
            number = int(match.group(1))
        elif number is None or line.startswith('+++') or line.startswith('---') or line.startswith('\\'):
            continue
        elif line.startswith('+'):
            first, last = number if first is None else first, number
            number += 1
        elif not line.startswith('-'):
            number += 1
    return (first, last) if first is not None else None


def _sentences(text):
    """A reply's sentences, with list marks and headings taken off. The words stay the agent's."""
    out = []
    for raw in text.split('\n'):
        line = re.sub(r'^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)', '', raw).strip()
        if not line or line.startswith('```'):
            continue
        out += [part.strip() for part in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9`“"(])', line) if part.strip()]
    return out


# ---------------------------------------------------------------- scope

class _Scope:
    """What one read may see: the project, its threads you can open, the task records in them and their evidence."""

    def __init__(self, host, args, actor):
        self.db = host.store.db
        self.project = project_record_scope(host.store, args['projectId'], args['profileId'])
        self.project_id = self.project['project_id']
        self.root = self.project['cwd']
        self.actor = actor
        self.profiles = {row['id']: dict(row) for row in self.db.execute(
            'SELECT id, provider, identity, category, method, name FROM profiles')}
        # Archived threads stay, marked: {thread: when}. Dervo's own tidy says when it found the thread quiet.
        archived, tidied = {}, {}
        if _table(self.db, 'conversation_archives'):
            archived = {row[0]: _num(row[1]) for row in self.db.execute('SELECT thread_id, archived_at FROM conversation_archives')}
        if _table(self.db, 'conversation_tidied'):
            tidied = {row[0]: dict(at=_num(row[1]), quietSince=_num(row[2])) for row in self.db.execute(
                'SELECT thread_id, tidied_at, quiet_since FROM conversation_tidied')}
        self.threads = {}
        for row in self.db.execute("SELECT t.id, t.visibility, t.profile_id, c.name FROM threads t LEFT JOIN local_channels c "
                                   "ON c.id=t.id WHERE t.project_id=? AND (t.visibility='shared' OR t.owner=?) ORDER BY t.rowid",
                                   (self.project_id, actor)):
            profile = self.profiles.get(row['profile_id'])
            # Threads on the project's own side only: a work project's records never show a personal account's, nor
            # the other way round.
            if not profile or profile['category'] != self.project['category']:
                continue
            self.threads[row['id']] = dict(name=row['name'] or 'Unnamed thread', private=row['visibility'] == 'private',
                                           archived=row['id'] in archived, tidied=tidied.get(row['id']) if row['id'] in archived else None)
        self._tasks = None
        self._evidence = {}
        self._fanout = None
        self._clients = None
        self.origins = {}
        self.incomplete = False
        self.slow = False           # git ran out of the budget
        self.cut = False            # the record scan stopped at its share of the budget
        self.started = time.monotonic()
        self.deadline = self.started + BUDGET
        self.scan_until = self.deadline

    def left(self):
        return self.deadline - time.monotonic()

    def visible(self, task_id, document):
        try:
            task = json.loads(document)
        except (TypeError, ValueError):
            return None
        if not isinstance(task, dict) or task.get('id') != task_id:
            return None
        context = _dict(task.get('context'))
        if context.get('actor') != self.actor or context.get('project_id') != self.project_id or context.get('thread_id') not in self.threads:
            return None
        profile = self.profiles.get(_dict(task.get('binding')).get('profile_id'))
        return task if profile and profile['category'] == self.project['category'] else None

    def tasks(self):
        """Every task you can see in this project, newest saved first."""
        if self._tasks is not None:
            return self._tasks
        self._tasks = []
        if not _table(self.db, 'coding_tasks'):
            return self._tasks
        rows = self.db.execute('SELECT id, CASE WHEN length(document)<=? THEN document END AS document FROM coding_tasks '
                               'WHERE instr(document, ?)>0 ORDER BY rowid DESC LIMIT ?',
                               (DOC_BYTES, self.project_id, SCAN_LIMIT + 1))
        for count, row in enumerate(rows):
            # Read lazily and stop at the scan's share of the budget: the newest records are the ones kept.
            if count >= SCAN_LIMIT or time.monotonic() > self.scan_until:
                self.incomplete, self.cut = True, self.cut or count < SCAN_LIMIT
                break
            if row['document'] is None:
                self.incomplete = True
                continue
            task = self.visible(row['id'], row['document'])
            if task:
                self._tasks.append(task)
        return self._tasks

    def task(self, task_id):
        if type(task_id) is not str or not task_id or len(task_id) > 128:
            return None
        known = next((task for task in self._tasks or [] if task['id'] == task_id), None)
        if known or not _table(self.db, 'coding_tasks'):
            return known
        row = self.db.execute('SELECT CASE WHEN length(document)<=? THEN document END AS document FROM coding_tasks WHERE id=?',
                              (DOC_BYTES, task_id)).fetchone()
        return self.visible(task_id, row['document']) if row and row['document'] else None

    def attempts(self, task_id):
        """{number: attempt} from the task's evidence: the before and after of each change and the check that ran on it."""
        if task_id not in self._evidence:
            found = {}
            if _table(self.db, 'task_evidence'):
                row = self.db.execute('SELECT document FROM task_evidence WHERE id=?', (task_id,)).fetchone()
                try:
                    document = json.loads(row['document']) if row else {}
                except (TypeError, ValueError):
                    document = {}
                for attempt in _dicts(_dict(document).get('attempts')):
                    if type(attempt.get('number')) is int:
                        found[attempt['number']] = attempt
            self._evidence[task_id] = found
        return self._evidence[task_id]

    def audit(self, task_id, tokens):
        """{token: audit document} for these tool calls: the arguments Dervo validated and what it answered."""
        wanted = [token for token in tokens if isinstance(token, str)]
        if not wanted or not _table(self.db, 'coding_tool_audit'):
            return {}
        found = {}
        for start in range(0, len(wanted), 400):
            chunk = wanted[start:start + 400]
            for row in self.db.execute(f'SELECT token, document FROM coding_tool_audit WHERE task_id=? AND token IN ({",".join("?" * len(chunk))})',
                                       (task_id, *chunk)):
                try:
                    found[row['token']] = json.loads(row['document'])
                except (TypeError, ValueError):
                    pass
        return found

    def fanout(self):
        """{thread: source task} for threads a run proposed and you started."""
        if self._fanout is None:
            self._fanout = {}
            if _table(self.db, 'thread_fanout_children'):
                for row in self.db.execute('SELECT thread_id, source_task_id FROM thread_fanout_children WHERE project_id=?', (self.project_id,)):
                    self._fanout[row['thread_id']] = row['source_task_id']
        return self._fanout

    def entry(self, entry_id):
        """One of Home's chat entries in this project, from an account on the project's side, or None."""
        if type(entry_id) is not str or not entry_id or not _table(self.db, 'orchestrator_entries'):
            return None
        row = self.db.execute('SELECT created_at, profile_id, document FROM orchestrator_entries WHERE id=? AND project_id=?',
                              (entry_id, self.project_id)).fetchone()
        profile = self.profiles.get(row['profile_id']) if row else None
        if not profile or profile['category'] != self.project['category']:
            return None
        try:
            document = json.loads(row['document'])
        except (TypeError, ValueError):
            return None
        return dict(document, id=entry_id, created_at=_num(row['created_at'])) if isinstance(document, dict) else None

    def clients(self):
        """{task id: the client id its request was sent with}, for this project's threads, read once."""
        if self._clients is None:
            self._clients = {}
            if _table(self.db, 'coding_requests') and self.threads:
                ids = list(self.threads)
                for start in range(0, len(ids), 400):
                    chunk = ids[start:start + 400]
                    for row in self.db.execute(f'SELECT task_id, client_id FROM coding_requests WHERE thread_id IN ({",".join("?" * len(chunk))})', chunk):
                        if isinstance(row['client_id'], str):
                            self._clients[row['task_id']] = row['client_id']
        return self._clients

    def record(self, table, key):
        """One document from a proposal or question table, in this project, or None."""
        if not _table(self.db, table):
            return None
        row = self.db.execute(f'SELECT document FROM {table} WHERE id=? AND project_id=?', (key, self.project_id)).fetchone()
        try:
            document = json.loads(row['document']) if row else None
        except (TypeError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def name(self, task):
        return self.threads.get(_dict(task.get('context')).get('thread_id'), {}).get('name', 'Unnamed thread')


# ---------------------------------------------------------------- one run, from its task

def _runs(task):
    return _dicts(task.get('runs'))


def _tokens(task, run):
    return [token for token in _dicts(task.get('tokens')) if token.get('run_id') == run.get('id')]


def _asks(task):
    """Your requests in the task, in order. A message you added to a running run is a steer, not a request."""
    return [m for m in _dicts(task.get('messages')) if m.get('role') == 'user' and isinstance(m.get('text'), str) and not m.get('steer')]


def _process_start(run):
    value = _num(_dict(run.get('process')).get('start'))
    if value is None:
        return None
    return value / 1e6 if value > 1e14 else value / 1e3 if value > 1e11 else value


def _window(task, index):
    """(start, end, request) for one run. The end is None while it runs."""
    runs = _runs(task)
    run = runs[index]
    tokens = _tokens(task, run)
    starts = [value for value in (_num(token.get('started_at')) for token in tokens) if value]
    start = _num(run.get('started_at')) or _process_start(run) or (min(starts) if starts else None)
    asks = _asks(task)
    ask = None
    if asks:
        if len(asks) == len(runs):
            ask = asks[index]
        elif start is not None:
            earlier = [m for m in asks if (_num(m.get('created_at')) or 0) <= start + 2]
            ask = earlier[-1] if earlier else asks[0]
        else:
            ask = asks[min(index, len(asks) - 1)]
    if start is None and ask:
        start = _num(ask.get('created_at'))
    end = _num(run.get('ended_at'))
    completion = _dict(task.get('completion'))
    if end is None and completion.get('run_id') == run.get('id'):
        end = _num(completion.get('at'))
    if end is None:
        said = [value for value in (_num(m.get('created_at')) for m in _dicts(task.get('messages'))
                                    if m.get('role') == 'assistant' and m.get('run_id') == run.get('id')) if value]
        end = max(said) if said else None
    if end is None:
        ends = [value for value in (_num(token.get('ended_at')) for token in tokens) if value]
        end = max(ends) if ends else None
    if task.get('state') == 'working' and index == len(runs) - 1:
        end = None
    return start, end, ask


def _attempt_range(task, index):
    """The evidence attempts a run made: numbers after its ``prior_attempts`` and up to the next run's."""
    runs = _runs(task)
    low = runs[index].get('prior_attempts')
    low = low if type(low) is int else 0
    high = runs[index + 1].get('prior_attempts') if index + 1 < len(runs) else None
    return low, high if type(high) is int else None


def _run_of_attempt(task, number):
    for index in range(len(_runs(task))):
        low, high = _attempt_range(task, index)
        if number > low and (high is None or number <= high):
            return index
    return max(0, len(_runs(task)) - 1)


def _access(task):
    if task.get('read_only'):
        return 'Read only'
    if task.get('full_access'):
        return 'Full access'
    if task.get('project_access'):
        return 'Whole project'
    return 'Chosen files'


def _who(scope, task):
    binding = _dict(task.get('binding'))
    profile = scope.profiles.get(binding.get('profile_id')) or {}
    provider = binding.get('provider') if isinstance(binding.get('provider'), str) else profile.get('provider')
    return dict(provider=provider, providerLabel=PROVIDERS.get(provider, 'Agent'),
                model=binding.get('model') if isinstance(binding.get('model'), str) else None,
                account=_text(profile.get('name')) or _text(profile.get('identity')) or _text(binding.get('identity')),
                access=_access(task))


def _title(task):
    """A task named the way it was: the guide's title, else your first line."""
    guide = _dict((_runs(task) or [{}])[-1].get('guide'))
    title = _text(guide.get('title'), 60)
    if title:
        return title
    asks = _asks(task)
    first = next((line for line in (asks[0]['text'] if asks else '').splitlines() if line.strip()), '')
    return _text(first, 60) or 'a request'


def _ended(task, index):
    """(glyph, words) for how one run ended, from its record. Only the latest run speaks for the task."""
    runs = _runs(task)
    run = runs[index]
    who = SHORT.get(_dict(task.get('binding')).get('provider'), 'The provider')
    if index < len(runs) - 1:
        state = run.get('state')
        if state == 'completed':
            return 'done', 'Finished'
        if state == 'interrupted':
            return 'stopped', 'Stopped'
        # Beta.17.1 (run_ending): an earlier run Dervo lost, stopped at a limit or never started didn't fail.
        if state == 'uncertain':
            return 'unknown', 'Outcome not confirmed'
        if run.get('end_cause') == 'host_limit':
            return 'stopped', 'Stopped at a limit'
        if run.get('end_cause') in ('not_started', 'host_restart'):
            return 'stopped', 'Didn’t start'
        kind = run.get('provider_failure')
        return 'failed', FAILURES[kind].format(who=who) if kind in FAILURES else 'Ended before it finished'
    from task_outcome import outcome_facts
    from conversation_activity import stopped_by_user
    facts = outcome_facts(task)
    state = task.get('state')
    phase = _dict(_dict(task.get('collaboration')).get('review')).get('phase')
    if state == 'working':
        return 'running', 'Running'
    if phase in REVIEW_OPEN:
        return 'running', 'Independent review running'
    import run_ending
    ending = run_ending.classify(task)
    if facts['outcome_uncertain']:
        return 'unknown', 'Outcome not confirmed'  # Dervo lost track: unknown, not failed (beta.17.1)
    if state == 'needs_permission':
        return 'needs', 'Waiting for file permission'
    if state == 'ready_for_review':
        if _dict(task.get('completion')).get('stale') is True:
            return 'needs', 'Files changed after this result'
        if facts['human_decision'] == 'rejected':
            return 'needs', 'Waiting for your correction'
        return 'needs', 'Waiting for your review'
    if state == 'blocked':
        if stopped_by_user(task):
            return 'stopped', 'You paused it'
        kind = run.get('provider_failure')
        if (ending or {}).get('limit') == 'provider' and kind in FAILURES:
            return 'stopped', FAILURES[kind].format(who=who)  # the provider's own limit: a stop, not a failure
        if run_ending.glyph_words(ending):
            return run_ending.glyph_words(ending)
        return 'failed', FAILURES[kind].format(who=who) if kind in FAILURES else 'Couldn’t finish'
    if state == 'disconnected':
        return run_ending.glyph_words(ending) or ('unknown', 'Dervo lost track of the run')
    if state == 'answered':
        return 'done', 'Answered'
    if state == 'idle' and facts['human_decision'] == 'approved':
        return 'done', 'Approved by you'
    if state in ('completed', 'idle'):
        return 'done', 'Finished'
    return 'idle', 'Saved'


def _check_file(argv):
    args = [arg for arg in argv if isinstance(arg, str)] if isinstance(argv, list) else []
    if '-p' in args and args.index('-p') + 1 < len(args):
        return _base(args[args.index('-p') + 1])
    return next((_base(arg) for arg in reversed(args) if re.search(r'\.(py|mjs|cjs|js|ts|tsx|rs|json)$', arg)), None)


def _check(test, at=None):
    """One check Dervo ran, from the attempt's test record, as a sentence and the facts behind it."""
    profile = test.get('check_profile') if isinstance(test.get('check_profile'), str) else None
    file = _check_file(test.get('argv'))
    exit_status = test.get('exit_status') if type(test.get('exit_status')) is int else None
    stopped = _text(test.get('stopped'), 120)
    what = f'Dervo’s check ran {file}' if file else f'Dervo ran {CHECK_WORDS.get(profile, "its check")}'
    if stopped:
        sentence = f'{what} and stopped it: {stopped}.'
    elif exit_status is not None:
        sentence = f'{what}: exit {exit_status}.'
    else:
        sentence = f'{what}. No exit code was recorded.'
    return dict(file=file, profile=profile, exit=exit_status, stopped=stopped, at=at, text=sentence)


def _response(audit_document):
    try:
        value = json.loads(_dict(_dict(audit_document).get('response')).get('text') or 'null')
    except (TypeError, ValueError):
        return {}
    return _dict(value)


def _checks(task, run, tokens, attempts, audit):
    """The checks Dervo ran in this run, in order: from the attempts' test records, timed by the call that asked."""
    found, seen = [], set()
    for token in tokens:
        if token.get('tool') != CHECK or token.get('state') != 'delivered':
            continue
        attempt = attempts.get(token.get('attempt')) if type(token.get('attempt')) is int else None
        test = _dict(attempt.get('test')) if attempt else {}
        if not test:
            test = _response(audit.get(token.get('token')))
        if test and (token.get('attempt'), token.get('token')) not in seen:
            seen.add((token.get('attempt'), token.get('token')))
            found.append(_check(test, _num(token.get('ended_at')) or _num(token.get('started_at'))))
    completion = _dict(task.get('completion'))
    if not found and completion.get('run_id') == run.get('id') and type(completion.get('test_exit_status')) is int:
        found.append(_check(dict(exit_status=completion['test_exit_status'], check_profile=completion.get('check_profile'), argv=task.get('argv')),
                            _num(completion.get('at'))))
    return found


def _own(tokens):
    """Commands the agent ran itself that read like checks: the command, when, and whether it errored. Never the output."""
    found = []
    for token in tokens:
        command = _text(token.get('command'), 200)
        if token.get('tool') == 'native:Bash' and command and CHECKISH.search(command) and token.get('state') == 'delivered':
            started, ended = _num(token.get('started_at')), _num(token.get('ended_at'))
            found.append(dict(command=command, prose=token.get('prose') is True, at=started, error=token.get('success') is False,
                              seconds=round(ended - started) if started and ended and ended >= started else None))
    return found


def _quick_files(task, run, tokens, root):
    """What a run changed, without reading evidence: the completion when it's this run's, else its write calls."""
    completion = _dict(task.get('completion'))
    if completion.get('run_id') == run.get('id') and isinstance(completion.get('changed_files'), list):
        return [path for path in completion['changed_files'] if isinstance(path, str)]
    seen = []
    for token in tokens:
        if token.get('tool') in WRITES and token.get('state') == 'delivered' and token.get('success') is not False:
            path = _relative(token.get('path'), root)
            if path and path not in seen:
                seen.append(path)
    for path in run.get('written') or []:
        path = _relative(path, root)
        if path and path not in seen:
            seen.append(path)
    return seen


def _files(task, index, attempts, root, tokens):
    """[{path, added, removed}] this run changed, from its evidence: first before to last after, per file."""
    low, high = _attempt_range(task, index)
    mine = [attempts[n] for n in sorted(attempts) if n > low and (high is None or n <= high) and attempts[n].get('kind') != 'restoration']
    order, first, last, summed = [], {}, {}, {}
    for attempt in mine:
        for path, diff in _dict(attempt.get('diff')).items():
            if not isinstance(diff, str) or not diff:
                continue
            if path not in order:
                order.append(path)
                first[path] = _dict(attempt.get('before')).get(path)
            last[path] = _dict(attempt.get('after')).get(path)
            added, removed = _counts(diff) if not diff.startswith('Binary') else (0, 0)
            summed[path] = (summed.get(path, (0, 0))[0] + added, summed.get(path, (0, 0))[1] + removed)
    files = []
    for path in order:
        before, after = _decode(first[path]) if first[path] else '', _decode(last[path])
        if before is not None and after is not None:
            added, removed = _counts('\n'.join(difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm='', n=0)))
        else:
            added, removed = summed[path]
        files.append(dict(path=path, added=added, removed=removed))
    if not files:
        files = [dict(path=path, added=None, removed=None) for path in _quick_files(task, _runs(task)[index], tokens, root)]
    return files


def _did(files, checks, commit, glyph, latest, answered):
    """What the run did, from the record: the files, the last check Dervo ran and whether the change was committed."""
    parts = []
    names = [_base(f) for f in files]
    if names:
        parts.append(f'Changed {_join(names)}.' if len(names) <= 2 else f'Changed {len(names)} files.')
    elif answered:
        parts.append('Changed no files.')
    if checks:
        parts.append(checks[-1]['text'])
    elif names:
        parts.append('No check ran in Dervo.')
    if latest and commit:
        parts.append(f'Committed as {commit["short"]}{", then undone" if commit["reverted"] else ""}.')
    elif names and latest and glyph not in ('running', 'needs'):
        parts.append('Not committed.')
    return ' '.join(parts)


def _commit(task):
    commit = _dict(task.get('commit'))
    sha = commit.get('sha')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{7,64}', sha):
        return None
    return dict(sha=sha, short=sha[:7], at=_num(commit.get('at')), reverted=bool(commit.get('reverted')))


def _told_line(run):
    """What the run was told, in one sentence built from its record."""
    told, snapshot = _dict(run.get('told')), _dict(run.get('knowledge_snapshot'))
    if not told and not snapshot:
        return None
    items = []
    files = [_base(f.get('path')) for f in _dicts(_dict(snapshot.get('repository')).get('files'))]
    if files:
        items.append(_join(files[:2]) if len(files) <= 2 else f'{len(files)} repository files')
    rules = len(_dicts(snapshot.get('rules')))
    if rules:
        items.append(f'{rules} of your rules' if rules > 1 else '1 of your rules')
    notes = len(_dicts(told.get('notes')))
    if notes:
        items.append(f'{notes} project notes' if notes > 1 else '1 project note')
    skills = len(_dicts(told.get('skills')))
    if skills:
        items.append(f'{skills} skills' if skills > 1 else '1 skill')
    if _text(told.get('transcript')):
        items.append('what happened earlier in this thread')
    recent = len(_recent(told))
    if recent:
        items.append(f'recent work in {recent} other threads' if recent > 1 else 'recent work in 1 other thread')
    return (_join(items) + '.') if items else 'Nothing beyond your request.'


def _recent(told):
    block = _text(told.get('recent')) or ''
    return [line[2:].strip() for line in block.splitlines()[1:] if line.startswith('- ')][:12]


def _said(task, run, latest):
    """The agent's own words about what it did: sentences of its reply that claim a result, and its limits line.
    Returned to be quoted under "It said", never used as a fact."""
    reply = next((m.get('text') for m in reversed(_dicts(task.get('messages')))
                  if m.get('role') == 'assistant' and isinstance(m.get('text'), str)
                  and (m.get('run_id') == run.get('id') or (latest and not m.get('run_id')))), None)
    found = []
    if reply:
        sentences = [s for s in _sentences(reply) if 8 <= len(s) <= 400]
        claims = [s for s in sentences if CLAIMS.search(s)][:3] or sentences[:1]
        found += [dict(text=_text(s, QUOTE), source='reply') for s in claims]
    limits = _text(_dict(run.get('guide')).get('limits'), QUOTE)
    if limits:
        found.append(dict(text=limits, source='limits'))
    return found


def _origin(scope, task):
    """Where a task's request came from when it wasn't your message in the thread: a Home job, a thread another run
    proposed and you started, or your answer to an agent's question. None for your own message. The last two carry an
    agent's words (the proposal, the question), which go in ``quote`` for the screen to show quoted as theirs, never as
    what you asked. Read from the request's client id, whatever ``requested_by`` says (security pass, 23 Sep, F2)."""
    if task['id'] not in scope.origins:
        scope.origins[task['id']] = _origin_lookup(scope, task)
    return scope.origins[task['id']]


def _home(scope, task):
    """The origin when it's a Home job, else None."""
    origin = _origin(scope, task)
    return origin if origin and origin['kind'] == 'home' else None


def _answer_parts(scope, question_id, text):
    """(question, your answer, picked one of its options, the asking thread) for an answer, from the question's record,
    else from the words Dervo sent (task_questions.answer_text)."""
    record = scope.record('task_questions', question_id) if isinstance(question_id, str) and question_id else None
    if record and isinstance(record.get('answer'), dict):
        return (_text(record.get('ask'), QUOTE), _text(record['answer'].get('text'), ASKED), record['answer'].get('kind') == 'option',
                record.get('thread_id'))
    head, _, answer = (text or '').partition('\n\nMy answer:\n')
    question = head.split('\n', 1)[1] if head.startswith('Answer to question ') and '\n' in head else None
    return _text(question, QUOTE), _text(answer, ASKED) if question else None, False, None


def _overview(scope, task, text):
    """True for the run Dervo starts in a project's overview thread with its own instruction."""
    from project_lifecycle import OVERVIEW_PROMPT
    channel = _dict(task.get('context')).get('thread_id')
    if text != OVERVIEW_PROMPT or not channel or not _table(scope.db, 'conversation_origins'):
        return False
    row = scope.db.execute('SELECT 1 FROM conversation_origins WHERE thread_id=? AND origin=?', (channel, 'overview')).fetchone()
    return row is not None


def _origin_lookup(scope, task):
    client = scope.clients().get(task['id'], '')
    asks = _asks(task)
    first = asks[0] if asks else {}
    text, at = first.get('text'), _num(first.get('created_at'))
    base = dict(you=None, youAt=None, at=at, instruction=None, replyId=None, name=None, taskId=None, channelId=None,
                quote=None, quoteFrom=None, answer=None, picked=False)
    if client.startswith(PROPOSAL):
        proposal = scope.record('thread_proposals', client[len(PROPOSAL):]) or {}
        source = proposal.get('thread_id') if proposal.get('thread_id') in scope.threads else None
        name = scope.threads[source]['name'] if source else 'another thread'
        return dict(base, kind='proposal', name=name if source else None, taskId=proposal.get('task_id') if source else None, channelId=source,
                    line='Another thread proposed this, and you started it.', quote=_text(text, ASKED), quoteFrom=f'Proposal from {name}')
    if client.startswith(ANSWER):
        question, answer, picked, source = _answer_parts(scope, client[len(ANSWER):], text)
        source = source if source in scope.threads else _dict(task.get('context')).get('thread_id')
        name = scope.threads.get(source, {}).get('name', 'this thread')
        same = source == _dict(task.get('context')).get('thread_id')
        return dict(base, kind='answer', name=name, channelId=source, line=f'You answered a question from {name}.',
                    quote=question, quoteFrom='Its question' if same else f'Question from {name}', answer=answer, picked=picked)
    if task.get('read_only') and _overview(scope, task, text):
        # Dervo asked for the overview when the project came in. Its instruction isn't yours (RC review, 9 Oct).
        return dict(base, kind='overview', name='Dervo', line='Dervo started this when the project was added, to read it and write you an overview.')
    if task.get('requested_by') != 'home':
        return None
    instruction = _text(text, ASKED)
    reply_id, _, part = client.rpartition('-')
    reply = scope.entry(reply_id) if part.isdigit() else None
    you = scope.entry(reply.get('answers')) if reply and reply.get('kind') == 'reply' else None
    you = you if you and you.get('kind') == 'you' and _text(you.get('text')) else None
    if not you:
        # Home's own words with no message of yours on record: they're quoted as Home's, never shown as yours.
        return dict(base, kind='home', name='Home', instruction=instruction, replyId=reply_id if reply else None,
                    at=reply.get('created_at') if reply else at, line='Started by Home. Dervo’s records don’t hold the message it came from.',
                    quote=instruction, quoteFrom='Home wrote')
    return dict(base, kind='home', name='Home', you=_text(you.get('text'), ASKED), youAt=you.get('created_at'), at=reply.get('created_at'),
                instruction=instruction, replyId=reply_id, line='Started by Home from your message', quote=instruction, quoteFrom='Home wrote')


def _asked(scope, task, index, ask):
    """(asked, origin) for a run. ``asked`` is only ever your own words: your message in the thread, your message on
    Home, or your answer to an agent's question. ``origin`` says where the run came from otherwise, with the agent's
    words quoted in it."""
    origin = _origin(scope, task) if index == 0 else None
    if origin is None:
        return (dict(text=_text(ask.get('text'), ASKED), at=_num(ask.get('created_at')), where='thread') if ask else None), None
    view = {key: origin[key] for key in ('kind', 'line', 'name', 'taskId', 'channelId', 'quote', 'quoteFrom', 'at')}
    if origin['kind'] == 'home' and origin['you']:
        return dict(text=origin['you'], at=origin['youAt'], where='home'), None
    if origin['kind'] == 'answer' and origin['answer']:
        return dict(text=origin['answer'], at=origin['at'], where='answer', picked=origin['picked']), view
    return None, view


def _steer(scope, message):
    """A message you added while a run worked. An answer to its question keeps the question apart, as its words."""
    text, at = message.get('text'), _num(message.get('created_at'))
    client = message.get('client_id') if isinstance(message.get('client_id'), str) else ''
    if message.get('question_id') or client.startswith(ANSWER):
        question, answer, picked, _ = _answer_parts(scope, message.get('question_id') or client[len(ANSWER):], text)
        if question:
            return dict(text=answer, at=at, question=question, picked=picked)
    return dict(text=_text(text, ASKED), at=at, question=None, picked=False)


def _links(scope, task, index):
    """How this run is linked to others, each kind in words: Home, the earlier run it carried on from, a retry on the
    other provider, a correction, a thread another run proposed, an independent review."""
    links = []
    origin = _origin(scope, task) if index == 0 else None
    if origin and origin['kind'] == 'home':
        links.append(dict(kind='home', name='Home', at=origin['youAt'] or origin['at'],
                          text='Started by Home from your message' if origin['you'] else 'Started by Home'))
    elif origin and origin['kind'] == 'proposal':
        links.append(dict(kind='proposed', taskId=origin['taskId'], channelId=origin['channelId'], name=origin['name'],
                          text=f'Proposed by {origin["name"]}, started by you' if origin['name'] else 'Proposed by another thread, started by you'))
    elif origin and origin['kind'] == 'answer':
        links.append(dict(kind='answer', channelId=origin['channelId'], name=origin['name'], at=origin['at'], text=origin['line'].rstrip('.')))
    if index > 0:
        ordinal = ORDINALS[index] if index < len(ORDINALS) else f'Try {index + 1}'
        links.append(dict(kind='correction', text=f'{ordinal} run in this task, after you asked for changes'))
    continuity = _dict(task.get('continuity'))
    earlier = scope.task(continuity.get('from_task_id')) if index == 0 else None
    if earlier:
        same = _dict(earlier.get('context')).get('thread_id') == _dict(task.get('context')).get('thread_id')
        label = f'“{_title(earlier)}”' if same else scope.name(earlier)
        if continuity.get('state') == 'continues':
            text = f'Carried on the same session from {label}'
        else:
            reason = _text(continuity.get('reason'), 200)
            text = f'Started fresh after {label}. It was told what happened there' + (f'. {reason}' if reason else '')
        links.append(dict(kind='continued' if continuity.get('state') == 'continues' else 'fresh', taskId=earlier['id'],
                          channelId=_dict(earlier.get('context')).get('thread_id'), name=scope.name(earlier), text=text.rstrip('.')))
    route = _dict(task.get('route'))
    retried = scope.task(route.get('retry_of')) if index == 0 else None
    if retried:
        other = SHORT.get(_dict(retried.get('binding')).get('provider'), 'the other provider')
        kind = (_runs(retried) or [{}])[-1].get('provider_failure')
        after = {'usage_limit': ' after its usage limit', 'rate_limit': ' after its rate limit', 'overloaded': ' after it was overloaded'}.get(kind, '')
        links.append(dict(kind='retry', taskId=retried['id'], channelId=_dict(retried.get('context')).get('thread_id'),
                          name=scope.name(retried), text=f'Took over from {other}{after}'))
    source = scope.fanout().get(_dict(task.get('context')).get('thread_id'))
    proposed = origin is not None and origin['kind'] == 'proposal'
    proposer = scope.task(source) if source and index == 0 and not proposed and _first_in_thread(scope, task) else None
    if proposer:
        links.append(dict(kind='proposed', taskId=proposer['id'], channelId=_dict(proposer.get('context')).get('thread_id'),
                          name=scope.name(proposer), text=f'Proposed by {scope.name(proposer)}, started by you'))
    review = _dict(_dict(task.get('collaboration')).get('review'))
    if review.get('phase') and index == len(_runs(task)) - 1:
        by = PROVIDERS.get(review.get('provider'), 'another agent')
        words = {'passed': 'passed', 'unresolved': 'ended with findings open', 'failed': 'failed', 'stale': 'went stale',
                 'interrupted': 'was interrupted', 'skipped': 'was skipped'}.get(review['phase'], 'is running')
        links.append(dict(kind='review', provider=review.get('provider'), text=f'Independent review by {by} {words}'))
    return links


def _first_in_thread(scope, task):
    thread = _dict(task.get('context')).get('thread_id')
    mine = [t for t in scope.tasks() if _dict(t.get('context')).get('thread_id') == thread]
    return bool(mine) and mine[-1]['id'] == task['id']


def _row(scope, task, index, audit):
    """One run as a History row."""
    runs = _runs(task)
    run = runs[index]
    latest = index == len(runs) - 1
    start, end, ask = _window(task, index)
    tokens = _tokens(task, run)
    glyph, ended = _ended(task, index)
    attempts = {}
    checks = _checks(task, run, tokens, attempts, audit)
    files = _quick_files(task, run, tokens, scope.root)
    did = _did(files, checks, _commit(task), glyph, latest, task.get('state') == 'answered' and latest)
    origin = _origin(scope, task) if index == 0 else None
    thread = _dict(task.get('context')).get('thread_id')
    by = origin['kind'] if origin else 'retry' if _dict(task.get('route')).get('retry_of') and index == 0 else 'you'
    asked, came = _asked(scope, task, index, ask)
    said = _said(task, run, latest)
    if came and came['quote']:
        said = [{'text': came['quote'], 'source': {'answer': 'question'}.get(came['kind'], came['kind']), 'from': came['quoteFrom']}] + said
    return dict(kind='run', key=f'{task["id"]}:{run.get("id")}', taskId=task['id'], runId=run.get('id'), runIndex=index + 1, runCount=len(runs),
                channelId=thread, name=scope.name(task), private=scope.threads[thread]['private'], **_who(scope, task),
                startedAt=start, endedAt=end, glyph=glyph, ended=ended, did=did, line=f'{ended}. {did}'.strip() if did else f'{ended}.',
                files=files, checks=checks, own=_own(tokens), asked=asked, origin=came, told=_told_line(run), said=said,
                startedBy=by, links=_links(scope, task, index), linked=[], commit=_commit(task) if latest else None)


def _home_rows(scope, starts):
    """Home's replies that sent jobs into threads, as rows. ``starts`` maps a reply id to the task keys it started."""
    if not _table(scope.db, 'orchestrator_entries'):
        return []
    rows = []
    for entry in scope.db.execute("SELECT id FROM orchestrator_entries WHERE project_id=? ORDER BY created_at DESC LIMIT 400", (scope.project_id,)):
        reply = scope.entry(entry['id'])
        if not reply or reply.get('kind') != 'reply':
            continue
        sent = [p for p in _dicts(reply.get('parts')) if p.get('state') == 'sent' and p.get('channel_id') in scope.threads]
        if not sent:
            continue
        started = [scope.threads[p['channel_id']]['name'] for p in sent if p.get('action') != 'add']
        added = [scope.threads[p['channel_id']]['name'] for p in sent if p.get('action') == 'add']
        line = ' and '.join(part for part in (f'Started {_join(started)}' if started else '', f'added to {_join(added)}' if added else '') if part)
        you = scope.entry(reply.get('answers'))
        you = you if you and you.get('kind') == 'you' else None
        rows.append(dict(kind='home', key=f'home:{reply["id"]}', taskId=None, runId=None, channelId=None, name='Home', private=False,
                         provider=None, providerLabel='Home', model=None, account=None, access=None,
                         startedAt=reply.get('created_at'), endedAt=None, glyph='done', ended='Done',
                         did='', line=(line[0].upper() + line[1:] if line else 'Sent your message on') + ' from your message.',
                         files=[], checks=[], own=[], asked=dict(text=_text(you.get('text'), ASKED), at=you.get('created_at'), where='home') if you else None, origin=None,
                         told=None, said=[], startedBy='you', links=[], linked=list(starts.get(reply['id'], [])), commit=None))
    return rows


def _link_same_files(rows):
    """Runs in different threads that changed the same file: the later one says it edited after the earlier one, and the
    earlier one names who edited it later. Relationships in words, never lines between rows."""
    by_path = {}
    for row in rows:
        for path in row['files']:
            by_path.setdefault(path, []).append(row)
    for path, touched in by_path.items():
        touched.sort(key=lambda row: row['startedAt'] or 0)
        for earlier, later in zip(touched, touched[1:]):
            if earlier['channelId'] == later['channelId']:
                continue
            if not any(link.get('kind') == 'after' for link in later['links']):
                later['links'].append(dict(kind='after', taskId=earlier['taskId'], channelId=earlier['channelId'], name=earlier['name'],
                                           path=path, text=f'Edited {_base(path)} after {earlier["name"]}'))
            if not any(link.get('kind') == 'later' for link in earlier['links']):
                on = f', on {later["providerLabel"]}' if later['provider'] != earlier['provider'] else ''
                earlier['links'].append(dict(kind='later', taskId=later['taskId'], channelId=later['channelId'], name=later['name'],
                                             path=path, text=f'{later["name"]} edited the same file later{on}'))
            for a, b in ((earlier, later), (later, earlier)):
                if b['key'] not in a['linked']:
                    a['linked'].append(b['key'])


# ---------------------------------------------------------------- pieces of work (beta.16)

def _checks_words(runs):
    """The last check Dervo ran across a thread's runs, in a few words: "check passed on run 2"."""
    for row in reversed(runs):
        for check in reversed(row['checks']):
            if check['exit'] is None and not check['stopped']:
                continue
            how = 'passed' if check['exit'] == 0 else 'was stopped' if check['stopped'] else 'failed'
            where = f' on run {row["runIndex"]}' if (row.get('runCount') or 1) > 1 or len(runs) > 1 else ''
            return f'check {how}{where}'
    return 'no check ran in Dervo' if any(row['files'] for row in runs) else ''


def _state_words(latest, committed):
    """How the piece of work stands, from its newest run: "Approved and committed", "Waiting for your review"."""
    if latest['ended'] == 'Approved by you':
        return 'Approved and committed' if committed else 'Approved'
    if latest['ended'] == 'Waiting for your review':
        return 'Ready for review'
    return latest['ended']


def _pr_view(record):
    """A pull request record as History shows it: what Dervo last saw, with when and as whom. Never read again here."""
    if not record:
        return None
    seen = _dict(record.get('seen'))
    return dict(number=record.get('number'), url=record.get('url') if isinstance(record.get('url'), str) else None, repo=record.get('repo'),
                head=record.get('head'), base=record.get('base'), mode=record.get('mode'), attempt=record.get('state'),
                detail=_text(record.get('detail'), 300), state=seen.get('state') or ('open' if record.get('number') else None),
                draft=bool(seen.get('draft', record.get('draft'))), review=seen.get('review'), reviewers=_dict(seen.get('reviewers')),
                checks=seen.get('checks') if isinstance(seen.get('checks'), dict) else None, comments=_dicts(seen.get('comments'))[-6:],
                checkedAt=_num(record.get('checked_at')), checkedAs=record.get('checked_as') if isinstance(record.get('checked_as'), str) else None)


def _work(scope, rows):
    """One piece of work per thread: its runs (Home's reply that started it first), how it stands, who worked on it,
    what it added up to, its commits and where they are, and its pull request. Everything from rows and records."""
    import github_host
    threads, home_for = {}, {}
    for row in rows:
        if row['kind'] == 'run' and row['channelId'] in scope.threads:
            threads.setdefault(row['channelId'], []).append(row)
    for row in rows:
        if row['kind'] == 'home':
            for key in row['linked']:
                started = next((r for r in rows if r['key'] == key and r['kind'] == 'run'), None)
                if started and started['channelId'] in threads and row not in home_for.setdefault(started['channelId'], []):
                    home_for[started['channelId']].append(row)
    prs = github_host.latest_prs(scope.db, scope.project_id)
    commits = []
    for runs in threads.values():
        commits += [row['commit']['sha'] for row in runs if row['commit'] and not row['commit']['reverted']]
    where = github_host.on_remotes(scope.root, commits, timeout=min(3.0, scope.left() - 0.5)) if commits and scope.left() > 1.5 else {}
    items = []
    for thread, runs in threads.items():
        runs.sort(key=lambda row: row['startedAt'] or 0)
        latest = max(runs, key=lambda row: (row['startedAt'] or 0, row['runIndex'] or 0))
        info = scope.threads[thread]
        agents = []
        for row in runs:
            if row['providerLabel'] not in agents:
                agents.append(row['providerLabel'])
        files = []
        for row in runs:
            files += [path for path in row['files'] if path not in files]
        made = [dict(row['commit'], onGitHub=where.get(row['commit']['sha'])) for row in runs if row['commit']]
        live = [commit for commit in made if not commit['reverted']]
        pr = _pr_view(prs.get(thread))
        opened = bool(pr and pr['number'] and pr['attempt'] == 'opened')
        known = [commit['onGitHub'] for commit in live if commit['onGitHub'] is not None]
        place = None
        if live:
            place = 'github' if opened or (known and len(known) == len(live) and all(known)) else 'local' if known and not all(known) else None
        ends = [row['endedAt'] for row in runs]
        glyph = latest['glyph']
        count = len(runs)
        summary = [f'{count} run' + ('' if count == 1 else 's')]
        if files:
            summary.append(f'changed {len(files)} file' + ('' if len(files) == 1 else 's'))
        check = _checks_words(runs)
        if check:
            summary.append(check)
        homes = home_for.get(thread, [])
        items.append(dict(
            key=f'work:{thread}', channelId=thread, name=info['name'], private=info['private'], archived=info['archived'], tidied=info['tidied'],
            glyph=glyph, state=_state_words(latest, bool(live)), agents=agents, runCount=count, files=files, summary=', '.join(summary),
            startedAt=min([row['startedAt'] for row in runs if row['startedAt']] or [None]) if any(row['startedAt'] for row in runs) else None,
            endedAt=None if glyph == 'running' or any(end is None for end in ends[-1:]) else max(end for end in ends if end),
            at=max([row['endedAt'] or row['startedAt'] or 0 for row in runs]), commits=made, where=place, pr=pr,
            reviewTaskId=latest['taskId'] if glyph == 'needs' else None,
            runs=[home['key'] for home in homes] + [row['key'] for row in runs]))
    items.sort(key=lambda item: item['at'] or 0, reverse=True)
    return items


def _work_counts(items):
    return dict(all=len(items), waiting=sum(1 for item in items if item['glyph'] == 'needs'),
                local=sum(1 for item in items if item['where'] == 'local'), prs=sum(1 for item in items if item['pr'] and item['pr']['number']))


# ---------------------------------------------------------------- history_list

def history_list(scope, args):
    wanted, provider, file = args['filter'], args['provider'], args['file']
    if wanted not in FILTERS:
        raise Rejected('Show all runs, runs that changed files, or runs Home started.')
    if provider not in ('', *PROVIDERS):
        raise Rejected('Choose Claude Code, Codex or any agent.')
    if type(file) is not str or len(file) > 300:
        raise Rejected('Filter by a file name of up to 300 characters.')
    rows, starts = [], {}
    scope.scan_until = scope.started + BUDGET * 0.6
    for task in scope.tasks():
        if scope.left() < 1.0:
            scope.incomplete = scope.cut = True  # the rest waits for a narrower filter rather than risk the host
            break
        if task.get('origin') != 'conversation' or not _runs(task):
            continue
        checks = [token.get('token') for token in _dicts(task.get('tokens')) if token.get('tool') == CHECK]
        audit = scope.audit(task['id'], checks) if checks else {}
        for index in range(len(_runs(task))):
            row = _row(scope, task, index, audit)
            rows.append(row)
            if row['startedBy'] == 'home' and index == 0:
                home = _home(scope, task)
                if home and home.get('replyId'):
                    starts.setdefault(home['replyId'], []).append(row['key'])
    for row in rows:
        if row['runCount'] > 1:
            row['linked'] += [other['key'] for other in rows if other['taskId'] == row['taskId'] and other['key'] != row['key']]
    home_rows = _home_rows(scope, starts)
    for home in home_rows:
        for row in rows:
            if row['key'] in home['linked']:
                row['linked'].append(home['key'])
    _link_same_files(rows)
    rows = sorted(rows + home_rows, key=lambda row: row['startedAt'] or 0, reverse=True)
    if provider:
        rows = [row for row in rows if row['provider'] == provider]
    needle = file.strip().lower()
    if needle:
        rows = [row for row in rows if any(needle in path.lower() for path in row['files'])]
    work = _work(scope, rows)
    work_counts = _work_counts(work)
    counts = dict(all=len(rows), changed=sum(1 for row in rows if row['files']),
                  home=sum(1 for row in rows if row['kind'] == 'home' or row['startedBy'] == 'home'),
                  work=work_counts['all'], waiting=work_counts['waiting'], local=work_counts['local'], prs=work_counts['prs'])
    if wanted == 'changed':
        rows = [row for row in rows if row['files']]
    elif wanted == 'home':
        rows = [row for row in rows if row['kind'] == 'home' or row['startedBy'] == 'home']
    elif wanted == 'waiting':
        work = [item for item in work if item['glyph'] == 'needs']
    elif wanted == 'local':
        work = [item for item in work if item['where'] == 'local']
    elif wanted == 'prs':
        work = [item for item in work if item['pr'] and item['pr']['number']]
    providers = sorted({task_provider for task_provider in (_dict(t.get('binding')).get('provider') for t in scope.tasks()) if task_provider in PROVIDERS})
    shown = work[:WORK_LIMIT]
    if wanted in FILTERS[:4]:
        # The grouped view's rows: every run of every piece shown, whatever the run cut would have dropped.
        keys = {key for item in shown for key in item['runs']}
        runs = [row for row in rows if row['key'] in keys]
        more = max(0, len(work) - WORK_LIMIT)
    else:
        runs, more = rows[:LIST_LIMIT], max(0, len(rows) - LIST_LIMIT)
    return dict(runs=runs, work=shown, more=more, counts=counts, providers=providers,
                incomplete=scope.incomplete, note=CUT if scope.cut else None)


# ---------------------------------------------------------------- run_record

def _call(token, audit, attempts, root):
    """One tool call as Dervo saw it: the tool, what it was on, and what came back that a record can say."""
    tool = token.get('tool') if isinstance(token.get('tool'), str) else ''
    name = tool[7:] if tool.startswith('native:') else TOOLS.get(tool, tool.replace('dervo_', '').replace('_', ' ').capitalize() or 'Call')
    arguments = _dict(_dict(audit.get(token.get('token'))).get('arguments'))
    target = (_relative(token.get('path'), root) or _text(token.get('command'), 160)
              or _text(arguments.get('query') or arguments.get('pattern') or arguments.get('directory') or arguments.get('name'), 160) or '')
    if tool == 'native:Connector' and isinstance(token.get('connector'), str):
        target = f'{token["connector"]}: {target}' if target else token['connector']
    failed = token.get('success') is False
    out = ''
    if token.get('state') == 'cancelled':
        out = 'cancelled'
    elif tool in ('dervo_write_file', 'dervo_edit_file', 'dervo_create_file') and type(token.get('attempt')) is int:
        diff = _dict(_dict(attempts.get(token['attempt'])).get('diff')).get(_relative(token.get('path'), root) or '')
        if isinstance(diff, str) and diff and not diff.startswith('Binary'):
            out = _delta(*_counts(diff))
    elif tool == CHECK:
        test = _dict(_dict(attempts.get(token.get('attempt'))).get('test')) or _response(audit.get(token.get('token')))
        if _text(test.get('stopped')):
            out = 'stopped'
        elif type(test.get('exit_status')) is int:
            out = f'exit {test["exit_status"]}'
    elif tool == 'native:Bash' and token.get('state') == 'delivered':
        out = 'error' if failed else 'no error'
    if failed and not out:
        out = 'error'
    started, ended = _num(token.get('started_at')), _num(token.get('ended_at'))
    if tool in (CHECK, 'native:Bash') and started and ended and ended - started >= 1:
        out = f'{round(ended - started)} s' + (f' · {out}' if out else '')
    return dict(tool=name, target=target, out=out, failed=failed, at=started)


def _steps(task, run, tokens, audit, attempts, root):
    """The steps it reported, each with the calls Dervo saw after it and before the next. Calls before its first report
    sit in a group with no text. The step words and their why are the agent's."""
    steps = []
    for token in tokens:
        if token.get('tool') != GUIDE or token.get('success') is False:
            continue
        arguments = _dict(_dict(audit.get(token.get('token'))).get('arguments'))
        text = _text(arguments.get('step'), QUOTE)
        if text:
            steps.append(dict(at=_num(token.get('started_at')), text=text, why=_text(arguments.get('why'), QUOTE), calls=[]))
    if not steps:
        steps = [dict(at=_num(s.get('at')), text=_text(s.get('step'), QUOTE), why=None, calls=[]) for s in _dicts(run.get('guide_steps')) if _text(s.get('step'))]
    head = dict(at=None, text=None, why=None, calls=[])
    for token in tokens:
        if token.get('tool') == GUIDE:
            continue
        call = _call(token, audit, attempts, root)
        target = head
        for step in steps:
            if step['at'] is not None and call['at'] is not None and step['at'] <= call['at'] + 0.001:
                target = step
        target['calls'].append(call)
    head['at'] = next((call['at'] for call in head['calls'] if call['at']), None)
    return ([head] if head['calls'] else []) + steps


def _told(scope, task, run):
    told, snapshot = _dict(run.get('told')), _dict(run.get('knowledge_snapshot'))
    continuity = _dict(told.get('continuity'))
    earlier = scope.task(continuity.get('from_task_id'))
    return dict(
        recorded=bool(told or snapshot),
        repository=[dict(path=f.get('path'), bytes=f.get('bytes') if type(f.get('bytes')) is int else None, cut=bool(f.get('cut')))
                    for f in _dicts(_dict(snapshot.get('repository')).get('files')) if isinstance(f.get('path'), str)],
        rules=len(_dicts(snapshot.get('rules'))),
        notes=[_text(n.get('text'), 300) for n in _dicts(told.get('notes')) if _text(n.get('text'))],
        skills=[_text(s.get('name'), 80) for s in _dicts(told.get('skills')) if _text(s.get('name'))],
        recent=_recent(told),
        workstream=_text(told.get('workstream'), 400),
        earlier=dict(state=continuity.get('state'), reason=_text(continuity.get('reason'), 200),
                     taskId=earlier['id'] if earlier else None, name=_title(earlier) if earlier else None) if _text(told.get('transcript')) else None)


def run_record(scope, args):
    task = scope.task(args['taskId'])
    if not task or task.get('origin') != 'conversation' or not _runs(task):
        raise Rejected('That run isn’t in this project’s records.')
    runs = _runs(task)
    run_id = args['runId']
    if type(run_id) is not str:
        raise Rejected('Choose a run of this task.')
    index = next((i for i, run in enumerate(runs) if run.get('id') == run_id), None) if run_id else len(runs) - 1
    if index is None:
        raise Rejected('That run isn’t in this task’s record.')
    run = runs[index]
    latest = index == len(runs) - 1
    tokens = _tokens(task, run)
    audit = scope.audit(task['id'], [token.get('token') for token in tokens])
    attempts = scope.attempts(task['id'])
    start, end, ask = _window(task, index)
    glyph, ended = _ended(task, index)
    checks = _checks(task, run, tokens, attempts, audit)
    own = _own(tokens)
    files = _files(task, index, attempts, scope.root, tokens)
    scope.scan_until = scope.started + BUDGET * SCAN_SHARE
    home = _home(scope, task) if index == 0 else None
    steers = [_steer(scope, m) for m in _dicts(task.get('messages'))
              if m.get('role') == 'user' and m.get('steer') and (start is None or (_num(m.get('created_at')) or 0) >= start - 1)
              and (end is None or (_num(m.get('created_at')) or 0) <= end + 1)]
    asked, came = _asked(scope, task, index, ask)
    if home and home['you']:
        why = dict(you=asked, home=dict(text=home['instruction'], at=home['at']), origin=None, steers=steers)
    else:
        why = dict(you=asked, home=None, origin=came, steers=steers)
    if checks:
        note = None
    elif own:
        note = 'Dervo ran no check of its own, so it can’t see a test count.'
    elif files:
        note = 'Nothing ran a check in Dervo, and it saw no test command.'
    else:
        note = None
    links = _links(scope, task, index)
    for other in scope.tasks():
        if other['id'] != task['id'] and _dict(other.get('continuity')).get('from_task_id') == task['id'] and latest:
            links.append(dict(kind='continued_by', taskId=other['id'], channelId=_dict(other.get('context')).get('thread_id'),
                              name=scope.name(other), at=_window(other, 0)[0] if _runs(other) else None,
                              text=f'{scope.name(other)} carried on from this' if _dict(other.get('context')).get('thread_id') != _dict(task.get('context')).get('thread_id')
                              else f'The next run in this thread carried on from this, “{_title(other)}”'))
    paths = {f['path'] for f in files}
    seen = set()
    for other in scope.tasks():
        if other['id'] == task['id'] or other.get('origin') != 'conversation' or not _runs(other):
            continue
        last = len(_runs(other)) - 1
        shared = sorted(paths & set(_quick_files(other, _runs(other)[last], _tokens(other, _runs(other)[last]), scope.root)))
        if not shared or other['id'] in seen:
            continue
        seen.add(other['id'])
        at = _window(other, last)[0]
        who = PROVIDERS.get(_dict(other.get('binding')).get('provider'), 'Another agent')
        when = 'after' if (at or 0) > (start or 0) else 'before'
        links.append(dict(kind='later' if when == 'after' else 'after', taskId=other['id'], channelId=_dict(other.get('context')).get('thread_id'),
                          name=scope.name(other), provider=_dict(other.get('binding')).get('provider'), at=at, path=shared[0],
                          text=f'{who} edited {_base(shared[0])} {when} this run'))
        if len(seen) >= 4:
            break
    return dict(taskId=task['id'], runId=run.get('id'), runIndex=index + 1, runCount=len(runs),
                channelId=_dict(task.get('context')).get('thread_id'), name=scope.name(task),
                private=scope.threads[_dict(task.get('context')).get('thread_id')]['private'], **_who(scope, task),
                startedAt=start, endedAt=end, glyph=glyph, ended=ended, state=task.get('state') if latest else run.get('state'),
                canReview=latest and task.get('state') == 'ready_for_review', why=why, told=_told(scope, task, run),
                steps=_steps(task, run, tokens, audit, attempts, scope.root), checks=dict(dervo=checks, own=own, note=note),
                said=_said(task, run, latest), files=files, commit=_commit(task) if latest else None, links=links)


# ---------------------------------------------------------------- trace

def _confine(root, path):
    """The file a trace reads: a regular file inside the project folder, reached without following a link, and never in
    git's own folder. Returns (absolute path, path relative to the project)."""
    if type(path) is not str or not path.strip() or len(path) > 1024 or '\0' in path:
        raise Rejected('Choose a file in this project.')
    relative = _relative(path, root)
    parts = [part for part in relative.split('/') if part not in ('', '.')]
    # This Mac's disk ignores case, so .GIT/config is git's own file too (security pass, L1).
    if relative.startswith('/') or not parts or '..' in parts or any(part.casefold() == '.git' for part in parts):
        raise Rejected('That file isn’t in this project folder.')
    base = os.path.realpath(str(root))
    current, info = base, None
    for part in parts:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            raise Rejected(f'{"/".join(parts)} isn’t in this project folder.') from None
        if stat.S_ISLNK(info.st_mode):
            raise Rejected('That path goes through a link. Dervo traces only files inside the project folder.')
    if not stat.S_ISREG(info.st_mode):
        raise Rejected('Choose a file, not a folder.')
    if os.path.commonpath([base, os.path.realpath(current)]) != base:
        raise Rejected('That file isn’t in this project folder.')
    return current, '/'.join(parts)


def _read_lines(absolute):
    try:
        descriptor = os.open(absolute, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError:
        raise Rejected('Dervo couldn’t open that file.') from None
    with os.fdopen(descriptor, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise Rejected('Choose a file, not a folder.')
        data = handle.read(FILE_BYTES + 1)
    if b'\0' in data[:8192]:
        raise Rejected('That’s a binary file. Dervo traces text files.')
    cut = len(data) > FILE_BYTES
    lines = data[:FILE_BYTES].decode('utf-8', errors='replace').split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    return [line.rstrip('\r') for line in lines], cut


class _OutOfTime(Exception):
    """The request's budget ran out before or during a git call."""


def _git(scope, *args):
    """One git call inside the request's budget. The repository's filter guards are read first (task_git.filter_guards),
    and both calls get only the time that's left. A guard read that timed out stops the call rather than running git
    without its guards."""
    import task_git
    timed_out = []

    def run(command, **options):
        left = scope.left()
        if left < 0.05:
            timed_out.append(True)
            raise subprocess.TimeoutExpired(command, 0)
        options['timeout'] = left
        try:
            return subprocess.run(command, **options)
        except subprocess.TimeoutExpired:
            timed_out.append(True)
            raise

    if scope.left() < MIN_GIT:
        raise _OutOfTime()
    guards = task_git.filter_guards(scope.root, run=run)
    if timed_out or scope.left() < MIN_GIT:
        raise _OutOfTime()
    try:
        return task_git._raw(scope.root, *guards, *args, run=run, text=False)
    except subprocess.TimeoutExpired:
        raise _OutOfTime() from None


def _blame(scope, relative, first, last):
    """({line: sha}, {sha: {author, at, summary}}, problem) from git blame. A file git doesn't track reads as all
    uncommitted; a folder outside git, git failing, or the request's budget running out gives None and says why."""
    try:
        result = _git(scope, 'blame', '--porcelain', '--no-textconv', '-L', f'{first},{last}', '--', relative)
    except _OutOfTime:
        scope.slow = True
        return None, {}, SLOW
    except Rejected as problem:
        return None, {}, str(problem)
    except (OSError, subprocess.SubprocessError):
        return None, {}, 'Git couldn’t run in the project folder.'
    stderr = (result.stderr or b'').decode('utf-8', errors='replace').lower()
    if result.returncode != 0:
        if 'not a git repository' in stderr:
            return None, {}, 'This folder isn’t in a git repository, so only Dervo’s own records can say who wrote a line.'
        if 'no such path' in stderr or 'no such ref' in stderr or 'bad revision' in stderr:
            return {}, {}, None
        return None, {}, 'Git couldn’t trace this file.'
    shas, info, sha = {}, {}, None
    for raw in result.stdout.decode('utf-8', errors='replace').split('\n'):
        if raw.startswith('\t'):
            continue
        parts = raw.split(' ')
        if len(parts) >= 3 and SHA.match(parts[0]) and parts[1].isdigit() and parts[2].isdigit():
            sha = parts[0]
            shas[int(parts[2])] = sha
            info.setdefault(sha, {})
            continue
        key, _, value = raw.partition(' ')
        if sha and key in ('author', 'author-time', 'summary'):
            info[sha][key] = value
    return shas, info, None


def _bodies(scope, shas):
    """{sha: commit message} for these commits, in one git call inside the budget. Empty when time ran out, and the
    answer says so."""
    wanted = [sha for sha in dict.fromkeys(shas) if SHA.match(sha) and sha != ZERO][:SHAS]
    if not wanted:
        return {}
    try:
        result = _git(scope, 'show', '-s', '--format=%H%x1f%B%x1e', *wanted, '--')
    except _OutOfTime:
        scope.slow = True
        return {}
    except (Rejected, OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    bodies = {}
    for record in result.stdout.decode('utf-8', errors='replace').split('\x1e'):
        sha, _, body = record.strip('\n').partition('\x1f')
        if SHA.match(sha):
            bodies[sha] = body
    return bodies


def _task_of(body):
    """The task a Dervo commit names, from its body. The subject is left out: it can carry a thread's name, which a
    proposing agent may have written (security pass, L2)."""
    rest = (body or '').split('\n', 1)[1] if '\n' in (body or '') else ''
    match = TRAILER.search(rest) or TASK_LINE.search(rest)
    return match.group(1) if match else None


def _touches(task, relative, root):
    paths = set(path for path in _dict(task.get('completion')).get('changed_files') or [] if isinstance(path, str))
    paths |= set(path for path in task.get('files') or [] if isinstance(path, str))
    for token in _dicts(task.get('tokens')):
        paths.add(_relative(token.get('path'), root))
    for run in _runs(task):
        paths |= {_relative(path, root) for path in run.get('written') or []}
    return relative in paths


def _writers(scope, relative, only=None):
    """Every recorded change to ``relative``, newest first: the task, its run, the attempt, the lines it added and the
    call that made it. ``only`` keeps one task's changes (a commit named it)."""
    found = []
    for task in ([only] if only else scope.tasks()):
        if not only and time.monotonic() > scope.scan_until:
            scope.incomplete = scope.cut = True
            break
        if not task or not _touches(task, relative, scope.root):
            continue
        attempts = scope.attempts(task['id'])
        for number in sorted(attempts):
            attempt = attempts[number]
            diff = _dict(attempt.get('diff')).get(relative)
            if not isinstance(diff, str) or not diff or diff.startswith('Binary') or attempt.get('kind') == 'restoration':
                continue
            index = _run_of_attempt(task, number)
            run = _runs(task)[index] if _runs(task) else {}
            token = next((t for t in _dicts(task.get('tokens')) if t.get('attempt') == number and t.get('tool') in WRITES
                          and _relative(t.get('path'), scope.root) == relative), None)
            if token is None:
                token = next((t for t in reversed(_tokens(task, run)) if t.get('tool') in ('native:Edit', 'native:Write')
                              and _relative(t.get('path'), scope.root) == relative), None)
            at = _num(token.get('started_at')) if token else None
            at = at or (_window(task, index)[1] if _runs(task) else None)
            before = _dict(attempt.get('before')).get(relative)
            created = not isinstance(before, dict) or not (before.get('bytes_b64') or before.get('stored'))
            found.append(dict(task=task, index=index, run=run, number=number, attempt=attempt, token=token, at=at,
                              added=_added(diff), lines=_wrote(diff), created=created))
    found.sort(key=lambda item: (item['at'] or 0, item['number']), reverse=True)
    return found


def _how(writer):
    token, tool = writer['token'], (writer['token'] or {}).get('tool')
    if tool == 'dervo_write_file':
        return 'through Dervo’s write tool'
    if tool == 'dervo_edit_file':
        return 'through Dervo’s edit tool'
    if tool == 'dervo_create_file':
        return 'through Dervo’s create tool'
    if tool in ('native:Edit', 'native:Write'):
        return f'with Claude Code’s own {tool[7:]} tool, in full access'
    if token is None and writer['attempt'].get('kind') == 'manual-review':
        return 'in full access. Dervo saw the change in git after the run, so it can’t name the call'
    return 'in this run'


def _explain(scope, writer, text):
    """The facts behind one change, for "Why line N is here"."""
    task, index, run = writer['task'], writer['index'], writer['run']
    tokens = _tokens(task, run)
    audit = scope.audit(task['id'], [t.get('token') for t in tokens if t.get('tool') in (GUIDE, CHECK)])
    attempts = scope.attempts(task['id'])
    at = writer['at']
    step = None
    for item in _steps(task, run, [t for t in tokens if t.get('tool') == GUIDE], audit, attempts, scope.root):
        if item['text'] and (at is None or item['at'] is None or item['at'] <= at + 0.001):
            step = dict(text=item['text'], at=item['at'])
    reads, relative = [], _relative((writer['token'] or {}).get('path'), scope.root)
    for token in tokens:
        if token.get('tool') in READS and (at is None or (_num(token.get('started_at')) or 0) <= at):
            name = 'this file' if _relative(token.get('path'), scope.root) == relative else _base(token.get('path'))
            if name and name not in reads:
                reads.append(name)
    recent = len(_recent(_dict(run.get('told'))))
    before = []
    if reads:
        before.append(f'It read {_join(reads[:3])}{" and more" if len(reads) > 3 else ""}.')
    if recent:
        before.append(f'Dervo had told it about recent work in {recent} other thread{"s" if recent != 1 else ""}.')
    if not before and run.get('told') is not None:
        before.append('It read nothing before this change, as far as Dervo’s records show.')
    _, _, ask = _window(task, index)
    asked, came = _asked(scope, task, index, ask)
    checked = None
    for token in tokens:
        started = _num(token.get('started_at'))
        if token.get('tool') == CHECK and token.get('state') == 'delivered' and (at is None or (started or 0) >= at):
            test = _dict(_dict(attempts.get(token.get('attempt'))).get('test')) or _response(audit.get(token.get('token')))
            if test:
                result = _check(test, _num(token.get('ended_at')) or started)
                checked = dict(text=result['text'][:-1] + '. It can’t tell which lines the check ran.', at=result['at'])
                break
    if checked is None:
        own = [c for c in _own(tokens) if at is None or (c['at'] or 0) >= at]
        if own:
            ran = own[0]
            checked = dict(text=f'It ran {"a command it described as “" + ran["command"] + "”" if ran["prose"] else "`" + ran["command"] + "`"} itself afterwards: '
                                f'{"it ended in an error" if ran["error"] else "no error"}. Dervo kept whether it errored, not its output.', at=ran['at'])
        else:
            checked = dict(text='Nothing ran a check in Dervo after this change.', at=None)
    return dict(run=dict(taskId=task['id'], runId=run.get('id'), runIndex=index + 1, channelId=_dict(task.get('context')).get('thread_id'),
                         name=scope.name(task), **_who(scope, task)),
                written=dict(at=at, how=_how(writer)), step=step, before=' '.join(before) or None, asked=asked, origin=came, checked=checked)


def _attribute(writers, text, strict=True):
    if strict and not text.strip():
        return None
    return next((writer for writer in writers if text in writer['added']), None)


def trace_line(scope, path, line):
    absolute, relative = _confine(scope.root, path)
    lines, _ = _read_lines(absolute)
    if line > len(lines):
        raise Rejected(f'{relative} has {len(lines)} lines.')
    text = lines[line - 1]
    scope.scan_until = scope.started + BUDGET * SCAN_SHARE
    shas, info, problem = _blame(scope, relative, line, line)
    sha = (shas or {}).get(line)
    result = dict(path=relative, line=line, text=text[:LINE_CHARS], committed=bool(sha and sha != ZERO), commit=None, run=None,
                  change=None, written=None, step=None, before=None, asked=None, origin=None, checked=None, nobody=None, author=None, note=problem)
    if problem == SLOW:
        return result  # a plain partial answer: nothing here is guessed to fill the time git didn't give
    if sha and sha != ZERO:
        meta = info.get(sha, {})
        body = _bodies(scope, [sha]).get(sha, '')
        if scope.slow:
            result['note'] = SLOW
        task_id = _task_of(body)
        commit = dict(sha=sha, short=sha[:7], author=_text(meta.get('author'), 120),
                      at=_num(int(meta['author-time'])) if meta.get('author-time', '').isdigit() else None,
                      subject=_text(meta.get('summary'), 200), taskId=task_id)
        result['commit'] = commit
        task = scope.task(task_id) if task_id else None
        if task:
            writer = _attribute(_writers(scope, relative, task), text, strict=False)
            named = TASK_LINE.search(body)
            result['change'] = (f'Committed as {sha[:7]} when you approved it in Dervo.' if named and named.group(2) == 'approved'
                                else f'Committed as {sha[:7]} by Dervo.')
            if writer:
                result.update(_explain(scope, writer, text))
            else:
                index = len(_runs(task)) - 1
                start, _, ask = _window(task, index) if _runs(task) else (None, None, None)
                result['run'] = dict(taskId=task['id'], runId=(_runs(task) or [{}])[-1].get('id'), runIndex=index + 1,
                                     channelId=_dict(task.get('context')).get('thread_id'), name=scope.name(task), **_who(scope, task))
                result['asked'], result['origin'] = _asked(scope, task, index, ask)
                result['before'] = 'Dervo’s records for this task don’t show which change added this line.'
            return result
        if task_id:
            result['change'] = f'Committed as {sha[:7]} by Dervo, but this Mac has no record of that task.'
        elif scope.slow:
            result['change'] = f'Committed as {sha[:7]}. Git ran out of time before Dervo could read which task made it.'
            result['author'] = dict(name=commit['author'], at=commit['at'], subject=commit['subject'])
            return result
        else:
            result['change'] = f'Committed as {sha[:7]}.'
        result['nobody'] = None if task_id else NOBODY
        result['author'] = dict(name=commit['author'], at=commit['at'], subject=commit['subject'])
        return result
    scope.scan_until = scope.deadline - 0.5
    writer = _attribute(_writers(scope, relative), text)
    if writer:
        task = writer['task']
        name = scope.name(task)
        if task.get('state') == 'ready_for_review' and writer['index'] == len(_runs(task)) - 1:
            result['change'] = f'Not committed. Part of {name}’s result, waiting for your review.'
        else:
            result['change'] = f'Not committed. Part of {name}’s change.'
        result.update(_explain(scope, writer, text))
        return result
    result['change'] = 'Not committed yet, so git has no author for it.' if shas is not None else None
    if scope.cut:
        result['note'] = 'Dervo ran out of time before it read every record, so it can’t say who wrote this line.'
        return result
    result['nobody'] = NOBODY
    return result


def trace_file(scope, path, line):
    absolute, relative = _confine(scope.root, path)
    lines, cut = _read_lines(absolute)
    total = len(lines)
    if line > total:
        raise Rejected(f'{relative} has {total} lines.')
    scope.scan_until = scope.started + BUDGET * SCAN_SHARE
    writers = _writers(scope, relative)
    # With no line named, the window opens on the newest change a run made here, not on line 1.
    focus = line or next((w['lines'][0] for w in writers if w['lines'] and w['lines'][0] <= total), 0)
    if total == 0:
        first = last = 0
    elif focus:
        first = max(1, min(focus - 20, total - WINDOW + 1))
        last = min(total, first + WINDOW - 1)
    else:
        first, last = 1, min(total, WINDOW)
    shas, info, problem = _blame(scope, relative, first, last) if total else ({}, {}, None)
    bodies = _bodies(scope, list((shas or {}).values())) if shas else {}
    problem = problem or (SLOW if scope.slow else CUT if scope.cut else None)
    by_task = {}
    labels = []
    previous = None
    for n in range(first, last + 1) if total else []:
        text = lines[n - 1]
        sha = (shas or {}).get(n)
        who = None
        if sha and sha != ZERO:
            task_id = _task_of(bodies.get(sha, ''))
            task = by_task.setdefault(task_id, scope.task(task_id)) if task_id else None
            if task:
                writer = _attribute([w for w in writers if w['task']['id'] == task['id']], text, strict=False)
                index = writer['index'] if writer else len(_runs(task)) - 1
                who = dict(key=f'{task["id"]}:{index}', label=scope.name(task), taskId=task['id'], runIndex=index + 1,
                           provider=_dict(task.get('binding')).get('provider'), committed=True, sha=sha[:7])
            else:
                meta = info.get(sha, {})
                who = dict(key=f'git:{sha}', label='Dervo, not on this Mac' if task_id else _text(meta.get('author'), 40) or 'Git',
                           taskId=None, provider=None, committed=True, sha=sha[:7])
        else:
            writer = _attribute(writers, text)
            if writer:
                who = dict(key=f'{writer["task"]["id"]}:{writer["index"]}', label=scope.name(writer['task']), taskId=writer['task']['id'],
                           runIndex=writer['index'] + 1, provider=_dict(writer['task'].get('binding')).get('provider'), committed=False, sha=None)
            elif not text.strip() and previous and not previous['committed']:
                who = previous
            else:
                who = dict(key='none', label='Not committed' if shas is not None else 'Not in Dervo’s records', taskId=None,
                           provider=None, committed=False, sha=None)
        labels.append(who)
        previous = who
    # A blank uncommitted line nobody claims belongs to the change below it when that change added blank lines, so a
    # function and the space before it read as one block.
    for position in range(len(labels) - 1, -1, -1):
        if labels[position]['key'] != 'none' or lines[first + position - 1].strip():
            continue
        below = labels[position + 1] if position + 1 < len(labels) else None
        writer = next((w for w in writers if below and f'{w["task"]["id"]}:{w["index"]}' == below['key']), None)
        if below and not below['committed'] and writer and '' in writer['added']:
            labels[position] = below
    blocks = []
    for n, who in zip(range(first, last + 1), labels):
        if blocks and blocks[-1]['key'] == who['key']:
            blocks[-1]['last'] = n
        else:
            blocks.append(dict(who, first=n, last=n))
    runs, seen = [], set()
    for writer in writers:
        key = (writer['task']['id'], writer['index'])
        if key in seen:
            continue
        seen.add(key)
        span = writer['lines']
        what = 'created the file' if writer['created'] else (f'wrote lines {span[0]} to {span[1]}' if span and span[1] > span[0]
                                                              else f'wrote line {span[0]}' if span else 'changed it')
        runs.append(dict(taskId=writer['task']['id'], runId=writer['run'].get('id'), runIndex=writer['index'] + 1,
                         channelId=_dict(writer['task'].get('context')).get('thread_id'), name=scope.name(writer['task']),
                         provider=_dict(writer['task'].get('binding')).get('provider'), at=writer['at'], what=what,
                         committed=bool(_commit(writer['task'])) and writer['index'] == len(_runs(writer['task'])) - 1))
    for task in scope.tasks():
        if len(runs) >= 8:
            break
        for index, run in enumerate(_runs(task)):
            if (task['id'], index) in seen:
                continue
            read = next((t for t in _tokens(task, run) if t.get('tool') in READS and _relative(t.get('path'), scope.root) == relative), None)
            if read:
                seen.add((task['id'], index))
                runs.append(dict(taskId=task['id'], runId=run.get('id'), runIndex=index + 1, channelId=_dict(task.get('context')).get('thread_id'),
                                 name=scope.name(task), provider=_dict(task.get('binding')).get('provider'), at=_num(read.get('started_at')),
                                 what='read it', committed=None))
    runs.sort(key=lambda item: item['at'] or 0, reverse=True)
    uncommitted = len({(w['task']['id'], w['index']) for w in writers if not _commit(w['task'])})
    folder = relative.rsplit('/', 1)[0] if '/' in relative else None
    return dict(path=relative, name=_base(relative), folder=folder, total=total, first=first, last=last, cut=cut, focus=focus or None,
                lines=[dict(n=n, text=lines[n - 1][:LINE_CHARS]) for n in range(first, last + 1)] if total else [],
                blocks=blocks, runs=runs[:8], changes=len({(w['task']['id'], w['index']) for w in writers}),
                uncommitted=uncommitted, note=problem)


# ---------------------------------------------------------------- the host's entry

def dispatch(host, operation, args, actor):
    if not isinstance(args, dict) or set(args) != FIELDS[operation]:
        raise Rejected('Unsupported history fields')
    scope = _Scope(host, args, actor)
    if operation == 'history_list':
        return history_list(scope, args)
    if operation == 'run_record':
        return run_record(scope, args)
    line = args['line']
    if type(line) is not int or line < 0 or line > 10_000_000:
        raise Rejected('Choose a line number.')
    if operation == 'trace_file':
        return trace_file(scope, args['path'], line)
    if line < 1:
        raise Rejected('Choose a line number.')
    return trace_line(scope, args['path'], line)
