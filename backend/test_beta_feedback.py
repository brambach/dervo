"""Offline feedback transport, privacy and account-boundary tests. No real sessions."""
from concurrent.futures import Future
from types import SimpleNamespace
import unittest
from uuid import uuid4

from beta_feedback import Feedback, validate, submit, ERROR
from dervo_connections import Rejected


class Immediate:
    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future
    def shutdown(self, **kwargs):
        pass


class Deferred(Immediate):
    def submit(self, fn, *args):
        self.action = (fn, args)
        self.future = Future()
        return self.future
    def run(self):
        fn, args = self.action
        try:
            self.future.set_result(fn(*args))
        except Exception as exc:
            self.future.set_exception(exc)


class Client:
    project = SimpleNamespace(url='https://example.supabase.co')
    allowed = True
    calls = 0
    fail = False
    receipt = True
    raises = None  # an error the RPC call raises, as the real client would
    def user(self, token, *, expected_user):
        assert token == 'fake-token'
    def access(self, session):
        return {'allowed': self.allowed, 'status': 'approved' if self.allowed else 'revoked'}
    def _request(self, method, path, payload, *, access_token):
        self.calls += 1
        self.payload = payload
        if self.fail:
            raise RuntimeError('raw-secret-in-transport-error')
        if self.raises is not None:
            raise self.raises
        self.assertion = (method, path, access_token)
        return {'id': payload['submission']['request_id'], 'created_at': '2026-09-16T00:00:00Z'} if self.receipt else None


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.client = Client()
        self.subject = str(uuid4())
        self.identity = {'issuer': self.client.project.url + '/auth/v1', 'subject': self.subject}
        self.account = SimpleNamespace(state='signed_in', identity=self.identity, client=self.client, session=SimpleNamespace(user_id=self.subject, access_token='fake-token'),
            require_access=lambda: None, status=lambda: {'identity': self.identity}, require_identity=lambda *args: None)
        self.host = SimpleNamespace(account_session=self.account)
        self.feedback = Feedback(self.host, executor=Immediate())
        self.args = {'contextId': self.feedback.dispatch('beta_feedback_context', {})['contextId'], 'requestId': str(uuid4()), 'category': 'bug', 'message': 'The panel closes unexpectedly.',
                     'context': {'route': 'conversation', 'projectId': 'p1', 'viewport': {'width': 1200, 'height': 800}}}

    def test_general_feedback_is_submitted_with_its_category(self):
        result = self.feedback.dispatch('beta_feedback_send', dict(self.args, category='feedback', message='I love catch-up.'))
        self.assertEqual(result['state'], 'sent')
        self.assertEqual(self.client.payload['submission']['category'], 'feedback')

    def test_receipt_and_identical_retry_do_not_double_submit(self):
        first = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(first['state'], 'sent')
        self.assertEqual(first['receipt']['id'], self.args['requestId'])
        self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args), first)
        self.assertEqual(self.client.calls, 1)
        self.assertEqual(set(self.client.payload['submission']), {'request_id','category','message','context'})
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_send', dict(self.args, message='Changed'))

    def test_ambiguous_failure_retries_same_request_id_and_redacts_errors(self):
        self.client.fail = True
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['detail'], ERROR)
        self.assertNotIn('raw-secret', str(result))
        self.client.fail = False
        self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args)['state'], 'sent')
        self.assertEqual(self.client.payload['submission']['request_id'], self.args['requestId'])

    def test_the_hourly_limit_says_so_and_nothing_else_does(self):
        """Security review L2, 23 Sep: the server refuses a person's 21st feedback in an hour with SQLSTATE PT429, which
        PostgREST answers as HTTP 429 and the client reads as rate_limited. That one says why. Every other failure keeps
        the generic line, and no server text reaches the result."""
        from supabase_client import AccountError
        limit = 'You’ve sent a lot of feedback this hour. Your draft is kept, so try again later.'
        self.client.raises = AccountError('rate_limited')
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual((result['state'], result['detail']), ('error', limit))
        # Negative controls: any other refusal of the same call, and a 429 before it, read as today's generic line.
        for code in ('rejected', 'service', 'expired', 'unavailable'):
            self.client.raises = AccountError(code)
            self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args)['detail'], ERROR, code)
        self.client.raises = None
        self.client.access = lambda session: (_ for _ in ()).throw(AccountError('rate_limited'))
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual((result['state'], result['detail']), ('error', ERROR))
        del self.client.access
        # An account change after a refusal shows the generic line, not the limit.
        self.client.raises = AccountError('rate_limited')
        self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args)['detail'], limit)
        self.feedback.invalidate()
        self.assertNotIn('detail', self.feedback.jobs[next(iter(self.feedback.jobs))])

    def test_no_receipt_never_reports_sent(self):
        self.client.receipt = False
        self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args)['state'], 'error')

    def test_remote_revocation_blocks_transport(self):
        self.client.allowed = False
        self.assertEqual(self.feedback.dispatch('beta_feedback_send', self.args)['state'], 'error')
        self.assertEqual(self.client.calls, 0)

    def test_local_denial_missing_session_and_wrong_owner_block(self):
        self.account.require_access = lambda: (_ for _ in ()).throw(Rejected('denied'))
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_send', self.args)
        self.account.require_access = lambda: None
        self.identity['subject'] = str(uuid4())
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(self.client.calls, 0)

    def test_receipt_cannot_be_polled_from_another_account(self):
        self.feedback.dispatch('beta_feedback_send', self.args)
        self.identity['subject'] = str(uuid4())
        self.account.session = SimpleNamespace(user_id=self.identity['subject'], access_token='fake-token')
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_status', {'requestId': self.args['requestId'], 'contextId': self.args['contextId']})

    def test_sensitive_context_unknown_fields_and_invalid_values_rejected(self):
        for bad in [{'title': 'private'}, {'error': 'secret'}, {'prompt':'secret'}, {'files':['secret']},
                    {'userId':self.subject}, {'created_at':'forged'}, {'viewport':{'width':True,'height':10}},
                    {'route':'/Users/private/project'}, {'projectId':'project with private name'}]:
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                self.feedback.dispatch('beta_feedback_send', dict(self.args, context={**self.args['context'], **bad}))
        self.assertEqual(self.client.calls, 0)
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_send', dict(self.args, userId=self.subject))

    def test_message_size_and_category_limits(self):
        for changes in [{'message':''}, {'message':'   '}, {'message':'x'*4001}, {'message':'x\x00'}, {'category':'other'}]:
            with self.subTest(changes=changes), self.assertRaises(Rejected):
                validate(dict(self.args, **changes))

    def deferred(self):
        executor = Deferred()
        self.feedback = Feedback(self.host, executor=executor)
        self.args['contextId'] = self.feedback.dispatch('beta_feedback_context', {})['contextId']
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(result['state'], 'sending')
        return executor

    def test_queued_send_stops_after_signout_invalidation(self):
        executor = self.deferred()
        self.feedback.invalidate()
        self.account.state = 'signed_out'
        executor.run()
        self.assertEqual(self.client.calls, 0)
        self.assertIsInstance(executor.future.exception(), Rejected)

    def test_queued_send_stops_if_account_removed_or_switched(self):
        for replacement in (None, SimpleNamespace()):
            with self.subTest(replacement=replacement):
                self.host.account_session = self.account
                executor = self.deferred()
                self.host.account_session = replacement
                executor.run()
                self.assertEqual(self.client.calls, 0)
                self.assertIsInstance(executor.future.exception(), Rejected)

    def test_invalidation_during_remote_identity_check_stops_before_post(self):
        self.client.user = lambda *args, **kwargs: self.feedback.invalidate()
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.client.calls, 0)

    def test_old_activation_cannot_send_or_poll_after_new_account(self):
        self.feedback.dispatch('beta_feedback_send', self.args)
        self.identity['subject'] = str(uuid4())
        self.account.session = SimpleNamespace(user_id=self.identity['subject'], access_token='fake-token')
        new_context = self.feedback.dispatch('beta_feedback_context', {})
        self.assertNotEqual(new_context['contextId'], self.args['contextId'])
        with self.assertRaises(Rejected):
            self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(self.client.calls, 1)

    def test_close_stops_queued_send(self):
        executor = self.deferred()
        self.feedback.close()
        executor.run()
        self.assertEqual(self.client.calls, 0)

    def test_delivered_but_invalidated_response_is_not_reported_sent(self):
        original = self.client._request
        def request(*args, **kwargs):
            result = original(*args, **kwargs)
            self.feedback.invalidate()
            return result
        self.client._request = request
        result = self.feedback.dispatch('beta_feedback_send', self.args)
        self.assertEqual(result['state'], 'error')
        self.assertIsNone(result['receipt'])
        self.assertEqual(self.client.calls, 1)

    def test_host_invalidates_before_every_account_transition(self):
        from host import Host
        events = []
        fake = SimpleNamespace(feedback=SimpleNamespace(invalidate=lambda: events.append('invalidate')),
                               account_session=SimpleNamespace(dispatch=lambda *args: events.append('account')))
        for operation in ('dervo_account_start', 'dervo_account_send_code', 'dervo_account_verify_code',
                          'dervo_account_cancel', 'dervo_account_restore', 'dervo_account_sign_out'):
            events.clear()
            Host.dispatch(fake, operation, {})
            self.assertEqual(events, ['invalidate', 'account'])

    def test_activation_changes_keep_same_owner_key_but_account_switch_changes_it(self):
        first = self.feedback.dispatch('beta_feedback_context', {})
        self.feedback.invalidate()
        second = self.feedback.dispatch('beta_feedback_context', {})
        self.assertNotEqual(first['contextId'], second['contextId'])
        self.assertEqual(first['ownerKey'], second['ownerKey'])
        self.identity['subject'] = str(uuid4())
        self.account.session = SimpleNamespace(user_id=self.identity['subject'], access_token='fake-token')
        third = self.feedback.dispatch('beta_feedback_context', {})
        self.assertNotEqual(second['ownerKey'], third['ownerKey'])


if __name__ == '__main__':
    unittest.main()
