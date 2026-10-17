"""Usage events: the allowlist, the switch, the queue, delivery and the admin report. Temporary stores and fakes only.
No request leaves this process."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import usage_events
from dervo_connections import Rejected
from host import ACTOR, Host
from supabase_account import SupabaseAccount
from supabase_client import AccountError, PublicProject, Session, SupabaseClient
from usage_events import UsageEvents, clean, model_family

CONTENT = {'prompt': 'Fix the billing bug', 'path': '/Users/maya/secret-project', 'email': 'maya@example.test',
           'project': 'secret-project', 'thread': 'Launch plan', 'repo': 'acme/payments', 'output': 'Done. Changed 3 files.',
           'error': 'Traceback: token sk-live-123', 'key': 'sk-ant-api03-xyz', 'name': 'Personal'}


def fake_host(store_root, account=None):
    """The smallest host a UsageEvents needs: a store, and optionally an account session."""
    from dervo_connections import Store
    store = Store(Path(store_root) / 'conversations.sqlite3')
    host = SimpleNamespace(store=store)
    if account is not None:
        host.account_session = account
    return host


class SignedIn:
    def __init__(self, clock=time.time, admin=False):
        self.state = 'signed_in'
        self.session = Session('a' * 40, 'r' * 40, '10000000-0000-0000-0000-000000000002', clock() + 3600)
        self.client = object()
        self.admin = admin
        self.clock = clock
        self.waiting = False

    def status(self):
        return {'state': self.state, 'membership': {'allowed': not self.waiting, 'status': 'pending' if self.waiting else 'approved',
                                                    'is_admin': self.admin}}

    _require_admin = SupabaseAccount._require_admin  # the real admin check

    def tick(self):
        pass

    def close(self):
        pass


class Sent:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, client, session, rows):
        self.calls.append((session, rows))
        if self.fail:
            raise AccountError('unavailable', transient=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1_800_000_000.0]
        self.clock = lambda: self.now[0]

    def usage(self, account=None, sender=None, version='0.1.0-beta.9', root='store'):
        host = fake_host(Path(self.temp.name) / root, account)
        (Path(self.temp.name) / root).mkdir(exist_ok=True)
        usage = UsageEvents(host, clock=self.clock, version=version, sender=sender or Sent())
        host.usage = usage
        return usage

    def drain(self, usage):
        thread = usage.flush()
        if thread is not None:
            thread.join(2)
        return thread


class AllowlistTests(Base):
    def test_content_fields_are_dropped_before_anything_is_queued(self):
        self.assertEqual(clean('screen_viewed', {'screen': 'home', **CONTENT}), {'screen': 'home'})
        self.assertEqual(clean('task_ended', {'outcome': 'failed', 'provider': 'codex', 'error_code': 'usage_limit', **CONTENT}),
                         {'outcome': 'failed', 'provider': 'codex', 'error_code': 'usage_limit'})
        usage = self.usage()
        usage.record('thread_created', CONTENT)
        queued = json.dumps([row for _, row in usage.queue])
        for value in CONTENT.values():
            self.assertNotIn(value, queued)
        self.assertEqual(usage.queue[-1][1]['properties'], {})

    def test_values_off_the_list_are_dropped_and_unknown_events_never_queue(self):
        self.assertIsNone(clean('prompt_sent', {'text': 'hello'}))
        self.assertIsNone(clean('screen_viewed', {'screen': 'settings.my-secret-project'}))  # required, and off the list
        self.assertIsNone(clean('screen_viewed', {}))
        self.assertEqual(clean('task_ended', {'outcome': 'completed', 'provider': 'openrouter', 'seconds': -1, 'error_code': 'Traceback'}),
                         {'outcome': 'completed'})
        self.assertEqual(clean('home_request', {'outcome': 'completed', 'threads_created': 21}), {'outcome': 'completed'})
        self.assertEqual(clean('home_request', {'outcome': 'completed', 'threads_created': True}), {'outcome': 'completed'})
        self.assertEqual(clean('model_changed', {'model_family': 'gpt-5-acme-internal-project'}), {})
        usage = self.usage()
        before = len(usage.queue)
        self.assertFalse(usage.record('prompt_sent', {'text': 'hello'}))
        self.assertEqual(len(usage.queue), before)

    def test_model_families_never_carry_a_custom_name(self):
        self.assertEqual(model_family('claude-opus-5'), 'opus-5')
        self.assertEqual(model_family('claude-haiku-4-5-20251001'), 'haiku-4.5')
        self.assertEqual(model_family('claude-fable-5-1'), 'fable-5.1')
        self.assertEqual(model_family('sonnet'), 'sonnet')
        self.assertEqual(model_family('gpt-5.6-sol'), 'gpt-5.6-sol')
        self.assertEqual(model_family('gpt-5.1-codex-max'), 'gpt-5.1-codex')
        self.assertEqual(model_family('gpt-5-acme-internal'), 'gpt-5')
        self.assertEqual(model_family('acme/payments-finetune'), 'other')
        self.assertEqual(model_family(''), 'default')
        for model in ('claude-opus-5', 'gpt-5-acme-internal', 'acme/payments-finetune', 'x' * 500):
            self.assertIsNotNone(usage_events._family(model_family(model)))

    def test_the_renderer_sends_only_its_own_two_events(self):
        usage = self.usage()
        usage.dispatch('usage_event', {'event': 'screen_viewed', 'props': {'screen': 'knowledge', 'path': '/Users/maya'}})
        usage.dispatch('usage_event', {'event': 'model_picker_opened', 'props': {}})
        usage.dispatch('usage_event', {'event': 'task_ended', 'props': {'outcome': 'completed'}})
        usage.dispatch('usage_event', {'event': 'screen_viewed', 'props': 'home'})
        self.assertEqual([row['event'] for _, row in usage.queue], ['app_opened', 'screen_viewed', 'model_picker_opened'])
        self.assertEqual(usage.queue[1][1]['properties'], {'screen': 'knowledge'})
        with self.assertRaises(Rejected):
            usage.dispatch('usage_event', {'event': 'screen_viewed', 'props': {}, 'userId': 'someone'})


class SwitchTests(Base):
    def test_off_queues_nothing_sends_nothing_and_throws_away_what_waited(self):
        sent = Sent()
        usage = self.usage(account=SignedIn(self.clock), sender=sent)
        usage.record('thread_created')
        self.assertEqual(usage.dispatch('usage_sharing_set', {'enabled': False}), {'enabled': False, 'notice': False})
        self.assertEqual(usage.queue, [])
        self.assertFalse(usage.record('thread_created'))
        usage.from_app('screen_viewed', {'screen': 'home'})
        usage.task_started('run-1', {'provider': 'claude', 'model': 'claude-opus-5', 'method': 'subscription'})
        self.now[0] += 3600
        usage.tick()
        self.assertIsNone(self.drain(usage))
        usage.close()
        usage.settle()
        self.assertEqual(sent.calls, [])
        self.assertEqual(usage.queue, [])

    def test_the_switch_is_remembered_and_on_by_default_with_the_notice_once(self):
        usage = self.usage()
        self.assertEqual(usage.sharing(), {'enabled': True, 'notice': True})
        self.assertEqual(usage.dispatch('usage_notice_seen', {}), {'enabled': True, 'notice': False})
        usage.set_enabled(False)
        again = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.9', sender=Sent())
        self.assertEqual(again.sharing(), {'enabled': False, 'notice': False})
        self.assertEqual(again.queue, [])  # off from the first line: not even app_opened
        with self.assertRaises(Rejected):
            again.set_enabled('yes')

    def test_a_damaged_record_starts_off_never_silently_on(self):
        usage = self.usage()
        with usage.db:
            usage.db.execute("update usage_settings set document='{\"enabled\": true}' where id=1")
        again = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.9', sender=Sent())
        self.assertEqual(again.sharing(), {'enabled': False, 'notice': True})

    def test_first_launch_after_an_update_records_it_once(self):
        usage = self.usage(version='0.1.0-beta.9')
        install = usage.settings['install_id']
        later = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.10', sender=Sent())
        self.assertEqual([row['event'] for _, row in later.queue], ['app_opened', 'update_installed'])
        self.assertEqual(later.queue[1][1]['app_version'], '0.1.0-beta.10')
        self.assertEqual(later.settings['install_id'], install)
        again = UsageEvents(usage.host, clock=self.clock, version='0.1.0-beta.10', sender=Sent())
        self.assertEqual([row['event'] for _, row in again.queue], ['app_opened'])


class DeliveryTests(Base):
    def test_rows_carry_only_the_event_its_properties_the_version_and_the_install(self):
        sent = Sent()
        usage = self.usage(account=SignedIn(self.clock), sender=sent)
        usage.from_app('screen_viewed', {'screen': 'home'})
        self.drain(usage)
        rows = sent.calls[0][1]
        self.assertEqual({key for row in rows for key in row}, {'install_id', 'event', 'properties', 'app_version'})
        self.assertEqual(rows[1], {'install_id': usage.settings['install_id'], 'event': 'screen_viewed',
                                   'properties': {'screen': 'home'}, 'app_version': '0.1.0-beta.9'})
        self.assertEqual(usage.queue, [])

    def test_batches_wait_for_the_interval_and_a_failed_send_is_dropped_not_retried(self):
        sent = Sent(fail=True)
        usage = self.usage(account=SignedIn(self.clock), sender=sent)
        usage.tick()
        self.assertEqual(sent.calls, [])  # not before the interval
        self.now[0] += UsageEvents.FLUSH_SECONDS
        usage.tick()
        usage.sending.join(2)
        self.assertEqual(len(sent.calls), 1)
        self.assertEqual(usage.queue, [])
        self.now[0] += UsageEvents.FLUSH_SECONDS
        usage.tick()
        self.assertEqual(len(sent.calls), 1)  # nothing left to retry

    def test_signed_out_events_wait_for_sign_in_for_a_day_and_sign_out_throws_them_away(self):
        account = SignedIn(self.clock)
        account.state = 'signed_out'
        sent = Sent()
        usage = self.usage(account=account, sender=sent)
        self.assertIsNone(self.drain(usage))
        self.assertEqual(len(usage.queue), 1)
        account.state = 'signed_in'
        self.drain(usage)
        self.assertEqual(len(sent.calls), 1)
        account.state = 'signed_out'
        usage.record('thread_created')
        self.now[0] += UsageEvents.HOLD_SECONDS + 1
        usage.record('thread_created')
        self.drain(usage)
        self.assertEqual(len(usage.queue), 1)  # the old one aged out
        usage.observe('dervo_account_sign_out', {}, {})
        self.assertEqual(usage.queue, [])

    def test_an_expiring_session_is_never_refreshed_here(self):
        account = SignedIn(self.clock)
        account.session = Session('a' * 40, 'r' * 40, account.session.user_id, self.clock() + 30)
        sent = Sent()
        usage = self.usage(account=account, sender=sent)
        self.assertIsNone(self.drain(usage))
        self.assertEqual(sent.calls, [])

    def test_the_queue_cap_holds_and_keeps_the_newest(self):
        usage = self.usage()
        for n in range(UsageEvents.QUEUE_LIMIT * 3):
            usage.record('home_request', {'outcome': 'completed', 'threads_created': n % 20})
        self.assertEqual(len(usage.queue), UsageEvents.QUEUE_LIMIT)
        self.assertEqual(usage.queue[-1][1]['properties']['threads_created'], (UsageEvents.QUEUE_LIMIT * 3 - 1) % 20)
        self.assertNotIn('app_opened', [row['event'] for _, row in usage.queue])

    def test_quitting_sends_once_and_waits_only_briefly(self):
        slow = Sent()
        def hang(client, session, rows):
            slow.calls.append(rows)
            time.sleep(5)
        usage = self.usage(account=SignedIn(self.clock), sender=hang)
        started = time.monotonic()
        usage.close()
        usage.settle()
        self.assertLess(time.monotonic() - started, UsageEvents.QUIT_SECONDS + 1)
        self.assertEqual(len(slow.calls), 1)
        self.assertTrue(usage.sending.daemon)
        self.assertFalse(usage.record('thread_created'))

    def test_only_the_builds_real_account_client_ever_sends(self):
        account = SignedIn(self.clock)
        host = fake_host(Path(self.temp.name) / 'real', account)
        (Path(self.temp.name) / 'real').mkdir(exist_ok=True)
        usage = UsageEvents(host, clock=self.clock, version='0.1.0-beta.9')  # the default sender
        self.assertIsNone(usage._target())  # a fake client is never used to send

    def test_the_client_never_sends_a_user_id_and_asks_for_nothing_back(self):
        project = PublicProject('abcdefghijklmnopqrst', 'https://abcdefghijklmnopqrst.supabase.co', 'sb_publishable_' + 'x' * 20)
        seen = []
        class Response(io.BytesIO):
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): return False
        class Recording:
            def open(self, request, timeout):
                seen.append((request, timeout))
                return Response(b'')
        client = SupabaseClient(project, opener=Recording())
        session = Session('a' * 40, 'r' * 40, '10000000-0000-0000-0000-000000000002', time.time() + 3600)
        row = {'install_id': '40000000-0000-0000-0000-000000000001', 'event': 'app_opened', 'properties': {}, 'app_version': None}
        client.record_usage(session, [row])
        request, timeout = seen[0]
        self.assertEqual(request.full_url, 'https://abcdefghijklmnopqrst.supabase.co/rest/v1/dervo_usage_events')
        self.assertEqual(request.get_header('Prefer'), 'return=minimal')
        self.assertLessEqual(timeout, 10)
        self.assertNotIn('user_id', json.loads(request.data))
        self.assertNotIn(session.user_id, request.data.decode())
        with self.assertRaises(AccountError):
            client.record_usage(session, [{**row, 'user_id': session.user_id}])
        with self.assertRaises(AccountError):
            client.record_usage(session, [])


class ReportTests(Base):
    DOC = {'since': '2026-09-13T00:00:00+00:00', 'days': 7, 'active': 2,
           'daily': [{'day': '2026-09-19', 'users': 2, 'events': 15}],
           'events': [{'day': '2026-09-19', 'event': 'screen_viewed', 'users': 2, 'count': 5}],
           'screens': [{'day': '2026-09-19', 'screen': 'settings.preferences', 'users': 1, 'count': 2}],
           'funnel': [{'step': 'sign_in', 'reached': 2, 'completed': 1}],
           'tasks': [{'provider': 'claude', 'outcome': 'completed', 'count': 2, 'users': 1, 'median_seconds': 60},
                     {'provider': 'claude', 'outcome': 'stopped', 'count': 1, 'users': 1, 'median_seconds': None}]}

    def test_a_non_admin_cant_read_and_nothing_is_asked_of_the_server(self):
        account = SignedIn(self.clock, admin=False)
        asked = []
        account.client = SimpleNamespace(usage_report=lambda *a: asked.append(a))
        usage = self.usage(account=account)
        with self.assertRaises(Rejected):
            usage.dispatch('usage_report', {'reload': False})
        self.assertEqual(asked, [])
        signed_out = self.usage(root='other')
        with self.assertRaises(Rejected):
            signed_out.dispatch('usage_report', {'reload': False})

    def test_an_admin_gets_the_servers_counts(self):
        account = SignedIn(self.clock, admin=True)
        account.client = SimpleNamespace(usage_report=lambda session, days: {'days': days, 'from': session.user_id})
        usage = self.usage(account=account)
        first = usage.dispatch('usage_report', {'reload': False})
        self.assertIn(first['state'], ('loading', 'ready'))
        usage._report['thread'].join(2)
        done = usage.dispatch('usage_report', {'reload': False})
        self.assertEqual(done, {'state': 'ready', 'report': {'days': 7, 'from': account.session.user_id}})

    def test_the_report_shape_is_checked(self):
        from supabase_client import _usage_document
        report = _usage_document(json.loads(json.dumps(self.DOC)))
        self.assertEqual(report['tasks'][1]['median_seconds'], None)
        self.assertEqual(report['screens'][0]['screen'], 'settings.preferences')
        for broken in ({**self.DOC, 'rows': []}, {**self.DOC, 'active': -1},
                       {**self.DOC, 'screens': [{'day': '2026-09-19', 'screen': 'Maya’s secret', 'users': 1, 'count': 1}]},
                       {**self.DOC, 'daily': [{'day': 'today', 'users': 1, 'events': 1}]},
                       {**self.DOC, 'funnel': [{'step': 'sign_in', 'reached': 2, 'completed': 1, 'email': 'maya@example.test'}]}):
            with self.assertRaises(AccountError):
                _usage_document(broken)


class PeopleTests(Base):
    """The Testers page's People section: counts per tester, for admins only."""
    PERSON = {'user_id': '31000000-0000-0000-0000-000000000002', 'email': 'maya@example.test', 'status': 'approved',
              'is_admin': False, 'last_seen': '2026-09-28T01:02:03.456789+00:00', 'app_version': '0.1.0-beta.16.3',
              'active_days': 2, 'opens': 3, 'threads': 1, 'home_requests': 1, 'tasks_started': 3, 'tasks_completed': 1,
              'tasks_failed': 1, 'claude_tasks': 1, 'codex_tasks': 2, 'knowledge': 0, 'feedback_opened': 0}
    IDLE = {**PERSON, 'user_id': '31000000-0000-0000-0000-000000000003', 'email': 'jordan@example.test', 'last_seen': None,
            'app_version': None, 'active_days': 0, 'opens': 0, 'threads': 0, 'home_requests': 0, 'tasks_started': 0,
            'tasks_completed': 0, 'tasks_failed': 0, 'claude_tasks': 0, 'codex_tasks': 0}
    DOC = {'since': '2026-09-22T00:00:00+00:00', 'days': 7, 'people': [PERSON, IDLE]}

    def test_a_non_admin_cant_read_and_nothing_is_asked_of_the_server(self):
        account = SignedIn(self.clock, admin=False)
        asked = []
        account.client = SimpleNamespace(usage_people=lambda *a: asked.append(a))
        usage = self.usage(account=account)
        with self.assertRaises(Rejected):
            usage.dispatch('usage_people', {'reload': False})
        self.assertEqual(asked, [])
        with self.assertRaises(Rejected):
            usage.dispatch('usage_people', {'reload': False, 'days': 90})

    def test_an_admin_gets_the_servers_people(self):
        account = SignedIn(self.clock, admin=True)
        account.client = SimpleNamespace(usage_people=lambda session, days: {'days': days, 'from': session.user_id})
        usage = self.usage(account=account)
        usage.dispatch('usage_people', {'reload': False})
        usage._people['thread'].join(2)
        self.assertEqual(usage.dispatch('usage_people', {'reload': False}),
                         {'state': 'ready', 'report': {'days': 7, 'from': account.session.user_id}})
        # Signing out forgets it, so the next account never sees the last one's list.
        usage.observe('dervo_account_sign_out', {}, None)
        self.assertIsNone(usage._people)

    def test_the_people_shape_is_checked(self):
        from supabase_client import _people_document
        report = _people_document(json.loads(json.dumps(self.DOC)))
        self.assertEqual([p['email'] for p in report['people']], ['maya@example.test', 'jordan@example.test'])
        self.assertEqual(report['people'][0]['codex_tasks'], 2)
        self.assertIsInstance(report['people'][0]['last_seen'], float)
        self.assertIsNone(report['people'][1]['last_seen'])
        for broken in ({**self.DOC, 'rows': []}, {**self.DOC, 'days': 0},
                       {**self.DOC, 'people': [{**self.PERSON, 'opens': -1}]},
                       {**self.DOC, 'people': [{**self.PERSON, 'is_admin': 'no'}]},
                       {**self.DOC, 'people': [{**self.PERSON, 'status': 'owner'}]},
                       {**self.DOC, 'people': [{**self.PERSON, 'app_version': 'beta 9; drop table'}]},
                       {**self.DOC, 'people': [{**self.PERSON, 'user_id': 'maya'}]},
                       {**self.DOC, 'people': [{**self.PERSON, 'last_screen': 'thread'}]},
                       {**self.DOC, 'people': [self.PERSON] * 501}):
            with self.assertRaises(AccountError):
                _people_document(broken)


