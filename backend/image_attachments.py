"""Local conversation images. No remote uploads or provider calls happen here.

Two scopes share one flow. A thread's images are keyed by its thread id, in conversation_images. Home's images (#3,
beta.11) are keyed by the project, in home_images, and reached with channelId ``home:<projectId>``. The two never read
each other's tables, so an id from one can't open, send or remove anything in the other.
"""
import base64
import binascii
import hashlib
import re
import struct
import time
import uuid
import zlib
from collections import namedtuple

from dervo_connections import Rejected

MAX_BYTES = 2 * 1024 * 1024
MAX_EDGE = 2048
CHUNK_BYTES = 48 * 1024
MAX_IMAGES = 4
WORKSPACE_BYTES = 256 * 1024 * 1024
SIGNATURE = b'\x89PNG\r\n\x1a\n'
THUMB_EDGE = 640         # a tile's picture: 192 px on its short side (96 px at 2x), capped on the long one
THUMB_BYTES = 256 * 1024
SCOPE = {'projectId', 'channelId'}
FIELDS = {
    'image_begin': SCOPE | {'clientId', 'name', 'size', 'sha256'},
    'image_chunk': SCOPE | {'id', 'offset', 'data'},
    'image_finish': SCOPE | {'id'},
    'image_discard': SCOPE | {'id'},
    'image_cancel': SCOPE | {'clientId'},
    'image_get': SCOPE | {'id'},
    # A small picture for a tile, so drawing one never sends the whole image over the pipe (send pipeline, 9 Oct). The
    # window makes it from the image it already decoded and puts it next to the upload; without one, image_thumb sends
    # the image itself, as image_get did.
    'image_thumb_put': SCOPE | {'id', 'data'},
    'image_thumb': SCOPE | {'id'},
    'image_drafts': SCOPE,
}


HOME_PREFIX = 'home:'
# Table names per scope, for the SQL below. ``key`` is the column that ties an image to its owner.
Tables = namedtuple('Tables', 'images key cancels uses')
THREAD = Tables('conversation_images', 'thread_id', 'conversation_image_cancellations', 'conversation_image_uses')
HOME = Tables('home_images', 'project_id', 'home_image_cancellations', 'home_image_uses')


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', binascii.crc32(kind + data) & 0xffffffff)


