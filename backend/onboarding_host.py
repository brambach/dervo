"""The whole-Mac onboarding's host operations (beta.15 group 10; evidence/design-tests/onboarding-v2.md): look, preview
the threads of the folders you accepted, bring them in as one batch, and undo that batch while nothing's been done in it.

- ``onboarding_sources``: whether Claude Code's and Codex's lists are on this Mac. Existence only, for the Ask screen.
- ``onboarding_look``: mac_survey.survey, metadata only. The host keeps the folder paths under opaque keys
  (``surveyId``), so the screens only ever hand back keys the look itself produced, never a path.
- ``onboarding_threads``: the second yes. For each accepted folder, the existing per-project reader's shortlist
  (history_import_host.preview_folder), which reads the first and last message of each conversation. Each folder comes
  with its Work or Personal and the account it'll start on, and is read only when that account is on its side, so a
  work folder's messages are never read with nowhere of its own to go (security review S4, 24 Sep).
- ``onboarding_bring_in``: makes the ticked folders projects, each marked Work or Personal, a Personal one on the
  personal account you chose and a Work one on the work account (``work``). A Work folder with no work account isn't
  made: it's kept on the batch as waiting, nothing about it is read past the look or sent anywhere, and it never falls
  back to the personal account. The screen then runs the existing ``history_read`` for each, one project per call, with
  the sessions you ticked: the reader reads whole transcripts, and one call for every project could pass the bridge's 10
  second limit.

Work and Personal came back on 26 Sep 2026 (Bryce: "make it very obvious WHY and make sure both sides work"), after a
day without them (25 Sep). Batches stored on the 25th have no ``waiting`` list and projects with no context; they still
load and undo. The account is checked here (connections_ui.owned_profile with the project's side), the screen says
which one each side starts on, and the account and exact model are verified again before each run.
- ``onboarding_waiting``: the names of the Work folders still waiting for a work account, for Settings' "It will run".
- ``onboarding_bring_waiting``: once a work account is signed in, makes those folders Work projects on it, in their own
  batch, so Home's Undo takes them back the same way. Never on a personal account.
- ``onboarding_batch``: the batch a project came in with, for Home's "Brought in from this Mac" line.
- ``onboarding_undo``: removes every project of the batch through ``project_delete``, only while nothing has been done
  in any of them. Source folders and the tools' histories are never touched.

No model is called and nothing leaves the Mac in any of these.
"""
import json
import os
import time
import unicodedata
import uuid

from dervo_connections import Rejected

FIELDS = {
    'onboarding_sources': set(),
    'onboarding_look': set(),
    'onboarding_threads': {'surveyId', 'projects'},
    'onboarding_bring_in': {'surveyId', 'projects', 'profileId', 'model'},
    'onboarding_batch': {'projectId'},
    'onboarding_undo': {'batchId'},
    'onboarding_waiting': set(),
    'onboarding_bring_waiting': {'work'},
}
# ``work``: {profileId, model} for the Work folders, when a work account is ready. Left out, Work folders wait.
OPTIONAL = {'onboarding_bring_in': {'work'},
            # The Threads screen's opt-in for sessions a tool ran, as on the per-project picker (NEXT-UP #38).
            'onboarding_threads': {'includeToolRuns'}}
