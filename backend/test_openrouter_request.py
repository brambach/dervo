from copy import deepcopy
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from uuid import uuid4

from dervo_connections import Rejected
from openrouter_budget import Budget
from openrouter_request import prepare
from release_profiles import stage

OWNER = ('z' * 20, 'https://' + 'z' * 20 + '.supabase.co/auth/v1', '12345678-1234-4234-8234-123456789abc')


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'model': 'openai/gpt-4.1-mini', 'systemPrompt': 'Help with this project.',
                        'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': 'Read the README.'}],
                                      'id': 'local-message', 'createdAt': 1000}],
                        'tools': []}
        self.selection = {'modelId': self.payload['model'], 'provider': 'openai', 'maxOutputTokens': 1000, 'allowFallbacks': False}
        self.endpoint = {'provider': 'openai', 'selectable': True, 'budgetContextTokens': 100000,
                         'maxOutputTokens': 4000, 'contextTokens': 100000, 'parameters': ['max_tokens', 'tools'],
                         'pricing': {'prompt': '0.0000004', 'completion': '0.0000016',
                                     'input_cache_read': '0', 'input_cache_write': '0'}}
        self.checked = {'modelId': self.payload['model'], 'endpoints': [self.endpoint]}

    def prepare(self, **kwargs):
        return prepare(self.payload, self.selection, self.checked, checked_at=100, now=kwargs.get('now', 120))

    def test_full_context_reserved_and_remote_price_ceiling_added(self):
        result = self.prepare()
        body = json.loads(result.body)
        self.assertEqual(result.upper_bound_usd, '0.041600000')
        self.assertEqual(body['provider'], {'only': ['openai'], 'order': ['openai'], 'allow_fallbacks': False,
                         'require_parameters': True, 'max_price': {'prompt': '0.4000000', 'completion': '1.6000000', 'request': '0'}})
        self.assertEqual(body['max_tokens'], 1000)
        self.assertNotIn('id', body['messages'][-1])
        self.assertNotIn('metadata', body)

    def test_shorter_prompt_does_not_reduce_the_reserved_bound(self):
        first = self.prepare().upper_bound_usd
        self.payload['messages'][0]['content'][0]['text'] = 'A'
        self.assertEqual(self.prepare().upper_bound_usd, first)

    def test_round_up_sub_nanodollar_cost(self):
        self.endpoint['budgetContextTokens'] = 1
        self.selection['maxOutputTokens'] = 1
        self.endpoint['pricing'].update(prompt='0.00000000001', completion='0')
        self.assertEqual(self.prepare().upper_bound_usd, '0.000000001')

    def test_request_and_cache_fees_are_counted(self):
        self.endpoint['pricing'].update(request='0.01', input_cache_write='0.0000005', input_cache_read='0.0000001')
        self.assertEqual(self.prepare().upper_bound_usd, '0.111600000')
        self.assertEqual(json.loads(self.prepare().body)['provider']['max_price']['request'], '0.01')

    def test_unknown_nonzero_cost_refuses(self):
        self.endpoint['pricing']['web_search'] = '0.005'
        with self.assertRaises(Rejected): self.prepare()

    def test_missing_cache_prices_cannot_understate_automatic_cache_charges(self):
        self.payload['model'] = self.selection['modelId'] = self.checked['modelId'] = 'openai/gpt-5.6'
        for missing in ('input_cache_read', 'input_cache_write'):
            rates = self.endpoint['pricing'].copy()
            del self.endpoint['pricing'][missing]
            with self.subTest(missing=missing), self.assertRaises(Rejected): self.prepare()
            self.endpoint['pricing'] = rates
        # The review counterexample costs $0.0511 for a 99k-token cache write.
        # Explicit checked fees reserve more than that, not the old $0.0416.
        self.endpoint['pricing'].update(input_cache_write='0.0000005', input_cache_read='0.0000001')
        self.assertEqual(self.prepare().upper_bound_usd, '0.101600000')

    def test_price_age_and_clock_fail_closed(self):
        for now in (99, 161, float('nan'), float('inf'), True):
            with self.subTest(now=now), self.assertRaises(Rejected): self.prepare(now=now)

    def test_fallback_and_model_changes_refuse(self):
        self.selection['allowFallbacks'] = True
        with self.assertRaises(Rejected): self.prepare()
        self.selection['allowFallbacks'] = False
        self.payload['model'] = 'openrouter/auto'
        with self.assertRaises(Rejected): self.prepare()

    def test_provider_side_tools_reasoning_and_images_refuse(self):
        for part in [{'type': 'reasoning', 'text': 'private'}, {'type': 'image', 'image': 'data:image/png;base64,fixture'},
                     {'type': 'tool-result', 'toolCallId': 'unknown', 'output': 'fake', 'execution': 'provider'}]:
            self.payload['messages'][0]['content'] = [part]
            with self.assertRaises(Rejected): self.prepare()

    def test_tool_call_and_result_project_without_extra_local_metadata(self):
        self.payload['tools'] = [{'name': 'dervo_read_file', 'description': 'Read', 'inputSchema': {'type': 'object'}}]
        self.payload['messages'].extend([
            {'role': 'assistant', 'content': [{'type': 'tool-call', 'toolCallId': 'one', 'toolName': 'dervo_read_file', 'input': {'path': 'README.md'}}]},
            {'role': 'tool', 'content': [{'type': 'tool-result', 'toolCallId': 'one', 'toolName': 'dervo_read_file', 'output': 'File contents', 'isError': False}]},
        ])
        body = json.loads(self.prepare().body)
        result = body['messages'][-1]
        self.assertEqual(result['role'], 'tool')
        self.assertEqual(result['tool_call_id'], 'one')
        self.assertEqual(json.loads(result['content']), {'success': True, 'text': 'File contents'})
        successful = self.prepare().body
        self.payload['messages'][-1]['content'][0]['isError'] = True
        failed = self.prepare().body
        self.assertNotEqual(successful, failed)
        self.assertEqual(json.loads(json.loads(failed)['messages'][-1]['content']), {'success': False, 'text': 'File contents'})
        self.payload['messages'][-1]['content'][0]['isError'] = 'false'
        with self.assertRaises(Rejected): self.prepare()
        self.payload['messages'][-1]['content'][0]['isError'] = False
        self.payload['messages'].append(deepcopy(self.payload['messages'][-1]))
        with self.assertRaises(Rejected): self.prepare()

    def test_second_model_call_cannot_exceed_total_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); stage(root, 'personal-experimental')
            db = sqlite3.connect(root / 'spending.sqlite3')
            self.addCleanup(db.close)
            budget = Budget(db, resources=root)
            budget.configure(OWNER, total='0.05', per_request='0.05', revision=0)
            first = str(uuid4())
            budget.reserve(OWNER, first, self.prepare().upper_bound_usd)
            budget.mark_sent(OWNER, first)
            budget.bind_generation(OWNER, first, 'gen-fixture')
            budget.settle(OWNER, first, cost='0.02', generation='gen-fixture')
            with self.assertRaises(Rejected): budget.reserve(OWNER, str(uuid4()), self.prepare().upper_bound_usd)
            self.assertEqual(budget.status(OWNER)['usedUsd'], '0.02')

    def test_another_models_cheaper_catalog_cannot_price_this_request(self):
        self.checked['modelId'] = 'openai/a-cheaper-model'
        with self.assertRaises(Rejected): self.prepare()


if __name__ == '__main__':
    unittest.main()