def normalize(raw):
    """Validate bounded browser-normalized RGB/RGBA PNGs and strip metadata."""
    if type(raw) is not bytes or not 57 <= len(raw) <= MAX_BYTES or not raw.startswith(SIGNATURE):
        raise Rejected('This image couldn’t open. Choose another PNG, JPEG or WebP image.')
    position, header, compressed, ended = 8, None, bytearray(), False
    try:
        while position < len(raw):
            if position + 12 > len(raw): raise ValueError()
            length = struct.unpack('>I', raw[position:position+4])[0]
            kind = raw[position+4:position+8]
            end = position + 12 + length
            if end > len(raw) or not re.fullmatch(b'[A-Za-z]{4}', kind): raise ValueError()
            data = raw[position+8:position+8+length]
            crc = struct.unpack('>I', raw[end-4:end])[0]
            if crc != binascii.crc32(kind + data) & 0xffffffff: raise ValueError()
            if header is None and kind != b'IHDR': raise ValueError()
            if kind == b'IHDR':
                if header is not None or length != 13: raise ValueError()
                width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', data)
                if (not 1 <= width <= MAX_EDGE or not 1 <= height <= MAX_EDGE
                        or (depth, compression, filtering, interlace) != (8, 0, 0, 0) or color not in (2, 6)):
                    raise ValueError()
                header = data
            elif kind == b'IDAT': compressed.extend(data)
            elif kind == b'IEND':
                if length or end != len(raw): raise ValueError()
                ended = True
                break
            elif not kind[0] & 32: raise ValueError()
            position = end
        if not ended or not compressed: raise ValueError()
        stride = width * (4 if color == 6 else 3) + 1
        expected = height * stride
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected + 1)
        if len(pixels) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError()
        if any(pixels[row * stride] > 4 for row in range(height)): raise ValueError()
        clean = SIGNATURE + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')
        if len(clean) > MAX_BYTES: raise ValueError()
        return clean, width, height
    except (ValueError, struct.error, zlib.error):
        raise Rejected('This image couldn’t open. Choose another PNG, JPEG or WebP image.') from None


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS conversation_images (
        id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, name TEXT NOT NULL, expected_size INTEGER NOT NULL,
        expected_hash TEXT NOT NULL, state TEXT NOT NULL, data BLOB NOT NULL,
        width INTEGER, height INTEGER, created_at INTEGER NOT NULL,
        UNIQUE(thread_id,client_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS conversation_image_cancellations (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, PRIMARY KEY(thread_id,client_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS conversation_image_uses (
        image_id TEXT NOT NULL REFERENCES conversation_images(id) ON DELETE CASCADE,
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
        PRIMARY KEY(image_id,source_kind,source_id))''')
    # Home's images: the same columns, owned by the project, so deleting the project deletes them.
    db.execute('''CREATE TABLE IF NOT EXISTS home_images (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, name TEXT NOT NULL, expected_size INTEGER NOT NULL,
        expected_hash TEXT NOT NULL, state TEXT NOT NULL, data BLOB NOT NULL,
        width INTEGER, height INTEGER, created_at INTEGER NOT NULL,
        UNIQUE(project_id,client_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS home_image_cancellations (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        client_id TEXT NOT NULL, PRIMARY KEY(project_id,client_id))''')
    db.execute('''CREATE TABLE IF NOT EXISTS home_image_uses (
        image_id TEXT NOT NULL REFERENCES home_images(id) ON DELETE CASCADE,
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
        PRIMARY KEY(image_id,source_kind,source_id))''')

    for table in (THREAD.images, HOME.images):
        columns = {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
        if 'document_text' not in columns:
            db.execute(f'ALTER TABLE {table} ADD COLUMN document_text TEXT')
        if 'thumb' not in columns:
            db.execute(f'ALTER TABLE {table} ADD COLUMN thumb BLOB')


def used_bytes(db):
    """Local image storage in use, both scopes together: one budget for the workspace."""
    return sum(db.execute(f"SELECT coalesce(sum(max(expected_size,length(data))+coalesce(length(CAST(document_text AS BLOB)),0)+coalesce(length(thumb),0)),0) FROM {t.images} WHERE state!='discarded'").fetchone()[0]
               for t in (THREAD, HOME))


def tables_for(channel):
    return HOME if channel.startswith(HOME_PREFIX) else THREAD


def home_owner(host, project, channel, actor):
    """Home's scope: only ``home:<this project>``, only for you (never an agent or Dervo's own actors), and only while
    the project's account is on the project's own Work or Personal side, the same rule Home's chat has
    (project_record_scope)."""
    from conversation_organisation import DERVO_ACTORS
    if channel != HOME_PREFIX + project or actor in DERVO_ACTORS:
        raise Rejected('These images belong to another project’s Home.')
    row = host.store.db.execute('SELECT profiles.category, COALESCE(projects.context, profiles.category) AS context FROM projects '
                                'JOIN profiles ON profiles.id=projects.profile_id WHERE projects.id=?', (project,)).fetchone()
    if row is None or row['category'] not in ('personal', 'work') or row['category'] != row['context']:
        raise Rejected('The project account changed or is unavailable.')


def scoped(host, project, channel, actor):
    host.require_beta_access()
    if type(project) is not str or type(channel) is not str:
        raise Rejected('Choose a conversation before attaching an image.')
    if tables_for(channel) is HOME:
        home_owner(host, project, channel, actor)
    else:
        host.store._access(channel, project, actor)
    db = host.store.db
    if db.execute('SELECT 1 FROM project_archives WHERE project_id=?', (project,)).fetchone():
        raise Rejected('Restore this project before opening its images.')
    if db.in_transaction:
        raise Rejected('Finish the current workspace save before changing images.')
    with db: schema(db)
    return db


def metadata(row):
    return {key: row[key] for key in ('id', 'name', 'state', 'width', 'height')} | {
        'size': row['expected_size'], 'received': len(row['data']) if row['state'] == 'uploading' else row['expected_size']}


def dispatch(host, operation, args, actor):
    if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
        raise Rejected('Invalid image request.')
    db = scoped(host, args['projectId'], args['channelId'], actor)
    if operation in {'image_begin', 'image_chunk', 'image_finish', 'image_discard', 'image_cancel', 'image_thumb_put'}:
        # Lock before reading quotas or upload state, including on another connection.
        with db:
            db.execute('BEGIN IMMEDIATE')
            return dispatch_scoped(db, operation, args)
    return dispatch_scoped(db, operation, args)


def dispatch_scoped(db, operation, args):
    t = tables_for(args['channelId'])
    # A thread's rows are keyed by its id; Home's by the project (the channel was checked against it in scoped).
    channel = args['projectId'] if t is HOME else args['channelId']
    if operation == 'image_drafts':
        return {'images': [metadata(row) for row in db.execute(
            f"SELECT * FROM {t.images} WHERE {t.key}=? AND state IN ('uploading','ready') "
            f'AND NOT EXISTS (SELECT 1 FROM {t.uses} WHERE image_id={t.images}.id) ORDER BY created_at,id', (channel,))]}
    if operation == 'image_cancel':
        client = args['clientId']
        if type(client) is not str or not re.fullmatch('[a-f0-9-]{32,36}', client):
            raise Rejected('Choose a valid image upload to cancel.')
        row = db.execute(f'SELECT id FROM {t.images} WHERE {t.key}=? AND client_id=?', (channel,client)).fetchone()
        if row and db.execute(f'SELECT 1 FROM {t.uses} WHERE image_id=?', (row['id'],)).fetchone():
            raise Rejected('This image belongs to a sent request. Check that request before removing it.')
        db.execute(f'INSERT OR IGNORE INTO {t.cancels} VALUES (?,?)', (channel,client))
        if row: db.execute(f"UPDATE {t.images} SET state='discarded',data=?,document_text=NULL,thumb=NULL WHERE id=?", (b'',row['id']))
        return {'removed': True, 'id': row['id'] if row else None}
    if operation == 'image_begin':
        name, size, digest, client = (args[k] for k in ('name','size','sha256','clientId'))
        if (type(name) is not str or not 1 <= len(name.encode()) <= 200 or any(ord(c) < 32 for c in name)
                or '/' in name or '\\' in name or type(size) is not int or not 1 <= size <= MAX_BYTES
                or type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest)
                or type(client) is not str or not re.fullmatch('[a-f0-9-]{32,36}', client)):
            raise Rejected('Choose a file up to 2 MB, or an image up to 2 MB after resizing.')
        if db.execute(f'SELECT 1 FROM {t.cancels} WHERE {t.key}=? AND client_id=?', (channel,client)).fetchone():
            raise Rejected('This upload was cancelled. Choose the image again.')
        row = db.execute(f'SELECT * FROM {t.images} WHERE {t.key}=? AND client_id=?', (channel,client)).fetchone()
        if row:
            if (row['name'],row['expected_size'],row['expected_hash']) != (name,size,digest) or row['state'] == 'discarded':
                raise Rejected('This image upload changed or was removed. Choose the image again.')
            return metadata(row)
        pending = db.execute(f"SELECT count(*) FROM {t.images} WHERE {t.key}=? AND state IN ('uploading','ready') "
            f'AND NOT EXISTS (SELECT 1 FROM {t.uses} WHERE image_id={t.images}.id)', (channel,)).fetchone()[0]
        if pending >= MAX_IMAGES: raise Rejected('Attach up to four images. Remove one before adding another.')
        if used_bytes(db) + size > WORKSPACE_BYTES: raise Rejected('Local image storage is full. Remove unused images before adding another.')
        image_id = uuid.uuid4().hex
        db.execute(f'INSERT INTO {t.images} (id,{t.key},client_id,name,expected_size,expected_hash,state,data,width,height,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (image_id,channel,client,name,size,digest,'uploading',b'',None,None,int(time.time())))
        return metadata(db.execute(f'SELECT * FROM {t.images} WHERE id=?', (image_id,)).fetchone())
    if type(args['id']) is not str:
        raise Rejected('This image isn’t available in this conversation.')
    row = db.execute(f'SELECT * FROM {t.images} WHERE id=? AND {t.key}=?', (args['id'],channel)).fetchone()
    if row is None or row['state'] == 'discarded':
        raise Rejected('This image isn’t available in this conversation.')
    if operation == 'image_chunk':
        offset, data = args['offset'], args['data']
        if row['state'] != 'uploading' or type(offset) is not int or offset < 0 or type(data) is not str or len(data) > CHUNK_BYTES * 4 // 3:
            raise Rejected('This image upload needs to be restarted.')
        try: incoming = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error): raise Rejected('This image chunk couldn’t be read.') from None
        current = row['data']
        if not incoming or offset + len(incoming) > row['expected_size']:
            raise Rejected('This image chunk is outside the upload.')
        if offset < len(current):
            if current[offset:offset+len(incoming)] != incoming: raise Rejected('This image chunk changed while retrying.')
            return metadata(row)
        if offset != len(current): raise Rejected('An earlier image chunk is missing. Retry the upload.')
        db.execute(f'UPDATE {t.images} SET data=? WHERE id=?', (current+incoming,row['id']))
    elif operation == 'image_finish':
        if row['state'] == 'ready': return metadata(row)
        if len(row['data']) != row['expected_size'] or hashlib.sha256(row['data']).hexdigest() != row['expected_hash']:
            raise Rejected('This image upload is incomplete. Retry it before sending.')
        document_text = None
        if row['data'].startswith(SIGNATURE):
            clean, width, height = normalize(row['data'])
        else:
            from document_attachments import extract
            document_text = extract(row['name'], row['data'])
            clean, width, height = row['data'], None, None
        if used_bytes(db) + max(0,len(clean)-row['expected_size']) + len((document_text or '').encode()) > WORKSPACE_BYTES:
            raise Rejected('Local image storage is full. Remove unused images before finishing this upload.')
        db.execute(f"UPDATE {t.images} SET data=?,width=?,height=?,document_text=?,state='ready' WHERE id=?", (clean,width,height,document_text,row['id']))
    elif operation == 'image_discard':
        if db.execute(f'SELECT 1 FROM {t.uses} WHERE image_id=?', (row['id'],)).fetchone():
            raise Rejected('This image is part of a sent message and can’t be removed from its draft.')
        db.execute(f"UPDATE {t.images} SET state='discarded',data=?,document_text=NULL,thumb=NULL WHERE id=?", (b'',row['id']))
        return {'removed': True}
    elif operation == 'image_thumb_put':
        if row['state'] != 'ready' or not row['data'].startswith(SIGNATURE):
            raise Rejected('Only a finished image can have a preview.')
        if db.execute(f'SELECT 1 FROM {t.uses} WHERE image_id=?', (row['id'],)).fetchone():
            raise Rejected('This image is part of a sent message and its preview can’t change.')
        data = args['data']
        if type(data) is not str or len(data) > THUMB_BYTES * 4 // 3 + 4:
            raise Rejected('This preview is too large.')
        try: raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error): raise Rejected('This preview couldn’t be read.') from None
        clean, width, height = normalize(raw)
        if max(width, height) > THUMB_EDGE or len(clean) > THUMB_BYTES:
            raise Rejected('This preview is too large.')
        # Within a couple of pixels of the image's own shape once scaled down, so a preview can't show a different picture's outline.
        if abs(width * row['height'] - height * row['width']) > 2 * max(row['width'], row['height']):
            raise Rejected('This preview doesn’t match the image.')
        if used_bytes(db) - len(row['thumb'] or b'') + len(clean) > WORKSPACE_BYTES:
            raise Rejected('Local image storage is full. Remove unused images before adding another.')
        db.execute(f'UPDATE {t.images} SET thumb=? WHERE id=?', (clean, row['id']))
        return {'saved': True}
    elif operation == 'image_thumb':
        if row['state'] != 'ready': raise Rejected('This image is still uploading.')
        if not row['data'].startswith(SIGNATURE):
            return metadata(row)
        picture = row['thumb'] or row['data']
        return metadata(row) | {'dataUrl': 'data:image/png;base64,' + base64.b64encode(picture).decode(), 'thumbnail': bool(row['thumb'])}
    elif operation == 'image_get':
        if row['state'] != 'ready': raise Rejected('This image is still uploading.')
        if not row['data'].startswith(SIGNATURE):
            from document_attachments import extract
            return metadata(row) | {'textPreview': row['document_text']}
        return metadata(row) | {'dataUrl':'data:image/png;base64,'+base64.b64encode(row['data']).decode()}
    return metadata(db.execute(f'SELECT * FROM {t.images} WHERE id=?', (row['id'],)).fetchone())


def checked_images(host, project, channel, actor, image_ids):
    """Host-only load before delivery; unsent images are never added to context automatically. Threads only: Home's
    images go through home_checked."""
    if type(channel) is not str or tables_for(channel) is not THREAD:
        raise Rejected('Choose up to four different images from this conversation.')
    db = scoped(host, project, channel, actor)
    if type(image_ids) is not list or len(image_ids) > MAX_IMAGES or any(type(i) is not str for i in image_ids) or len(set(image_ids)) != len(image_ids):
        raise Rejected('Choose up to four different images from this conversation.')
    rows = []
    for image_id in image_ids:
        row = db.execute("SELECT * FROM conversation_images WHERE id=? AND thread_id=? AND state='ready'", (image_id,channel)).fetchone()
        if row is None: raise Rejected('Finish uploading each image before sending.')
        rows.append(dict(row))
    from document_attachments import context
    context(rows)  # Refuse a combined document payload before recording a request.
    return rows


def pin_task_images(db, task):
    """Bind the ready images in the same transaction that saves their owning task."""
    if not db.in_transaction: raise Rejected('Image references need a workspace save.')
    for image_id in task.get('images', []):
        row = db.execute("SELECT id FROM conversation_images WHERE id=? AND thread_id=? AND state='ready'",
                         (image_id, task['context']['thread_id'])).fetchone()
        if row is None: raise Rejected('An attached image was removed before sending. Choose it again.')
        db.execute('INSERT OR IGNORE INTO conversation_image_uses VALUES (?,?,?)', (image_id, 'task', task['id']))


def task_rows(host, task):
    """Load saved image bytes only for their original conversation task at delivery time."""
    image_ids = task.get('images', [])
    if not image_ids: return ()
    ctx = task['context']
    rows = checked_images(host, ctx['project_id'], ctx['thread_id'], ctx['actor'], image_ids)
    for row in rows:
        if not host.store.db.execute('SELECT 1 FROM conversation_image_uses WHERE image_id=? AND source_kind=? AND source_id=?',
                                     (row['id'], 'task', task['id'])).fetchone():
            raise Rejected('This task’s image reference couldn’t be verified.')
    return rows


def task_payload(host, task):
    return tuple('data:image/png;base64,' + base64.b64encode(row['data']).decode()
                 for row in task_rows(host, task) if row['data'].startswith(SIGNATURE))


def task_documents(host, task):
    from document_attachments import context
    return context(task_rows(host, task))


def abandon_unstarted_task(coding, task_id, ctx):
    """Remove only this newly created task if attaching its request failed before launch."""
    import json
    db = coding.store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT document FROM coding_tasks WHERE id=?', (task_id,)).fetchone()
        evidence = db.execute('SELECT document FROM task_evidence WHERE id=?', (task_id,)).fetchone()
        if row is None or evidence is None: return False
        task, recorded = json.loads(row[0]), json.loads(evidence[0])
        if (task['context'] != ctx or task['state'] != 'needs_permission' or task['runs'] or task['tokens']
                or task['session_id'] or recorded.get('approval') or recorded.get('attempts')
                or db.execute('SELECT 1 FROM coding_requests WHERE task_id=?', (task_id,)).fetchone()
                or db.execute('SELECT 1 FROM coding_tool_audit WHERE task_id=?', (task_id,)).fetchone()
                or db.execute("SELECT 1 FROM conversation_image_uses WHERE source_kind='task' AND source_id=?", (task_id,)).fetchone()):
            return False
        db.execute('DELETE FROM task_activity WHERE task_id=?', (task_id,))
        db.execute('DELETE FROM coding_tasks WHERE id=?', (task_id,))
        db.execute('DELETE FROM task_evidence WHERE id=?', (task_id,))
    for pending in coding.pending.values(): pending.pop(task_id, None)
    return True


# ---------------------------------------------------------------- Home (#3)
def home_checked(host, project, actor, image_ids):
    """Home's images for one message: ready, in this project's Home, and not already sent with another message.
    Checked before the message is recorded, so a refusal leaves nothing behind."""
    if type(image_ids) is not list or not 1 <= len(image_ids) <= MAX_IMAGES or any(type(i) is not str for i in image_ids) \
            or len(set(image_ids)) != len(image_ids):
        raise Rejected('Choose up to four different images from this Home.')
    db = scoped(host, project, HOME_PREFIX + project, actor)
    for image_id in image_ids:
        if db.execute("SELECT 1 FROM home_images WHERE id=? AND project_id=? AND state='ready'", (image_id, project)).fetchone() is None:
            raise Rejected('Finish uploading each image before sending.')
        if db.execute('SELECT 1 FROM home_image_uses WHERE image_id=?', (image_id,)).fetchone():
            raise Rejected('That image was already sent. Attach it again to send it with this message.')
    from document_attachments import context
    context([dict(db.execute('SELECT * FROM home_images WHERE id=? AND project_id=?', (i, project)).fetchone()) for i in image_ids])
    return list(image_ids)


def pin_home_images(db, project, entry_id, image_ids):
    """Bind the images to your Home message in the same transaction that records it."""
    if not db.in_transaction: raise Rejected('Image references need a workspace save.')
    for image_id in image_ids:
        if db.execute("SELECT 1 FROM home_images WHERE id=? AND project_id=? AND state='ready'", (image_id, project)).fetchone() is None \
                or db.execute('SELECT 1 FROM home_image_uses WHERE image_id=?', (image_id,)).fetchone():
            raise Rejected('An attached image was removed before sending. Choose it again.')
        db.execute('INSERT INTO home_image_uses VALUES (?,?,?)', (image_id, 'home', entry_id))


def home_rows(host, project, actor, entry_id, image_ids):
    """The bytes for Home's model call, loaded only for the message they were sent with, as PNG data URLs."""
    if not image_ids: return ()
    db = scoped(host, project, HOME_PREFIX + project, actor)
    out = []
    for image_id in image_ids:
        row = db.execute("SELECT * FROM home_images WHERE id=? AND project_id=? AND state='ready'", (image_id, project)).fetchone()
        if row is None or not db.execute("SELECT 1 FROM home_image_uses WHERE image_id=? AND source_kind='home' AND source_id=?",
                                         (image_id, entry_id)).fetchone():
            raise Rejected('An image sent with this message couldn’t be verified.')
        out.append(dict(row))
    return tuple(out)


def home_payload(host, project, actor, entry_id, image_ids):
    return tuple('data:image/png;base64,' + base64.b64encode(row['data']).decode()
                 for row in home_rows(host, project, actor, entry_id, image_ids) if row['data'].startswith(SIGNATURE))


def home_documents(host, project, actor, entry_id, image_ids):
    from document_attachments import context
    return context(home_rows(host, project, actor, entry_id, image_ids))
