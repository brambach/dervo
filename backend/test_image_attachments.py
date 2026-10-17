"""Offline attachment lifecycle tests. Images are synthetic; no provider is called."""
import base64
import hashlib
import struct
import sqlite3
import threading
import tempfile
import unittest
import uuid
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from host import Host, ACTOR
from dervo_connections import Rejected
from image_attachments import chunk, normalize, SIGNATURE, dispatch, checked_images, home_checked


def png(width=8, height=6, extra=b'', pixels=None):
    header = struct.pack('>IIBBBBB',width,height,8,6,0,0,0)
    pixels = (b'\0'+bytes([240,200,100,255])*width)*height if pixels is None else pixels
    return SIGNATURE+chunk(b'IHDR',header)+extra+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')


class RasterTests(unittest.TestCase):
    def test_metadata_stripped_and_dimensions_retained(self):
        clean,width,height = normalize(png(extra=chunk(b'tEXt',b'private metadata')))
        self.assertEqual((width,height),(8,6))
        self.assertNotIn(b'private metadata',clean)
        self.assertEqual(normalize(clean)[0],clean)

    def test_invalid_or_unbounded_rasters_are_refused(self):
        raw = png()
        for value in (b'',b'not a PNG',raw[:-2],raw+b'trailing',png(width=2049),
                      png(pixels=b'\0'*100000),png(pixels=b'\x05'+b'\0'*197),
                      raw[:40]+b'X'+raw[41:],png(extra=chunk(b'CRIT',b'unknown'))):
            with self.assertRaises(Rejected): normalize(value)


