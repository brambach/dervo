"""Error reports: an error's shape leaves the Mac, never its text. Temporary stores and fakes only; nothing is sent."""
import json
from pathlib import Path
import tempfile
import unittest

import error_reports
from supabase_client import AccountError, SupabaseClient, _error_document
from test_usage_events import Base, Sent, SignedIn
from usage_events import NOTICE_MARK, UsageEvents

SECRET = '/Users/maya/secret-project/launch-plan.md'


def failing_open():
    raise FileNotFoundError(2, 'No such file or directory: ' + SECRET)


def caught(function, *args):
    try:
        function(*args)
    except Exception as error:
        return error
    raise AssertionError('expected an error')


class Shape(unittest.TestCase):
    def test_a_message_holding_a_path_never_leaves_the_mac(self):
        error = caught(failing_open)
        self.assertIn(SECRET, str(error))  # negative control: the text is there to leak
        report = error_reports.from_exception(error, operation='project_explore')
        self.assertEqual(report, {'source': 'host', 'error_type': 'FileNotFoundError', 'file': 'test_error_reports.py',
                                  'function': 'failing_open', 'line': failing_open.__code__.co_firstlineno + 1,
                                  'operation': 'project_explore', 'provider': None})
        for word in ('maya', 'secret', 'launch-plan', '/Users', 'No such file'):
            self.assertNotIn(word, json.dumps(report))

    def test_frames_outside_dervos_code_are_dropped(self):
        # The error is raised inside the standard library: the report names the Dervo frame that called it.
        error = caught(json.loads, '{')
        innermost = error.__traceback__
        while innermost.tb_next:
            innermost = innermost.tb_next
        self.assertEqual(Path(innermost.tb_frame.f_code.co_filename).name, 'decoder.py')  # negative control: raised in json
        self.assertEqual(error_reports.location(error), ('test_error_reports.py', 'caught', caught.__code__.co_firstlineno + 2))
        def parse():
            return json.loads('{')
        where = error_reports.location(caught(parse))
        self.assertEqual(where[:2], ('test_error_reports.py', 'Shape.test_frames_outside_dervos_code_are_dropped.<locals>.parse'))
        # Code in any other folder, even a file named like one of Dervo's, is never named.
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / 'host.py'
            outside.write_text('def boom():\n    raise KeyError("secret")\n')
            scope = {}
            exec(compile(outside.read_text(), str(outside), 'exec'), scope)
            error = caught(scope['boom'])
            self.assertEqual(error.__traceback__.tb_next.tb_frame.f_code.co_filename, str(outside))  # raised out there
            self.assertEqual(error_reports.location(error)[:2], ('test_error_reports.py', 'caught'))

    def test_unsafe_fields_are_dropped_not_sent(self):
        self.assertIsNone(error_reports.row('plugin', 'KeyError'))
        self.assertIsNone(error_reports.row('host', 'No such file: ' + SECRET))
        report = error_reports.row('host', 'KeyError', ('/Users/maya/host.py', 'dispatch', 12), 'Open my secret project', 'openrouter')
        self.assertEqual(report, {'source': 'host', 'error_type': 'KeyError', 'file': None, 'function': None, 'line': None,
                                  'operation': None, 'provider': None})
        self.assertIsNone(error_reports.row('host', 'KeyError', ('host.py', 'maya@example.test', 12))['file'])
        self.assertIsNone(error_reports.row('host', 'KeyError', ('host.py', 'dispatch', 0))['line'])

    def test_the_renderer_sends_a_built_in_name_and_a_screen_only(self):
        self.assertEqual(error_reports.from_renderer({'errorType': 'TypeError', 'screen': 'settings.model', 'message': SECRET}),
                         {'source': 'renderer', 'error_type': 'TypeError', 'file': None, 'function': None, 'line': None,
                          'operation': 'settings.model', 'provider': None})
        odd = error_reports.from_renderer({'errorType': 'MayaSecretProjectError', 'screen': 'My secret screen'})
        self.assertEqual((odd['error_type'], odd['operation']), ('Error', None))
        self.assertIsNone(error_reports.from_renderer('TypeError'))


