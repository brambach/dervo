"""Saved Home requests keep their context and authorization separate."""
import uuid
from test_project_orchestrator import HomeFixture, answer
from project_orchestrator import Rejected


class RequestTests(HomeFixture):
    def send_request(self, text, request_id=None):
        return self.orch('send', text=text, requestId=request_id, clientId='request-' + uuid.uuid4().hex)

    def test_new_request_excludes_other_request_history(self):
        first = self.send_request('Help me with purple-zebra-notes')['entries'][-1]['request_id']
        second = self.send_request('Help me with orange-otter-notes')['entries'][-1]['request_id']
        self.assertNotEqual(first, second)
        self.assertNotIn('purple-zebra-notes', self.prompts[-1])
        self.send_request('Continue with a summary', first)
        self.assertIn('purple-zebra-notes', self.prompts[-1])
        self.assertNotIn('orange-otter-notes', self.prompts[-1])

    def test_unknown_or_wrong_scope_request_is_rejected(self):
        for value in ('missing', 12, {}, ''):
            with self.assertRaises(Rejected):
                self.send_request('go', value)
        self.assertEqual(self.orch('list')['entries'], [])

    def test_legacy_request_can_be_reopened_without_newer_context(self):
        legacy = self.say('legacy-purple-zebra')['entries'][0]['id']
        self.say('unrelated-orange-otter', client='client-0002')
        result = self.send_request('Please explain more', legacy)
        self.assertEqual(result['entries'][-1]['request_id'], legacy)
        self.assertIn('legacy-purple-zebra', self.prompts[-1])
        self.assertNotIn('unrelated-orange-otter', self.prompts[-1])

    def test_go_cannot_accept_an_offer_from_another_request(self):
        self.reply = lambda prompt: answer('Say go if you want me to start APNIC catch-up.')
        self.send_request('Help me catch up on APNIC')
        self.reply = lambda prompt: answer(actions=[dict(kind='start', title='APNIC catch-up', workstream='Clients', focus='APNIC', quote='go')])
        result = self.send_request('go')['entries'][-1]
        self.assertEqual(result['parts'], [])

    def test_client_retry_keeps_one_request(self):
        args=dict(text='Hello', requestId=None, clientId='client-0001')
        first=self.orch('send', **args)
        second=self.orch('send', **args)
        self.assertEqual(first['entries'], second['entries'])
        self.assertEqual(len(self.prompts), 1)

    def test_clarification_choice_routes_original_request_after_other_work(self):
        question=dict(ask='Which client?', options=[dict(label='APNIC'), dict(label='Billi')])
        self.reply=lambda prompt: answer('Which client?', question=question)
        request=self.send_request('Prepare a client update')['entries'][-1]['request_id']
        self.reply=lambda prompt: answer('OK')
        self.send_request('Other unrelated work')
        self.reply=lambda prompt: answer(actions=[dict(kind='start',title='APNIC update',workstream='Clients',focus='APNIC',quote='APNIC')])
        reply=self.send_request('APNIC', request)['entries'][-1]
        self.assertEqual(len(reply['parts']), 1)
        self.assertIn('Prepare a client update', reply['parts'][0]['text'])
        self.assertNotIn('Other unrelated work', reply['parts'][0]['text'])

    def test_clarification_does_not_turn_a_question_into_work(self):
        self.reply=lambda prompt: answer('Which client?', question=dict(ask='Which client?', options=[dict(label='APNIC'), dict(label='Billi')]))
        request=self.send_request('What is a client update?')['entries'][-1]['request_id']
        self.reply=lambda prompt: answer(actions=[dict(kind='start',title='Client update',workstream='Clients',focus='client update',quote='APNIC',context_quote='client update')])
        reply=self.send_request('APNIC', request)['entries'][-1]
        self.assertEqual(reply['parts'], [])
