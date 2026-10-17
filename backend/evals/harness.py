"""EvalHost: a headless Dervo host with the fixtures the smartness cases need.

One host per case, on its own throwaway data dir and scratch git repo (no remote). Profiles:

* ``fake_profile()``: a Dervo-owned Claude profile that runs fake_claude_cli.py, in both modes. Seeds go through it:
  scripted answers, scripted tool calls (guide updates, notes, file writes) at no cost.
* ``agent('claude' | 'codex')``: in real mode, your existing Claude Code sign-in (claude_policy.is_system_home, the
  profile row's home is ~/.claude, identity read from ``auth status``) or a throwaway CODEX_HOME whose ``auth.json`` is
  a symlink to ~/.codex/auth.json (identity read from the app-server's ``account/read``). Nothing here opens either
  credential; the CLIs do, as they do for any launch on this Mac. In fake mode both are the fake profile.

Home always runs on the project's profile (project_record_scope), so a Home case makes that the real Claude one and
binds its seed threads to the fake profile with ``create_agent_conversation``.

Not read-only, see evals/README.md: a Codex token refresh writes through the auth.json link into ~/.codex/auth.json,
and real Claude coding runs write their session transcripts into ~/.claude/projects.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from headless_driver import HostDriver, HostError, Ids, make_project  # noqa: E402

CLAUDE_MODEL = 'claude-sonnet-5'
CODEX_MODEL = 'gpt-5.6-terra'
FAKE_MODEL = 'claude-fake-model'
# The host's checks run in confinement only under the audited framework Python (task_evidence: sys.prefix must be the
# 3.13 framework), so the eval host runs on it when this Mac has it; otherwise every write ends "Tests were not run".
FRAMEWORK_PYTHON = '/Library/Frameworks/Python.framework/Versions/3.13/bin/python3'
HOST_PYTHON = FRAMEWORK_PYTHON if os.path.exists(FRAMEWORK_PYTHON) else sys.executable
RUN_TIMEOUT = 420.0  # a real restricted run, seconds
HOME_TIMEOUT = 200.0  # Home applies an answer up to 150 s after the send
_IDENTITY = {}


def claude_identity():
    """The email of the Mac's existing Claude Code sign-in, from the CLI's own ``auth status`` (no model call)."""
    if 'claude' in _IDENTITY:
        return _IDENTITY['claude']
    import claude_policy
    import pwd
    name = pwd.getpwuid(os.getuid()).pw_name
    env = {'HOME': str(Path.home()), 'USER': name, 'LOGNAME': name, 'PATH': '/usr/local/bin:/usr/bin:/bin'}
    result = subprocess.run([claude_policy.executable(), 'auth', 'status'], env=env, capture_output=True, text=True, timeout=20)
    try:
        meta = json.loads(result.stdout or '{}')
    except ValueError:
        meta = {}
    if not meta.get('loggedIn') or not meta.get('email'):
        raise RuntimeError('Claude Code has no sign-in on this Mac that a clean environment can use; real Claude cases can\'t run')
    _IDENTITY['claude'] = meta['email']
    _IDENTITY['claude_plan'] = meta.get('subscriptionType')
    return meta['email']


def codex_identity(scratch):
    """The email of the Mac's existing Codex sign-in, from the app-server's ``account/read`` in a throwaway CODEX_HOME
    whose auth.json is a symlink to the real one (no model call, no thread)."""
    if 'codex' in _IDENTITY:
        return _IDENTITY['codex']
    import tempfile
    import codex_policy
    import codex_runtime
    from transport import OwnedProcess, clean_environment
    home = Path(scratch) / 'codex-identity'
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    link_codex_auth(home)
    argv = codex_policy.argv(codex_runtime.executable(), login_method='chatgpt')
    env = clean_environment('codex', str(home), str(home))
    account = None
    with tempfile.TemporaryDirectory(prefix='dervo-eval-identity-', dir='/private/tmp') as neutral, \
            OwnedProcess(argv, cwd=neutral, env=env, byte_limit=1024 * 1024) as process:
        process.send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'dervo', 'title': 'Dervo', 'version': '0.1.0'},
                                                                   'capabilities': {'experimentalApi': False}}})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and account is None:
            try:
                batch = process.read(1)
            except TimeoutError:
                continue
            if batch is None:
                break
            for message in batch:
                if message.get('id') == 1:
                    process.send({'method': 'initialized', 'params': {}})
                    process.send({'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}})
                elif message.get('id') == 2:
                    account = (message.get('result') or {}).get('account') or {}
    if not account or account.get('type') != 'chatgpt' or not account.get('email'):
        raise RuntimeError('Codex has no ChatGPT sign-in on this Mac; real Codex cases can\'t run')
    _IDENTITY['codex'] = account['email']
    _IDENTITY['codex_plan'] = account.get('planType')
    return account['email']


def link_codex_auth(home):
    real = Path.home() / '.codex' / 'auth.json'
    if not real.exists():
        raise RuntimeError('~/.codex/auth.json is missing; Codex is not signed in on this Mac')
    link = Path(home) / 'auth.json'
    if not link.exists():
        link.symlink_to(real)


class EvalHost(HostDriver):
    def __init__(self, data_dir, *, real=False, run_log=None, cap=60, case_cap=6, case='run', scratch=None, agent_provider=None,
                 home_provider=None):
        """``agent_provider`` ('claude' or 'codex') overrides the provider agent cases ask for, so a baseline can run
        when one sign-in is at its usage limit. It never moves Home. ``home_provider`` ('claude', the default, or 'codex')
        is the account Home cases' projects are on (beta.17: Home answers on Codex accounts too), see home_agent."""
        self.scratch = Path(scratch or Path(data_dir).parent).resolve()
        self.agent_provider = agent_provider
        self.home_provider = home_provider or 'claude'
        # A loaded Mac (other suites, real runs) can make one host reply slow; that is a driver finding, not an eval
        # error, so the bridge-style slow limit is off here and only the reply timeout bounds a call.
        super().__init__(data_dir, real=real, python=HOST_PYTHON, reply_timeout=90.0, slow_limit=89.0,
                         real_config=dict(cap=cap, case_cap=case_cap, case=case, run_log=run_log, codex_model=CODEX_MODEL))
        self.case = case
        self.fake = None
        self.thread_profile = {}  # thread id -> profile id, for scenario files
        self.thread_ids = {}  # thread id -> Ids

    # ------------------------------------------------------------------ profiles
    def fake_profile(self):
        if self.fake is None:
            self.fake = self.add_claude_profile()
            self.scenario(self.fake, result_text='Done.')
        return self.fake

    def agent(self, provider):
        """The profile an agent case runs on: the real sign-in in real mode, the fake otherwise. Returns (profile, model)."""
        if not self.real:
            return self.fake_profile(), FAKE_MODEL
        provider = self.agent_provider or provider
        if provider == 'codex':
            return self.real_codex_profile(), CODEX_MODEL
        return self.real_claude_profile(), CLAUDE_MODEL

    def home_agent(self, provider=None):
        """The account a Home case's project is on, so Home's own call runs on it: ``provider``, or the run's
        ``home_provider``. Real sign-ins in real mode, the fake otherwise. Returns (profile, model); the model is the
        project's, which Home doesn't use for itself (Sonnet on Claude, home_codex.MODEL on Codex)."""
        if not self.real:
            return self.fake_profile(), FAKE_MODEL
        if (provider or self.home_provider) == 'codex':
            return self.real_codex_profile(), CODEX_MODEL
        return self.real_claude_profile(), CLAUDE_MODEL

    def real_claude_profile(self):
        import claude_policy
        identity = claude_identity()
        profile = self.call('add_profile', provider='claude', identity=identity, category='personal')['id']
        with self.db() as db:
            db.execute('UPDATE profiles SET home=? WHERE id=?', (str(claude_policy.system_config_dir().resolve()), profile))
        return profile

    def real_codex_profile(self):
        identity = codex_identity(self.scratch)
        profile = self.call('add_profile', provider='codex', identity=identity, category='personal')['id']
        home = self.data_dir / 'profiles' / profile
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        link_codex_auth(home)
        return profile

    # ------------------------------------------------------------------ projects and threads
    def project(self, profile, model, *, path=None, files=None, name='proj'):
        """A scratch git repo (make_project's two files, plus ``files``: {relative path: text}) picked as a project on
        ``profile`` and ``model``. Returns Ids with no thread yet."""
        path = Path(path or self.scratch / name)
        make_project(path)
        for relative, text in (files or {}).items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        if files:
            env = dict(os.environ, GIT_AUTHOR_NAME='Dervo test', GIT_AUTHOR_EMAIL='test@example.com',
                       GIT_COMMITTER_NAME='Dervo test', GIT_COMMITTER_EMAIL='test@example.com', HOME=str(path))
            subprocess.run(['git', 'add', '.'], cwd=path, env=env, check=True, capture_output=True)
            subprocess.run(['git', 'commit', '-q', '-m', 'seed'], cwd=path, env=env, check=True, capture_output=True)
        project = self.call('_picked_project', path=str(path.resolve()), profile_id=profile, model=model)['id']
        ids = Ids(profile, project, None)
        ids.path = path
        return ids

    def grant(self, ids, files=('greeting.py', 'test_sample.py'), test_file='test_sample.py', thread=None):
        scope = dict(projectId=ids.project, channelId=thread or ids.thread)
        return self.call('coding_permission_grant', **scope, files=list(files), testFile=test_file)

    def thread(self, ids, name='probe', *, automatic=False, private=False, profile=None, model=None):
        """A thread on the project's profile, or (``profile``) on another of this host's profiles through
        create_agent_conversation. Automatic naming needs an empty name."""
        if profile and profile != ids.profile and automatic:
            # Automatic naming is only offered at create_conversation, so the thread starts on the project's account
            # and moves to the seed profile before its first message (as the account picker would).
            thread = self.call('create_conversation', projectId=ids.project, name='', private=private, automaticName=True)['id']
            self.rebind(ids, thread, profile, model or FAKE_MODEL)
        elif profile and profile != ids.profile:
            thread = self.call('create_agent_conversation', projectId=ids.project, profileId=profile, model=model or FAKE_MODEL, name=name or 'seed')['id']
        else:
            args = dict(projectId=ids.project, name='' if automatic else name, private=private)
            if automatic:
                args['automaticName'] = True
            thread = self.call('create_conversation', **args)['id']
            profile = ids.profile
        self.thread_profile[thread] = profile
        self.thread_ids[thread] = Ids(profile, ids.project, thread)
        if ids.thread is None:
            ids.thread = thread
        return thread

    def scoped(self, ids, thread):
        return Ids(self.thread_profile.get(thread, ids.profile), ids.project, thread)

    def rebind(self, ids, thread, profile, model):
        """Move a thread to another account and model (conversation_profile_set), as the account picker does."""
        row = self.call('binding', projectId=ids.project, channelId=thread)
        self.call('conversation_profile_set', projectId=ids.project, channelId=thread, expectedProfileId=row['profile_id'],
                  profileId=profile, expectedModel=row['model'], model=model)
        self.thread_profile[thread] = profile

    # ------------------------------------------------------------------ runs
    def scripted(self, ids, thread, text, result_text='Done.', tool_calls=(), guide=None, guide_last=False, timeout=120.0, **extra):
        """A coding request answered by the fake: ``result_text``, after ``tool_calls`` ({name, arguments}) and a
        task-guide update when ``guide`` gives goal/step/next (the rest is filled in), first by default or last with
        ``guide_last`` (a final report after the work, which is what makes its ``next`` fresh). Returns the settled task."""
        profile = self.thread_profile[thread]
        calls = list(tool_calls)
        if guide:
            full = dict(goal='Do what was asked.', step='Working through it.', why='It was asked for.', next='Nothing more.',
                        test_steps='Run the check.', limits='Nothing beyond the request was checked.')
            full.update(guide)
            update = dict(name='dervo_update_task_guide', arguments=full)
            calls = calls + [update] if guide_last else [update] + calls
        self.scenario(profile, result_text=result_text, tool_calls=calls)
        scoped = self.scoped(ids, thread)
        task = self.request(scoped, text, **extra)
        return self.wait_for_end(scoped, task, timeout=timeout)

    def run(self, ids, thread, text, timeout=RUN_TIMEOUT, **extra):
        """A coding request on the thread's own account (real in real mode); the settled task."""
        scoped = self.scoped(ids, thread)
        task = self.request(scoped, text, **extra)
        return self.wait_for_end(scoped, task, timeout=timeout if self.real else 120.0)

    def message(self, ids, thread, text):
        """A chat message queued without any run: what names and files a thread at its first message."""
        return self.call('queue', projectId=ids.project, channelId=thread, clientId='m-' + uuid.uuid4().hex[:10], text=text, parentId=None)

    # ------------------------------------------------------------------ Home
    def home(self, ids, text, timeout=HOME_TIMEOUT):
        """Send to Home on the project's profile and wait for the reply entry."""
        scope = dict(projectId=ids.project, profileId=ids.profile)
        self.call('orchestrator_send', **scope, text=text, clientId='home-' + uuid.uuid4().hex[:12])
        return self._home_reply(scope, timeout)

    def arrive(self, ids, timeout=HOME_TIMEOUT):
        scope = dict(projectId=ids.project, profileId=ids.profile)
        self.call('orchestrator_arrive', **scope)
        return self._home_reply(scope, timeout)

    def _home_reply(self, scope, timeout):
        def settled():
            listing = self.call('orchestrator_list', **scope)
            return None if listing['thinking'] else listing
        listing = self.wait_until(settled, timeout=timeout if self.real else 120.0, interval=1.5, what='Home reply')
        replies = [e for e in listing['entries'] if e.get('kind') == 'reply']
        return replies[-1] if replies else dict(state='none', text='', parts=[], changes=[], notes=[])

    def home_reply_seed(self, ids, text):
        """A ready reply from Home already in the chat, as if it had answered ``text`` a moment ago."""
        document = dict(kind='reply', state='ready', arrival=False, text=text, parts=[], looked=[], mentions=[], notes=[], changes=[])
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS orchestrator_entries (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, '
                       'profile_id TEXT NOT NULL, client_id TEXT, created_at REAL NOT NULL, document TEXT NOT NULL)')
            db.execute('INSERT INTO orchestrator_entries VALUES (?,?,?,?,?,?)',
                       (str(uuid.uuid4()), ids.project, ids.profile, None, round(time.time(), 3), json.dumps(document)))

    # ------------------------------------------------------------------ seeds
    def db(self):
        return sqlite3.connect(self.data_dir / 'conversations.sqlite3', timeout=30)

    def seed_notes(self, ids, texts, *, thread=None, visibility='shared', ago=0, provider='claude', ids_prefix=None):
        """Agent notes straight into project memory, in the document shape project_memory writes. Returns their ids."""
        import project_memory
        now = int(time.time()) - ago
        made = []
        with self.db() as db:
            project_memory.schema(db)
            for index, text in enumerate(texts):
                entry_id = (ids_prefix + f'{index:02d}') if ids_prefix else uuid.uuid4().hex[:8]
                source = dict(thread_id=thread or ids.thread, task_id='seed', run_id='seed', visibility=visibility)
                item = dict(id=entry_id, text=text, created_at=now, updated_at=now, provider=provider, source=source)
                db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?)', (entry_id, ids.project, 'personal', 'active', json.dumps(item)))
                made.append(entry_id)
        return made

    def notes(self, ids):
        """Active notes, oldest first: [{id, text, updated_at, ...}]."""
        with self.db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='project_memory'").fetchone():
                return []
            return [json.loads(row[0]) for row in db.execute(
                "SELECT document FROM project_memory WHERE project_id=? AND status='active' ORDER BY rowid", (ids.project,))]

    def workstream(self, ids, name, state='building'):
        listing = self.call('ideas_save', projectId=ids.project, profileId=ids.profile, ideaId=None, expectedRevision=0,
                            name=name, summary='', state=state, target=None)
        return next(i for i in listing['ideas'] if i['name'] == name)

    def ideas(self, ids):
        return self.call('ideas_list', projectId=ids.project, profileId=ids.profile)

    def assign(self, ids, thread, idea_id):
        return self.call('ideas_assign', projectId=ids.project, profileId=ids.profile, channelId=thread, ideaId=idea_id)

    def file_by_hand(self, ids, thread, group):
        """Move a thread into a workstream by hand, the way the sidebar's Move does (ConnectedApp.moveThread):
        ideas_assign on the workstream, which moves the membership and then saves the group through organisation_set
        as you, locking it against the rules and Home. organisation_set alone changes only the sidebar heading and
        leaves the thread counted in its old workstream; the app sends it with an unchanged group, for renames only."""
        idea = next((i for i in self.ideas(ids)['ideas'] if i['name'] == group), None) or self.workstream(ids, group)
        return self.assign(ids, thread, idea['id'])

    def organisation(self, ids, thread):
        return self.call('organisation_get', projectId=ids.project, channelId=thread)

    def item(self, ids, kind, name, text=''):
        listing = self.call('ideas_item_add', projectId=ids.project, profileId=ids.profile, kind=kind, name=name, text=text)
        return next(i for i in listing['items'] if i['name'] == name)

    def note_item(self, ids, item, text):
        listing = self.call('ideas_item_note', projectId=ids.project, profileId=ids.profile, itemId=item['id'], expectedRevision=item['revision'], text=text)
        return next(i for i in listing['items'] if i['id'] == item['id'])

    def set_item(self, ids, item, state):
        listing = self.call('ideas_item_set', projectId=ids.project, profileId=ids.profile, itemId=item['id'],
                            expectedRevision=item['revision'], name=item['name'], state=state, target=item.get('target'))
        return next((i for i in listing['items'] + listing.get('archived', []) if i['id'] == item['id']), None)

    def items(self, ids):
        return self.call('ideas_item_list', projectId=ids.project, profileId=ids.profile)

    def skill(self, ids, name, description, body):
        return self.call('skills_save', projectId=ids.project, profileId=ids.profile, skillId=None, expectedRevision=0,
                         name=name, description=description, body=body)

    def rule(self, ids, thread, text):
        scope = dict(projectId=ids.project, channelId=thread, profileId=ids.profile)
        rule = self.call('knowledge_save', **scope, ruleId=None, expectedRevision=0, text=text, provenance='Human authored for this project', source=None)
        return self.call('knowledge_approve', **scope, ruleId=rule['id'], versionId=rule['versions'][0]['id'], expectedRevision=rule['revision'])

    # ------------------------------------------------------------------ reads
    def activity(self, ids, thread):
        return next(c['activity'] for c in self.call('conversations', projectId=ids.project) if c['id'] == thread)

    def conversations(self, ids):
        return self.call('conversations', projectId=ids.project)

    def pickup(self, ids, thread):
        return self.call('conversation_pickup', projectId=ids.project, channelId=thread)

    def project_pickup(self, ids):
        return self.call('project_pickup', projectId=ids.project, profileId=ids.profile)

    def task_of(self, ids, thread, task_id):
        return self.task(self.scoped(ids, thread), task_id)

    def review(self, ids, thread, task, decision='approved'):
        scope = dict(projectId=ids.project, channelId=thread)
        attempt = self.call('task_detail', **scope, taskId=task['id'])['evidence']['attempts'][-1]['number']
        return self.call('task_review', **scope, taskId=task['id'], attemptNumber=attempt, decision=decision)

    def commit(self, ids, thread, task):
        return self.call('task_commit', projectId=ids.project, channelId=thread, taskId=task['id'])

    def undo(self, ids, thread, task):
        return self.call('task_undo', projectId=ids.project, channelId=thread, taskId=task['id'])

    def edit_task(self, task_id, change):
        """Edit a task record in place (a seed's timestamps, states), as test_headless_lifecycle does."""
        with self.db() as db:
            saved = json.loads(db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()[0])
            change(saved)
            db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task_id))

    def diff(self, ids):
        """The project folder's uncommitted diff plus untracked files' text, for behaviour checks."""
        path = ids.path
        env = dict(os.environ, HOME=str(path))
        diff = subprocess.run(['git', 'diff'], cwd=path, env=env, capture_output=True, text=True).stdout
        untracked = subprocess.run(['git', 'ls-files', '--others', '--exclude-standard'], cwd=path, env=env, capture_output=True, text=True).stdout.split()
        for name in untracked:
            try:
                diff += f'\n+++ {name}\n' + (path / name).read_text()
            except (OSError, UnicodeDecodeError):
                pass
        return diff


