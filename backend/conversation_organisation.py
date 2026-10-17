"""Local metadata only. No provider, content transfer, dispatch or historical scan.

A thread's name and group, kept beside the thread. The name is two to four words about the work (thread_names), set
from the first message unless someone chose one; the group is the workstream the thread was filed under
(project_ideas.file_by_rules), set at the same moment. Both happen every time, from records and rules; Home's model and
the coding agent's Task guide improve them. A name or group set by hand is locked and never replaced.
"""
import json
import re
from dervo_connections import Rejected

SCOPE = {'projectId', 'channelId'}
FIELDS = {'organisation_get': SCOPE, 'organisation_set': SCOPE | {'expectedRevision', 'name', 'group', 'nameLocked', 'groupLocked'},
          'organisation_undo': SCOPE | {'expectedRevision'}}
# Who changed a record, when it wasn't the person: Home's model filing and naming workstreams, and the rules filing a
# thread at its first message. Everything the person does keeps the host's own actor. A workstream whose events carry
# only these actors is Dervo's to rename; one the person made or renamed is not (19 Sep, map F2).
HOME_ACTOR = 'dervo-home'
RULES_ACTOR = 'dervo-rules'
DERVO_ACTORS = frozenset({HOME_ACTOR, RULES_ACTOR})
TOPICS = {'Authentication': {'login', 'authentication', 'signin', 'oauth'},
          'Database': {'database', 'migration', 'schema', 'sqlite'},
          'Tests': {'unittest', 'testing', 'pytest'},
          'Accessibility': {'accessibility', 'keyboard', 'screenreader'},
          'Design': {'design', 'redesign', 'typography', 'styling'},
          'Releases': {'release', 'releases', 'beta', 'installer', 'changelog'}}
MIGRATION = 'short-names-and-filing'  # the one-time tidy of threads named after their first message (beta.8)
TIDY_BUDGET = 2.0  # seconds of tidying per start: the app's first request has 10 s before the bridge ends the backend
TIDY_DAYS = 30  # only a thread active this recently is filed by the tidy; every thread still gets its short name
# Require phrases for ambiguous words such as session, startup and index.
TOPIC_PHRASES = {
    'Authentication': r'\b(?:(?:log|sign)[ -]+in|session\s+(?:expires?|expired|expiry|expiration)|expired\s+session)\b',
    'Performance': r'\b(?:startup\s+(?:delay|latency|time|performance)|slow\s+startup)\b',
    'Search indexing': r'\bsearch\s+index(?:ing)?\b',
    'Search': r'\b(?:search\s+(?:query|input|field|results?|filters?|persistence)|query\s+(?:retention|persistence))\b',
    'Design': r'\b(?:(?:sidebar|composer|dialog|button|layout)\s+(?:spacing|typography|alignment|motion|animation)|(?:reduced|interface)\s+motion)\b',
}

def topic_phrase_matches(topic, pattern, prose):
    for match in re.finditer(pattern, prose, re.I):
        if topic == 'Authentication' and re.fullmatch(r'sign\s+in', match.group(), re.I):
            before, after = prose[:match.start()], prose[match.end():]
            # Physical signs use "in" as a preposition. Keep verb and sign-in UI uses.
            if (re.search(r'\b(?:warning|road|traffic|stop|exit|directional|neon)\s+$', before, re.I)
                    or (re.search(r'\b(?:a|an|the|this|that|another|each|every)\s+$', before, re.I)
                        and re.match(r'\s+(?:a|an|the|this|that|my|our|your)\b', after, re.I))):
                continue
        return True
    return False

def on_side(binding):
    """The thread's account is Work or Personal and on its project's side (``context``, from Store.binding). A binding
    without ``context`` is from before sides, when only personal accounts filed."""
    from dervo_connections import same_side
    return same_side(binding.get('category'), binding.get('context') or 'personal')