CONTEXTS = ('personal', 'work')
PROJECT_LIMIT = 30  # projects one bring-in may make
SURVEY_SECONDS = 3600  # a look older than this is looked at again before anything is made
HISTORY_ACTOR = 'dervo-history'  # the actor history_read writes its briefs as
HOME = None  # the home folder the look measures against; None is this Mac's. Tests point it at a fixture
TEMP_ROOTS = None  # None is mac_survey.TEMP_ROOTS plus $TMPDIR
TOUCHED = ('Something’s been done in {name} since it was brought in, so Undo can’t take the batch back. '
           'Archive or delete projects one at a time from the project menu.')


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS onboarding_batches (id TEXT PRIMARY KEY, created_at REAL NOT NULL, document TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS onboarding_projects (project_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL)')


def _survey(host, survey_id):
    found = getattr(host, 'onboarding_survey', None)
    if not found or type(survey_id) is not str or survey_id != found['id']:
        raise Rejected('Look at this Mac again. The last look isn’t available any more.')
    if time.time() - found['at'] > SURVEY_SECONDS:
        raise Rejected('That look is more than an hour old. Look at this Mac again before bringing anything in.')
    return found


def sources(host):
    import mac_survey
    from history_import import CLAUDE_HOME, CODEX_HOME
    return dict(sources=mac_survey.sources(claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, home=HOME))


def look(host):
    import mac_survey
    from history_import import CLAUDE_HOME, CODEX_HOME
    dervo = [row[0] for row in host.store.db.execute('SELECT cwd FROM projects')]
    found = mac_survey.survey(claude_home=CLAUDE_HOME, codex_home=CODEX_HOME, dervo_projects=dervo, home=HOME, temp_roots=TEMP_ROOTS)
    paths = found.pop('paths')
    survey_id = uuid.uuid4().hex
    home = os.path.realpath(str(HOME) if HOME else os.path.expanduser('~'))
    host.onboarding_survey = dict(id=survey_id, at=time.time(), paths=paths, batch=None, home=home)
    return dict(found, surveyId=survey_id)


def _keys(found, keys, limit=PROJECT_LIMIT):
    if type(keys) is not list or not keys or len(keys) > limit or any(type(k) is not str for k in keys) or len(set(keys)) != len(keys):
        raise Rejected(f'Choose between 1 and {limit} folders from the look.')
    unknown = [k for k in keys if k not in found['paths']]
    if unknown:
        raise Rejected('A folder you picked isn’t in the last look. Look at this Mac again.')
    # Loose ends never come in (Bryce, 24 Sep): the look gives the home folder no key, and this holds even if one did.
    if found.get('home') and any(os.path.realpath(found['paths'][k]) == found['home'] for k in keys):
        raise Rejected('Your home folder can’t be a project. Conversations started there stay where they are.')
    return keys


def threads(host, args):
    """The per-project reader's shortlist for each accepted folder. This is where message text is first read, on this
    Mac, and only for these folders."""
    from history_import_host import preview_folder, tool_runs_flag
    found = _survey(host, args['surveyId'])
    include_tool_runs = tool_runs_flag(args)
    from connections_ui import owned_profile
    rows = args['projects']
    if type(rows) is not list or any(type(r) is not dict or set(r) != {'key', 'context', 'profileId'} for r in rows):
        raise Rejected('Choose the folders to read, each with Work or Personal and the account it starts on.')
    keys = _keys(found, [r['key'] for r in rows])
    for row in rows:
        if row['context'] not in CONTEXTS:
            raise Rejected('Put each folder under Work or Personal before its threads are read.')
        if type(row['profileId']) is not str or not row['profileId']:
            raise Rejected('A Work folder’s threads are read once there’s a work account for it. Nothing was read.'
                           if row['context'] == 'work' else 'Choose the account these projects start on.')
        owned_profile(host.store, host.root, row['profileId'], context=row['context'])
    result = []
    for key in keys:
        path = found['paths'][key]
        if not os.path.isdir(path):
            result.append(dict(key=key, candidates=[], limit=0, detail='This folder isn’t there any more.', toolRuns=0))
            continue
        shortlist = preview_folder(host, path, include_tool_runs=include_tool_runs)
        result.append(dict(key=key, candidates=shortlist['candidates'], limit=shortlist['limit'], detail=None, toolRuns=shortlist['toolRuns']))
    return dict(projects=result)


def _name(value):
    if type(value) is not str or not value.strip() or len(value.strip()) > 80 or any(unicodedata.category(c).startswith('C') for c in value):
        raise Rejected('Give each project a name of 1 to 80 characters on one line.')
    return value.strip()


def _make_project(host, path, profile_id, model, name, context=None):
    """One project through Add project's own writer (project_setup.make, behaviour review N3): the project, its name
    and its starter thread, marked Work or Personal. The folder came from the host's own look, not from the renderer."""
    from project_setup import make
    return make(host, path, profile_id, model, name, context)


def _account(host, choice, context):
    """(profile, model) for one side, checked, or None when that side has no account. Never another side's."""
    from connections_ui import owned_profile
    if choice is None:
        return None
    if type(choice) is not dict or set(choice) != {'profileId', 'model'}:
        raise Rejected('Choose the work account these projects start on.' if context == 'work' else 'Choose the account these projects start on.')
    if type(choice['profileId']) is not str or not choice['profileId']:
        raise Rejected('Choose the work account these projects start on.' if context == 'work' else 'Choose the account these projects start on.')
    profile = owned_profile(host.store, host.root, choice['profileId'], context=context)
    model = choice['model']
    if type(model) is not str or not model or model != model.strip() or len(model) > 256:
        raise Rejected('Choose a model with a valid model ID.')
    return profile, model


def bring_in(host, args):
    """Make the batch's projects. Returns it as onboarding_batch reads it, with each project's id by its look key so
    the screen can read its threads next. A second call for the same look returns the batch the first one made."""
    from connections_ui import owned_profile
    found = _survey(host, args['surveyId'])
    if found.get('batch'):
        return batch_view(host, found['batch'])
    rows = args['projects']
    if type(rows) is not list or not rows or len(rows) > PROJECT_LIMIT or any(type(r) is not dict or set(r) != {'key', 'name', 'context'} for r in rows):
        raise Rejected(f'Choose between 1 and {PROJECT_LIMIT} projects to bring in, each under Work or Personal.')
    keys = _keys(found, [r['key'] for r in rows])
    picked = []
    for row, key in zip(rows, keys):
        if row['context'] not in CONTEXTS:
            raise Rejected('Put each project under Work or Personal.')
        path = found['paths'][key]
        if not os.path.isdir(path):
            raise Rejected(f'{os.path.basename(path)} isn’t there any more. Look at this Mac again.')
        picked.append(dict(key=key, path=path, name=_name(row['name']), context=row['context']))
    # Each side on its own account, checked now: a personal account for Personal, a work one for Work, never a fallback.
    personal_choice = None if args['profileId'] is None and args['model'] is None else dict(profileId=args['profileId'], model=args['model'])
    accounts = {'personal': _account(host, personal_choice, 'personal') if any(p['context'] == 'personal' for p in picked) else None,
                'work': _account(host, args.get('work'), 'work') if any(p['context'] == 'work' for p in picked) else None}
    if any(p['context'] == 'personal' for p in picked) and accounts['personal'] is None:
        raise Rejected('Choose the account these projects start on.')
    if not any(accounts[p['context']] for p in picked):
        raise Rejected('These are all Work projects and there’s no work account yet. Add one, move any that are yours to Personal, or start empty.')
    existing = {row[0] for row in host.store.db.execute('SELECT cwd FROM projects')}
    batch_id = uuid.uuid4().hex
    made, waiting, problems = [], [], []
    document = dict(at=time.time(), projects=made, waiting=waiting, problems=problems, undone=False)
    schema(host.store.db)
    # The batch is on record before its first project, and rewritten after each, so a bring-in that stops partway
    # still undoes everything it made.
    with host.store.db:
        host.store.db.execute('INSERT INTO onboarding_batches VALUES (?,?,?)', (batch_id, document['at'], json.dumps(document)))
    for item in picked:
        if item['path'] in existing:
            problems.append(f'{item["name"]} is already in Dervo.')
            continue
        account = accounts[item['context']]
        if account is None:
            # A Work folder with no work account yet: on record, never made, never on the personal account.
            waiting.append(dict(key=item['key'], name=item['name'], path=item['path'], context=item['context']))
            continue
        profile, model = account
        project_id, starter = _make_project(host, item['path'], profile['id'], model, item['name'], item['context'])
        with host.store.db:
            host.store.db.execute('INSERT INTO onboarding_projects VALUES (?,?)', (project_id, batch_id))
        made.append(dict(id=project_id, key=item['key'], name=item['name'], path=item['path'], starter=starter, context=item['context']))
        existing.add(item['path'])
        with host.store.db:
            host.store.db.execute('UPDATE onboarding_batches SET document=? WHERE id=?', (json.dumps(document), batch_id))
    with host.store.db:
        host.store.db.execute('UPDATE onboarding_batches SET document=? WHERE id=?', (json.dumps(document), batch_id))
    found['batch'] = batch_id
    return batch_view(host, batch_id)


def _batch(db, batch_id):
    schema(db)
    row = db.execute('SELECT document FROM onboarding_batches WHERE id=?', (batch_id,)).fetchone() if type(batch_id) is str else None
    if not row:
        raise Rejected('That batch isn’t on this Mac.')
    return json.loads(row[0])


def brought_threads(db, project_id):
    """The threads history_read made in this project: the ones whose first message is its brief."""
    return {row[0] for row in db.execute(
        "SELECT DISTINCT t.id FROM threads t JOIN messages m ON m.thread_id=t.id WHERE t.project_id=? AND m.actor=? "
        "AND m.client_id LIKE 'history-import:%'", (project_id, HISTORY_ACTOR))}


def touched(host, project):
    """Why a brought-in project no longer counts as untouched, or None: a run, a message you wrote, a thread you
    started, or Home used there. The briefs history_read wrote don't count."""
    from project_lifecycle import documents
    db, pid = host.store.db, project['id']
    exists = db.execute('SELECT 1 FROM projects WHERE id=?', (pid,)).fetchone()
    if not exists:
        return None
    if any((doc.get('context') or {}).get('project_id') == pid for _, doc in documents(db, 'coding_tasks')):
        return 'a run'
    ids = [row[0] for row in db.execute('SELECT id FROM threads WHERE project_id=?', (pid,))]
    if set(ids) - brought_threads(db, pid) - {project['starter']}:
        return 'a new thread'
    if ids:
        marks = ','.join('?' * len(ids))
        if db.execute(f'SELECT 1 FROM turns WHERE thread_id IN ({marks}) LIMIT 1', ids).fetchone():
            return 'a message'
        if db.execute(f'SELECT 1 FROM messages WHERE thread_id IN ({marks}) AND actor != ? LIMIT 1', [*ids, HISTORY_ACTOR]).fetchone():
            return 'a message'
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='orchestrator_entries'").fetchone() and \
            db.execute('SELECT 1 FROM orchestrator_entries WHERE project_id=? LIMIT 1', (pid,)).fetchone():
        return 'Home'
    return None


def batch_view(host, batch_id):
    document = _batch(host.store.db, batch_id)
    live = [p for p in document['projects'] if host.store.db.execute('SELECT 1 FROM projects WHERE id=?', (p['id'],)).fetchone()]
    counts = {p['id']: len(brought_threads(host.store.db, p['id'])) for p in live}
    made_paths = {row[0] for row in host.store.db.execute('SELECT cwd FROM projects')}
    blocker = next(((p['name'], why) for p in live for why in [touched(host, p)] if why), None)
    return dict(batchId=batch_id, at=document['at'], undone=document['undone'],
                projects=[dict(id=p['id'], key=p.get('key'), name=p['name'], threads=counts[p['id']], context=_context_of(host, p)) for p in live],
                threads=sum(counts.values()),
                # Work folders still waiting for a work account: not the ones brought in since (bring_waiting). Batches from
                # 25 Sep have none; older ones may.
                waiting=[dict(name=w['name'], path=w['path']) for w in document.get('waiting', []) if w.get('path') not in made_paths],
                problems=document.get('problems', []),
                undoable=not document['undone'] and bool(live) and blocker is None,
                blocked=TOUCHED.format(name=blocker[0]) if blocker else None)


def _context_of(host, project):
    """The project's Work or Personal: as recorded on the batch, or, for a batch from before it was recorded, as the
    project itself says."""
    if project.get('context') in CONTEXTS:
        return project['context']
    from dervo_connections import project_context
    try:
        return project_context(host.store.db, project['id'])
    except Rejected:
        return None


def batch_for(host, args):
    """The batch this project came in with, while it can still be undone or says why not, or None."""
    db = host.store.db
    schema(db)
    if type(args['projectId']) is not str:
        raise Rejected('Choose a project')
    row = db.execute('SELECT batch_id FROM onboarding_projects WHERE project_id=?', (args['projectId'],)).fetchone()
    if not row:
        return None
    view = batch_view(host, row[0])
    return None if view['undone'] else view


def undo(host, args):
    db = host.store.db
    document = _batch(db, args['batchId'])
    if document['undone']:
        return batch_view(host, args['batchId'])
    view = batch_view(host, args['batchId'])
    if view['blocked']:
        raise Rejected(view['blocked'])
    for project in document['projects']:
        if not db.execute('SELECT 1 FROM projects WHERE id=?', (project['id'],)).fetchone():
            continue
        result = host.dispatch('project_delete', {'projectId': project['id'], 'confirmation': 'DELETE ' + project['id']})
        if result.get('stopping'):
            raise Rejected('A run is closing in one of these projects. Try Undo again in a moment.')
    document['undone'] = True
    with db:
        db.execute('UPDATE onboarding_batches SET document=? WHERE id=?', (json.dumps(document), args['batchId']))
        db.execute('DELETE FROM onboarding_projects WHERE batch_id=?', (args['batchId'],))
    survey = getattr(host, 'onboarding_survey', None)
    if survey and survey.get('batch') == args['batchId']:
        survey['batch'] = None
    return batch_view(host, args['batchId'])


def _waiting(db):
    """[(batch id, item)] for every Work folder a batch kept waiting that isn't a project yet and is still on disk."""
    schema(db)
    projects = {row[0] for row in db.execute('SELECT cwd FROM projects')}
    found, seen = [], set()
    for batch_id, document in db.execute('SELECT id, document FROM onboarding_batches ORDER BY created_at'):
        document = json.loads(document)
        if document.get('undone'):
            continue
        for item in document.get('waiting', []):
            path = item.get('path')
            if type(path) is str and path not in projects and path not in seen and os.path.isdir(path):
                seen.add(path)
                found.append((batch_id, item))
    return found


def waiting(host):
    return dict(names=[item['name'] for _, item in _waiting(host.store.db)])


def bring_waiting(host, args):
    """The waiting Work folders, made Work projects on the work account you just added. They were ticked at Bring in,
    and the account was added for them; nothing of theirs was read before, and their threads are offered inside each
    project the same way a new one's are."""
    items = _waiting(host.store.db)
    if not items:
        raise Rejected('No Work projects are waiting.')
    profile, model = _account(host, args['work'], 'work')
    batch_id = uuid.uuid4().hex
    made, problems = [], []
    document = dict(at=time.time(), projects=made, waiting=[], problems=problems, undone=False)
    db = host.store.db
    with db:
        db.execute('INSERT INTO onboarding_batches VALUES (?,?,?)', (batch_id, document['at'], json.dumps(document)))
    for _, item in items[:PROJECT_LIMIT]:
        project_id, starter = _make_project(host, item['path'], profile['id'], model, item['name'], 'work')
        made.append(dict(id=project_id, key=item.get('key'), name=item['name'], path=item['path'], starter=starter, context='work'))
        with db:
            db.execute('INSERT INTO onboarding_projects VALUES (?,?)', (project_id, batch_id))
            db.execute('UPDATE onboarding_batches SET document=? WHERE id=?', (json.dumps(document), batch_id))
    return batch_view(host, batch_id)


def dispatch(host, operation, args):
    if type(args) is not dict or not FIELDS[operation] <= set(args) <= FIELDS[operation] | OPTIONAL.get(operation, set()):
        raise Rejected('Unsupported onboarding fields')
    if operation == 'onboarding_sources':
        return sources(host)
    if operation == 'onboarding_look':
        return look(host)
    if operation == 'onboarding_threads':
        return threads(host, args)
    if operation == 'onboarding_bring_in':
        return bring_in(host, args)
    if operation == 'onboarding_batch':
        return batch_for(host, args)
    if operation == 'onboarding_waiting':
        return waiting(host)
    if operation == 'onboarding_bring_waiting':
        return bring_waiting(host, args)
    return undo(host, args)
