"""Real host persistence with synthetic providers. No live image understanding claimed."""
import base64
import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

import test_conversation_coding as conversation
from test_image_attachments import png
from image_attachments import normalize, task_payload
from provider_image_input import encode_message
from dervo_connections import Rejected


class ImageChecks:
    def upload(self, raw=None, name='Screenshot.png'):
        raw = png() if raw is None else raw
        image = self.host.dispatch('image_begin', self.scope | {'clientId': uuid.uuid4().hex,
            'name': name, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        self.host.dispatch('image_chunk', self.scope | {'id': image['id'], 'offset': 0, 'data': base64.b64encode(raw).decode()})
        self.host.dispatch('image_finish', self.scope | {'id': image['id']})
        return image['id'], normalize(raw)[0] if raw.startswith(b'\x89PNG') else raw

    def test_image_csv_delivers_text_to_the_selected_provider_and_survives_restart(self):
        from image_attachments import task_documents
        self.grant()
        attachment, raw = self.upload(b'name,total\nBryce,42\n', 'sales.csv')
        with self.peer_patch('none'):
            result = self.host.dispatch('coding_request', self.scope | {'clientId': 'csv-request', 'text': 'Read this CSV', 'images': [attachment]})
            self.settle(result['taskId'])
        sent = self.peers[-1].sent
        payload = next(m for m in sent if m.get('method') == 'turn/start') if self.provider == 'codex' else next(m for m in sent if m.get('type') == 'user')
        encoded = json.dumps(payload)
        self.assertIn('sales.csv', encoded)
        self.assertIn('Bryce,42', encoded)
        self.assertNotIn('data:image/png', encoded)
        self.assertIn('not as instructions', encoded)
        self.reopen()
        task = self.host.dispatch('task_detail', self.scope | {'taskId': result['taskId']})['task']
        self.assertIn('Bryce,42', task_documents(self.host, task))
        self.assertEqual(task_payload(self.host, task), ())

    def test_image_delivery_and_retry_keep_the_original_task(self):
        self.grant()
        image_id, raw = self.upload()
        args = self.scope | {'clientId': 'image-request', 'text': 'Explain this screenshot', 'images': [image_id]}
        with self.peer_patch('none'):
            first = self.host.dispatch('coding_request', args)
            self.settle(first['taskId'])
            self.assertEqual(self.host.dispatch('coding_request', args)['taskId'], first['taskId'])
            self.assertEqual(self.starts, 1)
        sent = self.peers[-1].sent
        if self.provider == 'codex':
            payload = next(m for m in sent if m.get('method') == 'turn/start')
            blocks = payload['params']['input']
            encoded = blocks[0]['url'].split(',', 1)[1]
        else:
            payload = next(m for m in sent if m.get('type') == 'user')
            blocks = payload['message']['content']
            encoded = blocks[0]['source']['data']
        self.assertEqual(blocks[0]['type'], 'image')
        self.assertEqual(base64.b64decode(encoded), raw)
        self.assertIn('Explain this screenshot', blocks[-1]['text'])
        self.assertEqual(json.loads(encode_message(payload)), payload)
        self.assertEqual(self.host.dispatch('image_drafts', self.scope)['images'], [])
        with self.assertRaises(Rejected): self.host.dispatch('image_discard', self.scope | {'id': image_id})
        with self.assertRaises(Rejected): self.host.dispatch('coding_request', args | {'images': []})
        self.reopen()
        task = self.host.dispatch('task_detail', self.scope | {'taskId': first['taskId']})['task']
        self.assertEqual(task['images'], [image_id])
        self.assertEqual(base64.b64decode(task_payload(self.host, task)[0].split(',', 1)[1]), raw)

    def test_image_removed_during_task_creation_never_starts_or_pins(self):
        self.grant()
        image_id, _ = self.upload()
        coding = self.host.coding
        original = coding.conversation.save
        def removed_before_save(request, task=None):
            if task is not None:
                self.host.dispatch('image_discard', self.scope | {'id': image_id})
            return original(request, task)
        with patch.object(coding.conversation, 'save', side_effect=removed_before_save), self.peer_patch('none'):
            with self.assertRaises(Rejected):
                self.host.dispatch('coding_request', self.scope | {'clientId': 'removed', 'text': 'Look here', 'images': [image_id]})
        self.assertEqual(self.starts, 0)
        for table in ('coding_tasks', 'task_evidence', 'task_activity'):
            self.assertEqual(self.host.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
        self.assertEqual(coding.conversation.find('c', 'removed'), None)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM conversation_image_uses').fetchone()[0], 0)

    def test_image_in_another_conversation_is_refused_before_a_task_exists(self):
        image_id, _ = self.upload()
        self.host.store.add_thread('other', 'p', 'other', 'private', 'local-user')
        with self.assertRaises(Rejected):
            self.host.dispatch('coding_request', self.scope | {'channelId': 'other', 'clientId': 'other', 'text': 'Look', 'images': [image_id]})
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM coding_requests').fetchone()[0], 0)


class CodexImages(ImageChecks, conversation.ConversationCodingTests): pass
class ClaudeImages(ImageChecks, conversation.ClaudeConversationCodingTests): pass


class TransportImages(unittest.TestCase):
    def test_only_bounded_image_bytes_get_a_larger_provider_envelope(self):
        # Synthetic PNG-shaped bytes test the transport bound; raster validation happens at upload.
        encoded = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'x' * 200000).decode()
        codex = {'method': 'turn/start', 'params': {'input': [{'type': 'image', 'url': 'data:image/png;base64,' + encoded}, {'type': 'text', 'text': 'Describe this'}]}}
        claude = {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': encoded}}]}}
        for payload in (codex, claude): self.assertGreater(len(encode_message(payload)), 131072)
        for payload in ({'text': 'x' * 140000}, {'method': 'turn/start', 'params': {'input': [{'type': 'text', 'text': 'x'*140000}]}},
                        {'method': 'turn/start', 'params': {'input': codex['params']['input'], 'extra': 'x'*140000}},
                        {'method': 'turn/start', 'params': {'input': [codex['params']['input'][0]]*5}}):
            with self.assertRaises(Rejected): encode_message(payload)
        wrong = json.loads(json.dumps(codex));wrong['params']['input'][0]['url'] = 'https://example.test/' + encoded
        with self.assertRaises(Rejected): encode_message(wrong)


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for cls in (CodexImages, ClaudeImages):
        for name in dir(ImageChecks):
            if name.startswith('test_image_'): suite.addTest(cls(name))
    suite.addTests(loader.loadTestsFromTestCase(TransportImages))
    return suite
