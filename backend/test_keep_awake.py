"""Keeping the Mac awake while runs are open (keep_awake.py). A fake spawn stands in for /usr/bin/caffeinate, except in
the one test that checks the real command's arguments on this Mac."""
import os
import subprocess
import unittest
import unittest.mock
from types import SimpleNamespace

import keep_awake


class FakeProcess:
    def __init__(self):
        self.code, self.terminated = None, False
    def poll(self): return self.code
    def terminate(self): self.terminated, self.code = True, -15
    def wait(self, timeout=None): return self.code
    def kill(self): self.code = -9


class KeepAwakeTests(unittest.TestCase):
    def setUp(self):
        self.spawned = []
        def spawn(argv, **kwargs):
            process = FakeProcess()
            self.spawned.append((argv, kwargs, process))
            return process
        self.keep = keep_awake.KeepAwake(spawn=spawn, pid=4242)

    def test_it_holds_while_runs_are_open_and_lets_go_when_none_are(self):
        self.assertFalse(self.keep.update(False), 'nothing runs: nothing is held')
        self.assertEqual(self.spawned, [])
        self.assertTrue(self.keep.update(True))
        self.assertTrue(self.keep.update(True))
        self.assertEqual(len(self.spawned), 1, 'one assertion for as long as runs go on, not one per tick')
        argv, kwargs, process = self.spawned[0]
        self.assertEqual(argv, ['/usr/bin/caffeinate', '-i', '-w', '4242'], 'idle sleep only, and it ends with the host')
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertFalse(self.keep.update(False))
        self.assertTrue(process.terminated)

    def test_it_comes_back_if_caffeinate_ends_while_runs_go_on(self):
        self.keep.update(True)
        self.spawned[0][2].code = 0
        self.assertTrue(self.keep.update(True))
        self.assertEqual(len(self.spawned), 2)

    def test_a_mac_without_caffeinate_never_breaks_a_tick_or_retries_every_tick(self):
        calls = []
        def broken(argv, **kwargs):
            calls.append(argv)
            raise FileNotFoundError(argv[0])
        keep = keep_awake.KeepAwake(spawn=broken, pid=1)
        self.assertFalse(keep.update(True))
        self.assertFalse(keep.update(True))
        self.assertEqual(len(calls), 1)
        keep.update(False)
        keep.update(True)
        self.assertEqual(len(calls), 2, 'after a quiet spell it tries again')

    def test_the_host_holds_it_only_while_a_run_is_open(self):
        import tempfile
        from pathlib import Path
        from host import Host
        with tempfile.TemporaryDirectory(prefix='dervo-awake-') as tmp:
            host = Host(Path(tmp) / 'store')
            try:
                turns = {}
                host.coding = SimpleNamespace(turns=turns, tick=lambda: None)
                host.keep_awake = self.keep
                host.keep_awake_tick()
                self.assertEqual(self.spawned, [])
                turns['t1'] = SimpleNamespace(closed=False)
                host.keep_awake_tick()
                self.assertTrue(self.keep.holding)
                turns['t1'].closed = True
                host.keep_awake_tick()
                self.assertFalse(self.keep.holding)
                turns['t1'].closed = False
                host.keep_awake_tick()
                del host.coding
            finally:
                host.close()
            self.assertFalse(self.keep.holding, 'closing Dervo lets go')

    def test_any_live_agent_run_holds_it_and_the_setting_can_turn_it_off(self):
        import tempfile
        from pathlib import Path
        from host import Host
        from dervo_connections import Rejected
        with tempfile.TemporaryDirectory(prefix='dervo-awake-') as tmp:
            host = Host(Path(tmp) / 'store')
            try:
                host.keep_awake = self.keep
                with unittest.mock.patch('keep_awake.supported', return_value=True):
                    self.assertEqual(host.dispatch('keep_awake_status', {}), {'enabled': True, 'holding': False, 'supported': True, 'line': keep_awake.LINE},
                                     'on unless you turn it off')
                    host.turns['m1'] = SimpleNamespace(closed=False)  # a chat turn
                    host.keep_awake_tick()
                    self.assertTrue(host.dispatch('keep_awake_status', {})['holding'])
                    host.turns['m1'].closed = True
                    host.harness = SimpleNamespace(live=True, tick=lambda: None)  # a coordinator helper at work
                    host.keep_awake_tick()
                    self.assertTrue(self.keep.holding)
                    off = host.dispatch('keep_awake_set', {'enabled': False})
                    self.assertEqual((off['enabled'], off['holding']), (False, False), 'turning it off lets go at once')
                    host.keep_awake_tick()
                    self.assertFalse(self.keep.holding, 'and it stays off while runs go on')
                    for bad in ({'enabled': 'no'}, {'enabled': None}, {}):
                        with self.assertRaises(Rejected):
                            host.dispatch('keep_awake_set', bad)
            finally:
                host.turns.clear()  # stand-ins, not turns the host can stop
                host.__dict__.pop('harness', None)
                host.close()
            again = Host(Path(tmp) / 'store')
            try:
                self.assertFalse(again.keep_awake_on(), 'the choice is kept')
                again.dispatch('keep_awake_set', {'enabled': True})
                self.assertTrue(keep_awake.enabled(again.store.db))
            finally:
                again.close()

    def test_windows_holds_the_execution_state_on_its_own_thread_and_clears_it(self):
        import threading
        from unittest import mock
        calls = []
        def fake(flags):
            calls.append((flags, threading.current_thread().name))
            return 0x80000000
        def no_caffeinate(argv, **kwargs):
            raise AssertionError('no caffeinate on Windows')
        with mock.patch('platform_os.WINDOWS', True):
            keep = keep_awake.KeepAwake(spawn=no_caffeinate, set_state=fake)
        self.assertFalse(keep.update(False))
        self.assertEqual(calls, [])
        self.assertTrue(keep.update(True))
        self.assertTrue(keep.update(True))
        self.assertEqual(calls, [(0x80000001, 'dervo-keep-awake')], 'ES_CONTINUOUS | ES_SYSTEM_REQUIRED, once while runs go on')
        self.assertFalse(keep.update(False))
        self.assertEqual(calls[1:], [(0x80000000, 'dervo-keep-awake')], 'cleared on the same thread that set it')
        self.assertTrue(keep.update(True))
        keep.close()
        self.assertFalse(keep.holding, 'closing Dervo clears it')
        self.assertEqual([flags for flags, _ in calls], [0x80000001, 0x80000000, 0x80000001, 0x80000000])

    def test_windows_refusing_the_state_never_breaks_a_tick_or_retries_every_tick(self):
        calls = []
        def refused(flags):
            calls.append(flags)
            return 0
        keep = keep_awake.KeepAwake(set_state=refused, windows=True)
        self.assertFalse(keep.update(True))
        self.assertFalse(keep.update(True))
        self.assertEqual(calls, [0x80000001])
        keep.update(False)
        self.assertFalse(keep.update(True))
        self.assertEqual(len(calls), 2, 'after a quiet spell it tries again')
        # No kernel32 at all (this Mac): the real call fails quietly instead of raising into the tick.
        mac = keep_awake.KeepAwake(windows=True)
        self.assertFalse(mac.update(True))
        mac.close()

    @unittest.skipUnless(os.path.exists(keep_awake.CAFFEINATE), 'macOS only')
    def test_the_real_caffeinate_starts_and_stops(self):
        keep = keep_awake.KeepAwake()
        try:
            self.assertTrue(keep.update(True))
        finally:
            keep.update(False)
        self.assertFalse(keep.holding)


if __name__ == '__main__':
    unittest.main()