class HostTests(unittest.TestCase):
    """The host's own events: the requests it notes, the first-run funnel and the task lifecycle."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.host = Host(self.base / 'store')
        self.addCleanup(lambda: self.host.close())
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text('import unittest\nclass Sample(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')

    def events(self):
        return [(row['event'], row['properties']) for _, row in self.host.usage.queue]

    def setup_project(self):
        self.host.store.add_profile('p', 'codex', self.base / 'store/profiles/p', 'synthetic@example.test', 'personal', method='api_key')
        self.host.store.add_project('p', self.project, 'p', 'gpt-5.6-sol')
        self.host.store.add_thread('c', 'p', 'c', 'private', ACTOR)
        self.scope = dict(projectId='p', channelId='c')

    def test_the_host_starts_sharing_and_answers_the_renderer(self):
        self.assertEqual(self.events()[0], ('app_opened', {}))
        self.assertEqual(self.host.dispatch('usage_sharing', {}), {'enabled': True, 'notice': True})
        self.assertIsNone(self.host.dispatch('usage_event', {'event': 'screen_viewed', 'props': {'screen': 'home'}}))
        self.assertEqual(self.events()[-1], ('screen_viewed', {'screen': 'home'}))
        with self.assertRaises(Rejected):
            self.host.dispatch('usage_sharing_set', {'enabled': False, 'everyone': True})

    def test_requests_that_are_events_are_noted_without_their_content(self):
        self.setup_project()
        self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'Secret launch plan', 'private': False})
        self.host.dispatch('conversation_model_set', {**self.scope, 'expectedModel': 'gpt-5.6-sol', 'model': 'gpt-5.6-terra'})
        events = self.events()
        self.assertIn(('thread_created', {}), events)
        self.assertIn(('model_changed', {'provider': 'codex', 'model_family': 'gpt-5.6-terra', 'scope': 'thread'}), events)
        self.assertNotIn('Secret launch plan', json.dumps(events))
        with self.assertRaises(Rejected):
            self.host.dispatch('create_conversation', {'projectId': 'nope', 'name': 'x', 'private': False})
        self.assertEqual(sum(1 for event, _ in self.events() if event == 'thread_created'), 1)  # a refused request is no event

    def test_first_run_funnel_marks_each_step_once(self):
        account = SignedIn()
        account.state = 'signed_out'
        self.host.account_session = account
        self.host.usage.observe('first_run_status', {}, {})
        self.host.usage.observe('first_run_status', {}, {})
        account.state, account.waiting = 'signed_in', True
        self.host.usage.observe('dervo_account_status', {}, {})
        account.waiting = False
        self.host.usage.observe('first_run_status', {}, {})
        self.host.usage.observe('first_run_inspect', {'selectionId': 's'}, {})
        self.setup_project()
        self.host.usage.observe('first_run_finish', {}, {})
        steps = [(p['step'], p['status']) for event, p in self.events() if event == 'first_run_step']
        self.assertEqual(steps, [('sign_in', 'reached'), ('sign_in', 'completed'), ('pending', 'reached'), ('pending', 'completed'),
                                 ('folder', 'reached'), ('folder', 'completed'),
                                 ('start', 'reached'), ('start', 'completed'), ('project_created', 'completed')])
        self.host.close()
        self.host = Host(self.base / 'store')
        self.host.account_session = account
        self.host.usage.observe('first_run_status', {}, {})
        self.assertEqual([event for event, _ in self.events() if event == 'first_run_step'], [])

    def test_approval_finishes_when_a_waiting_tester_is_approved_even_after_their_project_exists(self):
        # 30 Sep: the funnel showed Approval 1 reached, 0 finished. First run lets a waiting tester set up their project
        # straight away, so approval usually arrives after it, when the funnel had already stopped listening.
        account = SignedIn()
        account.waiting = True
        self.host.account_session = account
        self.host.usage.observe('dervo_account_status', {}, {})
        self.host.usage.observe('first_run_status', {}, {})
        self.host.usage.observe('first_run_inspect', {'selectionId': 's'}, {})
        self.setup_project()
        self.host.usage.observe('first_run_finish', {}, {})
        self.host.usage.observe('dervo_account_status', {}, {})  # still waiting: nothing to count yet
        account.waiting = False
        self.host.usage.observe('dervo_account_status', {}, {})
        self.host.usage.observe('dervo_account_status', {}, {})  # counted once
        steps = [(p['step'], p['status']) for event, p in self.events() if event == 'first_run_step']
        self.assertEqual(steps, [('sign_in', 'completed'), ('pending', 'reached'), ('folder', 'reached'), ('folder', 'completed'),
                                 ('start', 'reached'), ('start', 'completed'), ('project_created', 'completed'),
                                 ('pending', 'completed')])

    def test_a_tester_approved_at_sign_in_never_passes_through_approval(self):
        account = SignedIn()
        self.host.account_session = account
        self.host.usage.observe('dervo_account_status', {}, {})
        self.host.usage.observe('first_run_status', {}, {})
        steps = [(p['step'], p['status']) for event, p in self.events() if event == 'first_run_step']
        self.assertEqual(steps, [('sign_in', 'completed'), ('folder', 'reached')])

    def test_a_task_start_and_end_are_recorded_with_provider_family_kind_duration_and_code(self):
        self.setup_project()
        emits = []
        class SyntheticTurn:
            def __init__(peer, binding, *, task, context, emit):
                peer.closed = False
                peer.pending_tool_requests = ()
                peer.adapter = SimpleNamespace(state='running')
                emits.append(emit)
            def tick(peer): pass
            def stop(peer): peer.closed = True
            def close(peer): peer.closed = True
        task = self.host.dispatch('task_create', {**self.scope, 'text': 'Improve the test', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        with patch('codex_coding.CodexCodingTurn', SyntheticTurn):
            self.host.dispatch('task_approve', {**self.scope, 'taskId': task['id'], 'scopeVersion': 1})
        emits[0]('terminal', {'state': 'failed', 'provider_failure': 'usage_limit', 'error': 'You hit a limit in acme/payments', 'tokens': []})
        started = [p for event, p in self.events() if event == 'task_started']
        ended = [p for event, p in self.events() if event == 'task_ended']
        self.assertEqual(started, [{'provider': 'codex', 'model_family': 'gpt-5.6-sol', 'account_kind': 'api_key'}])
        self.assertEqual(len(ended), 1)
        self.assertEqual({k: v for k, v in ended[0].items() if k != 'seconds'},
                         {'provider': 'codex', 'model_family': 'gpt-5.6-sol', 'account_kind': 'api_key', 'outcome': 'failed',
                          'error_code': 'usage_limit'})
        self.assertIsInstance(ended[0]['seconds'], int)
        self.assertNotIn('acme', json.dumps(self.events()))

    def test_a_run_that_never_starts_is_a_failed_start(self):
        self.setup_project()
        class Broken:
            def __init__(peer, *a, **kw):
                raise RuntimeError('codex app-server exited: /Users/maya/secret')
        task = self.host.dispatch('task_create', {**self.scope, 'text': 'Improve the test', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        with patch('codex_coding.CodexCodingTurn', Broken):
            self.host.dispatch('task_approve', {**self.scope, 'taskId': task['id'], 'scopeVersion': 1})
        ended = [p for event, p in self.events() if event == 'task_ended']
        self.assertEqual(ended[0]['outcome'], 'failed')
        self.assertEqual(ended[0]['error_code'], 'start_failed')
        self.assertNotIn('secret', json.dumps(self.events()))

    def test_usage_failures_never_reach_a_request(self):
        self.setup_project()
        with patch.object(UsageEvents, '_observe', side_effect=RuntimeError('boom')):
            created = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'x', 'private': False})
        self.assertIn('id', created)



from test_project_orchestrator import HomeFixture, answer  # noqa: E402 - Home's own fixture: real host, stand-in model


class HomeRequestTests(HomeFixture):
    def home_requests(self):
        return [row['properties'] for _, row in self.host.usage.queue if row['event'] == 'home_request']

    def test_a_home_request_counts_the_threads_it_started_and_nothing_it_said(self):
        self.reply = lambda prompt: answer('Two threads and a note.', actions=[
            dict(kind='start', title='Connector warning copy', workstream='connectors', instruction='Add a Gmail example.'),
            dict(kind='start', title='Slow first load', workstream='Performance', instruction='Find why it takes 30 s.')])
        self.say('gmail example in the warning, and the secret acme list is slow')
        self.assertEqual(self.home_requests(), [{'outcome': 'completed', 'threads_created': 2}])
        self.assertNotIn('acme', json.dumps([row for _, row in self.host.usage.queue]))

    def test_a_failed_home_request_is_counted_as_failed(self):
        from project_orchestrator import TurnFailed
        def fail(prompt):
            raise TurnFailed('This project’s Claude account is signed out.')
        self.reply = fail
        self.say('what changed overnight?')
        self.assertEqual(self.home_requests(), [{'outcome': 'failed', 'threads_created': 0}])


if __name__ == '__main__':
    unittest.main()
