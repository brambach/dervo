import json
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

class HostStdioTests(unittest.TestCase):
    def test_startup_lock_error_reaches_the_native_request(self):
        from dervo_connections import Store
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root) / 'conversations.sqlite3')
            try:
                result = subprocess.run([sys.executable,str(Path(__file__).with_name('host.py')),root],
                    input='{"operation":"status","args":{}}\n', capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'error':'Another Dervo host owns this database'})
            finally:
                store.close()

    def test_native_shutdown_request_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('host.py')),root],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                p.stdin.write(b'{"operation":"_shutdown","args":{}}\n');p.stdin.flush()
                self.assertEqual(p.wait(timeout=2),0)
            finally:
                if p.poll() is None:p.kill();p.wait()
                p.stdin.close();p.stdout.close();p.stderr.close()

    def test_pipelined_requests_do_not_stall_in_text_buffer(self):
        with tempfile.TemporaryDirectory() as root:
            p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('host.py')),root],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
            try:
                p.stdin.write(b''.join((json.dumps({'id':n,'operation':'status','args':{}})+'\n').encode() for n in (1,2,3)))
                output=b''
                # Beta.18: the host's heartbeat shares stdout, one line a second; only replies count here. So do the state
                # spine's push lines (thread_states.py), which the bridge sends to the window, never to a caller.
                replies=lambda:[line for line in output.splitlines() if not line.startswith((b'{"heartbeat"',b'{"event"'))]
                beats=lambda:[line for line in output.splitlines() if line.startswith(b'{"heartbeat"')]
                while len(replies())<3 or not beats() or not output.endswith(b'\n'):
                    self.assertTrue(select.select([p.stdout],[],[],2)[0],'Buffered host requests stalled')
                    output+=p.stdout.read(1)
                self.assertEqual([json.loads(line)['id'] for line in replies()],[1,2,3])
                self.assertIn('operation',json.loads(beats()[0])['heartbeat'],'requests with ids start the heartbeat')
            finally:
                p.stdin.close()
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:p.kill();p.wait()
                p.stdout.close();p.stderr.close()
