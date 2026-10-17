"""Collaboration safety review: authority is re-read when it is used, and one writer per conversation holds across engines.

Real Host, CodingTasks, collaboration broker, evidence and confined tests on a disposable project; providers are the
synthetic peers of test_collaboration. Nothing launches a real provider or reads credentials.

Three concrete gaps of the current app, each closed here:

- an agent record re-saved into another project after the owner request was attached was still dispatched there at ask
  time (the helper task would have been created in the other project's conversation, with that project's files);
- a helper could start in an agent's conversation while a chat turn or a text coordination request was running there,
  against the declared one-writer-per-conversation rule;
- a chat turn could start in a conversation where a collaboration helper was working.
"""
import time
import unittest

from dervo_connections import Rejected
from host import ACTOR
from test_collaboration import CollaborationTests, GREETING, SAMPLE, MODEL


class CollaborationSafetyTests(CollaborationTests):
    def setUp(self):
        super().setUp()
        # A second project of the same host: the reviewer's account has a conversation there too.
        self.other = self.base / 'other'
        self.other.mkdir()
        (self.other / 'greeting.py').write_text('def greet(): return "other"\n')
        (self.other / 'test_sample.py').write_text(SAMPLE)
        self.host.store.add_project('q', self.other, 'cx', MODEL)
        self.host.store.add_thread('q-room', 'q', 'q-room', 'shared', None)

    def owner_asks_reviewer_after_a_read(self, peer, text):
        if self.role_of(text) != 'owner':
            return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('finish', 'REVIEW: ok')]
        return [('tool', 'dervo_read_file', {'path': 'test_sample.py'}),
                ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'look', 'instructions': '', 'files': ['greeting.py']}),
                ('finish', 'Done')]

    def test_agent_re_saved_into_another_project_is_refused_when_the_ask_arrives(self):
        self.configure(agents=('reviewer',))
        with self.peer_patch(self.owner_asks_reviewer_after_a_read):
            started = self.request('Ask after I read')
            # The owner's first tool (a read) is answered; its ask is queued at the peer but not yet received by the host.
            self.settle(started['taskId'], until=lambda d: len(d['task']['tokens']) == 1 and d['task']['tokens'][0]['state'] == 'delivered')
            self.assertEqual(self.host.dispatch('collab_policy', self.scope)['available'][0]['name'], 'reviewer')
            # Between attach and ask the record is replaced: same name, same account, another project's conversation.
            self.host.dispatch('collab_agent_save', dict(name='reviewer', profileId='cx', channelId='q-room', projectId='q', role='reviewer', instructions='Be brief.'))
            self.assertIn('another project', self.host.dispatch('collab_policy', self.scope)['unavailable'][0]['reason'])
            detail = self.settle(started['taskId'])
        packets = detail['collaboration']['packets']
        self.assertEqual([(p['status'], p['child_task_id']) for p in packets], [('refused', None)])
        self.assertIn('another project', packets[0]['refusal'])
        owner = next(p for p in self.peers if 'Role:' not in getattr(p, 'text', 'Role:'))
        self.assertEqual([ok for ok, _ in owner.results], [True, False])
        self.assertIn('another project', owner.results[1][1]['error'])
        self.assertEqual([s for s in self.starts if s[1] == 'cx'], [], 'no helper ran anywhere')
        self.assertEqual(self.host.dispatch('tasks', dict(projectId='q', channelId='q-room')), [], 'nothing was created in the other project')
        self.assertEqual((self.other / 'greeting.py').read_text(), 'def greet(): return "other"\n')
        self.assertEqual(detail['task']['state'], 'answered')

    def chat_factory(self):
        from test_codex_live import SyntheticPeer
        peers = []

        def factory(*args, **kwargs):
            peer = SyntheticPeer(*args, finish=False, **kwargs)
            peers.append(peer)
            return peer
        self.host.transport_factory = factory
        return peers

    def start_chat(self, channel, client_id='chat-1'):
        scope = dict(projectId='p', channelId=channel)
        mid = self.host.dispatch('queue', {**scope, 'clientId': client_id, 'text': 'hello', 'parentId': None})['id']
        self.host.dispatch('start_turn', {**scope, 'messageId': mid})
        return mid

    def test_a_chat_turn_in_the_agents_conversation_makes_a_root_ask_wait_until_it_ends(self):
        self.configure(agents=('reviewer',))
        self.chat_factory()
        mid = self.start_chat('review-room')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.host.turns[mid].begun:
            self.host.tick()
            time.sleep(.005)
        self.assertTrue(self.host.turns[mid].begun, 'the synthetic chat turn is running in the reviewer\'s conversation')

        def script(peer, text):
            if self.role_of(text) != 'owner':
                return [('finish', 'REVIEW: ok')]
            return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'look', 'instructions': '', 'files': ['greeting.py']}), ('finish', 'Done')]
        with self.peer_patch(script):
            started = self.request('Ask while the room chats')
            waiting = self.settle(started['taskId'], until=lambda d: any(p['status'] == 'queued' for p in d['collaboration']['packets']))
            packet = waiting['collaboration']['packets'][0]
            self.assertIn('busy', packet['error'])
            self.assertEqual([s for s in self.starts if s[1] == 'cx'], [], 'the helper did not start beside the chat turn')
            for _ in range(20):
                self.host.tick()
            self.assertEqual(self.packets(started['taskId'])[0]['status'], 'queued', 'it keeps waiting while the chat turn runs')
            self.host.dispatch('stop_turn', {'projectId': 'p', 'channelId': 'review-room', 'messageId': mid})
            detail = self.settle(started['taskId'])
        packets = detail['collaboration']['packets']
        self.assertEqual([p['status'] for p in packets], ['returned'])
        self.assertIsNotNone(packets[0]['queued_at'])
        self.assertEqual(len([s for s in self.starts if s[1] == 'cx']), 1, 'exactly one helper start, after the conversation was free')
        self.assertEqual(self.host.store.turn(mid)['state'], 'interrupted')
        self.assertEqual(detail['task']['state'], 'answered')

    def test_a_chat_turn_cannot_start_where_a_helper_is_working(self):
        self.configure(agents=('reviewer',))
        self.chat_factory()

        def script(peer, text):
            if self.role_of(text) != 'owner':
                return [('tool', 'dervo_read_file', {'path': 'greeting.py'}), ('hang',)]
            return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'look', 'instructions': '', 'files': ['greeting.py']}), ('finish', 'Done')]
        with self.peer_patch(script):
            started = self.request('Ask and hold')
            self.settle(started['taskId'], until=lambda d: any(p['status'] == 'running' for p in d['collaboration']['packets'])
                        and sum(len(p.results) for p in self.peers) >= 1)
            with self.assertRaises(Rejected) as refused:
                self.start_chat('review-room')
            self.assertIn('active coding task', str(refused.exception))
            self.assertEqual(self.host.store.turn(self.host.store.db.execute("SELECT id FROM turns WHERE thread_id='review-room'").fetchone()[0])['state'], 'queued')
            self.host.dispatch('task_stop', {**self.scope, 'taskId': started['taskId']})
            detail = self.settle(started['taskId'], until=lambda d: d['task']['state'] != 'working' and all(t.closed for t in self.host.coding.turns.values()))
        self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['withdrawn'])
        self.assertFalse(self.host.coding.busy_thread('review-room'))
        self.assertEqual(self.host.store.binding('review-room', 'p', actor=ACTOR)['id'], 'review-room')


class ClaudeOwnerSafetyTests(CollaborationSafetyTests):
    owner_provider = 'claude'


for _name in [n for n in dir(CollaborationTests) if n.startswith('test_')]:
    setattr(CollaborationSafetyTests, _name, None)  # the inherited scenarios run from test_collaboration, not again here
del CollaborationTests


if __name__ == '__main__':
    unittest.main()
