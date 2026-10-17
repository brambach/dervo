"""Unsent local drafts. Provider tools and context builders don't read this table."""
import json
from dervo_connections import Rejected

FIELDS = {'drafts_list', 'draft_save'}
MAX_BYTES = 65536
MAX_DRAFTS = 100


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS workspace_drafts (
        thread_id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL, draft TEXT, mutation_id TEXT NOT NULL,
        mutation_payload TEXT NOT NULL)''')


def owned(store, actor):
    return {row['id']: row['project_id'] for row in store.db.execute(
        "SELECT id,project_id FROM threads WHERE visibility='shared' OR owner=?", (actor,))}


def validate(draft, channel):
    if draft is None:
        return None
    if type(draft) is not dict:
        raise Rejected('This draft format needs an update. Your text is still in the editor.')
    required = {'content', 'selectionStart', 'selectionEnd', 'channelId', 'createdAt',
                'updatedAt', 'pendingImeta', 'spoileredAttachmentUrls', 'status'}
    if not required.issubset(draft) or set(draft) - required - {'mentionRefs'}:
        raise Rejected('This draft format needs an update. Your text is still in the editor.')
    if (draft['channelId'] != channel or draft['status'] != 'active'
            or any(type(draft[k]) is not str for k in ['content', 'createdAt', 'updatedAt'])
            or any(type(draft[k]) is not int or draft[k] < 0 for k in ['selectionStart', 'selectionEnd'])
            or any(type(draft[k]) is not list for k in ['pendingImeta', 'spoileredAttachmentUrls'])
            or type(draft.get('mentionRefs', [])) is not list):
        raise Rejected('This draft format needs an update. Your text is still in the editor.')
    if any(type(ref) is not dict or set(ref) != {'displayName', 'pubkey', 'isAgent'}
           or type(ref['isAgent']) is not bool
           or any(type(ref[k]) is not str or not ref[k].strip() for k in ['displayName', 'pubkey'])
           for ref in draft.get('mentionRefs', [])):
        raise Rejected('This draft format needs an update. Your text is still in the editor.')
    try:
        raw = json.dumps(draft, ensure_ascii=True, allow_nan=False, separators=(',', ':'), sort_keys=True)
    except (TypeError, ValueError):
        raise Rejected('This draft couldn’t be saved. Your text is still in the editor.') from None
    if len(raw.encode()) > MAX_BYTES:
        raise Rejected('This draft is too large to save. Shorten it or copy it somewhere safe before closing Dervo.')
    return raw


def dispatch(host, operation, args, actor):
    if type(args) is not dict:
        raise Rejected('Invalid draft request.')
    db = host.store.db
    with db:
        schema(db)
    allowed = owned(host.store, actor)
    if operation == 'drafts_list':
        if args:
            raise Rejected('Invalid draft request.')
        rows = {row['thread_id']: row for row in db.execute('SELECT * FROM workspace_drafts')}
        return {'entries': [{'key': f'dervo:{project}:{channel}', 'projectId': project,
                             'channelId': channel, 'revision': rows[channel]['revision'] if channel in rows else 0,
                             'draft': json.loads(rows[channel]['draft']) if channel in rows and rows[channel]['draft'] else None}
                            for channel, project in allowed.items()]}
    fields = {'projectId', 'channelId', 'revision', 'mutationId', 'draft'}
    if (operation != 'draft_save' or set(args) != fields
            or type(args['channelId']) is not str or type(args['projectId']) is not str
            or allowed.get(args['channelId']) != args['projectId']
            or type(args['revision']) is not int or args['revision'] < 0
            or type(args['mutationId']) is not str or not 1 <= len(args['mutationId']) <= 80):
        raise Rejected('This conversation isn’t available for saving a draft.')
    channel = args['channelId']
    raw = validate(args['draft'], channel)
    payload = json.dumps({'revision': args['revision'], 'draft': raw}, sort_keys=True)
    with db:
        old = db.execute('SELECT * FROM workspace_drafts WHERE thread_id=?', (channel,)).fetchone()
        if old and old['mutation_id'] == args['mutationId']:
            if old['mutation_payload'] != payload:
                raise Rejected('The draft save changed while retrying. Keep your text and reopen the conversation.')
            return {'revision': old['revision']}
        revision = old['revision'] if old else 0
        if revision != args['revision']:
            raise Rejected('A newer draft is already saved. Copy your text before restarting Dervo.')
        if raw is not None and (not old or old['draft'] is None):
            count = db.execute('SELECT count(*) FROM workspace_drafts WHERE draft IS NOT NULL').fetchone()[0]
            if count >= MAX_DRAFTS:
                raise Rejected('Dervo has 100 saved drafts. Clear an older draft before saving another.')
        db.execute('''INSERT INTO workspace_drafts VALUES (?,?,?,?,?)
                      ON CONFLICT(thread_id) DO UPDATE SET revision=excluded.revision,
                      draft=excluded.draft, mutation_id=excluded.mutation_id,
                      mutation_payload=excluded.mutation_payload''',
                   (channel, revision + 1, raw, args['mutationId'], payload))
    return {'revision': revision + 1}