class Organisation:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS conversation_organisation (id TEXT PRIMARY KEY, document TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS organisation_submissions (channel_id TEXT NOT NULL, kind TEXT NOT NULL, source_id TEXT NOT NULL,
            PRIMARY KEY(channel_id, kind, source_id));
          CREATE TABLE IF NOT EXISTS organisation_migrations (name TEXT PRIMARY KEY, at REAL NOT NULL);
        ''')

    def initial(self, channel_id, automatic=False):
        name = self.db.execute('SELECT name FROM local_channels WHERE id=?', (channel_id,)).fetchone()['name']
        state = dict(name=name, group=None, nameLocked=not automatic, groupLocked=False, revision=0,
                     reason='Waiting for a submitted request.', source=None, suggestion=None, undo=None)
        self.db.execute('INSERT OR IGNORE INTO conversation_organisation VALUES (?,?)', (channel_id, json.dumps(state)))
        return state

    def read(self, channel_id):
        row = self.db.execute('SELECT document FROM conversation_organisation WHERE id=?', (channel_id,)).fetchone()
        if row:
            state = json.loads(row['document'])
            # A name changed by another host path is a human choice, never a disposable generated title.
            name = self.db.execute('SELECT name FROM local_channels WHERE id=?', (channel_id,)).fetchone()['name']
            if name != state['name']:
                if state.get('namedBy') == 'router' and not state.get('titledBy') and not state.get('nameLocked'):
                    import thread_router  # renamed from the sidebar: the router's evaluation set learns from it
                    thread_router.outcome_for(self.db, channel_id, 'name', 'renamed:' + name)
                state.update(name=name, nameLocked=True, undo=None, revision=state['revision']+1)
                state.pop('namedBy', None)
                self.write(channel_id, state)
            return state
        return self.initial(channel_id)

    def write(self, channel_id, state):
        self.db.execute('UPDATE conversation_organisation SET document=? WHERE id=?', (json.dumps(state), channel_id))
        self.db.execute('UPDATE local_channels SET name=? WHERE id=?', (state['name'], channel_id))
        import conversation_activity
        conversation_activity.bump(self.db, channel_id)  # the new name reaches the sidebar with the next push

    def dispatch(self, operation, args, actor='local-user'):
        if not isinstance(args, dict) or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported organisation fields')
        if any(type(args[k]) is not str or not args[k] for k in SCOPE):
            raise Rejected('Choose the exact project and conversation')
        binding = self.store.binding(args['channelId'], args['projectId'], actor=actor)
        # Work and Personal both file, each only on its own project's side.
        if not on_side(binding):
            raise Rejected('Organisation is available only for an account on this project’s side, Work or Personal')
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            state = self.read(binding['id'])
            if operation != 'organisation_get':
                if type(args['expectedRevision']) is not int or args['expectedRevision'] != state['revision']:
                    raise Rejected('Organisation changed. Refresh before saving or undoing.')
                old = {k:v for k,v in state.items() if k != 'undo'}
                if operation == 'organisation_undo':
                    if state['undo'] is None:
                        raise Rejected('No current change to undo')
                    state = {**state['undo'], 'revision':state['revision']+1, 'undo':None,
                             'nameLocked':True, 'groupLocked':True, 'reason':'Change undone. Automatic changes are locked.',
                             'source':{'kind':'manual-undo'}, 'suggestion':None}
                    state.pop('filedBy', None)
                    if state['group'] != old['group']:
                        # Undoing a filing takes the thread out of the workstream too, so the sidebar and the ideas agree.
                        from project_ideas import sync_membership
                        sync_membership(self.db, binding['id'], state['group'])
                else:
                    for key, limit in [('name',100), ('group',60)]:
                        value = args[key]
                        if key == 'group' and value is None:
                            continue
                        if type(value) is not str or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
                            raise Rejected('Choose a name up to 100 characters and a group up to 60 characters')
                    if any(type(args[k]) is not bool for k in ('nameLocked','groupLocked')):
                        raise Rejected('Locks must be explicit booleans')
                    if binding['visibility'] == 'private' and args['group'] is not None:
                        raise Rejected('Private conversations stay ungrouped')
                    state.update({k:args[k] for k in ('name','group','nameLocked','groupLocked')})
                    # A corrected title is always a human choice. Unlock separately without changing the title.
                    if state['name'] != old['name']:
                        state['nameLocked'] = True
                    if state['group'] != old['group']:
                        state['groupLocked'] = True
                        state.pop('filedBy', None)  # a group set by hand or by Home is no longer the rules' filing
                        # Who chose it, kept through later messages (``source`` moves to each one): the rules file a
                        # new thread by the names of the threads you filed yourself before any topic guess.
                        state['groupedBy'] = 'home' if actor == HOME_ACTOR else 'you'
                    if all(state[k] == old[k] for k in ('name', 'group', 'nameLocked', 'groupLocked')):
                        return self.view(binding, state)
                    # Home's filing goes through this same save, so the record says it was Home and not you (a row
                    # note in the sidebar reads that to say who filed it, and Home's own line offers Undo).
                    if actor == HOME_ACTOR:
                        state.update(revision=state['revision']+1, undo=old, reason='Filed by Home. Undo puts it back.', source={'kind':'home'}, suggestion=None)
                    else:
                        state.update(revision=state['revision']+1, undo=old, reason='Manual organisation saved.', source={'kind':'manual'}, suggestion=None)
                        if state['name'] != old['name'] and old.get('namedBy') == 'router' and not old.get('titledBy'):
                            # What you called a thread Dervo's router named: its evaluation set learns from it.
                            import thread_router
                            thread_router.outcome_for(self.db, binding['id'], 'name', 'renamed:' + state['name'])
                        if state['name'] != old['name']:
                            state.pop('namedBy', None)  # the router's name is gone: a later rename isn't about it
                self.write(binding['id'], state)
            return self.view(binding, state)

    def view(self, binding, state):
        peers = []
        if state['group'] and binding['visibility'] == 'shared':
            rows = self.db.execute('''SELECT t.id,c.name,o.document FROM threads t JOIN local_channels c ON c.id=t.id
              JOIN conversation_organisation o ON o.id=t.id WHERE t.project_id=? AND t.profile_id=?
              AND t.visibility='shared' AND t.id!=? ORDER BY t.id''', (binding['project_id'],binding['profile_id'],binding['id']))
            peers = [dict(id=r['id'],name=r['name']) for r in rows if json.loads(r['document'])['group'] == state['group']]
        return {**{k:v for k,v in state.items() if k != 'undo'}, 'canUndo':state['undo'] is not None, 'groupAllowed':binding['visibility']=='shared', 'peers':peers}

    def submitted(self, binding, kind, source_id, text, filer=None):
        """Called only after the owning submit path commits. Replay of that saved ID is a no-op.

        The first message names the thread (a short name from thread_names) and files it: ``filer(binding, text,
        name)`` is project_ideas.file_by_rules, which picks or makes the workstream inside this transaction and answers
        with its name. Naming and filing land as one revision, so one undo takes both back. A private thread gets its
        name (its own, seen by its owner) and never a group: a workstream is a shared heading in the sidebar.
        """
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM local_channels WHERE id=?', (binding['id'],)).fetchone():
                return
            if self.db.execute('SELECT 1 FROM organisation_submissions WHERE channel_id=? AND kind=? AND source_id=?',
                               (binding['id'],kind,source_id)).fetchone():
                return
            state = self.read(binding['id'])
            old = {k:v for k,v in state.items() if k != 'undo'}
            title, group, reason = suggest(text)
            filed = False
            if not on_side(binding):
                title, group, reason = None, None, 'Account excluded: automatic organisation is off.'
            elif binding['visibility'] != 'shared':
                group, reason = None, 'Private conversation: named from your request, never filed.'
            name_applied = group_applied = False
            if title and not state['nameLocked'] and state['name'] == 'New conversation':
                routed = self.router_name(binding['id'], binding['project_id'], text) if binding['visibility'] == 'shared' else None
                state['name'], name_applied = routed or title, True
                if routed:
                    state['namedBy'] = 'router'
            if (filer is not None and on_side(binding) and binding['visibility'] == 'shared'
                    and not state['groupLocked'] and state['group'] is None):
                # Rules first: an existing workstream by overlap, a topic, a follow-up, or a new one under the cap.
                group = filer(binding, text, state['name'], RULES_ACTOR) or group
                filed = bool(group)
            if group and not state['groupLocked'] and state['group'] is None:
                state['group'], group_applied = group, True
                if filed:
                    state['groupLocked'], state['filedBy'] = True, 'rules'
            state.update(reason=reason, source={'kind':kind,'id':source_id},
                         suggestion={'name':title,'group':group,'nameApplied':name_applied,'groupApplied':group_applied} if title or group else None)
            # Observation alone mustn't erase the undo for the most recent actual change.
            if name_applied or group_applied:
                state.update(revision=state['revision']+1, undo=old)
            self.write(binding['id'], state)
            self.db.execute('INSERT INTO organisation_submissions VALUES (?,?,?)', (binding['id'],kind,source_id))

    def take_title(self, channel_id, title, source):
        """The coding agent's own title for the thread, from its Task guide, taken once: when the name is still the
        rules' (not chosen by hand, not already an agent's) and differs. True when the thread was renamed.

        Once means once for the thread, not once per request: ``titledBy`` records that an agent named it and stays
        through every later message (``source`` moves to each new message, so it can't carry that). A name you set by
        hand is locked and never replaced either way."""
        from thread_names import tidy_title
        title = tidy_title(title)
        if not title:
            return False
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM local_channels WHERE id=?', (channel_id,)).fetchone():
                return False
            state = self.read(channel_id)
            if state['nameLocked'] or state.get('titledBy') or state['name'] == title:
                return False
            old = {k: v for k, v in state.items() if k != 'undo'}
            state.pop('namedBy', None)
            state.update(name=title, titledBy='guide', revision=state['revision'] + 1, undo=old, source=source, suggestion=None,
                         reason='Named by the coding agent from its Task guide.')
            self.write(channel_id, state)
            return True

    def router_name(self, thread_id, project_id, text, current='New conversation'):
        """Dervo's router's name for a thread from its first message (thread_router.name), or None: only where the
        rules would name it too (the caller checks: no lock, no agent's title, on its project's side, shared), only at
        NAME_APPLY or above, with only same-side thread names alongside, and never where the rules abstain (a key, a path or a URL in the message) or the name
        itself would be refused. Every decision is logged."""
        import thread_router
        if thread_router.module() is None or not suggest(text)[0]:
            return None
        record = dict(id=thread_id, name=current, nameLocked=False, titledBy=None, visibility='shared',
                      messages=[text[:thread_router.TEXT_LIMIT]])
        side = thread_router.side_threads(self.db, project_id)
        others = [dict(id=row[0], name=row[1]) for row in self.db.execute(
            'SELECT t.id, c.name FROM threads t JOIN local_channels c ON c.id=t.id WHERE t.project_id=? AND t.id<>? '
            "AND t.visibility='shared' ORDER BY t.rowid DESC", (project_id, thread_id)) if row[0] in side][:200]
        decision = thread_router.name(record, [row['name'] for row in others])
        if decision is None:
            return None
        applied = decision['confidence'] >= thread_router.name_threshold() and suggest(decision['title'])[0] is not None
        thread_router.log(self.db, project_id, 'name', decision, dict(thread=record, others=others), thread_id=thread_id, applied=applied)
        return decision['title'] if applied else None

    def unfile(self, channel_id, actor, reason, only_from=None):
        """A thread whose workstream was archived: its group goes and its lock goes with it, so the rules file it again
        at its next message and Home may file it meanwhile. Archiving a workstream is a decision about the workstream,
        not about where its threads belong (19 Sep, map F3). One revision, so Undo puts the group back. Nothing
        happens to a thread that carries no group, or (with ``only_from``) a group other than that name."""
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM local_channels WHERE id=?', (channel_id,)).fetchone():
                return False
            state = self.read(channel_id)
            if state['group'] is None or (only_from is not None and state['group'] != only_from):
                return False
            old = {k: v for k, v in state.items() if k != 'undo'}
            state.update(group=None, groupLocked=False, revision=state['revision'] + 1, undo=old, suggestion=None,
                         reason=reason, source={'kind': 'archive', 'actor': actor})
            state.pop('filedBy', None)
            state.pop('groupedBy', None)
            self.write(channel_id, state)
            return True

    def tidy_once(self, ideas, budget=TIDY_BUDGET):
        """Threads from before short names: any whose name is still its first message (long, a sentence, or cut with an
        ellipsis) and not chosen by hand gets the short name, and any shared thread in no workstream and active in the
        last TIDY_DAYS gets filed by the same rules a new one meets. Runs once per workspace, recorded in
        organisation_migrations; a workspace that goes back to beta.7 keeps the short names, the filings and each
        thread's undo, and finds no table it doesn't know.

        Both come from the thread's current name and nothing else. That name is beta.7's title for it, the first
        message's opening, and it goes through suggest() like a typed first message, so a key, a path or a URL in it
        abstains. A thread's messages are never read here: ``source`` points at the newest message, not the first.

        Bounded, because it runs before the app's first request and the bridge ends the backend at 10 s: after about
        ``budget`` seconds of work it stops, commits what it did, and the next start carries on (a thread already
        named or filed drops out of the work). The marker lands only once every thread has been seen, so a partial run
        never counts as done. Each project's workstreams and their words are read once (project_ideas.FilingView), so
        the cost grows with the threads, not their square.
        """
        import time
        from project_ideas import FilingView
        from thread_names import first_message_name
        started = time.monotonic()
        with self.db:
            if not self.db.in_transaction:
                self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT 1 FROM organisation_migrations WHERE name=?', (MIGRATION,)).fetchone():
                return None
            archived = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='conversation_archives'").fetchone()
            from dervo_connections import ON_PROJECT_SIDE
            rows = self.db.execute('''SELECT t.id, t.project_id, t.profile_id, t.visibility, p.category, o.document,
                (''' + ON_PROJECT_SIDE + ''') AS on_side
                FROM threads t JOIN local_channels c ON c.id=t.id JOIN profiles p ON p.id=t.profile_id
                JOIN conversation_organisation o ON o.id=t.id ''' + (
                'WHERE t.id NOT IN (SELECT thread_id FROM conversation_archives) ' if archived else '') + 'ORDER BY t.rowid').fetchall()
            active = self.active_since(time.time() - TIDY_DAYS * 86400)
            views, renamed, filed, finished = {}, [], [], True
            for index, row in enumerate(rows):
                # Threads on their project's own side, Work or Personal; one whose account is on the other side is left.
                if row['category'] not in ('personal', 'work') or not row['on_side']:
                    continue
                state = self.read(row['id'])
                if state['name'] == 'New conversation':
                    continue  # nothing on record to name or file from; its next message will
                rename = not state['nameLocked'] and first_message_name(state['name'])
                file = (row['visibility'] == 'shared' and state['group'] is None and not state['groupLocked']
                        and ideas is not None and row['id'] in active)
                if not rename and not file:
                    continue
                old = {k: v for k, v in state.items() if k != 'undo'}
                text = first_message_text(state['name'])
                changed = False
                if rename:
                    title = suggest(text)[0]
                    routed = self.router_name(row['id'], row['project_id'], text, state['name']) if title and row['visibility'] == 'shared' else None
                    title = routed or title
                    if title and title != state['name']:
                        state['name'], changed = title, True
                        state.pop('namedBy', None)
                        if routed:
                            state['namedBy'] = 'router'
                        renamed.append((row['id'], old['name'], title))
                if file:
                    scope = dict(project_id=row['project_id'], profile_id=row['profile_id'])
                    view = views.get((row['project_id'], row['profile_id']))
                    if view is None:
                        view = views[(row['project_id'], row['profile_id'])] = FilingView(ideas, scope)
                    group = ideas.file_by_rules(dict(id=row['id'], **scope), text, state['name'], RULES_ACTOR, view=view)
                    if group:
                        state.update(group=group, groupLocked=True, filedBy='rules')
                        changed = True
                        filed.append((row['id'], group))
                if changed:
                    state.update(revision=state['revision'] + 1, undo=old, source={'kind': 'migration', 'id': MIGRATION},
                                 suggestion=None, reason='Named and filed from the first message when Dervo started.')
                    self.write(row['id'], state)
                    if index + 1 < len(rows) and time.monotonic() - started > budget:
                        finished = False
                        break
            if finished:
                self.db.execute('INSERT INTO organisation_migrations VALUES (?,?)', (MIGRATION, time.time()))
            return dict(renamed=renamed, filed=filed, finished=finished)

    def active_since(self, cutoff):
        """Ids of the threads with a message, a task change or a visit since ``cutoff``."""
        found = {row[0] for row in self.db.execute(
            'SELECT DISTINCT m.thread_id FROM local_projection p JOIN messages m ON m.id=p.id WHERE p.created_at >= ?', (cutoff,))}
        for table, column in (('conversation_task_state', 'changed'), ('conversation_seen', 'seen')):
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                found.update(row[0] for row in self.db.execute(f'SELECT DISTINCT thread_id FROM {table} WHERE {column} >= ?', (cutoff,)))
        return found


def first_message_text(name):
    """A beta.7 thread name as text to name and file from: the first message's opening, without the ellipsis the cut
    left on it."""
    return re.sub(r'\s*(?:…|\.{3})\s*$', '', name or '').strip()


# These are privacy/ambiguity exclusions, not a topic vocabulary for naming. "Rename" and "private" were here until
# 19 Sep (map F9): they left "Rename the sidebar button to Archive" and "Make private threads look different" as "New
# conversation", unfiled, and neither word says anything about the person's secrets. "Ignore" and "instructions" stay:
# a pasted instruction is never a name.
# "Key", "keys", "token", "tokens" and "apikey" left on 19 Sep (auto-sorting review #2): "Fix the key handling in the
# composer" and "Show token usage per thread" stayed "New conversation". The value itself never reaches a name without
# them: thread_names._words marks a key-shaped token (looks_secret) and any identifier within two words of key or token
# as a secret, and _pick never takes a secret. A value handed over in words ("the key is hunter", "token: swordfish",
# "key = ...") still abstains whole (KEY_GIVEN), as every "key" did before. A short password reads as a word ("the
# password is hunter2"), so password, secret and credential still abstain.
SENSITIVE = {'password', 'passwords', 'secret', 'secrets', 'credential',
             'credentials', 'ssn', 'confidential', 'medical', 'salary',
             'ignore', 'instructions'}
# "sidebar/composer": one slash between two plain words is two words. A path (leading slash, two slashes, a dot) is not.
KEY_GIVEN = re.compile(r"\b(?:api[ _-]?)?(?:keys?|tokens?|apikey)\b\s*(?::|=|\b(?:is|was|are|were)\b)", re.I)
# "Cmd+K", "C++", "#mention" and "#3" are ordinary; a long run glued to + or # is more likely a pasted value.
GLUED = re.compile(r'[\w-]{8,}[+#]|[+#][\w-]{8,}')
WORD_SLASH = re.compile(r'(?<![\w./~\\-])([^\W\d_]+)/([^\W\d_]+)(?![\w/\\.-])')

def suggest(text):
    """A short name and a topic group for a first message. Never summarizes code or inspects earlier messages.

    Titles don't require a known topic. Groups still require one unambiguous topic; the workstream filing in
    project_ideas uses that topic as one of its steps. This heuristic isn't a sensitive-content detector.
    """
    abstained = (None, None, 'Abstained: text is ambiguous, sensitive or outside the local naming rules.')
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        return abstained
    # macOS text substitution changes quote typography, not the request's content.
    text = text.strip().translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'"}))
    words = re.findall(r"[^\W\d_]+", text.casefold())
    # Relative source references can identify a task without exposing directory names.
    # Absolute paths, parent traversal, URLs and arbitrary inline code still abstain.
    path_pattern = r'(?<![\w./~\\])(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.(?:tsx?|jsx?|py|rs|css|html|md|json|toml)\b'
    def filename(match): return match.group().rsplit('/', 1)[-1]
    text = re.sub(r'`('+path_pattern+r')`', lambda m: m.group(1).rsplit('/', 1)[-1], text)
    text = re.sub(path_pattern, filename, text)
    text = WORD_SLASH.sub(r'\1 \2', text)
    # Check the whole request, including text that won't fit in the sidebar title. "20%", "Cmd+K" and "#mention" are
    # ordinary; = $ ; braces, angle brackets, backslashes, URLs and absolute paths still abstain.
    if (set(words) & SENSITIVE or KEY_GIVEN.search(text) or GLUED.search(text) or re.search(r'https?://|www\.|[@/\\`{}\[\]=<>;$]|\d{5}', text)
            or any(not (c.isalnum() or c.isspace() or c in ",.?!:()-_'’\"&%+#") for c in text)
            or any(ord(c) < 32 and c not in '\n\r\t' for c in text)):
        return abstained
    if re.search(r"\w\(|(?im:^[ \t]*(?:def|class|import|from|return|const|let|var)\b)|(?i:\bselect\b.+\bfrom\b)", text):
        return abstained
    prose = ' '.join(text.split())
    # Two to four words about the work, never the sentence itself. A setup line ("Read PLAN.md first"), a status line
    # ("The connection is now ready") and the preamble ("I think it would be good to") fall away inside short_name.
    from thread_names import short_name
    title = short_name(prose)
    if not title:
        return abstained
    group_words = re.findall(r"[^\W\d_]+", prose.casefold())
    hits = {topic for topic, tokens in TOPICS.items() if tokens.intersection(group_words)}
    hits.update(topic for topic, pattern in TOPIC_PHRASES.items() if topic_phrase_matches(topic, pattern, prose))
    if 'Search indexing' in hits: hits.discard('Search')
    group = next(iter(hits)) if len(hits) == 1 else None
    return title, group, ('Local title extracted from submitted prose. ' +
                          ('Group uses one matching topic within this project and account.' if group else
                           'No unambiguous local group match.'))
