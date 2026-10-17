"""Home's agent, model and thinking level (beta.15 group 8): what orchestrator_send accepts as the choice for the jobs
it starts, what it refuses, and that the choice rides on your message and its reply for Home.tsx to apply."""
import unittest

from dervo_connections import Rejected
from test_project_orchestrator import HomeFixture


class LaunchTests(HomeFixture):
    def setUp(self):
        super().setUp()
        self.host.store.add_profile('mine', 'claude', self.host.root / 'profiles' / 'mine', 'me@example.test', 'personal')
        self.host.store.add_profile('cx', 'codex', self.host.root / 'profiles' / 'cx', 'me@example.test', 'personal')
        self.host.store.add_profile('job', 'claude', self.host.root / 'profiles' / 'job', 'me@acme.test', 'work')

    def send(self, launch, client='client-0001'):
        return self.orch('send', text='Have Codex search the docs, think hard', clientId=client, launch=launch)

    def entries(self, listing):
        you = next(e for e in listing['entries'] if e['kind'] == 'you')
        reply = next(e for e in listing['entries'] if e['kind'] == 'reply' and e.get('answers') == you['id'])
        return you, reply

    def test_the_choice_rides_on_your_message_and_its_reply(self):
        you, reply = self.entries(self.send(dict(profileId='cx', model='gpt-6-sol', effort='high')))
        expected = dict(profile_id='cx', provider='codex', model='gpt-6-sol', effort='high')
        self.assertEqual(you['launch'], expected)
        self.assertEqual(reply['launch'], expected)

    def test_no_effort_keeps_the_threads_level_and_null_is_the_default(self):
        _, kept = self.entries(self.send(dict(profileId='mine', model='claude-opus-5-5')))
        self.assertNotIn('effort', kept['launch'])
        self.orch('send', text='Another', clientId='client-0002', launch=dict(profileId='mine', model='claude-sonnet-5', effort=None))
        newest = [e for e in self.orch('list')['entries'] if e['kind'] == 'reply'][-1]
        self.assertIsNone(newest['launch']['effort'])

    def test_without_a_choice_nothing_changes(self):
        you, reply = self.entries(self.orch('send', text='Tidy the sidebar', clientId='client-0003'))
        self.assertNotIn('launch', you)
        self.assertNotIn('launch', reply)

    def test_a_work_account_an_unknown_account_and_a_bad_level_are_refused_before_anything_is_recorded(self):
        for launch in (dict(profileId='job', model='claude-opus-5-5'),
                       dict(profileId='nobody', model='claude-opus-5-5'),
                       dict(profileId='mine', model='claude-opus-5-5', effort='turbo'),
                       dict(profileId='mine', model=' claude-opus-5-5'),
                       dict(profileId='mine', model='claude-opus-5-5', agent='codex'),
                       dict(model='claude-opus-5-5')):
            with self.subTest(launch=launch), self.assertRaises(Rejected):
                self.send(launch, client='client-0009')
        self.assertEqual(self.orch('list')['entries'], [])


if __name__ == '__main__':
    unittest.main()
