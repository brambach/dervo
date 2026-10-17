"""warm_runs.py on its own: what a held Claude Code replays into the task that takes it, and how long it's ticked at
full speed. The end-to-end warm starts are in test_conversation_coding."""
import unittest

import warm_runs


class HeldTests(unittest.TestCase):
    def test_an_old_connector_state_isnt_replayed_as_current(self):
        held = warm_runs.Held('sig', born=0)
        held.events = [('session', {'id': 's'}, 0.0), ('connectors', {'state': 'ready'}, 0.0), ('plan', {'plan': 'max'}, 0.0)]
        self.assertEqual([kind for kind, _ in held.replay(now=warm_runs.FRESH_SECONDS + 1)], ['session', 'plan'])
        self.assertEqual([kind for kind, _ in held.replay(now=1)], ['session', 'connectors', 'plan'])

    def test_only_a_just_started_one_keeps_the_host_ticking_fast(self):
        pool = warm_runs.WarmPool()
        held = warm_runs.Held('sig', born=100)
        held.turn = type('Turn', (), {'closed': False, 'close': lambda self: None})()
        pool.held['t'] = held
        self.assertTrue(pool.warming(now=101))
        self.assertFalse(pool.warming(now=100 + warm_runs.SETTLE_SECONDS))

    def test_one_held_at_a_time_the_older_one_is_closed(self):
        pool, closed = warm_runs.WarmPool(), []
        for name, born in (('a', 1), ('b', 2)):
            held = warm_runs.Held(name, born=born)
            held.turn = type('Turn', (), {'closed': False, 'close': lambda self, n=name: closed.append(n)})()
            pool.offer(name, held)
        self.assertEqual((list(pool.held), closed), (['b'], ['a']))


if __name__ == '__main__':
    unittest.main()
