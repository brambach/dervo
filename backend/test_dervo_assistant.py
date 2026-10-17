"""Home's scheduled jobs and heartbeat (dervo_assistant.py). Disposable host stores and synthetic starts: no provider
calls or external accounts. Times are fixed, so nothing here waits on the clock."""
import json
import time
import unittest
from datetime import datetime
from unittest.mock import patch

import dervo_assistant as assistant
from dervo_connections import Rejected
from test_dervo_agents import Base

def home_entries(host):
    db = host.store.db
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='orchestrator_entries'").fetchone():
        return []  # Home never opened and nothing was said there
    return [json.loads(r[0]) for r in db.execute('SELECT document FROM orchestrator_entries ORDER BY rowid')]


NOW = time.mktime((2026, 10, 8, 13, 0, 0, 0, 0, -1))  # Thursday 8 Oct 2026, 13:00 local


class Schedules(unittest.TestCase):
    def test_daily_on_weekdays_skips_the_weekend(self):
        friday_evening = time.mktime((2026, 10, 9, 20, 0, 0, 0, 0, -1))
        due = assistant.next_due({'kind': 'daily', 'time': '08:00', 'days': assistant.WEEKDAYS}, friday_evening)
        self.assertEqual(datetime.fromtimestamp(due).strftime('%a %H:%M'), 'Mon 08:00')

    def test_every_keeps_its_rhythm_and_runs_missed_slots_once(self):
        every = {'kind': 'every', 'minutes': 60}
        self.assertEqual(assistant.next_due(every, NOW), NOW + 3600)
        # Asleep for five hours since the slot at NOW: the next slot is the one after now, not five catch-up runs.
        self.assertEqual(assistant.next_due(every, NOW + 5 * 3600 + 60, NOW), NOW + 6 * 3600)

    def test_once_is_done_after_its_time(self):
        self.assertIsNone(assistant.next_due({'kind': 'once', 'at': NOW}, NOW))

    def test_checks_refuse_bad_shapes(self):
        for bad in [None, {'kind': 'every', 'minutes': 1}, {'kind': 'every', 'minutes': 60.0}, {'kind': 'daily', 'time': '8am', 'days': [0]},
                    {'kind': 'daily', 'time': '08:00', 'days': []}, {'kind': 'daily', 'time': '08:00', 'days': [7]},
                    {'kind': 'cron', 'expr': '* * * * *'}, {'kind': 'once', 'at': NOW, 'extra': 1}]:
            with self.subTest(bad=bad), self.assertRaises(Rejected):
                assistant.check_schedule(bad)

    def test_plain_words(self):
        cases = {
            'every weekday at 8 @catch-up catch me up': ('weekdays at 08:00', '@catch-up catch me up'),
            'daily at 7:30am run the gardener': ('every day at 07:30', 'run the gardener'),
            'Mondays and Fridays at 5pm @reviewer review': ('Mondays, Fridays at 17:00', '@reviewer review'),
            'every 2 hours check CI': ('every 2 hours', 'check CI'),
            'every mon and wed at 10 do x': ('Mondays, Wednesdays at 10:00', 'do x'),
            'in 2 hours check': ('once, Thu 08 Oct at 15:00', 'check'),
            'tomorrow at 9 remind me': ('once, Fri 09 Oct at 09:00', 'remind me'),
        }
        for text, (when, rest) in cases.items():
            with self.subTest(text=text):
                schedule, left = assistant.parse_schedule(text, NOW)
                self.assertEqual((assistant.describe(schedule), left), (when, rest))
        self.assertEqual(assistant.describe(assistant.parse_schedule('on sunday at 6pm plan the week', NOW)[0]), 'Sundays at 18:00')
        for text in ('look at 2 files', 'check the money', 'sunday best', 'the monitor is wed'):
            with self.subTest(text=text):
                self.assertEqual(assistant.parse_schedule(text, NOW), (None, text))