class AttachmentTests(unittest.TestCase):
    # HomeScopeTests runs every one of these again under Home's key, against Home's tables.
    IMAGES,USES='conversation_images','conversation_image_uses'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'workspace';self.host=Host(self.root)
        self.addCleanup(lambda:self.host.close())
        profile=self.host.dispatch('add_profile',{'provider':'codex','identity':'fixture@example.test','category':'personal'})['id']
        self.project=self.host.add_picked_project(self.temp.name,profile,'fixture')['id']
        self.channel=self.host.dispatch('create_conversation',{'projectId':self.project,'name':'Image trial','private':True})['id']
        self.scope={'projectId':self.project,'channelId':self.channel}
        self.raw=png()

    def call(self,op,**args): return self.host.dispatch(op,self.scope|args)

    def begin(self,**changes):
        self.begin_args={'clientId':uuid.uuid4().hex,'name':'Screenshot.png','size':len(self.raw),'sha256':hashlib.sha256(self.raw).hexdigest()}|changes
        return self.call('image_begin',**self.begin_args)

    def put(self):
        row=self.begin();image_id=row['id']
        self.call('image_chunk',id=image_id,offset=0,data=base64.b64encode(self.raw).decode())
        return self.call('image_finish',id=image_id)

    def test_csv_upload_preview_cancellation_and_scope(self):
        self.raw = b'name,total\nBryce,42\n'
        row = self.begin(name='sales.csv')
        self.call('image_chunk', id=row['id'], offset=0, data=base64.b64encode(self.raw).decode())
        self.call('image_finish', id=row['id'])
        preview = self.call('image_get', id=row['id'])
        self.assertEqual(preview['textPreview'], self.raw.decode())
        self.assertNotIn('dataUrl', preview)
        self.assertIsNone(preview['width'])
        with self.assertRaises(Rejected):
            self.host.dispatch('image_get', self.scope | {'channelId': 'not-this-conversation', 'id': row['id']})
        self.call('image_discard', id=row['id'])
        self.assertEqual(self.call('image_drafts'), {'images': []})
        self.assertIsNone(self.host.store.db.execute(f'SELECT document_text FROM {self.IMAGES} WHERE id=?', (row['id'],)).fetchone()[0])

    def test_a_tile_reads_its_small_picture_not_the_whole_image(self):
        self.raw=png(width=400,height=300)
        image_id=self.put()['id']
        before=self.call('image_thumb',id=image_id)
        self.assertEqual((before['thumbnail'],before['dataUrl']),(False,self.call('image_get',id=image_id)['dataUrl']),'an upload with no preview sends itself, as before')
        small=png(width=32,height=24)
        self.assertEqual(self.call('image_thumb_put',id=image_id,data=base64.b64encode(small).decode()),{'saved':True})
        thumb=self.call('image_thumb',id=image_id)
        self.assertTrue(thumb['thumbnail'])
        self.assertEqual(base64.b64decode(thumb['dataUrl'].split(',')[1]),normalize(small)[0])
        self.assertEqual((thumb['width'],thumb['height']),(400,300),'the image’s own size, not the preview’s')
        self.assertEqual(base64.b64decode(self.call('image_get',id=image_id)['dataUrl'].split(',')[1]),normalize(self.raw)[0],'opening it still reads the whole image')
        for data in (base64.b64encode(png(width=700,height=10)).decode(),base64.b64encode(b'not a PNG').decode(),'***'):
            with self.assertRaises(Rejected): self.call('image_thumb_put',id=image_id,data=data)
        self.call('image_discard',id=image_id)
        self.assertIsNone(self.host.store.db.execute(f'SELECT thumb FROM {self.IMAGES} WHERE id=?',(image_id,)).fetchone()[0])
        with self.assertRaises(Rejected): self.call('image_thumb',id=image_id)

    def test_a_preview_is_refused_where_it_could_mislead(self):
        small=base64.b64encode(png(width=32,height=24)).decode()
        self.raw=png(width=400,height=300)
        uploading=self.begin()['id']
        with self.assertRaises(Rejected,msg='an upload that hasn’t finished'):self.call('image_thumb_put',id=uploading,data=small)
        self.raw=b'name,total\nBryce,42\n'
        document=self.begin(name='sales.csv')['id']
        self.call('image_chunk',id=document,offset=0,data=base64.b64encode(self.raw).decode());self.call('image_finish',id=document)
        with self.assertRaises(Rejected,msg='a document, whose preview is never shown'):self.call('image_thumb_put',id=document,data=small)
        self.raw=png(width=400,height=300)
        image_id=self.put()['id']
        with self.assertRaises(Rejected,msg='another conversation'):
            self.host.dispatch('image_thumb_put',self.scope|{'channelId':'not-this-conversation','id':image_id,'data':small})
        with self.assertRaises(Rejected,msg='a different shape'):self.call('image_thumb_put',id=image_id,data=base64.b64encode(png(width=640,height=1)).decode())
        self.assertEqual(self.call('image_thumb_put',id=image_id,data=small),{'saved':True},'negative control: a finished draft image')
        with self.host.store.db:self.host.store.db.execute(f'INSERT INTO {self.USES} VALUES (?,?,?)',(image_id,'task','sent'))
        with self.assertRaises(Rejected,msg='a sent image keeps the picture it was sent with'):self.call('image_thumb_put',id=image_id,data=base64.b64encode(png(width=40,height=30)).decode())
        self.assertEqual(base64.b64decode(self.call('image_thumb',id=image_id)['dataUrl'].split(',')[1]),normalize(png(width=32,height=24))[0])
        self.assertEqual(self.host.store.db.execute(f'SELECT count(thumb) FROM {self.IMAGES} WHERE id IN (?,?)',(uploading,document)).fetchone()[0],0)

    def test_replacing_a_preview_only_counts_the_new_one(self):
        from image_attachments import used_bytes
        self.raw=png(width=400,height=300)
        image_id=self.put()['id']
        small=base64.b64encode(png(width=32,height=24)).decode()
        self.call('image_thumb_put',id=image_id,data=small)
        full=used_bytes(self.host.store.db)
        with patch('image_attachments.WORKSPACE_BYTES',full):
            self.assertEqual(self.call('image_thumb_put',id=image_id,data=small),{'saved':True},'the same preview again fits where the old one was')
            noisy=png(width=32,height=24,pixels=b''.join(b'\0'+uuid.uuid4().bytes*8 for _ in range(24)))
            with self.assertRaises(Rejected,msg='a bigger one past the limit'):self.call('image_thumb_put',id=image_id,data=base64.b64encode(noisy).decode())
        self.assertEqual(used_bytes(self.host.store.db),full)

    def test_a_document_tile_gets_no_picture(self):
        self.raw=b'name,total\nBryce,42\n'
        image_id=self.begin(name='sales.csv')['id']
        self.call('image_chunk',id=image_id,offset=0,data=base64.b64encode(self.raw).decode());self.call('image_finish',id=image_id)
        self.assertNotIn('dataUrl',self.call('image_thumb',id=image_id))

    def test_chunked_upload_retry_finish_restart_and_no_provider_work(self):
        row=self.begin();image_id=row['id'];half=len(self.raw)//2
        self.assertEqual(self.call('image_begin',**self.begin_args)['id'],image_id)
        for offset,data in [(0,self.raw[:half]),(0,self.raw[:half]),(half,self.raw[half:])]:
            self.call('image_chunk',id=image_id,offset=offset,data=base64.b64encode(data).decode())
        self.assertEqual(self.call('image_finish',id=image_id)['state'],'ready')
        self.assertEqual(self.call('image_finish',id=image_id)['state'],'ready')
        self.host.close();self.host=Host(self.root)
        self.assertEqual(self.call('image_drafts')['images'][0]['id'],image_id)
        image=self.call('image_get',id=image_id)
        self.assertEqual(base64.b64decode(image['dataUrl'].split(',')[1]),normalize(self.raw)[0])
        for table in ['messages','turns','events']:
            self.assertEqual(self.host.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_pasted_text_keeps_exact_file_bytes_and_survives_as_an_unsent_draft(self):
        text = '\ufeff  First line\r\n\tIndented café and 日本語 🙂\n\n' + ('  Keep spaces\tand newlines\r\n' * 120)
        self.raw = text.encode('utf-8')
        row = self.begin(name='Pasted text.txt')
        split = len(self.raw) // 2
        for offset, data in [(0, self.raw[:split]), (0, self.raw[:split]), (split, self.raw[split:])]:
            self.call('image_chunk', id=row['id'], offset=offset, data=base64.b64encode(data).decode())
        self.call('image_finish', id=row['id'])
        saved = self.host.store.db.execute(f'SELECT data FROM {self.IMAGES} WHERE id=?', (row['id'],)).fetchone()[0]
        self.assertEqual(saved, self.raw, 'the attached file retains every original UTF-8 byte')
        self.host.close()
        self.host = Host(self.root)
        self.assertEqual([item['id'] for item in self.call('image_drafts')['images']], [row['id']])
        self.assertEqual(self.call('image_get', id=row['id'])['textPreview'], text.removeprefix('\ufeff'))
        self.assertEqual(self.host.store.db.execute(f'SELECT count(*) FROM {self.USES}').fetchone()[0], 0)
        for table in ['messages', 'turns', 'events']:
            self.assertEqual(self.host.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_incomplete_or_changed_upload_is_never_readable(self):
        row=self.begin();image_id=row['id']
        for op in ('image_get','image_finish'):
            with self.assertRaises(Rejected):self.call(op,id=image_id)
        with self.assertRaises(Rejected):self.call('image_chunk',id=image_id,offset=1,data='YWJj')
        self.call('image_chunk',id=image_id,offset=0,data='YWJj')
        with self.assertRaises(Rejected):self.call('image_chunk',id=image_id,offset=0,data='ZGVm')
        with self.assertRaises(Rejected):checked_images(self.host,self.project,self.channel,ACTOR,[image_id])
        with self.assertRaises(Rejected):self.call('image_begin',**(self.begin_args|{'name':'Other.png'}))

    def test_private_owner_and_conversation_scope_are_checked(self):
        image_id=self.put()['id']
        other=self.host.dispatch('create_conversation',{'projectId':self.project,'name':'Other','private':True})['id']
        for args,actor in [(self.scope|{'id':image_id},'different-user'),(self.scope|{'id':image_id,'channelId':other},ACTOR),
                           (self.scope|{'id':image_id,'projectId':'wrong'},ACTOR)]:
            with self.assertRaises(Rejected):dispatch(self.host,'image_get',args,actor)
        with self.assertRaises(Rejected):checked_images(self.host,self.project,other,ACTOR,[image_id])

    def test_access_revocation_blocks_upload_and_read(self):
        image_id=self.put()['id']
        self.host.require_beta_access=Mock(side_effect=Rejected('Beta access required'))
        for op,args in [('image_get',{'id':image_id}),('image_drafts',{}),('image_discard',{'id':image_id})]:
            with self.assertRaises(Rejected):self.call(op,**args)

    def test_discarded_upload_cannot_resurrect_from_old_retry(self):
        row=self.put();image_id=row['id']
        self.call('image_discard',id=image_id)
        self.assertEqual(self.call('image_drafts'),{'images':[]})
        with self.assertRaises(Rejected):self.call('image_begin',**self.begin_args)
        with self.assertRaises(Rejected):self.call('image_get',id=image_id)
        self.assertEqual(self.host.store.db.execute(f'SELECT length(data) FROM {self.IMAGES} WHERE id=?',(image_id,)).fetchone()[0],0)

    def test_sent_reference_preserves_image_and_hides_it_from_drafts(self):
        image_id=self.put()['id']
        with self.host.store.db:self.host.store.db.execute(f'INSERT INTO {self.USES} VALUES (?,?,?)',(image_id,'task','fixture-task'))
        self.assertEqual(self.call('image_drafts'),{'images':[]})
        with self.assertRaises(Rejected):self.call('image_discard',id=image_id)
        self.assertEqual(len(checked_images(self.host,self.project,self.channel,ACTOR,[image_id])),1)
        with self.assertRaises(Rejected):checked_images(self.host,self.project,self.channel,ACTOR,[image_id,image_id])

    def test_upload_count_and_storage_quota_bound_unfinished_images(self):
        for _ in range(4):self.begin()
        with self.assertRaises(Rejected):self.begin()
        row=self.call('image_drafts')['images'][0]
        self.call('image_discard',id=row['id'])
        with patch('image_attachments.WORKSPACE_BYTES',len(self.raw)*3):
            with self.assertRaises(Rejected):self.begin()

    def test_invalid_fields_cannot_request_urls_paths_or_oversized_chunks(self):
        for change in ({'name':'../private.png'},{'size':True},{'size':3000000},{'sha256':'bad'}):
            with self.assertRaises(Rejected):self.begin(**change)
        image_id=self.begin()['id']
        for data in ('!'*10,'A'*65537):
            with self.assertRaises(Rejected):self.call('image_chunk',id=image_id,offset=0,data=data)
        with self.assertRaises(Rejected):self.call('image_get',id=image_id,url='https://invalid.test')

    def test_concurrent_begins_reserve_the_last_slot_once(self):
        for _ in range(3): self.begin()
        database = Path(self.temp.name) / 'concurrent-images.sqlite'
        with sqlite3.connect(database) as snapshot:
            self.host.store.db.backup(snapshot)
        first_read, release_first, second_read = (threading.Event() for _ in range(3))
        results = []
        images_table = self.IMAGES

        class Connection:
            def __init__(self, connection, first):
                self.connection, self.first = connection, first
            def __getattr__(self, name): return getattr(self.connection, name)
            def __enter__(self): self.connection.__enter__(); return self
            def __exit__(self, *args): return self.connection.__exit__(*args)
            def execute(self, sql, args=()):
                cursor = self.connection.execute(sql, args)
                if sql.startswith(f'SELECT count(*) FROM {images_table}'):
                    result = cursor.fetchone()
                    if self.first:
                        first_read.set()
                        if not release_first.wait(3): raise AssertionError('First upload was not released')
                    else: second_read.set()
                    return SimpleNamespace(fetchone=lambda: result)
                return cursor

        def begin(first):
            connection = sqlite3.connect(database, timeout=3)
            connection.row_factory = sqlite3.Row
            host = SimpleNamespace(require_beta_access=lambda: None,
                store=SimpleNamespace(db=Connection(connection, first), _access=lambda *args: None))
            try:
                dispatch(host, 'image_begin', self.scope | {
                    'clientId': uuid.uuid4().hex, 'name': 'Screenshot.png', 'size': len(self.raw),
                    'sha256': hashlib.sha256(self.raw).hexdigest()}, ACTOR)
                results.append('saved')
            except Rejected: results.append('refused')
            except Exception as error: results.append(type(error).__name__)
            finally: connection.close()

        first = threading.Thread(target=begin, args=(True,))
        second = threading.Thread(target=begin, args=(False,))
        first.start()
        try:
            self.assertTrue(first_read.wait(3))
            second.start()
            # The second connection must not read a quota already reserved by the first.
            self.assertFalse(second_read.wait(.2))
        finally:
            release_first.set()
            first.join(4)
            if second.ident is not None: second.join(4)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertCountEqual(results, ['saved', 'refused'])
        with sqlite3.connect(database) as connection:
            self.assertEqual(connection.execute(f'SELECT count(*) FROM {self.IMAGES}').fetchone()[0], 4)

    def test_rejected_mutation_rolls_back_without_owning_an_existing_save(self):
        image_id = self.begin()['id']
        db = self.host.store.db
        with self.assertRaises(Rejected): self.call('image_chunk', id=image_id, offset=1, data='YWJj')
        self.assertFalse(db.in_transaction)
        db.execute('BEGIN IMMEDIATE')
        db.execute(f'UPDATE {self.IMAGES} SET name=? WHERE id=?', ('Unsaved.png', image_id))
        with self.assertRaises(Rejected): self.call('image_discard', id=image_id)
        self.assertTrue(db.in_transaction)
        db.rollback()
        self.assertEqual(self.call('image_drafts')['images'][0]['name'], 'Screenshot.png')

    def test_cancel_by_client_handles_unknown_begin_and_late_replay(self):
        row = self.begin()
        args = dict(self.begin_args)
        self.assertEqual(self.call('image_cancel', clientId=args['clientId']), {'removed': True, 'id': row['id']})
        self.assertEqual(self.call('image_drafts')['images'], [])
        with self.assertRaises(Rejected): self.call('image_begin', **args)
        # Cancel can win before an uncertain begin reaches the host.
        args['clientId'] = uuid.uuid4().hex
        self.assertEqual(self.call('image_cancel', clientId=args['clientId']), {'removed': True, 'id': None})
        with self.assertRaises(Rejected): self.call('image_begin', **args)
        image_id = self.put()['id']
        with self.host.store.db:
            self.host.store.db.execute(f'INSERT INTO {self.USES} VALUES (?,?,?)', (image_id, 'task', 'saved'))
        with self.assertRaises(Rejected): self.call('image_cancel', clientId=self.begin_args['clientId'])
        self.assertEqual(self.call('image_get', id=image_id)['state'], 'ready')


class HomeScopeTests(AttachmentTests):
    """Home's images (#3): channelId home:<projectId>, the project's owner only, the same limits and checks as a thread,
    and never readable from a thread, another project's Home or another account's scope."""
    IMAGES,USES='home_images','home_image_uses'
    # Thread-only: a private thread's other local identity. Home has no private side; its owner rule is tested below.
    test_private_owner_and_conversation_scope_are_checked=None

    def setUp(self):
        super().setUp()
        self.thread_scope=self.scope
        self.scope={'projectId':self.project,'channelId':'home:'+self.project}
        profile=self.host.dispatch('add_profile',{'provider':'codex','identity':'other@example.test','category':'personal'})['id']
        Path(self.temp.name,'other').mkdir()
        self.other=self.host.add_picked_project(str(Path(self.temp.name,'other')),profile,'fixture')['id']

    def test_sent_reference_preserves_image_and_hides_it_from_drafts(self):
        # Home's version: a sent image stays readable, leaves the drafts, can't be removed, and can't be sent twice.
        image_id=self.put()['id']
        self.assertEqual(home_checked(self.host,self.project,ACTOR,[image_id]),[image_id],'negative control: unsent is fine')
        with self.host.store.db:self.host.store.db.execute(f'INSERT INTO {self.USES} VALUES (?,?,?)',(image_id,'home','entry'))
        self.assertEqual(self.call('image_drafts'),{'images':[]})
        with self.assertRaises(Rejected):self.call('image_discard',id=image_id)
        self.assertEqual(self.call('image_get',id=image_id)['state'],'ready')
        with self.assertRaises(Rejected):home_checked(self.host,self.project,ACTOR,[image_id])
        other=self.put()['id']
        with self.assertRaises(Rejected):home_checked(self.host,self.project,ACTOR,[other,other])

    def refused(self,op,scope,**args):
        with self.assertRaises(Rejected):self.host.dispatch(op,scope|args)

    def test_home_images_live_in_their_own_table(self):
        image_id=self.put()['id']
        db=self.host.store.db
        self.assertEqual(db.execute('SELECT project_id FROM home_images WHERE id=?',(image_id,)).fetchone()[0],self.project)
        self.assertIsNone(db.execute('SELECT 1 FROM conversation_images WHERE id=?',(image_id,)).fetchone())

    def test_home_and_thread_ids_never_cross(self):
        home_id=self.put()['id']
        self.scope=self.thread_scope;thread_id=self.put()['id'];self.scope={'projectId':self.project,'channelId':'home:'+self.project}
        # Negative controls first: each id opens in its own scope.
        self.assertEqual(self.call('image_get',id=home_id)['id'],home_id)
        self.assertEqual(self.host.dispatch('image_get',self.thread_scope|{'id':thread_id})['id'],thread_id)
        self.assertEqual([row['id'] for row in self.call('image_drafts')['images']],[home_id])
        for op in ('image_get','image_discard','image_finish'):
            self.refused(op,self.thread_scope,id=home_id)
            self.refused(op,self.scope,id=thread_id)
        self.refused('image_chunk',self.thread_scope,id=home_id,offset=0,data='AA==')
        # A thread's send path can't pick up a Home id, and Home's can't pick up a thread's.
        with self.assertRaises(Rejected):checked_images(self.host,self.project,self.channel,ACTOR,[home_id])
        with self.assertRaises(Rejected):checked_images(self.host,self.project,'home:'+self.project,ACTOR,[home_id])
        with self.assertRaises(Rejected):home_checked(self.host,self.project,ACTOR,[thread_id])
        self.assertEqual(home_checked(self.host,self.project,ACTOR,[home_id]),[home_id])
        self.assertEqual(checked_images(self.host,self.project,self.channel,ACTOR,[thread_id])[0]['id'],thread_id)

    def test_another_projects_home_is_refused_both_ways(self):
        home_id=self.put()['id']
        mine={'projectId':self.project,'channelId':'home:'+self.project}
        for scope in ({'projectId':self.other,'channelId':'home:'+self.project},{'projectId':self.project,'channelId':'home:'+self.other},
                      {'projectId':self.project,'channelId':'home:'+self.project+'x'},{'projectId':self.project,'channelId':'home:'},
                      {'projectId':'missing','channelId':'home:missing'}):
            for op in ('image_drafts',):self.refused(op,scope)
            self.refused('image_get',scope,id=home_id)
        other={'projectId':self.other,'channelId':'home:'+self.other}
        self.assertEqual(self.host.dispatch('image_drafts',other)['images'],[],'the other project’s own Home works and is empty')
        self.refused('image_get',other,id=home_id)
        with self.assertRaises(Rejected):home_checked(self.host,self.other,ACTOR,[home_id])
        self.assertEqual(self.host.dispatch('image_get',mine|{'id':home_id})['id'],home_id)

    def test_only_the_owner_on_a_personal_account(self):
        home_id=self.put()['id']
        # Dervo's own actors never reach Home's images, even with the right key.
        from conversation_organisation import HOME_ACTOR
        with self.assertRaises(Rejected):dispatch(self.host,'image_get',self.scope|{'id':home_id},HOME_ACTOR)
        self.assertEqual(dispatch(self.host,'image_get',self.scope|{'id':home_id},ACTOR)['id'],home_id)
        # The project on a work account: Home isn't open there, and neither are its images.
        work=self.host.dispatch('add_profile',{'provider':'codex','identity':'work@example.test','category':'work'})['id']
        db=self.host.store.db
        with db:db.execute('UPDATE projects SET profile_id=? WHERE id=?',(work,self.project))
        self.refused('image_get',self.scope,id=home_id)
        self.refused('image_drafts',self.scope)
        with db:db.execute('UPDATE projects SET profile_id=(SELECT profile_id FROM threads WHERE id=?) WHERE id=?',(self.channel,self.project))
        self.assertEqual(self.call('image_get',id=home_id)['id'],home_id)

    def test_same_limits_and_checks_as_a_thread(self):
        self.refused('image_begin',self.scope,clientId=uuid.uuid4().hex,name='big.png',size=2*1024*1024+1,sha256='0'*64)
        self.refused('image_begin',self.scope,clientId=uuid.uuid4().hex,name='a/b.png',size=100,sha256='0'*64)
        self.raw=b'\x89PNG\r\n\x1a\n'+b'not really a png'*8
        row=self.begin()
        self.call('image_chunk',id=row['id'],offset=0,data=base64.b64encode(self.raw).decode())
        self.refused('image_finish',self.scope,id=row['id'])
        self.raw=png()
        for _ in range(3):self.put()
        self.refused('image_begin',self.scope,clientId=uuid.uuid4().hex,name='fifth.png',size=len(self.raw),sha256=hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(len(self.call('image_drafts')['images']),4,'the broken upload counts until it’s removed, as in a thread')
