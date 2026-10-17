"""Offline HTTP peers only. No personal keys or paid model requests."""
from email.message import Message
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from uuid import uuid4

from dervo_connections import Rejected
from openrouter_budget import Budget
from openrouter_request import Prepared
from openrouter_stream import LINE_LIMIT, StreamClient
from release_profiles import stage

KEY = 'sk-or-v1-' + 'fixture' * 8
MODEL = 'openai/gpt-4.1-mini'
OWNER = ('z' * 20, 'https://' + 'z' * 20 + '.supabase.co/auth/v1', '12345678-1234-4234-8234-123456789abc')


def chunk(delta=None, reason=None, **extra):
    return {'id': 'gen-fixture', 'model': MODEL, 'object': 'chat.completion.chunk',
            'choices': [{'index': 0, 'delta': delta or {}, 'finish_reason': reason}], **extra}


def wire(*rows):
    return ''.join('data: ' + (row if type(row) is str else json.dumps(row)) + '\n\n' for row in rows).encode()


class Response(io.BytesIO):
    def __init__(self, raw, *, status=200, content_type='text/event-stream', generation=None):
        super().__init__(raw)
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = content_type
        if generation: self.headers['X-Generation-Id'] = generation


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='dervo-openrouter-stream-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        stage(self.root, 'personal-experimental')
        self.body = Prepared(json.dumps({'model': MODEL, 'stream': True}).encode(), '0.1')
        self.allowed, self.sent, self.events, self.generations = True, 0, [], []
        self.opener = Mock()
        self.client = StreamClient(self.root, opener=self.opener)

    def run_stream(self, raw, **kwargs):
        response = Response(raw, **kwargs)
        self.opener.open.return_value = response
        def sent(): self.sent += 1
        try:
            return self.client.run(KEY, self.body, allowed=lambda: self.allowed, before_send=sent,
                                   on_generation=self.generations.append, emit=self.events.append)
        finally:
            if self.opener.open.called:
                self.assertTrue(response.closed)

    def test_streams_text_and_accepts_repeated_finish_in_usage_frame(self):
        result = self.run_stream(b': keepalive\n\n' + wire(chunk({'role': 'assistant', 'content': 'Hello'}),
            chunk(reason='stop'), chunk({'content': '', 'role': 'assistant'}, 'stop', usage={'total_tokens': 4}), '[DONE]'))
        self.assertEqual(self.events, [{'type': 'text-delta', 'text': 'Hello'}])
        self.assertEqual((result.generation, result.reason), ('gen-fixture', 'stop'))
        self.assertEqual(self.generations, ['gen-fixture'])
        self.assertEqual(self.sent, 1)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(request.method, 'POST')
        self.assertEqual(request.data, self.body.body)

    def test_fragmented_tools_keep_their_index_and_arguments(self):
        result = self.run_stream(wire(chunk({'tool_calls': [{'index': 0, 'id': 'call-one', 'type': 'function',
            'function': {'name': 'dervo_read_file', 'arguments': '{"path":'}}]}),
            chunk({'tool_calls': [{'index': 0, 'function': {'arguments': '"README.md"}'}}]}),
            chunk(reason='tool_calls'), '[DONE]'))
        self.assertEqual(result.reason, 'tool-calls')
        self.assertEqual(self.events[1], {'type': 'tool-call-delta', 'index': 0, 'inputText': '"README.md"}'})
        self.assertFalse(any(e['type'] == 'finish' for e in self.events))

    def test_no_post_or_sent_record_when_permission_is_missing(self):
        self.allowed = False
        with self.assertRaises(Rejected): self.run_stream(b'')
        self.opener.open.assert_not_called()
        self.assertEqual(self.sent, 0)

    def test_private_beta_refuses_before_transport_construction(self):
        stage(self.root, 'private-beta')
        with self.assertRaises(ValueError): StreamClient(self.root, opener=self.opener)
        self.opener.open.assert_not_called()

    def test_midstream_error_is_sanitized_and_never_retried(self):
        with self.assertRaises(Rejected) as error:
            self.run_stream(wire(chunk({'content': 'Started'}), chunk(error={'message': KEY})))
        self.assertNotIn(KEY, str(error.exception))
        self.assertEqual(self.events, [{'type': 'text-delta', 'text': 'Started'}])
        self.assertEqual(self.generations, ['gen-fixture'])
        self.opener.open.assert_called_once()

    def test_eof_without_done_and_content_after_finish_refuse(self):
        for raw in (wire(chunk({'content': 'partial'})), wire(chunk(reason='stop')),
                    wire(chunk(reason='stop'), chunk({'content': 'late'}), '[DONE]'),
                    wire(chunk(reason='stop'), '[DONE]', chunk({'content': 'late'}))):
            with self.subTest(raw=raw), self.assertRaises(Rejected): self.run_stream(raw)

    def test_wrong_generation_or_model_refuses_before_content(self):
        for extra in ({'id': 'gen-other'}, {'model': 'other/model'}):
            row = chunk({'content': 'wrong'}, **extra)
            with self.subTest(extra=extra), self.assertRaises(Rejected):
                self.run_stream(wire(row), generation='gen-fixture')
        self.assertEqual(self.events, [])

    def test_reasoning_media_and_length_finish_are_not_accepted_as_success(self):
        for row in (chunk({'reasoning': 'private'}), chunk({'images': [{'url': 'private'}]}),
                    chunk({'content': 'cut off'}, 'length')):
            with self.subTest(row=row), self.assertRaises(Rejected): self.run_stream(wire(row, '[DONE]'))
        self.assertEqual(self.events, [])

    def test_duplicate_json_fields_non_json_and_line_limit_refuse(self):
        for raw in (b'data: {"id":"one","id":"two"}\n\n', b'data: not-json\n\n',
                    b'data: ' + b'x' * LINE_LIMIT + b'\n\n'):
            with self.subTest(length=len(raw)), self.assertRaises(Rejected): self.run_stream(raw)

    def test_multiline_data_and_crlf(self):
        raw = json.dumps(chunk({'content': 'hello'})).replace(', "model"', ',\ndata: "model"', 1)
        result = self.run_stream(('data: ' + raw + '\n\n').replace('\n', '\r\n').encode() + wire(chunk(reason='stop'), '[DONE]'))
        self.assertEqual(result.reason, 'stop')
        self.assertEqual(self.events, [{'type': 'text-delta', 'text': 'hello'}])

    def test_cancellation_after_read_prevents_delivery(self):
        response = Response(wire(chunk({'content': 'late'}), chunk(reason='stop'), '[DONE]'))
        original = response.readline
        def read(limit):
            self.allowed = False
            return original(limit)
        response.readline = read
        self.opener.open.return_value = response
        with self.assertRaises(Rejected):
            self.client.run(KEY, self.body, allowed=lambda: self.allowed, before_send=lambda: None,
                            on_generation=self.generations.append, emit=self.events.append)
        self.assertTrue(response.closed)
        self.assertEqual(self.events, [])

    def test_failed_post_retains_durable_reservation_and_generation_for_recovery(self):
        db = sqlite3.connect(self.root / 'budget.sqlite3')
        self.addCleanup(db.close)
        budget = Budget(db, resources=self.root)
        budget.configure(OWNER, total='0.20', per_request='0.10', revision=0)
        request = str(uuid4())
        budget.reserve(OWNER, request, self.body.upper_bound_usd)
        response = Response(wire(chunk({'content': 'Started'}), chunk(error={'message': 'connection lost'})))
        self.opener.open.return_value = response
        with self.assertRaises(Rejected):
            self.client.run(KEY, self.body, allowed=lambda: True,
                            before_send=lambda: budget.mark_sent(OWNER, request),
                            on_generation=lambda g: budget.bind_generation(OWNER, request, g), emit=self.events.append)
        budget.uncertain(OWNER, request)
        self.assertEqual(budget.receipt_generation(OWNER, request), 'gen-fixture')
        self.assertEqual(budget.status(OWNER)['reservedUsd'], '0.1')
        with self.assertRaises(Rejected): budget.reserve(OWNER, str(uuid4()), '0.01')
        # A verified receipt can reconcile this exact generation without a POST.
        budget.settle(OWNER, request, generation='gen-fixture', cost='0.02')
        self.assertEqual(budget.status(OWNER)['usedUsd'], '0.02')
        self.assertEqual(budget.status(OWNER)['reservedUsd'], '0')
        self.opener.open.assert_called_once()

    def test_non_stream_response_retains_header_generation_without_body_exposure(self):
        with self.assertRaises(Rejected):
            self.run_stream(KEY.encode(), content_type='application/json', generation='gen-fixture')
        self.assertEqual(self.generations, ['gen-fixture'])
        self.assertEqual(self.events, [])

    def test_escaped_or_split_keys_never_reach_callbacks(self):
        encoded = ''.join('\\u%04x' % ord(char) for char in KEY)
        escaped = wire(chunk({'content': 'PLACEHOLDER'}, 'stop')).replace(b'PLACEHOLDER', encoded.encode())
        cases = [escaped + wire('[DONE]'),
                 wire(chunk({'content': KEY[:20]}), chunk({'content': KEY[20:]}, 'stop'), '[DONE]'),
                 wire(chunk({'content': 'wrong'}, id=KEY)),
                 wire(chunk({'tool_calls': [{'index': 0, 'id': 'call-one', 'function': {
                     'name': 'dervo_read_file', 'arguments': '{"path":"' + encoded[:60]}}]}),
                      chunk({'tool_calls': [{'index': 0, 'function': {'arguments': encoded[60:] + '"}'}}]}, 'tool_calls'), '[DONE]')]
        for raw in cases:
            self.events, self.generations = [], []
            with self.subTest(size=len(raw)), self.assertRaises(Rejected): self.run_stream(raw)
            self.assertNotIn(KEY, json.dumps(self.events) + json.dumps(self.generations))
            self.assertEqual(self.events, [])
        with self.assertRaises(Rejected): self.run_stream(b'', generation=KEY)
        self.assertNotIn(KEY, self.generations)

    def test_http_error_binds_safe_header_generation_and_does_not_read_body(self):
        from urllib.error import HTTPError
        headers = Message(); headers['X-Generation-Id'] = 'gen-error-fixture'
        response = io.BytesIO(KEY.encode())
        error = HTTPError('https://openrouter.ai/api/v1/chat/completions', 500, KEY, headers, response)
        self.opener.open.side_effect = error
        with self.assertRaises(Rejected) as caught:
            self.client.run(KEY, self.body, allowed=lambda: True, before_send=lambda: None,
                            on_generation=self.generations.append, emit=self.events.append)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertEqual(self.generations, ['gen-error-fixture'])
        self.assertEqual(self.events, [])
        self.assertTrue(response.closed)
        self.opener.open.assert_called_once()


if __name__ == '__main__':
    unittest.main()
