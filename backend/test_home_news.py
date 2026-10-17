"""Home says what happened to the work it sent, as it happens (home_news, 9 Oct 2026): a thread Home sent a job to that
finishes, asks, fails or waits for your review gets one line in the request that sent it, with no model call. Real
host and store; the coding request is a stand-in, and tasks are recorded straight into the activity index."""
import json
import time

import conversation_activity
import home_news
from test_project_orchestrator import HomeDispatchTests, answer


class NewsTests(HomeDispatchTests):
    # Its fixture, not its tests: those run in test_project_orchestrator.
    locals().update({name: None for name in dir(HomeDispatchTests) if name.startswith('test_')})

    def setUp(self):
        super().setUp()
        self.n = 0

    def task(self, channel, state, client=None, task_id=None, **more):
        """Home's job's task (or, with ``client``, another request's) in this thread, as the coding runtime saves it."""
        db = self.host.store.db
        reply = self.job
        client = client or f"{reply['id']}-0"
        task_id = task_id or 'task-' + client
        task = dict(dict(id=task_id, context=dict(thread_id=channel, project_id='p'), state=state, runs=[], messages=[]), **more)
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS coding_tasks (id TEXT PRIMARY KEY, document TEXT NOT NULL)')
            db.execute('INSERT OR REPLACE INTO coding_tasks (id, document) VALUES (?, ?)', (task_id, json.dumps(task)))
            db.execute('CREATE TABLE IF NOT EXISTS coding_requests (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, client_id TEXT NOT NULL, '
                       'task_id TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(thread_id, client_id))')
            db.execute('INSERT OR IGNORE INTO coding_requests VALUES (?,?,?,?,?)', ('r-' + task_id, channel, client, task_id, '{}'))
            conversation_activity.record(db, task)

    def say(self, text, client='client-0001'):
        return self.orch('send', text=text, clientId=client, requestId=None)  # as Home sends it: each message a request

    def sent_job(self):
        self.world()
        started = self.start_reply()
        self.ticks()
        reply = dict(self.entry(started['id']), id=started['id'])
        self.assertEqual(reply['parts'][0]['state'], 'sent')
        self.job = reply
        return reply, reply['parts'][0]['channel_id']

    def news(self):
        return [entry for entry in self.orch('list')['entries'] if entry.get('news')]

    def test_a_job_s_thread_finishing_says_so_in_the_request_that_sent_it(self):
        reply, channel = self.sent_job()
        self.task(channel, 'working')
        self.ticks(2)
        self.assertEqual(self.news(), [], 'working is not news')
        self.task(channel, 'completed')
        self.ticks(1)
        news = self.news()
        self.assertEqual([entry['text'] for entry in news], ['Connector warning copy is done.'])
        self.assertEqual(news[0]['request_id'], reply['request_id'])
        self.assertEqual((news[0]['news']['kind'], news[0]['news']['thread']['id']), ('done', channel))
        self.assertEqual(self.prompts[1:], [], 'no model call')
        self.ticks(3)
        self.assertEqual(len(self.news()), 1, 'said once')

    def test_needs_you_and_failed_read_as_what_they_are(self):
        _, channel = self.sent_job()
        for state, text in (('needs_permission', 'Connector warning copy needs your go-ahead to start.'),
                            ('ready_for_review', 'Connector warning copy is done and ready for your review.')):
            self.task(channel, state)
            self.ticks(1)
            self.assertEqual(self.news()[-1]['text'], text)

    def test_the_first_round_after_a_launch_only_marks_where_it_starts(self):
        _, channel = self.sent_job()
        self.task(channel, 'completed')
        del self.host.home_news  # a new launch
        self.ticks(2)
        self.assertEqual(self.news(), [])

    def test_work_you_start_in_the_thread_yourself_says_nothing(self):
        _, channel = self.sent_job()
        self.ticks(1)
        # Work you start in the thread yourself, after Home's job: never posted into Home.
        self.task(channel, 'completed', client='client-yours001', task_id='yours')
        self.ticks(2)
        self.assertEqual(self.news(), [])

    def test_a_news_line_never_becomes_the_reply_a_yes_answers(self):
        self.world()
        self.reply = lambda prompt: answer('I can start that.', offers=[dict(kind='start', title='Connector docs', workstream='Connectors',
                                                                             instruction='Write the docs.', quote='write the connector docs')])
        offered = self.say('write the connector docs', client='client-offer001')['entries'][-1]
        # A line lands in that same request after the offer.
        with self.host.store.db:
            self.host.store.db.execute('INSERT INTO orchestrator_entries VALUES (?,?,?,?,?,?)', (
                'news-1', 'p', 'a', None, time.time(), __import__('json').dumps(dict(kind='reply', state='ready', text='X is done.',
                request_id=offered['request_id'], news=dict(kind='done', thread=dict(id='x', name='X'), at=time.time())))))
        from project_orchestrator import Orchestrator
        context = Orchestrator(self.host.store, host=self.host).request_entries(dict(project_id='p', profile_id='a'), offered['request_id'])
        self.assertEqual(context[-1]['id'], offered['id'])
        self.assertIn('news-1', [entry['id'] for entry in self.orch('list')['entries']], 'the listing shows it')

    def test_lines(self):
        line = lambda **task: home_news.line(dict(dict(id='t', runs=[], messages=[]), **task), 'Billing')
        self.assertEqual(line(state='answered'), ('done', 'Billing is done.'), 'a no-tools answer is done too')
        self.assertIsNone(line(state='idle', review='approved'), 'you approved it')
        self.assertEqual(line(state='idle', review='approved', commit=dict(how='automatic', sha='abc')),
                         ('done', 'Billing is done, and Dervo committed it.'))
        self.assertEqual(home_news.line(dict(id='t', state='working', runs=[], messages=[]), 'Billing', 'Monthly or yearly?'),
                         ('needs_you', 'Billing has a question for you: "Monthly or yearly?"'))
        self.assertIsNone(line(state='working'))

    def test_approving_an_older_task_doesn_t_repeat_a_newer_one_s_line(self):
        _, channel = self.sent_job()
        self.task(channel, 'ready_for_review')
        self.ticks(1)
        self.task(channel, 'idle', client='client-older001', task_id='older')
        self.ticks(1)
        self.task(channel, 'idle', client='client-older001', task_id='older', review='approved')
        self.ticks(2)
        self.assertEqual([entry['text'] for entry in self.news()], ['Connector warning copy is done and ready for your review.'])

if __name__ == '__main__':
    import unittest
    unittest.main()
