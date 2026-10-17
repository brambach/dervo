"""Disposable host stores and synthetic starts. No provider calls or external accounts."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from host import Host, ACTOR
from dervo_connections import Rejected
import thread_fanout


class ThreadFanoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'test_sample.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n')
        self.host = Host(self.root / 'data')
        self.addCleanup(lambda: self.host.close())
        self.host.store.add_profile('p', 'codex', self.root / 'data/profiles/p', 'fixture@example.test', 'personal')
        self.host.store.add_project('p', self.project, 'p', 'synthetic-model')
        self.channel = self.host.dispatch('create_conversation', {'projectId': 'p', 'name': 'Client tickets', 'private': True})['id']
        self.scope = dict(projectId='p', channelId=self.channel)
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        self.task = self.host.dispatch('task_create', {**self.scope, 'text': 'Propose separate threads for these three tickets.', 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        self.task.update(origin='conversation', state='answered')
        self.host.coding.save(self.task)
        self.args = {**self.scope, 'taskId': self.task['id']}

    def proposal(self, key='ticket-1', **extra):
        return thread_fanout.capture(self.host.coding, self.task, dict(proposal_key=key, title='Review ticket 1', purpose='Draft a focused answer', request='Read the local notes. Draft a reply without sending it.', **extra))

    def list(self):
        return self.host.dispatch('task_thread_proposals', self.args)

    def start(self, proposal):
        return self.host.dispatch('task_thread_start', {**self.args, 'proposalId': proposal['id']})

    def fake_start(self, task):
        task['state'] = 'working'
        return self.host.coding.save(task)

    def full_access_project(self):
        """Full access on for this project: a git repository, coding allowed for the whole folder, then the setting."""
        import subprocess
        for args in (['init', '-q'], ['config', 'user.name', 'Fixture'], ['config', 'user.email', 'f@example.test'],
                     ['add', '-A'], ['commit', '-qm', 'start']):
            subprocess.run(['/usr/bin/git', *args], cwd=self.project, check=True, capture_output=True)
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': [], 'testFile': None, 'projectAccess': True, 'checkProfile': 'manual-review'})
        self.assertTrue(self.host.dispatch('coding_full_access_set', {**self.scope, 'enabled': True, 'warning': 'beta.15'})['full_access']['active'])

    def source(self, client, **fields):
        """A new source task in this thread, started the way the composer or Home starts one, then finished."""
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            reply = self.host.dispatch('coding_request', {**self.scope, 'clientId': client, 'text': 'Look into the three tickets.', **fields})
        self.task = self.host.coding.read(reply['taskId'], dict(project_id='p', thread_id=self.channel, actor=ACTOR))
        self.task['state'] = 'answered'
        self.host.coding.save(self.task)
        self.args['taskId'] = self.task['id']
        return self.task

    def child(self, started):
        return self.host.coding.read(started['taskId'], dict(project_id='p', thread_id=started['channelId'], actor=ACTOR))

    def test_a_child_gets_only_the_access_its_source_had(self):
        """Security review H1, 23 Sep: Start thread sent every proposal as a clicked Home job. A Home job nobody clicked
        runs without the shell in a full-access project, but the child it proposed got full access in one click."""
        import conversation_coding
        self.full_access_project()
        source = self.source('home-job', origin='home')
        self.assertNotEqual(source.get('full_access'), True)
        self.assertEqual(source['notice'], conversation_coding.HOME_LIMITED)
        proposal = self.proposal()
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            started = self.start(proposal)
        child = self.child(started)
        self.assertNotEqual(child.get('full_access'), True, 'a restricted source proposes a restricted child')
        self.assertEqual((child['requested_by'], child['clicked'], child['notice']), ('home', False, conversation_coding.HOME_LIMITED))
        self.assertIs(self.list()[0]['fullAccess'], False)
        # Negative control: a message you typed in the thread had full access, and so does the child it proposes.
        typed = self.source('typed')
        self.assertIs(typed['full_access'], True)
        proposal = self.proposal()
        self.assertIs(self.list()[0]['fullAccess'], True, 'the row says the start gives full access')
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            started = self.start(proposal)
        child = self.child(started)
        self.assertIs(child['full_access'], True)
        self.assertEqual((child['requested_by'], child['clicked'], child.get('notice')), ('home', True, None))
        # A read-only source's child is read-only, so its row never promises full access.
        self.source('reading', readOnly=True)
        self.proposal()
        self.assertIs(self.list()[0]['fullAccess'], False)

    def test_proposal_is_durable_bounded_and_does_not_create_or_start_a_thread(self):
        before = self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0]
        first = self.proposal()
        self.assertEqual(self.proposal()['id'], first['id'])
        self.proposal('ticket-2')
        self.proposal('ticket-3')
        with self.assertRaisesRegex(Rejected, 'three thread proposals'): self.proposal('ticket-4')
        with self.assertRaisesRegex(Rejected, 'different request'):
            thread_fanout.capture(self.host.coding, self.task, dict(proposal_key='ticket-1', title='Changed', purpose='Different', request='Changed work'))
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0], before)
        self.assertEqual(len(self.list()), 3)
        self.host.close()
        self.host = Host(self.root / 'data')
        self.assertEqual(len(self.list()), 3)
        self.assertEqual(self.list()[0]['source_request'], 'Propose separate threads for these three tickets.')

    def test_explicit_start_keeps_parent_binding_visibility_and_one_request(self):
        proposal = self.proposal()
        with self.host.store.db:
            self.host.store.db.execute("UPDATE projects SET model='different-default' WHERE id='p'")
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start) as start:
            first = self.start(proposal)
            again = self.start(proposal)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(first['channelId'], again['channelId'])
        binding = self.host.store.binding(first['channelId'], 'p', actor=ACTOR)
        self.assertEqual((binding['profile_id'], binding['model'], binding['visibility']), ('p', 'synthetic-model', 'private'))
        self.assertNotEqual(first['channelId'], self.channel)
        child = self.host.coding.read(first['taskId'], dict(project_id='p', thread_id=first['channelId'], actor=ACTOR))
        self.assertEqual(child['thread_source']['channelId'], self.channel)
        self.assertEqual(child['thread_source']['purpose'], 'Draft a focused answer')
        self.assertNotIn(thread_fanout.TOOL, self.host.coding.tools_for(child))
        with self.assertRaises(Rejected): thread_fanout.capture(self.host.coding, child, dict(proposal_key='nested', title='Nested', purpose='More', request='More work'))
        self.assertEqual(len(self.host.coding.conversation.requests(first['channelId'])), 1)

    def test_acknowledgement_failure_reuses_the_recorded_child_and_does_not_replay(self):
        proposal = self.proposal()
        original = self.host.dispatch
        def fail_after_start(op, args):
            result = original(op, args)
            if op == 'coding_request': raise RuntimeError('Synthetic acknowledgement loss')
            return result
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start) as start:
            with patch.object(self.host, 'dispatch', side_effect=fail_after_start):
                first = self.start(proposal)
            second = self.start(proposal)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(first['channelId'], second['channelId'])
        self.assertEqual(second['state'], 'started')

    def test_failed_start_retains_one_child_for_retry_and_full_request(self):
        proposal = self.proposal()
        with patch.object(self.host.coding, 'start', side_effect=Rejected('Synthetic provider unavailable')):
            first = self.start(proposal)
        # The normal request ledger decides whether a failed start is already recorded; the proposal never creates another child.
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            second = self.start(proposal)
        self.assertEqual(first['channelId'], second['channelId'])
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0], 2)
        self.assertEqual(second['request'], 'Read the local notes. Draft a reply without sending it.')

    def test_changed_source_model_or_access_refuses_start_and_keeps_proposal_visible(self):
        proposal = self.proposal()
        self.host.dispatch('conversation_model_set', {**self.scope, 'expectedModel': 'synthetic-model', 'model': 'new-model'})
        self.assertFalse(self.list()[0]['canStart'])
        with self.assertRaises(Rejected): self.start(proposal)
        self.host.dispatch('conversation_model_set', {**self.scope, 'expectedModel': 'new-model', 'model': 'synthetic-model'})
        self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertFalse(self.list()[0]['canStart'])
        with self.assertRaisesRegex(Rejected, 'access changed'): self.start(proposal)
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0], 1)

    def test_queue_uses_the_same_child_request_and_read_only_choice(self):
        proposal = self.proposal()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks are running'):
            first = self.start(proposal)
            second = self.start(proposal)
        self.assertEqual(first['state'], 'queued')
        self.assertEqual(first['channelId'], second['channelId'])
        queued = self.host.coding.conversation.queue.items(first['channelId'])
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['client_id'], 'thread-proposal-' + proposal['id'])
        # The source here never had full access, so the queued child waits as a Home job nobody clicked (H1, 23 Sep).
        self.assertEqual((queued[0]['args']['origin'], queued[0]['args']['clicked']), ('home', False))

    def test_dismissal_cannot_cancel_started_work_or_cross_thread_scope(self):
        proposal = self.proposal()
        wrong = {**self.args, 'channelId': 'not-this-thread', 'proposalId': proposal['id']}
        with self.assertRaises(Rejected): self.host.dispatch('task_thread_start', wrong)
        dismissed = self.host.dispatch('task_thread_dismiss', {**self.args, 'proposalId': proposal['id']})
        self.assertEqual(dismissed['state'], 'dismissed')
        with self.assertRaises(Rejected): self.start(proposal)
        active = self.proposal('ticket-2')
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start): self.start(active)
        with self.assertRaisesRegex(Rejected, 'has started'):
            self.host.dispatch('task_thread_dismiss', {**self.args, 'proposalId': active['id']})

    # ---------------------------------------------------------------- Home's Suggestions (9 Oct)
    def suggested(self, now=None, profile='p'):
        args = {'projectId': 'p', 'profileId': profile}
        return thread_fanout.home_dispatch(self.host, 'home_suggestions', args, ACTOR, now) if now else self.host.dispatch('home_suggestions', args)

    def test_home_lists_the_proposals_nobody_started_or_dismissed(self):
        self.assertEqual(self.suggested(), {'suggestions': [], 'more': 0}, 'nothing proposed, nothing suggested')
        first = self.proposal()
        found = self.suggested()['suggestions']
        self.assertEqual([(row['id'], row['title'], row['purpose'], row['channelId'], row['thread'], row['taskId'], row['provider'])
                          for row in found],
                         [(first['id'], 'Review ticket 1', 'Draft a focused answer', self.channel, 'Client tickets', self.task['id'], 'codex')])
        self.assertNotIn('request', found[0], 'the whole request shows in the thread, before Start')
        self.host.dispatch('task_thread_dismiss', {**self.args, 'proposalId': first['id']})
        self.assertEqual(self.suggested()['suggestions'], [])
        second = self.proposal('ticket-2')
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            self.start(second)
        self.assertEqual(self.suggested()['suggestions'], [], 'a started one is a thread now, not a suggestion')
        third = self.proposal('ticket-3')
        self.assertEqual([row['id'] for row in self.suggested()['suggestions']], [third['id']])
        import time as clock
        self.assertEqual(self.suggested(now=clock.time() + 8 * 86400)['suggestions'], [], 'a week on, it has gone quiet')

    def test_home_shows_three_newest_and_counts_the_rest(self):
        made = []
        for index in range(3):
            made.append(self.proposal(f'ticket-{index}'))
        self.source('another')
        for index in range(2):
            made.append(self.proposal(f'more-{index}'))
        found = self.suggested()
        self.assertEqual([row['id'] for row in found['suggestions']], [row['id'] for row in made[::-1][:3]])
        self.assertEqual(found['more'], 2)

    def test_home_leaves_out_suggestions_you_cant_see_or_start(self):
        self.proposal()
        with self.assertRaises(Rejected):
            self.suggested(profile='someone-else')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner='someone-else' WHERE id=?", (self.channel,))
        self.assertEqual(self.suggested()['suggestions'], [], 'someone else’s private thread')
        with self.host.store.db:
            self.host.store.db.execute("UPDATE threads SET owner=? WHERE id=?", (ACTOR, self.channel))
        self.host.dispatch('coding_permission_revoke', self.scope)
        self.assertEqual(self.suggested()['suggestions'], [], 'project access changed, so its thread says why instead')
        self.assertFalse(self.list()[0]['canStart'])
        self.host.dispatch('coding_permission_grant', {**self.scope, 'files': ['test_sample.py'], 'testFile': 'test_sample.py'})
        self.assertEqual(len(self.suggested()['suggestions']), 1)
        self.host.dispatch('conversation_archive', {'projectId': 'p', 'channelId': self.channel})
        self.assertEqual(self.suggested()['suggestions'], [], 'an archived thread’s')
        with self.assertRaisesRegex(Rejected, 'fields'):
            self.host.dispatch('home_suggestions', {'projectId': 'p', 'profileId': 'p', 'start': True})

    def test_reading_suggestions_starts_nothing(self):
        self.proposal()
        before = self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0]
        with patch.object(self.host.coding, 'start') as start, patch.object(self.host.coding.conversation, 'request') as request:
            self.suggested()
        start.assert_not_called()
        request.assert_not_called()
        self.assertEqual(self.host.store.db.execute('SELECT count(*) FROM threads').fetchone()[0], before)
        self.assertEqual(self.list()[0]['state'], 'proposed')

    def test_agents_propose_threads_only_when_asked_to_split_work(self):
        # 9 Oct review: a standing "propose one next task" step went out on every run, scheduled jobs and automatic
        # reviews included, untried on real runs. Suggested lists what agents propose when asked; nothing nudges more.
        import coding_integration
        from codex_coding import dynamic_tools
        tools = self.host.coding.tools_for(self.task)
        self.assertIn(thread_fanout.TOOL, tools)
        self.assertNotIn('dervo_propose_thread', coding_integration.finishing_steps(self.host.store.db, 'p', tools))
        self.assertIn(thread_fanout.PROMPT, coding_integration.during_task(tools))
        described = dynamic_tools([thread_fanout.TOOL])[0]['description']
        self.assertTrue(described.startswith('Propose one focused child thread when the user asks to split work. '), described)

    def test_validation_and_provider_contracts(self):
        from codex_coding import dynamic_tools, validate_arguments
        from claude_policy import mcp_tools
        args = dict(proposal_key='ticket-1', title='One', purpose='Focus', request='Read the local notes')
        validate_arguments(thread_fanout.TOOL, args)
        self.assertEqual(dynamic_tools((thread_fanout.TOOL,))[0]['name'], thread_fanout.TOOL)
        self.assertIn(thread_fanout.TOOL, json.dumps(mcp_tools((thread_fanout.TOOL,))))
        for key, value in [('title', 'x'*101), ('request', 'x'*16001), ('proposal_key', '../elsewhere')]:
            with self.assertRaises(Rejected): thread_fanout.capture(self.host.coding, self.task, {**args, key: value})
        with self.assertRaises(Rejected): thread_fanout.capture(self.host.coding, self.task, {**args, 'projectId': 'other'})

    def test_queue_rechecks_access_before_start_and_retains_cancellation(self):
        proposal = self.proposal()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks are running'):
            queued = self.start(proposal)
        self.host.dispatch('coding_permission_revoke', self.scope)
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start) as start:
            self.host.coding.conversation.queue.drain()
        self.assertEqual(start.call_count, 0)
        failed = self.list()[0]
        self.assertEqual(failed['state'], 'failed')
        self.assertIn('access changed', failed['detail'])
        queue = self.host.coding.conversation.queue.items(queued['channelId'])[0]
        self.host.dispatch('coding_queue_cancel', dict(projectId='p', channelId=queued['channelId'], queueId=queue['id']))
        cancelled = self.list()[0]
        self.assertEqual(cancelled['state'], 'cancelled')
        self.assertFalse(cancelled['canStart'])
        self.assertIn('Cancelled before', cancelled['detail'])
        with self.assertRaises(Rejected): self.start(proposal)

    def test_child_cannot_fan_out_after_source_deletion(self):
        proposal = self.proposal()
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            started = self.start(proposal)
        context = dict(project_id='p', thread_id=started['channelId'], actor=ACTOR)
        child = self.host.coding.read(started['taskId'], context)
        self.assertFalse(thread_fanout.offered(self.host.coding, child))
        self.host.dispatch('conversation_delete', {**self.scope, 'confirmation': 'DELETE ' + self.channel})
        child = self.host.coding.read(started['taskId'], context)
        self.assertFalse(thread_fanout.offered(self.host.coding, child))
        self.assertIsNone(child['thread_source']['channelId'])
        self.assertEqual(child['thread_source']['name'], 'Source thread removed')
        with self.assertRaises(Rejected):
            thread_fanout.capture(self.host.coding, child, dict(proposal_key='nested', title='More', purpose='More', request='More work'))

    def test_legacy_codex_session_is_not_promised_a_new_dynamic_tool(self):
        self.assertIn(thread_fanout.TOOL, self.host.coding.tools_for(self.task))
        legacy = {**self.task, 'session_id': 'existing', 'memory_session_id': 'existing'}
        self.assertNotIn(thread_fanout.TOOL, self.host.coding.tools_for(legacy))
        self.assertIn(thread_fanout.TOOL, self.host.coding.tools_for({**legacy, 'thread_proposal_session_id': 'existing'}))
        claude = {**legacy, 'binding': {**legacy['binding'], 'provider': 'claude'}}
        self.assertIn(thread_fanout.TOOL, self.host.coding.tools_for(claude))

    def test_read_only_parent_keeps_read_only_in_the_child_queue(self):
        with patch.object(self.host.coding, 'start', side_effect=self.fake_start):
            source = self.host.dispatch('coding_request', {**self.scope, 'clientId': 'read-only-source', 'text': 'Propose a focused read-only investigation.', 'readOnly': True})
        self.task = self.host.coding.read(source['taskId'], dict(project_id='p', thread_id=self.channel, actor=ACTOR))
        self.assertTrue(self.task['read_only'])
        self.args['taskId'] = self.task['id']
        proposal = self.proposal()
        with patch.object(self.host.coding, 'owner_slot_problem', return_value='Other tasks are running'):
            result = self.start(proposal)
        queued = self.host.coding.conversation.queue.items(result['channelId'])[0]
        self.assertIs(queued['args']['readOnly'], True)
