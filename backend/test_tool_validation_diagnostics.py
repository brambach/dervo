"""Safe validation diagnostics. No provider processes or accounts."""
import unittest
from codex_coding import validate_arguments
from claude_coding import validate_tool
from dervo_connections import Rejected


class ValidationDiagnosticsTests(unittest.TestCase):
    def test_helper_argument_failure_names_field_without_echoing_content(self):
        for validator in (validate_arguments, validate_tool):
            for field, value in [('instructions', 'PRIVATE' * 300), ('purpose', None), ('agent', '')]:
                args = dict(agent='fixer', purpose='Implement', instructions='Small change', files=['sample.py'])
                args[field] = value
                with self.subTest(validator=validator.__name__, field=field):
                    with self.assertRaises(Rejected) as caught:
                        validator('dervo_request_help', args)
                    text = str(caught.exception)
                    self.assertIn('dervo_request_help.' + field, text)
                    self.assertNotIn('PRIVATE', text)


    def test_protocol_id_failure_names_field_without_echoing_payload(self):
        from claude_coding import ClaudeCodingAdapter
        from codex_coding import TrustedTaskBinding
        from test_claude_coding import BINDING
        binding = dict(BINDING)
        adapter = ClaudeCodingAdapter(binding, lambda _: None, lambda *_: None,
            task=TrustedTaskBinding('diagnostic-task', binding, lambda *_: True))
        adapter.state = 'running'
        with self.assertRaises(Rejected) as caught:
            adapter.receive({'type': 'control_request', 'request_id': 'PRIVATE' * 100,
                          'request': {'subtype': 'mcp_message'}})
        self.assertIn('control_request.request_id', str(caught.exception))
        self.assertNotIn('PRIVATE', str(caught.exception))
        self.assertEqual(adapter.state, 'blocked')


    def test_oversized_helper_instructions_return_error_without_disconnect(self):
        from claude_coding import ClaudeCodingAdapter
        from codex_coding import TrustedTaskBinding, ALL_TOOLS
        from test_claude_coding import BINDING, SID
        sent = []
        adapter = ClaudeCodingAdapter(dict(BINDING), sent.append, lambda *_: None,
            task=TrustedTaskBinding('diagnostic-task', dict(BINDING), lambda *_: True, tools=ALL_TOOLS))
        adapter.state, adapter.session_id, adapter.turn_id = 'running', SID, 'turn'
        def message(rid, instructions):
            return {'type':'control_request', 'request_id':rid,
                'request':{'subtype':'mcp_message', 'server_name':'dervo',
                    'message':{'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                        'name':'dervo_request_help', 'arguments':dict(agent='fixer', purpose='Implement',
                            instructions=instructions, files=['sample.py'])}}}}
        adapter.receive(message('bad', 'PRIVATE' * 300))
        self.assertEqual(adapter.state, 'running')
        self.assertFalse(adapter._tools)
        result = sent[-1]['response']['response']['mcp_response']['result']
        self.assertTrue(result['isError'])
        self.assertIn('2000', result['content'][0]['text'])
        self.assertNotIn('PRIVATE', result['content'][0]['text'])
        adapter.receive(message('corrected', 'Implement the supplied contract.'))
        self.assertEqual(len(adapter._tools), 1)
        self.assertEqual(adapter.state, 'running')
        with self.assertRaises(Rejected):
            adapter.receive(message('bad', 'Now short'))
        self.assertEqual(adapter.state, 'blocked')


    def test_bad_call_error_results_name_fields_without_echoing_content(self):
        # 16.4 item 4: these texts go back to the model, so they name trusted tool and field names, never what it sent.
        from codex_coding import tool_call_problem
        tools = frozenset({'dervo_read_file', 'dervo_write_file'})
        for tool, args in [('PRIVATE' * 30, {}), ('dervo_read_file', {'path': 'a.py', 'PRIVATE': 'PRIVATE'}),
                           ('dervo_write_file', {'path': 'a.py', 'content': 'PRIVATE'}), ('dervo_read_file', 'PRIVATE')]:
            with self.subTest(tool=tool[:20], args=str(args)[:40]):
                text = tool_call_problem(tool, args, tools, 0, 0)
                self.assertTrue(text)
                self.assertNotIn('PRIVATE', text)
        self.assertIsNone(tool_call_problem('dervo_read_file', {'path': 'a.py'}, tools, 0, 0))


    def test_helper_schema_advertises_enforced_string_limits(self):
        from codex_coding import dynamic_tools, STRING_LIMITS
        spec = dynamic_tools(['dervo_request_help'])[0]
        for key in ('agent', 'purpose', 'instructions'):
            field = spec['inputSchema']['properties'][key]
            self.assertEqual(field.get('maxLength'), STRING_LIMITS[key])
            self.assertIn('UTF-8 bytes', field.get('description', ''))


if __name__ == '__main__':
    unittest.main()
