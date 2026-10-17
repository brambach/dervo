"""Chat slice regressions with synthetic peers and temporary stores only."""
from test_codex_live import HostLiveTests


class ActivityTests(HostLiveTests):
    def test_status_is_scoped_and_never_drains_older_queues(self):
        from dervo_connections import Rejected
        older = self.queue('older')
        newer = self.queue('newer')
        other = self.host.dispatch('create_conversation', {'projectId':self.scope['projectId'], 'name':'Other', 'private':True})['id']
        other_scope = {**self.scope, 'channelId':other}
        self.host.dispatch('start_turn', {**self.scope, 'messageId':newer})
        self.peers[0].finish = False
        self.settle()
        self.assertEqual(self.host.dispatch('conversation_status', other_scope)['state'], 'unchecked')
        self.assertEqual(self.host.store.turn(older)['state'], 'queued')
        with self.assertRaises(Rejected):
            self.host.dispatch('conversation_status', {**self.scope, 'projectId':'wrong'})
        self.assertEqual(len(self.peers), 1)

    def test_failed_preflight_status_retains_queue_until_explicit_retry(self):
        mid = self.queue()
        self.host.dispatch('start_turn', {**self.scope, 'messageId':mid})
        self.peers[0].signed_in = False
        self.settle()
        self.assertEqual(self.host.dispatch('conversation_status', self.scope)['state'], 'blocked')
        self.assertEqual(self.host.store.turn(mid)['state'], 'queued')
        self.peers[0].signed_in = True
        self.settle()
        self.assertFalse(any(m.get('method') == 'turn/start' for m in self.peers[0].sent))
        self.assertEqual(self.queue(), mid)

    def test_scoped_activity_tracks_runtime_without_starting_queued_work(self):
        mid = self.queue()
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'unchecked')
        self.assertFalse(status['active'])
        self.assertEqual(self.peers, [])
        self.host.dispatch('start_turn', {**self.scope, 'messageId': mid})
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'checking')
        self.peers[0].finish = False
        self.settle()
        self.assertEqual(self.host.dispatch('conversation_status', self.scope)['state'], 'working')
        self.assertTrue(self.host.dispatch('status', {})['liveEnabled'])
        self.peers[0].complete('completed')
        self.settle()
        status = self.host.dispatch('conversation_status', self.scope)
        self.assertEqual(status['state'], 'idle')
        self.assertFalse(status['active'])
        self.assertNotIn('review', status['detail'].lower())
