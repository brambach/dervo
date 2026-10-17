"""Output limits on a provider process. A real full-access run was stopped partway by an 8 MiB whole-run cap."""
import json
import sys
import unittest
import os
import signal
import subprocess
import time
from unittest.mock import patch

from dervo_connections import Rejected
import transport
from transport import OwnedProcess

PY = sys.executable
# The Mac values these tests always used. Windows has no /private/tmp, and its Python won't start without SYSTEMROOT.
TMP = __import__('platform_os').scratch_root()
ENV = {'SYSTEMROOT': os.environ.get('SYSTEMROOT', '')} if os.name == 'nt' else {}


def drain(process):
    events = []
    while True:
        batch = process.read(30)
        if batch is None:
            return events
        events.extend(batch)


def child(code):
    return [PY, '-c', code]


class TransportLimits(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', '/bin/sleep, ps and a POSIX session')
    def test_native_command_in_its_own_session_stops_with_provider(self):
        # The child deliberately survives both stdin EOF and the provider's process group.
        code = ('import subprocess,sys,time,json\n'
                'p=subprocess.Popen(["/bin/sleep","30"], start_new_session=True)\n'
                'print(json.dumps({"pid":p.pid}),flush=True)\n'
                'sys.stdin.read()\n'
                'time.sleep(30)\n')
        process = OwnedProcess(child(code), cwd=TMP, env=ENV, native_commands=True)
        pid = process.read(5)[0]['pid']
        try:
            process.close()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'state='], capture_output=True, text=True).stdout.strip()
                if not state or state.startswith('Z'):
                    break
                time.sleep(.05)
            self.assertTrue(not state or state.startswith('Z'), state)
        finally:
            process.close()
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass

    def test_remembered_descendant_never_kills_reused_pid(self):
        import process_descendants
        saved = {123: {'start': 100, 'exe': '/bin/sleep'}}
        with patch('coding_integration.process_identity', return_value={'start': 200, 'exe': '/bin/sleep'}), \
                patch.object(process_descendants.os, 'kill') as kill:
            process_descendants.stop_descendants(saved)
            kill.assert_not_called()

    def test_close_survives_eperm_on_the_kill_after_a_slow_exit(self):
        # 16.3's host-errors.log: SIGKILL to the group after the wait timed out raised PermissionError out of tick.
        class FakePipe:
            closed = False
            def close(self): self.closed = True

        class FakeProcess:
            pid = 4242
            def __init__(self):
                self.waits, self.killed = 0, False
                self.stdin, self.stdout, self.stderr = FakePipe(), FakePipe(), FakePipe()
            def poll(self): return None
            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired('fake', timeout)
                return -9
            def kill(self): self.killed = True

        class FakeSelector:
            closed = False
            def close(self): self.closed = True

        owned = OwnedProcess.__new__(OwnedProcess)
        owned.process, owned.selector = FakeProcess(), FakeSelector()
        owned.closed, owned.native_commands = False, False
        owned._writer = None  # b16/windows's Windows stdin writer, which a POSIX pipe never starts
        sent = []
        def refuse(pid, sig):
            sent.append(sig)
            raise PermissionError(1, 'Operation not permitted')
        with patch.object(transport.platform_os, 'signal_group', side_effect=refuse):
            owned.close()
        self.assertEqual(sent, [signal.SIGTERM, transport.platform_os.SIGKILL, transport.platform_os.SIGKILL])  # SIGTERM on Windows
        self.assertTrue(owned.process.killed)
        self.assertEqual(owned.process.waits, 2)
        self.assertTrue(owned.selector.closed)
        self.assertTrue(all(p.closed for p in (owned.process.stdin, owned.process.stdout, owned.process.stderr)))

    def test_a_long_run_streams_well_past_8_mib(self):
        # 20 MiB in 1 KiB events, like a long task's streamed deltas and tool results.
        code = "import sys\nline=('{\"t\":\"' + 'x'*1000 + '\"}\\n').encode()\nfor _ in range(21000): sys.stdout.buffer.write(line)"
        with OwnedProcess(child(code), cwd=TMP, env=ENV) as process:
            self.assertEqual(len(drain(process)), 21000)

    def test_a_screenshot_sized_line_is_read(self):
        code = "import sys\nsys.stdout.buffer.write(('{\"image\":\"' + 'A'*(3*1024*1024) + '\"}\\n').encode())"
        with OwnedProcess(child(code), cwd=TMP, env=ENV) as process:
            (event,) = drain(process)
            self.assertEqual(len(event['image']), 3 * 1024 * 1024)

    def test_a_flood_is_still_stopped_and_named(self):
        code = "import sys\nline=('{\"t\":\"' + 'x'*1000 + '\"}\\n').encode()\nwhile True: sys.stdout.buffer.write(line)"
        with OwnedProcess(child(code), cwd=TMP, env=ENV, byte_limit=2 * 1024 * 1024) as process:
            with self.assertRaises(Rejected) as caught:
                drain(process)
        self.assertEqual(str(caught.exception), 'The agent sent more than 2 MiB of output in one run, so Dervo stopped it.')

    # Harry, 27 Sep: a run reading PUBLISHING.md stopped with "Provider did not consume input". A Windows pipe holds about
    # 4 KiB, so a bigger message waits on the provider reading it, and the 2 s non-blocking write gave up first.
    SLOW_READER = "import sys,time\ntime.sleep(3)\nline=sys.stdin.buffer.readline()\nprint('{\"got\":%d}' % len(line), flush=True)"

    def test_a_big_message_waits_for_a_slow_provider_on_windows(self):
        big = {'content': 'x' * (100 * 1024)}
        with patch.object(transport, 'threaded_writes', return_value=True):
            with OwnedProcess(child(self.SLOW_READER), cwd=TMP, env=ENV) as process:
                started = time.monotonic()
                process.send(big)
                self.assertLess(time.monotonic() - started, 1, 'the send waited on the provider')
                (event,) = drain(process)
        self.assertEqual(event['got'], len(__import__('provider_image_input').encode_message(big)))

    @unittest.skipIf(os.name == 'nt', 'the deadline path is the Mac one')
    def test_negative_control_the_deadline_still_gives_up_on_the_mac(self):
        with OwnedProcess(child(self.SLOW_READER), cwd=TMP, env=ENV) as process:
            with self.assertRaisesRegex(TimeoutError, 'Provider did not consume input'):
                process.send({'content': 'x' * (100 * 1024)})

    def test_a_provider_that_stopped_reading_is_reported_on_the_next_send(self):
        with patch.object(transport, 'threaded_writes', return_value=True):
            with OwnedProcess(child('import sys; sys.stdin.close()'), cwd=TMP, env=ENV) as process:
                process.process.wait(10)
                process.send({'content': 'x' * (100 * 1024)})
                process._writer.join(10)
                with self.assertRaisesRegex(TimeoutError, 'Provider did not consume input'):
                    process.send({'ok': 1})

    def test_an_oversized_line_is_still_stopped(self):
        decoder = transport.JsonLines(limit=1024)
        with self.assertRaises(Rejected) as caught:
            decoder.feed(b'{"x":"' + b'y' * 2048)
        self.assertIn('stopped the run', str(caught.exception))
        self.assertEqual(transport.JsonLines().feed(json.dumps({'ok': 1}).encode() + b'\n'), [{'ok': 1}])


if __name__ == '__main__':
    unittest.main()