class Jobs(Base):
    def setUp(self):
        super().setUp()
        self.reader = self.agent('catch-up')
        self.other = self.thread('Export fix')

    def job(self, gate='always', schedule=None, **fields):
        return self.host.dispatch('assistant_job_save', dict(projectId='p', name='Morning catch-up', prompt='Catch me up.',
                                                             agentId=self.reader['id'], gate=gate,
                                                             schedule=schedule or {'kind': 'every', 'minutes': 60}) | fields)

    def sweep(self, at):
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            self.host.assistant().sweep(at)

    def home(self):
        return home_entries(self.host)

    def test_due_job_runs_as_its_agent_without_full_access_and_reports_in_home(self):
        job = self.job()
        self.sweep(job['next_due'] - 1)
        self.assertEqual(self.started, [])  # not due yet
        self.sweep(job['next_due'] + 1)
        [task] = self.started
        self.assertEqual(task['context']['thread_id'], self.reader['channel_id'])
        self.assertTrue(task.get('read_only'))
        self.assertEqual(task.get('requested_by'), 'home')
        self.assertIsNot(task.get('clicked'), True)  # an unclicked Home job: never full access
        self.assertIn('scheduled job "Morning catch-up"', task['messages'][0]['text'])
        self.finish(task['id'], answer='Two threads finished, one waits on you.')
        self.sweep(job['next_due'] + 2)
        said = [e for e in self.home() if (e.get('assistant') or {}).get('source') == 'job']
        self.assertEqual(len(said), 1)
        self.assertIn('one waits on you', said[0]['text'])
        [run] = self.host.dispatch('assistant_runs', {'projectId': 'p', 'jobId': job['id']})
        self.assertEqual(run['state'], 'done')

    def test_a_slot_runs_once_even_across_a_restart(self):
        job = self.job()
        with patch('dervo_assistant.Assistant._start', side_effect=lambda job, run, **kw: run):  # closes before sending
            self.host.assistant().sweep(job['next_due'] + 1)
        self.reopen()
        self.sweep(job['next_due'] + 2)
        self.sweep(job['next_due'] + 3)
        self.assertEqual(len(self.started), 1)
        runs = self.host.dispatch('assistant_runs', {'projectId': 'p', 'jobId': job['id']})
        self.assertEqual([r['state'] for r in runs], ['working'])

    def test_waking_after_a_long_sleep_runs_once(self):
        job = self.job()
        self.sweep(job['next_due'] + 10 * 3600)
        self.assertEqual(len(self.started), 1)
        saved = self.host.assistant().job('p', job['id'])
        self.assertGreater(saved['next_due'], job['next_due'] + 10 * 3600)

    def test_changed_gate_skips_quietly_when_nothing_moved(self):
        job = self.job(gate='changed')
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.started, [])
        [run] = self.host.dispatch('assistant_runs', {'projectId': 'p', 'jobId': job['id']})
        self.assertEqual(run['state'], 'skipped')
        self.assertEqual(self.home(), [])  # a skip says nothing
        self.request_in(self.other)  # another thread moves
        saved = self.host.assistant().job('p', job['id'])
        self.sweep(saved['next_due'] + 1)
        self.assertEqual(len([t for t in self.started if t['context']['thread_id'] == self.reader['channel_id']]), 1)

    def request_in(self, cid):
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            return self.host.dispatch('coding_request', {'projectId': 'p', 'channelId': cid, 'clientId': 'c' + cid[:8], 'text': 'Do it.'})

    def test_needs_you_gate(self):
        job = self.job(gate='needs_you')
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.started, [])
        task_id = self.request_in(self.other)['taskId']
        self.finish(task_id, state='blocked', answer='')
        saved = self.host.assistant().job('p', job['id'])
        self.sweep(saved['next_due'] + 1)
        self.assertEqual(self.started[-1]['context']['thread_id'], self.reader['channel_id'])

    def test_pause_resume_delete_and_run_now(self):
        job = self.job()
        self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': True})
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.started, [])
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            run = self.host.dispatch('assistant_job_run_now', {'projectId': 'p', 'jobId': job['id']})
        self.assertEqual(run['state'], 'working')
        self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': False})
        self.assertEqual(self.host.dispatch('assistant_job_delete', {'projectId': 'p', 'jobId': job['id']}), [])

    def test_once_job_finishes_and_past_times_are_refused(self):
        job = self.job(schedule={'kind': 'once', 'at': time.time() + 60})
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.host.assistant().job('p', job['id'])['state'], 'done')
        with self.assertRaises(Rejected):
            self.job(schedule={'kind': 'once', 'at': time.time() - 60})

    def test_a_deleted_agent_fails_the_run_and_says_so(self):
        job = self.job()
        self.host.dispatch('agent_delete', {'projectId': 'p', 'agentId': self.reader['id']})
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.started, [])
        self.assertIn('deleted', self.host.assistant().runs(job['id'])[0]['detail'])

    def test_one_start_a_round_and_the_rest_on_the_next_rounds(self):
        jobs = [self.job(name=f'Job {i}') for i in range(4)]
        at = max(j['next_due'] for j in jobs) + 1
        more = []
        for _ in range(4):
            with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
                more.append(self.host.assistant().sweep(at))
            self.assertEqual(len(self.started), len(more))
        self.assertEqual(more, [True, True, True, False])

    def test_a_skipped_gate_doesnt_use_the_start(self):
        quiet = self.job(name='Quiet', gate='changed')
        loud = self.job(name='Loud')
        self.sweep(max(quiet['next_due'], loud['next_due']) + 1)
        self.assertEqual(len(self.started), 1)

    def test_a_changed_agent_pauses_its_jobs_and_says_why(self):
        job = self.job()
        self.agent('catch-up', scope='change', agentId=self.reader['id'])
        self.sweep(job['next_due'] + 1)
        self.assertEqual(self.started, [])
        saved = self.host.assistant().job('p', job['id'])
        self.assertEqual(saved['state'], 'paused')
        self.assertIn('changed after you set this job up', saved['paused_why'])
        [said] = [e for e in self.home() if (e.get('assistant') or {}).get('source') == 'job']
        self.assertIn('didn’t finish', said['assistant']['title'])
        self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': False})  # approve as it is now
        saved = self.host.assistant().job('p', job['id'])
        self.sweep(saved['next_due'] + 1)
        self.assertEqual(len(self.started), 1)
        self.assertFalse(self.started[0].get('read_only'))

    def test_a_job_that_cant_start_says_so_once_then_pauses(self):
        job = self.job()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE profiles SET removed_at=1 WHERE id='p'")
        at = job['next_due']
        for slot in range(3):
            at += 3600
            self.sweep(at)
        said = [e for e in self.home() if (e.get('assistant') or {}).get('source') == 'job']
        self.assertEqual(len(said), 2)  # the first failure, then the pause
        self.assertIn('Paused after 3', said[-1]['text'])
        self.assertEqual(self.host.assistant().job('p', job['id'])['state'], 'paused')

    def test_a_cancelled_queued_run_fails_instead_of_waiting_forever(self):
        job = self.job()
        with patch.object(self.host.coding_tasks(), 'busy_thread', lambda thread: True), \
                patch.object(self.host.coding, 'owner_slot_problem', lambda thread: 'Running'):
            self.sweep(job['next_due'] + 1)
        [run] = self.host.assistant().runs(job['id'])
        self.assertEqual(run['state'], 'queued')
        item = self.host.coding.conversation.queue.find(self.reader['channel_id'], run['client_id'])
        self.host.coding.conversation.queue.remove(item)
        self.sweep(job['next_due'] + 2)
        self.assertEqual(self.host.assistant().runs(job['id'])[0]['state'], 'failed')

    def test_a_lost_run_is_unknown_not_stopped(self):
        job = self.job()
        self.sweep(job['next_due'] + 1)
        self.finish(self.started[0]['id'], state='disconnected', answer='')
        self.sweep(job['next_due'] + 2)
        run = self.host.assistant().runs(job['id'])[0]
        self.assertEqual(run['state'], 'unknown')
        self.assertIn('can’t tell', run['detail'])

    def test_a_job_runs_notes_wait_for_the_person(self):
        import thread_notes
        job = self.job()
        self.sweep(job['next_due'] + 1)
        self.assertTrue(thread_notes._automatic(self.host.store.db, self.started[0]['id']))

    def test_other_agents_runs_dont_count_as_the_project_moving(self):
        other = self.agent('gardener')
        job = self.job(gate='changed')
        self.request_in(other['channel_id'])
        self.sweep(job['next_due'] + 1)
        self.assertEqual([t for t in self.started if t['context']['thread_id'] == self.reader['channel_id']], [])

    def test_resuming_a_one_off_whose_time_passed_is_refused(self):
        job = self.job(schedule={'kind': 'once', 'at': time.time() + 60})
        self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': True})
        with patch('dervo_assistant.time.time', return_value=time.time() + 3600), self.assertRaises(Rejected):
            self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': False})

    def test_editing_keeps_a_paused_job_paused_and_credentials_are_refused(self):
        job = self.job()
        self.host.dispatch('assistant_job_pause', {'projectId': 'p', 'jobId': job['id'], 'paused': True})
        self.assertEqual(self.job(jobId=job['id'], name='Renamed')['state'], 'paused')
        with self.assertRaises(Rejected):
            self.job(prompt='Use sk-ant-api03-' + 'A' * 90)

    def test_parse_proposes_without_saving(self):
        found = self.host.dispatch('assistant_parse', {'projectId': 'p', 'text': 'every weekday at 8, @catch-up catch me up'})
        self.assertEqual((found['when'], found['handle'], found['prompt'], found['missing']), ('weekdays at 08:00', 'catch-up', 'catch me up', []))
        self.assertEqual(self.host.dispatch('assistant_jobs', {'projectId': 'p'}), [])
        vague = self.host.dispatch('assistant_parse', {'projectId': 'p', 'text': 'catch me up sometimes'})
        self.assertEqual(vague['missing'], ['when', 'agent'])

    def test_parse_gate_defaults_to_changed_for_repeating_jobs(self):
        def gate(text):
            return self.host.dispatch('assistant_parse', {'projectId': 'p', 'text': text})['gate']
        self.assertEqual(gate('every weekday at 8, @catch-up catch me up'), 'changed')
        self.assertEqual(gate('every weekday at 8, @catch-up catch me up even if nothing moved'), 'always')
        self.assertEqual(gate('tomorrow at 9am @catch-up catch me up'), 'always')
        self.assertEqual(gate('every hour @catch-up check in, only when something needs me'), 'needs_you')


