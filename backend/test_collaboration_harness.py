"""The coding collaboration and the text coordination path are governed by the same harness delegation rules.

What is proven, with the real Host, the real ``CodingTasks`` and the packaged, provenance-verified ``dervo_harness``:
the one ``DelegationRules.decide`` of the library is the component that admits or refuses a help request in BOTH paths.
Patching it refuses a coding owner's ``dervo_request_help`` and a text helper's ``request_help`` with the same text,
with nothing created on either side; spying on it shows each path handing it its own lineage and its own policy. Without
the verified library no coding agent can be asked at all. Provider processes are the synthetic peers of the existing
tests; nothing launches a real provider or reads credentials.
"""
import unittest
from unittest.mock import patch

from dervo_connections import Rejected
from test_collaboration import CollaborationTests, GREETING, sha
from test_harness_host import FakeWorker, SCRIPTS

MARKER = 'SHARED-RULES-MARKER: refused by the one delegation layer'


def library():
    import dervo_harness  # already verified and imported by the Host's coordinator (harness_port.load_library)
    return dervo_harness


class SharedHarnessGovernanceTests(CollaborationTests):
    """Reuses the collaboration fixture (owner on Codex, one Codex reviewer, one Claude worker) and adds fake text helpers."""

    def setUp(self):
        super().setUp()
        from harness_port import HelperSpec
        self.text_helpers = {'dates': HelperSpec(FakeWorker(SCRIPTS), 'fake', 'none', note='answers date questions'),
                             'lead': HelperSpec(FakeWorker(SCRIPTS), 'fake', 'none', note='plans and asks dates')}

    def text_host(self):
        """A second Host on its own store with the fake text helpers, the same packaged library, no coding involved."""
        from host import Host
        from harness_adapter import open_supervisor
        host = Host(self.base / 'text-store', supervisor_factory=open_supervisor, harness_workers=self.text_helpers)
        self.addCleanup(host.close)
        profile = host.dispatch('add_profile', {'provider': 'codex', 'identity': 'expected@example.test', 'category': 'personal'})['id']
        project = host.add_picked_project(str(self.base / 'text-project'), profile, 'unverified-model')['id']
        channel = host.dispatch('create_conversation', {'projectId': project, 'name': 'Planning', 'private': False})['id']
        return host, {'projectId': project, 'channelId': channel}

    def text_request(self, host, scope, text, helper='lead', client_id='t-1'):
        import time
        started = host.dispatch('harness_request', {**scope, 'clientId': client_id, 'text': text, 'mode': 'coordinate', 'helper': helper})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            host.tick()
            status = host.dispatch('harness_status', {**scope, 'requestId': started['id']})
            if status['state'] in ('completed', 'cancelled', 'failed', 'lost', 'unknown'):
                events = host.dispatch('harness_events', {**scope, 'after': 0})
                return status, [e for e in events if e['request_id'] == started['id']]
            time.sleep(.001)
        self.fail('text request did not settle')

    def owner_asks_reviewer(self, peer, text):
        if self.role_of(text) != 'owner':
            return [('finish', 'REVIEW: ok')]
        return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'look', 'instructions': '', 'files': ['greeting.py']}), ('finish', 'Done')]

    def test_one_delegation_layer_refuses_both_paths_when_it_refuses(self):
        (self.base / 'text-project').mkdir()
        self.configure()
        lib = library()
        rules = lib.DelegationRules

        def refuse(self_rules, ask, lineage, **kw):
            raise lib.PolicyViolation(MARKER)

        with patch.object(rules, 'decide', refuse):
            # Coding path: the owner's ask is refused with the layer's text; no packet child, no helper start.
            with self.peer_patch(self.owner_asks_reviewer):
                started = self.request('Ask with the rules patched')
                detail = self.settle(started['taskId'])
            owner = next(p for p in self.peers if 'Role:' not in getattr(p, 'text', 'Role:'))
            self.assertEqual([ok for ok, _ in owner.results], [False])
            self.assertEqual(owner.results[0][1]['error'], MARKER)
            packets = detail['collaboration']['packets']
            self.assertEqual([(p['status'], p['refusal'], p['child_task_id']) for p in packets], [('refused', MARKER, None)])
            self.assertEqual([s for s in self.starts if s[1] == 'cx'], [], 'no helper ran')
            self.assertEqual(detail['task']['state'], 'answered')
            # Text path: the same patched component refuses the fake lead helper's structured ask with the same text.
            host, scope = self.text_host()
            status, events = self.text_request(host, scope, 'Plan the week')
            refused = [e for e in events if e['kind'] == 'help_refused']
            self.assertEqual(len(refused), 1)
            self.assertEqual((refused[0]['data']['error_type'], refused[0]['data']['error']), ('PolicyViolation', MARKER))
            self.assertEqual(status['delegations'], [])
            self.assertEqual(self.text_helpers['dates'].port.starts if hasattr(self.text_helpers['dates'].port, 'starts') else [], [])

    def test_each_path_hands_the_layer_its_own_lineage_and_policy(self):
        (self.base / 'text-project').mkdir()
        self.configure(depth=2, parallel=2, calls=4)
        lib = library()
        original = lib.DelegationRules.decide
        calls = []

        def spy(self_rules, ask, lineage, **kw):
            admission = original(self_rules, ask, lineage, **kw)
            calls.append((self_rules.policy, ask, lineage, kw, admission))
            return admission

        with patch.object(lib.DelegationRules, 'decide', spy):
            with self.peer_patch(self.owner_asks_reviewer):
                started = self.request('Ask with the rules observed')
                detail = self.settle(started['taskId'])
            host, scope = self.text_host()
            status, events = self.text_request(host, scope, 'Plan the week')
        self.assertEqual([p['status'] for p in detail['collaboration']['packets']], ['returned'])
        self.assertEqual(status['state'], 'completed')
        self.assertEqual(len(calls), 2, 'one admission per path, both through the same method')
        coding, text = calls
        # The coding path: the project policy became the library's policy; the lineage is the owner task at depth 0.
        policy, ask, lineage, kw, admission = coding
        self.assertEqual((policy.max_depth, policy.max_per_root, policy.max_per_run, policy.max_parallel, policy.allow_repeat_sources), (2, 4, 16, 2, True))
        self.assertEqual((ask.target, ask.target_id, ask.purpose, ask.sources), ('reviewer', 'reviewer', 'look', ('greeting.py', 'test_sample.py')))
        self.assertEqual((lineage.depth, lineage.root_id, lineage.ancestors, lineage.asker), (0, started['taskId'], (), started['taskId']))
        self.assertEqual((kw['per_run'], kw['per_root'], kw['priors'], kw['active'], kw['target_busy']), (0, 0, [], 1, False))
        self.assertEqual((admission.action, admission.depth), (lib.ADMIT_START, 1))
        # The text path: the Supervisor's own policy (library defaults, unbounded parallelism, repeats refused), a live run at depth 0.
        policy, ask, lineage, kw, admission = text
        self.assertEqual((policy.max_depth, policy.max_per_root, policy.max_per_run, policy.max_parallel, policy.allow_repeat_sources), (2, 6, 3, None, False))
        self.assertEqual((ask.target, ask.target_id, ask.purpose), ('dates', 'helper:dates', 'Which date?'))
        self.assertEqual(len(ask.sources), 1)
        self.assertTrue(ask.sources[0].startswith('message:'))
        self.assertEqual((lineage.depth, lineage.ancestors, lineage.asker), (0, (), 'helper:lead'))
        self.assertEqual(lineage.root_id, host.harness.requests(scope['channelId'])[0]['harness_request_id'], "a depth-0 run's root is its own request")
        self.assertEqual((admission.action, admission.depth), (lib.ADMIT_START, 1))

    def test_coding_refusals_carry_the_library_wording(self):
        self.configure(depth=1, calls=1)

        def script(peer, text):
            if self.role_of(text) != 'owner':
                return [('finish', 'REVIEW: ok')]
            return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'one', 'instructions': '', 'files': ['greeting.py']}),
                    ('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'two', 'instructions': '', 'files': ['greeting.py']}),
                    ('finish', 'Done')]
        with self.peer_patch(script):
            started = self.request('Spend the budget')
            detail = self.settle(started['taskId'])
        packets = detail['collaboration']['packets']
        self.assertEqual([p['status'] for p in packets], ['returned', 'refused'])
        self.assertIn('has used its budget of 1 help request(s) per original request', packets[1]['refusal'])
        self.assertTrue(packets[1]['refusal'].startswith(f"request {started['taskId']} "), packets[1]['refusal'])

    def test_no_verified_library_means_no_agent_can_be_asked(self):
        self.configure()
        self.assertEqual(self.host.dispatch('collab_policy', self.scope)['state'], 'automatic')
        collaboration = self.host.coding.collaboration
        self.assertIs(collaboration.library, library(), 'the coding path holds the same verified module the coordinator loads')
        # The same host, its library gone (as after a provenance refusal): the policy is unavailable and the owner gets no help tool.
        collaboration.library, collaboration.library_detail = None, 'not packaged in this build'
        status = self.host.dispatch('collab_policy', self.scope)
        self.assertEqual(status['state'], 'unavailable')
        self.assertIn('not packaged in this build', status['detail'])
        self.assertEqual(status['available'], [])
        with self.peer_patch(lambda peer, text: [('finish', 'No tools.')]):
            started = self.request('Ask for help without the library', 'nolib-1')
            detail = self.settle(started['taskId'])
        self.assertEqual(detail['task']['collaboration']['agents'], [])
        self.assertEqual(detail['task']['collaboration']['mode'], 'unavailable')
        owner = next(p for p in self.peers if 'Role:' not in getattr(p, 'text', 'Role:'))
        self.assertNotIn('dervo_request_help', owner.text)

    def test_after_a_restart_a_new_request_sees_earlier_returned_help_and_replays_nothing(self):
        """Recovery without replay: the interrupted owner and its child stay disconnected, their packets stay disconnected, and
        the user's next explicit request in the same conversation gets the earlier returned, verified help in its context
        (summaries and the exact after-hashes) without copying anything by hand. No provider start is repeated."""
        self.configure()
        with self.peer_patch(self.owner_asks_review_and_fix):
            first = self.request('Check greeting.py and make sure the tests stay green', 'done-1')
            self.assertEqual(self.settle(first['taskId'])['task']['state'], 'ready_for_review')

        def hang(peer, text):
            if self.role_of(text) != 'owner':
                return [('hang',)]
            return [('tool', 'dervo_request_help', {'agent': 'reviewer', 'purpose': 'review again', 'instructions': '', 'files': ['greeting.py']}), ('finish', 'x')]
        with self.peer_patch(hang):
            second = self.request('Review again', 'hang-1')
            self.settle(second['taskId'], until=lambda d: any(p['status'] == 'running' for p in d['collaboration']['packets']) and len(self.starts) == 5)
        starts = len(self.starts)
        self.reopen()
        seen = []

        def resume(peer, text):
            seen.append(text)
            return [('finish', 'Continuing from the recorded state.')]
        with self.peer_patch(resume):
            third = self.request('Continue from where the helpers left off', 'resume-1')
            detail = self.settle(third['taskId'])
        self.assertEqual(detail['task']['state'], 'answered')
        text = seen[-1]
        self.assertIn('Help returned earlier in this conversation', text)
        self.assertIn('REVIEW: greet() returns', text, 'the earlier review travels into the new request without manual copying')
        self.assertIn('FIX: added a comment', text)
        self.assertIn(sha(GREETING + '# fixed by helper\n'), text, "the worker's verified after-hash is stated, so the model can check the files")
        self.assertNotIn('review again', text.split('Help returned earlier')[1].split('Request:')[0], 'the interrupted packet is not presented as returned help')
        self.assertEqual(len(self.starts), starts + 1, 'exactly one new provider start: the new owner. Nothing replayed')
        interrupted = self.detail(second['taskId'])
        # The quit paused the owner (16.4), so it ended interrupted; its helper was still lost.
        self.assertEqual(interrupted['task']['state'], 'blocked')
        self.assertEqual([p['status'] for p in interrupted['collaboration']['packets']], ['disconnected'])
        child = self.detail(interrupted['collaboration']['packets'][0]['child_task_id'], dict(projectId='p', channelId='review-room'))['task']
        self.assertEqual(child['state'], 'disconnected')
        self.assertEqual(detail['collaboration']['packets'], [], 'the new request has no packets of its own; earlier ones are context, not replays')
        with self.assertRaises(Rejected):
            self.host.dispatch('task_continue', {**self.scope, 'taskId': second['taskId'], 'text': 'go on'})  # an unobserved run is never continued


for _name in [n for n in dir(CollaborationTests) if n.startswith('test_')]:
    setattr(SharedHarnessGovernanceTests, _name, None)  # the inherited scenarios run from test_collaboration, not again here
del CollaborationTests  # only the governance tests above run from this module


if __name__ == '__main__':
    unittest.main()
