"""Dervo's local-only conversation boundary. No credentials are stored here."""
import json
import os
from pathlib import Path
import sqlite3
import uuid
import platform_os


class Rejected(ValueError):
    """The requested action isn't safe or doesn't match its binding."""


CONTEXTS = ('personal', 'work')


def account_context(db, profile_id):
    """'personal' or 'work': the category the account was added under."""
    row = db.execute('SELECT category FROM profiles WHERE id=?', (profile_id,)).fetchone()
    if row is None:
        raise Rejected('Unknown account profile')
    return row[0]


def project_context(db, project_id):
    """'personal' or 'work': what the project was marked when it was made. Never read from whichever account it has now."""
    # A row written straight to the table since this store opened (only tests and old fixtures do) has no mark yet; it
    # gets the one the next open would give it, from the account it was made with.
    row = db.execute('SELECT COALESCE(context, (SELECT category FROM profiles WHERE profiles.id=projects.profile_id)) '
                     'FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None:
        raise Rejected('Unknown project')
    if row[0] not in CONTEXTS:
        raise Rejected('This project isn’t marked Work or Personal, so no account can run it')
    return row[0]


def same_side(*categories):
    """Whether every category given is Work or Personal and they're all the same one. The rule every read across
    threads, notes, rules and records follows: nothing crosses between a work and a personal account."""
    return bool(categories) and all(c in CONTEXTS for c in categories) and len(set(categories)) == 1


# A profiles row that could be the same sign-in as an organisation (two parameters, both the org): either side not
# known yet, or the same one. Beta.18: one email can be in several Claude organisations.
SAME_ORG = '(org IS NULL OR ? IS NULL OR org=?)'

# SQL: the thread aliased ``t`` is on an account (``p``) on its project's side. For queries that read threads directly.
ON_PROJECT_SIDE = ("p.category=(SELECT COALESCE(pr.context, (SELECT category FROM profiles WHERE id=pr.profile_id)) "
                   "FROM projects pr WHERE pr.id=t.project_id)")


def context_words(context):
    return 'a Work project' if context == 'work' else 'a Personal project'


def require_context(db, context, profile_id):
    """A work account only for a Work project and a personal one only for a Personal project. Never a fallback."""
    if context not in CONTEXTS:
        raise Rejected('This project isn’t marked Work or Personal, so no account can run it')
    category = account_context(db, profile_id)
    if category != context:
        raise Rejected(f'This is {context_words(context)}, so only a {context} account can run it. '
                       f'That’s a {category} account. Nothing was sent.')
    return category


class Store:
    """Single-host SQLite store. The host must own and serialize its lifetime."""
    def __init__(self, path):
        path = Path(path).absolute()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if (path.parent / '.dervo-restore-pending.json').exists():
            raise Rejected('A workspace restore was interrupted. Recover it before opening Dervo; your saved backups are retained.')
        self.lock_fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | platform_os.NOFOLLOW, 0o600)
        try:
            platform_os.lock_nowait(self.lock_fd)
        except BlockingIOError:
            os.close(self.lock_fd)
            raise Rejected('Another Dervo host owns this database') from None
        if (path.parent / '.dervo-restore-pending.json').exists() or (path.parent / '.dervo-restore-pending.json').is_symlink():
            os.close(self.lock_fd)
            raise Rejected('A workspace restore was interrupted. Recover it before opening Dervo; your saved backups are retained.')
        if path.is_symlink():
            os.close(self.lock_fd)
            raise Rejected('Database must not be a symlink')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | platform_os.NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY, provider TEXT NOT NULL CHECK(provider IN ('claude','codex')),
            home TEXT UNIQUE NOT NULL, identity TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('personal','work')));
          CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, cwd TEXT NOT NULL,
            profile_id TEXT NOT NULL REFERENCES profiles(id), model TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
            channel_id TEXT NOT NULL, profile_id TEXT NOT NULL REFERENCES profiles(id),
            model TEXT NOT NULL, visibility TEXT NOT NULL CHECK(visibility IN ('private','shared')),
            owner TEXT, session_id TEXT, CHECK(visibility='shared' OR owner IS NOT NULL));
          CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id),
            client_id TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user', UNIQUE(thread_id, client_id));
          CREATE TABLE IF NOT EXISTS turns (
            id TEXT PRIMARY KEY REFERENCES messages(id), thread_id TEXT NOT NULL REFERENCES threads(id),
            state TEXT NOT NULL, provider_turn_id TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS items (
            turn_id TEXT NOT NULL REFERENCES turns(id), item_id TEXT NOT NULL,
            text TEXT NOT NULL, PRIMARY KEY(turn_id,item_id));
          CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, turn_id TEXT NOT NULL REFERENCES turns(id),
            kind TEXT NOT NULL, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS reviews (
            turn_id TEXT PRIMARY KEY REFERENCES turns(id), decision TEXT NOT NULL, actor TEXT NOT NULL);
        ''')
        # Accounts added before 17 September 2026 are subscription sign-ins with no spend limit.
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(profiles)')}
        if 'method' not in columns:
            self.db.execute("ALTER TABLE profiles ADD COLUMN method TEXT NOT NULL DEFAULT 'subscription' CHECK(method IN ('subscription','api_key'))")
        if 'monthly_limit_usd' not in columns:
            self.db.execute('ALTER TABLE profiles ADD COLUMN monthly_limit_usd REAL')
        # The name you give an account on Connected accounts ("Personal", "Acme"), 19 Sep 2026. Null until
        # you name it, and then every screen falls back to the same default (accountLabel in the renderer).
        if 'name' not in columns:
            self.db.execute('ALTER TABLE profiles ADD COLUMN name TEXT')
        # Beta.18 (Bryce, 7 Oct 2026: "I can't really remove accounts"): a removed account keeps its row, marked here, so
        # the projects and threads that used it still point at it and say "pick an account" rather than move anywhere on
        # their own. Nothing runs on it again (connections_ui.owned_profile) and no list offers it (account_signin.py).
        if 'removed_at' not in columns:
            self.db.execute('ALTER TABLE profiles ADD COLUMN removed_at INTEGER')
        # Beta.18 (Bryce, 8 Oct 2026: a personal Max and a Team seat on one email "say both accounts are the same"): a
        # Claude account is its email and its organisation. ``org`` is Claude Code's ``auth status`` orgId, recorded when
        # the account is added or, for one added before this, by its first good check (Host._checked). Null takes any
        # organisation until then (connections_ui.same_org). ``org_name`` is only for showing.
        if 'org' not in columns:
            self.db.execute('ALTER TABLE profiles ADD COLUMN org TEXT')
        if 'org_name' not in columns:
            self.db.execute('ALTER TABLE profiles ADD COLUMN org_name TEXT')
        # Work or Personal belongs to the project, not only to its account (Bryce, 26 Sep 2026: "keep personal/work
        # contexts separate", with both sides working). Set once when the project is made, from its account until then,
        # so signing an account out or removing it can never turn a work project personal.
        if 'context' not in {row[1] for row in self.db.execute('PRAGMA table_info(projects)')}:
            self.db.execute("ALTER TABLE projects ADD COLUMN context TEXT CHECK(context IN ('personal','work'))")
        self.db.execute('UPDATE projects SET context=(SELECT category FROM profiles WHERE profiles.id=projects.profile_id) WHERE context IS NULL')
        # Beta.18: a thread's own branch and folder (thread_folders.py). New table, so opening an older store adds it.
        import thread_folders
        thread_folders.schema(self.db)
        self.db.commit()
        with self.db:
            for row in self.db.execute("SELECT id FROM turns WHERE state IN ('sending','running','stopping')").fetchall():
                data = {'state': 'uncertain', 'error': 'Host restarted before terminal confirmation'}
                self.db.execute('INSERT INTO events(turn_id,kind,data) VALUES (?,?,?)', (row['id'], 'terminal', json.dumps(data)))
            self.db.execute("UPDATE turns SET state='uncertain',error='Host restarted before terminal confirmation' WHERE state IN ('sending','running','stopping')")

    def turn(self, id):
        row = self.db.execute('SELECT * FROM turns WHERE id=?', (id,)).fetchone()
        if not row:
            raise Rejected('Unknown turn')
        return dict(row)

    def claim(self, id):
        """Commit sending before touching a provider. Never retry uncertainty."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            t = self.turn(id)
            busy = self.db.execute("SELECT 1 FROM turns WHERE thread_id=? AND state IN ('sending','running','stopping','uncertain')", (t['thread_id'],)).fetchone()
            if t['state'] != 'queued' or busy:
                raise Rejected('Conversation is busy or needs delivery reconciliation')
            self.db.execute("UPDATE turns SET state='sending' WHERE id=?", (id,))
            self.db.execute('INSERT INTO events(turn_id,kind,data) VALUES (?,?,?)', (id, 'sending', '{}'))

    def record(self, id, kind, data):
        """Persist normalized provider events before notifying the UI."""
        with self.db:
            t = self.turn(id)
            if t['state'] not in ('sending', 'running', 'stopping'):
                raise Rejected('Turn is not active')
            if kind == 'session':
                sid = data['session_id']
                old = self.db.execute('SELECT session_id FROM threads WHERE id=?', (t['thread_id'],)).fetchone()[0]
                if not sid or (old and old != sid):
                    raise Rejected('Provider changed the saved conversation ID')
                self.db.execute('UPDATE threads SET session_id=? WHERE id=?', (sid, t['thread_id']))
            elif kind == 'started':
                if not data['turn_id'] or (t['provider_turn_id'] and t['provider_turn_id'] != data['turn_id']):
                    raise Rejected('Provider changed the turn ID')
                self.db.execute("UPDATE turns SET state='running',provider_turn_id=? WHERE id=?", (data['turn_id'], id))
            elif kind in ('delta', 'snapshot'):
                old = self.db.execute('SELECT text FROM items WHERE turn_id=? AND item_id=?', (id, data['item_id'])).fetchone()
                text = ((old[0] if old else '') + data['text']) if kind == 'delta' else data['text']
                if len(text.encode()) > 2 * 1024 * 1024:
                    raise Rejected('Response item exceeded 2 MiB')
                self.db.execute('INSERT INTO items VALUES (?,?,?) ON CONFLICT(turn_id,item_id) DO UPDATE SET text=excluded.text', (id, data['item_id'], text))
            elif kind == 'stopping':
                self.db.execute("UPDATE turns SET state='stopping' WHERE id=?", (id,))
            elif kind == 'terminal':
                if data['state'] not in ('completed', 'interrupted', 'failed', 'uncertain'):
                    raise Rejected('Unknown terminal state')
                error = data.get('error')
                if error is not None and not isinstance(error, str):
                    error = json.dumps(error)
                self.db.execute('UPDATE turns SET state=?,error=? WHERE id=?', (data['state'], error, id))
                for item in self.items(id):
                    self.db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?,?)',
                                    (id + ':' + item['item_id'], t['thread_id'], id + ':' + item['item_id'], 'provider', item['text'], 'assistant'))
            elif kind not in ('approval_denied', 'usage', 'error', 'native_tool'):
                # native_tool: a Codex web search in a chat turn (PLAN-native A5), kept as an event beside the answer.
                raise Rejected('Unsupported provider event')
            self.db.execute('INSERT INTO events(turn_id,kind,data) VALUES (?,?,?)', (id, kind, json.dumps(data)))

    def reconcile_codex(self, id, project_id, *, actor, verified_binding, history):
        """Trusted host only: apply a fresh, authenticated thread/read response.

        This validates correlation, not provenance. Never accept these arguments
        from a renderer. The live host acquires history after fresh identity checks.
        """
        t = self.turn(id)
        binding = self.binding(t['thread_id'], project_id, actor=actor)
        keys = ('id', 'project_id', 'profile_id', 'provider', 'home', 'identity',
                'category', 'model', 'cwd', 'session_id')
        if (t['state'] != 'uncertain' or binding['provider'] != 'codex'
                or not binding['session_id']
                or any(verified_binding.get(k) != binding[k] for k in keys)):
            raise Rejected('Recovery requires the same verified binding and uncertain turn')
        if len(json.dumps(history).encode()) > 8 * 1024 * 1024:
            raise Rejected('Recovery history exceeded 8 MiB')
        thread = history.get('thread', {})
        if (thread.get('id') != binding['session_id'] or thread.get('cwd') != binding['cwd']
                or thread.get('modelProvider') != 'openai'):
            raise Rejected('Recovery history belongs to another conversation')
        turns = thread.get('turns', [])
        if t['provider_turn_id']:
            matches = [turn for turn in turns if turn.get('id') == t['provider_turn_id']]
        else:
            message=self.db.execute('SELECT client_id,text FROM messages WHERE id=?',(id,)).fetchone()
            def correlated(turn):
                return any(item.get('type')=='userMessage' and item.get('clientId')==message['client_id']
                    and item.get('content') in ([{'type':'text','text':message['text']}],
                        [{'type':'text','text':message['text'],'text_elements':[]}])
                    for item in turn.get('items',[]))
            matches=[turn for turn in turns if correlated(turn)]
        if len(matches) != 1 or any(turn.get('status') == 'inProgress' for turn in turns):
            raise Rejected('Recovery needs one matching terminal turn and no active turn')
        recovered = matches[0]
        if not isinstance(recovered.get('id'),str) or not recovered['id']:
            raise Rejected('Recovery needs an exact provider turn ID')
        if (recovered.get('status') not in ('completed', 'interrupted', 'failed')
                or recovered.get('itemsView', 'full') != 'full'
                or not isinstance(recovered.get('items'), list)):
            raise Rejected('Recovery needs terminal status and full item history')
        items, seen = [], set()
        for item in recovered['items']:
            if item.get('type') != 'agentMessage':
                continue
            key, text = item.get('id'), item.get('text')
            if (not isinstance(key, str) or not key or key in seen or not isinstance(text, str)
                    or len(text.encode()) > 2 * 1024 * 1024):
                raise Rejected('Recovery contains invalid or duplicate response items')
            seen.add(key)
            items.append({'item_id': key, 'text': text})
        data = {'source': 'codex.thread/read', 'session_id': binding['session_id'],
                'provider_turn_id': recovered['id'], 'state': recovered['status'],
                'error': recovered.get('error'), 'items': items}
        with self.db:
            for old in self.items(id):
                self.db.execute("DELETE FROM messages WHERE thread_id=? AND client_id=? AND role='assistant'",
                                (t['thread_id'], id + ':' + old['item_id']))
            self.db.execute('DELETE FROM items WHERE turn_id=?', (id,))
            for item in items:
                self.db.execute('INSERT INTO items VALUES (?,?,?)', (id, item['item_id'], item['text']))
                self.db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',
                    (id + ':' + item['item_id'], t['thread_id'], id + ':' + item['item_id'], 'provider', item['text'], 'assistant'))
            error = json.dumps(data['error']) if data['error'] is not None else None
            self.db.execute('UPDATE turns SET state=?,error=?,provider_turn_id=? WHERE id=?', (data['state'], error, recovered['id'], id))
            self.db.execute('INSERT INTO events(turn_id,kind,data) VALUES (?,?,?)', (id, 'reconciled', json.dumps(data)))

    def items(self, id):
        return [dict(r) for r in self.db.execute('SELECT * FROM items WHERE turn_id=? ORDER BY rowid', (id,))]

    def events(self, thread_id, project_id, *, actor, after=0):
        self._access(thread_id, project_id, actor)
        return [dict(r) for r in self.db.execute('''SELECT e.* FROM events e JOIN turns t ON t.id=e.turn_id
            WHERE t.thread_id=? AND e.seq>? ORDER BY seq LIMIT 1000''', (thread_id, after))]

    def review(self, id, decision=None, *, actor=None):
        t = self.turn(id)
        if decision is not None:
            if decision not in ('approved', 'rejected') or not actor or t['state'] not in ('completed', 'interrupted', 'failed'):
                raise Rejected('Human review requires a finished turn and explicit identity')
            thread = self.db.execute('SELECT * FROM threads WHERE id=?', (t['thread_id'],)).fetchone()
            self._access(t['thread_id'], thread['project_id'], actor)
            with self.db:
                self.db.execute('INSERT INTO reviews VALUES (?,?,?) ON CONFLICT(turn_id) DO UPDATE SET decision=excluded.decision,actor=excluded.actor', (id, decision, actor))
        row = self.db.execute('SELECT decision FROM reviews WHERE turn_id=?', (id,)).fetchone()
        return row[0] if row else 'pending'


    def close(self):
        self.db.close()
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def add_profile(self, id, provider, home, identity, category, method='subscription', monthly_limit_usd=None, org=None, org_name=None):
        if not identity or provider not in ('claude', 'codex') or category not in ('personal', 'work') or method not in ('subscription', 'api_key'):
            raise Rejected('Select an explicit provider and identity')
        if method == 'subscription':
            # The same sign-in can't be both. A work account added with your personal email (or the other way round)
            # would let one side's code reach the other's subscription. Another Claude organisation on the same email is
            # another sign-in, once both organisations are known (an unknown one counts as the same).
            other = self.db.execute("SELECT category FROM profiles WHERE provider=? AND method='subscription' AND lower(identity)=lower(?) AND category<>? "
                                    "AND removed_at IS NULL AND " + SAME_ORG, (provider, identity, category, org, org)).fetchone()
            if other:
                raise Rejected(f'{identity} is already your {other[0]} account, so it can’t be a {category} one too. '
                               f'Use the email you sign in with for {"work" if category == "work" else "yourself"}.')
        with self.db:
            self.db.execute('INSERT INTO profiles (id, provider, home, identity, category, method, monthly_limit_usd, org, org_name) VALUES (?,?,?,?,?,?,?,?,?)',
                            (id, provider, str(Path(home).resolve()), identity, category, method, monthly_limit_usd, org, org_name))

    def add_project(self, id, cwd, profile_id, model, context=None):
        cwd = Path(cwd).resolve(strict=True)
        if not cwd.is_dir() or not model:
            raise Rejected('Select a project folder and model')
        context = context or account_context(self.db, profile_id)
        require_context(self.db, context, profile_id)
        with self.db:
            self.db.execute('INSERT INTO projects (id, cwd, profile_id, model, context) VALUES (?,?,?,?,?)', (id, str(cwd), profile_id, model, context))

    def bind_project(self, project_id, profile_id, model):
        """Changes only future threads. Existing conversations keep their account."""
        if not model:
            raise Rejected('Select a model')
        require_context(self.db, project_context(self.db, project_id), profile_id)
        with self.db:
            if self.db.execute('UPDATE projects SET profile_id=?,model=? WHERE id=?',
                               (profile_id, model, project_id)).rowcount != 1:
                raise Rejected('Unknown project')

    def binding(self, thread_id, project_id, *, actor):
        t = self._access(thread_id, project_id, actor)
        p = self.db.execute('SELECT * FROM profiles WHERE id=?', (t['profile_id'],)).fetchone()
        project = self.db.execute('SELECT cwd FROM projects WHERE id=?', (project_id,)).fetchone()
        # The project's side travels with the binding. Reading a thread never refuses (a thread left on the other side's
        # account by an older build still opens and shows why); every run checks it before it starts, through
        # connections_ui.owned_profile(..., context=binding['context']).
        context = project_context(self.db, project_id)
        # Beta.18: a thread with its own folder runs there (thread_folders.cwd_for). ``project_cwd`` stays the project
        # folder, for the records kept per project (coding permission, full access) and the screens about the project.
        import thread_folders
        cwd, _ = thread_folders.cwd_for(self.db, thread_id, project['cwd'])
        return {**t, 'provider': p['provider'], 'home': p['home'], 'identity': p['identity'], 'org': p['org'],
                'category': p['category'], 'context': context, 'method': p['method'],
                'monthly_limit_usd': p['monthly_limit_usd'], 'cwd': cwd, 'project_cwd': project['cwd']}

    def shared_context(self, project_id):
        """The only project-wide context/search export. Private rows stay out."""
        return [dict(r) for r in self.db.execute('''SELECT m.* FROM messages m
            JOIN threads t ON t.id=m.thread_id WHERE t.project_id=? AND t.visibility='shared'
            ORDER BY m.rowid''', (project_id,))]

    def add_thread(self, id, project_id, channel_id, visibility, owner=None):
        if visibility not in ('shared', 'private') or (visibility == 'private' and not owner):
            raise Rejected('Private conversations need an owner')
        p = self.db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not p:
            raise Rejected('Unknown project')
        with self.db:
            self.db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,NULL)',
                            (id, project_id, channel_id, p['profile_id'], p['model'], visibility, owner))

    def thread(self, thread_id, project_id):
        row = self.db.execute('SELECT * FROM threads WHERE id=? AND project_id=?', (thread_id, project_id)).fetchone()
        if not row:
            raise Rejected('Conversation does not belong to this project')
        return dict(row)

    def _access(self, thread_id, project_id, actor):
        t = self.thread(thread_id, project_id)
        if t['visibility'] == 'private' and actor != t['owner']:
            raise Rejected('Private conversation belongs to another local identity')
        return t

    def messages(self, thread_id, project_id, *, actor):
        self._access(thread_id, project_id, actor)
        return [dict(r) for r in self.db.execute('SELECT * FROM messages WHERE thread_id=? ORDER BY rowid', (thread_id,))]

    def send(self, thread_id, project_id, client_id, text, *, actor):
        self._access(thread_id, project_id, actor)
        if not client_id or not actor or not text.strip() or len(text.encode()) > 65536:
            raise Rejected('Send needs an identity, request ID and text up to 64 KiB')
        with self.db:
            old = self.db.execute('SELECT * FROM messages WHERE thread_id=? AND client_id=?', (thread_id, client_id)).fetchone()
            if old:
                if old['text'] != text or old['actor'] != actor:
                    raise Rejected('Request ID was already used for different content')
                return old['id']
            id = str(uuid.uuid4())
            self.db.execute('INSERT INTO messages(id,thread_id,client_id,actor,text) VALUES (?,?,?,?,?)', (id, thread_id, client_id, actor, text))
            self.db.execute('INSERT INTO turns(id,thread_id,state) VALUES (?,?,?)', (id, thread_id, 'queued'))
            return id
