"""Offline HTTP contract tests. No credentials, email or hosted calls."""
from datetime import datetime
import io
import json
import ssl
import unittest
from urllib.error import HTTPError, URLError

from supabase_client import AccountError, PublicProject, Session, SupabaseClient, _NoRedirect

USER = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'
ACCESS = 'fixture-access-not-a-real-token'
REFRESH = 'fixture-refresh-not-a-real-token'
PROJECT = PublicProject('a' * 20, 'https://' + 'a' * 20 + '.supabase.co', 'sb_publishable_fixture_not_a_key')


class Response(io.BytesIO):
    status = 200


class HTTP:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return Response(response if isinstance(response, bytes) else json.dumps(response).encode())


def session_data(**changes):
    return dict(access_token=ACCESS, refresh_token=REFRESH, token_type='bearer', expires_in=3600,
                user={'id': USER, 'email': 'fixture@example.test',
                      'email_confirmed_at': '2026-09-14T00:00:00Z', 'is_anonymous': False}, **changes)


class ClientTests(unittest.TestCase):
    def client(self, *responses):
        http = HTTP(*responses)
        return SupabaseClient(PROJECT, opener=http, clock=lambda: 1000), http

    def test_public_config_rejects_privileged_or_foreign_destination(self):
        for url, key in [('https://other.example', PROJECT.publishable_key),
                         (PROJECT.url, 'sb_secret_fixture'), (PROJECT.url, 'eyJlegacykey'),
                         (PROJECT.url + '/redirect', PROJECT.publishable_key)]:
            with self.assertRaises(AccountError):
                PublicProject(PROJECT.project_ref, url, key)
        with self.assertRaises(AccountError):
            PublicProject.from_public_config({'project_ref': PROJECT.project_ref, 'url': PROJECT.url,
                                             'publishable_key': PROJECT.publishable_key, 'service_role': 'forbidden'})

    def test_send_code_does_not_establish_session(self):
        client, http = self.client({})
        self.assertIsNone(client.send_code('fixture@example.test'))
        request = http.requests[0]
        self.assertEqual(request.full_url, PROJECT.url + '/auth/v1/otp')
        self.assertEqual(json.loads(request.data), {'email': 'fixture@example.test', 'create_user': True})
        self.assertNotIn('Authorization', request.headers)

    def test_otp_returns_private_session_from_auth_endpoint(self):
        client, http = self.client(session_data())
        result = client.verify_code('fixture@example.test', '123456')
        self.assertEqual(result.user_id, USER)
        self.assertEqual(result.expires_at, 4600)
        self.assertNotIn(ACCESS, repr(result))
        self.assertNotIn(REFRESH, repr(result))
        self.assertEqual(http.requests[0].full_url, PROJECT.url + '/auth/v1/verify')
        self.assertEqual(json.loads(http.requests[0].data)['type'], 'email')

    def test_refresh_rotates_tokens_without_an_extra_fallible_call(self):
        data = session_data()
        data['refresh_token'] = 'fixture-rotated-refresh-token'
        client, http = self.client(data)
        result = client.refresh(Session(ACCESS, REFRESH, USER, 900))
        self.assertEqual(result.refresh_token, data['refresh_token'])
        self.assertEqual(len(http.requests), 1)
        self.assertEqual(json.loads(http.requests[0].data), {'refresh_token': REFRESH})
        self.assertNotIn(REFRESH, http.requests[0].full_url)

    def test_refresh_rejects_different_account(self):
        data = session_data()
        data['user']['id'] = OTHER
        client, _ = self.client(data)
        with self.assertRaises(AccountError):
            client.refresh(Session(ACCESS, REFRESH, USER, 900))

    def test_restored_identity_comes_from_user_endpoint_not_saved_claims(self):
        client, http = self.client({'id': USER, 'email_confirmed_at': '2026-09-14T00:00:00Z'})
        result = client.user(ACCESS, expected_user=USER)
        self.assertEqual(result, {'issuer': PROJECT.url + '/auth/v1', 'subject': USER})
        self.assertEqual(http.requests[0].headers['Authorization'], 'Bearer ' + ACCESS)
        client, _ = self.client({'id': OTHER, 'email_confirmed_at': '2026-09-14T00:00:00Z'})
        with self.assertRaises(AccountError):
            client.user(ACCESS, expected_user=USER)

    def test_unconfirmed_and_anonymous_sessions_are_rejected(self):
        for user in [{'id': USER, 'email': 'fixture@example.test'},
                     {'id': USER, 'email': 'fixture@example.test',
                      'email_confirmed_at': '2026-09-14T00:00:00Z', 'is_anonymous': True}]:
            data = session_data()
            data['user'] = user
            client, _ = self.client(data)
            with self.assertRaises(AccountError):
                client.verify_code('fixture@example.test', '123456')

    def test_confirmed_fields_and_requested_email_are_validated(self):
        for changes in [{'email_confirmed_at': {'bad': 1}}, {'email_confirmed_at': 'bad'},
                        {'email_confirmed_at': '2026-09-14'}, {'is_anonymous': 'true'},
                        {'is_anonymous': 0}, {'is_anonymous': None}, {'email': 'other@example.test'}]:
            data = session_data()
            data['user'].update(changes)
            client, _ = self.client(data)
            with self.assertRaises(AccountError):
                client.verify_code('fixture@example.test', '123456')
        data = session_data()
        data['user']['email'] = 'FIXTURE@example.test'
        client, _ = self.client(data)
        self.assertEqual(client.verify_code('fixture@example.test', '123456').user_id, USER)

    def test_error_bodies_tokens_and_urls_are_never_exposed(self):
        for status, expected in [(400, 'rejected'), (401, 'expired'), (403, 'expired'),
                                 (429, 'rate_limited'), (500, 'email_send'), (302, 'unavailable')]:
            error = HTTPError('https://fixture.invalid/' + ACCESS, status, REFRESH, {}, io.BytesIO(ACCESS.encode()))
            client, http = self.client(error)
            with self.assertRaises(AccountError) as raised:
                client.send_code('fixture@example.test')
            self.assertEqual(raised.exception.code, expected)
            self.assertNotIn(ACCESS, str(raised.exception))
            self.assertNotIn(REFRESH, str(raised.exception))
            self.assertEqual(len(http.requests), 1, 'No automatic email or refresh retry')
        client, _ = self.client(URLError(REFRESH))
        with self.assertRaises(AccountError) as raised:
            client.send_code('fixture@example.test')
        self.assertNotIn(REFRESH, str(raised.exception))
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://foreign.test'))

    def test_a_wrong_or_old_code_is_rejected_not_reported_as_an_expired_session(self):
        # Auth answers a bad code with 401 or 403. With no session yet that's the code, so the sign-in card asks for it
        # again instead of saying "Your Dervo session expired" and dropping the tester out of the code step.
        for status in (400, 401, 403, 422):
            error = HTTPError('https://fixture.invalid/' + ACCESS, status, REFRESH, {}, io.BytesIO(ACCESS.encode()))
            client, http = self.client(error)
            with self.assertRaises(AccountError) as raised:
                client.verify_code('fixture@example.test', '123456')
            self.assertEqual(raised.exception.code, 'rejected', status)
            self.assertNotIn(ACCESS, str(raised.exception))
            self.assertEqual(len(http.requests), 1, 'No automatic retry')
        # Only the code check is reclassified: a refused refresh is still an expired session.
        client, _ = self.client(HTTPError('https://fixture.invalid/', 403, '', {}, io.BytesIO()))
        with self.assertRaises(AccountError) as raised:
            client.refresh(Session(ACCESS, REFRESH, USER, 900))
        self.assertEqual(raised.exception.code, 'expired')
        # Other failures of the code check keep their own answers.
        for status, expected in [(429, 'rate_limited'), (500, 'service')]:
            client, _ = self.client(HTTPError('https://fixture.invalid/', status, '', {}, io.BytesIO()))
            with self.assertRaises(AccountError) as raised:
                client.verify_code('fixture@example.test', '123456')
            self.assertEqual(raised.exception.code, expected)

    def test_hosted_errors_are_not_reported_as_network_failures(self):
        for endpoint, expected in [('/auth/v1/otp', 'email_send'), ('/auth/v1/user', 'service')]:
            error = HTTPError('https://fixture.invalid/' + ACCESS, 503, REFRESH, {}, io.BytesIO(ACCESS.encode()))
            client, http = self.client(error)
            with self.assertRaises(AccountError) as raised:
                client._request('POST', endpoint, {})
            self.assertEqual(raised.exception.code, expected)
            self.assertNotIn(ACCESS, str(raised.exception))
            self.assertNotIn(REFRESH, str(raised.exception))
            self.assertEqual(len(http.requests), 1)

    def test_only_temporary_transport_errors_are_classified_for_read_recovery(self):
        for failure, transient in [(URLError('fixture offline'), True), (TimeoutError(), True),
                                   (ConnectionResetError(), True),
                                   (URLError(ssl.SSLCertVerificationError()), False),
                                   (URLError(ssl.SSLError()), False), (ValueError(), False)]:
            with self.subTest(failure=type(failure).__name__):
                client, http = self.client(failure)
                with self.assertRaises(AccountError) as raised:
                    client.user(ACCESS, expected_user=USER)
                self.assertEqual(raised.exception.transient, transient)
                self.assertEqual(len(http.requests), 1, 'The transport never retries by itself')
        for status in (302, 400, 401, 403, 429, 500, 502, 503, 504):
            client, http = self.client(HTTPError('https://fixture.invalid', status, '', {}, io.BytesIO()))
            with self.assertRaises(AccountError) as raised:
                client.user(ACCESS, expected_user=USER)
            self.assertEqual(raised.exception.transient, status in (502, 503, 504))
            self.assertEqual(len(http.requests), 1)

    def test_temporary_errors_never_replay_authentication_or_profile_writes(self):
        session = Session(ACCESS, REFRESH, USER, 4600)
        for operation in (lambda c: c.send_code('fixture@example.test'),
                          lambda c: c.verify_code('fixture@example.test', '123456'),
                          lambda c: c.refresh(session), lambda c: c.sign_out(session),
                          lambda c: c.save_name(session, 'Name')):
            client, http = self.client(URLError('fixture offline'), {})
            with self.assertRaises(AccountError):
                operation(client)
            self.assertEqual(len(http.requests), 1)

    def test_bad_inputs_do_not_start_network_calls(self):
        client, http = self.client()
        for email, code in [('bad', '123456'), ('fixture@example.test', 'wrong'),
                            ('fixture@example.test\n', '123456')]:
            with self.assertRaises(AccountError):
                client.verify_code(email, code)
        self.assertEqual(http.requests, [])

    def test_malformed_and_oversized_responses_fail_closed(self):
        for response in [b'{bad', b'x' * (SupabaseClient.MAX_RESPONSE + 1), {}, None]:
            client, _ = self.client(response)
            with self.assertRaises(AccountError):
                client.verify_code('fixture@example.test', '123456')

    def test_access_requires_consistent_server_membership(self):
        session = Session(ACCESS, REFRESH, USER, 4600)
        base = dict(status='approved', allowed=True, expires_at=None, release_channel='private-beta', is_admin=False)
        client, _ = self.client(base)
        self.assertTrue(client.access(session)['allowed'])
        for changes in [{'status': 'pending'}, {'status': 'revoked'}, {'allowed': 'true'},
                        {'expires_at': '1970-01-01T00:00:00Z'}, {'expires_at': 'invalid'},
                        {'release_channel': 'personal-experimental'}, {'is_admin': 'true'}]:
            client, _ = self.client({**base, **changes})
            with self.assertRaises(AccountError):
                client.access(session)
        client, _ = self.client({**base, 'status': 'pending', 'allowed': False})
        self.assertFalse(client.access(session)['allowed'])

    def test_profile_checks_owner_and_only_updates_name(self):
        session = Session(ACCESS, REFRESH, USER, 4600)
        client, http = self.client(None, [{'user_id': USER, 'display_name': 'Fieldnotes'}])
        self.assertEqual(client.save_name(session, ' Fieldnotes ')['display_name'], 'Fieldnotes')
        self.assertEqual(json.loads(http.requests[0].data), {'display_name': 'Fieldnotes'})
        self.assertIn('user_id=eq.' + USER, http.requests[0].full_url)
        client, _ = self.client([{'user_id': OTHER, 'display_name': 'Other'}])
        with self.assertRaises(AccountError):
            client.profile(session)

    def test_signout_is_current_session_only(self):
        client, http = self.client(None)
        client.sign_out(Session(ACCESS, REFRESH, USER, 4600))
        self.assertEqual(http.requests[0].full_url, PROJECT.url + '/auth/v1/logout?scope=local')


    # ---------------------------------------------------------------- Testers (admins only, 19 Sep 2026)
    def person(self, **changes):
        return {**dict(user_id=OTHER, email='maya@example.test', status='pending', expires_at=None,
                       signed_up_at='2026-09-18T23:02:00+00:00', updated_at='2026-09-18T23:02:00+00:00', is_admin=False), **changes}

    def change(self, **changes):
        return {**dict(id=7, changed_at='2026-09-19T09:12:00Z', actor_id=USER, actor_email='admin@example.test', subject_id=OTHER,
                       subject_email='maya@example.test', previous_status='pending', new_status='approved', expires_at=None), **changes}

    def test_testers_calls_use_the_callers_own_session_and_the_admin_functions(self):
        session = Session(ACCESS, REFRESH, USER, 5000)
        client, http = self.client([self.person()], [self.change()], None)
        rows = client.testers(session)
        self.assertEqual((rows[0]['email'], rows[0]['status'], rows[0]['signed_up_at']), ('maya@example.test', 'pending', 1789772520.0))
        audit = client.tester_audit(session, before=9, limit=21)
        self.assertEqual((audit[0]['id'], audit[0]['new_status'], audit[0]['changed_at']), (7, 'approved', 1789809120.0))
        client.set_membership(session, OTHER, 'approved', 1790000000)
        urls = [r.full_url for r in http.requests]
        self.assertEqual(urls, [PROJECT.url + '/rest/v1/rpc/dervo_admin_memberships', PROJECT.url + '/rest/v1/rpc/dervo_admin_membership_audit',
                                PROJECT.url + '/rest/v1/rpc/dervo_set_membership'])
        self.assertEqual(json.loads(http.requests[1].data), {'before_id': 9, 'max_rows': 21})
        sent = json.loads(http.requests[2].data)
        self.assertEqual((sent['target_user'], sent['new_status']), (OTHER, 'approved'))
        self.assertEqual(datetime.fromisoformat(sent['expiry']).timestamp(), 1790000000)
        for request in http.requests:
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + ACCESS, 'the admin’s own session, never a service key')

    def test_testers_answers_are_checked_and_bad_requests_never_leave(self):
        session = Session(ACCESS, REFRESH, USER, 5000)
        for bad in ({}, [self.person(status='admin')], [self.person(is_admin='yes')], [self.person(extra=1)],
                    [self.person(user_id='not-a-uuid')], [self.person(signed_up_at='yesterday')], [self.person(email='a\nb')]):
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                self.client(bad)[0].testers(session)
        for bad in ([self.change(new_status='owner')], [self.change(id=True)], [self.change(changed_at=None)], [self.change()] * 3):
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                self.client(bad)[0].tester_audit(session, limit=2)
        client, http = self.client()
        for call in (lambda: client.set_membership(session, OTHER, 'owner'), lambda: client.set_membership(session, 'x', 'approved'),
                     lambda: client.set_membership(session, OTHER, 'approved', 'soon'), lambda: client.tester_audit(session, limit=0),
                     lambda: client.tester_audit(session, before=-1)):
            with self.assertRaises(AccountError):
                call()
        self.assertEqual(http.requests, [])

    @staticmethod
    def waiting(**changes):
        return {**dict(email='ada@example.test', signed_up_at=1790000000, status='waiting', invited_at=None, expires_at=None,
                       delivery=None), **changes}

    def test_waitlist_calls_go_to_the_website_with_the_session_and_without_the_project_key(self):
        session = Session(ACCESS, REFRESH, USER, 5000)
        sent = {'email': 'ada@example.test', 'result': 'sent', 'expires_at': 1791209600}
        client, http = self.client({'ok': True, 'people': [self.waiting()], 'more': False},
                                   {'ok': True, 'results': [sent, {'email': 'bo@example.test', 'result': 'already_invited'}]})
        self.assertEqual(client.waitlist(session), {'people': [self.waiting()], 'more': False})
        results = client.waitlist_action(session, 'invite', ['ada@example.test', 'bo@example.test'])
        self.assertEqual(results, [sent, {'email': 'bo@example.test', 'result': 'already_invited', 'expires_at': None}])
        listing, batch = http.requests
        self.assertEqual((listing.get_method(), listing.full_url), ('GET', 'https://trydervo.com/api/admin/waitlist'))
        self.assertEqual((batch.get_method(), batch.full_url), ('POST', 'https://trydervo.com/api/admin/invitations'))
        self.assertEqual(json.loads(batch.data), {'action': 'invite', 'emails': ['ada@example.test', 'bo@example.test']})
        for request in http.requests:
            self.assertEqual(request.get_header('User-agent'), 'Dervo')
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + ACCESS)
            self.assertIsNone(request.get_header('Apikey'))
            self.assertNotIn(REFRESH, json.dumps(request.header_items()) + (request.data or b'').decode())

    def test_waitlist_answers_are_checked_and_bad_batches_never_leave(self):
        session = Session(ACCESS, REFRESH, USER, 5000)
        for bad in ({}, {'ok': False, 'people': [], 'more': False}, {'ok': True, 'people': [self.waiting(status='vip')], 'more': False},
                    {'ok': True, 'people': [self.waiting(signed_up_at='today')], 'more': False},
                    {'ok': True, 'people': [self.waiting(delivery='bounced')], 'more': False},
                    {'ok': True, 'people': [self.waiting(token='x')], 'more': False}, {'ok': True, 'people': [self.waiting()], 'more': 'no'}):
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                self.client(bad)[0].waitlist(session)
        for bad in ({'ok': True, 'results': []}, {'ok': True, 'results': [{'email': 'other@example.test', 'result': 'sent'}]},
                    {'ok': True, 'results': [{'email': 'ada@example.test', 'result': 'delivered'}]},
                    {'ok': True, 'results': [{'email': 'ada@example.test', 'result': 'sent', 'code': '123456'}]}):
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                self.client(bad)[0].waitlist_action(session, 'invite', ['ada@example.test'])
        client, http = self.client()
        for action, emails in (('email', ['ada@example.test']), ('invite', []), ('invite', ['ada@example.test'] * 2),
                               ('invite', ['not an email']), ('invite', ['p%d@example.test' % n for n in range(11)])):
            with self.subTest(action=action, emails=emails), self.assertRaises(AccountError):
                client.waitlist_action(session, action, emails)
        self.assertEqual(http.requests, [])

    def test_waitlist_source_preview_and_direct_add_are_checked(self):
        session = Session(ACCESS, REFRESH, USER, 5000)
        preview = {'from': 'Bryce <bryce@notify.trydervo.com>', 'reply_to': 'bryce@trydervo.com',
                   'subject': 'Your Dervo beta invitation', 'text': 'Hey,\n\nYou’re invited.'}
        listing = {'ok': True, 'people': [self.waiting(source='admin')], 'more': False, 'email_preview': preview}
        client, http = self.client(listing, {'ok': True, 'results': [{'email': 'new@example.test', 'result': 'added'}]})
        self.assertEqual(client.waitlist(session)['email_preview'], preview)
        self.assertEqual(client.waitlist_action(session, 'add', [' NEW@Example.test '])[0]['result'], 'added')
        self.assertEqual(json.loads(http.requests[-1].data), {'action': 'add', 'emails': ['new@example.test']})
        for bad in [{**preview, 'from': 'Bryce\nBcc: someone@example.test'}, {**preview, 'text': ''}, {**preview, 'secret': 'x'}]:
            with self.subTest(bad=bad), self.assertRaises(AccountError):
                self.client({**listing, 'email_preview': bad})[0].waitlist(session)
        client, http = self.client()
        for emails in [['ada@example.test', ' ADA@Example.test '], ['a..b@example.test'], ['a@-example.test']]:
            with self.subTest(emails=emails), self.assertRaises(AccountError):
                client.waitlist_action(session, 'add', emails)
        self.assertEqual(http.requests, [])


    # Paid plans and the free trial (8 Oct 2026).
    def plan_data(self, **changes):
        data = {'plan': 'paid', 'environment': 'sandbox', 'trial': {'days': 7, 'mode': 'card', 'ends_at': None, 'available': False},
                'subscription': {'status': 'active', 'interval': 'year', 'period_ends_at': '2027-10-08T00:00:00Z',
                                 'cancel_at': None, 'access_until': '2027-10-10T00:00:00+00:00'},
                'can_buy': False, 'can_manage': True}
        data.update(changes)
        return data

    def test_plan_is_read_as_the_caller_and_checked_field_by_field(self):
        client, http = self.client(self.plan_data())
        plan = client.plan(Session(ACCESS, REFRESH, USER, 4600))
        self.assertEqual(http.requests[0].full_url, PROJECT.url + '/rest/v1/rpc/dervo_plan')
        self.assertEqual(http.requests[0].headers['Authorization'], 'Bearer ' + ACCESS)
        self.assertEqual(plan['subscription']['interval'], 'year')
        self.assertIsInstance(plan['subscription']['period_ends_at'], float)
        for bad in [{'plan': 'free'}, {'environment': 'prod'}, {'can_buy': 'yes'}, {'extra': 1},
                    {'trial': {'days': 0, 'mode': 'card', 'ends_at': None, 'available': True}},
                    {'trial': {'days': 7, 'mode': 'card', 'ends_at': 'soon', 'available': True}},
                    {'trial': {'days': 7, 'mode': 'free', 'ends_at': None, 'available': True}},
                    {'trial': {'days': 7, 'ends_at': None, 'available': True}},
                    {'subscription': {'status': 'active', 'interval': 'week', 'period_ends_at': None, 'cancel_at': None, 'access_until': None}}]:
            client, _ = self.client(self.plan_data(**bad))
            with self.assertRaises(AccountError, msg=bad):
                client.plan(Session(ACCESS, REFRESH, USER, 4600))

    def test_trial_sends_only_a_device_hash(self):
        client, http = self.client({'state': 'started', 'ends_at': '2026-10-15T00:00:00Z'})
        result = client.start_trial(Session(ACCESS, REFRESH, USER, 4600), 'a' * 64)
        self.assertEqual(result['state'], 'started')
        self.assertEqual(json.loads(http.requests[0].data), {'device': 'a' * 64})
        for device in ['A' * 64, 'a' * 63, None, 'IOPlatformUUID']:
            with self.assertRaises(AccountError):
                client.start_trial(Session(ACCESS, REFRESH, USER, 4600), device)
        client, _ = self.client({'state': 'granted', 'ends_at': None})
        with self.assertRaises(AccountError):
            client.start_trial(Session(ACCESS, REFRESH, USER, 4600), 'a' * 64)

    def test_billing_links_are_https_on_paddle_or_trydervo_only(self):
        client, http = self.client({'action': 'checkout', 'url': 'https://trydervo.com/checkout?_ptxn=txn_1'})
        self.assertEqual(client.billing_link(Session(ACCESS, REFRESH, USER, 4600), 'checkout', 'month', 'a' * 64),
                         'https://trydervo.com/checkout?_ptxn=txn_1')
        self.assertEqual(http.requests[0].full_url, PROJECT.url + '/functions/v1/dervo-billing')
        self.assertEqual(json.loads(http.requests[0].data), {'action': 'checkout', 'interval': 'month', 'device': 'a' * 64})
        for url in ['http://trydervo.com/x', 'https://evil.example/x', 'https://trydervo.com.evil.example/',
                    'https://u:p@trydervo.com/', 'https://trydervo.com:8443/', 'file:///etc/passwd']:
            client, _ = self.client({'action': 'checkout', 'url': url})
            with self.assertRaises(AccountError, msg=url):
                client.billing_link(Session(ACCESS, REFRESH, USER, 4600), 'checkout', 'year', 'a' * 64)
        client, _ = self.client()
        for action, interval, device in [('checkout', None, 'a' * 64), ('checkout', 'week', 'a' * 64), ('manage', 'month', None),
                                         ('refund', None, None), ('checkout', 'month', None), ('checkout', 'month', 'A' * 64)]:
            with self.assertRaises(AccountError):
                client.billing_link(Session(ACCESS, REFRESH, USER, 4600), action, interval, device)


if __name__ == '__main__':
    unittest.main()
