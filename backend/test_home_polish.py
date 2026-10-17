"""Home polish sweep, 8 Oct: a thread Home filed reads as Home's, "you asked" is your words and never Dervo's routing
preamble, and a failure says why in words, never an exception's class name."""
import unittest

import chat_links
import project_orchestrator as po
import test_project_orchestrator as tpo


class PolishTests(tpo.HomeDispatchTests):
    locals().update({name: None for name in dir(tpo.HomeDispatchTests) if name.startswith('test_')})

    def test_a_thread_home_starts_in_a_workstream_reads_as_home_s_filing(self):
        self.world()
        self.start_reply('Connector warning copy')
        self.ticks()
        thread = self.sent[0]['channelId']
        record = self.host.dispatch('organisation_get', dict(projectId='p', channelId=thread))
        self.assertNotEqual(record.get('groupedBy'), 'you')
        rows = {row['id']: row for row in po.Orchestrator(self.host.store, host=self.host).ideas.conversations(self.scope_of())}
        organisation = po.Orchestrator(self.host.store, host=self.host).ideas.organisation_of(thread)
        self.assertEqual(po.Orchestrator.filed_by(organisation, rows[thread]['idea_id']), po.BY_HOME)

    def scope_of(self):
        return po.scope_of(self.host.store, 'p')


class WordsTests(unittest.TestCase):
    def test_your_words_from_a_routed_brief(self):
        brief = ("Home routed this request to this chat. Work only on the quoted focus.\n\nFocus for this chat:\nwarning\n\n"
                 "Your request:\nmake the warning red\nand bigger\n\nPrevious request for context:\nearlier\n\n"
                 'What you said on Home: "make the warning red"')
        self.assertEqual(chat_links.your_words(brief), 'make the warning red\nand bigger')
        self.assertEqual(chat_links.your_words('fix the picker'), 'fix the picker')
        self.assertIsNone(chat_links.your_words(None))

    def test_a_running_request_reads_as_your_words(self):
        import conversation_activity
        brief = 'Home routed this request to this chat. Work only on the quoted focus.\n\nYour request:\nmake the warning red'
        task = dict(state='working', runs=[], messages=[dict(role='user', text=brief)])
        self.assertEqual(conversation_activity.brief_of(task)[3], 'make the warning red')

    def test_only_a_dropped_connection_reads_as_one(self):
        self.assertEqual(po.failure_words(ConnectionResetError()), 'the connection dropped')
        self.assertNotEqual(po.failure_words(OSError(28, 'No space left on device')), 'the connection dropped')

    def test_a_failure_never_names_an_exception_class(self):
        for exc in (FileNotFoundError('x'), PermissionError('x'), TimeoutError(), ConnectionResetError(), KeyError('k'), RuntimeError('r')):
            words = po.failure_words(exc)
            self.assertNotIn('Error', words)
            self.assertTrue(words and words[0].islower())


if __name__ == '__main__':
    unittest.main()