def write_tool_calls(path, content_placeholder='{prev.content}'):
    """Scripted read, write and check of one granted file through the fake: the write carries the read's hash, and
    the check runs because a result without one ends blocked ("Tests were not run to completion")."""
    return [dict(name='dervo_read_file', arguments=dict(path=path)),
            dict(name='dervo_write_file', arguments=dict(path=path, expected_hash='{prev.sha256}', content=content_placeholder)),
            dict(name='dervo_run_tests', arguments={})]


def answer_text(task):
    return next((m.get('text') for m in reversed(task.get('messages') or []) if m.get('role') == 'assistant'), '') or ''


def told(task):
    runs = task.get('runs') or []
    return (runs[-1].get('told') or {}) if runs else {}


def remembered(task):
    runs = task.get('runs') or []
    return (runs[-1].get('remembered') or []) if runs else []


def tools_used(task):
    """Tool names the newest run called, in order (Dervo's tokens, both providers)."""
    runs = task.get('runs') or []
    run_id = runs[-1].get('id') if runs else None
    return [t.get('tool') for t in task.get('tokens') or [] if isinstance(t, dict) and (run_id is None or t.get('run_id') == run_id)]


def has(text, *needles):
    text = (text or '').casefold()
    return all(n.casefold() in text for n in needles)


def has_any(text, *needles):
    text = (text or '').casefold()
    return any(n.casefold() in text for n in needles)