class Heartbeat(Base):
    def setUp(self):
        super().setUp()
        self.here = self.thread('Auth bug')
        self.agent('reviewer')  # a project with an agent has the assistant
        self.beat(NOW - 3600)   # the first beat only learns what's waiting

    def beat(self, at):
        self.host.assistant().beat(at)

    def said(self):
        return [e for e in home_entries(self.host) if (e.get('assistant') or {}).get('source') == 'heartbeat']

    def blocked(self):
        with patch.object(self.host.coding_tasks(), 'start', side_effect=self.fake_start):
            task_id = self.host.dispatch('coding_request', {'projectId': 'p', 'channelId': self.here, 'clientId': 'auth-1', 'text': 'Fix it.'})['taskId']
        self.finish(task_id, state='blocked', answer='')

    def test_silent_when_nothing_needs_you(self):
        self.beat(NOW)
        self.assertEqual(self.said(), [])

    def test_says_a_new_need_once(self):
        self.blocked()
        self.beat(NOW)
        [said] = self.said()
        self.assertEqual(said['assistant']['title'], 'One thread needs you')
        self.assertIn('#Auth bug', said['text'])
        self.beat(NOW + 3600)  # still waiting, already said
        self.assertEqual(len(self.said()), 1)

    def test_respects_its_interval_and_switch(self):
        self.beat(NOW)
        self.blocked()
        self.beat(NOW + 60)  # inside the 30 minutes
        self.assertEqual(self.said(), [])
        self.host.dispatch('assistant_heartbeat_set', {'projectId': 'p', 'enabled': False, 'minutes': 30})
        self.beat(NOW + 3600)
        self.assertEqual(self.said(), [])
        with self.assertRaises(Rejected):
            self.host.dispatch('assistant_heartbeat_set', {'projectId': 'p', 'enabled': True, 'minutes': 1})

    def test_the_first_beat_never_dumps_whats_already_waiting(self):
        other = self.thread('Other project thread')
        self.host.store.add_project('q', self.project, 'p', 'synthetic-model')
        self.blocked()
        self.agent('reviewer2')
        self.beat(NOW)  # project p's beat says it, but nothing else
        self.assertEqual(len(self.said()), 1)

    def test_projects_without_an_agent_have_no_heartbeat(self):
        self.host.store.add_project('q', self.project, 'p', 'synthetic-model')
        self.host.assistant().beat(NOW)
        self.assertIsNone(self.host.store.db.execute("SELECT 1 FROM assistant_heartbeat WHERE project_id='q'").fetchone())

    def test_opened_threads_are_not_repeated(self):
        self.blocked()
        self.host.dispatch('conversation_seen', {'projectId': 'p', 'channelId': self.here})
        self.beat(NOW)
        self.assertEqual(self.said(), [])


if __name__ == '__main__':
    unittest.main()