class Delivery(Base):
    def usage(self, account=None, sender=None, errors=None, **kwargs):
        usage = super().usage(account=account, sender=sender, **kwargs)
        usage.error_sender = errors
        return usage

    def setUp(self):
        super().setUp()
        self.addCleanup(error_reports.install, None)

    def test_errors_are_sent_with_usage_as_counts_under_the_install_id(self):
        errors = Sent()
        usage = self.usage(SignedIn(self.clock), errors=errors)
        error_reports.install(usage)
        for _ in range(3):
            self.assertTrue(error_reports.capture(caught(failing_open), operation='project_explore'))
        error_reports.capture(caught(failing_open), source='task', operation='coding_turn', provider='claude')
        self.drain(usage)
        rows = errors.calls[0][1]
        self.assertEqual(len(rows), 2)
        first = next(row for row in rows if row['source'] == 'host')
        self.assertEqual((first['count'], first['install_id'], first['app_version']), (3, usage.settings['install_id'], '0.1.0-beta.9'))
        self.assertEqual(set(first), {'install_id', 'source', 'error_type', 'file', 'function', 'line', 'operation', 'provider',
                                      'app_version', 'count'})
        self.assertNotIn('maya', json.dumps(rows))
        self.assertEqual(usage.errors, {})  # taken off before the send: a failure drops them

    def test_nothing_is_sent_with_sharing_off(self):
        errors = Sent()
        usage = self.usage(SignedIn(self.clock), errors=errors)
        error_reports.install(usage)
        self.assertTrue(error_reports.capture(caught(failing_open)))
        usage.set_enabled(False)  # what was waiting goes too
        self.assertFalse(error_reports.capture(caught(failing_open)))
        self.assertFalse(usage.dispatch('error_event', {'errorType': 'TypeError', 'screen': 'home'}))
        self.assertIsNone(self.drain(usage))
        self.assertEqual((errors.calls, usage.errors), ([], {}))
        # Negative control: on again, the same capture is sent.
        usage.set_enabled(True)
        error_reports.capture(caught(failing_open))
        self.drain(usage)
        self.assertEqual(len(errors.calls), 1)

    def test_a_crash_loop_is_one_row_and_new_kinds_are_capped(self):
        errors = Sent()
        usage = self.usage(SignedIn(self.clock), errors=errors)
        report = error_reports.row('host', 'KeyError', ('host.py', 'dispatch', 12), 'status')
        for _ in range(20000):
            usage.record_error(report)
        for n in range(1, 100):
            usage.record_error(error_reports.row('host', 'KeyError', ('host.py', 'dispatch', n + 12), 'status'))
        self.assertEqual(len(usage.errors), usage.ERROR_KINDS)
        self.drain(usage)
        self.assertEqual(errors.calls[0][1][0]['count'], usage.ERROR_COUNT)

    def test_signing_out_throws_errors_away_and_nothing_is_captured_without_a_host(self):
        errors = Sent()
        usage = self.usage(SignedIn(self.clock), errors=errors)
        self.assertFalse(error_reports.capture(caught(failing_open)))  # no sink installed
        error_reports.install(usage)
        error_reports.capture(caught(failing_open))
        usage.observe('dervo_account_sign_out', {}, None)
        self.assertEqual(usage.errors, {})

    def test_the_renderer_event_goes_through_the_host_allowlist(self):
        errors = Sent()
        usage = self.usage(SignedIn(self.clock), errors=errors)
        self.assertIsNone(usage.dispatch('error_event', {'errorType': 'TypeError', 'screen': 'thread'}))
        with self.assertRaises(Exception):
            usage.dispatch('error_event', {'errorType': 'TypeError', 'screen': 'thread', 'message': SECRET})
        self.drain(usage)
        self.assertEqual([(r['source'], r['error_type'], r['operation']) for r in errors.calls[0][1]], [('renderer', 'TypeError', 'thread')])

    def test_installs_that_saw_the_earlier_notice_see_the_new_one_once(self):
        usage = self.usage()
        usage.settings['notice_seen'] = True  # an install from before error reports
        usage.settings['marks'] = [m for m in usage.settings['marks'] if m != NOTICE_MARK]
        usage._save()
        again = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.9', sender=Sent())
        self.assertEqual(again.sharing(), {'enabled': True, 'notice': True})
        self.assertEqual(again.dispatch('usage_notice_seen', {}), {'enabled': True, 'notice': False})
        third = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.9', sender=Sent())
        self.assertEqual(third.sharing(), {'enabled': True, 'notice': False})


class Client(unittest.TestCase):
    GROUP = {'source': 'host', 'error_type': 'UnboundLocalError', 'file': 'conversation_coding.py',
             'function': 'ConversationCoding.permission', 'line': 224, 'operation': 'coding_permission', 'provider': None,
             'count': 5, 'users': 2, 'first_seen': '2026-09-19T01:00:00+00:00', 'last_seen': '2026-09-19T02:00:00+00:00',
             'app_version': '0.1.0-beta.11'}

    def document(self, **changes):
        return {'since': '2026-09-13T00:00:00+00:00', 'days': 7, 'errors': [{**self.GROUP, **changes}]}

    def test_the_admin_document_is_read_field_by_field(self):
        read = _error_document(self.document())
        self.assertEqual(read['errors'][0]['line'], 224)
        for change in [{'error_type': 'No such file: ' + SECRET}, {'file': '/Users/maya/host.py'}, {'function': 'open my project'},
                       {'operation': 'Fix the login bug'}, {'provider': 'openrouter'}, {'count': 0}, {'app_version': 'beta; drop'},
                       {'message': SECRET}, {'last_seen': 'yesterday'}]:
            with self.subTest(change=change):
                with self.assertRaises(AccountError):
                    _error_document(self.document(**change))

    def test_rows_are_sent_only_in_the_agreed_shape(self):
        client = SupabaseClient.__new__(SupabaseClient)
        sent = []
        client._request = lambda *args, **kwargs: sent.append(args)
        session = SignedIn().session
        row = {'install_id': 'x', 'source': 'host', 'error_type': 'KeyError', 'file': None, 'function': None, 'line': None,
               'operation': None, 'provider': None, 'app_version': None, 'count': 1}
        client.record_errors(session, [row])
        self.assertEqual(sent[0][:2], ('POST', '/rest/v1/dervo_error_reports'))
        for rows in [[], [{**row, 'message': SECRET}], [row] * 501]:
            with self.assertRaises(AccountError):
                client.record_errors(session, rows)


if __name__ == '__main__':
    unittest.main()
